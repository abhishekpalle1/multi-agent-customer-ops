# Build Progress

Each phase appends: what was built, decisions made, known gaps, verified versions.

## Setup
- Repo created; architecture spec V20 added as docs/architecture.md.
- Supabase dev and test projects created; pgvector verified in both.
- Next: Phase 0 (bootstrap).

## Architecture update: database/runtime decision
- Built: `docs/architecture.md` updated (docs only; no code). Sections: 3 (tech table, new 3.2 database environments/config, new 3.3 pgvector), 6.5, 9, 11.2, 14, 16 (new 16.1 CI database), 22.1 (Phase 0 and Phase 2 wording), 22.3.
- Decisions:
  - No local Docker. Local dev uses Supabase Cloud as hosted PostgreSQL 17 + pgvector via `DATABASE_URL` (session pooler, SSL required). Supabase is infrastructure only: no Supabase SDK, Auth, Data API, Edge Functions, or Storage.
  - Local integration tests/evals use a separate Supabase test project via `TEST_DATABASE_URL`; tests must never touch `DATABASE_URL`.
  - GitHub Actions uses its own ephemeral PostgreSQL 17 + pgvector service container (e.g. `pgvector/pgvector:pg17`), runs `alembic upgrade head`, and never uses Supabase databases.
  - pgvector stays the vector store; first Alembic migration runs `CREATE EXTENSION IF NOT EXISTS vector;`. Alembic reads the URL from app config, not `alembic.ini`.
  - `docker-compose.yml` and testcontainers removed from the design; Docker Compose is an optional future convenience only. Windows uses `tasks.ps1`; Makefile kept for Linux/Codespaces/CI.
- Known gaps: Phase 0 not started; CI workflow, `tasks.ps1`, and Alembic setup are still to be built.
- Verified versions: Supabase dev and test projects both run PostgreSQL 17.12 with the `vector` extension enabled and verified. Python/package and Gemini model versions to be recorded in Phase 0.

## Phase 0: Repository bootstrap — IMPLEMENTED, PENDING EXTERNAL VERIFICATION (not complete)
Branch `feature/p0-bootstrap`. Phase 0 must not be marked complete until the Codespaces/CI checks listed under "Known gaps" pass.

- Built:
  - `pyproject.toml` (uv, Python 3.12, `src/` layout, ruff/mypy/pytest config), `.python-version`, `.env.example` (blank placeholders only), minimal `README.md`.
  - `src/support_ops/config.py`: `pydantic-settings` `Settings` (`APP_ENV`, `DATABASE_URL`, `TEST_DATABASE_URL`, `ALLOW_TEST_DATABASE_RESET`, `AGENT_MODEL`, `JUDGE_MODEL`, `EMBEDDING_MODEL`, `GEMINI_API_KEY`, `GEMINI_OPENAI_BASE_URL`); URLs and API key held as `SecretStr`; `postgres(ql)://` normalized to `postgresql+psycopg`; test-database guards.
  - `src/support_ops/db/session.py`: SQLAlchemy 2.0 engine factory (psycopg 3, `pool_pre_ping`, 5 s connect timeout), session factory, lazily created app engine.
  - Alembic: `alembic.ini` (no `sqlalchemy.url`), `alembic/env.py` (URL from `config.py`, or a connection passed in by tests), `0001_enable_pgvector` (`CREATE EXTENSION IF NOT EXISTS vector;`). No domain tables.
  - FastAPI `create_app()` + `GET /health` (200 when app up, `SELECT 1` succeeds, and `vector` is in `pg_extension`; 503 otherwise).
  - `src/support_ops/agents/openai_sdk_runtime.py`: the only provider-client construction site (`AsyncOpenAI` → `OpenAIChatCompletionsModel`, embeddings helper).
  - Tests: unit (`test_config.py`, `test_health_route.py`, `test_openai_sdk_runtime.py`); integration (`test_health.py`); live (`test_gemini_smoke.py`, `@pytest.mark.live_llm`).
  - `tasks.ps1` (help, lock, install, migrate, run, lint, format, typecheck, test, test-unit, test-live, check), `Makefile` (same targets), `.github/workflows/ci.yml`.
  - `.github/copilot-instructions.md`: CI image corrected `pg16` → `pg17` to match architecture §16.1.
- Decisions:
  - SQLAlchemy kept on the 2.0 line (`>=2.0.58,<2.1`) to match the architecture; 2.1.x deliberately not used.
  - `openai-agents` and `openai` pinned exactly (provider-adapter sensitive); other direct deps pinned exactly; `uv.lock` will be the source of resolved versions.
  - CI: one `pgvector/pgvector:pg17` service with two logical databases: `support_ops` (`DATABASE_URL`: `alembic upgrade head` + `/health` smoke check) and `support_ops_test` (`TEST_DATABASE_URL`: integration tests). This reconciles §16.1 with the §3.2 guard. CI gets no secrets and never uses Supabase; `live_llm` is excluded.
  - Test-database protection: tests resolve the database only via `resolve_test_database_url` (requires non-empty `TEST_DATABASE_URL`; never falls back to `DATABASE_URL`; refuses when both identify the same logical database). Identity = host (case-insensitive), port (default 5432), database, and pooler tenant suffix of the username (`postgres.<project-ref>`); differing roles/passwords on the same database are still refused. Destructive setup must go through `require_destructive_test_reset`, which additionally requires `APP_ENV=test` and `ALLOW_TEST_DATABASE_RESET=true`. Phase 0 performs no destructive reset; the guard is implemented and unit-tested for later phases. `tasks.ps1 test` / `make test` set `APP_ENV=test` for the pytest process only; `ALLOW_TEST_DATABASE_RESET` stays an explicit opt-in.
  - URLs are never printed: errors don't echo them, `/health` logs only the exception type (driver messages can contain the host), and integration-fixture connection failures report only the exception type.
  - `0001_enable_pgvector` downgrade is a deliberate no-op (never drops `vector` from a shared Supabase database).
  - Agents SDK trace export is disabled in the runtime (`set_tracing_disabled(True)`) because the default exporter targets OpenAI's platform; tracing is Phase 6/7.
  - Embeddings request `encoding_format="float"` explicitly (the `openai` client otherwise defaults to base64).
  - No live-LLM GitHub Actions workflow in Phase 0; live tests run locally via `tasks.ps1 test-live` / `make test-live`.
- Known gaps / pending verification:
  - **Environment limitation:** the development laptop's network blocks `files.pythonhosted.org` (TLS handshake fails from uv, PowerShell, and curl; `pypi.org` itself is reachable). Dependencies could not be resolved or installed, so **`uv.lock` has not been generated**. No internal package index was used or configured.
  - **Not executed:** `uv lock`, `uv sync`, mypy, pytest (unit, integration, live), `alembic upgrade head` against the Supabase development or test databases, the `/health` request, and the GitHub Actions workflow.
  - **Executed successfully:** `py_compile`/AST parse of all Python files (CPython 3.12.12); TOML parse of `pyproject.toml`; YAML parse of `ci.yml`; `tasks.ps1` PowerShell parse plus a dry run of every task against a `uv` argument-echo shim; `ruff check` and `ruff format --check` with the official ruff 0.17.6 Windows binary (SHA-256 verified against the GitHub release), both passing.
  - Pending: generate `uv.lock` from public PyPI (Codespaces), commit it, then run `make check`, migration, `/health`, and the live smoke test; record the results here.
- Live Gemini provider tests: **created, not executed.** Model IDs not yet recorded; embedding vector dimension not yet observed. (Final embedding model, output dimension, pgvector column/index settings, and chunking are Phase 4 decisions.)
- Versions:
  - Pinned in `pyproject.toml` (not yet resolved/locked): fastapi 0.165.0, uvicorn 0.54.0, pydantic 2.14.4, pydantic-settings 2.16.3, sqlalchemy >=2.0.58,<2.1, psycopg[binary] 3.3.9, alembic 1.20.1, openai-agents 0.26.0, openai 3.60.0; dev: pytest 9.2.0, pytest-asyncio 1.5.0, httpx 0.29.0, ruff 0.17.6, mypy 2.4.1.
  - Verified locally: uv 0.13.9, CPython 3.12.12, ruff 0.17.6 (standalone binary). CI pins uv 0.13.9, `actions/checkout@v7`, `astral-sh/setup-uv@v10.4.0`, `pgvector/pgvector:pg17`.
