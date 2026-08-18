"""Single-use device PAIRING codes (COMPANION-APPS C2 / T1.1).

A sibling of :mod:`personalclaw.auth.enrollment`, deliberately not a merge with it. Both mint
a short code redeemable for one session, but they answer to different surfaces and different
lifetimes: an enrollment code is a recovery affordance minted by `personalclaw auth enroll` at
the box, while a pairing code is minted by the running dashboard, displayed as a QR, and
redeemed by a companion app that then wants to be *named and revocable* — which is the whole
reason its redemption writes device provenance into the session row.

Folding them into one module would mean one function whose behavior forks on a `kind` flag
through five branches, which is how the two surfaces would drift apart while looking unified.
They share their security properties instead of their code path, and those properties are
asserted separately.

**Every property here exists to bound the blast radius of an 8-character string:**

* **single-use** — the record is removed and persisted BEFORE the caller mints a session, so
  two simultaneous redemptions cannot both succeed;
* **short-lived** — :data:`PAIR_CODE_TTL_SECS`. A code that lingers is a password with a
  shorter alphabet;
* **rate-limited by scarcity** — at most :data:`_MAX_ACTIVE` outstanding, so nobody can ask
  for thousands and widen the guess space (the route adds per-IP lockout on top);
* **hashed at rest** — the file stores SHA-256, so reading `pair_codes.json` yields nothing
  redeemable;
* **constant-time compared**, and **fail-closed** on an unreadable store;
* **never logged, never returned twice** — the plaintext exists in one response body and in
  the operator's eyes.

**On telling "expired" apart from "invalid".** The pairing contract requires two distinct
rejections, because they need two different sentences in the UI ("that code has run out, get
a fresh one" is a different instruction from "check what you typed"). The oracle this creates
is that a caller learns a guessed code once existed — negligible against 32^8 over a 300s
window with at most five live, and only ever true for a record still sitting in the file,
since every write prunes expired ones. A shared "invalid" for both would trade a real
usability defect for an imaginary security gain.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

CODES_FILE = "pair_codes.json"

#: Code lifetime in seconds. A pairing code is read off a screen and typed (or scanned) into a
#: device standing next to it, so the honest window is "long enough to walk over".
PAIR_CODE_TTL_SECS = 300

#: Outstanding codes allowed at once. Pairing is a deliberate human act, so a low ceiling costs
#: nothing and keeps the live guess space tiny.
_MAX_ACTIVE = 5

#: Crockford-ish base32 without I/O/0/1 — the ambiguous glyphs, since this gets read off one
#: screen and typed into another. 32^8 ≈ 1.1e12 combinations over a 300s window.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8

#: Redemption outcomes. The two rejections are separate on purpose — see the module note.
RESULT_OK = "ok"
RESULT_INVALID = "invalid"
RESULT_EXPIRED = "expired"


@dataclass(frozen=True)
class Redemption:
    """The outcome of one redemption attempt, plus whatever the issuer labelled the code."""

    result: str
    label: str = ""

    @property
    def ok(self) -> bool:
        return self.result == RESULT_OK


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
        logger.warning("pairing code store unreadable — treating it as empty")
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(codes: dict[str, Any]) -> None:
    path = codes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(codes, indent=2) + "\n", mode=0o600)


def _expires_at(rec: Any) -> float:
    try:
        return float((rec or {}).get("expires_at", 0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _prune(codes: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    cutoff = time.time() if now is None else now
    return {h: rec for h, rec in codes.items() if _expires_at(rec) > cutoff}


def format_code(code: str) -> str:
    """Group as ``XXXX-XXXX`` for reading aloud / off a screen."""
    c = _normalize(code)
    return f"{c[:4]}-{c[4:]}" if len(c) == _CODE_LEN else c


def issue_code(*, label: str = "") -> tuple[str, float]:
    """Mint a code. Returns ``(code, expires_at)``. The PLAINTEXT is returned once, never stored.

    Oldest codes are dropped when at :data:`_MAX_ACTIVE`, so minting a fresh one always works
    rather than failing at a limit the user cannot see.
    """
    codes = _prune(_load())
    if len(codes) >= _MAX_ACTIVE:
        oldest = sorted(codes.items(), key=lambda kv: float(kv[1].get("issued_at", 0)))
        for h, _rec in oldest[: len(codes) - _MAX_ACTIVE + 1]:
            codes.pop(h, None)

    code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    now = time.time()
    expires_at = now + PAIR_CODE_TTL_SECS
    codes[_hash(code)] = {
        "issued_at": now,
        "expires_at": expires_at,
        "label": str(label or "")[:64],
    }
    _save(codes)
    return code, expires_at


def redeem_code(code: str) -> Redemption:
    """Consume *code*. :data:`RESULT_OK` exactly once per issued code.

    The record is removed and persisted **before** the caller mints a session, so two
    simultaneous redemptions cannot both succeed. Comparison is constant-time over the hash.
    """
    candidate = _normalize(code)
    if len(candidate) != _CODE_LEN:
        return Redemption(RESULT_INVALID)

    codes = _load()
    wanted = _hash(candidate)
    matched = ""
    for stored_hash in codes:
        if hmac.compare_digest(stored_hash, wanted):
            matched = stored_hash
            break
    if not matched:
        return Redemption(RESULT_INVALID)

    record = codes.get(matched) or {}
    if _expires_at(record) <= time.time():
        # Drop it while we are here: an expired code is dead weight, and leaving it would keep
        # answering "expired" to the same guess forever.
        codes.pop(matched, None)
        try:
            _save(_prune(codes))
        except OSError:
            logger.debug("could not prune an expired pairing code", exc_info=True)
        return Redemption(RESULT_EXPIRED)

    codes.pop(matched, None)
    try:
        _save(_prune(codes))
    except OSError:
        # If the consumption cannot be persisted, REFUSE — a code that stays redeemable is
        # worse than a pairing the user has to retry.
        logger.error("could not persist pairing-code consumption — refusing the redemption")
        return Redemption(RESULT_INVALID)
    return Redemption(RESULT_OK, label=str(record.get("label") or ""))


# `enrollment` grew an `active_codes()` count and a `clear_codes()` panic button. Neither is
# shipped here: neither has a caller on this surface, and a public function nobody calls is a
# claim nobody checks. The scarcity assertion reads the store directly instead.
