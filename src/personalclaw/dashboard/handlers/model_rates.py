"""The prices your model calls are counted at: model_rates.json, read and set one rate at a time.

``GET /api/models/rates`` answers what Settings → Usage → Model prices shows
(``routing.rates.rates_view``): the rates you set, and each model your uses are bound to with the
rate a call to it is counted at and where that comes from. ``PUT`` sets one rate and ``DELETE``
removes one. Each is a change to one rate, applied to the file as it is stored now, so neither
names a revision (``stale_write``): it cannot undo a change made elsewhere.

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


def _view() -> web.Response:
    """The page's view (``routing.rates.rates_view``), over the models every use is bound to now."""
    from personalclaw.providers.use_cases import load_active_models
    from personalclaw.routing.rates import rates_view

    refs = [
        str(ref)
        for chain in (load_active_models() or {}).values()
        if isinstance(chain, list)
        for ref in chain
    ]
    view = rates_view(refs)
    return web.json_response(
        {"rates": view["rates"], "models": view["models"], "unreadable": view["unreadable"]}
    )


def _unreadable(exc: Exception) -> web.Response:
    return json_error(
        "model_rates_unreadable",
        message=(
            f"{exc}, so no price in it is in effect. Saving here would replace what it holds: fix "
            "the file or remove it first."
        ),
        status=409,
    )


async def api_model_rates(request: web.Request) -> web.Response:
    """GET /api/models/rates — the rates you set, and what each bound model is counted at."""
    return _view()


async def api_model_rate_put(request: web.Request) -> web.Response:
    """PUT /api/models/rates — set the rate for one key.

    Body ``{key, in_per_mtok, out_per_mtok, cache_read_per_mtok?, cache_write_per_mtok?}``, each
    rate USD per 1,000,000 tokens (``routing.rates.rate_entry``). Answers the page's new view.
    """
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
    except OSError:
        logger.warning("model rate for %s could not be saved", key, exc_info=True)
        return json_error("model_rate_unsaved", message="The price could not be saved.", status=500)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="model_rates.set",
        outcome="success",
        source="dashboard",
        resources=f"{key}: " + ", ".join(f"{name}={value:g}" for name, value in row.items()),
    )
    return _view()


async def api_model_rate_delete(request: web.Request) -> web.Response:
    """DELETE /api/models/rates?key= — remove the rate set for one key. Answers the new view."""
    from personalclaw.routing.rates import RatesUnreadable, clear_rate

    key = str(request.query.get("key", "") or "").strip()
    if not key:
        return json_error("bad_request", message="Name the key of the rate to remove.", status=400)
    try:
        removed = clear_rate(key)
    except RatesUnreadable as exc:
        return _unreadable(exc)
    except OSError:
        logger.warning("model rate for %s could not be removed", key, exc_info=True)
        return json_error(
            "model_rate_unsaved", message="The price could not be removed.", status=500
        )
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
