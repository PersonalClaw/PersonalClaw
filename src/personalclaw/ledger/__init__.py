"""The ledger — an append-only, redacted, deterministically-stamped event log, as a platform
primitive rather than a workflow feature.

The mechanism was born inside the workflow engine and every property worth having is already
there: append-only, `redact()` before every write, a deterministic `event_id` (`<run>-evt-<seq>`)
so a re-emit is an idempotent no-op, a monotonic `seq`, epoch stamping, a 64KB/binary spill to a
typed `result_omitted` stub, and one file read two ways (a resume cache and the Run Ledger the
Learning Flywheel's refiner reads). What it did NOT have was a second producer: loops, tasks and
triggers each kept their own partial record in their own shape, so the outer improvement loop could
only learn from workflow runs.

This package is the mechanism with the workflow flavour taken out:

* :mod:`~personalclaw.ledger.kinds` — the shared event vocabulary. ONE registry, so a second
  producer speaks the same words instead of adding a dialect.
* :mod:`~personalclaw.ledger.writer` — `LedgerWriter`: sequencing, stamping, redaction, the
  `events.jsonl` mirror, the oversize/binary spill.
* :mod:`~personalclaw.ledger.redaction` — what must never reach the file, and what must never sit
  inline in it.
* :mod:`~personalclaw.ledger.hashing` — canonical serialization and content hashing.
* :mod:`~personalclaw.ledger.reader` — reading it back.
* :mod:`~personalclaw.ledger.outcomes` — the outcome record: any producer's bet on what would
  LAND, and the idempotent, `measured`-vs-`inconclusive` resolution that closes it (PP-9).

Nothing here imports from `personalclaw.workflows`, and nothing here may: the direction of that
dependency is the whole point. A producer supplies its own :class:`~personalclaw.ledger.writer
.LedgerStore` and its own typed emitters; `personalclaw.workflows.journal` is the first such
producer and, for now, the only one.
"""

from __future__ import annotations

from personalclaw.ledger.hashing import hash_value, stable_json
from personalclaw.ledger.kinds import (
    BREAKER_TRIP,
    BUFFER_SEAL,
    CARRYOVER,
    CASCADE_BLOCKED,
    CHILD_RUN_ATTACH,
    CONFIRMATION_PENDING,
    CONFIRMATION_RESOLVED,
    CONSULTED,
    CRYSTALLIZED,
    DECISION,
    DELAY_CLAMPED,
    EFFECT,
    GATE_CRITERION,
    GATE_REJECTED,
    GATE_RESOLVED,
    GATE_REVISED,
    HANDOFF,
    INPUTS_STALE,
    ITEMS_COLLECTED,
    ITERATION,
    JUDGE_DIVERGENCE,
    JUDGE_VERDICT,
    LEDGER_KINDS,
    MUTATION_REJECTED,
    OUTCOME_RESOLVED,
    PENDING_OUTCOME,
    RUN_ABANDONED,
    RUN_FINISHED,
    RUN_STARTED,
    SEEN_SET,
    STEERING,
    STEP_ATTEMPT,
    STEP_CACHED,
    STEP_COMPLETED,
    STEP_ESCALATED,
    STEP_FAILED,
    STEP_SCOPE,
    STEP_SKIPPED,
    STEP_STARTED,
    TASK_MATERIALIZED,
    TASK_VERIFIED,
    USER_EDITED_MID_FLIGHT,
    WATCHER_REAPED,
    WORKSPACE_PROVISIONED,
    WORKSPACE_TEARDOWN,
)
from personalclaw.ledger.outcomes import (
    INCONCLUSIVE,
    MEASURED,
    PRODUCER_CONTROL,
    PRODUCER_DECISION,
    PRODUCER_ESCALATION,
    PRODUCER_PROPOSAL,
    PRODUCER_PUBLISH,
    PRODUCERS,
    SOURCE_LEDGER,
    SOURCE_MEMORY,
    OutcomeLedger,
    OutcomeQuestion,
    open_questions,
)
from personalclaw.ledger.reader import read_events, run_totals
from personalclaw.ledger.redaction import is_binary_payload, redact
from personalclaw.ledger.writer import (
    EVENTS_FILE,
    JOURNAL_FILE,
    MAX_INLINE_OUTPUT_BYTES,
    LedgerStore,
    LedgerWriter,
    now,
)

__all__ = [
    # machinery
    "LedgerStore",
    "LedgerWriter",
    "EVENTS_FILE",
    "JOURNAL_FILE",
    "MAX_INLINE_OUTPUT_BYTES",
    "now",
    "redact",
    "is_binary_payload",
    "hash_value",
    "stable_json",
    "read_events",
    "run_totals",
    # outcomes (PP-9)
    "OutcomeLedger",
    "OutcomeQuestion",
    "open_questions",
    "MEASURED",
    "INCONCLUSIVE",
    "PRODUCERS",
    "PRODUCER_CONTROL",
    "PRODUCER_DECISION",
    "PRODUCER_ESCALATION",
    "PRODUCER_PROPOSAL",
    "PRODUCER_PUBLISH",
    "SOURCE_LEDGER",
    "SOURCE_MEMORY",
    # vocabulary
    "LEDGER_KINDS",
    "BREAKER_TRIP",
    "BUFFER_SEAL",
    "CARRYOVER",
    "CASCADE_BLOCKED",
    "CHILD_RUN_ATTACH",
    "CONFIRMATION_PENDING",
    "CONFIRMATION_RESOLVED",
    "CONSULTED",
    "CRYSTALLIZED",
    "DECISION",
    "DELAY_CLAMPED",
    "EFFECT",
    "GATE_CRITERION",
    "GATE_REJECTED",
    "GATE_RESOLVED",
    "GATE_REVISED",
    "HANDOFF",
    "INPUTS_STALE",
    "ITEMS_COLLECTED",
    "ITERATION",
    "JUDGE_DIVERGENCE",
    "JUDGE_VERDICT",
    "MUTATION_REJECTED",
    "OUTCOME_RESOLVED",
    "PENDING_OUTCOME",
    "RUN_ABANDONED",
    "RUN_FINISHED",
    "RUN_STARTED",
    "SEEN_SET",
    "STEERING",
    "STEP_ATTEMPT",
    "STEP_CACHED",
    "STEP_COMPLETED",
    "STEP_ESCALATED",
    "STEP_FAILED",
    "STEP_SCOPE",
    "STEP_SKIPPED",
    "STEP_STARTED",
    "TASK_MATERIALIZED",
    "TASK_VERIFIED",
    "USER_EDITED_MID_FLIGHT",
    "WATCHER_REAPED",
    "WORKSPACE_PROVISIONED",
    "WORKSPACE_TEARDOWN",
]
