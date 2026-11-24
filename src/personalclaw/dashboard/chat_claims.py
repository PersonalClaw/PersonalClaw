"""A chat turn's reply, held to the turn's own record before it settles (`turn_claims`).

The chat runner settles a turn's last answer once its model has finished (``_flush_segment``).
Just before, :func:`noted` reads what that answer claims against the calls the turn made, as its
rows keep them, and the repository the chat works in. A claim they do not bear out gets a short
note after the answer, streamed like the answer's own words, so the chat shows it as the turn ends
and keeps it with the answer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.turn_claims import TurnRecord, note_for

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)


def noted(state: DashboardState, session: _ChatSession, reply: str) -> str:
    """The note *reply* gets, streamed to the chat, with the blank line before it; "" when every
    claim it makes matches the turn's record, and on any failure to read it (a note is never worth
    a turn)."""
    try:
        note = note_for(reply, record_of(session))
    except Exception:  # noqa: BLE001 - the answer settles as it is
        logger.debug("turn claims: %s could not be read", session.key, exc_info=True)
        return ""
    if not note:
        return ""
    chunk = f"\n\n{note}"
    session.stream_chunk(chunk)
    state.broadcast_ws(
        "chat_chunk", {"session": session.key, "content": chunk, "seq": state.next_stream_seq()}
    )
    return chunk


def record_of(session: Any) -> TurnRecord:
    """The turn's record as its chat keeps it: each call it made since her last message, by the
    title its row carries, and the folder the chat works in."""
    tools: list[str] = []
    for message in reversed(getattr(session, "messages", None) or []):
        role = message.get("role")
        if role == "user":
            break
        if role == "tool":
            tools.append(str(message.get("content") or ""))
    return TurnRecord(tools=tuple(reversed(tools)), folder=str(session.workspace_dir or ""))
