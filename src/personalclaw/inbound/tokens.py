"""How long an integration's token works, and what a token that stopped working is told.

Two kinds of credential let an external agent reach one of the five inbound surfaces (ledger
317a): a **surface token** (``personalclaw inbound token create <surface>``, one per surface, a
bare secret in the credential store or the environment) and a **client token** (``POST
/api/external-access/clients``, one per registered integration, stored as a hash in
``inbound_clients.json``). Neither had a lifetime, so a token pasted into an editor's config
long ago still worked. Both now last at most 90 days (``auth.lifetimes.MAX_LIFETIME_SECS``), are
listed in Settings → Devices with a revoke, and write the same sign-in and sign-out rows to the
security log that a dashboard session does (``auth.signins``).

**A lifetime belongs to a token, not to a surface.** ``inbound_tokens.json`` (0600) maps the
SHA-256 of every surface token this gateway has seen to when it was issued, when it stops
working, and whether it was revoked or replaced — never the token itself. A token keeps its
lifetime wherever it is configured, and nothing that briefly hides the configured value (a locked
keychain, an unset environment variable) touches its record: a value that cannot be read changes
nothing here, and is found again, with the lifetime it had, once it can.

**A token never seen before is recorded when it is first seen**, and lasts 90 days from then:
one created before lifetimes existed, set through Settings → Secrets, or injected by the
environment. When it was really made was never recorded, and a guess would be a lie.

**A revoked, expired or replaced token stays refused while it is configured.** Its record is
never dropped while it could still be the configured value, and a replaced token set again — by
hand, from a backup, or by a process still holding its old copy — is refused rather than brought
back. Only the record of a token that was replaced is ever forgotten, a year after the
replacement. A client's lifetime is on its own record (``InboundClient.expires_at``); one
registered before lifetimes existed ends 90 days after its ``created_at``.

**Whoever holds a token that stopped working is told why** (:func:`refusal`): it expired, was
revoked or was replaced, and when. A token this gateway never issued is told nothing more than
the uniform refusal every surface gives, and the code a script branches on is the same either
way.

**An unreadable registry refuses.** While ``inbound_tokens.json`` cannot be read or written,
every surface token is refused: recording each one as newly seen would give it a fresh 90 days.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.auth.lifetimes import MAX_LIFETIME_SECS, duration_words, when_words
from personalclaw.config.external_access import WEBHOOK_SURFACE as _WEBHOOK

logger = logging.getLogger(__name__)

_FILE = "inbound_tokens.json"
_VERSION = 1

#: What an integration token lasts unless a shorter lifetime is asked for: the limit.
INTEGRATION_TTL_SECS = MAX_LIFETIME_SECS

#: How long a replaced surface token's record is kept: its holder is told it was replaced, and
#: an old value restored from a backup keeps the lifetime it had. Past this, it counts as new.
REPLACED_RETENTION_SECS = 365 * 86400

#: How long a revoked client's token is remembered, so its holder is told it was revoked. The
#: client's record is gone, so the token cannot work again whatever this remembers.
CLIENT_ENDED_RETENTION_SECS = 7 * 86400

#: A surface token's last use is written at most this often: the list says when it was last
#: used, and a write per request would be the cost of saying it.
_LAST_SEEN_EVERY_SECS = 300.0

#: What a token's record says about it.
LIVE = "live"
EXPIRED = "expired"
REVOKED = "revoked"
REPLACED = "replaced"

#: The words the owner knows each surface by.
SURFACE_NAMES = {
    "openai": "OpenAI-compatible",
    "mcp": "MCP",
    "a2a": "A2A",
    "capture": "capture proxy",
    "bridge": "control bridge",
    "webhook": "webhook",
}

_lock = threading.Lock()
_open = threading.local()


class RegistryUnavailable(Exception):
    """``inbound_tokens.json`` cannot be read or written: every surface token refuses."""


def token_hash(token: str) -> str:
    """The form a token is recorded under: SHA-256 hex, never the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def surface_name(surface: str) -> str:
    return SURFACE_NAMES.get(surface, surface)


def create_command(surface: str) -> str:
    return f"personalclaw inbound token create {surface} --rotate"


@dataclass
class SurfaceToken:
    """What is known about one surface token."""

    surface: str
    token_hash: str
    issued_at: float
    expires_at: float
    #: True when this was recorded the first time it was seen rather than when it was made.
    found: bool = False
    revoked_at: float = 0.0
    revoked_by: str = ""
    replaced_at: float = 0.0
    last_seen: float = 0.0

    def state(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        if self.revoked_at:
            return REVOKED
        if self.replaced_at:
            return REPLACED
        if self.expires_at <= now:
            return EXPIRED
        return LIVE

    def usable(self, now: float | None = None) -> bool:
        return self.state(now) == LIVE

    def to_row(self) -> dict[str, Any]:
        return {
            "kind": "surface",
            "surface": self.surface,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "found": self.found,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "replaced_at": self.replaced_at,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_row(cls, digest: str, row: Any) -> SurfaceToken | None:
        if not isinstance(row, dict) or row.get("kind") != "surface":
            return None
        try:
            return cls(
                surface=str(row.get("surface") or ""),
                token_hash=digest,
                issued_at=float(row.get("issued_at") or 0.0),
                expires_at=float(row.get("expires_at") or 0.0),
                found=row.get("found") is True,
                revoked_at=float(row.get("revoked_at") or 0.0),
                revoked_by=str(row.get("revoked_by") or ""),
                replaced_at=float(row.get("replaced_at") or 0.0),
                last_seen=float(row.get("last_seen") or 0.0),
            )
        except (TypeError, ValueError):
            return None


# ── the registry ─────────────────────────────────────────────────────────────


def registry_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _FILE


def _read() -> dict[str, Any]:
    path = registry_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": _VERSION, "tokens": {}}
    except OSError as exc:
        raise RegistryUnavailable(f"{path} cannot be read: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RegistryUnavailable(f"{path} is not valid JSON") from exc
    rows = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        raise RegistryUnavailable(f"{path} holds no token list")
    return {
        "version": _VERSION,
        "tokens": {str(k): v for k, v in rows.items() if isinstance(v, dict)},
    }


def _write(data: dict[str, Any]) -> None:
    from personalclaw.atomic_write import atomic_write

    path = registry_path()
    try:
        atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o600)
    except OSError as exc:
        raise RegistryUnavailable(f"{path} cannot be written: {exc}") from exc


@contextmanager
def _transaction() -> Iterator[dict[str, Any]]:
    """The registry, read, then written back if the body changed it — under one lock that holds
    across threads and processes, because the CLI and the gateway both write it. A call made
    inside an open transaction on the same thread joins it."""
    joined = getattr(_open, "data", None)
    if joined is not None:
        yield joined
        return
    path = registry_path()
    with _lock:
        try:
            from personalclaw.atomic_write import ensure_private_dir
            from personalclaw.durability.home_paths import LinkInTheWay, open_lock

            ensure_private_dir(path.parent)
            handle = open_lock(path.parent / f".{_FILE}.lock")  # closed by the `with` below
        except (OSError, LinkInTheWay) as exc:
            raise RegistryUnavailable(f"{path} cannot be locked: {exc}") from exc
        with handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                data = _read()
                before = json.dumps(data, sort_keys=True)
                _open.data = data
                try:
                    yield data
                finally:
                    _open.data = None
                if json.dumps(data, sort_keys=True) != before:
                    _write(data)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _prune(data: dict[str, Any], now: float) -> None:
    """Forget what can no longer matter: a replaced token's record a year on, a revoked
    client's a week on. A revoked or expired token that is not replaced is never forgotten — it
    may still be the configured value, and forgetting it would let it be found as new."""
    for digest, row in list(data["tokens"].items()):
        if row.get("kind") == "client":
            ended_at, keep_for = float(row.get("at") or 0.0), CLIENT_ENDED_RETENTION_SECS
        elif row.get("kind") == "surface" and row.get("replaced_at"):
            ended_at, keep_for = float(row.get("replaced_at") or 0.0), REPLACED_RETENTION_SECS
        else:
            continue
        if now - ended_at > keep_for:
            del data["tokens"][digest]


def _of_surface(data: dict[str, Any], surface: str) -> list[SurfaceToken]:
    records = (SurfaceToken.from_row(d, row) for d, row in data["tokens"].items())
    return [r for r in records if r is not None and r.surface == surface]


def _settled(data: dict[str, Any], surface: str, digest: str) -> SurfaceToken | None:
    """The record for *digest* when there is nothing to record: it was replaced (and stays so),
    or it already is *surface*'s one current token. None when it must be recorded."""
    record = SurfaceToken.from_row(digest, data["tokens"].get(digest))
    if record is not None and record.replaced_at:
        return record
    if record is None or record.surface != surface:
        return None
    for other in _of_surface(data, surface):
        if other.token_hash != digest and not other.replaced_at:
            return None
    return record


def _install(data: dict[str, Any], record: SurfaceToken, now: float) -> list[SurfaceToken]:
    """Make *record* its surface's one current token. Returns the live tokens it replaced."""
    record.replaced_at = 0.0
    data["tokens"][record.token_hash] = record.to_row()
    replaced: list[SurfaceToken] = []
    for other in _of_surface(data, record.surface):
        if other.token_hash == record.token_hash or other.replaced_at:
            continue
        if other.usable(now):
            replaced.append(other)
        other.replaced_at = now
        data["tokens"][other.token_hash] = other.to_row()
    _prune(data, now)
    return replaced


def _resolve(
    data: dict[str, Any], surface: str, digest: str, now: float
) -> tuple[SurfaceToken, bool, list[SurfaceToken]]:
    """``(record, found, replaced)`` for the value *digest*, configured on *surface*: its
    record, whether it was recorded just now because it was new, and the live tokens it
    replaced.

    A token that was replaced stays replaced, whatever is configured: it was replaced for a
    reason (it leaked, it was lost), and one set again — by hand, from a backup, or from a
    process that still holds its old copy — is refused rather than brought back.
    """
    record = SurfaceToken.from_row(digest, data["tokens"].get(digest))
    if record is not None and record.replaced_at:
        return record, False, []
    found = record is None
    if record is None:
        record = SurfaceToken(
            surface=surface,
            token_hash=digest,
            issued_at=now,
            expires_at=now + INTEGRATION_TTL_SECS,
            found=True,
        )
    record.surface = surface
    return record, found, _install(data, record, now)


# ── the security log ─────────────────────────────────────────────────────────


def surface_session_id(surface: str) -> str:
    """The public handle a surface token is listed and audited under."""
    return f"surface-{surface}"


def client_session_id(client_id: str) -> str:
    """The public handle a client is listed and audited under."""
    return f"client-{client_id}"


def _audit(operation: str, session_id: str, kind: str, extra: dict[str, Any], actor: str) -> None:
    from personalclaw.auth import signins

    signins.record(
        operation,
        caller=actor or "system",
        session_id=session_id,
        issuer="inbound",
        kind=kind,
        source="inbound",
        extra=extra,
    )


def _signed_in(record: SurfaceToken, actor: str) -> None:
    from personalclaw.auth.signins import SIGNED_IN

    _audit(
        SIGNED_IN,
        surface_session_id(record.surface),
        "surface_token",
        {
            "surface": record.surface,
            "issued_at": record.issued_at,
            "expires_at": record.expires_at,
            "lifetime_secs": round(record.expires_at - record.issued_at),
            "found": record.found,
        },
        actor,
    )


def _signed_out(record: SurfaceToken, reason: str, actor: str) -> None:
    from personalclaw.auth.signins import SIGNED_OUT

    _audit(
        SIGNED_OUT,
        surface_session_id(record.surface),
        "surface_token",
        {
            "surface": record.surface,
            "issued_at": record.issued_at,
            "reason": reason,
            "actor": actor or "system",
        },
        actor,
    )


# ── surface tokens ───────────────────────────────────────────────────────────


def issue_surface_token(
    surface: str, token: str, ttl_secs: int, *, actor: str = "owner", now: float | None = None
) -> SurfaceToken:
    """Record a surface token that was just created, and write its sign-in. The token it
    replaces is told so, if it is presented again."""
    now = time.time() if now is None else now
    record = SurfaceToken(
        surface=surface,
        token_hash=token_hash(token),
        issued_at=now,
        expires_at=now + max(1, int(ttl_secs)),
    )
    with _transaction() as data:
        replaced = _install(data, record, now)
    for old in replaced:
        _signed_out(old, REPLACED, actor)
    _signed_in(record, actor)
    return record


def surface_token(
    surface: str, token: str | None, *, now: float | None = None
) -> SurfaceToken | None:
    """The record for *token*, the value configured for *surface*: recorded now if it is new.

    ``None`` when no token is configured, or it cannot be read; either way no record changes.
    A value no record describes (made before lifetimes existed, set through Settings → Secrets,
    or injected by the environment) is recorded as first seen now, with a sign-in row saying so,
    and the token it took the place of is marked replaced.

    Raises :class:`RegistryUnavailable` rather than guessing.
    """
    if not token:
        return None
    now = time.time() if now is None else now
    digest = token_hash(token)
    settled = _settled(_read(), surface, digest)
    if settled is not None:
        return settled
    with _transaction() as data:
        record, found, replaced = _resolve(data, surface, digest, now)
    for old in replaced:
        _signed_out(old, REPLACED, "system")
    if found:
        _signed_in(record, "system")
    return record


def was_replaced(token: str) -> bool:
    """Whether *token* is a surface token recorded as replaced. An unreadable record answers
    False: the caller then keeps the value it has, which is refused by `surface_usable`."""
    try:
        row = _read()["tokens"].get(token_hash(token))
    except RegistryUnavailable:
        return False
    record = SurfaceToken.from_row(token_hash(token), row)
    return record is not None and bool(record.replaced_at)


def surface_usable(surface: str, token: str, *, now: float | None = None) -> bool:
    """Whether *token* — already known to BE the configured value — is live: not expired,
    revoked or replaced. A registry that cannot be read or written answers False."""
    try:
        record = surface_token(surface, token, now=now)
    except Exception:  # noqa: BLE001 — anything but a clear yes refuses
        logger.warning(
            "inbound: %s is unavailable — every surface token refuses",
            registry_path(),
            exc_info=True,
        )
        return False
    return record is not None and record.usable(now)


def note_surface_use(surface: str, token: str, *, now: float | None = None) -> None:
    """Remember that *surface*'s token was just used — at most every few minutes. Never raises."""
    now = time.time() if now is None else now
    digest = token_hash(token)
    try:
        record = SurfaceToken.from_row(digest, _read()["tokens"].get(digest))
        if record is None or now - record.last_seen < _LAST_SEEN_EVERY_SECS:
            return
        with _transaction() as data:
            row = data["tokens"].get(digest)
            if isinstance(row, dict) and row.get("kind") == "surface":
                row["last_seen"] = now
    except Exception:  # noqa: BLE001 — bookkeeping must not fail the request
        logger.debug("inbound: last use of the %s token not recorded", surface, exc_info=True)


def revoke_surface_token(
    surface: str, token: str | None, *, actor: str = "owner", now: float | None = None
) -> bool:
    """Revoke *token*, the value configured for *surface*. False when none is, or it is already
    revoked.

    The surface stays on — a registered client's own token still works — and the revoked value
    stays refused for as long as it is configured, even when the environment sets it again at
    the next start. Creating a new one (``personalclaw inbound token create <surface>
    --rotate``) replaces it.
    """
    if not token:
        return False
    now = time.time() if now is None else now
    with _transaction() as data:
        record, found, replaced = _resolve(data, surface, token_hash(token), now)
        already = bool(record.revoked_at)
        if not already:
            record.revoked_at = now
            record.revoked_by = actor or "owner"
            data["tokens"][record.token_hash] = record.to_row()
    for old in replaced:
        _signed_out(old, REPLACED, "system")
    if found:
        _signed_in(record, "system")
    if already:
        return False
    _signed_out(record, REVOKED, actor)
    return True


# ── client tokens ────────────────────────────────────────────────────────────


def client_signed_in(client: Any, *, actor: str = "owner") -> None:
    """Write a new client's sign-in."""
    from personalclaw.auth.signins import SIGNED_IN

    _audit(
        SIGNED_IN,
        client_session_id(client.client_id),
        "client",
        {
            "client": client.client_id,
            "surfaces": list(client.surfaces),
            "expires_at": client.expires_at,
            "lifetime_secs": round(client.expires_at - _created_epoch(client)),
        },
        actor,
    )


def client_ended(
    client: Any, reason: str, *, actor: str = "owner", now: float | None = None
) -> None:
    """Remember that *client*'s token ended (it was revoked), and write the sign-out."""
    from personalclaw.auth.signins import SIGNED_OUT

    now = time.time() if now is None else now
    try:
        with _transaction() as data:
            if client.token_hash:
                data["tokens"][client.token_hash] = {
                    "kind": "client",
                    "client": client.client_id,
                    "label": client.label,
                    # What it was made for, so its holder is told what ended (`_client_sentence`).
                    "surfaces": list(client.surfaces),
                    "reason": reason,
                    "at": now,
                }
            _prune(data, now)
    except RegistryUnavailable:
        logger.warning("inbound: %s is unavailable — the ending is not remembered", registry_path())
    _audit(
        SIGNED_OUT,
        client_session_id(client.client_id),
        "client",
        {"client": client.client_id, "reason": reason, "actor": actor or "owner"},
        actor,
    )


def _iso_epoch(iso: Any) -> float:
    """An ISO-8601 time as epoch seconds; 0.0 when there is none or it cannot be read."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(iso)).timestamp() if iso else 0.0
    except (TypeError, ValueError):
        return 0.0


def _created_epoch(client: Any) -> float:
    return _iso_epoch(client.created_at)


def client_expires_at(created_at: str, stored: Any) -> float:
    """A client's expiry: the recorded one, or — for a client registered before lifetimes
    existed — 90 days after it was created. One whose creation time cannot be read has already
    expired: counting from now would give it a fresh 90 days every time it is read."""
    try:
        value = float(stored or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value > 0:
        return value
    created = _iso_epoch(created_at)
    return created + INTEGRATION_TTL_SECS if created else 0.0


# ── what a token that stopped working is told ────────────────────────────────


def _surface_sentence(record: SurfaceToken, state: str) -> str:
    name = surface_name(record.surface)
    how = f"Create a new one with `{create_command(record.surface)}`."
    if state == REVOKED:
        return f"This {name} token was revoked {when_words(record.revoked_at)}. {how}"
    if state == REPLACED:
        return (
            f"This {name} token was replaced by a newer one {when_words(record.replaced_at)}. "
            "Use the new token."
        )
    started = "first seen" if record.found else "created"
    return (
        f"This {name} token stopped working {when_words(record.expires_at)}, "
        f"{duration_words(record.expires_at - record.issued_at)} after it was {started}. {how}"
    )


def _client_sentence(
    label: str, *, surfaces: Any, revoked_at: float = 0.0, client: Any = None
) -> str:
    """What the holder of a client token that ended is told, worded by what the token was made
    for (*surfaces*, its client's), wherever it was presented. A client bound to the webhook is a
    sender token made for one automation, and its owner makes a new one where they made that one.
    """
    if isinstance(surfaces, list) and _WEBHOOK in surfaces:
        how = "The automation's owner makes a new one on its page in PersonalClaw."
        token = f"The sender token “{label}”" if label else "This sender token"
    else:
        how = "Register the client again to get a new token."
        token = f"The token for the “{label}” client" if label else "The token for this client"
    if client is None:
        return f"{token} was revoked {when_words(revoked_at)}. {how}"
    lifetime = client.expires_at - _created_epoch(client)
    took = f", {duration_words(lifetime)} after it was issued" if lifetime > 0 else ""
    return f"{token} stopped working {when_words(client.expires_at)}{took}. {how}"


def _match(candidates: dict[str, Any], digest: str) -> Any:
    """The value under *digest*, compared in constant time against every key, without stopping
    at a hit: this runs for a bearer the caller chose."""
    found = None
    for key, value in candidates.items():
        if hmac.compare_digest(key, digest):
            found = value
    return found


@dataclass(frozen=True)
class Ending:
    """Why a token this gateway issued stopped working: the reason the audit records, and the
    sentence its holder is told."""

    reason: str
    sentence: str


def ending(surface: str, presented: str, *, now: float | None = None) -> Ending | None:
    """Why the refused bearer *presented* on *surface* stopped working, or ``None``.

    Only for a token this gateway issued for *surface* that stopped working — it expired, or was
    revoked or replaced — or a client token that expired or was revoked. For anything else the
    uniform refusal stands, and says nothing more.
    """
    if not presented:
        return None
    now = time.time() if now is None else now
    digest = token_hash(presented)
    try:
        data = _read()
    except RegistryUnavailable:
        return None
    row = _match(data["tokens"], digest)
    if isinstance(row, dict) and row.get("kind") == "client":
        return Ending(
            f"client {row.get('client') or '?'} was revoked",
            _client_sentence(
                str(row.get("label") or ""),
                surfaces=row.get("surfaces"),
                revoked_at=float(row.get("at") or 0),
            ),
        )
    record = SurfaceToken.from_row(digest, row)
    if record is not None:
        state = record.state(now)
        if record.surface != surface or state == LIVE:
            return None
        return Ending(f"{surface} token {state}", _surface_sentence(record, state))
    try:
        from personalclaw.inbound.clients import load_clients

        registered = load_clients()
    except Exception:  # noqa: BLE001 — an unreadable client registry adds no sentence
        logger.debug("inbound: client registry unreadable while wording a refusal", exc_info=True)
        return None
    client = _match({c.token_hash: c for c in registered.values() if c.token_hash}, digest)
    if client is not None and client.expires_at <= now and client.may_use(surface):
        return Ending(
            f"client {client.client_id}'s token expired",
            _client_sentence(client.label, surfaces=list(client.surfaces), client=client),
        )
    return None


# ── the list Settings → Devices shows ────────────────────────────────────────


def integration_rows(*, now: float | None = None) -> tuple[list[dict[str, Any]], str]:
    """``(rows, problem)``: every integration token that can reach an inbound surface — each
    configured surface token and every registered client — and, when the token registry cannot
    be read, the sentence that says so. Never a token or a hash."""
    from personalclaw.inbound import auth, clients

    now = time.time() if now is None else now
    rows: list[dict[str, Any]] = []
    problem = ""
    for surface in auth.surfaces():
        token = auth.load_surface_token(surface)
        if not token or auth.token_problem(surface) is not None:
            continue
        try:
            record = surface_token(surface, token, now=now)
        except RegistryUnavailable as exc:
            problem = (
                "The record of when each integration token stops working can't be read, so every "
                f"surface token is refused until it can: {exc}"
            )
            continue
        if record is None:
            continue
        rows.append(
            {
                "id": surface_session_id(surface),
                "kind": "surface",
                "name": f"{surface_name(surface)} token",
                "surfaces": [surface],
                "surface_names": [surface_name(surface)],
                "issued_at": record.issued_at,
                "expires_at": record.expires_at,
                "last_seen": record.last_seen,
                "found": record.found,
                "state": record.state(now),
                "renew": create_command(surface),
            }
        )
    try:
        registered = clients.load_clients()
    except Exception:  # noqa: BLE001 — the surface tokens are still worth listing
        logger.warning("inbound: the client registry is unreadable", exc_info=True)
        registered = {}
    for client in registered.values():
        state = EXPIRED if client.expires_at <= now else "disabled" if client.disabled else LIVE
        rows.append(
            {
                "id": client_session_id(client.client_id),
                "kind": "client",
                "name": client.label or client.client_id,
                "surfaces": list(client.surfaces),
                "surface_names": [surface_name(s) for s in client.surfaces],
                "issued_at": _created_epoch(client),
                "expires_at": client.expires_at,
                "last_seen": _iso_epoch(client.last_seen_at),
                "found": False,
                "state": state,
                "renew": "",
            }
        )
    return rows, problem


def end_integration(row_id: str, *, actor: str = "owner") -> bool:
    """Revoke the integration token *row_id* names (an id from :func:`integration_rows`).
    False when it names nothing that can be revoked."""
    from personalclaw.inbound import auth, clients

    if row_id.startswith("surface-"):
        surface = row_id[len("surface-") :]
        if surface not in auth.surfaces():
            return False
        return revoke_surface_token(surface, auth.load_surface_token(surface), actor=actor)
    if row_id.startswith("client-"):
        return clients.revoke_client(row_id[len("client-") :], actor=actor)
    return False
