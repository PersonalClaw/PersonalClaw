"""Where a pending approval came from, in the few words every surface names it by.

An approval is raised by a chat's own call, a loop's worker (which asks on the chat path, with no
source of its own), a workflow run's step, a trigger's run, a subagent, a room member, or an MCP
server's question. The registry's entry carries those words as ``source_label``
(``dashboard.approval_state``), the dashboard's cards say "From <label>", the approval's Inbox row
keeps them, and a channel's prompt is tagged with them (``ChannelDelivery.request_approval``'s
``source``). The gateway asks a channel for a background call before the entry exists, and names
it here the same way (:func:`source_label_in`).

Wording helpers, so a store that cannot be read costs a name and never the approval it describes.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.security import redact_field

logger = logging.getLogger(__name__)

#: The registry's ``source`` for a room member's call (``rooms.posture.registry_approver``). The
#: entry's ``session`` is then the member's own session key (``rooms.turn.session_key``).
ROOM_SOURCE = "room"


def room_member_of(session: str) -> tuple[str, str] | None:
    """The member whose room session *session* is, and its room's title, masked ("" for a room
    that is gone or cannot be read), or None when *session* is not a room member's."""
    from personalclaw.rooms.turn import parse_session_key

    parsed = parse_session_key(session)
    if parsed is None:
        return None
    room_id, member = parsed
    try:
        from personalclaw.rooms import store

        room = store.get_room(room_id)
    except Exception:  # noqa: BLE001 - a wording helper never fails the approval it describes
        return member, ""
    title = str(getattr(room, "title", "") or "") if room is not None else ""
    return member, redact_field(title)


def loop_name_of(session: str) -> str | None:
    """The name of the loop whose worker or planner holds *session* ("" for a loop with none, or
    one that is gone), or None when *session* is not one of a loop's."""
    from personalclaw.loop import manager as loop_manager
    from personalclaw.loop.plan_walkthrough import planner_loop_id

    loop_id, _task_id = loop_manager.worker_ids(session)
    if not loop_id:
        loop_id = planner_loop_id(session)
    if not loop_id:
        return None
    try:
        from personalclaw.loop import store

        loop = store.get(loop_id)
    except Exception:  # noqa: BLE001 - a wording helper never fails the approval it describes
        return ""
    return str(getattr(loop, "name", "") or "").strip() if loop is not None else ""


def _quoted(kind: str, name: str) -> str:
    return f"{kind} “{name}”" if name else kind


def live_chat_name(sessions: Any, key: str) -> str:
    """The name of the live chat *key* in *sessions*, or "": a session's title defaults to its key
    until the chat is named, and a key is not a title."""
    live = sessions.get(key) if key and sessions else None
    title = str(getattr(live, "title", "") or "") if live is not None else ""
    return title if title != key else ""


def _run_of(run_id: str) -> Any:
    """The workflow run *run_id*, or None when it is gone or unreadable."""
    try:
        from personalclaw.workflows import store as run_store

        return run_store.get(run_id)
    except Exception:  # noqa: BLE001 - a wording helper never fails the approval it describes
        return None


def _run_name_of(run_id: str) -> str:
    """What a workflow run is called: its title (a loop started as a run keeps the loop's name
    there), else its workflow's name; "" when the run is gone or unreadable."""
    run = _run_of(run_id)
    if run is None:
        return ""
    return str(run.title or run.workflow_name or "").strip()


def is_a_batch(run_id: str) -> bool:
    """Whether the run *run_id* is a subagent batch (``workflows.batch_start``), as an ask names it:
    one ``subagent_run`` compiled, not one of its sub-runs."""
    from personalclaw.workflows.models import OriginKind

    run = _run_of(run_id)
    origin = getattr(run, "origin", None)
    return getattr(origin, "kind", None) == OriginKind.SUBAGENT_TOOL and not getattr(
        run, "parent_run_id", None
    )


def step_name(run_id: str, node_id: str) -> str:
    """A run's step as the run page names it: its label, else its id."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import Node, walk

    try:
        spec = store.read_spec(run_id) or {}
        for _path, node in walk(Node.from_dict(spec.get("root") or {})):
            if node.id == node_id:
                return node.label or node_id
    except Exception:  # noqa: BLE001 - a wording helper never fails the approval it describes
        logger.debug("could not read the steps of run %s", run_id, exc_info=True)
    return node_id


def whose_work(state: Any, *, session: str, approval_id: str = "") -> str:
    """Whose work an ask is when it is an app's, in words (``apps.app_work.named``): the app, and
    its scheduled job when the job's fire started the work; ``""`` for your own. A batch's ask by
    the batch's record, any other ask by the session it was asked under (``app_work.of_session``:
    a step of a run that is the app's, a subagent its agent started)."""
    from personalclaw.apps import app_work
    from personalclaw.workflows.batch_start import work_of

    try:
        work = work_of(approval_id) or (app_work.of_session(state, session) if session else None)
    except Exception:  # noqa: BLE001 - a wording helper never fails the approval it describes
        logger.debug("whose work the ask %s is could not be read", approval_id, exc_info=True)
        return ""
    return app_work.named(work) if work is not None else ""


def approval_source_label(
    *,
    source: str,
    session: str,
    trigger: str = "",
    trigger_name: str = "",
    title: str = "",
    approval_id: str = "",
    whose: str = "",
) -> str:
    """Where a pending approval came from, in the few words every surface shows it by.

    The dashboard's cards say "From <label>", and a channel's prompt is tagged ``[<label>]``
    (``ChannelDelivery.request_approval``'s ``source``): ``chat “Trip planning”``,
    ``loop “Fix the README”``, ``workflow “deep-research” · step “sweep”``,
    ``trigger “Friday digest”``, ``subagent of chat “Trip planning”``,
    ``batch of chat “Trip planning”``, ``MCP server “deepwiki”``,
    ``room “Is the demo worth it?” · member “talk-editor”``; a chat's own agent asking
    through the queue (*source* ``agent``) is its chat. A name that is not known leaves the bare
    kind (``chat``, ``loop``), which is still true. The work is read in the order that decides it:
    the trigger
    whose run asked, the workflow step whose session it is, the loop whose worker or planner it
    is, then the
    chat or the background origin. A loop's worker asks on the chat path, with no source of its
    own, and used to be called a chat for it.

    *source* is the registry's (``""`` for a chat's own call), *session* the key it asked under,
    *trigger* and *trigger_name* the trigger whose run asked, and *title* the chat's name. The
    names are the ones the entry shows, already masked. *approval_id* is the ask's own id: a
    batch's ask (``workflows.batch_start``) is raised on the subagent path before any of its
    tasks exists, so it is named the ``batch`` of its chat or loop, not a ``subagent`` of it.
    *whose* is whose work it is when it is an app's (:func:`whose_work`): its batch, its run's
    step and its subagent are named by it (``batch of the app “Research Lab” · step “…”``).
    """
    if trigger:
        return _quoted("trigger", trigger_name)
    from personalclaw.workflows.ownership import parse_owned

    step = parse_owned(session)
    if step is not None:
        run = _quoted("workflow", redact_field(_run_name_of(step[0])))
        if whose:
            run = f"batch of {whose}" if is_a_batch(step[0]) else f"{run} of {whose}"
        return f"{run} · step “{redact_field(step_name(*step))}”"
    from personalclaw.workflows.owner_allow import BATCH_PREFIX

    started = "batch" if approval_id.startswith(BATCH_PREFIX) else "subagent"
    if whose and source == "subagent":
        return f"{started} of {whose}"
    loop_name = loop_name_of(session)
    if loop_name is not None:
        where = _quoted("loop", redact_field(loop_name))
        return f"{started} of {where}" if source == "subagent" else where
    if session.startswith("cron:"):
        from personalclaw.triggers.store import trigger_name as name_of_trigger

        return _quoted("trigger", redact_field(name_of_trigger(session.split(":")[1])))
    if source.startswith("mcp:"):
        return _quoted("MCP server", source.removeprefix("mcp:"))
    if source == ROOM_SOURCE and (seat := room_member_of(session)) is not None:
        member, room = seat
        return f"{_quoted('room', room)} · member “{member}”"
    if source == "subagent":
        return f"{started} of {_quoted('chat', title)}" if title else started
    if not source:
        return _quoted("chat", title)
    if source == "agent":
        # The session's own agent, asking for its owner's Allow of what it cannot give itself
        # (`workflows.owner_allow`) through the queue rather than its turn's own card.
        return _quoted("chat", title) if title else "agent"
    return "background task"


def source_label_in(
    state: Any, *, source: str, session: str, trigger: str, approval_id: str = ""
) -> str:
    """:func:`approval_source_label` for a call about to ask, named from the live chats of the
    dashboard *state* and the trigger store the way its registry entry names it: the gateway asks a
    channel before the entry exists."""
    from personalclaw.triggers.store import trigger_name

    return approval_source_label(
        source=source,
        session=session,
        trigger=trigger,
        trigger_name=redact_field(trigger_name(trigger)) if trigger else "",
        title=redact_field(live_chat_name(getattr(state, "_sessions", None), session)),
        approval_id=approval_id,
        whose=whose_work(state, session=session, approval_id=approval_id),
    )
