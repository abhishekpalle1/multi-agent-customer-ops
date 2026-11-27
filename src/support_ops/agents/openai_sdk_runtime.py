"""The only module that constructs LLM provider clients.

Gemini is reached through Google's OpenAI-compatible endpoint: a custom ``AsyncOpenAI``
client wrapped by the Agents SDK ``OpenAIChatCompletionsModel``. Model identifiers come
exclusively from configuration.
"""

from collections.abc import Sequence

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI

from support_ops.config import Settings


class ProviderConfigError(RuntimeError):
    """Provider configuration is missing or invalid."""


def configure_agents_sdk() -> None:
    # The SDK's default trace exporter targets OpenAI's platform; tracing is set up in Phase 6/7.
    set_tracing_disabled(True)


def build_gemini_client(settings: Settings) -> AsyncOpenAI:
    if settings.gemini_api_key is None:
        raise ProviderConfigError("GEMINI_API_KEY is not set.")
    return AsyncOpenAI(
        api_key=settings.gemini_api_key.get_secret_value(),
        base_url=settings.gemini_openai_base_url,
    )


def build_chat_model(
    settings: Settings,
    *,
    model: str | None = None,
    client: AsyncOpenAI | None = None,
) -> OpenAIChatCompletionsModel:
    """Wrap a configured Gemini model (``AGENT_MODEL`` unless overridden) for the Agents SDK."""
    model_id = model or settings.agent_model
    if not model_id:
        raise ProviderConfigError("AGENT_MODEL is not set.")
    return OpenAIChatCompletionsModel(
        model=model_id,
        openai_client=client or build_gemini_client(settings),
    )


async def create_embeddings(
    settings: Settings,
    texts: Sequence[str],
    *,
    client: AsyncOpenAI | None = None,
) -> list[list[float]]:
    """Embed ``texts`` with ``EMBEDDING_MODEL``; vectors are returned in input order."""
    if not settings.embedding_model:
        raise ProviderConfigError("EMBEDDING_MODEL is not set.")
    embeddings_client = client or build_gemini_client(settings)
    response = await embeddings_client.embeddings.create(
        model=settings.embedding_model,
        input=list(texts),
        encoding_format="float",
    )
    return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
