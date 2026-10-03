"""The best-effort consequences of a run's terminal write.

`RunController._finish` is the single terminal writer and must never raise, so everything
here is fully guarded: a failure costs a lesson, an overview line, a trigger's report, a chain,
the next queued run's start or a step's batch — never this run's recorded outcome. Run-end learning
capture, the project overview revision, the report to the trigger that started the run, the
triggers waiting on the run, the `on_overlap: queue` drain and the end of what its steps started.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from personalclaw import memory_reads, memory_writes, project_context
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import ownership
from personalclaw.workflows.models import OriginKind, RunStatus, WorkflowRun, run_ending

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


async def end_what_its_steps_started(
    ctl: RunController, status: RunStatus, *, because: str = ""
) -> None:
    """End what the run's steps started, now that it has ended (`started_work.end_run`).

    A step's subagent can start a batch from its own session, and that batch went on after the
    run ended: its steps kept spending and kept their approvals answerable, for a run that was
    over. *because* is the clause the run's own cancel carried, which what it started ends with.
    Guarded like the rest (`end_run` never raises): a batch that will not stop costs that batch,
    never this run's recorded outcome.
    """
    from personalclaw import started_work

    await started_work.end_run(
        ctl.run,
        ending=run_ending(status),
        because=because,
        instances=ctl.instances,
        subagents=ctl.services.subagents,
        supervisor=ctl.services.supervisor,
    )


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
    `learning.run_end_enabled=False` turns the cadence off without touching the others. So is a
    run that is the work of an app not given your memory.
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
        # Whose work the run is: an app's when its scheduled job started it, or its conversation or
        # agent did (`memory_reads.reach_of`). An app's run teaches nothing unless the app was
        # given your memory, and with it what it teaches names the app (`memory_writes`).
        origin = ctl.run.origin
        whose = memory_reads.reach_of(
            getattr(ctl.services, "attention_state", None),
            origin.session_key,
            app=memory_reads.job_app(origin.trigger_id),
        ).app
        session = SimpleNamespace(
            key=origin.session_key,
            is_restricted=restricted,
            _ephemeral=False,
            created_by_app=whose,
        )
        decision = LearningGate.for_session(session, cfg).decide(
            Cadence.RUN_END, cadence_enabled=bool(getattr(cfg, "run_end_enabled", True))
        )
        if not decision.allowed:
            logger.debug("run %s: run-end capture gated (%s)", ctl.run.id, decision.reason.value)
            return
        own = ownership.owned_key(ctl.run.id, "run-end")
        with memory_writes.derived_from(own, app=whose) if whose else contextlib.nullcontext():
            run_end.capture(ctl.run, service, journal=journal_mod)
    except Exception:
        logger.debug("run %s: run-end capture failed", ctl.run.id, exc_info=True)


#: Endings a trigger does not report: whoever stopped the run, or declined what it asked,
#: knows. The same rule `attention.announce_run_end` holds for every other run.
_UNREPORTED_ENDINGS: frozenset[RunStatus] = frozenset({RunStatus.CANCELLED, RunStatus.DECLINED})


def report_to_its_trigger(services: Any, run: WorkflowRun, status: RunStatus) -> None:
    """Say how a run a trigger started went, on the trigger's run and route, now that it has ended.

    The fire that started it only said it launched the run, or queued it, which is not news yet
    (`delivery.says_nothing_now`). So this is when the trigger's run says how it went, and the
    trigger takes that ending as it takes any run's (`triggers.settle.settle_workflow_run`): a
    failure counts toward pausing the automation, a success starts the count over, and a run its
    owner stopped or declined counts for neither. Its route then hears: "<name> finished" and what
    the run said it produced, or "<name> failed" and why, for a run that failed or stopped before
    it finished. Only a trigger's own start (`OriginKind.HOOK`) carries a trigger id; a sub-run's
    origin names its parent's node instead.

    Asked by every terminal write: the controller's (`RunController._finish`) and the two the
    watchdog makes with no controller (a run whose spec cannot be read, and one whose steps all
    ended while nothing drove it), so a run's trigger hears however it ended. The report is inert
    unless the gateway wired its delivery into `EngineServices.report_to_trigger`, and the card
    for an automation the run's failure paused unless it wired `EngineServices.on_attention`. Fully
    guarded: a failure costs the report, never the run's terminal status.
    """
    origin = run.origin
    if origin.kind != OriginKind.HOOK or not origin.trigger_id:
        return
    try:
        from personalclaw.triggers.settle import settle_workflow_run

        error = ""
        if status != RunStatus.COMPLETE:
            error = str(run.error_message or "").strip() or (
                f"The workflow run {run_ending(status)}."
            )
        summary = _completion_summary(run)
        settle_workflow_run(
            origin.trigger_id,
            run.id,
            status,
            error=error,
            summary=summary,
            on_attention=getattr(services, "on_attention", None),
        )
        report = getattr(services, "report_to_trigger", None)
        if report is None or status in _UNREPORTED_ENDINGS:
            return
        report(origin.trigger_id, error=error, summary=summary, run_id=run.id)
    except Exception:
        logger.debug("run %s: could not report to its trigger", run.id, exc_info=True)


def report_to_its_chat(services: Any, run: WorkflowRun, status: RunStatus) -> None:
    """Tell the conversation that started a subagent batch how each of its tasks ended, now that
    the batch has ended (`batch_start.tells_its_chat`).

    A single subagent's result arrives in its chat as a completion event the agent reads on its
    next step; a batch's tasks ran for the batch's run alone, so the chat whose agent started it
    heard nothing. Every ending but a stop is said, a failure and a Deny too: the agent that
    started the batch waits on it, and how it ended is its answer. Asked by every terminal write,
    as :func:`report_to_its_trigger` is. Inert unless the gateway wired its completion delivery
    into `EngineServices.announce`, and fully guarded: a failure costs the report, never the run's
    terminal status.
    """
    announce = getattr(services, "announce", None)
    if announce is None:
        return
    from personalclaw.workflows import batch_start

    try:
        if not batch_start.tells_its_chat(run, status):
            return
        endings = batch_start.ending_of_run(
            run, status, subagents=getattr(services, "subagents", None)
        )
        if endings:
            announce(endings)
    except Exception:
        logger.debug("run %s: could not tell its chat how it ended", run.id, exc_info=True)


def chain_after_run(services: Any, run: WorkflowRun, status: RunStatus) -> None:
    """Hand the run's end to the triggers waiting on it: on this run, on any run of its workflow,
    or on the trigger that started it (`triggers.chain`). "When the research run finishes, post the
    summary" is one of those, and it runs now, not when the run started.

    Every ending but the two its owner chose (`_UNREPORTED_ENDINGS`): a run that failed has
    finished too, and what waits on it is told how it ended. Asked by every terminal write, as
    :func:`report_to_its_trigger` is. Inert unless the gateway wired its dispatch into
    `EngineServices.run_ended`; fully guarded, so a failure costs the chain, never the run's
    terminal status.
    """
    ended = getattr(services, "run_ended", None)
    if ended is None or status in _UNREPORTED_ENDINGS:
        return
    try:
        ended(run, status=status.value, summary=_completion_summary(run))
    except Exception:
        logger.debug("run %s: could not chain what waits on it", run.id, exc_info=True)


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
        summary = _completion_summary(ctl.run)
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


def _completion_summary(run: WorkflowRun) -> str:
    """A one-line summary for the overview append: the run's own handoff, else "".

    Pulled from the last recorded handoff's summary — what the run itself said it
    produced — rather than fabricated. A run that said nothing hands over nothing, so
    the line falls back to just name + status, which is honest.
    """
    try:
        handoff = getattr(run, "extra", {}).get("summary") if run.extra else ""
        if isinstance(handoff, str) and handoff.strip():
            return handoff.strip().splitlines()[0][:200]
    except Exception:
        return ""
    return ""
