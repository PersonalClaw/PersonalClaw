"""Agent-routing suppression endpoints (AGENT-ROUTING S1).

The dismiss/unmute/status routes over the ``entity_settings/agent_routing.json``
suppression store. New routes use the `AGENTS.md` §"Shared conventions" error envelope
(``{"error": {"code", "message"}}``); the suggestion itself is a WS-push, not a route.
"""

from __future__ import annotations

import logging
import time

from aiohttp import web

from personalclaw.agents import routing
from personalclaw.config.loader import AppConfig

logger = logging.getLogger(__name__)


def _bad(message: str, code: str = "bad_request", status: int = 400) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


async def _agent_from_body(request: web.Request) -> tuple[str | None, web.Response | None]:
    try:
        body = await request.json()
    except Exception:
        return None, _bad("invalid JSON body")
    if not isinstance(body, dict):
        return None, _bad("body must be an object")
    agent = str(body.get("agent", "")).strip()
    if not agent:
        return None, _bad("agent is required")
    return agent, None


async def api_routing_dismiss(request: web.Request) -> web.Response:
    """POST /api/agents/routing/dismiss {agent} — bump the dismissal counter; the
    agent is muted once it reaches the mute threshold."""
    agent, err = await _agent_from_body(request)
    if err is not None:
        return err
    status = routing.record_dismiss(str(agent), now=time.time())
    return web.json_response({"ok": True, **status})


async def api_routing_unmute(request: web.Request) -> web.Response:
    """POST /api/agents/routing/unmute {agent} — clear an agent's mute + dismissals."""
    agent, err = await _agent_from_body(request)
    if err is not None:
        return err
    routing.unmute(str(agent))
    return web.json_response({"ok": True, "agent": agent})


async def api_routing_status(request: web.Request) -> web.Response:
    """GET /api/agents/routing/status — enabled flag + muted/dismissal state."""
    try:
        enabled = bool(AppConfig.load().agents_routing.enabled)
    except Exception:
        enabled = True
    return web.json_response({"enabled": enabled, **routing.routing_status()})
