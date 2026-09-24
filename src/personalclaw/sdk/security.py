"""SDK: the security helpers an app applies to untrusted content + inbound requests.

``fence_untrusted`` wraps free-text an app surfaces from an external source (scraped
web results, third-party API payloads) in ``<untrusted_content>`` fences so a
prompt-injection in that data is treated as data, not instructions — the same fencing
core applies. An app that ingests external text uses this rather than reimplementing it.

``require_proxy_signature`` is the INBOUND authentication an app backend applies to
every request. An app backend binds on loopback with no auth of its own — the port is
a *network* boundary, not an *authorization* one (see
``docs/architecture/app-platform.md`` §2.1). The gateway reverse-proxy signs every
request it forwards with an HMAC over a per-app secret; this middleware verifies that
signature **fail-closed** so a local process that finds the port cannot bypass the
gateway proxy (and therefore session auth + the app-permission middleware). The signer
side lives in the gateway (``dashboard/handlers/apps.py``); both sides call
:func:`build_signing_string` / :func:`sign_proxy_request` here so the wire contract has
exactly one definition.

The signed message is::

    <ts>:<METHOD>:<raw_path?query>:<sha256_hex(body)>

with ``ts`` an integer unix second and a ±60s acceptance window. The secret reaches the
backend via the ``PERSONALCLAW_APP_SECRET`` environment variable (the supervisor mints
it 0600 on disk and injects it). ``/health`` is exempt because the gateway watchdog
probes it directly, not through the signing proxy.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from collections.abc import Awaitable, Callable, Iterable

from aiohttp import web

from personalclaw.security import fence_untrusted  # noqa: F401

__all__ = [
    "fence_untrusted",
    "require_proxy_signature",
    "sign_proxy_request",
    "build_signing_string",
    "PROXY_SIGNATURE_HEADER",
    "PROXY_SIGNATURE_WINDOW_SECS",
    "APP_SECRET_ENV",
]

# The header the gateway proxy attaches and the backend verifies. Value is
# ``<ts>:<hmac_hex>``.
PROXY_SIGNATURE_HEADER = "X-PersonalClaw-Proxy"
# Acceptance window in seconds either side of ``now`` — a captured signature replayed
# after this many seconds is refused.
PROXY_SIGNATURE_WINDOW_SECS = 60
# Environment variable the supervisor injects the per-app secret into.
APP_SECRET_ENV = "PERSONALCLAW_APP_SECRET"
# Path(s) exempt from signature: the gateway watchdog probes the backend's health
# endpoint directly (not through the signing proxy), so it must not require a signature.
_DEFAULT_EXEMPT: frozenset[str] = frozenset({"/health"})


def build_signing_string(ts: int, method: str, path_qs: str, body: bytes) -> str:
    """The canonical message both sides HMAC: ``<ts>:<METHOD>:<path?query>:<sha256(body)>``.

    ``path_qs`` is the on-the-wire request target the backend sees (aiohttp's
    ``request.raw_path`` — the path plus any query string, percent-encoded). The signer
    passes the exact same target it forwards, so the two reconstruct an identical string.
    """
    return f"{ts}:{method}:{path_qs}:{hashlib.sha256(body).hexdigest()}"


def _hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_proxy_request(
    secret: str, method: str, path_qs: str, body: bytes, *, ts: int | None = None
) -> str:
    """Build the ``X-PersonalClaw-Proxy`` header value for a request. Used by the proxy.

    Returns ``"<ts>:<hmac_hex>"``. ``ts`` defaults to the current unix second (injectable
    for tests).
    """
    ts = int(time.time()) if ts is None else ts
    return f"{ts}:{_hmac_hex(secret, build_signing_string(ts, method, path_qs, body))}"


def _verify(
    secret: str, method: str, path_qs: str, body: bytes, provided: str, window_secs: int
) -> tuple[bool, str]:
    """Constant-time verify. Returns ``(ok, reason)``; ``reason`` is set only on failure."""
    if not provided or ":" not in provided:
        return False, "absent or malformed signature"
    ts_str, mac = provided.split(":", 1)
    try:
        ts = int(ts_str)
    except ValueError:
        return False, "malformed timestamp"
    if abs(int(time.time()) - ts) > window_secs:
        return False, "stale signature (outside window)"
    expected = _hmac_hex(secret, build_signing_string(ts, method, path_qs, body))
    if not hmac.compare_digest(expected, mac):
        return False, "signature mismatch"
    return True, ""


def _deny(request: web.Request, reason: str) -> None:
    """Record a denial without leaking the secret or the presented signature.

    An app backend is a separate process without core's ``sel()`` (the SecurityEventLog
    is a gateway singleton), so the honest equivalent of the plan's "denials log to SEL"
    is a structured stderr warning in the app process. Neither the secret nor the
    signature value is logged — only the method + path + a category reason.
    """
    print(
        f"[personalclaw.sdk.security] proxy-signature denied: {reason} "
        f"method={request.method} path={request.path}",
        file=sys.stderr,
        flush=True,
    )


def require_proxy_signature(
    secret: str | None = None,
    *,
    exempt_paths: Iterable[str] = _DEFAULT_EXEMPT,
    window_secs: int = PROXY_SIGNATURE_WINDOW_SECS,
) -> Callable[
    [web.Request, Callable[[web.Request], Awaitable[web.StreamResponse]]],
    Awaitable[web.StreamResponse],
]:
    """An aiohttp middleware that verifies the gateway proxy signature, **fail-closed**.

    Install it on the app backend's ``web.Application(middlewares=[require_proxy_signature()])``.

    Behavior:

    - The secret is read from ``PERSONALCLAW_APP_SECRET`` at construction (override via
      ``secret`` for tests). If no secret is present, EVERY non-exempt request is refused
      (a backend with no verifiable caller serves nothing).
    - The raw request body is read once and stashed on ``request["body_bytes"]`` so a
      route handler reads it from there rather than consuming the single-read stream a
      second time — the ONE body-read mechanism shared with the app's ``_json`` helper.
    - Exempt paths (default ``/health``) skip the signature check — the watchdog probes
      health directly, not through the signing proxy.
    - For every other request: an absent, malformed, stale (outside ±``window_secs``), or
      mismatched signature returns ``401`` and the route body never runs. The compare is
      constant-time (:func:`hmac.compare_digest`).
    """
    resolved_secret = secret if secret is not None else os.environ.get(APP_SECRET_ENV, "")
    exempt = frozenset(exempt_paths)

    @web.middleware
    async def _middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        # Read the body exactly once and stash it — the single body-read mechanism the
        # backend's own body parsing reuses (so the stream is never consumed twice).
        body = await request.read()
        request["body_bytes"] = body

        if request.path in exempt:
            return await handler(request)

        if not resolved_secret:
            _deny(request, "no app secret in environment")
            return web.json_response({"error": "unauthorized"}, status=401)

        provided = request.headers.get(PROXY_SIGNATURE_HEADER, "")
        ok, reason = _verify(
            resolved_secret, request.method, request.raw_path, body, provided, window_secs
        )
        if not ok:
            _deny(request, reason)
            return web.json_response({"error": "unauthorized"}, status=401)

        return await handler(request)

    return _middleware
