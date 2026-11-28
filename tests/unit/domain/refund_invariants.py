"""Refund-calculator invariants, shared by the Hypothesis property tests.

Each ``check_*`` function asserts one invariant for concrete inputs; the property-test
module supplies those inputs from Hypothesis strategies.
"""

from decimal import Decimal

from domain_factories import make_line, make_order

from support_ops.domain.models import (
    OrderFacts,
    OrderLineFacts,
    RefundCalculationInput,
    RefundCalculationResult,
    RefundSelection,
)
from support_ops.domain.money import from_cents, to_cents
from support_ops.domain.refund_calculator import calculate_refund


def build_line(
    *,
    quantity: int,
    unit_cents: int,
    discount_cents: int,
    tax_cents: int,
    refunded_quantity: int = 0,
    refunded_cents: int = 0,
) -> OrderLineFacts:
    return make_line(
        quantity=quantity,
        unit_price=from_cents(unit_cents),
        discount_amount=from_cents(discount_cents),
        tax_amount=from_cents(tax_cents),
        refunded_quantity=refunded_quantity,
        refunded_amount=from_cents(refunded_cents),
    )


def with_refunds(
    line: OrderLineFacts, refunded_quantity: int, refunded_cents: int
) -> OrderLineFacts:
    return build_line(
        quantity=line.quantity,
        unit_cents=to_cents(line.unit_price),
        discount_cents=to_cents(line.discount_amount),
        tax_cents=to_cents(line.tax_amount),
        refunded_quantity=refunded_quantity,
        refunded_cents=refunded_cents,
    )


def min_refunded_cents(line: OrderLineFacts, refunded_quantity: int) -> int:
    """Lowest refunded balance consistent with ``refunded_quantity`` refunded units."""
    return to_cents(line.paid_for_units(refunded_quantity))


def run(order: OrderFacts, quantity: int, *, include_shipping: bool) -> RefundCalculationResult:
    return calculate_refund(
        RefundCalculationInput(
            order=order,
            selections=(RefundSelection(order_item_id="HEADPHONES-1", quantity=quantity),),
            include_shipping=include_shipping,
        )
    )


def check_refund_bounds(
    line: OrderLineFacts,
    quantity: int,
    shipping_cents: int,
    shipping_refunded_cents: int,
    include_shipping: bool,
) -> None:
    order = make_order(
        line,
        shipping_amount=from_cents(shipping_cents),
        shipping_refunded_amount=from_cents(shipping_refunded_cents),
    )
    result = run(order, quantity, include_shipping=include_shipping)
    breakdown = result.lines[0]

    selected_paid = line.paid_for_units(line.refunded_quantity + quantity) - line.paid_for_units(
        line.refunded_quantity
    )
    assert breakdown.eligible_amount == selected_paid
    assert Decimal("0.00") <= breakdown.refund_amount <= breakdown.eligible_amount
    assert breakdown.refund_amount <= line.paid_total - line.refunded_amount
    assert breakdown.eligible_amount <= breakdown.gross_amount + breakdown.refundable_tax
    assert breakdown.cap_reduction == breakdown.eligible_amount - breakdown.refund_amount
    assert breakdown.capped_by_prior_amount_refund is (breakdown.cap_reduction > 0)
    assert result.total_refund_amount <= order.remaining_refundable_amount
    expected_shipping = (
        from_cents(shipping_cents - shipping_refunded_cents) if include_shipping else Decimal("0")
    )
    assert result.shipping_refund_amount == expected_shipping
    assert result.total_refund_amount == breakdown.refund_amount + result.shipping_refund_amount
    for amount in (
        result.total_refund_amount,
        breakdown.refund_amount,
        breakdown.allocated_discount,
        breakdown.refundable_tax,
    ):
        assert amount.as_tuple().exponent == -2


def check_prior_amount_refund_never_increases_refund(
    line: OrderLineFacts, quantity: int, extra_cents: int
) -> None:
    ceiling = to_cents(line.paid_total)
    more_refunded = with_refunds(
        line,
        line.refunded_quantity,
        min(ceiling, to_cents(line.refunded_amount) + extra_cents),
    )

    before = run(make_order(line), quantity, include_shipping=False).total_refund_amount
    after = run(make_order(more_refunded), quantity, include_shipping=False).total_refund_amount

    assert after <= before


def check_unit_by_unit_refunds_settle_exactly(line: OrderLineFacts) -> None:
    expected_total = line.paid_total - line.refunded_amount
    total = Decimal("0.00")
    current = line
    while current.remaining_quantity:
        amount = run(make_order(current), 1, include_shipping=False).total_refund_amount
        total += amount
        # Re-validating the updated facts proves each step preserves the line invariants.
        current = with_refunds(
            current, current.refunded_quantity + 1, to_cents(current.refunded_amount + amount)
        )

    assert total == expected_total
    assert current.remaining_paid_amount == Decimal("0.00")


def check_single_refund_equals_unit_by_unit(line: OrderLineFacts) -> None:
    single = run(make_order(line), line.remaining_quantity, include_shipping=False)

    assert single.total_refund_amount == line.paid_total - line.refunded_amount
