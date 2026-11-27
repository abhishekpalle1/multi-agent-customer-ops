"""SQLAlchemy engine and session infrastructure for PostgreSQL (psycopg 3)."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from support_ops.config import get_settings

CONNECT_TIMEOUT_SECONDS = 5


def create_db_engine(url: URL) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the application engine for ``DATABASE_URL``, created on first use."""
    return create_db_engine(get_settings().sqlalchemy_database_url())


def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_engine.cache_clear()
