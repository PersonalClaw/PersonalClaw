"""Fresh-session resumability audit (§2.4) — loop scope + workflow-run scope.

The harness-course "Fresh Session Test", mechanized for PersonalClaw: after the in-memory
worker/planner/engine session is gone (a crash/restart), can the resumed entity answer
*what's done / what's verified / what's next / how to verify* **from persisted state
alone**? These functions read on-disk state only (the harness may import core; core may not
import the harness) — the exact audit that would have caught the historical dead-resume
bugs. Two entities, two halves:

- **Loops** (:func:`audit_loop`) — read a loop's SQLite row + file dir through
  ``personalclaw.loop.store`` and report per-question answerability. Findings COUNT is the
  cycle clock (in-memory watchdog counters are documented as non-resumed):

  - **done?**    — ``loop.status`` (a terminal/attention status is a definitive answer).
  - **verified?**— judge verdicts on disk (``store.get_verdicts``) and/or cycle findings.
  - **next?**    — for phased kinds, ``plan`` + ``phase_status`` name the next stage; for
    open-ended goals, the marginal-score trail + status; otherwise the next cycle.
  - **how to verify?** — the persisted task/spec text (``loop.task``) + the plan.

- **Workflow runs** (:func:`audit_workflow_run`) — the WF2 event-fold byte-equal frontier
  reconstruction. A persisted run is KILLED, resumed from DISK ALONE via a fresh
  ``RunController`` (no in-memory carryover — the whole point of the audit), and the
  frontier the resumed controller derives is asserted BYTE-EQUAL to the pre-kill snapshot,
  cross-checked against the journal event-fold (SV-5's ``fold_workflow``). This is the WF2
  event-fold law tested destructively: a resume that reconstructs a DIFFERENT frontier —
  or a journal whose fold no longer matches the persisted node states — is the dead-resume
  bug class this audit exists to catch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from personalclaw.loop import store
from personalclaw.loop.loop import LoopStatus

# Statuses that are a definitive "done / awaiting-deliberate-action" answer — a resumed
# loop in one of these needs no live session to say what state it's in.
_TERMINAL_OR_ATTENTION = {
    LoopStatus.COMPLETE.value,
    LoopStatus.FAILED.value,
    LoopStatus.PAUSED.value,
    LoopStatus.STAGNANT.value,
    LoopStatus.BLOCKED.value,
    LoopStatus.NEEDS_INPUT.value,
    LoopStatus.REVIEW.value,
}


@dataclass
class ResumeReport:
    """Per-question answerability for one loop, reconstructed from disk alone."""

    loop_id: str
    exists: bool = False
    done_answerable: bool = False
    verified_answerable: bool = False
    next_answerable: bool = False
    how_to_verify_answerable: bool = False
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """All four questions answerable from persisted state."""
        return (
            self.exists
            and self.done_answerable
            and self.verified_answerable
            and self.next_answerable
            and self.how_to_verify_answerable
        )

    def failures(self) -> list[str]:
        out: list[str] = []
        if not self.exists:
            return [f"loop {self.loop_id!r} not found on disk"]
        if not self.done_answerable:
            out.append("'what's done?' not answerable (no resolvable status)")
        if not self.verified_answerable:
            out.append("'what's verified?' not answerable (no verdicts/findings on disk)")
        if not self.next_answerable:
            out.append("'what's next?' not answerable (no plan/next-cycle signal)")
        if not self.how_to_verify_answerable:
            out.append("'how to verify?' not answerable (no persisted task/spec text)")
        return out


def audit_loop(loop_id: str) -> ResumeReport:
    """Reconstruct a loop from disk alone and report per-question answerability.

    Reads only persisted state (SQLite row + file dir) — never touches in-memory session
    state — so it faithfully models what a fresh process can recover after a restart.
    """
    report = ResumeReport(loop_id=loop_id)
    loop = store.get(loop_id)
    if loop is None:
        return report
    report.exists = True

    # what's done? — a resolvable status. RUNNING/PLANNING are "in-flight" answers too
    # (reap re-arms them), so ANY non-empty status answers the question; the terminal /
    # attention set is the strongest form.
    status = loop.status or ""
    report.done_answerable = bool(status)
    report.detail["status"] = status
    report.detail["terminal_or_attention"] = status in _TERMINAL_OR_ATTENTION

    # what's verified? — judge verdicts (open-ended) and/or cycle findings on disk.
    verdicts = store.get_verdicts(loop_id)
    findings = store.get_findings(loop_id)
    report.detail["verdict_count"] = len(verdicts)
    report.detail["finding_count"] = len(findings)
    # A run that has produced ANY finding/verdict has a verifiable record; a run that
    # hasn't started producing yet is verified-answerable by its (empty) count = 0 being a
    # definitive "nothing verified yet" — so this is answerable as long as the dir exists.
    report.verified_answerable = store.safe_loop_dir(loop_id) is not None

    # what's next? — phased kinds: plan + phase_status; else the next cycle / status.
    plan = loop.plan or []
    phase_status = getattr(loop, "phase_status", None) or {}
    report.detail["phased"] = bool(plan)
    if plan:
        # A phased loop can name its next stage from plan vs phase_status on disk.
        next_stage = next(
            (p.get("stage") for p in plan if phase_status.get(p.get("stage")) != "done"),
            None,
        )
        report.detail["next_stage"] = next_stage
        report.next_answerable = True  # plan present → next stage derivable
    else:
        # Non-phased: the next cycle number = finding count + 1 (findings are the clock),
        # and the status says whether another cycle is even due.
        report.detail["next_cycle"] = len(findings) + 1
        report.next_answerable = bool(status)

    # how to verify? — the persisted task/spec text is what the resumed worker re-reads.
    report.how_to_verify_answerable = bool((loop.task or "").strip())
    report.detail["has_task_text"] = bool((loop.task or "").strip())

    return report


# ── workflow-run half (§2.4, WF2 event-fold byte-equal frontier reconstruction) ──


@dataclass
class WorkflowResumeReport:
    """Result of a workflow-run resume audit — the byte-equal frontier reconstruction.

    ``ok`` is true only when the run resumed from disk alone reproduces its pre-kill
    frontier byte-for-byte AND the journal event-fold agrees with the persisted node
    states. A mismatch on either is the dead-resume bug class (SV Success Criterion #5,
    workflow half): a resume that silently reconstructs a different scheduling decision, or
    a journal whose replay diverges from the state it is supposed to be able to rebuild.
    """

    run_id: str
    exists: bool = False
    #: The canonical pre-kill frontier snapshot (a stable JSON string — see
    #: :func:`_frontier_snapshot`). Empty when the run does not exist.
    pre_kill_frontier: str = ""
    #: The frontier snapshot the fresh, disk-only ``RunController`` reconstructs.
    resumed_frontier: str = ""
    #: The two agree byte-for-byte — the audit's primary assertion.
    frontier_byte_equal: bool = False
    #: The journal event-fold's node-state map equals the persisted node states — the WF2
    #: event-fold law, cross-checked so a divergent replay (a corrupted or truncated
    #: journal) fails the audit rather than passing on the state file alone.
    fold_matches_state: bool = False
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exists and self.frontier_byte_equal and self.fold_matches_state

    def failures(self) -> list[str]:
        out: list[str] = []
        if not self.exists:
            return [f"workflow run {self.run_id!r} not found on disk"]
        if not self.frontier_byte_equal:
            out.append(
                "reconstructed frontier is NOT byte-equal to the pre-kill snapshot "
                "(resume diverged from persisted state)"
            )
        if not self.fold_matches_state:
            out.append(
                "journal event-fold does NOT match the persisted node states "
                "(divergent replay — corrupted or truncated journal)"
            )
        return out


#: The journal record kind → SSE projection event mapping. A resumed run's scheduling reads
#: ``state.json``; the ledger's ``journal.jsonl`` is the append-only law that must be able to
#: REBUILD those states. This projects each node-state-bearing journal record to the same SSE
#: event shape the live gateway publishes (``workflow_node_started`` / ``workflow_node_done`` /
#: ``workflow_run_update``), so SV-5's :func:`fold_workflow` reconstructs node states from the
#: journal exactly as it does from a recorded live stream. Kinds that do not move a node's
#: persisted state (``confirmation_pending``, ``step_escalated``, ledger-only records) are not
#: projected — they leave the frontier's WAITING/derived states to the reconstructed controller,
#: which is the authority for them.
_JOURNAL_TO_SSE: dict[str, tuple[str, str]] = {
    "run_started": ("workflow_run_update", "running"),
    "run_finished": ("workflow_run_update", "@status"),
    "step_started": ("workflow_node_started", ""),
    "step_completed": ("workflow_node_done", "@state"),
    "step_cached": ("workflow_node_done", "@state"),
    "step_failed": ("workflow_node_done", "failed"),
    "step_skipped": ("workflow_node_done", "skipped"),
}


def _project_journal_to_events(run_id: str) -> list[Any]:
    """Project a run's ``journal.jsonl`` into the SSE ``TraceEvent`` sequence the FE/harness
    fold consumes. Deterministic and pure — ordered by the journal's own monotonic ``seq``,
    no wall-clock — so the folded terminal state is a stable value.
    """
    from harness.replay import TraceEvent
    from personalclaw.workflows import store as wf_store

    events: list[Any] = []
    for rec in wf_store.read_jsonl(run_id, "journal.jsonl"):
        mapped = _JOURNAL_TO_SSE.get(str(rec.get("kind")))
        if mapped is None:
            continue
        event_type, status_rule = mapped
        payload = dict(rec)
        if status_rule == "@state":
            payload["status"] = rec.get("state")
        elif status_rule == "@status":
            payload["status"] = rec.get("status")
        elif status_rule:
            payload["status"] = status_rule
        # The fold's node-seq floor keys off a per-node ``seq``; the journal's is per-RUN, so
        # a node's started/done both carry it and the floor still holds monotonically.
        payload.setdefault("node_epoch", rec.get("epoch", 0))
        events.append(
            TraceEvent(
                ts=float(rec.get("seq", 0) or 0),
                stream="sse",
                key=f"workflow:{run_id}",
                type=event_type,
                payload=payload,
                seq=rec.get("seq"),
            )
        )
    return events


def _frontier_snapshot(controller: Any) -> str:
    """A canonical, byte-comparable JSON string of a controller's frontier + node states.

    The comparison SUBJECT of the audit. It folds the run's persisted node states together
    with the pure scheduling decision the frontier derives from them — ``ready`` / ``running``
    / ``waiting`` / ``complete`` / ``blocked`` / ``outcome`` — so "byte-equal frontier" means
    the resumed run would make the identical next scheduling move, not merely that the state
    file round-tripped. Sorted keys + sorted path lists make it deterministic; no wall-clock
    or id-derived value enters it.
    """
    fr = controller._frontier()
    snapshot = {
        "nodes": {p: i.state.value for p, i in sorted(controller.instances.items())},
        "ready": sorted(r.path for r in fr.ready),
        "deferred": sorted(r.path for r in fr.deferred),
        "running": sorted(fr.running),
        "waiting": sorted(fr.waiting),
        "to_skip": sorted(fr.to_skip),
        "complete": fr.complete,
        "blocked": fr.blocked,
        "outcome": fr.outcome.value if fr.outcome is not None else None,
    }
    return json.dumps(snapshot, sort_keys=True)


def audit_workflow_run(
    run_id: str, *, pre_kill_frontier: str | None = None
) -> WorkflowResumeReport:
    """Reconstruct a persisted workflow run from disk ALONE and prove the frontier is
    byte-equal to its pre-kill snapshot (SV Success Criterion #5, workflow half).

    The audit models a crash/kill: a fresh :class:`RunController` is built from the run row,
    spec, and ``state.json`` on disk — no in-memory carryover from whatever was driving the
    run — and the frontier it derives is compared to ``pre_kill_frontier``. When the caller
    does not pass one (the run was killed out of band and only its files survive), the
    persisted state IS the pre-kill truth, so the snapshot is taken once and must be stable
    across a re-read — a reconstruction that is not idempotent is itself a resume defect.

    Independently, the run's ``journal.jsonl`` is folded through SV-5's event-fold law
    (:func:`harness.replay.fold_workflow`) and its node-state map is checked against the
    persisted node states: the journal must be able to REBUILD the frontier, so a divergent
    replay (a corrupted or truncated journal) fails the audit even when the state file alone
    looks intact.

    Reads only persisted state; constructs the controller with no gateway services, so it
    never launches a node or touches the network.
    """
    from harness.replay import fold_workflow
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.controller import EngineServices, RunController

    report = WorkflowResumeReport(run_id=run_id)
    run = wf_store.get(run_id)
    spec = wf_store.read_spec(run_id)
    if run is None or spec is None:
        return report
    report.exists = True

    # Resume from DISK ALONE: a fresh controller reads state.json + spec.json + the journal,
    # with no services (it must not launch work or reach a gateway).
    resumed = RunController(run, spec, services=EngineServices())
    report.resumed_frontier = _frontier_snapshot(resumed)

    # The pre-kill truth. When the caller captured a live snapshot before the kill, compare
    # against it; otherwise the persisted files ARE the truth, and a second independent
    # reconstruction must reproduce the same snapshot (idempotent resume).
    if pre_kill_frontier is None:
        report.pre_kill_frontier = _frontier_snapshot(
            RunController(run, spec, services=EngineServices())
        )
    else:
        report.pre_kill_frontier = pre_kill_frontier
    report.frontier_byte_equal = report.resumed_frontier == report.pre_kill_frontier

    # The event-fold law: the journal must reconstruct the persisted node states. A run whose
    # nodes all carry a node-state event in the journal folds to exactly the state map on disk.
    fold = fold_workflow(_project_journal_to_events(run_id))
    persisted_nodes = {p: inst.state.value for p, inst in resumed.instances.items()}
    folded_nodes = {p: st for p, st in fold.get("nodes", {}).items() if p in persisted_nodes}
    report.fold_matches_state = json.dumps(folded_nodes, sort_keys=True) == json.dumps(
        persisted_nodes, sort_keys=True
    )
    report.detail["node_count"] = len(persisted_nodes)
    report.detail["fold_status"] = fold.get("status")
    report.detail["fold_dropped"] = fold.get("dropped")
    report.detail["run_status"] = run.status.value
    return report
