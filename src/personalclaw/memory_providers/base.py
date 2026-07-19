"""The Memory Provider contract (L2) — the real seam: record CRUD + vector ops.

A provider is the **dumb, swappable persistence layer**: it stores and queries
``MemoryRecord`` rows and (optionally) their vectors, appends the reversible
event log, and declares its capabilities. It owns NO intelligence — supersession,
promotion, recall ranking, the L1 manifest, lessons, facet derivation, lint, and
any prompt-shaped string live in the Memory Service (L3). This mirrors how a
``KnowledgeProvider`` sources/persists items while the Knowledge platform service
runs insights/embedding/retrieval over every item regardless of source
(memory-architecture.md §2/§3.2).

Clean break: the old content-shaped ABC (read_preferences/read_projects/
read_history/get_context/search) is GONE — those were a filesystem-document shape
inherited from an earlier lineage. They are not aliased; the Memory Service
renders preferences/projects/history by querying ``kind=preference|note`` records
and projecting them (mem-fs-mirror), so there is one source of truth.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.memory_record import MemoryCapabilities, MemoryRecord


class MemoryProvider(ABC):
    """Record+vector+event persistence for agent memory. No intelligence."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def init(self) -> None:
        """Initialize storage (create dirs/tables/indices)."""
        ...

    @abstractmethod
    def capabilities(self) -> "MemoryCapabilities":
        """Declare what this provider can do so the service degrades per-capability
        (vector? transactional batch? event log? FTS?) instead of all-or-nothing."""
        ...

    # ── record CRUD ──────────────────────────────────────────────────────────

    @abstractmethod
    def put(self, records: "list[MemoryRecord]") -> None:
        """Atomically upsert a batch of records (O-A5 / batch ops). Providers
        without ``transactional_batch`` apply best-effort one at a time."""
        ...

    @abstractmethod
    def get(self, record_id: str) -> "MemoryRecord | None":
        """Fetch one active record by id, or None."""
        ...

    @abstractmethod
    def delete(self, record_id: str, *, source: str = "user_explicit") -> bool:
        """Tombstone a record. Returns True if one was deleted."""
        ...

    @abstractmethod
    def query(
        self,
        *,
        kinds: "set[str] | None" = None,
        scope: str | None = None,
        scope_ref: str | None = None,
        include_deleted: bool = False,
        limit: int | None = None,
    ) -> "list[MemoryRecord]":
        """Filtered record query (NOT vector — that's ``vector_query``)."""
        ...

    # ── vector ops (degrade gracefully if capabilities.vector is False) ────────

    @abstractmethod
    def vector_query(
        self,
        *,
        text: str = "",
        embedding: "list[float] | None" = None,
        k: int = 8,
        kinds: "set[str] | None" = None,
    ) -> "list[dict]":
        """Nearest-neighbour search returning scored hit dicts. Empty list when
        the provider has no vector capability (the service degrades to FTS)."""
        ...

    @abstractmethod
    def embed(self, text: str) -> "list[float] | None":
        """Embed text via the provider's wired model, or None if unavailable."""
        ...

    # ── durability: the reversible WAL substrate the service drives ────────────

    @abstractmethod
    def append_event(
        self,
        *,
        event_type: str,
        memory_type: str,
        memory_key: str,
        old_value: str | None,
        new_value: str | None,
        source: str,
    ) -> int:
        """Append an audit/WAL event; returns its id."""
        ...

    @abstractmethod
    def read_events(self, *, limit: int = 50, offset: int = 0) -> "list[dict]":
        """Read recent WAL events, newest first."""
        ...

    # ── lifecycle hooks (C27) — defined now, implemented lazily ────────────────
    # The service calls these at the corresponding moments; the default no-ops
    # let a provider opt in without forcing every provider to implement them.

    def on_turn_start(self, session_key: str) -> None:  # noqa: B027 - intentional no-op hook
        """Called at the start of a turn (e.g. to warm caches)."""

    def on_pre_compress(self, session_key: str) -> None:  # noqa: B027
        """Called before context compaction (e.g. to flush working memory)."""

    def on_memory_write(self, record: "MemoryRecord") -> None:  # noqa: B027
        """Called after the service commits a write (e.g. to mirror to FS)."""

    def on_delegation(self, agent: str) -> None:  # noqa: B027
        """Called when work is delegated to a subagent (scope handoff)."""

    def close(self) -> None:  # noqa: B027
        """Release resources (DB handles, etc.)."""
