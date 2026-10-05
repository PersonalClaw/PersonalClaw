"""A spawn that has not started yet, kept on disk while it waits, so a restart neither drops it nor
starts it with more than it had.

A spawn waits before it starts: for a free slot, while the concurrency limit or its fan-out's lane
is full (``SubagentManager``'s queue), or for its owner's Allow of its start. It is recorded the
moment it starts to wait, in the folder every agent keeps (``subagent_persistence``): ``queued`` or
``asking``, with its ``request``, everything it is started again from (:func:`record`). When it
starts, the folder becomes the running agent's. When it ends without starting (her Deny, an
approval nobody answered, a cancel, a refusal), the folder goes (:func:`forget`), so nothing brings
it back.

The record carries no grant. A spawn its caller holds on to is not recorded at all (:func:`kept`): a
workflow run's step, whose run starts the step again when it resumes, asking again unless her Allow
of exactly that start still stands (``stage_settlement.requeue_orphaned_stages``); an automation's
agent, whose run the stop closes as interrupted and puts on the review, where she runs it again or
dismisses it (``reaper.record_stopped_work``); and a spawn its caller started on consent it gave for
that run (``approval_mode="auto"``) or with a leaf's posture of its own, which that caller, gone
after a restart, held. So every grant a recorded spawn starts on is read when it starts again, as
for a new spawn: its chat's Trust, YOLO, the hook setting. What it was made with is kept, as its
queue held it, since none of it approves a call: its capability class, its sandbox, a dry run, the
files its step may change, the app or project whose work it is, and the run whose step started it
(``SubagentInfo.workflow_run``), which a workflow it starts is held to. Who asked for its work, when
the owner did not, is recorded (``lasting_work.ASKED_BY``) and marked again, so none of her own
grants starts it or approves its calls (``approval_grants``, rule 4), as before the restart.

After a restart :func:`take_back` settles each record, once the gateway can say what it did
(``gateway._settle_left_behind_agents``), in the order the spawns were made, the ones that were
asking first, since they held slots. Each goes back through the door a new spawn takes
(``SubagentManager._admission``, then ``_enter``), its limits and grants read now, so a start nobody
allowed asks again with the same card; or, when it can no longer run, it ends saying why. An app's
work is held to what its app may run now: no wider than the app's tier, and refused once the app
runs no agent work. The files a step may change are held to what an automation may be saved to
change now. A refusal is told where the spawn reports, as any helper's ending is: its chat, which is
still there, or the app that reads it by its id. One whose chat or asking turn is gone has nobody to
tell but the owner, in the restart's notice (``subagent_orphans.announce_orphans``), which names
every spawn this pass settled.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from personalclaw import lasting_work, session_keys
from personalclaw.security import redact_for_display, redact_for_model
from personalclaw.sel import sel
from personalclaw.subagent_persistence import (
    ASKING,
    QUEUED,
    create_agent_folder,
    delete_agent_folder,
    list_waiting,
    read_state,
)

if TYPE_CHECKING:
    from personalclaw.subagent import SubagentInfo, SubagentManager

logger = logging.getLogger(__name__)

#: What a spawn taken back does now, besides waiting again (``QUEUED``, ``ASKING``).
STARTED = "started"
ENDED = "ended"

#: The parents that are a chat, besides a key no kind names (a chat's own: a channel's
#: conversation is kept under its own key): a dashboard chat's key in any of its forms.
_CHATS = (
    session_keys.DASHBOARD,
    session_keys.CHAT,
    session_keys.CHAT_FILE,
    session_keys.SCHEDULE_CHAT,
)

#: Answers for a chat's key why that chat cannot be handed a report now, ``""`` when it can.
ChatCheck = Callable[[str], str]


def kept(info: SubagentInfo) -> bool:
    """Whether a restart keeps *info* while it waits to start: every spawn but those its caller
    holds on to (the module's docstring)."""
    return not (
        info.approval_mode == "auto"
        or info.trigger_id
        or session_keys.WORKFLOW_STEP.names(info.parent_run)
        or info.extra_env
    )


def cut_off(agents: Iterable[SubagentInfo]) -> int:
    """How many of *agents* a restart cuts off: every one that has not ended, but one queued for a
    slot that a restart keeps (:func:`kept`), which loses nothing to it. One asking for its start
    counts: its ask is closed, and asked again after."""
    return sum(1 for a in agents if not (a.done or (a.queued and kept(a))))


def record(info: SubagentInfo, status: str) -> None:
    """Keep *info*, waiting as *status* says (``QUEUED``, ``ASKING``), when a restart keeps it
    (:func:`kept`). One that cannot be written still waits: a restart then loses it, and the log
    says so."""
    if not kept(info):
        return
    try:
        create_agent_folder(
            info.id,
            task=info.task,
            agent=info.agent,
            parent_session=info.parent_session_key,
            max_turns=info.max_turns,
            title=info.title,
            status=status,
            request=_request_of(info),
        )
    except (OSError, ValueError):
        logger.warning("could not record waiting agent %s; a restart loses it", info.id)


def forget(agent_id: str) -> None:
    """Drop the record of a spawn that ended without starting, so no restart brings it back."""
    delete_agent_folder(agent_id)


def _request_of(info: SubagentInfo) -> dict[str, Any]:
    """What a waiting spawn is started again from: its parameters, none of them a grant."""
    from personalclaw.memory_writes import asked_for_work

    return {
        # The task as its model is handed it (`subagent_prompt.first_prompt`), masked, so no
        # secret the task named is written here.
        "prompt": redact_for_model(info._raw_task or info.task),
        "requested_at": info.started,
        "model": info.model,
        "cwd": info.cwd,
        "capability_class": info.capability_class,
        "silent": info.silent,
        "dry_run": info.dry_run,
        "sandbox": info.sandbox,
        "parent_run": info.parent_run,
        "app": info.app,
        # The project its work is: its shell fills a secret from that project's own first.
        "project_id": info.project_id,
        # The files its step may change (real paths), checked again when it is taken back
        # (`write_scope.problem`), and why it may do less than its step asks.
        "may_change": list(info.may_change),
        "held_back": info.held_back,
        # The run whose step started it: a workflow it starts is held to what that run may start
        # (`automation_version.bound_for`), after a restart too.
        "workflow_run": info.workflow_run,
        lasting_work.ASKED_BY: asked_for_work(session_keys.SUBAGENT.key(info.id)),
    }


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def info_of(state: Mapping[str, Any]) -> SubagentInfo | None:
    """The spawn a waiting record keeps (:func:`record`), as it was made; ``None`` for a record
    that cannot be read whole, or that names what no recorded spawn has, which is ended rather than
    guessed at."""
    from personalclaw.subagent import SubagentInfo
    from personalclaw.subagent_persistence import _agent_dir

    request = state.get("request")
    if not isinstance(request, Mapping) or session_keys.WORKFLOW_STEP.names(
        _text(request.get("parent_run"))
    ):
        return None
    may_change = request.get("may_change")
    if not isinstance(may_change, list) or not all(isinstance(p, str) for p in may_change):
        return None
    try:
        agent_id = _agent_dir(_text(state.get("id"))).name
        requested_at = float(request.get("requested_at") or 0.0)
        max_turns = int(state.get("max_turns") or 0)
    except (TypeError, ValueError):
        return None
    info = SubagentInfo(
        id=agent_id,
        task=_text(state.get("task")),
        started=requested_at or time.time(),
        parent_session_key=_text(state.get("parent_session")),
        agent=_text(state.get("agent")),
        capability_class=_text(request.get("capability_class")),
        dry_run=bool(request.get("dry_run")),
        silent=bool(request.get("silent")),
        max_turns=max_turns,
        model=_text(request.get("model")),
        cwd=_text(request.get("cwd")),
        sandbox=_text(request.get("sandbox")) or "none",
        parent_run=_text(request.get("parent_run")),
        title=_text(state.get("title")),
        app=_text(request.get("app")),
        project_id=_text(request.get("project_id")),
        may_change=tuple(may_change),
        held_back=_text(request.get("held_back")),
        workflow_run=_text(request.get("workflow_run")),
    )
    info._raw_task = _text(request.get("prompt")) or info.task
    return info


@dataclass(frozen=True)
class TakenBack:
    """One spawn the last gateway left waiting, as the restart's pass settled it."""

    agent_id: str
    #: What it is called: its title, else the start of its task. Masked.
    name: str
    #: What it was waiting for: ``QUEUED`` (a free slot) or ``ASKING`` (her Allow).
    was: str
    #: What it does now: ``QUEUED``, ``ASKING``, :data:`STARTED` or :data:`ENDED`.
    now: str
    #: Why it ended, when it did.
    why: str = ""
    #: Whether its chat was told it ended.
    told: bool = False


def take_back(manager: SubagentManager, chat_check: ChatCheck | None) -> list[TakenBack]:
    """Settle every spawn the last gateway left waiting: back in, or ended saying why (the
    module's docstring). *chat_check* answers whether a chat is still there to be handed a report
    (``dashboard.chat_persistence.why_no_report_reaches``); ``None`` when no dashboard can say.
    Never raises: a record it cannot settle is logged and left for the next start."""
    settled: list[TakenBack] = []
    for waiting in sorted(list_waiting(), key=_made):
        agent_id = _text(waiting.get("id"))
        if not agent_id or manager.get(agent_id) is not None:
            continue  # this run's own
        try:
            settled.append(_take_one(manager, chat_check, waiting, agent_id))
        except Exception:  # noqa: BLE001 - one record must not cost the others their start
            logger.warning("could not take back waiting agent %s", agent_id, exc_info=True)
    return settled


def _made(waiting: Mapping[str, Any]) -> tuple[int, float]:
    """The order the spawns were made in, those that were asking first: they held slots."""
    request = waiting.get("request")
    at = request.get("requested_at") if isinstance(request, Mapping) else None
    return (
        0 if waiting.get("status") == ASKING else 1,
        at if isinstance(at, (int, float)) else 0.0,
    )


def _take_one(
    manager: SubagentManager,
    chat_check: ChatCheck | None,
    waiting: Mapping[str, Any],
    agent_id: str,
) -> TakenBack:
    was = ASKING if waiting.get("status") == ASKING else QUEUED
    name = redact_for_display(_text(waiting.get("title")) or _text(waiting.get("task"))[:100])
    info = info_of(waiting)
    if info is None:
        forget(agent_id)
        return TakenBack(agent_id, name, was, ENDED, why="its record could not be read")
    request = waiting.get("request")
    asked = lasting_work.recorded(request.get(lasting_work.ASKED_BY) if request else None)
    if asked:
        from personalclaw import session_restrictions

        session_restrictions.mark_asked_by(session_keys.SUBAGENT.key(agent_id), asked)
    gone = _nowhere_to_report(info, chat_check)
    refused = "" if gone else _what_holds_it_now(info)
    why, told = _back_in(manager, info, gone=gone, refused=refused)
    if why or info.done:  # ended at the door, or by its dispatch (its fan-out stopped, say)
        return TakenBack(agent_id, name, was, ENDED, why=why or info.error, told=told)
    if info.queued:
        return TakenBack(agent_id, name, was, QUEUED)
    status = (read_state(agent_id) or {}).get("status")
    return TakenBack(agent_id, name, was, ASKING if status == ASKING else STARTED)


def _back_in(
    manager: SubagentManager, info: SubagentInfo, *, gone: str, refused: str
) -> tuple[str, bool]:
    """Take *info* back through the door a new spawn takes (``SubagentManager._admission``, then
    ``_enter``): its limits and every grant read now, so a start nobody allowed asks again. When
    *gone* says why what it reports to is gone, *refused* why what holds it now does not start it
    (:func:`_what_holds_it_now`), or the door refuses it, it ends saying so; a refusal is
    delivered where it reports, as any helper's ending is, its chat among them. Returns ``(why it
    ended, whether a chat is told)``, ``("", False)`` when it was taken in."""
    why = gone or refused
    if not why:
        why, cwd, agent = manager._admission(
            info.task,
            info.parent_session_key,
            info.agent,
            info.cwd,
            info.parent_run,
            info.capability_class,
        )
        if not why:
            info.cwd, info.agent = cwd, agent
            manager._enter(info)
            return "", False
    info.done = True
    info.error = f"The gateway restarted before it started, and it cannot start now: {why}"
    manager._agents[info.id] = info  # its status reads how it ended, by the id its chat was given
    forget(info.id)
    sel().log_tool_invocation(
        session_key=info.parent_session_key or "",
        source="subagent",
        tool_name="subagent_run",
        outcome="cancelled",
        metadata={"subagent_id": info.id, "reason": why, "after_restart": True},
    )
    if gone or manager._on_done is None:
        return why, False
    manager._enqueue_delivery(info)
    return why, _reports_to_a_chat(info.parent_session_key)


def _reports_to_a_chat(key: str) -> bool:
    """Whether the work *key* names is a chat's: a dashboard chat's, or a channel's conversation."""
    kind = session_keys.judged_kind(key)
    return bool(key) and key != session_keys.DASHBOARD_UI and (kind is None or kind in _CHATS)


def _nowhere_to_report(info: SubagentInfo, chat_check: ChatCheck | None) -> str:
    """Why what *info* reports to is gone after the restart, so it cannot start again and nobody
    but the owner can be told, or ``""`` when it is there."""
    key = info.parent_session_key
    if not key or key == session_keys.DASHBOARD_UI:
        return ""  # the owner's own: a notice tells her how it ends, as before
    kind = session_keys.judged_kind(key)
    if kind is session_keys.APP:
        return ""  # its app reads it by its id
    if kind is None or kind in _CHATS:  # a chat's (`_reports_to_a_chat`)
        return chat_check(key) if chat_check is not None else ""
    return (
        f"the turn that asked for it ({kind.who[0].lower()}{kind.who[1:]}) ended with the restart"
    )


def _what_holds_it_now(info: SubagentInfo) -> str:
    """Why *info* cannot start now on what it was made with, or ``""``: the app whose work it is
    (:func:`_its_app_now`), and the files its step may change, which are held to what an
    automation may be saved to change now (``write_scope.problem``)."""
    if info.app and (refused := _its_app_now(info)):
        return refused
    from personalclaw import write_scope

    return write_scope.problem(list(info.may_change))


def _its_app_now(info: SubagentInfo) -> str:
    """Why the app whose work *info* is cannot start it now, or ``""``, having held *info* to the
    tier that app holds now: an update that narrowed it, or the app switched off, holds the work it
    left waiting as it holds the app's next start (``permissions.agent_tier_now``)."""
    from personalclaw.apps.agent_tiers import capability_class
    from personalclaw.apps.permissions import agent_tier_now, no_agent_work
    from personalclaw.subagent import CAPABILITY_MUTATING, CAPABILITY_RESEARCH
    from personalclaw.subagent_tier import CAPABILITY_TEXT

    tier = agent_tier_now(info.app)
    if not tier:
        return no_agent_work(info.app)
    breadth = {CAPABILITY_TEXT: 0, CAPABILITY_RESEARCH: 1, CAPABILITY_MUTATING: 2}
    now = capability_class(tier)
    if breadth[now] < breadth.get(info.capability_class, 2):
        info.capability_class = now
    return ""


#: What it was waiting for, as the restart's notice says it.
_WAS = {QUEUED: "it had not started", ASKING: "it was waiting for your Allow to start"}
#: What a spawn the restart took back does now, by what it was waiting for, as its notice says it.
_NOW = {
    (QUEUED, QUEUED): "waits for a free slot again",
    (QUEUED, ASKING): "now asks for your Allow to start",
    (QUEUED, STARTED): "has started now",
    (ASKING, ASKING): "asks again, since the restart closed the ask you had not answered",
    (ASKING, QUEUED): "waits for a free slot before it asks again",
    (ASKING, STARTED): "has started now, on an approval that stands",
}


def said(taken: TakenBack) -> str:
    """What the restart's notice says of *taken*."""
    if taken.now != ENDED:
        return f"{_WAS[taken.was]}, and {_NOW[(taken.was, taken.now)]}."
    told = " Its chat was told." if taken.told else ""
    return f"{_WAS[taken.was]}, and was ended: {taken.why}.{told}"
