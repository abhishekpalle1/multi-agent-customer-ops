"""Deterministic executable policy engine.

Evaluates trusted structured facts against a small, explicit rule catalog. It does not read
policy prose, retrieve documents, use embeddings, or authorize anything; approval thresholds,
ownership, amount, and duplicate checks belong to ``ActionAuthorizationService`` (Phase 3).

Synthetic project policy (fictional, for this demo platform only):

- The standard change-of-mind return window and the damaged-item reporting window are both
  30 UTC calendar days after the delivery date. Day 30 is eligible; day 31 is not.
- A damaged item reported in time is eligible even if the item is non-returnable.
- The unrefunded remainder of the original shipping is refundable only for eligible
  damaged-item refunds.
- Orders may be cancelled only before shipment.
- Replacements require a damaged item and available stock.

Order-level rules short-circuit. Item-level rules are all evaluated (after the
already-refunded and replacement-reason gates), and every rule that produced a reason code
is reported in a fixed, deterministic order.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, timedelta
from types import MappingProxyType
from typing import Final

from support_ops.domain.enums import (
    ActionType,
    OrderStatus,
    PolicyDecision,
    PolicyReasonCode,
    RequestReason,
)
from support_ops.domain.errors import PolicyEvaluationError
from support_ops.domain.models import (
    ItemPolicyResult,
    OrderLineFacts,
    PolicyEvaluation,
    PolicyRequest,
    PolicyRule,
)

RULESET_VERSION: Final = "synthetic-p1-2026.11"
RETURN_WINDOW_DAYS: Final = 30
DAMAGE_REPORT_WINDOW_DAYS: Final = 30

ORDER_CANCELLED: Final = "ORD-1.1"
ORDER_NOT_DELIVERED: Final = "ORD-1.2"
ITEM_ALREADY_REFUNDED: Final = "RFD-2.1"
SHIPPING_REFUND: Final = "RFD-2.2"
RETURN_WINDOW: Final = "RET-3.1"
NON_RETURNABLE: Final = "RET-3.2"
DAMAGE_WINDOW: Final = "DMG-4.1"
DAMAGED_ITEM_EXCEPTION: Final = "DMG-4.2"
CANCEL_BEFORE_SHIPMENT: Final = "CAN-5.1"
CANCEL_AFTER_SHIPMENT: Final = "CAN-5.2"
ALREADY_CANCELLED: Final = "CAN-5.3"
REPLACEMENT_REQUIRES_DAMAGE: Final = "RPL-6.1"
REPLACEMENT_STOCK: Final = "RPL-6.2"

_ITEM_ACTIONS: Final = frozenset({ActionType.REFUND, ActionType.RETURN, ActionType.REPLACEMENT})


def _rule(rule_id: str, title: str, *applies_to: ActionType) -> PolicyRule:
    return PolicyRule(rule_id=rule_id, title=title, applies_to=frozenset(applies_to))


POLICY_RULES: Final[Mapping[str, PolicyRule]] = MappingProxyType(
    {
        rule.rule_id: rule
        for rule in (
            _rule(ORDER_CANCELLED, "Cancelled orders are not eligible", *_ITEM_ACTIONS),
            _rule(ORDER_NOT_DELIVERED, "Items must be delivered first", *_ITEM_ACTIONS),
            _rule(ITEM_ALREADY_REFUNDED, "Fully refunded items are not eligible", *_ITEM_ACTIONS),
            _rule(
                SHIPPING_REFUND, "Shipping is refunded only for damaged items", ActionType.REFUND
            ),
            _rule(
                RETURN_WINDOW,
                f"Change-of-mind requests within {RETURN_WINDOW_DAYS} days of delivery",
                *_ITEM_ACTIONS,
            ),
            _rule(NON_RETURNABLE, "Non-returnable items are final sale", *_ITEM_ACTIONS),
            _rule(
                DAMAGE_WINDOW,
                f"Damage reported within {DAMAGE_REPORT_WINDOW_DAYS} days of delivery",
                *_ITEM_ACTIONS,
            ),
            _rule(
                DAMAGED_ITEM_EXCEPTION,
                "Damaged items are eligible even if non-returnable",
                *_ITEM_ACTIONS,
            ),
            _rule(
                CANCEL_BEFORE_SHIPMENT,
                "Orders may be cancelled before shipment",
                ActionType.CANCELLATION,
            ),
            _rule(
                CANCEL_AFTER_SHIPMENT,
                "Shipped or delivered orders cannot be cancelled",
                ActionType.CANCELLATION,
            ),
            _rule(
                ALREADY_CANCELLED,
                "Cancelled orders cannot be cancelled again",
                ActionType.CANCELLATION,
            ),
            _rule(
                REPLACEMENT_REQUIRES_DAMAGE,
                "Replacements are only for damaged items",
                ActionType.REPLACEMENT,
            ),
            _rule(
                REPLACEMENT_STOCK, "Replacements require available stock", ActionType.REPLACEMENT
            ),
        )
    }
)

_Outcome = tuple[str, PolicyReasonCode, bool]
"""(rule ID, reason code, passed)."""


def evaluate_policy(request: PolicyRequest) -> PolicyEvaluation:
    order = request.order
    if request.evaluated_at < order.placed_at:
        raise PolicyEvaluationError(f"Evaluation time precedes placement of {order.order_id}.")
    if request.action_type is ActionType.CANCELLATION:
        return _evaluate_cancellation(request)

    lines: list[OrderLineFacts] = []
    for item_id in request.item_ids:
        line = order.line(item_id)
        if line is None:
            raise PolicyEvaluationError(f"Item {item_id} is not part of order {order.order_id}.")
        lines.append(line)

    if order.status is OrderStatus.CANCELLED:
        return _order_level_ineligible(request, ORDER_CANCELLED, PolicyReasonCode.ORDER_CANCELLED)
    if order.delivered_at is None:
        return _order_level_ineligible(
            request, ORDER_NOT_DELIVERED, PolicyReasonCode.ORDER_NOT_DELIVERED
        )
    if request.evaluated_at < order.delivered_at:
        raise PolicyEvaluationError(f"Evaluation time precedes delivery of {order.order_id}.")

    delivered_on = order.delivered_at.astimezone(UTC).date()
    days_since_delivery = (request.evaluated_at.astimezone(UTC).date() - delivered_on).days
    window_days = (
        DAMAGE_REPORT_WINDOW_DAYS
        if request.reason is RequestReason.DAMAGED_ITEM
        else RETURN_WINDOW_DAYS
    )
    within_window = days_since_delivery <= window_days

    item_results = tuple(_evaluate_item(request, line, within_window) for line in lines)
    all_eligible = all(r.decision is PolicyDecision.ELIGIBLE for r in item_results)

    outcomes: list[_Outcome] = []
    shipping_refundable = False
    if request.action_type is ActionType.REFUND:
        shipping_refundable = all_eligible and request.reason is RequestReason.DAMAGED_ITEM
        code = (
            PolicyReasonCode.SHIPPING_REFUNDABLE
            if shipping_refundable
            else PolicyReasonCode.SHIPPING_NOT_REFUNDABLE
        )
        outcomes.append((SHIPPING_REFUND, code, True))

    rule_ids, codes = _merge(item_results, outcomes)
    return PolicyEvaluation(
        order_id=order.order_id,
        action_type=request.action_type,
        reason=request.reason,
        decision=PolicyDecision.ELIGIBLE if all_eligible else PolicyDecision.INELIGIBLE,
        item_results=item_results,
        triggered_rule_ids=rule_ids,
        reason_codes=codes,
        shipping_refundable=shipping_refundable,
        delivered_on=delivered_on,
        days_since_delivery=days_since_delivery,
        window_days=window_days,
        window_ends_on=delivered_on + timedelta(days=window_days),
        ruleset_version=RULESET_VERSION,
        evaluated_at=request.evaluated_at,
    )


def _evaluate_item(
    request: PolicyRequest, line: OrderLineFacts, within_window: bool
) -> ItemPolicyResult:
    outcomes: list[_Outcome] = []
    damaged = request.reason is RequestReason.DAMAGED_ITEM

    if line.remaining_quantity == 0:
        outcomes.append((ITEM_ALREADY_REFUNDED, PolicyReasonCode.ITEM_ALREADY_REFUNDED, False))
    elif request.action_type is ActionType.REPLACEMENT and not damaged:
        outcomes.append(
            (REPLACEMENT_REQUIRES_DAMAGE, PolicyReasonCode.REPLACEMENT_REQUIRES_DAMAGE, False)
        )
    else:
        if damaged:
            if within_window:
                outcomes.append((DAMAGE_WINDOW, PolicyReasonCode.WITHIN_DAMAGE_WINDOW, True))
                code = (
                    PolicyReasonCode.DAMAGED_ITEM_ELIGIBLE
                    if line.returnable
                    else PolicyReasonCode.DAMAGED_ITEM_EXCEPTION
                )
                outcomes.append((DAMAGED_ITEM_EXCEPTION, code, True))
            else:
                outcomes.append(
                    (DAMAGE_WINDOW, PolicyReasonCode.DAMAGE_REPORT_WINDOW_EXPIRED, False)
                )
        else:
            outcomes.append(
                (RETURN_WINDOW, PolicyReasonCode.WITHIN_RETURN_WINDOW, True)
                if within_window
                else (RETURN_WINDOW, PolicyReasonCode.RETURN_WINDOW_EXPIRED, False)
            )
            if not line.returnable:
                outcomes.append((NON_RETURNABLE, PolicyReasonCode.ITEM_NON_RETURNABLE, False))

        if request.action_type is ActionType.REPLACEMENT:
            in_stock = line.order_item_id in (request.in_stock_item_ids or frozenset())
            outcomes.append(
                (REPLACEMENT_STOCK, PolicyReasonCode.REPLACEMENT_IN_STOCK, True)
                if in_stock
                else (REPLACEMENT_STOCK, PolicyReasonCode.REPLACEMENT_OUT_OF_STOCK, False)
            )

    eligible = all(passed for _, _, passed in outcomes)
    return ItemPolicyResult(
        order_item_id=line.order_item_id,
        decision=PolicyDecision.ELIGIBLE if eligible else PolicyDecision.INELIGIBLE,
        triggered_rule_ids=_unique(rule_id for rule_id, _, _ in outcomes),
        reason_codes=_unique(code for _, code, _ in outcomes),
    )


def _evaluate_cancellation(request: PolicyRequest) -> PolicyEvaluation:
    status = request.order.status
    outcome: _Outcome
    if status is OrderStatus.PLACED:
        outcome = (CANCEL_BEFORE_SHIPMENT, PolicyReasonCode.CANCELLATION_BEFORE_SHIPMENT, True)
    elif status is OrderStatus.SHIPPED:
        outcome = (CANCEL_AFTER_SHIPMENT, PolicyReasonCode.ORDER_ALREADY_SHIPPED, False)
    elif status is OrderStatus.DELIVERED:
        outcome = (CANCEL_AFTER_SHIPMENT, PolicyReasonCode.ORDER_ALREADY_DELIVERED, False)
    else:
        outcome = (ALREADY_CANCELLED, PolicyReasonCode.ORDER_ALREADY_CANCELLED, False)
    return _order_level_result(request, outcome)


def _order_level_ineligible(
    request: PolicyRequest, rule_id: str, code: PolicyReasonCode
) -> PolicyEvaluation:
    return _order_level_result(request, (rule_id, code, False))


def _order_level_result(request: PolicyRequest, outcome: _Outcome) -> PolicyEvaluation:
    rule_id, code, passed = outcome
    decision = PolicyDecision.ELIGIBLE if passed else PolicyDecision.INELIGIBLE
    item_results = tuple(
        ItemPolicyResult(
            order_item_id=item_id,
            decision=decision,
            triggered_rule_ids=(rule_id,),
            reason_codes=(code,),
        )
        for item_id in request.item_ids
    )
    return PolicyEvaluation(
        order_id=request.order.order_id,
        action_type=request.action_type,
        reason=request.reason,
        decision=decision,
        item_results=item_results,
        triggered_rule_ids=(rule_id,),
        reason_codes=(code,),
        shipping_refundable=False,
        delivered_on=None,
        days_since_delivery=None,
        window_days=None,
        window_ends_on=None,
        ruleset_version=RULESET_VERSION,
        evaluated_at=request.evaluated_at,
    )


def _merge(
    item_results: tuple[ItemPolicyResult, ...], order_outcomes: list[_Outcome]
) -> tuple[tuple[str, ...], tuple[PolicyReasonCode, ...]]:
    rule_ids = [rule_id for r in item_results for rule_id in r.triggered_rule_ids]
    codes = [code for r in item_results for code in r.reason_codes]
    rule_ids += [rule_id for rule_id, _, _ in order_outcomes]
    codes += [code for _, code, _ in order_outcomes]
    return _unique(rule_ids), _unique(codes)


def _unique[T](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))
