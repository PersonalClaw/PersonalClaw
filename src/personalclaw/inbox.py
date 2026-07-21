"""Inbox — persisted message state read by the dashboard Inbox page.

Holds the inbox entity (items, per-user/channel state, retention) that the
dashboard inbox handlers read and mutate. Live message ingestion is provided
separately by the message-source providers in ``personalclaw.inbox_providers``.
"""

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

__all__ = [
    "Classification",
    "Confidence",
    "ItemStatus",
    "InboxStore",
    "InboxItem",
    "InboxState",
    "UserResolver",
    "evaluate_alert",
    "notify_inbox_alert",
]

_STATE_FILE = "inbox_state.json"
_ITEMS_FILE = "inbox.json"
_USER_CACHE_TTL = 86400  # 24 hours


# ── Models ──


class ItemStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    DISMISSED = "dismissed"
    HANDLED = "handled"  # user replied at the source (or via inbox reply routing)


class Classification(str, Enum):
    NEEDS_REPLY = "needs_reply"
    FYI = "fyi"
    NOISE = "noise"


class Confidence(str, Enum):
    HIGH = "high"
    NEEDS_REVIEW = "needs_review"
    ESCALATE = "escalate"


@dataclass
class InboxItem:
    """A message surfaced by Inbox with an optional draft reply."""

    id: str  # channel_ts (unique key)
    channel: str
    channel_name: str
    thread_ts: str | None
    message: str
    sender_id: str
    sender_name: str
    thread_context: list[dict[str, str]] = field(default_factory=list)
    classification: str = Classification.NEEDS_REPLY
    draft: str = ""
    confidence: str = Confidence.NEEDS_REVIEW
    status: str = ItemStatus.PENDING
    created_at: float = 0.0
    context_summary: str = ""  # what context the LLM used for drafting
    # Which source provider produced this item (native / filesystem / slack / …).
    source: str = "native"
    # Whether this item's source supports a user reply (drives the UI Send gate).
    # Native agent-posted questions route the reply back to the posting agent's
    # session (reply_target); poll-based sources reply through their provider.
    can_reply: bool = False
    reply_target: str = ""  # native: the posting agent's session key for reply routing
    # P11: whether the user favorited this item — a strong positive engagement signal
    # feeding the engagement-ranking multiplier (tolerant from_dict makes it back-compat).
    favorited: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts
        return d

    @property
    def ts(self) -> str:
        """Message timestamp extracted from the item ID ({channel}_{ts})."""
        return self.id.rsplit("_", 1)[-1]

    @classmethod
    def from_dict(cls, d: dict) -> "InboxItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── User Resolver ──


class UserResolver:
    """Caches user id → display name (persisted with the inbox state). Names are
    resolved by the message source that has the channel client; this just stores them."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}  # user_id → (name, ts)

    def load(self, data: dict[str, Any]) -> None:
        for uid, entry in data.items():
            if isinstance(entry, dict):
                self._cache[uid] = (entry.get("name", uid), entry.get("ts", 0.0))

    def dump(self) -> dict[str, Any]:
        return {uid: {"name": n, "ts": ts} for uid, (n, ts) in self._cache.items()}

    def get_cached(self, user_id: str) -> str | None:
        entry = self._cache.get(user_id)
        if entry and (time.time() - entry[1]) < _USER_CACHE_TTL:
            return entry[0]
        return None

    def put(self, user_id: str, name: str) -> None:
        self._cache[user_id] = (name, time.time())


# ── State Persistence ──


class InboxState:
    """Persists polling state, user cache, and dismissed/muted sets."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_dir() / _STATE_FILE)
        self.last_read_ts: dict[str, str] = {}  # channel_id → ts
        self.channel_names: dict[str, str] = {}  # channel_id → display name
        self.dismissed: set[str] = set()  # item IDs
        self.muted_threads: set[str] = set()  # thread_ts values
        self.active_threads: dict[str, dict[str, str]] = {}  # channel → {thread_ts → last_reply_ts}
        self.user_resolver = UserResolver()
        self._user_alias: str | None = None

    def load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self.last_read_ts.clear()
                self.channel_names.clear()
                self.dismissed.clear()
                self.muted_threads.clear()
                self.last_read_ts.update(data.get("last_read_ts", {}))
                self.channel_names.update(data.get("channel_names", {}))
                self.dismissed.update(data.get("dismissed", []))
                self.muted_threads.update(data.get("muted_threads", []))
                self.user_resolver.load(data.get("user_cache", {}))
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load inbox state, starting fresh")

    def save(self) -> None:
        data = {
            "last_read_ts": self.last_read_ts,
            "channel_names": self.channel_names,
            "dismissed": list(self.dismissed),
            "muted_threads": list(self.muted_threads),
            "user_cache": self.user_resolver.dump(),
        }
        try:
            atomic_write(self._path, json.dumps(data, indent=2), mode=0o600)
        except OSError:
            logger.warning("Failed to save inbox state")

    def prune_dismissed(self, retention_hours: float = 168.0) -> int:
        """Remove dismissed IDs older than retention_hours."""
        cutoff = time.time() - (retention_hours * 3600)
        stale = set()
        for did in self.dismissed:
            parts = did.rsplit("_", 1)
            try:
                if float(parts[-1]) < cutoff:
                    stale.add(did)
            except (ValueError, IndexError):
                stale.add(did)
        self.dismissed -= stale
        return len(stale)


# ── Inbox (item storage) ──


class InboxStore:
    """Persists InboxItems to disk."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_dir() / _ITEMS_FILE)
        self.items: dict[str, InboxItem] = {}  # id → item
        self._dirty = False

    def load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self.items.clear()
                for d in data.get("items", []):
                    item = InboxItem.from_dict(d)
                    self.items[item.id] = item
                self._dirty = False
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load inbox items, starting fresh")

    def save(self) -> None:
        data = {"items": [item.to_dict() for item in self.items.values()]}
        try:
            atomic_write(self._path, json.dumps(data, indent=2), mode=0o600)
            self._dirty = False
        except OSError:
            logger.warning("Failed to save inbox items")

    def add(self, item: InboxItem) -> None:
        self.items[item.id] = item
        self._dirty = True

    def flush(self) -> None:
        """Save to disk if there are pending changes."""
        if self._dirty:
            self.save()

    def update(self, item_id: str, **kwargs: Any) -> InboxItem | None:
        item = self.items.get(item_id)
        if not item:
            return None
        for k, v in kwargs.items():
            if hasattr(item, k):
                setattr(item, k, v)
        self.save()
        return item

    def pending(self) -> list[InboxItem]:
        return [i for i in self.items.values() if i.status == ItemStatus.PENDING]

    def cleanup_by_retention(self, retention_days: int = 90) -> int:
        """Delete items older than *retention_days*, regardless of status.

        The single inbox retention mechanism (source-agnostic — items from the
        native push sink, poll providers, and digests age out uniformly). Runs
        from the InboxService maintenance loop when auto-cleanup is enabled.
        """
        cutoff = time.time() - (retention_days * 86400)
        expired = [item_id for item_id, item in self.items.items() if item.created_at < cutoff]
        for item_id in expired:
            del self.items[item_id]
        if expired:
            self.save()
            logger.info("Inbox auto-cleanup: deleted %d expired items", len(expired))
        return len(expired)


# ── Alerts ──


def evaluate_alert(item: InboxItem, settings: dict, user_name: str = "") -> str:
    """Why *item* deserves an immediate notification, or "" if it doesn't.

    Reads the inbox entity settings (``alert_keywords`` — case-insensitive
    substring match — and ``alert_on_name_mention`` against the operator's
    name). Called once per NEW item at ingestion (native push + poll paths).
    """
    text = (item.message or "").lower()
    if not text:
        return ""
    for kw in settings.get("alert_keywords") or []:
        k = str(kw).strip().lower()
        if k and k in text:
            return f"keyword: {kw}"
    if settings.get("alert_on_name_mention") and user_name.strip():
        # Match name PARTS as whole words ("Jordan Marlow" fires on "hey Jordan")
        # — messages use first names, not the configured full name. Skip short
        # fragments (initials, particles) that would false-positive.
        for part in user_name.strip().lower().split():
            if len(part) >= 3 and re.search(rf"\b{re.escape(part)}\b", text):
                return "name mention"
    return ""


def notify_inbox_alert(state: Any, item: InboxItem, reason: str) -> None:
    """Fire a dashboard notification for an alert-worthy inbox item.

    Message text is external/untrusted — redacted before it enters the
    notification feed (same treatment as the inbox item handlers)."""
    if state is None:
        return
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    msg, _ = redact_exfiltration_urls(item.message)
    msg, _ = redact_credentials(msg)
    state.notify(
        "inbox_alert",
        f"{item.sender_name} in {item.channel_name}",
        f"Alert ({reason}): {msg[:200]}",
        meta={"session": f"inbox:{item.id}"},
    )
