"""The run-end learner — a terminal workflow run mines its own Run Ledger for lessons.

LEARNING-FLYWHEEL §3.3 (§7 step 5), the RUN_END cadence. When a run reaches a terminal state
the controller routes it through the `LearningGate` (permission: not ephemeral, not restricted,
learning enabled, this cadence on) and then calls :func:`capture`. This module is the RUN_END
sibling of `self_model_observer.observe_turn` (PER_TURN) and the consolidation envelope
(SESSION_END), and it is built to the same two rules:

**Inert unless a memory service with a live vector store is injected.** `capture` returns an
empty report the moment `service` is None or `has_vector` is False — the exact guard
`self_model_observer.observe_turn` uses. So every terminal-run controller test (which passes no
memory service) writes nothing, and an embedder-less box learns nothing rather than crashing.

**Propose, never install.** A failed step files a `lesson_batch` PROPOSAL through the shared
human-gated queue (`learning.proposals.enqueue`) — the same queue every other inferred lesson
clears. Nothing here writes a live `lesson.*` row; that happens only when the human accepts. This
closes the injection hole the plan names: a run's own failure text can no longer become a standing
instruction without a person in the loop.

What a terminal run contributes (§3.3):

- **Lesson proposal per distinct terminal failure**, through the env-failure deny-filter
  (`lesson_worthy` / `is_environment_failure_claim` — a flaky network is not a lesson), keyed by
  `(template, failure_mode, signature)` (LEARN-R8a/b) so the same mechanism failing twice is ONE
  proposal and the lesson can be re-injected on future runs of the template.
- **A failure CAPSULE** (LEARN-R8d): repro command + failure signature + forbidden success modes
  + bounded evidence, rendered into the proposal body so a later replay can verify the lesson
  still applies rather than re-reading prose.
- **A procedural prior** per failed step (`record_procedural(tool="workflow:<template>/<step>",
  outcome="failed")`) — the existing ≥3-failure synthesis then surfaces the prior next time the
  template is planned, for free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: How many failure-text samples a capsule keeps as evidence. Bounded on purpose (§3.3d says
#: "bounded evidence"): a capsule is a checkable summary, not a transcript, and the signature
#: already collapses the mechanism — three verbatim samples orient a reviewer without unbounding
#: the proposal body.
_MAX_EVIDENCE = 3

#: Clip length for one evidence sample. Long enough to carry the failing assertion, short enough
#: that a handful of them still fit a reviewable body.
_EVIDENCE_CLIP = 400


@dataclass
class FailureCapsule:
    """A checkable failure record (LEARN-R8d) — the alternative to a prose lesson.

    A prose lesson ages into folklore; a capsule can be REPLAYED. `repro_command` points at the
    deterministic path that reproduces or diagnoses the failure, `signature` is the refiner's own
    noise-stripped mechanism key (shared, so a cluster and its capsule are joinable),
    `forbidden_success_modes` is what a replay must NOT count as a pass, and `evidence` is the
    bounded sample. Rendered into the proposal body, not executed here — this module proposes.
    """

    template: str
    step: str
    mode: str
    signature: str
    repro_command: str
    forbidden_success_modes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def render(self) -> str:
        """The human-readable capsule block for a proposal body."""
        lines = [
            "Failure capsule (replay to verify this lesson still applies):",
            f"- template/step: {self.template}/{self.step}",
            f"- failure mode: {self.mode}",
            f"- signature: {self.signature or '(none)'}",
            f"- reproduce: {self.repro_command}",
        ]
        if self.forbidden_success_modes:
            lines.append("- must NOT be called success: " + "; ".join(self.forbidden_success_modes))
        if self.evidence:
            lines.append("- evidence:")
            lines.extend(f"    {sample}" for sample in self.evidence)
        return "\n".join(lines)


def _terminal_failures(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The last terminal failure per node (§3.3 "on step_failed, post retry-exhaustion").

    `step_failed` is written once per attempt; only the retry-exhausted one is the step's real
    outcome. Keyed by node so a step that failed five times contributes ONE failure, not five —
    the dedup the lesson key would otherwise have to redo. When a node has no retry-exhausted
    record (a run cancelled mid-retry) its latest failure stands in, so a cancelled run still
    teaches from what it saw rather than nothing.
    """
    terminal: dict[str, dict[str, Any]] = {}
    latest: dict[str, dict[str, Any]] = {}
    for rec in events:
        node = str(rec.get("node_id") or "")
        if not node:
            continue
        latest[node] = rec
        if rec.get("retries_exhausted"):
            terminal[node] = rec
    for node, rec in latest.items():
        terminal.setdefault(node, rec)
    return terminal


def _repro_command(run: Any) -> str:
    """The deterministic path that diagnoses THIS run's failure.

    `diagnose-run` is a bundled template keyed by run_id — a real, replayable verb, not an
    invented CLI flag. It compares the failed run against the last one that worked, which is
    exactly what a capsule replay wants.
    """
    return f'workflow_start(name="diagnose-run", inputs={{"run_id": "{run.id}"}})'


def capture(run: Any, service: Any, *, journal: Any = None) -> dict[str, int]:
    """Mine a terminal run's ledger into lesson proposals + procedural priors. Returns a report.

    Inert unless `service` has a live vector store (mirrors `self_model_observer.observe_turn`).
    Best-effort: never raises into `_finish`, the single terminal writer that must not fail.

    Report keys: ``proposed`` (lesson proposals filed), ``procedural`` (priors recorded),
    ``filtered`` (failures the env/unknown deny-filter dropped), ``skipped`` (failures the quota
    or a prior decision suppressed).
    """
    report = {"proposed": 0, "procedural": 0, "filtered": 0, "skipped": 0}
    if service is None or not getattr(service, "has_vector", False):
        return report
    if journal is None:
        from personalclaw.workflows import journal as journal_mod

        journal = journal_mod
    try:
        events = journal.ledger(run.id, kinds={journal.STEP_FAILED})
    except Exception:
        logger.debug(
            "run-end: could not read ledger for %s", getattr(run, "id", "?"), exc_info=True
        )
        return report
    if not events:
        return report

    from personalclaw.after_turn_review import is_environment_failure_claim
    from personalclaw.learning import proposals
    from personalclaw.learning.detectors import (
        LessonKey,
        classify_failure,
        dedupe_signature,
        lesson_worthy,
    )

    template = str(getattr(run, "workflow_name", "") or "unknown")
    scope_ref = str(getattr(run, "project_id", "") or "") or str(getattr(run, "id", "") or "")
    repro = _repro_command(run)

    filed_keys: set[str] = set()
    for node, rec in _terminal_failures(events).items():
        text = str(rec.get("error") or "")
        # The env deny-filter runs first and absolutely (§3.3): a world condition must never
        # become a lesson, or the agent learns to refuse a valid action later.
        if is_environment_failure_claim(text):
            report["filtered"] += 1
            continue
        worthy, why = lesson_worthy(text)
        if not worthy:
            logger.debug("run-end: skipping failure at %s/%s — %s", template, node, why)
            report["filtered"] += 1
            continue

        # The procedural prior is recorded even when the lesson proposal is quota-suppressed:
        # it is free, has no injection surface (no text, just a tool×outcome counter), and its
        # ≥3-failure synthesis is what surfaces the prior next time the template is planned.
        try:
            if service.record_procedural(
                tool=f"workflow:{template}/{node}",
                task_shape=f"workflow:{template}/{node}",
                outcome="failed",
                scope_ref=scope_ref,
            ):
                report["procedural"] += 1
        except Exception:
            logger.debug(
                "run-end: procedural capture failed at %s/%s", template, node, exc_info=True
            )

        mode = classify_failure(text)
        signature = dedupe_signature(text)
        key = LessonKey(template=template, mode=mode, signature=signature)
        if key.key in filed_keys:
            continue

        if proposals.quota_remaining(report["proposed"]) <= 0:
            report["skipped"] += 1
            continue

        capsule = FailureCapsule(
            template=template,
            step=node,
            mode=mode,
            signature=signature,
            repro_command=repro,
            forbidden_success_modes=[
                f"reporting success while the {mode} failure at {node} is unaddressed"
            ],
            evidence=[text[:_EVIDENCE_CLIP]][:_MAX_EVIDENCE],
        )
        body = (
            f"Step `{node}` of template `{template}` failed ({mode}). A future run of this "
            f"template should account for this failure mode.\n\n{capsule.render()}"
        )
        _verdict, proposal = proposals.enqueue(
            kind=proposals.Kind.LESSON_BATCH.value,
            title=f"Run failure in {template}/{node}: {mode}"[:120],
            body=body,
            target=key.key,
            provenance="inferred",
            # RUN_END cadence — the durable record field the accept-installer and the coverage
            # scan both read, matching `Cadence.RUN_END.value`.
            source_cadence="run_end",
            run_id=str(getattr(run, "id", "") or ""),
            source_excerpt=text,
            evidence_strength="correlated",
            confidence=0.5,
            tags=["run_end", "workflow_run", mode],
            # A terminal run failure is a single first-class signal, not a pattern that must
            # recur ≥3 times to be worth PROPOSING (the ≥3 floor is for consolidation-mined
            # habits). The dedup key already collapses repeats within a run, and R8 wants the
            # lesson re-injectable on FUTURE runs — waiting for three separate runs to fail the
            # same way before proposing anything is exactly the miss it exists to avoid. So the
            # evidence floor is lowered to 1 here, matching the curator's own review proposals.
            occurrences=1,
            min_evidence=1,
        )
        if proposal is not None:
            filed_keys.add(key.key)
            report["proposed"] += 1
        else:
            # A SKIP means a prior human decision forbids re-filing — not a failure. Counted as
            # skipped so "nothing proposed" is an explained observation, never a silent no-op.
            report["skipped"] += 1
    return report
