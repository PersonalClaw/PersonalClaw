"""HTTP API for the onboarding local-model zero-key on-ramp — OU-13.

Three routes under ``/api/onboarding/local-model``, all credential-free:

``GET /api/onboarding/local-model``
    Is a local Ollama reachable on ``localhost``? A loopback round-trip, not a scan,
    so the wizard may call it automatically. Answers ``{"detected": bool, endpoint?,
    model?}``.

``POST /api/onboarding/local-model/scan``
    The OPT-IN LAN sweep. It exists as a ``POST`` the wizard fires from an explicit
    button precisely so nothing scans the network on first boot: there is no code
    path that runs the sweep without this call. Answers ``{"endpoints": [{endpoint,
    model}, …]}`` — only endpoints that answered ``/api/tags`` live, only on private
    addresses. A scan fault surfaces NO endpoints (fail closed) rather than a
    partial/guessed result, and is logged.

``POST /api/onboarding/local-model/bind``
    One-click bind of a discovered endpoint. Delegates to
    :func:`personalclaw.seed_local_model.bind_local_model`, so the credential-free
    contract (no ``api_key`` in ``config.json``) is the seed path's, not a second
    copy. The endpoint is re-validated as loopback/RFC-1918 before binding, so the
    route cannot be driven to bind an arbitrary (public/SSRF) URL.

Both discovery calls run their synchronous socket work through
:func:`asyncio.to_thread` — a first-run probe must not stall the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import urlparse

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def _caller(request: web.Request) -> str:
    """The acting identity for the audit log — an app token, else the owner."""
    app_name = request.get("app", "")
    return f"app:{app_name}" if app_name else "owner"


def _endpoint_is_local(endpoint: str) -> bool:
    """True only when every address ``endpoint`` resolves to is loopback or private.

    Fails closed: an unparseable endpoint, an unresolvable host, or ANY public /
    link-local / metadata address in the resolution set is rejected. This is the same
    RFC-1918 rail the scan obeys, enforced again at the bind boundary so a caller
    cannot hand this route a public URL to reach on the server's behalf.
    """
    from personalclaw.net.guard import classify_host

    host = (urlparse(endpoint).hostname or "").strip()
    if not host:
        return False
    verdict = classify_host(host)
    if verdict.category != "invalid":  # host was an IP literal — classify it directly
        return verdict.category in ("loopback", "private")
    # A hostname (e.g. "localhost"): resolve and require EVERY address be local.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {str(info[4][0]).split("%", 1)[0] for info in infos}
    return bool(ips) and all(classify_host(ip).category in ("loopback", "private") for ip in ips)


async def api_local_model_detect(request: web.Request) -> web.Response:
    """GET /api/onboarding/local-model — is a local Ollama reachable on localhost?"""
    from personalclaw.local_model_detect import detect_localhost

    found = await asyncio.to_thread(detect_localhost)
    if found is None:
        return web.json_response({"detected": False})
    return web.json_response({"detected": True, **found.to_dict()})


async def api_local_model_scan(request: web.Request) -> web.Response:
    """POST /api/onboarding/local-model/scan — opt-in LAN sweep for an Ollama.

    The only code path that runs the sweep. A first boot that never calls this route
    performs no outbound scan at all.
    """
    from personalclaw.local_model_detect import scan_local_network

    try:
        found = await asyncio.to_thread(scan_local_network)
    except Exception:  # noqa: BLE001 — a scan fault surfaces nothing, never a partial guess
        logger.warning("onboarding: local-model LAN scan failed", exc_info=True)
        found = []
    sel().log_api_access(
        caller=_caller(request),
        operation="onboarding.local_model.scan",
        outcome="ok",
        resources=f"{len(found)} endpoint(s) on private subnet",
    )
    return web.json_response({"endpoints": [item.to_dict() for item in found]})


async def api_local_model_bind(request: web.Request) -> web.Response:
    """POST /api/onboarding/local-model/bind — credential-free bind of an endpoint.

    Body: ``{"endpoint": "http://…:11434"}``. Mirrors ``--seed-local-model``: writes
    the ``providers[]`` entry and the chat binding with NO credential, only after a
    live re-probe confirms a bindable model.
    """
    from personalclaw.seed_local_model import bind_local_model

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — an unparsable body is a 400, never a 500
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)
    endpoint = str(body.get("endpoint") or "").strip()
    if not _endpoint_is_local(endpoint):
        return json_error(
            "local_model_endpoint_invalid",
            message=(
                "A local model can only be bound to a loopback or private (RFC-1918) "
                "endpoint — the address a local or LAN Ollama listens on."
            ),
            status=400,
        )

    result = await asyncio.to_thread(bind_local_model, endpoint=endpoint)
    if result.ok:
        sel().log_api_access(
            caller=_caller(request),
            operation="onboarding.local_model.bind",
            outcome="ok",
            resources=result.provider_name,
        )
        return web.json_response(
            {
                "ok": True,
                "status": result.status,
                "model": result.model,
                "provider": result.provider_name,
            }
        )
    # A skip (no server / no model / no provider app) is a real answer the wizard
    # renders in place — it carries the seed path's own remediation sentence.
    return json_error("local_model_bind_failed", message=result.detail, status=400)


def register_local_model_routes(app: web.Application) -> None:
    """Register the three /api/onboarding/local-model routes."""
    app.router.add_get("/api/onboarding/local-model", api_local_model_detect)
    app.router.add_post("/api/onboarding/local-model/scan", api_local_model_scan)
    app.router.add_post("/api/onboarding/local-model/bind", api_local_model_bind)
