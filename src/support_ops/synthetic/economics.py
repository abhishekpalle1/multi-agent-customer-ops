"""Centralized, deterministic order economics for synthetic data.

All arithmetic is on integer cents; amounts leave this module as ``Decimal`` exact to the
cent. This module is the single place synthetic order totals and refund-ledger rows are
produced, and it never re-implements the Phase 1 refund algorithm:

- ``apply_calculated_refund`` records the result of a real Phase 1 ``calculate_refund``
  call. Callers pass ``include_shipping`` from a Phase 1 policy evaluation (golden history)
  or ``False`` (background history, which never refunds shipping).
- ``apply_amount_adjustment`` records an amount-only goodwill adjustment (``quantity == 0``),
  the one ledger shape the calculator does not model; Phase 1 ``OrderLineFacts`` invariants
  bound it.
- ``apply_cancellation_reversal`` records an **order-cancellation payment reversal**: a
  cancelled (never shipped) order's whole captured payment, shipping included, is returned.
  It is part of cancellation, not a refund request, and is not a ``RFD-2.2`` shipping-refund
  decision (that rule governs refund requests for damaged items).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from support_ops.domain.enums import OrderStatus, PaymentStatus
from support_ops.domain.models import RefundCalculationInput, RefundSelection
from support_ops.domain.money import ZERO, from_cents, to_cents
from support_ops.domain.refund_calculator import calculate_refund
from support_ops.synthetic.facts import order_facts
from support_ops.synthetic.records import (
    OrderItemRecord,
    OrderRecord,
    PaymentRecord,
    RefundItemRecord,
    RefundRecord,
    ShipmentRecord,
    SyntheticDatasetError,
)

BASIS_POINTS = 10_000


def percent_of_cents(cents: int, basis_points: int) -> int:
    """``cents * basis_points / 10000`` rounded half up, in exact integer arithmetic."""
    if cents < 0 or basis_points < 0:
        raise SyntheticDatasetError("Cents and basis points must be non-negative.")
    quotient, remainder = divmod(cents * basis_points, BASIS_POINTS)
    return quotient + (1 if 2 * remainder >= BASIS_POINTS else 0)


@dataclass(frozen=True, slots=True)
class LinePrice:
    unit_price: Decimal
    quantity: int
    gross: Decimal
    discount: Decimal
    tax: Decimal

    @property
    def paid(self) -> Decimal:
        return self.gross - self.discount + self.tax


def price_line(
    unit_price_cents: int, quantity: int, *, discount_bps: int, tax_bps: int
) -> LinePrice:
    """Price a background line: percentage discount on gross, tax on (gross - discount)."""
    if unit_price_cents <= 0 or quantity < 1:
        raise SyntheticDatasetError("A line needs a positive unit price and quantity.")
    gross = unit_price_cents * quantity
    discount = percent_of_cents(gross, discount_bps)
    tax = percent_of_cents(gross - discount, tax_bps)
    return LinePrice(
        unit_price=from_cents(unit_price_cents),
        quantity=quantity,
        gross=from_cents(gross),
        discount=from_cents(discount),
        tax=from_cents(tax),
    )


def explicit_line(unit_price: str, quantity: int, *, discount: str, tax: str) -> LinePrice:
    """Price a golden line from explicitly stated cent-exact amounts."""
    unit_cents, discount_cents, tax_cents = (
        to_cents(Decimal(unit_price)),
        to_cents(Decimal(discount)),
        to_cents(Decimal(tax)),
    )
    gross = unit_cents * quantity
    if quantity < 1 or unit_cents <= 0 or discount_cents > gross or tax_cents < 0:
        raise SyntheticDatasetError("Golden line amounts are inconsistent.")
    return LinePrice(
        unit_price=from_cents(unit_cents),
        quantity=quantity,
        gross=from_cents(gross),
        discount=from_cents(discount_cents),
        tax=from_cents(tax_cents),
    )


def line_paid_total(item: OrderItemRecord) -> Decimal:
    return item.unit_price * item.quantity - item.discount_amount + item.tax_amount


def order_total(items: Iterable[OrderItemRecord], shipping_amount: Decimal) -> Decimal:
    """The captured payment amount: every line's paid total plus shipping."""
    return sum((line_paid_total(item) for item in items), start=ZERO) + shipping_amount


def payment_status(amount: Decimal, amount_refunded: Decimal) -> PaymentStatus:
    if amount_refunded == 0:
        return PaymentStatus.CAPTURED
    if amount_refunded < amount:
        return PaymentStatus.PARTIALLY_REFUNDED
    return PaymentStatus.REFUNDED


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderState:
    """One order and everything hanging off it while history is being applied."""

    order: OrderRecord
    items: tuple[OrderItemRecord, ...]
    shipment: ShipmentRecord | None
    payment: PaymentRecord
    refunds: tuple[RefundRecord, ...] = ()
    refund_items: tuple[RefundItemRecord, ...] = ()


def apply_calculated_refund(
    state: OrderState,
    *,
    returnable_by_product: Mapping[str, bool],
    selections: Sequence[tuple[str, int]],
    include_shipping: bool,
    refund_id: str,
    refunded_at: datetime,
) -> OrderState:
    """Record a refund whose amounts come from a real Phase 1 ``calculate_refund`` call."""
    facts = order_facts(state.order, state.items, state.shipment, returnable_by_product)
    result = calculate_refund(
        RefundCalculationInput(
            order=facts,
            selections=tuple(
                RefundSelection(order_item_id=item_id, quantity=quantity)
                for item_id, quantity in selections
            ),
            include_shipping=include_shipping,
        )
    )
    items = tuple(
        RefundItemRecord(
            refund_id=refund_id,
            order_item_id=line.order_item_id,
            order_id=state.order.order_id,
            quantity=line.quantity_selected,
            amount=line.refund_amount,
            created_at=refunded_at,
        )
        for line in result.lines
    )
    return _record_refund(
        state,
        returnable_by_product,
        items=items,
        shipping_amount=result.shipping_refund_amount,
        refund_id=refund_id,
        refunded_at=refunded_at,
    )


def apply_amount_adjustment(
    state: OrderState,
    *,
    returnable_by_product: Mapping[str, bool],
    order_item_id: str,
    amount: Decimal,
    refund_id: str,
    refunded_at: datetime,
) -> OrderState:
    """Record an amount-only goodwill adjustment (no units returned)."""
    item = next(i for i in state.items if i.order_item_id == order_item_id)
    if amount <= 0 or amount > line_paid_total(item) - item.refunded_amount:
        raise SyntheticDatasetError(f"Invalid adjustment amount for {order_item_id}.")
    ledger_item = RefundItemRecord(
        refund_id=refund_id,
        order_item_id=order_item_id,
        order_id=state.order.order_id,
        quantity=0,
        amount=amount,
        created_at=refunded_at,
    )
    return _record_refund(
        state,
        returnable_by_product,
        items=(ledger_item,),
        shipping_amount=ZERO,
        refund_id=refund_id,
        refunded_at=refunded_at,
    )


def apply_cancellation_reversal(
    state: OrderState,
    *,
    returnable_by_product: Mapping[str, bool],
    refund_id: str,
    refunded_at: datetime,
) -> OrderState:
    """Record the order-cancellation payment reversal: return the entire captured payment,
    shipping included. Not a refund-policy decision (see the module docstring)."""
    if state.order.status is not OrderStatus.CANCELLED or state.shipment is not None:
        raise SyntheticDatasetError("Only a cancelled, never-shipped order can be reversed.")
    if state.refunds:
        raise SyntheticDatasetError("A cancellation reversal must be the order's only refund.")
    items = tuple(
        RefundItemRecord(
            refund_id=refund_id,
            order_item_id=item.order_item_id,
            order_id=state.order.order_id,
            quantity=item.quantity,
            amount=line_paid_total(item),
            created_at=refunded_at,
        )
        for item in state.items
    )
    reversed_state = _record_refund(
        state,
        returnable_by_product,
        items=items,
        shipping_amount=state.order.shipping_amount,
        refund_id=refund_id,
        refunded_at=refunded_at,
    )
    if reversed_state.payment.amount_refunded != reversed_state.payment.amount:
        raise SyntheticDatasetError("A cancellation reversal must return the whole payment.")
    return reversed_state


def _record_refund(
    state: OrderState,
    returnable_by_product: Mapping[str, bool],
    *,
    items: tuple[RefundItemRecord, ...],
    shipping_amount: Decimal,
    refund_id: str,
    refunded_at: datetime,
) -> OrderState:
    amount = sum((i.amount for i in items), start=ZERO) + shipping_amount
    if amount <= 0:
        raise SyntheticDatasetError(f"Refund {refund_id} must be positive.")
    if refunded_at < state.order.placed_at:
        raise SyntheticDatasetError(f"Refund {refund_id} precedes its order.")

    by_item = {i.order_item_id: i for i in items}
    updated_items = tuple(
        replace(
            item,
            refunded_quantity=item.refunded_quantity + by_item[item.order_item_id].quantity,
            refunded_amount=item.refunded_amount + by_item[item.order_item_id].amount,
            updated_at=refunded_at,
        )
        if item.order_item_id in by_item
        else item
        for item in state.items
    )
    order = replace(
        state.order,
        shipping_refunded_amount=state.order.shipping_refunded_amount + shipping_amount,
        updated_at=max(state.order.updated_at, refunded_at),
    )
    refunded_total = state.payment.amount_refunded + amount
    payment = replace(
        state.payment,
        amount_refunded=refunded_total,
        status=payment_status(state.payment.amount, refunded_total),
        updated_at=refunded_at,
    )
    refund = RefundRecord(
        refund_id=refund_id,
        order_id=state.order.order_id,
        payment_id=state.payment.payment_id,
        action_id=None,
        amount=amount,
        shipping_amount=shipping_amount,
        refunded_at=refunded_at,
        created_at=refunded_at,
    )
    new_state = replace(
        state,
        order=order,
        items=updated_items,
        payment=payment,
        refunds=(*state.refunds, refund),
        refund_items=(*state.refund_items, *items),
    )
    # Re-validate against the Phase 1 trusted-fact invariants after every ledger write.
    order_facts(new_state.order, new_state.items, new_state.shipment, returnable_by_product)
    if payment.amount_refunded > payment.amount:
        raise SyntheticDatasetError(f"Refund {refund_id} over-refunds its payment.")
    return new_state
