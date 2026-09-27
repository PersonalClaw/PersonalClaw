"""Durable signing key + session records.

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

**Row shape.** A row is a RECORD, not a bare expiry:
``{"exp": float, "issuer": str, "app": str?, "device": {...}}``. ``issuer`` is the DOOR the
session came through (:data:`ISSUERS`), written at mint time by the code that owns that door and
never by the client. ``device`` is the client behind the session — what it is, when it signed
in, when and where it was last seen — and EVERY row carries one, because Settings → Devices lists
every sign-in, not only the paired ones (ledger 255: a list of paired phones hid the owner's own
browsers, which were exactly what was being signed out). A row written before the block was
universal has it synthesized on read with an id derived from its nonce, so the list can describe
and revoke it; nothing about it is invented beyond "never seen, signed in at an unknown time".

**Pairing is the ISSUER, not the presence of a device block.** Two capabilities belong to a
paired device only — the origin-less ``/api/ws`` upgrade and the browser connector
 — and both used to test "the row has a ``device``". With a block on every row that test
would have handed both to every browser and script token, so :func:`paired_sessions` is the one
predicate, and it reads ``issuer == "pair"``.

**Each kind of sign-in has its own limit.** The limit used to be five sessions in
total, in memory: the sixth mint of ANY kind — a ``personalclaw token``, a ``personalclaw run``,
the token the gateway prints at startup, an app's per-request token — silently signed out the
least recently used session wherever it was, the owner's phone included. The reason for a limit
still holds (a token pasted into a terminal or a script should not stay live forever just
because newer ones keep coming), so it is kept, per :func:`pool_of`: paired devices, browsers,
tokens and each app are bounded separately (:data:`POOL_CAPS`), so no kind can push another out.
The limit is enforced over THIS file rather than a process's memory, so it holds across restarts
and counts every session the home has, and the one it signs out is the least recently USED.

**Why a session ended is remembered** (the ``ended`` map, bounded). A signed-out device's next
request carries a token whose signature still verifies; :func:`ended_session` is what lets the
gateway tell that device WHY it was signed out and how to sign back in, instead of a bare 403.
It is keyed by nonce because that is what the device presents, and it is only consulted after
the signature has verified, so a forged token learns nothing from it.

:func:`load_sessions` remains the ``{nonce: exp}`` PROJECTION of the records — it is the only
thing the token middleware needs on its hot path. One stored shape, typed views; not two paths.

**An old-shape file (a bare float per row) is DISCARDED, not upgraded.** A row with no issuer
at all is a session nobody recorded the door of; admitting one would mean shipping a list that
is silently incomplete. The cost is bounded and documented by the pre-1.0 banner — one
``personalclaw token`` re-mint, which is exactly the pre-S1 behavior this store replaced.

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

import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.auth.lifetimes import MAX_LIFETIME_SECS
from personalclaw.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

KEY_FILE = "session_key"
SESSIONS_FILE = "sessions.json"

#: Signing-key length. 32 bytes matches the HMAC-SHA256 block security and the previous
#: `os.urandom(32)`, so the bump changes durability without changing strength.
KEY_BYTES = 32

#: Backstop on stored session records, far above what the per-kind limits allow. A record is
#: ~200 bytes; this bounds the file at ~400 KB even if something mints for many apps in a loop.
#: Oldest-expiring are dropped first.
MAX_SESSIONS = 2000

# ── Where a session came from: the door it was issued through ──────────
#
# Written at mint time by the code that owns the door, never by the client.
ISSUER_STARTUP = "startup"  # the link the gateway prints, and opens, when it starts
ISSUER_READY = "ready"  # the `--json-ready` line's token, for a test harness
ISSUER_TOKEN = "token"  # `personalclaw token`, `personalclaw run`, a script, an app's link
ISSUER_LOGIN = "login"  # a password sign-in
ISSUER_ENROLL = "enroll"  # a device code typed on the sign-in page
ISSUER_PAIR = "pair"  # Settings → Devices → Pair a device
ISSUER_APP = "app"  # an app-scoped token (it narrows a session to one app)
ISSUER_UNKNOWN = "unknown"  # a row written before the door was recorded
ISSUERS: tuple[str, ...] = (
    ISSUER_STARTUP,
    ISSUER_READY,
    ISSUER_TOKEN,
    ISSUER_LOGIN,
    ISSUER_ENROLL,
    ISSUER_PAIR,
    ISSUER_APP,
    ISSUER_UNKNOWN,
)

#: A link or a token: it counts as a browser once a browser has opened it, and as a token until.
LINK_ISSUERS = frozenset({ISSUER_STARTUP, ISSUER_READY, ISSUER_TOKEN})

#: Closed set of device kinds. `pair/complete` is reachable WITHOUT a session (a device with
#: no session is the whole point), so its body is untrusted input that ends up in a file the
#: dashboard renders. Clamping to a fixed vocabulary means the stored value cannot be chosen
#: by the caller at all, which is stronger than escaping it later.
DEVICE_KINDS: tuple[str, ...] = ("browser", "mobile", "desktop", "cli", "unknown")

#: The kinds a person signs in with. A link opened by one of these is a browser sign-in.
BROWSER_KINDS = frozenset({"browser", "mobile", "desktop"})

#: Cap on a device's display name. Same reasoning: untrusted, rendered, so bounded.
MAX_DEVICE_NAME = 64

#: Cap on a stored client address. An IPv6 address with a zone id fits comfortably.
MAX_DEVICE_IP = 64

# ── The limits, one per kind of sign-in ─────────────────────────────────
POOL_DEVICE = "device"  # paired and enrolled devices: phones, tablets, the desktop app
POOL_BROWSER = "browser"  # browsers signed in with a password or a link
POOL_TOKEN = "token"  # links and tokens no browser has opened: the CLI, scripts, a harness

#: How many of each kind may be signed in at once. 20 is several times the most devices one
#: person uses (the persona runs the desktop app, two browsers, a phone, the CLI and a script),
#: and still a small, fixed number of live credentials of each kind.
POOL_CAPS: dict[str, int] = {POOL_DEVICE: 20, POOL_BROWSER: 20, POOL_TOKEN: 20}

#: Per app. An app holds one token per signed-in user at a time (`token_auth.app_session_token`
#: reuses it), so this only bounds a burst that re-mints.
APP_POOL_CAP = 8

# ── Why a session ended ────────────────────────────────────────────────
END_SIGNED_OUT = "signed_out"  # the device signed itself out
END_SIGNED_OUT_ELSEWHERE = "signed_out_elsewhere"  # Settings → Devices on another device
END_SIGNED_OUT_OTHERS = "signed_out_others"  # "Sign out all other devices" on another device
END_SIGNED_OUT_EVERYWHERE = "signed_out_everywhere"  # `personalclaw logout` / `auth revoke --all`
END_LIMIT = "limit"  # more of its kind were signed in than the limit, and it was the idlest
END_REPLACED = "replaced"  # the same browser signed in again with a newer link
END_EXPIRED = "expired"  # it ran its whole lifetime (recorded by the store, never passed in)
END_REASONS: tuple[str, ...] = (
    END_SIGNED_OUT,
    END_SIGNED_OUT_ELSEWHERE,
    END_SIGNED_OUT_OTHERS,
    END_SIGNED_OUT_EVERYWHERE,
    END_LIMIT,
    END_REPLACED,
    END_EXPIRED,
)

#: How many ended sessions are remembered. Past this, the oldest ending is forgotten and that
#: device reads the plain refusal — the cost of forgetting is a missing explanation, never access.
MAX_ENDED = 500

#: How long after a session would have expired its ending is still explained.
ENDED_RETENTION_SECS = 7 * 86400

#: How stale a recorded ``last_seen`` must be before an authorized request rewrites the store.
#:
#: 60s, and the number is a cost decision rather than a taste one: this write sits on the
#: request path, where a dashboard that polls every few seconds would otherwise rewrite
#: `sessions.json` several times per second per device. The readers are a device list that
#: renders the field as relative minutes ("3 minutes ago") and the per-kind limit, which signs
#: out the least recently used; a full minute of slack is below what either can perceive, while
#: it bounds the write rate at one atomic rewrite per session per minute.
LAST_SEEN_THROTTLE_SECS = 60.0


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


@dataclass
class DeviceInfo:
    """The client behind a session row — the entry Settings → Devices shows.

    ``id`` is the registry handle the sign-out route takes; it is NOT the nonce, because the
    nonce names the credential and a sign-out URL must not carry one.

    ``minted_at`` is when it signed in. ``last_seen`` and ``ip`` are written only where a
    request is AUTHORIZED (`TokenStateManager.is_nonce_valid` and the middleware's
    :func:`note_client`) — never at mint time. **0.0 means "never made an authorized
    request", and that is load-bearing:** it is not backfilled from ``minted_at``, because a
    ``last_seen`` set at sign-in would read as fresh forever, and the owner uses it to decide
    whether a device is still in use.
    """

    id: str
    name: str = ""
    kind: str = "unknown"
    minted_at: float = 0.0
    last_seen: float = 0.0
    ip: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "minted_at": self.minted_at,
            "last_seen": self.last_seen,
            "ip": self.ip,
        }


def new_device_id() -> str:
    """A fresh registry handle. Random, so it reveals nothing about the nonce it names."""
    return secrets.token_hex(8)


def _derived_device_id(nonce: str) -> str:
    """The handle of a row written before every row carried a device block.

    Derived rather than generated so it is the same on every read until the row is next
    saved (which persists it). One-way, so the handle cannot be turned back into the nonce.
    """
    return hashlib.sha256(f"personalclaw-session:{nonce}".encode()).hexdigest()[:16]


@dataclass
class SessionRecord:
    """One ``sessions.json`` row. ``device`` is always present once parsed."""

    expiry: float
    issuer: str = ISSUER_UNKNOWN
    device: DeviceInfo = field(default_factory=lambda: DeviceInfo(id=new_device_id()))
    app: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"exp": self.expiry, "issuer": self.issuer}
        if self.app:
            out["app"] = self.app
        out["device"] = self.device.to_dict()
        return out


@dataclass(frozen=True)
class EndedSession:
    """Why a session ended, kept so the device that held it can be told."""

    reason: str
    at: float
    issuer: str = ISSUER_UNKNOWN
    kind: str = "unknown"
    expiry: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "at": self.at,
            "issuer": self.issuer,
            "kind": self.kind,
            "exp": self.expiry,
        }


def sanitize_device_name(name: str) -> str:
    """A display name safe to store and render: printable, single-line, bounded."""
    cleaned = "".join(ch for ch in str(name or "") if ch.isprintable() and ch not in "\r\n\t")
    return cleaned.strip()[:MAX_DEVICE_NAME]


def sanitize_device_kind(kind: str) -> str:
    """Clamp to :data:`DEVICE_KINDS`. An unrecognized kind becomes ``unknown``, never itself."""
    candidate = str(kind or "").strip().lower()
    return candidate if candidate in DEVICE_KINDS else "unknown"


def _sanitize_ip(ip: str) -> str:
    cleaned = "".join(ch for ch in str(ip or "") if ch.isprintable() and not ch.isspace())
    return cleaned[:MAX_DEVICE_IP]


#: Command-line and script clients, matched first: their User-Agent names the tool and no OS.
_SCRIPT_AGENTS: tuple[tuple[str, str], ...] = (
    ("curl/", "curl"),
    ("wget/", "Wget"),
    ("python-requests", "Python script"),
    ("python-urllib", "Python script"),
    ("python-httpx", "Python script"),
    ("aiohttp/", "Python script"),
    ("node-fetch", "Script"),
    ("undici", "Script"),
    ("go-http-client", "Script"),
)

#: Runtimes whose User-Agent is their bare product name: matched on the FIRST product token only,
#: because "node" or "bun" inside a browser's string would be a coincidence, not an identity.
#: Node's own `fetch` sends exactly ``node`` — measured, and the case that was missing: a script
#: using it read as "Token not used yet" in Settings → Devices while its last use was listed.
_SCRIPT_PRODUCTS: dict[str, str] = {
    "node": "Script",
    "deno": "Script",
    "bun": "Script",
    "axios": "Script",
    "httpie": "HTTPie",
}

#: Operating systems, most specific first (an iPhone's User-Agent also says "Mac OS X").
_AGENT_OS: tuple[tuple[str, str], ...] = (
    ("iphone", "iPhone"),
    ("ipad", "iPad"),
    ("android", "Android"),
    ("cros", "ChromeOS"),
    ("macintosh", "Mac"),
    ("mac os x", "Mac"),
    ("windows", "Windows"),
    ("linux", "Linux"),
)

#: Browsers, most specific first (Edge and Opera also say "Chrome"; Chrome also says "Safari").
_AGENT_BROWSERS: tuple[tuple[str, str], ...] = (
    ("edg/", "Edge"),
    ("opr/", "Opera"),
    ("firefox/", "Firefox"),
    ("fxios/", "Firefox"),
    ("headlesschrome", "Headless Chrome"),
    ("crios/", "Chrome"),
    ("chrome/", "Chrome"),
    ("chromium/", "Chromium"),
    ("safari/", "Safari"),
)


def describe_user_agent(user_agent: str) -> tuple[str, str]:
    """``(name, kind)`` for a client, from its User-Agent — the ONE derivation of both.

    Coarse on purpose. A parsed version string would be precise and wrong within a month;
    "Chrome on Mac" is what the owner would have typed, and it is enough to recognise a
    device. Both values are clamped on the way into the store anyway.
    """
    ua = str(user_agent or "").lower()
    if not ua:
        return "", "unknown"
    for token, name in _SCRIPT_AGENTS:
        if token in ua:
            return name, "cli"
    product = ua.split(None, 1)[0].split("/", 1)[0]
    if product in _SCRIPT_PRODUCTS:
        return _SCRIPT_PRODUCTS[product], "cli"
    if "electron/" in ua:
        return "PersonalClaw desktop app", "desktop"
    os_label = next((label for token, label in _AGENT_OS if token in ua), "")
    browser = next((label for token, label in _AGENT_BROWSERS if token in ua), "")
    if os_label in ("iPhone", "iPad", "Android") or " mobile" in ua:
        kind = "mobile"
    elif os_label or browser:
        kind = "browser"
    else:
        kind = "unknown"
    if browser and os_label:
        name = f"{browser} on {os_label}"
    else:
        name = browser or os_label
    return name, kind


def _parse_device(raw: Any, nonce: str) -> DeviceInfo:
    """The row's device block — synthesized, with a derived id, for a row that has none."""
    if not isinstance(raw, dict) or not str(raw.get("id") or ""):
        return DeviceInfo(id=_derived_device_id(nonce))
    try:
        minted_at = float(raw.get("minted_at") or 0.0)
    except (TypeError, ValueError):
        minted_at = 0.0
    try:
        # A missing/garbage stamp reads as 0.0 — "never seen" — never as `minted_at`.
        last_seen = float(raw.get("last_seen") or 0.0)
    except (TypeError, ValueError):
        last_seen = 0.0
    return DeviceInfo(
        id=str(raw.get("id")),
        name=sanitize_device_name(raw.get("name", "")),
        kind=sanitize_device_kind(raw.get("kind", "")),
        minted_at=minted_at,
        last_seen=last_seen,
        ip=_sanitize_ip(raw.get("ip", "")),
    )


def _parse_record(raw: Any, nonce: str) -> SessionRecord | None:
    """One stored row → a record, or *None* when the row is not one.

    **The single place the old bare-float shape is handled**, and it is handled by refusing
    it: see the module docstring. A non-dict row returns *None*, so a pre-C1 store reads as
    empty and every token in it is re-minted rather than admitted un-attributed.
    """
    if not isinstance(raw, dict):
        return None
    try:
        expiry = float(raw.get("exp"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    issuer = str(raw.get("issuer") or ISSUER_UNKNOWN)
    if issuer not in ISSUERS:
        issuer = ISSUER_UNKNOWN
    app = str(raw.get("app") or "") if issuer == ISSUER_APP else ""
    device = _parse_device(raw.get("device"), nonce)
    # No row outlives the 90-day limit, however long it was minted for before the
    # limit existed: 90 days from when it signed in — or, for a row from before that was
    # recorded, from now at the latest. The gateway refuses such a token 90 days after its own
    # signed issue time (`token_auth._session_deadline`); this keeps the LIST from promising
    # longer, and `note_client` replaces "now" with the real issue time the first time the
    # session is used.
    expiry = min(expiry, (device.minted_at or time.time()) + MAX_LIFETIME_SECS)
    return SessionRecord(expiry=expiry, issuer=issuer, device=device, app=app)


def _parse_ended(raw: Any) -> EndedSession | None:
    if not isinstance(raw, dict) or raw.get("reason") not in END_REASONS:
        return None
    try:
        at = float(raw.get("at") or 0.0)
        expiry = float(raw.get("exp") or 0.0)
    except (TypeError, ValueError):
        return None
    issuer = str(raw.get("issuer") or ISSUER_UNKNOWN)
    return EndedSession(
        reason=str(raw["reason"]),
        at=at,
        issuer=issuer if issuer in ISSUERS else ISSUER_UNKNOWN,
        kind=sanitize_device_kind(raw.get("kind", "")),
        expiry=expiry,
    )


@dataclass
class _State:
    records: dict[str, SessionRecord]
    ended: dict[str, EndedSession]


def _load_state() -> _State:
    """Everything in the file: live records (expired ones dropped) and remembered endings.

    Returns an empty state on any read failure. Fail-CLOSED in effect: an unreadable store
    means no nonce validates, so tokens are rejected rather than blanket-accepted.
    """
    path = sessions_path()
    if not path.is_file():
        return _State({}, {})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("session store unreadable — treating every session as absent")
        return _State({}, {})
    if not isinstance(raw, dict):
        return _State({}, {})
    now = time.time()
    records: dict[str, SessionRecord] = {}
    expired: dict[str, EndedSession] = {}
    dropped = 0
    sessions = raw.get("sessions")
    for nonce, row in (sessions if isinstance(sessions, dict) else {}).items():
        record = _parse_record(row, str(nonce))
        if record is None:
            dropped += 1
            continue
        if record.expiry > now:
            records[str(nonce)] = record
        else:
            # Its ending is remembered like any other, so the device that held it can still
            # be told which door to come back through after the row itself is gone.
            expired[str(nonce)] = _expired(record)
    if dropped:
        logger.info(
            "dropped %d session row(s) that predate the device-session record shape — "
            "re-run `personalclaw token` (or log in) to get a fresh session",
            dropped,
        )
    ended: dict[str, EndedSession] = {}
    stored_ended = raw.get("ended")
    for nonce, row in (stored_ended if isinstance(stored_ended, dict) else {}).items():
        entry = _parse_ended(row)
        if entry is not None:
            ended[str(nonce)] = entry
    for nonce, entry in expired.items():
        ended.setdefault(nonce, entry)
    return _State(records, ended)


def _expired(record: SessionRecord) -> EndedSession:
    return EndedSession(
        END_EXPIRED, record.expiry, record.issuer, record.device.kind, record.expiry
    )


def _save_state(state: _State) -> bool:
    """Persist *state*: expired rows dropped, both maps capped. Returns whether it WROTE.

    Never raises: a store that cannot be written costs durability, not the request.
    """
    now = time.time()
    live = {n: r for n, r in state.records.items() if r.expiry > now}
    for nonce, record in state.records.items():
        if nonce not in live:
            state.ended.setdefault(nonce, _expired(record))
    if len(live) > MAX_SESSIONS:
        # Keep the LONGEST-lived: a session about to expire anyway is the cheapest to lose.
        live = dict(sorted(live.items(), key=lambda kv: kv[1].expiry, reverse=True)[:MAX_SESSIONS])
    ended = {
        n: e
        for n, e in state.ended.items()
        if max(e.expiry, e.at) + ENDED_RETENTION_SECS > now and n not in live
    }
    if len(ended) > MAX_ENDED:
        ended = dict(sorted(ended.items(), key=lambda kv: kv[1].at, reverse=True)[:MAX_ENDED])
    payload = {
        "sessions": {n: r.to_dict() for n, r in live.items()},
        "ended": {n: e.to_dict() for n, e in ended.items()},
    }
    path = sessions_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(payload, indent=2) + "\n", mode=0o600)
        return True
    except OSError:
        logger.warning("could not persist the session store", exc_info=True)
        return False


def load_session_records() -> dict[str, SessionRecord]:
    """``{nonce: SessionRecord}`` for every live stored session."""
    return _load_state().records


def load_sessions() -> dict[str, float]:
    """``{nonce: session_exp}`` — the expiry projection of :func:`load_session_records`.

    The token middleware asks exactly one question of this store ("is this nonce live, and
    until when?"), so it gets exactly that. Same file, same rows, narrower view.
    """
    return {nonce: record.expiry for nonce, record in load_session_records().items()}


def save_session_records(records: dict[str, SessionRecord]) -> None:
    """Persist *records* as the live set, keeping the remembered endings."""
    state = _load_state()
    _save_state(_State(dict(records), state.ended))


def pool_of(record: SessionRecord) -> str:
    """Which limit *record* counts against — see :data:`POOL_CAPS`.

    A link or a token counts as a token until a browser opens it; after that it is that
    browser's sign-in. A row from before the door was recorded counts as a browser: that is
    what it almost certainly is, and it keeps script tokens from pushing it out.
    """
    if record.issuer == ISSUER_APP:
        return f"app:{record.app}"
    if record.issuer in (ISSUER_PAIR, ISSUER_ENROLL):
        return POOL_DEVICE
    if record.issuer in LINK_ISSUERS and record.device.kind not in BROWSER_KINDS:
        return POOL_TOKEN
    return POOL_BROWSER


def pool_cap(pool: str) -> int:
    return POOL_CAPS.get(pool, APP_POOL_CAP)


def _recency(record: SessionRecord) -> float:
    """When *record* was last used; a session never used counts from when it signed in."""
    return record.device.last_seen or record.device.minted_at


def _enforce_limit(
    state: _State, pool: str, keep: str, now: float
) -> list[tuple[str, SessionRecord]]:
    """Sign out the least recently used of *pool* beyond its limit. *keep* is never one."""
    members = [(n, r) for n, r in state.records.items() if pool_of(r) == pool]
    excess = len(members) - pool_cap(pool)
    if excess <= 0:
        return []
    idlest = sorted((m for m in members if m[0] != keep), key=lambda m: _recency(m[1]))[:excess]
    for nonce, record in idlest:
        del state.records[nonce]
        state.ended[nonce] = EndedSession(
            END_LIMIT, now, record.issuer, record.device.kind, record.expiry
        )
    return idlest


@dataclass(frozen=True)
class Remembered:
    """What :func:`remember_session` did: whether the row reached the disk, and whom the
    limit signed out to make room (never the new session itself)."""

    persisted: bool = False
    evicted: tuple[tuple[str, SessionRecord], ...] = ()


def remember_session(
    nonce: str,
    expiry: float,
    *,
    issuer: str = ISSUER_UNKNOWN,
    device: DeviceInfo | None = None,
    app: str = "",
) -> Remembered:
    """Record one minted session so it survives a restart, and enforce its kind's limit.

    The caller drops each evicted session from memory and records its sign-out; a caller
    that must not hand out a session the registry cannot show (pairing) checks
    ``persisted``. The write itself is best-effort (see :func:`_save_state`).
    """
    if not nonce:
        return Remembered()
    now = time.time()
    state = _load_state()
    record = SessionRecord(
        expiry=float(expiry),
        issuer=issuer if issuer in ISSUERS else ISSUER_UNKNOWN,
        device=device or DeviceInfo(id=new_device_id(), minted_at=now),
        app=app if issuer == ISSUER_APP else "",
    )
    state.records[nonce] = record
    state.ended.pop(nonce, None)
    evicted = _enforce_limit(state, pool_of(record), nonce, now)
    return Remembered(persisted=_save_state(state), evicted=tuple(evicted))


@dataclass(frozen=True)
class ClientNote:
    """What :func:`note_client` did: whether it wrote, and whom the limit signed out."""

    wrote: bool = False
    evicted: tuple[tuple[str, SessionRecord], ...] = ()


def note_client(
    nonce: str,
    *,
    ip: str,
    user_agent: str,
    browser_carrier: bool,
    issued_at: float = 0.0,
    now: float | None = None,
) -> ClientNote:
    """Record where *nonce*'s client was seen from, and what it is. Writes only on a change.

    Called by the middleware for an AUTHORIZED request, which is the one place that knows
    the request. ``ip`` is always the latest address ("where it was last seen"). The kind and
    name follow the User-Agent the first time the session is seen, and a link or token that a
    browser opens (``browser_carrier``: the ``?token=`` exchange or the cookie) becomes that
    browser's sign-in — which moves it from the token limit to the browser limit, so that
    limit is enforced here too. A paired or enrolled device keeps the name it was given.
    A row from before sign-in times were recorded adopts *issued_at* (the token's signed
    ``iat``) as its sign-in time, which also moves its listed end to 90 days after it.

    **Never raises**, for the reason :func:`touch_device_last_seen` never does: the request
    is already authorized, and a failed note must not become a refusal.
    """
    try:
        stamp = time.time() if now is None else float(now)
        state = _load_state()
        record = state.records.get(nonce)
        if record is None:
            return ClientNote()
        device = record.device
        before_pool = pool_of(record)
        wrote = False
        if not device.minted_at and issued_at > 0:
            device.minted_at = float(issued_at)
            record.expiry = min(record.expiry, device.minted_at + MAX_LIFETIME_SECS)
            wrote = True
        clean_ip = _sanitize_ip(ip)
        if clean_ip and device.ip != clean_ip:
            device.ip = clean_ip
            wrote = True
        if record.issuer not in (ISSUER_PAIR, ISSUER_ENROLL, ISSUER_APP):
            name, kind = describe_user_agent(user_agent)
            becomes_browser = (
                browser_carrier and kind in BROWSER_KINDS and device.kind not in BROWSER_KINDS
            )
            if becomes_browser or (device.kind == "unknown" and kind != "unknown"):
                device.kind = sanitize_device_kind(kind)
                device.name = sanitize_device_name(name) or device.name
                wrote = True
            elif not device.name and name:
                device.name = sanitize_device_name(name)
                wrote = True
        if not wrote:
            return ClientNote()
        evicted: list[tuple[str, SessionRecord]] = []
        after_pool = pool_of(record)
        if after_pool != before_pool:
            evicted = _enforce_limit(state, after_pool, nonce, stamp)
        _save_state(state)
        return ClientNote(wrote=True, evicted=tuple(evicted))
    except Exception:  # noqa: BLE001 — best-effort by contract; see the docstring
        logger.debug("could not note the client of an authorized session", exc_info=True)
        return ClientNote()


def touch_device_last_seen(nonce: str, *, now: float | None = None) -> bool:
    """Best-effort: stamp ``last_seen`` on *nonce*'s row. Returns whether it WROTE.

    Two no-ops, each deliberate:

    * **no row** — a nonce the store never recorded is not resurrected here.
    * **still fresh** — while the recorded stamp is newer than
      :data:`LAST_SEEN_THROTTLE_SECS`, this returns without touching the file. That throttle
      is the whole reason the field was safe to add: see the constant.

    **Never raises.** This is called from inside the authorization path, so every failure mode
    — an unreadable store, an unwritable directory, a corrupt row — must degrade to "no stamp
    written", never to "session denied". A device list with a stale timestamp is a cosmetic
    defect; an auth path that fails closed on a cosmetic write is an outage.
    """
    if not nonce:
        return False
    try:
        stamp = time.time() if now is None else float(now)
        state = _load_state()
        record = state.records.get(nonce)
        if record is None:
            return False
        if stamp - record.device.last_seen < LAST_SEEN_THROTTLE_SECS:
            return False
        record.device.last_seen = stamp
        _save_state(state)
        return True
    except Exception:  # noqa: BLE001 — best-effort by contract; see the docstring
        logger.debug("could not stamp device last_seen", exc_info=True)
        return False


def signed_in_sessions() -> dict[str, SessionRecord]:
    """Every live sign-in the owner can see and sign out — all but app-scoped tokens.

    An app token is not a device: it only narrows a signed-in session to one app's
    permissions, lasts an hour, and is re-minted as the app needs it.
    """
    return {n: r for n, r in load_session_records().items() if r.issuer != ISSUER_APP}


def paired_sessions() -> dict[str, SessionRecord]:
    """Only the sessions issued by PAIRING — the predicate for pairing-only capabilities."""
    return {n: r for n, r in load_session_records().items() if r.issuer == ISSUER_PAIR}


def nonces_for_session(session_id: str) -> list[str]:
    """Every live nonce whose device block carries *session_id*.

    A list, not one nonce: a handle is written once per row, but an owner reading the list
    must be able to trust that "sign out" left nothing behind under that name.
    """
    if not session_id:
        return []
    return [n for n, r in load_session_records().items() if r.device.id == session_id]


def end_sessions(
    nonces: list[str], reason: str, *, now: float | None = None
) -> list[tuple[str, SessionRecord]]:
    """End each of *nonces* that is live, remembering *reason*. Returns what it ended."""
    if reason not in END_REASONS or reason == END_EXPIRED:
        raise ValueError(f"unknown end reason {reason!r}")
    stamp = time.time() if now is None else float(now)
    state = _load_state()
    ended: list[tuple[str, SessionRecord]] = []
    for nonce in dict.fromkeys(nonces):
        record = state.records.pop(nonce, None)
        if record is None:
            continue
        state.ended[nonce] = EndedSession(
            reason, stamp, record.issuer, record.device.kind, record.expiry
        )
        ended.append((nonce, record))
    if ended:
        _save_state(state)
    return ended


def ended_session(nonce: str) -> EndedSession | None:
    """Why the session *nonce* named ended, or *None* when that is not remembered."""
    if not nonce:
        return None
    return _load_state().ended.get(nonce)


def clear_sessions() -> None:
    """Drop every stored session AND every remembered ending — for a new signing key only.

    Under a new key no old token's signature verifies, so there is nothing left to explain.
    Signing everyone out under the SAME key is :func:`end_sessions`, which remembers why.
    """
    _save_state(_State({}, {}))


def session_stats() -> dict[str, Any]:
    """Counts for the doctor / status surface — never the nonces themselves."""
    records = load_session_records()
    by_pool: dict[str, int] = {}
    for record in records.values():
        pool = pool_of(record)
        key = "app" if pool.startswith("app:") else pool
        by_pool[key] = by_pool.get(key, 0) + 1
    return {
        "sessions": len(records),
        "devices": sum(1 for r in records.values() if r.issuer == ISSUER_PAIR),
        "by_kind": by_pool,
        "key_present": key_path().is_file(),
        "soonest_expiry": min((r.expiry for r in records.values()), default=0.0),
    }
