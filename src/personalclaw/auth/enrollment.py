"""Single-use device enrollment codes (REMOTE-USER-AUTH C3 / T4.3).

The problem this solves: you are holding a phone, off your home network, and you want it to
have a durable session. Typing a long password into a phone keyboard over a tunnel is both
unpleasant and the worst place to expose the credential — phone keyboards autocomplete, screens
are shoulder-surfable, and the password is the one secret that must not be spent casually.

So: mint a short code **locally** (`personalclaw auth enroll`, which requires being at the box
or already authenticated), read it off the screen, type it into the phone once. The code is
worth exactly one session and nothing else.

**Every property here exists to bound the blast radius of an 8-character string:**

* **single-use** — redeemed codes are removed before the session is minted, so a race cannot
  redeem twice;
* **short-lived** — 300s, per the plan. A code that lingers is a password with a shorter
  alphabet;
* **rate-limited by scarcity** — at most `_MAX_ACTIVE` outstanding at once, so an attacker
  cannot ask for thousands and widen the guess space;
* **hashed at rest** — the file stores a SHA-256 of the code, so reading `enroll_codes.json`
  does not yield a redeemable credential;
* **constant-time compared**, and **fail-closed** on an unreadable store.

Codes live in their own 0600 file rather than in `sessions.json`: they are not sessions, and
mixing a pre-auth artifact into the post-auth store is how one bug becomes two.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

CODES_FILE = "enroll_codes.json"

#: Code lifetime in seconds (the plan's TTL 300s).
CODE_TTL_SECS = 300

#: Outstanding codes allowed at once. Enrollment is a deliberate human act — you mint one and
#: walk to your phone — so a low ceiling costs nothing and keeps the live guess space tiny.
_MAX_ACTIVE = 5

#: Crockford-ish base32 without I/O/0/1 — the ambiguous glyphs, since this gets read off one
#: screen and typed into another. 32^8 ≈ 1.1e12 combinations, over a 300s window, with at most
#: 5 live at a time.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


def codes_path() -> Path:
    return config_dir() / "auth" / CODES_FILE


def _hash(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode()).hexdigest()


def _normalize(code: str) -> str:
    """Uppercase, strip separators. A code read off a screen gets typed with a dash."""
    return "".join(ch for ch in (code or "").upper() if ch in _ALPHABET)


def _load() -> dict[str, Any]:
    path = codes_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Fail closed: an unreadable store means "no valid codes", never "accept anything".
        logger.warning("enrollment code store unreadable — treating it as empty")
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(codes: dict[str, Any]) -> None:
    path = codes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(codes, indent=2) + "\n", mode=0o600)


def _prune(codes: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    cutoff = time.time() if now is None else now
    return {h: rec for h, rec in codes.items() if float(rec.get("expires_at", 0)) > cutoff}


def format_code(code: str) -> str:
    """Group as ``XXXX-XXXX`` for reading aloud / off a screen."""
    c = _normalize(code)
    return f"{c[:4]}-{c[4:]}" if len(c) == _CODE_LEN else c


def issue_code(*, label: str = "") -> tuple[str, float]:
    """Mint a code. Returns ``(code, expires_at)``. The PLAINTEXT is returned once, never stored.

    Oldest codes are dropped when at `_MAX_ACTIVE`, so minting a fresh one always works rather
    than failing at a limit the user cannot see.
    """
    codes = _prune(_load())
    if len(codes) >= _MAX_ACTIVE:
        oldest = sorted(codes.items(), key=lambda kv: float(kv[1].get("issued_at", 0)))
        for h, _rec in oldest[: len(codes) - _MAX_ACTIVE + 1]:
            codes.pop(h, None)

    code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    now = time.time()
    expires_at = now + CODE_TTL_SECS
    codes[_hash(code)] = {
        "issued_at": now,
        "expires_at": expires_at,
        "label": str(label or "")[:64],
    }
    _save(codes)
    _audit("enroll_code_issued", "ok")
    return code, expires_at


def redeem_code(code: str) -> bool:
    """Consume *code*. True exactly once per issued code; False for unknown/expired/reused.

    The record is removed and persisted **before** the caller mints a session, so two
    simultaneous redemptions cannot both succeed. Comparison is constant-time over the hash.
    """
    candidate = _normalize(code)
    if len(candidate) != _CODE_LEN:
        _audit("enroll_completed", "denied", "malformed code")
        return False

    codes = _prune(_load())
    wanted = _hash(candidate)
    matched = ""
    for stored_hash in codes:
        if hmac.compare_digest(stored_hash, wanted):
            matched = stored_hash
            break
    if not matched:
        _audit("enroll_completed", "denied", "unknown or expired code")
        return False

    codes.pop(matched, None)
    try:
        _save(codes)
    except OSError:
        # If the consumption cannot be persisted, REFUSE — a code that stays redeemable is
        # worse than an enrollment the user has to retry.
        logger.error("could not persist enrollment-code consumption — refusing the redemption")
        _audit("enroll_completed", "denied", "could not consume the code")
        return False
    _audit("enroll_completed", "ok")
    return True


def active_codes() -> int:
    """How many codes are outstanding (never the codes themselves)."""
    return len(_prune(_load()))


def clear_codes() -> None:
    """Drop every outstanding code (`auth enroll --clear`, and test isolation)."""
    _save({})


def _audit(operation: str, outcome: str, error: str = "") -> None:
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="owner",
            operation=operation,
            outcome=outcome,
            source="auth",
            error=error,
        )
    except Exception:
        logger.debug("SEL audit failed for %s", operation, exc_info=True)
