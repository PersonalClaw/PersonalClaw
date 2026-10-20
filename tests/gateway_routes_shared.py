"""The gateway's real route table, for tests that ask which handler serves a method and path.

A test that wants to know whether the gateway serves a route asks the router the gateway builds,
not the text of the module that registers it: a refactor that moves a registration keeps the
route, and a route left unregistered no longer resolves. The table is built as ``start_dashboard``
builds its routes (``dashboard/server.py``): the MCP-tool routes, the dashboard's route table
(``dashboard/routes.register_dashboard_routes``) and the knowledge routes. It is built once per
process; nothing serves it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

_APP: web.Application | None = None


def gateway_app() -> web.Application:
    """The gateway's routes on an app nothing serves, built once per process."""
    global _APP
    if _APP is None:
        from personalclaw.dashboard import server
        from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes
        from personalclaw.dashboard.routes import register_dashboard_routes

        app = web.Application()
        server._register_mcp_routes(app)
        register_dashboard_routes(app)
        setup_knowledge_routes(app)
        _APP = app
    return _APP


def handler_for(method: str, path: str) -> Callable[..., Any] | None:
    """The handler the gateway runs for *method* on *path*, or ``None`` when it serves none
    (a 404, or a 405 for a path it serves under another method)."""
    app = gateway_app()

    async def resolve() -> Any:
        return await app.router.resolve(make_mocked_request(method, path, app=app))

    match = asyncio.run(resolve())
    return None if getattr(match, "http_exception", None) is not None else match.handler


def registrations(method: str, path: str) -> int:
    """How many routes the table holds for *method* on the exact template *path*."""
    return sum(
        1
        for route in gateway_app().router.routes()
        if route.method == method
        and route.resource is not None
        and route.resource.canonical == path
    )
