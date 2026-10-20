"""The prices your model calls are counted at: your prices in ``config.json``, set one at a time.

``GET /api/models/rates`` answers what Settings → Usage → Model prices shows
(``routing.rates.rates_view``): the prices you set, and each model your uses are bound to, or that
was spent on in the last :data:`USED_WINDOW_DAYS` days, with the rate a call to it is counted at,
where that comes from and, under a price of yours, the default it stands in front of. A model
unbound after it spent money is listed too: the Usage page names it, and this is where its price is
set. ``PUT`` sets your price for one model, a known one included (``config.json`` →
``model_prices.overrides``, through its one writer), and ``DELETE`` resets it to the default. Each
is a change to one price, applied to the file as it is stored now, so neither names a revision
(``stale_write``): it cannot undo a change made elsewhere.

A price is what the daily and per-run dollar caps count a call at, the Usage page's dollars and
the order cost-aware routing tries models in. So all three routes are the owner's
(``apps.permissions.ROUTE_AUTHZ``): an app that could set one could make a model read free to the
caps. Each change is recorded in the security log.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding, like every handler module's, so a test's patch of the package reaches it."""
    import personalclaw.dashboard.handlers as _pkg

    return _pkg.sel()


#: How far back a model that was spent on is listed, bound or not: the Usage page's longest period.
USED_WINDOW_DAYS = 30


def _view() -> web.Response:
    """The page's view (``routing.rates.rates_view``), over the models every use is bound to now
    and the ones spent on lately."""
    from datetime import datetime, timedelta, timezone

    from personalclaw.providers.use_cases import load_active_models
    from personalclaw.routing.rates import rates_view
    from personalclaw.usage_ledger import models_used

    bound: list[tuple[str, str]] = []
    for chain in (load_active_models() or {}).values():
        for ref in chain if isinstance(chain, list) else ():
            provider, _, model = str(ref).partition(":")
            bound.append((provider, model))
    since = (datetime.now(timezone.utc) - timedelta(days=USED_WINDOW_DAYS)).isoformat()
    view = rates_view([*bound, *models_used(since=since)])
    return web.json_response(
        {"rates": view["rates"], "models": view["models"], "unreadable": view["unreadable"]}
    )


def _described(row: dict) -> str:
    """A rate as the security log records it: each field and its value, a tier as its own."""
    parts: list[str] = []
    for name, value in row.items():
        if isinstance(value, list):
            for tier in value:
                if isinstance(tier, dict):
                    shown = "/".join(str(tier.get(k) or "any") for k in ("size", "quality"))
                    parts.append(f"{shown}={tier.get('per_image')}")
        elif isinstance(value, float):
            parts.append(f"{name}={value:g}")
        else:
            parts.append(f"{name}={value}")
    return ", ".join(parts)


def _unreadable(exc: Exception) -> web.Response:
    return json_error(
        "model_rates_unreadable",
        message=(
            f"{exc}, so no price you set is in effect. Saving here would replace what it holds: "
            "fix the file first."
        ),
        status=409,
    )


def _unsaved(key: str, what: str) -> web.Response:
    logger.warning("model price for %s could not be %s", key, what, exc_info=True)
    return json_error("model_rate_unsaved", message=f"The price could not be {what}.", status=500)


async def api_model_rates(request: web.Request) -> web.Response:
    """GET /api/models/rates — the rates you set, and what each bound or recent model costs."""
    return _view()


async def api_model_rate_put(request: web.Request) -> web.Response:
    """PUT /api/models/rates — set your price for one key, a model a known price is listed for
    included: yours wins over it until you reset it.

    Body ``{key, unit?, …}`` in the unit the model is billed in (``routing.rates.rate_entry``):
    per 1M tokens ``in_per_mtok``, ``out_per_mtok`` and the optional cache rates; per image
    ``per_image`` or ``tiers`` by size and quality; per second, minute or 1M characters that one
    price. Answers the page's new view.
    """
    from personalclaw.config.loader import ConfigWriteError
    from personalclaw.routing.rates import RatesUnreadable, rate_entry, set_rate

    body = await json_object_body(request, empty_ok=False)
    try:
        key, row = rate_entry(body)
    except ValueError as exc:
        return json_error("bad_request", message=str(exc), status=400)
    try:
        set_rate(key, row)
    except RatesUnreadable as exc:
        return _unreadable(exc)
    except (OSError, ConfigWriteError):
        return _unsaved(key, "saved")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="model_rates.set",
        outcome="success",
        source="dashboard",
        resources=f"{key}: {_described(row)}",
    )
    return _view()


async def api_model_rate_delete(request: web.Request) -> web.Response:
    """DELETE /api/models/rates?key= — reset your price for one key, so the model's default
    prices it again. Answers the new view."""
    from personalclaw.config.loader import ConfigWriteError
    from personalclaw.routing.rates import RatesUnreadable, clear_rate

    key = str(request.query.get("key", "") or "").strip()
    if not key:
        return json_error("bad_request", message="Name the key of the rate to remove.", status=400)
    try:
        removed = clear_rate(key)
    except RatesUnreadable as exc:
        return _unreadable(exc)
    except (OSError, ConfigWriteError):
        return _unsaved(key, "reset")
    if not removed:
        return json_error("not_found", message=f"No price is set for {key}.", status=404)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="model_rates.clear",
        outcome="success",
        source="dashboard",
        resources=key,
    )
    return _view()


def register_model_rates_routes(app: web.Application) -> None:
    app.router.add_get("/api/models/rates", api_model_rates)
    app.router.add_put("/api/models/rates", api_model_rate_put)
    app.router.add_delete("/api/models/rates", api_model_rate_delete)
