"""Integration-test database fixtures.

The only database these tests may touch is the one resolved from ``TEST_DATABASE_URL``
by ``resolve_test_database_url``, which never falls back to ``DATABASE_URL`` and refuses
a URL that identifies the same logical database. Destructive setup (truncate/drop/reset)
must additionally go through ``require_destructive_test_reset``; Phase 0 performs none.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from support_ops.config import DatabaseConfigError, get_settings, resolve_test_database_url
from support_ops.db.session import create_db_engine

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def alembic_head_revision() -> str | None:
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    try:
        url = resolve_test_database_url(get_settings())
    except DatabaseConfigError as exc:
        pytest.fail(str(exc), pytrace=False)

    engine = create_db_engine(url)
    try:
        try:
            with engine.begin() as connection:
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        except SQLAlchemyError as exc:
            # Driver messages can include the host; report only the failure type.
            pytest.fail(
                f"Could not migrate the TEST_DATABASE_URL database ({type(exc).__name__}).",
                pytrace=False,
            )
        yield engine
    finally:
        engine.dispose()
