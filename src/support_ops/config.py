"""Central application configuration.

This module is the only place that reads ``DATABASE_URL`` and ``TEST_DATABASE_URL``.
Connection URLs are held as ``SecretStr`` and error messages never echo them.
"""

from functools import lru_cache
from typing import Any

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

DEFAULT_GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_PSYCOPG_DRIVERNAME = "postgresql+psycopg"
_POSTGRES_DRIVERNAMES = frozenset({"postgres", "postgresql", _PSYCOPG_DRIVERNAME})
_DEFAULT_POSTGRES_PORT = 5432


class DatabaseConfigError(RuntimeError):
    """Database configuration is missing or invalid."""


class UnsafeTestDatabaseError(DatabaseConfigError):
    """The test database configuration could target a non-test database."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: str = "development"
    database_url: SecretStr | None = None
    test_database_url: SecretStr | None = None
    allow_test_database_reset: bool = False

    agent_model: str | None = None
    judge_model: str | None = None
    embedding_model: str | None = None
    gemini_api_key: SecretStr | None = None
    gemini_openai_base_url: str = DEFAULT_GEMINI_OPENAI_BASE_URL

    @field_validator(
        "database_url",
        "test_database_url",
        "agent_model",
        "judge_model",
        "embedding_model",
        "gemini_api_key",
        mode="before",
    )
    @classmethod
    def _blank_as_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def sqlalchemy_database_url(self) -> URL:
        """Return the development/application database URL for SQLAlchemy + psycopg."""
        if self.database_url is None:
            raise DatabaseConfigError("DATABASE_URL is not set.")
        return to_sqlalchemy_url(self.database_url.get_secret_value(), env_var="DATABASE_URL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def to_sqlalchemy_url(raw_url: str, *, env_var: str) -> URL:
    """Parse a PostgreSQL URL and select the psycopg 3 driver."""
    try:
        url = make_url(raw_url)
    except (ArgumentError, ValueError):
        raise DatabaseConfigError(f"{env_var} is not a valid PostgreSQL connection URL.") from None
    if url.drivername not in _POSTGRES_DRIVERNAMES:
        raise DatabaseConfigError(f"{env_var} must be a postgresql:// connection URL.")
    return url.set(drivername=_PSYCOPG_DRIVERNAME)


def database_identity(url: URL) -> tuple[str, int, str, str]:
    """Return a normalized identity for the logical database a URL connects to.

    Usernames are ignored except for a pooler tenant suffix (``postgres.<project-ref>``),
    which is how a shared Supabase pooler host distinguishes separate projects. Two URLs
    that differ only in role or password therefore resolve to the same database.
    """
    username = url.username or ""
    tenant = username.split(".", 1)[1].lower() if "." in username else ""
    return (
        (url.host or "localhost").lower(),
        url.port or _DEFAULT_POSTGRES_PORT,
        url.database or username,
        tenant,
    )


def resolve_test_database_url(settings: Settings) -> URL:
    """Return the integration-test database URL; never falls back to ``DATABASE_URL``."""
    if settings.test_database_url is None or not settings.test_database_url.get_secret_value():
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL is not set. Integration tests never fall back to DATABASE_URL."
        )
    test_url = to_sqlalchemy_url(
        settings.test_database_url.get_secret_value(), env_var="TEST_DATABASE_URL"
    )
    if settings.database_url is not None:
        dev_url = to_sqlalchemy_url(
            settings.database_url.get_secret_value(), env_var="DATABASE_URL"
        )
        if database_identity(test_url) == database_identity(dev_url):
            raise UnsafeTestDatabaseError(
                "TEST_DATABASE_URL and DATABASE_URL identify the same logical database; "
                "refusing to use it for tests."
            )
    return test_url


def require_destructive_test_reset(settings: Settings) -> URL:
    """Return the test database URL only if destructive test setup is explicitly allowed."""
    test_url = resolve_test_database_url(settings)
    if settings.app_env != "test":
        raise UnsafeTestDatabaseError("Destructive test-database setup requires APP_ENV=test.")
    if not settings.allow_test_database_reset:
        raise UnsafeTestDatabaseError(
            "Destructive test-database setup requires ALLOW_TEST_DATABASE_RESET=true."
        )
    return test_url
