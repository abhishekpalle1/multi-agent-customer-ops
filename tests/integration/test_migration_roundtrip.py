"""Destructive migration round trip: head -> 0001_enable_pgvector -> head.

Runs only when every destructive-test guard passes (APP_ENV=test,
ALLOW_TEST_DATABASE_RESET=true, a non-empty TEST_DATABASE_URL distinct from DATABASE_URL);
otherwise it is skipped with a visible reason. It holds the test-database advisory lock
exclusively, so it never runs while another test session uses the shared test database, and
it always restores Phase 1 head before releasing the lock.
"""

import pytest
from migration_helpers import PHASE_0_REVISION, PHASE_1_REVISION, current_revision, migrate
from sqlalchemy import Engine, text

from support_ops.db.schema import Base

pytestmark = pytest.mark.destructive_db

DOMAIN_TABLES = sorted(Base.metadata.tables)


def _existing_domain_tables(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ANY(:tables)"
            ),
            {"tables": DOMAIN_TABLES},
        )
        return set(rows.scalars())


def _exists(engine: Engine, sql: str) -> bool:
    with engine.connect() as connection:
        return bool(connection.execute(text(sql)).scalar_one())


def _vector_installed(engine: Engine) -> bool:
    return _exists(engine, "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")


def _ledger_function_exists(engine: Engine) -> bool:
    return _exists(
        engine,
        "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'reject_refund_ledger_mutation')",
    )


def _ledger_trigger_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname IN "
                    "('trg_refunds_append_only', 'trg_refund_items_append_only')"
                )
            ).scalar_one()
        )


def test_downgrade_to_phase_0_and_restore_phase_1_head(
    exclusive_test_database: Engine, alembic_head_revision: str | None
) -> None:
    engine = exclusive_test_database
    assert alembic_head_revision == PHASE_1_REVISION
    assert current_revision(engine) == PHASE_1_REVISION
    assert _existing_domain_tables(engine) == set(DOMAIN_TABLES)

    try:
        migrate(engine, "downgrade", PHASE_0_REVISION)

        assert current_revision(engine) == PHASE_0_REVISION
        assert _existing_domain_tables(engine) == set()
        assert not _ledger_function_exists(engine)
        assert _vector_installed(engine)
    finally:
        migrate(engine, "upgrade", "head")

    assert current_revision(engine) == PHASE_1_REVISION
    assert _existing_domain_tables(engine) == set(DOMAIN_TABLES)
    assert _ledger_function_exists(engine)
    assert _ledger_trigger_count(engine) == 2
    assert _vector_installed(engine)
