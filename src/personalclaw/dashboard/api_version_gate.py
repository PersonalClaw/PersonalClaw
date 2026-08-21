"""The ONE chokepoint where a client's declared API version is compared.

:mod:`personalclaw.api_version` owns the numbers, the window and the comparison;
this module is the aiohttp adapter that runs it once per request and turns a
refusal into the PL-8 wire envelope. There is deliberately no second comparison
site — ``tests/test_api_version_one_origin.py`` asserts that
:func:`personalclaw.api_version.negotiate` has exactly one non-test caller,
because a version check duplicated into a handler is how a window quietly stops
applying on half the surface.

**What is exempt, and why.** A version refusal on the wrong route is an outage,
so every exemption below is deliberate. The rule of thumb: never gate anything a
refused client must reach *in order to* stop being refused.

* **Everything outside ``/api/``** — the SPA document, ``/assets/**``,
  ``/login``, icons, fonts. Recovering from a version refusal *is* a page load:
  gating the bundle means a stale client can never fetch the newer bundle that
  would fix it, which converts a legible refusal back into the blank screen this
  atom exists to remove.
* **``GET /api/healthz``** — the liveness probe (``personalclaw status``, a
  launchd/systemd supervisor, a container health check). A non-200 here reads as
  "the gateway is down", so gating it would turn a client-version problem into a
  false outage and, worse, into an automatic restart loop.
* **``GET /api/manifest``** — the endpoint that PUBLISHES ``apiVersion``.
  Refusing it is circular: it is how a mismatched client learns which number to
  send, and a client that cannot read the manifest cannot discover the window it
  failed.
* **The pre-session front door** (``/api/token/local``, ``/api/logout``,
  ``/api/auth/status``, ``/api/auth/login``, ``/api/auth/enroll/complete``,
  ``/api/devices/pair/complete``) — these authenticate themselves and exist to
  mint the session a client needs before it can do anything else. A version wall
  in front of login turns "reload the page" into "you cannot even authenticate
  far enough to see why you were refused". Exempting them opens nothing: each
  keeps its own loopback/secret/argon2/rate-limit guard.
* **``/mcp``** — the inbound MCP surface negotiates its own protocol version per
  the MCP specification. A second, PersonalClaw-specific version wall in front of
  it would refuse a spec-compliant client over a number it has no way to know.
* **WebSocket upgrades (``/api/ws`` and below)** — a browser ``WebSocket``
  cannot set a request header, so the only carrier would be a query parameter
  appended at every socket site (four of them today), i.e. four declaration
  sites instead of one. Gating them buys no coverage either: a socket is opened
  by an already-mounted SPA that has necessarily made HTTP calls first, so the
  chokepoint has already judged that client.

Everything else under ``/api/`` (plus ``/mcp``, which is inside the gate's scope
so that its exemption above is a live, checkable decision rather than an accident
of path shape) is gated.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiohttp import web

from personalclaw.api_version import VERSION_HEADER, negotiate
from personalclaw.http_errors import json_error

#: Exact paths that are never version-gated. Each entry's reason is in the module
#: docstring — an exemption list without stated reasons is how a control quietly
#: stops applying.
EXEMPT_EXACT: frozenset[str] = frozenset(
    {
        "/api/healthz",  # liveness probe: a refusal here reads as "gateway down"
        "/api/manifest",  # publishes apiVersion; refusing it is circular
        "/api/token/local",  # loopback + X-Local-Secret, pre-session
        "/api/logout",  # loopback + X-Local-Secret, pre-session
        "/api/auth/status",  # login front door
        "/api/auth/login",  # login front door
        "/api/auth/enroll/complete",  # device enrollment has no session yet
        "/api/devices/pair/complete",  # device pairing has no session yet
        "/mcp",  # negotiates its own protocol version
    }
)

#: Path prefixes that are never version-gated (WebSocket upgrades — see the
#: module docstring; the browser cannot declare a version on a socket).
EXEMPT_PREFIXES: tuple[str, ...] = ("/api/ws",)

#: The gate's scope. Everything outside it is never version-gated — chiefly the
#: SPA document and ``/assets/**``, which are the recovery path.
GATED_PREFIXES: tuple[str, ...] = ("/api/", "/mcp")


def is_gated(path: str) -> bool:
    """Whether ``path`` is subject to version negotiation.

    Split out from the middleware so the exemption policy is testable without a
    running gateway, and so the boundary is one function rather than an inline
    condition that a later edit can widen by accident.
    """
    if not path.startswith(GATED_PREFIXES):
        return False
    if path in EXEMPT_EXACT:
        return False
    if path.startswith(EXEMPT_PREFIXES):
        return False
    return True


def api_version_middleware() -> Any:
    """Build the version-negotiation middleware.

    A factory (rather than a bare middleware) so the marker attribute below can
    be attached to the instance the app installs, letting a test assert the gate
    is actually in ``app.middlewares`` instead of merely importable.
    """

    @web.middleware  # type: ignore[misc]
    async def _mw(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if not is_gated(request.path):
            return await handler(request)

        outcome = negotiate(request.headers.get(VERSION_HEADER))
        if outcome.refusal is not None:
            return json_error(
                "api_version_unsupported",
                message=outcome.refusal.message,
                status=400,
                error_extra=outcome.refusal.as_error_extra(),
            )

        resp = await handler(request)
        # Echo the NEGOTIATED version back. This is what makes "an undeclared
        # client is the oldest supported version" a fact on the wire instead of an
        # internal assumption: `curl -sD- /api/config` with no declaration reports
        # the window's floor, not the current version. `prepared` guards a response
        # that already began streaming, whose headers are gone.
        if outcome.negotiated is not None and not resp.prepared:
            resp.headers[VERSION_HEADER] = str(outcome.negotiated)
        return resp

    _mw._is_api_version_gate = True  # type: ignore[attr-defined]
    return _mw
