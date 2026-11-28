"""In-memory representation of the synthetic dataset.

One frozen record type per seeded table; field names and types mirror the ORM columns in
``support_ops.db.schema`` exactly (including ``created_at``/``updated_at``, which are set
explicitly so a reseed reproduces every column). Money is ``Decimal`` exact to the cent and
timestamps are timezone-aware UTC.

Canonical serialization sorts rows by primary key in Python (never by database collation)
and renders every value deterministically, so the same rows always produce the same
SHA-256 fingerprint whether they were generated in memory or read back from PostgreSQL.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from support_ops.domain.enums import Currency, OrderStatus, PaymentStatus, ShipmentStatus


class SyntheticDatasetError(RuntimeError):
    """The synthetic dataset or its manifest is invalid or inconsistent."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerRecord:
    customer_id: str
    email: str
    full_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductRecord:
    product_id: str
    name: str
    category: str
    is_returnable: bool
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class InventoryRecord:
    product_id: str
    available_quantity: int
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRecord:
    order_id: str
    customer_id: str
    status: OrderStatus
    currency: Currency
    shipping_amount: Decimal
    shipping_refunded_amount: Decimal
    placed_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderItemRecord:
    order_item_id: str
    order_id: str
    product_id: str
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    refunded_quantity: int
    refunded_amount: Decimal
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ShipmentRecord:
    shipment_id: str
    order_id: str
    carrier: str
    tracking_number: str
    status: ShipmentStatus
    shipped_at: datetime
    estimated_delivery_date: date | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentRecord:
    payment_id: str
    order_id: str
    currency: Currency
    amount: Decimal
    amount_refunded: Decimal
    status: PaymentStatus
    captured_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class RefundRecord:
    """Immutable ledger entry. ``action_id`` is NULL for historical (pre-platform) refunds."""

    refund_id: str
    order_id: str
    payment_id: str
    action_id: str | None
    amount: Decimal
    shipping_amount: Decimal
    refunded_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class RefundItemRecord:
    """Immutable ledger line; ``quantity == 0`` records an amount-only adjustment."""

    refund_id: str
    order_item_id: str
    order_id: str
    quantity: int
    amount: Decimal
    created_at: datetime


Record = (
    CustomerRecord
    | ProductRecord
    | InventoryRecord
    | OrderRecord
    | OrderItemRecord
    | ShipmentRecord
    | PaymentRecord
    | RefundRecord
    | RefundItemRecord
)

RECORD_TYPES: Final[Mapping[str, type[Record]]] = MappingProxyType(
    {
        "customers": CustomerRecord,
        "products": ProductRecord,
        "inventory": InventoryRecord,
        "orders": OrderRecord,
        "order_items": OrderItemRecord,
        "shipments": ShipmentRecord,
        "payments": PaymentRecord,
        "refunds": RefundRecord,
        "refund_items": RefundItemRecord,
    }
)

PRIMARY_KEYS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "customers": ("customer_id",),
        "products": ("product_id",),
        "inventory": ("product_id",),
        "orders": ("order_id",),
        "order_items": ("order_item_id",),
        "shipments": ("shipment_id",),
        "payments": ("payment_id",),
        "refunds": ("refund_id",),
        "refund_items": ("refund_id", "order_item_id"),
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SyntheticDataset:
    """A complete synthetic world, sorted by primary key within each table."""

    dataset_version: str
    seed: int
    anchor: datetime
    customers: tuple[CustomerRecord, ...]
    products: tuple[ProductRecord, ...]
    inventory: tuple[InventoryRecord, ...]
    orders: tuple[OrderRecord, ...]
    order_items: tuple[OrderItemRecord, ...]
    shipments: tuple[ShipmentRecord, ...]
    payments: tuple[PaymentRecord, ...]
    refunds: tuple[RefundRecord, ...]
    refund_items: tuple[RefundItemRecord, ...]
    golden_order_ids: frozenset[str]

    def tables(self) -> dict[str, tuple[Record, ...]]:
        """Every seeded table's records, in foreign-key insert order."""
        return {
            "customers": self.customers,
            "products": self.products,
            "inventory": self.inventory,
            "orders": self.orders,
            "order_items": self.order_items,
            "shipments": self.shipments,
            "payments": self.payments,
            "refunds": self.refunds,
            "refund_items": self.refund_items,
        }

    def row_counts(self) -> dict[str, int]:
        return {name: len(records) for name, records in self.tables().items()}


def record_values(record: Record) -> dict[str, object]:
    """Column name to value, ready for a SQLAlchemy Core insert."""
    return {field.name: getattr(record, field.name) for field in fields(record)}


def primary_key(table: str, record: Record) -> tuple[str, ...]:
    return tuple(str(getattr(record, column)) for column in PRIMARY_KEYS[table])


def sort_records[R: Record](table: str, records: Iterable[R]) -> tuple[R, ...]:
    return tuple(sorted(records, key=lambda record: primary_key(table, record)))


def canonical_value(value: object) -> str | int | bool | None:
    """Deterministic JSON-safe rendering of one column value."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str | int):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise SyntheticDatasetError("Synthetic timestamps must be timezone-aware.")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise SyntheticDatasetError(f"Unsupported synthetic column type {type(value).__name__}.")


def canonical_rows(table: str, records: Iterable[Record]) -> list[str]:
    return [
        json.dumps(
            {name: canonical_value(value) for name, value in record_values(record).items()},
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in sort_records(table, records)
    ]


def table_fingerprint(table: str, records: Iterable[Record]) -> str:
    joined = "\n".join(canonical_rows(table, records))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def table_fingerprints(dataset: SyntheticDataset) -> dict[str, str]:
    return {name: table_fingerprint(name, records) for name, records in dataset.tables().items()}


def combined_fingerprint(fingerprints: Mapping[str, str]) -> str:
    payload = json.dumps(dict(fingerprints), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dataset_fingerprint(dataset: SyntheticDataset) -> str:
    """SHA-256 identity of every seeded row (not of the version/seed/anchor labels)."""
    return combined_fingerprint(table_fingerprints(dataset))
