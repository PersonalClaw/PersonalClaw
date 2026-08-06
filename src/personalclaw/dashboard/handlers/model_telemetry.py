"""Model routing-telemetry read route (MODEL-ROUTING-TELEMETRY §1.5, MRT-1d).

One GET over the routing fold + a bounded ``model_calls.jsonl`` tail: per-model efficiency rows
for a (use_case, query_class), each flagged ``on_frontier`` (not dominated on quality/latency/cost).
Read-only — this plan is observation, never a routing decision here (that's the Session-3 router).
Errors use the §2.2 ``{error:{code,message}}`` envelope. The Routing & Efficiency FE tab (MRT-1e)
renders this; this is the data it reads.
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)

# Bound the JSONL tail read for percentile derivation — recent forensic window, not the whole log.
_AUDIT_TAIL = 2000


def _bad_request(message: str) -> web.Response:
    return web.json_response({"error": {"code": "bad_request", "message": message}}, status=400)


async def api_models_telemetry(request: web.Request) -> web.Response:
    """GET /api/models/telemetry?use_case=&query_class= — per-model efficiency rows.

    Both params are required (a telemetry view is always scoped to one bucket); an empty either
    is a clean 400. Returns ``{use_case, query_class, rows: [...]}`` where each row is
    ``{ref, n, success, feedback, avg_cost_usd, p50_ms, p95_ms, on_frontier}`` — the fold supplies
    the aggregates, the JSONL tail supplies p50/p95, and the frontier flag is a dominance check."""
    use_case = request.query.get("use_case", "")
    query_class = request.query.get("query_class", "")
    if not use_case:
        return _bad_request("use_case is required")
    if not query_class:
        return _bad_request("query_class is required")
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.guardrails.audit import read_recent
        from personalclaw.routing.stats import load_stats
        from personalclaw.routing.telemetry import telemetry_rows

        stats = load_stats(config_dir())
        audit_rows = read_recent(limit=_AUDIT_TAIL)
        rows = telemetry_rows(stats, audit_rows, use_case, query_class)
    except Exception:  # noqa: BLE001 — a read-only telemetry view must never 500
        logger.debug("model telemetry read failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not read routing telemetry"}},
            status=500,
        )
    return web.json_response({"use_case": use_case, "query_class": query_class, "rows": rows})


def register_model_telemetry_routes(app: web.Application) -> None:
    app.router.add_get("/api/models/telemetry", api_models_telemetry)
