"""The best-effort consequences of a run's terminal write.

`RunController._finish` is the single terminal writer (WF2-R10) and must never raise, so everything
here is fully guarded: a failure costs a lesson, an overview line or the next queued run's start —
never this run's recorded outcome. Run-end learning capture (LEARNING-FLYWHEEL §3.3), the project
overview revision (WORK-CONTAINERS §6.1) and the `on_overlap: queue` drain (WV-14).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from personalclaw import project_context
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import ownership

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


async def drain_overlap_queue(ctl: RunController) -> None:
    """Start the next `on_overlap: queue` run for this def, now that this one has ended.

    The live call site for the queue (WV-14). It belongs here because this is the moment
    the def stops being busy: `_save_run` above has already written the terminal status,
    so `store.active_runs()` no longer counts this run and the drain's own re-check sees
    a free def.

    Awaited inline rather than fired as a task, deliberately: a floating task makes the
    handoff untestable ("did it start?" becomes a race) and can outlive the loop that
    created it. Fully guarded, because `_finish` is the single terminal writer (WF2-R10)
    and MUST NOT raise — a failure here costs the NEXT run's start, never this run's
    recorded outcome, and the watchdog's poll re-drains what this missed.
    """
    supervisor = getattr(ctl.services, "supervisor", None)
    if supervisor is None:
        return
    try:
        from personalclaw.workflows import overlap

        await overlap.drain(ctl.run.workflow_name, supervisor)
    except Exception:
        logger.debug("run %s: overlap drain failed", ctl.run.id, exc_info=True)


def capture_run_end(ctl: RunController) -> None:
    """Route a terminal run through the LearningGate → run-end learner (LEARNING-FLYWHEEL §3.3).

    The RUN_END cadence. Best-effort and fully guarded: `_finish` is the single terminal
    writer (WF2-R10) and MUST NOT raise, so a failure here costs a lesson, never the run's
    terminal status.

    Inert unless a memory service with a live vector store was injected into EngineServices —
    every test and CLI path leaves `services.memory` None, so this no-ops there exactly as
    `self_model_observer.observe_turn` no-ops without `has_vector`. The gate is what honors
    success criterion 10: an incognito/temporary session's terminal run is denied here (via
    the session-key restriction registry) and writes nothing through this cadence, and
    `learning.run_end_enabled=False` turns the cadence off without touching the others.
    """
    service = getattr(ctl.services, "memory", None)
    if service is None or not getattr(service, "has_vector", False):
        return
    try:
        from types import SimpleNamespace

        from personalclaw.config.loader import AppConfig
        from personalclaw.learning import run_end
        from personalclaw.learning.gate import Cadence, LearningGate

        cfg = AppConfig.load().learning
        # The session that started the run is likely gone by terminal time. `for_session` then
        # reads the process-global registry by key (`run_start.enforce_inherited_mode` marked it at
        # start), AND the `is_restricted` flag carried on this namespace — set from the RUN's
        # inherited mode, not hardcoded False. The run record is the durable authority: a
        # registry mark can evict from the bounded LRU over a long run, and reading
        # `is_restricted=False` there would re-open the gate an incognito origin closed. Belt
        # (record) and suspenders (registry), fail-closed by construction.
        restricted = ownership.run_mode(ctl.run) in ownership.WRITE_SUPPRESSED
        session = SimpleNamespace(
            key=ctl.run.origin.session_key, is_restricted=restricted, _ephemeral=False
        )
        decision = LearningGate.for_session(session, cfg).decide(
            Cadence.RUN_END, cadence_enabled=bool(getattr(cfg, "run_end_enabled", True))
        )
        if not decision.allowed:
            logger.debug("run %s: run-end capture gated (%s)", ctl.run.id, decision.reason.value)
            return
        run_end.capture(ctl.run, service, journal=journal_mod)
    except Exception:
        logger.debug("run %s: run-end capture failed", ctl.run.id, exc_info=True)


def revise_project_overview(ctl: RunController) -> None:
    """Auto-revise the run's project overview on a successful completion (WORK-CONTAINERS §6.1).

    DETERMINISTIC by design: this appends a terse line (run name + terminal status +
    a one-line summary drawn from the run's handoff/summary, else its workflow name) to
    the living overview and records the outcome in the decisions ledger. It does NOT
    call an LLM — the `completion` service is inert in prod, and an LLM-summarized
    overview is an explicit follow-on. This is a DEVIATION from a literal "revise"
    (append, not summarize), recorded in the plan's Execution log.

    Best-effort and fully guarded: `_finish` is the single terminal writer and MUST
    NOT raise, so a failure here costs the overview line, never the terminal status.
    """
    pid = ctl.run.project_id
    if not pid:
        return
    try:
        name = ctl.run.workflow_name or "run"
        summary = _completion_summary(ctl)
        line = f"- {name} → {ctl.run.status.value}"
        if summary:
            line += f": {summary}"
        current = project_context.read_overview(pid)
        new_text = f"{current}\n{line}" if current else line
        project_context.write_overview(pid, new_text)
        project_context.append_ledger(
            pid,
            "decisions",
            f"{name} → {ctl.run.status.value}",
            link=ctl.run.id,
        )
    except Exception:
        logger.debug("project overview revision skipped for %s", ctl.run.id, exc_info=True)


def _completion_summary(ctl: RunController) -> str:
    """A one-line summary for the overview append: the run's own handoff, else "".

    Pulled from the last recorded handoff's summary — what the run itself said it
    produced — rather than fabricated. A run that said nothing hands over nothing, so
    the line falls back to just name + status, which is honest.
    """
    try:
        handoff = getattr(ctl.run, "extra", {}).get("summary") if ctl.run.extra else ""
        if isinstance(handoff, str) and handoff.strip():
            return handoff.strip().splitlines()[0][:200]
    except Exception:
        return ""
    return ""
