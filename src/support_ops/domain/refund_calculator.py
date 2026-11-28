"""Deterministic refund calculator.

For each selected line with ``Q`` purchased units, ``p`` units already refunded, and ``q``
selected units (``1 <= q <= Q - p``)::

    gross              = unit_price * q
    allocated_discount = alloc(discount, p + q) - alloc(discount, p)
    refundable_tax     = alloc(tax, p + q) - alloc(tax, p)
    eligible           = gross - allocated_discount + refundable_tax
    refund             = min(eligible, line_paid_total - prior_refunded_amount)

``alloc`` is cumulative half-up proration to the cent (``money.allocate_cumulative_cents``).
Shipping refunds the unrefunded remainder of the order's shipping only when
``include_shipping`` is set from the policy engine's ``shipping_refundable`` outcome.

The calculator calculates; it does not authorize, persist, or execute anything.
"""

from collections.abc import Iterable
from decimal import Decimal, localcontext

from support_ops.domain.errors import DomainInvariantError, RefundCalculationError
from support_ops.domain.models import (
    OrderLineFacts,
    RefundCalculationInput,
    RefundCalculationResult,
    RefundLineBreakdown,
    RefundSelection,
)
from support_ops.domain.money import CENT, MONEY_CONTEXT, ROUNDING_POLICY, ZERO


def calculate_refund(request: RefundCalculationInput) -> RefundCalculationResult:
    """Calculate the refund breakdown, independent of the caller's decimal context."""
    with localcontext(MONEY_CONTEXT):
        return _calculate(request)


def _calculate(request: RefundCalculationInput) -> RefundCalculationResult:
    order = request.order
    breakdowns = tuple(
        _calculate_line(order.order_id, order.line(selection.order_item_id), selection)
        for selection in request.selections
    )

    shipping_refund = (
        order.shipping_amount - order.shipping_refunded_amount if request.include_shipping else ZERO
    )
    items_refund_total = _sum(b.refund_amount for b in breakdowns)
    total = items_refund_total + shipping_refund

    if total > order.remaining_refundable_amount:
        raise DomainInvariantError(
            f"Refund for order {order.order_id} exceeds its remaining refundable amount."
        )

    return RefundCalculationResult(
        order_id=order.order_id,
        currency=order.currency,
        rounding_policy=ROUNDING_POLICY,
        lines=breakdowns,
        gross_amount_total=_sum(b.gross_amount for b in breakdowns),
        allocated_discount_total=_sum(b.allocated_discount for b in breakdowns),
        refundable_tax_total=_sum(b.refundable_tax for b in breakdowns),
        eligible_items_total=_sum(b.eligible_amount for b in breakdowns),
        items_refund_total=items_refund_total,
        shipping_refund_amount=shipping_refund,
        total_refund_amount=total,
        any_capped=any(b.capped_by_prior_amount_refund for b in breakdowns),
    )


def _calculate_line(
    order_id: str, line: OrderLineFacts | None, selection: RefundSelection
) -> RefundLineBreakdown:
    item_id = selection.order_item_id
    if line is None:
        raise RefundCalculationError(f"Item {item_id} is not part of order {order_id}.")
    if line.remaining_quantity == 0:
        raise RefundCalculationError(f"Every unit of item {item_id} has already been refunded.")
    if selection.quantity > line.remaining_quantity:
        raise RefundCalculationError(
            f"Selected quantity {selection.quantity} for item {item_id} exceeds the "
            f"{line.remaining_quantity} unrefunded unit(s)."
        )

    before = line.refunded_quantity
    after = before + selection.quantity
    gross = line.gross_for_units(after) - line.gross_for_units(before)
    discount = line.discount_for_units(after) - line.discount_for_units(before)
    tax = line.tax_for_units(after) - line.tax_for_units(before)
    eligible = gross - discount + tax
    remaining_paid = line.remaining_paid_amount
    refund = min(eligible, remaining_paid)

    if min(gross, discount, tax, eligible, refund) < 0:
        raise DomainInvariantError(f"Negative refund component for item {item_id}.")
    if refund > eligible or refund > remaining_paid:
        raise DomainInvariantError(f"Refund for item {item_id} exceeds its eligible amount.")
    for amount in (gross, discount, tax, eligible, refund):
        if amount != amount.quantize(CENT):
            raise DomainInvariantError(f"Refund component for item {item_id} is not cent-exact.")

    return RefundLineBreakdown(
        order_item_id=item_id,
        quantity_purchased=line.quantity,
        quantity_previously_refunded=before,
        quantity_remaining=line.remaining_quantity,
        quantity_selected=selection.quantity,
        unit_price=line.unit_price,
        gross_amount=gross,
        allocated_discount=discount,
        refundable_tax=tax,
        eligible_amount=eligible,
        line_paid_total=line.paid_total,
        prior_refunded_amount=line.refunded_amount,
        remaining_paid_amount=remaining_paid,
        refund_amount=refund,
        capped_by_prior_amount_refund=refund < eligible,
        cap_reduction=eligible - refund,
    )


def _sum(amounts: Iterable[Decimal]) -> Decimal:
    return sum(amounts, start=ZERO)
