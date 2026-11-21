"""A chat turn the gateway stopped while it waited on a tool approval: asked again at the next
start, and carried on by its answer (``parked_turns`` keeps it on disk).

While a chat's turn waits on an approval (``chat_runner``), :func:`park` keeps it when its chat
keeps words and its runtime can say where the turn stands, as PersonalClaw's own runtime does; an
agent CLI's turn cannot be carried on from the middle of a call, and ends at a restart as before.
How the wait ends decides what is left: the gateway stopping keeps the record
(:func:`kept_through_stop`), and every other ending drops it.

After a restart :func:`take_back` settles each record once the dashboard is up:

* its chat deleted, archived or now keeping no words, or already running a turn: dropped;
* its window run out while the gateway was down: dropped, and the chat's row says it expired;
* otherwise the same approval is asked again (:func:`_carry_on`): its row in the chat, its card on
  every surface, for the window it had left. It is the chat's running turn while it waits, so a
  message sent meanwhile waits behind it as it would have before the restart, and a Stop ends it.

Its answer is given as any approval's is, by the same routes and rules. The record is taken off disk
before the call runs, so a crash while it runs never runs it again. Then the turn is carried on
(``run_chat(resume=...)``): an Allow runs the call on its guarded path, a Deny or an approval nobody
answered runs nothing and is told to the model as what it was, and the model is asked only for what
comes after the call. Nothing the turn ran under is taken from the record: the chat's mode, its
model, its grants and every check are read when it carries on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from personalclaw import memory_writes, parked_turns
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.turn_endings import UNANSWERED

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)

#: The chats whose turn the gateway's stop kept while it waited (:func:`settle`), until the turn's
#: ending says so (:func:`was_kept`).
_KEPT: set[str] = set()


def keeps(state: DashboardState, session: _ChatSession) -> bool:
    """Whether *session*'s waiting turn may be kept on disk: its chat keeps words. An Incognito
    or Temporary chat keeps none, so neither does a record of its turn."""
    try:
        mode = memory_writes.session_mode(_history_key_for(session.key), state=state)
    except Exception:  # noqa: BLE001 — a mode nothing can read keeps nothing
        return False
    return mode == memory_writes.PERSISTENT


def keep(
    state: DashboardState,
    session: _ChatSession,
    client: object,
    request_id: str,
    *,
    row: Mapping[str, Any],
    asked_for_by: Mapping[str, str] | None,
    window_secs: float,
) -> asyncio.Future[bool]:
    """Start keeping *session*'s turn, waiting on *request_id*'s answer (:func:`park`), in a task
    of its own: a stop that comes while it is written still learns, from :func:`settle`, whether
    the turn was kept. The card kept is the one every surface shows, the approval's entry."""
    from personalclaw.dashboard.approval_state import chat_approval_id

    card = dict(state._pending_approvals.get(chat_approval_id(session.key, request_id)) or {})
    return asyncio.ensure_future(
        park(
            state,
            session,
            client,
            request_id,
            card=card,
            row=row,
            asked_for_by=asked_for_by,
            window_secs=window_secs,
        )
    )


async def settle(
    state: DashboardState,
    session: _ChatSession,
    request_id: str,
    keeping: asyncio.Future[bool] | None,
    *,
    answered: bool,
    timed_out: bool,
    window_secs: float,
) -> bool:
    """End the wait on *request_id*'s answer: whether its turn is kept for the next start.

    Kept (:func:`kept_through_stop`), every surface lets the approval go as cancelled and its row
    is left asking, which a save does not write, so the next start asks it again. Otherwise the
    record is dropped and the approval ends as its wait did (``end_session_approval``: expired,
    cancelled, or a no-op once an answer withdrew it)."""
    from personalclaw.dashboard.approval_state import chat_approval_id

    parked = await written(keeping)
    if parked and kept_through_stop(state, session, answered=answered or timed_out):
        state.end_approval(chat_approval_id(session.key, request_id), outcome="cancelled")
        _KEPT.add(session.key)
        return True
    if parked:
        await forget(session, request_id)
    state.end_session_approval(
        session,
        request_id,
        outcome="expired" if timed_out else "cancelled",
        window_secs=window_secs if timed_out else 0.0,
    )
    return False


async def park(
    state: DashboardState,
    session: _ChatSession,
    client: object,
    request_id: str,
    *,
    card: Mapping[str, Any],
    row: Mapping[str, Any],
    asked_for_by: Mapping[str, str] | None,
    window_secs: float,
) -> bool:
    """Keep *session*'s turn, waiting on *request_id*'s answer, so a restart carries it on: its
    approval's *card*, its permission *row*, who asked for it (*asked_for_by*), its window, and
    where its runtime (*client*) says it stands. False, and nothing kept, when its chat keeps no
    words, its runtime cannot say, the approval was not listed, or the record cannot be written."""
    point_of = getattr(client, "park_point", None)
    if not card or not callable(point_of) or not keeps(state, session):
        return False
    point = point_of(request_id)
    if not isinstance(point, Mapping):
        return False

    def _keep() -> bool:
        rec = parked_turns.record(
            session.key,
            request_id,
            card=card,
            row=row,
            point=point,
            asked_for_by=asked_for_by,
            window_secs=window_secs,
        )
        return rec is not None and parked_turns.write(rec)

    return await asyncio.to_thread(_keep)


async def written(park_task: asyncio.Future[bool] | None) -> bool:
    """Whether the record :func:`park` was writing (*park_task*) was written: awaited to its end,
    so a wait a stop ended while it was written still knows whether its turn is kept."""
    if park_task is None:
        return False
    try:
        return bool(await asyncio.shield(park_task))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a record that failed to be written keeps nothing
        logger.warning("keeping a waiting turn failed", exc_info=True)
        return False


def kept_through_stop(state: DashboardState, session: _ChatSession, *, answered: bool) -> bool:
    """Whether a wait that just ended leaves its turn kept: nobody answered it, and the gateway,
    not the owner, ended it to restart or shut down."""
    return not answered and bool(state.stopping_for) and not session._stop_asked


def was_kept(session: _ChatSession) -> bool:
    """Whether *session*'s turn, ending now, was kept for the next start (:func:`settle`); asked
    once, by the turn's ending."""
    if session.key in _KEPT:
        _KEPT.discard(session.key)
        return True
    return False


def cannot_carry_on(resume: Mapping[str, Any] | None, client: object) -> bool:
    """Whether the turn kept through a restart (*resume*) cannot be carried on by the runtime that
    serves its chat now (*client*): one that cannot go on from the middle of a call."""
    return resume is not None and not callable(getattr(client, "resume", None))


async def forget(session: _ChatSession, request_id: str) -> None:
    """Drop the record of *session*'s turn waiting on *request_id*, if one was kept."""
    await asyncio.to_thread(parked_turns.forget, session.key, request_id)


def _open_chat(state: DashboardState, name: str) -> _ChatSession | None:
    """The chat *name*, opened, or ``None`` when no chat is kept under it any more (deleted, or a
    Temporary chat, ended) or it is archived."""
    from personalclaw.dashboard.chat_persistence import resolve_session, session_key_exists

    if not session_key_exists(state, name):
        return None
    return resolve_session(state, name)


async def take_back(state: DashboardState) -> int:
    """Settle every turn the last gateway left waiting on an approval (the module's docstring);
    how many are asked again."""
    asked = 0
    for rec in await asyncio.to_thread(parked_turns.waiting):
        name, request_id = str(rec["session"]), str(rec["request_id"])
        session = _open_chat(state, name)
        if session is None or session.running or not keeps(state, session):
            await asyncio.to_thread(parked_turns.forget, name, request_id)
            continue
        left = parked_turns.window_left(rec)
        if left <= 0:
            await asyncio.to_thread(parked_turns.forget, name, request_id)
            _row(session, rec, resolved="expired")
            continue
        task = asyncio.create_task(_carry_on(state, session, rec, left))
        session.task = task
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        asked += 1
    return asked


def _row(session: _ChatSession, rec: Mapping[str, Any], *, resolved: str = "") -> None:
    """Put *rec*'s permission row back in its chat: asking, or *resolved* as it ended."""
    row = rec.get("row") or {}
    meta = dict(row.get("meta") or {})
    if resolved:
        meta["resolved"] = resolved
    session.append("permission", str(row.get("title") or ""), json.dumps(meta))


async def _carry_on(
    state: DashboardState, session: _ChatSession, rec: Mapping[str, Any], window: float
) -> None:
    """Ask *rec*'s approval again in *session* for *window* seconds, and carry its turn on with
    the answer. A Stop, or the gateway stopping again, ends the wait as it would end any."""
    request_id = str(rec["request_id"])
    _row(session, rec)
    fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    session._approval_futures[request_id] = fut
    outcome, answered, timed_out = "rejected", False, False
    try:
        await state.hold_session_approval(session, request_id, **dict(rec["card"]))
        outcome = await asyncio.wait_for(fut, timeout=window)
        answered = True
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        session._approval_futures.pop(request_id, None)
        if kept_through_stop(state, session, answered=answered or timed_out):
            # Kept for the next start: its row is not written (a save drops a row that asks),
            # and every surface lets it go as cancelled.
            from personalclaw.dashboard.approval_state import chat_approval_id

            state.end_approval(chat_approval_id(session.key, request_id), outcome="cancelled")
        else:
            state.end_session_approval(
                session,
                request_id,
                outcome="expired" if timed_out else "cancelled",
                window_secs=window if timed_out else 0.0,
            )
            if not (answered or timed_out):
                # Stopped while it waited: nothing carries the turn on, now or at the next start.
                asyncio.get_running_loop().run_in_executor(
                    None, parked_turns.forget, session.key, request_id
                )
    if outcome == "cancelled":
        # The work that asked was stopped (`cancel_approval`): the turn ends with it.
        await forget(session, request_id)
        return
    # Taken off disk before the call can run: a crash from here on never runs it again.
    taken = await asyncio.to_thread(parked_turns.take, session.key, request_id)
    if taken is None:
        return
    if outcome == "approved_trust_reads":
        session._trust_reads = True
        session._trust_from_floor = ""
        outcome = "approved"
    allowed = outcome == "approved"
    why = UNANSWERED["expired"] if timed_out else ""
    from personalclaw.dashboard.chat_runner import run_chat

    await run_chat(
        state,
        session,
        "",
        asked_for_by=taken.get("asked_for_by") or None,
        _retry=True,
        resume=taken,
        resume_allowed=allowed,
        resume_why=why,
    )
