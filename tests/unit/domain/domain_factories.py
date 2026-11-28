"""Builders for trusted domain facts used by the domain unit tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from support_ops.domain.enums import Currency, OrderStatus
from support_ops.domain.models import OrderFacts, OrderLineFacts

PLACED_AT = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
SHIPPED_AT = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
DELIVERED_AT = datetime(2026, 10, 5, 18, 30, tzinfo=UTC)


def d(value: str) -> Decimal:
    return Decimal(value)


def make_line(**overrides: Any) -> OrderLineFacts:
    values: dict[str, Any] = {
        "order_item_id": "HEADPHONES-1",
        "product_id": "SKU-HEADPHONES",
        "quantity": 1,
        "unit_price": d("129.99"),
        "discount_amount": d("0.00"),
        "tax_amount": d("0.00"),
        "refunded_quantity": 0,
        "refunded_amount": d("0.00"),
        "returnable": True,
    }
    values.update(overrides)
    return OrderLineFacts(**values)


_TIMESTAMPS: dict[OrderStatus, tuple[datetime | None, datetime | None]] = {
    OrderStatus.PLACED: (None, None),
    OrderStatus.CANCELLED: (None, None),
    OrderStatus.SHIPPED: (SHIPPED_AT, None),
    OrderStatus.DELIVERED: (SHIPPED_AT, DELIVERED_AT),
}


def make_order(
    *lines: OrderLineFacts, status: OrderStatus = OrderStatus.DELIVERED, **overrides: Any
) -> OrderFacts:
    shipped_at, delivered_at = _TIMESTAMPS[status]
    values: dict[str, Any] = {
        "order_id": "ORD-1042",
        "customer_id": "CUST-10",
        "currency": Currency.USD,
        "status": status,
        "placed_at": PLACED_AT,
        "shipped_at": shipped_at,
        "delivered_at": delivered_at,
        "shipping_amount": d("0.00"),
        "shipping_refunded_amount": d("0.00"),
        "lines": lines or (make_line(),),
    }
    values.update(overrides)
    return OrderFacts(**values)


def days_after_delivery(days: int, *, hour: int = 12) -> datetime:
    delivered_day = DELIVERED_AT.replace(hour=0, minute=0)
    return delivered_day + timedelta(days=days, hours=hour)
