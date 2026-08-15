"""The loop's ledger — the SECOND producer of the platform ledger (PP-5).

`workflows/journal.py` made the workflow engine the first producer of
:mod:`personalclaw.ledger`; this module makes the loop engine the second. Every property is
inherited from :class:`~personalclaw.ledger.writer.LedgerWriter` — append-only, `redact()` on
every write, deterministic `event_id`, the `events.jsonl` mirror — and the only loop-shaped thing
here is the four typed emitters and the store binding.

Why this exists (PP-5). `learning/mining.py` derives from *"the run's own journal"*, so the outer
improvement loop learned from workflow runs and nothing else — the loop kinds, which carry the
long-horizon autonomous work, were a blind spot because their findings/verdicts lived in their own
file store with no event vocabulary. A loop that emits the SAME kinds a workflow does
(`step_started`/`step_completed`, `judge_verdict`, `breaker_trip`, `watcher_reaped`) is visible to
the flywheel through the same reader.

The vocabulary is imported from :mod:`personalclaw.ledger`, never re-minted: a loop that emitted
its own `cycle_finished` would be the fifth dialect the extraction (PP-4) and the verdict
reconciliation (WF2LOO-16) exist to prevent. `judge_verdict` in particular carries the reconciled
:class:`~personalclaw.workflows.judge_contract.JudgeVerdict` shape — the loop no longer has a
private verdict dialect.

Boundary: this is a LOOP-side thing that writes THROUGH the ledger primitive. `personalclaw.ledger`
does not, and may not, import `personalclaw.loop` — the direction is the whole point (the rail
`test_the_ledger_package_does_not_import_the_workflow_engine` guards it). `loop.store` is the
:class:`~personalclaw.ledger.writer.LedgerStore` this ledger appends through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

# The vocabulary + machinery are re-exported so a reader (and `learning.mining`, passed this module
# as its `journal=`) can read the loop ledger through the same names the workflow ledger uses.
from personalclaw.ledger import (  # noqa: F401 — re-exported for this module's importers
    BREAKER_TRIP,
    EVENTS_FILE,
    JOURNAL_FILE,
    JUDGE_VERDICT,
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_STARTED,
    WATCHER_REAPED,
    LedgerStore,
    LedgerWriter,
    read_events,
)
from personalclaw.loop import store as loop_store

#: Ledger meta keys the writer stamps on every record. Stripped when a projection reconstructs the
#: producer's own payload, so `get_verdicts` returns the verdict dict a kind persisted, not the dict
#: plus four bookkeeping fields the kind never wrote.
_META_KEYS = frozenset({"kind", "ts", "seq", "event_id"})


def _node_id(finding: dict[str, Any], cycle: int) -> str:
    """The step identity a cycle's finding advertises.

    A kind with named phases (code/sdlc `stage`, design `step`) has a STABLE per-cycle label, and a
    recurring sequence of them is exactly what `mining.positive_path_candidates` clusters. A kind
    without one (a plain goal cycle) has no per-cycle step structure, so it reports the constant
    ``"cycle"`` rather than the ordinal — an ordinal would mint a distinct node per cycle and make
    every loop look like it took a unique path, which is worse than reporting no structure.
    """
    for key in ("stage", "step", "node_id"):
        val = str(finding.get(key) or "").strip()
        if val:
            return val
    return "cycle"


@dataclass
class LoopJournal(LedgerWriter):
    """The loop engine's ledger: the shared writer plus the loop's four typed emitters.

    Constructed per emit through :meth:`open`, which recovers `seq` from the existing journal so a
    poll (or a restart) never re-mints an `event_id` the file already holds — the property that
    makes a re-emit an idempotent no-op.
    """

    #: The loop store owns `loop/<id>/`, so it is what this ledger appends through.
    _store: ClassVar[LedgerStore] = loop_store  # type: ignore[assignment]

    @classmethod
    def open(cls, loop_id: str) -> "LoopJournal":
        """Build a journal for `loop_id`, seq recovered from its existing journal."""
        j = cls(run_id=loop_id)
        for rec in loop_store.read_jsonl(loop_id, JOURNAL_FILE):
            j.seq = max(j.seq, int(rec.get("seq", 0) or 0))
        return j

    # ── the four PP-5 emit points ──

    def cycle(self, cycle: int, finding: dict[str, Any]) -> None:
        """One loop cycle — the worker produced a finding. `step_started` + `step_completed`.

        The finding dict is carried whole so `store.get_findings` can PROJECT it back off the
        ledger rather than glob a parallel file store. `source_file` keys the ingest idempotently:
        a poll that re-reads a finding already ledgered skips it.
        """
        node_id = _node_id(finding, cycle)
        task_id = str(finding.get("task_id") or "")
        source_file = str(finding.get("_source_file") or "")
        self.write(STEP_STARTED, cycle=cycle, node_id=node_id, task_id=task_id)
        self.write(
            STEP_COMPLETED,
            cycle=cycle,
            node_id=node_id,
            task_id=task_id,
            source_file=source_file,
            finding=finding,
        )

    def verdict(self, verdict: dict[str, Any]) -> None:
        """A supervisor/judge assessment — `judge_verdict` in the reconciled vocabulary.

        `verdict` is `{"cycle": n, **JudgeVerdict.to_dict()}` (WF2LOO-16), so the ledger record
        carries the reconciled keys (`verdict`, `done`, `marginal_value`, `quality_score`, …) at
        top level — the loop no longer speaks a private verdict dialect.
        """
        self.write(JUDGE_VERDICT, **verdict)

    def breaker_trip(self, cycle: int, reason: str) -> None:
        """A stall — the supervisor cut the loop off. Same kind the workflow breaker emits."""
        self.write(BREAKER_TRIP, cycle=cycle, reason=reason)

    def watcher_reaped(self, *, cycles: int, reason: str) -> None:
        """A reap — a running watcher's worker was cut off before its cadence completed.

        A `watcher_reaped` records that a long-run loop produced fewer cycles than its cadence
        implies, so a refiner reading cycle counts does not conclude the template under-performed.
        """
        self.write(WATCHER_REAPED, cycles=int(cycles), reason=reason)


# ── read side (the projection + the mining reader) ──


def ledger(loop_id: str, *, kinds: set[str] | None = None) -> list[dict[str, Any]]:
    """Read a loop's ledger back, optionally filtered by kind.

    The read half of the same file the emitters write. `learning.mining` is handed THIS module as
    its `journal=` and calls exactly this, so a loop run is mined through the same reader a
    workflow run is.
    """
    return read_events(loop_store, loop_id, kinds=kinds)


def strip_meta(record: dict[str, Any]) -> dict[str, Any]:
    """A ledger record with the writer's bookkeeping fields removed — the producer's own payload."""
    return {k: v for k, v in record.items() if k not in _META_KEYS}
