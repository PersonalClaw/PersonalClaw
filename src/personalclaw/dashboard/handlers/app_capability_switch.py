"""``POST /api/apps/{name}/capabilities/{capability}/on`` and ``…/off``: one capability's own
switch (#3843).

Each writes the switch to the app's installed record, then builds or tears down that capability
alone while the app runs, and answers its status as ``GET /api/apps`` reads it
(``apps.capability_switches``). Only the owner switches a capability: an app's token is refused
before these run (``apps/permissions.ROUTE_AUTHZ``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.sel import sel_log_async


def _sel_log(*args: Any, **kwargs: Any) -> None:
    from personalclaw.dashboard.handlers.apps import _sel_log as log

    log(*args, **kwargs)


async def _switch(request: web.Request, on: bool) -> web.Response:
    from personalclaw.apps import capability_switches
    from personalclaw.dashboard.handlers.apps import _reconcile_app_crons
    from personalclaw.providers.routes import settle_provider_change

    name, capability = request.match_info["name"], request.match_info["capability"]
    op = "apps.capability.on" if on else "apps.capability.off"
    try:
        status = await asyncio.to_thread(
            capability_switches.switch,
            name,
            capability,
            on,
            caller=request.get("user", "dashboard"),
        )
    except capability_switches.SwitchRefused as exc:
        await sel_log_async(_sel_log, op, "refused", name, request, error=str(exc))
        if exc.native:
            return json_error("app_native", message=str(exc), status=exc.status)
        return web.json_response({"error": str(exc)}, status=exc.status)
    await sel_log_async(_sel_log, op, "ok", f"{name}/{capability}", request)
    await settle_provider_change()
    # A job of the capability joins or leaves the trigger store now, not at the next start.
    _reconcile_app_crons(request)
    return web.json_response(
        {"ok": True, "name": name, "capability": capability, **status.to_wire()}
    )


async def api_app_capability_on(request: web.Request) -> web.Response:
    """Switch one capability of an app on: built now while the app runs."""
    return await _switch(request, True)


async def api_app_capability_off(request: web.Request) -> web.Response:
    """Switch one capability of an app off: torn down now, the app's others left running."""
    return await _switch(request, False)


def register_routes(app: web.Application) -> None:
    app.router.add_post("/api/apps/{name}/capabilities/{capability}/on", api_app_capability_on)
    app.router.add_post("/api/apps/{name}/capabilities/{capability}/off", api_app_capability_off)
