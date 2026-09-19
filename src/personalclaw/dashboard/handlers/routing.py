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
from personalclaw.request_validation import json_object_body, require_string

logger = logging.getLogger(__name__)

# `_agent_from_body` + its private `_bad` emitter used to live here. Both are gone: the
# shared reader answers the malformed-body half and `require_string` the missing-`agent`
# half, so the two routes below read the same two lines and no longer carry a
# `(value, response)` tuple whose error slot each caller had to remember to check.


async def api_routing_dismiss(request: web.Request) -> web.Response:
    """POST /api/agents/routing/dismiss {agent} — bump the dismissal counter; the
    agent is muted once it reaches the mute threshold."""
    agent = require_string(await json_object_body(request), "agent")
    status = routing.record_dismiss(agent, now=time.time())
    return web.json_response({"ok": True, **status})


async def api_routing_unmute(request: web.Request) -> web.Response:
    """POST /api/agents/routing/unmute {agent} — clear an agent's mute + dismissals."""
    agent = require_string(await json_object_body(request), "agent")
    routing.unmute(agent)
    return web.json_response({"ok": True, "agent": agent})


async def api_routing_status(request: web.Request) -> web.Response:
    """GET /api/agents/routing/status — enabled flag + muted/dismissal state."""
    try:
        enabled = bool(AppConfig.load().agents_routing.enabled)
    except Exception:
        enabled = True
    return web.json_response({"enabled": enabled, **routing.routing_status()})
