"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_docker_single_container.py``

Its checks:
``tests/rails/test_docker_single_container_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

_REPO = Path(__file__).resolve().parents[1]

_APP: web.Application | None = None


def get_handler(path: str) -> Callable[..., Any] | None:
    """The handler the gateway runs for ``GET`` on *path*, or ``None`` when it serves none.

    The route table is the one ``tests/gateway_routes_shared.py`` builds, as ``start_dashboard``
    does, plus the provider routes ``start_dashboard`` registers once the extensions load
    (``providers/routes.register_routes``), which serve ``/api/providers``. A table of its own, so
    the shared one stays what every other reader of it expects. Built once per process; nothing
    serves it.
    """
    global _APP
    if _APP is None:
        from personalclaw.dashboard import server
        from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes
        from personalclaw.dashboard.routes import register_dashboard_routes
        from personalclaw.providers.routes import register_routes as register_provider_routes

        app = web.Application()
        server._register_mcp_routes(app)
        register_dashboard_routes(app)
        setup_knowledge_routes(app)
        register_provider_routes(app)
        _APP = app
    app = _APP

    async def resolve() -> Any:
        return await app.router.resolve(make_mocked_request("GET", path, app=app))

    match = asyncio.run(resolve())
    return None if getattr(match, "http_exception", None) is not None else match.handler
