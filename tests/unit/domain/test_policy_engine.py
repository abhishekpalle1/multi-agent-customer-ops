from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from domain_factories import DELIVERED_AT, PLACED_AT, d, days_after_delivery, make_line, make_order
from pydantic import TypeAdapter

from support_ops.domain.enums import ActionType, OrderStatus, PolicyDecision, RequestReason
from support_ops.domain.enums import PolicyReasonCode as C
from support_ops.domain.errors import PolicyEvaluationError
from support_ops.domain.models import PolicyEvaluation, PolicyRequest, PolicyRuleId
from support_ops.domain.policy_engine import (
    DAMAGE_REPORT_WINDOW_DAYS,
    POLICY_RULES,
    RETURN_WINDOW_DAYS,
    RULESET_VERSION,
    evaluate_policy,
)

ITEM = "HEADPHONES-1"
REFUND, RETURN, REPLACE, CANCEL = (
    ActionType.REFUND,
    ActionType.RETURN,
    ActionType.REPLACEMENT,
    ActionType.CANCELLATION,
)
DAMAGED, CHANGE = RequestReason.DAMAGED_ITEM, RequestReason.CHANGE_OF_MIND
ELIGIBLE, INELIGIBLE = PolicyDecision.ELIGIBLE, PolicyDecision.INELIGIBLE


@dataclass(frozen=True)
class Case:
    id: str
    action: ActionType
    reason: RequestReason
    expected: PolicyDecision
    rule_ids: tuple[str, ...]
    codes: tuple[C, ...]
    days: int = 8
    status: OrderStatus = OrderStatus.DELIVERED
    line: dict[str, Any] = field(default_factory=dict)
    in_stock: bool = True
    shipping_refundable: bool = False


CASES = [
    Case("damaged-8-days-refund", REFUND, DAMAGED, ELIGIBLE,
         ("DMG-4.1", "DMG-4.2", "RFD-2.2"),
         (C.WITHIN_DAMAGE_WINDOW, C.DAMAGED_ITEM_ELIGIBLE, C.SHIPPING_REFUNDABLE),
         shipping_refundable=True),
    Case("damaged-8-days-return", RETURN, DAMAGED, ELIGIBLE,
         ("DMG-4.1", "DMG-4.2"), (C.WITHIN_DAMAGE_WINDOW, C.DAMAGED_ITEM_ELIGIBLE)),
    Case("damaged-day-30-boundary", REFUND, DAMAGED, ELIGIBLE,
         ("DMG-4.1", "DMG-4.2", "RFD-2.2"),
         (C.WITHIN_DAMAGE_WINDOW, C.DAMAGED_ITEM_ELIGIBLE, C.SHIPPING_REFUNDABLE),
         days=30, shipping_refundable=True),
    Case("damaged-day-31-expired", REFUND, DAMAGED, INELIGIBLE,
         ("DMG-4.1", "RFD-2.2"), (C.DAMAGE_REPORT_WINDOW_EXPIRED, C.SHIPPING_NOT_REFUNDABLE),
         days=31),
    Case("clearance-damaged-exception", REFUND, DAMAGED, ELIGIBLE,
         ("DMG-4.1", "DMG-4.2", "RFD-2.2"),
         (C.WITHIN_DAMAGE_WINDOW, C.DAMAGED_ITEM_EXCEPTION, C.SHIPPING_REFUNDABLE),
         line={"returnable": False}, shipping_refundable=True),
    Case("change-of-mind-within-window", RETURN, CHANGE, ELIGIBLE,
         ("RET-3.1",), (C.WITHIN_RETURN_WINDOW,)),
    Case("change-of-mind-refund-no-shipping", REFUND, CHANGE, ELIGIBLE,
         ("RET-3.1", "RFD-2.2"), (C.WITHIN_RETURN_WINDOW, C.SHIPPING_NOT_REFUNDABLE)),
    Case("change-of-mind-day-30-boundary", RETURN, CHANGE, ELIGIBLE,
         ("RET-3.1",), (C.WITHIN_RETURN_WINDOW,), days=30),
    Case("expired-31-days", RETURN, CHANGE, INELIGIBLE,
         ("RET-3.1",), (C.RETURN_WINDOW_EXPIRED,), days=31),
    Case("expired-90-days", REFUND, CHANGE, INELIGIBLE,
         ("RET-3.1", "RFD-2.2"), (C.RETURN_WINDOW_EXPIRED, C.SHIPPING_NOT_REFUNDABLE), days=90),
    Case("non-returnable-change-of-mind", RETURN, CHANGE, INELIGIBLE,
         ("RET-3.1", "RET-3.2"), (C.WITHIN_RETURN_WINDOW, C.ITEM_NON_RETURNABLE),
         line={"returnable": False}),
    Case("non-returnable-and-expired", RETURN, CHANGE, INELIGIBLE,
         ("RET-3.1", "RET-3.2"), (C.RETURN_WINDOW_EXPIRED, C.ITEM_NON_RETURNABLE),
         days=31, line={"returnable": False}),
    Case("already-fully-refunded", REFUND, DAMAGED, INELIGIBLE,
         ("RFD-2.1", "RFD-2.2"), (C.ITEM_ALREADY_REFUNDED, C.SHIPPING_NOT_REFUNDABLE),
         line={"refunded_quantity": 1, "refunded_amount": d("129.99")}),
    Case("partially-refunded-remaining-unit", REFUND, DAMAGED, ELIGIBLE,
         ("DMG-4.1", "DMG-4.2", "RFD-2.2"),
         (C.WITHIN_DAMAGE_WINDOW, C.DAMAGED_ITEM_ELIGIBLE, C.SHIPPING_REFUNDABLE),
         line={"quantity": 2, "refunded_quantity": 1, "refunded_amount": d("129.99")},
         shipping_refundable=True),
    Case("shipped-not-delivered-refund", REFUND, DAMAGED, INELIGIBLE,
         ("ORD-1.2",), (C.ORDER_NOT_DELIVERED,), status=OrderStatus.SHIPPED),
    Case("placed-not-delivered-return", RETURN, CHANGE, INELIGIBLE,
         ("ORD-1.2",), (C.ORDER_NOT_DELIVERED,), status=OrderStatus.PLACED),
    Case("cancelled-order-refund", REFUND, CHANGE, INELIGIBLE,
         ("ORD-1.1",), (C.ORDER_CANCELLED,), status=OrderStatus.CANCELLED),
    Case("replacement-damaged-in-stock", REPLACE, DAMAGED, ELIGIBLE,
         ("DMG-4.1", "DMG-4.2", "RPL-6.2"),
         (C.WITHIN_DAMAGE_WINDOW, C.DAMAGED_ITEM_ELIGIBLE, C.REPLACEMENT_IN_STOCK)),
    Case("replacement-out-of-stock", REPLACE, DAMAGED, INELIGIBLE,
         ("DMG-4.1", "DMG-4.2", "RPL-6.2"),
         (C.WITHIN_DAMAGE_WINDOW, C.DAMAGED_ITEM_ELIGIBLE, C.REPLACEMENT_OUT_OF_STOCK),
         in_stock=False),
    Case("replacement-requires-damage", REPLACE, CHANGE, INELIGIBLE,
         ("RPL-6.1",), (C.REPLACEMENT_REQUIRES_DAMAGE,)),
    Case("replacement-damage-window-expired", REPLACE, DAMAGED, INELIGIBLE,
         ("DMG-4.1", "RPL-6.2"), (C.DAMAGE_REPORT_WINDOW_EXPIRED, C.REPLACEMENT_IN_STOCK),
         days=45),
    Case("cancel-before-shipment", CANCEL, CHANGE, ELIGIBLE,
         ("CAN-5.1",), (C.CANCELLATION_BEFORE_SHIPMENT,), status=OrderStatus.PLACED),
    Case("cancel-shipped-not-delivered", CANCEL, CHANGE, INELIGIBLE,
         ("CAN-5.2",), (C.ORDER_ALREADY_SHIPPED,), status=OrderStatus.SHIPPED),
    Case("cancel-delivered", CANCEL, DAMAGED, INELIGIBLE,
         ("CAN-5.2",), (C.ORDER_ALREADY_DELIVERED,)),
    Case("cancel-already-cancelled", CANCEL, CHANGE, INELIGIBLE,
         ("CAN-5.3",), (C.ORDER_ALREADY_CANCELLED,), status=OrderStatus.CANCELLED),
]  # fmt: skip


def _with(request: PolicyRequest, **changes: Any) -> PolicyRequest:
    """Rebuild a request with changes, re-running validation (unlike ``model_copy``)."""
    return PolicyRequest(**(dict(request) | changes))


def build_request(case: Case) -> PolicyRequest:
    item_ids = () if case.action is CANCEL else (ITEM,)
    evaluated_at = (
        days_after_delivery(case.days)
        if case.status is OrderStatus.DELIVERED
        else PLACED_AT + timedelta(days=case.days)
    )
    return PolicyRequest(
        action_type=case.action,
        reason=case.reason,
        item_ids=item_ids,
        evaluated_at=evaluated_at,
        order=make_order(make_line(**case.line), status=case.status),
        in_stock_item_ids=(
            (frozenset({ITEM}) if case.in_stock else frozenset())
            if case.action is REPLACE
            else None
        ),
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_policy_outcomes(case: Case) -> None:
    result = evaluate_policy(build_request(case))

    assert result.decision is case.expected
    assert result.triggered_rule_ids == case.rule_ids
    assert result.reason_codes == case.codes
    assert result.shipping_refundable is case.shipping_refundable
    assert result.ruleset_version == RULESET_VERSION
    for item in result.item_results:
        assert set(item.triggered_rule_ids) <= set(result.triggered_rule_ids)
    if case.action is CANCEL:
        assert result.item_results == ()
    else:
        assert [r.order_item_id for r in result.item_results] == [ITEM]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_every_triggered_rule_is_in_the_catalog_for_that_action(case: Case) -> None:
    result = evaluate_policy(build_request(case))

    for rule_id in result.triggered_rule_ids:
        assert case.action in POLICY_RULES[rule_id].applies_to


def test_delivered_evaluation_reports_dates_and_window() -> None:
    result = evaluate_policy(build_request(Case("x", REFUND, DAMAGED, ELIGIBLE, (), (), days=8)))

    assert result.delivered_on == date(2026, 10, 5)
    assert result.days_since_delivery == 8
    assert result.window_days == DAMAGE_REPORT_WINDOW_DAYS == 30
    assert result.window_ends_on == date(2026, 11, 4)
    assert RETURN_WINDOW_DAYS == 30


def test_window_uses_utc_calendar_days() -> None:
    """Day 30 (2026-11-04) ends at 23:59:59 UTC; a local time zone does not move it."""
    chicago = timezone(timedelta(hours=-6))
    last_moment = datetime(2026, 11, 4, 17, 59, 59, tzinfo=chicago)  # 23:59:59 UTC
    first_late = datetime(2026, 11, 4, 18, 0, tzinfo=chicago)  # 2026-11-05 00:00 UTC
    request = build_request(Case("x", RETURN, CHANGE, ELIGIBLE, (), ()))

    def decide(at: datetime) -> PolicyDecision:
        return evaluate_policy(_with(request, evaluated_at=at)).decision

    assert decide(last_moment) is ELIGIBLE
    assert decide(first_late) is INELIGIBLE


def test_mixed_items_are_evaluated_individually() -> None:
    order = make_order(
        make_line(order_item_id="MUG-1"),
        make_line(order_item_id="CANDLE-1", returnable=False),
    )
    request = PolicyRequest(
        action_type=RETURN,
        reason=CHANGE,
        item_ids=("MUG-1", "CANDLE-1"),
        evaluated_at=days_after_delivery(5),
        order=order,
    )

    result = evaluate_policy(request)

    assert result.decision is INELIGIBLE
    assert [r.decision for r in result.item_results] == [ELIGIBLE, INELIGIBLE]
    assert result.item_results[1].reason_codes == (C.WITHIN_RETURN_WINDOW, C.ITEM_NON_RETURNABLE)
    assert result.triggered_rule_ids == ("RET-3.1", "RET-3.2")


def test_evaluation_is_deterministic() -> None:
    request = build_request(CASES[0])

    assert evaluate_policy(request) == evaluate_policy(request)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"item_ids": ("OTHER-1",)}, "not part of order"),
        ({"evaluated_at": DELIVERED_AT - timedelta(minutes=1)}, "precedes delivery"),
        ({"evaluated_at": PLACED_AT - timedelta(minutes=1)}, "precedes placement"),
    ],
)
def test_impossible_requests_raise_policy_evaluation_error(
    overrides: dict[str, Any], message: str
) -> None:
    request = _with(build_request(CASES[0]), **overrides)

    with pytest.raises(PolicyEvaluationError, match=message):
        evaluate_policy(request)


def test_rule_ids_are_stable() -> None:
    assert set(POLICY_RULES) == {
        "ORD-1.1",
        "ORD-1.2",
        "RFD-2.1",
        "RFD-2.2",
        "RET-3.1",
        "RET-3.2",
        "DMG-4.1",
        "DMG-4.2",
        "CAN-5.1",
        "CAN-5.2",
        "CAN-5.3",
        "RPL-6.1",
        "RPL-6.2",
    }
    adapter = TypeAdapter(PolicyRuleId)
    for rule_id, rule in POLICY_RULES.items():
        assert adapter.validate_python(rule_id) == rule.rule_id == rule_id


def test_policy_evaluation_serializes_to_stable_json_values() -> None:
    result = evaluate_policy(build_request(CASES[0]))
    payload = result.model_dump(mode="json")

    assert payload["decision"] == "eligible"
    assert payload["reason_codes"] == [
        "within_damage_window",
        "damaged_item_eligible",
        "shipping_refundable",
    ]
    assert PolicyEvaluation.model_validate_json(result.model_dump_json()) == result
