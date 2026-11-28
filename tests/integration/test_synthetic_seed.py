"""Synthetic seed/reset against the TEST_DATABASE_URL database only.

Non-destructive tests seed inside a transaction that is always rolled back, so they never
change the shared test database. Destructive tests (``destructive_db``) run only when every
destructive-test guard passes, hold the test-database advisory lock exclusively, and require
a clean baseline: schema at ``0002_create_domain_schema`` and every Phase-2-owned business /
transactional table empty. If those tables already hold data (for example a manually seeded
test dataset), the destructive tests are skipped with the reason shown rather than deleting
it. After each destructive test, a ``finally`` block restores that baseline (Phase-2-owned
tables truncated, schema at head) and leaves ``policy_docs``/``policy_chunks`` untouched.
"""

import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from migration_helpers import current_revision
from sqlalchemy import Connection, Engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from support_ops.config import UnsafeTestDatabaseError, get_settings, resolve_test_database_url
from support_ops.db.schema import Base, Customer, PolicyDoc
from support_ops.db.session import create_db_engine
from support_ops.synthetic import seeding
from support_ops.synthetic.dataset import PreparedDataset, prepare_dataset
from support_ops.synthetic.dataset_config import REQUIRED_SCHEMA_REVISION
from support_ops.synthetic.reconcile import (
    persisted_fingerprints,
    persisted_row_counts,
    reconcile,
)
from support_ops.synthetic.records import RefundItemRecord, table_fingerprints
from support_ops.synthetic.seeding import (
    PRESERVED_TABLES,
    SeedError,
    SeedOutcome,
    SeedTarget,
    owned_table_counts,
    reset_and_seed_dataset,
    reset_owned_tables,
    run_seed,
    seed_dataset,
    seed_engine,
)

LEDGER_MUTATIONS = (
    "UPDATE refunds SET amount = amount WHERE refund_id = 'REF-CANCELLED-001-1'",
    "DELETE FROM refunds WHERE refund_id = 'REF-CANCELLED-001-1'",
    "UPDATE refund_items SET quantity = quantity WHERE refund_id = 'REF-CANCELLED-001-1'",
    "DELETE FROM refund_items WHERE refund_id = 'REF-CANCELLED-001-1'",
)


@pytest.fixture(scope="module")
def prepared() -> PreparedDataset:
    return prepare_dataset()


def _owned_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return owned_table_counts(connection)


def _policy_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(
                connection.execute(
                    select(func.count()).select_from(Base.metadata.tables[table])
                ).scalar_one()
            )
            for table in PRESERVED_TABLES
        }


def _assert_representative_golden_facts(connection: Connection) -> None:
    def one(sql: str) -> tuple[object, ...]:
        return tuple(connection.execute(text(sql)).one())

    assert one(
        "SELECT o.customer_id, o.status, s.delivered_at FROM orders o "
        "JOIN shipments s ON s.order_id = o.order_id WHERE o.order_id = 'ORD-DAMAGED-008'"
    ) == ("CUST-10", "delivered", datetime(2026, 10, 24, 16, 0, tzinfo=UTC))
    assert one("SELECT customer_id FROM orders WHERE order_id = 'ORD-OTHER-CUSTOMER'") == (
        "CUST-20",
    )
    assert one(
        "SELECT available_quantity FROM inventory WHERE product_id = 'SKU-GOLDEN-DESK-LAMP'"
    ) == (0,)
    assert one(
        "SELECT is_returnable FROM products WHERE product_id = 'SKU-GOLDEN-CLEARANCE-VASE'"
    ) == (False,)
    assert one(
        "SELECT refunded_quantity, refunded_amount FROM order_items "
        "WHERE order_item_id = 'ITEM-ADJUSTED-001-1'"
    ) == (0, Decimal("30.00"))
    assert one(
        "SELECT status, amount, amount_refunded FROM payments WHERE order_id = 'ORD-CANCELLED-001'"
    ) == ("refunded", Decimal("60.45"), Decimal("60.45"))
    assert one(
        "SELECT unit_price, discount_amount, tax_amount FROM order_items "
        "WHERE order_item_id = 'ITEM-AMOUNT-150-1'"
    ) == (Decimal("155.00"), Decimal("15.00"), Decimal("10.00"))
    assert one(
        "SELECT count(*) FROM shipments WHERE order_id IN ('ORD-PLACED-001', 'ORD-CANCELLED-001')"
    ) == (0,)
    assert one("SELECT count(*) FROM cases") == (0,)


def _assert_ledger_is_immutable(connection: Connection) -> None:
    for statement in LEDGER_MUTATIONS:
        with pytest.raises(IntegrityError) as caught, connection.begin_nested():
            connection.execute(text(statement))
        assert getattr(caught.value.orig, "sqlstate", None) == "23000"


# --- Non-destructive -------------------------------------------------------------------


def test_schema_is_at_the_required_revision(test_engine: Engine) -> None:
    assert current_revision(test_engine) == REQUIRED_SCHEMA_REVISION


def test_seed_inside_a_rolled_back_transaction(
    test_engine: Engine, prepared: PreparedDataset
) -> None:
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            counts = owned_table_counts(connection)
            if any(counts.values()):
                if seed_dataset(connection, prepared) is not SeedOutcome.ALREADY_PRESENT:
                    pytest.fail("seed_dataset must not insert into a non-empty database")
                reconcile(connection, prepared.dataset, prepared.manifest)
                return

            assert seed_dataset(connection, prepared) is SeedOutcome.INSERTED
            report = reconcile(connection, prepared.dataset, prepared.manifest)
            assert report.scenarios_checked == 30
            assert report.row_counts == prepared.manifest.row_counts

            assert seed_dataset(connection, prepared) is SeedOutcome.ALREADY_PRESENT
            assert persisted_row_counts(connection) == prepared.manifest.row_counts
            _assert_representative_golden_facts(connection)
            _assert_ledger_is_immutable(connection)
            reconcile(connection, prepared.dataset, prepared.manifest)
        except SeedError as exc:
            pytest.skip(f"Test database holds other synthetic data: {exc}")
        finally:
            transaction.rollback()


def test_reset_without_the_destructive_guard_is_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("no engine may be created when the guard refuses")

    monkeypatch.setattr(seeding, "create_db_engine", forbidden)
    settings = get_settings().model_copy(
        update={"app_env": "test", "allow_test_database_reset": False}
    )

    with pytest.raises(UnsafeTestDatabaseError, match="ALLOW_TEST_DATABASE_RESET=true"):
        run_seed(SeedTarget.TEST, reset=True, settings=settings, emit=lambda _: None)


# --- Destructive -----------------------------------------------------------------------


@pytest.fixture
def clean_baseline(exclusive_test_database: Engine) -> Iterator[Engine]:
    """Require (never create) the clean baseline, then always restore it afterwards."""
    engine = exclusive_test_database
    revision = current_revision(engine)
    if revision != REQUIRED_SCHEMA_REVISION:
        pytest.skip(
            f"Destructive synthetic-data tests need schema {REQUIRED_SCHEMA_REVISION}; "
            f"found {revision!r}."
        )
    occupied = sorted(table for table, rows in _owned_counts(engine).items() if rows)
    if occupied:
        pytest.skip(
            "Destructive synthetic-data tests need empty Phase-2-owned tables; found data in "
            f"{occupied}. Refusing to delete an existing (possibly manually seeded) test "
            "dataset; run reset-seed-test or empty it explicitly first."
        )
    policy_before = _policy_counts(engine)
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            reset_owned_tables(connection)
        assert current_revision(engine) == REQUIRED_SCHEMA_REVISION
        assert not any(_owned_counts(engine).values())
        assert _policy_counts(engine) == policy_before


@pytest.mark.destructive_db
def test_committed_seed_is_reconciled_and_visible_to_an_independent_connection(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    assert seed_engine(clean_baseline, prepared, reset=False, lock_key=None) is (
        SeedOutcome.INSERTED
    )

    independent = create_db_engine(resolve_test_database_url(get_settings()))
    try:
        with independent.connect() as connection:
            assert persisted_row_counts(connection) == prepared.manifest.row_counts
            report = reconcile(connection, prepared.dataset, prepared.manifest)
            _assert_representative_golden_facts(connection)
    finally:
        independent.dispose()
    assert report.fingerprint == prepared.manifest.dataset_fingerprint


@pytest.mark.destructive_db
def test_plain_reseed_is_an_idempotent_no_op(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    seed_engine(clean_baseline, prepared, reset=False, lock_key=None)
    with clean_baseline.connect() as connection:
        before = persisted_fingerprints(connection, prepared.dataset)

    outcome = seed_engine(clean_baseline, prepared, reset=False, lock_key=None)

    assert outcome is SeedOutcome.ALREADY_PRESENT
    with clean_baseline.connect() as connection:
        assert persisted_fingerprints(connection, prepared.dataset) == before
        assert persisted_row_counts(connection) == prepared.manifest.row_counts


@pytest.mark.destructive_db
def test_reset_and_reseed_reproduces_the_identical_dataset(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    seed_engine(clean_baseline, prepared, reset=False, lock_key=None)
    with clean_baseline.connect() as connection:
        first = persisted_fingerprints(connection, prepared.dataset)

    outcome = seed_engine(clean_baseline, prepared, reset=True, lock_key=None)

    assert outcome is SeedOutcome.RESET_AND_INSERTED
    with clean_baseline.connect() as connection:
        second = persisted_fingerprints(connection, prepared.dataset)
    assert first == second == table_fingerprints(prepared.dataset)


@pytest.mark.destructive_db
def test_plain_seed_refuses_a_database_holding_other_data(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    seed_engine(clean_baseline, prepared, reset=False, lock_key=None)
    with clean_baseline.begin() as connection:
        connection.execute(
            insert(Customer).values(
                customer_id="CUST-ITEST-EXTRA",
                email="extra@itest.invalid",
                full_name="Extra",
            )
        )
    before = _owned_counts(clean_baseline)

    with pytest.raises(SeedError, match="refusing to modify it"):
        seed_engine(clean_baseline, prepared, reset=False, lock_key=None)

    assert _owned_counts(clean_baseline) == before


def _corrupted(prepared: PreparedDataset) -> PreparedDataset:
    """Valid rows except one refund line (inserted last) whose refund does not exist."""
    dataset = prepared.dataset
    item = dataset.order_items[0]
    orphan = RefundItemRecord(
        refund_id="REF-DOES-NOT-EXIST",
        order_item_id=item.order_item_id,
        order_id=item.order_id,
        quantity=1,
        amount=Decimal("1.00"),
        created_at=dataset.anchor,
    )
    return PreparedDataset(
        dataset=dataclasses.replace(dataset, refund_items=(*dataset.refund_items, orphan)),
        manifest=prepared.manifest,
    )


@pytest.mark.destructive_db
def test_failed_seed_rolls_back_atomically(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    with pytest.raises(IntegrityError):
        seed_engine(clean_baseline, _corrupted(prepared), reset=False, lock_key=None)

    assert not any(_owned_counts(clean_baseline).values())


@pytest.mark.destructive_db
def test_failed_reset_seed_keeps_the_previous_dataset(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    seed_engine(clean_baseline, prepared, reset=False, lock_key=None)

    with pytest.raises(IntegrityError):
        seed_engine(clean_baseline, _corrupted(prepared), reset=True, lock_key=None)

    with clean_baseline.connect() as connection:
        assert persisted_fingerprints(connection, prepared.dataset) == table_fingerprints(
            prepared.dataset
        )


@pytest.mark.destructive_db
def test_ledger_stays_append_only_after_a_committed_seed(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    seed_engine(clean_baseline, prepared, reset=False, lock_key=None)

    with clean_baseline.connect() as connection, connection.begin():
        _assert_ledger_is_immutable(connection)
    with clean_baseline.connect() as connection:
        reconcile(connection, prepared.dataset, prepared.manifest)


@pytest.mark.destructive_db
def test_reset_preserves_the_policy_corpus_tables(
    clean_baseline: Engine, prepared: PreparedDataset
) -> None:
    """Runs in a transaction that is rolled back, so the policy tables are never changed."""
    with clean_baseline.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                insert(PolicyDoc).values(
                    policy_doc_id="POLICY-ITEST-1",
                    policy_key="itest_policy",
                    version=1,
                    title="Integration test policy",
                    source_path="itest.md",
                    content_sha256="0" * 64,
                    effective_from=prepared.dataset.anchor,
                )
            )
            assert reset_and_seed_dataset(connection, prepared) is SeedOutcome.RESET_AND_INSERTED
            remaining = connection.execute(
                select(func.count())
                .select_from(PolicyDoc)
                .where(PolicyDoc.policy_doc_id == "POLICY-ITEST-1")
            ).scalar_one()
            assert remaining == 1
        finally:
            transaction.rollback()
