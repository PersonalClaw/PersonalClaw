"""Per-app proxy secret — mint + read the HMAC key that authenticates the proxy.

An app backend binds on loopback with no inbound auth of its own (see
``docs/architecture/app-platform.md`` §2.1): the port is a *network* boundary, not
an *authorization* one. To make the permission model hold, every request the gateway
reverse-proxy forwards is signed with an HMAC over a per-app secret, and the backend's
SDK middleware refuses anything unsigned (fail-closed). This module owns that secret's
one true storage shape so the two call sites agree:

- :func:`ensure_app_secret` — used by the backend supervisor at ``start()`` to mint
  (once) and read the secret, then inject it into the child env as
  ``PERSONALCLAW_APP_SECRET``. Fail-closed: returns ``None`` if the secret cannot be
  written/read, so the supervisor declines to start an unprotected backend.
- :func:`read_app_secret` — used by the proxy handler at forward time to sign. The
  supervisor already minted it; the proxy just reads (returns ``None`` if absent →
  the proxy fails closed rather than forwarding unsigned).

Kept in its own module (not inline in ``backend_runtime`` or the handler) precisely
because two independent call sites need identical path + 0600 discipline; a single
auditable home is safer than duplicating the crypto-adjacent bits.

The secret is a 256-bit hex token (``secrets.token_hex(32)``). The file is 0600 and its
value is NEVER logged.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from personalclaw.apps.manager import app_dir

logger = logging.getLogger(__name__)

APP_SECRET_FILENAME = ".app_secret"
_SECRET_BYTES = 32  # 256-bit → 64 hex chars


def secret_path(name: str) -> Path:
    """``apps_dir()/<app>/.app_secret`` — the per-app secret file path."""
    return app_dir(name) / APP_SECRET_FILENAME


def _write_0600(path: Path, value: str) -> None:
    """Write ``value`` to ``path`` with mode 0600, enforced even under a loose umask.

    ``os.open`` honors the mode arg only modulo the umask, so a permissive umask could
    leave the fresh file group/other-readable. We fchmod after creating to pin 0600
    regardless — the secret must never be world-readable.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # pin 0600 even if umask loosened O_CREAT's mode
        os.write(fd, value.encode("ascii"))
    finally:
        os.close(fd)


def ensure_app_secret(name: str) -> str | None:
    """Mint (if absent) and return app ``name``'s proxy secret. ``None`` on failure.

    Fail-closed: if the secret cannot be created or read, the caller (the backend
    supervisor) must NOT start the backend — an unprotected backend is worse than a
    missing one. Never logs the secret value.
    """
    path = secret_path(name)
    try:
        if path.exists():
            existing = path.read_text(encoding="ascii").strip()
            if existing:
                # Re-pin perms: a secret left group/other-readable by an older run is
                # a finding, not something to silently trust.
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                return existing
            # Empty/corrupt file → re-mint over it.
        token = secrets.token_hex(_SECRET_BYTES)
        _write_0600(path, token)
        return token
    except OSError as exc:
        logger.warning("app %s: could not mint/read proxy secret: %s", name, exc)
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
