"""The API-only server: the MCP tools' HTTP transport, served without the dashboard UI.

The gateway starts this in place of `start_dashboard` when it runs without the dashboard, so the
MCP tools (spawn, lessons, triggers, send-message, notifications) keep the routes they call. It
serves exactly `_register_mcp_routes`'s set on loopback, behind an audit middleware that records
each call in the security event log.
"""

import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw.dashboard.memory_write_gate import memory_write_middleware
from personalclaw.dashboard.server import (
    _precompute_telemetry,
    _register_mcp_routes,
    _single_post_ceiling,
    _start_site,
)
from personalclaw.dashboard.state import _DEFAULT_PORT, DashboardState
from personalclaw.hooks import ScriptHookStore, set_global_hook_store

if TYPE_CHECKING:
    from personalclaw.dashboard._types import SessionManager, SubagentManager

logger = logging.getLogger(__name__)


async def start_api_server(
    sessions: "SessionManager",
    port: int = _DEFAULT_PORT,
    subagents: "SubagentManager | None" = None,
    owner_id: str = "",
) -> tuple[web.AppRunner, DashboardState]:
    """Start a minimal API-only server for MCP tool transport (no UI)."""
    state = DashboardState(
        sessions=sessions,
        start_time=time.time(),
        subagents=subagents,
        owner_id=owner_id,
    )
    state._hook_store = ScriptHookStore()
    set_global_hook_store(state._hook_store)

    from personalclaw.inbox_providers.native_source import set_dashboard_state as _set_inbox_state

    _set_inbox_state(state)

    # Wire script hooks into subagent tool execution path
    if state.subagents is not None:
        state.subagents.hook_store = state._hook_store

    # Visible notice + pct reset when auto-compaction fires on a dashboard session
    state.wire_session_compact_callback()

    app = web.Application(
        client_max_size=_single_post_ceiling()
    )  # small single-POST uploads only; large media → resumable upload sub-app
    app["state"] = state
    state.load_folders()
    state.load_tags()
    app["port"] = port
    from personalclaw.auth.modes import AuthConfig as _AuthConfig

    app["auth_cfg"] = _AuthConfig.from_env()

    _precompute_telemetry(state)

    # SEL audit middleware — log mutating MCP tool calls
    _sel_methods = {"GET", "POST", "PUT", "DELETE"}

    @web.middleware  # type: ignore[misc]
    async def sel_audit_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method in _sel_methods and request.path.startswith("/api/"):
            from personalclaw.sel import sel

            try:
                resp = await handler(request)  # type: ignore[operator]
                sel().log_api_access(
                    caller="mcp_tool",
                    operation=f"{request.method} {request.path}",
                    outcome="ok" if resp.status < 400 else "error",
                    resources=request.path,
                )
                return resp  # type: ignore[return-value]
            except Exception as exc:
                sel().log_api_access(
                    caller="mcp_tool",
                    operation=f"{request.method} {request.path}",
                    outcome="error",
                    resources=request.path,
                    error=str(exc)[:200],
                )
                raise
        return await handler(request)  # type: ignore[operator]

    app.middlewares.append(sel_audit_middleware)
    # The tools' memory, knowledge and vocabulary writes for their session (see its module).
    app.middlewares.append(memory_write_middleware())

    _register_mcp_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await _start_site(site, port)
    logger.info("API-only server listening on 127.0.0.1:%d", port)

    return runner, state
