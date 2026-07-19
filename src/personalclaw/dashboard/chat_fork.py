"""Fork session — copy messages into a new tab."""

import logging

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for, _sync_dashboard_sessions
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

_MAX_SESSIONS_FOR_FORK = 500


async def api_chat_session_fork(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/fork — fork session into a new tab.

    Creates a new session with messages copied from the source up to
    ``at_message_index`` (inclusive, into the visible user/assistant list).
    An optional ``prompt`` is returned so the frontend can send it.

    Body: ``{ at_message_index?: number, prompt?: string }``
    """

    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    request_app = request.get("app", "")
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    # Rate/resource guard: reject if we're already at the cap.
    if len(state._sessions) >= _MAX_SESSIONS_FOR_FORK:
        sel().log_api_access(
            caller=request_app or "dashboard", operation="chat.session_fork",
            outcome="denied", source="rate_limit",
            resources=f"session={name},session_count={len(state._sessions)}",
            error="session cap reached",
        )
        return web.json_response(
            {"error": f"session cap reached ({_MAX_SESSIONS_FOR_FORK})"}, status=429,
        )

    # App ownership check: an app may only fork sessions it owns.
    if request_app:
        if not session._app:
            sel().log_api_access(
                caller=request_app, operation="chat.session_fork", outcome="denied",
                source="app_isolation", resources=f"session={name}",
                error="app cannot fork unscoped sessions",
            )
            return web.json_response({"error": "app cannot fork unscoped sessions"}, status=403)
        if session._app != request_app:
            sel().log_api_access(
                caller=request_app, operation="chat.session_fork", outcome="denied",
                source="app_isolation", resources=f"session={name}",
                error="app does not own this session",
            )
            return web.json_response({"error": "app does not own this session"}, status=403)

    if session.memory_mode != "persistent":
        sel().log_api_access(
            caller=request_app or "dashboard", operation="chat.session_fork",
            outcome="denied", source="dashboard",
            resources=f"session={name},memory_mode={session.memory_mode}",
            error="non-persistent session",
        )
        return web.json_response({"error": "cannot fork a non-persistent session"}, status=400)
    if request.body_exists:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
    else:
        body = {}
    at_index = body.get("at_message_index")
    prompt = body.get("prompt")
    fork_mode = session.mode
    if prompt is not None and not isinstance(prompt, str):
        return web.json_response({"error": "prompt must be a string"}, status=400)
    prompt = (prompt or "").strip()
    if len(prompt) > 32_768:
        return web.json_response(
            {"error": "prompt too long (max 32768 chars)"}, status=400,
        )

    # Read disk FIRST (full history)
    async with session._fork_lock:
        all_messages: list[dict] = []
        if state.conversation_log:
            all_messages = state.conversation_log.read_messages(_history_key_for(session.key))
        if all_messages and session._dirty:
            new_msgs = session.messages[session._resumed_count:]
            if new_msgs:
                all_messages.extend(new_msgs)
        if session._dirty:
            _save_session_to_history(state, session)
            session._resumed_count = len(session.messages)
            session._dirty = False
        if not all_messages:
            all_messages = list(session.messages)
    visible = [m for m in all_messages if m.get("role") in ("user", "assistant")]
    if not visible:
        return web.json_response({"error": "no messages to fork"}, status=400)
    if at_index is not None:
        if isinstance(at_index, bool) or not isinstance(at_index, int) or at_index < 0:
            return web.json_response(
                {"error": "at_message_index must be a non-negative integer"},
                status=400,
            )
        if at_index >= len(visible):
            return web.json_response(
                {"error": f"at_message_index {at_index} out of range (have {len(visible)} visible messages)"},
                status=400,
            )
        visible = visible[: at_index + 1]

    new_session = state.get_or_create_session(
        name=None, agent=session.agent, workspace_dir=session.workspace_dir, model=session.model, mode=fork_mode,
        app=request_app,
    )
    new_session.forked_from = _history_key_for(session.key)
    new_session.reasoning_effort = session.reasoning_effort
    # Inherit project folder so the fork appears next to its parent in the sidebar.
    new_session.folder_id = session.folder_id
    parent_title = session.title if session._titled else "Untitled"
    parent_title, _ = redact_exfiltration_urls(parent_title)
    parent_title, _ = redact_credentials(parent_title)
    new_session.title = f"Fork of {parent_title}"
    new_session._titled = True

    try:
        for m in visible:
            role = m.get("role", "assistant")
            content = m.get("content", "")
            if role != "user":
                content, _ = redact_exfiltration_urls(content)
                content, _ = redact_credentials(content)
            cls = "msg msg-u" if role == "user" else "msg msg-a"
            new_session.append(role, content, cls, ts=m.get("ts", ""), broadcast=False)
        new_session.drain()
        _save_session_to_history(state, new_session)
        new_session._resumed_count = len(new_session.messages)
    except Exception:
        state._sessions.pop(new_session.key, None)
        sel().log_api_access(
            caller=request_app or "dashboard",
            operation="chat.session_fork",
            outcome="error",
            source="dashboard",
            resources=f"from={session.key},to={new_session.key}",
            error="fork finalisation failed",
        )
        raise
    sel().log_api_access(
        caller=request_app or "dashboard",
        operation="chat.session_fork",
        outcome="allowed",
        source="dashboard",
        resources=(
            f"from={session.key},to={new_session.key},messages={len(visible)},"
            f"at_index={at_index if at_index is not None else 'last'},"
            f"prompt_len={len(prompt)}"
        ),
    )
    _sync_dashboard_sessions(state)
    state.push_sessions_update()
    return web.json_response(
        {"ok": True, "key": new_session.key, "title": new_session.title,
         "messages": len(visible), "prompt": prompt}
    )
