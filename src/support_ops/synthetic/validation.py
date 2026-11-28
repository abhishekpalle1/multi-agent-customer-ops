"""In-memory validation of a synthetic dataset (no database).

Checks are independent of the generator: totals are recomputed from Phase 1
``OrderLineFacts`` and from the ledger rows, not read back from the values the generator
maintained. Every problem is collected and reported together.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from pydantic import ValidationError

from support_ops.domain.enums import Currency, OrderStatus, PaymentStatus, ShipmentStatus
from support_ops.domain.errors import DomainError
from support_ops.domain.money import ZERO
from support_ops.synthetic.dataset_config import EMAIL_DOMAIN, EXPECTED_ROW_COUNTS
from support_ops.synthetic.facts import FactIndex
from support_ops.synthetic.records import (
    PRIMARY_KEYS,
    SyntheticDataset,
    SyntheticDatasetError,
    primary_key,
    record_values,
)

_BACKGROUND_ID: Final = re.compile(r"^(CUST-1[0-9]{3}|ORD-1[0-9]{5}|SKU-1[0-9]{4})$")
"""Background IDs are fixed-width numbers; golden IDs are words or the two-digit
``CUST-10``/``CUST-20``."""
_MAX_REPORTED: Final = 25


def validate_dataset(dataset: SyntheticDataset) -> None:
    """Raise ``SyntheticDatasetError`` listing every invariant the dataset violates."""
    problems: list[str] = []
    _check_counts_and_keys(dataset, problems)
    _check_values(dataset, problems)
    _check_id_namespaces(dataset, problems)
    _check_references(dataset, problems)
    _check_orders(dataset, problems)
    _check_ledger(dataset, problems)
    if problems:
        shown = "\n- ".join(problems[:_MAX_REPORTED])
        more = len(problems) - _MAX_REPORTED
        suffix = f"\n(and {more} more)" if more > 0 else ""
        raise SyntheticDatasetError(f"Synthetic dataset failed validation:\n- {shown}{suffix}")


def _check_counts_and_keys(dataset: SyntheticDataset, problems: list[str]) -> None:
    if dataset.row_counts() != dict(EXPECTED_ROW_COUNTS):
        problems.append(f"Row counts {dataset.row_counts()} != {dict(EXPECTED_ROW_COUNTS)}.")
    for table, records in dataset.tables().items():
        keys = [primary_key(table, record) for record in records]
        if len(keys) != len(set(keys)):
            problems.append(f"Duplicate {PRIMARY_KEYS[table]} in {table}.")
        if keys != sorted(keys):
            problems.append(f"{table} is not sorted by primary key.")
    _require_unique((c.email for c in dataset.customers), "customer email", problems)
    _require_unique((s.tracking_number for s in dataset.shipments), "tracking number", problems)
    for customer in dataset.customers:
        if not customer.email.endswith(f"@{EMAIL_DOMAIN}"):
            problems.append(f"{customer.customer_id} email is not on {EMAIL_DOMAIN}.")


def _require_unique(values: Iterable[str], what: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            problems.append(f"Duplicate {what}: {value}.")
        seen.add(value)


def _check_values(dataset: SyntheticDataset, problems: list[str]) -> None:
    for table, records in dataset.tables().items():
        for record in records:
            for column, value in record_values(record).items():
                if isinstance(value, float):
                    problems.append(f"{table}.{column} is a float.")
                elif isinstance(value, Decimal):
                    exponent = value.as_tuple().exponent
                    if not value.is_finite() or exponent != -2 or value < 0:
                        problems.append(f"{table}.{column} {value} is not non-negative cents.")
                elif isinstance(value, datetime):
                    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                        problems.append(f"{table}.{column} is not a UTC timestamp.")
                    elif value > dataset.anchor:
                        problems.append(f"{table}.{column} is after the scenario anchor.")
    for order in dataset.orders:
        if order.currency is not Currency.USD:
            problems.append(f"{order.order_id} is not USD.")
    for payment in dataset.payments:
        if payment.currency is not Currency.USD:
            problems.append(f"{payment.payment_id} is not USD.")
    for row in dataset.inventory:
        if row.available_quantity < 0:
            problems.append(f"Inventory for {row.product_id} is negative.")


def _check_id_namespaces(dataset: SyntheticDataset, problems: list[str]) -> None:
    golden_orders = dataset.golden_order_ids
    golden_customers = {o.customer_id for o in dataset.orders if o.order_id in golden_orders}
    golden_products = {i.product_id for i in dataset.order_items if i.order_id in golden_orders} | {
        p.product_id for p in dataset.products if p.product_id.startswith("SKU-GOLDEN-")
    }
    for ids, golden in (
        ([c.customer_id for c in dataset.customers], golden_customers),
        ([o.order_id for o in dataset.orders], golden_orders),
        ([p.product_id for p in dataset.products], golden_products),
    ):
        for entity_id in ids:
            background = bool(_BACKGROUND_ID.fullmatch(entity_id))
            if background == (entity_id in golden):
                problems.append(f"{entity_id} is in the wrong golden/background namespace.")
    for order in dataset.orders:
        if order.order_id not in golden_orders and order.customer_id in golden_customers:
            problems.append(f"Background order {order.order_id} uses a golden customer.")
    for item in dataset.order_items:
        if item.order_id not in golden_orders and item.product_id in golden_products:
            problems.append(f"Background item {item.order_item_id} uses a golden product.")


def _check_references(dataset: SyntheticDataset, problems: list[str]) -> None:
    customers = {c.customer_id for c in dataset.customers}
    products = {p.product_id for p in dataset.products}
    orders = {o.order_id for o in dataset.orders}
    item_orders = {i.order_item_id: i.order_id for i in dataset.order_items}
    payment_orders = {p.payment_id: p.order_id for p in dataset.payments}
    refund_orders = {r.refund_id: r.order_id for r in dataset.refunds}

    if {row.product_id for row in dataset.inventory} != products:
        problems.append("Inventory rows do not match the product catalog one to one.")
    for order in dataset.orders:
        if order.customer_id not in customers:
            problems.append(f"{order.order_id} references a missing customer.")
    for item in dataset.order_items:
        if item.order_id not in orders or item.product_id not in products:
            problems.append(f"{item.order_item_id} references a missing order or product.")
    for shipment in dataset.shipments:
        if shipment.order_id not in orders:
            problems.append(f"{shipment.shipment_id} references a missing order.")
    for payment in dataset.payments:
        if payment.order_id not in orders:
            problems.append(f"{payment.payment_id} references a missing order.")
    for refund in dataset.refunds:
        if payment_orders.get(refund.payment_id) != refund.order_id:
            problems.append(f"{refund.refund_id} uses another order's payment.")
    for line in dataset.refund_items:
        if refund_orders.get(line.refund_id) != line.order_id:
            problems.append(f"{line.refund_id}/{line.order_item_id} crosses orders (refund).")
        if item_orders.get(line.order_item_id) != line.order_id:
            problems.append(f"{line.refund_id}/{line.order_item_id} crosses orders (item).")


def _check_orders(dataset: SyntheticDataset, problems: list[str]) -> None:
    index = FactIndex.from_dataset(dataset)
    payments: defaultdict[str, list[str]] = defaultdict(list)
    for payment in dataset.payments:
        payments[payment.order_id].append(payment.payment_id)

    expected_shipment = {
        OrderStatus.PLACED: None,
        OrderStatus.CANCELLED: None,
        OrderStatus.SHIPPED: ShipmentStatus.IN_TRANSIT,
        OrderStatus.DELIVERED: ShipmentStatus.DELIVERED,
    }
    payment_by_order = {p.order_id: p for p in dataset.payments}
    for order in dataset.orders:
        oid = order.order_id
        if oid not in index.items_by_order:
            problems.append(f"{oid} has no items.")
            continue
        try:
            facts = index.facts(oid)
        except (DomainError, ValidationError) as exc:
            problems.append(f"{oid} violates Phase 1 trusted-fact invariants ({exc}).")
            continue
        if len(payments[oid]) != 1:
            problems.append(f"{oid} has {len(payments[oid])} payments.")
            continue
        payment = payment_by_order[oid]
        expected_amount = (
            sum((line.paid_total for line in facts.lines), start=ZERO) + facts.shipping_amount
        )
        if payment.amount != expected_amount:
            problems.append(f"{oid} payment {payment.amount} != order total {expected_amount}.")
        if payment.captured_at != order.placed_at:
            problems.append(f"{oid} payment was not captured at placement.")
        shipment = index.shipments_by_order.get(oid)
        actual = shipment.status if shipment is not None else None
        if actual is not expected_shipment[order.status]:
            problems.append(f"{oid} status {order.status} contradicts its shipment.")
        if order.status is OrderStatus.CANCELLED and (
            payment.amount_refunded != payment.amount
            or order.shipping_refunded_amount != order.shipping_amount
        ):
            problems.append(f"Cancelled order {oid} is not fully reversed.")


def _check_ledger(dataset: SyntheticDataset, problems: list[str]) -> None:
    refunded_by_order: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
    shipping_by_order: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
    items_by_refund: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
    quantity_by_item: defaultdict[str, int] = defaultdict(int)
    amount_by_item: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
    orders = {o.order_id: o for o in dataset.orders}
    shipments = {s.order_id: s for s in dataset.shipments}

    for refund in dataset.refunds:
        refunded_by_order[refund.order_id] += refund.amount
        shipping_by_order[refund.order_id] += refund.shipping_amount
        order = orders[refund.order_id]
        shipment = shipments.get(refund.order_id)
        earliest = (
            shipment.delivered_at
            if shipment is not None and shipment.delivered_at is not None
            else order.placed_at
        )
        if not earliest <= refund.refunded_at <= dataset.anchor:
            problems.append(f"{refund.refund_id} is outside its order's timeline.")
        if refund.action_id is not None:
            problems.append(f"{refund.refund_id} must be a historical refund (no action).")
    for line in dataset.refund_items:
        items_by_refund[line.refund_id] += line.amount
        quantity_by_item[line.order_item_id] += line.quantity
        amount_by_item[line.order_item_id] += line.amount

    for refund in dataset.refunds:
        if refund.amount != items_by_refund[refund.refund_id] + refund.shipping_amount:
            problems.append(f"{refund.refund_id} amount != its item lines + shipping.")
    for payment in dataset.payments:
        if payment.amount_refunded != refunded_by_order[payment.order_id]:
            problems.append(f"{payment.payment_id} refunded balance != ledger.")
        expected_status = (
            PaymentStatus.CAPTURED
            if payment.amount_refunded == 0
            else PaymentStatus.REFUNDED
            if payment.amount_refunded == payment.amount
            else PaymentStatus.PARTIALLY_REFUNDED
        )
        if payment.status is not expected_status:
            problems.append(f"{payment.payment_id} status contradicts its refunded balance.")
    for item in dataset.order_items:
        if (
            item.refunded_quantity != quantity_by_item[item.order_item_id]
            or item.refunded_amount != amount_by_item[item.order_item_id]
        ):
            problems.append(f"{item.order_item_id} refunded balances != ledger.")
    for order in dataset.orders:
        if order.shipping_refunded_amount != shipping_by_order[order.order_id]:
            problems.append(f"{order.order_id} shipping refunded balance != ledger.")
