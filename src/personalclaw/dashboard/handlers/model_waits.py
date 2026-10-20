"""The requests you are waiting for while a local model is busy (``guardrails.local_queue``).

``GET /api/models/waits`` lists each one: what is waiting, on which model, what that model is busy
with, which model it moves on to and in how long. A page that is waiting shows it, and re-reads the
list on each ``refresh`` frame naming ``model_waits``, which the queue sends whenever a wait starts
or ends. ``POST /api/models/waits/{id}/move-on`` stops a wait now, so the next model answers.

Both are the owner's (``apps.permissions.ROUTE_AUTHZ``): a wait names what your chats and pages
are doing, and moving one on sends its prompt to another model.
"""

from __future__ import annotations

from aiohttp import web

from personalclaw.guardrails import local_queue
from personalclaw.http_errors import json_error

#: The ``refresh`` kind a page re-reads the waits on.
REFRESH_KIND = "model_waits"


def _view(row: dict) -> dict:
    """One wait as a page reads it: the chat it is in by its dashboard name."""
    return {**row, "session": str(row.get("session") or "").removeprefix("dashboard:")}


async def api_model_waits(request: web.Request) -> web.Response:
    """GET /api/models/waits — the requests waiting for a busy local model, oldest first.

    ``{"waits": [...]}``: each with what waits, on which model, what that model is busy with,
    which model is asked next and in how long (``guardrails.local_queue.waits``)."""
    return web.json_response({"waits": [_view(row) for row in local_queue.waits()]})


async def api_model_wait_move_on(request: web.Request) -> web.Response:
    """POST /api/models/waits/{id}/move-on — stop a wait now, so its next model answers."""
    wait_id = request.match_info["id"]
    if not local_queue.move_on(wait_id):
        return json_error(
            "not_found",
            message="That request is no longer waiting, or has no other model to move on to.",
            status=404,
        )
    return web.json_response({"ok": True})


def register_model_waits_routes(app: web.Application) -> None:
    app.router.add_get("/api/models/waits", api_model_waits)
    app.router.add_post("/api/models/waits/{id}/move-on", api_model_wait_move_on)

    # Every open page hears when a wait starts or ends, from whichever thread the call waits on
    # (a broadcast is safe from any thread). Unsubscribed on stop, so a later gateway in the same
    # process is the one told.
    def _announce() -> None:
        state = app.get("state")
        if state is not None:
            state.push_refresh(REFRESH_KIND)

    async def _subscribe(_app: web.Application) -> None:
        local_queue.subscribe(_announce)

    async def _unsubscribe(_app: web.Application) -> None:
        local_queue.unsubscribe(_announce)

    app.on_startup.append(_subscribe)
    app.on_cleanup.append(_unsubscribe)
