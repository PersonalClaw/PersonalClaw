"""Sender trust as a core seam — the contract every channel transport binds to.

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
compares in constant time and consumes the code on success. Redemption is *reached* from
:func:`guard_inbound` — the one gate every transport already crosses — and not from each
transport, for the same reason the content fence lives there: a per-transport obligation is
a hope, not a property, and every shipping channel duly forgot it (#950).

**The owner's code.** :func:`create_owner_pairing_code` mints the code a channel's Configure page
shows; whoever sends it in a DM becomes that channel's OWNER (its id is stored under
``owner_id_credential(provider)``). Same machinery, plus a cap on wrong guesses
(:data:`OWNER_PAIRING_MAX_ATTEMPTS`), because it hands over the owner's DMs and approval prompts.
A channel whose messages carry the code inside other text (a mail's body) redeems it with
:func:`redeem_owner_pairing_code`, by the same rules.

**Audit.** These security events are emitted through the SEL: ``pairing_code_created`` and
``owner_pairing_code_created`` (never carrying a code), ``sender_paired``, ``owner_paired``,
``owner_pairing_cancelled``, ``sender_denied``.

**Observability.** A fail-closed gate that is also silent is indistinguishable from a dead
socket, so every verdict :func:`guard_inbound` reaches passes through
:func:`report_inbound_verdict` — the single owner of "say what happened to this message",
whose level is derived from the verdict rather than from a table of reasons. See that
function for the derivation and why the routine cases stay at DEBUG.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections import OrderedDict
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
#: SAME unknown sender. One SEL entry + one notification + one canned reply per sender per
#: window — never a flood from a chatty stranger, and never a reply per message.
UNKNOWN_SENDER_RENOTIFY_SECS = 24 * 3600

#: The canned reply a DM-policy=pairing transport sends back to an unknown sender.
CANNED_PAIRING_REPLY = (
    "I don't recognize you yet. Ask my owner for an 8-digit pairing code, "
    "then send it here to start talking."
)

#: The canned reply for the message that WAS a valid pairing code. The sender is now
#: allow-listed and their NEXT message is a real turn, but this one is spent on pairing —
#: so it comes back ``allowed=False, reason="paired"`` and the transport delivers this.
#: Lives here, beside :data:`CANNED_PAIRING_REPLY`, because both halves of the pairing
#: conversation are trust vocabulary: every channel must say the same thing.
CANNED_PAIRED_REPLY = "Paired — you can talk to me now."

#: The reply to the message that was the OWNER's code (:func:`create_owner_pairing_code`). True of
#: what pairing did: the sender's id is now the one core reaches the owner by on this channel
#: (``channel_delivery.reach_owner``), and they are trusted, so their next message is a turn.
CANNED_OWNER_PAIRED_REPLY = "Paired — you're my owner here now. I'll reach you in this chat."

#: Wrong 8-digit codes a provider takes while an owner code is outstanding before that code is
#: cancelled. An owner code hands over the owner's DMs and approval prompts, so guessing it is
#: capped rather than left to the TTL alone; the page that showed the code says it was cancelled.
OWNER_PAIRING_MAX_ATTEMPTS = 5


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
        "owner_pairing": {},
        "policies": {"dm": DEFAULT_DM_POLICY, "group": DEFAULT_GROUP_POLICY},
        "rate": {},
    }


# The literal above, exposed for tests/readers wanting the shape without a side effect.
_DEFAULT_PROVIDER = _default_provider()


# ── storage (fail-open read, atomic write) ───────────────────────────────────


def _read_store() -> dict[str, Any]:
    """The whole trust store, or ``{}`` on a corrupt/missing file (warn, never crash).

    Reads the raw path rather than delegating to ``_load_entity_settings``. **The reason
    originally given here — that the helper swallows a corrupt file silently — is not true:**
    it warns, and it now returns ``None`` for a discarded read rather than choosing a
    fallback at all. What keeps this reader separate is that the store is a *trust* surface,
    so `{}` here is the fail-CLOSED answer (nothing is trusted) and CE-1's contract is
    defaults + warn. Folding it into the shared helper would be a change to a security
    control's read path, which is a decision of its own and not a tidy-up.
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
    """Start tracking a group/room on ``provider``. Idempotent; emits ``channel_tracked``.

    A tracked group is one whose messages reach the agent, so tracking is a grant and is
    audited like one. The group leaves the seen-but-untracked list (:func:`note_untracked_channel`),
    and a name it was seen with is kept when none is given."""
    store = _read_store()
    rec = _provider_record(store, provider)
    raw_seen = rec.get("seen_channels")
    seen: dict[str, Any] = raw_seen if isinstance(raw_seen, dict) else {}
    seen_as = seen.pop(channel_id, None) or {}
    already = channel_id in (rec.get("tracked_channels") or {})
    rec.setdefault("tracked_channels", {})[channel_id] = {
        "name": name or str(seen_as.get("name", "") or ""),
        "added_at": _iso(_now()),
    }
    if seen_as:
        rec["seen_channels"] = seen
    store[provider] = rec
    _write_store(store)
    if not already:
        _emit_sel("channel_tracked", "owner", provider, channel_id)


def untrack(provider: str, channel_id: str) -> None:
    """Stop tracking a group/room on ``provider``. Idempotent; emits ``channel_untracked``."""
    store = _read_store()
    rec = _provider_record(store, provider)
    was = rec.get("tracked_channels", {}).pop(channel_id, None)
    store[provider] = rec
    _write_store(store)
    if was is not None:
        _emit_sel("channel_untracked", "owner", provider, channel_id)


#: How many untracked groups a provider remembers, and how often one group's sighting is
#: written again. A busy group the bot sits in sends every message through the gate, and each
#: one would otherwise be a store write.
SEEN_CHANNELS_MAX = 20
SEEN_CHANNEL_REWRITE_SECS = 3600


def note_untracked_channel(provider: str, channel_id: str, name: str = "") -> None:
    """Remember that an untracked group on ``provider`` messaged the agent.

    Its messages are refused silently (``untracked_channel``), which is right for a stranger's
    room and leaves the owner nowhere to track one they do want: the group's id is the vendor's,
    and nothing showed it. The Sender trust page lists these with a Track button. Bounded to the
    :data:`SEEN_CHANNELS_MAX` most recent, and written at most once per
    :data:`SEEN_CHANNEL_REWRITE_SECS` per group unless its name changed."""
    if not channel_id:
        return
    store = _read_store()
    rec = _provider_record(store, provider)
    if channel_id in (rec.get("tracked_channels") or {}):
        return
    raw_seen = rec.get("seen_channels")
    seen: dict[str, Any] = raw_seen if isinstance(raw_seen, dict) else {}
    now = _now()
    before = seen.get(channel_id) or {}
    try:
        recent = (now - datetime.fromisoformat(str(before.get("last_seen", "")))).total_seconds()
    except ValueError:
        recent = None
    if recent is not None and recent < SEEN_CHANNEL_REWRITE_SECS and before.get("name") == name:
        return
    seen[channel_id] = {"name": name or str(before.get("name", "") or ""), "last_seen": _iso(now)}
    if len(seen) > SEEN_CHANNELS_MAX:
        newest = sorted(seen.items(), key=lambda kv: str(kv[1].get("last_seen", "")), reverse=True)
        seen = dict(newest[:SEEN_CHANNELS_MAX])
    rec["seen_channels"] = seen
    store[provider] = rec
    _write_store(store)


# ── policies ────────────────────────────────────────────────────────────────


def trust_policies(provider: str) -> dict[str, str]:
    """The ``{"dm": ..., "group": ...}`` policy for ``provider`` (defaults if unset)."""
    return dict(_provider_record(_read_store(), provider)["policies"])


def set_trust_policies(
    provider: str, *, dm: str | None = None, group: str | None = None
) -> dict[str, str]:
    """Set ``provider``'s DM and/or group policy, and return the policies now in force.

    A value outside :data:`DM_POLICIES` / :data:`GROUP_POLICIES` raises ``ValueError`` and
    changes nothing. Each change emits ``trust_policy_changed`` with the new value as its
    outcome (``dm=open``, ``group=off``): who may talk to the agent is a security setting."""
    if dm is not None and dm not in DM_POLICIES:
        raise ValueError(f"dm policy must be one of {', '.join(DM_POLICIES)}")
    if group is not None and group not in GROUP_POLICIES:
        raise ValueError(f"group policy must be one of {', '.join(GROUP_POLICIES)}")
    store = _read_store()
    rec = _provider_record(store, provider)
    changed = [
        (kind, value)
        for kind, value in (("dm", dm), ("group", group))
        if value is not None and rec["policies"].get(kind) != value
    ]
    if changed:
        for kind, value in changed:
            rec["policies"][kind] = value
        store[provider] = rec
        _write_store(store)
        for kind, value in changed:
            _emit_sel("trust_policy_changed", f"{kind}={value}", provider)
    return dict(rec["policies"])


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
    attempts into a page about the allowlist. Untracked GROUPS that messaged the agent are
    projected (``seen_channels``): a group's id is the one thing the owner needs to track it,
    and nothing else shows it.
    """
    rec = _provider_record(_read_store(), provider)
    pairing = rec.get("pairing") or {}
    senders = rec.get("allowed_senders") or {}
    channels = rec.get("tracked_channels") or {}
    raw_seen = rec.get("seen_channels")
    seen: dict[str, Any] = raw_seen if isinstance(raw_seen, dict) else {}
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
        # Groups that messaged the agent while untracked, newest first: the page's Track button.
        "seen_channels": [
            {
                "channel_id": cid,
                "name": str((meta or {}).get("name", "") or ""),
                "last_seen": str((meta or {}).get("last_seen", "") or ""),
            }
            for cid, meta in sorted(
                seen.items(), key=lambda kv: str((kv[1] or {}).get("last_seen", "")), reverse=True
            )
            if cid not in channels
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


def cancel_pairing_code(provider: str) -> bool:
    """Cancel ``provider``'s outstanding sender pairing code. Returns whether there was one.

    Emits ``pairing_code_cancelled``. After this the code is refused like any wrong code."""
    store = _read_store()
    rec = _provider_record(store, provider)
    if not (rec.get("pairing") or {}).get("code_hash"):
        return False
    rec["pairing"] = {}
    store[provider] = rec
    _write_store(store)
    _emit_sel("pairing_code_cancelled", "owner", provider)
    return True


def _pairing_code_outstanding(provider: str) -> bool:
    """Whether ``provider`` has a pairing code on record at all (says nothing about it).

    Reads only the presence of ``code_hash`` — never its value, never whether a candidate
    matches, and deliberately NOT whether it has expired: an expired code must still reach
    :func:`redeem_pairing_code` so that function can clear the dead record and emit its
    ``expired_code`` denial. This exists so :func:`guard_inbound` can skip the redemption
    attempt entirely when no code was ever minted, which keeps an unpaired stranger who
    spams digit strings from driving an unbounded stream of ``no_active_code`` audit rows —
    the same flood the :data:`UNKNOWN_SENDER_RENOTIFY_SECS` window exists to prevent on the
    notification side.
    """
    pairing = _provider_record(_read_store(), provider).get("pairing") or {}
    return bool(pairing.get("code_hash"))


def _looks_like_a_pairing_code(text: str) -> bool:
    """Whether ``text`` has the exact shape :func:`create_pairing_code` mints.

    Exactly :data:`PAIRING_CODE_DIGITS` ASCII digits and nothing else. Stricter than a bare
    ``str.isdigit()`` on purpose: that accepts non-ASCII digit forms (Arabic-Indic, fullwidth
    …) which can never match an ASCII code hash, and accepts any length, so both would burn a
    redemption attempt — and an audit row — on text that could not possibly be a code.
    """
    return len(text) == PAIRING_CODE_DIGITS and text.isascii() and text.isdigit()


def redeem_pairing_code(provider: str, sender_id: str, code: str, name: str = "") -> bool:
    """Redeem ``code`` for ``sender_id`` on ``provider``.

    Within TTL and unused → the sender is allowed (``via="pairing"``, which emits
    ``sender_paired``) and the code is consumed. Expired / already-used / wrong code →
    ``False`` and ``sender_denied``. The compare is constant-time over the hashes.

    ``name`` is the sender's display name, kept beside the id so the Sender trust page can
    say who was let in; on most channels the id alone is a number."""
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
    allow_sender(provider, sender_id, name, via="pairing")
    return True


# ── the owner's pairing code (minted on the dashboard, redeemed at the gate) ─────────────────
#
# A sender's code (above) lets someone talk to the agent. The OWNER's code does more: whoever sends
# it in a DM becomes the channel's owner — the id core DMs results and approval prompts to
# (``owner_id_credential(<provider>)``). The owner id used to be written only by
# ``personalclaw setup``; a channel set up in the UI had none, and everything core sent the owner
# reached nobody. Same machinery as a sender's code — hash-only, single-use, TTL'd, constant-time —
# plus a cap on wrong guesses, because this code hands over the owner's approvals.
#
# ``owner_pairing`` holds either the outstanding code (``code_hash``, ``expires_at``,
# ``attempts``) or how the last one ended (``ended``: ``paired`` / ``expired`` / ``cancelled`` /
# ``too_many_attempts``, and ``ended_at``), so the page that showed the code can say what happened.


def create_owner_pairing_code(provider: str) -> str:
    """Create (and return once) the code that makes its sender ``provider``'s owner.

    One outstanding owner code per provider — a new one replaces it. TTL
    :data:`PAIRING_CODE_TTL_SECS`; only the SHA-256 hash is stored, and neither the code nor its
    hash is ever projected or logged. Emits ``owner_pairing_code_created``."""
    code = f"{secrets.randbelow(10**PAIRING_CODE_DIGITS):0{PAIRING_CODE_DIGITS}d}"
    now = _now()
    store = _read_store()
    rec = _provider_record(store, provider)
    rec["owner_pairing"] = {
        "code_hash": _hash_code(code),
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=PAIRING_CODE_TTL_SECS)),
        "attempts": 0,
    }
    store[provider] = rec
    _write_store(store)
    _emit_sel("owner_pairing_code_created", "created", provider)
    return code


def _ended(how: str) -> dict[str, Any]:
    return {"ended": how, "ended_at": _iso(_now())}


def _owner_code_expired(record: dict[str, Any]) -> bool:
    try:
        return _now() > datetime.fromisoformat(str(record.get("expires_at", "")))
    except ValueError:
        return True


def cancel_owner_pairing(provider: str) -> bool:
    """Cancel ``provider``'s outstanding owner code. True when one was outstanding."""
    store = _read_store()
    rec = _provider_record(store, provider)
    outstanding = bool((rec.get("owner_pairing") or {}).get("code_hash"))
    if outstanding:
        rec["owner_pairing"] = _ended("cancelled")
        store[provider] = rec
        _write_store(store)
        _emit_sel("owner_pairing_cancelled", "cancelled", provider)
    return outstanding


def owner_pairing_status(provider: str) -> dict[str, Any]:
    """Whether an owner code is outstanding and, once none is, how the last one ended.

    ``{"active", "expires_at", "attempts_left", "ended", "ended_at"}`` — never the code or its
    hash. A code past its TTL reads as ``ended: "expired"`` at once, whether or not anyone has
    tried it since."""
    record = _provider_record(_read_store(), provider).get("owner_pairing") or {}
    if record.get("code_hash"):
        if _owner_code_expired(record):
            return {
                "active": False,
                "expires_at": "",
                "attempts_left": 0,
                "ended": "expired",
                "ended_at": str(record.get("expires_at", "")),
            }
        return {
            "active": True,
            "expires_at": str(record.get("expires_at", "")),
            "attempts_left": max(0, OWNER_PAIRING_MAX_ATTEMPTS - int(record.get("attempts", 0))),
            "ended": "",
            "ended_at": "",
        }
    return {
        "active": False,
        "expires_at": "",
        "attempts_left": 0,
        "ended": str(record.get("ended", "")),
        "ended_at": str(record.get("ended_at", "")),
    }


def _owner_code_verdict(provider: str, candidate: str) -> str:
    """``"match"`` / ``"miss"`` for a code-shaped DM while an owner code is live, else ``""``.

    Side-effect free except for retiring a code found past its TTL. The compare is constant-time
    over the hashes, as for a sender's code."""
    store = _read_store()
    rec = _provider_record(store, provider)
    record = rec.get("owner_pairing") or {}
    stored = str(record.get("code_hash", ""))
    if not stored:
        return ""
    if _owner_code_expired(record):
        rec["owner_pairing"] = {"ended": "expired", "ended_at": str(record.get("expires_at", ""))}
        store[provider] = rec
        _write_store(store)
        return ""
    return "match" if hmac.compare_digest(stored, _hash_code(candidate)) else "miss"


def _pair_owner(provider: str, sender_id: str, sender_name: str) -> None:
    """Make ``sender_id`` the owner of ``provider``: its own owner key, trusted, code spent."""
    from personalclaw.config.credentials import owner_id_credential, save_credential

    save_credential(owner_id_credential(provider), sender_id)
    store = _read_store()
    rec = _provider_record(store, provider)
    rec["owner_pairing"] = _ended("paired")
    store[provider] = rec
    _write_store(store)
    allow_sender(provider, sender_id, sender_name, via="owner_pairing")
    _emit_sel("owner_paired", "paired", provider, sender_id)


def redeem_owner_pairing_code(provider: str, sender_id: str, code: str, name: str = "") -> bool:
    """Redeem the OWNER's code where a message carries it inside other text.

    :func:`guard_inbound` redeems the owner's code when a DM is the code and nothing else. A
    channel whose messages cannot be that (a mail's body, under a quote and a signature) finds
    the code-shaped words in the text itself and hands each here, after a sender's code had its
    turn (:func:`redeem_pairing_code`). A match makes ``sender_id`` the owner exactly as the gate
    does: stored under ``owner_id_credential(provider)``, trusted, the code spent,
    ``owner_paired`` audited. A code-shaped word that does not match counts as a wrong guess at
    the outstanding code, as a code-shaped DM does at the gate, so the cap on wrong guesses
    (:data:`OWNER_PAIRING_MAX_ATTEMPTS`) holds however the code arrives. ``False`` when no owner
    code is outstanding, when it expired, and for text that is not code-shaped.
    """
    candidate = (code or "").strip()
    if not _looks_like_a_pairing_code(candidate):
        return False
    verdict = _owner_code_verdict(provider, candidate)
    if verdict == "match":
        _pair_owner(provider, sender_id, name)
        return True
    if verdict == "miss":
        _count_wrong_owner_code(provider, sender_id)
    return False


def _count_wrong_owner_code(provider: str, sender_id: str) -> None:
    """One wrong guess at the outstanding owner code; the last allowed one cancels it."""
    store = _read_store()
    rec = _provider_record(store, provider)
    record = rec.get("owner_pairing") or {}
    if not record.get("code_hash"):
        return
    attempts = int(record.get("attempts", 0)) + 1
    if attempts >= OWNER_PAIRING_MAX_ATTEMPTS:
        rec["owner_pairing"] = _ended("too_many_attempts")
        outcome = "owner_code_cancelled_after_wrong_codes"
    else:
        rec["owner_pairing"] = {**record, "attempts": attempts}
        outcome = "wrong_owner_code"
    store[provider] = rec
    _write_store(store)
    _emit_sel("sender_denied", outcome, provider, sender_id)


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


# ── the observability half of a verdict ───────────────────────────────────────

#: How many recent ``(provider, subject, reason)`` dispositions to remember for the
#: visible-line window. Bounded so a bot sitting in a hundred untracked channels cannot
#: grow this without limit; FIFO eviction, and an evicted subject simply re-announces —
#: the safe direction for a mechanism whose failure mode is silence.
_REPORT_WINDOW_MAX = 512

#: subject key → when its last operator-visible line was emitted.
_REPORTED: "OrderedDict[str, datetime]" = OrderedDict()


def reset_inbound_reports() -> None:
    """Forget every remembered disposition, so the next drop announces again. For tests."""
    _REPORTED.clear()


def _visible_line_is_deduped(key: str) -> bool:
    """Whether ``key`` already had an operator-visible line inside the renotify window.

    Asking is what *claims* the window: a miss records the stamp before returning. So this
    may only be called for a line the caller is about to emit, which keeps the "one visible
    line per subject per window" invariant in one place instead of splitting it across a
    check and a later record that a future edit could separate."""
    now = _now()
    last = _REPORTED.get(key)
    if last is not None and (now - last).total_seconds() < UNKNOWN_SENDER_RENOTIFY_SECS:
        return True
    _REPORTED[key] = now
    _REPORTED.move_to_end(key)
    while len(_REPORTED) > _REPORT_WINDOW_MAX:
        _REPORTED.popitem(last=False)
    return False


def report_inbound_verdict(
    provider: str,
    verdict: TrustVerdict,
    *,
    sender_id: str = "",
    channel_id: str = "",
    is_dm: bool = True,
    policy: str = "",
) -> TrustVerdict:
    """Say what happened to one inbound message, then hand the verdict straight back.

    Every branch of :func:`guard_inbound` used to be a bare ``return``, so a *correct*
    fail-closed decision and a dead socket produced the identical observation: nothing, at
    no level, not even DEBUG. Proving that an @-mention in an untracked group channel had
    ever been sent meant reading the channel history back out of the vendor's REST API.
    This function is the one owner of that reporting, and it returns its argument so every
    mint site reads ``return report_inbound_verdict(...)`` — which is what makes "one owner"
    structurally checkable rather than a convention that the next branch forgets.

    **The level is DERIVED from the verdict, never from a table of reasons.** A
    reason→level map needs a new row for every future policy, and the row gets forgotten
    exactly when a new silent drop is introduced. But the verdict already records who was
    told: ``canned_reply`` is text the *sender* receives, ``fired_notification`` is the
    *owner* notification this call raised. So:

    * ``allowed`` → DEBUG. The message becomes a turn, and the session transcript plus the
      dashboard broadcast are the evidence. A per-message INFO line on the happy path is
      the noise that teaches an operator to stop reading the log — but the line exists,
      because "traffic is arriving and being admitted" is the one observation that separates
      a healthy socket from a dead one.
    * denied, and somebody was told → INFO. The loop is closed in-channel (a pairing nudge)
      or in the notification centre, so this line corroborates rather than being the only
      trace. It is still INFO and not DEBUG: a denied sender is a decision, not a detail.
    * denied, and NOBODY was told → WARNING. This is the reported defect's exact shape. An
      untracked group channel deliberately returns no ``canned_reply`` (no owner spam) and
      raises no notification, so absent this line the message leaves no trace anywhere. The
      derivation also catches a case the report missed: an ``owner_only`` DM inside the 24h
      renotify window, where silence toward the sender is by policy and the notification is
      deduped, leaving the drop invisible on every axis at once.

    **Flood control reuses the store's existing rule instead of inventing one.** A bot
    holding MESSAGE_CONTENT sees every message in every visible channel, so one WARNING per
    message in an untracked ``#general`` is itself a flood. The first visible line per
    subject per :data:`UNKNOWN_SENDER_RENOTIFY_SECS` is emitted at its derived level and
    later repeats fall to DEBUG — the same "one alert per subject per window, never one per
    message" rule :func:`note_unknown_sender` already applies to the owner notification.
    Nothing ever becomes silent: a suppressed line is demoted, not dropped.

    That window lives in memory rather than in the store because the drop being reported
    happens on *every* inbound message of a busy untracked channel, and a persisted stamp
    would mean a disk write per message — the flood merely moves from the log to the
    filesystem. A restart therefore re-announces once, which is the right bias: the operator
    who restarted to diagnose this is exactly who needs to see it.

    **Subject** is whatever the operator would have to change to unblock the message — the
    channel for a group denial, the sender for a DM one. That is the ``is_dm`` split the gate
    already makes, so it is not a third axis to keep in sync. ``reason`` is part of the key,
    so a channel that goes from ``untracked_channel`` to ``group_policy_off`` announces
    again rather than hiding behind the earlier line.

    The line carries sender and channel identifiers but never message text. The identifiers
    are already persisted in the trust store and already carried by the ``sender_denied``
    SEL row's ``caller_identity``, so this adds no exposure that did not exist; the body is
    untrusted third-party content and has no business in an operator's log.

    ``policy`` is empty when the disposition was reached without consulting one (the pairing
    short-circuit in :mod:`personalclaw.channel_inbound`) and renders as ``-``, because
    naming a policy that was never read would put a value outside :data:`DM_POLICIES` into
    the operator's vocabulary. It doubles as the signal for whether a remedy hint applies —
    see the comment on ``remedy`` below.
    """
    scope = "dm" if is_dm else "group"
    subject = sender_id if is_dm else channel_id
    # Two branches on the gate's own axis, not a per-reason remedy table: whatever the
    # reason, a DM denial clears by trusting the sender and a group denial by tracking the
    # channel (or changing the policy that refused it). Carried only on a POLICY denial —
    # a verdict reported without a policy was not a policy decision, so there is nothing
    # for the operator to change, and telling them to "pair this sender" on the very
    # message that just paired them would be worse than saying nothing.
    remedy = ""
    if policy and not verdict.allowed:
        remedy = (
            " — pair or allow this sender"
            if is_dm
            else " — track this channel or change the policy"
        )

    if verdict.allowed:
        level = logging.DEBUG
    elif verdict.canned_reply or verdict.fired_notification:
        level = logging.INFO
    else:
        level = logging.WARNING

    deduped = False
    if level > logging.DEBUG:
        deduped = _visible_line_is_deduped(f"{provider}|{scope}|{subject}|{verdict.reason}")
        if deduped:
            level = logging.DEBUG

    logger.log(
        level,
        "channel inbound %s: provider=%s scope=%s reason=%s policy=%s sender=%s channel=%s%s%s",
        "admitted" if verdict.allowed else "discarded",
        provider,
        scope,
        verdict.reason or "-",
        policy or "-",
        sender_id or "-",
        channel_id or "-",
        remedy,
        " (repeat inside the renotify window)" if deduped else "",
    )
    return verdict


def channel_display_name(provider: str) -> str:
    """What the owner calls ``provider``: its registered transport's display name, else the key.

    Core never names a vendor, and the key is the transport's own choice (``telegram``), so the
    name a sentence shows comes from the channel that registered it."""
    try:
        from personalclaw.channel_transports import get_transport

        transport = get_transport(provider)
        name = str(getattr(transport, "display_name", "") or "") if transport else ""
    except Exception:  # noqa: BLE001 - a name is presentation; the key still identifies it
        logger.debug("display name for channel %s unavailable", provider, exc_info=True)
        name = ""
    return name or provider


def note_unknown_sender(state: Any, provider: str, sender_id: str, sender_name: str = "") -> bool:
    """Record + surface a first contact from an unknown sender. Returns whether it fired.

    Emits exactly ONE ``sender_denied`` SEL entry and ONE actionable owner notification per
    sender per :data:`UNKNOWN_SENDER_RENOTIFY_SECS` window — a chatty stranger cannot flood
    either. The notification carries ``actions=["allow","deny"]`` plus the ``provider`` /
    ``sender_id`` the Allow button needs; a click routes to :func:`apply_trust_action`,
    which persists the sender. Whether the sender also gets the canned reply is
    :func:`guard_inbound`'s call (policy ``pairing`` only), riding the same window.

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
            where = channel_display_name(provider)
            state.notify(
                notification_kinds.WARNING,
                f"Someone you haven't paired messaged you on {where}",
                f"{who} messaged your agent on {where} and isn't paired. "
                "Allow them to talk to it, or deny.",
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
    channel_name: str = "",
) -> TrustVerdict:
    """THE trust gate a transport calls at the top of its inbound path.

    This is the seam CE-2..9 (Telegram/Discord/email + External-Access) bind to, so its
    shape is a contract. It applies the provider's policy:

    * **DM**, policy ``open`` → allowed. Policy ``pairing`` / ``owner_only`` → allowed only
      if the sender is already approved; otherwise the unknown-sender flow fires
      (:func:`note_unknown_sender`) and the message is denied. ``pairing`` returns the
      canned pairing-needed reply with the notification, so once per sender per
      :data:`UNKNOWN_SENDER_RENOTIFY_SECS`; ``owner_only`` stays silent (open question
      resolved: no in-channel reply).
    * **DM**, policy ``pairing``, and the message is exactly an outstanding 8-digit code →
      :func:`redeem_pairing_code` consumes it and the sender joins the allowlist
      (``via="pairing"``). The verdict is ``allowed=False, reason="paired"`` carrying
      :data:`CANNED_PAIRED_REPLY`: the sender is trusted from their NEXT message on, but a
      pairing code is not something the agent should be asked to answer. Redemption is
      applied HERE for exactly the reason the fence is — see below — because a transport
      that had to remember it forgot: before this, ``redeem_pairing_code`` was reachable
      only through the platform's inbound door, which no shipping channel crossed, so
      ``personalclaw pair`` minted codes that nothing could spend (#950). Policy
      ``owner_only`` deliberately does NOT redeem: there, the owner's Allow is the only
      door, and honouring a code would make ``owner_only`` no stronger than ``pairing``.
    * **DM**, and the message is exactly the outstanding OWNER code
      (:func:`create_owner_pairing_code`, minted on the channel's Configure page) → the sender
      becomes the channel's owner: their id is stored under ``owner_id_credential(provider)``
      and they are trusted (``via="owner_pairing"``). Checked before the policy, under every
      policy and for a sender already trusted — the owner minted it, so it is the owner's own
      act, and under ``open`` the code must not become a turn. The verdict is
      ``allowed=False, reason="owner_paired"`` carrying :data:`CANNED_OWNER_PAIRED_REPLY`. A
      code-shaped DM that matches neither code counts against the owner code, which
      :data:`OWNER_PAIRING_MAX_ATTEMPTS` wrong ones cancel.
    * **group/room**, policy ``off`` → denied (``group_policy_off``). Policy
      ``tracked_only`` → allowed only for a tracked channel; an untracked group is denied
      (``untracked_channel``) without any in-channel reply, so a stranger's room cannot spam
      the owner. It is remembered (:func:`note_untracked_channel`, with ``channel_name`` when
      the transport knows it) so the owner can track it from the Sender trust page.

    Those two group denials used to share the single reason ``untracked_channel``, which was
    a lie for half of them: an operator reading it could not tell "you switched groups off"
    from "you never tracked this room", and the second is one :func:`track` call away from
    working. The reason now names the policy value that refused the message
    (:data:`GROUP_POLICIES`), so the vocabulary is derived rather than invented.

    Denied is not the same as unobservable. Every verdict below leaves through
    :func:`report_inbound_verdict`, which decides — from the verdict itself — whether the
    operator sees a line or only DEBUG does. Silence toward the *sender* is a policy choice;
    silence toward the *owner* was a defect.

    When allowed non-owner group content is present, ``fenced_text`` carries the
    :func:`fence_channel_content` wrapping the transport must use before the text enters a
    session — the fence is applied HERE so a transport can't forget it."""
    if is_dm:
        candidate = (text or "").strip()
        # The OWNER's code first, under every DM policy and for a sender already trusted: it was
        # minted by the owner on the dashboard, so it is the owner's own act — the door
        # ``owner_only`` leaves open — and under ``open`` it must not reach the agent as a turn.
        owner_code = (
            _owner_code_verdict(provider, candidate)
            if _looks_like_a_pairing_code(candidate)
            else ""
        )
        if owner_code == "match":
            _pair_owner(provider, sender_id, sender_name)
            return report_inbound_verdict(
                provider,
                TrustVerdict(
                    allowed=False,
                    reason="owner_paired",
                    canned_reply=CANNED_OWNER_PAIRED_REPLY,
                    meta={"paired": True, "owner": True},
                ),
                sender_id=sender_id,
                channel_id=channel_id,
                is_dm=True,
            )
        policy = trust_policies(provider).get("dm", DEFAULT_DM_POLICY)
        if policy == "open" or is_allowed_sender(provider, sender_id):
            verdict = TrustVerdict(allowed=True, reason="allowed")
        else:
            # Redemption happens HERE, for the same reason the fence does: this is the one
            # function every transport already crosses, so no channel can forget it. Only
            # under policy ``pairing`` — that policy's canned reply is literally an
            # instruction to send a code, whereas ``owner_only`` means the owner's Allow is
            # the ONLY way in and a code must not be a second door.
            if (
                policy == "pairing"
                and _looks_like_a_pairing_code(candidate)
                and _pairing_code_outstanding(provider)
                and redeem_pairing_code(provider, sender_id, candidate, sender_name)
            ):
                # Not a turn for the agent: the sender is trusted from their NEXT message on,
                # and this one is spent on pairing. ``allowed=False`` keeps the code out of
                # the session transcript as a side benefit. Reported WITHOUT a ``policy`` so
                # it carries no remedy hint: telling a sender to "pair" on the very message
                # that just paired them is worse than silence (see report_inbound_verdict).
                return report_inbound_verdict(
                    provider,
                    TrustVerdict(
                        allowed=False,
                        reason="paired",
                        canned_reply=CANNED_PAIRED_REPLY,
                        meta={"paired": True},
                    ),
                    sender_id=sender_id,
                    channel_id=channel_id,
                    is_dm=True,
                )
            fired = note_unknown_sender(state, provider, sender_id, sender_name)
            # The reply rides the same per-sender window as the notification
            # (UNKNOWN_SENDER_RENOTIFY_SECS): once per stranger per window, never once per
            # message. Answering every message put one reply on the wire for each mail anyone
            # sent to a mailbox the email channel watches, from the owner's own address.
            verdict = TrustVerdict(
                allowed=False,
                reason="unknown_sender",
                canned_reply=CANNED_PAIRING_REPLY if fired and policy == "pairing" else "",
                fired_notification=fired,
            )
        # A code-shaped DM that was neither the owner's code nor a sender's code just redeemed
        # is a wrong guess at the owner's — counted after the sender's code had its turn, so
        # redeeming that is never charged against this one.
        if owner_code == "miss":
            _count_wrong_owner_code(provider, sender_id)
        return report_inbound_verdict(
            provider,
            verdict,
            sender_id=sender_id,
            channel_id=channel_id,
            is_dm=True,
            policy=policy,
        )

    # Group / room. ``off`` is checked first so the store is not read a second time for a
    # channel the policy already refuses.
    gpolicy = trust_policies(provider).get("group", DEFAULT_GROUP_POLICY)
    if gpolicy == "off":
        verdict = TrustVerdict(allowed=False, reason="group_policy_off")
    elif not is_tracked_channel(provider, channel_id):
        note_untracked_channel(provider, channel_id, channel_name)
        verdict = TrustVerdict(allowed=False, reason="untracked_channel")
    else:
        # Tracked group: non-owner content is data — fence it before it enters a session.
        # An ALLOWED sender is exempt: the docstring above has always promised the fence for
        # non-owner content, and fencing the owner's own message would make an agent read the
        # owner's instruction in a linked group thread as untrusted data it must not act on.
        verdict = TrustVerdict(
            allowed=True,
            reason="tracked_channel",
            fenced_text=(
                fence_channel_content(text, provider, sender_id)
                if text and not is_allowed_sender(provider, sender_id)
                else ""
            ),
        )
    return report_inbound_verdict(
        provider,
        verdict,
        sender_id=sender_id,
        channel_id=channel_id,
        is_dm=False,
        policy=gpolicy,
    )
