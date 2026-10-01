"""An API request a session makes runs as deriving from that session.

The agent's memory, knowledge and vocabulary tools reach the gateway over HTTP and name their
session in ``X-Session-Key``, as the chat page does. This middleware makes that session the
request's memory-write scope (:mod:`personalclaw.memory_writes`), so the stores refuse a write the
request makes for an Incognito or Temporary session, whichever handler makes it, and answers the
refusal as the API always has: 403 ``Memory writes are not allowed in this session mode.``, with a
security-log row.

``dashboard:ui`` is the dashboard's own pages acting for the owner, not a session.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from personalclaw import memory_writes
from personalclaw.sel import sel

#: The key the dashboard's own pages send: the owner, not a session.
_DASHBOARD_UI = "dashboard:ui"


def _live_mode(request: web.Request, session_key: str) -> str | None:
    """The mode of the live dashboard chat ``session_key`` names, or ``None`` when none is live."""
    state = request.app.get("state")
    sessions = getattr(state, "_sessions", None)
    if not isinstance(sessions, dict):
        return None
    session = sessions.get(session_key.split(":", 1)[-1])
    mode = getattr(session, "memory_mode", None)
    return mode if isinstance(mode, str) else None


def memory_write_middleware() -> Any:
    """Build the middleware (a factory, so the installed instance can be found by a test)."""

    @web.middleware  # type: ignore[misc]
    async def _mw(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        session_key = request.headers.get("X-Session-Key", "").strip()
        if not session_key or session_key == _DASHBOARD_UI:
            return await handler(request)
        with memory_writes.derived_from(session_key, memory_mode=_live_mode(request, session_key)):
            try:
                return await handler(request)
            except memory_writes.MemoryWriteRefused as refused:
                sel().log_api_access(
                    caller=session_key,
                    operation=f"{request.method} {request.path}",
                    outcome="denied",
                    source="dashboard",
                    resources="restricted_session_block",
                    error=refused.what,
                )
                return web.json_response({"error": memory_writes.REFUSAL}, status=403)

    _mw._is_memory_write_gate = True  # type: ignore[attr-defined]
    return _mw
