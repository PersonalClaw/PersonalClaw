"""Channel integration — link sessions, handoff, channel listing."""

import logging
from typing import Any

from aiohttp import web

from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.request_validation import json_object_body
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
    # redact_and_truncate applies both redact_exfiltration_urls + redact_credentials
    title = redact_and_truncate(session.title or name, max_chars=200)
    opening = f"\U0001f9f5 *{title}*\nSession linked from dashboard."
    if not raw_channel or raw_channel == "dm":
        # The owner's DM on the first channel that reaches the owner, with the id that channel
        # keeps for them — it used to be the first channel with the one shared id, which is
        # another platform's user id on every channel but one.
        from personalclaw.channel_delivery import reach_owner

        async def _open_thread(owner_delivery: Any, dm: str) -> str:
            ts = await owner_delivery.deliver_text(dm, opening)
            if not ts:
                raise RuntimeError("the channel created no thread")
            return str(ts)

        owner = await reach_owner(_open_thread)
        if not owner.delivered:
            return web.json_response(
                {"error": owner.sentence() or "Channel not connected"}, status=502
            )
        delivery, target_channel, thread_ts = owner.delivery, owner.channel, owner.result
    else:
        target_channel = raw_channel
        thread_ts = await delivery.deliver_text(target_channel, opening)
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


def _continue_there(
    state: DashboardState, session: Any, *, provider: str, channel: str, thread_ts: str, dm: bool
) -> None:
    """Link the chat to the conversation a reply will arrive in, so it CONTINUES on the channel.

    The handoff posted into the channel and recorded a link nothing inbound reads: the guarded door
    finds a chat by the thread key a message carries (``channel_inbound._route_to_session``), and a
    reply to a handoff opened a new chat. The key is the channel's call: in a DM that is one
    conversation (``ChannelCapabilities.dm_thread_is_channel``) every message carries the DM itself,
    elsewhere the thread the handoff opened. ``state.link_channel`` records it where the door and
    the next restart read it. The chat's channel origin is set to the channel it continues on, if it
    had none, so its replies go back out there too (``DashboardState.channel_provider_for``).
    """
    from personalclaw.channel_transports import get_transport

    transport = get_transport(provider)
    try:
        dm_is_one_thread = bool(
            dm and transport is not None and transport.capabilities().dm_thread_is_channel
        )
    except Exception:  # noqa: BLE001 - a broken capabilities() declares nothing
        dm_is_one_thread = False
    if not session._app:
        session._app = provider
    state.link_channel(session.key, channel if dm_is_one_thread else thread_ts, channel)
    try:
        save_session_to_history(state, session)  # the origin rides the meta line across restarts
    except Exception:
        logger.warning("chat %s: saving its channel origin failed", session.key, exc_info=True)


async def api_chat_session_handoff(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/handoff — continue a chat in a channel thread.

    ``provider`` names the channel (the chat's "Continue on …" menu item does): the thread opens in
    the owner's DM there, with the owner id that channel keeps. Without it, the first connected
    channel that reaches the owner takes it. ``channel`` names a thread target instead of the DM,
    and only together with ``provider`` — an id means nothing without the channel that issued it.
    """
    from personalclaw.channel_delivery import delivery_for, reach_owner
    from personalclaw.http_errors import json_error

    state: DashboardState = request.app["state"]
    name = request.match_info.get("session", "")
    session = state.get_session(name) or state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    body = await json_object_body(request)
    channel = str(body.get("channel") or "").strip()
    provider = str(body.get("provider") or "").strip()
    if channel and not provider:
        return json_error(
            "invalid_request",
            message="Name the channel ('provider') the thread id belongs to.",
            status=400,
        )
    if provider and delivery_for(provider) is None:
        return json_error("channel_unknown", status=404)
    if not provider and not state.channel_delivery:
        return web.json_response({"error": "Channel not connected"}, status=503)
    conversation_log = state.conversation_log
    if not conversation_log:
        return web.json_response({"error": "no conversation log"}, status=500)

    try:
        save_session_to_history(state, session)
    except Exception:
        pass

    history_key = _history_key_for(session.key)
    if not conversation_log.read_messages(history_key):
        return web.json_response(
            {"error": "This conversation has no messages to hand off yet."}, status=400
        )

    async def _handoff(delivery: Any, target: str) -> str:
        ts = await handoff_to_channel(
            delivery,
            "",
            conversation_log,
            history_key,
            title=session.title if session._titled else "",
            channel=target,
        )
        if not ts:
            raise RuntimeError("the channel created no thread")
        return ts

    # A thread target goes through the channel that issued it; the owner's DM through the channel
    # named (or, with none named, the first that reaches the owner), with the id it keeps for them.
    if channel:
        try:
            thread_ts = await _handoff(delivery_for(provider), channel)
        except RuntimeError:
            return web.json_response({"error": "handoff failed"}, status=500)
        took, where = provider, channel
    else:
        owner = await reach_owner(_handoff, only=provider)
        if not owner.delivered:
            return json_error(
                "channel_handoff_failed",
                message=owner.sentence() or "Channel not connected",
                status=502,
            )
        thread_ts, took, where = owner.result, owner.provider, owner.channel

    _continue_there(
        state, session, provider=took, channel=where, thread_ts=thread_ts, dm=not channel
    )
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_handoff",
        outcome="allowed",
        source="dashboard",
        resources=session.key,
    )
    return web.json_response({"ok": True, "thread_ts": thread_ts, "provider": took})
