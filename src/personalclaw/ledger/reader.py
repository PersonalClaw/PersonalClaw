"""Reading the ledger back — the queries every producer's consumers ask.

Pass-rate, failure distribution and latency percentiles are queries over the event file, not a
separate metrics store. Keeping them here rather than beside one producer is what stops the second
producer from growing its own counter with its own subtly different definition of "a completed
step".
"""

from __future__ import annotations

from typing import Any

from personalclaw.ledger.kinds import STEP_CACHED, STEP_COMPLETED, STEP_FAILED
from personalclaw.ledger.writer import EVENTS_FILE, LedgerStore


def read_events(
    store: LedgerStore, run_id: str, *, kinds: set[str] | None = None
) -> list[dict[str, Any]]:
    """Read the ledger, optionally filtered. Pass-rate, failure distribution and
    latency percentiles are queries over this — not a separate metrics store."""
    records = store.read_jsonl(run_id, EVENTS_FILE)
    if kinds is None:
        return records
    return [r for r in records if r.get("kind") in kinds]


def run_totals(store: LedgerStore, run_id: str) -> dict[str, Any]:
    """Aggregate a run's ledger into the counters the run row carries.

    Budgets are PRE-CHARGED from this on resume (WF2-R4 invariant #1): a resumed run
    must inherit what it already spent, or a crash loop becomes an unbounded spend.
    """
    tokens = 0
    cost = 0.0
    steps = 0
    failures = 0
    cached = 0
    for rec in store.read_jsonl(run_id, EVENTS_FILE):
        kind = rec.get("kind")
        if kind == STEP_COMPLETED:
            steps += 1
            tokens += int(rec.get("tokens", 0) or 0)
            cost += float(rec.get("cost_usd", 0.0) or 0.0)
        elif kind == STEP_FAILED:
            failures += 1
        elif kind == STEP_CACHED:
            cached += 1
    return {
        "tokens": tokens,
        "cost_usd": round(cost, 6),
        "steps_completed": steps,
        "steps_failed": failures,
        "steps_cached": cached,
    }
