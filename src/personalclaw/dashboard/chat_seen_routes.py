"""You have seen a chat, or every chat: the gateway's record of it, and what a chat's row derives.

The record is ``chat_seen`` (its one writer). Two routes advance it, monotonically:
``POST /api/chat/sessions/{session}/seen`` for one chat (the chat page, while it is open in front of
you: its page visible and its window focused) and ``POST /api/chat/seen`` for every chat (⇧Esc,
#3822). Each advance is said in one ``chat_seen {session, seen_through}`` frame (``session`` is
``"*"`` for every chat), so every other open window clears its mark at once. The frame is about
you, not the chat, so it reaches no app's socket, not even the app that started the chat
(``ws_state.OWNER_FRAMES``).

A row of ``GET /api/chat/sessions`` carries ``attention: {unread, needs_you, your_turn}``,
derived on every read and never stored (:func:`attention`). An app reading its own chats gets no
``unread``: when you have read them is yours, not the app's.

- ``unread``: its last turn ended after you last saw it. A turn you stopped yourself never makes it
  unread, and a turn that is still running is no reply yet. A chat with no turn on record in this
  process (one kept only on disk, or not run since the gateway started) reads its last activity.
- ``needs_you``: something waiting on you is shown in it (``waiting``): its own approval, question
  or plan step, or a helper's it started. Seeing a chat never clears it.
- ``your_turn``: nothing runs in it and nothing waits on you there, and its last turn ended in a
  reply: the next word is yours.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from aiohttp import web

from personalclaw import chat_seen
from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body

logger = logging.getLogger(__name__)

#: The frame that says how far a chat has been seen, and the name it gives every chat at once.
SEEN_FRAME = "chat_seen"
EVERY_CHAT = "*"

#: The endings of a turn that leave a reply to read: not your own Stop.
_REPLIED = frozenset({"complete", "error", "interrupted"})


def attention(row: Mapping[str, Any], seen: chat_seen.Seen, waiting: Iterable[str]) -> dict:
    """What *row* (a ``GET /api/chat/sessions`` row) says needs your attention, read against
    *seen* and the chats something waiting on you is shown in (*waiting*)."""
    key = str(row.get("key") or "")
    phase = row.get("run_state") if isinstance(row.get("run_state"), Mapping) else {}
    running = phase.get("turn") is not None
    outcome = phase.get("last_outcome")
    needs_you = bool(key) and key in set(waiting)
    if running or outcome == "stopped":
        ended = None
    elif outcome in _REPLIED:
        ended = chat_seen.epoch_of(phase.get("since"))
    else:
        ended = chat_seen.epoch_of(row.get("last_activity_ts"))
    unread = ended is not None and ended > seen.through(key)
    your_turn = not running and not needs_you and ended is not None
    return {"unread": unread, "needs_you": needs_you, "your_turn": your_turn}


def attend(rows: list[dict], waiting: Iterable[str], *, yours: bool = True) -> list[dict]:
    """*rows* each with its ``attention``, read for you, or for an app (``yours`` False), which is
    told nothing of when you read them: its rows carry no ``unread``. Call off the event loop: it
    reads the seen record."""
    seen = chat_seen.read() if yours else chat_seen.Seen()
    chats = set(waiting)
    for row in rows:
        row["attention"] = attention(row, seen, chats)
        if not yours:
            del row["attention"]["unread"]
    return rows


def _owner_only(request: web.Request) -> web.Response | None:
    """A refusal for anyone but you: how far you have read is yours to say, never an app's token
    nor an agent's tool, which could clear a mark you have not seen."""
    from personalclaw import approval_answer

    if approval_answer.of_request(request).kind == approval_answer.OWNER:
        return None
    return json_error("seen_owner_only", status=403)


async def _through(request: web.Request) -> float | web.Response:
    """The instant the body says (``through``: ISO 8601 or epoch seconds), now when it says none."""
    body = await json_object_body(request)
    raw = body.get("through") if isinstance(body, dict) else None
    if raw is None or raw == "":
        return chat_seen._now()
    at = chat_seen.epoch_of(raw)
    if at is None:
        return json_error(
            "invalid_request", message="through must be an instant with its zone.", status=400
        )
    return at


def _said(state: Any, moved: Mapping[str, float]) -> None:
    from personalclaw.instants import utc_iso

    for chat, at in moved.items():
        state.broadcast_ws(SEEN_FRAME, {"session": chat, "seen_through": utc_iso(at)})


async def api_chat_session_seen(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/seen — you have seen the chat through ``through`` (now
    when absent); its unread mark clears on every window. Monotonic: an older ``through`` changes
    nothing. Only you: an app or an agent is refused."""
    import asyncio

    from personalclaw.dashboard.chat_persistence import session_key_exists

    refused = _owner_only(request)
    if refused is not None:
        return refused
    state = request.app["state"]
    chat = request.match_info["session"]
    if not session_key_exists(state, chat):
        return json_error("not_found", message="No chat has that name.", status=404)
    at = await _through(request)
    if isinstance(at, web.Response):
        return at
    try:
        moved = await asyncio.to_thread(chat_seen.advance_all, [chat], at)
    except chat_seen.record_files.Unreadable as exc:
        return json_error("seen_unreadable", message=str(exc), status=409)
    _said(state, moved)
    return web.json_response({"ok": True, "moved": sorted(moved)})


async def api_chat_seen_all(request: web.Request) -> web.Response:
    """POST /api/chat/seen — you have seen every chat through ``through`` (now when absent): every
    unread mark clears, on every window (⇧Esc). Monotonic. Only you."""
    import asyncio

    from personalclaw.instants import utc_iso

    refused = _owner_only(request)
    if refused is not None:
        return refused
    state = request.app["state"]
    at = await _through(request)
    if isinstance(at, web.Response):
        return at
    try:
        moved = await asyncio.to_thread(chat_seen.advance_every, at)
    except chat_seen.record_files.Unreadable as exc:
        return json_error("seen_unreadable", message=str(exc), status=409)
    if moved is not None:
        # Every chat at once, named as no chat is (`EVERY_CHAT`): your own windows hear it, and no
        # app's, which hears of its own conversations only.
        state.broadcast_ws(SEEN_FRAME, {"session": EVERY_CHAT, "seen_through": utc_iso(moved)})
    return web.json_response({"ok": True, "moved": moved is not None})
