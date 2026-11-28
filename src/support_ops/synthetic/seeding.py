"""Guarded seeding and reset of approved synthetic-data targets.

Targets are named, never URLs: ``development`` resolves ``DATABASE_URL`` and ``test``
resolves ``TEST_DATABASE_URL`` through ``support_ops.config``. All guards run before an
engine is created, and log lines show only a sanitized target identity.

- **Seed** (non-destructive) inserts the dataset into empty tables in one transaction, is a
  verified no-op when the persisted rows already equal the dataset, and otherwise refuses.
  It never updates or deletes rows, and never migrates.
- **Reset + seed** (destructive, separately guarded) truncates the Phase-2-owned business
  and transactional tables, inserts the dataset, and reconciles, all in one transaction, so
  any failure rolls back to the previous contents. ``policy_docs``/``policy_chunks`` are
  preserved. ``TRUNCATE`` bypasses the refund ledger's row-level append-only triggers, as
  approved in Phase 1; normal seeding never touches existing ledger rows.
"""

import hashlib
from collections.abc import Callable
from enum import StrEnum
from typing import Final

from sqlalchemy import Connection, Engine, func, insert, select, text
from sqlalchemy.engine import URL

from support_ops.config import (
    Settings,
    UnsafeTestDatabaseError,
    database_identity,
    get_settings,
    require_destructive_dev_reset,
    require_destructive_test_reset,
    resolve_development_database_url,
    resolve_test_database_url,
)
from support_ops.db.locks import DEVELOPMENT_SEED_LOCK_KEY, TEST_DATABASE_LOCK_KEY
from support_ops.db.schema import Base
from support_ops.db.session import create_db_engine
from support_ops.synthetic.dataset import PreparedDataset, prepare_dataset
from support_ops.synthetic.dataset_config import REQUIRED_SCHEMA_REVISION, SEEDED_TABLES
from support_ops.synthetic.reconcile import persisted_fingerprints, reconcile
from support_ops.synthetic.records import record_values, table_fingerprints

RESET_TABLES: Final = (
    "customers",
    "products",
    "inventory",
    "orders",
    "order_items",
    "shipments",
    "payments",
    "refunds",
    "refund_items",
    "cases",
    "actions",
    "action_items",
    "action_policy_references",
    "action_triggered_rules",
    "approvals",
    "returns",
    "return_items",
    "replacements",
    "replacement_items",
    "audit_log",
    "outbox",
)
"""Phase-2-owned business and transactional tables: checked empty before seeding and
truncated by a guarded reset."""

PRESERVED_TABLES: Final = ("policy_docs", "policy_chunks")
"""Never truncated by reset-seed (Phase 4 owns the policy corpus and its embeddings)."""

LOCK_TIMEOUT: Final = "120s"


class SeedTarget(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"


class SeedOutcome(StrEnum):
    INSERTED = "inserted"
    ALREADY_PRESENT = "already_present"
    RESET_AND_INSERTED = "reset_and_inserted"


class SeedError(RuntimeError):
    """The target database is not in a state the requested seed operation may change."""


def resolve_target_url(target: SeedTarget, settings: Settings, *, destructive: bool) -> URL:
    """Apply every guard for ``target`` and return its URL. Never creates an engine."""
    if target is SeedTarget.DEVELOPMENT:
        if destructive:
            return require_destructive_dev_reset(settings)
        return resolve_development_database_url(settings)
    if destructive:
        return require_destructive_test_reset(settings)
    if settings.app_env != "test":
        raise UnsafeTestDatabaseError("Seeding the test database requires APP_ENV=test.")
    return resolve_test_database_url(settings)


def describe_target(target: SeedTarget, url: URL) -> str:
    """Sanitized identity for logs: never the host, user, password, or URL."""
    identity = hashlib.sha256(repr(database_identity(url)).encode("utf-8")).hexdigest()[:12]
    return f"target={target.value} database={url.database or '?'} identity={identity}"


def schema_revision(connection: Connection) -> str | None:
    if connection.execute(text("SELECT to_regclass('alembic_version')")).scalar() is None:
        return None
    revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
    found = list(revisions)
    return found[0] if len(found) == 1 else None


def require_schema_revision(connection: Connection) -> None:
    revision = schema_revision(connection)
    if revision != REQUIRED_SCHEMA_REVISION:
        raise SeedError(
            f"Database schema revision is {revision!r}; synthetic data requires "
            f"{REQUIRED_SCHEMA_REVISION}. Run the migrate task first."
        )


def owned_table_counts(connection: Connection) -> dict[str, int]:
    return {
        name: int(
            connection.execute(
                select(func.count()).select_from(Base.metadata.tables[name])
            ).scalar_one()
        )
        for name in RESET_TABLES
    }


def insert_dataset(connection: Connection, prepared: PreparedDataset) -> None:
    for name, records in prepared.dataset.tables().items():
        if records:
            connection.execute(
                insert(Base.metadata.tables[name]), [record_values(r) for r in records]
            )


def seed_dataset(connection: Connection, prepared: PreparedDataset) -> SeedOutcome:
    """Insert into empty tables, or verify an identical existing dataset. Caller owns the
    transaction."""
    require_schema_revision(connection)
    counts = owned_table_counts(connection)
    if not any(counts.values()):
        insert_dataset(connection, prepared)
        return SeedOutcome.INSERTED
    others_empty = all(counts[name] == 0 for name in RESET_TABLES if name not in SEEDED_TABLES)
    if others_empty and persisted_fingerprints(connection, prepared.dataset) == (
        table_fingerprints(prepared.dataset)
    ):
        return SeedOutcome.ALREADY_PRESENT
    raise SeedError(
        f"The database already contains data that is not synthetic dataset "
        f"{prepared.dataset.dataset_version}; refusing to modify it. Use the guarded "
        "reset-seed task to replace it explicitly."
    )


def reset_owned_tables(connection: Connection) -> None:
    """TRUNCATE the Phase-2-owned tables (no CASCADE, so an unexpected dependent table fails
    loudly instead of being emptied). Caller owns the transaction and the guard."""
    table_list = ", ".join(RESET_TABLES)
    connection.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY"))


def reset_and_seed_dataset(connection: Connection, prepared: PreparedDataset) -> SeedOutcome:
    require_schema_revision(connection)
    reset_owned_tables(connection)
    insert_dataset(connection, prepared)
    return SeedOutcome.RESET_AND_INSERTED


def seed_engine(
    engine: Engine, prepared: PreparedDataset, *, reset: bool, lock_key: int | None
) -> SeedOutcome:
    """Seed (or reset + seed) in one transaction, reconcile inside it, then reconcile again
    through a new connection after commit. No guards: callers apply them first."""
    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        if lock_key is not None:
            connection.execute(select(func.pg_advisory_xact_lock(lock_key)))
        if reset:
            outcome = reset_and_seed_dataset(connection, prepared)
        else:
            outcome = seed_dataset(connection, prepared)
        reconcile(connection, prepared.dataset, prepared.manifest)
    with engine.connect() as connection:
        reconcile(connection, prepared.dataset, prepared.manifest)
    return outcome


def run_seed(
    target: SeedTarget,
    *,
    reset: bool,
    settings: Settings | None = None,
    emit: Callable[[str], None] = print,
) -> SeedOutcome:
    """Guarded entry point used by the CLI."""
    url = resolve_target_url(target, settings or get_settings(), destructive=reset)
    prepared = prepare_dataset()
    manifest = prepared.manifest
    action = "Reset and seed" if reset else "Seed"
    emit(
        f"{action} synthetic dataset {manifest.dataset_version} (seed {manifest.random_seed}, "
        f"anchor {manifest.anchor_time.isoformat()}): {describe_target(target, url)}"
    )
    lock_key = TEST_DATABASE_LOCK_KEY if target is SeedTarget.TEST else DEVELOPMENT_SEED_LOCK_KEY
    engine = create_db_engine(url)
    try:
        outcome = seed_engine(engine, prepared, reset=reset, lock_key=lock_key)
    finally:
        engine.dispose()
    emit(
        f"{outcome.value}: {sum(manifest.row_counts.values())} rows across "
        f"{len(manifest.row_counts)} tables; fingerprint {manifest.dataset_fingerprint}; "
        "reconciliation passed."
    )
    return outcome
