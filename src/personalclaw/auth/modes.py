"""Auth modes and configuration for the PersonalClaw gateway.

This module defines the two authentication modes — ``local_token`` (the default) and
``none`` — and the ``AuthConfig`` dataclass that the gateway's middleware dispatches on.

The ``effective_bind`` helper enforces the loopback invariant: when the
mode is ``NONE``, the bind host is forced to ``127.0.0.1`` regardless of
what was configured. ``local_token`` honors the configured ``bind_host``.

A request for anything else — including ``api_key`` and ``oauth2``, which were declared
here without any configuration able to select them and were deleted — is
NAMED at startup rather than downgraded in silence (see :func:`classify_auth_mode_request`).
"""

import logging
from dataclasses import KW_ONLY, dataclass
from enum import Enum

LOOPBACK_HOST = "127.0.0.1"

logger = logging.getLogger(__name__)


class AuthMode(str, Enum):
    """Authentication mode selected by the operator at gateway start."""

    NONE = "none"
    LOCAL_TOKEN = "local_token"


#: The ``PERSONALCLAW_AUTH_MODE`` values an operator can put in force, mapped to the mode
#: each selects. This is the selector's single source of truth —
#: :func:`classify_auth_mode_request` reads it rather than re-deciding, and every
#: :class:`AuthMode` must be here (``tests/test_unhonored_auth_mode_is_named.py``), so a
#: mode cannot again be declared without anything able to select it.
SELECTABLE_MODES: dict[str, AuthMode] = {
    AuthMode.NONE.value: AuthMode.NONE,
    AuthMode.LOCAL_TOKEN.value: AuthMode.LOCAL_TOKEN,
}

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
    reason when the requested value is not a mode (``api_key`` and ``oauth2``
    included, since they were deleted). That leaves ``LOCAL_TOKEN`` in force,
    which fails CLOSED (a client presenting some other credential is refused, not
    admitted) — the cost is lost access, not weakened auth. The defect this names
    is legibility: before this warning, an operator who set ``oauth2`` believed they had
    enforced IdP SSO and was in fact on a local token, with nothing said.
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

    return AuthModeRequest(
        requested=requested, effective=AuthMode.LOCAL_TOKEN, unhonored_reason=_UNKNOWN_REASON
    )


@dataclass(frozen=True)
class AuthConfig:
    """Runtime auth configuration consumed by ``auth_middleware``.

    Defaults to ``LOCAL_TOKEN`` mode bound to loopback with CSRF on.
    """

    mode: AuthMode = AuthMode.LOCAL_TOKEN
    bind_host: str = LOOPBACK_HOST
    cookie_name: str = "personalclaw_token"
    # Keyword-only: the four deleted fields sat between `cookie_name` and this one, so a caller
    # still passing them by position must fail loudly rather than bind an issuer URL to this.
    _: KW_ONLY
    csrf_required: bool = True

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Build the runtime auth config, honoring ``PERSONALCLAW_AUTH_MODE``.

        Defaults to ``LOCAL_TOKEN``. Setting ``PERSONALCLAW_AUTH_MODE=none`` selects
        ``AuthMode.NONE`` — passes all requests through, with the bind host forced to
        loopback by ``effective_bind`` so an unauthenticated gateway can never reach a
        non-loopback interface (dev convenience on localhost only).

        Any other value (``api_key`` and ``oauth2`` included) still yields ``LOCAL_TOKEN``
        — the admission decision is unchanged — but it is WARNED about rather than
        downgraded in silence. ``personalclaw doctor`` prints the same sentence."""
        request = classify_auth_mode_request()
        if request.detail:
            logger.warning("%s", request.detail)
        return cls(mode=request.effective)


def effective_bind(auth_cfg: AuthConfig) -> str:
    """Return the TCP bind host that must be used for ``auth_cfg``.

    When ``auth_cfg.mode == AuthMode.NONE`` the bind host is forced to
    ``127.0.0.1`` so an unauthenticated gateway can never reach a
    non-loopback interface. For ``LOCAL_TOKEN`` the configured
    ``bind_host`` is returned unchanged.
    """
    if auth_cfg.mode == AuthMode.NONE:
        return LOOPBACK_HOST
    return auth_cfg.bind_host
