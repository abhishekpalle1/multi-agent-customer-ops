# Repository instructions: multi-agent-customer-ops

Source of truth: `docs/architecture.md`. Progress log: `docs/progress.md`.
Read both before any task. If a request conflicts with architecture.md, stop and ask.

## Non-negotiable rules
- Agents reason; deterministic Python orchestrates, authorizes, and executes.
- Never let an LLM supply `customer_id`; inject it from `AuthContext`.
- Money uses `Decimal`, never float.
- MUTATE tools accept only an authorized `action_id`; never amount/customer/order.
- Only `ActionAuthorizationService` authorizes; only `ActionExecutionService` executes.
- Provider clients (AsyncOpenAI, OpenAIChatCompletionsModel) are created only in
  `src/support_ops/agents/openai_sdk_runtime.py`. Use Gemini via its OpenAI-compatible
  endpoint. Do not hard-code `gpt-*` models.
- Do not add LiteLLM unless a later compatibility gap justifies it.
- Never commit secrets; read them from environment variables.

## Environment
- No Docker locally. Do not create docker-compose.yml or use testcontainers.
- Local dev DB: hosted Supabase PostgreSQL + pgvector via DATABASE_URL
  (session pooler, sslmode=require). Tests use TEST_DATABASE_URL (separate project).
- CI uses a `pgvector/pgvector:pg16` service container.

## Engineering conventions
- Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, PostgreSQL + pgvector.
- Full type hints; must pass `ruff` and `mypy --strict` on `src/`.
- Unit tests: no network, DB, or LLM. Integration tests: PostgreSQL via TEST_DATABASE_URL + stubbed LLM.
- Live Gemini tests use `@pytest.mark.live_llm` and are excluded from PR CI.
- Keep changes within the current phase scope. Do not scaffold future phases.
- Finish every task by running `make check` / `tasks.ps1 check` and appending to `docs/progress.md`.