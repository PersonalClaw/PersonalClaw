"""Usage-ledger read routes (COST-AND-TOKEN-OBSERVABILITY CATO-5).

Two GETs over the per-turn cost/token ledger (``usage_ledger``): a grouped rollup
and the grand totals. Read-only — this plan is observation, never enforcement, so
there is no write/mutate route here. Errors use the §2.2 ``{error:{code,message}}``
envelope. The surfaces that render this (S2: turn readout, session header, Usage
panel) are later atoms; this is the data they read.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw import usage_ledger as ul

logger = logging.getLogger(__name__)

# The rollup grouping keys the ledger supports (mirrors usage_ledger._GROUP_KEYS);
# validated at the route boundary so a bad ?group_by= is a clean 400, not a 500.
_GROUP_KEYS = ("model", "source", "agent", "provider", "day")


def _bad_request(message: str) -> web.Response:
    return web.json_response({"error": {"code": "bad_request", "message": message}}, status=400)


async def api_usage_rollup(request: web.Request) -> web.Response:
    """GET /api/usage/rollup?group_by=&since=&until=&session= — aggregated ledger rows.

    ``group_by`` defaults to ``model``; ``since``/``until`` are optional ISO
    timestamps bounding a ``[since, until)`` window (empty = unbounded); ``session``
    restricts to one session key (empty = all)."""
    group_by = request.query.get("group_by", "model")
    if group_by not in _GROUP_KEYS:
        return _bad_request(f"group_by must be one of {list(_GROUP_KEYS)}, got {group_by!r}")
    since = request.query.get("since", "")
    until = request.query.get("until", "")
    session = request.query.get("session", "")
    try:
        rows = ul.rollup(since=since, until=until, group_by=group_by, session_key=session)
    except Exception:  # noqa: BLE001 — a ledger read must never 500 a read-only surface
        logger.debug("usage rollup failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not read the usage ledger"}},
            status=500,
        )
    return web.json_response(
        {"group_by": group_by, "since": since, "until": until, "session": session, "rows": rows}
    )


async def api_usage_totals(request: web.Request) -> web.Response:
    """GET /api/usage/totals?since=&until=&session= — the grand total over the window.

    ``session`` (when given) restricts to one session key — the session-total surface."""
    since = request.query.get("since", "")
    until = request.query.get("until", "")
    session = request.query.get("session", "")
    try:
        totals = ul.totals(since=since, until=until, session_key=session)
    except Exception:  # noqa: BLE001
        logger.debug("usage totals failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not read the usage ledger"}},
            status=500,
        )
    return web.json_response({"since": since, "until": until, "session": session, "totals": totals})


def register_usage_routes(app: web.Application) -> None:
    app.router.add_get("/api/usage/rollup", api_usage_rollup)
    app.router.add_get("/api/usage/totals", api_usage_totals)
