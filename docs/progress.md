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

## Phase 0: Repository bootstrap — COMPLETE
Branch `feature/p0-bootstrap`. All Phase 0 "done when" criteria from architecture §22.1 are met: the app connects to the development database, the Alembic migration is at head, `/health` returns 200 with database and pgvector checks, integration tests run against `TEST_DATABASE_URL` only, ruff/mypy/pytest pass, GitHub Actions CI passes on its own ephemeral PostgreSQL 17 + pgvector, and the Gemini live smoke test passes. Model and package versions are recorded below.

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
  - SQLAlchemy kept on the 2.0 line (pinned `==2.0.57`) to match the architecture; 2.1.x deliberately not used.
  - `openai-agents` and `openai` pinned exactly (provider-adapter sensitive); other direct deps pinned exactly; `uv.lock` (generated from public PyPI) is the source of truth for the resolved graph.
  - Dependency realignment: direct pins lowered to versions available on both public PyPI and the approved local package mirror (fastapi 0.165.0 → 0.162.7, pydantic 2.14.4 → 2.14.3, sqlalchemy floor 2.0.58 → 2.0.57, openai-agents 0.26.0 → 0.24.0, openai 3.60.0 → 3.51.0, httpx 0.29.0 → 0.28.1, ruff 0.17.6 → 0.17.5). No mirror URL, hostname, or credentials are stored in the repository; `uv.lock` stays public-PyPI only. An API review of upstream `openai-agents` v0.24.0 and `openai` v3.51.0 found every API used by `openai_sdk_runtime.py` and the tests unchanged (`AsyncOpenAI(api_key=, base_url=)`, `OpenAIChatCompletionsModel(model=, openai_client=)`, `Agent(name, instructions, model, tools, output_type)`, `Runner.run`, `function_tool`, `RunResult.final_output_as(..., raise_if_incorrect_type=)`, `RunResult.new_items` / `ToolCallItem`, `set_tracing_disabled`, `embeddings.create(..., encoding_format="float")` returning `data[].embedding: list[float]` and `index`), so no application or test code changed. httpx 0.28.1 sits inside Starlette 1.10.0's declared `TestClient` range (`httpx>=0.27.0,<0.29.0`).
  - Transitive caps via uv constraint dependencies: a full comparison of the public-PyPI lock against the approved local package mirror found nine transitive versions the mirror does not carry. `[tool.uv] constraint-dependencies` in `pyproject.toml` pins them exactly (resolution-only; they are not direct dependencies): annotated-doc 0.0.6 (via fastapi), certifi 2026.10.23 (via httpx, httpcore, requests), charset-normalizer 3.5.2 (via requests), httpcore2 2.16.0 (via httpx2, which pins it exactly, so both move together), httpx2 2.16.0 (via openai, openai-agents, mcp), librt 0.16.0 (via mypy), packaging 26.3 (via pytest), python-multipart 0.0.33 (via openai-agents, mcp), sse-starlette 3.7.0 (via mcp). Each version was checked against the PyPI metadata of every parent at its locked version (e.g. openai `httpx2<3,>=2.12.0`, openai-agents `python-multipart>=0.0.31`, mypy `librt>=0.16.0`, mcp `sse-starlette>=3.0.0`) and against its own requirements; none introduces a package absent from the lock. `uv.lock` is still resolved from public PyPI only; the repository contains no mirror URL, hostname, or credentials. Each cap can be removed once the mirror carries the newer release.
  - CI: one `pgvector/pgvector:pg17` service with two logical databases: `support_ops` (`DATABASE_URL`: `alembic upgrade head` + `/health` smoke check) and `support_ops_test` (`TEST_DATABASE_URL`: integration tests). This reconciles §16.1 with the §3.2 guard. CI gets no secrets and never uses Supabase; `live_llm` is excluded.
  - Test-database protection: tests resolve the database only via `resolve_test_database_url` (requires non-empty `TEST_DATABASE_URL`; never falls back to `DATABASE_URL`; refuses when both identify the same logical database). Identity = host (case-insensitive), port (default 5432), database, and pooler tenant suffix of the username (`postgres.<project-ref>`); differing roles/passwords on the same database are still refused. Destructive setup must go through `require_destructive_test_reset`, which additionally requires `APP_ENV=test` and `ALLOW_TEST_DATABASE_RESET=true`. Phase 0 performs no destructive reset; the guard is implemented and unit-tested for later phases. `tasks.ps1 test` / `make test` set `APP_ENV=test` for the pytest process only; `ALLOW_TEST_DATABASE_RESET` stays an explicit opt-in.
  - URLs are never printed: errors don't echo them, `/health` logs only the exception type (driver messages can contain the host), and integration-fixture connection failures report only the exception type.
  - `0001_enable_pgvector` downgrade is a deliberate no-op (never drops `vector` from a shared Supabase database).
  - Agents SDK trace export is disabled in the runtime (`set_tracing_disabled(True)`) because the default exporter targets OpenAI's platform; tracing is Phase 6/7.
  - Embeddings request `encoding_format="float"` explicitly (the `openai` client otherwise defaults to base64).
  - No live-LLM GitHub Actions workflow in Phase 0; live tests run locally via `tasks.ps1 test-live` / `make test-live`.
- Verification (final; dependency graph from `uv.lock` at `45895b7`):
  - **Local Windows (CPython 3.12.12):** the final constrained dependency graph installed into the local `.venv` from the approved package source (no package-source configuration is stored in the repository).
    - `ruff check .` passed; `ruff format --check .`: 23 files already formatted.
    - mypy (strict): no issues in 10 source files.
    - Non-live suite (`-m "not live_llm"`): 40 collected, 4 `live_llm` deselected, 36 selected, 36 passed (unit tests plus integration tests against `TEST_DATABASE_URL`).
    - Alembic connected to the development PostgreSQL database and confirmed it is at head (`0001_enable_pgvector`).
    - Local FastAPI `GET /health`: HTTP 200; app = ok, database = ok, pgvector = ok; pgvector version 0.8.2.
  - **Gemini live smoke test (local, `-m live_llm`):** 4 passed, 36 deselected, about 5.74 s, with `AGENT_MODEL=gemini-3.5-flash-lite` and `EMBEDDING_MODEL=gemini-embedding-2`. Basic generation, Pydantic structured output (`output_type`), function-tool calling through the Agents SDK `Runner`, and one embedding request all passed. Observed embedding dimension: 3072.
  - **GitHub Actions CI:** the latest run on the final dependency graph passed, including lint, type-check, migration, and tests. It used its own ephemeral `pgvector/pgvector:pg17` service, no Supabase database, and no secrets, with `live_llm` excluded.
  - **Earlier verification (superseded by the final results above):**
    - Windows static checks before dependencies could be installed (public package downloads were blocked on that network): `py_compile`/AST parse of all Python files, TOML and YAML parses, `tasks.ps1` parse plus a dry run of every task against a `uv` argument-echo shim, and `ruff check` / `ruff format --check` with the official ruff 0.17.6 and 0.17.5 binaries (SHA-256 verified). All passed.
    - GitHub Codespaces (uv 0.13.9, Python 3.12.3) with the initial dependency set (fastapi 0.165.0, openai-agents 0.26.0, openai 3.60.0, ...): `uv lock` (68 packages, public PyPI; scanned clean of internal package-index references, Supabase host/project references, passwords, and tokens), `uv sync --locked`, lint, mypy, unit tests (34 passed), `make migrate` (applied `0001_enable_pgvector`), `/health` 200 (pgvector 0.8.2), and `make test` (36 passed, twice).
- Known gaps / follow-ups (no open Phase 0 verification items):
  - Embedding dimension 3072 is recorded only as the observed default output of `gemini-embedding-2` in the Phase 0 smoke test. The final Phase 4 RAG embedding model, output dimension, pgvector column dimension, and index configuration (HNSW/IVFFlat) remain a benchmark-driven Phase 4 decision; nothing in Phase 0 fixes them.
  - `JUDGE_MODEL` is configurable but not exercised in Phase 0.
  - Live provider tests are not part of PR CI by design; a manual/scheduled `live_llm` workflow is out of Phase 0 scope.
  - The nine `[tool.uv] constraint-dependencies` caps are a maintenance item: remove each once the approved package source carries the newer release, then regenerate `uv.lock` from public PyPI and re-verify.
  - On networks that block public PyPI package downloads, `tasks.ps1 install` (`uv sync --locked`) cannot fetch packages; there the locked graph is installed from the approved package source instead.
- Versions:
  - **Direct dependencies (pinned in `pyproject.toml`):** fastapi 0.162.7, uvicorn 0.54.0, pydantic 2.14.3, pydantic-settings 2.16.3, sqlalchemy 2.0.57, psycopg[binary] 3.3.9, alembic 1.20.1, openai-agents 0.24.0, openai 3.51.0; dev group: pytest 9.2.0, pytest-asyncio 1.5.0, httpx 0.28.1, ruff 0.17.5, mypy 2.4.1.
  - **Transitive constraint dependencies (`[tool.uv] constraint-dependencies`):** annotated-doc 0.0.6, certifi 2026.10.23, charset-normalizer 3.5.2, httpcore2 2.16.0, httpx2 2.16.0, librt 0.16.0, packaging 26.3, python-multipart 0.0.33, sse-starlette 3.7.0.
  - **Resolved dependency graph (`uv.lock`, `45895b7`):** 68 packages, all resolved from public PyPI (`https://pypi.org/simple`). It includes the direct pins and the nine constrained transitive versions, records the nine constraints in its manifest, and every version is also available from the approved package source. `uv.lock` is the authoritative record of every exact resolved version; individual versions are not duplicated here.
  - **Toolchain:** uv 0.13.9; CPython 3.12.12 (local Windows, final verification) and 3.12.3 (Codespaces, earlier verification); ruff 0.17.5. CI pins uv 0.13.9, `actions/checkout@v7`, `astral-sh/setup-uv@v10.4.0`, `pgvector/pgvector:pg17`.
  - **Database:** development PostgreSQL 17 (Supabase) with pgvector 0.8.2, as reported by `/health`.
  - **Gemini:** `AGENT_MODEL=gemini-3.5-flash-lite`; `EMBEDDING_MODEL=gemini-embedding-2` (observed default dimension 3072); accessed through Google's OpenAI-compatible endpoint via `openai-agents` 0.24.0 and `openai` 3.51.0. `JUDGE_MODEL` was not recorded (not exercised).
