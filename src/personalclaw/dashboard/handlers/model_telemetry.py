"""Model routing-telemetry read route (MODEL-ROUTING-TELEMETRY §1.5, MRT-1d).

One GET over the routing fold + a bounded ``model_calls.jsonl`` tail: per-model efficiency rows
for a (use_case, query_class), each flagged ``on_frontier`` (not dominated on quality/latency/cost).
Read-only — this plan is observation, never a routing decision here (that's the Session-3 router).
Errors use the shared ``{error:{code,message}}`` envelope
(:func:`personalclaw.http_errors.json_error`). The Routing & Efficiency FE tab (MRT-1e)
renders this; this is the data it reads.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

# Bound the JSONL tail read for percentile derivation — recent forensic window, not the whole log.
_AUDIT_TAIL = 2000

# The use cases routing applies to (§3.2): the NON-INTERACTIVE text axes. Interactive chat resolves
# through the native-agent branch, which bypasses the routing seam entirely (a human is watching),
# so listing it here would render a control that cannot take effect.
_ROUTED_USE_CASES = ("reasoning", "background", "loops", "orchestration")


async def api_models_telemetry(request: web.Request) -> web.Response:
    """GET /api/models/telemetry?use_case=&query_class= — per-model efficiency rows.

    Both params are required (a telemetry view is always scoped to one bucket); an empty either
    is a clean 400. Returns ``{use_case, query_class, rows: [...]}`` where each row is
    ``{ref, n, success, feedback, avg_cost_usd, p50_ms, p95_ms, on_frontier}`` — the fold supplies
    the aggregates, the JSONL tail supplies p50/p95, and the frontier flag is a dominance check."""
    use_case = request.query.get("use_case", "")
    query_class = request.query.get("query_class", "")
    if not use_case:
        return json_error("bad_request", message="use_case is required", status=400)
    if not query_class:
        return json_error("bad_request", message="query_class is required", status=400)
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

    **Every lever is validated before any lever is applied.** Interleaving the two (validate mode,
    write mode, validate order, reject) made a 400 that had already moved the store: a body
    carrying a good ``mode`` and a malformed ``order`` answered ``400 order must be a list of
    refs`` with the new mode persisted, so the client saw "nothing applied" while the table had
    changed under it. ``mode``/``pin`` and ``order`` live in different stores, so there is no
    single write to make atomic — the fix is to have nothing left to reject once the first write
    goes out.
    """
    from personalclaw.providers.use_cases import VALID_USE_CASES
    from personalclaw.routing.policy import MODES, set_mode, set_order, set_pin

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return json_error("bad_request", message="a JSON body is required", status=400)
    if not isinstance(body, dict):
        return json_error("bad_request", message="a JSON object is required", status=400)
    use_case = str(body.get("use_case", "") or "")
    if use_case not in VALID_USE_CASES:
        return json_error("bad_request", message=f"unknown use_case {use_case!r}", status=400)

    # ── validate ────────────────────────────────────────────────────────────────
    mode: str | None = None
    if "mode" in body:
        mode = str(body.get("mode", "") or "")
        if mode not in MODES:
            return json_error(
                "bad_request", message=f"mode must be one of {list(MODES)}", status=400
            )
    pin: str | None = None
    if "pin" in body:
        pin = str(body.get("pin") or "")
    order: list[str] | None = None
    query_class = ""
    if "order" in body:
        raw_order = body.get("order")
        if not isinstance(raw_order, list) or not all(isinstance(r, str) for r in raw_order):
            return json_error("bad_request", message="order must be a list of refs", status=400)
        query_class = str(body.get("query_class", "") or "")
        if not query_class:
            return json_error(
                "bad_request",
                message="query_class is required when setting an order",
                status=400,
            )
        order = list(raw_order)
    if mode is None and pin is None and order is None:
        return json_error(
            "bad_request", message="nothing to change: send mode, pin, and/or order", status=400
        )

    # ── apply ───────────────────────────────────────────────────────────────────
    applied: list[str] = []
    try:
        if mode is not None:
            set_mode(use_case, mode)
            applied.append("mode")
        if pin is not None:
            set_pin(use_case, pin)
            applied.append("pin")
        if order is not None:
            set_order(use_case, query_class, order)
            applied.append("order")
    except ValueError as exc:
        return json_error("bad_request", message=str(exc), status=400)
    except Exception:  # noqa: BLE001
        logger.debug("routing policy write failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not save the routing policy"}},
            status=500,
        )
    return web.json_response({"ok": True, "use_case": use_case, "applied": applied})


async def api_routing_proposals(request: web.Request) -> web.Response:
    """GET /api/models/routing-proposals — the propose-don't-write review queue (§6.3).

    ``{count, proposals: [{...summary, evidence}]}``, oldest first. The evidence rides along on the
    list because a proposal without it is not reviewable, and there is no second round-trip worth
    saving for a queue capped at 50. ``count`` is the Routing tab's badge.

    Fail-open to an empty queue, exactly like the policy read above: an unreadable proposal store
    means "nothing to review", never a 500 that blanks the tab.
    """
    try:
        from personalclaw.routing.proposals import pending

        props = pending()
        rows = [{**p.summary(), "evidence": p.evidence} for p in props]
    except Exception:  # noqa: BLE001 — a review queue must never 500 the tab
        logger.debug("routing proposals read failed", exc_info=True)
        return web.json_response({"count": 0, "proposals": []})
    return web.json_response({"count": len(rows), "proposals": rows})


async def api_routing_proposal_accept(request: web.Request) -> web.Response:
    """POST /api/models/routing-proposals/{id}/accept — apply it to the table (§6.3).

    The table write, the ``proposal_id`` basis and the SEL row are all the policy layer's; this
    handler only turns the outcome into a response. ``accept`` returns ``False`` for two unlike
    things, so they answer differently: an id the queue does not hold pending is a **404**, while a
    REFUSAL (the cell's order was set by hand — a user decision routing may propose changing but
    never overwrite) is a **200** carrying ``applied: false`` and the recorded reason. A refusal
    is a correct answer to a legitimate request, not a client error, and the surface has to be able
    to say why rather than appearing to do nothing.
    """
    proposal_id = request.match_info.get("id", "")
    try:
        from personalclaw.routing.proposals import accept, find

        applied = accept(proposal_id)
        record = find(proposal_id)
    except Exception:  # noqa: BLE001
        logger.debug("routing proposal accept failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not accept the proposal"}},
            status=500,
        )
    if applied:
        return web.json_response({"ok": True, "applied": True, "id": proposal_id})
    if record is None:
        return json_error("not_found", message=f"no routing proposal {proposal_id!r}", status=404)
    if record.status == "refused":
        return web.json_response(
            {"ok": True, "applied": False, "id": proposal_id, "reason": record.refusal_reason}
        )
    return json_error(
        "not_found", message=f"routing proposal {proposal_id!r} is not pending", status=404
    )


async def api_routing_proposal_reject(request: web.Request) -> web.Response:
    """DELETE /api/models/routing-proposals/{id} — decline it, and remember the decision (§6.3).

    Writes NO table. The rejection suppresses the same finding for
    ``routing.reproposal_cooldown_days``, which is the proposals module's job — the shape mirrors
    ``DELETE /api/learning/proposals/{id}``, the tree's other propose-only queue, so "dismiss"
    means the same verb in both places.
    """
    proposal_id = request.match_info.get("id", "")
    try:
        from personalclaw.routing.proposals import reject

        dismissed = reject(proposal_id)
    except Exception:  # noqa: BLE001
        logger.debug("routing proposal reject failed", exc_info=True)
        return web.json_response(
            {"error": {"code": "internal", "message": "could not reject the proposal"}},
            status=500,
        )
    if not dismissed:
        return json_error(
            "not_found", message=f"routing proposal {proposal_id!r} is not pending", status=404
        )
    return web.json_response({"ok": True, "id": proposal_id})


def register_model_telemetry_routes(app: web.Application) -> None:
    app.router.add_get("/api/models/telemetry", api_models_telemetry)
    app.router.add_get("/api/models/routing-policy", api_routing_policy)
    app.router.add_put("/api/models/routing-policy", api_routing_policy_put)
    app.router.add_get("/api/models/routing-proposals", api_routing_proposals)
    app.router.add_post("/api/models/routing-proposals/{id}/accept", api_routing_proposal_accept)
    app.router.add_delete("/api/models/routing-proposals/{id}", api_routing_proposal_reject)
