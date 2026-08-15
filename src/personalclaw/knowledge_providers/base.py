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


#: A sighting of a guid the source has never emitted before → a NEW item.
CHANGE_CREATED = "created"
#: A sighting of a guid that already has an item, whose content changed → the SAME
#: item is updated and re-enqueued for re-indexing (never a second row).
CHANGE_MODIFIED = "modified"
#: The guid disappeared upstream → its item is ARCHIVED with a ``source_deleted_at``
#: stamp. Never a hard delete: the upstream copy is gone, so the library row is the
#: only remaining copy of what the user had (WATCHED-SOURCES §4, SC#5).
CHANGE_DELETED = "deleted"
#: The closed vocabulary. One set, read by the engine's dispatch — an unknown value
#: is a programming error, not a silently-defaulted create (a default branch that
#: swallowed "deleted" would hard-index a vanished file).
SOURCE_CHANGES = frozenset({CHANGE_CREATED, CHANGE_MODIFIED, CHANGE_DELETED})

#: Full enrichment: a source's items run the whole per-type pipeline graph plus the
#: model-backed terminal stages (insights / entities / intents).
ENRICHMENT_FULL = "full"
#: Raw enrichment — the no-AI contract (WATCHED-SOURCES §6.3). A raw source's items are
#: indexed by the DETERMINISTIC stages only (the FTS row, the local embedding, dedup) and
#: reach no model at all. Honored STRUCTURALLY: the pipeline routes a raw item through
#: ``FeedItemGraph``, whose LLM nodes are absent rather than disabled, and the runner does
#: not call the model-backed terminal stages. A promise kept by a flag is one config edit
#: away from being broken; a promise kept by an absent node cannot be re-enabled at all.
ENRICHMENT_RAW = "raw"
#: The closed vocabulary for ``sources.enrichment``. Matched EXPLICITLY wherever it is
#: read, so an unknown value is never treated as "full" — a default branch there would
#: silently send a no-AI source's content to a model (the exact shape of bug the closed
#: :data:`SOURCE_CHANGES` vocabulary exists to prevent on the change axis).
ENRICHMENTS = frozenset({ENRICHMENT_FULL, ENRICHMENT_RAW})

#: A poll that produced its items normally.
HEALTH_OK = "ok"
#: A poll that failed in a recoverable way (a timeout, a malformed page, a soft provider
#: error). The source stays enabled and the cursor is kept, so the next poll retries.
HEALTH_DEGRADED = "degraded"
#: A poll that could not run at all (no enrolled provider, a provider that raised).
HEALTH_ERROR = "error"
#: The page needs the render tier and is not allowed to use it (WATCHED-SOURCES §2.3, SC#2).
#: A DISTINCT status rather than a generic ``degraded`` because the remediation is a specific
#: one the user can act on — turn on ``budget.allow_render`` (or install the render extra) —
#: and a source silently returning nothing forever is the failure this whole status exists to
#: prevent. The engine records whatever status a provider declares here, so a provider that
#: knows WHY it found nothing is not flattened into "degraded".
HEALTH_NEEDS_RENDER = "needs render tier"
#: The closed vocabulary for ``sources.health_status``.
SOURCE_HEALTH = frozenset({HEALTH_OK, HEALTH_DEGRADED, HEALTH_ERROR, HEALTH_NEEDS_RENDER})


@dataclass
class SourceItem:
    """One item pulled from an external feed during a poll (WATCHED-SOURCES §1.1).

    ``guid`` is the feed-stable de-duplication key (RSS guid, HN object id, commit
    sha, …) — the engine keys ``UNIQUE(source_id, guid)`` on it so the same story
    seen twice is one item. ``url``/``published_at`` are optional provenance the
    engine records; ``also_seen_in`` lets a provider declare cross-source
    attribution (SC#3, e.g. the same story via HN and RSS) without the engine
    re-deriving it.

    ``change`` is the sighting's KIND, from :data:`SOURCE_CHANGES` (WS-5). An
    append-only feed only ever emits :data:`CHANGE_CREATED` (the default, so the
    §1.1 contract is unchanged); a MUTABLE corpus — a watched local directory — also
    emits :data:`CHANGE_MODIFIED` for an edited item and :data:`CHANGE_DELETED` for
    one that vanished. The provider observes the change; the ENGINE owns what
    persisting it means, so no provider can decide to hard-delete a user's item.
    """

    guid: str
    title: str
    content: str = ""
    url: str = ""
    published_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    also_seen_in: list[str] = field(default_factory=list)
    change: str = CHANGE_CREATED


@dataclass
class SourcePollResult:
    """The outcome of one :meth:`KnowledgeSourceProvider.poll` (WATCHED-SOURCES §1.1).

    ``items`` are the (possibly-new) items pulled this cycle; the engine dedups
    them by ``(source_id, guid)`` — a provider need not track what it already
    emitted. ``cursor`` is an opaque provider-defined position the engine persists
    and hands back on the next poll (an etag, a last-seen id, a timestamp), so a
    provider can poll incrementally. ``error`` set (with ``items`` empty) reports a
    soft failure the engine can surface without treating the source as dead.

    ``escalations`` are the human-readable markers of any tier a poll had to climb — or was
    refused. §2.3 requires an escalation to be RECORDED, because an escalation nobody can
    see is indistinguishable from a cheap poll, and the render tier is the expensive one.
    They are recorded on the poll record whether the poll succeeded or not.

    ``health_status`` lets a provider that knows WHY a poll produced nothing say so
    (:data:`HEALTH_NEEDS_RENDER`); empty means "the engine decides" — :data:`HEALTH_OK` on
    success, :data:`HEALTH_DEGRADED` when ``error`` is set. Without it, a source needing the
    render tier would be flattened into the same ``degraded`` a timeout produces and the user
    would never learn that one knob fixes it.
    """

    items: list[SourceItem] = field(default_factory=list)
    cursor: str = ""
    error: str = ""
    escalations: list[str] = field(default_factory=list)
    health_status: str = ""


@dataclass
class SourcePreview:
    """A dry run of a source's extraction, for the paste-URL create flow (§2.4).

    Preview answers the only question that matters before saving a source: *would this spec
    produce the items I expect?* So it returns the items it WOULD have written plus which
    detector produced them — the user tunes a named detector, not a black box — and any
    escalation the attempt needed.

    It persists nothing (no item, no cursor, no seen-set row) but it DOES spend the poll's
    request budget, because a preview is a real fetch at somebody else's server and
    pretending otherwise is how a tuning loop becomes a hammer.

    ``guidance`` is the remediation to show when ``items`` is empty: §2.1's
    pick-a-listing-page advice for a page that rendered fine and simply is not a listing, or
    the render-tier advice for a JS shell. ``error`` is a hard failure (an egress denial, an
    invalid spec) as distinct from an empty extraction, which is a tuning problem.
    """

    items: list[SourceItem] = field(default_factory=list)
    detector: str = ""
    escalations: list[str] = field(default_factory=list)
    requests_used: int = 0
    guidance: str = ""
    health_status: str = ""
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
