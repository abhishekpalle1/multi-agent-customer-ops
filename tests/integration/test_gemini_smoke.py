"""Phase 0 live smoke test for Gemini through the OpenAI Agents SDK.

Requires GEMINI_API_KEY, AGENT_MODEL, and EMBEDDING_MODEL. Skipped when GEMINI_API_KEY
is absent and excluded from PR CI (``-m "not live_llm"``).
"""

import secrets

import pytest
from agents import Agent, Runner, ToolCallItem, function_tool
from pydantic import BaseModel

from support_ops.agents.openai_sdk_runtime import (
    build_chat_model,
    configure_agents_sdk,
    create_embeddings,
)
from support_ops.config import Settings, get_settings

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(get_settings().gemini_api_key is None, reason="GEMINI_API_KEY is not set"),
]


class CityFact(BaseModel):
    city: str
    country: str


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("AGENT_MODEL", settings.agent_model),
            ("EMBEDDING_MODEL", settings.embedding_model),
        )
        if not value
    ]
    if missing:
        pytest.fail(f"Live Gemini smoke tests require: {', '.join(missing)}", pytrace=False)
    configure_agents_sdk()
    return settings


@pytest.mark.asyncio
async def test_basic_generation(live_settings: Settings) -> None:
    agent = Agent(
        name="smoke_generation",
        instructions="Reply with exactly one word: PONG",
        model=build_chat_model(live_settings),
    )

    result = await Runner.run(agent, "Ping.")

    assert isinstance(result.final_output, str)
    assert "PONG" in result.final_output.upper()


@pytest.mark.asyncio
async def test_structured_output(live_settings: Settings) -> None:
    agent = Agent(
        name="smoke_structured_output",
        instructions="Extract the city mentioned by the user and the country it is in.",
        model=build_chat_model(live_settings),
        output_type=CityFact,
    )

    result = await Runner.run(agent, "I just got back from visiting the Eiffel Tower in Paris.")

    fact = result.final_output_as(CityFact, raise_if_incorrect_type=True)
    assert fact.city.strip().lower() == "paris"
    assert "france" in fact.country.lower()


@pytest.mark.asyncio
async def test_function_tool_call(live_settings: Settings) -> None:
    # A random value proves the answer came from the tool rather than the model.
    expected_code = secrets.token_hex(4)
    calls: list[str] = []

    @function_tool
    def lookup_verification_code(label: str) -> str:
        """Return the verification code registered for a label.

        Args:
            label: The label whose verification code is requested.
        """
        calls.append(label)
        return expected_code

    agent = Agent(
        name="smoke_tool_call",
        instructions=(
            "You cannot know verification codes yourself. Always call "
            "lookup_verification_code, then reply with only the code it returns."
        ),
        model=build_chat_model(live_settings),
        tools=[lookup_verification_code],
    )

    result = await Runner.run(agent, "What is the verification code for the label 'smoke'?")

    assert calls, "the function tool was not executed"
    assert any(isinstance(item, ToolCallItem) for item in result.new_items)
    assert expected_code in str(result.final_output)


@pytest.mark.asyncio
async def test_embedding_request(live_settings: Settings) -> None:
    vectors = await create_embeddings(
        live_settings, ["Damaged items reported within 30 days are eligible for a refund."]
    )

    assert len(vectors) == 1
    vector = vectors[0]
    assert vector
    assert all(isinstance(value, float) for value in vector)
    # Observation only; the production embedding dimension is decided in Phase 4.
    print(f"embedding_model={live_settings.embedding_model} dimension={len(vector)}")
