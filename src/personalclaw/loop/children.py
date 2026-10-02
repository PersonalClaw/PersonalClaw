"""The work a loop started, which ends when the loop does.

A loop's worker starts work that outlives the turn that started it. Two or more tasks handed to
``subagent_run`` run as one workflow run (a batch), and one task runs as a background subagent.
Each records the worker session it was started from: the run its ``origin.session_key``
(``workflows.service.start_run``, from the call's session header), the subagent its
``parent_session_key``. That is the link, and it is exact: a loop's worker sessions are
``loop-<id>`` and ``loop-<id>-<task>`` (``manager.worker_loop_id``), and nothing else is named so.

When the loop ends, that work has nobody left to report to. Stopping a loop ends every child it
started (:func:`end_children`), and so do its failing and its deletion: each workflow run is
cancelled, saying how its loop ended, and each subagent is stopped, so an approval either of them
was waiting on ends with them and can no longer be allowed. What a run was asking for ends through
the run (its stages' subagents are stopped, and each approval ends naming the loop:
``dashboard.approval_owner``).

A child can also outlive a loop that ended some other way (it finished), or one stopped while the
gateway was down. So the workflow supervisor asks :func:`parent_ended` of every run it is about to
drive, at boot and on every poll after, and a run whose loop has ended is cancelled rather than
resumed.

"Ended" is the loop's own ENDED phase — stopped, failed, finished — or gone: the same rule the
decision path holds a loop's worker's own approvals to. A FAILED loop keeps its workers' worktrees
for a Resume (``manager.stand_down``), but a batch run is no worker's: its turn was stopped with the
failure, nothing would read what it found, and asking her to allow its steps would start agents for
a loop that has failed. A paused loop has not ended, and its children go on.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: How an ended loop ended, as the rest of "its loop …" or "the loop that asked for it …": one
#: table for its children and its worker's own approvals (``dashboard.approval_owner``).
ENDINGS = {"stopped": "was stopped", "failed": "failed", "complete": "has finished"}

#: The ending of a loop whose row is gone.
DELETED = "was deleted"


def loop_of(session_key: str) -> str:
    """The loop whose worker session *session_key* is (in either form: bare or ``dashboard:``),
    or ``""`` when it is no loop worker's."""
    from personalclaw.constants import DASHBOARD_SESSION_PREFIX
    from personalclaw.loop.manager import worker_loop_id

    return worker_loop_id(str(session_key or "").removeprefix(DASHBOARD_SESSION_PREFIX))


def why_over(loop_id: str) -> str:
    """How loop *loop_id* ended ("was stopped", "failed", "has finished", "was deleted"), or ""
    while it has not, and what it started may still report to it."""
    from personalclaw.loop import store
    from personalclaw.loop.loop import ENDED_STATUSES

    loop = store.get(loop_id)
    if loop is None:
        return DELETED
    status = str(getattr(loop.status, "value", loop.status))
    if status not in {s.value for s in ENDED_STATUSES}:
        return ""
    return ENDINGS.get(status, f"is {status}")


def _clause(loop_id: str, why: str) -> str:
    """ "its loop “Release notes” was stopped": how a child names the loop that ended it."""
    from personalclaw.loop import store

    loop = store.get(loop_id)
    name = str(getattr(loop, "name", "") or "").strip() if loop is not None else ""
    return f"its loop “{name}” {why}" if name else f"its loop {why}"


def parent_ended(run: Any) -> str:
    """Why the loop that started workflow run *run* is over, as the clause its cancel carries
    ("its loop “Release notes” was stopped"), or "" when no loop started it or that loop is still
    one it reports to."""
    loop_id = loop_of(getattr(getattr(run, "origin", None), "session_key", ""))
    if not loop_id:
        return ""
    why = why_over(loop_id)
    return _clause(loop_id, why) if why else ""


def ended_by(run: Any) -> str:
    """How an approval a stage of *run* asked for reads once the loop that started the run is
    over: "the loop that started its run was stopped", or ""."""
    loop_id = loop_of(getattr(getattr(run, "origin", None), "session_key", ""))
    why = why_over(loop_id) if loop_id else ""
    return f"the loop that started its run {why}" if why else ""


def child_runs(loop_id: str) -> list[Any]:
    """The workflow runs loop *loop_id* started that are still going."""
    from personalclaw.workflows import store

    return [run for run in store.active_runs() if loop_of(run.origin.session_key) == loop_id]


async def end_children(state: Any, loop_id: str, *, why: str) -> int:
    """End every child loop *loop_id* started, as its loop *why* ("was stopped"). Returns how many.

    Each workflow run is cancelled with the reason, and the controller driving it ends it saying
    so, stopping its stages' subagents (whose approvals end naming the loop). Each subagent a
    worker started is stopped with the reason. Called before the loop's worker turns are stopped,
    so a subagent a turn started ends saying its loop was stopped, rather than as that turn's.

    Fail-open, child by child: a child that will not stop must not keep the loop from reaching
    the state its owner asked for, and the workflow supervisor ends any run left behind
    (:func:`parent_ended`).
    """
    from personalclaw.workflows import service

    clause = _clause(loop_id, why)
    supervisor = getattr(state, "workflows", None)
    ended = 0
    for run in child_runs(loop_id):
        try:
            if service.cancel_run(run.id, supervisor=supervisor, reason=clause).get("ok"):
                ended += 1
        except Exception:
            logger.warning("loop %s: could not stop its run %s", loop_id, run.id, exc_info=True)
    manager = getattr(state, "subagents", None)
    if manager is None:
        return ended
    for info in list(getattr(manager, "running", None) or []):
        if loop_of(getattr(info, "parent_session_key", "")) != loop_id:
            continue
        try:
            if await manager.cancel(info.id, reason=f"Cancelled because {clause}"):
                ended += 1
        except Exception:
            logger.warning("loop %s: could not stop subagent %s", loop_id, info.id, exc_info=True)
    if ended:
        logger.info("loop %s %s: ended %d of the things it started", loop_id, why, ended)
    return ended
