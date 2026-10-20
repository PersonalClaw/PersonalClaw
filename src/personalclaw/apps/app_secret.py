"""Per-app proxy secret — mint + read the HMAC key that authenticates the proxy.

An app backend binds on loopback with no inbound auth of its own (see "Inbound
authentication" in ``docs/architecture/app-platform.md``): the port is a *network* boundary, not
an *authorization* one. To make the permission model hold, every request the gateway
reverse-proxy forwards is signed with an HMAC over a per-app secret, and the backend's
SDK middleware refuses anything unsigned (fail-closed). This module owns that secret's
one true storage shape so the two call sites agree:

- :func:`mint_app_secret` — used by the backend supervisor each time it launches the backend,
  to mint a fresh secret and inject it into the child env as ``PERSONALCLAW_APP_SECRET``.
  Fail-closed: returns ``None`` if the secret cannot be written, so the supervisor declines to
  start an unprotected backend.
- :func:`read_app_secret` — used by the proxy handler at forward time to sign. The
  supervisor already minted it; the proxy just reads (returns ``None`` if absent →
  the proxy fails closed rather than forwarding unsigned).

Kept in its own module (not inline in ``backend_runtime`` or the handler) precisely
because two independent call sites need identical path + 0600 discipline; a single
auditable home is safer than duplicating the crypto-adjacent bits.

The secret is a 256-bit hex token (``secrets.token_hex(32)``). The file is 0600 and its
value is NEVER logged.

**It lasts as long as the backend it protects.** It was minted once and read back on every start,
so a copy made once — from the file, or from a backend's environment — signed requests the backend
accepted for as long as the app stayed installed. Now each launch mints a new one: the backend that
starts is the only one holding it, and a copy stops working the next time the backend starts (a
gateway restart, an update, a crash revived). Nothing else holds it — every signer reads the file
when it signs (:func:`proxy_signature`).
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from personalclaw.apps.manager import app_dir
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

APP_SECRET_FILENAME = ".app_secret"
_SECRET_BYTES = 32  # 256-bit → 64 hex chars


def secret_path(name: str) -> Path:
    """``apps_dir()/<app>/.app_secret`` — the per-app secret file path."""
    return app_dir(name) / APP_SECRET_FILENAME


def mint_app_secret(name: str) -> str | None:
    """Mint app ``name``'s proxy secret for the backend about to start. ``None`` on failure.

    A new one on every call, replacing the last: the supervisor calls this only when it is about
    to launch a backend, when no live backend holds the old one (see the module docstring).
    Fail-closed: if the secret cannot be written, the caller (the backend supervisor) must NOT
    start the backend — an unprotected backend is worse than a missing one. Never logs the
    secret value.
    """
    path = secret_path(name)
    if not path.parent.is_dir():
        logger.warning("app %s: no install folder to keep its proxy secret in", name)
        return None
    try:
        token = secrets.token_hex(_SECRET_BYTES)
        # A new file renamed over the old, created 0600 before any byte lands: a signer never
        # reads half a secret, and a link planted under the name is replaced, never written
        # through.
        atomic_write(path, token, mode=0o600)
        return token
    except (OSError, ValueError) as exc:
        logger.warning("app %s: could not mint the proxy secret: %s", name, exc)
        return None


def read_app_secret(name: str) -> str | None:
    """Read app ``name``'s proxy secret for signing. ``None`` if absent/unreadable.

    Used by the proxy at forward time. Does NOT mint — the supervisor owns minting at
    boot; a missing secret here means the backend was never started protected, so the
    proxy fails closed. Never logs the secret value.
    """
    path = secret_path(name)
    try:
        value = path.read_text(encoding="ascii").strip()
        return value or None
    except OSError:
        return None


def proxy_signature(name: str, method: str, path_qs: str, body: bytes) -> str | None:
    """The ``X-PersonalClaw-Proxy`` value for a request the gateway sends app ``name``'s backend,
    or ``None`` when the app has no secret (its backend was never started protected, and the
    request must not go unsigned).

    *path_qs* is the exact wire path and query the backend's aiohttp reads as
    ``request.raw_path``, and *body* the exact bytes sent (``b""`` for none): the signature covers
    both. The one signer for every such request — the dashboard's reverse proxy and an agent's
    or a trigger's ``call_app_route`` — so an app's fail-closed middleware
    (`sdk.security.require_proxy_signature`) admits them alike.
    """
    from personalclaw.proxy_signature import sign_proxy_request

    secret = read_app_secret(name)
    return sign_proxy_request(secret, method, path_qs, body) if secret else None
