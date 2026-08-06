"""Abstract base for Knowledge providers.

WATCHED-SOURCES §1.1 ("one contract, four shapes"): a knowledge provider is
either a plain :class:`KnowledgeProvider` (search/get over an owned corpus) or a
poll-capable :class:`KnowledgeSourceProvider` that a scheduler drives to pull new
items from an external feed. The poll shape is defined here (the contract owner)
ahead of the engine that consumes it (WS-2's ``SourceEngine``) — the roadmap's
contract-owner-before-consumer rule.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeSource:
    id: str
    name: str
    source_type: str = ""
    item_count: int = 0
    provider: str = ""


@dataclass
class KnowledgeItem:
    id: str
    title: str
    content: str = ""
    source_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceItem:
    """One item pulled from an external feed during a poll (WATCHED-SOURCES §1.1).

    ``guid`` is the feed-stable de-duplication key (RSS guid, HN object id, commit
    sha, …) — the engine keys ``UNIQUE(source_id, guid)`` on it so the same story
    seen twice is one item. ``url``/``published_at`` are optional provenance the
    engine records; ``also_seen_in`` lets a provider declare cross-source
    attribution (SC#3, e.g. the same story via HN and RSS) without the engine
    re-deriving it.
    """

    guid: str
    title: str
    content: str = ""
    url: str = ""
    published_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    also_seen_in: list[str] = field(default_factory=list)


@dataclass
class SourcePollResult:
    """The outcome of one :meth:`KnowledgeSourceProvider.poll` (WATCHED-SOURCES §1.1).

    ``items`` are the (possibly-new) items pulled this cycle; the engine dedups
    them by ``(source_id, guid)`` — a provider need not track what it already
    emitted. ``cursor`` is an opaque provider-defined position the engine persists
    and hands back on the next poll (an etag, a last-seen id, a timestamp), so a
    provider can poll incrementally. ``error`` set (with ``items`` empty) reports a
    soft failure the engine can surface without treating the source as dead.
    """

    items: list[SourceItem] = field(default_factory=list)
    cursor: str = ""
    error: str = ""


class KnowledgeProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def list_sources(self) -> list[KnowledgeSource]: ...

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]: ...

    @abstractmethod
    async def get_item(self, item_id: str) -> KnowledgeItem | None: ...

    async def ingest(
        self, source_id: str, content: str, title: str = "", metadata: dict[str, Any] | None = None
    ) -> KnowledgeItem | None:
        return None

    async def delete_item(self, item_id: str) -> bool:
        return False

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}


class KnowledgeSourceProvider(KnowledgeProvider):
    """A knowledge provider that a scheduler can POLL for new items (§1.1).

    Adds the pull contract on top of the search/get corpus contract: the engine
    (WS-2) arms a single loop over every poll-capable provider, calls
    :meth:`poll` with the last persisted ``cursor``, dedups the returned items by
    ``(source_id, guid)``, and persists the new cursor. ``poll_interval_seconds``
    lets a provider advertise how often it wants to be polled (the engine clamps
    it to its own floor). A provider that only serves an owned corpus stays a
    plain :class:`KnowledgeProvider`; implementing this is what enrolls it in the
    polling loop.
    """

    #: Provider's requested seconds between polls; the engine clamps to its floor.
    poll_interval_seconds: int = 3600

    @abstractmethod
    async def poll(self, source_id: str, cursor: str = "") -> SourcePollResult:
        """Pull items newer than ``cursor`` for ``source_id`` (never raises to the
        engine — report a soft failure via ``SourcePollResult.error`` instead)."""
        ...
