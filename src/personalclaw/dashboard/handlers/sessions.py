"""Session lifecycle, usage, search, approvals, and reset handlers."""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.llm.base import ModelProvider  # noqa: F811

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.history import SEARCH_MIN_CHARS
from personalclaw.mcp_discovery import (
    discover_servers_to_sync,
    register_servers_for_cc,
    sync_to_agent_config,
)
from personalclaw.validation import sanitize_string

logger = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT_SECS = 10


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811 — circular import

    return _pkg.sel()


async def api_sessions_context(request: web.Request) -> web.Response:
    """GET /api/sessions/context — context usage for all active sessions."""
    state: DashboardState = request.app["state"]
    return web.json_response({"sessions": state.sessions.context_info()})


_health_cache: dict[str, dict] = {}
_health_cache_ts: float = 0.0
_health_lock: asyncio.Lock | None = None
_HEALTH_REFRESH_SECS = 15


async def api_sessions_health(request: web.Request) -> web.Response:
    """GET /api/sessions/health — sessions flagged as stalled from log scan."""
    global _health_cache, _health_cache_ts, _health_lock
    if _health_lock is None:
        _health_lock = asyncio.Lock()
    now = time.monotonic()
    if now - _health_cache_ts > _HEALTH_REFRESH_SECS:
        async with _health_lock:
            # Re-check after acquiring lock (another request may have refreshed)
            if time.monotonic() - _health_cache_ts > _HEALTH_REFRESH_SECS:
                try:
                    from personalclaw.dashboard import session_health

                    _health_cache = await asyncio.to_thread(session_health.compute_session_health)
                    _health_cache_ts = time.monotonic()
                except Exception:
                    logger.warning("session_health scan failed", exc_info=True)
                    _health_cache_ts = time.monotonic()
    return web.json_response({"stalled": _health_cache})


async def api_sessions(request: web.Request) -> web.Response:
    """GET /api/sessions — list conversation session files.

    Query params:
      - ``limit``: max sessions to return (default 50, max 200)
      - ``offset``: skip first N sessions (default 0)

    Returns ``{sessions, total, has_more}`` for pagination.
    """
    state: DashboardState = request.app["state"]
    if not state.conversation_log:
        return web.json_response({"sessions": [], "total": 0, "has_more": False})
    try:
        limit = min(int(request.query.get("limit", "50")), 200)
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(request.query.get("offset", "0"))
    except (TypeError, ValueError):
        offset = 0
    all_sessions = [
        s
        for s in state.conversation_log.list_sessions()
        # Restricted (incognito/temporary) sessions stay out of every
        # discovery surface, not just the chat-history list.
        if s.get("memory_mode") not in ("incognito", "temporary")
    ]
    total = len(all_sessions)
    page = all_sessions[offset : offset + limit]
    return web.json_response(
        {
            "sessions": page,
            "total": total,
            "has_more": offset + limit < total,
        }
    )


async def api_sessions_search(request: web.Request) -> web.Response:
    """GET /api/sessions/search — content search over session JSONL files.

    Query params:
      - ``q``: search string (min 2 chars; empty returns no results)
      - ``limit``: max results (default 50, max 200)

    Returns ``{sessions}`` — same metadata shape as:func:`api_sessions`.
    Session titles may be LLM-generated and are redacted before return.
    """
    state: DashboardState = request.app["state"]
    if not state.conversation_log:
        return web.json_response({"sessions": []})
    q = sanitize_string(request.query.get("q", "")).strip()
    if len(q) < SEARCH_MIN_CHARS:
        return web.json_response({"sessions": []})
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
    except (TypeError, ValueError):
        limit = 50
    sessions = await asyncio.get_running_loop().run_in_executor(
        None, state.conversation_log.search_sessions, q, limit
    )
    for s in sessions:
        title = s.get("title")
        if title:
            import personalclaw.dashboard.handlers as _h  # noqa: F811
            title, _ = _h.redact_exfiltration_urls(title)
            title, _ = _h.redact_credentials(title)
            s["title"] = title
    return web.json_response({"sessions": sessions})


def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd
        return _cd()
    except Exception:
        from pathlib import Path as _P
        return _P.home() / ".personalclaw"


async def api_session_detail(request: web.Request) -> web.Response:
    """GET /api/sessions/{key} — return messages for a session."""
    state: DashboardState = request.app["state"]
    key = request.match_info["key"]
    if not state.conversation_log:
        return web.json_response([])
    return web.json_response(state.conversation_log.read_messages(key))


async def api_session_delete(request: web.Request) -> web.Response:
    """DELETE /api/sessions/{key} — permanently delete a history session."""
    state: DashboardState = request.app["state"]
    key = request.match_info["key"]
    if not state.conversation_log:
        return web.json_response({"error": "no conversation log"}, status=400)
    ok = state.conversation_log.delete_session(key)
    if ok:
        try:
            await _remove_session_for_history_key(state, key)
        except Exception:
            logger.warning("cleanup failed for session %s", key, exc_info=True)
        state.push_sessions_update()
        state.push_refresh("history")
    return web.json_response({"ok": ok})


async def _remove_session_for_history_key(state: DashboardState, key: str) -> None:
    """Remove the active chat session corresponding to a history key.

    Session keys may be the raw history key (``dashboard_chat-X-TS`` when
    resumed from history) or the stripped form (``chat-X-TS`` for
    sessions that were never closed and resumed).  Try the exact key
    first, then the stripped variant.  Also kills the ACP agent session
    to prevent orphaned processes.
    """
    from personalclaw.dashboard.chat import _history_key_for  # circular import  # noqa: F811

    session = state._sessions.pop(key, None)
    if not session:
        stripped = key
        if stripped.startswith("dashboard:"):
            stripped = stripped[len("dashboard:") :]
        while stripped.startswith("dashboard_"):
            stripped = stripped[len("dashboard_") :]
        session = state._sessions.pop(stripped, None)
    if not session:
        # Reverse: history key has no prefix, but session was stored with one
        session = state._sessions.pop("dashboard_" + key, None)
    if session and session.running and session.task is not None:
        session.task.cancel()
        try:
            await asyncio.wait_for(session.task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    # Kill the ACP agent subprocess to free resources
    if session:
        try:
            await state.sessions.destroy(_history_key_for(key))
        except Exception:
            pass


async def api_sessions_clear(request: web.Request) -> web.Response:
    """DELETE /api/sessions — permanently delete closed history sessions only.

    Skips sessions currently open in the sidebar (any session in
    ``state._sessions``) and sessions with ``pinned=True`` on disk.
    Bulk-archiving open unpinned/idle sessions.
    """
    state: DashboardState = request.app["state"]
    if not state.conversation_log:
        return web.json_response({"error": "no conversation log"}, status=400)

    from personalclaw.dashboard.chat import _history_key_for  # noqa: F811

    protected: set[str] = set()
    for session in state._sessions.values():
        hk = _history_key_for(session.key)
        protected.add(hk)
        protected.add(hk.replace(":", "_", 1))

    sessions = state.conversation_log.list_sessions()
    count = 0
    skipped = 0
    failed = 0
    cleanup_tasks = []
    for s in sessions:
        key = s["key"]
        if key in protected:
            skipped += 1
            continue
        try:
            meta = state.conversation_log.get_metadata(key)
        except Exception:
            logger.warning(
                "api_sessions_clear: unreadable metadata for %s, skipping", key, exc_info=True
            )
            skipped += 1
            continue
        if not isinstance(meta, dict):
            skipped += 1
            continue
        if meta.get("pinned"):
            skipped += 1
            continue
        try:
            if state.conversation_log.delete_session(key):
                cleanup_tasks.append(_remove_session_for_history_key(state, key))
                count += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.warning("api_sessions_clear: delete raised for %s", key, exc_info=True)
    if cleanup_tasks:
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    if count:
        state.push_sessions_update()
        state.push_refresh("history")
    logger.info("api_sessions_clear: cleared=%d skipped=%d failed=%d", count, skipped, failed)
    return web.json_response(
        {"ok": failed == 0, "cleared": count, "skipped": skipped, "failed": failed}
    )


# ── Approvals ──


async def api_approvals(request: web.Request) -> web.Response:
    """GET /api/approvals — list pending tool approvals."""
    state: DashboardState = request.app["state"]
    return web.json_response(list(state._pending_approvals.values()))


async def api_approval_resolve(request: web.Request) -> web.Response:
    """POST /api/approvals/{id}/{action} — approve or reject."""
    state: DashboardState = request.app["state"]
    approval_id = request.match_info["id"]
    action = request.match_info["action"]
    if action not in ("approve", "reject"):
        return web.json_response({"error": "invalid action"}, status=400)
    ok = state.resolve_approval(approval_id, action == "approve")
    if not ok:
        return web.json_response({"error": "not found or expired"}, status=404)
    return web.json_response({"ok": True})


async def api_session_keepalive(request: web.Request) -> web.Response:
    """POST /api/session-keepalive — refresh activity timestamp on the
    session's provider so idle-detection/stale-checks don't SIGTERM a
    session that's intentionally blocking in a long-running MCP tool
    (e.g. the `wait` tool).

    Authenticated via X-Internal-Secret; session is selected via the
    X-Session-Key header that all MCP subprocesses already send.
    """
    state: DashboardState = request.app["state"]
    session_key = request.headers.get("X-Session-Key", "").strip()
    if not session_key:
        return web.json_response({"error": "X-Session-Key required"}, status=400)
    provider = state.sessions.get_provider(session_key)
    if provider is None:
        return web.json_response({"error": "session not found"}, status=404)
    try:
        provider.touch_activity()
    except Exception as exc:
        logger.debug("touch_activity failed for %s: %s", session_key, exc)
        return web.json_response({"error": "touch failed"}, status=500)
    return web.json_response({"ok": True})


async def api_session_tool_policy(request: web.Request) -> web.Response:
    """GET /api/session-tool-policy — return managedToolPolicy for the
    calling session's agent.

    Used by managed MCP servers (personalclaw-core, personalclaw-schedule) to filter
    their tool lists per-agent.  Returns {"exclude": [...]} on success,
    or 400/404 when the session cannot be identified (deny-by-default:
    callers that cannot prove identity get an error, not an empty policy).
    Authenticated via X-Internal-Secret + X-Session-Key.
    """
    state: DashboardState = request.app["state"]
    session_key = request.headers.get("X-Session-Key", "").strip()
    if not session_key:
        _sel().log_api_access(
            caller="unknown",
            operation="session_tool_policy",
            outcome="denied",
            source="dashboard",
            resources="missing X-Session-Key",
        )
        return web.json_response({"error": "X-Session-Key required"}, status=400)

    # Resolve agent name from session
    agent_name = ""

    # Dashboard session
    if session_key.startswith("dashboard:"):
        session_name = session_key[len("dashboard:") :]
        session = state.get_session(session_name)
        if session:
            agent_name = session.agent
    # Subagent — look up in SubagentManager
    elif session_key.startswith("subagent:"):
        if state.subagents:
            subagent_id = session_key[len("subagent:") :]
            info = state.subagents.get(subagent_id)
            if info:
                agent_name = info.agent
    # Cron — fall through to session manager lookup below
    elif session_key.startswith("cron:"):
        pass

    # Also check session manager for agent name
    if not agent_name and state.sessions:
        agent_name = state.sessions.get_agent(session_key)

    if not agent_name:
        _sel().log_api_access(
            caller=session_key,
            operation="session_tool_policy",
            outcome="denied",
            source="dashboard",
            resources="agent not resolved",
        )
        return web.json_response({"error": "agent not resolved"}, status=404)

    # Sanitize agent_name to prevent path traversal
    if "/" in agent_name or "\\" in agent_name or ".." in agent_name:
        _sel().log_api_access(
            caller=session_key,
            operation="session_tool_policy",
            outcome="denied",
            source="dashboard",
            resources=f"invalid agent_name={agent_name!r}",
        )
        return web.json_response({"error": "invalid agent name"}, status=400)

    # Read agent config from disk
    agent_path = _path_home_pclaw() / "agents" / f"{agent_name}.json"
    if not agent_path.is_file():
        return web.json_response({})

    try:
        config = json.loads(agent_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return web.json_response({})

    policy = config.get("managedToolPolicy", {})
    if not isinstance(policy, dict):
        return web.json_response({})

    _sel().log_api_access(
        caller=session_key,
        operation="session_tool_policy",
        outcome="ok",
        source="dashboard",
        resources=f"agent={agent_name}",
    )
    return web.json_response(policy)


async def _reset_all_sessions(request: web.Request) -> int:
    """Reset all active sessions so they pick up config changes.

    Reloads provider factory (handles provider switch between ACP backends, e.g.
    native↔claude-code), shuts down all active sessions AND drains the warm pool
    (pre-spawned processes loaded the old MCP config at spawn time).
    New sessions cold-start on next message.
    Returns the number of sessions reset.
    """
    state: DashboardState = request.app["state"]
    sessions = state.sessions

    # Reload factory so provider switch takes effect immediately
    await sessions.reload_provider_factory()

    # Pop all active sessions
    providers: "list[ModelProvider]" = []
    count = sessions.count
    if count > 0:
        providers = await sessions.drain_all_providers()

    # Drain warm pool — pre-spawned processes have stale MCP config
    pool_providers = await sessions.drain_warm_pool()
    providers.extend(pool_providers)

    if count > 0 or pool_providers:
        logger.info(
            "Reset %d session(s) + %d pool process(es) after config change",
            count,
            len(pool_providers),
        )

    state.broadcast_ws("sessions_restarting", {"status": "restarting"})

    async def _background_restart() -> None:
        if providers:

            async def _safe_shutdown(p: "ModelProvider") -> None:
                import personalclaw.dashboard.handlers as _h  # noqa: F811

                _timeout = _SHUTDOWN_TIMEOUT_SECS
                try:
                    await asyncio.wait_for(p.shutdown(), timeout=_timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Session shutdown hung past %.1fs; forcing kill",
                        _timeout,
                    )
                    try:
                        _h._sync_kill_provider(p)
                    except Exception:
                        logger.exception("Force-kill fallback also failed for %r", p)
                except Exception:
                    pass

            await asyncio.gather(*[_safe_shutdown(p) for p in providers])

        sessions._pool_started = False
        await sessions.start_pool(blocking=False)
        logger.info("Background session restarted")
        state.push_refresh("agents")
        state.push_sessions_update()
        state.broadcast_ws("sessions_restarting", {"status": "ready"})

    task = asyncio.create_task(_background_restart())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)

    return count


async def api_sessions_restart(request: web.Request) -> web.Response:
    """POST /api/sessions/restart — reset all ACP agent sessions.

    Forces fresh context injection on the next message. Use after editing
    memory, lessons, or skills to pick up changes immediately.

    Also syncs MCP servers from mcp.json → personalclaw.json so newly
    installed servers (e.g. via marketplace) are picked up on restart.
    """
    # Sync MCP servers before restarting so new installs take effect.
    # Run in thread — discover/sync do blocking file I/O and subprocess calls.
    # Cap at 30s so a hung ACP agent subprocess doesn't stall the restart.
    synced = 0
    try:

        async def _sync() -> int:
            to_sync = await asyncio.to_thread(discover_servers_to_sync)
            if to_sync:
                ok: bool = await asyncio.to_thread(sync_to_agent_config, to_sync)
                # Register for claude-code unconditionally (it reads its own .mcp.json)
                await asyncio.to_thread(register_servers_for_cc, to_sync)
                if ok:
                    return len(to_sync)
            return 0

        synced = await asyncio.wait_for(_sync(), timeout=30)
    except Exception:
        logger.warning("MCP server sync failed before restart", exc_info=True)
    count = await _reset_all_sessions(request)
    return web.json_response({"ok": True, "sessions_reset": count, "mcp_synced": synced})


async def api_session_archive_list(request: web.Request) -> web.Response:
    """GET /api/session/archive?key=... — list archive files for a session key."""
    from typing import Any

    from personalclaw.history import _archive_dir, _safe_key

    key = request.query.get("key", "").strip()
    adir = _archive_dir()
    if not adir.exists():
        return web.json_response({"archives": []})
    prefix = f"{_safe_key(key)}__" if key else ""

    def _collect() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for p in adir.glob(f"{prefix}*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            stem = p.stem
            # Archive filenames use '__' delimiter: {safekey}__{stamp}.jsonl
            sep = stem.find("__")
            safekey = stem[:sep] if sep >= 0 else stem
            stamp = stem[sep + 2 :] if sep >= 0 else ""
            items.append(
                {
                    "name": p.name,
                    "key": safekey,
                    "stamp": stamp,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return items

    items = await asyncio.to_thread(_collect)
    return web.json_response({"archives": items})


async def api_session_archive_read(request: web.Request) -> web.Response:
    """GET /api/session/archive/{name} — read a single archive file as JSONL text."""
    from personalclaw.history import _archive_dir
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    name = request.match_info.get("name", "")
    if not name.endswith(".jsonl"):
        return web.json_response({"error": "invalid archive name"}, status=400)
    adir = _archive_dir().resolve()
    try:
        resolved = (adir / name).resolve()
    except (OSError, RuntimeError, ValueError):
        return web.json_response({"error": "invalid archive name"}, status=400)
    # Canonical path check: file must be a direct child of the archive dir.
    if resolved.parent != adir:
        return web.json_response({"error": "invalid archive name"}, status=400)

    def _read_capped(p: Path, limit: int = 250_000) -> str:
        with p.open(encoding="utf-8") as f:
            data = f.read(limit)
        # Truncate at last newline to keep NDJSON valid.
        if len(data) == limit:
            nl = data.rfind("\n")
            if nl > 0:
                data = data[: nl + 1]
        return data

    try:
        raw = await asyncio.to_thread(_read_capped, resolved)
    except FileNotFoundError:
        return web.json_response({"error": "not found"}, status=404)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read archive %s: %s", name, exc)
        return web.json_response({"error": "unreadable archive"}, status=422)
    # Archives contain LLM output; redact credentials and exfiltration URLs before serving.
    redacted = await asyncio.to_thread(
        lambda: redact_exfiltration_urls(redact_credentials(raw)[0])[0]
    )
    return web.Response(text=redacted, content_type="application/x-ndjson")
