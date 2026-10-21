"""Where a pending approval is answered, read from the key of the session it was asked under.

An approval names the session it was asked under, and the page that answers it is the work that
session runs: a chat's own page, a loop's (its workers' and its planner's), a workflow run's (at
its step), or a room's. The gateway reads the key once, here, and the approval carries what it read
(``dashboard.approval_state``): its registry row and ``approval`` frame, its Inbox row, and the
note it leaves when nobody answered it (``auto_denials``). An agent's question's Inbox row carries
it too (``owner_questions``). No surface reads a session key's shape for it (#3889).
"""

from __future__ import annotations

from personalclaw import session_keys
from personalclaw.workref import work_ref

#: The keys an Inbox row keeps the place under, in its ``refs``: their own, so no other row's ref is
#: read as one of them, nor one of them as another row's (a room's pause row is closed on its
#: ``room``, ``rooms.arbiter.PAUSE_REF``, which a member's approval row must not carry).
REFS = {"work": "place_work", "step": "place_step", "room": "place_room"}


def place_of(session: str) -> dict[str, str]:
    """Where *session*'s approval is answered: ``work`` (``chat:<key>`` for a chat's own,
    ``loop:<id>`` for a loop's workers and its planner, ``workflow:<run>`` for a run's steps, or
    ``""`` for work no page answers), ``step`` (the run's step) and ``room`` (the room a member
    asks in). Each is ``""`` where it says nothing."""
    name = str(session or "").removeprefix(session_keys.DASHBOARD.prefix)
    work = step = room = ""
    kind = session_keys.kind_of(name)
    if not name:
        pass
    elif kind is None or kind is session_keys.CHAT:
        work = work_ref("chat", name)
    elif kind is session_keys.LOOP:
        from personalclaw.loop.manager import worker_ids
        from personalclaw.loop.plan_walkthrough import planner_loop_id

        loop_id = worker_ids(name)[0] or planner_loop_id(name)
        work = work_ref("loop", loop_id) if loop_id else ""
    elif kind is session_keys.WORKFLOW_STEP:
        from personalclaw.workflows.ownership import parse_owned

        owned = parse_owned(name)
        if owned is not None:
            work, step = work_ref("workflow", owned[0]), owned[1]
    elif kind is session_keys.ROOM:
        from personalclaw.rooms.turn import parse_session_key

        member = parse_session_key(name)
        room = member[0] if member is not None else ""
    return {"work": work, "step": step, "room": room}


def place_refs(session: str) -> dict[str, str]:
    """Where *session*'s approval or question is answered, as an Inbox row's ``refs`` keep it
    (:data:`REFS`)."""
    return {REFS[field]: value for field, value in place_of(session).items()}
