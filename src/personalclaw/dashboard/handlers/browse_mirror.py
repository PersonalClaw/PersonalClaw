"""The browse mirror + kill-switch HTTP routes (BA-5).

The aiohttp surface for the browse mirror: the read model the panel polls (``GET
/api/browse/status``) and the kill switch (``POST /api/browse/kill`` + ``/release``). Owner-
authenticated by the ordinary dashboard middleware, like every other ``/api/*`` route here.

The relays these routes drive — the ``browse_step`` / ``browse_kill`` / ``browse_auth_expired`` WS
broadcasts and the expired-session surfacing — live one layer DOWN in
:mod:`personalclaw.browse.mirror`, so the domain (the action provider) can call them without
importing this HTTP module. This module imports DOWN into that one (the allowed direction);
:func:`broadcast_kill` is re-driven here only so a kill issued over HTTP updates the panel at once.
"""

from __future__ import annotations

from aiohttp import web

from personalclaw.browse.mirror import broadcast_kill
from personalclaw.http_errors import json_error


async def api_browse_status(request: web.Request) -> web.Response:
    """GET /api/browse/status — the mirror's read model: kill state + expired sites.

    One read so the panel's kill button and the persistent banner cannot show a stale pair. Values
    never cross this boundary — ``expired`` carries site slugs and a key-PRESENCE boolean, never
    the profile-encryption key itself.
    """
    from personalclaw.browse import killswitch
    from personalclaw.browse.handoff import expired_sites

    kill = killswitch.get_kill()
    return web.json_response(
        {
            "kill": {"active": kill.active, "reason": kill.reason, "started_at": kill.started_at},
            "expired": expired_sites(),
        }
    )


async def api_browse_kill(request: web.Request) -> web.Response:
    """POST /api/browse/kill — stop unattended browsing. Body: ``{reason?: str}``.

    SEL-audited in :func:`killswitch.engage`. A running loop parks within one step; a new run
    refuses to start. Interactive chat is untouched.
    """
    from personalclaw.browse import killswitch

    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = str(body.get("reason", "")) if isinstance(body, dict) else ""
    kill = killswitch.engage(reason)
    broadcast_kill(kill, state=request.app.get("state"))
    return web.json_response(
        {"kill": {"active": kill.active, "reason": kill.reason, "started_at": kill.started_at}}
    )


async def api_browse_kill_release(request: web.Request) -> web.Response:
    """POST /api/browse/kill/release — re-enable unattended browsing.

    EXPLICIT, like incident resume: requires ``{confirm: true}`` so a stray request cannot silently
    re-enable browsing a human deliberately stopped. SEL-audited.
    """
    from personalclaw.browse import killswitch

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not (isinstance(body, dict) and body.get("confirm") is True):
        return json_error(
            "confirmation_required", message='release requires {"confirm": true}', status=400
        )
    kill = killswitch.release()
    broadcast_kill(kill, state=request.app.get("state"))
    return web.json_response(
        {"kill": {"active": kill.active, "reason": kill.reason, "started_at": kill.started_at}}
    )


def register_browse_mirror_routes(app: web.Application) -> None:
    """Wire the browse mirror + kill-switch routes (owner-authenticated by the ordinary
    dashboard middleware, like the rest of ``/api/*``)."""
    app.router.add_get("/api/browse/status", api_browse_status)
    app.router.add_post("/api/browse/kill", api_browse_kill)
    app.router.add_post("/api/browse/kill/release", api_browse_kill_release)


__all__ = [
    "api_browse_status",
    "api_browse_kill",
    "api_browse_kill_release",
    "register_browse_mirror_routes",
]
