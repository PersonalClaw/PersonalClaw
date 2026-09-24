"""Companion-app routes (COMPANION-APPS S2) — what LAN discovery is actually doing.

One read route. It exists because ``companion.discovery_enabled`` is a toggle whose effect
is invisible from the machine you set it on: a gateway bound to loopback advertises nothing
by design (COMPANION-APPS C3), so "on" and "announcing" are genuinely different states. A
settings panel that showed only the stored flag would render that difference as success.

It also returns the TXT record verbatim. Showing the owner the exact bytes their network
receives is the cheapest possible answer to "what did I just publish about myself" — and it
makes the no-token property checkable by the person who cares, not just by a test.
"""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def api_companion_discovery(request: web.Request) -> web.Response:
    """GET /api/companion/discovery — the live state of the LAN advertiser.

    ``{advertising, reason, detail, service_type, instance_name, port, addresses, txt}``.
    ``reason`` is a stable snake code from a closed set (``advertising``, ``disabled``,
    ``loopback_only``, ``no_lan_address``, ``gateway_not_running``); ``detail`` is the
    sentence to show a user, sent from here so the frontend never has to invent a second
    wording for a state it does not own.
    """
    from personalclaw.companion import discovery

    return web.json_response(discovery.status())
