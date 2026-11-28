"""Conversion of synthetic records into Phase 1 trusted-fact contracts.

The manifest builder, the in-memory validator, and the post-seed reconciler all rebuild
``OrderFacts`` through this module, so every consumer applies the same Phase 1 invariants.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from support_ops.domain.models import OrderFacts, OrderLineFacts
from support_ops.synthetic.records import (
    InventoryRecord,
    OrderItemRecord,
    OrderRecord,
    ProductRecord,
    ShipmentRecord,
    SyntheticDataset,
)


def order_facts(
    order: OrderRecord,
    items: Sequence[OrderItemRecord],
    shipment: ShipmentRecord | None,
    returnable_by_product: Mapping[str, bool],
) -> OrderFacts:
    """Build validated Phase 1 ``OrderFacts``; raises if the records contradict each other."""
    return OrderFacts(
        order_id=order.order_id,
        customer_id=order.customer_id,
        currency=order.currency,
        status=order.status,
        placed_at=order.placed_at,
        shipped_at=shipment.shipped_at if shipment is not None else None,
        delivered_at=shipment.delivered_at if shipment is not None else None,
        shipping_amount=order.shipping_amount,
        shipping_refunded_amount=order.shipping_refunded_amount,
        lines=tuple(
            OrderLineFacts(
                order_item_id=item.order_item_id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_amount=item.discount_amount,
                tax_amount=item.tax_amount,
                refunded_quantity=item.refunded_quantity,
                refunded_amount=item.refunded_amount,
                returnable=returnable_by_product[item.product_id],
            )
            for item in items
        ),
    )


@dataclass(frozen=True, slots=True)
class FactIndex:
    """Lookup tables over one dataset (generated in memory or loaded from PostgreSQL)."""

    orders: Mapping[str, OrderRecord]
    items_by_order: Mapping[str, tuple[OrderItemRecord, ...]]
    shipments_by_order: Mapping[str, ShipmentRecord]
    products: Mapping[str, ProductRecord]
    inventory: Mapping[str, InventoryRecord]

    @classmethod
    def from_dataset(cls, dataset: SyntheticDataset) -> "FactIndex":
        return cls.from_records(
            orders=dataset.orders,
            items=dataset.order_items,
            shipments=dataset.shipments,
            products=dataset.products,
            inventory=dataset.inventory,
        )

    @classmethod
    def from_records(
        cls,
        *,
        orders: Iterable[OrderRecord],
        items: Iterable[OrderItemRecord],
        shipments: Iterable[ShipmentRecord],
        products: Iterable[ProductRecord],
        inventory: Iterable[InventoryRecord],
    ) -> "FactIndex":
        grouped: defaultdict[str, list[OrderItemRecord]] = defaultdict(list)
        for item in items:
            grouped[item.order_id].append(item)
        return cls(
            orders={order.order_id: order for order in orders},
            items_by_order={
                order_id: tuple(sorted(lines, key=lambda line: line.order_item_id))
                for order_id, lines in grouped.items()
            },
            shipments_by_order={shipment.order_id: shipment for shipment in shipments},
            products={product.product_id: product for product in products},
            inventory={row.product_id: row for row in inventory},
        )

    @property
    def returnable_by_product(self) -> dict[str, bool]:
        return {product_id: p.is_returnable for product_id, p in self.products.items()}

    def facts(self, order_id: str) -> OrderFacts:
        return order_facts(
            self.orders[order_id],
            self.items_by_order[order_id],
            self.shipments_by_order.get(order_id),
            self.returnable_by_product,
        )

    def item(self, order_item_id: str) -> OrderItemRecord:
        for items in self.items_by_order.values():
            for item in items:
                if item.order_item_id == order_item_id:
                    return item
        raise KeyError(order_item_id)

    def in_stock_item_ids(self, selections: Sequence[tuple[str, int]]) -> frozenset[str]:
        """Trusted replacement stock fact: an item is in stock when its product's available
        inventory covers the requested quantity."""
        return frozenset(
            order_item_id
            for order_item_id, quantity in selections
            if self.inventory[self.item(order_item_id).product_id].available_quantity >= quantity
        )
