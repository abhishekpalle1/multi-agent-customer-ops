# multi-agent-customer-ops

Policy-grounded multi-agent orchestration for transaction-safe customer operations.
The design is specified in [docs/architecture.md](docs/architecture.md); build status is
tracked in [docs/progress.md](docs/progress.md).

**Current phase: 2 (synthetic data, golden scenarios, policy prose).** Phase 0 provides
configuration, PostgreSQL + pgvector connectivity, Alembic, `GET /health`, the Gemini
provider smoke test, and CI. Phase 1 adds the deterministic domain layer
(`src/support_ops/domain/`: enums, Pydantic contracts, refund calculator, executable policy
engine, action state machine), the PostgreSQL schema (`src/support_ops/db/schema.py`), and
migration `0002_create_domain_schema`. Phase 2 adds a deterministic synthetic store
(`src/support_ops/synthetic/`), golden-scenario ground truth (`data/golden/scenarios.json`),
and synthetic policy prose (`data/policies/`). No Phase 1 or Phase 2 behavior uses an LLM.

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
  additionally requires `APP_ENV=test` and `ALLOW_TEST_DATABASE_RESET=true`; without both,
  destructive tests (for example the `destructive_db` migration round trip) are skipped with
  the reason shown. Each test session holds a shared advisory lock on the test database and
  destructive tests take it exclusively, so they never overlap another test session.
- Property-based tests use Hypothesis; the derandomized `ci` profile is selected
  automatically when the `CI` environment variable is set.
- Live tests need `GEMINI_API_KEY`, `AGENT_MODEL`, and `EMBEDDING_MODEL`; they are skipped
  when `GEMINI_API_KEY` is unset and are not run by PR CI.

`GET /health` returns `200` when the app is up, PostgreSQL is reachable, and the `vector`
extension is installed, and `503` otherwise.

## Synthetic data (Phase 2)

Dataset `p2-v1`: random seed `20261101` and scenario anchor `2026-11-01T12:00:00Z`. It has
625 orders (25 of them golden) and 30 golden scenarios. Generation is deterministic and uses
no LLM. The people, emails (`@example.test`), products, and transactions are all fictional.

| Task | Windows | Linux / Codespaces | Touches a database? |
|---|---|---|---|
| Generate + validate in memory; check the committed manifest | `.\tasks.ps1 synthetic-validate` | `make synthetic-validate` | No |
| Regenerate `data/golden/scenarios.json` | `.\tasks.ps1 synthetic-manifest` | `make synthetic-manifest` | No |
| Seed the development database | `.\tasks.ps1 seed-dev` | `make seed-dev` | `DATABASE_URL` |
| **Destructive** reset + reseed of the development database | `.\tasks.ps1 reset-seed-dev` | `make reset-seed-dev` | `DATABASE_URL` |
| Seed the test database | `.\tasks.ps1 seed-test` | `make seed-test` | `TEST_DATABASE_URL` |
| **Destructive** reset + reseed of the test database | `.\tasks.ps1 reset-seed-test` | `make reset-seed-test` | `TEST_DATABASE_URL` |

Safety rules:

- Targets are named (`development`, `test`), never given as URLs. Logs show only a
  sanitized database identity hash.
- The database must already be at `0002_create_domain_schema`. Seeding never migrates.
- `seed-*` inserts into empty tables in one transaction. If the database already holds this
  exact dataset, it is a verified no-op. Otherwise it refuses and changes nothing.
- **`reset-seed-*` truncates every business and transactional table**: customers, orders,
  payments, refunds, cases, actions, approvals, returns, replacements, audit log, and
  outbox. `policy_docs` and `policy_chunks` are kept.
  - The truncate, the reseed, and the reconciliation happen in one transaction, so a failure
    keeps the previous data.
  - `reset-seed-dev` requires `APP_ENV=development` and `ALLOW_DEV_DATABASE_RESET=true`.
  - `reset-seed-test` requires `ALLOW_TEST_DATABASE_RESET=true`.
  - Both refuse when the development and test URLs identify the same database.
  - The task runners never set these flags for you.
- Every seed is followed by an independent reconciliation. It checks the SQL money and
  ledger invariants, compares per-table fingerprints, and re-evaluates every golden scenario
  with the Phase 1 policy engine and refund calculator.
- `check`, `test`, and `migrate` never seed or reset anything. The destructive seed
  integration tests run only against an *empty* test database and leave it empty. If the
  test database holds data (for example after `seed-test`), they are skipped rather than
  deleting it.
