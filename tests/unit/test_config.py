import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from support_ops.config import (
    DEFAULT_GEMINI_OPENAI_BASE_URL,
    DatabaseConfigError,
    Settings,
    UnsafeDevelopmentDatabaseError,
    UnsafeTestDatabaseError,
    require_destructive_dev_reset,
    require_destructive_test_reset,
    resolve_development_database_url,
    resolve_test_database_url,
)

DEV_URL = "postgresql://postgres.devref:dev-pw@pooler.example.test:5432/postgres?sslmode=require"
TEST_URL = "postgresql://postgres.testref:test-pw@pooler.example.test:5432/postgres?sslmode=require"


def make_settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DEV_URL)
    monkeypatch.setenv("TEST_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("AGENT_MODEL", "agent-model-id")
    monkeypatch.setenv("JUDGE_MODEL", "judge-model-id")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-model-id")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-value")
    monkeypatch.setenv("GEMINI_OPENAI_BASE_URL", "https://provider.example.test/v1/")

    settings = Settings(_env_file=None)

    assert settings.agent_model == "agent-model-id"
    assert settings.judge_model == "judge-model-id"
    assert settings.embedding_model == "embedding-model-id"
    assert settings.gemini_openai_base_url == "https://provider.example.test/v1/"
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "gemini-key-value"
    rendered = f"{settings!r} {settings!s}"
    for secret in ("dev-pw", "test-pw", "devref", "gemini-key-value"):
        assert secret not in rendered


def test_blank_values_are_unset_and_base_url_has_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "AGENT_MODEL", "EMBEDDING_MODEL", "GEMINI_API_KEY"):
        monkeypatch.setenv(name, "")

    settings = Settings(_env_file=None)

    assert settings.database_url is None
    assert settings.agent_model is None
    assert settings.embedding_model is None
    assert settings.gemini_api_key is None
    assert settings.gemini_openai_base_url == DEFAULT_GEMINI_OPENAI_BASE_URL
    assert settings.app_env == "development"
    assert settings.allow_test_database_reset is False


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@db.example.test:5432/app",
        "postgresql://u:p@db.example.test:5432/app",
        "postgresql+psycopg://u:p@db.example.test:5432/app",
    ],
)
def test_database_url_uses_psycopg_driver(raw: str) -> None:
    url = make_settings(database_url=raw).sqlalchemy_database_url()

    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database) == ("db.example.test", 5432, "app")


def test_database_url_preserves_sslmode() -> None:
    url = make_settings(database_url=DEV_URL).sqlalchemy_database_url()

    assert url.query["sslmode"] == "require"


@pytest.mark.parametrize(
    "raw",
    ["mysql://user:leaky-pw@db.example.test/app", "not a url leaky-pw"],
)
def test_invalid_database_url_error_does_not_echo_url(raw: str) -> None:
    with pytest.raises(DatabaseConfigError) as exc_info:
        make_settings(database_url=raw).sqlalchemy_database_url()

    assert "leaky-pw" not in str(exc_info.value)
    assert "DATABASE_URL" in str(exc_info.value)


def test_missing_database_url_is_reported() -> None:
    with pytest.raises(DatabaseConfigError, match="DATABASE_URL is not set"):
        make_settings().sqlalchemy_database_url()


# --- Integration-test database guard -------------------------------------------------


def test_test_database_url_required_and_never_falls_back_to_dev() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="TEST_DATABASE_URL is not set"):
        resolve_test_database_url(make_settings(database_url=DEV_URL))


@pytest.mark.parametrize(
    "test_url",
    [
        DEV_URL,
        "postgresql+psycopg://postgres.devref:dev-pw@pooler.example.test:5432/postgres",
        "postgres://postgres.devref:other-pw@POOLER.EXAMPLE.TEST/postgres?sslmode=require",
        "postgresql://other_role.DEVREF:x@pooler.example.test:5432/postgres",
    ],
    ids=["identical", "driver-and-query-differ", "scheme-case-default-port", "role-differs"],
)
def test_same_logical_database_is_refused(test_url: str) -> None:
    settings = make_settings(database_url=DEV_URL, test_database_url=test_url)

    with pytest.raises(UnsafeTestDatabaseError, match="same logical database") as exc_info:
        resolve_test_database_url(settings)

    message = str(exc_info.value)
    for fragment in ("pooler.example.test", "devref", "dev-pw", "other-pw"):
        assert fragment not in message.lower()


def test_plain_postgres_roles_on_same_database_are_refused() -> None:
    settings = make_settings(
        database_url="postgresql://app:pw@db.example.test:5432/support_ops",
        test_database_url="postgresql://admin:pw@db.example.test:5432/support_ops",
    )

    with pytest.raises(UnsafeTestDatabaseError):
        resolve_test_database_url(settings)


@pytest.mark.parametrize(
    ("dev_url", "test_url"),
    [
        (DEV_URL, TEST_URL),
        (
            "postgresql://postgres:postgres@localhost:5432/support_ops",
            "postgresql://postgres:postgres@localhost:5432/support_ops_test",
        ),
        (
            "postgresql://u:p@dev.example.test:5432/app",
            "postgresql://u:p@test.example.test:5432/app",
        ),
        (
            "postgresql://u:p@db.example.test:5432/app",
            "postgresql://u:p@db.example.test:6543/app",
        ),
    ],
    ids=["pooler-tenant", "database-name", "host", "port"],
)
def test_distinct_test_database_is_accepted(dev_url: str, test_url: str) -> None:
    settings = make_settings(database_url=dev_url, test_database_url=test_url)

    resolved = resolve_test_database_url(settings)

    expected = make_url(test_url)
    assert resolved.drivername == "postgresql+psycopg"
    assert (resolved.host, resolved.port, resolved.database, resolved.username) == (
        expected.host,
        expected.port,
        expected.database,
        expected.username,
    )


def test_test_database_accepted_when_dev_database_unset() -> None:
    resolved = resolve_test_database_url(make_settings(test_database_url=TEST_URL))

    assert resolved.username == "postgres.testref"


def _reset_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": DEV_URL,
        "test_database_url": TEST_URL,
        "app_env": "test",
        "allow_test_database_reset": True,
    }
    values.update(overrides)
    return make_settings(**values)


def test_destructive_reset_allowed_when_all_conditions_hold() -> None:
    resolved = require_destructive_test_reset(_reset_settings())

    assert resolved.username == "postgres.testref"


def test_destructive_reset_conditions_parse_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ALLOW_TEST_DATABASE_RESET", "true")

    resolved = require_destructive_test_reset(Settings(_env_file=None))

    assert resolved.username == "postgres.testref"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"app_env": "development"}, "APP_ENV=test"),
        ({"app_env": "production"}, "APP_ENV=test"),
        ({"allow_test_database_reset": False}, "ALLOW_TEST_DATABASE_RESET=true"),
        ({"test_database_url": None}, "TEST_DATABASE_URL is not set"),
        ({"test_database_url": SecretStr("")}, "TEST_DATABASE_URL"),
        ({"test_database_url": DEV_URL}, "same logical database"),
    ],
    ids=[
        "app-env-development",
        "app-env-production",
        "reset-not-allowed",
        "test-url-unset",
        "test-url-empty",
        "test-url-is-dev",
    ],
)
def test_destructive_reset_refused(overrides: dict[str, object], expected: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError, match=expected):
        require_destructive_test_reset(_reset_settings(**overrides))


def _dev_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": DEV_URL,
        "test_database_url": TEST_URL,
        "app_env": "development",
        "allow_dev_database_reset": True,
    }
    values.update(overrides)
    return make_settings(**values)


def test_destructive_dev_reset_allowed_when_all_conditions_hold() -> None:
    assert require_destructive_dev_reset(_dev_settings()).username == "postgres.devref"


def test_destructive_dev_reset_allowed_without_a_test_database_configured() -> None:
    resolved = require_destructive_dev_reset(_dev_settings(test_database_url=None))

    assert resolved.username == "postgres.devref"


def test_destructive_dev_reset_conditions_parse_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DEV_URL)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_DEV_DATABASE_RESET", "true")

    assert require_destructive_dev_reset(Settings(_env_file=None)).username == "postgres.devref"


@pytest.mark.parametrize(
    ("overrides", "error", "expected"),
    [
        ({"app_env": "test"}, UnsafeDevelopmentDatabaseError, "APP_ENV=development"),
        (
            {"allow_dev_database_reset": False},
            UnsafeDevelopmentDatabaseError,
            "ALLOW_DEV_DATABASE_RESET=true",
        ),
        ({"database_url": None}, DatabaseConfigError, "DATABASE_URL is not set"),
        ({"test_database_url": DEV_URL}, UnsafeDevelopmentDatabaseError, "same logical database"),
    ],
    ids=["app-env-test", "reset-not-allowed", "dev-url-unset", "dev-url-is-test"],
)
def test_destructive_dev_reset_refused(
    overrides: dict[str, object], error: type[Exception], expected: str
) -> None:
    with pytest.raises(error, match=expected):
        require_destructive_dev_reset(_dev_settings(**overrides))


def test_development_writes_require_app_env_development() -> None:
    with pytest.raises(UnsafeDevelopmentDatabaseError, match="APP_ENV=development"):
        resolve_development_database_url(_dev_settings(app_env="test"))
