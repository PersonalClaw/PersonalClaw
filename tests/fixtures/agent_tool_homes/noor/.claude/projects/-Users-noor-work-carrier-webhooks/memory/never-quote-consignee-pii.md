---
name: Never quote consignee PII
description: strip consignee names, addresses and phone numbers before anything leaves the laptop
metadata:
  node_type: memory
  type: feedback
  originSessionId: 17152948-5b79-4fd8-abf0-5e51dcfd2ade
  modified: 2026-08-18
---

Noor pastes carrier logs and payloads. Consignee names, street addresses and phone numbers must be replaced with `<consignee>` in anything quoted back, in PR text, issues and commit messages.

**Why:** customer PII; Cartwheel policy and her own rule.
**How to apply:** tracking numbers are fine; names and addresses are not.
