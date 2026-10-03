"""An API request a session makes runs as deriving from that session.

The agent's memory, knowledge and vocabulary tools reach the gateway over HTTP and name their
session in ``X-Session-Key``, as the chat page does. This middleware makes that session the
request's memory-write scope (:mod:`personalclaw.memory_writes`), so the stores refuse a write the
request makes for an Incognito or Temporary session, whichever handler makes it, and answers the
refusal as the API always has: 403 ``Memory writes are not allowed in this session mode.``, with a
security-log row. Such a session's request also runs on the one model its work stays on
(``memory_writes.as_work_of``): the model its turn named, so a subagent its agent starts here runs
on it, and nothing the request does reaches another.

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


def _mode_of(request: web.Request, session_key: str) -> str | None:
    """The mode of the session ``session_key`` names, as the one reader of a session's mode reads
    it over the gateway's live chats (``memory_writes.session_mode``): the live chat first, so a
    chat whose transcript is not written yet is read as it is, and one the gateway does not hold
    that nothing records keeps nothing."""
    return memory_writes.session_mode(session_key, state=request.app.get("state"))


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
        with memory_writes.as_work_of(session_key, memory_mode=_mode_of(request, session_key)):
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
