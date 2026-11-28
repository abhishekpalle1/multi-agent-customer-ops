"""Controlled domain vocabulary.

Every enum is a ``StrEnum`` with UPPERCASE member names and stable lowercase serialized
values (``Currency`` uses the ISO 4217 code). These Python enums are the source of truth:
the database stores the same values in ``VARCHAR`` columns guarded by CHECK constraints,
not PostgreSQL ENUM types.

A status exists only when a documented current or later workflow uses it.

Similar names, disambiguated:

- ``ActionStatus.AUTHORIZED`` means the stored action is cleared to execute (after an
  ``AuthorizationDecision.ALLOW`` or an ``ApprovalStatus.APPROVED`` approval).
- ``ActionStatus.DENIED`` follows ``AuthorizationDecision.DENY``: a hard rule, ownership,
  or security failure that approval can never override.
- ``ActionStatus.REJECTED`` and ``ApprovalStatus.REJECTED`` both mean a supervisor
  declined an action that required approval; the approval row records the decision and
  the action row records the resulting lifecycle state.
- ``PaymentStatus.REFUNDED`` describes the whole payment; an individual refund ledger row
  exists only for a verified, successful refund, so refunds carry no status at all.
- ``OrderStatus.DELIVERED`` is the order-level fact; ``ShipmentStatus.DELIVERED`` is the
  carrier-level fact for the order's single shipment.
"""

from enum import StrEnum


class Currency(StrEnum):
    """Supported settlement currency. The synthetic platform is USD-only."""

    USD = "USD"


class ActionType(StrEnum):
    REFUND = "refund"
    RETURN = "return"
    REPLACEMENT = "replacement"
    CANCELLATION = "cancellation"


class ActionStatus(StrEnum):
    """Lifecycle of a stored action. Transitions live in ``state_machine``."""

    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    """The provider outcome is unknown (for example, a timeout after commit); only
    reconciliation may resolve it, never a blind retry."""
    DENIED = "denied"
    REJECTED = "rejected"


class AuthorizationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OrderStatus(StrEnum):
    PLACED = "placed"
    """Placed and paid, not yet handed to a carrier."""
    SHIPPED = "shipped"
    """Handed to the carrier, not yet delivered."""
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    """Cancelled before shipment (cancellation is only allowed while ``PLACED``)."""


class ShipmentStatus(StrEnum):
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"


class PaymentStatus(StrEnum):
    """State of the order's single captured payment, derived from its refunded balance."""

    CAPTURED = "captured"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


class ReturnStatus(StrEnum):
    CREATED = "created"
    LABEL_CREATED = "label_created"


class ReplacementStatus(StrEnum):
    CREATED = "created"
    SHIPPED = "shipped"


class CaseStatus(StrEnum):
    """Support case status. A clarification exchange keeps the case ``OPEN``; a pending
    approval is tracked on the action, not the case."""

    OPEN = "open"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class RequestReason(StrEnum):
    """Customer-stated reason for a refund, return, replacement, or cancellation."""

    DAMAGED_ITEM = "damaged_item"
    CHANGE_OF_MIND = "change_of_mind"


class PolicyDecision(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


class PolicyReasonCode(StrEnum):
    """Machine-readable outcomes emitted by the executable policy engine."""

    ORDER_CANCELLED = "order_cancelled"
    ORDER_NOT_DELIVERED = "order_not_delivered"
    ITEM_ALREADY_REFUNDED = "item_already_refunded"
    SHIPPING_REFUNDABLE = "shipping_refundable"
    SHIPPING_NOT_REFUNDABLE = "shipping_not_refundable"
    WITHIN_RETURN_WINDOW = "within_return_window"
    RETURN_WINDOW_EXPIRED = "return_window_expired"
    ITEM_NON_RETURNABLE = "item_non_returnable"
    WITHIN_DAMAGE_WINDOW = "within_damage_window"
    DAMAGE_REPORT_WINDOW_EXPIRED = "damage_report_window_expired"
    DAMAGED_ITEM_ELIGIBLE = "damaged_item_eligible"
    DAMAGED_ITEM_EXCEPTION = "damaged_item_exception"
    """A damaged item is eligible even though it is non-returnable."""
    CANCELLATION_BEFORE_SHIPMENT = "cancellation_before_shipment"
    ORDER_ALREADY_SHIPPED = "order_already_shipped"
    ORDER_ALREADY_DELIVERED = "order_already_delivered"
    ORDER_ALREADY_CANCELLED = "order_already_cancelled"
    REPLACEMENT_REQUIRES_DAMAGE = "replacement_requires_damage"
    REPLACEMENT_IN_STOCK = "replacement_in_stock"
    REPLACEMENT_OUT_OF_STOCK = "replacement_out_of_stock"


class Role(StrEnum):
    CUSTOMER = "customer"
    SUPERVISOR = "supervisor"


class AuditActorType(StrEnum):
    SYSTEM = "system"
    CUSTOMER = "customer"
    SUPERVISOR = "supervisor"
