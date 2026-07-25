"""Legibility endpoints — the Discover section + hub's data (Platform-Legibility §6).

``GET /api/legibility/discover`` returns the curated Discover tips still worth showing
(the hand-authored catalog minus dismissed tips and minus areas the user has already
engaged), grouped by area, or ``enabled: false`` when the ``legibility.discover_tips``
kill switch is off. ``POST /api/legibility/discover/dismiss`` persists a per-tip
dismissal so it never resurfaces. Propose-don't-write: neither endpoint ever enables
or configures anything on the user's behalf.
"""

import logging

from aiohttp import web

from personalclaw.legibility.discover import compute_discover, dismiss

logger = logging.getLogger(__name__)


async def api_discover(request: web.Request) -> web.Response:
    """GET /api/legibility/discover — the curated Discover tips still worth showing.

    Takes the hand-authored catalog, drops tips the user dismissed and areas they've
    already engaged (a cheap read of existing state), and returns the rest grouped by
    area. Never mutates state.
    """
    return web.json_response(compute_discover(request.app["state"]))


async def api_discover_dismiss(request: web.Request) -> web.Response:
    """POST /api/legibility/discover/dismiss — hide a Discover tip forever.

    Body: ``{"id": "<tip-id>"}``. Persists the dismissal in
    ``entity_settings/legibility.json`` and echoes the full dismissed set.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)
    tip_id = str(body.get("id", "")).strip()
    if not tip_id:
        return web.json_response({"error": "id is required"}, status=400)
    ids = dismiss(tip_id)
    return web.json_response({"ok": True, "dismissed": sorted(ids)})
