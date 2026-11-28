"""PostgreSQL persistence schema (SQLAlchemy 2.0 typed declarative mappings).

ORM rows here map persistent state; they do not replace the Pydantic domain contracts, and
nothing in this module authorizes or executes anything. Migration ``0002`` is the
authoritative DDL; an integration test asserts this metadata and the migrated database do
not drift apart.

Conventions:

- Business tables use readable text primary keys (``VARCHAR(64)``) with format CHECKs;
  ``audit_log`` and ``outbox`` use identity ``BIGINT`` keys.
- Money is ``NUMERIC(12,2)`` USD; floating-point columns are never used.
- Timestamps are ``TIMESTAMPTZ``.
- Enums are stored as ``VARCHAR`` with a CHECK constraint generated from the Python enum,
  which stays the source of truth (no PostgreSQL ENUM types).
- Child item tables use composite foreign keys that include ``order_id`` so an item of one
  order can never be attached to a refund/return/replacement/action of another order.

Refund ledger and aggregate balances:

- ``refunds`` and ``refund_items`` are the immutable, append-only refund ledger and the
  source of truth for money returned. A row is written only for a successfully verified
  refund; pending/failed/unknown outcomes live on ``actions``. Database triggers created by
  migration ``0002`` reject row-level ``UPDATE`` and ``DELETE``. Corrections are recorded as
  new compensating ledger rows, never by mutating history.
- ``payments.amount_refunded``, ``order_items.refunded_quantity``,
  ``order_items.refunded_amount``, and ``orders.shipping_refunded_amount`` are maintained
  aggregate balances used for row locking and fast authorization. CHECK constraints only
  bound each balance within its own row; they cannot prove the balances equal the ledger
  sums. Phase 3 must lock the affected rows and write the ledger rows and the balance updates
  atomically in one database transaction.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from support_ops.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    AuditActorType,
    AuthorizationDecision,
    CaseStatus,
    Currency,
    OrderStatus,
    PaymentStatus,
    ReplacementStatus,
    RequestReason,
    ReturnStatus,
    ShipmentStatus,
)

NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY_PRECISION: Final = 12
MONEY_SCALE: Final = 2
ID_LENGTH: Final = 64

_ID_SUFFIX = "[A-Z0-9]+([._-][A-Z0-9]+)*$"
_RULE_ID_REGEX = "^[A-Z]{2,5}-[0-9]+([.][0-9]+)*$"
_SHA256_REGEX = "^[0-9a-f]{64}$"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _money() -> Numeric[Decimal]:
    return Numeric(MONEY_PRECISION, MONEY_SCALE)


def _tstz() -> DateTime:
    return DateTime(timezone=True)


def _id() -> String:
    return String(ID_LENGTH)


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _enum(enum_cls: type[StrEnum], name: str, *, length: int = 32) -> Enum:
    """VARCHAR + CHECK constraint named ``ck_<table>_<name>``, values from the Python enum."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        values_callable=_enum_values,
        validate_strings=True,
    )


def _id_format(column: str, prefix: str = "") -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^{prefix}{_ID_SUFFIX}'", name=f"{column}_format")


def _not_blank(column: str) -> CheckConstraint:
    return CheckConstraint(f"btrim({column}) <> ''", name=f"{column}_not_blank")


def _non_negative(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} >= 0", name=f"{column}_non_negative")


def _positive(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} > 0", name=f"{column}_positive")


class _CreatedAt:
    created_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())


class _Timestamps(_CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        _tstz(), server_default=func.now(), onupdate=func.now()
    )


# --- Catalog and customers -------------------------------------------------------------


class Customer(_CreatedAt, Base):
    __tablename__ = "customers"
    __table_args__ = (
        _id_format("customer_id", "CUST-"),
        _not_blank("email"),
        _not_blank("full_name"),
    )

    customer_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Product(_CreatedAt, Base):
    __tablename__ = "products"
    __table_args__ = (_id_format("product_id"), _not_blank("name"), _not_blank("category"))

    product_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(64))
    is_returnable: Mapped[bool] = mapped_column(Boolean)


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (_non_negative("available_quantity"),)

    product_id: Mapped[str] = mapped_column(
        _id(), ForeignKey("products.product_id"), primary_key=True
    )
    available_quantity: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        _tstz(), server_default=func.now(), onupdate=func.now()
    )


# --- Orders, fulfillment, payment ------------------------------------------------------


class Order(_Timestamps, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("order_id", "customer_id"),
        _id_format("order_id", "ORD-"),
        _non_negative("shipping_amount"),
        CheckConstraint(
            "shipping_refunded_amount >= 0 AND shipping_refunded_amount <= shipping_amount",
            name="shipping_refunded_amount_within_shipping",
        ),
    )

    order_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    customer_id: Mapped[str] = mapped_column(_id(), ForeignKey("customers.customer_id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus, "order_status"))
    currency: Mapped[Currency] = mapped_column(_enum(Currency, "currency", length=3))
    shipping_amount: Mapped[Decimal] = mapped_column(_money())
    shipping_refunded_amount: Mapped[Decimal] = mapped_column(_money(), server_default=text("0"))
    placed_at: Mapped[datetime] = mapped_column(_tstz())

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


class OrderItem(_Timestamps, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "order_item_id"),
        _id_format("order_item_id"),
        _positive("quantity"),
        _non_negative("unit_price"),
        CheckConstraint(
            "discount_amount >= 0 AND discount_amount <= unit_price * quantity",
            name="discount_amount_within_gross",
        ),
        _non_negative("tax_amount"),
        CheckConstraint(
            "refunded_quantity >= 0 AND refunded_quantity <= quantity",
            name="refunded_quantity_within_quantity",
        ),
        CheckConstraint(
            "refunded_amount >= 0 AND "
            "refunded_amount <= unit_price * quantity - discount_amount + tax_amount",
            name="refunded_amount_within_paid_total",
        ),
    )

    order_item_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id(), ForeignKey("orders.order_id"), index=True)
    product_id: Mapped[str] = mapped_column(_id(), ForeignKey("products.product_id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(_money())
    discount_amount: Mapped[Decimal] = mapped_column(_money())
    tax_amount: Mapped[Decimal] = mapped_column(_money())
    refunded_quantity: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    refunded_amount: Mapped[Decimal] = mapped_column(_money(), server_default=text("0"))

    order: Mapped[Order] = relationship(back_populates="items")


class Shipment(_Timestamps, Base):
    """The order's single shipment (one shipment per order in the current scope)."""

    __tablename__ = "shipments"
    __table_args__ = (
        _id_format("shipment_id"),
        _not_blank("carrier"),
        _not_blank("tracking_number"),
        CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name="delivered_status_matches_delivered_at",
        ),
        CheckConstraint(
            "delivered_at IS NULL OR delivered_at >= shipped_at", name="delivered_after_shipped"
        ),
    )

    shipment_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id(), ForeignKey("orders.order_id"), unique=True)
    carrier: Mapped[str] = mapped_column(String(64))
    tracking_number: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[ShipmentStatus] = mapped_column(_enum(ShipmentStatus, "shipment_status"))
    shipped_at: Mapped[datetime] = mapped_column(_tstz())
    estimated_delivery_date: Mapped[date | None] = mapped_column(Date)
    delivered_at: Mapped[datetime | None] = mapped_column(_tstz())


class Payment(_Timestamps, Base):
    """The order's single captured payment. ``amount_refunded`` is a maintained balance."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("payment_id", "order_id"),
        _id_format("payment_id"),
        _positive("amount"),
        CheckConstraint(
            "amount_refunded >= 0 AND amount_refunded <= amount",
            name="amount_refunded_within_amount",
        ),
        CheckConstraint(
            "(status = 'captured' AND amount_refunded = 0) OR "
            "(status = 'partially_refunded' AND amount_refunded > 0 "
            "AND amount_refunded < amount) OR "
            "(status = 'refunded' AND amount_refunded = amount)",
            name="status_matches_amount_refunded",
        ),
    )

    payment_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id(), ForeignKey("orders.order_id"), unique=True)
    currency: Mapped[Currency] = mapped_column(_enum(Currency, "currency", length=3))
    amount: Mapped[Decimal] = mapped_column(_money())
    amount_refunded: Mapped[Decimal] = mapped_column(_money(), server_default=text("0"))
    status: Mapped[PaymentStatus] = mapped_column(_enum(PaymentStatus, "payment_status"))
    captured_at: Mapped[datetime] = mapped_column(_tstz())


# --- Cases, actions, approvals ---------------------------------------------------------


class Case(_Timestamps, Base):
    __tablename__ = "cases"
    __table_args__ = (
        # A case may only reference an order owned by the case's customer.
        ForeignKeyConstraint(
            ["order_id", "customer_id"], ["orders.order_id", "orders.customer_id"]
        ),
        _id_format("case_id", "CASE-"),
    )

    case_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    customer_id: Mapped[str] = mapped_column(_id(), ForeignKey("customers.customer_id"), index=True)
    order_id: Mapped[str | None] = mapped_column(_id(), index=True)
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus, "case_status"))


_EXECUTION_STARTED = "('executing', 'succeeded', 'failed', 'unknown')"


class Action(_Timestamps, Base):
    """A proposed/authorized consequential action. Lifecycle rules: ``state_machine``."""

    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("action_id", "order_id"),
        _id_format("action_id", "ACT-"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="amount_non_negative"),
        CheckConstraint(
            "action_type <> 'refund' OR (amount IS NOT NULL AND amount > 0)",
            name="refund_requires_positive_amount",
        ),
        CheckConstraint(f"idempotency_key ~ '{_SHA256_REGEX}'", name="idempotency_key_format"),
        # Decision-bearing states require IS NOT NULL explicitly: without it, a NULL decision
        # makes its branch UNKNOWN rather than FALSE, and PostgreSQL accepts UNKNOWN CHECKs.
        CheckConstraint(
            "(status = 'proposed' AND authorization_decision IS NULL) OR "
            "(status = 'denied' AND authorization_decision IS NOT NULL "
            "AND authorization_decision = 'deny') OR "
            "(status IN ('awaiting_approval', 'rejected') AND authorization_decision IS NOT NULL "
            "AND authorization_decision = 'requires_approval') OR "
            "(status IN ('authorized', 'executing', 'succeeded', 'failed', 'unknown') "
            "AND authorization_decision IS NOT NULL "
            "AND authorization_decision IN ('allow', 'requires_approval'))",
            name="status_matches_authorization_decision",
        ),
        CheckConstraint(
            "(authorization_decision IS NULL) = (decided_at IS NULL)",
            name="decided_at_matches_decision",
        ),
        CheckConstraint(
            f"(status IN {_EXECUTION_STARTED}) = (execution_started_at IS NOT NULL)",
            name="execution_started_at_matches_status",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (completed_at IS NOT NULL)",
            name="completed_at_matches_status",
        ),
    )

    action_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    case_id: Mapped[str] = mapped_column(_id(), ForeignKey("cases.case_id"), index=True)
    order_id: Mapped[str] = mapped_column(_id(), ForeignKey("orders.order_id"), index=True)
    action_type: Mapped[ActionType] = mapped_column(_enum(ActionType, "action_type"))
    status: Mapped[ActionStatus] = mapped_column(_enum(ActionStatus, "action_status"), index=True)
    reason: Mapped[RequestReason] = mapped_column(_enum(RequestReason, "request_reason"))
    amount: Mapped[Decimal | None] = mapped_column(_money())
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    authorization_decision: Mapped[AuthorizationDecision | None] = mapped_column(
        _enum(AuthorizationDecision, "authorization_decision")
    )
    authorization_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(_tstz())
    execution_started_at: Mapped[datetime | None] = mapped_column(_tstz())
    completed_at: Mapped[datetime | None] = mapped_column(_tstz())

    items: Mapped[list["ActionItem"]] = relationship()
    policy_references: Mapped[list["ActionPolicyReference"]] = relationship()
    triggered_rules: Mapped[list["ActionTriggeredRule"]] = relationship()


class ActionItem(Base):
    __tablename__ = "action_items"
    __table_args__ = (
        ForeignKeyConstraint(["action_id", "order_id"], ["actions.action_id", "actions.order_id"]),
        ForeignKeyConstraint(
            ["order_id", "order_item_id"], ["order_items.order_id", "order_items.order_item_id"]
        ),
        _positive("quantity"),
    )

    action_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id())
    quantity: Mapped[int] = mapped_column(Integer)


class ActionPolicyReference(Base):
    """A policy section/rule ID cited by the action's proposal."""

    __tablename__ = "action_policy_references"
    __table_args__ = (
        CheckConstraint(f"policy_reference ~ '{_RULE_ID_REGEX}'", name="policy_reference_format"),
    )

    action_id: Mapped[str] = mapped_column(_id(), ForeignKey("actions.action_id"), primary_key=True)
    policy_reference: Mapped[str] = mapped_column(_id(), primary_key=True)


class ActionTriggeredRule(Base):
    """An executable-policy rule ID that deterministic code triggered for the action."""

    __tablename__ = "action_triggered_rules"
    __table_args__ = (CheckConstraint(f"rule_id ~ '{_RULE_ID_REGEX}'", name="rule_id_format"),)

    action_id: Mapped[str] = mapped_column(_id(), ForeignKey("actions.action_id"), primary_key=True)
    rule_id: Mapped[str] = mapped_column(_id(), primary_key=True)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        _id_format("approval_id"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)", name="decided_at_matches_status"
        ),
        CheckConstraint(
            "(status = 'pending') = (decided_by IS NULL)", name="decided_by_matches_status"
        ),
        CheckConstraint(
            "status <> 'rejected' OR btrim(coalesce(decision_reason, '')) <> ''",
            name="rejection_requires_reason",
        ),
        CheckConstraint(
            "decided_at IS NULL OR decided_at >= requested_at", name="decided_after_requested"
        ),
        _not_blank("decided_by"),
    )

    approval_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    action_id: Mapped[str] = mapped_column(_id(), ForeignKey("actions.action_id"), unique=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus, "approval_status"), index=True
    )
    requested_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(_tstz())
    decided_by: Mapped[str | None] = mapped_column(_id())
    decision_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        _tstz(), server_default=func.now(), onupdate=func.now()
    )


# --- Refund ledger (append-only) ---------------------------------------------------------


class Refund(_CreatedAt, Base):
    """Immutable ledger entry for one successfully verified refund."""

    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("refund_id", "order_id"),
        ForeignKeyConstraint(
            ["payment_id", "order_id"], ["payments.payment_id", "payments.order_id"]
        ),
        ForeignKeyConstraint(["action_id", "order_id"], ["actions.action_id", "actions.order_id"]),
        _id_format("refund_id"),
        _positive("amount"),
        CheckConstraint(
            "shipping_amount >= 0 AND shipping_amount <= amount",
            name="shipping_amount_within_amount",
        ),
    )

    refund_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id(), index=True)
    payment_id: Mapped[str] = mapped_column(_id(), index=True)
    action_id: Mapped[str | None] = mapped_column(_id(), unique=True)
    amount: Mapped[Decimal] = mapped_column(_money())
    shipping_amount: Mapped[Decimal] = mapped_column(_money(), server_default=text("0"))
    refunded_at: Mapped[datetime] = mapped_column(_tstz())

    items: Mapped[list["RefundItem"]] = relationship()


class RefundItem(_CreatedAt, Base):
    """Immutable per-item ledger line. ``quantity = 0`` records an amount-only refund."""

    __tablename__ = "refund_items"
    __table_args__ = (
        ForeignKeyConstraint(["refund_id", "order_id"], ["refunds.refund_id", "refunds.order_id"]),
        ForeignKeyConstraint(
            ["order_id", "order_item_id"], ["order_items.order_id", "order_items.order_item_id"]
        ),
        _non_negative("quantity"),
        _non_negative("amount"),
        CheckConstraint("quantity > 0 OR amount > 0", name="quantity_or_amount_positive"),
    )

    refund_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id())
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(_money())


# --- Returns and replacements ---------------------------------------------------------


class Return(_Timestamps, Base):
    __tablename__ = "returns"
    __table_args__ = (
        UniqueConstraint("return_id", "order_id"),
        ForeignKeyConstraint(["action_id", "order_id"], ["actions.action_id", "actions.order_id"]),
        _id_format("return_id"),
        _not_blank("label_code"),
        CheckConstraint(
            "(status = 'label_created') = (label_code IS NOT NULL)",
            name="label_status_matches_label_code",
        ),
        CheckConstraint(
            "(label_code IS NULL) = (label_created_at IS NULL)",
            name="label_code_matches_label_created_at",
        ),
    )

    return_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id(), ForeignKey("orders.order_id"), index=True)
    case_id: Mapped[str | None] = mapped_column(_id(), ForeignKey("cases.case_id"), index=True)
    action_id: Mapped[str | None] = mapped_column(_id(), unique=True)
    status: Mapped[ReturnStatus] = mapped_column(_enum(ReturnStatus, "return_status"))
    reason: Mapped[RequestReason] = mapped_column(_enum(RequestReason, "request_reason"))
    label_code: Mapped[str | None] = mapped_column(String(64), unique=True)
    label_created_at: Mapped[datetime | None] = mapped_column(_tstz())

    items: Mapped[list["ReturnItem"]] = relationship()


class ReturnItem(Base):
    __tablename__ = "return_items"
    __table_args__ = (
        ForeignKeyConstraint(["return_id", "order_id"], ["returns.return_id", "returns.order_id"]),
        ForeignKeyConstraint(
            ["order_id", "order_item_id"], ["order_items.order_id", "order_items.order_item_id"]
        ),
        _positive("quantity"),
    )

    return_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id())
    quantity: Mapped[int] = mapped_column(Integer)


class Replacement(_Timestamps, Base):
    __tablename__ = "replacements"
    __table_args__ = (
        UniqueConstraint("replacement_id", "order_id"),
        ForeignKeyConstraint(["action_id", "order_id"], ["actions.action_id", "actions.order_id"]),
        ForeignKeyConstraint(["return_id", "order_id"], ["returns.return_id", "returns.order_id"]),
        _id_format("replacement_id"),
        CheckConstraint(
            "(status = 'shipped') = (shipped_at IS NOT NULL)",
            name="shipped_status_matches_shipped_at",
        ),
    )

    replacement_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id(), ForeignKey("orders.order_id"), index=True)
    action_id: Mapped[str | None] = mapped_column(_id(), unique=True)
    return_id: Mapped[str | None] = mapped_column(_id(), index=True)
    status: Mapped[ReplacementStatus] = mapped_column(
        _enum(ReplacementStatus, "replacement_status")
    )
    shipped_at: Mapped[datetime | None] = mapped_column(_tstz())

    items: Mapped[list["ReplacementItem"]] = relationship()


class ReplacementItem(Base):
    __tablename__ = "replacement_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["replacement_id", "order_id"],
            ["replacements.replacement_id", "replacements.order_id"],
        ),
        ForeignKeyConstraint(
            ["order_id", "order_item_id"], ["order_items.order_id", "order_items.order_item_id"]
        ),
        _positive("quantity"),
    )

    replacement_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    order_id: Mapped[str] = mapped_column(_id())
    quantity: Mapped[int] = mapped_column(Integer)


# --- Audit and outbox ------------------------------------------------------------------


class AuditLogEntry(Base):
    """Append-only audit trail (by convention; Phase 3 writes it)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),
        _not_blank("event_type"),
        _not_blank("entity_type"),
        _not_blank("entity_id"),
        _not_blank("actor_id"),
    )

    audit_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now(), index=True)
    actor_type: Mapped[AuditActorType] = mapped_column(_enum(AuditActorType, "actor_type"))
    actor_id: Mapped[str | None] = mapped_column(_id())
    event_type: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(_id())
    case_id: Mapped[str | None] = mapped_column(_id(), ForeignKey("cases.case_id"), index=True)
    action_id: Mapped[str | None] = mapped_column(
        _id(), ForeignKey("actions.action_id"), index=True
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_unpublished", "created_at", postgresql_where=text("published_at IS NULL")),
        _not_blank("event_type"),
        _not_blank("aggregate_type"),
        _not_blank("aggregate_id"),
        CheckConstraint(
            "published_at IS NULL OR published_at >= created_at", name="published_after_created"
        ),
    )

    outbox_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(_id())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(_tstz())


# --- Policy corpus (Phase 4 adds embeddings, tsvector, and ANN indexes) -----------------


class PolicyDoc(_CreatedAt, Base):
    __tablename__ = "policy_docs"
    __table_args__ = (
        UniqueConstraint("policy_key", "version"),
        _id_format("policy_doc_id"),
        CheckConstraint("policy_key ~ '^[a-z0-9]+(_[a-z0-9]+)*$'", name="policy_key_format"),
        _positive("version"),
        _not_blank("title"),
        _not_blank("source_path"),
        CheckConstraint(f"content_sha256 ~ '{_SHA256_REGEX}'", name="content_sha256_format"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="effective_window_valid",
        ),
    )

    policy_doc_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    policy_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    source_path: Mapped[str] = mapped_column(String(255))
    content_sha256: Mapped[str] = mapped_column(String(64))
    effective_from: Mapped[datetime] = mapped_column(_tstz())
    effective_until: Mapped[datetime | None] = mapped_column(_tstz())

    chunks: Mapped[list["PolicyChunk"]] = relationship(
        back_populates="doc", cascade="all, delete-orphan", passive_deletes=True
    )


class PolicyChunk(_CreatedAt, Base):
    __tablename__ = "policy_chunks"
    __table_args__ = (
        UniqueConstraint("policy_doc_id", "ordinal"),
        _id_format("chunk_id"),
        _not_blank("section_id"),
        _non_negative("ordinal"),
        _not_blank("heading"),
        _not_blank("content"),
        CheckConstraint(f"content_sha256 ~ '{_SHA256_REGEX}'", name="content_sha256_format"),
    )

    chunk_id: Mapped[str] = mapped_column(_id(), primary_key=True)
    policy_doc_id: Mapped[str] = mapped_column(
        _id(), ForeignKey("policy_docs.policy_doc_id", ondelete="CASCADE"), index=True
    )
    section_id: Mapped[str] = mapped_column(_id(), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))

    doc: Mapped[PolicyDoc] = relationship(back_populates="chunks")


DOMAIN_TABLES: Final = tuple(Base.metadata.sorted_tables)
"""All Phase 1 tables in dependency order."""

REFUND_LEDGER_TABLES: Final = ("refunds", "refund_items")
