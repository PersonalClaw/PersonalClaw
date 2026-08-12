"""Inbox — persisted message state read by the dashboard Inbox page.

Holds the inbox entity (items, per-user/channel state, retention) that the
dashboard inbox handlers read and mutate. Live message ingestion is provided
separately by the message-source providers in ``personalclaw.inbox_providers``.
"""

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from personalclaw import notification_kinds
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
    "ItemKind",
    "NON_CHANNEL_KINDS",
    "SOURCE_DECLARABLE_KINDS",
    "make_item_id",
    "emit_attention_item",
    "evaluate_alert",
    "notify_inbox_alert",
]

_STATE_FILE = "inbox_state.json"
_ITEMS_FILE = "inbox.json"
_USER_CACHE_TTL = 86400  # 24 hours


# ── Models ──


class ItemStatus(str, Enum):
    """The attention lifecycle: PENDING → SEEN → HANDLED | DISMISSED.

    SENT predates the others and is specific to reply-drafts (a draft was sent at the
    source); it stays because those items exist on disk and it means something the other
    four don't.
    """

    PENDING = "pending"
    SEEN = "seen"  # surfaced to the user but not yet acted on — the read/unread boundary
    SENT = "sent"
    DISMISSED = "dismissed"
    HANDLED = "handled"  # user replied at the source (or via inbox reply routing)


class ItemKind(str, Enum):
    """What kind of thing is asking for attention.

    ``MESSAGE`` is the default so every item written before this existed stays valid —
    the inbox began as a channel-message surface, and that is exactly what those items are.
    """

    MESSAGE = "message"
    MENTION = "mention"
    EMAIL = "email"
    AGENT_REQUEST = "agent_request"
    PROPOSAL = "proposal"
    NEEDS_INPUT = "needs_input"
    DIGEST = "digest"
    SYSTEM = "system"


#: Kinds with no channel behind them: no draft, no reply routing, no send affordance.
#: The UI uses this to decide whether a row gets reply machinery at all.
NON_CHANNEL_KINDS = frozenset(
    {
        ItemKind.AGENT_REQUEST.value,
        ItemKind.PROPOSAL.value,
        ItemKind.NEEDS_INPUT.value,
        ItemKind.DIGEST.value,
        ItemKind.SYSTEM.value,
    }
)

#: The kinds a MESSAGE SOURCE may declare for its own items (``IncomingMessage.kind``).
#:
#: Exactly the channel-shaped kinds — everything in :class:`ItemKind` that is NOT in
#: :data:`NON_CHANNEL_KINDS`. Enumerated literally rather than computed by subtraction so
#: both ends read as a closed set at a glance; ``test_inbox_item_kind_seam`` asserts the
#: two stay equal, so adding an enum member cannot silently skip this decision.
#:
#: The narrowing is the point: the non-channel kinds are core's OWN attention vocabulary,
#: raised only through :func:`emit_attention_item` with the ``refs`` that make them
#: actionable. A source that could claim ``proposal`` would produce a row with no refs,
#: no deep-link and no reply — a dead row wearing a live kind's chip.
SOURCE_DECLARABLE_KINDS = frozenset(
    {
        ItemKind.MESSAGE.value,
        ItemKind.MENTION.value,
        ItemKind.EMAIL.value,
    }
)


def make_item_id(kind: str, *, now: float | None = None) -> str:
    """An id for a non-channel item: ``{kind}_{uuid8}_{ts}``.

    **The trailing ``_{ts}`` is load-bearing.** ``InboxItem.ts`` rsplits the id on the last
    underscore, and sorting/retention both read that property — an id without a numeric
    tail would silently sort as if it had no timestamp. The uuid8 in the middle is what
    keeps two same-second items of the same kind distinct, which ``{channel}_{ts}`` got for
    free from the channel's own message ids.
    """
    stamp = time.time() if now is None else now
    return f"{kind}_{uuid.uuid4().hex[:8]}_{stamp:.6f}"


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
    # Which source provider produced this item — its ``source_name`` (native /
    # filesystem / an app-contributed channel source / …).
    source: str = "native"
    # Whether this item's source supports a user reply (drives the UI Send gate).
    # Native agent-posted questions route the reply back to the posting agent's
    # session (reply_target); poll-based sources reply through their provider.
    can_reply: bool = False
    reply_target: str = ""  # native: the posting agent's session key for reply routing
    # P11: whether the user favorited this item — a strong positive engagement signal
    # feeding the engagement-ranking multiplier (tolerant from_dict makes it back-compat).
    favorited: bool = False
    # What kind of attention this item wants. Defaults to `message` so every item written
    # before the inbox became a general attention store stays valid and unchanged.
    item_kind: str = ItemKind.MESSAGE.value
    # Ids of the things this item is ABOUT: {"session":…, "loop":…, "skill_proposal": pid,
    # "workflow":…}. Deep-linking is what makes a needs_input row actionable rather than a
    # notification with extra steps.
    refs: dict = field(default_factory=dict)

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


def evaluate_alert(item: InboxItem, user_name: str = "") -> str:
    """Why *item* deserves an immediate notification, or "" if it doesn't.

    Now reads the ``inbox/alert`` notification RULE's conditions rather than the retired
    ``alert_keywords``/``alert_on_name_mention`` inbox fields (plan 42 S3). The matching
    semantics are unchanged — `Conditions.matches` was lifted from this function's own
    body — so a user whose keywords were backfilled sees identical behavior; what changed
    is that the same conditions are now expressible for every notification kind, not just
    inbox messages.

    The ``settings`` parameter is gone rather than kept and ignored: a caller still passing
    a dict of retired fields would silently get no alerts, which is exactly the failure a
    clean break is supposed to make impossible.
    """
    text = item.message or ""
    if not text.strip():
        return ""
    try:
        from personalclaw import notification_rules

        rule = notification_rules.resolve_rule("inbox", "alert")
    except Exception:  # a policy read must never break ingestion
        logger.debug("alert rule resolution failed", exc_info=True)
        return ""
    return rule.conditions.matches(text, user_name)


def live_store(state: Any) -> "InboxStore | None":
    """The RUNNING inbox service's store, or None when no service is up.

    **Every writer must go through this.** The service holds its items in MEMORY and never
    re-reads the file, so a writer that constructs its own `InboxStore()` writes a row the API
    cannot see (`_get_inbox` serves the service's instance) and that the service's next save
    silently overwrites. Found twice while wiring workflow gates: once raising a row that never
    appeared, once resolving a row that stayed open after its gate was answered.

    Type-checked, not duck-typed: a test's `MagicMock()` state answers every getattr, so an
    attribute check alone would route real writes into a mock and the row would vanish. An
    isinstance is the only thing that distinguishes a live store from an obliging fake.
    """
    svc = getattr(state, "_inbox_svc", None)
    live = getattr(svc, "inbox", None) if svc is not None else None
    return live if isinstance(live, InboxStore) else None


def emit_attention_item(
    state: Any,
    *,
    source: str,
    kind: str,
    title: str,
    body: str = "",
    refs: dict | None = None,
    item_kind: str = "",
    store: "InboxStore | None" = None,
    dedup_key: str = "",
) -> str:
    """Raise a standing attention item AND deliver one notification for it.

    **The only correct way to raise a durable agent request.** A caller that did
    ``store.add(...)`` and ``state.notify(...)`` separately would drift the two apart — the
    common failure being two notifications for one event, or an inbox row with no delivery
    at all. Routing both through here means the notification is a *view* of the item.

    ``source``/``kind`` are the registered notification pair (delivery policy, S1);
    ``item_kind`` is the inbox row's own type and defaults to ``kind`` since for the
    attention kinds they coincide (``needs_input`` is both).

    ``dedup_key`` makes re-emission idempotent: a loop that re-checks every 30s must not
    stack a hundred identical rows. When supplied, an existing PENDING/SEEN item with the
    same key is returned untouched and **no second notification fires** — the user was
    already told.

    Returns the inbox item id ("" only if the store could not be reached, which is logged;
    a failure to persist must not also lose the notification, so delivery still happens).
    """
    resolved_kind = item_kind or kind
    target = store or live_store(state)
    if target is None:
        target = InboxStore()
        try:
            target.load()
        except Exception:  # pragma: no cover - load() already swallows OSError
            logger.warning("attention item: inbox load failed", exc_info=True)

    if dedup_key:
        existing = _find_open_by_dedup(target, dedup_key)
        if existing is not None:
            logger.debug("attention item deduped on %r → %s", dedup_key, existing.id)
            return existing.id

    now = time.time()
    item = InboxItem(
        id=make_item_id(resolved_kind, now=now),
        channel=source,
        channel_name=source,
        thread_ts=None,
        # Title FIRST, then the body. `body or title` dropped the title whenever a body
        # existed — so a workflow gate's row read "Waiting for your approval." and lost the
        # actual question ("Ship the release to production?"), which is the one thing a user
        # needs to decide from the list. Joined rather than either/or: the body is detail
        # ABOUT the title, not a replacement for it.
        message="\n\n".join(p for p in (title, body) if p),
        sender_id=source,
        sender_name=source,
        created_at=now,
        source=source,
        # Non-channel kinds have nowhere to send a reply; the UI keys its send affordance
        # off this, so leaving it True would render a Send button that cannot work.
        can_reply=False,
        classification=Classification.NEEDS_REPLY.value,
        confidence=Confidence.HIGH.value,
        item_kind=resolved_kind,
        refs=dict(refs or {}),
    )
    if dedup_key:
        item.refs["dedup_key"] = dedup_key

    item_id = ""
    try:
        target.add(item)
        target.flush()
        item_id = item.id
    except Exception:
        logger.warning("attention item: inbox write failed", exc_info=True)

    if state is not None:
        try:
            state.notify(
                notification_kinds.kind_for_legacy_pair(source, kind),
                title,
                body,
                meta={"inbox_item": item_id, "item_kind": resolved_kind, **dict(refs or {})},
            )
        except Exception:
            logger.warning("attention item: notify failed", exc_info=True)
    return item_id


def _find_open_by_dedup(store: "InboxStore", dedup_key: str) -> "InboxItem | None":
    """An unresolved item carrying ``dedup_key``, newest first.

    Only PENDING/SEEN count as open: once the user has HANDLED or DISMISSED a request, a
    later re-emission is genuinely new and should surface again rather than be swallowed.
    """
    open_states = {ItemStatus.PENDING.value, ItemStatus.SEEN.value}
    matches = [
        i
        for i in store.items.values()
        if i.refs.get("dedup_key") == dedup_key and i.status in open_states
    ]
    if not matches:
        return None
    return max(matches, key=lambda i: i.created_at)


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
        notification_kinds.INBOX_ALERT,
        f"{item.sender_name} in {item.channel_name}",
        f"Alert ({reason}): {msg[:200]}",
        meta={"session": f"inbox:{item.id}"},
    )
