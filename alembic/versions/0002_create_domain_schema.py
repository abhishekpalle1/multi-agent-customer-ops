"""Create the Phase 1 domain schema and the append-only refund-ledger triggers.

Revision ID: 0002_create_domain_schema
Revises: 0001_enable_pgvector
Create Date: 2026-11-27

Hand-written and frozen: enum value lists are literal copies of the Python enums at this
revision (migrations are immutable history and must not import application enums). Tests
verify this DDL matches ``support_ops.db.schema``. The downgrade never drops the ``vector``
extension owned by ``0001_enable_pgvector``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_create_domain_schema"
down_revision: str | None = "0001_enable_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_STATUS = ("placed", "shipped", "delivered", "cancelled")
SHIPMENT_STATUS = ("in_transit", "delivered")
PAYMENT_STATUS = ("captured", "partially_refunded", "refunded")
CURRENCY = ("USD",)
CASE_STATUS = ("open", "escalated", "resolved")
ACTION_TYPE = ("refund", "return", "replacement", "cancellation")
ACTION_STATUS = (
    "proposed",
    "authorized",
    "awaiting_approval",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "denied",
    "rejected",
)
AUTHORIZATION_DECISION = ("allow", "deny", "requires_approval")
APPROVAL_STATUS = ("pending", "approved", "rejected")
REQUEST_REASON = ("damaged_item", "change_of_mind")
RETURN_STATUS = ("created", "label_created")
REPLACEMENT_STATUS = ("created", "shipped")
ACTOR_TYPE = ("system", "customer", "supervisor")

ID_SUFFIX = "[A-Z0-9]+([._-][A-Z0-9]+)*$"
RULE_ID_REGEX = "^[A-Z]{2,5}-[0-9]+([.][0-9]+)*$"
SHA256_REGEX = "^[0-9a-f]{64}$"

LEDGER_FUNCTION = "reject_refund_ledger_mutation"
LEDGER_TRIGGERS = {
    "refunds": "trg_refunds_append_only",
    "refund_items": "trg_refund_items_append_only",
}

# Dependency order; downgrade drops in reverse.
TABLES = (
    "customers",
    "products",
    "inventory",
    "orders",
    "order_items",
    "shipments",
    "payments",
    "cases",
    "actions",
    "action_items",
    "action_policy_references",
    "action_triggered_rules",
    "approvals",
    "refunds",
    "refund_items",
    "returns",
    "return_items",
    "replacements",
    "replacement_items",
    "audit_log",
    "outbox",
    "policy_docs",
    "policy_chunks",
)


# --- Column and constraint helpers ----------------------------------------------------


def _id(name: str, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.String(64), nullable=nullable)


def _str(name: str, length: int, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.String(length), nullable=nullable)


def _money(name: str, *, nullable: bool = False, default_zero: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.Numeric(12, 2),
        nullable=nullable,
        server_default=sa.text("0") if default_zero else None,
    )


def _int(name: str, *, default_zero: bool = False) -> sa.Column[int]:
    return sa.Column(
        name, sa.Integer(), nullable=False, server_default=sa.text("0") if default_zero else None
    )


def _tstz(name: str, *, nullable: bool = False, default_now: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def _created_at() -> sa.Column[object]:
    return _tstz("created_at", default_now=True)


def _updated_at() -> sa.Column[object]:
    return _tstz("updated_at", default_now=True)


def _enum(name: str, length: int = 32, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.String(length), nullable=nullable)


def _ck(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _enum_ck(
    table: str, column: str, enum_name: str, values: tuple[str, ...]
) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return _ck(table, enum_name, f"{column} IN ({quoted})")


def _id_format(table: str, column: str, prefix: str = "") -> sa.CheckConstraint:
    return _ck(table, f"{column}_format", f"{column} ~ '^{prefix}{ID_SUFFIX}'")


def _not_blank(table: str, column: str) -> sa.CheckConstraint:
    return _ck(table, f"{column}_not_blank", f"btrim({column}) <> ''")


def _non_negative(table: str, column: str) -> sa.CheckConstraint:
    return _ck(table, f"{column}_non_negative", f"{column} >= 0")


def _positive(table: str, column: str) -> sa.CheckConstraint:
    return _ck(table, f"{column}_positive", f"{column} > 0")


def _pk(table: str, *columns: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint(*columns, name=op.f(f"pk_{table}"))


def _uq(table: str, *columns: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint(*columns, name=op.f(f"uq_{table}_{'_'.join(columns)}"))


def _fk(
    table: str,
    columns: list[str],
    referred_table: str,
    referred_columns: list[str],
    *,
    ondelete: str | None = None,
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        columns,
        [f"{referred_table}.{column}" for column in referred_columns],
        name=op.f(f"fk_{table}_{'_'.join(columns)}_{referred_table}"),
        ondelete=ondelete,
    )


def _index(table: str, *columns: str, unique: bool = False) -> None:
    op.create_index(op.f(f"ix_{table}_{'_'.join(columns)}"), table, list(columns), unique=unique)


# --- Upgrade --------------------------------------------------------------------------


def upgrade() -> None:
    _create_catalog_and_customers()
    _create_orders_fulfillment_and_payments()
    _create_cases_actions_and_approvals()
    _create_refund_ledger()
    _create_returns_and_replacements()
    _create_audit_and_outbox()
    _create_policy_corpus()
    _create_refund_ledger_triggers()


def _create_catalog_and_customers() -> None:
    op.create_table(
        "customers",
        _id("customer_id"),
        _str("email", 320),
        _str("full_name", 200),
        _created_at(),
        _pk("customers", "customer_id"),
        _uq("customers", "email"),
        _id_format("customers", "customer_id", "CUST-"),
        _not_blank("customers", "email"),
        _not_blank("customers", "full_name"),
    )
    op.create_table(
        "products",
        _id("product_id"),
        _str("name", 200),
        _str("category", 64),
        sa.Column("is_returnable", sa.Boolean(), nullable=False),
        _created_at(),
        _pk("products", "product_id"),
        _id_format("products", "product_id"),
        _not_blank("products", "name"),
        _not_blank("products", "category"),
    )
    op.create_table(
        "inventory",
        _id("product_id"),
        _int("available_quantity"),
        _updated_at(),
        _pk("inventory", "product_id"),
        _fk("inventory", ["product_id"], "products", ["product_id"]),
        _non_negative("inventory", "available_quantity"),
    )


def _create_orders_fulfillment_and_payments() -> None:
    op.create_table(
        "orders",
        _id("order_id"),
        _id("customer_id"),
        _enum("status"),
        _enum("currency", 3),
        _money("shipping_amount"),
        _money("shipping_refunded_amount", default_zero=True),
        _tstz("placed_at"),
        _created_at(),
        _updated_at(),
        _pk("orders", "order_id"),
        _uq("orders", "order_id", "customer_id"),
        _fk("orders", ["customer_id"], "customers", ["customer_id"]),
        _id_format("orders", "order_id", "ORD-"),
        _non_negative("orders", "shipping_amount"),
        _ck(
            "orders",
            "shipping_refunded_amount_within_shipping",
            "shipping_refunded_amount >= 0 AND shipping_refunded_amount <= shipping_amount",
        ),
        _enum_ck("orders", "status", "order_status", ORDER_STATUS),
        _enum_ck("orders", "currency", "currency", CURRENCY),
    )
    _index("orders", "customer_id")

    op.create_table(
        "order_items",
        _id("order_item_id"),
        _id("order_id"),
        _id("product_id"),
        _int("quantity"),
        _money("unit_price"),
        _money("discount_amount"),
        _money("tax_amount"),
        _int("refunded_quantity", default_zero=True),
        _money("refunded_amount", default_zero=True),
        _created_at(),
        _updated_at(),
        _pk("order_items", "order_item_id"),
        _uq("order_items", "order_id", "order_item_id"),
        _fk("order_items", ["order_id"], "orders", ["order_id"]),
        _fk("order_items", ["product_id"], "products", ["product_id"]),
        _id_format("order_items", "order_item_id"),
        _positive("order_items", "quantity"),
        _non_negative("order_items", "unit_price"),
        _ck(
            "order_items",
            "discount_amount_within_gross",
            "discount_amount >= 0 AND discount_amount <= unit_price * quantity",
        ),
        _non_negative("order_items", "tax_amount"),
        _ck(
            "order_items",
            "refunded_quantity_within_quantity",
            "refunded_quantity >= 0 AND refunded_quantity <= quantity",
        ),
        _ck(
            "order_items",
            "refunded_amount_within_paid_total",
            "refunded_amount >= 0 AND "
            "refunded_amount <= unit_price * quantity - discount_amount + tax_amount",
        ),
    )
    _index("order_items", "order_id")
    _index("order_items", "product_id")

    op.create_table(
        "shipments",
        _id("shipment_id"),
        _id("order_id"),
        _str("carrier", 64),
        _str("tracking_number", 64),
        _enum("status"),
        _tstz("shipped_at"),
        sa.Column("estimated_delivery_date", sa.Date(), nullable=True),
        _tstz("delivered_at", nullable=True),
        _created_at(),
        _updated_at(),
        _pk("shipments", "shipment_id"),
        _uq("shipments", "order_id"),
        _uq("shipments", "tracking_number"),
        _fk("shipments", ["order_id"], "orders", ["order_id"]),
        _id_format("shipments", "shipment_id"),
        _not_blank("shipments", "carrier"),
        _not_blank("shipments", "tracking_number"),
        _ck(
            "shipments",
            "delivered_status_matches_delivered_at",
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
        ),
        _ck(
            "shipments",
            "delivered_after_shipped",
            "delivered_at IS NULL OR delivered_at >= shipped_at",
        ),
        _enum_ck("shipments", "status", "shipment_status", SHIPMENT_STATUS),
    )

    op.create_table(
        "payments",
        _id("payment_id"),
        _id("order_id"),
        _enum("currency", 3),
        _money("amount"),
        _money("amount_refunded", default_zero=True),
        _enum("status"),
        _tstz("captured_at"),
        _created_at(),
        _updated_at(),
        _pk("payments", "payment_id"),
        _uq("payments", "order_id"),
        _uq("payments", "payment_id", "order_id"),
        _fk("payments", ["order_id"], "orders", ["order_id"]),
        _id_format("payments", "payment_id"),
        _positive("payments", "amount"),
        _ck(
            "payments",
            "amount_refunded_within_amount",
            "amount_refunded >= 0 AND amount_refunded <= amount",
        ),
        _ck(
            "payments",
            "status_matches_amount_refunded",
            "(status = 'captured' AND amount_refunded = 0) OR "
            "(status = 'partially_refunded' AND amount_refunded > 0 "
            "AND amount_refunded < amount) OR "
            "(status = 'refunded' AND amount_refunded = amount)",
        ),
        _enum_ck("payments", "currency", "currency", CURRENCY),
        _enum_ck("payments", "status", "payment_status", PAYMENT_STATUS),
    )


def _create_cases_actions_and_approvals() -> None:
    op.create_table(
        "cases",
        _id("case_id"),
        _id("customer_id"),
        _id("order_id", nullable=True),
        _enum("status"),
        _created_at(),
        _updated_at(),
        _pk("cases", "case_id"),
        _fk("cases", ["customer_id"], "customers", ["customer_id"]),
        _fk("cases", ["order_id", "customer_id"], "orders", ["order_id", "customer_id"]),
        _id_format("cases", "case_id", "CASE-"),
        _enum_ck("cases", "status", "case_status", CASE_STATUS),
    )
    _index("cases", "customer_id")
    _index("cases", "order_id")

    execution_started = "('executing', 'succeeded', 'failed', 'unknown')"
    op.create_table(
        "actions",
        _id("action_id"),
        _id("case_id"),
        _id("order_id"),
        _enum("action_type"),
        _enum("status"),
        _enum("reason"),
        _money("amount", nullable=True),
        _str("idempotency_key", 64),
        _enum("authorization_decision", nullable=True),
        sa.Column("authorization_reason", sa.Text(), nullable=True),
        _tstz("decided_at", nullable=True),
        _tstz("execution_started_at", nullable=True),
        _tstz("completed_at", nullable=True),
        _created_at(),
        _updated_at(),
        _pk("actions", "action_id"),
        _uq("actions", "action_id", "order_id"),
        _uq("actions", "idempotency_key"),
        _fk("actions", ["case_id"], "cases", ["case_id"]),
        _fk("actions", ["order_id"], "orders", ["order_id"]),
        _id_format("actions", "action_id", "ACT-"),
        _ck("actions", "amount_non_negative", "amount IS NULL OR amount >= 0"),
        _ck(
            "actions",
            "refund_requires_positive_amount",
            "action_type <> 'refund' OR (amount IS NOT NULL AND amount > 0)",
        ),
        _ck("actions", "idempotency_key_format", f"idempotency_key ~ '{SHA256_REGEX}'"),
        # Decision-bearing states require IS NOT NULL explicitly: without it, a NULL decision
        # makes its branch UNKNOWN rather than FALSE, and PostgreSQL accepts UNKNOWN CHECKs.
        _ck(
            "actions",
            "status_matches_authorization_decision",
            "(status = 'proposed' AND authorization_decision IS NULL) OR "
            "(status = 'denied' AND authorization_decision IS NOT NULL "
            "AND authorization_decision = 'deny') OR "
            "(status IN ('awaiting_approval', 'rejected') AND authorization_decision IS NOT NULL "
            "AND authorization_decision = 'requires_approval') OR "
            "(status IN ('authorized', 'executing', 'succeeded', 'failed', 'unknown') "
            "AND authorization_decision IS NOT NULL "
            "AND authorization_decision IN ('allow', 'requires_approval'))",
        ),
        _ck(
            "actions",
            "decided_at_matches_decision",
            "(authorization_decision IS NULL) = (decided_at IS NULL)",
        ),
        _ck(
            "actions",
            "execution_started_at_matches_status",
            f"(status IN {execution_started}) = (execution_started_at IS NOT NULL)",
        ),
        _ck(
            "actions",
            "completed_at_matches_status",
            "(status IN ('succeeded', 'failed')) = (completed_at IS NOT NULL)",
        ),
        _enum_ck("actions", "action_type", "action_type", ACTION_TYPE),
        _enum_ck("actions", "status", "action_status", ACTION_STATUS),
        _enum_ck("actions", "reason", "request_reason", REQUEST_REASON),
        _enum_ck(
            "actions", "authorization_decision", "authorization_decision", AUTHORIZATION_DECISION
        ),
    )
    _index("actions", "case_id")
    _index("actions", "order_id")
    _index("actions", "status")

    op.create_table(
        "action_items",
        _id("action_id"),
        _id("order_item_id"),
        _id("order_id"),
        _int("quantity"),
        _pk("action_items", "action_id", "order_item_id"),
        _fk("action_items", ["action_id", "order_id"], "actions", ["action_id", "order_id"]),
        _fk(
            "action_items",
            ["order_id", "order_item_id"],
            "order_items",
            ["order_id", "order_item_id"],
        ),
        _positive("action_items", "quantity"),
    )
    op.create_table(
        "action_policy_references",
        _id("action_id"),
        _id("policy_reference"),
        _pk("action_policy_references", "action_id", "policy_reference"),
        _fk("action_policy_references", ["action_id"], "actions", ["action_id"]),
        _ck(
            "action_policy_references",
            "policy_reference_format",
            f"policy_reference ~ '{RULE_ID_REGEX}'",
        ),
    )
    op.create_table(
        "action_triggered_rules",
        _id("action_id"),
        _id("rule_id"),
        _pk("action_triggered_rules", "action_id", "rule_id"),
        _fk("action_triggered_rules", ["action_id"], "actions", ["action_id"]),
        _ck("action_triggered_rules", "rule_id_format", f"rule_id ~ '{RULE_ID_REGEX}'"),
    )

    op.create_table(
        "approvals",
        _id("approval_id"),
        _id("action_id"),
        _enum("status"),
        _tstz("requested_at", default_now=True),
        _tstz("decided_at", nullable=True),
        _id("decided_by", nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        _updated_at(),
        _pk("approvals", "approval_id"),
        _uq("approvals", "action_id"),
        _fk("approvals", ["action_id"], "actions", ["action_id"]),
        _id_format("approvals", "approval_id"),
        _ck(
            "approvals", "decided_at_matches_status", "(status = 'pending') = (decided_at IS NULL)"
        ),
        _ck(
            "approvals", "decided_by_matches_status", "(status = 'pending') = (decided_by IS NULL)"
        ),
        _ck(
            "approvals",
            "rejection_requires_reason",
            "status <> 'rejected' OR btrim(coalesce(decision_reason, '')) <> ''",
        ),
        _ck(
            "approvals",
            "decided_after_requested",
            "decided_at IS NULL OR decided_at >= requested_at",
        ),
        _not_blank("approvals", "decided_by"),
        _enum_ck("approvals", "status", "approval_status", APPROVAL_STATUS),
    )
    _index("approvals", "status")


def _create_refund_ledger() -> None:
    op.create_table(
        "refunds",
        _id("refund_id"),
        _id("order_id"),
        _id("payment_id"),
        _id("action_id", nullable=True),
        _money("amount"),
        _money("shipping_amount", default_zero=True),
        _tstz("refunded_at"),
        _created_at(),
        _pk("refunds", "refund_id"),
        _uq("refunds", "refund_id", "order_id"),
        _uq("refunds", "action_id"),
        _fk("refunds", ["payment_id", "order_id"], "payments", ["payment_id", "order_id"]),
        _fk("refunds", ["action_id", "order_id"], "actions", ["action_id", "order_id"]),
        _id_format("refunds", "refund_id"),
        _positive("refunds", "amount"),
        _ck(
            "refunds",
            "shipping_amount_within_amount",
            "shipping_amount >= 0 AND shipping_amount <= amount",
        ),
    )
    _index("refunds", "order_id")
    _index("refunds", "payment_id")

    op.create_table(
        "refund_items",
        _id("refund_id"),
        _id("order_item_id"),
        _id("order_id"),
        _int("quantity"),
        _money("amount"),
        _created_at(),
        _pk("refund_items", "refund_id", "order_item_id"),
        _fk("refund_items", ["refund_id", "order_id"], "refunds", ["refund_id", "order_id"]),
        _fk(
            "refund_items",
            ["order_id", "order_item_id"],
            "order_items",
            ["order_id", "order_item_id"],
        ),
        _non_negative("refund_items", "quantity"),
        _non_negative("refund_items", "amount"),
        _ck("refund_items", "quantity_or_amount_positive", "quantity > 0 OR amount > 0"),
    )


def _create_returns_and_replacements() -> None:
    op.create_table(
        "returns",
        _id("return_id"),
        _id("order_id"),
        _id("case_id", nullable=True),
        _id("action_id", nullable=True),
        _enum("status"),
        _enum("reason"),
        _str("label_code", 64, nullable=True),
        _tstz("label_created_at", nullable=True),
        _created_at(),
        _updated_at(),
        _pk("returns", "return_id"),
        _uq("returns", "return_id", "order_id"),
        _uq("returns", "action_id"),
        _uq("returns", "label_code"),
        _fk("returns", ["order_id"], "orders", ["order_id"]),
        _fk("returns", ["case_id"], "cases", ["case_id"]),
        _fk("returns", ["action_id", "order_id"], "actions", ["action_id", "order_id"]),
        _id_format("returns", "return_id"),
        _not_blank("returns", "label_code"),
        _ck(
            "returns",
            "label_status_matches_label_code",
            "(status = 'label_created') = (label_code IS NOT NULL)",
        ),
        _ck(
            "returns",
            "label_code_matches_label_created_at",
            "(label_code IS NULL) = (label_created_at IS NULL)",
        ),
        _enum_ck("returns", "status", "return_status", RETURN_STATUS),
        _enum_ck("returns", "reason", "request_reason", REQUEST_REASON),
    )
    _index("returns", "order_id")
    _index("returns", "case_id")

    op.create_table(
        "return_items",
        _id("return_id"),
        _id("order_item_id"),
        _id("order_id"),
        _int("quantity"),
        _pk("return_items", "return_id", "order_item_id"),
        _fk("return_items", ["return_id", "order_id"], "returns", ["return_id", "order_id"]),
        _fk(
            "return_items",
            ["order_id", "order_item_id"],
            "order_items",
            ["order_id", "order_item_id"],
        ),
        _positive("return_items", "quantity"),
    )

    op.create_table(
        "replacements",
        _id("replacement_id"),
        _id("order_id"),
        _id("action_id", nullable=True),
        _id("return_id", nullable=True),
        _enum("status"),
        _tstz("shipped_at", nullable=True),
        _created_at(),
        _updated_at(),
        _pk("replacements", "replacement_id"),
        _uq("replacements", "replacement_id", "order_id"),
        _uq("replacements", "action_id"),
        _fk("replacements", ["order_id"], "orders", ["order_id"]),
        _fk("replacements", ["action_id", "order_id"], "actions", ["action_id", "order_id"]),
        _fk("replacements", ["return_id", "order_id"], "returns", ["return_id", "order_id"]),
        _id_format("replacements", "replacement_id"),
        _ck(
            "replacements",
            "shipped_status_matches_shipped_at",
            "(status = 'shipped') = (shipped_at IS NOT NULL)",
        ),
        _enum_ck("replacements", "status", "replacement_status", REPLACEMENT_STATUS),
    )
    _index("replacements", "order_id")
    _index("replacements", "return_id")

    op.create_table(
        "replacement_items",
        _id("replacement_id"),
        _id("order_item_id"),
        _id("order_id"),
        _int("quantity"),
        _pk("replacement_items", "replacement_id", "order_item_id"),
        _fk(
            "replacement_items",
            ["replacement_id", "order_id"],
            "replacements",
            ["replacement_id", "order_id"],
        ),
        _fk(
            "replacement_items",
            ["order_id", "order_item_id"],
            "order_items",
            ["order_id", "order_item_id"],
        ),
        _positive("replacement_items", "quantity"),
    )


def _create_audit_and_outbox() -> None:
    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _tstz("occurred_at", default_now=True),
        _enum("actor_type"),
        _id("actor_id", nullable=True),
        _str("event_type", 64),
        _str("entity_type", 64),
        _id("entity_id"),
        _id("case_id", nullable=True),
        _id("action_id", nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _pk("audit_log", "audit_id"),
        _fk("audit_log", ["case_id"], "cases", ["case_id"]),
        _fk("audit_log", ["action_id"], "actions", ["action_id"]),
        _not_blank("audit_log", "event_type"),
        _not_blank("audit_log", "entity_type"),
        _not_blank("audit_log", "entity_id"),
        _not_blank("audit_log", "actor_id"),
        _enum_ck("audit_log", "actor_type", "actor_type", ACTOR_TYPE),
    )
    _index("audit_log", "occurred_at")
    _index("audit_log", "case_id")
    _index("audit_log", "action_id")
    _index("audit_log", "entity_type", "entity_id")

    op.create_table(
        "outbox",
        sa.Column("outbox_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _str("event_type", 64),
        _str("aggregate_type", 64),
        _id("aggregate_id"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        _tstz("published_at", nullable=True),
        _pk("outbox", "outbox_id"),
        _not_blank("outbox", "event_type"),
        _not_blank("outbox", "aggregate_type"),
        _not_blank("outbox", "aggregate_id"),
        _ck(
            "outbox",
            "published_after_created",
            "published_at IS NULL OR published_at >= created_at",
        ),
    )
    op.create_index(
        op.f("ix_outbox_unpublished"),
        "outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def _create_policy_corpus() -> None:
    # Embedding, tsvector, vector-dimension, and ANN-index decisions belong to Phase 4.
    op.create_table(
        "policy_docs",
        _id("policy_doc_id"),
        _str("policy_key", 64),
        sa.Column("version", sa.Integer(), nullable=False),
        _str("title", 200),
        _str("source_path", 255),
        _str("content_sha256", 64),
        _tstz("effective_from"),
        _tstz("effective_until", nullable=True),
        _created_at(),
        _pk("policy_docs", "policy_doc_id"),
        _uq("policy_docs", "policy_key", "version"),
        _id_format("policy_docs", "policy_doc_id"),
        _ck("policy_docs", "policy_key_format", "policy_key ~ '^[a-z0-9]+(_[a-z0-9]+)*$'"),
        _positive("policy_docs", "version"),
        _not_blank("policy_docs", "title"),
        _not_blank("policy_docs", "source_path"),
        _ck("policy_docs", "content_sha256_format", f"content_sha256 ~ '{SHA256_REGEX}'"),
        _ck(
            "policy_docs",
            "effective_window_valid",
            "effective_until IS NULL OR effective_until > effective_from",
        ),
    )
    op.create_table(
        "policy_chunks",
        _id("chunk_id"),
        _id("policy_doc_id"),
        _id("section_id"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        _str("heading", 255),
        sa.Column("content", sa.Text(), nullable=False),
        _str("content_sha256", 64),
        _created_at(),
        _pk("policy_chunks", "chunk_id"),
        _uq("policy_chunks", "policy_doc_id", "ordinal"),
        _fk(
            "policy_chunks",
            ["policy_doc_id"],
            "policy_docs",
            ["policy_doc_id"],
            ondelete="CASCADE",
        ),
        _id_format("policy_chunks", "chunk_id"),
        _not_blank("policy_chunks", "section_id"),
        _non_negative("policy_chunks", "ordinal"),
        _not_blank("policy_chunks", "heading"),
        _not_blank("policy_chunks", "content"),
        _ck("policy_chunks", "content_sha256_format", f"content_sha256 ~ '{SHA256_REGEX}'"),
    )
    _index("policy_chunks", "policy_doc_id")
    _index("policy_chunks", "section_id")


def _create_refund_ledger_triggers() -> None:
    # Row-level triggers do not fire for TRUNCATE or DROP TABLE, so controlled test resets
    # and migration downgrades still work. The message names only the table and operation,
    # never row contents. (No '%' placeholders: string concatenation avoids driver escaping.)
    op.execute(
        f"""
        CREATE FUNCTION {LEDGER_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                MESSAGE = 'refund ledger table ' || TG_TABLE_NAME
                    || ' is append-only; ' || TG_OP || ' is not permitted',
                ERRCODE = 'integrity_constraint_violation',
                HINT = 'Record a compensating refund ledger entry instead of changing history.';
        END;
        $$
        """
    )
    for table, trigger in LEDGER_TRIGGERS.items():
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {LEDGER_FUNCTION}()"
        )


# --- Downgrade ------------------------------------------------------------------------


def downgrade() -> None:
    for table, trigger in LEDGER_TRIGGERS.items():
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {LEDGER_FUNCTION}()")
    for table in reversed(TABLES):
        op.drop_table(table)
    # The vector extension belongs to 0001_enable_pgvector and is intentionally kept.
