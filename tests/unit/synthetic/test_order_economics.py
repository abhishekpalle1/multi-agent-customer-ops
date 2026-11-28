"""Centralized synthetic order economics: cent-exact pricing and calculator-backed ledgers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from support_ops.domain.enums import Currency, OrderStatus, PaymentStatus, ShipmentStatus
from support_ops.domain.models import RefundCalculationInput, RefundSelection
from support_ops.domain.money import ZERO, from_cents
from support_ops.domain.refund_calculator import calculate_refund
from support_ops.synthetic.economics import (
    OrderState,
    apply_amount_adjustment,
    apply_calculated_refund,
    apply_cancellation_reversal,
    explicit_line,
    line_paid_total,
    order_total,
    payment_status,
    percent_of_cents,
    price_line,
)
from support_ops.synthetic.facts import order_facts
from support_ops.synthetic.records import (
    OrderItemRecord,
    OrderRecord,
    PaymentRecord,
    ShipmentRecord,
    SyntheticDatasetError,
)

_SUPPRESSED = [HealthCheck.function_scoped_fixture]
PLACED = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
RETURNABLE = {"SKU-T1": True}


def _state(
    unit_cents: int,
    quantity: int,
    discount_bps: int,
    tax_bps: int,
    shipping_cents: int,
    status: OrderStatus = OrderStatus.DELIVERED,
) -> OrderState:
    price = price_line(unit_cents, quantity, discount_bps=discount_bps, tax_bps=tax_bps)
    item = OrderItemRecord(
        order_item_id="ITEM-T-1",
        order_id="ORD-T",
        product_id="SKU-T1",
        quantity=quantity,
        unit_price=price.unit_price,
        discount_amount=price.discount,
        tax_amount=price.tax,
        refunded_quantity=0,
        refunded_amount=ZERO,
        created_at=PLACED,
        updated_at=PLACED,
    )
    shipping = from_cents(shipping_cents)
    shipment = None
    if status is OrderStatus.DELIVERED:
        shipment = ShipmentRecord(
            shipment_id="SHP-T",
            order_id="ORD-T",
            carrier="Test Carrier",
            tracking_number="TRK-T",
            status=ShipmentStatus.DELIVERED,
            shipped_at=PLACED + timedelta(days=1),
            estimated_delivery_date=None,
            delivered_at=PLACED + timedelta(days=3),
            created_at=PLACED,
            updated_at=PLACED,
        )
    return OrderState(
        order=OrderRecord(
            order_id="ORD-T",
            customer_id="CUST-T",
            status=status,
            currency=Currency.USD,
            shipping_amount=shipping,
            shipping_refunded_amount=ZERO,
            placed_at=PLACED,
            created_at=PLACED,
            updated_at=PLACED,
        ),
        items=(item,),
        shipment=shipment,
        payment=PaymentRecord(
            payment_id="PAY-T",
            order_id="ORD-T",
            currency=Currency.USD,
            amount=order_total((item,), shipping),
            amount_refunded=ZERO,
            status=PaymentStatus.CAPTURED,
            captured_at=PLACED,
            created_at=PLACED,
            updated_at=PLACED,
        ),
    )


@pytest.mark.parametrize(
    ("cents", "bps", "expected"),
    [(1000, 825, 83), (999, 725, 72), (200, 2500, 50), (1, 5000, 1), (1, 4999, 0), (0, 825, 0)],
)
def test_percent_of_cents_rounds_half_up_exactly(cents: int, bps: int, expected: int) -> None:
    assert percent_of_cents(cents, bps) == expected


def test_price_line_known_values() -> None:
    price = price_line(2499, 3, discount_bps=1000, tax_bps=825)

    assert (price.gross, price.discount, price.tax, price.paid) == (
        Decimal("74.97"),
        Decimal("7.50"),
        Decimal("5.57"),
        Decimal("73.04"),
    )


def test_explicit_line_rejects_a_discount_above_gross() -> None:
    with pytest.raises(SyntheticDatasetError):
        explicit_line("10.00", 1, discount="10.01", tax="0.00")


def test_payment_status_follows_the_refunded_balance() -> None:
    assert payment_status(Decimal("10.00"), ZERO) is PaymentStatus.CAPTURED
    assert payment_status(Decimal("10.00"), Decimal("0.01")) is PaymentStatus.PARTIALLY_REFUNDED
    assert payment_status(Decimal("10.00"), Decimal("10.00")) is PaymentStatus.REFUNDED


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESSED)
@given(
    unit=st.integers(1, 1_000_000),
    quantity=st.integers(1, 5),
    discount_bps=st.integers(0, 10_000),
    tax_bps=st.integers(0, 2_500),
)
def test_line_pricing_is_cent_exact_and_consistent(
    unit: int, quantity: int, discount_bps: int, tax_bps: int
) -> None:
    price = price_line(unit, quantity, discount_bps=discount_bps, tax_bps=tax_bps)

    for amount in (price.gross, price.discount, price.tax, price.paid):
        assert isinstance(amount, Decimal)
        assert amount.as_tuple().exponent == -2
        assert amount >= 0
    assert price.gross == price.unit_price * quantity
    assert price.discount <= price.gross
    assert price.paid == price.gross - price.discount + price.tax


@settings(max_examples=150, deadline=None, suppress_health_check=_SUPPRESSED)
@given(
    unit=st.integers(100, 100_000),
    quantity=st.integers(1, 4),
    discount_bps=st.sampled_from((0, 1000, 1500, 3333)),
    tax_bps=st.sampled_from((0, 600, 725, 825)),
    shipping=st.sampled_from((0, 599, 999)),
    data=st.data(),
)
def test_calculator_backed_refunds_record_exactly_the_calculator_result(
    unit: int,
    quantity: int,
    discount_bps: int,
    tax_bps: int,
    shipping: int,
    data: st.DataObject,
) -> None:
    state = _state(unit, quantity, discount_bps, tax_bps, shipping)
    selected = data.draw(st.integers(1, quantity))
    include_shipping = data.draw(st.booleans())
    at = PLACED + timedelta(days=4)
    expected = calculate_refund(
        RefundCalculationInput(
            order=order_facts(state.order, state.items, state.shipment, RETURNABLE),
            selections=(RefundSelection(order_item_id="ITEM-T-1", quantity=selected),),
            include_shipping=include_shipping,
        )
    )
    if expected.total_refund_amount == 0:
        return

    after = apply_calculated_refund(
        state,
        returnable_by_product=RETURNABLE,
        selections=(("ITEM-T-1", selected),),
        include_shipping=include_shipping,
        refund_id="REF-T-1",
        refunded_at=at,
    )

    (refund,) = after.refunds
    (line,) = after.refund_items
    assert refund.amount == expected.total_refund_amount
    assert refund.shipping_amount == expected.shipping_refund_amount
    assert line.amount == expected.items_refund_total
    assert line.quantity == selected
    assert after.items[0].refunded_quantity == selected
    assert after.items[0].refunded_amount == expected.items_refund_total
    assert after.order.shipping_refunded_amount == expected.shipping_refund_amount
    assert after.payment.amount_refunded == refund.amount
    assert after.payment.status is payment_status(after.payment.amount, refund.amount)


def test_amount_adjustment_records_zero_quantity_and_is_bounded() -> None:
    state = _state(8000, 1, 0, 800, 699)

    after = apply_amount_adjustment(
        state,
        returnable_by_product=RETURNABLE,
        order_item_id="ITEM-T-1",
        amount=Decimal("30.00"),
        refund_id="REF-T-1",
        refunded_at=PLACED + timedelta(days=5),
    )

    assert after.refund_items[0].quantity == 0
    assert after.items[0].refunded_quantity == 0
    assert after.items[0].refunded_amount == Decimal("30.00")
    with pytest.raises(SyntheticDatasetError):
        apply_amount_adjustment(
            state,
            returnable_by_product=RETURNABLE,
            order_item_id="ITEM-T-1",
            amount=line_paid_total(state.items[0]) + Decimal("0.01"),
            refund_id="REF-T-1",
            refunded_at=PLACED + timedelta(days=5),
        )


def test_cancellation_reversal_returns_the_whole_payment_including_shipping() -> None:
    state = _state(4950, 2, 1000, 825, 699, status=OrderStatus.CANCELLED)

    after = apply_cancellation_reversal(
        state,
        returnable_by_product=RETURNABLE,
        refund_id="REF-T-1",
        refunded_at=PLACED + timedelta(hours=3),
    )

    assert after.payment.amount_refunded == state.payment.amount
    assert after.payment.status is PaymentStatus.REFUNDED
    assert after.refunds[0].shipping_amount == Decimal("6.99")
    assert after.items[0].refunded_quantity == 2


def test_cancellation_reversal_requires_a_cancelled_order() -> None:
    with pytest.raises(SyntheticDatasetError, match="cancelled"):
        apply_cancellation_reversal(
            _state(4950, 1, 0, 0, 0),
            returnable_by_product=RETURNABLE,
            refund_id="REF-T-1",
            refunded_at=PLACED + timedelta(hours=3),
        )
