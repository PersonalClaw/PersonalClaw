---
name: Dedupe lives in Postgres, not the LRU
description: carrier-webhooks deduplicates through the event_dedupe table since Aug 18; the in-process LRU is only a fast path
metadata:
  node_type: memory
  type: project
  originSessionId: 17152948-5b79-4fd8-abf0-5e51dcfd2ade
  modified: 2026-08-18
---

Since 2026-08-18 (fix/dedupe-rebalance) carrier-webhooks claims every dedupe key in the `event_dedupe` table with `INSERT ... ON CONFLICT DO NOTHING RETURNING 1`. The LRU in `ingest/handlers.py` is a fast path only; never rely on it for correctness. A failed publish releases its key.

**Why:** the 2026-08-17 rebalance incident (1,412 duplicate SMS) came from a per-pod cache.
**How to apply:** any new consumer that emits side effects claims its key in Postgres first.
