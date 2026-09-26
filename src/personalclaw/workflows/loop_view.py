"""The LOOP view of a run started as a loop — the read half of PP-16's noun change.

`loop_run_map` DECLARES where every `Loop` field lives once a loop is a `WorkflowRun`. This module
reads a run back OUT in the loop wire shape, so a run-backed loop is listed, opened and controlled
on the same surfaces as a loops-table row: ONE listing of every loop, whatever backs it.

🔴 Why it exists. A ported kind (`general`) stopped writing a loops row and started a run, and every
loop surface kept reading the loops table alone: measured 2026-09-25, a General loop was working
while the Loops list said "No loops yet", Home said "0 loops running" and "No active work", and
Mission Control's Working lane said "Nothing is running" — only Agent world, which reads subagents,
noticed it. A run-backed loop IS a loop; the surfaces were simply looking in one of its two homes.

**The discriminator is `run_id`.** Present on a projected row and absent on a loops-table row, so a
surface decides by the BODY it holds (where the cockpit is, which lifecycle it has) rather than by
the kind — which kinds are run-backed is `service.PORTED_LOOP_KINDS` to decide, and it grows.

**Statuses map onto the loop vocabulary, not a copy of it.** The wire union is railed equal to
`LoopStatus` (`tests/test_loop_status_vocabulary.py`), so a run's status is projected onto the
nearest loop state that tells the truth about it — see `_STATUS`. What that costs is stated there
rather than hidden, and the ACTIONS a run supports are a separate, narrower table
(:data:`RUN_ACTION_SOURCE_STATES`), because a run resumes only from a pause.
"""

from __future__ import annotations

import calendar
import logging
import time
from typing import Any

from personalclaw.loop.loop import LoopStatus, LoopStopReason
from personalclaw.workflows import introspection
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import loop_aliases, store, supervisor_policy
from personalclaw.workflows.models import Node, NodeKind, RunStatus, WorkflowRun

logger = logging.getLogger(__name__)

#: A run's status → the loop status a loop surface reads. EXHAUSTIVE over `RunStatus` (railed), so a
#: new run state cannot reach the loop list unnamed.
#:
#: Two rows are projections rather than renames, and say what they cost:
#:
#: * ``cancelled`` → ``stopped`` (stop reason ``user``). A run is only ever cancelled by a person.
#: * ``escalated`` → ``complete`` with a non-``done`` stop reason, which every loop surface already
#:   renders "Ended early": the run stopped before its done condition and a human decides what
#:   happens next. The loop vocabulary has no terminal "needs a decision" state, and borrowing an
#:   ATTENTION state (``blocked``/``needs_input``) would offer a Resume a finished run cannot
#:   honour. The run page, which the row opens, carries the decision itself.
_STATUS: dict[RunStatus, LoopStatus] = {
    RunStatus.DRAFT: LoopStatus.READY,
    RunStatus.RUNNING: LoopStatus.RUNNING,
    RunStatus.PAUSED: LoopStatus.PAUSED,
    RunStatus.NEEDS_INPUT: LoopStatus.NEEDS_INPUT,
    RunStatus.COMPLETE: LoopStatus.COMPLETE,
    RunStatus.FAILED: LoopStatus.FAILED,
    RunStatus.CANCELLED: LoopStatus.STOPPED,
    RunStatus.ESCALATED: LoopStatus.COMPLETE,
}

#: The escalation reasons that mean the loop spent its cycle budget, as opposed to giving up on the
#: work (`controller._BUDGET_TRIPS` — the one token the engine surfaces a satisfied budget with).
_BUDGET_REASONS = frozenset({"max_iterations"})

#: What each lifecycle action may be invoked FROM on a RUN-BACKED loop, in the projected loop
#: statuses. Narrower than `loop.loop:ACTION_SOURCE_STATES`, and it has to be: a run has one
#: attempt, so it cannot resume from ``failed`` (terminal on the run side), and a run waiting on a
#: gate is answered on its run page — a bare "resume" there would clear nothing and report success.
#: Mirrored by `web/src/lib/loopStatus.ts:RUN_LOOP_ACTION_SOURCE_STATUSES` and railed equal.
RUN_ACTION_SOURCE_STATES: dict[str, frozenset[LoopStatus]] = {
    "start": frozenset({LoopStatus.READY}),
    "pause": frozenset({LoopStatus.RUNNING}),
    "resume": frozenset({LoopStatus.PAUSED}),
    "stop": frozenset(
        {LoopStatus.READY, LoopStatus.RUNNING, LoopStatus.PAUSED, LoopStatus.NEEDS_INPUT}
    ),
}


def loop_status(run: WorkflowRun) -> LoopStatus:
    """The loop status a surface reads for ``run`` (see :data:`_STATUS`)."""
    return _STATUS[run.status]


def _redact(text: str | None) -> str | None:
    """The redaction a loops-table row's free text gets (`loop.store._redact_loop`), so the two
    halves of one listing cannot disagree about what a task that quoted a secret shows."""
    if not text:
        return text
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    cleaned, _ = redact_credentials(text)
    cleaned, _ = redact_exfiltration_urls(cleaned)
    return cleaned


def _epoch(ts: str | None) -> float | None:
    """A run's ISO-8601 ``...Z`` stamp as the epoch seconds the loop wire shape carries.

    ``calendar.timegm`` for the reason `controller._epoch` records (``mktime`` reads the struct as
    LOCAL time). ``None`` for an absent or unreadable stamp: the loop shape types ``started_at`` and
    ``completed_at`` nullable, and a 0 there would render as 1970.
    """
    if not ts:
        return None
    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def _stop_reason(run: WorkflowRun) -> str:
    """The closed `LoopStopReason` a projected ENDED row carries; ``""`` while it has not ended."""
    if run.status == RunStatus.COMPLETE:
        return LoopStopReason.DONE.value
    if run.status == RunStatus.CANCELLED:
        return LoopStopReason.USER.value
    if run.status == RunStatus.ESCALATED:
        reason = str((run.attention or {}).get("reason") or "")
        return (
            LoopStopReason.CYCLE_BUDGET.value
            if reason in _BUDGET_REASONS
            else LoopStopReason.WORKER_FAILED.value
        )
    if run.status == RunStatus.FAILED:
        return LoopStopReason.WORKER_FAILED.value
    return ""


def _error_message(run: WorkflowRun) -> str | None:
    """Why an ended run ended, in the engine's own words: its error, else its escalation detail."""
    if run.error_message:
        return run.error_message
    if run.status == RunStatus.ESCALATED:
        attention = run.attention or {}
        detail = str(attention.get("detail") or attention.get("reason") or "").strip()
        return detail or None
    return None


def _loop_root(spec: dict[str, Any] | None) -> tuple[str, Node] | None:
    """The run's loop node and its instance path, when its template's root IS a loop.

    Every ported template's cycles are its ROOT loop's iterations (`general-project`'s root is the
    loop). A template whose root is not a loop has no cycle count to report, which is the honest
    reading — not a zero pretending to be one.
    """
    if not isinstance(spec, dict):
        return None
    try:
        root = Node.from_dict(spec.get("root") or {})
    except ValueError:
        return None
    return ("root", root) if root.kind == NodeKind.LOOP else None


def _cycles_completed(run_id: str, loop_path: str) -> int:
    """How many iterations of the loop at ``loop_path`` have finished — the run's cycle count.

    Read off the ledger's ``iteration`` rows, which `RunController._advance_loop` writes as each
    iteration ends. DISTINCT iteration indexes, because a tripped breaker writes a second row for
    the same iteration (``breaker:<reason>`` and then the continue decision), and counting rows
    would report one iteration as two.
    """
    try:
        rows = journal_mod.ledger(run_id, kinds={journal_mod.ITERATION})
    except Exception:
        logger.debug("loop view: iteration read failed for %s", run_id, exc_info=True)
        return 0
    seen = {
        int(r.get("iteration", -1))
        for r in rows
        if str(r.get("instance_path") or "") == loop_path and isinstance(r.get("iteration"), int)
    }
    return len({i for i in seen if i >= 0})


def run_loop_view(run: WorkflowRun) -> dict[str, Any]:
    """``run`` in the loop wire shape (`web/src/lib/api.ts:Loop`), plus the ``run_id`` marker.

    Every field is either read off the run or honestly empty: a run has no worker chat session, no
    findings files and no kind_config, so those arrive as their empty values rather than invented
    ones. The cycle budget is the run's own ``max_cycles`` override when it has one, else the
    template's declared ``max_iterations`` — the number that ACTUALLY bounds the run, so a list
    reading "cycle 3/30" means 30 is what the engine will stop at.
    """
    spec = store.read_spec(run.id)
    intake = loop_aliases.template_intake(spec)
    loop = _loop_root(spec)
    cap = supervisor_policy.loop_iteration_cap(run.policy_overrides)
    if not cap and loop is not None:
        declared = (loop[1].config or {}).get("max_iterations")
        cap = declared if isinstance(declared, int) and declared > 0 else 0
    task = str(run.inputs.get(intake.get("task", ""), "") or "")
    criterion = str(run.inputs.get(intake.get("success_criteria", ""), "") or "")
    status = loop_status(run)
    return {
        "id": run.id,
        "run_id": run.id,
        "kind": run.loop_kind,
        "name": _redact(run.title or task[:60] or run.workflow_name),
        "task": _redact(task),
        "summary": _redact(run.intent),
        "status": status.value,
        "stop_reason": _stop_reason(run),
        "error_message": _redact(_error_message(run)),
        # An explicit `attended: false` is the unattended grant; anything else asks per stage.
        "attended": not supervisor_policy.unattended_grant(run.policy_overrides),
        "max_cycles": cap,
        "total_cycles": _cycles_completed(run.id, loop[0]) if loop is not None else 0,
        "idle_secs": 0,
        "success_criteria": _redact(criterion) or None,
        "created_at": _epoch(run.created_at) or 0.0,
        "started_at": _epoch(run.started_at),
        "completed_at": _epoch(run.completed_at),
        # The loop contract: time BANKED outside the current running stretch, which a surface adds
        # to `now - started_at` while the row is running. So a running run banks nothing, and any
        # other run carries the one duration every run surface renders (`run_elapsed`).
        "elapsed_seconds": (
            0.0 if run.status == RunStatus.RUNNING else introspection.run_elapsed(run, time.time())
        ),
        "project_id": run.project_id,
        "execution": "solo",
        "agent": "",
        "model": "",
        "session_key": "",
        "kind_config": {},
        "plan": [],
        "phase_tracked": False,
        "findings": [],
        "workflow_name": run.workflow_name,
    }


def list_loop_views(*, project_id: str = "", kind: str = "") -> list[dict[str, Any]]:
    """Every run started as a loop, as loop rows, newest first."""
    out: list[dict[str, Any]] = []
    for run in store.list_loop_runs(project_id=project_id, kind=kind):
        try:
            out.append(run_loop_view(run))
        except Exception:
            # One unreadable run must not blank the whole loop list — the list is how a user finds
            # a broken run to delete it. Logged, and the rest still render.
            logger.warning("loop view: could not project run %s", run.id, exc_info=True)
    return out


def get_loop_view(run_id: str) -> dict[str, Any] | None:
    """The loop row for one run-backed loop, or ``None`` when ``run_id`` is not one."""
    run = store.get(run_id)
    if run is None or not run.loop_kind:
        return None
    return run_loop_view(run)
