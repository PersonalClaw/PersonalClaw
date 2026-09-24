"""Auth modes and configuration for the PersonalClaw gateway.

This module defines the four supported authentication modes
(``none``, ``local_token``, ``api_key``, ``oauth2``) and the
``AuthConfig`` dataclass that the gateway's middleware dispatches on.

The ``effective_bind`` helper enforces the loopback invariant: when the
mode is ``NONE``, the bind host is forced to ``127.0.0.1`` regardless of
what was configured. Any other mode honors the configured ``bind_host``.

Only ``none`` and ``local_token`` are *selectable*. ``api_key`` and
``oauth2`` are declared here and their request-side halves are built, but
no configuration reaches them — so a request for one is NAMED at startup
rather than downgraded in silence (see
:func:`classify_auth_mode_request`).

This module MUST NOT import provider SDKs or auth libraries
(``cryptography``, ``httpx``): the JWT verification path loads lazily
inside ``auth/oidc.py`` only when ``AuthMode.OAUTH2`` is in use.
"""

import logging
from dataclasses import dataclass
from enum import Enum

LOOPBACK_HOST = "127.0.0.1"

logger = logging.getLogger(__name__)


class AuthMode(str, Enum):
    """Authentication mode selected by the operator at gateway start."""

    NONE = "none"
    LOCAL_TOKEN = "local_token"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"


#: The ``PERSONALCLAW_AUTH_MODE`` values an operator can actually put in force,
#: mapped to the mode each selects. This is the selector's single source of
#: truth — :func:`classify_auth_mode_request` reads it rather than re-deciding,
#: so a mode added to :class:`AuthMode` without a wired selector lands in
#: neither this mapping nor :data:`UNSELECTABLE_MODES` and is caught by the
#: classification rail in ``tests/test_sl8_unhonored_auth_mode_is_named.py``.
SELECTABLE_MODES: dict[str, AuthMode] = {
    AuthMode.NONE.value: AuthMode.NONE,
    AuthMode.LOCAL_TOKEN.value: AuthMode.LOCAL_TOKEN,
}

#: Modes ``AuthMode`` declares that no configuration can select. Their
#: request-side halves exist — ``dashboard/token_auth.py`` validates a Bearer key
#: and ``auth/oidc.py`` verifies an OIDC JWT — but nothing populates
#: ``AuthConfig``'s per-mode fields, so ``from_env`` cannot hand them a usable
#: config. Documented in ``.env.example`` and ``docs/architecture/security.md``;
#: this is the runtime's own copy of that fact, so it can say so out loud.
UNSELECTABLE_MODES: frozenset[str] = frozenset({AuthMode.API_KEY.value, AuthMode.OAUTH2.value})

_UNSELECTABLE_REASON = (
    "declared in AuthMode but not selectable — the request-side half is built "
    "(dashboard/token_auth.py, auth/oidc.py) but no configuration reaches it"
)
_UNKNOWN_REASON = "not a known auth mode"

#: Cap on how much of the requested value is echoed back. The value of an auth
#: MODE is a short keyword, never a credential, but a bounded echo keeps a pasted
#: blob from becoming a multi-kilobyte log line.
_MAX_ECHOED_CHARS = 40


def _echoable(value: str) -> str:
    if len(value) <= _MAX_ECHOED_CHARS:
        return value
    return value[:_MAX_ECHOED_CHARS] + "…(truncated)"


@dataclass(frozen=True)
class AuthModeRequest:
    """What the operator ASKED for versus what the runtime put in force.

    Pure description: building one decides nothing. ``effective`` is the mode
    :meth:`AuthConfig.from_env` selects for the same input — ``from_env``
    consumes this type, so the two can never disagree.
    """

    #: Normalized ``PERSONALCLAW_AUTH_MODE`` value; ``""`` when unset.
    requested: str
    #: The mode actually in force.
    effective: AuthMode
    #: Why the request was not honored; ``""`` when it was (or when unset).
    unhonored_reason: str = ""

    @property
    def detail(self) -> str:
        """The one line to log and to print in ``doctor``; ``""`` when honored.

        Names all three facts an operator needs: what they asked for, that it
        was NOT applied, and which mode is enforcing instead.
        """
        if not self.unhonored_reason:
            return ""
        return (
            f"PERSONALCLAW_AUTH_MODE={_echoable(self.requested)!r} was NOT applied: "
            f"{self.unhonored_reason}. Auth mode {self.effective.value!r} is in force "
            '(see docs/architecture/security.md "Auth modes").'
        )


def classify_auth_mode_request(raw: str | None = None) -> AuthModeRequest:
    """Describe a ``PERSONALCLAW_AUTH_MODE`` request without acting on it.

    ``raw`` defaults to the environment. This function makes no admission
    decision and changes none: it reports the mode ``from_env`` selects, plus a
    reason when the requested mode is not the one that ends up in force. That
    happens two ways — the value names a declared-but-unwired mode
    (:data:`UNSELECTABLE_MODES`), or it is not a mode name at all. Both leave
    ``LOCAL_TOKEN`` in force, which fails CLOSED (a client presenting the
    credential the operator configured is refused, not admitted) — the cost is
    lost access, not weakened auth. The defect this names is legibility: before
    SL-8 an operator who set ``oauth2`` believed they had enforced IdP SSO and
    was in fact on a shared bearer token, with nothing said.
    """
    if raw is None:
        import os

        raw = os.environ.get("PERSONALCLAW_AUTH_MODE") or ""
    requested = raw.strip().lower()

    if requested == "":
        # Nothing was asked for, so nothing was ignored: the documented default.
        return AuthModeRequest(requested=requested, effective=AuthMode.LOCAL_TOKEN)
    selected = SELECTABLE_MODES.get(requested)
    if selected is not None:
        return AuthModeRequest(requested=requested, effective=selected)

    reason = _UNSELECTABLE_REASON if requested in UNSELECTABLE_MODES else _UNKNOWN_REASON
    return AuthModeRequest(
        requested=requested, effective=AuthMode.LOCAL_TOKEN, unhonored_reason=reason
    )


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
        non-loopback interface (dev convenience on localhost only).

        A value the runtime cannot honor (``api_key``, ``oauth2``, or anything
        unrecognised) still yields ``LOCAL_TOKEN`` — the admission decision is
        unchanged — but it is now WARNED about rather than downgraded in silence
        (SL-8). ``personalclaw doctor`` prints the same sentence."""
        request = classify_auth_mode_request()
        if request.detail:
            logger.warning("%s", request.detail)
        return cls(mode=request.effective)


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
