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