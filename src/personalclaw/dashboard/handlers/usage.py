"""Usage read routes — the per-turn ledger (CATO-5) plus the per-day spend fold (MRT-3).

Three read-only GETs, deliberately in ONE module because they answer one user question ("what did
this cost me?") at different grains, and a second usage handler module would split that answer:

* ``/api/usage/rollup`` + ``/api/usage/totals`` (CATO-5) — the per-TURN ledger
  (``usage_ledger``), filterable by session and an arbitrary ``[since, until)`` window. The
  session/turn-grain forensic view.
* ``/api/usage`` (MRT-3) — the per-DAY durable fold (``routing/usage.py``) over BOTH recorded
  stores: the ledger's streamed turns AND ``model_calls.jsonl``'s guarded ``complete()`` attempts,
  which the two routes above cannot see at all (the ledger has no row for them, so the entire
  unattended axis was invisible spend). Grouped by model / provider / purpose under the single
  ``interactive|background|loop|eval|app`` vocabulary.

The overlap is intentional and bounded: the fold is the long-horizon record (both JSONLs are
capped), the rollup is the recent per-session detail. Neither derives from the other, and only the
fold claims to cover both axes.

Read-only throughout — this is observation, never enforcement, so there is no write/mutate route
here. Errors use the §2.2 ``{error:{code,message}}`` envelope.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw import usage_ledger as ul
from personalclaw.routing import usage as usage_fold

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


async def api_usage(request: web.Request) -> web.Response:
    """GET /api/usage?window=day|week|month&group=model|provider|purpose — the per-day spend fold.

    Returns ``{rows, total, estimated_share, series, unmapped, …}``. Every call refreshes the fold
    from the two source JSONLs first, so a deleted ``usage_stats.json`` self-heals here (the fold's
    "reproducible after delete" contract) and a day that has aged out of the capped JSONL survives.

    ``estimated_share`` is the dollar-weighted fraction of the figure that is a rate-table estimate
    rather than a provider-reported charge; ``priced: false`` + ``unpriced_calls`` mark a total that
    is a FLOOR because some model has no price row. The two are separate on purpose — an unpriced
    model must never read as "$0 spent".
    """
    window = request.query.get("window", "day")
    if window not in usage_fold.WINDOW_DAYS:
        return _bad_request(f"window must be one of {list(usage_fold.WINDOW_DAYS)}, got {window!r}")
    group = request.query.get("group", "model")
    if group not in usage_fold.GROUPS:
        return _bad_request(f"group must be one of {list(usage_fold.GROUPS)}, got {group!r}")
    try:
        from personalclaw.config.loader import config_dir

        fold = usage_fold.refresh(config_dir())
    except Exception:  # noqa: BLE001 — a read-only spend view must never 500 on a bad fold
        logger.debug("usage fold refresh failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not read the usage fold"}},
            status=500,
        )
    return web.json_response(usage_fold.query(fold, window=window, group=group))


def register_usage_routes(app: web.Application) -> None:
    app.router.add_get("/api/usage", api_usage)
    app.router.add_get("/api/usage/rollup", api_usage_rollup)
    app.router.add_get("/api/usage/totals", api_usage_totals)
