"""Seed/reset safety guards and the CLI surface (no database)."""

import pytest

from support_ops.config import (
    DatabaseConfigError,
    Settings,
    UnsafeDevelopmentDatabaseError,
    UnsafeTestDatabaseError,
)
from support_ops.db.schema import Base
from support_ops.synthetic import seeding
from support_ops.synthetic.cli import build_parser, main
from support_ops.synthetic.dataset_config import SEEDED_TABLES
from support_ops.synthetic.seeding import (
    PRESERVED_TABLES,
    RESET_TABLES,
    SeedTarget,
    describe_target,
    resolve_target_url,
    run_seed,
)

DEV_URL = "postgresql://postgres.devref:dev-secret-pw@pooler.example.test:5432/postgres"
TEST_URL = "postgresql://postgres.testref:test-secret-pw@pooler.example.test:5432/postgres"


def _settings(**values: object) -> Settings:
    base: dict[str, object] = {"database_url": DEV_URL, "test_database_url": TEST_URL}
    base.update(values)
    return Settings(_env_file=None, **base)


@pytest.fixture
def no_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything tries to build an engine or generate data."""

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("must not be reached when a guard refuses")

    monkeypatch.setattr(seeding, "create_db_engine", forbidden)
    monkeypatch.setattr(seeding, "prepare_dataset", forbidden)


# --- Target resolution -------------------------------------------------------------------


def test_development_seed_uses_database_url() -> None:
    url = resolve_target_url(
        SeedTarget.DEVELOPMENT, _settings(app_env="development"), destructive=False
    )

    assert url.username == "postgres.devref"


def test_test_seed_uses_test_database_url_with_app_env_test() -> None:
    url = resolve_target_url(SeedTarget.TEST, _settings(app_env="test"), destructive=False)

    assert url.username == "postgres.testref"


@pytest.mark.parametrize(
    ("target", "destructive", "overrides", "error", "message"),
    [
        (
            SeedTarget.DEVELOPMENT,
            False,
            {"app_env": "test"},
            UnsafeDevelopmentDatabaseError,
            "APP_ENV=development",
        ),
        (
            SeedTarget.DEVELOPMENT,
            False,
            {"app_env": "development", "test_database_url": DEV_URL},
            UnsafeDevelopmentDatabaseError,
            "same logical database",
        ),
        (
            SeedTarget.DEVELOPMENT,
            False,
            {"app_env": "development", "database_url": None},
            DatabaseConfigError,
            "DATABASE_URL is not set",
        ),
        (
            SeedTarget.DEVELOPMENT,
            True,
            {"app_env": "development"},
            UnsafeDevelopmentDatabaseError,
            "ALLOW_DEV_DATABASE_RESET=true",
        ),
        (
            SeedTarget.DEVELOPMENT,
            True,
            {"app_env": "test", "allow_dev_database_reset": True},
            UnsafeDevelopmentDatabaseError,
            "APP_ENV=development",
        ),
        (
            SeedTarget.TEST,
            False,
            {"app_env": "development"},
            UnsafeTestDatabaseError,
            "APP_ENV=test",
        ),
        (
            SeedTarget.TEST,
            False,
            {"app_env": "test", "test_database_url": None},
            UnsafeTestDatabaseError,
            "TEST_DATABASE_URL is not set",
        ),
        (
            SeedTarget.TEST,
            True,
            {"app_env": "test"},
            UnsafeTestDatabaseError,
            "ALLOW_TEST_DATABASE_RESET=true",
        ),
        (
            SeedTarget.TEST,
            True,
            {"app_env": "development", "allow_test_database_reset": True},
            UnsafeTestDatabaseError,
            "APP_ENV=test",
        ),
        (
            SeedTarget.TEST,
            True,
            {"app_env": "test", "allow_test_database_reset": True, "test_database_url": DEV_URL},
            UnsafeTestDatabaseError,
            "same logical database",
        ),
    ],
    ids=[
        "dev-seed-wrong-env",
        "dev-seed-same-as-test",
        "dev-seed-url-unset",
        "dev-reset-not-allowed",
        "dev-reset-wrong-env",
        "test-seed-wrong-env",
        "test-seed-url-unset",
        "test-reset-not-allowed",
        "test-reset-wrong-env",
        "test-reset-same-as-dev",
    ],
)
def test_guards_refuse_before_any_engine_or_generation(
    no_engine: None,
    target: SeedTarget,
    destructive: bool,
    overrides: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        run_seed(target, reset=destructive, settings=_settings(**overrides), emit=print)


def test_destructive_targets_resolve_only_with_explicit_opt_in() -> None:
    dev = resolve_target_url(
        SeedTarget.DEVELOPMENT,
        _settings(app_env="development", allow_dev_database_reset=True),
        destructive=True,
    )
    test = resolve_target_url(
        SeedTarget.TEST,
        _settings(app_env="test", allow_test_database_reset=True),
        destructive=True,
    )

    assert (dev.username, test.username) == ("postgres.devref", "postgres.testref")


def test_reset_flags_default_to_disabled() -> None:
    settings = Settings(_env_file=None)

    assert settings.allow_dev_database_reset is False
    assert settings.allow_test_database_reset is False


def test_sanitized_target_never_reveals_connection_details() -> None:
    url = resolve_target_url(SeedTarget.TEST, _settings(app_env="test"), destructive=False)
    described = describe_target(SeedTarget.TEST, url)

    assert described.startswith("target=test database=postgres identity=")
    for secret in ("pooler", "example.test", "testref", "test-secret-pw", "5432"):
        assert secret not in described
    assert described != describe_target(
        SeedTarget.DEVELOPMENT,
        resolve_target_url(SeedTarget.DEVELOPMENT, _settings(), destructive=False),
    )


# --- Reset scope -------------------------------------------------------------------------


def test_reset_scope_is_every_domain_table_except_the_policy_corpus() -> None:
    domain_tables = set(Base.metadata.tables)

    assert set(RESET_TABLES) | set(PRESERVED_TABLES) == domain_tables
    assert not set(RESET_TABLES) & set(PRESERVED_TABLES)
    assert len(RESET_TABLES) == 21
    assert set(SEEDED_TABLES) <= set(RESET_TABLES)


# --- CLI -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["seed"],
        ["seed", "--target", "production"],
        ["seed", "--target", "postgresql://user:pw@host/db"],
        ["reset-seed", "--target", "development", "--database-url", "postgresql://x"],
        ["seed", "--target", "test", "--url", "postgresql://x"],
    ],
)
def test_cli_accepts_only_named_targets(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_cli_reports_a_refused_guard_without_connection_details(
    no_engine: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TEST_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("DATABASE_URL", DEV_URL)
    monkeypatch.setattr(seeding, "get_settings", lambda: Settings(_env_file=None))

    assert main(["reset-seed", "--target", "test"]) == 1

    err = capsys.readouterr().err
    assert "ALLOW_TEST_DATABASE_RESET=true" in err
    for secret in ("pooler", "test-secret-pw", "testref"):
        assert secret not in err
