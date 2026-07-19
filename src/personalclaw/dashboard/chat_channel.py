"""Channel integration — link sessions, handoff, channel listing."""

import logging

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_and_truncate
from personalclaw.sel import sel
from personalclaw.sync_bridge import handoff_to_channel

logger = logging.getLogger(__name__)


async def api_chat_session_channel_link(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/channel-link — link a dashboard session to a channel."""

    state: DashboardState = request.app["state"]
    name = request.match_info.get("session", "")
    session = state.get_session(name) or state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    delivery = state.channel_delivery
    if not delivery:
        return web.json_response({"error": "Channel not connected"}, status=503)
    owner_id = getattr(state, "owner_id", None)
    if not owner_id:
        return web.json_response({"error": "owner not configured"}, status=500)

    session_key = _history_key_for(name)

    # Check if already linked
    existing_ts, existing_chan = state.sessions.get_channel_link(session_key)
    if existing_ts and existing_chan:
        try:
            await delivery.deliver_text(
                existing_chan, "🔗 Session linked from dashboard — continuing here.", existing_ts
            )
        except Exception:
            pass
        return web.json_response(
            {"ok": True, "already_linked": True, "thread_ts": existing_ts, "channel": existing_chan}
        )

    body = await request.json() if request.content_length else {}
    raw_channel = body.get("channel", "")
    if not raw_channel or raw_channel == "dm":
        target_channel = await delivery.open_dm(owner_id)
    else:
        target_channel = raw_channel

    # redact_and_truncate applies both redact_exfiltration_urls + redact_credentials
    title = redact_and_truncate(session.title or name, max_chars=200)
    thread_ts = await delivery.deliver_text(
        target_channel, f"\U0001f9f5 *{title}*\nSession linked from dashboard."
    )
    if not thread_ts:
        return web.json_response({"error": "failed to create thread"}, status=500)

    state.sessions.set_channel_link(session_key, thread_ts, target_channel)
    session._channel_linked = True
    session._channel_id = target_channel
    session._channel_thread_ts = thread_ts

    # Post last 5 messages as context
    for m in session.messages[-5:]:
        role = m.get("role", "")
        txt = redact_and_truncate(m.get("content") or "", max_chars=2000)
        if role in ("user", "assistant") and txt:
            icon = "\U0001f9d1" if role == "user" else "\U0001f916"
            try:
                await delivery.deliver_text(target_channel, f"{icon} {txt}", thread_ts)
            except Exception:
                pass

    sel().log_api_access(
        caller="dashboard",
        operation="chat.channel_link",
        outcome="success",
        source="dashboard",
        resources=session.key,
    )
    state.push_sessions_update()
    return web.json_response({"ok": True, "thread_ts": thread_ts, "channel": target_channel})


async def api_channel_reply_targets(request: web.Request) -> web.Response:
    """GET /api/channels/reply-targets — list channels the bot can reply in.

    The channel APP owns which channels are reply-eligible (its own tracked/active
    config), surfaced through the provider-agnostic ChannelDelivery seam — core
    holds no channel config."""
    state: DashboardState = request.app["state"]
    delivery = state.channel_delivery
    if delivery is None or not hasattr(delivery, "list_reply_channels"):
        return web.json_response([{"id": "dm", "name": "Direct Message"}])
    try:
        return web.json_response(delivery.list_reply_channels())
    except Exception:
        logger.exception("list_reply_channels failed")
        return web.json_response([{"id": "dm", "name": "Direct Message"}])


async def api_chat_session_handoff(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/handoff — hand off session to channel DM thread."""

    state: DashboardState = request.app["state"]
    name = request.match_info.get("session", "")
    session = state.get_session(name) or state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    if not state.channel_delivery:
        return web.json_response({"error": "Channel not connected"}, status=503)
    if not state.conversation_log:
        return web.json_response({"error": "no conversation log"}, status=500)

    try:
        _save_session_to_history(state, session)
    except Exception:
        pass

    channel = None
    try:
        body = await request.json()
        channel = body.get("channel")
    except Exception:
        pass

    history_key = _history_key_for(session.key)
    thread_ts = await handoff_to_channel(
        state.channel_delivery,
        state.owner_id,
        state.conversation_log,
        history_key,
        title=session.title if session._titled else "",
        channel=channel,
        sessions=state.sessions,
    )
    if not thread_ts:
        return web.json_response({"error": "handoff failed"}, status=500)

    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_handoff",
        outcome="allowed",
        source="dashboard",
        resources=session.key,
    )
    return web.json_response({"ok": True, "thread_ts": thread_ts})
