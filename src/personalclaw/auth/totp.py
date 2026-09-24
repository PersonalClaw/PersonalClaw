"""RFC 6238 TOTP, on the standard library (REMOTE-USER-AUTH T4.2 primitives).

No `pyotp`. TOTP is an HMAC-SHA1 over a counter and a truncation — about fifteen lines of
`hmac` and `base64` — so a dependency here would buy nothing and add a supply-chain edge to
the credential path, which is the last place to want one. This is the deliberate exception
to "don't hand-roll crypto": we are not inventing a construction, we are calling
`hmac.new()` exactly as the RFC specifies, and the test vectors in RFC 6238 Appendix B pin
it. (Password hashing is the opposite case, and gets `argon2-cffi`.)

The verify accepts the adjacent time steps, because a phone whose clock is a few seconds
off is normal and a user who cannot log in because of it will simply turn 2FA off.
"""

from __future__ import annotations

import base64
import hmac
import secrets
import struct
import time
from hashlib import sha1
from urllib.parse import quote

#: 30-second steps and 6 digits: the RFC defaults, and what every authenticator app assumes.
STEP_SECS = 30
DIGITS = 6

#: How many steps either side of "now" are accepted. One step (±30s) covers ordinary clock
#: drift without meaningfully widening the guess space — a code stays valid for at most 90s.
_SKEW_STEPS = 1


def new_secret() -> str:
    """A fresh base32 secret (160 bits, the RFC 4226 recommendation)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.strip().replace(" ", "").upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**DIGITS)).zfill(DIGITS)


def code_now(secret: str, *, at: float | None = None) -> str:
    """The current code for *secret* (used by tests and by `auth totp verify`)."""
    now = time.time() if at is None else at
    return _code_at(secret, int(now // STEP_SECS))


def verify_code(secret: str, code: str, *, at: float | None = None) -> bool:
    """Whether *code* is valid for *secret* now (± `_SKEW_STEPS`).

    Compared with `hmac.compare_digest`, so a wrong code cannot be narrowed down by timing.
    Returns False for a malformed secret rather than raising: a corrupt stored secret must
    fail the login, not 500 the endpoint.
    """
    candidate = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(candidate) != DIGITS or not secret:
        return False
    now = time.time() if at is None else at
    step = int(now // STEP_SECS)
    for drift in range(-_SKEW_STEPS, _SKEW_STEPS + 1):
        try:
            expected = _code_at(secret, step + drift)
        except Exception:
            return False
        if hmac.compare_digest(expected, candidate):
            return True
    return False


def provisioning_uri(secret: str, account: str, *, issuer: str = "PersonalClaw") -> str:
    """An ``otpauth://`` URI for authenticator apps (and QR codes)."""
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECS}"
    )
