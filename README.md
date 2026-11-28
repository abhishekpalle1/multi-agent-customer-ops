# multi-agent-customer-ops

Policy-grounded multi-agent orchestration for transaction-safe customer operations.
The design is specified in [docs/architecture.md](docs/architecture.md); build status is
tracked in [docs/progress.md](docs/progress.md).

**Current phase: 0 (repository bootstrap).** It provides configuration, PostgreSQL +
pgvector connectivity, Alembic, `GET /health`, the Gemini provider smoke test, and CI.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv if needed)
- A PostgreSQL 17 database with the `vector` extension for development (`DATABASE_URL`)
  and a **separate** one for integration tests (`TEST_DATABASE_URL`). No Docker is needed.

## Setup

```powershell
Copy-Item .env.example .env   # then fill in .env locally; never commit it
.\tasks.ps1 install           # Linux/Codespaces: make install
.\tasks.ps1 migrate           # alembic upgrade head against DATABASE_URL
.\tasks.ps1 run               # http://127.0.0.1:8000/health
```

If script execution is blocked, run `powershell -ExecutionPolicy Bypass -File .\tasks.ps1 <task>`.

## Verify

| Task | Windows | Linux / Codespaces |
|---|---|---|
| Lint (ruff) | `.\tasks.ps1 lint` | `make lint` |
| Type-check (mypy strict) | `.\tasks.ps1 typecheck` | `make typecheck` |
| Tests, excluding `live_llm` | `.\tasks.ps1 test` | `make test` |
| All of the above | `.\tasks.ps1 check` | `make check` |
| Live Gemini smoke test | `.\tasks.ps1 test-live` | `make test-live` |

- Unit tests (`tests/unit`) use no network, database, or LLM.
- Integration tests use only `TEST_DATABASE_URL`. They never fall back to `DATABASE_URL`
  and refuse to run if both identify the same logical database. Destructive test setup
  additionally requires `APP_ENV=test` and `ALLOW_TEST_DATABASE_RESET=true`.
- Live tests need `GEMINI_API_KEY`, `AGENT_MODEL`, and `EMBEDDING_MODEL`; they are skipped
  when `GEMINI_API_KEY` is unset and are not run by PR CI.

`GET /health` returns `200` when the app is up, PostgreSQL is reachable, and the `vector`
extension is installed, and `503` otherwise.
