"""One turn of a chat, read by the id of the message that started it (shared contracts §3.9).

``GET /api/chat/sessions/{session}/turns/{message_id}`` answers the turn document: ``{session,
message_id, state, outcome, reply, attempts, usage, error, run_state}``. A message still waiting in
the chat's queue is ``queued``. The turn that runs now is ``running``, with the chat's run state
(§4). A turn that ended is ``settled``, with how it ended and its shown answer (``reply``, each
result in it cut at its first :data:`RESULT_CHARS` characters), read from the chat's rows as the
typed turn (``turn_rows.from_rows``). A steer's id and a merged message's id answer the turn they
ran in, under that turn's own ``message_id``.

How a turn ended is the outcome its start row records (``meta.turn``, written when it ends:
``chat_phase.ended``), else what its rows say. The script, channel or page that sent a message
reads how that one turn went here, with the detail route's authorization: an app reads only the
turns of conversations it started.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw import ids, turns
from personalclaw.dashboard import chat_intake, turn_rows
from personalclaw.http_errors import json_error

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

#: How much of each result a turn document's reply carries; the part's route answers it whole.
RESULT_CHARS = 8_000


async def api_chat_turn(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/turns/{message_id} — how one turn went, by its message's id.

    Answers ``{session, message_id, state, outcome, reply, attempts, usage, error, run_state}``:
    ``state`` is ``queued``, ``running`` (with ``run_state``) or ``settled`` (with ``outcome`` and
    the shown ``reply``, each result cut at 8,000 characters). A steer's or a merged message's id
    answers the turn it ran in. ``400 invalid_message_id`` for an id of another shape, ``404
    session_not_found`` for an unknown chat and ``404 turn_not_found`` when nothing in the chat has
    that id.
    """
    from personalclaw.dashboard.chat_persistence import _rehydrate_session_from_history

    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    message_id = request.match_info["message_id"]
    if not ids.is_id("msg", message_id):
        return json_error(
            "invalid_message_id",
            message="A message id must be `msg_` followed by 26 letters and digits.",
            status=400,
        )
    session = state._sessions.get(name)
    if session is None:
        held = _in_the_sessions_folder(state, name)
        session = _rehydrate_session_from_history(state, held) if held is not None else None
    if session is None:
        return json_error("session_not_found", status=404)
    found = find(session, message_id)
    if found is None:
        return not_found()
    return web.json_response(found.document)


@dataclass(frozen=True)
class Found:
    """The one turn a message id resolves to: its ``state`` (``queued``, ``running``, ``settled``),
    the id of the message that started it (``message_id``: a queued message's own), every id that
    answers to it (``ids``: that one first, then its merged messages and its steers) and its
    document."""

    state: str
    message_id: str
    ids: tuple[str, ...]
    document: dict[str, Any]


def find(session: _ChatSession, message_id: str) -> Found | None:
    """The turn *message_id* started or ran in, in *session*; ``None`` when nothing in the chat has
    that id. A message of another chat is no turn of this one."""
    for item in session._queue:
        if item.get(chat_intake.MESSAGE_ID) == message_id:
            return Found("queued", message_id, (message_id,), _queued(session, message_id))
    messages = turn_rows.from_rows(session.messages, session_key=session.key)
    running = chat_intake.running_turn_ids(session)
    # A steer the running turn has not taken yet has no row: its receipt names the turn it joined.
    start = _start_of(messages, running[0] if message_id in running[1:] else message_id)
    if start is None:
        return None
    answers = [m for m in messages if m.role == "assistant" and m.parent_id == start.id]
    shown = next((m for m in answers if m.shown), answers[-1] if answers else None)
    if running and running[0] == start.id:
        document = {
            **_settled(session, start, shown, answers),
            "state": "running",
            "outcome": None,
            "run_state": session.run_state,
        }
        return Found("running", start.id, running, document)
    ids = (
        start.id,
        *(str(m.get(chat_intake.MESSAGE_ID) or "") for m in start.facts.merged),
        *(m.id for m in messages if m.role == "user" and m.facts.steered_into == start.id),
    )
    document = _settled(session, start, shown, answers)
    return Found("settled", start.id, tuple(i for i in ids if i), document)


def document(session: _ChatSession, message_id: str) -> dict[str, Any] | None:
    """The turn document of the turn *message_id* started or ran in (:func:`find`)."""
    found = find(session, message_id)
    return found.document if found is not None else None


def _in_the_sessions_folder(state: DashboardState, name: str) -> str | None:
    """*name*, held to the folder the chats are kept in before anything is read for it: the name
    of the folder's direct child it names, joined and normalised, when that child is named exactly
    as given. ``None`` for anything else (``..``, ``.``, ``./x``, ``a/b``), which is no chat, and
    when there is no folder to hold it to."""
    log = state.conversation_log
    if log is None:
        return None
    folder = os.path.normpath(str(log._dir))
    held = os.path.normpath(os.path.join(folder, name))
    if not held.startswith(folder + os.sep) or os.path.dirname(held) != folder:
        return None
    child = os.path.basename(held)
    return child if child == name else None


def not_found() -> web.Response:
    """``404 turn_not_found``, which says nothing of any other chat."""
    return json_error(
        "turn_not_found", message="No turn in this chat started with that message.", status=404
    )


def cut_results(reply: Mapping[str, Any]) -> dict[str, Any]:
    """*reply*, a message's ``to_dict``, with each tool result's text cut at :data:`RESULT_CHARS`
    characters and marked so; *reply* itself is left whole."""
    parts = []
    for part in reply.get("parts") or ():
        result = part.get("result") if isinstance(part, Mapping) else None
        text = result.get("text") if isinstance(result, Mapping) else None
        if isinstance(result, Mapping) and isinstance(text, str) and len(text) > RESULT_CHARS:
            whole = result.get("original_length") or len(text)
            part = {
                **part,
                "result": {
                    **result,
                    "text": text[:RESULT_CHARS],
                    "truncated": True,
                    "original_length": whole,
                },
            }
        parts.append(part)
    return {**reply, "parts": parts}


def _start_of(messages: list[turns.Message], message_id: str) -> turns.Message | None:
    """The user message that started the turn *message_id* names: itself, the turn a steer joined,
    or the turn a merged message ran in."""
    by_id = {m.id: m for m in messages if m.role == "user"}
    found = by_id.get(message_id)
    if found is not None:
        into = found.facts.steered_into
        return by_id.get(into, found) if into else found
    for message in by_id.values():
        if any(m.get(chat_intake.MESSAGE_ID) == message_id for m in message.facts.merged):
            return message
    return None


def _queued(session: _ChatSession, message_id: str) -> dict[str, Any]:
    return {
        "session": session.key,
        "message_id": message_id,
        "state": "queued",
        "outcome": None,
        "reply": None,
        "attempts": 0,
        "usage": None,
        "error": None,
        "run_state": None,
    }


def _settled(
    session: _ChatSession,
    start: turns.Message,
    shown: turns.Message | None,
    answers: list[turns.Message],
) -> dict[str, Any]:
    recorded = _recorded_outcome(session, start.id)
    return {
        "session": session.key,
        "message_id": start.id,
        "state": "settled",
        "outcome": recorded or (shown.outcome if shown is not None else None) or "complete",
        "reply": cut_results(shown.to_dict()) if shown is not None else None,
        "attempts": len(answers),
        "usage": shown.usage.to_dict() if shown is not None and shown.usage else None,
        "error": shown.error.to_dict() if shown is not None and shown.error else None,
        "run_state": None,
    }


def _recorded_outcome(session: _ChatSession, message_id: str) -> str | None:
    """What the turn's start row records of how it ended (``meta.turn.outcome``), when it does."""
    for row in reversed(session.messages):
        meta = row.get("meta") if isinstance(row, Mapping) else None
        if not isinstance(meta, Mapping):
            meta = {}
        if meta.get(chat_intake.MESSAGE_ID) == message_id:
            turn = meta.get("turn")
            outcome = turn.get("outcome") if isinstance(turn, Mapping) else None
            return outcome if isinstance(outcome, str) and outcome else None
    return None
