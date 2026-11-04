"""``GET /api/agents/day``: what each agent did in a window of time, for Home's agents band.

Read-only, and folded from the usage ledger on every read (:mod:`personalclaw.agent_day`), off
the event loop, since the band asks again whenever work changes. It answers agent names, times,
session keys and the kind of work, never what was said or done, for a day at most and
:data:`~personalclaw.agent_day.MAX_BLOCKS` blocks at most.

It names every chat's sessions and when each worked, so it is yours to read: your signed-in
session, and a tool your own pages run (``dashboard:ui``). No app's token reaches it
(``apps/permissions.ROUTE_AUTHZ``), and a chat's agent is refused it here, as the running list
shows each of them only its own work. Errors use the shared ``{error:{code,message}}`` envelope.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiohttp import web

from personalclaw import agent_day, approval_answer, session_keys
from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

#: The window the band asks for when it names none: the last four hours.
DEFAULT_WINDOW = timedelta(hours=4)


def _yours(request: web.Request) -> bool:
    """Whether *request* is yours: your sign-in, or a tool your own pages run."""
    by = approval_answer.of_request(request)
    if by.kind == approval_answer.OWNER:
        return True
    return (
        by.kind == approval_answer.AGENT
        and approval_answer.work_of_request(request) == session_keys.DASHBOARD_UI
    )


def _limit(raw: str) -> int | None:
    """The ``limit`` asked for, ``MAX_BLOCKS`` when none is, ``None`` when it is out of bounds."""
    if not raw:
        return agent_day.MAX_BLOCKS
    try:
        limit = int(raw)
    except ValueError:
        return None
    return limit if 1 <= limit <= agent_day.MAX_BLOCKS else None


async def api_agents_day(request: web.Request) -> web.Response:
    """GET /api/agents/day?since=&until=&limit= — each agent's blocks of work in ``[since, until)``.

    ``since`` and ``until`` are ISO times; without them the window is the last four hours up to
    now. ``limit`` is the most blocks to answer (the newest are kept, and ``cut`` says how many
    were not). A window that runs backwards or is wider than a day is refused, and so is a
    ``limit`` past :data:`~personalclaw.agent_day.MAX_BLOCKS`, and a caller that is not you
    (:func:`_yours`)."""
    if not _yours(request):
        return json_error("agents_day_owner_only", status=403)
    now = agent_day.clock()
    raw_since = request.query.get("since", "")
    raw_until = request.query.get("until", "")
    until = agent_day.parse_time(raw_until) if raw_until else now
    if until is None:
        return json_error("bad_request", message="since and until must be ISO times", status=400)
    since = agent_day.parse_time(raw_since) if raw_since else until - DEFAULT_WINDOW
    if since is None:
        return json_error("bad_request", message="since and until must be ISO times", status=400)
    if until <= since or until - since > agent_day.MAX_WINDOW:
        return json_error(
            "bad_request", message="the window must run forwards, 24 hours at most", status=400
        )
    limit = _limit(request.query.get("limit", ""))
    if limit is None:
        return json_error(
            "bad_request",
            message=f"limit must be a whole number from 1 to {agent_day.MAX_BLOCKS}",
            status=400,
        )
    try:
        agents, cut = await asyncio.to_thread(
            agent_day.read, since=since, until=until, now=now, limit=limit
        )
    except Exception:  # noqa: BLE001 — a ledger read must never 500 a read-only surface quietly
        logger.debug("agent day read failed", exc_info=True)
        return json_error("internal_error", message="could not read the usage ledger", status=500)
    return web.json_response(
        {"since": since.isoformat(), "until": until.isoformat(), "agents": agents, "cut": cut}
    )


def register_agent_day_routes(app: web.Application) -> None:
    app.router.add_get("/api/agents/day", api_agents_day)
