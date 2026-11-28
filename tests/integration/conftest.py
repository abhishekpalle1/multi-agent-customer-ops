"""Integration-test database fixtures.

The only database these tests may touch is the one resolved from ``TEST_DATABASE_URL``
by ``resolve_test_database_url``, which never falls back to ``DATABASE_URL`` and refuses
a URL that identifies the same logical database. Destructive setup (truncate/drop/reset,
migration downgrades) must additionally go through ``require_destructive_test_reset``; use
the ``destructive_reset_allowed`` fixture, which skips with a visible reason otherwise.

Every test session holds a *shared* PostgreSQL advisory lock on the test database for its
whole run. Destructive tests take the same lock *exclusively* (``exclusive_test_database``),
so they never run while another test session is using the shared test database.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

import pytest
from alembic.script import ScriptDirectory
from migration_helpers import alembic_config, migrate
from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from support_ops.config import (
    DatabaseConfigError,
    UnsafeTestDatabaseError,
    get_settings,
    require_destructive_test_reset,
    resolve_test_database_url,
)
from support_ops.db.locks import TEST_DATABASE_LOCK_KEY
from support_ops.db.session import create_db_engine

LOCK_TIMEOUT: Final = "120s"


@dataclass(frozen=True)
class _TestDatabase:
    engine: Engine
    lock_connection: Connection


@pytest.fixture(scope="session")
def alembic_head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


@pytest.fixture(scope="session")
def test_database_session() -> Iterator[_TestDatabase]:
    try:
        url = resolve_test_database_url(get_settings())
    except DatabaseConfigError as exc:
        pytest.fail(str(exc), pytrace=False)

    engine = create_db_engine(url)
    try:
        lock_connection = _acquire_shared_lock(engine)
        try:
            migrate(engine, "upgrade", "head")
        except SQLAlchemyError as exc:
            # Driver messages can include the host; report only the failure type.
            pytest.fail(
                f"Could not migrate the TEST_DATABASE_URL database ({type(exc).__name__}).",
                pytrace=False,
            )
        try:
            yield _TestDatabase(engine=engine, lock_connection=lock_connection)
        finally:
            lock_connection.close()
    finally:
        engine.dispose()


def _acquire_shared_lock(engine: Engine) -> Connection:
    try:
        connection = engine.connect()
    except SQLAlchemyError as exc:
        pytest.fail(
            f"Could not connect to the TEST_DATABASE_URL database ({type(exc).__name__}).",
            pytrace=False,
        )
    try:
        connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))
        connection.execute(select(func.pg_advisory_lock_shared(TEST_DATABASE_LOCK_KEY)))
        connection.commit()
    except OperationalError as exc:
        connection.close()
        pytest.fail(
            "Timed out waiting for the shared test-database lock; another session may be "
            f"running a destructive test ({type(exc).__name__}).",
            pytrace=False,
        )
    return connection


@pytest.fixture(scope="session")
def test_engine(test_database_session: _TestDatabase) -> Engine:
    return test_database_session.engine


@pytest.fixture
def destructive_reset_allowed() -> None:
    """Skip unless every destructive-test guard passes (APP_ENV=test, explicit opt-in,
    non-empty TEST_DATABASE_URL that differs from DATABASE_URL)."""
    try:
        require_destructive_test_reset(get_settings())
    except UnsafeTestDatabaseError as exc:
        pytest.skip(f"Destructive test-database test skipped: {exc}")


@pytest.fixture
def exclusive_test_database(
    test_database_session: _TestDatabase, destructive_reset_allowed: None
) -> Iterator[Engine]:
    """Hold the test-database lock exclusively, waiting for other sessions' shared locks."""
    connection = test_database_session.lock_connection
    connection.execute(select(func.pg_advisory_unlock_shared(TEST_DATABASE_LOCK_KEY)))
    try:
        connection.execute(select(func.pg_advisory_lock(TEST_DATABASE_LOCK_KEY)))
        connection.commit()
    except OperationalError as exc:
        connection.rollback()
        connection.execute(select(func.pg_advisory_lock_shared(TEST_DATABASE_LOCK_KEY)))
        connection.commit()
        pytest.fail(
            "Timed out waiting for exclusive use of the shared test database "
            f"({type(exc).__name__}).",
            pytrace=False,
        )
    try:
        yield test_database_session.engine
    finally:
        connection.execute(select(func.pg_advisory_unlock(TEST_DATABASE_LOCK_KEY)))
        connection.execute(select(func.pg_advisory_lock_shared(TEST_DATABASE_LOCK_KEY)))
        connection.commit()
