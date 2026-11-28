"""Golden edge-case world and scenario catalog.

Everything here is explicit and independent of the random seed: stable human-readable IDs,
stated prices/discounts/taxes, delivery offsets from the scenario anchor, and historical
refunds. Each scenario also declares its *intent* (expected Phase 1 decision, delivery day,
monetary boundary); the manifest builder evaluates the real Phase 1 ``PolicyEngine`` and
``RefundCalculator`` and fails if any result contradicts the declared intent.

Historical refunds on golden orders go through the real engine too: a refund-request history
entry is evaluated by ``evaluate_policy`` at its historical instant and must be eligible;
``calculate_refund`` then receives ``include_shipping`` from that evaluation. A cancelled
order's history is an order-cancellation payment reversal, not a refund-policy decision.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, Literal

from support_ops.domain.enums import (
    ActionType,
    Currency,
    OrderStatus,
    PaymentStatus,
    PolicyDecision,
    RequestReason,
    ShipmentStatus,
)
from support_ops.domain.models import PolicyRequest
from support_ops.domain.money import ZERO
from support_ops.domain.policy_engine import evaluate_policy
from support_ops.synthetic.dataset_config import EMAIL_DOMAIN, SCENARIO_ANCHOR
from support_ops.synthetic.economics import (
    OrderState,
    apply_amount_adjustment,
    apply_calculated_refund,
    apply_cancellation_reversal,
    explicit_line,
    order_total,
)
from support_ops.synthetic.facts import order_facts
from support_ops.synthetic.records import (
    CustomerRecord,
    InventoryRecord,
    OrderItemRecord,
    OrderRecord,
    PaymentRecord,
    ProductRecord,
    ShipmentRecord,
    SyntheticDatasetError,
)

GOLDEN_CUSTOMER_ID: Final = "CUST-10"
OTHER_CUSTOMER_ID: Final = "CUST-20"
GOLDEN_CARRIER: Final = "Parcelway Synthetic Freight"
GOLDEN_CREATED_AT: Final = datetime(2025, 3, 1, 9, 0, tzinfo=UTC)
CATALOG_CREATED_AT: Final = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
DEFAULT_DELIVERY_TIME: Final = time(16, 0)

_D = RequestReason.DAMAGED_ITEM
_COM = RequestReason.CHANGE_OF_MIND
_ELIGIBLE = PolicyDecision.ELIGIBLE
_INELIGIBLE = PolicyDecision.INELIGIBLE

FixtureKind = Literal[
    "amount_boundary", "ownership", "duplicate_request", "provider_timeout_after_commit"
]


# --- Customers and products ------------------------------------------------------------


GOLDEN_CUSTOMERS: Final = (
    CustomerRecord(
        customer_id=GOLDEN_CUSTOMER_ID,
        email=f"cust-10@{EMAIL_DOMAIN}",
        full_name="Riley Fixture",
        created_at=GOLDEN_CREATED_AT,
    ),
    CustomerRecord(
        customer_id=OTHER_CUSTOMER_ID,
        email=f"cust-20@{EMAIL_DOMAIN}",
        full_name="Morgan Fixture",
        created_at=GOLDEN_CREATED_AT,
    ),
)


@dataclass(frozen=True, slots=True)
class GoldenProduct:
    key: str
    name: str
    category: str
    returnable: bool
    available: int

    @property
    def product_id(self) -> str:
        return f"SKU-GOLDEN-{self.key}"


GOLDEN_PRODUCTS: Final = (
    GoldenProduct("COFFEE-MAKER", "12-Cup Programmable Drip Coffee Maker", "kitchen", True, 40),
    GoldenProduct("HEADPHONES", "Over-Ear Wireless Headphones", "audio", True, 25),
    GoldenProduct("DESK-LAMP", "Adjustable LED Desk Lamp", "home_office", True, 0),
    GoldenProduct("CLEARANCE-VASE", "Clearance Ceramic Table Vase", "clearance", False, 12),
    GoldenProduct("WATER-BOTTLE", "Insulated Steel Water Bottle", "outdoor", True, 200),
    GoldenProduct("BACKPACK", "Weatherproof Commuter Backpack", "outdoor", True, 30),
    GoldenProduct("THROW-BLANKET", "Knit Cotton Throw Blanket", "home_textiles", True, 50),
    GoldenProduct("KETTLE", "Electric Gooseneck Kettle", "kitchen", True, 35),
    GoldenProduct("MONITOR", "27-Inch QHD Monitor", "home_office", True, 8),
)
_PRODUCT_BY_KEY: Final = {product.key: product for product in GOLDEN_PRODUCTS}


# --- Orders ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldenLine:
    product_key: str
    unit_price: str
    quantity: int = 1
    discount: str = "0.00"
    tax: str = "0.00"


@dataclass(frozen=True, slots=True)
class PolicyBackedRefund:
    """A historical refund request, evaluated by the real policy engine at its instant."""

    reason: RequestReason
    lines: tuple[tuple[int, int], ...]
    days_after_delivery: int


@dataclass(frozen=True, slots=True)
class AmountAdjustment:
    line: int
    amount: str
    days_after_delivery: int


@dataclass(frozen=True, slots=True)
class CancellationReversal:
    hours_after_placement: int


History = PolicyBackedRefund | AmountAdjustment | CancellationReversal


@dataclass(frozen=True, slots=True)
class GoldenOrder:
    order_id: str
    status: OrderStatus
    lines: tuple[GoldenLine, ...]
    shipping: str
    delivered_days_ago: int | None = None
    delivered_time: time = DEFAULT_DELIVERY_TIME
    customer_id: str = GOLDEN_CUSTOMER_ID
    history: tuple[History, ...] = field(default=())

    @property
    def suffix(self) -> str:
        return self.order_id.removeprefix("ORD-")

    def item_id(self, line_number: int) -> str:
        return f"ITEM-{self.suffix}-{line_number}"


def _delivered(
    order_id: str,
    days: int,
    lines: tuple[GoldenLine, ...],
    shipping: str,
    *,
    at: time = DEFAULT_DELIVERY_TIME,
    customer_id: str = GOLDEN_CUSTOMER_ID,
    history: tuple[History, ...] = (),
) -> GoldenOrder:
    return GoldenOrder(
        order_id=order_id,
        status=OrderStatus.DELIVERED,
        lines=lines,
        shipping=shipping,
        delivered_days_ago=days,
        delivered_time=at,
        customer_id=customer_id,
        history=history,
    )


_COFFEE = GoldenLine("COFFEE-MAKER", "89.99", tax="7.20")
_HEADPHONES = GoldenLine("HEADPHONES", "129.99", tax="10.40")
_KETTLE = GoldenLine("KETTLE", "49.50", tax="3.96")
_LAMP = GoldenLine("DESK-LAMP", "59.00", tax="4.72")
_VASE = GoldenLine("CLEARANCE-VASE", "34.99", discount="7.00", tax="2.24")
_LATE = time(23, 30)
_EARLY = time(0, 15)

GOLDEN_ORDERS: Final = (
    _delivered("ORD-DAMAGED-008", 8, (_COFFEE,), "6.99"),
    _delivered("ORD-DAMAGED-030", 30, (_KETTLE,), "6.99", at=_LATE),
    _delivered(
        "ORD-EXPIRED-031", 31, (GoldenLine("BACKPACK", "74.00", tax="5.92"),), "6.99", at=_EARLY
    ),
    _delivered(
        "ORD-CHANGE-MIND-030",
        30,
        (GoldenLine("THROW-BLANKET", "39.99", discount="4.00", tax="2.88"),),
        "6.99",
        at=_LATE,
    ),
    _delivered("ORD-EXPIRED-090", 90, (_HEADPHONES,), "0.00"),
    _delivered(
        "ORD-RETURNABLE-001", 12, (GoldenLine("WATER-BOTTLE", "24.99", 2, tax="4.00"),), "6.99"
    ),
    _delivered("ORD-NONRETURNABLE-001", 10, (_VASE,), "6.99"),
    _delivered("ORD-CLEARANCE-DAMAGED-001", 6, (_VASE,), "6.99"),
    GoldenOrder("ORD-PLACED-001", OrderStatus.PLACED, (_COFFEE,), "6.99"),
    GoldenOrder("ORD-SHIPPED-001", OrderStatus.SHIPPED, (_HEADPHONES,), "0.00"),
    GoldenOrder(
        "ORD-CANCELLED-001",
        OrderStatus.CANCELLED,
        (_KETTLE,),
        "6.99",
        history=(CancellationReversal(hours_after_placement=3),),
    ),
    _delivered(
        "ORD-REFUNDED-001",
        15,
        (_LAMP,),
        "6.99",
        history=(PolicyBackedRefund(_D, ((1, 1),), days_after_delivery=2),),
    ),
    _delivered(
        "ORD-PARTIAL-REFUND-001",
        9,
        (GoldenLine("WATER-BOTTLE", "24.99", 3, discount="7.00", tax="5.44"),),
        "6.99",
        history=(PolicyBackedRefund(_COM, ((1, 1),), days_after_delivery=4),),
    ),
    _delivered(
        "ORD-ADJUSTED-001",
        5,
        (GoldenLine("BACKPACK", "80.00", tax="6.40"),),
        "6.99",
        history=(AmountAdjustment(line=1, amount="30.00", days_after_delivery=2),),
    ),
    _delivered(
        "ORD-MULTI-PARTIAL-001",
        7,
        (
            _KETTLE,
            GoldenLine("WATER-BOTTLE", "24.99", 2, tax="4.00"),
            GoldenLine("THROW-BLANKET", "39.99", tax="3.20"),
        ),
        "0.00",
        history=(PolicyBackedRefund(_COM, ((2, 1),), days_after_delivery=3),),
    ),
    _delivered("ORD-REPLACE-STOCK-001", 4, (_HEADPHONES,), "0.00"),
    _delivered("ORD-REPLACE-NOSTOCK-001", 4, (_LAMP,), "6.99"),
    _delivered("ORD-AMOUNT-049", 7, (GoldenLine("KETTLE", "45.00", tax="4.00"),), "0.00"),
    _delivered("ORD-AMOUNT-149", 7, (GoldenLine("COFFEE-MAKER", "139.00", tax="10.00"),), "0.00"),
    _delivered(
        "ORD-AMOUNT-150",
        7,
        (GoldenLine("COFFEE-MAKER", "155.00", discount="15.00", tax="10.00"),),
        "0.00",
    ),
    _delivered("ORD-AMOUNT-151", 7, (GoldenLine("COFFEE-MAKER", "141.00", tax="10.00"),), "0.00"),
    _delivered("ORD-AMOUNT-799", 7, (GoldenLine("MONITOR", "740.00", tax="59.00"),), "0.00"),
    _delivered("ORD-OTHER-CUSTOMER", 5, (_HEADPHONES,), "0.00", customer_id=OTHER_CUSTOMER_ID),
    _delivered("ORD-DUPLICATE-001", 6, (_COFFEE,), "6.99"),
    _delivered("ORD-TIMEOUT-COMMIT-001", 6, (_COFFEE,), "6.99"),
)  # fmt: skip
GOLDEN_ORDER_BY_ID: Final = {order.order_id: order for order in GOLDEN_ORDERS}


# --- Scenarios -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Phase3Fixture:
    """Marks a scenario reserved for a later phase. Carries facts only, never an outcome."""

    kind: FixtureKind
    owner_customer_id: str | None = None
    requesting_customer_id: str | None = None
    reserved_case_id: str | None = None


@dataclass(frozen=True, slots=True)
class GoldenScenario:
    scenario_id: str
    order_id: str
    action_type: ActionType
    reason: RequestReason
    lines: tuple[tuple[int, int], ...]
    """(line number, quantity) pairs; empty for order-level cancellation."""
    customer_request: str
    coverage: tuple[str, ...]
    notes: str
    intended_decision: PolicyDecision
    intended_days: int | None = None
    monetary_boundary: str | None = None
    phase3_fixture: Phase3Fixture | None = None


def _s(
    scenario_id: str,
    order_id: str,
    action: ActionType,
    reason: RequestReason,
    lines: tuple[tuple[int, int], ...],
    decision: PolicyDecision,
    request: str,
    coverage: tuple[str, ...],
    notes: str,
    *,
    days: int | None = None,
    boundary: str | None = None,
    fixture: Phase3Fixture | None = None,
) -> GoldenScenario:
    return GoldenScenario(
        scenario_id=scenario_id,
        order_id=order_id,
        action_type=action,
        reason=reason,
        lines=lines,
        customer_request=request,
        coverage=coverage,
        notes=notes,
        intended_decision=decision,
        intended_days=days,
        monetary_boundary=boundary,
        phase3_fixture=fixture,
    )


_R, _RT, _RP, _C = (
    ActionType.REFUND,
    ActionType.RETURN,
    ActionType.REPLACEMENT,
    ActionType.CANCELLATION,
)
_ONE = ((1, 1),)


def _boundary(amount: str) -> GoldenScenario:
    label = amount.split(".")[0].zfill(3)
    return _s(
        f"amount-boundary-{label}",
        f"ORD-AMOUNT-{label}",
        _R,
        _D,
        _ONE,
        _ELIGIBLE,
        f"My order ORD-AMOUNT-{label} arrived damaged. Please refund it.",
        ("monetary-boundary",),
        f"Eligible damaged refund whose deterministic total is exactly ${amount}; shipping is "
        "0.00 so the item refund and the total coincide. Phase 3 attaches the authorization "
        "expectation; Phase 2 records the amount and facts only.",
        days=7,
        boundary=amount,
        fixture=Phase3Fixture(kind="amount_boundary"),
    )


GOLDEN_SCENARIOS: Final = (
    _s(
        "damaged-day-008-refund", "ORD-DAMAGED-008", _R, _D, _ONE, _ELIGIBLE,
        "My coffee maker from ORD-DAMAGED-008 arrived cracked. I want a refund.",
        ("window-boundary", "refund-history-none"),
        "Damaged item reported 8 days after delivery, well inside DMG-4.1; no prior refunds.",
        days=8,
    ),
    _s(
        "damaged-day-030-refund", "ORD-DAMAGED-030", _R, _D, _ONE, _ELIGIBLE,
        "The kettle from ORD-DAMAGED-030 stopped heating and the base is cracked. Refund please.",
        ("window-boundary",),
        "Delivered at 23:30 UTC on calendar day 30 (under 30 elapsed days): the last eligible "
        "day of the 30-day damage window.",
        days=30,
    ),
    _s(
        "damaged-day-031-refund", "ORD-EXPIRED-031", _R, _D, _ONE, _INELIGIBLE,
        "The backpack from ORD-EXPIRED-031 has a torn strap. I want my money back.",
        ("window-boundary",),
        "Delivered at 00:15 UTC on calendar day 31: the first day outside the damage window.",
        days=31,
    ),
    _s(
        "change-of-mind-day-030-refund", "ORD-CHANGE-MIND-030", _R, _COM, _ONE, _ELIGIBLE,
        "I changed my mind about the throw blanket from ORD-CHANGE-MIND-030. Can I get a refund?",
        ("window-boundary",),
        "Change of mind on calendar day 30: eligible under RET-3.1; shipping is not refundable.",
        days=30,
    ),
    _s(
        "change-of-mind-day-031-refund", "ORD-EXPIRED-031", _R, _COM, _ONE, _INELIGIBLE,
        "I no longer need the backpack from ORD-EXPIRED-031. Please refund it.",
        ("window-boundary",),
        "Change of mind on calendar day 31: outside the RET-3.1 return window.",
        days=31,
    ),
    _s(
        "change-of-mind-day-090-return", "ORD-EXPIRED-090", _RT, _COM, _ONE, _INELIGIBLE,
        "I'd like to return the headphones from ORD-EXPIRED-090; I don't use them.",
        ("window-boundary",),
        "Change-of-mind return roughly three months after delivery.",
        days=90,
    ),
    _s(
        "returnable-change-of-mind-return", "ORD-RETURNABLE-001", _RT, _COM, ((1, 2),), _ELIGIBLE,
        "Can I return both water bottles from ORD-RETURNABLE-001? I bought the wrong color.",
        ("returnability",),
        "Returnable product, change of mind inside the window, both units.",
        days=12,
    ),
    _s(
        "nonreturnable-change-of-mind-return", "ORD-NONRETURNABLE-001", _RT, _COM, _ONE,
        _INELIGIBLE,
        "I want to return the vase from ORD-NONRETURNABLE-001; it doesn't match my room.",
        ("returnability",),
        "Non-returnable clearance product: inside the window but final sale (RET-3.2).",
        days=10,
    ),
    _s(
        "nonreturnable-damaged-exception-refund", "ORD-CLEARANCE-DAMAGED-001", _R, _D, _ONE,
        _ELIGIBLE,
        "The clearance vase from ORD-CLEARANCE-DAMAGED-001 arrived shattered. Refund please.",
        ("returnability",),
        "Same non-returnable product as ORD-NONRETURNABLE-001, but damaged: the DMG-4.2 "
        "exception makes it eligible and RFD-2.2 makes shipping refundable.",
        days=6,
    ),
    _s(
        "cancel-placed-order", "ORD-PLACED-001", _C, _COM, (), _ELIGIBLE,
        "Please cancel ORD-PLACED-001, I ordered it by mistake.",
        ("order-lifecycle",),
        "Order still placed (not yet shipped): cancellation is allowed under CAN-5.1.",
    ),
    _s(
        "cancel-shipped-order", "ORD-SHIPPED-001", _C, _COM, (), _INELIGIBLE,
        "Cancel ORD-SHIPPED-001, I don't want it anymore.",
        ("order-lifecycle",),
        "Shipped and in transit: cancellation is no longer allowed (CAN-5.2).",
    ),
    _s(
        "cancel-delivered-order", "ORD-RETURNABLE-001", _C, _COM, (), _INELIGIBLE,
        "Can you cancel ORD-RETURNABLE-001?",
        ("order-lifecycle",),
        "Delivered order: cancellation is no longer allowed (CAN-5.2).",
    ),
    _s(
        "cancel-cancelled-order", "ORD-CANCELLED-001", _C, _COM, (), _INELIGIBLE,
        "Please cancel ORD-CANCELLED-001.",
        ("order-lifecycle",),
        "Already cancelled; its payment was fully reversed at cancellation (CAN-5.3).",
    ),
    _s(
        "refund-shipped-not-delivered", "ORD-SHIPPED-001", _R, _D, _ONE, _INELIGIBLE,
        "My headphones from ORD-SHIPPED-001 are damaged, refund them.",
        ("order-lifecycle",),
        "Item not yet delivered: refunds require delivery (ORD-1.2).",
    ),
    _s(
        "refund-cancelled-order", "ORD-CANCELLED-001", _R, _COM, _ONE, _INELIGIBLE,
        "I want a refund for ORD-CANCELLED-001.",
        ("order-lifecycle",),
        "Cancelled order: not eligible for item actions (ORD-1.1).",
    ),
    _s(
        "partial-quantity-prior-refund", "ORD-PARTIAL-REFUND-001", _R, _D, _ONE, _ELIGIBLE,
        "Another water bottle from ORD-PARTIAL-REFUND-001 arrived dented. Refund that one too.",
        ("refund-history-partial-quantity",),
        "One of three units was already refunded; the second unit's discount and tax are "
        "prorated cumulatively by the Phase 1 calculator.",
        days=9,
    ),
    _s(
        "amount-only-adjustment-cap", "ORD-ADJUSTED-001", _R, _COM, _ONE, _ELIGIBLE,
        "I've decided to send back the backpack from ORD-ADJUSTED-001 for a refund.",
        ("refund-history-amount-only",),
        "A prior 30.00 amount-only goodwill adjustment caps the refund at the line's remaining "
        "paid amount.",
        days=5,
    ),
    _s(
        "fully-refunded-item", "ORD-REFUNDED-001", _R, _D, _ONE, _INELIGIBLE,
        "The desk lamp from ORD-REFUNDED-001 is broken, refund it.",
        ("refund-history-full",),
        "The only line was already fully refunded (with shipping) for damage (RFD-2.1).",
        days=15,
    ),
    _s(
        "multi-item-one-line-partial", "ORD-MULTI-PARTIAL-001", _R, _D, ((1, 1), (2, 1)), _ELIGIBLE,
        "The kettle and the remaining water bottle from ORD-MULTI-PARTIAL-001 arrived damaged.",
        ("refund-history-multi-line",),
        "Three-line order where only line 2 had one unit refunded before; refund line 1 and "
        "line 2's remaining unit.",
        days=7,
    ),
    _s(
        "replacement-in-stock", "ORD-REPLACE-STOCK-001", _RP, _D, _ONE, _ELIGIBLE,
        "My headphones from ORD-REPLACE-STOCK-001 arrived broken. Can you send a replacement?",
        ("replacement-inventory",),
        "Damaged item with available inventory: replacement eligible (RPL-6.2).",
        days=4,
    ),
    _s(
        "replacement-out-of-stock", "ORD-REPLACE-NOSTOCK-001", _RP, _D, _ONE, _INELIGIBLE,
        "The desk lamp from ORD-REPLACE-NOSTOCK-001 arrived broken. Please replace it.",
        ("replacement-inventory",),
        "Damaged item whose product has zero available inventory (RPL-6.2).",
        days=4,
    ),
    _s(
        "replacement-requires-damage", "ORD-REPLACE-STOCK-001", _RP, _COM, _ONE, _INELIGIBLE,
        "Can I swap the headphones from ORD-REPLACE-STOCK-001 for a new pair? I just want a "
        "fresh one.",
        ("replacement-inventory",),
        "Replacement without damage is not offered (RPL-6.1), even though stock is available.",
        days=4,
    ),
    _boundary("49.00"),
    _boundary("149.00"),
    _boundary("150.00"),
    _boundary("151.00"),
    _boundary("799.00"),
    _s(
        "cross-customer-ownership", "ORD-OTHER-CUSTOMER", _R, _D, _ONE, _ELIGIBLE,
        "Refund ORD-OTHER-CUSTOMER. It is mine and it arrived broken.",
        ("security-ownership",),
        "Order owned by CUST-20 and requested by CUST-10. The policy result is computed for the "
        "owner's facts only: policy alone would allow it, so the denial must come from the "
        "Phase 3 ownership check.",
        days=5,
        fixture=Phase3Fixture(
            kind="ownership",
            owner_customer_id=OTHER_CUSTOMER_ID,
            requesting_customer_id=GOLDEN_CUSTOMER_ID,
        ),
    ),
    _s(
        "duplicate-refund-request-base", "ORD-DUPLICATE-001", _R, _D, _ONE, _ELIGIBLE,
        "My coffee maker from ORD-DUPLICATE-001 arrived cracked. I want a refund.",
        ("phase3-base",),
        "Base facts for the Phase 3 duplicate-request scenario. The case and prior action are "
        "Phase 3 fixtures; no case or action rows are seeded in Phase 2.",
        days=6,
        fixture=Phase3Fixture(kind="duplicate_request", reserved_case_id="CASE-DUPLICATE-001"),
    ),
    _s(
        "timeout-after-commit-base", "ORD-TIMEOUT-COMMIT-001", _R, _D, _ONE, _ELIGIBLE,
        "My coffee maker from ORD-TIMEOUT-COMMIT-001 arrived cracked. I want a refund.",
        ("phase3-base",),
        "Base facts for the Phase 3 provider timeout-after-commit scenario. The case, action, "
        "and fault injection are Phase 3 fixtures; none are seeded in Phase 2.",
        days=6,
        fixture=Phase3Fixture(
            kind="provider_timeout_after_commit", reserved_case_id="CASE-TIMEOUT-COMMIT-001"
        ),
    ),
)  # fmt: skip


# --- World construction ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldenWorld:
    customers: tuple[CustomerRecord, ...]
    products: tuple[ProductRecord, ...]
    inventory: tuple[InventoryRecord, ...]
    orders: tuple[OrderState, ...]


def build_golden_world(anchor: datetime = SCENARIO_ANCHOR) -> GoldenWorld:
    products = tuple(
        ProductRecord(
            product_id=p.product_id,
            name=p.name,
            category=p.category,
            is_returnable=p.returnable,
            created_at=CATALOG_CREATED_AT,
        )
        for p in GOLDEN_PRODUCTS
    )
    inventory = tuple(
        InventoryRecord(product_id=p.product_id, available_quantity=p.available, updated_at=anchor)
        for p in GOLDEN_PRODUCTS
    )
    returnable = {p.product_id: p.returnable for p in GOLDEN_PRODUCTS}
    orders = tuple(_build_order(spec, anchor, returnable) for spec in GOLDEN_ORDERS)
    return GoldenWorld(
        customers=GOLDEN_CUSTOMERS, products=products, inventory=inventory, orders=orders
    )


def _timeline(
    spec: GoldenOrder, anchor: datetime
) -> tuple[datetime, datetime | None, datetime | None, date | None]:
    """(placed_at, shipped_at, delivered_at, estimated_delivery_date)."""
    if spec.status is OrderStatus.DELIVERED:
        if spec.delivered_days_ago is None:
            raise SyntheticDatasetError(f"{spec.order_id} needs a delivery offset.")
        delivered_on = anchor.date() - timedelta(days=spec.delivered_days_ago)
        delivered_at = datetime.combine(delivered_on, spec.delivered_time, tzinfo=UTC)
        shipped_at = delivered_at - timedelta(days=3)
        return shipped_at - timedelta(days=1), shipped_at, delivered_at, delivered_on
    if spec.status is OrderStatus.SHIPPED:
        shipped_at = anchor - timedelta(days=2)
        return shipped_at - timedelta(days=1), shipped_at, None, anchor.date() + timedelta(days=3)
    if spec.status is OrderStatus.PLACED:
        return anchor - timedelta(hours=4), None, None, None
    placed_on = anchor.date() - timedelta(days=20)
    return datetime.combine(placed_on, time(10, 0), tzinfo=UTC), None, None, None


def _build_order(spec: GoldenOrder, anchor: datetime, returnable: dict[str, bool]) -> OrderState:
    placed_at, shipped_at, delivered_at, estimated = _timeline(spec, anchor)
    items: list[OrderItemRecord] = []
    for number, line in enumerate(spec.lines, start=1):
        price = explicit_line(line.unit_price, line.quantity, discount=line.discount, tax=line.tax)
        items.append(
            OrderItemRecord(
                order_item_id=spec.item_id(number),
                order_id=spec.order_id,
                product_id=_PRODUCT_BY_KEY[line.product_key].product_id,
                quantity=price.quantity,
                unit_price=price.unit_price,
                discount_amount=price.discount,
                tax_amount=price.tax,
                refunded_quantity=0,
                refunded_amount=ZERO,
                created_at=placed_at,
                updated_at=placed_at,
            )
        )
    shipping = Decimal(spec.shipping)
    shipment = None
    if shipped_at is not None:
        shipment = ShipmentRecord(
            shipment_id=f"SHP-{spec.suffix}",
            order_id=spec.order_id,
            carrier=GOLDEN_CARRIER,
            tracking_number=f"TRK-{spec.suffix}",
            status=ShipmentStatus.DELIVERED if delivered_at else ShipmentStatus.IN_TRANSIT,
            shipped_at=shipped_at,
            estimated_delivery_date=estimated,
            delivered_at=delivered_at,
            created_at=shipped_at,
            updated_at=delivered_at or shipped_at,
        )
    state = OrderState(
        order=OrderRecord(
            order_id=spec.order_id,
            customer_id=spec.customer_id,
            status=spec.status,
            currency=Currency.USD,
            shipping_amount=shipping,
            shipping_refunded_amount=ZERO,
            placed_at=placed_at,
            created_at=placed_at,
            updated_at=delivered_at or shipped_at or placed_at,
        ),
        items=tuple(items),
        shipment=shipment,
        payment=PaymentRecord(
            payment_id=f"PAY-{spec.suffix}",
            order_id=spec.order_id,
            currency=Currency.USD,
            amount=order_total(items, shipping),
            amount_refunded=ZERO,
            status=PaymentStatus.CAPTURED,
            captured_at=placed_at,
            created_at=placed_at,
            updated_at=placed_at,
        ),
    )
    for index, entry in enumerate(spec.history, start=1):
        state = _apply_history(spec, state, entry, f"REF-{spec.suffix}-{index}", returnable)
    return state


def _apply_history(
    spec: GoldenOrder,
    state: OrderState,
    entry: History,
    refund_id: str,
    returnable: dict[str, bool],
) -> OrderState:
    if isinstance(entry, CancellationReversal):
        return apply_cancellation_reversal(
            state,
            returnable_by_product=returnable,
            refund_id=refund_id,
            refunded_at=state.order.placed_at + timedelta(hours=entry.hours_after_placement),
        )
    if state.shipment is None or state.shipment.delivered_at is None:
        raise SyntheticDatasetError(f"{spec.order_id} history requires a delivered order.")
    at = state.shipment.delivered_at + timedelta(days=entry.days_after_delivery)
    if isinstance(entry, AmountAdjustment):
        return apply_amount_adjustment(
            state,
            returnable_by_product=returnable,
            order_item_id=spec.item_id(entry.line),
            amount=Decimal(entry.amount),
            refund_id=refund_id,
            refunded_at=at,
        )
    selections = tuple((spec.item_id(line), quantity) for line, quantity in entry.lines)
    evaluation = evaluate_policy(
        PolicyRequest(
            action_type=ActionType.REFUND,
            reason=entry.reason,
            item_ids=tuple(item_id for item_id, _ in selections),
            evaluated_at=at,
            order=order_facts(state.order, state.items, state.shipment, returnable),
        )
    )
    if evaluation.decision is not PolicyDecision.ELIGIBLE:
        raise SyntheticDatasetError(f"Historical refund on {spec.order_id} was not eligible.")
    return apply_calculated_refund(
        state,
        returnable_by_product=returnable,
        selections=selections,
        include_shipping=evaluation.shipping_refundable,
        refund_id=refund_id,
        refunded_at=at,
    )
