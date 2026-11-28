"""Synthetic dataset identity and fixed generation parameters.

The dataset identity is ``(repository revision, DATASET_VERSION, random seed,
SCENARIO_ANCHOR)``. Every timestamp in the dataset, and every golden-scenario evaluation, is
derived from ``SCENARIO_ANCHOR``; nothing reads the wall clock. Changing the anchor, the
allocation tables, or the golden catalog is a dataset-version change.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final

from support_ops.domain.enums import OrderStatus

DATASET_VERSION: Final = "p2-v1"
DEFAULT_RANDOM_SEED: Final = 20261101
SCENARIO_ANCHOR: Final = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
REQUIRED_SCHEMA_REVISION: Final = "0002_create_domain_schema"
MANIFEST_SCHEMA_VERSION: Final = 1
EMAIL_DOMAIN: Final = "example.test"

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
MANIFEST_PATH: Final = REPO_ROOT / "data" / "golden" / "scenarios.json"
POLICY_DIR: Final = REPO_ROOT / "data" / "policies"

BACKGROUND_CUSTOMER_COUNT: Final = 180
BACKGROUND_PRODUCT_COUNT: Final = 60
NON_RETURNABLE_PRODUCT_COUNT: Final = 9
OUT_OF_STOCK_PRODUCT_COUNT: Final = 6

ORDER_ALLOCATION: Final[Mapping[OrderStatus, tuple[int, int, int, int]]] = MappingProxyType(
    {
        OrderStatus.DELIVERED: (216, 162, 108, 54),
        OrderStatus.SHIPPED: (10, 8, 4, 2),
        OrderStatus.PLACED: (5, 4, 2, 1),
        OrderStatus.CANCELLED: (10, 8, 4, 2),
    }
)
"""Background orders per status with 1, 2, 3, and 4 lines. The (status, line count) pairs
are shuffled, so the seed changes which orders they are but never how many."""

PARTIAL_QUANTITY_REFUNDS: Final = 18
FULL_LINE_REFUNDS: Final = 9
AMOUNT_ADJUSTMENTS: Final = 9

SEEDED_TABLES: Final = (
    "customers",
    "products",
    "inventory",
    "orders",
    "order_items",
    "shipments",
    "payments",
    "refunds",
    "refund_items",
)
"""Tables the synthetic dataset populates, in foreign-key insert order."""

EXPECTED_ROW_COUNTS: Final[Mapping[str, int]] = MappingProxyType(
    {
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
)
"""Exact row counts for ``p2-v1``, independent of the random seed."""
