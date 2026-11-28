from decimal import Decimal, localcontext
from typing import Any

import pytest
from domain_factories import d, make_line, make_order

from support_ops.domain.errors import RefundCalculationError
from support_ops.domain.models import (
    OrderFacts,
    OrderLineFacts,
    RefundCalculationInput,
    RefundCalculationResult,
    RefundSelection,
)
from support_ops.domain.refund_calculator import calculate_refund


def refund(
    order: OrderFacts, *selections: tuple[str, int], include_shipping: bool = False
) -> RefundCalculationResult:
    return calculate_refund(
        RefundCalculationInput(
            order=order,
            selections=tuple(
                RefundSelection(order_item_id=item_id, quantity=qty) for item_id, qty in selections
            ),
            include_shipping=include_shipping,
        )
    )


def single_line_refund(quantity: int = 1, **line: Any) -> RefundCalculationResult:
    """Refund ``quantity`` units of a single line that has ``quantity`` units by default."""
    line.setdefault("quantity", quantity)
    return refund(make_order(make_line(**line)), ("HEADPHONES-1", quantity))


def test_no_discount_no_tax_refunds_the_unit_price() -> None:
    result = single_line_refund()

    assert result.total_refund_amount == Decimal("129.99")
    assert result.gross_amount_total == Decimal("129.99")
    assert result.allocated_discount_total == Decimal("0.00")
    assert result.refundable_tax_total == Decimal("0.00")
    assert result.any_capped is False
    assert result.currency.value == "USD"
    assert result.rounding_policy == "half_up_to_cent"


def test_tax_is_refunded_with_the_item() -> None:
    result = single_line_refund(unit_price=d("100.00"), tax_amount=d("8.25"))

    line = result.lines[0]
    assert (line.gross_amount, line.refundable_tax, line.eligible_amount) == (
        Decimal("100.00"),
        Decimal("8.25"),
        Decimal("108.25"),
    )
    assert result.total_refund_amount == Decimal("108.25")


def test_discount_and_tax_are_allocated_without_drift_across_partial_refunds() -> None:
    line = make_line(
        quantity=3, unit_price=d("10.00"), discount_amount=d("1.00"), tax_amount=d("2.00")
    )
    expected = [
        (Decimal("0.33"), Decimal("0.67"), Decimal("10.34")),
        (Decimal("0.34"), Decimal("0.66"), Decimal("10.32")),
        (Decimal("0.33"), Decimal("0.67"), Decimal("10.34")),
    ]
    refunds: list[Decimal] = []

    for discount, tax, amount in expected:
        result = refund(make_order(line), ("HEADPHONES-1", 1))
        breakdown = result.lines[0]
        assert (breakdown.allocated_discount, breakdown.refundable_tax) == (discount, tax)
        assert breakdown.refund_amount == amount
        refunds.append(amount)
        line = make_line(
            quantity=3,
            unit_price=d("10.00"),
            discount_amount=d("1.00"),
            tax_amount=d("2.00"),
            refunded_quantity=line.refunded_quantity + 1,
            refunded_amount=line.refunded_amount + amount,
        )

    assert sum(refunds) == Decimal("31.00") == line.paid_total
    assert line.remaining_paid_amount == Decimal("0.00")


def test_full_quantity_in_one_refund_equals_sum_of_unit_refunds() -> None:
    result = single_line_refund(
        quantity=3, unit_price=d("10.00"), discount_amount=d("1.00"), tax_amount=d("2.00")
    )

    assert result.total_refund_amount == Decimal("31.00")


def test_multiple_lines_and_eligible_shipping() -> None:
    order = make_order(
        make_line(order_item_id="MUG-1", unit_price=d("12.50"), tax_amount=d("1.03")),
        make_line(order_item_id="KETTLE-1", unit_price=d("40.00"), discount_amount=d("4.00")),
        shipping_amount=d("7.99"),
    )

    result = refund(order, ("MUG-1", 1), ("KETTLE-1", 1), include_shipping=True)

    assert [b.refund_amount for b in result.lines] == [Decimal("13.53"), Decimal("36.00")]
    assert result.items_refund_total == Decimal("49.53")
    assert result.shipping_refund_amount == Decimal("7.99")
    assert result.total_refund_amount == Decimal("57.52")


def test_shipping_is_excluded_unless_policy_allows_it() -> None:
    order = make_order(shipping_amount=d("7.99"))

    result = refund(order, ("HEADPHONES-1", 1))

    assert result.shipping_refund_amount == Decimal("0.00")
    assert result.total_refund_amount == Decimal("129.99")


def test_only_unrefunded_shipping_is_refunded() -> None:
    order = make_order(shipping_amount=d("7.99"), shipping_refunded_amount=d("2.00"))

    result = refund(order, ("HEADPHONES-1", 1), include_shipping=True)

    assert result.shipping_refund_amount == Decimal("5.99")


def test_prior_partial_quantity_refund_leaves_remaining_units_refundable() -> None:
    order = make_order(
        make_line(
            quantity=2, unit_price=d("50.00"), refunded_quantity=1, refunded_amount=d("50.00")
        )
    )

    result = refund(order, ("HEADPHONES-1", 1))

    line = result.lines[0]
    assert (line.quantity_previously_refunded, line.quantity_remaining) == (1, 1)
    assert line.prior_refunded_amount == Decimal("50.00")
    assert line.refund_amount == Decimal("50.00")
    assert line.capped_by_prior_amount_refund is False


def test_prior_amount_only_refund_caps_and_reports_the_cap() -> None:
    result = single_line_refund(unit_price=d("100.00"), refunded_amount=d("20.00"))

    line = result.lines[0]
    assert line.quantity_remaining == 1
    assert line.eligible_amount == Decimal("100.00")
    assert line.remaining_paid_amount == Decimal("80.00")
    assert line.refund_amount == Decimal("80.00")
    assert line.capped_by_prior_amount_refund is True
    assert line.cap_reduction == Decimal("20.00")
    assert result.any_capped is True


def test_zero_remaining_paid_amount_yields_zero_refund_not_an_error() -> None:
    result = single_line_refund(unit_price=d("100.00"), refunded_amount=d("100.00"))

    assert result.total_refund_amount == Decimal("0.00")
    assert result.lines[0].cap_reduction == Decimal("100.00")


def test_fully_refunded_item_cannot_be_selected() -> None:
    with pytest.raises(RefundCalculationError, match="already been refunded"):
        single_line_refund(refunded_quantity=1, refunded_amount=d("129.99"))


def test_quantity_above_unrefunded_units_is_rejected() -> None:
    order = make_order(
        make_line(quantity=2, unit_price=d("5.00"), refunded_quantity=1, refunded_amount=d("5.00"))
    )

    with pytest.raises(RefundCalculationError, match="exceeds"):
        refund(order, ("HEADPHONES-1", 2))


def test_item_outside_the_order_is_rejected() -> None:
    with pytest.raises(RefundCalculationError, match="not part of order"):
        refund(make_order(), ("OTHER-1", 1))


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ({"unit_price": d("0.01")}, Decimal("0.01")),
        ({"unit_price": d("10.00"), "refunded_amount": d("9.99")}, Decimal("0.01")),
        ({"unit_price": d("10.00"), "discount_amount": d("9.99")}, Decimal("0.01")),
        ({"unit_price": d("10.00"), "discount_amount": d("10.00")}, Decimal("0.00")),
        ({"unit_price": d("0.00"), "tax_amount": d("0.01")}, Decimal("0.01")),
    ],
    ids=[
        "one-cent-item",
        "one-cent-remaining",
        "one-cent-after-discount",
        "fully-discounted",
        "tax-only",
    ],
)
def test_one_cent_boundaries(line: dict[str, Any], expected: Decimal) -> None:
    assert single_line_refund(**line).total_refund_amount == expected


def test_half_cent_allocation_rounds_half_up_then_settles_exactly() -> None:
    line = make_line(quantity=2, unit_price=d("0.00"), tax_amount=d("0.01"))
    first = refund(make_order(line), ("HEADPHONES-1", 1)).lines[0]
    after_first = make_line(
        quantity=2,
        unit_price=d("0.00"),
        tax_amount=d("0.01"),
        refunded_quantity=1,
        refunded_amount=first.refund_amount,
    )
    second = refund(make_order(after_first), ("HEADPHONES-1", 1)).lines[0]

    assert (first.refundable_tax, second.refundable_tax) == (Decimal("0.01"), Decimal("0.00"))


def test_results_are_cent_exact_json_strings() -> None:
    result = single_line_refund(
        quantity=3, unit_price=d("10.00"), discount_amount=d("1.00"), tax_amount=d("2.00")
    )

    for amount in (result.total_refund_amount, *(b.refund_amount for b in result.lines)):
        assert amount.as_tuple().exponent == -2
    assert '"total_refund_amount":"31.00"' in result.model_dump_json()


def test_calculation_is_independent_of_the_callers_decimal_context() -> None:
    line: OrderLineFacts = make_line(
        quantity=7, unit_price=d("1234567.89"), discount_amount=d("100.01"), tax_amount=d("0.99")
    )

    with localcontext() as ctx:
        ctx.prec = 3
        result = refund(make_order(line), ("HEADPHONES-1", 7))

    assert result.total_refund_amount == Decimal("8641876.21")
