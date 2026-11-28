"""Offline checks that migration 0002 matches the ORM schema (no database needed).

The migration is hand-written with frozen enum values, so these tests compare its rendered
PostgreSQL DDL with DDL compiled from ``support_ops.db.schema`` column by column and
constraint by constraint. The integration suite repeats the comparison against a real
database with Alembic's ``compare_metadata``.
"""

import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Float, Numeric
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from support_ops.db.schema import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_0 = "0001_enable_pgvector"
PHASE_1 = "0002_create_domain_schema"
CORE_TABLES = {
    "customers", "products", "orders", "order_items", "shipments", "payments", "refunds",
    "returns", "replacements", "inventory", "cases", "actions", "approvals", "audit_log",
    "outbox", "policy_docs", "policy_chunks",
}  # fmt: skip
CHILD_TABLES = {
    "action_items", "action_policy_references", "action_triggered_rules", "refund_items",
    "return_items", "replacement_items",
}  # fmt: skip

_CREATE_TABLE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\)", re.DOTALL)
_CREATE_INDEX = re.compile(r"CREATE (?:UNIQUE )?INDEX \w+ ON .*")


def _offline_sql(direction: str, revisions: str) -> str:
    buffer = io.StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=buffer)
    config.attributes["configure_logger"] = False
    getattr(command, direction)(config, revisions, sql=True)
    return buffer.getvalue()


def _table_parts(sql: str) -> dict[str, set[str]]:
    return {
        name: {part.strip() for part in re.split(r",\s*\n", body)}
        for name, body in _CREATE_TABLE.findall(sql)
    }


def _indexes(sql: str) -> set[str]:
    return {match.group(0).rstrip(";").strip() for match in _CREATE_INDEX.finditer(sql)}


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _offline_sql("upgrade", f"{PHASE_0}:{PHASE_1}")


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return _offline_sql("downgrade", f"{PHASE_1}:{PHASE_0}")


def test_schema_defines_the_architecture_tables_plus_item_children() -> None:
    configure_mappers()

    assert set(Base.metadata.tables) == CORE_TABLES | CHILD_TABLES


def test_migration_ddl_matches_orm_metadata(upgrade_sql: str) -> None:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    expected_tables: dict[str, set[str]] = {}
    expected_indexes: set[str] = set()
    for table in Base.metadata.sorted_tables:
        expected_tables.update(_table_parts(str(CreateTable(table).compile(dialect=dialect))))
        expected_indexes |= {
            str(CreateIndex(index).compile(dialect=dialect)).strip() for index in table.indexes
        }

    actual_tables = _table_parts(upgrade_sql)

    assert set(actual_tables) == set(expected_tables)
    for name, parts in expected_tables.items():
        assert actual_tables[name] == parts, name
    assert _indexes(upgrade_sql) == expected_indexes


def test_money_columns_are_numeric_12_2_and_no_float_columns_exist() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, Float), f"{table.name}.{column.name}"
            if isinstance(column.type, Numeric):
                assert (column.type.precision, column.type.scale) == (12, 2), column.name


def test_all_timestamps_are_timezone_aware(upgrade_sql: str) -> None:
    assert "TIMESTAMP WITHOUT TIME ZONE" not in upgrade_sql
    assert re.search(r"\bTIMESTAMP\b(?! WITH TIME ZONE)", upgrade_sql) is None


def test_upgrade_creates_append_only_ledger_triggers(upgrade_sql: str) -> None:
    assert "CREATE FUNCTION reject_refund_ledger_mutation() RETURNS trigger" in upgrade_sql
    for table in ("refunds", "refund_items"):
        assert (
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_refund_ledger_mutation()"
        ) in upgrade_sql
    assert "TRUNCATE" not in upgrade_sql
    assert "integrity_constraint_violation" in upgrade_sql


def test_upgrade_adds_no_seed_data_or_retrieval_columns(upgrade_sql: str) -> None:
    assert "INSERT INTO" not in upgrade_sql.replace("INSERT INTO alembic_version", "")
    assert re.search(r"\b(VECTOR|TSVECTOR|HALFVEC)\b", upgrade_sql.upper()) is None
    assert "USING HNSW" not in upgrade_sql.upper()
    assert "USING IVFFLAT" not in upgrade_sql.upper()


def test_downgrade_drops_everything_in_reverse_and_keeps_the_vector_extension(
    downgrade_sql: str, upgrade_sql: str
) -> None:
    created = [name for name, _ in _CREATE_TABLE.findall(upgrade_sql)]
    dropped = re.findall(r"DROP TABLE (\w+);", downgrade_sql)

    assert dropped == list(reversed(created))
    assert "DROP EXTENSION" not in downgrade_sql.upper()
    assert "DROP FUNCTION IF EXISTS reject_refund_ledger_mutation()" in downgrade_sql
    first_drop_table = downgrade_sql.index("DROP TABLE")
    for table in ("refunds", "refund_items"):
        trigger = f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}"
        assert downgrade_sql.index(trigger) < first_drop_table
    assert f"version_num='{PHASE_0}'" in downgrade_sql
