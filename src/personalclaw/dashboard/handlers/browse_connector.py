"""The browse user-browser CONNECTOR endpoint — where the operator's own browser attaches.

``browse.target`` keeps the process-global "is the operator's own browser attached right now"
registry; these routes are its writer. A paired device ATTACHES here (naming no page), and then
answers each granted ``user_browser`` run that asks it for a tab of the run's own: it opens that
tab, announces the tab's page target, and reports the tab's end. A run drives that one tab and
nothing else, so a page the operator already has open is never a browse target.

* ``POST /api/browse/connector`` — attach (a paired device, no body fields).
* ``GET /api/browse/connector/tabs`` — the runs that asked the attached browser for a tab, and the
  tabs it holds for runs still going; a 409 tells a browser that is not (or no longer) attached.
* ``POST /api/browse/connector/tabs/{request_id}`` — ``{"cdp_url": ...}`` announces the run's tab
  (once; its page target never changes after), ``{"state": ...}`` reports it ``unavailable``,
  ``closed`` or ``taken_over`` (``browse.target.TAB_REPORTS``).

**Loopback only, and no new listening surface.** The connector is the operator's own browser on
the SAME machine as the gateway, so its endpoints are reached over loopback and these routes
refuse any non-loopback caller — on the raw TCP peer, never an ``X-Real-IP`` a proxy or a caller
could spoof. They mount on the gateway's EXISTING dashboard server, so no second socket is opened,
and the page target a device announces is validated against the shipped ``LOOPBACK_INTERNAL``
egress rail before it is stored — so a public ``cdp_url`` cannot be registered even by a loopback
caller. That is why registration is the structural gate: the transport's own ``connect`` is not
egress-guarded, so "the connector endpoint is loopback" has to be made true here or nowhere.

**Paired via the shipped device-session machinery, not a second one.** The announcing client is
an ordinary paired device: it holds the session cookie ``pair/complete`` minted, and the
``device_id`` handed to :func:`register_connector` is the one the pairing registry already knows
— so the attached browser IS a row in ``GET /api/devices``, listed as a connected device rather
than tracked by a parallel notion. A session with no ``device`` provenance (the owner's own
dashboard tab) is refused: only a paired device may attach as the connector, and only the device
a run asked may announce or report that run's tab.

**No browser-vendor knowledge in core.** This module names a *paired device* and a *CDP
page-target endpoint*; which browser produced that endpoint, how it groups a run's tab, and the
extension that speaks the typed local contract (navigate / read-outline / click / type / close) to
it, live entirely in the removable app bundle (apps repo). ``tests/test_browse_connector_route``
rails this module against every browser-vendor name so a future edit cannot leak one in.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from personalclaw.browse.target import (
    TAB_REPORTS,
    announce_run_tab,
    attached_device,
    clear_connector,
    connector_status,
    register_connector,
    report_run_tab,
    run_tabs_for,
)
from personalclaw.dashboard.origin import is_loopback
from personalclaw.dashboard.session_store import DeviceInfo, paired_sessions
from personalclaw.http_errors import json_error
from personalclaw.net import LOOPBACK_INTERNAL, evaluate
from personalclaw.request_validation import json_object_body

logger = logging.getLogger(__name__)

#: The endpoint a device announces is a CDP page-target WebSocket URL — the browse provider hands
#: it straight to the CDP transport's ``connect`` — so the loopback rail is the shipped
#: ``LOOPBACK_INTERNAL`` posture with only its scheme set widened from http(s) to ws(s). Every
#: other clause (loopback required, public denied, no IP pinning) is inherited unchanged: this
#: is the SAME rail consumed, not a second definition of "loopback".
_LOOPBACK_WS = LOOPBACK_INTERNAL.with_overrides(allow_schemes=("ws", "wss"))


def _sel() -> Any:
    from personalclaw.sel import sel

    return sel()


def _audit(
    operation: str,
    outcome: str,
    *,
    caller: str = "device",
    resources: str = "",
    error: str = "",
) -> None:
    """One SEL row per connector state change. Never raises — an audit failure must not eat
    the reply (the same posture as the device-pairing routes this sits beside)."""
    try:
        _sel().log_api_access(
            caller=caller,
            operation=operation,
            outcome=outcome,
            source="browse_connector",
            error=error,
            resources=resources,
        )
    except Exception:  # noqa: BLE001
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


def _require_loopback(request: web.Request, operation: str) -> web.Response | None:
    """Refuse a non-loopback caller. The connector is the operator's browser on THIS machine;
    a LAN or remote peer reaching it would be a different device entirely, so the check is on
    the raw TCP peer (``request.remote``) and not on a header a proxy or caller can set."""
    if not is_loopback(request.remote or ""):
        _audit(operation, "denied", caller=request.remote or "", error="non-loopback")
        return json_error("browse_connector_loopback_only", status=403)
    return None


def _paired_device(request: web.Request) -> DeviceInfo | None:
    """The paired-device row that authorized this request, or ``None``.

    The middleware has already validated the session and recorded its nonce; a connector
    caller must ADDITIONALLY be a paired device (a ``sessions.json`` row issued by pairing),
    so its identity in the connector registry is the same ``device_id`` pairing minted and the
    attached browser is a real ``GET /api/devices`` entry. The owner's own browser session is
    listed there too, but it was not paired, and is therefore not eligible.
    """
    nonce = str(request.get("session_nonce") or "")
    record = paired_sessions().get(nonce)
    if record is None:
        return None
    return record.device


def _connector_device(request: web.Request, operation: str) -> DeviceInfo | web.Response:
    """The paired device calling, over loopback — or the refusal to send instead."""
    denied = _require_loopback(request, operation)
    if denied is not None:
        return denied
    device = _paired_device(request)
    if device is None:
        _audit(operation, "denied", error="not a paired device session")
        return json_error("browse_connector_unpaired", status=403)
    return device


async def api_browse_connector_attach(request: web.Request) -> web.Response:
    """POST /api/browse/connector — record the operator's attached browser.

    From a paired device over loopback; the body names nothing. Attaching names no page: a page
    target is announced only for a tab the browser opened for a granted run, on
    ``POST /api/browse/connector/tabs/{request_id}``.
    """
    device = _connector_device(request, "browse_connector_attached")
    if isinstance(device, web.Response):
        return device
    await json_object_body(request)
    session = register_connector(device_id=device.id)
    _audit("browse_connector_attached", "ok", caller=device.id, resources=f"device={device.id}")
    return web.json_response({"ok": True, "device_id": session.device_id})


async def api_browse_connector_detach(request: web.Request) -> web.Response:
    """DELETE /api/browse/connector — detach the operator's browser. Idempotent."""
    device = _connector_device(request, "browse_connector_detached")
    if isinstance(device, web.Response):
        return device
    clear_connector()
    _audit("browse_connector_detached", "ok", caller=device.id, resources=f"device={device.id}")
    return web.json_response({"ok": True})


async def api_browse_connector_status(request: web.Request) -> web.Response:
    """GET /api/browse/connector — whether a browser is attached right now.

    Loopback-only like its siblings. Returns the two sentences ``ConnectorStatus`` already
    carries, so the announcing client can show the same reason/fix the browse provider would.
    """
    denied = _require_loopback(request, "browse_connector_status")
    if denied is not None:
        return denied
    status = connector_status()
    return web.json_response(
        {
            "connected": status.connected,
            "device_id": status.device_id,
            "reason": status.reason,
            "fix": status.fix,
        }
    )


async def api_browse_connector_tabs(request: web.Request) -> web.Response:
    """GET /api/browse/connector/tabs — the runs that asked this browser for a tab of their own.

    ``{"runs": [{"request_id", "group", "state"}]}``: a ``requested`` run wants a new tab, opened in
    a group named ``group``; any other state is a tab the browser holds for a run still going. A run
    that is not listed has ended. Only the attached device is answered: ``409
    browse_connector_not_attached`` when no browser is attached (attach again), ``409
    browse_connector_replaced`` when another one is (stop asking). The ``user_browser`` switch
    does not enter into it: switched off, no run asks for a tab, but the browser stays attached.
    """
    device = _connector_device(request, "browse_connector_tabs")
    if isinstance(device, web.Response):
        return device
    attached = attached_device()
    if not attached:
        return json_error("browse_connector_not_attached", status=409)
    if attached != device.id:
        return json_error("browse_connector_replaced", status=409)
    runs = [
        {"request_id": tab.request_id, "group": tab.group, "state": tab.state}
        for tab in run_tabs_for(device.id)
    ]
    return web.json_response({"runs": runs})


async def api_browse_connector_tab(request: web.Request) -> web.Response:
    """POST /api/browse/connector/tabs/{request_id} — announce or report one run's tab.

    ``{"cdp_url": "ws://127.0.0.1:.../devtools/page/..."}`` names the page target of the tab the
    browser opened for that run, once; ``{"state": "unavailable" | "closed" | "taken_over"}``
    reports it. Only the device the run asked may answer, and only while the report applies.
    """
    operation = "browse_run_tab"
    device = _connector_device(request, operation)
    if isinstance(device, web.Response):
        return device
    request_id = str(request.match_info.get("request_id") or "")
    body = await json_object_body(request)
    has_url, has_state = "cdp_url" in body, "state" in body
    if has_url == has_state:
        _audit(operation, "denied", caller=device.id, error="neither or both of cdp_url and state")
        return json_error("browse_run_tab_report_invalid", status=400)

    if has_url:
        cdp_url = str(body.get("cdp_url") or "").strip()
        decision = evaluate(cdp_url, _LOOPBACK_WS) if cdp_url else None
        if decision is None or not decision.allow:
            _audit(operation, "denied", caller=device.id, error="non-loopback endpoint")
            return json_error(
                "browse_connector_endpoint_invalid",
                status=400,
                message=(decision.reason if decision is not None else "") or None,
            )
        tab = announce_run_tab(device_id=device.id, request_id=request_id, cdp_url=cdp_url)
        what = "announced"
    else:
        state = str(body.get("state") or "")
        if state not in TAB_REPORTS:
            _audit(operation, "denied", caller=device.id, error="unknown run tab state")
            return json_error("browse_run_tab_report_invalid", status=400)
        tab = report_run_tab(device_id=device.id, request_id=request_id, state=state)
        what = state

    if tab is None:
        _audit(operation, "denied", caller=device.id, error=f"no run tab to take {what}")
        return json_error("browse_run_tab_unknown", status=404)
    _audit(
        operation,
        "ok",
        caller=device.id,
        resources=f"device={device.id}; run_tab={request_id}; {what}",
    )
    return web.json_response({"ok": True, "state": tab.state})


def register_browse_connector_routes(app: web.Application) -> None:
    """Wire the connector routes onto the EXISTING dashboard server — no new listener.

    Registered beside the device routes because the connector IS a paired device: it
    authenticates with the session ``pair/complete`` minted and is listed by the same
    registry.
    """
    app.router.add_post("/api/browse/connector", api_browse_connector_attach)
    app.router.add_delete("/api/browse/connector", api_browse_connector_detach)
    app.router.add_get("/api/browse/connector", api_browse_connector_status)
    app.router.add_get("/api/browse/connector/tabs", api_browse_connector_tabs)
    app.router.add_post("/api/browse/connector/tabs/{request_id}", api_browse_connector_tab)
