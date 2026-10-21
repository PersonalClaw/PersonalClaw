"""The running list's routes: ``GET /api/running`` and ``GET /api/running/count`` (#3889).

What each row is, what is live and who reads what are ``personalclaw.running_now``'s. These routes
add no authority of their own and no ``ROUTE_AUTHZ`` row: they are in no security route family, so
an app reaches them only when its manifest declares the path, and then reads only the rows its
socket would hear the phase of.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from personalclaw import running_now


async def _read(request: web.Request) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The live rows and the just-finished tail *request* reads: the gateway's memory taken on the
    event loop, and the stores, the rows and who reads them read off it."""
    state = request.app["state"]
    snap = running_now.taken(state)

    def off_the_loop() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows, tail = running_now.read(snap)
        reach = running_now.reach_of(request, state)
        return [r for r in rows if reach.sees(r)], [r for r in tail if reach.sees(r)]

    return await asyncio.to_thread(off_the_loop)


async def api_running(request: web.Request) -> web.Response:
    """GET /api/running — everything running now, with what each is doing, and what just finished.

    ``{"running": [row, ...], "finished": [row, ...]}``: every piece of live work once, oldest
    first, and the endings of the last ten minutes, newest first (``running_now``). An app reads
    the rows its socket would hear the phase of, an agent its own chat's.
    """
    rows, tail = await _read(request)
    return web.json_response({"running": rows, "finished": tail})


async def api_running_count(request: web.Request) -> web.Response:
    """GET /api/running/count — how much is running, blocked, waiting on you and just finished.

    ``{"live", "blocked", "waiting", "finished"}``, counted over the rows the caller reads, from the
    same reading as the list and reading no loop's findings.
    """
    rows, tail = await _read(request)
    count = running_now.counted(rows, tail)
    return web.json_response(
        {
            "live": count.live,
            "blocked": count.blocked,
            "waiting": count.waiting,
            "finished": count.finished,
        }
    )


def register_running_routes(app: web.Application) -> None:
    """Register the running list's routes on *app*."""
    app.router.add_get("/api/running", api_running)
    app.router.add_get("/api/running/count", api_running_count)
