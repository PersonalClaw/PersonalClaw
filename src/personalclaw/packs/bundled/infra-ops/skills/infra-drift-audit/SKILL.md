---
name: infra-drift-audit
description: Compare declared infrastructure against what a state refresh reports, and list the divergences without resolving them.
---

# Audit infrastructure drift

## Steps

1. Ask which working directory holds the configuration, and confirm it contains `.tf` files
   before going further.
2. Read the declared resources from the configuration. Read the observed resources from a state
   refresh output the user supplies. Never assume one from the other.
3. Report three lists, in this order: declared but absent, present but undeclared, and present
   with differing attributes. Include the resource address in every entry.
4. For the third list, name each differing attribute with its declared and observed values side
   by side. An entry with no named attribute is not a drift finding — leave it out.

## What not to do

- Do not import, taint or remove anything from state. This skill reports divergence; changing
  state is a human decision made after reading the report.
- Do not treat a formatting difference as drift.
- Do not report a total drift count without the per-resource lists behind it.
