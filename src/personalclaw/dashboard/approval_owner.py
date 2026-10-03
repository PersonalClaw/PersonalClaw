"""Whether the work that asked for a pending approval can still use an answer.

An approval is asked FOR something: a chat turn, a subagent waiting to start or mid-run, the
workflow run whose stage spawned that subagent, the loop that drives a worker chat. Once that owner
has ended, an answer has nowhere to go, and an Approve delivered anyway does the work for it. That
is what was measured: cancelling a ``deep-research`` run while its ``sweep`` stage waited for spawn
approval left the approval listed on Home and on the cancelled run's page, and approving it logged
``subagent_run spawned`` for ``workflow:df5827ca:sweep`` — a subagent started for a run that no
longer existed.

Ending an owner withdraws its approvals at the source (a run that ends stops the subagents it
dispatched, a stopped turn cancels its own approvals, a cancelled subagent's waiter ends with it).
This module is the second line: the one question the decision path asks before any door delivers
an answer. There is one grammar per owner kind, read off the registry entry:

* a ``spawn:<id>`` / ``subagent:<id>:<request>`` id — a subagent (its start, or one of its calls);
* a ``workflow:<run>:<node>`` session — a workflow run's stage, and the loop that started the
  run, if one did (``loop.children``);
* a ``loop-<id>`` (or ``loop-<id>-<task>``) session — a loop's worker chat;
* a ``room:<room>:<member>`` session of a room member's call (``source`` ``room``) — the room, and
  the member's seat in it.

A chat's own turn needs no entry here: its future is pending exactly while its runner waits, and
the decision path already refuses a future that is not.

**Evidence, not absence of evidence.** A reason is returned only when the owner's own record says
it ended (or is gone). An entry that names no owner this module knows — an MCP elicitation, an
ordinary chat — is live, because nothing says otherwise. A lookup that RAISES is the one exception
and it fails closed: an Approve performs something irreversible, so "could not tell" refuses it,
with a sentence saying exactly that.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: What a 409 says when a lookup could not be completed (see the module docstring).
UNVERIFIABLE = "the work that asked for it could not be confirmed as still running"


def owner_ended(entry: dict[str, Any], *, subagents: Any) -> str:
    """Why the owner of *entry* has ended, as a clause ("the … that asked for it was …"), or "".

    The broadest owner is asked first — the run or loop before the subagent it dispatched — so
    when both have ended the sentence names the one the user acted on: a stage's spawn of a
    cancelled run says the RUN was cancelled, not that some subagent was.
    """
    try:
        return (
            _run_ended(str(entry.get("session") or ""))
            or _loop_ended(str(entry.get("session") or ""))
            or _room_ended(entry)
            or _subagent_ended(str(entry.get("id") or ""), subagents)
        )
    except Exception:
        logger.warning("could not check the owner of approval %s", entry.get("id"), exc_info=True)
        return UNVERIFIABLE


def _subagent_ended(approval_id: str, subagents: Any) -> str:
    from personalclaw.subagent import approval_subagent_id

    agent_id = approval_subagent_id(approval_id)
    if not agent_id or subagents is None:
        return ""
    info = subagents.get(agent_id)
    if info is None:
        return "the subagent that asked for it is no longer running"
    if getattr(info, "cancelled", False):
        return "the subagent that asked for it was cancelled"
    if getattr(info, "done", False):
        return "the subagent that asked for it has already finished"
    return ""


def _run_ended(session_key: str) -> str:
    from personalclaw.workflows import ownership

    owned = ownership.parse_owned(session_key)
    if owned is None:
        return ""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import TERMINAL_RUN_STATUSES, ended_because, run_ending

    run_id = owned[0]
    run = store.get(run_id)
    if run is None:
        return "the workflow run that asked for it was deleted"
    # The loop that started the run, the broadest owner: once it is over, nothing the run does
    # reaches anyone, and its ending is the one the owner acted on (`loop.children`).
    from personalclaw.loop.children import ended_by

    loop_over = ended_by(run)
    if loop_over:
        return loop_over
    if run.status in TERMINAL_RUN_STATUSES:
        return f"the workflow run that asked for it {run_ending(run.status)}"
    if store.cancel_requested(run_id):
        # The controller applies a cancel on its next step; between the request and that step the
        # run still reads RUNNING, and an answer in that window is exactly the one to refuse. It
        # says what ended the run when something other than its owner did (its turn's Stop).
        return ended_because(
            "the workflow run that asked for it was cancelled", store.cancel_reason(run_id)
        )
    return ""


def _room_ended(entry: dict[str, Any]) -> str:
    """Why the room a member's call asked from can no longer use an answer: the room is gone or
    archived, or the member left it. Read strictly, so an unreadable room raises to the caller,
    which refuses the answer (:data:`UNVERIFIABLE`)."""
    from personalclaw.approval_source import ROOM_SOURCE

    if entry.get("source") != ROOM_SOURCE:
        return ""
    from personalclaw.rooms import store
    from personalclaw.rooms.posture import ROOM_ARCHIVED, ROOM_GONE, member_left
    from personalclaw.rooms.turn import parse_session_key

    seat = parse_session_key(str(entry.get("session") or ""))
    if seat is None:
        return ""
    room_id, member = seat
    try:
        room = store.require_room(room_id)
    except store.RoomError as exc:
        if exc.code == "room_not_found":
            return ROOM_GONE
        raise
    if room.archived:
        return ROOM_ARCHIVED
    if room.member(member) is None:
        return member_left(member)
    return ""


def _loop_ended(session_key: str) -> str:
    if not session_key.startswith("loop-"):
        return ""
    from personalclaw.loop.children import why_over
    from personalclaw.loop.manager import worker_ids

    # A loop's stage worker or one of its task workers; a planner is not a loop's worker, so it
    # has no owner here, rather than a wrong one.
    loop_id, _task_id = worker_ids(session_key)
    if not loop_id:
        return ""
    why = why_over(loop_id)
    return f"the loop that asked for it {why}" if why else ""
