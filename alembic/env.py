"""Alembic environment.

The database URL comes from application configuration (``DATABASE_URL``), never from
alembic.ini. Callers may instead pass an open connection via
``config.attributes["connection"]``; integration tests use this to migrate the database
resolved from ``TEST_DATABASE_URL``.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from support_ops.config import get_settings
from support_ops.db.session import CONNECT_TIMEOUT_SECONDS

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Phase 0 defines no ORM tables; later phases set this to the schema metadata.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if isinstance(supplied_connection, Connection):
        _run_migrations(supplied_connection)
        return

    engine = create_engine(
        get_settings().sqlalchemy_database_url(),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
    try:
        with engine.connect() as connection:
            _run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
