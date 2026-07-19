"""HTTP API handlers for dashboard chat endpoints."""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import (
    AppConfig,
    config_dir,
    default_workspace_dir,
    resolve_agent_bindings,
)
from personalclaw.dashboard.chat_persistence import (
    _attach_variants,
    _redact_meta,
    _rehydrate_session_from_history,
    _save_session_to_history,
    _validate_reasoning_effort,
    resolve_session,
)
from personalclaw.dashboard.chat_runner import _run_chat
from personalclaw.dashboard.chat_utils import (
    _build_stream_chunk,
    _emit_agent_assignment,
    _history_key_for,
    _normalize_model,
    _prepare_messages,
    _redact_for_display,
    _remove_queued_by_id,
    _sync_dashboard_sessions,
    resolve_history_key,
)
from personalclaw.dashboard.state import (
    DashboardState,
    _ChatSession,
    _mark_permission_resolved,
)
from personalclaw.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls
from personalclaw.sel import SecurityEvent, sel
from personalclaw.validation import _AGENT_NAME_RE

logger = logging.getLogger(__name__)


async def api_chat(request: web.Request) -> web.StreamResponse:
    """POST /api/chat — send message to a session, stream response via SSE."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    message = body.get("message", "").strip()
    agent = body.get("agent", "")
    session_name = body.get("session")
    color_theme = body.get("color_theme", "")
    user_meta = body.get("meta")  # knowledge/files/pastes metadata from frontend
    if not isinstance(user_meta, dict):
        user_meta = None
    # Client-supplied message timestamp: the FE stamps its optimistic user turn with
    # this and we store the SAME ts here, so the turn's ts matches the persisted
    # message immediately (we don't broadcast the user echo). Without it a live turn
    # has no ts until reload, which broke Edit & resend's by-ts lookup. Validate as
    # ISO-8601; pop it so it doesn't linger in persisted meta.
    client_ts = ""
    if user_meta:
        _raw_ts = user_meta.pop("client_ts", "")
        if isinstance(_raw_ts, str) and _raw_ts:
            try:
                datetime.fromisoformat(_raw_ts)
                client_ts = _raw_ts
            except (ValueError, TypeError):
                client_ts = ""  # malformed → fall back to server-stamped ts
        if not user_meta:
            user_meta = None
    if not isinstance(color_theme, str) or color_theme not in {"", "lumon"}:
        color_theme = ""
    if not isinstance(agent, str) or not (agent == "" or _AGENT_NAME_RE.match(agent)):
        _emit_agent_assignment(str(session_name or ""), str(agent), outcome="denied_invalid")
        return web.json_response({"error": "invalid agent name"}, status=400)
    if not isinstance(session_name, str) and session_name is not None:
        session_name = None  # coerce non-string session to auto-generate

    session = state.get_or_create_session(session_name, app=request.get("app", ""))

    # App ownership check: deny-by-default for app tokens.
    # Apps can only access sessions they own. Dashboard users (empty request_app)
    # can access everything.
    request_app = request.get("app", "")
    if request_app:
        if not session._app:
            # Unscoped session created by dashboard — apps cannot access it.
            sel().log_api_access(
                caller=request_app, operation="chat_send", outcome="denied",
                source="app_isolation", resources=f"session={session.key}",
                error="app cannot access unscoped sessions",
            )
            return web.json_response({"error": "app cannot access unscoped sessions"}, status=403)
        elif request_app != session._app:
            sel().log_api_access(
                caller=request_app, operation="chat_send", outcome="denied",
                source="app_isolation", resources=f"session={session.key}",
                error="app does not own this session",
            )
            return web.json_response({"error": "app does not own this session"}, status=403)

    if session.agent not in (None, ""):
        # Session already has an agent — only reject explicit mismatches (non-empty different agent).
        # Empty agent in request means "use existing" (e.g. follow-up messages from frontend).
        if agent and session.agent != agent:
            _emit_agent_assignment(session.key, agent or "", outcome="denied_mismatch")
            return web.json_response({"error": "session agent mismatch"}, status=409)
        else:
            logger.debug("agent match for session=%s agent=%s", session.key, agent)
    elif agent:
        # Session has no agent — set it if not running
        if session.running:
            _emit_agent_assignment(session.key, agent, outcome="denied_running")
            return web.json_response(
                {"error": "cannot set agent on running session"},
                status=409,
            )
        session.agent = agent
        _emit_agent_assignment(session.key, agent)
    else:
        # No agent on session, no agent in request — nothing to enforce.
        pass

    if "color_theme" in body:
        session.color_theme = color_theme

    if session.running:
        # Mid-run handling (#37) — 4 modes:
        #   steer (default): inject at the next model boundary of the RUNNING turn
        #     (native loop only); followup: queue for after the turn; collect: queue
        #     (coalesced later); interrupt: handled by /interrupt, not here.
        mode = str(body.get("queue_mode") or "steer").strip().lower()
        if message and mode == "steer" and state.sessions.add_steer(session.key, message):
            _c, _ = redact_exfiltration_urls(message)
            _c, _ = redact_credentials(_c)
            state.broadcast_ws("activity_event", {
                "session": session.key, "kind": "status",
                "text": f"Steering: {_redact_for_display(_c)[:80]}",
            })
            return web.json_response({"ok": True, "steered": True})
        # followup / collect / steer-when-not-native → queue as before.
        if message:
            qid = session.queue_append(message)
            _c, _ = redact_exfiltration_urls(message)
            _c, _ = redact_credentials(_c)
            _redacted = _redact_for_display(_c)
            state.broadcast_ws("queue_push", {"session": session.key, "content": _redacted, "ts": datetime.now(timezone.utc).isoformat(), "queue_id": qid})
        return web.json_response({"ok": True, "queued": True})

    if not message:
        return web.json_response({"error": "message is required"}, status=400)

    # WS mode: return JSON immediately, chunks delivered via WebSocket
    ws_mode = request.query.get("ws") == "1"

    session._has_reader = not ws_mode  # Only block SSE broadcast if HTTP SSE reader
    if user_meta:
        user_meta = _redact_meta(user_meta)
    session.append("user", message, "msg msg-u", ts=client_ts, meta=user_meta)

    # ── AutoNudge: user input cancels any pending nudge timer (user wins). ──
    try:
        from personalclaw.autonudge import (
            get_instance as _autonudge_get,  # circular: autonudge -> dashboard.chat -> chat_handlers
        )

        _autonudge = _autonudge_get()
        if _autonudge is not None:
            _autonudge.notify_user_input(session.key)
    except Exception:
        logger.warning("autonudge.notify_user_input failed", exc_info=True)

    # ── Orchestrator stop detection ─────────────────────────────────
    _stop_words = {"stop", "cancel", "abort"}
    tracker = session._orch_tracker
    if (
        tracker is not None
        and tracker.has_escalated
        and not tracker.stopped
        and message.strip().lower().split()[0] in _stop_words
    ):
        tracker.stop()
        session._auto_run = False
        # Cancel running agents for this session
        if state.subagents:
            session_key = f"dashboard:{session.key}"
            mgr = state.subagents
            for a in mgr.running_agents_for(session_key):
                t = mgr._tasks.get(a["id"])
                if t and not t.done():
                    t.cancel()
        stop_msg = "🛑 [SYSTEM] Orchestration stopped by user."
        session.append("assistant", stop_msg, "msg msg-a")
        state.broadcast_ws(
            "chat_message", {"session": session.key, "role": "assistant", "content": stop_msg}
        )
        state.broadcast_ws("chat_done", {"session": session.key})
        return web.json_response({"ok": True, "stopped": True})

    # ── Reset rounds after user guidance (not a stop) ───────────────
    if tracker is not None and tracker.has_escalated:
        tracker.reset_after_guidance()
        logger.info("Rounds reset after user guidance for session %s", session.key)

    task = asyncio.create_task(_run_chat(state, session, message))
    session.task = task
    session._recovery_retrigger_count = 0
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    state.push_sessions_update()

    if ws_mode:
        return web.json_response({"ok": True, "session": session.key})

    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    try:
        while True:
            pending = session.drain()
            for msg in pending:
                if msg["cls"] == "done":
                    await resp.write(b"data: [DONE]\n\n")
                    session._has_reader = False
                    return resp
                chunk = _build_stream_chunk(msg)
                await resp.write(f"data: {chunk}\n\n".encode())
            try:
                await asyncio.wait_for(session.event.wait(), timeout=30)
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, ClientConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        session.drain()
        session._has_reader = False
    return resp


# Worker session key prefixes — loop/code/campaign engines persist under the
# ``dashboard_`` namespace like user chats, but are NOT user-initiated chats.
# They're identified by key prefix (robust: their `agent` field varies —
# personalclaw-loop, "Claude Code", "default", …) rather than the in-memory `app`
# tag, which isn't always persisted to disk. Each prefix maps to an ORIGIN so the
# history list can tag these sessions and default-hide them behind a filter
# (rather than dropping them entirely, which left loop/code conversations
# unreachable). The originating entity id is the key with the prefix stripped.
_WORKER_PREFIX_ORIGIN = (("loop-", "loop"), ("campaign-", "campaign"))

# The unified planner session key (loop-plan-<id>) — no standing loop to link to.
_LOOP_PLAN_PREFIX = "loop-plan-"


def _origin_of(name: str, app: str = "") -> tuple[str, str]:
    """Classify a session by origin → ``(origin, source_id)``.

    ``origin`` is ``manual`` for a user-initiated chat, else ``loop`` (every unified
    loop kind — general/goal/code/design) or ``campaign``. Prefer the persisted/in-
    memory ``app`` tag when present; fall back to the key prefix (the only signal for
    disk-only worker sessions). The source_id is the originating loop id.
    """
    for prefix, origin in _WORKER_PREFIX_ORIGIN:
        if name.startswith(prefix):
            if origin == "loop":
                # Unified loop sessions: loop-<id> (main worker), loop-<id>-<taskid>
                # (a parallel code/design task-worker → its parent loop <id>), and
                # loop-plan-<id> (the stepwise planner → no standing loop to link).
                if name.startswith(_LOOP_PLAN_PREFIX):
                    return origin, ""
                rest = name[len(prefix):]
                from personalclaw.loop import store as loop_store
                if loop_store.valid_loop_id(rest):
                    return origin, rest          # main worker → exact loop id
                # task-worker loop-<id>-<taskid>: the loop id is the FIRST segment (the
                # task id itself is hyphenated, e.g. t-abc, so a trailing rsplit is
                # wrong) — take the leading segment when it's a valid loop id.
                head = rest.split("-", 1)[0]
                return origin, (head if loop_store.valid_loop_id(head) else rest)
            return origin, name[len(prefix):]
    if app in ("loop", "code", "campaign"):
        return "loop" if app == "code" else app, ""
    return "manual", ""


def _origin_label(origin: str, source_id: str) -> str:
    """A friendly name for a worker session's originating loop (any unified kind), for
    the history row's origin chip. Falls back to the id. Best-effort: a missing/failed
    lookup yields the bare id."""
    if origin == "channel":
        # source_id is the channel id; a friendly "Channel · <id>" chip.
        return f"Channel · {source_id}" if source_id else "Channel"
    if not source_id:
        return ""
    try:
        if origin == "loop":
            from personalclaw.loop import store as loop_store
            lp = loop_store.get(source_id)
            return (lp.name if lp and lp.name else source_id)
    except Exception:
        logger.debug("origin label lookup failed for %s/%s", origin, source_id, exc_info=True)
    return source_id


async def api_chat_sessions(request: web.Request) -> web.Response:
    """GET /api/chat/sessions — list all chat sessions.

    Merges in-memory sessions with persisted-on-disk ones so the history list
    survives gateway restarts and ``restore_sessions=false`` (older chats live
    only on disk until opened). In-memory entries win on key collision (they're
    live/authoritative). Non-persistent (incognito/temporary) histories are
    excluded. Worker sessions (goal loops / code projects / campaigns) ARE included
    but tagged with their ``origin`` + ``source_id``/``source_label`` so the UI can
    default-hide them behind a filter and link each back to its cockpit.
    """
    state: DashboardState = request.app["state"]
    out: list[dict] = []
    seen: set[str] = set()
    # In-memory first — these are live and authoritative.
    for s in state._sessions.values():
        # Restricted (incognito/temporary) sessions never surface in the list —
        # the disk-merge branch below filters them; live ones must match.
        if getattr(s, "memory_mode", "persistent") in ("incognito", "temporary"):
            seen.add(s.key)
            continue
        d = s.to_dict()
        # A channel-linked session keeps its channel origin even once resumed live,
        # so it stays grouped under the Channel scope rather than folding into
        # 'manual'.
        link_thread = link_channel = None
        try:
            link_thread, link_channel = state.sessions.get_channel_link(s.key)
        except Exception:
            link_thread = link_channel = None
        if link_thread:
            origin, sid = "channel", (link_channel or "")
        else:
            origin, sid = _origin_of(s.key, getattr(s, "_app", "") or "")
        d["origin"] = origin
        if origin != "manual":
            d["source_id"] = sid
            d["source_label"] = _origin_label(origin, sid)
        out.append(d)
        seen.add(s.key)

    # Then merge disk-only sessions not already represented in memory.
    if state.conversation_log:
        try:
            disk = state.conversation_log.list_sessions()
        except Exception:
            logger.warning("list_sessions failed for chat history merge", exc_info=True)
            disk = []
        for d in disk:
            raw_key = d.get("key", "")
            if raw_key.startswith("dashboard:"):
                name = raw_key.removeprefix("dashboard:")
            elif raw_key.startswith("dashboard_"):
                name = raw_key.removeprefix("dashboard_")
            else:
                name = raw_key  # bare key (e.g. a channel thread_ts)
            # A channel conversation is keyed by its thread_ts + linked in the
            # session map. It reaches disk under either the bare key or (once
            # resumed) the dashboard_ namespace, so check the link on the RESOLVED
            # name regardless of prefix — surface + tag it origin=channel. Other
            # bare/non-dashboard namespaces (internal workers) are still skipped
            # below.
            try:
                link_thread, link_channel = state.sessions.get_channel_link(name)
            except Exception:
                link_thread = link_channel = None
            if not link_thread and raw_key == name and not raw_key.startswith(("dashboard:", "dashboard_")):
                continue  # non-dashboard, non-channel (worker namespace) — not chat history
            if name in seen:
                continue
            meta = state.conversation_log.get_metadata(raw_key)
            if meta.get("closed"):
                continue
            # Incognito/temporary histories are never surfaced in the list.
            if meta.get("memory_mode") in ("incognito", "temporary"):
                continue
            seen.add(name)
            if link_thread:
                origin, sid = "channel", (link_channel or "")
            else:
                origin, sid = _origin_of(name, meta.get("app", "") or "")
            row = {
                "key": name,
                "title": d.get("title") or name,
                "agent": meta.get("agent", d.get("agent", "")),
                "model": meta.get("model", ""),
                "messages": d.get("messages", 0),
                "running": False,
                "created": meta.get("created_at") or d.get("created", ""),
                "last_ts": "",
                "last_activity_ts": datetime.fromtimestamp(
                    d["modified"], tz=timezone.utc
                ).isoformat() if d.get("modified") else "",
                "folder_id": meta.get("folder_id", ""),
                "pinned": bool(meta.get("pinned")),
                "tags": [t for t in meta.get("tags", []) if isinstance(t, str)],
                "color_index": meta.get("color_index"),
                "memory_mode": meta.get("memory_mode", "persistent"),
                "origin": origin,
            }
            if origin != "manual":
                row["source_id"] = sid
                row["source_label"] = _origin_label(origin, sid)
            out.append(row)

    return web.json_response(out)


async def api_chat_tool_result(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/tool-result/{rid} — the FULL raw output of
    a projected tool result (tool-output-projection raw store), for the chat
    card's "Show full result" affordance. Optional ?grep= / ?start= / ?end= to
    pull a slice. Read-only; redacted the same way the live output was."""
    from personalclaw.security import redact_credentials, redact_exfiltration_urls
    from personalclaw.tool_providers import result_store

    # The raw store is keyed by the canonical (dashboard:-prefixed) session key —
    # the same key the projection write path uses during a turn (chat_runner sets
    # session_key = _history_key_for(session.key)). The URL carries the bare id, so
    # canonicalize here or the lookup misses (a stored result would 404 as "expired").
    name = _history_key_for(request.match_info["session"])
    rid = request.match_info["rid"]
    grep = request.query.get("grep") or None
    try:
        start = int(request.query.get("start") or 0)
    except ValueError:
        start = 0
    end_raw = request.query.get("end")
    end = int(end_raw) if (end_raw and end_raw.isdigit()) else None
    res = result_store.fetch_slice(name, rid, start=start, end=end, grep=grep, max_chars=200_000)
    if not res.get("ok"):
        return web.json_response({"error": res.get("error", "not found")}, status=404)
    content, _ = redact_exfiltration_urls(res.get("content", ""))
    content, _ = redact_credentials(content)
    res["content"] = content
    return web.json_response(res)


async def api_chat_session_detail(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session} — message history for a session.

    Two query modes:
      - Default (no ``limit`` and no ``before``): return the full chained history
        from disk across gateway restarts.
      - Paginated (``limit`` and/or ``before``): ``limit`` caps the number of
        messages returned; ``before`` returns messages before that index.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    # Cache miss → rehydrate from persisted history. After a gateway restart
    # (or with restore_sessions=false) older sessions aren't in memory, but the
    # chat history list still surfaces them — opening one must load it from disk
    # rather than 404. Returns None only if the session was never persisted.
    if not session:
        session = _rehydrate_session_from_history(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    # Canonical persisted key — a channel-provider thread keeps its own bare key;
    # a dashboard session uses the dashboard: namespace. Resolve provider-agnostically
    # (falls back to the dashboard form for a live session with no disk history yet).
    resolved_key = resolve_history_key(state.conversation_log, session.key) or _history_key_for(session.key)

    limit_raw = request.query.get("limit")
    before = request.query.get("before")

    # No limit → load ALL messages (chained across gateway restarts).
    # In-memory session.messages is authoritative for the current session.
    # _disk_older_count gates whether to read disk AND provides the stable
    # slice boundary (set at restore/resume, never drifts with new messages).
    if limit_raw is None and before is None:
        mem_msgs = list(session.messages)
        if session._disk_older_count > 0 and state.conversation_log:
            history_key = resolved_key
            try:
                disk_msgs = state.conversation_log.read_messages_chained(history_key)
            except Exception:
                logger.warning(
                    "read_messages_chained failed for %s", history_key, exc_info=True
                )
                disk_msgs = []
            older = disk_msgs[: session._disk_older_count] if disk_msgs else []
            messages = older + mem_msgs
        else:
            messages = mem_msgs
        total = len(messages)
        has_more = False
    else:
        # Paginated path: always reads from chained disk history; no in-memory
        # offset math.
        limit = min(int(limit_raw or "200"), 500)
        history_key = resolved_key
        try:
            all_msgs = (
                state.conversation_log.read_messages_chained(history_key)
                if state.conversation_log
                else []
            )
        except Exception:
            logger.warning("read_messages_chained failed for %s", history_key, exc_info=True)
            all_msgs = []
        # Append any un-flushed in-memory tail messages beyond what's on disk.
        # Use _disk_older_count to isolate current-session disk count, since
        # chained disk includes older sessions that inflate disk_len.
        mem_len = len(session.messages)
        disk_len = len(all_msgs)
        current_session_disk = max(0, disk_len - session._disk_older_count)
        unflushed = mem_len - current_session_disk
        if unflushed > 0:
            all_msgs = list(all_msgs) + list(session.messages[-unflushed:])
        total = len(all_msgs)
        if before is not None:
            end = max(0, min(int(before), total))
        else:
            end = total
        start = max(0, end - limit)
        messages = all_msgs[start:end]
        has_more = start > 0

    prepared = _prepare_messages(messages, session.running)

    return web.json_response(
        {
            "key": session.key,
            "title": session.title,
            "running": session.running,
            "stopping": session._stopping,
            "messages": prepared,
            "queue": [{"id": q["id"], "content": _redact_for_display(q["content"])} for q in session._queue],
            "total": total,
            "has_more": has_more,
            # agent/model binding so the composer restores the SAME selection the
            # session was using when reopened (native agent/model OR ACP provider
            # + provider_agent + reasoning effort).
            "agent": session.agent or "",
            "model": session.model or "",
            # session mode so the UI can show the right indicator when a session
            # is reopened.
            "mode": getattr(session, "mode", "") or "",
            "acp_provider": getattr(session, "acp_provider", "") or "",
            "acp_provider_agent": getattr(session, "acp_provider_agent", "") or "",
            "reasoning_effort": getattr(session, "reasoning_effort", "") or "",
            # Both composer axes so the segmented controls restore to the session's
            # ACTUAL posture on reopen (not the visual defaults). task_mode is
            # per-session; approval is derived from the yolo(global)/trust/
            # trust_reads precedence so the single enum the UI uses round-trips.
            "task_mode": getattr(session, "_task_mode", "agent") or "agent",
            "approval": (
                "yolo" if state.is_yolo_active()
                else "trust" if session._trust
                else "trust_reads" if session._trust_reads
                else "normal"
            ),
            # Memory mode so mode-gated affordances restore on reopen (e.g. the chat
            # page hides Fork on a non-persistent session — the backend refuses to
            # fork temporary/incognito). The session-list endpoint already returns
            # this; the detail endpoint must too, or a reopened chat looks persistent.
            "memory_mode": getattr(session, "memory_mode", "persistent") or "persistent",
            # True when the turn is parked on an unanswered tool approval. The chat
            # page's idle-reconciler uses this to recover a permission card whose
            # live `approval` WS frame was lost/early (the turn otherwise stalls
            # silently — no `chat_done` fires while awaiting the human).
            "pending_approval": any(not f.done() for f in session._approval_futures.values()),
            # persisted side-chat transcript (reloads attached to the session).
            "side": session._side.to_dict() if getattr(session, "_side", None) and session._side.messages else None,
        }
    )


async def api_chat_session_create(request: web.Request) -> web.Response:
    """POST /api/chat/sessions — create a new chat session."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        return web.json_response({"error": "name must be a string"}, status=400)
    agent = body.get("agent", "")
    model = body.get("model", "")
    project_id = str(body.get("project_id", "") or "")

    # Resolve the agent's default working directory from its bindings.
    workspace_dir = ""
    try:
        cfg = AppConfig.load()
        if agent and agent in cfg.agents:
            bindings = resolve_agent_bindings(cfg, agent)
            workspace_dir = str(bindings.workspace_dir)
    except Exception:
        logger.warning("Failed to resolve bindings for session create", exc_info=True)
    # A project-bound chat works in the project's bound workspace (so file tools +
    # memory scope to the project's codebase). The project's workspace wins over the
    # agent default; falls back to the agent default when the project has none.
    if project_id:
        try:
            from personalclaw.tasks.hierarchy import HierarchyStore
            proj = HierarchyStore().get_project(project_id)
            pdir = str(getattr(proj, "workspace_dir", "") or "") if proj else ""
            if pdir:
                workspace_dir = pdir
        except Exception:
            logger.debug("project workspace resolve failed for %s", project_id, exc_info=True)

    try:
        memory_mode = body.get("memory_mode", "persistent")
        if memory_mode not in ("persistent", "incognito", "temporary"):
            return web.json_response({"error": "invalid memory_mode"}, status=400)
        session = state.get_or_create_session(
            name, agent=agent, workspace_dir=workspace_dir, model=model, mode=body.get("mode", ""),
            memory_mode=memory_mode,
            ephemeral=body.get("ephemeral"),
            app=request.get("app", ""),
            project_id=project_id,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    if session.is_restricted:
        logger.info("Session %s created with memory_mode=%s", session.key, session.memory_mode)
    # A project-bound chat must be able to READ the project's context dir even when its
    # cwd is the project's bound workspace (the context dir lives outside it, under
    # config/projects/<id>/context). Grant it as an extra native-tool root so the chat
    # can actually read the sibling loops'/chats' shared outcomes the preamble points it
    # at — the "mutually accessible per-project context" the vision promises. Mirrors the
    # loop worker (manager.py grants the same dir). Best-effort.
    if project_id:
        try:
            from personalclaw.tasks.hierarchy import HierarchyStore
            ctx = str(HierarchyStore().context_dir(project_id))
            if ctx and ctx not in (session._extra_tool_roots or []):
                session._extra_tool_roots = [*(session._extra_tool_roots or []), ctx]
        except Exception:
            logger.debug("project context-dir tool-root grant failed for %s", project_id, exc_info=True)
    # Default the working directory to the workspace root so file search works
    # out of the box.
    if not session.workspace_dir:
        session.workspace_dir = default_workspace_dir()
    _sync_dashboard_sessions(state)
    return web.json_response(session.to_dict())


def _resolve_stop_event(session: _ChatSession, outcome: str) -> None:
    """Update the in-flight stop_event message in place with final state."""
    stop_id = session._stop_event_id
    logger.debug("_resolve_stop_event: outcome=%s stop_id=%r", outcome, stop_id)
    if not stop_id:
        return
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    final_state = "stopped" if outcome == "soft" else "stop_failed_reset"
    found = False
    for msg in reversed(session.messages):
        cls_val = msg.get("cls", "")
        if not cls_val:
            continue
        try:
            cls_data = json.loads(cls_val) if isinstance(cls_val, str) else None
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(cls_data, dict) or cls_data.get("kind") != "stop_event":
            continue
        if cls_data.get("id") != stop_id:
            continue
        cls_data["state"] = final_state
        cls_data["outcome"] = outcome
        cls_data["ts_end"] = now_ts
        serialized = json.dumps(cls_data)
        msg["cls"] = serialized
        msg["content"] = serialized
        session._dirty = True
        found = True
        # Re-broadcast updated stop_event so frontend StopEventCard
        # transitions from "stopping" → "stopped"/"stop_failed_reset".
        on_msg = getattr(session, "_on_message", None)
        if on_msg:
            try:
                on_msg(session.key, msg)
            except Exception:
                logger.debug("stop_event re-broadcast failed", exc_info=True)
        break
    if not found:
        logger.debug("_resolve_stop_event: no matching message for stop_id=%s", stop_id)
    session._stop_event_id = None


async def api_chat_session_stop(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/stop — cooperative stop with kill fallback.

    First press: soft cancel (cooperative). Second press (?force=true):
    hard kill. Inserts a stop_event message into the session transcript.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    force = request.query.get("force", "").lower() == "true"

    # Force path: already soft_pending, user pressed again
    if session._stop_state == "soft_pending" and force:
        session._stop_state = "killing"
        state.push_sessions_update()
        logger.info("Stop (force): hard-killing session for session %s", name)

        async def _on_hard_force() -> None:
            if session._stop_state != "killing":
                return
            _resolve_stop_event(session, "hard")
            session._stop_state = "idle"
            state.push_sessions_update()

        await state.sessions.stop_turn(
            _history_key_for(name), force=True, on_hard=_on_hard_force
        )
        sel().log_tool_invocation(
            session_key=_history_key_for(name),
            agent=getattr(session, "agent", "") or "personalclaw",
            source="dashboard",
            tool_name="dashboard_stop",
            tool_kind="command",
            outcome="hard",
            metadata={"session": name, "force": True},
        )
        return web.json_response({"ok": True})

    # Already stopping or not running — no-op
    if session._stop_state != "idle" or not session.running:
        if not session.running:
            logger.info("Stop: session %s not running, ignoring", name)
        return web.json_response({"ok": True})

    # First press: soft stop
    session._stop_state = "soft_pending"
    session._queue.clear()
    _was_auto = session._auto_run
    session._auto_run = False
    if _was_auto:
        sel().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex,
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="auto_run_stopped",
                caller_identity=f"dashboard:{session.key}",
                agent=getattr(session, "agent", ""),
                source="dashboard",
                operation="stop",
                outcome="stopped",
                resources=f"session={session.key}",
            )
        )

    # Insert stop_event message into transcript
    stop_id = f"stop-{uuid.uuid4().hex}"
    session._stop_event_id = stop_id
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    stop_data = {
        "kind": "stop_event",
        "id": stop_id,
        "state": "stopping",
        "outcome": None,
        "ts_start": now_ts,
    }
    # cls must be JSON-encoded so parse_cls_meta() populates meta on the wire.
    # content mirrors the same payload so consumers that read only content
    # still see the stop event.
    stop_msg = json.dumps(stop_data)
    session.append("system", stop_msg, stop_msg)
    state.push_sessions_update()
    logger.info(
        "Stop: cooperative cancel for session %s (queue=%d)", name, len(session._queue)
    )

    async def _on_soft() -> None:
        logger.debug("_on_soft called: stop_state=%r stop_event_id=%r", session._stop_state, session._stop_event_id)
        if session._stop_state != "soft_pending":
            logger.debug("_on_soft: state not soft_pending, bail")
            return
        _resolve_stop_event(session, "soft")
        session._stop_state = "idle"
        state.push_sessions_update()

    async def _on_hard() -> None:
        logger.debug("_on_hard called: stop_state=%r", session._stop_state)
        if session._stop_state not in ("soft_pending", "killing"):
            logger.debug("_on_hard: state not soft_pending/killing, bail")
            return
        _resolve_stop_event(session, "hard")
        session._stop_state = "idle"
        state.push_sessions_update()

    outcome = await state.sessions.stop_turn(
        _history_key_for(name), force=False, on_soft=_on_soft, on_hard=_on_hard
    )
    sel().log_tool_invocation(
        session_key=_history_key_for(name),
        agent=getattr(session, "agent", "") or "personalclaw",
        source="dashboard",
        tool_name="dashboard_stop",
        tool_kind="command",
        outcome=outcome,
        metadata={"session": name, "force": False},
    )
    return web.json_response({"ok": True})


async def api_chat_session_interrupt(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/interrupt — stop the turn, KEEP the queue.

    Unlike /stop (which clears the queue), /interrupt soft-cancels the current
    turn and preserves the queue so the _run_chat finally-block dequeue picks up
    the next queued message immediately. Optional body ``{"queue_id": ...}``
    promotes a specific queued message to the front first.

    Preconditions: the session must be running (else ``{ok, info}``) and the
    queue must be non-empty (else 400 — with nothing queued, /interrupt is just
    /stop, so the two verbs stay distinct).
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    if not session.running:
        return web.json_response({"ok": True, "info": "not running"})
    if not session._queue:
        return web.json_response({"error": "queue empty, use /stop instead"}, status=400)
    if session._stop_state != "idle":
        return web.json_response({"ok": True, "info": "already stopping"})

    body = {}
    if request.body_exists:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
    queue_id = body.get("queue_id")
    if queue_id:
        if not session.queue_promote(str(queue_id)):
            return web.json_response({"error": "queue_id not found"}, status=404)

    session._stop_state = "soft_pending"
    session._auto_run = False

    stop_id = f"stop-{uuid.uuid4().hex}"
    session._stop_event_id = stop_id
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    stop_data = {
        "kind": "stop_event",
        "id": stop_id,
        "state": "interrupting",
        "outcome": None,
        "ts_start": now_ts,
    }
    stop_msg = json.dumps(stop_data)
    session.append("system", stop_msg, stop_msg)
    state.push_sessions_update()
    logger.info("Interrupt: cooperative cancel for session %s (queue=%d preserved)", name, len(session._queue))

    async def _on_soft() -> None:
        if session._stop_state != "soft_pending":
            return
        _resolve_stop_event(session, "soft")
        session._stop_state = "idle"
        state.push_sessions_update()

    async def _on_hard() -> None:
        if session._stop_state not in ("soft_pending", "killing"):
            return
        _resolve_stop_event(session, "hard")
        session._stop_state = "idle"
        state.push_sessions_update()

    outcome = await state.sessions.stop_turn(
        _history_key_for(name), force=False, preserve_queue=True, on_soft=_on_soft, on_hard=_on_hard
    )
    sel().log_tool_invocation(
        session_key=_history_key_for(name),
        agent=getattr(session, "agent", "") or "personalclaw",
        source="dashboard",
        tool_name="dashboard_interrupt",
        tool_kind="command",
        outcome=outcome,
        metadata={"session": name, "queue_len": len(session._queue)},
    )
    return web.json_response({"ok": True})


async def api_chat_session_queue_cancel(request: web.Request) -> web.Response:
    """DELETE /api/chat/sessions/{session}/queue/{queue_id} — cancel a queued message.

    Removes the message from the backend queue and broadcasts a
    ``queue_cancel`` WebSocket event so the frontend can move the
    text back to the input box.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    queue_id = request.match_info["queue_id"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    content = session.queue_remove_by_id(queue_id)
    if content is None:
        return web.json_response({"error": "queue item not found"}, status=404)
    _remove_queued_by_id(session.messages, queue_id)
    _redacted = _redact_for_display(content)
    state.broadcast_ws("queue_cancel", {"session": name, "queue_id": queue_id, "content": _redacted})
    state.push_sessions_update()
    sel().log_tool_invocation(
        session_key=f"dashboard:{name}", agent="personalclaw", source="dashboard",
        tool_name="queue_cancel", tool_kind="permission",
        outcome="allowed",
        metadata={"queue_id": queue_id, "session": name},
    )
    return web.json_response({"ok": True, "content": _redacted})


async def api_chat_session_delete(request: web.Request) -> web.Response:
    """DELETE /api/chat/sessions/{session} — stop and remove a UI session.

    Kills the per-tab ACP agent session and saves history.  The session
    will be recreated from the warm pool if the tab is resumed later.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    history_key = _history_key_for(name)
    # A chat is deletable if it's warm in memory OR only persisted on disk. After a
    # gateway restart only recent/pinned/foldered sessions are restored to memory, so
    # requiring an in-memory session here made "Delete" a silent 404 no-op for the
    # common "delete an old chat from history" flow — leaving its JSONL + tool_results
    # on disk AND letting it resurrect on reopen. So fall through to a disk purge when
    # the session isn't resident; only 404 if it exists in neither place.
    on_disk = False
    if not session and state.conversation_log:
        try:
            on_disk = bool(state.conversation_log.get_metadata(history_key)) or \
                state.conversation_log.has_log(history_key)
        except Exception:
            on_disk = False
    if not session and not on_disk:
        return web.json_response({"error": "not found"}, status=404)

    # App ownership check: app can only delete sessions it created.
    # Unscoped sessions (empty _app) cannot be deleted by app tokens.
    # Dashboard users (empty request_app) can delete anything. (Only enforceable
    # against a resident session's _app; a disk-only session predates any app scope.)
    request_app = request.get("app", "")
    if request_app and session is not None:
        if session._app != request_app:
            sel().log_api_access(
                caller=request_app, operation="session_delete", outcome="denied",
                source="app_isolation", resources=f"session={name}",
                error="app does not own this session",
            )
            return web.json_response({"error": "app does not own this session"}, status=403)
        if not session._app:
            sel().log_api_access(
                caller=request_app, operation="session_delete", outcome="denied",
                source="app_isolation", resources=f"session={name}",
                error="app cannot delete unscoped sessions",
            )
            return web.json_response({"error": "app cannot delete unscoped sessions"}, status=403)
    elif request_app and session is None:
        # An app token cannot hard-delete a disk-only (unscoped) session it can't prove it owns.
        return web.json_response({"error": "app cannot delete unscoped sessions"}, status=403)

    # Remove from dict before async operations
    state._sessions.pop(name, None)
    if session is not None and session.running and session.task is not None:
        session.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(session.task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    # HARD DELETE (product decision 2026-07-03): the explicit "Delete chat" button
    # must actually destroy the conversation, not soft-close it. Previously this
    # wrote the session to history with closed=True — which (a) left the raw
    # tool-result store (file contents / command output) on disk, and (b) let the
    # session RESURRECT if its URL was reopened (the rehydrate path clears `closed`).
    # So we purge every on-disk artifact instead. The soft-close/archive path lives
    # ONLY in /cleanup (api_chat_sessions_cleanup), which is unchanged. (history_key
    # was resolved at the top so the disk-only path could check existence.)
    # 1) the JSONL history file (keyed by the canonical history key).
    try:
        if state.conversation_log:
            state.conversation_log.delete_session(history_key)
    except Exception:
        logger.warning("hard-delete: history file removal failed for %s", name, exc_info=True)
    # 2) the per-session workspace dir(s) incl. the tool_results raw store. The store
    #    is keyed by the canonical (dashboard:-prefixed) session key during a turn,
    #    but the bare id is also used by some paths — purge both forms.
    try:
        from personalclaw.tool_providers import result_store
        for _sid in {history_key, name}:
            result_store.purge_session(_sid)
    except Exception:
        logger.warning("hard-delete: workspace purge failed for %s", name, exc_info=True)
    state._restricted_keys.discard(f"dashboard:{name}")
    # Kill the per-tab session to free resources.
    await state.sessions.remove(history_key)
    _sync_dashboard_sessions(state)
    state.push_sessions_update()
    state.push_refresh("history")
    return web.json_response({"ok": True})


async def api_chat_sessions_cleanup(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/cleanup — bulk-archive inactive sessions to history.

    Body: ``{"max_inactive_days": 3, "active_session": "chat-1-123"}``
    Skips the active session and pinned sessions.
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    max_days = 3
    try:
        max_days = max(1, int(body.get("max_inactive_days", 3)))
    except (ValueError, TypeError):
        pass
    active_session = body.get("active_session", "")
    dry_run = body.get("dry_run", False)
    request_app = request.get("app", "")
    cutoff = time.time() - max_days * 86400
    stale_keys: list[str] = []
    active_is_stale = False
    for name in list(state._sessions):
        session = state._sessions.get(name)
        if session is None or session.pinned:
            continue
        # App Kit ownership isolation: app callers can only archive
        # their own sessions. Dashboard users (empty request_app) pass
        # through and can archive anything.
        if request_app:
            if session._app != request_app:
                continue
        last_activity = 0.0
        if session.messages:
            for m in reversed(session.messages):
                ts = m.get("ts", "")
                if not ts:
                    continue
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    last_activity = dt.timestamp()
                except (ValueError, TypeError):
                    continue
                break
        if not last_activity:
            try:
                dt = datetime.fromisoformat(session.created_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                last_activity = dt.timestamp()
            except Exception:
                last_activity = 0.0
        if not last_activity:
            continue  # unknown activity — don't archive
        if last_activity >= cutoff:
            continue
        if name == active_session:
            active_is_stale = True
            continue
        stale_keys.append(name)
    # Dry-run: return the exact list without archiving
    if dry_run:
        sel().log_api_access(
            caller="dashboard", operation="chat.cleanup_dry_run",
            outcome="allowed", source="dashboard",
            resources=f"count={len(stale_keys)} threshold={max_days}d",
        )
        return web.json_response({"ok": True, "dry_run": True, "keys": stale_keys, "count": len(stale_keys), "active_is_stale": active_is_stale})
    archived: list[str] = []
    failed: list[str] = []
    _tasks_to_cancel: list[asyncio.Task] = []
    for name in stale_keys:
        removed = state._sessions.pop(name, None)
        if not removed:
            continue
        try:
            _save_session_to_history(state, removed, closed=True)
        except Exception:
            logger.error("Cleanup: failed to archive session %s", name, exc_info=True)
            state._sessions[name] = removed
            failed.append(name)
            continue
        else:
            state._restricted_keys.discard(f"dashboard:{name}")
        # Session cleanup is best-effort — history is already written
        try:
            await state.sessions.remove(_history_key_for(name))
        except Exception:
            logger.warning("Cleanup: session remove failed for %s", name, exc_info=True)
        archived.append(name)
        # Collect running tasks for concurrent cancellation after the loop
        if removed.running and removed.task is not None:
            removed.task.cancel()
            _tasks_to_cancel.append(removed.task)
    # Await all cancelled tasks concurrently with a single bounded timeout
    if _tasks_to_cancel:
        await asyncio.wait(_tasks_to_cancel, timeout=5.0)
    if archived:
        _sync_dashboard_sessions(state)
        state.push_sessions_update()
        state.push_refresh("history")
    sel().log_api_access(
        caller="dashboard",
        operation="chat.sessions_cleanup",
        outcome="ok" if not failed else ("partial" if archived else "error"),
        source="dashboard",
        resources=f"archived={len(archived)} failed={len(failed)} threshold={max_days}d keys={','.join(archived[:10])}",
    )
    return web.json_response({"ok": True, "archived": len(archived), "keys": archived, "failed": failed})


async def api_chat_session_agent(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/agent — set agent for a chat session."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    agent_name = body.get("agent", "")
    if agent_name and not _AGENT_NAME_RE.match(agent_name):
        return web.json_response({"error": "invalid agent name"}, status=400)
    session.agent = agent_name
    # Selecting a saved/native agent clears any ephemeral discovered-ACP override
    # so the new selection isn't shadowed by a stale runtime binding.
    session.acp_provider = ""
    session.acp_provider_agent = ""

    # Resolve the new agent's default working directory from its bindings.
    try:
        cfg = AppConfig.load()
        # Look up by config key or by provider_agent name
        matched = agent_name if agent_name in cfg.agents else None
        if agent_name and not matched:
            for k, v in cfg.agents.items():
                if v.provider_agent == agent_name:
                    matched = k
                    break
        if matched:
            bindings = resolve_agent_bindings(cfg, matched)
            session.workspace_dir = str(bindings.workspace_dir)
    except Exception:
        logger.warning("Failed to resolve agent bindings for %r", agent_name, exc_info=True)

    # Reset session so next message uses the new agent
    logger.info("Session %s agent switched to %r, resetting session", name, agent_name or "personalclaw")
    await state.sessions.reset(_history_key_for(name))
    # Persist the new agent so the session resumes under the correct agent
    # after a gateway restart.  Written after reset succeeds so we never
    # advertise an agent we couldn't actually switch to.
    if state.conversation_log:
        try:
            state.conversation_log.update_metadata(
                _history_key_for(name),
                {"agent": agent_name, "acp_provider": "", "acp_provider_agent": ""},
            )
        except Exception:
            logger.warning(
                "Failed to persist agent for session %s", name, exc_info=True
            )
    state.push_sessions_update()
    return web.json_response(
        {"ok": True, "agent": agent_name, "workspace_dir": session.workspace_dir}
    )


async def api_chat_session_acp_agent(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/acp-agent — bind a DISCOVERED ACP agent.

    Body: ``{provider, provider_agent?, model?, reasoning_effort?}`` where
    ``provider`` is the runtime id (``acp:<cli>``), ``provider_agent`` the ACP
    modeId (persona-style agent; omit/empty for claude), ``model`` an optional override
    from the runtime's model list, and ``reasoning_effort`` the pinned effort
    (claude effort-agents). These are EPHEMERAL session overrides — nothing is
    written to config (discovered catalogs are account-dynamic). They win over the
    named-definition resolution in chat_runner. Passing an empty ``provider``
    clears the override (revert to the saved/default agent)."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    provider = str(body.get("provider", "") or "").strip()
    # Only acp:<cli> runtimes are valid here ("" clears the override).
    if provider and not provider.startswith("acp:"):
        return web.json_response({"error": "provider must be an acp:<cli> runtime id"}, status=400)
    provider_agent = str(body.get("provider_agent", "") or "").strip()
    if provider_agent and not _AGENT_NAME_RE.match(provider_agent):
        return web.json_response({"error": "invalid provider_agent"}, status=400)
    effort = str(body.get("reasoning_effort", "") or "").strip()
    if effort and effort not in ("low", "medium", "high", "max"):
        return web.json_response({"error": "invalid reasoning_effort"}, status=400)

    session.acp_provider = provider
    session.acp_provider_agent = provider_agent if provider else ""
    if "model" in body:
        session.model = str(body.get("model", "") or "")
    session.reasoning_effort = effort

    logger.info(
        "Session %s ACP override → provider=%r agent=%r model=%r effort=%r",
        name, provider, provider_agent, session.model, effort,
    )
    await state.sessions.reset(_history_key_for(name))
    # Persist so the ephemeral binding survives a gateway restart for THIS
    # session (still never written to the global agents config).
    if state.conversation_log:
        try:
            state.conversation_log.update_metadata(
                _history_key_for(name),
                {
                    "acp_provider": provider,
                    "acp_provider_agent": session.acp_provider_agent,
                    "reasoning_effort": effort,
                    "model": session.model,
                },
            )
        except Exception:
            logger.warning("Failed to persist ACP override for session %s", name, exc_info=True)
    state.push_sessions_update()
    return web.json_response(
        {"ok": True, "provider": provider, "provider_agent": session.acp_provider_agent,
         "model": session.model, "reasoning_effort": effort}
    )


async def api_chat_session_model(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/model — set model for a chat session."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    model_name = _normalize_model(body.get("model", ""))
    if session.model == model_name:
        return web.json_response({"ok": True, "model": model_name})
    session.model = model_name
    logger.info("Session %s model switched to %r, resetting session", name, model_name or "auto")
    await state.sessions.reset(_history_key_for(name))
    state.push_sessions_update()
    return web.json_response({"ok": True, "model": model_name})


async def api_chat_session_reasoning_effort(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/reasoning-effort — set reasoning effort.

    Body: {"reasoning_effort": "" | "low" | "medium" | "high" | "max"}.
    "" = provider default (e.g. CC falls back to its opus heuristic).

    Currently consumed by Claude Code only; ACP/OpenCode wired later via
    the same `reasoning_effort_override` factory kwarg seam.
    """
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_effort = body.get("reasoning_effort", "")
    if not isinstance(raw_effort, str):
        return web.json_response({"error": "reasoning_effort must be a string"}, status=400)
    # No fixed scale — each backend declares its own effort values. Enforce a safe
    # FORMAT (short lowercase-alnum token) so any real backend value is accepted
    # while blocking injection into the subprocess arg / config value. "" clears.
    effort = _validate_reasoning_effort(raw_effort)
    if raw_effort and not effort:
        return web.json_response(
            {"error": "reasoning_effort must be a short lowercase token (a-z0-9_-) or ''"},
            status=400,
        )
    if session.reasoning_effort == effort:
        return web.json_response({"ok": True, "reasoning_effort": effort})
    session.reasoning_effort = effort
    logger.info(
        "Session %s reasoning_effort switched to %r, resetting session",
        name,
        effort or "default",
    )
    # Reset so the next message spawns a fresh subprocess with the new --effort
    # flag. Same UX as model switch.
    await state.sessions.reset(_history_key_for(name))
    state.push_sessions_update()
    return web.json_response({"ok": True, "reasoning_effort": effort})


async def api_chat_session_workspace_dir(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/workspace-dir — set the working directory.

    The working directory is the session's workspace: it is the agent's cwd and
    scopes the session's memory partition.
    """
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    workspace_dir = body.get("workspace_dir", "")
    if not isinstance(workspace_dir, str):
        return web.json_response({"error": "workspace_dir must be a string"}, status=400)
    workspace_dir = workspace_dir.strip()
    if workspace_dir:
        workspace_dir = os.path.realpath(os.path.expanduser(workspace_dir))
        if not os.path.isdir(workspace_dir):
            return web.json_response({"error": "Not a directory"}, status=400)
        if is_sensitive_path(workspace_dir):
            sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="chat_session_workspace_dir",
                outcome="denied",
                resources=f"session={name} workspace_dir={workspace_dir}",
                error="sensitive path"
            )
            return web.json_response({"error": "Access denied"}, status=403)
    session.workspace_dir = workspace_dir
    logger.info("Session %s workspace_dir set to %r", name, workspace_dir)
    sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="chat_session_workspace_dir",
        outcome="allowed",
        resources=f"session={name} workspace_dir={workspace_dir}",
    )
    # Track recent working directories
    if workspace_dir:
        try:
            await asyncio.to_thread(_save_recent_project, workspace_dir)
        except Exception:
            logger.warning("Failed to save recent workspace dir", exc_info=True)
    state.push_sessions_update()
    return web.json_response({"ok": True, "workspace_dir": workspace_dir})


_MAX_RECENT_PROJECTS = 10


def _recent_projects_path() -> Path:
    return config_dir() / "recent_projects.json"


def _save_recent_project(path: str) -> None:
    """Prepend path to recent projects list (deduped, capped)."""

    fp = _recent_projects_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else []
    except (json.JSONDecodeError, OSError):
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing = [p for p in existing if p != path]
    existing.insert(0, path)
    existing = existing[:_MAX_RECENT_PROJECTS]
    atomic_write(fp, json.dumps(existing))


async def api_recent_projects(request: web.Request) -> web.Response:
    """GET /api/recent-projects — list recently used project directories."""

    def _read_recent_projects() -> list[str]:
        fp = _recent_projects_path()
        try:
            dirs = json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else []
        except Exception:
            dirs = []
        if not isinstance(dirs, list):
            dirs = []
        return [
            d for d in dirs
            if isinstance(d, str) and os.path.isdir(d) and not is_sensitive_path(d)
        ]

    dirs = await asyncio.to_thread(_read_recent_projects)
    sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="recent_projects",
        outcome="allowed",
        resources=f"count={len(dirs)}",
    )
    return web.json_response({"dirs": dirs})


async def api_chat_session_resume(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/resume — load a history session into a session."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    if name.startswith("dashboard_"):
        name = name.removeprefix("dashboard_")
    if not state.conversation_log:
        return web.json_response({"error": "no conversation log"}, status=400)
    try:
        body = await request.json()
    except Exception:
        body = {}
    history_key = body.get("key", name)

    # If session already exists (active session), just return it — no duplicate.
    # Check both by session name AND by canonical session key to prevent two
    # sessions sharing the same ACP agent process.
    canonical = _history_key_for(history_key)
    existing = state._sessions.get(name)
    if not existing:
        for session in state._sessions.values():
            if _history_key_for(session.key) == canonical:
                existing = session
                break
    if existing:
        # App ownership check: an app may only act on sessions it owns.
        request_app = request.get("app", "")
        if request_app:
            if not existing._app:
                sel().log_api_access(
                    caller=request_app, operation="session_resume", outcome="denied",
                    source="app_isolation", resources=f"session={existing.key}",
                    error="app cannot access unscoped sessions",
                )
                return web.json_response({"error": "app cannot access unscoped sessions"}, status=403)
            elif request_app != existing._app:
                sel().log_api_access(
                    caller=request_app, operation="session_resume", outcome="denied",
                    source="app_isolation", resources=f"session={existing.key}",
                    error="app does not own this session",
                )
                return web.json_response({"error": "app does not own this session"}, status=403)
        total = len(existing.messages)
        recent = existing.messages[-200:] if total > 200 else existing.messages
        prepared = _prepare_messages(recent, existing.running)
        return web.json_response(
            {
                "ok": True,
                "key": existing.key,
                "messages": prepared,
                "queue": [{"id": q["id"], "content": _redact_for_display(q["content"])} for q in existing._queue],
                "total": total,
                "has_more": total > 200,
                "memory_mode": existing.memory_mode,
            }
        )

    session = state.get_or_create_session(name, app=request.get("app", ""))
    title = body.get("title", "")
    if title:
        session.title = title
        session._titled = True
    else:
        sessions = state.conversation_log.list_sessions()
        for s in sessions:
            if s.get("key") == history_key:
                session.title = s.get("title", history_key)
                session._titled = True
                break
    # Restore original created_at from history metadata
    meta = state.conversation_log.get_metadata(history_key)
    if meta.get("created_at"):
        session.created_at = meta["created_at"]
    if meta.get("agent"):
        session.agent = meta["agent"]
    if meta.get("workspace_dir"):
        session.workspace_dir = meta["workspace_dir"]
    if meta.get("mode"):
        session.mode = meta["mode"]
    if meta.get("folder_id"):
        session.folder_id = meta["folder_id"]
    if meta.get("pinned"):
        session.pinned = True
    if meta.get("color_index") is not None:
        session.color_index = meta["color_index"]
    if meta.get("color_theme"):
        session.color_theme = meta["color_theme"]
    mm = meta.get("memory_mode", "persistent")
    session.memory_mode = mm
    if mm != "persistent":
        state._restricted_keys.add(f"dashboard:{name}")
    else:
        state._restricted_keys.discard(f"dashboard:{name}")
    if meta.get("forked_from") is not None:
        session.forked_from = meta["forked_from"]
    # Clear closed flag so session restores on next gateway restart
    if meta.get("closed"):
        try:
            path = state.conversation_log._path(history_key)
            if path.exists():
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                if lines:
                    first_line_data = json.loads(lines[0])
                    first_line_data.pop("closed", None)
                    lines[0] = json.dumps(first_line_data) + "\n"
                    atomic_write(path, "".join(lines))
                    state.conversation_log._meta_cache.pop(history_key, None)
        except Exception:
            logger.warning("Failed to clear closed flag for %s", history_key, exc_info=True)
    all_messages = state.conversation_log.read_messages_chained(history_key)
    disk_total = len(all_messages)
    max_resume = 500
    messages = all_messages[-max_resume:] if disk_total > max_resume else all_messages
    # Stable count of messages older than what we loaded into memory
    session._disk_older_count = max(0, disk_total - len(messages))
    for m in messages:
        role = m.get("role", "assistant")
        cls = "msg msg-u" if role == "user" else "msg msg-a"
        content = m.get("content", "")
        if role != "user":
            content, _ = redact_exfiltration_urls(content)
            content, _ = redact_credentials(content)
        session.append(role, content, cls, ts=m.get("ts", ""))
        _attach_variants(session, m)
    session.drain()
    session._resumed_count = len(session.messages)
    total = disk_total
    recent = session.messages[-200:] if len(session.messages) > 200 else session.messages
    _sync_dashboard_sessions(state)
    state.push_sessions_update()
    return web.json_response(
        {"ok": True, "key": session.key, "messages": _prepare_messages(recent, session.running), "queue": [{"id": q["id"], "content": _redact_for_display(q["content"])} for q in session._queue], "total": total, "has_more": total > len(recent), "memory_mode": session.memory_mode}
    )


async def api_chat_mode(request: web.Request) -> web.Response:
    """POST /api/chat/mode — set the tool APPROVAL mode (whether tools auto-approve).

    Modes:
      - ``normal``: reset to interactive (ask for each tool)
      - ``trust_reads``: auto-approve read-only tools
      - ``trust``: auto-approve tools for active session
      - ``yolo``: auto-approve all tools everywhere

    Orthogonal to the TASK mode (agent/ask/plan/build — see ``/api/chat/task-mode``),
    which gates *which* tools are available + how the agent frames the work. Unlike
    the per-tool approve endpoint, this doesn't require a pending approval — it
    preemptively sets the mode for future tools.
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    mode = body.get("mode", "normal")
    session_name = body.get("session") or None

    if mode == "yolo":
        state.enable_yolo()  # TTL enforced internally (state._YOLO_TTL)
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:yolo",
                outcome="enabled",
                resources=",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning("SEL audit failed for YOLO mode activation", exc_info=True)
    elif mode == "trust_reads":
        state.disable_yolo()
        if session_name and session_name in state._sessions:
            state._sessions[session_name]._trust = False
            state._sessions[session_name]._trust_reads = True
        else:
            for session in state._sessions.values():
                session._trust = False
                session._trust_reads = True
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:trust_reads",
                outcome="enabled",
                resources=session_name or ",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning("SEL audit failed for trust_reads mode activation", exc_info=True)
    elif mode == "trust":
        state.disable_yolo()
        if session_name is not None:
            if session_name not in state._sessions:
                return web.json_response({"ok": False, "error": "unknown session"}, status=400)
            state._sessions[session_name]._trust = True
        else:
            for session in state._sessions.values():
                session._trust = True
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:trust",
                outcome="enabled",
                resources=session_name or ",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning("SEL audit failed for trust mode activation", exc_info=True)
    else:  # normal
        state.disable_yolo()
        if session_name is not None:
            if session_name not in state._sessions:
                return web.json_response({"ok": False, "error": "unknown session"}, status=400)
            state._sessions[session_name]._trust = False
            state._sessions[session_name]._trust_reads = False
        else:
            for session in state._sessions.values():
                session._trust = False
                session._trust_reads = False
        try:
            sel().log_api_access(
                caller="dashboard:mode",
                operation="mode_change:normal",
                outcome="disabled",
                resources=session_name or ",".join(s.key for s in state._sessions.values()),
            )
        except Exception:
            logger.warning("SEL audit failed for normal mode activation", exc_info=True)

    # YOLO is unified process-global trust state (personalclaw.trust_mode):
    # state.enable_yolo()/disable_yolo() above already drive the single source of
    # truth that the channel handler also reads — no separate sync needed.

    # If any session has a pending approval and mode is trust/yolo, auto-approve it
    if mode in ("trust", "yolo"):
        for session in state._sessions.values():
            for aid, fut in list(session._approval_futures.items()):
                if not fut.done():
                    fut.set_result("approved")
                    # Persist resolved state into the permission message
                    _mark_permission_resolved(session.messages, aid, mode)
                    state.broadcast_ws("approval_resolved", {"id": aid, "approved": True})
                    try:
                        sel().log_api_access(
                            caller=f"dashboard:{session.key}",
                            operation=f"tool_approval:bulk_{mode}",
                            outcome="approved",
                            resources=aid,
                        )
                    except Exception:
                        logger.warning("SEL audit failed for bulk approval %s", aid, exc_info=True)
        # Also auto-approve all pending background approvals (cron/subagent)
        for aid in list(state._approval_futures):
            fut = state._approval_futures[aid]
            if not fut.done():
                state.resolve_approval(aid, True)
                try:
                    sel().log_api_access(
                        caller="dashboard:background",
                        operation=f"tool_approval:bulk_{mode}",
                        outcome="approved",
                        resources=aid,
                    )
                except Exception:
                    logger.warning("SEL audit failed for bulk approval %s", aid, exc_info=True)
    # Propagate trust/yolo to session approval policies so subagents inherit.
    for session in state._sessions.values():
        policy = "auto" if session._trust or state.is_yolo_active() else ""
        state.sessions.set_approval_policy(f"dashboard:{session.key}", policy)

    state.push_sessions_update()
    return web.json_response({"ok": True, "mode": mode})


VALID_TASK_MODES = ("agent", "ask", "plan", "build")


async def api_chat_task_mode(request: web.Request) -> web.Response:
    """POST /api/chat/task-mode — set the per-session TASK mode.

    Task mode is ORTHOGONAL to the approval mode (``/api/chat/mode``): approval
    gates *whether* a tool auto-approves; task mode gates *which* tools are
    available + *how* the agent frames the work, layered on the active agent:
      - ``agent``: full execution (default)
      - ``ask``:   read-only — only SAFE/read tools run; mutating tools denied
      - ``plan``:  the agent plans; NO tool executes
      - ``build``: scoped to producing an artifact/widget/skill

    Body: ``{mode: <task mode>, session?: <key>}``. When ``session`` is omitted the
    mode applies to all sessions (mirrors the approval handler's broadcast shape).
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    mode = body.get("mode", "agent")
    if mode not in VALID_TASK_MODES:
        return web.json_response(
            {"error": f"invalid task mode (expected one of {VALID_TASK_MODES})"}, status=400
        )
    session_name = body.get("session") or None
    if session_name is not None and session_name not in state._sessions:
        return web.json_response({"ok": False, "error": "unknown session"}, status=400)

    targets = (
        [state._sessions[session_name]]
        if session_name is not None
        else list(state._sessions.values())
    )
    for session in targets:
        session._task_mode = mode
        # Push to the runtime so its tool gate enforces the mode regardless of
        # approval (native runtime gates in _guard_and_invoke, before approval —
        # so a Trust/YOLO auto-approve can't bypass an ask/plan/build restriction).
        state.sessions.set_task_mode(f"dashboard:{session.key}", mode)
    try:
        sel().log_api_access(
            caller="dashboard:task-mode",
            operation=f"task_mode_change:{mode}",
            outcome="enabled",
            resources=session_name or ",".join(s.key for s in state._sessions.values()),
        )
    except Exception:
        logger.warning("SEL audit failed for task-mode change", exc_info=True)

    state.push_sessions_update()
    return web.json_response({"ok": True, "task_mode": mode})


async def api_chat_session_approve(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/approve — resolve a pending tool approval."""
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
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    action = body.get("action", "rejected")
    original_action = action
    # Trust: auto-approve remaining tools for this session
    if action == "trust":
        session._trust = True
        state.sessions.set_approval_policy(f"dashboard:{name}", "auto")
        action = "approved"
    # Trust-agent ("Always allow for this agent"): trust THIS chat now (like trust)
    # AND persist the grant onto the bound agent's profile (approval_mode="auto") so
    # every future chat with that agent starts auto-approving — seeded at session-open
    # by chat_runner. One vocabulary, one gate: this just writes the persistent floor
    # the runtime already consumes. Skipped for the default/unnamed agent (no editable
    # profile) and reserved system agents (their config is fixed).
    elif action == "trust_agent":
        session._trust = True
        state.sessions.set_approval_policy(f"dashboard:{name}", "auto")
        action = "approved"
        from personalclaw.agents.defaults import is_reserved_agent
        try:
            cfg = AppConfig.load()
            # Resolve the grant target: an empty session.agent means the implicit
            # default agent — persist to config.default_agent's profile (that IS the
            # agent running this chat), not nowhere. Reserved system agents keep their
            # fixed config, so a grant on one degrades to session-scope only.
            agent_name = (session.agent or "").strip() or cfg.default_agent
            if agent_name and not is_reserved_agent(agent_name) and agent_name in cfg.agents:
                prof = cfg.agents[agent_name]
                if prof.approval_mode != "auto":
                    prof.approval_mode = "auto"
                    cfg.save()
                sel().log_api_access(
                    caller="dashboard:approval",
                    operation="mode_change:always_for_agent",
                    outcome="enabled",
                    resources=f"{name} agent={agent_name}",
                )
            else:
                logger.info("trust_agent on non-persistable agent %r — session-scope only", agent_name or "(none)")
        except Exception:
            logger.warning("Failed to persist always-for-agent grant", exc_info=True)
    # Trust-reads: auto-approve read-only bash commands for this session
    # Defer setting _trust_reads until after the approval future is consumed
    # to prevent the frontend from seeing trust_reads=true while still pending.
    elif action == "trust_reads":
        action = "approved_trust_reads"
    # YOLO: auto-approve all tools globally (all sessions)
    elif action == "yolo":
        state.enable_yolo()
        for s in state._sessions.values():
            state.sessions.set_approval_policy(f"dashboard:{s.key}", "auto")
        action = "approved"
    request_id = body.get("request_id", "")
    if not request_id:
        pending = [(k, f) for k, f in session._approval_futures.items() if not f.done()]
        if len(pending) == 1:
            request_id, fut = pending[0]
        else:
            fut = None
    else:
        fut = session._approval_futures.get(request_id)
    if not fut or fut.done():
        # Distinguish ambiguous (multiple pending) from truly empty
        if not request_id and session._approval_futures:
            pending_ids = [k for k, f in session._approval_futures.items() if not f.done()]
            if len(pending_ids) > 1:
                return web.json_response(
                    {
                        "error": "multiple approvals pending, specify request_id",
                        "pending": pending_ids,
                    },
                    status=400,
                )
        return web.json_response({"error": "no pending approval"}, status=404)
    resolved = action if action in ("approved", "approved_trust_reads") else "rejected"
    fut.set_result(resolved)
    # Persist resolved state into the permission message so it survives tab switches
    if request_id:
        _mark_permission_resolved(
            session.messages, request_id,
            original_action if original_action in ("trust", "trust_reads") else resolved,
        )
    # Broadcast first to ensure frontend is unblocked
    if request_id:
        state.broadcast_ws(
            "approval_resolved", {"id": request_id, "approved": resolved != "rejected"}
        )
    state.push_sessions_update()
    # SEL audit (best-effort — must not block the UI-unblocking path above)
    try:
        sel().log_api_access(
            caller=f"dashboard:{name}",
            operation=f"tool_approval:{original_action}",
            outcome=resolved,
            resources=request_id,
        )
    except Exception:
        logger.warning("SEL audit failed for approval %s", request_id, exc_info=True)
    return web.json_response({"ok": True})


MAX_COLOR_INDEX = 20


async def api_chat_session_color(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/color — set session color."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ci = body.get("color_index")
    if ci is not None and (
        isinstance(ci, bool) or not isinstance(ci, int) or ci < 0 or ci > MAX_COLOR_INDEX
    ):
        return web.json_response(
            {"error": f"color_index must be a non-negative integer <= {MAX_COLOR_INDEX} or null"},
            status=400,
        )
    session.color_index = ci
    session._dirty = True
    state.push_sessions_update()
    return web.json_response({"ok": True, "color_index": ci})


_MAX_CONTEXT_PER_SOURCE = 10


async def api_chat_session_context(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/context — inject silent background context.

    Adds a ContextEntry to the session's ``_pending_context`` queue.
    The content is consumed on the next user-initiated message via
    ``ctx_builder.build_message()`` and prepended to the LLM prompt.

    No LLM turn is triggered, no WS event is broadcast, and no visible
    message is appended to the session's chat history.

    Body::

        {
            "content": "...",
            "source": "watch-check",   // optional
            "ephemeral": true,         // optional, default true
            "maxAge": 300              // optional, seconds
        }
    """

    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "session not found"}, status=404)

    # App ownership check: deny-by-default for app tokens.
    # Apps can only access sessions they own. Dashboard users (empty request_app)
    # can access everything.
    request_app = request.get("app", "")
    if request_app:
        if not session._app:
            sel().log_api_access(
                caller=request_app,
                operation="context_inject",
                outcome="denied",
                source="app_isolation",
                resources=f"session={name}",
                error="app cannot access unscoped sessions",
            )
            return web.json_response({"error": "app cannot access unscoped sessions"}, status=403)
        elif request_app != session._app:
            sel().log_api_access(
                caller=request_app,
                operation="context_inject",
                outcome="denied",
                source="app_isolation",
                resources=f"session={name}",
                error="app does not own this session",
            )
            return web.json_response({"error": "app does not own this session"}, status=403)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    content = body.get("content", "")
    if not content:
        return web.json_response({"error": "content is required"}, status=400)

    # Content size limit (40,000 chars — same as message limit)
    max_context_content = 40000
    if len(content) > max_context_content:
        return web.json_response(
            {"error": f"content exceeds {max_context_content} char limit"}, status=400
        )

    entry: dict[str, object] = {
        "content": content,
        "source": body.get("source", ""),
        "ephemeral": body.get("ephemeral", True),
        "injectedAt": time.time(),
    }
    max_age = body.get("maxAge")
    if max_age is not None:
        entry["maxAge"] = max_age

    # Per-source cap: prevent one app from evicting all others' context
    source = body.get("source", "")
    if source:
        source_count = sum(1 for e in session._pending_context if e.get("source") == source)
        if source_count >= _MAX_CONTEXT_PER_SOURCE:
            return web.json_response(
                {"error": f"source {source!r} has {_MAX_CONTEXT_PER_SOURCE} pending entries"}, status=429
            )

    # FIFO eviction: cap pending queue at 50 entries
    max_pending_context = 50
    while len(session._pending_context) >= max_pending_context:
        session._pending_context.pop(0)

    session._pending_context.append(entry)  # type: ignore[arg-type]

    # SEL audit logging
    sel().log_api_access(
        caller=request_app or request.get("user", "dashboard"),
        operation="context_inject",
        outcome="ok",
        source="app_kit",
        resources=f"session={name}",
    )

    return web.json_response({"ok": True, "pending": len(session._pending_context)})


# ── Chat navigation: batched link summaries ──

# Caps so one request can't fan out into a huge prompt or echo unbounded text.
_NAV_MAX_LINKS = 30
_NAV_CONTEXT_CAP = 400
_NAV_SUMMARY_CAP = 80


def _build_nav_links_prompt(links: list[dict[str, str]]) -> str:
    """One prompt for the whole batch — concise human label per (index, url).

    The instruction lives in the prompt system (bundled ``task-nav-links``); we
    assemble the numbered link lines and render them into it."""
    link_lines = []
    for i, link in enumerate(links):
        ctx = link.get("context", "")
        ctx = f" — context: {ctx}" if ctx else ""
        link_lines.append(f"{i}: {link.get('url', '')}{ctx}")
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    return render_use_case_prompt("nav_links", {"numbered_links": "\n".join(link_lines)}) or ""


def _parse_nav_links_response(text: str, count: int) -> list[str]:
    """Parse ``<index>: <title>`` lines into a list aligned to the input order.

    Any index we can't parse (or that the model omitted) stays an empty string,
    so the response is always positionally aligned to the request.
    """
    summaries = [""] * count
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        idx_part, _, title = line.partition(":")
        try:
            idx = int(idx_part.strip())
        except ValueError:
            continue
        if 0 <= idx < count:
            clean, _ = redact_exfiltration_urls(title.strip())
            clean, _ = redact_credentials(clean)
            summaries[idx] = clean[:_NAV_SUMMARY_CAP]
    return summaries


async def api_nav_resolve_links(request: web.Request) -> web.Response:
    """POST /api/chat/nav/resolve-links — batch-summarize bare links.

    Request ``{"links": [{"url": str, "context": str}]}``; response
    ``{"summaries": [str]}`` positionally aligned to the input (empty string for
    any that fail). One stateless Model-entity call summarizes the whole batch
    (the explicit anti-N+1 goal) via the ``background`` use-case binding.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_links = body.get("links")
    if not isinstance(raw_links, list):
        return web.json_response({"error": "links must be a list"}, status=400)

    # Normalize + cap the batch defensively before it reaches the model.
    links: list[dict[str, str]] = []
    for item in raw_links[:_NAV_MAX_LINKS]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        links.append({"url": url, "context": str(item.get("context", "")).strip()[:_NAV_CONTEXT_CAP]})

    if not links:
        return web.json_response({"summaries": []})

    try:
        from personalclaw.llm_helpers import one_shot_completion

        text = await one_shot_completion(_build_nav_links_prompt(links), use_case="background")
    except Exception:
        logger.warning("nav link resolve failed", exc_info=True)
        # Soft-fail: the UI keeps its structured fallback labels.
        return web.json_response({"summaries": [""] * len(links)})

    summaries = _parse_nav_links_response(text, len(links))
    try:
        sel().log_api_access(
            caller="dashboard:nav",
            operation="resolve_links",
            outcome="ok",
            resources=f"count={len(links)}",
        )
    except Exception:
        logger.debug("SEL audit failed for nav resolve-links", exc_info=True)
    return web.json_response({"summaries": summaries})
