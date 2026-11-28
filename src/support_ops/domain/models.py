"""Validated, immutable domain contracts.

These are application data contracts, not ORM rows. Pydantic validates structure; it does
not prove database truth or authorize anything. Contracts that describe *trusted facts*
(``OrderFacts``) raise ``DomainInvariantError`` when their values contradict each other,
because that signals corrupt trusted data rather than bad user or LLM input.

All money is ``Decimal`` exact to the cent; all timestamps are timezone-aware and
normalized to UTC; all collections are tuples or frozensets so frozen models stay immutable.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Final, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic.types import StringConstraints

from support_ops.domain.enums import (
    ActionType,
    Currency,
    OrderStatus,
    PolicyDecision,
    PolicyReasonCode,
    RequestReason,
    Role,
)
from support_ops.domain.errors import DomainInvariantError
from support_ops.domain.money import Money, allocate_cumulative_cents, from_cents, to_cents

ID_PATTERN: Final = r"^[A-Z0-9]+(?:[._-][A-Z0-9]+)*$"
POLICY_RULE_ID_PATTERN: Final = r"^[A-Z]{2,5}-[0-9]+(?:\.[0-9]+)*$"
MAX_ID_LENGTH: Final = 64

_SUFFIX = r"[A-Z0-9]+(?:[._-][A-Z0-9]+)*$"

EntityId = Annotated[str, StringConstraints(max_length=MAX_ID_LENGTH, pattern=ID_PATTERN)]
"""Uppercase business identifier, e.g. ``HEADPHONES-1``; never empty."""
CustomerId = Annotated[
    str, StringConstraints(max_length=MAX_ID_LENGTH, pattern=r"^CUST-" + _SUFFIX)
]
OrderId = Annotated[str, StringConstraints(max_length=MAX_ID_LENGTH, pattern=r"^ORD-" + _SUFFIX)]
CaseId = Annotated[str, StringConstraints(max_length=MAX_ID_LENGTH, pattern=r"^CASE-" + _SUFFIX)]
ActionId = Annotated[str, StringConstraints(max_length=MAX_ID_LENGTH, pattern=r"^ACT-" + _SUFFIX)]
OrderItemId = EntityId
ProductId = EntityId
PolicyRuleId = Annotated[
    str, StringConstraints(max_length=MAX_ID_LENGTH, pattern=POLICY_RULE_ID_PATTERN)
]
"""Stable executable-rule / policy-section ID, e.g. ``DMG-4.2``."""


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""Timezone-aware datetime normalized to UTC; naive datetimes are rejected."""


def _require_unique(values: Iterable[object], what: str) -> None:
    seen: set[object] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"Duplicate {what}: {value}.")
        seen.add(value)


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


# --- Identity ------------------------------------------------------------------------


class AuthContext(DomainModel):
    """Trusted identity of the caller, built by the API layer from the authenticated
    session. Never populated from a request body or an LLM. ``permissions`` (architecture
    section 6.1) is deferred until Phase 3 defines its vocabulary."""

    role: Role
    customer_id: CustomerId | None = None

    @model_validator(mode="after")
    def _role_matches_identity(self) -> Self:
        if self.role is Role.CUSTOMER and self.customer_id is None:
            raise ValueError("A customer AuthContext requires customer_id.")
        if self.role is Role.SUPERVISOR and self.customer_id is not None:
            raise ValueError("A supervisor AuthContext must not carry a customer_id.")
        return self


# --- Trusted order facts --------------------------------------------------------------


class OrderLineFacts(DomainModel):
    """Trusted facts about one order line, including its maintained refund balances.

    ``discount_amount`` and ``tax_amount`` are the line totals fixed at purchase time.
    ``refunded_quantity`` / ``refunded_amount`` mirror ``order_items`` balances; an
    amount-only refund (``quantity == 0`` in the ledger) raises ``refunded_amount``
    without consuming quantity.
    """

    order_item_id: OrderItemId
    product_id: ProductId
    quantity: Annotated[int, Field(ge=1)]
    unit_price: Money
    discount_amount: Money
    tax_amount: Money
    refunded_quantity: Annotated[int, Field(ge=0)]
    refunded_amount: Money
    returnable: bool

    @property
    def gross_amount(self) -> Decimal:
        return self.unit_price * self.quantity

    @property
    def paid_total(self) -> Decimal:
        """Amount the customer paid for the whole line: gross - discount + tax."""
        return self.gross_amount - self.discount_amount + self.tax_amount

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.refunded_quantity

    @property
    def remaining_paid_amount(self) -> Decimal:
        return self.paid_total - self.refunded_amount

    def gross_for_units(self, units: int) -> Decimal:
        return self.unit_price * units

    def discount_for_units(self, units: int) -> Decimal:
        """Discount allocated (cumulatively, half up) to the first ``units`` units."""
        return from_cents(
            allocate_cumulative_cents(to_cents(self.discount_amount), units, self.quantity)
        )

    def tax_for_units(self, units: int) -> Decimal:
        """Tax allocated (cumulatively, half up) to the first ``units`` units."""
        return from_cents(
            allocate_cumulative_cents(to_cents(self.tax_amount), units, self.quantity)
        )

    def paid_for_units(self, units: int) -> Decimal:
        return (
            self.gross_for_units(units) - self.discount_for_units(units) + self.tax_for_units(units)
        )

    @model_validator(mode="after")
    def _facts_are_consistent(self) -> Self:
        where = f"order item {self.order_item_id}"
        if self.discount_amount > self.gross_amount:
            raise DomainInvariantError(f"Discount exceeds the gross amount of {where}.")
        if self.refunded_quantity > self.quantity:
            raise DomainInvariantError(f"Refunded quantity exceeds quantity of {where}.")
        if self.refunded_amount > self.paid_total:
            raise DomainInvariantError(f"Refunded amount exceeds the paid total of {where}.")
        # Every refunded unit was refunded at least its allocated paid share, so the
        # refunded balance can never be lower than that share.
        if self.refunded_amount < self.paid_for_units(self.refunded_quantity):
            raise DomainInvariantError(
                f"Refunded amount is lower than the paid share of refunded units of {where}."
            )
        return self


class OrderFacts(DomainModel):
    """Trusted snapshot of one order, loaded from the database by deterministic code.

    ``customer_id`` is the database owner of the order, not a caller-supplied value.
    ``shipped_at`` / ``delivered_at`` come from the order's single shipment.
    """

    order_id: OrderId
    customer_id: CustomerId
    currency: Currency
    status: OrderStatus
    placed_at: UtcDatetime
    shipped_at: UtcDatetime | None
    delivered_at: UtcDatetime | None
    shipping_amount: Money
    shipping_refunded_amount: Money
    lines: Annotated[tuple[OrderLineFacts, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _facts_are_consistent(self) -> Self:
        _require_unique((line.order_item_id for line in self.lines), "order item ID")

        shipped = self.shipped_at is not None
        delivered = self.delivered_at is not None
        expected: dict[OrderStatus, tuple[bool, bool]] = {
            OrderStatus.PLACED: (False, False),
            OrderStatus.CANCELLED: (False, False),
            OrderStatus.SHIPPED: (True, False),
            OrderStatus.DELIVERED: (True, True),
        }
        if expected[self.status] != (shipped, delivered):
            raise DomainInvariantError(
                f"Order {self.order_id} status {self.status.value} contradicts its "
                "shipment timestamps."
            )
        if self.shipped_at is not None and self.shipped_at < self.placed_at:
            raise DomainInvariantError(f"Order {self.order_id} shipped before it was placed.")
        if (
            self.delivered_at is not None
            and self.shipped_at is not None
            and self.delivered_at < self.shipped_at
        ):
            raise DomainInvariantError(f"Order {self.order_id} delivered before it shipped.")
        if self.shipping_refunded_amount > self.shipping_amount:
            raise DomainInvariantError(
                f"Order {self.order_id} refunded more shipping than was charged."
            )
        return self

    def line(self, order_item_id: str) -> OrderLineFacts | None:
        return next((line for line in self.lines if line.order_item_id == order_item_id), None)

    @property
    def remaining_refundable_amount(self) -> Decimal:
        """Everything still refundable on the order: line balances plus unrefunded shipping."""
        lines = sum((line.remaining_paid_amount for line in self.lines), start=Decimal("0.00"))
        return lines + self.shipping_amount - self.shipping_refunded_amount


# --- Refund calculation ---------------------------------------------------------------


class RefundSelection(DomainModel):
    order_item_id: OrderItemId
    quantity: Annotated[int, Field(ge=1)]


class RefundCalculationInput(DomainModel):
    """What to refund. ``include_shipping`` must come from the policy engine's
    ``shipping_refundable`` outcome, never from a customer or LLM."""

    order: OrderFacts
    selections: Annotated[tuple[RefundSelection, ...], Field(min_length=1)]
    include_shipping: bool

    @model_validator(mode="after")
    def _unique_selections(self) -> Self:
        _require_unique((s.order_item_id for s in self.selections), "selected order item ID")
        return self


class RefundLineBreakdown(DomainModel):
    """Per-line refund breakdown.

    Quantities and money are reported separately: ``quantity_remaining`` is unrefunded
    units, ``remaining_paid_amount`` is unrefunded money. ``refund_amount`` is
    ``eligible_amount`` capped at ``remaining_paid_amount``; a cap happens only when earlier
    amount-only refunds already returned part of the line and is reported via
    ``capped_by_prior_amount_refund`` / ``cap_reduction``.
    """

    order_item_id: OrderItemId
    quantity_purchased: Annotated[int, Field(ge=1)]
    quantity_previously_refunded: Annotated[int, Field(ge=0)]
    quantity_remaining: Annotated[int, Field(ge=1)]
    quantity_selected: Annotated[int, Field(ge=1)]
    unit_price: Money
    gross_amount: Money
    allocated_discount: Money
    refundable_tax: Money
    eligible_amount: Money
    line_paid_total: Money
    prior_refunded_amount: Money
    remaining_paid_amount: Money
    refund_amount: Money
    capped_by_prior_amount_refund: bool
    cap_reduction: Money


class RefundCalculationResult(DomainModel):
    order_id: OrderId
    currency: Currency
    rounding_policy: str
    lines: tuple[RefundLineBreakdown, ...]
    gross_amount_total: Money
    allocated_discount_total: Money
    refundable_tax_total: Money
    eligible_items_total: Money
    items_refund_total: Money
    shipping_refund_amount: Money
    total_refund_amount: Money
    any_capped: bool


# --- Executable policy ----------------------------------------------------------------


class PolicyRule(DomainModel):
    """One executable rule in the policy-engine catalog."""

    rule_id: PolicyRuleId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    applies_to: Annotated[frozenset[ActionType], Field(min_length=1)]


class PolicyRequest(DomainModel):
    """Trusted facts plus the requested action, evaluated at an injected instant.

    Item actions (refund, return, replacement) name at least one order item; cancellation is
    order-level and names none. ``in_stock_item_ids`` is the trusted inventory fact required
    for replacements only.
    """

    action_type: ActionType
    reason: RequestReason
    item_ids: tuple[OrderItemId, ...]
    evaluated_at: UtcDatetime
    order: OrderFacts
    in_stock_item_ids: frozenset[OrderItemId] | None = None

    @model_validator(mode="after")
    def _shape_matches_action(self) -> Self:
        _require_unique(self.item_ids, "item ID")
        if self.action_type is ActionType.CANCELLATION:
            if self.item_ids:
                raise ValueError("Cancellation is order-level and must not name item IDs.")
        elif not self.item_ids:
            raise ValueError(f"A {self.action_type.value} request must name at least one item.")

        if self.action_type is ActionType.REPLACEMENT:
            if self.in_stock_item_ids is None:
                raise ValueError("A replacement request requires in_stock_item_ids.")
            if not self.in_stock_item_ids <= set(self.item_ids):
                raise ValueError("in_stock_item_ids must be a subset of item_ids.")
        elif self.in_stock_item_ids is not None:
            raise ValueError("in_stock_item_ids is only valid for replacement requests.")
        return self


class ItemPolicyResult(DomainModel):
    order_item_id: OrderItemId
    decision: PolicyDecision
    triggered_rule_ids: tuple[PolicyRuleId, ...]
    reason_codes: tuple[PolicyReasonCode, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        _require_unique(self.triggered_rule_ids, "rule ID")
        _require_unique(self.reason_codes, "reason code")
        return self


class PolicyEvaluation(DomainModel):
    """Structured, deterministic policy outcome. It is not an authorization decision:
    ownership, amount, duplicate, and approval-threshold checks belong to Phase 3."""

    order_id: OrderId
    action_type: ActionType
    reason: RequestReason
    decision: PolicyDecision
    item_results: tuple[ItemPolicyResult, ...]
    triggered_rule_ids: tuple[PolicyRuleId, ...]
    reason_codes: tuple[PolicyReasonCode, ...]
    shipping_refundable: bool
    delivered_on: date | None
    days_since_delivery: Annotated[int, Field(ge=0)] | None
    window_days: Annotated[int, Field(ge=1)] | None
    window_ends_on: date | None
    ruleset_version: Annotated[str, StringConstraints(min_length=1)]
    evaluated_at: UtcDatetime

    @model_validator(mode="after")
    def _unique(self) -> Self:
        _require_unique(self.triggered_rule_ids, "rule ID")
        _require_unique(self.reason_codes, "reason code")
        _require_unique((r.order_item_id for r in self.item_results), "item result")
        return self
