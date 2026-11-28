"""Database-layer invariants and the append-only refund ledger.

Every test works inside a transaction that is always rolled back, so the shared test database
is left unchanged. Expected failures run inside savepoints and are matched by SQLSTATE and
constraint name.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, Executable, insert, text
from sqlalchemy.exc import IntegrityError

from support_ops.db.schema import (
    Action,
    ActionTriggeredRule,
    Approval,
    Case,
    Customer,
    Order,
    OrderItem,
    Payment,
    Product,
    Refund,
    RefundItem,
    Shipment,
)
from support_ops.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    AuthorizationDecision,
    CaseStatus,
    Currency,
    OrderStatus,
    PaymentStatus,
    RequestReason,
    ShipmentStatus,
)

CHECK, FOREIGN_KEY, UNIQUE, INTEGRITY = "23514", "23503", "23505", "23000"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
KEY_1, KEY_2 = "a" * 64, "b" * 64


def _order(order_id: str, customer_id: str) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "customer_id": customer_id,
        "status": OrderStatus.DELIVERED,
        "currency": Currency.USD,
        "shipping_amount": Decimal("5.00"),
        "placed_at": NOW,
    }


def _item(item_id: str, order_id: str, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_item_id": item_id,
        "order_id": order_id,
        "product_id": "SKU-ITEST-1",
        "quantity": 2,
        "unit_price": Decimal("50.00"),
        "discount_amount": Decimal("0.00"),
        "tax_amount": Decimal("4.00"),
    }
    values.update(overrides)
    return values


def _action(action_id: str, key: str, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "action_id": action_id,
        "case_id": "CASE-ITEST-1",
        "order_id": "ORD-ITEST-1",
        "action_type": ActionType.REFUND,
        "status": ActionStatus.PROPOSED,
        "reason": RequestReason.DAMAGED_ITEM,
        "amount": Decimal("52.00"),
        "idempotency_key": key,
    }
    values.update(overrides)
    return values


def _refund(refund_id: str, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "refund_id": refund_id,
        "order_id": "ORD-ITEST-1",
        "payment_id": "PAY-ITEST-1",
        "amount": Decimal("52.00"),
        "refunded_at": NOW,
    }
    values.update(overrides)
    return values


@pytest.fixture
def db(test_engine: Engine) -> Iterator[Connection]:
    """A transaction that is always rolled back, seeded with two customers' orders."""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            _seed(connection)
            yield connection
        finally:
            transaction.rollback()


def _seed(connection: Connection) -> None:
    connection.execute(
        insert(Customer),
        [
            {"customer_id": "CUST-ITEST-1", "email": "one@itest.invalid", "full_name": "One"},
            {"customer_id": "CUST-ITEST-2", "email": "two@itest.invalid", "full_name": "Two"},
        ],
    )
    connection.execute(
        insert(Product).values(
            product_id="SKU-ITEST-1", name="Kettle", category="kitchen", is_returnable=True
        )
    )
    connection.execute(
        insert(Order),
        [_order("ORD-ITEST-1", "CUST-ITEST-1"), _order("ORD-ITEST-2", "CUST-ITEST-2")],
    )
    connection.execute(
        insert(OrderItem),
        [_item("ITEM-ITEST-1", "ORD-ITEST-1"), _item("ITEM-ITEST-2", "ORD-ITEST-2")],
    )
    connection.execute(
        insert(Payment),
        [
            {
                "payment_id": f"PAY-ITEST-{n}",
                "order_id": f"ORD-ITEST-{n}",
                "currency": Currency.USD,
                "amount": Decimal("109.00"),
                "status": PaymentStatus.CAPTURED,
                "captured_at": NOW,
            }
            for n in (1, 2)
        ],
    )
    connection.execute(
        insert(Case).values(
            case_id="CASE-ITEST-1",
            customer_id="CUST-ITEST-1",
            order_id="ORD-ITEST-1",
            status=CaseStatus.OPEN,
        )
    )
    connection.execute(insert(Action).values(**_action("ACT-ITEST-1", KEY_1)))


def assert_rejected(
    db: Connection, statement: Executable | str, sqlstate: str, constraint: str | None = None
) -> Any:
    """Assert the statement fails in a savepoint with the given SQLSTATE/constraint."""
    with pytest.raises(IntegrityError) as exc_info, db.begin_nested():
        db.execute(text(statement) if isinstance(statement, str) else statement)
    error: Any = exc_info.value.orig
    assert error.sqlstate == sqlstate
    if constraint is not None:
        assert error.diag.constraint_name == constraint
    return error


# --- Money and quantity bounds --------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"tax_amount": Decimal("-0.01")}, "ck_order_items_tax_amount_non_negative"),
        ({"discount_amount": Decimal("100.01")}, "ck_order_items_discount_amount_within_gross"),
        ({"refunded_quantity": 3}, "ck_order_items_refunded_quantity_within_quantity"),
        (
            {"refunded_amount": Decimal("104.01")},
            "ck_order_items_refunded_amount_within_paid_total",
        ),
        ({"quantity": 0}, "ck_order_items_quantity_positive"),
    ],
)
def test_order_item_bounds(db: Connection, overrides: dict[str, Any], constraint: str) -> None:
    statement = insert(OrderItem).values(**_item("ITEM-ITEST-9", "ORD-ITEST-1", **overrides))
    assert_rejected(db, statement, CHECK, constraint)


def test_line_refund_balance_may_reach_but_not_exceed_the_paid_total(db: Connection) -> None:
    db.execute(
        text("UPDATE order_items SET refunded_quantity = 2, refunded_amount = 104.00 "
             "WHERE order_item_id = 'ITEM-ITEST-1'")
    )  # fmt: skip
    assert_rejected(
        db,
        "UPDATE order_items SET refunded_amount = 104.01 WHERE order_item_id = 'ITEM-ITEST-1'",
        CHECK,
        "ck_order_items_refunded_amount_within_paid_total",
    )


def test_payment_cannot_be_over_refunded(db: Connection) -> None:
    assert_rejected(
        db,
        "UPDATE payments SET amount_refunded = 109.01, status = 'refunded' "
        "WHERE payment_id = 'PAY-ITEST-1'",
        CHECK,
        "ck_payments_amount_refunded_within_amount",
    )


@pytest.mark.parametrize(
    ("status", "refunded"),
    [("refunded", "0"), ("captured", "10.00"), ("partially_refunded", "109.00")],
)
def test_payment_status_must_match_refunded_balance(
    db: Connection, status: str, refunded: str
) -> None:
    assert_rejected(
        db,
        f"UPDATE payments SET status = '{status}', amount_refunded = {refunded} "  # noqa: S608
        "WHERE payment_id = 'PAY-ITEST-1'",
        CHECK,
        "ck_payments_status_matches_amount_refunded",
    )


def test_shipping_refund_balance_is_bounded(db: Connection) -> None:
    assert_rejected(
        db,
        "UPDATE orders SET shipping_refunded_amount = 5.01 WHERE order_id = 'ORD-ITEST-1'",
        CHECK,
        "ck_orders_shipping_refunded_amount_within_shipping",
    )


# --- Controlled vocabulary and identifiers ---------------------------------------------


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "UPDATE orders SET status = 'lost' WHERE order_id = 'ORD-ITEST-1'",
            "ck_orders_order_status",
        ),
        ("UPDATE orders SET currency = 'EUR' WHERE order_id = 'ORD-ITEST-1'", "ck_orders_currency"),
        (
            "UPDATE actions SET status = 'PROPOSED' WHERE action_id = 'ACT-ITEST-1'",
            "ck_actions_action_status",
        ),
        (
            "UPDATE cases SET status = 'pending' WHERE case_id = 'CASE-ITEST-1'",
            "ck_cases_case_status",
        ),
        (
            "INSERT INTO customers (customer_id, email, full_name) VALUES ('cust-3', 'x@y', 'X')",
            "ck_customers_customer_id_format",
        ),
        (
            "INSERT INTO action_triggered_rules (action_id, rule_id) "
            "VALUES ('ACT-ITEST-1', 'dmg-4')",
            "ck_action_triggered_rules_rule_id_format",
        ),
    ],
)
def test_invalid_vocabulary_and_identifiers_are_rejected(
    db: Connection, statement: str, constraint: str
) -> None:
    assert_rejected(db, statement, CHECK, constraint)


# --- Cross-order and ownership protection ----------------------------------------------


def test_case_cannot_reference_another_customers_order(db: Connection) -> None:
    statement = insert(Case).values(
        case_id="CASE-ITEST-2",
        customer_id="CUST-ITEST-1",
        order_id="ORD-ITEST-2",
        status=CaseStatus.OPEN,
    )
    assert_rejected(db, statement, FOREIGN_KEY, "fk_cases_order_id_customer_id_orders")


def test_refund_cannot_use_another_orders_payment(db: Connection) -> None:
    statement = insert(Refund).values(**_refund("REF-ITEST-9", payment_id="PAY-ITEST-2"))
    assert_rejected(db, statement, FOREIGN_KEY, "fk_refunds_payment_id_order_id_payments")


def test_refund_item_cannot_belong_to_another_order(db: Connection) -> None:
    db.execute(insert(Refund).values(**_refund("REF-ITEST-1")))
    statement = insert(RefundItem).values(
        refund_id="REF-ITEST-1",
        order_id="ORD-ITEST-1",
        order_item_id="ITEM-ITEST-2",
        quantity=1,
        amount=Decimal("10.00"),
    )
    assert_rejected(
        db, statement, FOREIGN_KEY, "fk_refund_items_order_id_order_item_id_order_items"
    )


def test_refund_action_must_belong_to_the_same_order(db: Connection) -> None:
    statement = insert(Refund).values(
        **_refund("REF-ITEST-9", order_id="ORD-ITEST-2", payment_id="PAY-ITEST-2",
                  action_id="ACT-ITEST-1")
    )  # fmt: skip
    assert_rejected(db, statement, FOREIGN_KEY, "fk_refunds_action_id_order_id_actions")


# --- Actions, idempotency, approvals -----------------------------------------------------


def test_action_idempotency_key_is_globally_unique(db: Connection) -> None:
    statement = insert(Action).values(**_action("ACT-ITEST-2", KEY_1))
    assert_rejected(db, statement, UNIQUE, "uq_actions_idempotency_key")


def _timestamps_for(status: ActionStatus) -> dict[str, Any]:
    """Execution/completion timestamps that satisfy their own CHECKs for ``status``."""
    values: dict[str, Any] = {}
    if status in {
        ActionStatus.EXECUTING,
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED,
        ActionStatus.UNKNOWN,
    }:
        values["execution_started_at"] = NOW
    if status in {ActionStatus.SUCCEEDED, ActionStatus.FAILED}:
        values["completed_at"] = NOW
    return values


DECISION_REQUIRED_STATUSES = [
    status for status in ActionStatus if status is not ActionStatus.PROPOSED
]


@pytest.mark.parametrize("status", DECISION_REQUIRED_STATUSES, ids=lambda s: s.value)
def test_decision_bearing_statuses_reject_a_null_decision(
    db: Connection, status: ActionStatus
) -> None:
    """NULL must make the CHECK FALSE, not UNKNOWN (which PostgreSQL would accept)."""
    statement = insert(Action).values(
        **_action("ACT-ITEST-2", KEY_2, status=status, **_timestamps_for(status))
    )
    assert_rejected(db, statement, CHECK, "ck_actions_status_matches_authorization_decision")


VALID_STATUS_DECISIONS = [
    (ActionStatus.PROPOSED, None),
    (ActionStatus.DENIED, AuthorizationDecision.DENY),
    (ActionStatus.AWAITING_APPROVAL, AuthorizationDecision.REQUIRES_APPROVAL),
    (ActionStatus.REJECTED, AuthorizationDecision.REQUIRES_APPROVAL),
    *(
        (status, decision)
        for status in (
            ActionStatus.AUTHORIZED,
            ActionStatus.EXECUTING,
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED,
            ActionStatus.UNKNOWN,
        )
        for decision in (AuthorizationDecision.ALLOW, AuthorizationDecision.REQUIRES_APPROVAL)
    ),
]


@pytest.mark.parametrize(
    ("status", "decision"),
    VALID_STATUS_DECISIONS,
    ids=lambda v: v.value if v is not None else "null",
)
def test_valid_status_and_decision_combinations_are_accepted(
    db: Connection, status: ActionStatus, decision: AuthorizationDecision | None
) -> None:
    db.execute(
        insert(Action).values(
            **_action(
                "ACT-ITEST-2",
                KEY_2,
                status=status,
                authorization_decision=decision,
                decided_at=None if decision is None else NOW,
                **_timestamps_for(status),
            )
        )
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        (
            {
                "status": ActionStatus.DENIED,
                "authorization_decision": AuthorizationDecision.ALLOW,
                "decided_at": NOW,
            },
            "ck_actions_status_matches_authorization_decision",
        ),
        (
            {
                "status": ActionStatus.AWAITING_APPROVAL,
                "authorization_decision": AuthorizationDecision.ALLOW,
                "decided_at": NOW,
            },
            "ck_actions_status_matches_authorization_decision",
        ),
        (
            {
                "status": ActionStatus.PROPOSED,
                "authorization_decision": AuthorizationDecision.ALLOW,
                "decided_at": NOW,
            },
            "ck_actions_status_matches_authorization_decision",
        ),
        (
            {
                "authorization_decision": AuthorizationDecision.ALLOW,
                "status": ActionStatus.AUTHORIZED,
            },
            "ck_actions_decided_at_matches_decision",
        ),
        ({"amount": None}, "ck_actions_refund_requires_positive_amount"),
        ({"idempotency_key": "not-a-sha"}, "ck_actions_idempotency_key_format"),
        (
            {
                "status": ActionStatus.SUCCEEDED,
                "authorization_decision": AuthorizationDecision.ALLOW,
                "decided_at": NOW,
                "execution_started_at": NOW,
            },
            "ck_actions_completed_at_matches_status",
        ),
    ],
)
def test_action_lifecycle_fields_are_consistent(
    db: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    statement = insert(Action).values(**_action("ACT-ITEST-2", KEY_2, **overrides))
    assert_rejected(db, statement, CHECK, constraint)


def test_action_triggered_rules_are_unique_per_action(db: Connection) -> None:
    db.execute(insert(ActionTriggeredRule).values(action_id="ACT-ITEST-1", rule_id="DMG-4.2"))
    statement = insert(ActionTriggeredRule).values(action_id="ACT-ITEST-1", rule_id="DMG-4.2")
    assert_rejected(db, statement, UNIQUE, "pk_action_triggered_rules")


def _approval(approval_id: str, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "approval_id": approval_id,
        "action_id": "ACT-ITEST-1",
        "status": ApprovalStatus.PENDING,
        "requested_at": NOW,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": ApprovalStatus.APPROVED, "decided_by": "SUPERVISOR-1"},
         "ck_approvals_decided_at_matches_status"),
        ({"status": ApprovalStatus.REJECTED, "decided_by": "SUPERVISOR-1", "decided_at": NOW},
         "ck_approvals_rejection_requires_reason"),
        ({"decided_at": NOW}, "ck_approvals_decided_at_matches_status"),
    ],
)  # fmt: skip
def test_approval_decisions_are_consistent(
    db: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    assert_rejected(db, insert(Approval).values(**_approval("APR-ITEST-1", **overrides)), CHECK,
                    constraint)  # fmt: skip


def test_only_one_approval_per_action(db: Connection) -> None:
    db.execute(insert(Approval).values(**_approval("APR-ITEST-1")))
    statement = insert(Approval).values(**_approval("APR-ITEST-2"))
    assert_rejected(db, statement, UNIQUE, "uq_approvals_action_id")


def test_delivered_shipment_requires_delivery_time(db: Connection) -> None:
    statement = insert(Shipment).values(
        shipment_id="SHP-ITEST-1",
        order_id="ORD-ITEST-1",
        carrier="ACME",
        tracking_number="TRK-ITEST-1",
        status=ShipmentStatus.DELIVERED,
        shipped_at=NOW,
    )
    assert_rejected(db, statement, CHECK, "ck_shipments_delivered_status_matches_delivered_at")


# --- Append-only refund ledger ---------------------------------------------------------


@pytest.fixture
def ledger(db: Connection) -> Connection:
    db.execute(insert(Refund).values(**_refund("REF-ITEST-1", amount=Decimal("52.37"))))
    db.execute(
        insert(RefundItem).values(
            refund_id="REF-ITEST-1",
            order_id="ORD-ITEST-1",
            order_item_id="ITEM-ITEST-1",
            quantity=1,
            amount=Decimal("52.37"),
        )
    )
    return db


@pytest.mark.parametrize(
    ("statement", "operation"),
    [
        ("UPDATE refunds SET amount = 1.00 WHERE refund_id = 'REF-ITEST-1'", "UPDATE"),
        ("DELETE FROM refunds WHERE refund_id = 'REF-ITEST-1'", "DELETE"),
        ("UPDATE refund_items SET quantity = 2 WHERE refund_id = 'REF-ITEST-1'", "UPDATE"),
        ("DELETE FROM refund_items WHERE refund_id = 'REF-ITEST-1'", "DELETE"),
    ],
)
def test_refund_ledger_rejects_update_and_delete(
    ledger: Connection, statement: str, operation: str
) -> None:
    error = assert_rejected(ledger, statement, INTEGRITY)

    table = statement.split()[1] if operation == "UPDATE" else statement.split()[2]
    assert error.diag.message_primary == (
        f"refund ledger table {table} is append-only; {operation} is not permitted"
    )
    diagnostics = " ".join(
        str(part)
        for part in (
            error.diag.message_primary,
            error.diag.message_detail,
            error.diag.message_hint,
            error.diag.context,
        )
        if part
    )
    for sensitive in ("REF-ITEST-1", "ITEM-ITEST-1", "ORD-ITEST-1", "52.37"):
        assert sensitive not in diagnostics


def test_refund_ledger_accepts_appended_compensating_rows(ledger: Connection) -> None:
    ledger.execute(insert(Refund).values(**_refund("REF-ITEST-2", amount=Decimal("0.63"))))

    total = ledger.execute(
        text("SELECT sum(amount) FROM refunds WHERE order_id = 'ORD-ITEST-1'")
    ).scalar_one()
    assert total == Decimal("53.00")


def test_controlled_truncate_bypasses_the_row_triggers(
    ledger: Connection, destructive_reset_allowed: None
) -> None:
    with ledger.begin_nested():
        ledger.execute(text("TRUNCATE refund_items, refunds"))
        assert ledger.execute(text("SELECT count(*) FROM refunds")).scalar_one() == 0
