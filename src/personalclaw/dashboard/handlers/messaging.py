"""Messaging handlers — spawn, notifications, send-message, channel profile."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _rehydrate_session_from_history
from personalclaw.dashboard.chat_utils import _remove_queued_by_id
from personalclaw.dashboard.state import (
    CRON_NOTIFY_END,
    CRON_NOTIFY_PREFIX,
    DashboardState,
    _rewrite_notifications,
)
from personalclaw.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls
from personalclaw.subagent_persistence import _agent_dir, read_state
from personalclaw.validation import (
    SPAWN_RUN_SCHEMA,
    ValidationError,
    validate_tool_args,
)

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


# ── Subagents ──


async def api_spawn(request: web.Request) -> web.Response:
    """POST /api/spawn — spawn a subagent."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    try:
        cleaned = validate_tool_args(
            {
                "task": body.get("task", ""),
                "agent": body.get("agent", ""),
                "max_turns": body.get("max_turns", 0),
                "cwd": body.get("cwd", ""),
            },
            SPAWN_RUN_SCHEMA,
        )
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    task = (cleaned.get("task") or "").strip()
    if not task:
        return web.json_response({"error": "task is required"}, status=400)
    parent_session = body.get("parent_session", "")
    # approval_mode and silent are HTTP API parameters passed by the SDK,
    # NOT MCP tool arguments from the LLM.  The LLM's subagent_run tool
    # (mcp_core.py) does not expose these params — they are added by the
    # SDK's spawn() method for app-level control.  Validated inline here
    # rather than in SPAWN_RUN_SCHEMA because they are transport-layer
    # params, not tool-schema params.
    #
    # Security: this endpoint requires X-Internal-Secret (internal_paths
    # in server.py), so only local MCP server processes can call it.
    approval_mode = body.get("approval_mode", "")
    if approval_mode not in ("", "auto"):
        return web.json_response({"error": "approval_mode must be '' or 'auto'"}, status=400)
    silent = body.get("silent", False)
    if not isinstance(silent, bool):
        silent = str(silent).lower() in ("true", "1", "yes")
    agent = cleaned.get("agent") or ""
    max_turns = cleaned.get("max_turns") or 0
    cwd = cleaned.get("cwd") or ""
    info = state.subagents.spawn(
        task,
        parent_session_key=parent_session,
        agent=agent,
        max_turns=max_turns,
        cwd=cwd,
        approval_mode=approval_mode or None,
        silent=silent,
    )
    if not info:
        return web.json_response(
            {"error": f"capacity reached ({state.subagents.max_concurrent})"}, status=429
        )
    if info.done and info.error:
        return web.json_response({"error": info.error}, status=400)
    return web.json_response({"id": info.id, "task": task, "status": "spawned"})


def _redact(text: str) -> str:
    """Two-pass redaction for LLM-derived content on external surfaces."""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


async def api_spawn_status(request: web.Request) -> web.Response:
    """GET /api/spawn/{id} — poll subagent status."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    agent_id = request.match_info["agent_id"]
    info = state.subagents.get(agent_id)
    if not info:
        # Fall back to persistence layer (orphaned/recovered agents)
        try:
            disk_state = read_state(agent_id)
            if disk_state:
                disk_data: dict[str, object] = {
                    "id": agent_id,
                    "task": _redact(disk_state.get("task", "")),
                    "done": True,
                    "started": disk_state.get("started"),
                }
                result_path = _agent_dir(agent_id) / "result.txt"
                result = ""
                if result_path.exists() and not is_sensitive_path(str(result_path)):
                    try:
                        result = await asyncio.to_thread(
                            result_path.read_text, encoding="utf-8", errors="replace"
                        )
                    except OSError:
                        pass
                # _redact() defined at line 82 of this file; calls both
                # redact_exfiltration_urls() and redact_credentials() per security guidelines.
                disk_data["result"] = _redact(result) if result else "_No result._"
                # Check for tombstone
                tombstone_path = _agent_dir(agent_id) / "tombstone.json"
                if tombstone_path.exists() and not is_sensitive_path(str(tombstone_path)):
                    try:
                        raw = await asyncio.to_thread(tombstone_path.read_text, encoding="utf-8")
                        ts = json.loads(raw)
                        disk_data["error"] = _redact(f"Orphaned: {ts.get('cause', 'unknown')}")
                    except (OSError, ValueError):
                        disk_data["error"] = "Orphaned (unknown cause)"
                else:
                    disk_data["error"] = ""
                return web.json_response(disk_data)
        except Exception:
            logger.debug("Persistence fallback failed for %s", agent_id, exc_info=True)
        return web.json_response({"error": "not found"}, status=404)
    data = {"id": info.id, "task": _redact(info.task), "done": info.done}  # type: dict[str, object]
    data["started"] = info.started
    if info.done:
        # Read full result from disk (info.result is truncated to 3000 chars)
        result = info.result
        if info.result_path and not is_sensitive_path(info.result_path):
            try:
                result = await asyncio.to_thread(
                    Path(info.result_path).read_text,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                pass
        data["result"] = _redact(result)
        data["error"] = _redact(info.error) if info.error else ""
    else:
        data["turns"] = info.turns
        data["last_tool"] = _redact(info.last_tool)
        data["elapsed"] = round(time.time() - info.started)
    return web.json_response(data)


async def api_spawn_list(request: web.Request) -> web.Response:
    """GET /api/spawn — list all subagents."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"agents": []})
    agents = []
    for info in state.subagents.all_agents:
        entry: dict[str, object] = {
            "id": info.id,
            "task": _redact(info.task),
            "done": info.done,
            "parent": info.parent_session_key,
            "agent": info.agent,
            "started": info.started,
        }
        if info.done:
            entry["result"] = _redact(info.result)
            entry["error"] = _redact(info.error) if info.error else ""
        else:
            entry["turns"] = info.turns
            entry["last_tool"] = _redact(info.last_tool)
            entry["elapsed"] = round(time.time() - info.started)
        agents.append(entry)
    return web.json_response({"agents": agents})


async def api_spawn_delete(request: web.Request) -> web.Response:
    """DELETE /api/spawn/{agent_id} — cancel a running subagent or remove a finished one."""
    state: DashboardState = request.app["state"]
    agent_id = request.match_info["agent_id"]
    if not state.subagents or agent_id not in state.subagents._agents:
        return web.json_response({"error": "not found"}, status=404)
    cancelled = await state.subagents.cancel(agent_id)
    if not cancelled:
        # Already done — just remove from list
        state.subagents._agents.pop(agent_id, None)
        state.subagents._tasks.pop(agent_id, None)
    return web.json_response({"ok": True, "cancelled": cancelled})


async def api_spawn_clear(request: web.Request) -> web.Response:
    """DELETE /api/spawn — clear all completed subagents."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"ok": True})
    done_ids = [a.id for a in state.subagents.all_agents if a.done]
    for aid in done_ids:
        state.subagents._agents.pop(aid, None)
        state.subagents._tasks.pop(aid, None)
    return web.json_response({"ok": True, "cleared": len(done_ids)})


# ── Sessions / Notifications ──


async def api_notifications(request: web.Request) -> web.Response:
    state: DashboardState = request.app["state"]
    return web.json_response(
        {"notifications": state._notification_log, "unread": state.unread_count()}
    )


async def api_notification_delete(request: web.Request) -> web.Response:
    """DELETE /api/notifications — delete a single notification by timestamp."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.delete_notification(ts)
    return web.json_response({"ok": ok})


async def api_notifications_clear(request: web.Request) -> web.Response:
    """POST /api/notifications/clear — clear all notifications."""
    state: DashboardState = request.app["state"]
    state.clear_notifications()
    return web.json_response({"ok": True})


async def api_notification_ack(request: web.Request) -> web.Response:
    """POST /api/notifications/ack — mark a single notification as read."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.ack_notification(ts)
    return web.json_response({"ok": ok})


async def api_notification_unack(request: web.Request) -> web.Response:
    """POST /api/notifications/unack — mark a single notification as unread."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.unack_notification(ts)
    return web.json_response({"ok": ok})


async def api_notifications_ack_all(request: web.Request) -> web.Response:
    """POST /api/notifications/ack-all — mark all notifications as read."""
    state: DashboardState = request.app["state"]
    for n in state._notification_log:
        n["acked"] = True
    _rewrite_notifications(state._notification_log)
    state.broadcast_ws("notification_ack", {"ts": "*"})
    return web.json_response({"ok": True})


_MAX_BLOCKS = 50  # rich-message block limit (channel wire cap)
_MAX_WALK_DEPTH = 10  # defense-in-depth against deeply nested LLM output


def _sanitize_blocks(
    blocks: list[dict],
    *redactors: Any,
) -> list[dict]:
    """Walk Block Kit blocks and sanitize all strings (both keys and values).

    Block Kit structural keys (type, text, mrkdwn, etc.) pass through
    sanitizers unchanged since they don't match hostile patterns.
    """
    from copy import deepcopy  # noqa: F811

    def _redact_str(s: str) -> str:
        for fn in redactors:
            s, _ = fn(s)
        return s

    def _walk(obj: Any, depth: int = 0) -> Any:
        if depth > _MAX_WALK_DEPTH:
            if isinstance(obj, str):
                return _redact_str(obj)
            if isinstance(obj, (dict, list)):
                return {} if isinstance(obj, dict) else []
            return obj  # scalars (int, bool, None) are safe
        if isinstance(obj, str):
            return _redact_str(obj)
        if isinstance(obj, dict):
            return {_redact_str(k): _walk(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(item, depth + 1) for item in obj]
        return obj

    return _walk(deepcopy(blocks[:_MAX_BLOCKS]))


def _resolve_session_target(
    state: DashboardState, target: str, caller_session: str
) -> tuple[str, str] | tuple[None, None]:
    """Resolve a session target to a dashboard session key and job name.

    ``target="origin"`` looks up the cron job that owns *caller_session*
    and returns ``(session_key, job_name)``.
    Returns ``(None, None)`` if the origin session can't be resolved
    (non-"origin" target, non-cron caller, unknown job, or cron with no
    originating session_key — e.g. one created from the dashboard UI).

    Note: ``target="channel"`` is NOT handled here — it is intercepted in
    ``api_send_message`` and converted to an explicit fall-through to the
    channel-delivery path, so it never reaches this resolver.
    """
    if target != "origin":
        return None, None  # only "origin" is allowed — reject arbitrary session keys
    # caller_session is e.g. "cron:abc12345" — extract the job ID
    if not caller_session.startswith("cron:"):
        return None, None
    cron_id = caller_session.removeprefix("cron:")
    jobs = state.crons.list_jobs(include_disabled=True)
    job = next((j for j in jobs if j.id == cron_id), None)
    if not job or not job.session_key:
        return None, None
    # session_key is e.g. "dashboard:chat-3-1712793600" but session names
    # don't have the "dashboard:" prefix
    session_name = job.session_key.removeprefix("dashboard:")
    return session_name, job.name


def _is_owner_user(owner_id: str, user_id: str) -> bool:
    """Owner-only channel access (multi-user disabled), with W/U prefix cross-match."""
    if not owner_id or not user_id:
        return False
    return (
        user_id == owner_id
        or user_id.replace("W", "U", 1) == owner_id
        or user_id.replace("U", "W", 1) == owner_id
    )


def _is_tracked_channel(state: "DashboardState", channel_id: str) -> bool:
    """Whether a channel is in the ACTIVE channel app's outbound allowlist.

    The channel app owns its tracked-channel config; core
    consults it through the provider-agnostic ChannelDelivery seam. No channel
    connected → nothing is tracked (deny-by-default)."""
    if not channel_id:
        return False
    delivery = getattr(state, "channel_delivery", None)
    if delivery is None or not hasattr(delivery, "is_tracked_channel"):
        return False
    try:
        return bool(delivery.is_tracked_channel(channel_id))
    except Exception:
        logger.exception("is_tracked_channel failed")
        return False


async def api_send_message(request: web.Request) -> web.Response:
    """POST /api/send-message — deliver a message to the messaging channel and/or dashboard.

    Authorization is channel-agnostic: owner-only user access + a config-backed
    tracked-channel allowlist. No import of any channel app — delivery goes through
    the provider-agnostic ``state.channel_delivery`` (:class:`ChannelDelivery`)."""
    from personalclaw.validation import CHANNEL_ID_RE, USER_ID_RE  # noqa: F811

    _owner = getattr(request.app["state"], "owner_id", "") or ""

    def is_tracked_channel(channel_id: str) -> bool:
        return _is_tracked_channel(request.app["state"], channel_id)

    def is_allowed_user(user_id: str) -> bool:
        return _is_owner_user(_owner, user_id)

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text required"}, status=400)
    title = body.get("title", "Agent Message")
    blocks = body.get("blocks")
    if blocks and not isinstance(blocks, list):
        return web.json_response({"error": "blocks must be an array"}, status=400)

    target_channel = body.get("channel", "").strip()
    target_user = body.get("user", "").strip()
    unfurl_links = body.get("unfurl_links")
    unfurl_media = body.get("unfurl_media")
    if (unfurl_links is not None and not isinstance(unfurl_links, bool)) or (
        unfurl_media is not None and not isinstance(unfurl_media, bool)
    ):
        return web.json_response(
            {"error": "unfurl_links and unfurl_media must be booleans"}, status=400
        )

    thread_ts = body.get("thread_ts")
    if thread_ts is not None:
        if not isinstance(thread_ts, str) or not re.match(r"^\d+\.\d+$", thread_ts):
            return web.json_response(
                {"error": "thread_ts must be a channel timestamp string like '1712793600.123456'"},
                status=400,
            )
    reply_broadcast = body.get("reply_broadcast")
    if reply_broadcast is not None and not isinstance(reply_broadcast, bool):
        return web.json_response({"error": "reply_broadcast must be a boolean"}, status=400)
    if reply_broadcast and not thread_ts:
        return web.json_response({"error": "reply_broadcast requires thread_ts"}, status=400)

    # Fail fast: mutual exclusion before any redaction/regex work
    if target_channel and target_user:
        return web.json_response({"error": "specify channel or user, not both"}, status=400)

    # Validate format first, then redact
    if target_channel and not CHANNEL_ID_RE.match(target_channel):
        return web.json_response({"error": "invalid channel ID format"}, status=400)
    if target_user and not USER_ID_RE.match(target_user):
        return web.json_response({"error": "invalid user ID format"}, status=400)

    # Redact after format validation
    if target_channel:
        target_channel, _ = redact_exfiltration_urls(target_channel)
        target_channel, _ = redact_credentials(target_channel)
    if target_user:
        target_user, _ = redact_exfiltration_urls(target_user)
        target_user, _ = redact_credentials(target_user)

    # Sanitize LLM-generated content before any external surface.
    # This covers all downstream paths (session injection, fallback, channel).
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    if blocks:
        blocks = _sanitize_blocks(blocks, redact_exfiltration_urls, redact_credentials)

    # --- Authorization gates (before any side effects) ---
    if target_channel and not is_tracked_channel(target_channel):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="send_message",
            outcome="denied",
            downstream_service="channel",
            resources=f"target_channel={target_channel}",
        )
        return web.json_response(
            {
                "error": f"channel {target_channel} is not in the channel app's tracked "
                "channels. Add it in the channel app's settings (tracking channels "
                "via /personalclaw #channel or the app config)."
            },
            status=403,
        )

    if target_user and not is_allowed_user(target_user):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="send_message",
            outcome="denied",
            downstream_service="channel",
            resources=f"target_user={target_user}",
        )
        return web.json_response(
            {"error": "user not in allowlist — add them in the channel app's settings"}, status=403
        )

    sent_channel = False
    channel_ts: str | None = None
    sent_session = False
    target_session = body.get("session")
    job_name = None
    channel_attempted = False
    channel_error = ""
    try:
        # ───────────────────────────────────────────────────────────────────
        # send_message delivery contract
        # ───────────────────────────────────────────────────────────────────
        # For cron jobs, the intended behavior is:
        #
        #   1. Try the origin dashboard session first (the chat that created
        #      this cron). Inject the message there so the session agent can
        #      react to it (not just display it). When injection succeeds,
        #      the message appears in the chat UI directly — no extra bell
        #      notification needed.
        #   2. Fall through to the owner channel DM if origin is unreachable.
        #   3. Dashboard notification (bell icon + notifications.jsonl) fires
        #      ONLY on the fallback path, so channel-less setups still surface
        #      messages that couldn't reach their origin. The invariant is
        #      "never silently dropped", not "always notified".
        #
        # "Origin reachable" = one of:
        #   - Hot: session in state._sessions (user has the tab open) → fast path
        #   - Cold: session not loaded but JSONL exists without closed=true →
        #     _rehydrate_session_from_history restores it from disk, tab reappears
        #
        # "Origin unreachable" = any of:
        #   - User clicked ✕ on the tab (closed=true in JSONL metadata) —
        #     respect the close, do NOT resurrect the tab
        #   - JSONL file deleted entirely (history.delete_session)
        #   - Cron created from dashboard UI without an originating chat
        #     (job.session_key is empty — api_crons_create never sets it)
        #   - Cron's caller_session doesn't match any known job
        #
        # session param values (enforced by _resolve_session_target):
        #   - "origin":  route to originating dashboard session as above
        #   - "channel": explicitly bypass origin, go straight to the owner's
        #                messaging-channel DM (or channel/user if those are
        #                also set). Useful when the prompt author wants
        #                channel delivery regardless. Treated as a
        #                fallback-path call: notification fires.
        #   - omitted:   for cron callers, auto-defaults to "origin" in
        #                mcp_core.py. For non-cron callers, goes to owner DM
        #                as before (also a fallback-path call).
        #
        # Security note: caller_session is set by the MCP tool from
        # PERSONALCLAW_SESSION_KEY (gateway-injected at process spawn, not LLM
        # input). The endpoint is HMAC-protected via X-Internal-Secret, so
        # only our own ACP processes can call it. _resolve_session_target
        # further restricts session= to "origin"/"channel".
        # ───────────────────────────────────────────────────────────────────
        if target_session == "channel":
            # Explicit opt-out: skip origin routing entirely, fall through to
            # the owner's messaging channel (or channel/user if also set).
            target_session = None
        if target_session:
            session_name, job_name = _resolve_session_target(
                state, target_session, body.get("caller_session", "")
            )
            if session_name:
                # Resolve the origin session. get_session is the hot path (fast,
                # O(1) dict lookup). On miss, _rehydrate_session_from_history
                # restores from disk if the session exists and isn't closed.
                # Truly-gone sessions (never persisted, deleted, or closed)
                # return None and delivery falls through to the channel DM
                # path below — no phantom empty tab is ever created.
                session = state.get_session(session_name)
                was_loaded = session is not None
                if session is None:
                    session = _rehydrate_session_from_history(state, session_name)
                logger.info(
                    "send_message session=origin resolved session_name=%s job=%s was_loaded=%s rehydrated=%s",  # noqa: E501
                    session_name,
                    job_name,
                    was_loaded,
                    (session is not None and not was_loaded),
                )
                if session:
                    label = job_name or "cron"
                    label, _ = redact_exfiltration_urls(label)
                    label, _ = redact_credentials(label)
                    # text and title already redacted above (L2538-2542)
                    # Text wrapper kept for LLM context and queue detection;
                    # cronLabel in cls JSON provides structured data for frontend.
                    wrapped = f'{CRON_NOTIFY_PREFIX}"{label}"]\n{text}\n{CRON_NOTIFY_END}'
                    inject_cls = json.dumps({"cronLabel": label})
                    if session.running:
                        if len(session._queue) >= 50:
                            evicted = session.queue_pop(0)
                            logger.warning(
                                "Queue full for session %s — evicting oldest message", session_name
                            )
                            _remove_queued_by_id(session.messages, evicted["id"])
                        qid = session.queue_append(wrapped)
                        _cls = json.loads(inject_cls)
                        _cls["queue_id"] = qid
                        session.append("queued", wrapped, json.dumps(_cls))
                        state.push_sessions_update()
                    else:
                        # circular import: chat_runner imports from
                        # personalclaw.dashboard.handlers (MAX_PROMPT_BYTES,
                        # _list_provider_prompts), so we can't import it at
                        # module top-level without a cycle.
                        from personalclaw.dashboard.chat_runner import _run_chat

                        session.append("inject", wrapped, inject_cls)
                        task = asyncio.create_task(_run_chat(state, session, wrapped))
                        session.task = task
                        state._background_tasks.add(task)
                        task.add_done_callback(state._background_tasks.discard)
                        state.push_sessions_update()
                    sent_session = True
        # Fall back to normal delivery if no session target or session is gone
        if not sent_session:
            if target_session and job_name:
                safe_name, _ = redact_exfiltration_urls(job_name)
                safe_name, _ = redact_credentials(safe_name)
                title = f"⏰ {safe_name}"
                text += "\n\n_(session closed — delivered as notification)_"
            state.notify("agent", title, text)
            if state.channel_delivery:
                try:
                    if target_channel:
                        channel = target_channel
                    elif target_user:
                        channel = await state.channel_delivery.open_dm(target_user)
                    elif state.owner_id:
                        channel = await state.channel_delivery.open_dm(state.owner_id)
                    else:
                        channel = ""

                    if channel:
                        channel_attempted = True
                        if blocks:
                            channel_ts = await state.channel_delivery.deliver_rich(
                                channel,
                                blocks,
                                text,
                                thread_ts=thread_ts,
                                unfurl_links=unfurl_links,
                                unfurl_media=unfurl_media,
                                reply_broadcast=reply_broadcast,
                            )
                        else:
                            channel_ts = await state.channel_delivery.deliver_text(
                                channel,
                                text,
                                thread_ts=thread_ts,
                                unfurl_links=unfurl_links,
                                unfurl_media=unfurl_media,
                                reply_broadcast=reply_broadcast,
                            )
                        sent_channel = True
                except Exception as exc:
                    channel_attempted = True
                    channel_error = str(exc)
                    logger.exception("send_message: channel delivery failed")
    finally:
        try:
            thread_hint = " threaded=1" if thread_ts else ""
            if reply_broadcast:
                thread_hint += " broadcast=1"
            base_res = (
                f"target_channel={target_channel} target_user={target_user}"
                if (target_channel or target_user)
                else ("session=origin" if sent_session else "fallback=owner_dm")
            )
            _sel().log_tool_invocation(
                session_key="dashboard",
                tool_name="send_message",
                outcome=(
                    "completed"
                    if sent_channel or sent_session or not channel_attempted
                    else "error"
                ),
                downstream_service=(
                    "session" if sent_session else ("channel" if sent_channel else "dashboard")
                ),
                resources=base_res + thread_hint,
            )
        except Exception:
            logger.warning("SEL logging failed for send_message", exc_info=True)
    if channel_attempted and not sent_channel:
        safe_error, _ = redact_credentials(channel_error)
        safe_error, _ = redact_exfiltration_urls(safe_error)
        return web.json_response(
            {"ok": False, "error": f"Channel delivery failed: {safe_error}", "channel": False},
            status=502,
        )
    resp_body: dict[str, Any] = {"ok": True, "channel": sent_channel, "session": sent_session}
    if channel_ts:
        resp_body["ts"] = channel_ts
    return web.json_response(resp_body)


async def api_channel_profile(request: web.Request) -> web.Response:
    """POST /api/channel/profile — read a channel user's profile."""
    import time  # noqa: F811

    from personalclaw.validation import USER_ID_RE  # noqa: F811

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    raw_user = body.get("user", "")
    if not isinstance(raw_user, str):
        return web.json_response({"error": "user must be a string"}, status=400)
    user_id = raw_user.strip()
    if not user_id:
        return web.json_response({"error": "user required"}, status=400)
    # Validate format first, then redact
    if not USER_ID_RE.match(user_id):
        return web.json_response({"error": "invalid user ID format"}, status=400)
    user_id, _ = redact_exfiltration_urls(user_id)
    user_id, _ = redact_credentials(user_id)

    # Authorization first (deny-by-default) — owner-only (multi-user disabled).
    if not _is_owner_user(getattr(state, "owner_id", "") or "", user_id):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="denied",
            downstream_service="channel",
            resources=f"user={user_id}",
        )
        return web.json_response({"error": "user not in allowlist"}, status=403)

    if not state.channel_delivery:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="error",
            downstream_service="channel",
            resources=f"user={user_id} reason=channel_not_connected",
        )
        return web.json_response({"error": "Channel not connected"}, status=503)

    # Rate limiting: max 5 profile lookups per minute
    # Only counts authorized requests — unauthorized 403s don't consume sessions
    now = time.monotonic()
    history: list[float] = getattr(state, "_profile_lookup_times", [])
    history = [t for t in history if now - t < 60]
    if len(history) >= 5:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="denied",
            downstream_service="channel",
            resources=f"user={user_id} reason=rate_limit",
        )
        return web.json_response(
            {"error": "rate limit exceeded — max 5 profile lookups per minute"}, status=429
        )
    history.append(now)
    state._profile_lookup_times = history  # type: ignore[attr-defined]

    try:
        profile = await state.channel_delivery.resolve_user_profile(user_id)
    except Exception:
        logger.exception("channel-profile: failed for %s", user_id)
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="error",
            downstream_service="channel",
            resources=f"user={user_id}",
        )
        return web.json_response({"error": "Channel API error"}, status=502)

    # Redact free-form profile fields that could contain prompt-injection
    for key in list(profile):
        val = profile[key]
        if isinstance(val, str) and key not in ("id",):
            val, _ = redact_exfiltration_urls(val)
            val, _ = redact_credentials(val)
            profile[key] = val

    _sel().log_tool_invocation(
        session_key="dashboard",
        tool_name="read_channel_profile",
        outcome="completed",
        downstream_service="channel",
        resources=f"user={user_id}",
    )
    return web.json_response({"profile": profile})
