"""Durable signing key + session records (REMOTE-USER-AUTH S1).

Before this, `token_auth._SECRET = os.urandom(32)` ran at module scope and the valid-nonce
set lived in memory. Both consequences were the same: **every gateway restart invalidated
every token.** On a local box that means re-running `personalclaw token` after each restart;
off-network it means you cannot get back in at all, because minting a fresh URL requires
being on the machine. That is the concrete pain this fixes.

Two pieces of state, deliberately separate files:

* **the signing key** (`session_key`) — 32 random bytes, 0600. Rotating it invalidates
  everything at once, which is exactly what you want from a panic button and exactly what
  you do NOT want to happen accidentally on reboot.
* **the session records** (`sessions.json`) — one entry per minted nonce with its expiry,
  so a token minted before a restart still verifies afterwards.

**Why a file and not the credential store:** the key must be readable during middleware
setup, before any provider or keychain prompt can run, and on a headless box there may be no
keychain at all. A 0600 file in the config dir is the same trust level as
`.local_secret`, which already guards the same surface.

**Failure posture is FAIL-CLOSED, unlike most of this codebase.** If the key cannot be read
or written, `load_or_create_key()` raises rather than falling back to an ephemeral key.
A silent fallback would look identical to working — until the next restart logged everyone
out again, which is the bug being fixed. An auth surface that cannot persist its trust root
should refuse to pretend otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

KEY_FILE = "session_key"
SESSIONS_FILE = "sessions.json"

#: Signing-key length. 32 bytes matches the HMAC-SHA256 block security and the previous
#: `os.urandom(32)`, so the bump changes durability without changing strength.
KEY_BYTES = 32

#: Cap on stored session records. A record is ~100 bytes; this bounds the file at ~200 KB
#: even if something mints tokens in a loop. Oldest-expiring are dropped first.
MAX_SESSIONS = 2000


def key_path() -> Path:
    return config_dir() / KEY_FILE


def sessions_path() -> Path:
    return config_dir() / SESSIONS_FILE


def load_or_create_key() -> bytes:
    """The persistent signing key, creating it on first use.

    Raises ``OSError`` when the key can neither be read nor written — see the module note on
    fail-closed. A caller that genuinely wants ephemeral behavior (tests, `--test-mode`) asks
    for it explicitly rather than getting it from a swallowed error.
    """
    path = key_path()
    try:
        if path.is_file():
            raw = path.read_bytes()
            if len(raw) >= KEY_BYTES:
                _ensure_owner_only(path)
                return raw
            # A short key is corruption, not a valid smaller key: refuse to sign with it.
            logger.warning(
                "session key at %s is too short (%d bytes) — regenerating", path, len(raw)
            )
    except OSError:
        logger.warning("session key unreadable at %s", path, exc_info=True)

    key = os.urandom(KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mode=0o600 from creation: a key that is briefly world-readable has already leaked.
    atomic_write_bytes(path, key, mode=0o600)
    logger.info("created a persistent session signing key at %s", path)
    return key


def rotate_key() -> bytes:
    """Replace the signing key, invalidating every existing token. Returns the new key."""
    key = os.urandom(KEY_BYTES)
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, key, mode=0o600)
    # The records are meaningless under a new key — a nonce whose signature can no longer be
    # verified is not a session, it is 100 bytes of noise that would outlive its own expiry.
    clear_sessions()
    logger.info("rotated the session signing key; all existing tokens are now invalid")
    return key


def _ensure_owner_only(path: Path) -> None:
    """Tighten the key file to 0600 if something loosened it.

    Not merely cosmetic: a key readable by another local account is a key that account can
    use to mint a dashboard session for itself.
    """
    try:
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            path.chmod(0o600)
            logger.warning("session key had mode %o — tightened to 0600", mode)
    except OSError:
        logger.debug("could not verify session key permissions", exc_info=True)


# ── Session records ─────────────────────────────────────────────────────


def load_sessions() -> dict[str, float]:
    """``{nonce: session_exp}`` for every stored session, expired ones dropped.

    Returns ``{}`` on any read failure. Fail-CLOSED in effect: an unreadable store means no
    nonce validates, so tokens are rejected rather than blanket-accepted.
    """
    path = sessions_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("session store unreadable — treating every session as absent")
        return {}
    if not isinstance(raw, dict):
        return {}
    now = time.time()
    out: dict[str, float] = {}
    for nonce, exp in (raw.get("sessions") or {}).items():
        try:
            expiry = float(exp)
        except (TypeError, ValueError):
            continue
        if expiry > now:
            out[str(nonce)] = expiry
    return out


def save_sessions(sessions: dict[str, float]) -> None:
    """Persist *sessions*, dropping expired entries and capping the total."""
    now = time.time()
    live = {n: e for n, e in sessions.items() if e > now}
    if len(live) > MAX_SESSIONS:
        # Keep the LONGEST-lived: a session about to expire anyway is the cheapest to lose.
        live = dict(sorted(live.items(), key=lambda kv: kv[1], reverse=True)[:MAX_SESSIONS])
    path = sessions_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps({"sessions": live}, indent=2) + "\n", mode=0o600)
    except OSError:
        logger.warning("could not persist the session store", exc_info=True)


def remember_session(nonce: str, expiry: float) -> None:
    """Record one minted session so it survives a restart."""
    if not nonce:
        return
    sessions = load_sessions()
    sessions[nonce] = float(expiry)
    save_sessions(sessions)


def forget_session(nonce: str) -> None:
    """Drop one session (logout / eviction)."""
    sessions = load_sessions()
    if sessions.pop(nonce, None) is not None:
        save_sessions(sessions)


def clear_sessions() -> None:
    """Drop every stored session."""
    save_sessions({})


def session_stats() -> dict[str, Any]:
    """Counts for the doctor / status surface — never the nonces themselves."""
    sessions = load_sessions()
    return {
        "sessions": len(sessions),
        "key_present": key_path().is_file(),
        "soonest_expiry": min(sessions.values(), default=0.0),
    }
