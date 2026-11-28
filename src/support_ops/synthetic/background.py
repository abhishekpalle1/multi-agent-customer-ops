"""Deterministic background business data.

Every random choice comes from the ``random.Random`` instance passed in (owned by
``dataset.build_dataset``); the global generator and the wall clock are never used. Row
counts come from the fixed allocation tables in ``dataset_config`` and do not depend on the
seed; only the values do.
"""

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from support_ops.domain.enums import Currency, OrderStatus, PaymentStatus, ShipmentStatus
from support_ops.domain.money import ZERO, from_cents, to_cents
from support_ops.synthetic.dataset_config import (
    AMOUNT_ADJUSTMENTS,
    BACKGROUND_CUSTOMER_COUNT,
    BACKGROUND_PRODUCT_COUNT,
    EMAIL_DOMAIN,
    FULL_LINE_REFUNDS,
    NON_RETURNABLE_PRODUCT_COUNT,
    ORDER_ALLOCATION,
    OUT_OF_STOCK_PRODUCT_COUNT,
    PARTIAL_QUANTITY_REFUNDS,
)
from support_ops.synthetic.economics import (
    LinePrice,
    OrderState,
    apply_amount_adjustment,
    apply_calculated_refund,
    apply_cancellation_reversal,
    line_paid_total,
    order_total,
    price_line,
)
from support_ops.synthetic.records import (
    CustomerRecord,
    InventoryRecord,
    OrderItemRecord,
    OrderRecord,
    PaymentRecord,
    ProductRecord,
    ShipmentRecord,
)
from support_ops.synthetic.vocabulary import (
    ADJECTIVES,
    CARRIERS,
    FIRST_NAMES,
    LAST_NAMES,
    NON_RETURNABLE_CATEGORIES,
    RETURNABLE_CATEGORIES,
    CategorySpec,
)

FIRST_CUSTOMER_NUMBER: Final = 1001
FIRST_PRODUCT_NUMBER: Final = 10001
FIRST_ORDER_NUMBER: Final = 100001
CUSTOMER_CREATED_RANGE: Final = (
    datetime(2025, 1, 1, tzinfo=UTC),
    datetime(2026, 5, 1, tzinfo=UTC),
)
PRODUCT_CREATED_RANGE: Final = (
    datetime(2025, 6, 1, tzinfo=UTC),
    datetime(2026, 1, 1, tzinfo=UTC),
)
FREE_SHIPPING_THRESHOLD_CENTS: Final = 7500
SHIPPING_FEES_CENTS: Final = (599, 999)
DISCOUNT_BPS_CHOICES: Final = (0, 0, 0, 1000, 1500)
TAX_BPS_CHOICES: Final = (0, 600, 725, 825)
MAX_INVENTORY: Final = 250

_MINUTE = timedelta(minutes=1)
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class BackgroundData:
    customers: tuple[CustomerRecord, ...]
    products: tuple[ProductRecord, ...]
    inventory: tuple[InventoryRecord, ...]
    orders: tuple[OrderState, ...]


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    product: ProductRecord
    list_price_cents: int


def _between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """A whole-minute instant in ``[start, end)``."""
    minutes = int((end - start) / _MINUTE)
    return start + rng.randrange(max(minutes, 1)) * _MINUTE


def generate_background(rng: random.Random, anchor: datetime) -> BackgroundData:
    customers = _customers(rng)
    catalog = _catalog(rng)
    inventory = _inventory(rng, catalog, anchor)
    returnable = {entry.product.product_id: entry.product.is_returnable for entry in catalog}
    orders = _orders(rng, anchor, customers, catalog, returnable)
    return BackgroundData(
        customers=customers,
        products=tuple(entry.product for entry in catalog),
        inventory=inventory,
        orders=orders,
    )


def _customers(rng: random.Random) -> tuple[CustomerRecord, ...]:
    customers = []
    for number in range(FIRST_CUSTOMER_NUMBER, FIRST_CUSTOMER_NUMBER + BACKGROUND_CUSTOMER_COUNT):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        customers.append(
            CustomerRecord(
                customer_id=f"CUST-{number}",
                email=f"{first.lower()}.{last.lower()}.{number}@{EMAIL_DOMAIN}",
                full_name=f"{first} {last}",
                created_at=_between(rng, *CUSTOMER_CREATED_RANGE),
            )
        )
    return tuple(customers)


def _catalog(rng: random.Random) -> tuple[_CatalogEntry, ...]:
    non_returnable = list(NON_RETURNABLE_CATEGORIES.items())
    returnable = list(RETURNABLE_CATEGORIES.items())
    slots: list[tuple[tuple[str, CategorySpec], bool]] = [
        (non_returnable[i % len(non_returnable)], False)
        for i in range(NON_RETURNABLE_PRODUCT_COUNT)
    ] + [
        (returnable[i % len(returnable)], True)
        for i in range(BACKGROUND_PRODUCT_COUNT - NON_RETURNABLE_PRODUCT_COUNT)
    ]
    rng.shuffle(slots)
    catalog = []
    for offset, ((category, (nouns, low, high)), is_returnable) in enumerate(slots):
        catalog.append(
            _CatalogEntry(
                product=ProductRecord(
                    product_id=f"SKU-{FIRST_PRODUCT_NUMBER + offset}",
                    name=f"{rng.choice(ADJECTIVES)} {rng.choice(nouns)}",
                    category=category,
                    is_returnable=is_returnable,
                    created_at=_between(rng, *PRODUCT_CREATED_RANGE),
                ),
                list_price_cents=rng.randint(low, high) * 100 + 99,
            )
        )
    return tuple(catalog)


def _inventory(
    rng: random.Random, catalog: tuple[_CatalogEntry, ...], anchor: datetime
) -> tuple[InventoryRecord, ...]:
    product_ids = [entry.product.product_id for entry in catalog]
    out_of_stock = set(rng.sample(product_ids, OUT_OF_STOCK_PRODUCT_COUNT))
    return tuple(
        InventoryRecord(
            product_id=product_id,
            available_quantity=0 if product_id in out_of_stock else rng.randint(1, MAX_INVENTORY),
            updated_at=anchor,
        )
        for product_id in product_ids
    )


_PARTIAL, _FULL_LINE, _ADJUSTMENT = "partial", "full_line", "adjustment"


@dataclass(frozen=True, slots=True)
class _Timeline:
    placed_at: datetime
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    estimated_delivery: date | None = None
    cancelled_at: datetime | None = None


def _timeline(rng: random.Random, status: OrderStatus, anchor: datetime) -> _Timeline:
    if status is OrderStatus.DELIVERED:
        # Placed at least 9 days ago: shipping (<= 2 days) plus transit (< 6 days) always
        # completes before the anchor.
        placed = _between(rng, anchor - 150 * _DAY, anchor - 9 * _DAY)
        shipped = placed + rng.randrange(6 * 60, 48 * 60) * _MINUTE
        delivered = shipped + rng.randrange(24 * 60, 6 * 24 * 60) * _MINUTE
        return _Timeline(placed, shipped, delivered, delivered.date())
    if status is OrderStatus.SHIPPED:
        shipped = _between(rng, anchor - 5 * _DAY, anchor - _HOUR)
        placed = shipped - rng.randrange(6 * 60, 48 * 60) * _MINUTE
        estimated = max((shipped + rng.randrange(2, 7) * _DAY).date(), anchor.date())
        return _Timeline(placed, shipped, None, estimated)
    if status is OrderStatus.PLACED:
        return _Timeline(_between(rng, anchor - 36 * _HOUR, anchor - 10 * _MINUTE))
    placed = _between(rng, anchor - 150 * _DAY, anchor - 2 * _DAY)
    return _Timeline(placed, cancelled_at=placed + rng.randrange(60, 20 * 60) * _MINUTE)


def _orders(
    rng: random.Random,
    anchor: datetime,
    customers: tuple[CustomerRecord, ...],
    catalog: tuple[_CatalogEntry, ...],
    returnable: dict[str, bool],
) -> tuple[OrderState, ...]:
    allocation = [
        (status, line_count)
        for status, counts in ORDER_ALLOCATION.items()
        for line_count, orders in enumerate(counts, start=1)
        for _ in range(orders)
    ]
    rng.shuffle(allocation)
    delivered = [i for i, (status, _) in enumerate(allocation) if status is OrderStatus.DELIVERED]
    with_history = rng.sample(
        delivered, PARTIAL_QUANTITY_REFUNDS + FULL_LINE_REFUNDS + AMOUNT_ADJUSTMENTS
    )
    history_kind = {
        index: (
            _PARTIAL
            if position < PARTIAL_QUANTITY_REFUNDS
            else _FULL_LINE
            if position < PARTIAL_QUANTITY_REFUNDS + FULL_LINE_REFUNDS
            else _ADJUSTMENT
        )
        for position, index in enumerate(with_history)
    }
    return tuple(
        _order(
            rng,
            anchor,
            number=FIRST_ORDER_NUMBER + index,
            status=status,
            line_count=line_count,
            history=history_kind.get(index),
            customers=customers,
            catalog=catalog,
            returnable=returnable,
        )
        for index, (status, line_count) in enumerate(allocation)
    )


def _order(
    rng: random.Random,
    anchor: datetime,
    *,
    number: int,
    status: OrderStatus,
    line_count: int,
    history: str | None,
    customers: tuple[CustomerRecord, ...],
    catalog: tuple[_CatalogEntry, ...],
    returnable: dict[str, bool],
) -> OrderState:
    order_id = f"ORD-{number}"
    customer = rng.choice(customers)
    discount_bps = rng.choice(DISCOUNT_BPS_CHOICES)
    tax_bps = rng.choice(TAX_BPS_CHOICES)
    entries = rng.sample(catalog, line_count)
    quantities = [rng.choices((1, 2, 3), weights=(70, 20, 10))[0] for _ in entries]
    if history == _PARTIAL:
        quantities[0] = rng.choice((2, 3))
    prices: list[LinePrice] = [
        price_line(entry.list_price_cents, quantity, discount_bps=discount_bps, tax_bps=tax_bps)
        for entry, quantity in zip(entries, quantities, strict=True)
    ]
    merchandise_cents = sum(to_cents(p.gross - p.discount) for p in prices)
    shipping = (
        ZERO
        if merchandise_cents >= FREE_SHIPPING_THRESHOLD_CENTS
        else from_cents(rng.choice(SHIPPING_FEES_CENTS))
    )
    timeline = _timeline(rng, status, anchor)
    placed = timeline.placed_at

    items = tuple(
        OrderItemRecord(
            order_item_id=f"ITEM-{number}-{line}",
            order_id=order_id,
            product_id=entry.product.product_id,
            quantity=price.quantity,
            unit_price=price.unit_price,
            discount_amount=price.discount,
            tax_amount=price.tax,
            refunded_quantity=0,
            refunded_amount=ZERO,
            created_at=placed,
            updated_at=placed,
        )
        for line, (entry, price) in enumerate(zip(entries, prices, strict=True), start=1)
    )
    shipment = None
    if timeline.shipped_at is not None:
        shipment = ShipmentRecord(
            shipment_id=f"SHP-{number}",
            order_id=order_id,
            carrier=rng.choice(CARRIERS),
            tracking_number=f"TRK-{number}",
            status=(
                ShipmentStatus.DELIVERED if timeline.delivered_at else ShipmentStatus.IN_TRANSIT
            ),
            shipped_at=timeline.shipped_at,
            estimated_delivery_date=timeline.estimated_delivery,
            delivered_at=timeline.delivered_at,
            created_at=timeline.shipped_at,
            updated_at=timeline.delivered_at or timeline.shipped_at,
        )
    state = OrderState(
        order=OrderRecord(
            order_id=order_id,
            customer_id=customer.customer_id,
            status=status,
            currency=Currency.USD,
            shipping_amount=shipping,
            shipping_refunded_amount=ZERO,
            placed_at=placed,
            created_at=placed,
            updated_at=timeline.delivered_at or timeline.shipped_at or placed,
        ),
        items=items,
        shipment=shipment,
        payment=PaymentRecord(
            payment_id=f"PAY-{number}",
            order_id=order_id,
            currency=Currency.USD,
            amount=order_total(items, shipping),
            amount_refunded=ZERO,
            status=PaymentStatus.CAPTURED,
            captured_at=placed,
            created_at=placed,
            updated_at=placed,
        ),
    )
    refund_id = f"REF-{number}-1"
    if timeline.cancelled_at is not None:
        return apply_cancellation_reversal(
            state,
            returnable_by_product=returnable,
            refund_id=refund_id,
            refunded_at=timeline.cancelled_at,
        )
    if history is None or timeline.delivered_at is None:
        return state

    refunded_at = _between(
        rng,
        timeline.delivered_at + _HOUR,
        min(timeline.delivered_at + 25 * _DAY, anchor),
    )
    if history == _ADJUSTMENT:
        item = rng.choice(items)
        paid_cents = to_cents(line_paid_total(item))
        adjustment = min(max(paid_cents * rng.randint(10, 25) // 100, 100), paid_cents)
        return apply_amount_adjustment(
            state,
            returnable_by_product=returnable,
            order_item_id=item.order_item_id,
            amount=from_cents(adjustment),
            refund_id=refund_id,
            refunded_at=refunded_at,
        )
    if history == _PARTIAL:
        selection = (items[0].order_item_id, 1)
    else:
        item = rng.choice(items)
        selection = (item.order_item_id, item.quantity)
    # Historical background refunds never include shipping.
    return apply_calculated_refund(
        state,
        returnable_by_product=returnable,
        selections=(selection,),
        include_shipping=False,
        refund_id=refund_id,
        refunded_at=refunded_at,
    )
