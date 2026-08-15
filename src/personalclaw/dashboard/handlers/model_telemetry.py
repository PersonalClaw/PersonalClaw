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

# The use cases routing applies to (§3.2): the NON-INTERACTIVE text axes. Interactive chat resolves
# through the native-agent branch, which bypasses the routing seam entirely (a human is watching),
# so listing it here would render a control that cannot take effect.
_ROUTED_USE_CASES = ("reasoning", "background", "loops", "orchestration")


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


async def api_routing_policy(request: web.Request) -> web.Response:
    """GET /api/models/routing-policy — the inspectable routing table (§6.1).

    One row per routed use case: its mode, its pin, the refs currently bound to it (each flagged
    ``local``), and every recorded per-class order together with the ``basis`` that decided it —
    so the user can always see WHY the table says what it says. Read-only; the writes are the PUT
    below. Fail-open: an unreadable table renders as "no opinion yet", never a 500 that blanks the
    tab, because routing being unreadable is not the same as routing being broken.
    """
    try:
        from personalclaw.providers.use_cases import active_model_refs
        from personalclaw.routing.policy import is_local_ref, master_enabled, table_for

        rows = []
        for use_case in _ROUTED_USE_CASES:
            table = table_for(use_case)
            table["candidates"] = [
                {"ref": ref, "local": is_local_ref(ref)} for ref in active_model_refs(use_case)
            ]
            rows.append(table)
        return web.json_response({"enabled": master_enabled(), "use_cases": rows})
    except Exception:  # noqa: BLE001 — an inspection view must never 500
        logger.debug("routing policy read failed", exc_info=True)
        return web.json_response({"enabled": False, "use_cases": []})


async def api_routing_policy_put(request: web.Request) -> web.Response:
    """PUT /api/models/routing-policy — set one of the three user levers (§6.2).

    Body: ``{use_case, mode?, pin?, query_class?, order?}``. Each lever is applied only when
    present, so the UI can PATCH-like a single control without echoing the rest of the table back
    (which is how a stale client silently reverts a setting it never rendered). Every accepted
    mutation is SEL-audited by the policy layer (§6.4).

    ``order`` requires ``query_class``: an order is always recorded per class, because "which model
    first" has no single answer across kinds of work — that is the whole premise of the table.
    """
    from personalclaw.providers.use_cases import VALID_USE_CASES
    from personalclaw.routing.policy import MODES, set_mode, set_order, set_pin

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad_request("a JSON body is required")
    if not isinstance(body, dict):
        return _bad_request("a JSON object is required")
    use_case = str(body.get("use_case", "") or "")
    if use_case not in VALID_USE_CASES:
        return _bad_request(f"unknown use_case {use_case!r}")

    applied: list[str] = []
    try:
        if "mode" in body:
            mode = str(body.get("mode", "") or "")
            if mode not in MODES:
                return _bad_request(f"mode must be one of {list(MODES)}")
            set_mode(use_case, mode)
            applied.append("mode")
        if "pin" in body:
            set_pin(use_case, str(body.get("pin") or ""))
            applied.append("pin")
        if "order" in body:
            order = body.get("order")
            if not isinstance(order, list) or not all(isinstance(r, str) for r in order):
                return _bad_request("order must be a list of refs")
            query_class = str(body.get("query_class", "") or "")
            if not query_class:
                return _bad_request("query_class is required when setting an order")
            set_order(use_case, query_class, order)
            applied.append("order")
    except ValueError as exc:
        return _bad_request(str(exc))
    except Exception:  # noqa: BLE001
        logger.debug("routing policy write failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not save the routing policy"}},
            status=500,
        )
    if not applied:
        return _bad_request("nothing to change: send mode, pin, and/or order")
    return web.json_response({"ok": True, "use_case": use_case, "applied": applied})


def register_model_telemetry_routes(app: web.Application) -> None:
    app.router.add_get("/api/models/telemetry", api_models_telemetry)
    app.router.add_get("/api/models/routing-policy", api_routing_policy)
    app.router.add_put("/api/models/routing-policy", api_routing_policy_put)
