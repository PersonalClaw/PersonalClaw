"""What a turn or a run started, which ends when it does.

A chat's turn, a loop's worker and a workflow run's steps start work that outlives the call that
started it. Two or more tasks handed to ``subagent_run`` run as one workflow run (a batch), and one
task runs as a background subagent. Each records the session it was started from: the run its
``origin.session_key`` (the call's session header, ``workflows.service.start_run``), the subagent
its ``parent_session_key``. That is the link, and one rule holds over it: when what started the work
ends, the work ends with it, saying why. Nothing would read what it found, and asking her to allow
its steps would start agents for work that is over; nor does its report start a turn where that
work ran (``SubagentInfo.starter_ended``), which would take the work up again.

* A loop's ending ends what its workers started (``loop.children.end_children``).
* A turn's Stop ends what the turn started (:func:`end_turn`, which the dashboard state hands
  ``SessionManager.stop_turn``): what the turn's session started since the turn began. What an
  earlier turn of the same chat started is that turn's, which ended without being stopped, so it
  goes on.
* A workflow run's ending ends what its steps started (:func:`end_run`, from
  ``RunController._finish`` and ``service.delete_run``): what the session of one of its steps'
  subagents (``subagent:<id>``) started, and what four step kinds launched (:func:`_end_launched`):
  the agent of an Invoke Agent (``invoke-agent``) or Run Prompt (``run-prompt``) action step
  (``SubagentInfo.workflow_run``), the child run a ``subworkflow`` step waits on, and the run a
  Run workflow (``run-workflow``) step starts to go on alone (both ``parent_run_id``; the second
  with ``spawned_by_node_id``). Which of those end depends on how the run ended:

  - cancelled or deleted: all four end, saying the run's clause, a Run workflow run still queued
    behind its workflow's overlap policy included, and so does what those agents started from
    their own sessions (``subagent:<id>``);
  - failed, escalated or declined: the subworkflow runs it was still waiting on end, since nothing
    waits for them now. One whose step settled and let it go on (``child_run_attach``) goes on, and
    so do the agents and Run workflow runs its steps launched to go on alone, since no one stopped
    them;
  - finished: nothing more ends.

  No ending reaches a fork, which continues the run rather than being its work. A child it ends is
  cancelled like any run, so the child's own ending ends what its steps launched, all four kinds,
  after a parent's failure too.

And what an ended subagent started from its own session ends with it, so a batch a background
subagent started does not outlive the Stop that ends the subagent. So does what still waits for
its owner's Allow (``workflows.owner_allow``: a batch's start, a workflow's save): its ask ends,
saying why, and nothing it asked for is started or saved.

Each run is cancelled with the clause, which its ending says ("Stopped because its chat turn was
stopped."), and the controller driving it stops its steps' subagents and ends what they were
waiting on, saying why; each subagent is stopped saying it ("Cancelled because …"). A run whose
cancel was already asked keeps the reason it was asked with: a loop's Stop ends its children before
its workers' turns are stopped, so they read as the loop's.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, NamedTuple

logger = logging.getLogger(__name__)

#: How what a turn started reads once the turn's Stop ends it: "Stopped because its chat turn was
#: stopped." Every turn stop is one (the Stop button, a message that replaced the turn, a move to
#: another agent), and none ends the chat itself.
TURN_STOPPED = "its chat turn was stopped"


class Ended(NamedTuple):
    """What an ending reached: the runs whose cancel it asked, the subagents it stopped (a
    cancelled run's steps still running among them, which that run's controller stops), and the
    asks for the owner's Allow it ended (a batch waiting to start, a workflow waiting to be
    saved)."""

    runs: int
    subagents: int
    asks: int = 0


def _agents(subagents: Any) -> list[Any]:
    return list(getattr(subagents, "all_agents", None) or [])


def _started_since(created_at: str, since: float) -> bool:
    """Whether a run created at *created_at* (a ``…Z`` stamp, whole seconds) started at or after
    *since*. A stamp in the second *since* falls in counts, since a stamp cannot say which part of
    that second it names."""
    if not since:
        return True
    from personalclaw.workflows.models import stamp_epoch

    return stamp_epoch(created_at) >= math.floor(since)


async def end_started(
    *,
    subagents: Any,
    supervisor: Any,
    owns: Callable[[str], bool],
    clause: str,
    since: float = 0.0,
    asks: Any = None,
) -> Ended:
    """End what the sessions *owns* claims started, and what each subagent among it started in
    turn, as *clause* ("its chat turn was stopped"). *asks* is the approval registry, which holds
    what still waits for her Allow (``workflows.owner_allow.end_asks``).

    *since* (an epoch) keeps it to what those sessions started from then on: a turn's own work,
    not an earlier turn's. What a subagent it reaches started is that subagent's, whenever it was.

    Fail-open, child by child: a child that will not stop must not keep the owner from reaching
    the state she asked for. A run left behind is still ended by the workflow supervisor when its
    loop has ended (``loop.children.parent_ended``).
    """
    from personalclaw.workflows import service, store
    from personalclaw.workflows.ownership import OWNED_PREFIX

    agents = _agents(subagents)
    started = {
        info.id: info
        for info in agents
        if owns(str(getattr(info, "parent_session_key", "") or ""))
        and float(getattr(info, "started", 0.0) or 0.0) >= since
    }
    while True:
        sessions = {f"subagent:{agent_id}" for agent_id in started}
        more = {
            info.id: info
            for info in agents
            if info.id not in started
            and str(getattr(info, "parent_session_key", "") or "") in sessions
        }
        if not more:
            break
        started.update(more)
    # Each one, finished or not, hands its report to no turn from here: one stopped below would
    # otherwise be handed back as a turn that tells the agent its work was cut off, which the agent
    # takes up again, and one that finished just before has its report waiting for the turn that
    # is ending, to start the next (`gateway._subagent_done`). Before any is stopped, so the
    # report each stop sends finds it said.
    for info in started.values():
        info.starter_ended = clause
    runs = 0
    steps = 0
    for run in store.active_runs():
        key = str(run.origin.session_key or "")
        if not (key in sessions or (owns(key) and _started_since(run.created_at, since))):
            continue
        if store.cancel_requested(run.id):
            continue
        try:
            if service.cancel_run(run.id, supervisor=supervisor, reason=clause).get("ok"):
                runs += 1
                lane = f"{OWNED_PREFIX}{run.id}"
                steps += sum(1 for info in agents if not info.done and info.parent_run == lane)
        except Exception:
            logger.warning("could not stop run %s (%s)", run.id, clause, exc_info=True)
    stopped = 0
    going = [agent_id for agent_id, info in started.items() if not info.done]
    if going and subagents is not None:
        try:
            stopped = await subagents.stop_agents(going, because=clause)
        except Exception:
            logger.warning("could not stop subagents %s (%s)", going, clause, exc_info=True)
    waiting = 0
    if asks is not None:
        from personalclaw.workflows.owner_allow import end_asks

        try:
            waiting = end_asks(
                asks,
                lambda session, asked_at: (owns(session) or session in sessions)
                and asked_at >= since,
                reason=clause,
            )
        except Exception:
            logger.warning(
                "could not end the asks waiting for an Allow (%s)", clause, exc_info=True
            )
    if runs or stopped or waiting:
        logger.info(
            "ended %d run(s), %d subagent(s) and %d ask(s) for an Allow: %s",
            runs,
            stopped,
            waiting,
            clause,
        )
    return Ended(runs=runs, subagents=stopped + steps, asks=waiting)


async def end_turn(state: Any, session_key: str) -> int:
    """A turn of *session_key* is being stopped (``SessionManager.stop_turn``): end what it
    started. Returns how many subagents that reached, a batch's running steps among them, which
    the turn's stop record counts.

    The turn is the one the chat runner registered as running on the chat
    (``resilience.active_jobs``), from the moment its prompt is about to go out, and what it started
    is what the chat's session started since then. With no turn on record (none is running, or
    the Stop came before its prompt went out) it started nothing, and an earlier turn's work is
    not this Stop's to end.
    """
    from personalclaw.constants import DASHBOARD_SESSION_PREFIX, dashboard_session_key
    from personalclaw.resilience.active_jobs import get_tracker

    name = str(session_key or "").removeprefix(DASHBOARD_SESSION_PREFIX)
    job = get_tracker().get(name) if name else None
    if job is None:
        return 0
    keys = frozenset({name, dashboard_session_key(name)})
    ended = await end_started(
        subagents=getattr(state, "subagents", None),
        supervisor=getattr(state, "workflows", None),
        asks=state,
        owns=keys.__contains__,
        clause=_turn_clause(name),
        since=float(job.started_at),
    )
    return ended.subagents


def _turn_clause(name: str) -> str:
    """How what the turn of chat *name* started reads once its Stop ends it: a paused loop's when
    the turn is that loop's worker's (a pause stops the cycle in flight), else the turn's Stop."""
    from personalclaw.loop import children

    loop_id = children.loop_of(name)
    if loop_id and children.is_paused(loop_id):
        return children.clause(loop_id, "was paused")
    return TURN_STOPPED


async def end_run(
    run: Any,
    *,
    ending: str,
    because: str = "",
    instances: dict[str, Any] | None = None,
    subagents: Any = None,
    supervisor: Any = None,
) -> int:
    """Workflow run *run* has ended *ending* ("was cancelled", "failed", "was deleted"): end what
    its steps started. Returns how many runs and subagents that ended.

    Its steps are the subagents it dispatched: the ones its instances name (*instances*, else its
    persisted state), and the ones this process's manager knows ran in its lane. What each started
    from its own session ends, and so does any step of its own still going. *because* is the clause
    the run's own cancel carried, when something else ended it (its loop's Stop, its turn's): what
    it started ends saying that, as the run does. Otherwise it names the run and how it ended.

    Never raises: both callers end something that must not fail because what the run started
    would not stop (the run's terminal write, its deletion).
    """
    from personalclaw.workflows import store
    from personalclaw.workflows.ownership import OWNED_PREFIX

    try:
        lane = f"{OWNED_PREFIX}{run.id}"
        state = instances if instances is not None else store.read_state(run.id)
        steps = {str(inst.subagent_id) for inst in state.values() if inst.subagent_id}
        steps |= {info.id for info in _agents(subagents) if info.parent_run == lane}
        if _stopped(ending):
            # What the agents its steps launched started from their own sessions ends too.
            steps |= {info.id for info in _launched_agents(run, subagents)}
        sessions = frozenset(f"subagent:{agent_id}" for agent_id in steps)
        own = f"{lane}:"
        name = str(run.title or run.workflow_name or run.id)
        clause = because or f"the workflow run “{name}” that started it {ending}"
        ended = await end_started(
            subagents=subagents,
            supervisor=supervisor,
            owns=lambda key: key in sessions or key.startswith(own),
            clause=clause,
        )
        launched = await _end_launched(
            run, ending=ending, clause=clause, subagents=subagents, supervisor=supervisor
        )
    except Exception:
        logger.warning("run %s: could not end what its steps started", run.id, exc_info=True)
        return 0
    return ended.runs + ended.subagents + launched


def _stopped(ending: str) -> bool:
    """Whether a run that ended *ending* was stopped: its owner's Cancel, an ending that stopped
    it, or its deletion."""
    from personalclaw.loop.children import DELETED
    from personalclaw.workflows.models import RunStatus, run_ending

    return ending in {run_ending(RunStatus.CANCELLED), DELETED}


def _launched_agents(run: Any, subagents: Any) -> list[Any]:
    """The agents run *run*'s Invoke Agent and Run Prompt steps launched
    (``SubagentInfo.workflow_run``)."""
    return [
        info
        for info in _agents(subagents)
        if str(getattr(info, "workflow_run", "") or "") == run.id
    ]


def _let_go(run_id: str) -> set[str]:
    """The child runs a settled `subworkflow` step of run *run_id* recorded
    (``child_run_attach``): that step stopped waiting for them, so they go on alone."""
    from personalclaw.workflows import journal

    return {
        str(row.get("child_run_id") or "")
        for row in journal.ledger(run_id, kinds={journal.CHILD_RUN_ATTACH})
    }


def _launched_runs(run: Any, *, stopped: bool) -> list[Any]:
    """The runs run *run*'s steps launched that end with it (*stopped*: it was stopped). Never a
    fork, which continues it. A stopped run's include a Run workflow step's run, live or still
    queued behind its workflow's overlap policy (a DRAFT, ``overlap.is_queued``). One that ended
    unfinished ends only the subworkflow runs it was still waiting on."""
    from personalclaw.workflows import overlap, store
    from personalclaw.workflows.models import RunStatus

    let_go = set() if stopped else _let_go(run.id)
    found: dict[str, Any] = {}
    for child in store.active_runs():
        if child.parent_run_id != run.id or child.forked_from or child.id in let_go:
            continue
        if child.spawned_by_node_id and not stopped:
            continue
        found[child.id] = child
    if stopped:
        for child in store.started_runs(run.id):
            if child.status == RunStatus.DRAFT and overlap.is_queued(child):
                found.setdefault(child.id, child)
    return list(found.values())


async def _end_launched(
    run: Any, *, ending: str, clause: str, subagents: Any, supervisor: Any
) -> int:
    """End what run *run*'s steps launched, by how it ended (*ending*, the module's rule): the
    runs :func:`_launched_runs` names, and, when it was stopped, the agents whose
    ``workflow_run`` it is (an Invoke Agent or Run Prompt step's). Each ends saying *clause*,
    through the paths every ending uses, so a cancelled child's own ending ends what it launched
    in turn.
    Returns how many it reached. Fail-open, one by one."""
    from personalclaw.workflows import service, store
    from personalclaw.workflows.models import RunStatus, run_ending

    stopped = _stopped(ending)
    unfinished = {
        run_ending(s) for s in (RunStatus.FAILED, RunStatus.ESCALATED, RunStatus.DECLINED)
    }
    if not stopped and ending not in unfinished:
        return 0

    reached = 0
    for child in _launched_runs(run, stopped=stopped):
        if store.cancel_requested(child.id):
            continue
        try:
            if service.cancel_run(child.id, supervisor=supervisor, reason=clause).get("ok"):
                reached += 1
        except Exception:
            logger.warning("could not stop run %s (%s)", child.id, clause, exc_info=True)
    if not stopped or subagents is None:
        return reached
    going = [info.id for info in _launched_agents(run, subagents) if not info.done]
    if going:
        try:
            reached += await subagents.stop_agents(going, because=clause)
        except Exception:
            logger.warning("could not stop subagents %s (%s)", going, clause, exc_info=True)
    return reached
