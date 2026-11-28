"""Assemble the complete synthetic dataset from the golden world and background data."""

import random
from collections.abc import Iterable
from dataclasses import dataclass

from support_ops.synthetic.background import generate_background
from support_ops.synthetic.dataset_config import (
    DATASET_VERSION,
    DEFAULT_RANDOM_SEED,
    SCENARIO_ANCHOR,
)
from support_ops.synthetic.golden import build_golden_world
from support_ops.synthetic.manifest import ScenarioManifest, build_manifest
from support_ops.synthetic.records import (
    PRIMARY_KEYS,
    SyntheticDataset,
    SyntheticDatasetError,
    primary_key,
    sort_records,
)
from support_ops.synthetic.validation import validate_dataset


@dataclass(frozen=True, slots=True)
class PreparedDataset:
    """A dataset that passed in-memory validation, with its golden manifest. Persistence
    accepts only this type."""

    dataset: SyntheticDataset
    manifest: ScenarioManifest


def prepare_dataset(seed: int = DEFAULT_RANDOM_SEED) -> PreparedDataset:
    dataset = build_dataset(seed)
    validate_dataset(dataset)
    return PreparedDataset(dataset=dataset, manifest=build_manifest(dataset))


def build_dataset(seed: int = DEFAULT_RANDOM_SEED) -> SyntheticDataset:
    """Build the full in-memory dataset. Golden rows never depend on ``seed``."""
    golden = build_golden_world(SCENARIO_ANCHOR)
    # S311 is suppressed on this line only: an owned, seeded generator for deterministic,
    # non-security synthetic test data. Nothing here is a secret, token, or key.
    rng = random.Random(seed)  # noqa: S311
    background = generate_background(rng, SCENARIO_ANCHOR)
    states = (*golden.orders, *background.orders)

    dataset = SyntheticDataset(
        dataset_version=DATASET_VERSION,
        seed=seed,
        anchor=SCENARIO_ANCHOR,
        customers=sort_records("customers", (*golden.customers, *background.customers)),
        products=sort_records("products", (*golden.products, *background.products)),
        inventory=sort_records("inventory", (*golden.inventory, *background.inventory)),
        orders=sort_records("orders", (s.order for s in states)),
        order_items=sort_records("order_items", _flatten(s.items for s in states)),
        shipments=sort_records("shipments", (s.shipment for s in states if s.shipment)),
        payments=sort_records("payments", (s.payment for s in states)),
        refunds=sort_records("refunds", _flatten(s.refunds for s in states)),
        refund_items=sort_records("refund_items", _flatten(s.refund_items for s in states)),
        golden_order_ids=frozenset(s.order.order_id for s in golden.orders),
    )
    _reject_collisions(dataset)
    return dataset


def _flatten[T](groups: Iterable[tuple[T, ...]]) -> list[T]:
    return [item for group in groups for item in group]


def _reject_collisions(dataset: SyntheticDataset) -> None:
    """Golden IDs are words and background IDs are numbers; any primary-key collision
    between them (or within either) is a generator bug."""
    for table, records in dataset.tables().items():
        keys = [primary_key(table, record) for record in records]
        if len(keys) != len(set(keys)):
            raise SyntheticDatasetError(f"Duplicate {PRIMARY_KEYS[table]} in {table}.")
