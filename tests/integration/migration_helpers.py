"""Alembic helpers shared by the integration fixtures and migration tests."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_0_REVISION = "0001_enable_pgvector"
PHASE_1_REVISION = "0002_create_domain_schema"


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = False
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def migrate(engine: Engine, direction: str, revision: str) -> None:
    """Run an Alembic upgrade/downgrade against ``engine`` in one transaction."""
    with engine.begin() as connection:
        getattr(command, direction)(alembic_config(connection), revision)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
