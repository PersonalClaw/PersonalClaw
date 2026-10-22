"""The buddy's policy route: ``GET /api/buddy/policy`` (#3901).

What it answers is ``personalclaw.buddy_policy``'s: the owner's buddy knobs and the notification
posture its alerts follow, in one answer every window and every machine reads. It is the owner's
alone (``apps.permissions.OWNER_ONLY_API_PATHS``): whether your notifications are muted or in quiet
hours, and whether your buddy shows, are not an app's to read. The shell reads it with its own
owner session; a change rings ``buddy_policy_changed`` on the owner's sockets.
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from personalclaw import buddy_policy


async def api_buddy_policy(request: web.Request) -> web.Response:
    """GET /api/buddy/policy — the buddy's effective policy now (``buddy_policy.policy``)."""
    return web.json_response(await asyncio.to_thread(buddy_policy.policy))


def register_buddy_routes(app: web.Application) -> None:
    app.router.add_get("/api/buddy/policy", api_buddy_policy)
