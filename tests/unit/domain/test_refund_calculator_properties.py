"""Hypothesis property tests for the refund calculator.

The ``ci`` profile (selected automatically when the ``CI`` environment variable is set, as
on GitHub Actions) is derandomized so CI runs are reproducible.
"""

import os

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from refund_invariants import (
    build_line,
    check_prior_amount_refund_never_increases_refund,
    check_refund_bounds,
    check_single_refund_equals_unit_by_unit,
    check_unit_by_unit_refunds_settle_exactly,
    min_refunded_cents,
    with_refunds,
)

from support_ops.domain.models import OrderLineFacts
from support_ops.domain.money import to_cents

# tests/unit/conftest.py has a function-scoped autouse fixture that only clears config
# environment variables; running it once per test (not per example) is correct here.
_SUPPRESSED = [HealthCheck.function_scoped_fixture]
settings.register_profile("dev", deadline=None, max_examples=200, suppress_health_check=_SUPPRESSED)
settings.register_profile(
    "ci",
    deadline=None,
    max_examples=500,
    derandomize=True,
    print_blob=True,
    suppress_health_check=_SUPPRESSED,
)
settings.load_profile("ci" if os.environ.get("CI") else "dev")

# Small values hit one-cent and rounding boundaries; large values hit realistic prices.
cents = st.one_of(st.integers(0, 5), st.integers(0, 2_000_000))


@st.composite
def lines(draw: st.DrawFn, *, require_remaining_units: bool = True) -> OrderLineFacts:
    quantity = draw(st.integers(1, 12))
    unit_cents = draw(cents)
    base = build_line(
        quantity=quantity,
        unit_cents=unit_cents,
        discount_cents=draw(st.integers(0, unit_cents * quantity)),
        tax_cents=draw(cents),
    )
    max_refunded_quantity = quantity - 1 if require_remaining_units else quantity
    refunded_quantity = draw(st.integers(0, max_refunded_quantity))
    refunded_cents = draw(
        st.integers(min_refunded_cents(base, refunded_quantity), to_cents(base.paid_total))
    )
    return with_refunds(base, refunded_quantity, refunded_cents)


@given(line=lines(), data=st.data(), include_shipping=st.booleans())
def test_refund_is_bounded_non_negative_and_cent_exact(
    line: OrderLineFacts, data: st.DataObject, include_shipping: bool
) -> None:
    quantity = data.draw(st.integers(1, line.remaining_quantity), label="quantity")
    shipping_cents = data.draw(cents, label="shipping_cents")
    shipping_refunded_cents = data.draw(st.integers(0, shipping_cents), label="shipping_refunded")

    check_refund_bounds(line, quantity, shipping_cents, shipping_refunded_cents, include_shipping)


@given(line=lines(), data=st.data(), extra_cents=cents)
def test_valid_prior_refund_never_increases_the_refund(
    line: OrderLineFacts, data: st.DataObject, extra_cents: int
) -> None:
    quantity = data.draw(st.integers(1, line.remaining_quantity), label="quantity")

    check_prior_amount_refund_never_increases_refund(line, quantity, extra_cents)


@given(line=lines())
def test_unit_by_unit_refunds_settle_exactly_to_the_remaining_paid_amount(
    line: OrderLineFacts,
) -> None:
    check_unit_by_unit_refunds_settle_exactly(line)


@given(line=lines())
def test_one_refund_of_all_units_equals_the_remaining_paid_amount(line: OrderLineFacts) -> None:
    check_single_refund_equals_unit_by_unit(line)
