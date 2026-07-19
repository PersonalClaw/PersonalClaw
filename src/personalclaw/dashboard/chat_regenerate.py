"""Regenerate, variant switch, and edit-resend endpoints."""

import asyncio
import logging
from datetime import datetime

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _save_session_to_history
from personalclaw.dashboard.chat_runner import _run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

_MAX_VARIANTS = 20


async def _persist_history_off_thread(
    state: DashboardState, session: _ChatSession, label: str
) -> None:
    """Rewrite the session's persisted history off the event loop.

    Snapshots the current message list and writes it on a worker thread so a
    slow disk write never blocks the event loop. Failures are logged but not
    raised — the in-memory session stays authoritative until the next save.
    """
    try:
        msgs_snapshot = list(session.messages)
        await asyncio.to_thread(_save_session_to_history, state, session, msgs_snapshot)
    except Exception:
        logger.warning("%s: failed to persist session history", label, exc_info=True)


async def api_chat_session_regenerate(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/regenerate — regenerate the last assistant reply."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    async with session._lock:
        if session.running:
            return web.json_response({"error": "session is running"}, status=409)

        msgs = session.messages
        ai_idx = -1
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "assistant":
                ai_idx = i
                break
        if ai_idx < 0:
            return web.json_response({"error": "no assistant message to regenerate"}, status=400)
        u_idx = -1
        for i in range(ai_idx - 1, -1, -1):
            if msgs[i].get("role") == "user":
                u_idx = i
                break
        if u_idx < 0:
            return web.json_response({"error": "no preceding user message"}, status=400)

        user_msg = msgs[u_idx].get("content", "")
        if not user_msg:
            return web.json_response({"error": "empty user message"}, status=400)

        ai_msg = msgs[ai_idx]
        _rv = ai_msg.get("variants")
        variants: list[dict] = list(_rv) if isinstance(_rv, list) else []  # type: ignore[arg-type]
        current_entry = {"content": ai_msg.get("content", ""), "ts": ai_msg.get("ts", "")}
        if not any(v.get("content") == current_entry["content"] for v in variants):
            variants.append(current_entry)
        if len(variants) > _MAX_VARIANTS:
            variants = variants[-_MAX_VARIANTS:]

        del session.messages[u_idx + 1:]
        session._dirty = True
        session._resumed_count = 0
        session._pending_variants = variants

        await _persist_history_off_thread(state, session, "regenerate")

        sel().log_api_access(
            caller="dashboard",
            operation="chat.regenerate",
            outcome="allowed",
            source="dashboard",
            resources=session.key,
        )

        hint = (
            "The user regenerated the previous response. Produce a fresh answer — "
            "vary phrasing, structure, or angle. Do not say you already answered or "
            "reference the prior reply."
        )
        task = asyncio.create_task(_run_chat(state, session, user_msg, regenerate_hint=hint))
        session.task = task
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

        def _clear_pending_on_done(t: asyncio.Task) -> None:
            if session._pending_variants:
                if not t.cancelled() and t.exception() is None:
                    logger.warning("Regenerate: pending variants not consumed by flush, discarding")
                session._pending_variants = []

        task.add_done_callback(_clear_pending_on_done)
    state.push_sessions_update()
    return web.json_response({"ok": True})


async def api_chat_session_switch_variant(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/switch-variant — switch which regenerated variant is active."""

    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)
    try:
        idx = int(body.get("index"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid index"}, status=400)

    async with session._lock:
        if session.running:
            return web.json_response({"error": "session is running"}, status=409)

        target = None
        for m in reversed(session.messages):
            if m.get("role") == "assistant" and m.get("variants"):
                target = m
                break
        if target is None:
            return web.json_response({"error": "no variants"}, status=400)
        raw_target_variants = target.get("variants")
        variants: list[dict] = (
            list(raw_target_variants)  # type: ignore[arg-type]
            if isinstance(raw_target_variants, list)
            else []
        )
        if idx < 0 or idx >= len(variants):
            return web.json_response({"error": "index out of range"}, status=400)

        chosen = variants[idx]
        if not isinstance(chosen, dict):
            return web.json_response({"error": "corrupt variant entry"}, status=400)
        target_dict: dict = target
        target_dict["content"] = chosen.get("content", "")
        target_dict["ts"] = chosen.get("ts", target_dict.get("ts", ""))
        target_dict["variant_idx"] = idx
        session._dirty = True
        session._resumed_count = 0
        await _persist_history_off_thread(state, session, "switch-variant")
        sel().log_api_access(
            caller="dashboard",
            operation="chat.switch_variant",
            outcome="allowed",
            source="dashboard",
            resources=session.key,
        )
        _bc, _ = redact_exfiltration_urls(target_dict["content"])
        _bc, _ = redact_credentials(_bc)
        state.broadcast_ws(
            "chat_variant_switch",
            {"session": session.key, "index": idx, "count": len(variants), "content": _bc},
        )
        return web.json_response({"ok": True, "index": idx})


async def api_chat_session_edit_resend(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/edit-resend — edit a user message and resend."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    index = body.get("index")
    ts = body.get("ts")
    client_ts = body.get("client_ts")
    content = (body.get("content") or "").strip()
    if not content:
        return web.json_response({"error": "content is required"}, status=400)

    async with session._lock:
        if session.running:
            return web.json_response({"error": "session is running"}, status=409)

        msgs = session.messages

        # Locate the user message to edit, as a graceful cascade so the edit never
        # dead-ends: (1) by ts, (2) by a valid user-message index, (3) the LAST user
        # message. "Edit & resend" always targets the most recent user turn from the
        # composer, so the final fallback is the right target — and it makes the
        # endpoint robust to a client whose optimistic turn had no ts yet.
        target = -1
        if ts:
            target = next((i for i, m in enumerate(msgs) if m.get("ts") == ts and m.get("role") == "user"), -1)
        if target < 0 and isinstance(index, int) and 0 <= index < len(msgs) and msgs[index].get("role") == "user":
            target = index
        if target < 0:
            target = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "user"), -1)
        if target < 0:
            return web.json_response({"error": "no user message to edit"}, status=400)
        index = target

        del session.messages[index:]
        session._dirty = True
        session._resumed_count = 0

        _bc, _ = redact_exfiltration_urls(content)
        _bc, _ = redact_credentials(_bc)
        # Store the FE's fresh client ts (if valid ISO-8601) on the re-appended
        # message so a subsequent edit-resend still matches by ts; else server-stamp.
        _resend_ts = ""
        if isinstance(client_ts, str) and client_ts:
            try:
                datetime.fromisoformat(client_ts)
                _resend_ts = client_ts
            except (ValueError, TypeError):
                _resend_ts = ""
        session.append("user", _bc, "msg msg-u", ts=_resend_ts)

        await _persist_history_off_thread(state, session, "edit-resend")

        sel().log_api_access(
            caller="dashboard",
            operation="chat.edit_resend",
            outcome="allowed",
            source="dashboard",
            resources=session.key,
        )

        task = asyncio.create_task(_run_chat(state, session, _bc))
        session.task = task
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

        def _on_done(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception() is not None:
                logger.error("edit-resend _run_chat failed for %s", session.key, exc_info=t.exception())

        task.add_done_callback(_on_done)

    state.push_sessions_update()
    return web.json_response({"ok": True})
