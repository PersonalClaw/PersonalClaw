"""Sender trust as a core seam — the contract every channel transport binds to (CE-1).

Before this module, "is this person allowed to talk to my agent?" was answered
app-locally: the Slack bundle kept its own allowlist JSON and its own owner Allow/Deny
prompt. Every new channel (Telegram, Discord, email, …) would have re-invented the same
trust vocabulary, and each copy would drift. This module lifts that vocabulary into
provider-agnostic core so a transport declares *who* and *where* (a ``provider`` string
plus a ``sender_id`` / ``channel_id``) and inherits one trust posture, one pairing
mechanism, one owner-notification flow, and one fence for untrusted content.

**Provider-agnostic.** Nothing here names a vendor. ``provider`` is an opaque key the
transport chooses ("slack", "telegram", …); the store partitions by it. Vendor-specific
rendering (a Slack Block-Kit prompt, a Telegram inline keyboard) stays in the app bundle.

**The store** lives at ``entity_settings/channel_trust.json`` (atomic writes via the
shared entity-settings helpers), shaped per provider — see :data:`_DEFAULT_PROVIDER`.
A corrupt or missing store falls back to defaults with a warning (fail-OPEN for the
*store* so a bad file never crashes inbound handling) — but an unknown sender is still
denied by *policy* (the fail-CLOSED half: absence of data means "not trusted", never
"trust everyone").

**Pairing codes** are 8-digit numeric, single active per provider, single-use, TTL 600s,
and **only the SHA-256 hash is stored** — the plaintext is returned once by
:func:`create_pairing_code` and never persisted or logged. :func:`redeem_pairing_code`
compares in constant time and consumes the code on success.

**Audit.** Three security events are emitted through the SEL: ``pairing_code_created``
(never carrying the code), ``sender_paired``, ``sender_denied``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: entity_settings key (one file, partitioned per provider).
_ENTITY = "channel_trust"

#: Pairing-code parameters (Design §S1 / Contract C1).
PAIRING_CODE_TTL_SECS = 600  # 10 minutes
PAIRING_CODE_DIGITS = 8

#: Policy vocabularies (Contract C2). The default posture for an unknown sender.
DM_POLICIES: tuple[str, ...] = ("pairing", "owner_only", "open")
GROUP_POLICIES: tuple[str, ...] = ("tracked_only", "off")
DEFAULT_DM_POLICY = "pairing"
DEFAULT_GROUP_POLICY = "tracked_only"

#: How often the canned pairing-needed reply / owner notification may re-fire for the
#: SAME unknown sender. One SEL entry + one notification per sender per window — never a
#: flood from a chatty stranger.
UNKNOWN_SENDER_RENOTIFY_SECS = 24 * 3600

#: The canned reply a DM-policy=pairing transport sends back to an unknown sender.
CANNED_PAIRING_REPLY = (
    "I don't recognize you yet. Ask my owner for an 8-digit pairing code "
    "(they can run `personalclaw pair <provider>`), then send it here to start talking."
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _default_provider() -> dict[str, Any]:
    """A fresh, empty provider record with the default policy posture."""
    return {
        "allowed_senders": {},
        "tracked_channels": {},
        "pairing": {},
        "policies": {"dm": DEFAULT_DM_POLICY, "group": DEFAULT_GROUP_POLICY},
        "rate": {},
    }


# The literal above, exposed for tests/readers wanting the shape without a side effect.
_DEFAULT_PROVIDER = _default_provider()


# ── storage (fail-open read, atomic write) ───────────────────────────────────


def _read_store() -> dict[str, Any]:
    """The whole trust store, or ``{}`` on a corrupt/missing file (warn, never crash).

    Reads the raw path rather than delegating to ``_load_entity_settings`` because that
    helper swallows a corrupt file silently — and CE-1's contract is *defaults + warn*, so
    a broken store is visible in the log rather than an invisible reset.
    """
    from personalclaw.providers.entity_routes import _entity_settings_path

    path = _entity_settings_path(_ENTITY)
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning(
            "channel_trust store at %s is unreadable/corrupt — using defaults", path, exc_info=True
        )
        return {}
    return data if isinstance(data, dict) else {}


def _write_store(data: dict[str, Any]) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_ENTITY, data)


def _provider_record(store: dict[str, Any], provider: str) -> dict[str, Any]:
    """The provider's record from ``store``, merged over defaults (never mutates ``store``).

    A partially-shaped record (an older store missing ``policies``, say) still yields every
    key, so a reader never has to guard each field."""
    rec = _default_provider()
    existing = store.get(provider)
    if isinstance(existing, dict):
        for key, val in existing.items():
            if key == "policies" and isinstance(val, dict):
                rec["policies"] = {**rec["policies"], **val}
            else:
                rec[key] = val
    return rec


def _save_provider(provider: str, record: dict[str, Any]) -> None:
    store = _read_store()
    store[provider] = record
    _write_store(store)


# ── SEL audit ─────────────────────────────────────────────────────────────────


def _emit_sel(operation: str, outcome: str, provider: str, sender_id: str = "") -> None:
    """Emit one channel-trust security event. Never raises, never logs the pairing code.

    ``operation`` is one of ``pairing_code_created`` / ``sender_paired`` /
    ``sender_denied`` — free-form audit strings (the SEL has no closed kind allowlist; the
    HMAC chain is what makes the log tamper-evident, not an enum)."""
    try:
        import uuid

        from personalclaw.sel import SecurityEvent, sel

        sel().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=_iso(_now()),
                event_type="channel_trust",
                caller_identity=f"{provider}:{sender_id}" if sender_id else provider,
                agent="personalclaw",
                source="channel",
                operation=operation,
                outcome=outcome,
                resources=f"provider={provider}",
            )
        )
    except Exception:  # audit must never break the trust decision
        logger.debug("channel_trust SEL emit failed for %s", operation, exc_info=True)


# ── sender allowlist ───────────────────────────────────────────────────────────


def is_allowed_sender(provider: str, sender_id: str) -> bool:
    """Whether ``sender_id`` is an approved sender on ``provider`` (owner- or pairing-added)."""
    rec = _provider_record(_read_store(), provider)
    return sender_id in rec.get("allowed_senders", {})


def allow_sender(provider: str, sender_id: str, name: str = "", *, via: str = "owner") -> None:
    """Approve ``sender_id`` on ``provider``. Idempotent; emits ``sender_paired``.

    ``via`` records provenance (``owner`` = the owner clicked Allow; ``pairing`` = a
    redeemed code) so the store can show *how* someone was trusted."""
    store = _read_store()
    rec = _provider_record(store, provider)
    rec.setdefault("allowed_senders", {})[sender_id] = {
        "name": name,
        "added_at": _iso(_now()),
        "via": via,
    }
    store[provider] = rec
    _write_store(store)
    _emit_sel("sender_paired", via, provider, sender_id)


def deny_sender(provider: str, sender_id: str) -> None:
    """Revoke ``sender_id`` on ``provider`` (owner Deny). Idempotent; emits ``sender_denied``."""
    store = _read_store()
    rec = _provider_record(store, provider)
    rec.get("allowed_senders", {}).pop(sender_id, None)
    store[provider] = rec
    _write_store(store)
    _emit_sel("sender_denied", "owner", provider, sender_id)


# ── tracked channels (group/room membership) ─────────────────────────────────


def is_tracked_channel(provider: str, channel_id: str) -> bool:
    """Whether ``channel_id`` is a tracked group/room on ``provider``."""
    rec = _provider_record(_read_store(), provider)
    return channel_id in rec.get("tracked_channels", {})


def track(provider: str, channel_id: str, name: str = "") -> None:
    """Start tracking a group/room on ``provider``. Idempotent."""
    store = _read_store()
    rec = _provider_record(store, provider)
    rec.setdefault("tracked_channels", {})[channel_id] = {
        "name": name,
        "added_at": _iso(_now()),
    }
    store[provider] = rec
    _write_store(store)


def untrack(provider: str, channel_id: str) -> None:
    """Stop tracking a group/room on ``provider``. Idempotent."""
    store = _read_store()
    rec = _provider_record(store, provider)
    rec.get("tracked_channels", {}).pop(channel_id, None)
    store[provider] = rec
    _write_store(store)


# ── policies ────────────────────────────────────────────────────────────────


def trust_policies(provider: str) -> dict[str, str]:
    """The ``{"dm": ..., "group": ...}`` policy for ``provider`` (defaults if unset)."""
    return dict(_provider_record(_read_store(), provider)["policies"])


# ── read projection (the owner-facing surface) ───────────────────────────────


def list_providers() -> list[str]:
    """Every provider the trust store holds any state for, sorted.

    A provider appears here as soon as it has ANY trust state — a paired sender, a tracked
    channel, a policy, or merely a first contact from an unknown sender (which writes the
    renotify stamp). There is deliberately no separate registry to enumerate against: the
    store is the only thing that knows which opaque ``provider`` keys transports chose, and
    inventing a second list would let the two disagree.
    """
    return sorted(_read_store().keys())


def provider_trust(provider: str) -> dict[str, Any]:
    """One provider's trust posture, shaped for a read surface.

    **Never carries a secret.** The pairing record holds only a SHA-256 hash of the active
    code, and not even that is projected — the caller learns whether a code is outstanding
    and when it expires, which is all a UI needs to say "a code is live". The per-sender
    renotify stamps (``rate``) are also withheld: they are a log of who tried to reach the
    owner, which is a different surface from "who is allowed" and would leak contact
    attempts into a page about the allowlist.
    """
    rec = _provider_record(_read_store(), provider)
    pairing = rec.get("pairing") or {}
    senders = rec.get("allowed_senders") or {}
    channels = rec.get("tracked_channels") or {}
    return {
        "provider": provider,
        "policies": dict(rec["policies"]),
        "allowed_senders": [
            {
                "sender_id": sid,
                "name": str((meta or {}).get("name", "") or ""),
                "added_at": str((meta or {}).get("added_at", "") or ""),
                "via": str((meta or {}).get("via", "") or ""),
            }
            for sid, meta in sorted(senders.items())
            if isinstance(senders, dict)
        ],
        "tracked_channels": [
            {
                "channel_id": cid,
                "name": str((meta or {}).get("name", "") or ""),
                "added_at": str((meta or {}).get("added_at", "") or ""),
            }
            for cid, meta in sorted(channels.items())
            if isinstance(channels, dict)
        ],
        "pairing_active": bool(pairing.get("code_hash")),
        "pairing_expires_at": str(pairing.get("expires_at", "") or ""),
    }


# ── pairing codes (hash-stored, single-use, TTL'd) ───────────────────────────


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_pairing_code(provider: str) -> str:
    """Create (and return once) an 8-digit pairing code for ``provider``.

    Only the SHA-256 hash is stored; a single active code per provider (a new one
    replaces the old). TTL :data:`PAIRING_CODE_TTL_SECS`. Emits ``pairing_code_created``
    (the code itself is NEVER logged)."""
    code = f"{secrets.randbelow(10**PAIRING_CODE_DIGITS):0{PAIRING_CODE_DIGITS}d}"
    now = _now()
    store = _read_store()
    rec = _provider_record(store, provider)
    rec["pairing"] = {
        "code_hash": _hash_code(code),
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=PAIRING_CODE_TTL_SECS)),
    }
    store[provider] = rec
    _write_store(store)
    _emit_sel("pairing_code_created", "created", provider)
    return code


def redeem_pairing_code(provider: str, sender_id: str, code: str) -> bool:
    """Redeem ``code`` for ``sender_id`` on ``provider``.

    Within TTL and unused → the sender is allowed (``via="pairing"``, which emits
    ``sender_paired``) and the code is consumed. Expired / already-used / wrong code →
    ``False`` and ``sender_denied``. The compare is constant-time over the hashes."""
    store = _read_store()
    rec = _provider_record(store, provider)
    pairing = rec.get("pairing") or {}
    stored_hash = pairing.get("code_hash", "")

    if not stored_hash:
        _emit_sel("sender_denied", "no_active_code", provider, sender_id)
        return False

    # Expired → clear the dead code and deny.
    try:
        expired = _now() > datetime.fromisoformat(pairing.get("expires_at", ""))
    except ValueError:
        expired = True
    if expired:
        rec["pairing"] = {}
        store[provider] = rec
        _write_store(store)
        _emit_sel("sender_denied", "expired_code", provider, sender_id)
        return False

    # Constant-time compare; a wrong code leaves the (still-valid) code in place.
    if not hmac.compare_digest(stored_hash, _hash_code(code or "")):
        _emit_sel("sender_denied", "wrong_code", provider, sender_id)
        return False

    # Success: consume the code, then allow (which emits sender_paired).
    rec["pairing"] = {}
    store[provider] = rec
    _write_store(store)
    allow_sender(provider, sender_id, via="pairing")
    return True


# ── fencing (untrusted channel content) ──────────────────────────────────────


def fence_channel_content(text: str, provider: str, sender_id: str) -> str:
    """Wrap untrusted channel ``text`` so a model reads it as DATA, not instructions.

    Delegates to the one core fence (``security.fence_untrusted``) with a channel-shaped
    provenance ``source`` — so transports can't hand-roll a weaker fence and the neutralised
    chat-template-token / fence-break defences are inherited unchanged. Non-owner group
    content MUST pass through here before entering any session context."""
    from personalclaw.security import fence_untrusted

    return fence_untrusted(text, source=f"channel:{provider}:{sender_id}")


# ── the unknown-sender flow (transport-side contract) ─────────────────────────


@dataclass
class TrustVerdict:
    """The decision a transport acts on for one inbound message.

    ``allowed`` gates whether the message enters a session. ``canned_reply`` (when set) is
    the exact text to send back to an unknown DM sender. ``fired_notification`` reports
    whether THIS call raised the owner notification (False = deduped inside the renotify
    window), so a transport can log honestly without re-deriving the dedup rule."""

    allowed: bool
    reason: str = ""
    canned_reply: str = ""
    fired_notification: bool = False
    fenced_text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def note_unknown_sender(
    state: Any, provider: str, sender_id: str, sender_name: str = "", *, silent: bool = False
) -> bool:
    """Record + surface a first contact from an unknown sender. Returns whether it fired.

    Emits exactly ONE ``sender_denied`` SEL entry and ONE actionable owner notification per
    sender per :data:`UNKNOWN_SENDER_RENOTIFY_SECS` window — a chatty stranger cannot flood
    either. The notification carries ``actions=["allow","deny"]`` plus the ``provider`` /
    ``sender_id`` the Allow button needs; a click routes to :func:`apply_trust_action`,
    which persists the sender. ``silent`` suppresses the canned reply text only (policy
    ``owner_only``), never the audit/notification.

    Deduped on the persisted ``rate`` map (an ISO timestamp per sender), so the dedup
    survives a restart — an unknown sender who messaged before you slept does not re-alert
    when the gateway comes back up."""
    store = _read_store()
    rec = _provider_record(store, provider)
    rate = rec.setdefault("rate", {})

    now = _now()
    last = rate.get(sender_id, "")
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < UNKNOWN_SENDER_RENOTIFY_SECS:
                return False
        except ValueError:
            pass  # unparseable stamp → treat as first contact

    rate[sender_id] = _iso(now)
    store[provider] = rec
    _write_store(store)

    _emit_sel("sender_denied", "unknown_sender", provider, sender_id)

    if state is not None:
        try:
            from personalclaw import notification_kinds

            who = sender_name or sender_id
            state.notify(
                notification_kinds.WARNING,
                f"Unknown {provider} sender wants to talk",
                f"{who} messaged your agent on {provider} but isn't paired. "
                "Allow them to converse, or deny.",
                meta={
                    "event": "channel.unknown_sender",
                    "provider": provider,
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "actions": ["allow", "deny"],
                },
            )
        except Exception:
            logger.warning("unknown-sender owner notification failed", exc_info=True)
    return True


def apply_trust_action(action: str, provider: str, sender_id: str, name: str = "") -> bool:
    """Apply the owner's click on an unknown-sender notification. Returns the allow state.

    ``allow`` → :func:`allow_sender` (persists, ``via="owner"``) and returns True.
    ``deny`` → :func:`deny_sender` and returns False. This is the backend the notification's
    Allow/Deny buttons resolve to — the seam that makes the notification *actionable*."""
    act = (action or "").strip().lower()
    if act == "allow":
        allow_sender(provider, sender_id, name, via="owner")
        return True
    if act == "deny":
        deny_sender(provider, sender_id)
        return False
    logger.debug("apply_trust_action: unknown action %r", action)
    return False


def guard_inbound(
    state: Any,
    provider: str,
    sender_id: str,
    *,
    sender_name: str = "",
    channel_id: str = "",
    is_dm: bool = True,
    text: str = "",
) -> TrustVerdict:
    """THE trust gate a transport calls at the top of its inbound path.

    This is the seam CE-2..9 (Telegram/Discord/email + External-Access) bind to, so its
    shape is a contract. It applies the provider's policy:

    * **DM**, policy ``open`` → allowed. Policy ``pairing`` / ``owner_only`` → allowed only
      if the sender is already approved; otherwise the unknown-sender flow fires
      (:func:`note_unknown_sender`) and the message is denied. ``pairing`` returns the
      canned pairing-needed reply; ``owner_only`` stays silent (open question resolved:
      no in-channel reply).
    * **group/room**, policy ``off`` → denied silently. Policy ``tracked_only`` → allowed
      only for a tracked channel; an untracked group is silently ignored (no owner spam).

    When allowed non-owner group content is present, ``fenced_text`` carries the
    :func:`fence_channel_content` wrapping the transport must use before the text enters a
    session — the fence is applied HERE so a transport can't forget it."""
    if is_dm:
        policy = trust_policies(provider).get("dm", DEFAULT_DM_POLICY)
        if policy == "open" or is_allowed_sender(provider, sender_id):
            return TrustVerdict(allowed=True, reason="allowed")
        fired = note_unknown_sender(
            state, provider, sender_id, sender_name, silent=(policy == "owner_only")
        )
        return TrustVerdict(
            allowed=False,
            reason="unknown_sender",
            canned_reply="" if policy == "owner_only" else CANNED_PAIRING_REPLY,
            fired_notification=fired,
        )

    # Group / room.
    gpolicy = trust_policies(provider).get("group", DEFAULT_GROUP_POLICY)
    if gpolicy == "off" or not is_tracked_channel(provider, channel_id):
        return TrustVerdict(allowed=False, reason="untracked_channel")
    # Tracked group: non-owner content is data — fence it before it enters a session.
    return TrustVerdict(
        allowed=True,
        reason="tracked_channel",
        fenced_text=fence_channel_content(text, provider, sender_id) if text else "",
    )
