"""Everything running now: one list of the live work, each piece with what it is doing (#3889).

Each kind of work kept its own list, and only helpers said what they were doing, so a surface that
wanted to know what was running read each kind's own route and guessed the rest. This is the one
list, served as ``GET /api/running``, with a cheap count beside it (``GET /api/running/count``).

**One row per piece of live work.** Each row is ``{work, kind, name, agent, phase, line, since,
started_at, route, session, blocked, waiting}``:

- ``work``, ``kind``, ``session``, ``phase``, ``line`` and ``since`` are the work's own last
  ``work_phase`` frame (``work_phases``, ``chat_phase``), and a row carries the rest of that frame
  too, so a chat's row IS its run state: the same payload the chat list and the chat's detail
  serve, never a second reading of the chat. Work no producer has said anything of yet (a loop or
  a run that was running before a restart, a trigger's fire, a webhook's turn) reads its phase from
  its status, by the vocabulary every surface shares (``work_phase``).
- ``name`` is what the owner calls it (a chat's title, a helper's title or task, a loop's name, a
  run's title or workflow, the trigger's or the webhook's name), redacted and clipped; ``agent``
  the agent doing it, ``""`` for none or several; ``started_at`` when it started; ``route`` the
  dashboard address that opens it, the same for every reader.
- ``blocked`` is true while the work waits on something other than the owner: a slot, a busy
  local model, a retry (``BLOCKED_PHASES``). Its line says which.
- ``waiting`` names the approvals and the owner's questions that wait on her for it.

**What is live.** A chat with a turn running; a helper that has not ended, a queued one included;
a loop or a run that has started and not ended, including one waiting on her (a loop the owner
paused and one not started yet run nothing); a trigger's fire whose work has no row of its own;
a webhook's turn. A loop's workers and planner are their loop: their turns say what the loop is
doing (``work_phases.worker_said``), so they are never rows of their own, and a loop backed by a
workflow run is one row, a loop, named by the run its phases are said as. A run started by
another run's step is its parent's work, which lists it.

**What just finished** is a short tail, never history: the last :data:`TAIL_ROWS` endings, each
kept :data:`TAIL_SECS`, newest first. Dismissing it is a reader's own business; nothing here
deletes a record.

**Who reads what** is the frame's rule (:class:`Reach`): an app reads a row when the rule its socket
hears that work's phase frame by lets it (the state's ``_app_hears_of``), so
its own chats and the helpers working for them, and no work no chat started; an agent reads its
own chat's work; you read everything. The count and the tail are cut the same way.

The gateway's memory is read on its event loop (:func:`taken`), and the stores, the rows and who
reads them off it (:func:`read`), so nothing the loop changes is read from another thread.

**The list says it moved** in one ``running_changed`` frame per window (:data:`WINDOW_SECS`), a
doorbell naming the work that moved, ``{"changed": [{"work", "session"}, ...]}``, never a payload
to render: a reader reads the list again (:func:`changed`). It rings when a row is added or removed
or its phase or line changes, and when what waits on the owner for a row does; a streamed chunk
and a turn's liveness re-send of an unchanged phase ring nothing. An app's socket hears a ring cut
to the work it may hear the phase of, and none when that is nothing (``ws_state``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple
from urllib.parse import quote

from personalclaw import session_keys, workref
from personalclaw.instants import utc_iso
from personalclaw.security import redact_for_display
from personalclaw.textfmt import clip_words
from personalclaw.work_phase import WorkPhase, done_in, loop_phase, run_phase
from personalclaw.workref import work_ref

logger = logging.getLogger(__name__)

#: The clock the tail is read on: the gateway's own, which a test replaces.
_now = time.time

#: How long a name may be, cut at a word.
NAME_LIMIT = 120

#: How many endings the just-finished tail keeps, and for how long each.
TAIL_ROWS = 20
TAIL_SECS = 10 * 60

#: The phases a row is blocked in: waiting on something other than the owner, with its line saying
#: what.
BLOCKED_PHASES = frozenset({WorkPhase.QUEUED, WorkPhase.WAITING_MODEL, WorkPhase.RETRYING})

#: The phases a row waits on the owner in.
WAITING_PHASES = frozenset({WorkPhase.APPROVAL, WorkPhase.INPUT})

#: The loop statuses a loop is live in: started (or being prepared) and not ended. A loop that is
#: ready waits for her to start it and a paused one is hers to resume: neither runs anything.
LIVE_LOOP_STATUSES = frozenset(
    {"intake", "planning", "review", "running", "stagnant", "blocked", "needs_input"}
)

#: The run statuses a workflow run is live in. A draft has not started; a paused run is hers.
LIVE_RUN_STATUSES = ("running", "needs_input")

#: The frame that says the list moved, and how long the changes it names are gathered for: short
#: enough that a phase change reads at once, long enough that a burst is one read of the list.
CHANGED = "running_changed"
WINDOW_SECS = 0.15

#: The fields every row carries, whatever its kind.
ROW_FIELDS = (
    "work",
    "kind",
    "name",
    "agent",
    "phase",
    "line",
    "since",
    "started_at",
    "route",
    "session",
    "blocked",
    "waiting",
)


#: The key a row is ordered by, which no reader is sent.
_ORDER = "_order"


def _name(text: Any, fallback: str) -> str:
    shown = clip_words(redact_for_display(str(text or "").strip()), NAME_LIMIT)
    return shown or fallback


def _iso(epoch: Any) -> str | None:
    try:
        value = float(epoch)
    except (TypeError, ValueError):
        return None
    return utc_iso(value) if value > 0 else None


def _row(
    payload: Mapping[str, Any] | None,
    *,
    work: str,
    kind: str,
    name: str,
    agent: str,
    started_at: str | None,
    route: str,
    session: str,
    fallback: tuple[WorkPhase, str],
    waiting: Iterable[str] = (),
    order: float = 0.0,
) -> dict[str, Any]:
    """One row: the work's last frame when it said one, its status's phase otherwise, with the
    row's own fields beside it. *order* is when the work began, which never changes while it
    runs: the list is ordered by it (:func:`ordered`)."""
    if payload is None:
        phase, line = fallback
        payload = {
            "work": work,
            "kind": kind,
            "session": session,
            "phase": phase.value,
            "line": line,
            "since": started_at,
        }
    row = dict(payload)
    row.update(
        {
            "work": work,
            "kind": kind,
            "name": name,
            "agent": agent,
            "started_at": started_at,
            "route": route,
            "session": session,
            "blocked": WorkPhase(row["phase"]) in BLOCKED_PHASES,
            "waiting": sorted(set(waiting)),
            _ORDER: float(order or 0.0),
        }
    )
    return row


# ── What waits on the owner, and for which work ─────────────────────────────────────────────


def work_of_session(key: str) -> str:
    """The work a session key runs, as the running list names it: a chat's own, a loop's (its
    workers' and its planner's), a workflow run's (its steps'), or ``""`` for any other kind. The
    work an approval asked under it is answered in (``approval_place.place_of``)."""
    from personalclaw.approval_place import place_of

    return place_of(key)["work"]


def approval_work(entry: Mapping[str, Any]) -> tuple[str, ...]:
    """The work an approval waits on the owner for: the helper that asked it (its start or one of
    its calls) and the work its session runs."""
    from personalclaw.subagent import approval_subagent_id

    found: list[str] = []
    helper = approval_subagent_id(str(entry.get("id") or ""))
    if helper:
        found.append(work_ref("subagent", helper))
    session = work_of_session(str(entry.get("session") or ""))
    if session:
        found.append(session)
    return tuple(found)


def _waits(state: Any) -> dict[str, list[str]]:
    """The ids of what waits on the owner, by the work it is for: each pending approval, and each
    question an agent is waiting on her answer to."""
    out: dict[str, list[str]] = {}
    for approval_id, entry in list((getattr(state, "_pending_approvals", None) or {}).items()):
        for work in approval_work({**entry, "id": entry.get("id") or approval_id}):
            out.setdefault(work, []).append(str(entry.get("id") or approval_id))
    questions = getattr(state, "owner_questions", None)
    for session in list(getattr(state, "_sessions", {}).values()):
        cards = questions.pending_for(session.key) if questions is not None else []
        work = work_of_session(session.key) if cards else ""
        for card in cards:
            if work:
                out.setdefault(work, []).append(str(card.get("id") or ""))
    return out


# ── Each kind of work ───────────────────────────────────────────────────────────────────────


def _is_a_chat(session: Any) -> bool:
    """Whether a session the dashboard holds is a chat of its own: not a room member's, not
    incognito or temporary (the chat list's own rule), and not a loop's or a run's, whose turns are
    their loop's and their run's."""
    key = str(session.key)
    if session_keys.ROOM.names(key):
        return False
    if getattr(session, "memory_mode", "persistent") in ("incognito", "temporary"):
        return False
    kind = session_keys.kind_of(key)
    return kind not in (session_keys.LOOP, session_keys.LOOP_COLON, session_keys.WORKFLOW_STEP)


def _chat_route(key: str) -> str:
    return f"#/chat/{quote(key, safe='')}" if key else ""


def _chats(state: Any, waits: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    out = []
    for session in list(getattr(state, "_sessions", {}).values()):
        if not _is_a_chat(session):
            continue
        served = session.run_state
        if served.get("turn") is None:
            continue
        work = served["work"]
        out.append(
            _row(
                served,
                work=work,
                kind="chat",
                name=_name(session.title, session.key),
                agent=str(session.agent or ""),
                started_at=served["turn"].get("started_at"),
                route=_chat_route(session.key),
                session=session.key,
                fallback=(WorkPhase.THINKING, "Thinking"),
                waiting=waits.get(work, ()),
                order=session._run.turn.started_at if session._run.turn is not None else 0.0,
            )
        )
    return out


def _helper_route(info: Any, session: str) -> str:
    """Where a helper opens: the chat it works for, else the work that started it."""
    if session:
        return _chat_route(session)
    trigger = str(getattr(info, "trigger_id", "") or "")
    if trigger:
        return f"#/triggers?open={quote(trigger, safe='')}"
    parent = work_of_session(str(getattr(info, "parent_session_key", "") or ""))
    run = str(getattr(info, "parent_run", "") or "")
    if not parent and run:
        parent = work_ref("workflow", run)
    return _route_of(parent)


def _route_of(work: str) -> str:
    kind, ident = workref.read(work)
    if kind == "loop":
        return f"#/loops/{ident}"
    if kind == "workflow":
        return f"#/workflows/runs/{ident}"
    if kind == "chat":
        return _chat_route(ident)
    return ""


def _helper_row(info: Any, payload: Any, waits: Mapping[str, list[str]]) -> dict[str, Any]:
    from personalclaw import work_phases

    work = work_ref("subagent", str(info.id))
    session = (
        str(payload["session"])
        if payload is not None
        else work_phases.chat_of(str(getattr(info, "parent_session_key", "") or ""))
    )
    fallback = (
        (WorkPhase.QUEUED, "Waiting to start")
        if getattr(info, "queued", False)
        else (WorkPhase.THINKING, "Thinking")
    )
    return _row(
        payload,
        work=work,
        kind="subagent",
        name=_name(getattr(info, "title", "") or getattr(info, "task", ""), "A helper"),
        agent=str(getattr(info, "agent", "") or ""),
        started_at=_iso(getattr(info, "started", 0.0)),
        route=_helper_route(info, session),
        session=session,
        fallback=fallback,
        waiting=waits.get(work, ()),
        order=float(getattr(info, "started", 0.0) or 0.0),
    )


def _helpers(snap: Snapshot) -> list[dict[str, Any]]:
    out = []
    for info in snap.helpers:
        if getattr(info, "done", False):
            continue
        work = work_ref("subagent", str(info.id))
        out.append(_helper_row(info, snap.said.get(work), snap.waits))
    return out


def _loop_route(loop: Any) -> str:
    return f"#/code/{loop.id}" if getattr(loop, "kind", "") == "code" else f"#/loops/{loop.id}"


def _loop_row(snap: Snapshot, loop: Any) -> dict[str, Any]:
    from personalclaw.loop.loop import held_reason

    work = work_ref("loop", str(loop.id))
    status = str(getattr(loop.status, "value", loop.status))
    return _row(
        snap.said.get(work),
        work=work,
        kind="loop",
        name=_name(loop.name or loop.task, "A loop"),
        agent=str(loop.agent or ""),
        started_at=_iso(loop.started_at or loop.created_at),
        route=_loop_route(loop),
        # The chat it was made for, so that chat lists it among its work (`Loop.origin_session`).
        session=str(getattr(loop, "origin_session", "") or ""),
        fallback=loop_phase(status, held=held_reason(status), reason=str(loop.error_message or "")),
        waiting=snap.waits.get(work, ()),
        # Created, not started: a loop's start is reset each time it resumes.
        order=float(loop.created_at or 0.0),
    )


def _loops(snap: Snapshot) -> list[dict[str, Any]]:
    from personalclaw.loop import store

    out = []
    for loop in store.list_all():
        if str(getattr(loop.status, "value", loop.status)) in LIVE_LOOP_STATUSES:
            out.append(_loop_row(snap, loop))
    return out


def _run_row(snap: Snapshot, run: Any) -> dict[str, Any]:
    from personalclaw import work_phases
    from personalclaw.loop.loop import held_reason
    from personalclaw.workflows.models import stamp_epoch

    work = work_ref("workflow", str(run.id))
    status = str(getattr(run.status, "value", run.status))
    if status == "running" and not run.started_at:
        fallback = (WorkPhase.QUEUED, "Waiting to start")  # waiting for a slot
    else:
        fallback = run_phase(status, reason=str(run.error_message or ""), held=held_reason(status))
    stamp = run.started_at or run.created_at
    backed = bool(getattr(run, "loop_kind", ""))
    return _row(
        snap.said.get(work),
        work=work,
        # A loop backed by a run is a loop, named by the run its phases are said as.
        kind="loop" if backed else "workflow",
        name=_name(run.title or run.workflow_name, "A loop" if backed else "A workflow"),
        agent="",
        started_at=_iso(stamp_epoch(stamp)) if stamp else None,
        route=f"#/workflows/runs/{run.id}",
        # The chat that started it, by its own name, as a helper's row names its parent.
        session=work_phases.chat_of(str(run.origin.session_key or "")),
        fallback=fallback,
        waiting=snap.waits.get(work, ()),
        order=stamp_epoch(run.created_at) if run.created_at else 0.0,
    )


def _runs(snap: Snapshot) -> tuple[list[dict[str, Any]], set[str]]:
    """Each live run's row, with the triggers whose fire started one."""
    from personalclaw.workflows import store

    out, fired = [], set()
    for status in LIVE_RUN_STATUSES:
        offset = 0
        while True:
            page, total = store.list_runs(status=status, limit=200, offset=offset)
            for run in page:
                if run.origin.trigger_id:
                    fired.add(str(run.origin.trigger_id))
                if not run.parent_run_id:
                    out.append(_run_row(snap, run))
            offset += len(page)
            if not page or offset >= total:
                break
    return out, fired


def _triggers(started_by: set[str]) -> list[dict[str, Any]]:
    """Each trigger with a fire in flight whose work has no row of its own: once the fire has
    started its helper or its run, that work's row says what it does, and names the trigger."""
    from personalclaw.triggers import claims
    from personalclaw.triggers.store import trigger_name

    out = []
    for trigger_id in claims.running_ids():
        if trigger_id in started_by:
            continue
        since = claims.running_since(trigger_id)
        out.append(
            _row(
                None,
                work=work_ref("trigger", trigger_id),
                kind="trigger",
                name=_name(trigger_name(trigger_id), "An automation"),
                agent="",
                started_at=_iso(since),
                route=f"#/triggers?open={quote(trigger_id, safe='')}",
                session="",
                fallback=(WorkPhase.THINKING, "Running"),
                order=float(since or 0.0),
            )
        )
    return out


# ── A webhook's turns ───────────────────────────────────────────────────────────────────────


@dataclass
class _Hook:
    name: str
    agent: str
    started: float
    turns: int = 1


def _hooks(state: Any) -> dict[str, _Hook]:
    book = getattr(state, "_running_hooks", None)
    if not isinstance(book, dict):
        book = {}
        state._running_hooks = book
    return book


def _hook_work(session_key: str) -> str:
    return work_ref("webhook", str(session_key).removeprefix(session_keys.WEBHOOK.prefix))


def webhook_started(state: Any, session_key: str, *, name: str, agent: str) -> None:
    """A webhook's agent turn under *session_key* started (``hooks._run_hook_agent``)."""
    work = _hook_work(session_key)
    held = _hooks(state).get(work)
    if held is not None:
        held.turns += 1
        return
    _hooks(state)[work] = _Hook(name=str(name or ""), agent=str(agent or ""), started=_now())
    changed(state, work)


def webhook_ended(state: Any, session_key: str, *, outcome: str, said: str) -> None:
    """The turn ended with *outcome* (``"completed"``, else how it stopped), *said* what it said
    of it: it leaves the list for the just-finished tail."""
    work = _hook_work(session_key)
    book = _hooks(state)
    held = book.get(work)
    if held is None:
        return
    held.turns -= 1
    if held.turns > 0:
        return
    book.pop(work, None)
    changed(state, work)
    if outcome == "completed":
        phase, line = WorkPhase.DONE, done_in(max(0.0, _now() - held.started))
    elif outcome == "cancelled":
        phase, line = WorkPhase.DONE, "Stopped"
    else:
        phase, line = WorkPhase.ERROR, clip_words(redact_for_display(said or "Stopped"), 160)
    ended(
        state,
        {
            "work": work,
            "kind": "webhook",
            "session": "",
            "phase": phase.value,
            "line": line,
            "since": utc_iso(_now()),
        },
        name=held.name,
        agent=held.agent,
    )


def _webhooks(state: Any) -> list[dict[str, Any]]:
    out = []
    for work, held in list(_hooks(state).items()):
        out.append(
            _row(
                None,
                work=work,
                kind="webhook",
                name=_name(held.name, "A webhook"),
                agent=held.agent,
                started_at=utc_iso(held.started),
                route="",
                session="",
                fallback=(WorkPhase.THINKING, "Running"),
                order=held.started,
            )
        )
    return out


# ── What just finished ──────────────────────────────────────────────────────────────────────


@dataclass
class _Ended:
    payload: dict[str, Any]
    at: float
    name: str = ""
    agent: str = ""


def _tail(state: Any) -> deque[_Ended]:
    tail = getattr(state, "_running_tail", None)
    if not isinstance(tail, deque):
        tail = deque(maxlen=TAIL_ROWS)
        state._running_tail = tail
    return tail


def chat_ended(state: Any, session: Any) -> None:
    """The chat *session*'s turn said its last frame (``chat_phase.end``): a chat's own joins the
    tail; a loop's worker's turn is its loop's, which says its own ending."""
    if _is_a_chat(session) and session._run is not None and session._run.sent is not None:
        ended(state, session._run.sent)


def ended(state: Any, payload: Mapping[str, Any], *, name: str = "", agent: str = "") -> None:
    """The work *payload* names said its last frame: it joins the just-finished tail, newest
    first, in place of an earlier ending of the same work."""
    tail = _tail(state)
    work = str(payload.get("work") or "")
    for earlier in [e for e in tail if e.payload.get("work") == work]:
        tail.remove(earlier)
    tail.append(_Ended(dict(payload), _now(), name=name, agent=agent))


def _finished_row(snap: Snapshot, held: _Ended) -> dict[str, Any]:
    """An ending as a row, named as the work is named while it runs."""
    payload = held.payload
    work, kind = str(payload["work"]), str(payload["kind"])
    _kind, ident = workref.read(work)
    name, agent, route = held.name, held.agent, ""
    if kind == "chat":
        title, agent = snap.chats.get(ident, (ident, agent))
        name, route = name or title, _chat_route(ident)
    elif kind == "subagent":
        info = next((i for i in snap.helpers if str(i.id) == ident), None)
        if info is not None:
            name = name or str(getattr(info, "title", "") or getattr(info, "task", ""))
            agent = agent or str(getattr(info, "agent", "") or "")
            route = _helper_route(info, str(payload.get("session") or ""))
    elif kind == "loop":
        from personalclaw.loop import store as loops

        loop = loops.get(ident)
        if loop is not None:
            name, agent = name or loop.name or loop.task, str(loop.agent or "")
            route = _loop_route(loop)
        else:
            route = _route_of(work)
    elif kind == "workflow":
        from personalclaw.workflows import store as runs

        run = runs.get(ident)
        if run is not None:
            name = name or run.title or run.workflow_name
            kind = "loop" if getattr(run, "loop_kind", "") else kind
        route = _route_of(work)
    row = _row(
        payload,
        work=work,
        kind=kind,
        name=_name(name, {"webhook": "A webhook", "loop": "A loop"}.get(kind, "Work")),
        agent=agent,
        started_at=None,
        route=route,
        session=str(payload.get("session") or ""),
        fallback=(WorkPhase.DONE, ""),
    )
    row.pop(_ORDER, None)
    return row


# ── The list, and who reads what of it ──────────────────────────────────────────────────────


@dataclass
class Snapshot:
    """What the list reads of the gateway's memory, taken on its event loop (:func:`taken`), so
    nothing the loop changes is read from another thread: the rows of the chats and the webhooks'
    turns, what waits on the owner by work, the helpers, each work's last frame, the
    just-finished tail, and each chat's title and agent for it."""

    rows: list[dict[str, Any]]
    waits: dict[str, list[str]]
    helpers: list[Any]
    said: dict[str, dict[str, Any]]
    tail: list[_Ended]
    chats: dict[str, tuple[str, str]]


def taken(state: Any) -> Snapshot:
    """The list's reading of the gateway's memory. On the event loop, reading no store."""
    from personalclaw import work_phases

    waits = _waits(state)
    said = {
        work: dict(held.sent)
        for work, held in list(work_phases._book(state).items())
        if held.sent is not None
    }
    sessions = list(getattr(state, "_sessions", {}).values())
    return Snapshot(
        rows=_chats(state, waits) + _webhooks(state),
        waits=waits,
        helpers=list(getattr(getattr(state, "subagents", None), "all_agents", None) or []),
        said=said,
        tail=list(_tail(state)),
        chats={str(s.key): (str(s.title or s.key), str(s.agent or "")) for s in sessions},
    )


def read(snap: Snapshot) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The live rows, oldest first, and what just finished, newest first, from *snap* and the
    stores: the helpers, the loops, the runs and the triggers' fires. Off the event loop: each
    store is a read of a file."""
    rows = list(snap.rows) + _helpers(snap)
    started_by = {
        str(getattr(info, "trigger_id", "") or "")
        for info in snap.helpers
        if not getattr(info, "done", False)
    }
    try:
        rows += _loops(snap)
    except Exception:  # noqa: BLE001 - one store that cannot be read never blanks the list
        logger.warning("running now: could not read the loops", exc_info=True)
    try:
        found, fired = _runs(snap)
        rows += found
        started_by |= fired
    except Exception:  # noqa: BLE001 - as above
        logger.warning("running now: could not read the runs", exc_info=True)
    try:
        rows += _triggers(started_by)
    except Exception:  # noqa: BLE001 - as above
        logger.warning("running now: could not read the triggers' fires", exc_info=True)
    now = _now()
    tail = [_finished_row(snap, held) for held in reversed(snap.tail) if now - held.at <= TAIL_SECS]
    return ordered(rows), tail


def ordered(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """By when each piece of work began (a chat's turn, a helper, a loop's or a run's creation, a
    fire, a webhook's turn), which never changes while it runs, so a row never moves as its phase
    does. The key is not sent."""
    out = sorted(rows, key=lambda r: (float(r.get(_ORDER) or 0.0), r["work"]))
    for row in out:
        row.pop(_ORDER, None)
    return out


def live(state: Any) -> list[dict[str, Any]]:
    """Every piece of live work, oldest first. Reads the stores: off the event loop, from a snapshot
    taken on it, in a gateway."""
    return read(taken(state))[0]


class Count(NamedTuple):
    """The count read: how many live, how many blocked, how many waiting on the owner, and how many
    just finished."""

    live: int
    blocked: int
    waiting: int
    finished: int


def counted(rows: Iterable[Mapping[str, Any]], tail: Iterable[Mapping[str, Any]]) -> Count:
    """The count of *rows* and the just-finished *tail*."""
    rows = list(rows)
    return Count(
        live=len(rows),
        blocked=sum(1 for r in rows if r["blocked"]),
        waiting=sum(1 for r in rows if r["waiting"] or WorkPhase(r["phase"]) in WAITING_PHASES),
        finished=len(list(tail)),
    )


@dataclass
class Reach:
    """Who reads the list: you, everything (:attr:`everyone`); an app, what its socket would hear
    of (:attr:`app`); an agent, its own chat's work (:attr:`chat`); anyone else, nothing."""

    state: Any
    everyone: bool = False
    app: str = ""
    chat: str = ""

    def sees(self, row: Mapping[str, Any]) -> bool:
        if self.everyone:
            return True
        session = str(row.get("session") or "")
        if self.app:
            return self.state._app_hears_of(
                self.app, "work_phase", {"work": row.get("work"), "session": session}
            )
        return bool(self.chat) and session == self.chat.removeprefix(session_keys.DASHBOARD.prefix)


def reach_of(request: Any, state: Any) -> Reach:
    """Who *request* reads as, from the principal it proved (``approval_answer.of_request``). Call
    off the event loop: an agent's chat is walked (``subagent_reach``)."""
    from personalclaw import approval_answer
    from personalclaw.subagent_reach import reader_of

    by = approval_answer.of_request(request)
    if by.kind == approval_answer.APP:
        return Reach(state, app=by.name)
    if by.kind not in (approval_answer.OWNER, approval_answer.AGENT):
        return Reach(state)
    reader = reader_of(request, state)
    if reader.everyone:
        return Reach(state, everyone=True)
    return Reach(state, chat=reader.chat)


# ── The list moved ──────────────────────────────────────────────────────────────────────────


@dataclass
class _Bell:
    """The changes gathered for the next ring, by work, each with the chat it is in, and the
    event loop the ring is due on."""

    pending: dict[str, str] = field(default_factory=dict)
    handle: asyncio.TimerHandle | None = None
    loop: asyncio.AbstractEventLoop | None = None


def _bell(state: Any) -> _Bell:
    bell = getattr(state, "_running_bell", None)
    if not isinstance(bell, _Bell):
        bell = _Bell()
        state._running_bell = bell
    return bell


def changed(state: Any, work: str, session: str = "") -> None:
    """The row of *work*, in the chat *session* (``""`` for none), was added or removed, or what
    it says changed: the list rings once its window closes, naming every piece of work that moved
    in it. Called from any thread: off the event loop, the change is handed to it."""
    home = getattr(state, "_ws_loop", None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if home is not None and loop is not home:
        # Off the gateway's loop (a thread, or a loop of its own): the change is handed to it.
        if not home.is_closed():
            home.call_soon_threadsafe(changed, state, work, session)
        return
    if loop is None:
        return
    bell = _bell(state)
    bell.pending[str(work)] = str(session or "")
    if bell.handle is not None and (bell.loop is None or bell.loop.is_closed()):
        bell.handle = None  # due on a loop that is gone: it will never ring
    if bell.handle is None:
        bell.loop = loop
        bell.handle = loop.call_later(WINDOW_SECS, _ring, state)


def _ring(state: Any) -> None:
    bell = _bell(state)
    bell.handle = None
    pending, bell.pending = bell.pending, {}
    if pending:
        moved = [{"work": work, "session": session} for work, session in pending.items()]
        state.broadcast_ws(CHANGED, {"changed": moved})


def changed_elsewhere(work: str) -> None:
    """*work* moved somewhere that holds no dashboard state (the trigger claim store): the
    gateway's list rings for it, when this process is the gateway's."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    state = get_dashboard_state()
    if state is not None:
        changed(state, work)


def chat_said(state: Any, session: Any, *, again: bool) -> None:
    """The chat *session* said its phase (``chat_phase``): its row moved, unless the frame is a
    liveness re-send (*again*), or the chat is a loop's worker, which its loop speaks for."""
    if not again and _is_a_chat(session):
        changed(state, work_ref("chat", str(session.key)), str(session.key))


def asked(state: Any, entry: Mapping[str, Any]) -> None:
    """The approval *entry* was asked or answered: each row it waits on the owner for moved, the
    chat's in its chat, a helper's in the chat its own frames name (the chat it works for)."""
    from personalclaw import work_phases

    raw = str(entry.get("session") or "")
    for work in approval_work(entry):
        kind, ident = workref.read(work)
        if kind == "chat":
            changed(state, work, ident)
        elif kind == "subagent":
            said = work_phases.current(state, work)
            changed(state, work, said["session"] if said is not None else work_phases.chat_of(raw))
        else:
            changed(state, work)


def question_changed(state: Any, session_key: str) -> None:
    """A question an agent asks the owner in *session_key* was asked or ended: the row of the work
    that session runs (a chat's, its loop's, its run's) moved, whether or not its phase did."""
    work = work_of_session(session_key)
    if work:
        kind, ident = workref.read(work)
        changed(state, work, ident if kind == "chat" else "")
