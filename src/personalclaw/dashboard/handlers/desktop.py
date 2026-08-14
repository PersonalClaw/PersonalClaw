"""Desktop shell seam — the gateway routes behind the capability bridge (DC-2 C2/C3).

Four write routes for the Electron MAIN process (loopback + a credential) and two
read routes for the dashboard and for apps holding a ``desktop`` permission. The
registry itself, and the reasoning behind the ``shell_token``, live in
:mod:`personalclaw.dashboard.desktop_registry`.

Every rejection here does two things, always in this order: emit a SEL row, then
return 403 with a body that names the *class* of failure and never echoes a
presented credential (an error body is the easiest place to leak a token into a
screenshot or a bug report). ``operation`` values are stable strings so the
Security surface can be filtered on them:

* ``desktop.register`` — mint a session token (needs ``X-Local-Secret``)
* ``desktop.state.push`` — refresh the manifest (needs ``X-Shell-Token``)
* ``desktop.unregister`` — shell going away (needs ``X-Shell-Token``)
* ``desktop.capability_denied`` — an app read a capability it did not declare

Loopback is checked BEFORE the credential on every write route. A remote caller
must not be able to distinguish a wrong token from a right one, and must not be
able to use these routes as a token oracle at all.
"""

import hmac
import logging

from aiohttp import web

from personalclaw.dashboard.desktop_registry import CAPABILITIES

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding sel() so tests can monkeypatch the singleton."""
    import personalclaw.dashboard.handlers as _pkg

    return _pkg.sel()


def _registry(request: web.Request):
    return request.app["state"].desktop


def _deny(
    request: web.Request, *, operation: str, reason: str, message: str, status: int = 403
) -> web.Response:
    """Emit the SEL row for a refused desktop call, then return the error response.

    ``reason`` is a short machine-ish token (``non-loopback``, ``invalid-secret``,
    ``invalid-shell-token``) that lands in the SEL ``resources`` field. It never
    contains any part of the credential that was presented.
    """
    try:
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation=operation,
            outcome="denied",
            source="desktop-shell",
            resources=reason,
        )
    except Exception:  # audit must never change the security decision
        logger.debug("desktop SEL emit failed for %s", operation, exc_info=True)
    return web.json_response({"error": message}, status=status)


def _require_loopback(request: web.Request, operation: str) -> web.Response | None:
    import personalclaw.dashboard.handlers as _h

    if not _h.is_loopback(request.remote or ""):
        return _deny(request, operation=operation, reason="non-loopback", message="loopback only")
    return None


def _require_shell_token(request: web.Request, operation: str) -> web.Response | None:
    """Verify ``X-Shell-Token`` against the registry's per-session token."""
    token = request.headers.get("X-Shell-Token", "")
    if not _registry(request).verify(token):
        return _deny(
            request,
            operation=operation,
            reason="invalid-shell-token",
            message="invalid shell token",
        )
    return None


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# ── Shell-side writes (loopback only) ─────────────────────────────────


async def api_desktop_register(request: web.Request) -> web.Response:
    """POST /api/desktop/register — the shell announces itself, gets a session token.

    Loopback only, and gated on ``X-Local-Secret`` — the same per-session secret
    file that already backs ``GET /api/token/local``. Proving you can read
    ``$PERSONALCLAW_HOME/.local_secret`` is exactly the claim "I am a process
    running as this user on this machine", which is the claim a desktop shell
    needs to make. Reusing it means no new secret on disk.

    Body: ``{"shell": {"version", "platform"}, "capabilities": {<cap>: {...}}}``.
    Returns ``{"ok": true, "shell_token": "...", "capabilities": {...}}`` — the
    only place the token ever appears. Re-registering rotates it.
    """
    denied = _require_loopback(request, "desktop.register")
    if denied is not None:
        return denied

    expected = request.app.get("local_secret", "")
    if not expected:
        # No secret minted (an embedded/test gateway) — refuse rather than
        # fall back to loopback-only, which would let any local process register.
        return web.json_response({"error": "not available"}, status=503)
    provided = request.headers.get("X-Local-Secret", "")
    if not provided or not hmac.compare_digest(expected, provided):
        return _deny(
            request,
            operation="desktop.register",
            reason="invalid-secret",
            message="invalid secret",
        )

    body = await _json_body(request)
    shell = body.get("shell")
    reg = _registry(request)
    token = reg.register(
        shell=shell if isinstance(shell, dict) else {},
        capabilities=body.get("capabilities"),
    )
    snap = reg.snapshot()
    try:
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="desktop.register",
            outcome="success",
            source="desktop-shell",
            # The capability NAMES are auditable; the token is not logged.
            resources=",".join(sorted(snap["capabilities"])) or "none",
        )
    except Exception:
        logger.debug("desktop SEL emit failed for desktop.register", exc_info=True)
    return web.json_response(
        {"ok": True, "shell_token": token, "capabilities": snap["capabilities"]}
    )


async def api_desktop_state_push(request: web.Request) -> web.Response:
    """POST /api/desktop/state — the shell pushes a refreshed capability manifest.

    Loopback + ``X-Shell-Token``. Called after every grant/deny so the Settings
    panel reflects the OS without polling the shell.
    """
    denied = _require_loopback(request, "desktop.state.push") or _require_shell_token(
        request, "desktop.state.push"
    )
    if denied is not None:
        return denied
    body = await _json_body(request)
    reg = _registry(request)
    if not reg.update(
        token=request.headers.get("X-Shell-Token", ""), capabilities=body.get("capabilities")
    ):
        # Token rotated between verify and update (a racing re-register).
        return _deny(
            request,
            operation="desktop.state.push",
            reason="invalid-shell-token",
            message="invalid shell token",
        )
    return web.json_response({"ok": True, "capabilities": reg.snapshot()["capabilities"]})


async def api_desktop_unregister(request: web.Request) -> web.Response:
    """POST /api/desktop/unregister — the shell is quitting; forget its capabilities.

    Loopback + ``X-Shell-Token``. After this the gateway reports
    ``connected: false`` again, so a still-open browser tab stops claiming the
    desktop can do anything.
    """
    denied = _require_loopback(request, "desktop.unregister") or _require_shell_token(
        request, "desktop.unregister"
    )
    if denied is not None:
        return denied
    _registry(request).unregister(request.headers.get("X-Shell-Token", ""))
    try:
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="desktop.unregister",
            outcome="success",
            source="desktop-shell",
            resources="shell-disconnected",
        )
    except Exception:
        logger.debug("desktop SEL emit failed for desktop.unregister", exc_info=True)
    return web.json_response({"ok": True})


# ── Read side (dashboard + apps) ──────────────────────────────────────


def _app_denied(request: web.Request, cap: str) -> web.Response | None:
    """Enforce the manifest ``desktop`` permission for an app-identity request.

    ``request["app"]`` is set by ``token_auth`` for an app-scoped token and is the
    only identity trusted here (never a body field). ``cap`` empty means "any
    desktop capability" — the whole-state read, which an app may only make if it
    declared at least one capability.
    """
    app_name = request.get("app")
    if not app_name:
        return None  # owner/dashboard identity — the app gate does not apply
    from personalclaw.apps.permissions import checker_for

    # An unresolvable app (uninstalled, unreadable manifest) denies — fail closed.
    checker = checker_for(app_name)
    if checker is not None:
        ok = checker.can_use_desktop(cap) if cap else bool(checker.permissions.desktop)
        if ok:
            return None
    try:
        _sel().log_api_access(
            caller=f"app:{app_name}",
            operation="desktop.capability_denied",
            outcome="denied",
            source="app",
            resources=cap or "desktop-state",
        )
    except Exception:
        logger.debug("desktop SEL emit failed for capability_denied", exc_info=True)
    return web.json_response(
        {"error": "desktop capability not declared in the app manifest"}, status=403
    )


async def api_desktop_state(request: web.Request) -> web.Response:
    """GET /api/desktop/state — what the desktop shell can actually do, right now.

    In a browser tab (or before the shell registers) this is
    ``{"connected": false, "capabilities": {}}``. The empty map is the point: the
    gateway refuses to name capabilities it cannot deliver, so no surface can
    render a grant control that would do nothing.
    """
    denied = _app_denied(request, "")
    if denied is not None:
        return denied
    return web.json_response(_registry(request).snapshot())


async def api_desktop_capability(request: web.Request) -> web.Response:
    """GET /api/desktop/capabilities/{cap} — one capability, gateway-mediated.

    This is the app-facing read: apps never touch Electron IPC, they ask the
    gateway, and the gateway checks the manifest ``desktop`` grant first. 404 when
    the shell is absent or is not claiming that capability (fail closed — never a
    synthesized "not granted yet" that an app could mistake for "ask again").
    """
    cap = request.match_info.get("cap", "")
    if cap not in CAPABILITIES:
        return web.json_response({"error": "unknown capability"}, status=404)
    denied = _app_denied(request, cap)
    if denied is not None:
        return denied
    entry = _registry(request).capability(cap)
    if entry is None:
        return web.json_response({"error": "desktop shell not connected"}, status=404)
    return web.json_response({"capability": cap, **entry})
