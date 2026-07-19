"""Auth modes and configuration for the PersonalClaw gateway.

This module defines the four supported authentication modes
(``none``, ``local_token``, ``api_key``, ``oauth2``) and the
``AuthConfig`` dataclass that the gateway's middleware dispatches on.

The ``effective_bind`` helper enforces the loopback invariant: when the
mode is ``NONE``, the bind host is forced to ``127.0.0.1`` regardless of
what was configured. Any other mode honors the configured ``bind_host``.

This module MUST NOT import provider SDKs or auth libraries
(``cryptography``, ``httpx``): the JWT verification path loads lazily
inside ``auth/oidc.py`` only when ``AuthMode.OAUTH2`` is in use.
"""

from dataclasses import dataclass
from enum import Enum

LOOPBACK_HOST = "127.0.0.1"


class AuthMode(str, Enum):
    """Authentication mode selected by the operator at gateway start."""

    NONE = "none"
    LOCAL_TOKEN = "local_token"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"


@dataclass(frozen=True)
class AuthConfig:
    """Runtime auth configuration consumed by ``auth_middleware``.

    Defaults to ``LOCAL_TOKEN`` mode bound to loopback with CSRF on.
    Operators flip ``mode`` (and supply the matching per-mode fields)
    to opt into stronger auth.
    """

    mode: AuthMode = AuthMode.LOCAL_TOKEN
    bind_host: str = LOOPBACK_HOST
    cookie_name: str = "personalclaw_token"
    oauth2_issuer: str | None = None
    oauth2_client_id: str | None = None
    oauth2_audience: str | None = None
    api_key_env: str | None = None
    csrf_required: bool = True

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Build the runtime auth config, honoring ``PERSONALCLAW_AUTH_MODE``.

        Defaults to ``LOCAL_TOKEN``. Setting ``PERSONALCLAW_AUTH_MODE=none`` selects
        ``AuthMode.NONE`` — passes all requests through, with the bind host forced to
        loopback by ``effective_bind`` so an unauthenticated gateway can never reach a
        non-loopback interface (dev convenience on localhost only)."""
        import os

        raw = (os.environ.get("PERSONALCLAW_AUTH_MODE") or "").strip().lower()
        if raw == "none":
            return cls(mode=AuthMode.NONE)
        return cls()


def effective_bind(auth_cfg: AuthConfig) -> str:
    """Return the TCP bind host that must be used for ``auth_cfg``.

    When ``auth_cfg.mode == AuthMode.NONE`` the bind host is forced to
    ``127.0.0.1`` so an unauthenticated gateway can never reach a
    non-loopback interface. For every other mode the configured
    ``bind_host`` is returned unchanged.
    """
    if auth_cfg.mode == AuthMode.NONE:
        return LOOPBACK_HOST
    return auth_cfg.bind_host
