"""Enable the pgvector extension.

Revision ID: 0001_enable_pgvector
Revises:
Create Date: 2026-11-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_enable_pgvector"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def downgrade() -> None:
    # Intentionally a no-op: the extension may be shared (e.g. a Supabase project), and
    # dropping it would destroy data owned outside this migration history.
    pass
