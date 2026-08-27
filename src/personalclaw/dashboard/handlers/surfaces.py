"""The L2 surface-overlay read endpoint (AMBIENT-SURFACES §6 / AS-6).

One route. It is a READ of ``$PERSONALCLAW_HOME/surfaces/`` — there is deliberately no
write endpoint: an overlay is authored by the user or by an agent through the ordinary
file tools, so adding an HTTP writer here would mint a second producer with a second set
of refusals. The loader (`personalclaw.surface_overlay`) owns the threat posture; this
handler owns nothing but the JSON.

Refusals come back on a **200** alongside the accepted overlays, the same shape the tile
action refusal uses: the FE renders "your overlay names a component that does not exist"
beside the surface it belongs to, and a 4xx would make that indistinguishable from a
broken request.
"""

from aiohttp import web


async def api_surface_overlays(request: web.Request) -> web.Response:
    """GET /api/surfaces/overlays — the user/agent (L2) overlays, plus named refusals.

    Safe mode is NOT enforced here: the ceiling is a client decision (`maxSurfaceLayer`)
    and the FE loader does not even call this route under ``maxLayer=0``. Gating the read
    as well would leave an operator in safe mode unable to see WHY their overlay was
    refused, which is the one thing they are trying to find out.
    """
    from personalclaw.surface_overlay import overlay_payload

    return web.json_response(overlay_payload())
