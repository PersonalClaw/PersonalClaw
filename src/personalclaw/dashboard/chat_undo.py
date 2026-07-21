"""Conversation-turn rollback — `/undo N` (power-user-surfaces P7).

Rolls the conversation back N turns: removes the last N user→assistant turns from
the session's message history (in-memory AND persisted transcript), returning to an
earlier state. Distinct from memory-event undo (which reverts a learned *fact*, not
the *conversation*).

Bound (honest-status discipline): this undoes the CONVERSATION only. Side effects the
undone turns already took (files written, tasks created, tools run) are NOT reversed —
the response says so. Reversing side effects is a much larger transactional-agent
problem, explicitly out of scope.
"""

import logging

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _save_session_to_history
from personalclaw.dashboard.chat_utils import _sync_dashboard_sessions
from personalclaw.dashboard.state import DashboardState
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def _turn_starts(messages: list[dict]) -> list[int]:
    """Indices where each conversational TURN begins — i.e. each ``user`` message.
    A turn is one user message + everything the agent produced in response (assistant
    text, tool calls, tool results) up to the next user message."""
    return [i for i, m in enumerate(messages) if m.get("role") == "user"]


async def api_chat_session_undo(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/undo — roll back the last N conversation turns.

    Body: ``{ n?: number }`` (default 1). Truncates the session's message history to the
    start of the Nth-from-last user turn, updating both the in-memory list and the
    persisted transcript so a reload doesn't resurrect undone turns. Returns the number
    of turns actually removed + a notice that side effects were NOT reverted.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    request_app = request.get("app", "")
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    # App-isolation: an app may only undo sessions it owns (mirrors fork/stop).
    if request_app and session._app != request_app:
        sel().log_api_access(
            caller=request_app,
            operation="chat.session_undo",
            outcome="denied",
            source="app_isolation",
            resources=f"session={name}",
            error="app does not own this session",
        )
        return web.json_response({"error": "app does not own this session"}, status=403)

    n = 1
    if request.body_exists:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if isinstance(body, dict) and body.get("n") is not None:
            raw = body.get("n")
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
                return web.json_response({"error": "n must be a positive integer"}, status=400)
            n = raw

    # Don't undo mid-stream — a running turn owns the message list.
    if session.running:
        return web.json_response({"error": "cannot undo while a turn is running"}, status=409)

    starts = _turn_starts(session.messages)
    if not starts:
        return web.json_response({"error": "no turns to undo"}, status=400)
    # Remove the last min(n, available) turns → truncate to the start of that turn.
    removed = min(n, len(starts))
    cut = starts[-removed]
    session.messages = session.messages[:cut]
    # Persist the rollback: _save_session_to_history rewrites the WHOLE transcript file
    # from session.messages (not append-only), so the truncated list becomes the on-disk
    # state — a reload won't resurrect the undone turns. force=True since the shrunken
    # list is ≤ _resumed_count (the guard at :402 would otherwise skip the write).
    session._resumed_count = 0
    try:
        _save_session_to_history(state, session, force=True)
    except Exception:
        logger.warning("undo: failed to persist truncated transcript for %s", name, exc_info=True)

    _sync_dashboard_sessions(state)
    state.broadcast_ws("chat_undone", {"session": session.key, "turns": removed})
    sel().log_api_access(
        caller=request_app or "dashboard",
        operation="chat.session_undo",
        outcome="success",
        source="dashboard",
        resources=f"session={name},turns={removed}",
    )
    return web.json_response(
        {
            "ok": True,
            "turns_undone": removed,
            "notice": (
                f"Rolled back {removed} turn(s). Side effects from those turns "
                "(files written, tasks created, tools run) were NOT reverted."
            ),
        }
    )
