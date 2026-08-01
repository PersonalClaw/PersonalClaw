"""`workflow_audit` — diagnose and heal a run store that drifted (WF2-R10).

The watchdog handles the failures it can see while running. This handles the ones that
accumulate when it was *not* running: a gateway killed mid-write, a subagent that
vanished while its node stayed RUNNING, a gate parked on a deadline that passed weeks
ago, journal keys orphaned by a rewind.

Every finding is a typed record with a `heal` verb, and **`dry_run` defaults to True**.
An auto-repair that runs before a human has seen the diagnosis is how a maintenance tool
turns one broken run into a broken store — the ordering (diagnose, show, then heal on
request) is the safety property, not a UX preference.

Healing is deliberately narrow. It writes terminal statuses onto runs that have **no
live controller**, which is the same safety argument the watchdog's orphan reap rests on:
with no controller there is no second writer to race (WF2-R10). A run with a live
controller is reported and left alone.
"""

from __future__ import annotations

import calendar
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows import store
from personalclaw.workflows.models import (
    TERMINAL_STATES,
    InstanceState,
    RunStatus,
    WorkflowRun,
)

logger = logging.getLogger(__name__)

#: A RUNNING node whose backing work has been silent this long is presumed lost. Well
#: above any legitimate node duration, because a false "lost" verdict kills live work.
STALE_RUNNING_SECS = 6 * 60 * 60

#: Grace after a WAITING deadline before the wait is called expired. Absorbs a gateway
#: that was simply down over the deadline.
EXPIRED_WAIT_GRACE_SECS = 60


class Finding:
    """Finding kinds. Strings, so a UI and the ledger can render them without importing
    the enum."""

    STALE_RUNNING = "stale_running"
    DEAD_GATE = "dead_gate"
    EXPIRED_WAIT = "expired_wait"
    LOST_RUN = "lost_run"
    ORPHANED_JOURNAL = "orphaned_journal"
    MISSING_SPEC = "missing_spec"
    PENDING_CANCEL = "pending_cancel"


@dataclass
class AuditFinding:
    """One diagnosis. `healed` is set only when a repair actually ran."""

    kind: str
    run_id: str
    detail: str = ""
    instance_path: str = ""
    heal: str = ""
    healed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "detail": self.detail,
            "instance_path": self.instance_path,
            "heal": self.heal,
            "healed": self.healed,
        }


@dataclass
class AuditReport:
    findings: list[AuditFinding] = field(default_factory=list)
    runs_scanned: int = 0
    dry_run: bool = True

    @property
    def healthy(self) -> bool:
        return not self.findings

    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "dry_run": self.dry_run,
            "runs_scanned": self.runs_scanned,
            "counts": self.by_kind(),
            "findings": [f.to_dict() for f in self.findings],
        }


def audit(
    *,
    dry_run: bool = True,
    supervisor: Any = None,
    now: float = 0.0,
) -> AuditReport:
    """Scan every live run; heal only when `dry_run` is False.

    `supervisor` is the watchdog, consulted so a run with a live controller is never
    healed underneath it — the controller is that run's only legitimate writer.
    """
    clock = now or time.time()
    report = AuditReport(dry_run=dry_run)
    for run in store.active_runs():
        report.runs_scanned += 1
        has_controller = _has_live_controller(supervisor, run.id)
        _audit_run(run, report, clock=clock, dry_run=dry_run, has_controller=has_controller)
    return report


def _has_live_controller(supervisor: Any, run_id: str) -> bool:
    if supervisor is None:
        return False
    getter = getattr(supervisor, "controller", None)
    if not callable(getter):
        return False
    try:
        return getter(run_id) is not None
    except Exception:
        logger.debug("audit: supervisor controller lookup failed", exc_info=True)
        return False


def _audit_run(
    run: WorkflowRun,
    report: AuditReport,
    *,
    clock: float,
    dry_run: bool,
    has_controller: bool,
) -> None:
    spec = store.read_spec(run.id)
    if spec is None:
        finding = AuditFinding(
            kind=Finding.MISSING_SPEC,
            run_id=run.id,
            detail="run spec is missing or unreadable; the run cannot be resumed",
            heal="mark the run failed",
        )
        if not dry_run and not has_controller:
            run.status = RunStatus.FAILED
            run.error_message = "run spec is missing or unreadable"
            run.completed_at = run.completed_at or _now()
            store.save(run)
            finding.healed = True
        report.findings.append(finding)
        return

    if store.cancel_requested(run.id) and not has_controller:
        finding = AuditFinding(
            kind=Finding.PENDING_CANCEL,
            run_id=run.id,
            detail="a sticky cancel intent was never consumed (no live controller)",
            heal="finalize the run as cancelled",
        )
        if not dry_run:
            run.status = RunStatus.CANCELLED
            run.completed_at = run.completed_at or _now()
            store.save(run)
            store.clear_cancel(run.id)
            finding.healed = True
        report.findings.append(finding)
        return

    instances = store.read_state(run.id)
    dirty = False
    for path, inst in instances.items():
        if inst.state == InstanceState.RUNNING:
            age = clock - _epoch(inst.started_at)
            if inst.started_at and age > STALE_RUNNING_SECS:
                finding = AuditFinding(
                    kind=Finding.STALE_RUNNING,
                    run_id=run.id,
                    instance_path=path,
                    detail=f"node has been RUNNING for {int(age // 3600)}h with no completion",
                    heal="transition to blocked{protocol_violation}",
                )
                if not dry_run and not has_controller:
                    _mark_protocol_violation(inst)
                    dirty = True
                    finding.healed = True
                report.findings.append(finding)
        elif inst.state == InstanceState.WAITING and inst.wake_at:
            if clock - inst.wake_at > EXPIRED_WAIT_GRACE_SECS:
                finding = AuditFinding(
                    kind=Finding.EXPIRED_WAIT,
                    run_id=run.id,
                    instance_path=path,
                    detail="a WAITING deadline passed with nothing scheduled to wake it",
                    heal="clear the deadline so the next tick resolves it",
                )
                if not dry_run and not has_controller:
                    # Cleared, NOT completed: the controller owns deciding what a woken
                    # wait means (WF2-R10). Healing only removes the wedge.
                    inst.wake_at = 0.0
                    inst.state = InstanceState.PENDING
                    dirty = True
                    finding.healed = True
                report.findings.append(finding)
        elif inst.state == InstanceState.WAITING and not inst.wake_at:
            # A gate with no deadline is legitimately parked on a human — only a dead
            # gate on a run nobody is driving is a finding, and it is reported, never
            # auto-resolved: nobody approved anything (WF2-R7).
            if run.status != RunStatus.NEEDS_INPUT:
                report.findings.append(
                    AuditFinding(
                        kind=Finding.DEAD_GATE,
                        run_id=run.id,
                        instance_path=path,
                        detail=(
                            "node parked on an unanswered gate but the run is not "
                            f"surfaced as needs_input (status={run.status.value})"
                        ),
                        heal="surface the run as needs_input",
                    )
                )

    if instances and all(st.state in TERMINAL_STATES for st in instances.values()):
        if not has_controller and run.status not in (
            RunStatus.COMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.ESCALATED,
        ):
            finding = AuditFinding(
                kind=Finding.LOST_RUN,
                run_id=run.id,
                detail="every node is terminal but the run status was never written",
                heal="write the derived terminal status",
            )
            if not dry_run:
                _finalize_from_instances(run, instances)
                finding.healed = True
            report.findings.append(finding)

    if dirty:
        store.write_state(run.id, instances)


def _mark_protocol_violation(inst: Any) -> None:
    """A node whose backing work vanished without a structured completion.

    `blocked{protocol_violation}` rather than FAILED: "the worker disappeared without
    telling us how it went" is a different fact from "the work failed", and it routes to
    the needs-input surface instead of looking like a defect (WF2-R10).
    """
    from personalclaw.workflows.models import Failure, FailureClass

    inst.state = InstanceState.BLOCKED
    inst.completed_at = _now()
    inst.failure = Failure(
        failure_class=FailureClass.PROTOCOL,
        cause_plain="node stopped without reporting a structured completion",
        remediation=(
            "the backing subagent or command vanished; re-run this node, or inspect the "
            "gateway log for why it died"
        ),
        terminal_reason="protocol_violation",
    )


def _finalize_from_instances(run: WorkflowRun, instances: dict[str, Any]) -> None:
    """Write the run's terminal status derived from its node states.

    Uses the same severity collapse the frontier does, via `_ROOT_TO_RUN`, so an audit
    and a normal completion cannot disagree about what a mixed child set means.
    """
    from personalclaw.workflows.controller import _ROOT_TO_RUN
    from personalclaw.workflows.tick import _worst

    states = [i.state for i in instances.values()]
    outcome = _worst(states) if states else InstanceState.DONE
    run.status = _ROOT_TO_RUN.get(outcome, RunStatus.COMPLETE)
    run.completed_at = run.completed_at or _now()
    store.save(run)
    logger.info("workflow audit finalized lost run %s → %s", run.id, run.status.value)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(ts: str | None) -> float:
    """Parse a UTC `...Z` stamp to a real epoch.

    `calendar.timegm`, NOT `time.mktime`: mktime interprets the struct as LOCAL time, so
    parsing a UTC stamp shifts it by the machine's offset. Here that offset silently
    cancelled the measured age — a node stale for hours read as fresh, and the whole
    stale-running check was inert in exactly the timezone the servers run in.
    """
    if not ts:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0
