"""The owner login credential (REMOTE-USER-AUTH C2 / T2.1).

One owner, one credential set. This is **authentication, not multi-tenancy** — the plan's soul
guardrail — so there is deliberately no user table, no roles, and no signup. A username exists
only so the login form has a subject and so it can later graduate into an SSO-provisioned one
(TEAM-SHARED-ENTITIES' identity string).

**argon2id, not a hand-rolled PBKDF2.** Memory-hard and tunable, which is the current
recommendation for password storage; rolling our own over `hashlib` to avoid one small wheel
would be the wrong trade on a credential path.

**What is stored, and what is deliberately not.** `auth/credentials.json` (0600) holds the
username, the argon2 hash, the algorithm tag and a timestamp. It does **not** hold:

* the plaintext — obviously, and it is never logged, never put in argv, never returned by an
  API, and never included in a status payload;
* the TOTP secret — that is a *secret*, so it goes to the credential store (`.env`, 0600) via
  `save_credential`, not into a JSON file that the snapshot/export set might later sweep up.

**Failure posture is FAIL-CLOSED.** An unreadable or malformed credential file means "no
credential configured", which means login cannot succeed — never "let them in". The local
`?token=` path is the escape hatch precisely so that failing closed here cannot brick the box.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = "credentials.json"
ALGO = "argon2id"

#: The env key the TOTP secret lives under, in the credential store — never in the JSON.
TOTP_SECRET_KEY = "PERSONALCLAW_TOTP_SECRET"

#: Minimum password length. Not a strength meter — a floor. A one-character password on an
#: internet-exposed box is the failure this whole plan exists to prevent, and a floor is the
#: one check that cannot be argued with. Deliberately not a complexity rule: length beats
#: character classes, and rules that force `P@ssw0rd!` make things worse.
MIN_PASSWORD_LEN = 12

#: argon2id parameters. Defaults from `argon2-cffi`'s own RFC 9106-informed profile rather
#: than numbers invented here; a login verify runs once per session, so the cost is paid at a
#: moment where ~50ms is invisible and brute-force resistance is the whole point.
_TIME_COST = 3
_MEMORY_COST = 65536  # 64 MiB
_PARALLELISM = 4


def auth_dir() -> Path:
    return config_dir() / "auth"


def credentials_path() -> Path:
    return auth_dir() / CREDENTIALS_FILE


def _hasher():
    """The argon2 hasher, imported lazily so a missing wheel degrades legibly.

    `argon2-cffi` is a core dependency, so this should not fail — but if an install is
    somehow stripped, the caller gets a typed error naming the remedy rather than an
    ImportError from deep inside a login request.
    """
    try:
        from argon2 import PasswordHasher
    except ImportError as exc:  # pragma: no cover - core dep
        raise CredentialError(
            "password hashing is unavailable (argon2-cffi missing) — "
            "reinstall personalclaw or `pip install argon2-cffi`"
        ) from exc
    return PasswordHasher(time_cost=_TIME_COST, memory_cost=_MEMORY_COST, parallelism=_PARALLELISM)


class CredentialError(Exception):
    """A credential could not be stored or read (never raised for a wrong password)."""


def load_credentials() -> dict[str, Any]:
    """The stored credential record, or ``{}`` when absent/unreadable (fail-closed).

    ``{}`` means "no credential configured", so `verify_password` returns False and login
    cannot succeed. It never means "allow".
    """
    path = credentials_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("auth credentials unreadable — treating login as unconfigured")
        return {}
    return raw if isinstance(raw, dict) else {}


def has_credentials() -> bool:
    """Whether a usable owner credential exists."""
    rec = load_credentials()
    return bool(rec.get("username")) and bool(rec.get("password_hash"))


def set_password(username: str, plaintext: str) -> None:
    """Store *username* + the argon2id hash of *plaintext*, replacing any existing pair.

    Raises ``ValueError`` for an empty username or a password under `MIN_PASSWORD_LEN`, and
    ``CredentialError`` when the file cannot be written. The plaintext is never logged and is
    not retained after hashing.
    """
    user = (username or "").strip()
    if not user:
        raise ValueError("username is required")
    if len(plaintext or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")

    digest = _hasher().hash(plaintext)
    record = {
        "username": user,
        "password_hash": digest,
        "algo": ALGO,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "totp_enabled": bool(load_credentials().get("totp_enabled")),
    }
    path = credentials_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(record, indent=2) + "\n", mode=0o600)
    except OSError as exc:
        raise CredentialError(f"could not write {path}: {exc}") from exc
    _audit("password_set", user, "ok")
    logger.info("owner login credential set for %r", user)


def verify_password(username: str, plaintext: str) -> bool:
    """Whether *username*/*plaintext* match the stored credential.

    Runs the argon2 verify **even when there is no stored credential or the username does not
    match**, against a dummy hash. Returning early would make "no such user" measurably faster
    than "wrong password", which tells an attacker on an exposed box whether they have the
    username right — the one piece of information they most want. The plaintext is never logged.
    """
    rec = load_credentials()
    stored_user = str(rec.get("username") or "")
    stored_hash = str(rec.get("password_hash") or "")

    # A real argon2 hash of a value nobody can supply, so the timing profile of the failure
    # path matches the success path.
    if not stored_hash:
        stored_hash = _DUMMY_HASH
    hasher = _hasher()
    try:
        ok_password = bool(hasher.verify(stored_hash, plaintext or ""))
    except Exception:
        ok_password = False
    # Compare the username in constant time too, and only AFTER the hash work.
    ok_user = secrets.compare_digest((username or "").strip().encode(), stored_user.encode())
    return bool(ok_password and ok_user and stored_user)


def clear_credentials() -> bool:
    """Remove the stored credential. Returns whether one existed."""
    path = credentials_path()
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError as exc:
        raise CredentialError(f"could not remove {path}: {exc}") from exc
    _audit("password_cleared", "", "ok")
    return True


def status() -> dict[str, Any]:
    """A safe summary for the CLI / Settings — never the hash, never the TOTP secret."""
    rec = load_credentials()
    return {
        "configured": has_credentials(),
        "username": str(rec.get("username") or ""),
        "algo": str(rec.get("algo") or ""),
        "updated_at": str(rec.get("updated_at") or ""),
        "totp_enabled": bool(rec.get("totp_enabled")),
    }


# ── TOTP (S4 consumes this; the secret lives in the credential store) ────


def set_totp_secret(secret: str) -> None:
    """Store the TOTP secret in the credential store and mark TOTP enabled.

    Deliberately NOT in credentials.json: a TOTP secret is a second factor, and putting it
    beside the password hash in a file the snapshot set may sweep would make one leak two.
    """
    from personalclaw.config.loader import save_credential

    save_credential(TOTP_SECRET_KEY, secret)
    _set_flag("totp_enabled", True)


def totp_secret() -> str:
    """The stored TOTP secret, or "" — read from the environment/credential store."""
    return str(os.environ.get(TOTP_SECRET_KEY, "") or "")


def disable_totp() -> None:
    """Turn TOTP off. The secret is left in the credential store for deliberate re-enable."""
    _set_flag("totp_enabled", False)


def _set_flag(name: str, value: bool) -> None:
    rec = load_credentials()
    if not rec:
        return
    rec[name] = bool(value)
    rec["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    try:
        atomic_write(credentials_path(), json.dumps(rec, indent=2) + "\n", mode=0o600)
    except OSError as exc:
        raise CredentialError(f"could not update the credential record: {exc}") from exc


def _audit(operation: str, caller: str, outcome: str, error: str = "") -> None:
    """SEL trail. Best-effort — an audit failure must not break a credential operation."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=caller or "owner",
            operation=operation,
            outcome=outcome,
            source="auth",
            error=error,
        )
    except Exception:
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


def bootstrap_from_env() -> bool:
    """Enroll a credential from the environment on first start (T2.4). Returns whether it ran.

    For container/systemd installs, where there is no terminal to type a password at. Reads
    ``PERSONALCLAW_LOGIN_USER`` + ``PERSONALCLAW_LOGIN_PASSWORD`` and enrolls them ONCE.

    Three deliberate properties:

    * **It never overwrites.** If a credential already exists this is a no-op, so leaving the
      variables set in a unit file cannot silently reset the password back to the deploy-time
      one on every restart — which would quietly undo a rotation.
    * **It does not enable login.** Enrolling a credential and opening a front door are
      separate decisions; `auth.login_enabled` stays whatever the config says.
    * **It fails loudly but non-fatally.** A too-short password logs an error and leaves login
      unconfigured rather than aborting startup: a gateway that will not boot is worse than
      one you have to set a password on, and the local token path still works.

    The variables are read from the process environment, so they inherit whatever secrecy the
    deployment gives them (Docker secret, systemd ``EnvironmentFile`` with 0600, etc.). They
    are consumed and never echoed, and the values are never written anywhere but the hash.
    """
    user = str(os.environ.get("PERSONALCLAW_LOGIN_USER", "") or "").strip()
    password = str(os.environ.get("PERSONALCLAW_LOGIN_PASSWORD", "") or "")
    if not user or not password:
        return False
    if has_credentials():
        logger.debug("login credential already set — ignoring PERSONALCLAW_LOGIN_* variables")
        return False
    try:
        set_password(user, password)
    except (ValueError, CredentialError) as exc:
        # No password material in the message — `exc` is our own text ("at least N
        # characters" / a path), never the plaintext.
        logger.error("could not enroll the login credential from the environment: %s", exc)
        _audit("password_set", user, "denied", error=str(exc))
        return False
    logger.info("enrolled the owner login credential for %r from the environment", user)
    return True


def _make_dummy_hash() -> str:
    """A real argon2 hash used only to equalize the failure path's timing."""
    try:
        return _hasher().hash(secrets.token_hex(16))
    except Exception:  # pragma: no cover - core dep
        return ""


_DUMMY_HASH = _make_dummy_hash()
