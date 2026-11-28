---
policy_key: refunds
version: 1
title: Refund Policy
effective_from: 2026-01-01T00:00:00Z
ruleset_version: synthetic-p1-2026.11
synthetic: true
---

# Refund Policy

> Synthetic policy for a fictional demo store; not the policy of any real company.

A refund returns what the customer paid for the selected units: the item price, less the
share of any discount applied to those units, plus the share of tax charged on them.
Discount and tax shares for a partial quantity are prorated to the cent. A refund can never
exceed what is still unrefunded on an item line. Whether a refund is allowed at all is
decided by the Returns Policy (change of mind) or the Damaged Items Policy (damage).

## RFD-2.1 Fully refunded items are not eligible

An item line whose every unit has already been refunded cannot be refunded, returned, or
replaced again. A line with some units still unrefunded remains eligible for those units.

Earlier amount-only adjustments (for example, a goodwill credit that did not return any
units) reduce how much of the line can still be refunded, so a later refund for the same
line is limited to the amount that remains.

## RFD-2.2 Shipping is refunded only for damaged items

The order's original shipping charge, or whatever part of it has not already been refunded,
is included in a refund only when the refund is for a damaged item and every selected item
is eligible. Change-of-mind refunds never include shipping.

This rule governs refund requests. Reversing the whole payment of an order that is cancelled
before shipment is part of cancellation (see the Cancellation Policy), not a refund request.
