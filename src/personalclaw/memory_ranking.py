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
#: re-embeds them, and a recall reads those by keyword, which the capabilities cannot show.
STATE_FIELDS: tuple[str, ...] = ("stale", "comparable")

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
    #: Memories holding a vector of the model bound now — what the semantic arm compares. With
    #: none of them and some stale, that arm has nothing to compare and the recall is by keyword.
    comparable: int = 0

    @property
    def _vectors_stale(self) -> bool:
        """The semantic arm is wired and every stored vector is another model's."""
        return self.vector and self.stale > 0 and self.comparable == 0

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
        because another embedding model wrote its vectors (:attr:`stale`)."""
        return not all(getattr(self, a.field) for a in AXES) or (self.vector and self.stale > 0)

    @property
    def label(self) -> str:
        """Terse form for a status line — the ON axes, or "nothing" when none ran."""
        on = [a.label for a in AXES if getattr(self, a.field)]
        return " + ".join(on) if on else "no ranking available"

    @property
    def summary(self) -> str:
        """One sentence a surface renders verbatim, and a second when :attr:`stale` memories are
        waiting on the re-index.

        Built from the axis table rather than a 2×N matrix of hand-written strings,
        so a new axis extends every sentence instead of needing new ones.
        """
        missing = [a.absent for a in AXES if not getattr(self, a.field)]
        if self._vectors_stale:
            missing.insert(0, "every stored vector is another embedding model's")
        ranked = _ranked(self.mode, missing)
        if not (self.vector and self.stale > 0):
            return ranked
        n = f"{self.stale} {'memory' if self.stale == 1 else 'memories'}"
        if self._vectors_stale:
            return f"{ranked} The re-index in Settings → Models re-embeds the {n}."
        one = self.stale == 1
        return (
            f"{ranked} {n} embedded by another embedding model {'is' if one else 'are'} read by "
            f"keyword until the re-index in Settings → Models re-embeds {'it' if one else 'them'}."
        )

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {a.field: getattr(self, a.field) for a in AXES}
        d.update(
            stale=self.stale,
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


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def recall_ranking(
    caps: "MemoryCapabilities", *, stale: int = 0, comparable: int = 0
) -> RecallRanking:
    """Derive the recall disclosure from a provider's declared capabilities.

    ``caps`` is whatever ``MemoryService.capabilities()`` returned — the live store
    state (``embed_fn`` presence, the graph toggle), not a config reading, so the
    disclosure describes the recall that actually ran. ``stale`` and ``comparable`` are
    the store's ``embedded_stale`` and ``embedded_count`` (:class:`RecallRanking`).
    """
    return RecallRanking(
        vector=bool(getattr(caps, "vector", False)),
        full_text_search=bool(getattr(caps, "full_text_search", False)),
        entity_graph=bool(getattr(caps, "entity_graph", False)),
        stale=max(0, stale),
        comparable=max(0, comparable),
    )


def ranking_payload(
    caps: "MemoryCapabilities", *, stale: int = 0, comparable: int = 0
) -> dict[str, object]:
    """``recall_ranking(caps, …).to_dict()`` — the shape every recall-ish API returns."""
    return recall_ranking(caps, stale=stale, comparable=comparable).to_dict()
