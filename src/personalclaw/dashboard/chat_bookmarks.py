"""A chat's bookmarked messages: ``PUT /api/chat/sessions/{session}/bookmarks``.

A bookmark marks one of the owner's own messages in a conversation, so the Session Map shows it
in the rail, its preview and the compact map (#4776). The gateway keeps them on the session,
beside its colour and tags, so every window and the phone agree.

**Keyed by the message's ``ts``.** A message has no id of its own, and its visible position
(``at_message_index``) moves when the conversation is rewound or edited. A user message's ``ts``
is what edit-resend already addresses it by. A bookmark whose message is gone (rewound away,
edited into a new one) matches no message, and the map shows nothing for it.
"""

from __future__ import annotations

from aiohttp import web

from personalclaw.dashboard.chat_persistence import resolve_session, save_session_to_history
from personalclaw.dashboard.chat_utils import full_session_messages
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.sel import sel

#: How many messages one conversation may keep bookmarked. Far past what a map is for, and a
#: bound on what one session's meta line carries.
MAX_BOOKMARKS = 500

#: The longest ``ts`` taken. A message's stamp is an ISO time, well under this.
_TS_MAX = 64


def _user_message_stamps(state: DashboardState, session) -> set[str]:
    """The ``ts`` of every message the owner sent in *session*, its older files included."""
    return {
        str(m.get("ts"))
        for m in full_session_messages(state, session)
        if m.get("role") == "user" and m.get("ts")
    }


async def api_chat_session_bookmarks(request: web.Request) -> web.Response:
    """PUT /api/chat/sessions/{session}/bookmarks: bookmark messages, or take bookmarks off.

    Body: ``{"add": [ts, ...], "remove": [ts, ...]}``, either or both, each a message's ``ts``.
    Both apply to the bookmarks as stored when the write lands, the removals first, then the
    additions in order, a message already bookmarked skipped. Answers with the bookmarks after.

    One message in or out, never the page's list, for the reason the tags route gives: a list
    built from one window's copy would drop a bookmark another window had just set.

    A message to bookmark must be one the owner sent in this conversation (``400
    unknown_message``). One to take off need not: removing a bookmark that is not there leaves
    the conversation as asked.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", message="the body is not valid JSON", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", message="the body must be an object", status=400)
    edits: dict[str, list[str]] = {}
    for key in ("add", "remove"):
        if key not in body:
            continue
        stamps = body[key]
        if not isinstance(stamps, list) or not all(
            isinstance(t, str) and 0 < len(t) <= _TS_MAX for t in stamps
        ):
            return json_error(
                "invalid_field_type",
                message=f"'{key}' must be a list of message stamps (each a message's 'ts')",
                status=400,
            )
        edits[key] = stamps
    if not edits:
        return json_error(
            "invalid_request",
            message="send the message stamps to 'add' and/or 'remove'; the other bookmarks stay",
            status=400,
        )
    if edits.get("add"):
        sent = _user_message_stamps(state, session)
        unknown = [t for t in edits["add"] if t not in sent]
        if unknown:
            return json_error(
                "unknown_message",
                message=f"This conversation has no message of yours sent at {unknown[0]!r}.",
                status=400,
            )
    removed = set(edits.get("remove", []))
    kept = [t for t in session.bookmarks if t not in removed]
    for ts in edits.get("add", []):
        if ts not in kept:
            kept.append(ts)
    if len(kept) > MAX_BOOKMARKS:
        return json_error(
            "invalid_request",
            message=f"A conversation keeps at most {MAX_BOOKMARKS} bookmarks.",
            status=400,
        )
    session.bookmarks = kept
    save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_bookmarks",
        outcome="allowed",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True, "bookmarks": session.bookmarks})
