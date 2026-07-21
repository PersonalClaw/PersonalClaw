"""The typed memory record — one shape the whole memory subsystem speaks.

Today memory is several ad-hoc shapes: semantic key/value rows, episodic rows,
lessons (semantic rows with a ``lesson.*`` key), preference facets, and the
markdown prefs/projects/history files. This module introduces **one**
``MemoryRecord`` with a ``kind`` discriminator so the provider stores rows and
the service reasons over them uniformly (memory-architecture.md §3.1).

M0 scope: this is a typed view + (de)serialization helpers ONLY. The SQLite
schema does not change here — ``MemoryRecord`` maps onto the existing
``semantic_memory`` and ``episodic_memories`` rows. The new durability/reach
axes (tier/scope/category/…) are carried on the dataclass with safe defaults so
M5+ can populate them once the backing columns exist; until then they round-trip
through defaults and never alter behavior.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:  # numpy is optional (same as vector_memory)
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Discriminators ──────────────────────────────────────────────────────────


class MemoryKind(str, Enum):
    """What KIND of thing a record is (memory-architecture.md §3.1/§3.7).

    The first four are the user/world-facing classes that exist today; the last
    three (``procedural``/``commitment``/``self_persona``) are the M5+ classes
    that turn memory from "facts about the user" into "memory that acts".
    """

    SEMANTIC = "semantic"  # distilled fact (allow-listed key)
    EPISODIC = "episodic"  # discrete conversation fragment
    LESSON = "lesson"  # corrective rule (a semantic row, key=lesson.*)
    PREFERENCE = "preference"  # user/agent preference
    NOTE = "note"  # free note (subsumes prefs.md/projects.md text)
    # NEW (M5+):
    PROCEDURAL = "procedural"  # how-to-work prior (tool/source outcomes)
    COMMITMENT = "commitment"  # inferred future check-in obligation
    SELF_PERSONA = "self_persona"  # the agent's positive self-model


class MemoryTier(str, Enum):
    """DURABILITY axis — deepens via SEALING (§3.5). Independent of SCOPE."""

    WORKING = "working"  # rolling session summary, always-injected
    EPISODIC = "episodic"  # discrete facts
    SEGMENT = "segment"  # topic clusters
    SEMANTIC = "semantic"  # distilled truths


class MemoryScope(str, Enum):
    """REACH axis — widens via heat-gated PROMOTION (§3.5). Independent of TIER."""

    SESSION = "session"
    WORKSPACE = "workspace"
    AGENT = "agent"
    GLOBAL = "global"


# Default (tier, scope) per kind for records minted before the M5+ axes are
# populated — chosen so M0–M4 behavior is byte-identical to today (everything
# global, durable). M5+ overrides these at the write path.
_DEFAULT_TIER: dict[str, MemoryTier] = {
    MemoryKind.SEMANTIC: MemoryTier.SEMANTIC,
    MemoryKind.LESSON: MemoryTier.SEMANTIC,
    MemoryKind.PREFERENCE: MemoryTier.SEMANTIC,
    MemoryKind.NOTE: MemoryTier.SEMANTIC,
    MemoryKind.EPISODIC: MemoryTier.EPISODIC,
    MemoryKind.PROCEDURAL: MemoryTier.SEMANTIC,
    MemoryKind.COMMITMENT: MemoryTier.EPISODIC,
    MemoryKind.SELF_PERSONA: MemoryTier.SEMANTIC,
}


# ── Embedding (de)serialization — matches vector_memory's on-disk format ──────
# Embeddings are stored as L2-normalized float32 bytes (so inner-product == cosine
# on the FAISS IndexFlatIP). These helpers are the single source of truth for that
# encoding so MemoryRecord and the store agree byte-for-byte.


def embedding_to_blob(embedding: list[float] | None) -> bytes | None:
    """Normalize an embedding and pack it as float32 bytes (or None)."""
    if embedding is None:
        return None
    if _HAS_NUMPY:
        vec = np.array(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tobytes()
    norm_f = math.sqrt(sum(x * x for x in embedding))
    normed = [x / norm_f for x in embedding] if norm_f > 0 else embedding
    return struct.pack(f"{len(normed)}f", *normed)


def blob_to_embedding(blob: bytes | None) -> list[float] | None:
    """Decode float32 bytes back to a list[float] (or None)."""
    if not blob:
        return None
    if _HAS_NUMPY:
        return np.frombuffer(blob, dtype=np.float32).tolist()
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ── The record ───────────────────────────────────────────────────────────────


@dataclass
class MemoryRecord:
    """One row the provider stores and the service reasons over.

    Subsumes the legacy semantic_memory + episodic_memories shapes. Fields with
    NEW-axis semantics (tier/scope/category/visit_count/…) carry safe defaults
    until the M5+ migration adds their backing columns; they round-trip through
    ``extra`` so a forward-written record never loses data on an older store.
    """

    id: str
    kind: MemoryKind
    text: str = ""
    # value: the decoded semantic value (may be non-string for legacy rows);
    # ``text`` is its string projection for search/embedding. For episodic,
    # value is None and text is the fragment.
    value: Any = None
    embedding: list[float] | None = None
    importance: float = 0.5
    confidence: float = 0.5
    source: str = ""
    # heat inputs
    recall_count: int = 0
    visit_count: int = 0  # NEW (§3.1)
    last_accessed_at: str | None = None
    # axes (NEW — §3.5; defaults preserve today's "global/durable" behavior)
    tier: MemoryTier | None = None
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_ref: str | None = None
    category: str | None = None  # fact|pref|decision|event|debug → category-TTL
    # provenance + lifecycle
    superseded_by: str | None = None
    invalidated_at: str | None = None
    conversation_id: str = ""
    tags: list[str] = field(default_factory=list)
    is_deleted: bool = False
    created_at: str = ""
    updated_at: str = ""
    # action-safety / commitment envelopes (NEW — §3.7; M5+)
    safe_to_act: dict | None = None
    due_window: str | None = None
    channel: str | None = None
    dismissed_at: str | None = None
    source_ref: dict | None = None
    # anything not yet a first-class column (forward-compat carrier)
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = MemoryKind(self.kind)
        if isinstance(self.tier, str):
            self.tier = MemoryTier(self.tier)
        if isinstance(self.scope, str):
            self.scope = MemoryScope(self.scope)
        if self.tier is None:
            self.tier = _DEFAULT_TIER.get(self.kind, MemoryTier.SEMANTIC)
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        # text is the search/embed projection of value for non-episodic rows
        if not self.text and self.value is not None:
            self.text = self.value if isinstance(self.value, str) else json.dumps(self.value)

    # ── row mapping: semantic_memory ─────────────────────────────────────────

    @classmethod
    def from_semantic_row(cls, row: Any) -> "MemoryRecord":
        """Build a record from a ``semantic_memory`` row (sqlite3.Row or dict)."""
        g = row.__getitem__ if hasattr(row, "keys") else row.get  # Row vs dict
        key = g("key")
        value_json = g("value_json")
        try:
            value = json.loads(value_json) if value_json is not None else None
        except (json.JSONDecodeError, TypeError):
            value = value_json
        kind = _kind_from_key(str(key))
        text = value if isinstance(value, str) else json.dumps(value)
        return cls(
            id=key,
            kind=kind,
            text=text,
            value=value,
            embedding=blob_to_embedding(_row_get(row, "embedding")),
            confidence=_row_get(row, "confidence", 0.5) or 0.5,
            source=_row_get(row, "source", "") or "",
            recall_count=_row_get(row, "recall_count", 0) or 0,
            visit_count=_row_get(row, "visit_count", 0) or 0,
            tier=_row_get(row, "tier") or None,
            scope=_row_get(row, "scope") or MemoryScope.GLOBAL,
            scope_ref=_row_get(row, "scope_ref"),
            category=_row_get(row, "category"),
            superseded_by=_row_get(row, "superseded_by"),
            invalidated_at=_row_get(row, "invalidated_at"),
            is_deleted=bool(_row_get(row, "is_deleted", 0)),
            created_at=_row_get(row, "created_at", "") or "",
            updated_at=_row_get(row, "updated_at", "") or "",
        )

    @classmethod
    def from_episodic_row(cls, row: Any) -> "MemoryRecord":
        """Build a record from an ``episodic_memories`` row."""
        tags_raw = _row_get(row, "tags", "[]")
        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
        except (json.JSONDecodeError, TypeError):
            tags = []
        return cls(
            id=_row_get(row, "id"),
            kind=MemoryKind.EPISODIC,
            text=_row_get(row, "text", "") or "",
            embedding=blob_to_embedding(_row_get(row, "embedding")),
            importance=_row_get(row, "importance", 0.5) or 0.5,
            conversation_id=_row_get(row, "conversation_id", "") or "",
            tags=tags,
            visit_count=_row_get(row, "visit_count", 0) or 0,
            tier=_row_get(row, "tier") or None,
            scope=_row_get(row, "scope") or MemoryScope.GLOBAL,
            scope_ref=_row_get(row, "scope_ref"),
            category=_row_get(row, "category"),
            last_accessed_at=_row_get(row, "last_accessed_at"),
            is_deleted=bool(_row_get(row, "is_deleted", 0)),
            created_at=_row_get(row, "created_at", "") or "",
            updated_at=_row_get(row, "last_accessed_at") or _row_get(row, "created_at", "") or "",
        )

    def embedding_blob(self) -> bytes | None:
        """This record's embedding as the store's on-disk float32 blob."""
        return embedding_to_blob(self.embedding)

    def heat(self, *, now: datetime | None = None) -> float:
        """Operational heat — how 'hot' this record is for promotion + boost
        (memory-architecture.md §3.6: heat = α·visit + β·interaction + γ·recency).

        A bounded [0, ~1.5] signal combining how often the record has been
        recalled/visited and how recently it was touched. Drives the two-stage
        retrieval boost (M5b) and the heat-gated global promotion (M5c). Pure
        function of the record's own fields — no DB access."""
        import math as _math

        visits = float(self.recall_count + self.visit_count)
        # diminishing-returns visit term (log) so a runaway counter can't dominate
        visit_term = _math.log1p(visits) / _math.log(10)  # ~1.0 at 9 visits
        # recency term: 1.0 today, decaying ~30-day half-life
        recency_term = 0.0
        stamp = self.last_accessed_at or self.updated_at or self.created_at
        if stamp:
            try:
                ref = now or datetime.now(tz=timezone.utc)
                last = datetime.fromisoformat(stamp)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                days = max(0.0, (ref - last).total_seconds() / 86400.0)
                recency_term = _math.exp(-days / 30.0)
            except (ValueError, TypeError):
                recency_term = 0.0
        return 0.7 * visit_term + 0.5 * recency_term

    def to_public_dict(self) -> dict:
        """A JSON-safe view for dashboards/tools (no embedding bytes)."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "text": self.text,
            "value": self.value,
            "importance": self.importance,
            "confidence": self.confidence,
            "source": self.source,
            "recall_count": self.recall_count,
            "tier": self.tier.value if self.tier else None,
            "scope": self.scope.value,
            "category": self.category,
            "superseded_by": self.superseded_by,
            "conversation_id": self.conversation_id,
            "tags": self.tags,
            "is_deleted": self.is_deleted,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _kind_from_key(key: str) -> MemoryKind:
    """Infer a semantic-row record's kind from its key prefix.

    The new memory classes (lesson / procedural / self_persona / commitment) ride
    the semantic table keyed by a reserved prefix — the same kind-by-key-prefix
    convention lessons already use — so no extra ``kind`` column is needed. A
    plain ``pref.*`` / ``project.*`` key is a SEMANTIC fact."""
    if key.startswith("lesson."):
        return MemoryKind.LESSON
    if key.startswith("user.procedural."):
        return MemoryKind.PROCEDURAL
    if key.startswith("user.persona."):
        return MemoryKind.SELF_PERSONA
    if key.startswith("user.commitment."):
        return MemoryKind.COMMITMENT
    return MemoryKind.SEMANTIC


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Read a column from a sqlite3.Row (no .get) or a dict, tolerating absence."""
    if row is None:
        return default
    if hasattr(row, "keys"):  # sqlite3.Row
        try:
            return row[key] if key in row.keys() else default
        except (IndexError, KeyError):
            return default
    if isinstance(row, dict):
        return row.get(key, default)
    return default


# ── Capability declaration (the L2 contract flags — used from M2) ─────────────


@dataclass(frozen=True)
class MemoryCapabilities:
    """What a memory provider can do, so the service degrades per-capability
    instead of all-or-nothing (memory-architecture.md §3.2/§3.4).

    Mirrors the Tool result-contract + Knowledge "degrade to FTS" discipline.
    """

    vector: bool = False  # supports embedding upsert + vector_query
    transactional_batch: bool = False  # atomic multi-record put
    event_log: bool = False  # append/read the reversible WAL
    full_text_search: bool = True  # keyword/FTS query

    def to_dict(self) -> dict:
        return {
            "vector": self.vector,
            "transactional_batch": self.transactional_batch,
            "event_log": self.event_log,
            "full_text_search": self.full_text_search,
        }
