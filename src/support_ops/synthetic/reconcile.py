"""Post-seed reconciliation against PostgreSQL.

Independent of the generator: aggregate SQL recomputes every money relationship and
lifecycle invariant directly from the persisted rows; canonical per-table fingerprints prove
the persisted rows equal the validated in-memory dataset; and every golden scenario is
re-evaluated by the real Phase 1 ``evaluate_policy``/``calculate_refund`` from facts loaded
from the database, then compared with the manifest.
"""

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import Connection, func, select, text

from support_ops.db.schema import Base
from support_ops.domain.errors import DomainError
from support_ops.domain.policy_engine import POLICY_RULES
from support_ops.synthetic.dataset_config import EMAIL_DOMAIN, SEEDED_TABLES
from support_ops.synthetic.facts import FactIndex
from support_ops.synthetic.manifest import ScenarioManifest, build_scenarios
from support_ops.synthetic.records import (
    CustomerRecord,
    InventoryRecord,
    OrderItemRecord,
    OrderRecord,
    PaymentRecord,
    ProductRecord,
    Record,
    RefundItemRecord,
    RefundRecord,
    ShipmentRecord,
    SyntheticDataset,
    SyntheticDatasetError,
    sort_records,
    table_fingerprint,
    table_fingerprints,
)


class ReconciliationError(RuntimeError):
    """Persisted synthetic data disagrees with the validated dataset or its invariants."""


_SQL_CHECKS: Final = (
    (
        "orders reference a missing customer",
        "SELECT count(*) FROM orders o LEFT JOIN customers c ON c.customer_id = o.customer_id "
        "WHERE c.customer_id IS NULL",
    ),
    (
        "order items reference a missing order or product",
        "SELECT count(*) FROM order_items i LEFT JOIN orders o ON o.order_id = i.order_id "
        "LEFT JOIN products p ON p.product_id = i.product_id "
        "WHERE o.order_id IS NULL OR p.product_id IS NULL",
    ),
    (
        "orders without items",
        "SELECT count(*) FROM orders o "
        "WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.order_id)",
    ),
    (
        "orders without exactly one payment",
        "SELECT count(*) FROM orders o "
        "WHERE (SELECT count(*) FROM payments p WHERE p.order_id = o.order_id) <> 1",
    ),
    (
        "shipments or payments reference a missing order",
        "SELECT (SELECT count(*) FROM shipments s LEFT JOIN orders o ON o.order_id = s.order_id "
        "WHERE o.order_id IS NULL) + (SELECT count(*) FROM payments p "
        "LEFT JOIN orders o ON o.order_id = p.order_id WHERE o.order_id IS NULL)",
    ),
    (
        "products and inventory are not one to one",
        "SELECT count(*) FROM products p FULL JOIN inventory v ON v.product_id = p.product_id "
        "WHERE p.product_id IS NULL OR v.product_id IS NULL",
    ),
    ("negative inventory", "SELECT count(*) FROM inventory WHERE available_quantity < 0"),
    (
        "payment amount differs from the recomputed order total",
        "SELECT count(*) FROM payments p JOIN orders o ON o.order_id = p.order_id "
        "JOIN (SELECT order_id, sum(unit_price * quantity - discount_amount + tax_amount) "
        "AS lines FROM order_items GROUP BY order_id) t ON t.order_id = o.order_id "
        "WHERE p.amount <> t.lines + o.shipping_amount",
    ),
    (
        "payment refunded balance differs from the refund ledger",
        "SELECT count(*) FROM payments p WHERE p.amount_refunded <> coalesce("
        "(SELECT sum(r.amount) FROM refunds r WHERE r.order_id = p.order_id), 0)",
    ),
    (
        "payment status contradicts its refunded balance",
        "SELECT count(*) FROM payments WHERE status <> CASE WHEN amount_refunded = 0 "
        "THEN 'captured' WHEN amount_refunded = amount THEN 'refunded' "
        "ELSE 'partially_refunded' END",
    ),
    (
        "order item refunded balances differ from the refund ledger",
        "SELECT count(*) FROM order_items i WHERE i.refunded_quantity <> coalesce("
        "(SELECT sum(ri.quantity) FROM refund_items ri "
        "WHERE ri.order_item_id = i.order_item_id), 0) "
        "OR i.refunded_amount <> coalesce((SELECT sum(ri.amount) FROM refund_items ri "
        "WHERE ri.order_item_id = i.order_item_id), 0)",
    ),
    (
        "order shipping refunded balance differs from the refund ledger",
        "SELECT count(*) FROM orders o WHERE o.shipping_refunded_amount <> coalesce("
        "(SELECT sum(r.shipping_amount) FROM refunds r WHERE r.order_id = o.order_id), 0)",
    ),
    (
        "refund amount differs from its item lines plus shipping",
        "SELECT count(*) FROM refunds r WHERE r.amount <> r.shipping_amount + coalesce("
        "(SELECT sum(ri.amount) FROM refund_items ri WHERE ri.refund_id = r.refund_id), 0)",
    ),
    (
        "refund lines cross orders",
        "SELECT count(*) FROM refund_items ri JOIN refunds r ON r.refund_id = ri.refund_id "
        "JOIN order_items i ON i.order_item_id = ri.order_item_id "
        "WHERE r.order_id <> ri.order_id OR i.order_id <> ri.order_id",
    ),
    (
        "refunds reference another order's payment",
        "SELECT count(*) FROM refunds r JOIN payments p ON p.payment_id = r.payment_id "
        "WHERE p.order_id <> r.order_id",
    ),
    (
        "order status contradicts its shipment",
        "SELECT count(*) FROM orders o LEFT JOIN shipments s ON s.order_id = o.order_id "
        "WHERE NOT ((o.status IN ('placed', 'cancelled') AND s.shipment_id IS NULL) "
        "OR (o.status = 'shipped' AND coalesce(s.status, '') = 'in_transit' "
        "AND s.delivered_at IS NULL) "
        "OR (o.status = 'delivered' AND coalesce(s.status, '') = 'delivered' "
        "AND s.delivered_at IS NOT NULL))",
    ),
    (
        "timestamps out of order or after the anchor",
        "SELECT count(*) FROM orders o LEFT JOIN shipments s ON s.order_id = o.order_id "
        "WHERE o.placed_at > :anchor OR coalesce(s.shipped_at < o.placed_at, false) "
        "OR coalesce(s.delivered_at < s.shipped_at, false) "
        "OR coalesce(s.delivered_at > :anchor, false)",
    ),
    (
        "payment not captured at placement",
        "SELECT count(*) FROM payments p JOIN orders o ON o.order_id = p.order_id "
        "WHERE p.captured_at <> o.placed_at",
    ),
    (
        "refund outside its order's timeline",
        "SELECT count(*) FROM refunds r JOIN orders o ON o.order_id = r.order_id "
        "LEFT JOIN shipments s ON s.order_id = r.order_id "
        "WHERE r.refunded_at > :anchor OR r.refunded_at < coalesce(s.delivered_at, o.placed_at)",
    ),
    (
        "cancelled orders not fully reversed",
        "SELECT count(*) FROM orders o JOIN payments p ON p.order_id = o.order_id "
        "WHERE o.status = 'cancelled' AND (p.amount_refunded <> p.amount "
        "OR o.shipping_refunded_amount <> o.shipping_amount)",
    ),
    (
        "non-USD currency",
        "SELECT (SELECT count(*) FROM orders WHERE currency <> 'USD') "
        "+ (SELECT count(*) FROM payments WHERE currency <> 'USD')",
    ),
    (
        f"customer emails outside {EMAIL_DOMAIN}",
        "SELECT count(*) FROM customers WHERE email NOT LIKE :email_pattern",
    ),
)


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    row_counts: dict[str, int]
    fingerprint: str
    scenarios_checked: int
    sql_checks: int


def persisted_row_counts(connection: Connection) -> dict[str, int]:
    return {
        name: int(
            connection.execute(
                select(func.count()).select_from(Base.metadata.tables[name])
            ).scalar_one()
        )
        for name in SEEDED_TABLES
    }


def _load[R: Record](connection: Connection, name: str, record_type: type[R]) -> tuple[R, ...]:
    table = Base.metadata.tables[name]
    columns = [field.name for field in fields(record_type)]
    rows = connection.execute(select(*(table.c[column] for column in columns))).mappings()
    records = []
    for row in rows:
        values: dict[str, Any] = {column: _normalize(row[column]) for column in columns}
        records.append(record_type(**values))
    return sort_records(name, records)


def _normalize(value: object) -> object:
    return value.astimezone(UTC) if isinstance(value, datetime) else value


def load_dataset(connection: Connection, expected: SyntheticDataset) -> SyntheticDataset:
    """Read every seeded table back into records, labelled like ``expected``."""
    return SyntheticDataset(
        dataset_version=expected.dataset_version,
        seed=expected.seed,
        anchor=expected.anchor,
        customers=_load(connection, "customers", CustomerRecord),
        products=_load(connection, "products", ProductRecord),
        inventory=_load(connection, "inventory", InventoryRecord),
        orders=_load(connection, "orders", OrderRecord),
        order_items=_load(connection, "order_items", OrderItemRecord),
        shipments=_load(connection, "shipments", ShipmentRecord),
        payments=_load(connection, "payments", PaymentRecord),
        refunds=_load(connection, "refunds", RefundRecord),
        refund_items=_load(connection, "refund_items", RefundItemRecord),
        golden_order_ids=expected.golden_order_ids,
    )


def persisted_fingerprints(connection: Connection, expected: SyntheticDataset) -> dict[str, str]:
    loaded = load_dataset(connection, expected)
    return {name: table_fingerprint(name, records) for name, records in loaded.tables().items()}


def reconcile(
    connection: Connection, dataset: SyntheticDataset, manifest: ScenarioManifest
) -> ReconciliationReport:
    """Raise ``ReconciliationError`` unless the persisted data matches exactly."""
    failures: list[str] = []

    counts = persisted_row_counts(connection)
    if counts != manifest.row_counts:
        failures.append(f"row counts {counts} != expected {manifest.row_counts}")

    parameters = {"anchor": dataset.anchor, "email_pattern": f"%@{EMAIL_DOMAIN}"}
    for description, sql in _SQL_CHECKS:
        violations = connection.execute(text(sql), parameters).scalar_one()
        if violations:
            failures.append(f"{description} ({violations} rows)")

    loaded = load_dataset(connection, dataset)
    persisted = {name: table_fingerprint(name, rows) for name, rows in loaded.tables().items()}
    expected = table_fingerprints(dataset)
    failures.extend(
        f"table {name} differs from the generated dataset"
        for name in SEEDED_TABLES
        if persisted[name] != expected[name]
    )

    try:
        rescored = build_scenarios(
            FactIndex.from_dataset(loaded),
            dataset_version=dataset.dataset_version,
            anchor=dataset.anchor,
        )
    except (KeyError, DomainError, ValidationError, SyntheticDatasetError) as exc:
        failures.append(f"golden scenarios could not be rebuilt from the database ({exc!r})")
        rescored = ()
    if len(rescored) != len(manifest.scenarios):
        failures.append("golden scenario count differs from the manifest")
    for actual, wanted in zip(rescored, manifest.scenarios, strict=False):
        if actual != wanted:
            failures.append(f"golden scenario {wanted.scenario_id} differs from the manifest")
    for scenario in manifest.scenarios:
        unknown = set(scenario.expected_policy["triggered_rule_ids"]) - set(POLICY_RULES)
        if unknown:
            failures.append(f"{scenario.scenario_id} uses unknown rule IDs {sorted(unknown)}")

    if failures:
        raise ReconciliationError("Synthetic data reconciliation failed: " + "; ".join(failures))
    return ReconciliationReport(
        row_counts=counts,
        fingerprint=manifest.dataset_fingerprint,
        scenarios_checked=len(rescored),
        sql_checks=len(_SQL_CHECKS),
    )
