"""The earned-autonomy ladder over HTTP (AUTONOMY-GUARDRAILS §6.1).

Four endpoints, and the asymmetry between them IS the safety property:

``GET  /api/autonomy``          the inventory + the derived proposals (reads only)
``POST /api/autonomy/grant``    the click that promotes ONE type by ONE step
``POST /api/autonomy/demote``   give a type's autonomy back (always allowed)
``POST /api/autonomy/undo``     reverse one automatic action AND demote its type

**Promotion is a click and nothing else.** The GET reports that a type has earned its next
rung; only this POST grants it, and the decision belongs to
:func:`~personalclaw.guardrails.autonomy.grant_rung` — registration, ladder membership, the
declared ceiling, the demotion cooldown and "is this actually an increase" are its checks,
called, not re-implemented here. A client-supplied rung is therefore never trusted: it is
an ASK that ``grant_rung`` validates and can refuse, and the refusal is explained by
``ladder.explain_refused_grant`` AFTER the fact rather than by a second gate that could
drift from the first.

The evidence string stored on the grant is the SERVER's derived record, never text from the
body: an audit row is worth exactly as much as its provenance.

**Demotion needs no confirmation and undo needs no rung.** Both only ever reduce autonomy,
so they are the safe direction — the same reasoning that lets ``POST /api/incident``
activate without a confirm while resuming requires one.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


async def api_autonomy(request: web.Request) -> web.Response:
    """GET /api/autonomy — every governed action type, its rung, and what it has earned.

    Off the event loop: the derived track record reads the SEL tail once per declared type,
    which is real file work and must not stall the gateway.
    """
    from personalclaw.guardrails import ladder
    from personalclaw.guardrails.rungs import RUNG_HINTS, RUNG_LABELS

    view = await asyncio.to_thread(ladder.ladder_view)
    # The rung vocabulary travels WITH the data: the frontend renders a chip per rung and
    # must not carry its own copy of the wording (a rung called "runs on its own" in a chip
    # and "autonomous" in the proposal that offered it is two names for one permission).
    view["rung_meta"] = [
        {"key": r, "label": RUNG_LABELS.get(r, r), "hint": RUNG_HINTS.get(r, "")}
        for r in view.get("rungs", [])
    ]
    return web.json_response(view)


async def api_autonomy_grant(request: web.Request) -> web.Response:
    """POST /api/autonomy/grant — the promotion click. Body ``{key, rung}``.

    Returns 400 with a named reason when the grant is refused, so a refusal is never
    indistinguishable from a promotion that happened.
    """
    from personalclaw.guardrails import ladder
    from personalclaw.guardrails.autonomy import grant_rung, promotion_eligibility
    from personalclaw.guardrails.rungs import ensure_core_action_types

    body = await _body(request)
    key = str(body.get("key", "") or "").strip()
    rung = str(body.get("rung", "") or "").strip()
    if not key or not rung:
        return web.json_response({"error": "key and rung are required"}, status=400)

    def _grant() -> tuple[str | None, str]:
        # Core declarations first: a grant for a type this process has not registered would
        # be refused as "not a registered action type" purely because of which surface the
        # request happened to hit.
        ensure_core_action_types()
        # The record the user was shown, recomputed server-side. Never the body's text.
        evidence = promotion_eligibility(key).reason
        granted = grant_rung(key, rung, evidence_window=evidence)
        if granted is None:
            return None, ladder.explain_refused_grant(key, rung)
        return granted, evidence

    granted, detail = await asyncio.to_thread(_grant)
    if granted is None:
        return web.json_response({"ok": False, "error": detail}, status=400)
    return web.json_response({"ok": True, "key": key, "rung": granted, "evidence": detail})


async def api_autonomy_demote(request: web.Request) -> web.Response:
    """POST /api/autonomy/demote — hand a type's autonomy back. Body ``{key}``.

    Always permitted, for any declared type, granted or not: this only ever REMOVES
    autonomy, and it starts the same cooldown an automatic demotion does — so a user who
    changes their mind is not immediately re-offered the promotion they just withdrew.
    """
    from personalclaw.guardrails.autonomy import action_type, demote
    from personalclaw.guardrails.rungs import ensure_core_action_types

    body = await _body(request)
    key = str(body.get("key", "") or "").strip()
    if not key:
        return web.json_response({"error": "key is required"}, status=400)

    def _demote() -> dict | None:
        ensure_core_action_types()
        if action_type(key) is None:
            return None
        record = demote(key, "you handed this action's autonomy back")
        return {"ok": True, "key": key, "cooldown_until": record.cooldown_until}

    result = await asyncio.to_thread(_demote)
    if result is None:
        return web.json_response({"error": f"{key} is not a registered action type"}, status=404)
    return web.json_response(result)


async def api_autonomy_undo(request: web.Request) -> web.Response:
    """POST /api/autonomy/undo — reverse one automatic action. Body ``{id}``.

    ``id`` is a RECORD id from ``GET /api/autonomy`` (or a notification's ``reversal_id``),
    never a reversal handle: the handle is read out of our own persisted state, so a request
    can only ask to undo something this machine actually did and told the user about.

    A successful undo also demotes the action type — the response says so, because "your
    automation will stop doing this by itself" is not a detail to discover later.
    """
    from personalclaw.guardrails.ladder import reverse_action

    body = await _body(request)
    record_id = str(body.get("id", "") or "").strip()
    if not record_id:
        return web.json_response({"error": "id is required"}, status=400)
    outcome = await reverse_action(record_id)
    payload = {
        "ok": outcome.ok,
        "code": outcome.code,
        "action_type": outcome.action_type,
        "demoted": outcome.demoted,
    }
    if outcome.ok:
        payload["detail"] = outcome.reason
        return web.json_response(payload)
    payload["error"] = outcome.reason
    return web.json_response(payload, status=404 if outcome.code == "unknown_record" else 400)
