"""Authentication primitives for the PersonalClaw gateway.

``AuthMode`` selects one of ``none``, ``local_token``, ``api_key``, or
``oauth2`` (see ``modes.py``); ``dashboard.token_auth.auth_middleware``
dispatches by that mode.
"""

from personalclaw.auth.modes import AuthConfig, AuthMode, effective_bind

__all__ = [
    "AuthConfig",
    "AuthMode",
    "effective_bind",
]
