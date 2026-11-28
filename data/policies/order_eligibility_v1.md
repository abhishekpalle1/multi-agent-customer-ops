---
policy_key: order_eligibility
version: 1
title: Order Eligibility Policy
effective_from: 2026-01-01T00:00:00Z
ruleset_version: synthetic-p1-2026.11
synthetic: true
---

# Order Eligibility Policy

> Synthetic policy for a fictional demo store; not the policy of any real company.

These rules decide whether an order's items can be considered for a refund, return, or
replacement at all. They are checked before any item-level rule. Order cancellation is
covered separately by the Cancellation Policy.

## ORD-1.1 Cancelled orders are not eligible

Items on an order that has been cancelled cannot be refunded, returned, or replaced. A
cancelled order was never shipped, and its payment was reversed when it was cancelled.

## ORD-1.2 Items must be delivered first

Refunds, returns, and replacements are available only after the order has been delivered.
While an order is still being prepared or is in transit, item requests are not eligible yet;
the customer can ask again after delivery.
