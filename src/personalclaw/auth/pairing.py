"""Single-use device PAIRING codes.

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

**A scoped code lives in its own file.** A code made for an automation client
(``auth.automation_scopes``) carries its scope and its session's lifetime, and is kept in
:data:`SCOPED_CODES_FILE`, beside the unscoped one. A build from before scoped pairing reads only
:data:`CODES_FILE`, so after a downgrade a live scoped code can't be redeemed there as a full
session. The outstanding cap counts both files, and :func:`redeem_code` looks in both.

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
from personalclaw.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

CODES_FILE = "pair_codes.json"
SCOPED_CODES_FILE = "pair_codes.scoped.json"

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


#: A scoped code's session lifetime, in seconds: one day at least, the 90-day limit at most.
MIN_LIFETIME_SECS = 86400
MAX_LIFETIME_SECS = 90 * 86400


@dataclass(frozen=True)
class Redemption:
    """The outcome of one redemption attempt, plus whatever the issuer labelled the code, and a
    scoped code's scope and session lifetime (``()`` and ``0`` for an unscoped one)."""

    result: str
    label: str = ""
    scope: tuple[str, ...] = ()
    lifetime_secs: int = 0

    @property
    def ok(self) -> bool:
        return self.result == RESULT_OK


def codes_path() -> Path:
    return config_dir() / "auth" / CODES_FILE


def scoped_codes_path() -> Path:
    return config_dir() / "auth" / SCOPED_CODES_FILE


def _hash(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode()).hexdigest()


def _normalize(code: str) -> str:
    """Uppercase, strip separators. A code read off a screen gets typed with a dash."""
    return "".join(ch for ch in (code or "").upper() if ch in _ALPHABET)


def _load(path: Path | None = None) -> dict[str, Any]:
    path = path or codes_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Fail closed: an unreadable store means "no valid codes", never "accept anything".
        logger.warning("pairing code store unreadable — treating it as empty")
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(codes: dict[str, Any], path: Path | None = None) -> None:
    path = path or codes_path()
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


def _checked_scope(scope: Any, lifetime_secs: Any) -> tuple[tuple[str, ...], int]:
    """A scoped code's ``(scope, lifetime_secs)``, or ``ValueError``."""
    from personalclaw.auth.automation_scopes import normalize_scope

    normalized = normalize_scope(scope)
    if not isinstance(lifetime_secs, int) or isinstance(lifetime_secs, bool):
        raise ValueError("a scoped code's lifetime is a whole number of seconds")
    if not MIN_LIFETIME_SECS <= lifetime_secs <= MAX_LIFETIME_SECS:
        raise ValueError("a scoped code's lifetime is 1 to 90 days")
    return normalized, lifetime_secs


def issue_code(
    *, label: str = "", scope: tuple[str, ...] = (), lifetime_secs: int = 0
) -> tuple[str, float]:
    """Mint a code. Returns ``(code, expires_at)``. The PLAINTEXT is returned once, never stored.

    With *scope*, the code is a scoped pairing's: it keeps the scope and *lifetime_secs* (the
    session's, 1 to 90 days) in :data:`SCOPED_CODES_FILE`. A scope outside the vocabulary, a
    lifetime out of range, or a lifetime with no scope raises ``ValueError`` and stores nothing.

    Oldest codes are dropped when at :data:`_MAX_ACTIVE`, counted over both files, so minting a
    fresh one always works rather than failing at a limit the user cannot see.
    """
    if scope:
        scope, lifetime_secs = _checked_scope(scope, lifetime_secs)
    elif lifetime_secs:
        raise ValueError("only a scoped code carries a lifetime")
    plain, scoped = _prune(_load()), _prune(_load(scoped_codes_path()))
    held = [(h, rec, plain) for h, rec in plain.items()]
    held += [(h, rec, scoped) for h, rec in scoped.items()]
    if len(held) >= _MAX_ACTIVE:
        held.sort(key=lambda item: float((item[1] or {}).get("issued_at", 0)))
        for h, _rec, codes in held[: len(held) - _MAX_ACTIVE + 1]:
            codes.pop(h, None)

    code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    now = time.time()
    expires_at = now + PAIR_CODE_TTL_SECS
    record: dict[str, Any] = {
        "issued_at": now,
        "expires_at": expires_at,
        "label": str(label or "")[:64],
    }
    if scope:
        record.update(scope=list(scope), lifetime_secs=lifetime_secs)
        scoped[_hash(code)] = record
    else:
        plain[_hash(code)] = record
    _save(plain)
    if scope or scoped_codes_path().is_file():
        _save(scoped, scoped_codes_path())
    return code, expires_at


def redeem_code(code: str) -> Redemption:
    """Consume *code*. :data:`RESULT_OK` exactly once per issued code.

    The record is removed and persisted **before** the caller mints a session, so two
    simultaneous redemptions cannot both succeed. Comparison is constant-time over the hash.
    """
    candidate = _normalize(code)
    if len(candidate) != _CODE_LEN:
        return Redemption(RESULT_INVALID)

    wanted = _hash(candidate)
    for path, scoped in ((codes_path(), False), (scoped_codes_path(), True)):
        codes = _load(path)
        matched = ""
        for stored_hash in codes:
            if hmac.compare_digest(stored_hash, wanted):
                matched = stored_hash
                break
        if matched:
            return _consume(codes, matched, path, scoped=scoped)
    return Redemption(RESULT_INVALID)


def _consume(codes: dict[str, Any], matched: str, path: Path, *, scoped: bool) -> Redemption:
    """Redeem *matched* from the *codes* stored at *path*: removed and persisted first."""
    record = codes.get(matched) or {}
    if _expires_at(record) <= time.time():
        # Drop it while we are here: an expired code is dead weight, and leaving it would keep
        # answering "expired" to the same guess forever.
        codes.pop(matched, None)
        try:
            _save(_prune(codes), path)
        except OSError:
            logger.debug("could not prune an expired pairing code", exc_info=True)
        return Redemption(RESULT_EXPIRED)

    codes.pop(matched, None)
    try:
        _save(_prune(codes), path)
    except OSError:
        # If the consumption cannot be persisted, REFUSE — a code that stays redeemable is
        # worse than a pairing the user has to retry.
        logger.error("could not persist pairing-code consumption — refusing the redemption")
        return Redemption(RESULT_INVALID)
    label = str(record.get("label") or "") if isinstance(record, dict) else ""
    if not scoped:
        # An unscoped code that names a scope was written by nothing this gateway does: refused,
        # never redeemed as the full session an older build would read it as.
        if isinstance(record, dict) and ("scope" in record or "lifetime_secs" in record):
            return Redemption(RESULT_INVALID)
        return Redemption(RESULT_OK, label=label)
    try:
        scope, lifetime = _checked_scope(record.get("scope"), record.get("lifetime_secs"))
    except (AttributeError, ValueError):
        # Fail closed: a scoped code whose scope or lifetime doesn't read is no code at all,
        # never an unscoped one.
        logger.warning("a scoped pairing code did not carry a scope it can hold — refused")
        return Redemption(RESULT_INVALID)
    return Redemption(RESULT_OK, label=label, scope=scope, lifetime_secs=lifetime)


# `enrollment` grew an `active_codes()` count and a `clear_codes()` panic button. Neither is
# shipped here: neither has a caller on this surface, and a public function nobody calls is a
# claim nobody checks. The scarcity assertion reads the store directly instead.
