"""Where a pending approval came from, in the few words every surface names it by.

An approval is raised by a chat's own call, a loop's worker (which asks on the chat path, with no
source of its own), a workflow run's step, a trigger's run, a subagent, or an MCP server's
question. The registry's entry carries those words as ``source_label``
(``dashboard.approval_state``), the dashboard's cards say "From <label>", the approval's Inbox row
keeps them, and a channel's prompt is tagged with them (``ChannelDelivery.request_approval``'s
``source``). The gateway asks a channel for a background call before the entry exists, and names
it here the same way (:func:`source_label_in`).

Wording helpers, so a store that cannot be read costs a name and never the approval it describes.
"""

from __future__ import annotations

from typing import Any

from personalclaw.security import redact_field


def loop_name_of(session: str) -> str | None:
    """The name of the loop whose worker or planner holds *session* ("" for a loop with none, or
    one that is gone), or None when *session* is not one of a loop's."""
    from personalclaw.loop import manager as loop_manager
    from personalclaw.loop.plan_walkthrough import planner_loop_id

    loop_id = loop_manager.worker_loop_id(session)
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


def _run_name_of(run_id: str) -> str:
    """What a workflow run is called: its title (a loop started as a run keeps the loop's name
    there), else its workflow's name; "" when the run is gone or unreadable."""
    try:
        from personalclaw.workflows import store as run_store

        run = run_store.get(run_id)
    except Exception:  # noqa: BLE001 - a wording helper never fails the approval it describes
        return ""
    if run is None:
        return ""
    return str(run.title or run.workflow_name or "").strip()


def approval_source_label(
    *, source: str, session: str, trigger: str = "", trigger_name: str = "", title: str = ""
) -> str:
    """Where a pending approval came from, in the few words every surface shows it by.

    The dashboard's cards say "From <label>", and a channel's prompt is tagged ``[<label>]``
    (``ChannelDelivery.request_approval``'s ``source``): ``chat “Trip planning”``,
    ``loop “Fix the README”``, ``workflow “deep-research” · step “sweep”``,
    ``trigger “Friday digest”``, ``subagent of chat “Trip planning”``,
    ``MCP server “deepwiki”``. A name that is not known leaves the bare kind (``chat``,
    ``loop``), which is still true. The work is read in the order that decides it: the trigger
    whose run asked, the workflow step whose session it is, the loop whose worker or planner it
    is, then the
    chat or the background origin. A loop's worker asks on the chat path, with no source of its
    own, and used to be called a chat for it.

    *source* is the registry's (``""`` for a chat's own call), *session* the key it asked under,
    *trigger* and *trigger_name* the trigger whose run asked, and *title* the chat's name. The
    names are the ones the entry shows, already masked.
    """
    if trigger:
        return _quoted("trigger", trigger_name)
    from personalclaw.workflows.ownership import parse_owned

    step = parse_owned(session)
    if step is not None:
        return f"{_quoted('workflow', redact_field(_run_name_of(step[0])))} · step “{step[1]}”"
    loop_name = loop_name_of(session)
    if loop_name is not None:
        where = _quoted("loop", redact_field(loop_name))
        return f"subagent of {where}" if source == "subagent" else where
    if session.startswith("cron:"):
        from personalclaw.triggers.store import trigger_name as name_of_trigger

        return _quoted("trigger", redact_field(name_of_trigger(session.split(":")[1])))
    if source.startswith("mcp:"):
        return _quoted("MCP server", source.removeprefix("mcp:"))
    if source == "subagent":
        return f"subagent of {_quoted('chat', title)}" if title else "subagent"
    if not source:
        return _quoted("chat", title)
    return "background task"


def source_label_in(sessions: Any, *, source: str, session: str, trigger: str) -> str:
    """:func:`approval_source_label` for a call about to ask, named from the live *sessions* and
    the trigger store the way its registry entry names it: the gateway asks a channel before the
    entry exists."""
    from personalclaw.triggers.store import trigger_name

    return approval_source_label(
        source=source,
        session=session,
        trigger=trigger,
        trigger_name=redact_field(trigger_name(trigger)) if trigger else "",
        title=redact_field(live_chat_name(sessions, session)),
    )
