"""How a memory recall actually ranked — the ONE owner of that disclosure.

A recall's quality depends on capabilities that can be absent: with no embedding
model bound there is no vector arm, and with the entity graph off no links are
followed. The results still come back and still look ranked, so a surface that
renders them without saying which arms ran is a surface the user cannot read.

Two surfaces already stated a piece of this in their own words — the Settings
entity-graph section ("memory recall falls back to search alone") and the inbound
``pc_status`` tool ("vector search" / "keyword search"). This module is the single
definition both now read, so a third surface cannot mint a third phrasing.

DERIVED, never hand-listed: the axes below are keyed by
:class:`~personalclaw.memory_record.MemoryCapabilities` FIELD NAMES, and every
field of that dataclass must be classified here (recall-relevant, or explicitly
not). ``tests/test_recall_ranking_disclosure.py`` censuses
``dataclasses.fields(MemoryCapabilities)`` against this module, so adding a
capability forces the classification instead of silently leaving recall unable to
describe itself.

The sentence is composed SERVER-side (the ``authority``-field precedent in
AUTONOMY-GUARDRAILS): a client renders it, never assembles it. Only the closed
``mode`` vocabulary and the boolean axes cross the wire alongside it, so an agent
or a chip can branch without parsing prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from personalclaw.memory_record import MemoryCapabilities

#: The closed ``mode`` vocabulary. Ordered strongest → weakest, which is also the
#: resolution order in :func:`recall_ranking`.
MODES: tuple[str, ...] = ("semantic", "keyword", "unranked")

#: Capability fields that do NOT change how a recall ranks, with the reason. Listed
#: so the census can tell "classified as irrelevant" from "forgotten".
NON_RANKING_CAPABILITIES: dict[str, str] = {
    "transactional_batch": "write path — an atomic multi-record put, never a read",
    "event_log": "the reversible WAL — an audit/undo path, never a read",
}


@dataclass(frozen=True)
class _Axis:
    """One capability that changes how a recall ranks, in both states."""

    field: str
    #: Terse noun phrase for the status-line label when the axis is ON.
    label: str
    #: Clause naming the loss when the axis is OFF (reads after "so"/"—").
    absent: str


#: Recall-relevant capability axes, keyed by ``MemoryCapabilities`` field name.
AXES: tuple[_Axis, ...] = (
    _Axis(field="vector", label="semantic ranking", absent="no embedding model is bound"),
    _Axis(field="full_text_search", label="keyword search", absent="keyword search is unavailable"),
    _Axis(field="entity_graph", label="entity graph", absent="the entity graph is off"),
)

#: The :class:`RecallRanking` fields that are not axes: store state, not capabilities. An
#: embedding rebind leaves vectors of the previous model in the store until the re-index
#: re-embeds them, a memory written while no model was bound has none, and a recall reads both
#: by keyword, which the capabilities cannot show.
STATE_FIELDS: tuple[str, ...] = ("stale", "unembedded", "comparable")

#: The authored degradation clause. It comes from the Settings entity-graph section,
#: which said it first; reusing it is why the two surfaces read as one product.
FALLS_BACK_CLAUSE = "recall falls back to search alone"


@dataclass(frozen=True)
class RecallRanking:
    """What actually ranked one recall: the provider's capabilities, and the two counts of store
    state an embedding rebind leaves behind."""

    vector: bool
    full_text_search: bool
    entity_graph: bool
    #: Memories whose vectors another embedding model wrote (a rebind, until the re-index
    #: re-embeds them). A semantic search reads those by keyword beside its vector results.
    stale: int = 0
    #: Memories no model embedded: written while none was bound, or when it failed. Read by
    #: keyword the same way, until the re-index embeds them.
    unembedded: int = 0
    #: Memories holding a vector of the model bound now — what the semantic arm compares. With
    #: none of them and some waiting, that arm has nothing to compare and the recall is by keyword.
    comparable: int = 0

    @property
    def waiting(self) -> int:
        """The memories read by keyword because the model bound now has not embedded them."""
        return self.stale + self.unembedded

    @property
    def _vectors_stale(self) -> bool:
        """The semantic arm is wired and no stored memory holds a vector it can compare."""
        return self.vector and self.waiting > 0 and self.comparable == 0

    @property
    def mode(self) -> str:
        """The strongest ranking arm that ran (a member of :data:`MODES`)."""
        if self.vector and not self._vectors_stale:
            return "semantic"
        if self.full_text_search:
            return "keyword"
        return "unranked"

    @property
    def degraded(self) -> bool:
        """True when any recall-relevant axis is missing, or part of the store is read by keyword
        because the model bound now has not embedded it (:attr:`waiting`)."""
        return not all(getattr(self, a.field) for a in AXES) or (self.vector and self.waiting > 0)

    @property
    def label(self) -> str:
        """Terse form for a status line — the ON axes, or "nothing" when none ran."""
        on = [a.label for a in AXES if getattr(self, a.field)]
        return " + ".join(on) if on else "no ranking available"

    @property
    def summary(self) -> str:
        """One sentence a surface renders verbatim, and a second when memories are waiting on the
        re-index (:attr:`waiting`, in :func:`keyword_read_note`'s words).

        Built from the axis table rather than a 2×N matrix of hand-written strings,
        so a new axis extends every sentence instead of needing new ones.
        """
        missing = [a.absent for a in AXES if not getattr(self, a.field)]
        if self._vectors_stale:
            missing.insert(0, _nothing_comparable(self.stale, self.unembedded))
        ranked = _ranked(self.mode, missing)
        if not (self.vector and self.waiting > 0):
            return ranked
        if self._vectors_stale:
            _what, verb = _waiting(self.stale, self.unembedded)
            n = f"{self.waiting} {'memory' if self.waiting == 1 else 'memories'}"
            return f"{ranked} The re-index in Settings → Models {verb} the {n}."
        return f"{ranked} {keyword_read_note(self.stale, self.unembedded)}"

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {a.field: getattr(self, a.field) for a in AXES}
        d.update(
            stale=self.stale,
            unembedded=self.unembedded,
            mode=self.mode,
            degraded=self.degraded,
            label=self.label,
            summary=self.summary,
        )
        return d


def _ranked(mode: str, missing: list[str]) -> str:
    """The ranking sentence for ``mode`` with the ``missing`` clauses named."""
    if not missing:
        return "Ranked by semantic similarity over embeddings, following entity-graph links."
    loss = _join(missing)
    if mode == "semantic":
        # The vector arm ran; something advisory did not.
        return f"Ranked by semantic similarity over embeddings, but {loss}."
    if mode == "keyword":
        return f"Keyword-ranked only: {loss}, so {FALLS_BACK_CLAUSE}."
    return f"Not ranked: {loss}, so results are whatever the store returned unscored."


def _waiting(stale: int, unembedded: int) -> tuple[str, str]:
    """``(what they are, what the re-index does to them)`` for the memories a semantic search
    reads by keyword: another model's vectors are re-embedded, a memory with none is embedded."""
    if not unembedded:
        return "embedded by another embedding model", "re-embeds"
    if not stale:
        return "not embedded yet", "embeds"
    return (
        f"not embedded by the model bound now ({stale} by another embedding model, "
        f"{unembedded} not at all)",
        "embeds",
    )


def _nothing_comparable(stale: int, unembedded: int) -> str:
    """Why the semantic arm had nothing to compare, as a clause for :func:`_ranked`."""
    if not unembedded:
        return "every stored vector is another embedding model's"
    if not stale:
        return "no memory is embedded yet"
    return "no memory is embedded by the model bound now"


def keyword_read_note(stale: int, unembedded: int) -> str:
    """The sentence for the memories a semantic search reads by keyword until the re-index embeds
    them, ``""`` when there are none.

    ONE sentence for one count: the recall disclosure (:attr:`RecallRanking.summary`) and the
    Memory page's Embedded stat (``GET /api/memory/stats``'s ``read_by_keyword_note``) both say
    it, and the Doctor's memory row counts the same memories, all from
    ``vector_memory.embedding_coverage``. It used to count only another model's vectors, so a
    memory written while no model was bound was in no count anywhere but ``/api/memory/stats``.
    """
    n = max(0, stale) + max(0, unembedded)
    if not n:
        return ""
    what, verb = _waiting(stale, unembedded)
    one = n == 1
    return (
        f"{n} {'memory' if one else 'memories'} {what} {'is' if one else 'are'} read by keyword "
        f"until the re-index in Settings → Models {verb} {'it' if one else 'them'}."
    )


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def recall_ranking(
    caps: "MemoryCapabilities", *, stale: int = 0, unembedded: int = 0, comparable: int = 0
) -> RecallRanking:
    """Derive the recall disclosure from a provider's declared capabilities.

    ``caps`` is whatever ``MemoryService.capabilities()`` returned — the live store
    state (``embed_fn`` presence, the graph toggle), not a config reading, so the
    disclosure describes the recall that actually ran. ``stale``, ``unembedded`` and
    ``comparable`` are the store's ``embedded_stale``, ``unembedded`` and ``embedded_count``
    (:class:`RecallRanking`).
    """
    return RecallRanking(
        vector=bool(getattr(caps, "vector", False)),
        full_text_search=bool(getattr(caps, "full_text_search", False)),
        entity_graph=bool(getattr(caps, "entity_graph", False)),
        stale=max(0, stale),
        unembedded=max(0, unembedded),
        comparable=max(0, comparable),
    )


def ranking_payload(
    caps: "MemoryCapabilities", *, stale: int = 0, unembedded: int = 0, comparable: int = 0
) -> dict[str, object]:
    """``recall_ranking(caps, …).to_dict()`` — the shape every recall-ish API returns."""
    return recall_ranking(caps, stale=stale, unembedded=unembedded, comparable=comparable).to_dict()
