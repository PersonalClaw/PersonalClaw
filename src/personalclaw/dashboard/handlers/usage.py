"""Usage read routes — the usage ledger, its per-day spend fold, and the daily cap beside the
spend it is held to.

Four read-only GETs, deliberately in ONE module because they answer one user question ("what did
this cost me?") at different grains, and a second usage handler module would split that answer:

* ``/api/usage/rollup`` + ``/api/usage/totals`` — the ledger (``usage_ledger``), one row
  per model call, filterable by session and an arbitrary ``[since, until)`` window, or by the
  fold's ``window`` of her days so a page's tiles and its chart count the same days. The
  session-grain forensic view.
* ``/api/usage`` — the per-DAY durable fold (``routing/usage.py``) of the same ledger,
  grouped by model / provider / purpose under the single ``interactive|background|loop|eval|app``
  vocabulary, with a census of the guarded model calls (``model_calls.jsonl``) no row counts: a
  call that did not finish writes none.
* ``/api/usage/budget`` — today's spend as the daily cap counts it (the spend meter), beside that
  cap. The ledger also holds every chat turn, which no cap counts, so its totals are no figure to
  set beside the cap.

Every day here is her day (:mod:`personalclaw.spend_day`): the calendar day in her timezone, the
one her schedules run in, whatever zone the gateway process's own clock is in.

The overlap is intentional and bounded: the fold is the long-horizon record (the ledger JSONL is
capped), the rollup is the recent per-session detail.

Read-only throughout — this is observation, never enforcement, so there is no write/mutate route
here: the budget route reads the meter the cap is enforced from and changes nothing. Errors use
the shared ``{error:{code,message}}`` envelope (:func:`personalclaw.http_errors.json_error`).
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw import spend_day
from personalclaw import usage_ledger as ul
from personalclaw.constants import dashboard_history_key
from personalclaw.http_errors import json_error
from personalclaw.routing import usage as usage_fold

logger = logging.getLogger(__name__)


def _bounds(request: web.Request) -> tuple[str, str] | web.Response:
    """The ``[since, until)`` a ledger read covers: the query's own, or the start of the fold's
    ``window`` (``day``/``week``/``month``) of her days, which ends now. A window is the days the
    daily cap counts (``spend_day``), so "Today" here is the cap's today in her timezone, not the
    UTC one nor the gateway clock's."""
    since = request.query.get("since", "")
    until = request.query.get("until", "")
    window = request.query.get("window", "")
    if not window:
        return since, until
    if window not in usage_fold.WINDOW_DAYS:
        return json_error(
            "bad_request",
            message=f"window must be one of {list(usage_fold.WINDOW_DAYS)}, got {window!r}",
            status=400,
        )
    if since or until:
        return json_error(
            "bad_request", message="give a window or since/until, not both", status=400
        )
    zone = spend_day.zone()
    first = usage_fold.window_dates(window, today=spend_day.today(zone))[0]
    return spend_day.start_of(first, zone), ""


# The rollup grouping keys the ledger supports (mirrors usage_ledger._GROUP_KEYS);
# validated at the route boundary so a bad ?group_by= is a clean 400, not a 500.
_GROUP_KEYS = ("model", "source", "agent", "provider", "day")


async def api_usage_rollup(request: web.Request) -> web.Response:
    """GET /api/usage/rollup?group_by=&since=&until=&window=&session= — aggregated ledger rows.

    ``group_by`` defaults to ``model``; ``since``/``until`` are optional ISO
    timestamps bounding a ``[since, until)`` window (empty = unbounded), or ``window`` names
    the fold's days instead (:func:`_bounds`); ``session``
    restricts to one session key (empty = all). The param carries the bare chat session
    id the frontend/URL hold; ledger rows are keyed by the ``dashboard:``-namespaced
    form (``chat_runner.run_chat`` writes via ``_history_key_for``), so it is
    canonicalized here through the writer's own rule, ``dashboard_history_key``, as every
    reader of those rows is — a bare-key query would otherwise match nothing and silently
    report a confident 0."""
    group_by = request.query.get("group_by", "model")
    if group_by not in _GROUP_KEYS:
        return json_error(
            "bad_request",
            message=f"group_by must be one of {list(_GROUP_KEYS)}, got {group_by!r}",
            status=400,
        )
    bounds = _bounds(request)
    if isinstance(bounds, web.Response):
        return bounds
    since, until = bounds
    session = request.query.get("session", "")
    session_key = dashboard_history_key(session) if session else session
    try:
        rows = ul.rollup(since=since, until=until, group_by=group_by, session_key=session_key)
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
    """GET /api/usage/totals?since=&until=&window=&session= — the grand total over the window.

    The window is bounded as :func:`api_usage_rollup` bounds it.

    ``session`` (when given) restricts to one session key — the session-total surface
    (``ChatPage``'s cost chip). Canonicalized the same way as ``api_usage_rollup``
    above — see that docstring."""
    bounds = _bounds(request)
    if isinstance(bounds, web.Response):
        return bounds
    since, until = bounds
    session = request.query.get("session", "")
    session_key = dashboard_history_key(session) if session else session
    try:
        totals = ul.totals(since=since, until=until, session_key=session_key)
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
        return json_error(
            "bad_request",
            message=f"window must be one of {list(usage_fold.WINDOW_DAYS)}, got {window!r}",
            status=400,
        )
    group = request.query.get("group", "model")
    if group not in usage_fold.GROUPS:
        return json_error(
            "bad_request",
            message=f"group must be one of {list(usage_fold.GROUPS)}, got {group!r}",
            status=400,
        )
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


async def api_usage_budget(request: web.Request) -> web.Response:
    """GET /api/usage/budget — today's metered spend beside the daily cap it is held to.

    The two numbers the guardrails compare, from the one place they compare them: the day total
    the spend meter charges (``guardrails.budgets.SpendMeter``) and the ceiling
    ``budget_from_config`` builds. The meter counts the model calls PersonalClaw makes on its own
    (automations, loops, subagents, background work) and no chat turn, over her day. The Usage
    page used to set the ledger's total beside the cap instead: chat turns included, on a UTC day,
    so it could show a cap spent that was not, or not show one that was.

    ``cap_unreadable`` is True when the configured ceiling could not be read; the caps are then
    ``null``, never an unlimited 0 nobody chose.

    ``unpriced_calls`` is how many of today's metered calls had no price: nothing priced their
    model, so their dollars are not in ``spent_dollars`` and the dollar cap could not count them.
    The page says so beside the total, rather than letting them read as free.

    ``resets_at`` is when the day the caps count ends (epoch seconds, her next midnight), which a
    run a cap stopped says in the reader's own time.
    """
    from personalclaw.guardrails.budgets import (
        BudgetConfigUnreadable,
        budget_from_config,
        get_meter,
    )

    spent = get_meter().day_totals()
    max_dollars: float | None = None
    max_tokens: int | None = None
    try:
        budget = budget_from_config()
    except BudgetConfigUnreadable:
        unreadable = True
    else:
        unreadable = False
        max_dollars, max_tokens = float(budget.max_dollars), int(budget.max_tokens)
    return web.json_response(
        {
            "spent_dollars": round(float(spent.dollars), 6),
            "spent_tokens": int(spent.tokens),
            "unpriced_calls": int(spent.unpriced),
            "max_dollars_per_day": max_dollars,
            "max_tokens_per_day": max_tokens,
            "cap_unreadable": unreadable,
            "resets_at": round(spend_day.next_day_starts(), 3),
        }
    )


def register_usage_routes(app: web.Application) -> None:
    app.router.add_get("/api/usage", api_usage)
    app.router.add_get("/api/usage/budget", api_usage_budget)
    app.router.add_get("/api/usage/rollup", api_usage_rollup)
    app.router.add_get("/api/usage/totals", api_usage_totals)
