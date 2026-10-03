"""The callbacks the agent registers, on the Triggers page (`webhook_callbacks`).

A callback is listed beside the other triggers as the ``callback`` kind (``GET /api/triggers``), and
the page's routes act on it through here: its switch is the owner's yes to the context it holds, so
switching it on is Allow, which asks first, and there is no editor — the agent that registered it
owns its context.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from personalclaw.dashboard.state import DashboardState

#: The kind a callback is listed and addressed as (``callback:<id>``).
KIND = "callback"


def serialize(callback: Any) -> dict[str, Any]:
    """A callback as the Triggers page lists it.

    Its switch is the owner's yes: ``enabled`` is whether they allowed it with the context it has
    now, so a callback the agent registered, or re-registered with other context, is off and
    ``needs_grant`` until they switch it on — which asks, like any trigger's Allow. The context is
    the agent's text, shown redacted and as text: it is what the turn starts from, so it is what the
    owner reads before allowing it.
    """
    from personalclaw import webhook_callbacks
    from personalclaw.dashboard.handlers.triggers import _redact
    from personalclaw.owner_grants import seal

    allowed = webhook_callbacks.allowed(callback)
    return {
        "kind": KIND,
        "id": f"{KIND}:{callback.id}",
        "raw_id": callback.id,
        "name": callback.id,
        "enabled": allowed,
        "created_by": "agent",
        "needs_grant": [] if allowed else [webhook_callbacks.RUNS_LABEL],
        "context_summary": _redact(callback.context_summary),
        "session_key": callback.session_key,
        "registered_at": callback.registered_at,
        # What switching it on sends back, so the yes is to this context (`toggle`).
        "seal": seal(callback.context_summary),
    }


def delete(request: web.Request, state: DashboardState, raw: str) -> web.Response:
    """DELETE /api/triggers/callback:{id} — forget the callback, and the owner's yes with it."""
    from personalclaw import webhook_callbacks
    from personalclaw.dashboard.handlers.triggers import _sel

    if not webhook_callbacks.remove(raw):
        return web.json_response({"error": "not found"}, status=404)
    state.push_refresh("crons")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.delete",
        outcome="success",
        source="dashboard",
        resources=f"trigger:{KIND}:{raw}",
    )
    return web.json_response({"ok": True})


async def toggle(request: web.Request, state: DashboardState, raw: str) -> web.Response:
    """POST /api/triggers/callback:{id}/toggle — on (the owner's Allow, asked first) or off (the
    yes taken back).

    Switching one on must name the context the owner read (``seal``, from the listed row): the
    agent can re-register a callback with other context at any moment, and a yes given to the
    context on the page must not become a yes to one the page never showed (409 when it moved).
    Both answers are written to the security audit, like every grant (`triggers._audit_grant`).
    """
    from personalclaw import webhook_callbacks
    from personalclaw.dashboard.handlers.triggers import _audit_grant
    from personalclaw.http_errors import consent_required, json_error
    from personalclaw.owner_grants import seal
    from personalclaw.request_validation import bool_field, json_object_body
    from personalclaw.safety_flags import confirm_granted

    callback = webhook_callbacks.get(raw)
    if callback is None:
        return web.json_response({"error": "not found"}, status=404)
    body = await json_object_body(request)
    want = bool_field(body, "enabled", default=None)
    allowed = webhook_callbacks.allowed(callback)
    on = (not allowed) if want is None else want
    caller = request.get("user", "dashboard")
    if on and not allowed:
        if body.get("seal") != seal(callback.context_summary):
            return json_error(
                "stale_write",
                message=(
                    f"“{callback.id}” was registered again with other context since this page "
                    "read it. Look at it again before allowing it."
                ),
                status=409,
            )
        field = f"triggers.{KIND}:{raw}.allowed"
        if not confirm_granted(body):
            _audit_grant(caller, "denied", f"{field}: switching on without confirm")
            return consent_required(
                field, webhook_callbacks.consent(callback), title=webhook_callbacks.CONSENT_TITLE
            )
        webhook_callbacks.allow(callback)
        _audit_grant(caller, "success", f"trigger:{KIND}:{raw}")
    elif not on and allowed:
        webhook_callbacks.disallow(callback)
        _audit_grant(caller, "success", f"trigger:{KIND}:{raw}: taken back")
    state.push_refresh("crons")
    return web.json_response({"ok": True, "trigger": serialize(callback)})
