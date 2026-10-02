"""What a turn or a run started, which ends when it does.

A chat's turn, a loop's worker and a workflow run's steps start work that outlives the call that
started it. Two or more tasks handed to ``subagent_run`` run as one workflow run (a batch), and one
task runs as a background subagent. Each records the session it was started from: the run its
``origin.session_key`` (the call's session header, ``workflows.service.start_run``), the subagent
its ``parent_session_key``. That is the link, and one rule holds over it: when what started the work
ends, the work ends with it, saying why. Nothing would read what it found, and asking her to allow
its steps would start agents for work that is over.

* A loop's ending ends what its workers started (``loop.children.end_children``).
* A turn's Stop ends what the turn started (:func:`end_turn`, which the dashboard state hands
  ``SessionManager.stop_turn``): what the turn's session started since the turn began. What an
  earlier turn of the same chat started is that turn's, which ended without being stopped, so it
  goes on.
* A workflow run's ending ends what its steps started (:func:`end_run`, from
  ``RunController._finish`` and ``service.delete_run``): what the session of one of its steps'
  subagents (``subagent:<id>``) started.

And what an ended subagent started from its own session ends with it, so a batch a background
subagent started does not outlive the Stop that ends the subagent.

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
    """What an ending reached: the runs whose cancel it asked, and the subagents it stopped (a
    cancelled run's steps still running among them, which that run's controller stops)."""

    runs: int
    subagents: int


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
) -> Ended:
    """End what the sessions *owns* claims started, and what each subagent among it started in
    turn, as *clause* ("its chat turn was stopped").

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
    if runs or stopped:
        logger.info("ended %d run(s) and %d subagent(s): %s", runs, stopped, clause)
    return Ended(runs=runs, subagents=stopped + steps)


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
        sessions = frozenset(f"subagent:{agent_id}" for agent_id in steps)
        own = f"{lane}:"
        name = str(run.title or run.workflow_name or run.id)
        ended = await end_started(
            subagents=subagents,
            supervisor=supervisor,
            owns=lambda key: key in sessions or key.startswith(own),
            clause=because or f"the workflow run “{name}” that started it {ending}",
        )
    except Exception:
        logger.warning("run %s: could not end what its steps started", run.id, exc_info=True)
        return 0
    return ended.runs + ended.subagents
