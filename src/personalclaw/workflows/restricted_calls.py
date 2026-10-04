"""What a call made for an Incognito or Temporary chat may change in your workflows.

One rule, asked by both doors a workflow tool's call reaches the engine through, before anything
changes: the gateway's workflow routes (`handlers._guard`, and the loop door that shares it), which
the owner's pages and the tool server an agent CLI runs call, and the workflow tools a native agent
calls inside the gateway (`mcp_workflows`), which reach the engine without a route.

Such a call may start a run or a batch: the run keeps the chat's mode and stays on its model
(`service.start_run`, through `ownership.inherit_mode`). It may control a run it started that keeps
nothing as it does: start its draft, edit it, skip, rewind or re-run its steps, fork it, pause,
resume or stop it. Anything else it would change keeps what it keeps (a definition in your library,
a run that you or another chat started), so it is refused, and so is every call made for work whose
chat's mode cannot be read. The refusal says why, in the words of the one reader of a session's mode
(``memory_writes.session_mode``), and leaves a security-log row where it is made.

What the call is made for is read from two records, and the strictest wins, as a run's inherited
mode is read (`ownership.inherit_mode`): the session it names, judged up the chain it works for to
the chat at the top (``memory_reads.reach_of``: a subagent's or a workflow step's call is its
chat's), each session read over the gateway's live chats, and the work it runs inside
(``memory_writes.restricted_mode``), so a native agent's call is held to its chat's turn whichever
session it names: ``memory_reads.keeps_nothing``, the answer the gateway's routes and the agent's
artifact tools ask too. A call made for no chat (yours, from your own pages, a scheduled job's, an
app's) keeps what it does and is not held to this rule.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw import memory_writes
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

#: The stable code a refusal carries, on the wire (``{"error": {"code": ...}}``) and in a tool's
#: answer alike.
CODE = "restricted_session"

#: What such a call may start: a run, a batch. Each keeps the chat's mode and stays on its model.
STARTS_WORK = frozenset({"workflow_run_start", "workflow_batch_start"})

#: What such a call may do to a run it started, which keeps nothing as the chat does: what its
#: agent's workflow tools do to a run.
CONTROLS_ITS_RUN = frozenset(
    {
        "workflow_run_start",  # its draft
        "workflow_run_edit",
        "workflow_run_rewind",
        "workflow_run_from",
        "workflow_run_fork",
        "workflow_run_pause",
        "workflow_run_resume",
        "workflow_run_cancel",
    }
)

#: The key the dashboard's own pages send: the owner, not a session.
_DASHBOARD_UI = "dashboard:ui"
_DASHBOARD = "dashboard:"


def refusal(session_key: str, operation: str, *, run_id: str = "", state: Any = None) -> str:
    """Why a call made for the session *session_key* may not make *operation* (on the run
    *run_id*, when the call is for one), or ``""`` when it may. *state* is the gateway's dashboard
    state, whose live chats the one reader of a session's mode reads first. A refusal is audited
    here."""
    from personalclaw import memory_reads

    mode = memory_reads.keeps_nothing(state, session_key)
    if not mode:
        return ""
    why = memory_reads.why_it_keeps_nothing(mode)
    if mode in memory_writes.RESTRICTED_MODES:
        if run_id and operation in CONTROLS_ITS_RUN:
            if its_own_run(session_key, run_id):
                return ""
            why += ", so it changes only a run it started, which keeps nothing as it does"
        elif not run_id and operation in STARTS_WORK:
            return ""
    _audit(session_key, operation, run_id)
    return why


def sentence(why: str) -> str:
    """What a refused call is told, *why* being :func:`refusal`'s answer."""
    return f"this session cannot mutate: {why}"


def its_own_run(session_key: str, run_id: str) -> bool:
    """Whether the run *run_id* is the work of the restricted session *session_key*: started by it
    (a fork keeps the origin of the run it forks, and a subworkflow's tree is rooted in the run that
    started it) and keeping nothing, as the session keeps nothing (`ownership.run_mode`). A call
    that names no chat started no run.

    True for a run there is none of: the call is answered that there is no such run, and changes
    nothing."""
    from personalclaw.workflows import ownership, store

    run = store.get(run_id)
    if run is None:
        return True
    chat = _chat(session_key)
    if not chat or ownership.run_mode(run) is ownership.MemoryMode.NORMAL:
        return False
    root = store.get(run.root_run_id) if run.root_run_id not in ("", run.id) else None
    return any(_chat(r.origin.session_key) == chat for r in (run, root) if r is not None)


def _chat(session_key: str | None) -> str:
    """The chat a key names, as a dashboard chat's key and its bare name both name it."""
    return (session_key or "").strip().removeprefix(_DASHBOARD)


def _audit(session_key: str, operation: str, run_id: str) -> None:
    try:
        sel().log_api_access(
            caller=(session_key or "").strip() or _DASHBOARD_UI,
            operation=operation,
            outcome="denied",
            resources=run_id,
        )
    except Exception:  # noqa: BLE001 - an audit failure never decides the refusal it records
        logger.debug("workflow refusal audit skipped", exc_info=True)
