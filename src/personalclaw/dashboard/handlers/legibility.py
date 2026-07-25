"""Legibility endpoints — the dashboard power-ups widget's data (Platform-Legibility §6).

``GET /api/legibility/power-ups`` returns the next untouched capability to propose
(or ``null`` when the user has explored everything, or ``enabled: false`` when the
``legibility.power_ups`` kill switch is off). ``POST /api/legibility/power-ups/dismiss``
persists a per-capability dismissal so it never resurfaces. Propose-don't-write:
neither endpoint ever enables or configures anything on the user's behalf.
"""

import logging

from aiohttp import web

from personalclaw.legibility.power_ups import compute_power_up, dismiss, load_dismissed

logger = logging.getLogger(__name__)


async def api_power_ups(request: web.Request) -> web.Response:
    """GET /api/legibility/power-ups — the next untouched-capability proposal.

    Reads the live tool surface (the §1 manifest) as the denominator, subtracts
    the tools the user has invoked and the capabilities they've dismissed, and
    returns one deterministic mini-lesson. Never mutates state.
    """
    return web.json_response(await compute_power_up())


async def api_power_ups_dismiss(request: web.Request) -> web.Response:
    """POST /api/legibility/power-ups/dismiss — hide a capability forever.

    Body: ``{"id": "tool:<name>"}``. Persists the dismissal in
    ``entity_settings/legibility.json`` and echoes the full dismissed set.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)
    power_up_id = str(body.get("id", "")).strip()
    if not power_up_id:
        return web.json_response({"error": "id is required"}, status=400)
    ids = dismiss(power_up_id)
    return web.json_response({"ok": True, "dismissed": sorted(ids)})


async def api_power_ups_dismissed(request: web.Request) -> web.Response:
    """GET /api/legibility/power-ups/dismissed — the set of dismissed ids."""
    return web.json_response({"dismissed": sorted(load_dismissed())})
