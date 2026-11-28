"""Exact USD money handling.

Rounding policy: monetary inputs must already be exact to the cent and are never rounded.
The only rounding in the domain is proration (allocating a line's discount or tax to a
subset of its units), which rounds half up to $0.01 using exact integer arithmetic on
cents. Allocation is cumulative (see ``allocate_cumulative_cents``), so refunding every
unit across several partial refunds always sums exactly to the original line amounts.
"""

import re
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from typing import Annotated, Final

from pydantic import PlainSerializer, PlainValidator

CENT: Final = Decimal("0.01")
ZERO: Final = Decimal("0.00")
MAX_MONEY: Final = Decimal("9999999999.99")
ROUNDING_POLICY: Final = "half_up_to_cent"

MONEY_CONTEXT: Final = Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)
"""Decimal context for money arithmetic. Trapping ``Inexact`` turns any silent rounding of
cent amounts into an error; all intended rounding is done on integer cents."""

_VALIDATION_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_UP)

_DECIMAL_STRING = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def _parse_money(value: object) -> Decimal:
    # bool is an int subclass and float is inexact; neither may silently become money.
    if isinstance(value, bool | float):
        raise ValueError("Money must be a Decimal, int, or decimal string, never float or bool.")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, str):
        if not _DECIMAL_STRING.fullmatch(value):
            raise ValueError("Money strings must be plain non-negative decimals like '129.99'.")
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise ValueError("Money string is not a valid decimal.") from None
    else:
        raise ValueError("Money must be a Decimal, int, or decimal string.")

    if not amount.is_finite():
        raise ValueError("Money must be finite.")
    if amount < 0:
        raise ValueError("Money must not be negative.")
    if amount > MAX_MONEY:
        raise ValueError("Money exceeds the supported maximum of 9999999999.99.")
    # An explicit context keeps validation independent of the caller's decimal context.
    cents = amount.quantize(CENT, context=_VALIDATION_CONTEXT)
    if amount != cents:
        raise ValueError("Money must have at most two decimal places; it is never rounded.")
    # copy_abs() normalizes Decimal("-0") so zero always serializes as "0.00".
    return cents.copy_abs()


Money = Annotated[
    Decimal,
    PlainValidator(_parse_money, json_schema_input_type=str),
    PlainSerializer(str, return_type=str, when_used="json"),
]
"""Non-negative USD amount, exact to the cent (NUMERIC(12,2) in PostgreSQL)."""


def to_cents(amount: Decimal) -> int:
    """Convert an exact-to-the-cent amount to integer cents."""
    scaled = amount.scaleb(2)
    if scaled != scaled.to_integral_value():
        raise ValueError("Amount is not exact to the cent.")
    return int(scaled)


def from_cents(cents: int) -> Decimal:
    return Decimal(cents).scaleb(-2).quantize(CENT)


def allocate_cumulative_cents(total_cents: int, units: int, of_units: int) -> int:
    """Return the cents of ``total_cents`` allocated to the first ``units`` of ``of_units``.

    Rounds half up. ``units == of_units`` always returns ``total_cents`` exactly, and the
    result is non-decreasing in ``units``, so the difference between two cumulative
    allocations is the exact, drift-free share of the units in between.
    """
    if of_units < 1 or not 0 <= units <= of_units or total_cents < 0:
        raise ValueError("Invalid proration arguments.")
    quotient, remainder = divmod(total_cents * units, of_units)
    return quotient + (1 if 2 * remainder >= of_units else 0)
