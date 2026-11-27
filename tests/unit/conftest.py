import pytest

CONFIG_ENV_VARS = (
    "APP_ENV",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "ALLOW_TEST_DATABASE_RESET",
    "AGENT_MODEL",
    "JUDGE_MODEL",
    "EMBEDDING_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_OPENAI_BASE_URL",
)


@pytest.fixture(autouse=True)
def _isolated_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never see the process environment's (or CI's) configuration values."""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
