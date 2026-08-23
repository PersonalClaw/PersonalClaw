"""Reading the ledger back — the queries every producer's consumers ask.

Pass-rate, failure distribution and latency percentiles are queries over the event file, not a
separate metrics store. Keeping them here rather than beside one producer is what stops the second
producer from growing its own counter with its own subtly different definition of "a completed
step".
"""

from __future__ import annotations

from typing import Any

from personalclaw.ledger.kinds import LEDGER_KINDS, STEP_CACHED, STEP_COMPLETED, STEP_FAILED
from personalclaw.ledger.writer import EVENTS_FILE, JOURNAL_FILE, LedgerStore


def read_events(
    store: LedgerStore, run_id: str, *, kinds: set[str] | None = None
) -> list[dict[str, Any]]:
    """Read the ledger, optionally filtered. Pass-rate, failure distribution and
    latency percentiles are queries over this — not a separate metrics store."""
    records = store.read_jsonl(run_id, EVENTS_FILE)
    if kinds is None:
        return records
    return [r for r in records if r.get("kind") in kinds]


def read_journal(
    store: LedgerStore, run_id: str, *, kinds: set[str] | None = None
) -> list[dict[str, Any]]:
    """Read `journal.jsonl` — the SUPERSET, for the kinds `events.jsonl` never mirrors.

    :data:`~personalclaw.ledger.kinds.LEDGER_KINDS` is a SUBSET of the vocabulary: the writer
    mirrors only those kinds into `events.jsonl`, so `run_started` and `run_finished` — the two
    records that carry a run's INPUTS and its final status — are invisible to
    :func:`read_events`. A consumer that needs them (replaying a run, harvesting it into a
    regression case) has to read the journal, and it reads it through here rather than reaching
    into the store: `read_jsonl` is a file call with no vocabulary attached, and a second caller
    doing its own filtering is how "which file holds `run_started`" becomes two answers.

    Both records still went through the writer's `redact()`, which is why a harvester reading
    inputs from HERE inherits redaction and one reading them off the run row does not.
    """
    records = store.read_jsonl(run_id, JOURNAL_FILE)
    if kinds is None:
        return records
    return [r for r in records if r.get("kind") in kinds]


def journal_only_kinds(kinds: set[str]) -> frozenset[str]:
    """The subset of `kinds` that :func:`read_events` can NEVER return.

    Exists so a consumer can assert its own event vocabulary against the mirror rather than
    discovering the gap as an empty query: a kind outside :data:`LEDGER_KINDS` is journal-only,
    and asking `events.jsonl` for it returns `[]` that looks exactly like "the run never did
    that".
    """
    return frozenset(k for k in kinds if k not in LEDGER_KINDS)


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
