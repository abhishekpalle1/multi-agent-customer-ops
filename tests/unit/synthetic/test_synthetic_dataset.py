"""Determinism and invariants of the generated synthetic dataset (no database)."""

import dataclasses
import functools
import json
import re
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from support_ops.domain.enums import OrderStatus, PaymentStatus
from support_ops.synthetic.dataset import PreparedDataset, build_dataset
from support_ops.synthetic.dataset_config import (
    DATASET_VERSION,
    DEFAULT_RANDOM_SEED,
    EXPECTED_ROW_COUNTS,
    MANIFEST_PATH,
    SCENARIO_ANCHOR,
)
from support_ops.synthetic.economics import line_paid_total
from support_ops.synthetic.manifest import build_manifest
from support_ops.synthetic.records import (
    SyntheticDataset,
    SyntheticDatasetError,
    dataset_fingerprint,
    record_values,
    table_fingerprint,
)
from support_ops.synthetic.validation import validate_dataset

_SUPPRESSED = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


def _golden_fingerprints(dataset: SyntheticDataset) -> dict[str, str]:
    golden = dataset.golden_order_ids
    customers = {o.customer_id for o in dataset.orders if o.order_id in golden}
    products = {i.product_id for i in dataset.order_items if i.order_id in golden}
    rows = {
        "customers": [c for c in dataset.customers if c.customer_id in customers],
        "products": [p for p in dataset.products if p.product_id in products],
        "inventory": [v for v in dataset.inventory if v.product_id in products],
        "orders": [o for o in dataset.orders if o.order_id in golden],
        "order_items": [i for i in dataset.order_items if i.order_id in golden],
        "shipments": [s for s in dataset.shipments if s.order_id in golden],
        "payments": [p for p in dataset.payments if p.order_id in golden],
        "refunds": [r for r in dataset.refunds if r.order_id in golden],
        "refund_items": [r for r in dataset.refund_items if r.order_id in golden],
    }
    return {name: table_fingerprint(name, records) for name, records in rows.items()}


def test_identity_labels(prepared: PreparedDataset) -> None:
    dataset = prepared.dataset
    assert (dataset.dataset_version, dataset.seed, dataset.anchor) == (
        DATASET_VERSION,
        DEFAULT_RANDOM_SEED,
        SCENARIO_ANCHOR,
    )
    assert SCENARIO_ANCHOR.isoformat() == "2026-11-01T12:00:00+00:00"


def test_same_seed_reproduces_the_dataset_exactly(prepared: PreparedDataset) -> None:
    again = build_dataset(DEFAULT_RANDOM_SEED)

    assert again == prepared.dataset
    assert dataset_fingerprint(again) == dataset_fingerprint(prepared.dataset)


def test_fingerprint_matches_the_committed_manifest(prepared: PreparedDataset) -> None:
    """The committed fingerprint was generated in an earlier process, so equality also shows
    generation depends on no wall-clock or process state."""
    committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert committed["dataset_fingerprint"] == dataset_fingerprint(prepared.dataset)


def test_row_counts_are_exact(prepared: PreparedDataset) -> None:
    assert prepared.dataset.row_counts() == dict(EXPECTED_ROW_COUNTS)
    assert prepared.dataset.row_counts() == {
        "customers": 182,
        "products": 69,
        "inventory": 69,
        "orders": 625,
        "order_items": 1222,
        "shipments": 587,
        "payments": 625,
        "refunds": 65,
        "refund_items": 87,
    }


def test_status_mix_and_golden_order_count(prepared: PreparedDataset) -> None:
    statuses = [o.status for o in prepared.dataset.orders]

    assert len(prepared.dataset.golden_order_ids) == 25
    assert statuses.count(OrderStatus.DELIVERED) == 540 + 22
    assert statuses.count(OrderStatus.SHIPPED) == 24 + 1
    assert statuses.count(OrderStatus.PLACED) == 12 + 1
    assert statuses.count(OrderStatus.CANCELLED) == 24 + 1


def test_dataset_passes_in_memory_validation(prepared: PreparedDataset) -> None:
    validate_dataset(prepared.dataset)


def test_different_seed_changes_background_but_not_golden_truth(
    prepared: PreparedDataset,
) -> None:
    other = build_dataset(DEFAULT_RANDOM_SEED + 1)

    assert other.row_counts() == prepared.dataset.row_counts()
    assert dataset_fingerprint(other) != dataset_fingerprint(prepared.dataset)
    assert _golden_fingerprints(other) == _golden_fingerprints(prepared.dataset)
    assert build_manifest(other).scenarios == prepared.manifest.scenarios


_BACKGROUND = re.compile(r"^(CUST-1[0-9]{3}|ORD-1[0-9]{5}|SKU-1[0-9]{4})$")


def test_golden_and_background_ids_never_collide(prepared: PreparedDataset) -> None:
    dataset = prepared.dataset
    golden_orders = dataset.golden_order_ids
    background_orders = {o.order_id for o in dataset.orders} - golden_orders

    assert all(_BACKGROUND.fullmatch(order_id) for order_id in background_orders)
    assert not any(_BACKGROUND.fullmatch(order_id) for order_id in golden_orders)
    assert {o.customer_id for o in dataset.orders if o.order_id in golden_orders} == {
        "CUST-10",
        "CUST-20",
    }
    background_items = [i for i in dataset.order_items if i.order_id not in golden_orders]
    assert not any(i.product_id.startswith("SKU-GOLDEN-") for i in background_items)


def test_money_is_exact_decimal_cents_and_never_float(prepared: PreparedDataset) -> None:
    for records in prepared.dataset.tables().values():
        for record in records:
            for value in record_values(record).values():
                assert not isinstance(value, float)
                if isinstance(value, Decimal):
                    assert value.as_tuple().exponent == -2
                    assert value >= 0


def test_timestamps_are_utc_and_never_after_the_anchor(prepared: PreparedDataset) -> None:
    for records in prepared.dataset.tables().values():
        for record in records:
            for value in record_values(record).values():
                if hasattr(value, "utcoffset") and hasattr(value, "hour"):
                    assert value.utcoffset() == timedelta(0)
                    assert value <= SCENARIO_ANCHOR


def test_people_are_fictional_and_emails_use_the_reserved_domain(
    prepared: PreparedDataset,
) -> None:
    assert all(c.email.endswith("@example.test") for c in prepared.dataset.customers)


def test_cancelled_orders_carry_a_full_payment_reversal_with_shipping(
    prepared: PreparedDataset,
) -> None:
    dataset = prepared.dataset
    cancelled = {o.order_id: o for o in dataset.orders if o.status is OrderStatus.CANCELLED}
    payments = {p.order_id: p for p in dataset.payments}
    refunds = {r.order_id: r for r in dataset.refunds}

    assert "ORD-CANCELLED-001" in cancelled
    for order_id, order in cancelled.items():
        refund = refunds[order_id]
        assert payments[order_id].status is PaymentStatus.REFUNDED
        assert refund.amount == payments[order_id].amount
        assert refund.shipping_amount == order.shipping_amount
        assert refund.action_id is None
    items = [i for i in dataset.order_items if i.order_id in cancelled]
    assert all(i.refunded_quantity == i.quantity for i in items)
    assert all(i.refunded_amount == line_paid_total(i) for i in items)


def test_background_history_refunds_never_include_shipping(prepared: PreparedDataset) -> None:
    dataset = prepared.dataset
    statuses = {o.order_id: o.status for o in dataset.orders}
    background_delivered = [
        r
        for r in dataset.refunds
        if r.order_id not in dataset.golden_order_ids
        and statuses[r.order_id] is OrderStatus.DELIVERED
    ]

    assert len(background_delivered) == 36
    assert all(r.shipping_amount == 0 for r in background_delivered)
    adjustments = [
        line
        for line in dataset.refund_items
        if line.quantity == 0 and line.order_id not in dataset.golden_order_ids
    ]
    assert len(adjustments) == 9


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(
            lambda d: dataclasses.replace(
                d,
                payments=(
                    dataclasses.replace(d.payments[0], amount=d.payments[0].amount + 1),
                    *d.payments[1:],
                ),
            ),
            id="payment-total",
        ),
        pytest.param(
            lambda d: dataclasses.replace(
                d,
                refund_items=(
                    dataclasses.replace(
                        d.refund_items[0], amount=d.refund_items[0].amount + Decimal("0.01")
                    ),
                    *d.refund_items[1:],
                ),
            ),
            id="ledger-vs-balances",
        ),
        pytest.param(
            lambda d: dataclasses.replace(d, customers=d.customers[1:]),
            id="missing-customer",
        ),
    ],
)
def test_validation_rejects_corrupted_datasets(
    prepared: PreparedDataset, corrupt: Callable[[SyntheticDataset], SyntheticDataset]
) -> None:
    with pytest.raises(SyntheticDatasetError, match="failed validation"):
        validate_dataset(corrupt(prepared.dataset))


@functools.cache
def _reference_golden_fingerprints() -> dict[str, str]:
    return _golden_fingerprints(build_dataset(0))


@settings(max_examples=5, deadline=None, suppress_health_check=_SUPPRESSED)
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_any_seed_produces_a_valid_dataset_with_identical_golden_rows(seed: int) -> None:
    dataset = build_dataset(seed)

    validate_dataset(dataset)
    assert dataset.row_counts() == dict(EXPECTED_ROW_COUNTS)
    assert _golden_fingerprints(dataset) == _reference_golden_fingerprints()
