"""Push subscription routes — MOBILE-COMPANION `MC-5` (S3 T3.2) + `MC-9` (S4 T4.3).

Five routes, all owner-authenticated by the ordinary middleware (nothing here is added to
``_BYPASS_PREFIXES`` — a subscription endpoint reachable without a session would let anyone
who can reach the gateway register a destination for its pings):

* ``GET  /api/push`` — what the browser needs to subscribe (the VAPID public key) and what
  is already subscribed. Never returns the private key; :func:`personalclaw.push.push_status`
  is the only shape this route serves.
* ``POST /api/push/subscribe`` — store one W3C ``PushSubscription`` per device id, AND route
  ``approval/requested`` to the ``push`` target for a user who has never set that rule (see
  :func:`personalclaw.notification_rules.ensure_target` — this is why the phone's "Turn on
  push" is one switch rather than two).
* ``POST /api/push/unsubscribe`` — drop it.
* ``POST /api/push/relay-register`` — store one vendor push-service device token per device id for
  the ``relay`` backend (MC-9), with the same approval-rule tap as subscribe. The
  Capacitor shell calls this after native push registration.
* ``POST /api/push/relay-unregister`` — drop it.

The device id names a BROWSER PROFILE, not a paired device, and is minted client-side
(``web/src/app/pushClient.ts``). That is not laziness: a push subscription belongs to a
browser instance, so one phone running both the installed PWA and Safari holds two
subscriptions and a single paired-device id could not name them apart. It is not a
credential either — the session cookie authenticates this call; the id only says which row
to replace when a browser re-subscribes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from personalclaw import push
from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

ERR_INVALID = "push_subscription_invalid"
ERR_NOT_SUBSCRIBED = "push_not_subscribed"
ERR_RELAY_INVALID = "push_relay_registration_invalid"
ERR_NOT_REGISTERED = "push_relay_not_registered"

#: A W3C ``PushSubscription``'s endpoint. Bounded so a hostile body cannot make the
#: subscriptions file arbitrarily large — the endpoints browsers actually mint are ~200 chars.
_MAX_ENDPOINT = 1024
_MAX_DEVICE_ID = 128


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _audit(operation: str, outcome: str, *, resources: str = "") -> None:
    """One SEL line per outcome. Carries the device id, never the endpoint.

    The endpoint is a bearer capability: anyone holding it can send this user's phone a
    push. Logging it would put that capability in every log shipper downstream.
    """
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="owner", operation=operation, outcome=outcome, resources=resources
        )
    except Exception:
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


async def api_push_status(request: web.Request) -> web.Response:
    """The subscribe-side facts: backend, VAPID public key, which devices are subscribed."""
    # Spelled out as a literal rather than forwarding `push_status()` wholesale. Two reasons,
    # and the wire-error census (`test_unclassifiable_payloads_do_not_grow`) asks for the
    # first: a response body that is an opaque call cannot be read statically, so nobody —
    # reviewer or scanner — can see what this route serves. The second is an ALLOWLIST: a
    # field added to `push_status()` for the CLI's benefit cannot become public by accident,
    # which matters on the one route in this module that touches key material.
    status = push.push_status()
    return web.json_response(
        {
            "backend": status["backend"],
            "vapid_public_key": status["vapid_public_key"],
            "vapid_ready": status["vapid_ready"],
            "ntfy_configured": status["ntfy_configured"],
            "relay_configured": status["relay_configured"],
            "relay_devices": status["relay_devices"],
            "approval_targeted": status["approval_targeted"],
            "devices": status["devices"],
            "subscribed": status["subscribed"],
        }
    )


async def api_push_subscribe(request: web.Request) -> web.Response:
    """store one device's W3C push subscription, and route approvals to it."""
    body = await _body(request)
    device_id = str(body.get("device_id") or "").strip()[:_MAX_DEVICE_ID]
    subscription = body.get("subscription")
    if not device_id or not isinstance(subscription, dict):
        _audit("push_subscribe", "denied", resources="missing device_id or subscription")
        return json_error(ERR_INVALID, status=400)
    if len(str(subscription.get("endpoint") or "")) > _MAX_ENDPOINT:
        _audit("push_subscribe", "denied", resources=f"device={device_id} endpoint too long")
        return json_error(ERR_INVALID, status=400)
    try:
        push.subscribe(device_id, subscription)
    except ValueError as exc:
        # The message names the missing field, which is a client-programming fact, not a
        # user secret — so it is safe to return and genuinely useful in a console.
        _audit("push_subscribe", "denied", resources=f"device={device_id}")
        return json_error(ERR_INVALID, message=str(exc), status=400)
    # Turning push on for a device IS the statement "wake me for a blocked run", so this tap
    # configures plan 42's rule rather than leaving the user a second switch to find. Only
    # when they have never set that rule — see `ensure_target` on why this is neither a
    # default nor an override.
    routed = False
    try:
        from personalclaw import notification_rules

        routed = notification_rules.ensure_target("approval", "requested", "push")
    except Exception:
        logger.warning("could not route approvals to the push target", exc_info=True)
    _audit("push_subscribe", "ok", resources=f"device={device_id} approval_rule_written={routed}")
    return web.json_response({"ok": True, "device_id": device_id, "approval_rule_written": routed})


async def api_push_unsubscribe(request: web.Request) -> web.Response:
    """drop one device's push subscription."""
    body = await _body(request)
    device_id = str(body.get("device_id") or "").strip()[:_MAX_DEVICE_ID]
    if not device_id or not push.unsubscribe(device_id):
        _audit("push_unsubscribe", "denied", resources=f"device={device_id}")
        return json_error(ERR_NOT_SUBSCRIBED, status=404)
    _audit("push_unsubscribe", "ok", resources=f"device={device_id}")
    return web.json_response({"ok": True})


async def api_push_relay_register(request: web.Request) -> web.Response:
    """Store one device's vendor push-service token for the relay backend, and route approvals.

    The token is a capability against the vendor push service, so the audit line carries
    the device id and platform only — the same reasoning that keeps webpush endpoints
    out of SEL lines (see :func:`_audit`).
    """
    body = await _body(request)
    device_id = str(body.get("device_id") or "").strip()[:_MAX_DEVICE_ID]
    platform = str(body.get("platform") or "").strip().lower()
    token = str(body.get("token") or "").strip()[:_MAX_ENDPOINT]
    if not device_id or not token:
        _audit("push_relay_register", "denied", resources="missing device_id or token")
        return json_error(ERR_RELAY_INVALID, status=400)
    try:
        push.register_relay_token(device_id, platform, token)
    except ValueError as exc:
        # Names the bad field/vocabulary — a client-programming fact, not a secret.
        _audit("push_relay_register", "denied", resources=f"device={device_id} platform={platform}")
        return json_error(ERR_RELAY_INVALID, message=str(exc), status=400)
    # The same one-switch tap as subscribe: turning push on IS "wake me for a blocked run".
    routed = False
    try:
        from personalclaw import notification_rules

        routed = notification_rules.ensure_target("approval", "requested", "push")
    except Exception:
        logger.warning("could not route approvals to the push target", exc_info=True)
    _audit(
        "push_relay_register",
        "ok",
        resources=f"device={device_id} platform={platform} approval_rule_written={routed}",
    )
    return web.json_response({"ok": True, "device_id": device_id, "approval_rule_written": routed})


async def api_push_relay_unregister(request: web.Request) -> web.Response:
    """Drop one device's relay token."""
    body = await _body(request)
    device_id = str(body.get("device_id") or "").strip()[:_MAX_DEVICE_ID]
    if not device_id or not push.unregister_relay_token(device_id):
        _audit("push_relay_unregister", "denied", resources=f"device={device_id}")
        return json_error(ERR_NOT_REGISTERED, status=404)
    _audit("push_relay_unregister", "ok", resources=f"device={device_id}")
    return web.json_response({"ok": True})


def register_push_routes(app: web.Application) -> None:
    """Wire the five push routes."""
    app.router.add_get("/api/push", api_push_status)
    app.router.add_post("/api/push/subscribe", api_push_subscribe)
    app.router.add_post("/api/push/unsubscribe", api_push_unsubscribe)
    app.router.add_post("/api/push/relay-register", api_push_relay_register)
    app.router.add_post("/api/push/relay-unregister", api_push_relay_unregister)
