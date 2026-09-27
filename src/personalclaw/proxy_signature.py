"""The gateway's signature on a request it sends an app's backend: the one wire contract.

An app backend binds on loopback with no auth of its own: the port is a network boundary, not
an authorization one (``docs/architecture/app-platform.md`` §2.1). So every request the gateway
sends a backend — its reverse proxy, and an agent's or a trigger's ``call_app_route`` — carries
an HMAC over the app's secret (``apps.app_secret.proxy_signature``), and the backend's SDK
middleware (``sdk.security.require_proxy_signature``) verifies it fail-closed. Both sides build
the message here, so the contract has exactly one definition; the SDK re-exports it for the app
side.

The signed message is::

    <ts>:<METHOD>:<raw_path?query>:<sha256_hex(body)>

with ``ts`` an integer unix second. Standard library only: an app backend imports this through
the SDK in its own process.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# The header the gateway attaches and the backend verifies. Value is ``<ts>:<hmac_hex>``.
PROXY_SIGNATURE_HEADER = "X-PersonalClaw-Proxy"


def build_signing_string(ts: int, method: str, path_qs: str, body: bytes) -> str:
    """The canonical message both sides HMAC: ``<ts>:<METHOD>:<path?query>:<sha256(body)>``.

    ``path_qs`` is the on-the-wire request target the backend sees (aiohttp's
    ``request.raw_path`` — the path plus any query string, percent-encoded). The signer
    passes the exact same target it forwards, so the two reconstruct an identical string.
    """
    return f"{ts}:{method}:{path_qs}:{hashlib.sha256(body).hexdigest()}"


def hmac_hex(secret: str, message: str) -> str:
    """The HMAC-SHA256 of *message* under *secret*, hex."""
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_proxy_request(
    secret: str, method: str, path_qs: str, body: bytes, *, ts: int | None = None
) -> str:
    """Build the ``X-PersonalClaw-Proxy`` header value for a request. Used by the gateway.

    Returns ``"<ts>:<hmac_hex>"``. ``ts`` defaults to the current unix second (injectable
    for tests).
    """
    ts = int(time.time()) if ts is None else ts
    return f"{ts}:{hmac_hex(secret, build_signing_string(ts, method, path_qs, body))}"
