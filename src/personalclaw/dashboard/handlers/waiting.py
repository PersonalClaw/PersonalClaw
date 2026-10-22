"""The waiting list's route: ``GET /api/waiting`` (#3888).

What each item is, its order and who reads what are ``personalclaw.waiting``'s. This route adds
no authority of its own and no ``ROUTE_AUTHZ`` row: it is in no security route family, so an app
reaches it only when its manifest declares the path, and nothing is answered through it. Every
item names its own source's route, which keeps its own guards.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from personalclaw import waiting


async def api_waiting(request: web.Request) -> web.Response:
    """GET /api/waiting — everything waiting on you to answer, from every source.

    ``{"waiting": [item, ...], "unreadable": [kind, ...]}``: by consequence, then age, oldest
    first (``waiting``). A source that could not be read is named in ``unreadable``, never shown
    as nothing waiting. An app whose manifest declares the route reads all of it, without what
    answers an item; an agent reads its own chat's items.
    """
    state = request.app["state"]
    snap = waiting.taken(state)

    def off_the_loop() -> tuple[list[dict[str, Any]], list[str]]:
        items, unreadable = waiting.read(snap)
        reach = waiting.reach_of(request, state)
        return [waiting.for_reader(reach, i) for i in items if reach.sees(i)], unreadable

    items, unreadable = await asyncio.to_thread(off_the_loop)
    return web.json_response({"waiting": items, "unreadable": unreadable})


def register_waiting_routes(app: web.Application) -> None:
    """Register the waiting list's route on *app*."""
    app.router.add_get("/api/waiting", api_waiting)
