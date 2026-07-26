"""Doctor HTTP surface (PLATFORM-RESILIENCE §1).

``GET /api/doctor`` runs every registered probe grouped by capability (cached 30s
so a dashboard poll can't turn the probe suite into a load source), and
``GET /api/doctor/{capability}`` re-runs just one capability's probes (uncached —
it's an explicit user re-probe of one card). Both are read-only.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from aiohttp import web

from personalclaw.resilience.doctor import DoctorContext, run_capability, run_doctor

# Full-report cache (§11 risk mitigation: 30s TTL so the dashboard rollup poll
# reuses one run instead of re-probing every capability each tick).
_DOCTOR_TTL = 30.0
_doctor_cache: Optional[dict[str, Any]] = None
_doctor_cache_ts = 0.0


def _ctx(request: web.Request) -> DoctorContext:
    """Build the probe context from the live app: the dashboard state and the port
    the gateway bound (both optional — probes degrade to read-only file access)."""
    state = request.app.get("state")
    try:
        port = int(request.app.get("port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    return DoctorContext(state=state, port=port)


async def api_doctor(request: web.Request) -> web.Response:
    """GET /api/doctor — all probes, grouped by capability, cached 30s."""
    global _doctor_cache, _doctor_cache_ts
    now = time.monotonic()
    if _doctor_cache is not None and now - _doctor_cache_ts < _DOCTOR_TTL:
        return web.json_response(_doctor_cache)
    report = await run_doctor(_ctx(request))
    _doctor_cache = report
    _doctor_cache_ts = now
    return web.json_response(report)


async def api_doctor_capability(request: web.Request) -> web.Response:
    """GET /api/doctor/{capability} — re-run one capability's probes (uncached)."""
    capability = request.match_info.get("capability", "")
    result = await run_capability(capability, _ctx(request))
    if result.get("unknown"):
        return web.json_response(
            {
                "error": {
                    "code": "unknown_capability",
                    "message": f"no such capability: {capability}",
                }
            },
            status=404,
        )
    return web.json_response(result)
