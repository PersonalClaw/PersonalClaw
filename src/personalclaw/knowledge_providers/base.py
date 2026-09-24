"""Abstract base for Knowledge providers.

WATCHED-SOURCES §1.1 ("one contract, four shapes"): a knowledge provider is
either a plain :class:`KnowledgeProvider` (search/get over an owned corpus) or a
poll-capable :class:`KnowledgeSourceProvider` that a scheduler drives to pull new
items from an external feed. The poll shape is defined here (the contract owner)
ahead of the engine that consumes it (WS-2's ``SourceEngine``) — the roadmap's
contract-owner-before-consumer rule.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
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
#: The page defeated even the render tier and needs the full gateway browse tier (BA-6), which
#: is not allowed. Distinct from ``needs render tier`` for the same reason that one is distinct
#: from ``degraded``: the remediation is its own knob (turn on ``budget.allow_browse`` and point
#: ``budget.cdp_url`` at a gateway), and a source that quietly returns nothing forever is exactly
#: what a specific, actionable status prevents.
HEALTH_NEEDS_BROWSE = "needs browse tier"
#: The closed vocabulary for ``sources.health_status``.
SOURCE_HEALTH = frozenset(
    {HEALTH_OK, HEALTH_DEGRADED, HEALTH_ERROR, HEALTH_NEEDS_RENDER, HEALTH_NEEDS_BROWSE}
)


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

    async def push(self, item: KnowledgeItem) -> KnowledgeItem | None:
        """Send an owner-shared item OUT to this provider's corpus (MULTI-TENANCY-ENTITY TSE2-4).

        The OUTBOUND counterpart of :meth:`ingest`, and deliberately its mirror image: ingest
        takes content the harness received and files it here; push takes an item the harness
        OWNS and offers it to a shared store. Only an item whose
        ``sharing_policy`` is ``shared`` is ever offered — the gate lives in
        :func:`personalclaw.knowledge.sharing.push_shared_item`, not in a provider, so no
        provider can widen it.

        Returns the provider's OWN record on acceptance (its ``id`` is the remote key), or
        ``None`` to DECLINE — the same "declined" default as :meth:`ingest`, so a provider
        that has no outbound side is silent rather than broken, and a caller can tell
        "nobody took it" from "the team store has it" instead of assuming success.

        The harness is a CLIENT of shared stores, never a server
        (``docs/architecture/shared-store-provider-conformance.md``): this method hands an
        item over. It does not permission it, merge it, or promise the remote store will
        keep it.
        """
        return None

    async def delete_item(self, item_id: str) -> bool:
        return False

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}


#: The keywords the engine may hand a poll on top of ``(source_id, cursor)``. A provider
#: OPTS IN to each one by naming it on its own ``poll``, and the engine passes exactly the
#: ones that signature declares (:meth:`SourceEngine._poll_kwargs`) — one negotiation for
#: every extra, so ``poll`` stays a single contract rather than a family of shapes.
#:
#: Named here, on the contract owner, rather than left implicit in the engine: ``policy``
#: was an undeclared extension only the engine's own branch knew about, so an app author
#: reading the ABC could not discover it. This is the declared list.
ENGINE_POLL_KWARGS = ("spec", "policy")


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

    **The per-source spec, and why the engine has to hand it over (AECO-2).** Every
    poll-capable provider in core is constructed with a
    :class:`~personalclaw.knowledge.store.KnowledgeStore` handle, so it reads its own
    source row — and with it the row's validated ``spec`` — for itself. An APP-bundled
    provider cannot: an app reaches core only through ``personalclaw.sdk.*`` and holds no
    store, so all it ever received was a ``source_id`` it could not resolve. The only
    configuration left to it was therefore a per-INSTALL setting, which caps one install at
    one watched source — a Notion app could never watch two workspaces, a git app never two
    repositories. Declaring ``spec`` on ``poll`` is what lifts that ceiling: the engine
    re-reads the row at poll time and hands over a private copy, so what a provider sees is
    the spec persisted NOW (not a snapshot from the top of the tick) and mutating it cannot
    corrupt the engine's row. Resolve it with :func:`resolve_source_spec`.
    """

    #: Provider's requested seconds between polls; the engine clamps to its floor.
    poll_interval_seconds: int = 3600

    @abstractmethod
    async def poll(self, source_id: str, cursor: str = "") -> SourcePollResult:
        """Pull items newer than ``cursor`` for ``source_id`` (never raises to the
        engine — report a soft failure via ``SourcePollResult.error`` instead).

        Two OPTIONAL keyword-only extras are available, both listed in
        :data:`ENGINE_POLL_KWARGS`; name either one on your own ``poll`` and the engine
        passes it. They are not declared here because declaring them would force every
        existing override to restate them (a narrower override is a typing error), and a
        provider that needs neither must stay a two-argument method:

        ``spec``
            ``dict`` — this source's persisted spec, re-read at poll time and handed over as
            a private copy. The row is MUTABLE data an MCP tool or a hand-edit can change
            after the save, so re-validate it here as well as at save time; that is the same
            poll-time re-validation ``dir_source``/``feed_source``/``web_source``/
            ``connector_pack`` do from their own store read. :func:`resolve_source_spec`
            does the merge-and-refuse.
        ``policy``
            the engine-owned egress posture a fetching provider must run its
            ``sdk.net`` calls under, so no provider chooses its own network stance.
        """
        ...


def resolve_source_spec(
    spec: Mapping[str, Any] | None,
    *,
    defaults: Mapping[str, Any] | None = None,
    allowed: Iterable[str] | None = None,
) -> tuple[dict[str, Any], str]:
    """One source's spec resolved over a provider's per-install defaults, fail-CLOSED.

    The merge every multi-source provider needs and none should hand-roll (AECO-2): an app
    has per-install settings (the app's own ``settingsSchema``) AND, now, a per-source
    ``spec``, and the two have to compose the one way that makes a second source possible —
    the row wins where it says something, the install fills in the rest.

    Returns ``(resolved, error)``. A NON-EMPTY ``error`` means refuse: return it from
    ``validate_spec`` at save time *and* from ``poll``, because the spec is a mutable row
    and a guard that only ran at save time is one out-of-band edit from being bypassed.

    ``allowed`` closes the key set. Passing it is what turns an unknown key into a refusal
    instead of a silently-ignored typo — a source configured with ``repos`` when the
    provider reads ``repo`` would otherwise poll the install default forever and look like
    it was working. A blank value (``None`` or ``""``) does NOT override its default: an
    empty field in a create form means "inherit", not "clear".
    """
    if spec is None:
        spec = {}
    if not isinstance(spec, Mapping):
        return {}, "spec must be an object"
    if allowed is not None:
        unknown = sorted(set(spec) - set(allowed))
        if unknown:
            return {}, f"spec: unknown key(s) {unknown}"
    resolved: dict[str, Any] = dict(defaults or {})
    resolved.update({k: v for k, v in spec.items() if v not in (None, "")})
    return resolved, ""
