import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from domain_factories import DELIVERED_AT, PLACED_AT, SHIPPED_AT, d, make_line, make_order
from pydantic import ValidationError

from support_ops.domain.enums import ActionType, OrderStatus, PolicyDecision, RequestReason, Role
from support_ops.domain.errors import DomainInvariantError
from support_ops.domain.models import (
    ActionId,
    AuthContext,
    CaseId,
    CustomerId,
    DomainModel,
    EntityId,
    ItemPolicyResult,
    OrderFacts,
    OrderId,
    PolicyRequest,
    PolicyRuleId,
    RefundCalculationInput,
    RefundSelection,
    UtcDatetime,
)
from support_ops.domain.money import Money


class MoneyHolder(DomainModel):
    amount: Money


class Ids(DomainModel):
    entity: EntityId = "HEADPHONES-1"
    customer: CustomerId = "CUST-10"
    order: OrderId = "ORD-1042"
    case: CaseId = "CASE-1"
    action: ActionId = "ACT-123"
    rule: PolicyRuleId = "DMG-4.2"


class Stamp(DomainModel):
    at: UtcDatetime


# --- Money ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("129.99"), "129.99"),
        ("129.99", "129.99"),
        ("1.5", "1.50"),
        (7, "7.00"),
        ("0", "0.00"),
        (Decimal("-0"), "0.00"),
        (Decimal("1.500"), "1.50"),
        ("9999999999.99", "9999999999.99"),
    ],
)
def test_money_accepts_exact_cent_amounts(raw: object, expected: str) -> None:
    holder = MoneyHolder(amount=raw)  # type: ignore[arg-type]

    assert isinstance(holder.amount, Decimal)
    assert str(holder.amount) == expected
    assert holder.amount.as_tuple().exponent == -2


@pytest.mark.parametrize(
    "raw",
    [
        1.5,
        0.1,
        True,
        "1.005",
        Decimal("0.001"),
        "-0.01",
        Decimal("-1"),
        -1,
        "1e3",
        "NaN",
        Decimal("NaN"),
        Decimal("Infinity"),
        "10000000000.00",
        " 1.00",
        "",
        "1,000.00",
        None,
    ],
)
def test_money_rejects_inexact_negative_or_non_decimal_values(raw: object) -> None:
    with pytest.raises(ValidationError):
        MoneyHolder(amount=raw)  # type: ignore[arg-type]


def test_money_serializes_to_json_string_and_round_trips_exactly() -> None:
    holder = MoneyHolder(amount=Decimal("0.10"))

    assert holder.model_dump_json() == '{"amount":"0.10"}'
    restored = MoneyHolder.model_validate_json(holder.model_dump_json())
    assert restored.amount + MoneyHolder(amount="0.20").amount == Decimal("0.30")


def test_money_rejects_json_float_numbers() -> None:
    with pytest.raises(ValidationError):
        MoneyHolder.model_validate_json('{"amount": 0.1}')


# --- Identifiers ----------------------------------------------------------------------


def test_identifier_defaults_are_valid() -> None:
    Ids()


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("entity", ""),
        ("entity", "headphones-1"),
        ("entity", "HEADPHONES 1"),
        ("entity", "HEADPHONES--1"),
        ("entity", "-HEADPHONES"),
        ("entity", "A" * 65),
        ("customer", "CUST-"),
        ("customer", "ORD-10"),
        ("order", "1042"),
        ("order", "ord-1042"),
        ("case", "CASE"),
        ("action", "ACTION-1"),
        ("rule", "DMG"),
        ("rule", "dmg-4.2"),
        ("rule", "DMG-4."),
    ],
)
def test_malformed_identifiers_are_rejected(field: str, raw: str) -> None:
    with pytest.raises(ValidationError):
        Ids(**{field: raw})


def test_identifiers_are_strict_strings() -> None:
    with pytest.raises(ValidationError):
        Ids(order=1042)  # type: ignore[arg-type]


# --- Datetimes, immutability, strictness ----------------------------------------------


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Stamp(at=datetime(2026, 10, 5, 12, 0))


def test_aware_datetimes_are_normalized_to_utc() -> None:
    chicago = timezone(timedelta(hours=-6))

    stamp = Stamp(at=datetime(2026, 10, 5, 20, 0, tzinfo=chicago))

    assert stamp.at == datetime(2026, 10, 6, 2, 0, tzinfo=UTC)
    assert stamp.at.tzinfo is UTC


def test_contracts_are_frozen_and_forbid_extra_fields() -> None:
    line = make_line()

    with pytest.raises(ValidationError):
        line.quantity = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        make_line(unexpected="value")


def test_strict_mode_rejects_coercible_strings_for_integers() -> None:
    with pytest.raises(ValidationError):
        make_line(quantity="1")


# --- AuthContext ----------------------------------------------------------------------


def test_customer_context_requires_customer_id() -> None:
    assert AuthContext(role=Role.CUSTOMER, customer_id="CUST-10").customer_id == "CUST-10"
    with pytest.raises(ValidationError, match="requires customer_id"):
        AuthContext(role=Role.CUSTOMER)


def test_supervisor_context_must_not_carry_customer_id() -> None:
    assert AuthContext(role=Role.SUPERVISOR).customer_id is None
    with pytest.raises(ValidationError, match="must not carry"):
        AuthContext(role=Role.SUPERVISOR, customer_id="CUST-10")


# --- Trusted order facts --------------------------------------------------------------


def test_line_derived_amounts_are_exact() -> None:
    line = make_line(
        quantity=3, unit_price=d("10.00"), discount_amount=d("1.00"), tax_amount=d("2.00")
    )

    assert line.gross_amount == Decimal("30.00")
    assert line.paid_total == Decimal("31.00")
    assert line.remaining_paid_amount == Decimal("31.00")


@pytest.mark.parametrize(
    "overrides",
    [
        {"unit_price": d("10.00"), "discount_amount": d("10.01")},
        {"quantity": 1, "refunded_quantity": 2},
        {"unit_price": d("10.00"), "refunded_amount": d("10.01")},
        {"unit_price": d("10.00"), "refunded_quantity": 1, "refunded_amount": d("9.99")},
    ],
    ids=[
        "discount-exceeds-gross",
        "refunded-quantity-exceeds-quantity",
        "refunded-amount-exceeds-paid",
        "refunded-amount-below-refunded-units",
    ],
)
def test_contradictory_line_facts_raise_invariant_error(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainInvariantError):
        make_line(**overrides)


def test_amount_only_prior_refund_is_consistent() -> None:
    line = make_line(unit_price=d("100.00"), refunded_amount=d("20.00"))

    assert line.remaining_quantity == 1
    assert line.remaining_paid_amount == Decimal("80.00")


def test_order_rejects_duplicate_and_empty_lines() -> None:
    with pytest.raises(ValidationError, match="Duplicate order item ID"):
        make_order(make_line(), make_line())
    with pytest.raises(ValidationError):
        make_order(lines=())


@pytest.mark.parametrize(
    ("status", "shipped_at", "delivered_at"),
    [
        (OrderStatus.PLACED, SHIPPED_AT, None),
        (OrderStatus.CANCELLED, SHIPPED_AT, None),
        (OrderStatus.SHIPPED, None, None),
        (OrderStatus.SHIPPED, SHIPPED_AT, DELIVERED_AT),
        (OrderStatus.DELIVERED, SHIPPED_AT, None),
    ],
)
def test_order_status_must_match_shipment_timestamps(
    status: OrderStatus, shipped_at: datetime | None, delivered_at: datetime | None
) -> None:
    with pytest.raises(DomainInvariantError, match="contradicts"):
        make_order(status=status, shipped_at=shipped_at, delivered_at=delivered_at)


@pytest.mark.parametrize(
    "overrides",
    [
        {"shipped_at": PLACED_AT - timedelta(seconds=1)},
        {"delivered_at": SHIPPED_AT - timedelta(seconds=1)},
        {"shipping_amount": d("5.00"), "shipping_refunded_amount": d("5.01")},
    ],
    ids=["shipped-before-placed", "delivered-before-shipped", "shipping-over-refunded"],
)
def test_contradictory_order_facts_raise_invariant_error(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainInvariantError):
        make_order(**overrides)


@pytest.mark.parametrize("currency", ["EUR", "usd", ""])
def test_unsupported_currency_is_rejected(currency: str) -> None:
    payload = make_order().model_dump(mode="json")
    payload["currency"] = currency

    with pytest.raises(ValidationError):
        OrderFacts.model_validate_json(json.dumps(payload))


def test_order_facts_round_trip_through_json_exactly() -> None:
    order = make_order(make_line(unit_price=d("0.10"), tax_amount=d("0.01")))

    assert OrderFacts.model_validate_json(order.model_dump_json()) == order


# --- Calculation and policy request shapes --------------------------------------------


def test_refund_input_rejects_duplicate_and_empty_selections() -> None:
    order = make_order()
    selection = RefundSelection(order_item_id="HEADPHONES-1", quantity=1)

    with pytest.raises(ValidationError, match="Duplicate selected order item ID"):
        RefundCalculationInput(
            order=order, selections=(selection, selection), include_shipping=False
        )
    with pytest.raises(ValidationError):
        RefundCalculationInput(order=order, selections=(), include_shipping=False)
    with pytest.raises(ValidationError):
        RefundSelection(order_item_id="HEADPHONES-1", quantity=0)


def _policy_request(**overrides: Any) -> PolicyRequest:
    values: dict[str, Any] = {
        "action_type": ActionType.REFUND,
        "reason": RequestReason.DAMAGED_ITEM,
        "item_ids": ("HEADPHONES-1",),
        "evaluated_at": DELIVERED_AT + timedelta(days=1),
        "order": make_order(),
    }
    values.update(overrides)
    return PolicyRequest(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"item_ids": ()}, "at least one item"),
        ({"item_ids": ("HEADPHONES-1", "HEADPHONES-1")}, "Duplicate item ID"),
        ({"action_type": ActionType.CANCELLATION}, "must not name item IDs"),
        ({"action_type": ActionType.REPLACEMENT}, "requires in_stock_item_ids"),
        (
            {"action_type": ActionType.REPLACEMENT, "in_stock_item_ids": frozenset({"OTHER-1"})},
            "subset",
        ),
        ({"in_stock_item_ids": frozenset({"HEADPHONES-1"})}, "only valid for replacement"),
    ],
)
def test_policy_request_shape_is_validated(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _policy_request(**overrides)


def test_policy_request_naive_evaluation_time_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _policy_request(evaluated_at=datetime(2026, 10, 6, 12, 0))


def test_policy_results_reject_duplicate_rule_ids() -> None:
    with pytest.raises(ValidationError, match="Duplicate rule ID"):
        ItemPolicyResult(
            order_item_id="HEADPHONES-1",
            decision=PolicyDecision.ELIGIBLE,
            triggered_rule_ids=("DMG-4.1", "DMG-4.1"),
            reason_codes=(),
        )
