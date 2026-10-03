"""An API request a session makes runs as deriving from that session, and as its app's work.

The agent's memory, knowledge and vocabulary tools reach the gateway over HTTP and name their
session in ``X-Session-Key``, as the chat page does. This middleware makes that session the
request's memory-write scope (:mod:`personalclaw.memory_writes`), so the stores refuse a write the
request makes for an Incognito or Temporary session, whichever handler makes it, and answers the
refusal as the API always has: 403 ``Memory writes are not allowed in this session mode.``, with a
security-log row. Such a session's request also runs on the one model its work stays on
(``memory_writes.as_work_of``): the model its turn named, so a subagent its agent starts here runs
on it, and nothing the request does reaches another.

The scope also names the app whose work the request is: the app whose own token made it, or the
one whose conversation, agent, agent run or scheduled job the session names
(:func:`personalclaw.memory_reads.reach_of`, asked only when the request writes). Such work
changes your memory only when the app holds the ``memory`` permission; otherwise the memory stores
refuse the change and the 403 says why in the app's words, and with it what it writes names the app
as its source. A request an app's own token makes with no session of its own is the app's work too.
The same walk names the chat at the top the request's work is done for, which what it writes is
filed under (``memory_writes.filed_under``): a subagent's lesson is its chat's.

``dashboard:ui`` is the dashboard's own pages acting for the owner, not a session, and a request of
yours that names no session is yours. A request made with the gateway's internal credential is
not yours: PersonalClaw's own processes present it (an agent's tools, a tool the gateway runs for a
scheduled script or an app, the CLI), each naming the work it does, and a handler reads the
session it names, so one naming none would run as yours, reading and changing your memory and
starting work that belongs to nobody. Such a request is refused before any handler runs
(``403 internal_call_names_no_work``), with a security-log row.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from personalclaw import approval_answer, memory_reads, memory_writes
from personalclaw.http_errors import json_error
from personalclaw.sel import sel

#: The key the dashboard's own pages send: the owner, not a session.
_DASHBOARD_UI = "dashboard:ui"


def _mode_of(request: web.Request, session_key: str) -> str | None:
    """The mode of the session ``session_key`` names, as the one reader of a session's mode reads
    it over the gateway's live chats (``memory_writes.session_mode``): the live chat first, so a
    chat whose transcript is not written yet is read as it is, and one the gateway does not hold
    that nothing records keeps nothing."""
    return memory_writes.session_mode(session_key, state=request.app.get("state"))


def _names_no_work(request: web.Request) -> bool:
    """Whether *request* was made with the internal credential and names no work it is for: the
    principal it proved is an agent (``approval_answer.of_request``: an app's token makes it the
    app's, whatever else it carries) whose ``X-Session-Key`` is empty."""
    by = approval_answer.of_request(request)
    return by.kind == approval_answer.AGENT and not by.name


def memory_write_middleware() -> Any:
    """Build the middleware (a factory, so the installed instance can be found by a test)."""

    @web.middleware  # type: ignore[misc]
    async def _mw(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if _names_no_work(request):
            sel().log_api_access(
                caller="internal",
                operation=f"{request.method} {request.path}",
                outcome="denied",
                source="dashboard",
                resources="names_no_work",
                error="a call made with the internal credential named no session",
            )
            return json_error("internal_call_names_no_work", status=403)
        session_key = request.headers.get("X-Session-Key", "").strip()
        if session_key == _DASHBOARD_UI:
            session_key = ""
        token_app = str(request.get("app") or "")
        if not session_key and not token_app:
            return await handler(request)
        key = session_key or f"{memory_writes.APP_SOURCE_PREFIX}{token_app}"
        state = request.app.get("state")
        whose = functools.partial(memory_reads.reach_of, state, key, app=token_app)

        with memory_writes.as_work_of(key, memory_mode=_mode_of(request, key), reach=whose):
            try:
                return await handler(request)
            except memory_writes.MemoryWriteRefused as refused:
                sel().log_api_access(
                    caller=key,
                    operation=f"{request.method} {request.path}",
                    outcome="denied",
                    source="dashboard",
                    resources=(
                        "app_memory_not_granted"
                        if refused.reason != memory_writes.REFUSAL
                        else "restricted_session_block"
                    ),
                    error=refused.what,
                )
                return web.json_response({"error": refused.reason}, status=403)

    _mw._is_memory_write_gate = True  # type: ignore[attr-defined]
    return _mw
