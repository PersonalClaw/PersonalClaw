"""Fresh-session resumability audit (§2.4) — loop scope.

The harness-course "Fresh Session Test", mechanized for PersonalClaw loops: after the
in-memory worker/planner session is gone (a crash/restart), can the resumed entity answer
*what's done / what's verified / what's next / how to verify* **from persisted state
alone**? This module reads a loop's on-disk state through ``personalclaw.loop.store`` (the
harness may import core; core may not import the harness) and reports whether each question
is answerable — the exact audit that would have caught the historical dead-resume bugs.

Loop mapping of the four questions (findings COUNT is the cycle clock — the plan's note
that in-memory watchdog counters are documented as non-resumed):

- **done?**    — ``loop.status`` (a terminal/attention status is a definitive answer).
- **verified?**— judge verdicts on disk (``store.get_verdicts``) and/or cycle findings.
- **next?**    — for phased kinds, ``plan`` + ``phase_status`` name the next stage; for
  open-ended goals, the marginal-score trail + status; otherwise the next cycle.
- **how to verify?** — the persisted task/spec text (``loop.task``) + the plan.

Workflow-run resume (the WF2 event-fold byte-equal frontier reconstruction) is DEFERRED —
it needs the Workflows-v2 engine + journal, which don't exist yet (see the plan's S4
BLOCKED note). This module covers the loop half, which is fully persistable today.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
