"""Phase 1 schema checks against the migrated TEST_DATABASE_URL database (read-only)."""

import re
from enum import StrEnum

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Engine, Enum, text

from support_ops.db.schema import Base

DOMAIN_TABLES = sorted(Base.metadata.tables)


def _scalars(engine: Engine, sql: str, **params: object) -> list[object]:
    with engine.connect() as connection:
        return list(connection.execute(text(sql), params).scalars())


def _only_domain_objects(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    # Ignore database tables this metadata does not own (alembic_version, extension tables).
    return not (type_ == "table" and reflected and compare_to is None)


def test_migrated_database_has_no_drift_from_orm_metadata(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_object": _only_domain_objects}
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == []


def test_all_domain_tables_exist(test_engine: Engine) -> None:
    existing = set(
        _scalars(
            test_engine,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()",
        )
    )

    assert set(DOMAIN_TABLES) <= existing


def test_money_columns_are_numeric_12_2_and_no_floats_exist(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ANY(:tables)"
            ),
            {"tables": DOMAIN_TABLES},
        ).all()

    numeric = {(r.table_name, r.column_name): (r.numeric_precision, r.numeric_scale)
               for r in rows if r.data_type == "numeric"}  # fmt: skip
    assert numeric
    assert set(numeric.values()) == {(12, 2)}
    assert ("payments", "amount_refunded") in numeric
    assert ("order_items", "refunded_amount") in numeric
    assert not [r for r in rows if r.data_type in {"real", "double precision"}]
    timestamps = {r.data_type for r in rows if r.data_type.startswith("timestamp")}
    assert timestamps == {"timestamp with time zone"}


def _enum_columns() -> list[tuple[str, str, type[StrEnum]]]:
    found = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, Enum) and column.type.enum_class is not None:
                found.append((table.name, str(column.type.name), column.type.enum_class))
    return found


@pytest.mark.parametrize(
    ("table", "enum_name", "enum_cls"), _enum_columns(), ids=lambda v: getattr(v, "__name__", v)
)
def test_enum_check_constraints_match_python_enums(
    test_engine: Engine, table: str, enum_name: str, enum_cls: type[StrEnum]
) -> None:
    definition = _scalars(
        test_engine,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name",
        name=f"ck_{table}_{enum_name}",
    )

    assert len(definition) == 1
    assert set(re.findall(r"'([^']*)'", str(definition[0]))) == {m.value for m in enum_cls}


def test_every_orm_check_constraint_exists_in_the_database(test_engine: Engine) -> None:
    expected = {
        str(constraint.name)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    actual = set(
        _scalars(
            test_engine,
            "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE c.contype = 'c' AND t.relname = ANY(:tables) "
            "AND t.relnamespace = current_schema()::regnamespace",
            tables=DOMAIN_TABLES,
        )
    )

    assert actual == expected


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("fk_cases_order_id_customer_id_orders", "f"),
        ("fk_refunds_payment_id_order_id_payments", "f"),
        ("fk_refunds_action_id_order_id_actions", "f"),
        ("fk_refund_items_order_id_order_item_id_order_items", "f"),
        ("fk_action_items_action_id_order_id_actions", "f"),
        ("uq_actions_idempotency_key", "u"),
        ("uq_approvals_action_id", "u"),
        ("uq_shipments_order_id", "u"),
        ("uq_payments_order_id", "u"),
        ("pk_action_triggered_rules", "p"),
        ("pk_action_policy_references", "p"),
        ("ck_payments_status_matches_amount_refunded", "c"),
        ("ck_actions_status_matches_authorization_decision", "c"),
        ("ck_order_items_refunded_amount_within_paid_total", "c"),
    ],
)
def test_key_constraints_exist(test_engine: Engine, name: str, kind: str) -> None:
    assert _scalars(
        test_engine, "SELECT contype FROM pg_constraint WHERE conname = :n", n=name
    ) == [kind]


@pytest.mark.parametrize(
    "name",
    ["ix_approvals_status", "ix_actions_status", "ix_outbox_unpublished", "ix_orders_customer_id"],
)
def test_key_indexes_exist(test_engine: Engine, name: str) -> None:
    assert _scalars(
        test_engine,
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND indexname = :n",
        n=name,
    ) == [name]


@pytest.mark.parametrize("table", ["refunds", "refund_items"])
def test_refund_ledger_tables_have_append_only_triggers(test_engine: Engine, table: str) -> None:
    definitions = _scalars(
        test_engine,
        "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE c.relname = :table AND NOT t.tgisinternal AND t.tgenabled <> 'D'",
        table=table,
    )

    assert len(definitions) == 1
    definition = str(definitions[0])
    assert definition.startswith(f"CREATE TRIGGER trg_{table}_append_only BEFORE DELETE OR UPDATE")
    assert "FOR EACH ROW EXECUTE FUNCTION" in definition
    assert definition.endswith("reject_refund_ledger_mutation()")
    assert "TRUNCATE" not in definition
    assert "INSERT" not in definition
