---
policy_key: returns
version: 1
title: Returns Policy
effective_from: 2026-01-01T00:00:00Z
ruleset_version: synthetic-p1-2026.11
synthetic: true
---

# Returns Policy

> Synthetic policy for a fictional demo store; not the policy of any real company.

This policy covers change-of-mind requests: the item is not damaged, but the customer no
longer wants it. Damaged items follow the Damaged Items Policy instead.

## RET-3.1 Change-of-mind requests within 30 days of delivery

A change-of-mind return or refund must be requested within 30 days of delivery. Days are
counted as UTC calendar days from the delivery date: the delivery date is day 0, day 30 is
the last eligible day, and from day 31 onward the request is outside the return window. The
time of day of delivery does not matter, only the calendar date.

## RET-3.2 Non-returnable items are final sale

Items marked non-returnable (for example, clearance products) cannot be returned or refunded
for a change of mind, even inside the return window. This does not apply to damaged items;
see DMG-4.2 in the Damaged Items Policy.
