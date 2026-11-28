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
Branch `feature/p0-bootstrap`. Phase 0 stays pending until both of the following pass: (1) GitHub Actions CI, and (2) the Gemini live smoke tests (`make test-live` / `tasks.ps1 test-live`). The Codespaces results under "Verification" were obtained with the **previous** dependency set; after the dependency realignment below, `uv.lock` must be regenerated and that verification re-run.

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
  - SQLAlchemy kept on the 2.0 line (`>=2.0.57,<2.1`) to match the architecture; 2.1.x deliberately not used.
  - `openai-agents` and `openai` pinned exactly (provider-adapter sensitive); other direct deps pinned exactly; `uv.lock` (generated from public PyPI) is the source of truth for the resolved graph.
  - Dependency realignment: direct pins lowered to versions available on both public PyPI and the approved local package mirror (fastapi 0.165.0 → 0.162.7, pydantic 2.14.4 → 2.14.3, sqlalchemy floor 2.0.58 → 2.0.57, openai-agents 0.26.0 → 0.24.0, openai 3.60.0 → 3.51.0, httpx 0.29.0 → 0.28.1, ruff 0.17.6 → 0.17.5). No mirror URL, hostname, or credentials are stored in the repository; `uv.lock` stays public-PyPI only. An API review of upstream `openai-agents` v0.24.0 and `openai` v3.51.0 found every API used by `openai_sdk_runtime.py` and the tests unchanged (`AsyncOpenAI(api_key=, base_url=)`, `OpenAIChatCompletionsModel(model=, openai_client=)`, `Agent(name, instructions, model, tools, output_type)`, `Runner.run`, `function_tool`, `RunResult.final_output_as(..., raise_if_incorrect_type=)`, `RunResult.new_items` / `ToolCallItem`, `set_tracing_disabled`, `embeddings.create(..., encoding_format="float")` returning `data[].embedding: list[float]` and `index`), so no application or test code changed. httpx 0.28.1 sits inside Starlette 1.10.0's declared `TestClient` range (`httpx>=0.27.0,<0.29.0`).
  - CI: one `pgvector/pgvector:pg17` service with two logical databases: `support_ops` (`DATABASE_URL`: `alembic upgrade head` + `/health` smoke check) and `support_ops_test` (`TEST_DATABASE_URL`: integration tests). This reconciles §16.1 with the §3.2 guard. CI gets no secrets and never uses Supabase; `live_llm` is excluded.
  - Test-database protection: tests resolve the database only via `resolve_test_database_url` (requires non-empty `TEST_DATABASE_URL`; never falls back to `DATABASE_URL`; refuses when both identify the same logical database). Identity = host (case-insensitive), port (default 5432), database, and pooler tenant suffix of the username (`postgres.<project-ref>`); differing roles/passwords on the same database are still refused. Destructive setup must go through `require_destructive_test_reset`, which additionally requires `APP_ENV=test` and `ALLOW_TEST_DATABASE_RESET=true`. Phase 0 performs no destructive reset; the guard is implemented and unit-tested for later phases. `tasks.ps1 test` / `make test` set `APP_ENV=test` for the pytest process only; `ALLOW_TEST_DATABASE_RESET` stays an explicit opt-in.
  - URLs are never printed: errors don't echo them, `/health` logs only the exception type (driver messages can contain the host), and integration-fixture connection failures report only the exception type.
  - `0001_enable_pgvector` downgrade is a deliberate no-op (never drops `vector` from a shared Supabase database).
  - Agents SDK trace export is disabled in the runtime (`set_tracing_disabled(True)`) because the default exporter targets OpenAI's platform; tracing is Phase 6/7.
  - Embeddings request `encoding_format="float"` explicitly (the `openai` client otherwise defaults to base64).
  - No live-LLM GitHub Actions workflow in Phase 0; live tests run locally via `tasks.ps1 test-live` / `make test-live`.
- Verification:
  - **Windows laptop (static only):** the laptop's network blocks `files.pythonhosted.org` (TLS handshake fails from uv, PowerShell, and curl; `pypi.org` is reachable), so dependencies cannot be installed there. No internal package index was used or configured. Executed successfully there: `py_compile`/AST parse of all Python files (CPython 3.12.12); TOML parse of `pyproject.toml`; YAML parse of `ci.yml`;   `tasks.ps1` parse plus a dry run of every task against a `uv` argument-echo shim; `ruff check` and `ruff format --check` with the official ruff 0.17.6 binary (SHA-256 verified), and again with the official ruff 0.17.5 binary (SHA-256 verified) after the dependency realignment, both passing.
    - **GitHub Codespaces (uv 0.13.9, Python 3.12.3), executed successfully with the previous dependency set (fastapi 0.165.0, openai-agents 0.26.0, openai 3.60.0, ...; superseded, must be re-run):**
    - `uv lock` resolved 68 packages from public PyPI. `uv.lock` was scanned: no CNA Artifactory, `cna.com`, JFrog, Supabase host/project reference, password, or token.
    - `uv sync --locked` installed 64 packages.
    - `make lint`: `ruff check .` passed; `ruff format --check .` reported 23 files already formatted.
    - `make typecheck`: mypy (strict) reported no issues in 10 source files.
    - `make test-unit`: 34 passed.
    - `make migrate` against the Supabase development database: Alembic applied `0001_enable_pgvector`.
    - Running app `GET /health`: HTTP 200; app = ok, database = ok, pgvector = ok; pgvector version 0.8.2.
    - `make test` (run twice, both passed): 40 collected, 4 `live_llm` deselected, 36 selected, 36 passed. The integration health tests ran against `TEST_DATABASE_URL`.
- Known gaps / pending verification:
  - **`uv.lock` is stale:** the committed lock (`3f91c35`) still records the previous dependency graph, so every `--locked` command fails until it is regenerated from public PyPI (Codespaces) and committed. After regeneration, re-run lint, type-check, unit/integration tests, migration, and `/health` with the new dependency set.
  - **Mirror availability of the resolved graph is unverified:** the lock resolves against public PyPI, which picks the newest versions allowed by the constraints (for example, `sqlalchemy>=2.0.57,<2.1` resolves to 2.0.58 on public PyPI today, and transitive packages resolve to their latest releases). Only the direct pins were confirmed on the approved mirror; every exact version in the regenerated `uv.lock` must also be available there for local installation to succeed.
  - **GitHub Actions CI: not yet verified.**
  - **Gemini live smoke tests: created, not executed.** Model IDs not yet recorded; embedding vector dimension not yet observed. (Final embedding model, output dimension, pgvector column/index settings, and chunking are Phase 4 decisions.)
- Versions:
  - **Direct dependencies (pinned in `pyproject.toml`):** fastapi 0.162.7, uvicorn 0.54.0, pydantic 2.14.3, pydantic-settings 2.16.3, sqlalchemy 2.0.57, psycopg[binary] 3.3.9, alembic 1.20.1, openai-agents 0.24.0, openai 3.51.0; dev group: pytest 9.2.0, pytest-asyncio 1.5.0, httpx 0.28.1, ruff 0.17.5, mypy 2.4.1.
  - **Resolved dependency graph (`uv.lock`):** pending regeneration for the pins above. The committed lock (`3f91c35`, 68 packages from public PyPI) describes the superseded set (e.g. fastapi 0.165.0, openai-agents 0.26.0, openai 3.60.0, sqlalchemy 2.0.58, starlette 1.10.0). Once regenerated, `uv.lock` is the authoritative record of every exact resolved version; individual resolved versions are not duplicated here.
  - **Toolchain:** uv 0.13.9 (Windows and Codespaces); CPython 3.12.12 (Windows) and 3.12.3 (Codespaces); ruff 0.17.5 (dev pin). CI pins uv 0.13.9, `actions/checkout@v7`, `astral-sh/setup-uv@v10.4.0`, `pgvector/pgvector:pg17`.
  - **Database:** Supabase development PostgreSQL 17 with pgvector 0.8.2 (reported by `/health`).
  - **Gemini model IDs:** not yet recorded (live tests not run).
