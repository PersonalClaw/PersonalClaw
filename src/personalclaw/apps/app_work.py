"""The work an app's agent starts is the app's work, held to the app's tier wherever it runs.

An app's agent (an agent run it asked for, its scheduled job's, a turn of a conversation it started)
can start more agents with the owner's tools: one subagent, a batch of them (``subagent_run``), a
workflow run whose steps are agents (``workflow_start``). Each is the app's work, as the agent that
started it is, and each is held as that agent is:

* **By its name.** Each agent carries the app's (``SubagentInfo.app``), so it starts on the app's
  install consent (``approval_grants.APP``) and approves none of its calls: each call that needs
  approval asks the owner, whatever her YOLO, a chat's Trust, the hook settings or her Approval
  mode say (``subagent_tier``), and the ask names the app, and its scheduled job where the job's
  fire started the work (:func:`named`). Never a grant of hers starts it either: those are for her
  own agents.
* **To its tier.** Each runs at no more than the agent tier the app holds when it starts
  (``permissions.agent_tier_now``): a step that may change things is refused at ``read``, and at
  ``text``, or with no tier at all, nothing that could call a tool starts, and what asked for it is
  told why (:func:`beyond_tier`).

Whose work a session is, is ``memory_reads.reach_of``'s answer (:func:`of_session`): the app whose
conversation, agent run or scheduled job it is, followed up the chain of agents that started it. A
workflow run records it when it is created (:data:`RUN_KEY`, ``service.start_run``), because what it
was started for may be gone before its steps start: a batch waits for its owner's Allow, and a run
outlives a restart where the agent that started it does not. A run an app's scheduled job started
(``RunOrigin.trigger_id``), and a run started from one of the app's runs (a step's sub-run, a fork:
``ownership.inherited_extra``), are the app's work too (:func:`of_run`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The run-record key (``WorkflowRun.extra``) of whose work a run is: ``{"app": <name>, "job":
#: <its scheduled job's trigger id, when one started it>}``. Absent for the owner's own runs.
RUN_KEY = "app_work"

#: The actions that start an agent, or a run of agents, of their own (an automation's agent, with
#: the step's own approval and write access; another workflow's run, which is no app's), and so
#: start nothing in a run that is an app's work (:func:`action_refusal`).
STARTS_AGENTS: frozenset[str] = frozenset({"invoke-agent", "run-prompt", "run-workflow"})


@dataclass(frozen=True)
class AppWork:
    """Whose work: the app, and the trigger id of its scheduled job when the job's fire started
    the work (``app_crons.job_id``), ``""`` otherwise. An ``app`` of ``""`` is a run whose record
    says it is an app's work and cannot say which, and nothing of it starts."""

    app: str
    job: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"app": self.app, **({"job": self.job} if self.job else {})}


def _recorded(raw: object) -> AppWork:
    """The work a run's record names (:data:`RUN_KEY`). Read fail-closed: a record that does not
    name an app is the work of an app nobody can name, which starts nothing."""
    from personalclaw.apps.app_crons import app_of

    if not isinstance(raw, dict) or not isinstance(raw.get("app"), str):
        return AppWork("")
    app = str(raw["app"])
    job = raw.get("job")
    return AppWork(app, job if isinstance(job, str) and app and app_of(job) == app else "")


def of_session(state: Any, session_key: str) -> AppWork | None:
    """Whose work the session *session_key* is, when it is an app's: the app, and the scheduled
    job whose fire started it (``memory_reads.reach_of``); ``None`` for the owner's own."""
    from personalclaw import memory_reads

    reach = memory_reads.reach_of(state, session_key)
    return AppWork(reach.app, reach.job) if reach.app else None


def of_record(record: object) -> AppWork | None:
    """Whose work a record (a run's ``extra``, a waiting batch's record) says it is: ``None`` when
    it says nothing, as the owner's own work's does (:data:`RUN_KEY`)."""
    if not isinstance(record, dict) or RUN_KEY not in record:
        return None
    return _recorded(record[RUN_KEY])


def of_run(run: Any) -> AppWork | None:
    """Whose work the workflow run *run* is, when it is an app's: as its record says
    (:func:`of_record`), or as the app's scheduled job that started it says
    (``origin.trigger_id``); ``None`` for the owner's own."""
    from personalclaw.apps.app_crons import app_of

    if (recorded := of_record(getattr(run, "extra", None))) is not None:
        return recorded
    job = str(getattr(getattr(run, "origin", None), "trigger_id", "") or "")
    app = app_of(job)
    return AppWork(app, job) if app else None


def of_run_id(run_id: str) -> AppWork | None:
    """:func:`of_run` for the run *run_id*, read from the store; ``None`` for no run. A record
    that cannot be read is the work of an app nobody can name (fail closed: a step whose run could
    be an app's starts nothing it cannot hold)."""
    if not run_id:
        return None
    from personalclaw.workflows import store

    try:
        run = store.get(run_id)
    except Exception:  # noqa: BLE001 - see the docstring: an unreadable record starts nothing
        logger.warning("run %s unreadable: whose work it is cannot be told", run_id, exc_info=True)
        return AppWork("")
    return of_run(run) if run is not None else None


def stamp(extra: dict[str, Any] | None, work: AppWork | None) -> dict[str, Any]:
    """*extra* with whose work the run is recorded (:data:`RUN_KEY`), as a new dict; *extra* as
    it is for the owner's own run."""
    return {**(extra or {}), RUN_KEY: work.to_dict()} if work is not None else dict(extra or {})


def named(work: AppWork) -> str:
    """The work's app as the owner knows it, by the name install consent showed, and its scheduled
    job where one started it: ``the app “Research Lab”``, ``the app “Research Lab”'s scheduled
    job “advance-campaigns”``."""
    if not work.app:
        return "an app its record does not name"
    from personalclaw.apps.app_manager import display_name_of

    app = f"the app “{display_name_of(work.app)}”"
    _, _, cron = work.job.partition(f"app:{work.app}:")
    return f"{app}'s scheduled job “{cron}”" if cron else app


def beyond_tier(work: AppWork, *, changes: bool) -> str:
    """Why an agent of *work* that may change things (*changes*), or only read, cannot start: the
    app may run no agent work now, it may use no tools (``text``), or it may only read (``read``)
    and the agent may change things. A clause that follows "it is the work of …, and"; ``""`` when
    the agent may start. Read now (``permissions.agent_tier_now``), so a narrowed or switched-off
    app holds every start after it."""
    from personalclaw.apps.agent_tiers import AGENT_TEXT, AGENT_TOOLS
    from personalclaw.apps.permissions import agent_tier_now, no_agent_work

    if not work.app:
        return "nothing can say what that app may run"
    tier = agent_tier_now(work.app)
    if not tier:
        return f"the app may run no agent work now ({no_agent_work(work.app)})"
    if tier == AGENT_TEXT:
        return "the app's agent work may use no tools (its agent tier is text)"
    if changes and tier != AGENT_TOOLS:
        return f"the app's agent work may only read (its agent tier is {tier})"
    return ""


def subagent_class(work: AppWork) -> tuple[str, str]:
    """``(capability class, "")`` for one subagent *work*'s agent starts: the app's tier as a class
    (``agent_tiers.capability_class``: a text agent has no tools), or ``("", why)`` when the app may
    run no agent work now and nothing starts."""
    from personalclaw.apps.agent_tiers import capability_class
    from personalclaw.apps.permissions import agent_tier_now

    tier = agent_tier_now(work.app) if work.app else ""
    if not tier:
        why = beyond_tier(work, changes=False)
        return "", f"It is the work of {named(work)}, and {why}, so no subagent started."
    return capability_class(tier), ""


def step_refusal(run_id: str, capability: str) -> tuple[AppWork | None, str]:
    """Whose work the step of the run *run_id* is, and why it starts nothing (``""`` when it may
    start): a step that asks for *capability* (``research`` reads, ``mutating`` may change things)
    of a run that is an app's work starts only within the tier the app holds now."""
    from personalclaw.subagent import CAPABILITY_MUTATING

    work = of_run_id(run_id)
    if work is None:
        return None, ""
    why = beyond_tier(work, changes=capability == CAPABILITY_MUTATING)
    if not why:
        return work, ""
    return work, f"This step is the work of {named(work)}, and {why}, so it started no agent."


def action_refusal(run_id: str, provider: str) -> str:
    """Why an action step of *provider* in the run *run_id* does not run, or ``""``: in a run that
    is an app's work, an agent starts only as one of the run's own steps, held to the app's tier,
    so an action that starts one of its own (:data:`STARTS_AGENTS`) starts nothing."""
    if provider not in STARTS_AGENTS:
        return ""
    work = of_run_id(run_id)
    if work is None:
        return ""
    return (
        f"This step would start an agent of its own (the “{provider}” action), and the run is the "
        f"work of {named(work)}, whose agents start only as the run's own steps, held to the "
        "app's agent tier, so it did not run."
    )
