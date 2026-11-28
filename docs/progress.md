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

## Phase 1: Domain foundation — COMPLETE
Branch `feature/p1-domain`. All Phase 1 "done when" criteria from architecture §22.1 are met: unit and Hypothesis tests pass and no Phase 1 code uses an LLM. In addition, GitHub Actions CI passed on the final Phase 1 code and dependency graph, the full non-live suite (unit, Hypothesis, and PostgreSQL integration tests including the guarded migration round trip) passed on local Windows against the Supabase test database, the development database was migrated to `0002_create_domain_schema` (head), and `GET /health` returned HTTP 200 after the migration.

- Built:
  - `src/support_ops/domain/`:
    - `enums.py`: `StrEnum`s with UPPERCASE names and stable lowercase values (`Currency.USD = "USD"` is the ISO 4217 exception). `ActionType`, `ActionStatus`, `AuthorizationDecision`, `ApprovalStatus`, `OrderStatus` (placed/shipped/delivered/cancelled), `ShipmentStatus` (in_transit/delivered), `PaymentStatus` (captured/partially_refunded/refunded), `ReturnStatus` (created/label_created), `ReplacementStatus` (created/shipped), `CaseStatus` (open/escalated/resolved), `RequestReason` (damaged_item/change_of_mind), `PolicyDecision`, `PolicyReasonCode`, `Role` (customer/supervisor), `AuditActorType`, `Currency`.
    - `money.py`: `Money` type (Decimal only; rejects float/bool/NaN/infinity/negative/>2 dp; never rounds inputs; normalized to 0.01; JSON string), exact integer-cent proration, and a `MONEY_CONTEXT` that traps `Inexact`.
    - `models.py`: frozen, strict, `extra="forbid"` Pydantic contracts: `AuthContext`, `OrderLineFacts`, `OrderFacts`, `RefundSelection`, `RefundCalculationInput`, `RefundLineBreakdown`, `RefundCalculationResult`, `PolicyRule`, `PolicyRequest`, `ItemPolicyResult`, `PolicyEvaluation`; prefixed ID types (`CUST-`, `ORD-`, `CASE-`, `ACT-`), `PolicyRuleId`, UTC-normalized aware datetimes.
    - `errors.py`: `DomainError` → `DomainInvariantError`, `InvalidStateTransitionError`, `RefundCalculationError`, `PolicyEvaluationError` (none subclass `ValueError`, so Pydantic never wraps them).
    - `refund_calculator.py`, `policy_engine.py`, `state_machine.py` (details below).
  - `src/support_ops/db/schema.py`: SQLAlchemy 2.0 typed mappings for the 17 architecture tables plus six child tables (`action_items`, `action_policy_references`, `action_triggered_rules`, `refund_items`, `return_items`, `replacement_items`).
  - `alembic/versions/0002_create_domain_schema.py` (hand-written; frozen enum literals; creates tables, constraints, indexes, and the ledger trigger function/triggers; downgrade drops triggers, function, then tables in reverse order and keeps `vector`). `alembic/env.py` now sets `target_metadata = Base.metadata`.
  - Tests: `tests/unit/domain/` (enums, contracts, calculator, Hypothesis properties, policy engine, state machine), `tests/unit/test_domain_migration_ddl.py` (offline migration-vs-ORM DDL comparison), `tests/integration/test_domain_schema.py`, `test_domain_constraints.py`, `test_migration_roundtrip.py`, plus `tests/integration/migration_helpers.py`.
- Decisions:
  - **Action lifecycle clarification** (recorded here; `docs/architecture.md` intentionally unchanged): `proposed → authorized | awaiting_approval | denied`; `awaiting_approval → authorized | rejected`; `authorized → executing`; `executing → succeeded | failed | unknown`; `unknown → succeeded | failed`; terminal `succeeded`, `failed`, `denied`, `rejected`. `authorized` is the only entry into `executing`, whether reached by ALLOW or by approval of the same stored action. `denied` (hard rule/ownership/security failure; never overridable) and `rejected` (supervisor declined) are additions to the §8.2 diagram so DENY and approval-rejection outcomes do not leave actions in `proposed`/`awaiting_approval` forever. The map is enforced at import time and by a pairwise test.
  - **Synthetic project policy (fictional, executable, ruleset `synthetic-p1-2026.11`):** standard change-of-mind return window and damaged-item reporting window are both 30 UTC calendar days after the delivery date (day 30 eligible, day 31 not); evaluation time is injected, never read from the clock. Rules: `ORD-1.1` cancelled order ineligible; `ORD-1.2` items must be delivered; `RFD-2.1` fully refunded item ineligible; `RFD-2.2` remaining shipping refundable only for eligible damaged-item refunds; `RET-3.1` return window; `RET-3.2` non-returnable items final for change of mind; `DMG-4.1` damage-reporting window; `DMG-4.2` damaged item eligible even if non-returnable; `CAN-5.1` cancel only while `placed`; `CAN-5.2` no cancellation once shipped/delivered; `CAN-5.3` already cancelled; `RPL-6.1` replacement requires damage; `RPL-6.2` replacement requires stock. The policy engine does not authorize: monetary approval thresholds (including the $49 and $149/$150/$151 boundaries), duplicate-action detection, and the return-label rule are deferred to Phase 3.
  - **Refund calculation:** per selected line, gross = unit_price × q; discount and tax are allocated by cumulative half-up proration to the cent (exact integer cents), so partial refunds always sum exactly to the line totals; eligible = gross − discount + tax; refund = min(eligible, line paid total − prior refunded amount). Remaining *quantity* and remaining *paid amount* are reported separately; a cap caused by prior amount-only refunds is exposed (`capped_by_prior_amount_refund`, `cap_reduction`). Contradictory trusted facts raise `DomainInvariantError`; impossible selections raise `RefundCalculationError`; inputs are never clamped or rounded.
  - **Refund ledger:** `refunds` and `refund_items` are the immutable source of truth. A ledger row is written only for a successfully verified refund; pending/failed/unknown states live on `actions`. Migration `0002` creates `reject_refund_ledger_mutation()` and the row-level `BEFORE UPDATE OR DELETE` triggers `trg_refunds_append_only` and `trg_refund_items_append_only` (SQLSTATE 23000; the message names only the table and operation, never row contents). `INSERT`, `TRUNCATE`, and `DROP TABLE` are unaffected. **Future corrections must be appended as compensating ledger rows, never by updating or deleting historical refund rows.**
  - **Aggregate balances:** `payments.amount_refunded`, `order_items.refunded_quantity`, `order_items.refunded_amount`, and `orders.shipping_refunded_amount` are maintained balances for row locking and fast authorization. CHECK constraints only bound each balance within its own row; they do not and cannot prove the balances equal the ledger sums. **Phase 3 must lock the affected rows and write the ledger rows and the balance updates atomically in one database transaction.**
  - Schema: readable text primary keys with format CHECKs; `NUMERIC(12,2)` money; `TIMESTAMPTZ` everywhere; enums as `VARCHAR` + CHECK generated from the Python enums (no PostgreSQL ENUM types); deterministic constraint names via a naming convention; one shipment and one captured payment per order (UNIQUE `order_id`); USD-only `currency` CHECKs; composite foreign keys that include `order_id` so items, refunds, actions, returns, and replacements cannot cross orders, and `cases(order_id, customer_id) → orders` so a case cannot reference another customer's order; globally UNIQUE `actions.idempotency_key` (retry policy is Phase 3); one approval per action; policy references and triggered rule IDs are normalized child tables keyed per action (the architecture does not require arrays).
  - `policy_docs`/`policy_chunks` keep the Phase 4 metadata only; embedding, `tsvector`, vector dimension, and ANN indexes remain a Phase 4 benchmark decision.
  - Deferred: `AuthContext.permissions`, `ProposedAction`/authorization result contracts (first consumer is Phase 3), case state machine.
  - Test-database safety: every integration test session now holds a shared PostgreSQL advisory lock on the test database; the `destructive_db` round-trip test takes it exclusively (120 s `lock_timeout`), checks the database starts at Phase 1 head, downgrades to `0001_enable_pgvector`, restores head in a `finally`, and verifies the restoration. It and the `TRUNCATE` test are skipped unless `require_destructive_test_reset` passes. Constraint tests run inside always-rolled-back transactions.
  - **CHECK constraints and SQL NULL semantics:** the first real PostgreSQL constraint run showed that `ck_actions_status_matches_authorization_decision` accepted an `authorized` action with a NULL `authorization_decision`. Root cause: SQL three-valued logic. With a NULL decision, a branch such as `status IN (...) AND authorization_decision IN ('allow', 'requires_approval')` evaluates to UNKNOWN rather than FALSE, and PostgreSQL accepts a CHECK whose result is UNKNOWN; the same hole affected `denied`, `awaiting_approval`, and `rejected`. Fix (in both the ORM and migration `0002`, kept identical; `0002` was edited in place because it had not reached the development database or `develop`): every decision-bearing state now requires `authorization_decision IS NOT NULL` explicitly. Coverage now includes a NULL decision for every decision-bearing status, every valid status/decision combination, and representative mismatches. All other CHECKs that reference nullable columns were reviewed; they either use `IS [NOT] NULL`/`coalesce` (always TRUE or FALSE) or accept NULL intentionally, with presence enforced by a paired status constraint.
  - **Released migration history:** `0002_create_domain_schema` has now been applied to the development database and is released migration history. It must not be edited further; every future schema change must be a new migration (`0003` onward).
  - Dependency: `hypothesis==6.170.15` added to the dev group (newest version confirmed on both public PyPI and the approved package source); its only runtime dependency, `sortedcontainers` 2.4.0, stays transitive (it is the latest release, so no constraint is needed). Existing direct pins and the nine transitive constraints are unchanged. No package-source configuration is in the repository.
- Final verification:
  - **Dependencies:** locking and installation of the Hypothesis dev dependency completed: `hypothesis==6.170.15`, `sortedcontainers==2.4.0`.
  - **Codespaces:** full Phase 1 unit/Hypothesis suite: 323 passed. Ruff passed; Ruff format check passed; mypy (strict) passed with no issues in 19 source files.
  - **Test-database migration:** the corrected migration was reapplied to the Supabase test database through the guarded migration round trip (`0002` → `0001_enable_pgvector` → corrected `0002`).
  - **Local Windows, non-live suite against the Supabase test database:** 432 collected, 4 `live_llm` deselected, 428 selected, **428 passed** in about 33.35 s. The suite covers the Pydantic/domain tests, Hypothesis refund invariants, policy engine, action state machine, PostgreSQL schema/drift tests, PostgreSQL domain constraints, immutable refund-ledger trigger tests, the migration round trip, and the Phase 0 unit/integration tests.
  - **Supabase test-database migration round trip** (part of that suite): Phase 1 head → `0001_enable_pgvector` → Phase 1 head passed, and pgvector remained installed throughout. Schema/drift, PostgreSQL constraints, immutable refund-ledger triggers, Pydantic/domain tests, Hypothesis properties, policy engine, state machine, and Phase 0 regression tests all passed.
  - The test database finished restored at Phase 1 head (`0002_create_domain_schema`). `APP_ENV` and `ALLOW_TEST_DATABASE_RESET` were removed from the environment after the run.
  - **GitHub Actions CI:** passed on the final Phase 1 code and dependency graph, using its own ephemeral `pgvector/pgvector:pg17` service database (no Supabase database, no secrets, `live_llm` excluded).
  - **Development database:** migration from `0001_enable_pgvector` to `0002_create_domain_schema` succeeded; final development Alembic revision: `0002_create_domain_schema (head)`.
  - **Local FastAPI app:** started successfully after the migration; `GET /health` returned HTTP 200.
- Earlier verification (before the dependency was installed; superseded by the final results above), local Windows with the existing `.venv`:
  - `ruff check .`: all checks passed; `ruff format --check .`: 46 files already formatted.
  - mypy (strict) on `src/`: no issues in 19 source files.
  - Unit tests excluding the Hypothesis module (`tests/unit`, `-m "not live_llm"`, `--ignore=tests/unit/domain/test_refund_calculator_properties.py`): 319 passed, including the offline migration-vs-ORM DDL comparison (23 tables, 330 column/constraint entries, 22 indexes, downgrade order, no `DROP EXTENSION`).
  - Refund invariants (`tests/unit/domain/refund_invariants.py`) exercised with a temporary random-input harness (20,000 seeded samples, not committed) in place of Hypothesis: all invariants held.
  - `pytest tests/integration --collect-only`: 86 tests collected.
- Known gaps / follow-ups (no open Phase 1 verification items):
  - Phase 2 must generate seed data that satisfies the trusted-fact invariants (for example `refunded_amount` ≥ the allocated paid share of `refunded_quantity`, payment status consistent with `amount_refunded`).
  - `audit_log` is append-only by convention only (no trigger); revisit in Phase 3 if required.
  - Return-label requirement, case transitions, approval thresholds, idempotency-key hashing, and FAILED-action retry policy are Phase 3+ decisions.
- Versions: unchanged from Phase 0 except the new dev dependency `hypothesis==6.170.15` (transitive `sortedcontainers` 2.4.0), now recorded in the regenerated `uv.lock`.

## Phase 2: Synthetic data, golden scenarios, policy prose — IMPLEMENTED (pending commit, CI, and development-database seed approval)
Branch `feature/p2-synthetic-data`. No Phase 2 code uses an LLM: generation, golden labels, expected policy outcomes, and expected refund amounts are all deterministic, and the policy and refund ground truth comes from the Phase 1 engine and calculator.

- Dataset identity:
  - `DATASET_VERSION=p2-v1`, `DEFAULT_RANDOM_SEED=20261101`, `SCENARIO_ANCHOR=2026-11-01T12:00:00Z`, in `src/support_ops/synthetic/dataset_config.py` and recorded in the manifest.
  - Required schema `0002_create_domain_schema`; ruleset `synthetic-p1-2026.11`.
  - Every timestamp column, including `created_at`/`updated_at`, is set from the anchor. No wall clock and no UUIDs.
  - Changing the anchor, the allocation tables, or the golden catalog is a dataset-version change.
- Exact row counts (independent of the seed):

  | Table | Rows |
  |---|---:|
  | customers | 182 |
  | products | 69 |
  | inventory | 69 |
  | orders | 625 |
  | order_items | 1,222 |
  | shipments | 587 |
  | payments | 625 |
  | refunds | 65 |
  | refund_items | 87 |
  | **Total** | **3,531** |

  - Background: 180 customers, 60 products (9 non-returnable, 6 out of stock), 600 orders (540 delivered, 24 shipped, 12 placed, 24 cancelled).
  - Golden: 2 customers (`CUST-10`, `CUST-20`), 9 products, 25 orders.
  - No case, action, approval, return, replacement, audit-log, outbox, or policy rows.
  - Dataset fingerprint (SHA-256 of canonical rows, default seed): `3cecef171648bfb9c2bc4536f96d658ce318ee67fc531e19179d0cd268b3290f`.
- Built:
  - `src/support_ops/synthetic/`:
    - `dataset_config`: identity constants and allocation tables.
    - `records`: frozen row records, canonical serialization, per-table and combined fingerprints.
    - `vocabulary`: fixed fictional word lists.
    - `facts`: records to Phase 1 `OrderFacts`; replacement stock fact.
    - `economics`: the single integer-cent pricing and refund-ledger module.
    - `background`: generator driven by an owned `random.Random(seed)`.
    - `golden`: explicit golden world and 30 scenarios with declared intent.
    - `dataset`: assembly, collision checks, `PreparedDataset`.
    - `validation`: in-memory invariants.
    - `manifest`: Pydantic manifest models; built by calling the real `evaluate_policy`/`calculate_refund`; checked against declared intent.
    - `reconcile`: independent SQL aggregates, per-table fingerprints, re-evaluation of golden scenarios from facts loaded from the database.
    - `seeding`: named targets, guards, sanitized identity, advisory locks, single-transaction seed and reset.
    - `cli` / `__main__`: `validate`, `manifest`, `seed`, `reset-seed`.
  - `src/support_ops/db/locks.py`: advisory-lock keys, now shared by the test harness and the CLI.
  - `config.py`: `ALLOW_DEV_DATABASE_RESET` (default false), `resolve_development_database_url`, `require_destructive_dev_reset`.
  - `data/golden/scenarios.json`: the committed manifest, generated rather than hand-written, sorted keys and scenarios.
  - `data/policies/`: six synthetic policy documents. `order_eligibility_v1`, `refunds_v1`, `returns_v1`, `damaged_items_v1`, `cancellations_v1`, and `replacements_v1` hold one section for each Phase 1 rule ID (ORD-1.1/1.2, RFD-2.1/2.2, RET-3.1/3.2, DMG-4.1/4.2, CAN-5.1/5.2/5.3, RPL-6.1/6.2) with titles identical to the executable catalog. They are not persisted, chunked, or embedded; that is Phase 4.
  - Tasks in both `tasks.ps1` and the `Makefile`: `synthetic-validate`, `synthetic-manifest`, `seed-dev`, `reset-seed-dev`, `seed-test`, `reset-seed-test`.
  - Tests: `tests/unit/synthetic/` (dataset, economics, golden manifest, policy prose, seed guards), new dev-reset guard tests in `tests/unit/test_config.py`, and `tests/integration/test_synthetic_seed.py`.
- Golden scenario inventory (30). Each one's policy and refund expectations come from real Phase 1 calls at the anchor:
  - **Window boundaries:**
    - `damaged-day-008-refund`: eligible, 104.18.
    - `damaged-day-030-refund`: delivered 23:30 UTC, eligible, 60.45.
    - `damaged-day-031-refund`: delivered 00:15 UTC, ineligible (DMG-4.1).
    - `change-of-mind-day-030-refund`: eligible, 38.87, no shipping.
    - `change-of-mind-day-031-refund`: ineligible (RET-3.1).
    - `change-of-mind-day-090-return`: ineligible.
  - **Returnability:**
    - `returnable-change-of-mind-return`: eligible.
    - `nonreturnable-change-of-mind-return`: ineligible (RET-3.2).
    - `nonreturnable-damaged-exception-refund`: eligible via the DMG-4.2 damaged-item exception, 37.22 including shipping.
  - **Lifecycle:**
    - `cancel-placed-order`: CAN-5.1 eligible.
    - `cancel-shipped-order`: CAN-5.2.
    - `cancel-delivered-order`: CAN-5.2.
    - `cancel-cancelled-order`: CAN-5.3.
    - `refund-shipped-not-delivered`: ORD-1.2.
    - `refund-cancelled-order`: ORD-1.1.
  - **Refund history:**
    - `partial-quantity-prior-refund`: 31.46, prorated.
    - `amount-only-adjustment-cap`: 56.40, capped by 30.00.
    - `fully-refunded-item`: RFD-2.1.
    - `multi-item-one-line-partial`: 80.45.
  - **Replacement:**
    - `replacement-in-stock`: eligible.
    - `replacement-out-of-stock`: RPL-6.2.
    - `replacement-requires-damage`: RPL-6.1.
  - **Phase 3 fixtures (facts only, no authorization outcome):**
    - `amount-boundary-049`, `-149`, `-150`, `-151`, `-799`: totals exactly 49.00, 149.00, 150.00, 151.00, and 799.00, with zero shipping so the item amount equals the total.
    - `cross-customer-ownership`: owner `CUST-20`, requester `CUST-10`. Policy alone would allow it, so the denial must come from Phase 3 ownership checks. No access label is recorded.
    - `duplicate-refund-request-base` and `timeout-after-commit-base`: reserved IDs `CASE-DUPLICATE-001` and `CASE-TIMEOUT-COMMIT-001`.
  - All 13 rule IDs and all 19 `PolicyReasonCode` values are covered.
- Decisions:
  - **Code location and manifest format (deviates from architecture §9/§10.3/§22.1, approved):** code lives under `src/support_ops/synthetic/` so it is type-checked with mypy strict, instead of `seed/generate_seed.py`. The manifest is JSON at `data/golden/scenarios.json` instead of `seed/golden_scenarios.yaml`, which avoids adding PyYAML. `docs/architecture.md` is unchanged.
  - **Eval world-state fixtures deferred to Phase 6b (approved deviation from §22.1):** their format belongs to the eval harness.
  - **Golden case rows not seeded (approved):** the base orders for `CASE-DUPLICATE-001` and `CASE-TIMEOUT-COMMIT-001` are seeded, but the case rows are not. The manifest records only the reserved case IDs, so the architecture's "every golden ID exists" criterion holds for orders, not for these two case IDs. Phase 3 creates the case and action fixtures.
  - **Scale:** lighter than the architecture §10 *suggested* demo scale, as approved.
  - **Money:**
    - Pricing is done in integer cents in `economics.py`.
    - Every historical refund that returns units is recorded from a real `calculate_refund` result.
      - Golden refund-history entries are first evaluated by the real policy engine at their historical instant, and `include_shipping` comes from that evaluation.
      - Background historical refunds pass `include_shipping=False`.
    - Amount-only goodwill adjustments (`quantity=0`) are the only refund-ledger rows the calculator does not produce. They are bounded by the Phase 1 `OrderLineFacts` invariants.
  - **Cancelled orders:**
    - A cancelled order's history is modelled as an **order-cancellation payment reversal**: the entire captured payment, shipping included, with `action_id` NULL.
    - It is not a refund request and not an application of RFD-2.2. The refunds and cancellations prose says so.
    - Reason: a `placed` order is already paid (enums docstring).
  - **Replacement stock fact:** a product is in stock when its available inventory is at least the requested quantity. Phase 3 must use the same definition or update the manifest.
  - **Golden and background namespaces:** background IDs are fixed-width numbers (`CUST-1xxx`, `ORD-1xxxxx`, `SKU-1xxxx`); golden IDs are words, plus `CUST-10`/`CUST-20`. Golden data uses no randomness, so a different seed changes background values only. This is verified for arbitrary seeds with Hypothesis.
  - **Seed and reset safety:**
    - Targets are named; no URL argument exists.
    - Guards run before any engine is created.
    - Logs show `target`, `database` name, and a 12-character SHA-256 identity hash only.
    - Plain seed inserts only into empty Phase-2-owned tables, is a verified no-op if every table fingerprint matches, and otherwise refuses. It never runs UPDATE, DELETE, or upsert.
    - Reset + seed is TRUNCATE (21 business and transactional tables, no CASCADE, `RESTART IDENTITY`), insert, and reconcile in **one transaction**, so a failure keeps the previous data. It preserves `policy_docs`/`policy_chunks`.
    - Test-target CLI runs take the test-database advisory lock exclusively; development seeds use a separate key.
  - **Ruff S311:** Ruff 0.17.5 flagged only the owned `random.Random(seed)` construction. It is suppressed inline on that one line with a justification (deterministic, non-security synthetic data). `pyproject.toml` is unchanged and S311 remains enabled everywhere else.
  - **Destructive integration tests:**
    - They require schema `0002` and empty Phase-2-owned tables. Otherwise they skip with the reason shown and never delete an existing test dataset.
    - A `finally` block restores the baseline (owned tables truncated, schema at head) and asserts that the policy-table counts are unchanged.
- Verification (local Windows, CPython 3.12.12, against the Supabase **test** database only):
  - `ruff check .`: all checks passed. `ruff format`: clean. mypy (strict): no issues in 35 source files.
  - Unit and Hypothesis tests: 419 passed (`tests/unit`).
  - Full non-live suite with `APP_ENV=test ALLOW_TEST_DATABASE_RESET=true`: **535 passed, 4 `live_llm` deselected, 0 skipped**. This includes all 11 synthetic integration tests (8 destructive) and the Phase 1 migration round trip.
  - CLI on the test target:
    - `reset-seed-test` without the guard was refused (exit 1, no connection).
    - `seed-test` inserted 3,531 rows and reconciliation passed.
    - A second `seed-test` was `already_present`.
    - Guarded `reset-seed-test` produced `reset_and_inserted` with the identical fingerprint.
  - With the test database seeded and the destructive guard on, all 8 destructive tests skipped with the explanation, and the dataset still reconciled afterwards.
  - Final test-database state: `0002_create_domain_schema`, all 21 Phase-2-owned tables empty, `policy_docs`/`policy_chunks` unchanged (0 rows).
  - Dependencies: none added. `pyproject.toml` and `uv.lock` unchanged.
- Not done / pending:
  - **Development database not seeded or reset.** It awaits separate approval: `tasks.ps1 migrate` (expect no-op at `0002`), then `tasks.ps1 seed-dev` twice (insert, then `already_present`), then `GET /health`.
  - GitHub Actions CI has not yet run on this branch. No workflow change is needed, because `make test` already runs the destructive tests against the ephemeral database.
  - Eval world-state fixtures (Phase 6b), the golden case/action rows for duplicate and timeout (Phase 3), and policy ingestion (Phase 4).
