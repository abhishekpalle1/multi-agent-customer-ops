import pytest

from support_ops.agents.openai_sdk_runtime import (
    ProviderConfigError,
    build_chat_model,
    create_embeddings,
)
from support_ops.config import Settings


def make_settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)


def test_chat_model_uses_configured_model_and_endpoint() -> None:
    settings = make_settings(
        gemini_api_key="test-key",
        agent_model="configured-agent-model",
        gemini_openai_base_url="https://provider.example.test/v1beta/openai/",
    )

    model = build_chat_model(settings)

    assert model.model == "configured-agent-model"
    assert str(model._client.base_url) == "https://provider.example.test/v1beta/openai/"


def test_chat_model_requires_api_key_and_model() -> None:
    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY"):
        build_chat_model(make_settings(agent_model="configured-agent-model"))
    with pytest.raises(ProviderConfigError, match="AGENT_MODEL"):
        build_chat_model(make_settings(gemini_api_key="test-key"))


@pytest.mark.asyncio
async def test_embeddings_require_embedding_model() -> None:
    with pytest.raises(ProviderConfigError, match="EMBEDDING_MODEL"):
        await create_embeddings(make_settings(gemini_api_key="test-key"), ["text"])
