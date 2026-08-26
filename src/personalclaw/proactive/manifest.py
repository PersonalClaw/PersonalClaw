"""Stage 1 — Collect: the digest's ordinal manifest (PROACTIVE-ASSISTANT §1.1).

The manifest is the pipeline's *anti-hallucination contract*. Every later stage — the
classifier gate, the strict-JSON proposal call, the ranking, the digest body — addresses an
item by the ordinal assigned HERE (`"1"`, `"2"`, …) and by nothing else. A proposal naming
an id the manifest never minted is refused rather than repaired, because "repair" means
guessing which of the user's messages a model meant, and a wrong guess archives the wrong
thing.

Two properties make that contract worth having, and both are properties of this module:

**Ordinals are a function of the collected set, not of arrival order.** `build_manifest`
sorts before numbering (source lane, then timestamp, then source id), so two collects over
the same window mint the same ordinals. Without that, a re-collect after a gateway restart
would renumber the window and a reply that said `3 yes` would act on a different item —
which is success criterion 9's wrong-target execution, reached without any adversary.

**One real item is one ordinal.** Deduplication is by fingerprint, and the fingerprint is
derived from provenance (source + source id) rather than from rendered text, so an item
whose title was re-rendered is still the same item, while two genuinely different messages
that happen to share a subject line stay two.

Nothing here reads the clock, the network or a model: a manifest is a pure function of the
rows the collectors handed over, which is what lets the ordinal contract be asserted
directly instead of inferred from a digest body.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

#: The three collect lanes §1.1 names. Ordered — this tuple IS the manifest's lane order,
#: so "inbox first" is one list, not a comparison scattered across the ranking code.
SOURCE_INBOX = "inbox"
SOURCE_CHANNEL = "channel"
SOURCE_RUN = "run"
COLLECT_SOURCES: tuple[str, ...] = (SOURCE_INBOX, SOURCE_CHANNEL, SOURCE_RUN)

_LANE_ORDER = {name: i for i, name in enumerate(COLLECT_SOURCES)}

#: The substrate's materiality vocabulary (AUTOMATION-SUBSTRATE §1.3 / AUTO-R2), consumed
#: here rather than re-derived: `action` touched the world, `error` needs a human, `response`
#: produced words, `none` is noise. Ranking (§1.5) is this order and nothing else, so a new
#: weight is one entry here rather than a new comparator.
MATERIALITY_ACTION = "action"
MATERIALITY_ERROR = "error"
MATERIALITY_RESPONSE = "response"
MATERIALITY_NONE = "none"
MATERIALITY_ORDER: dict[str, int] = {
    MATERIALITY_ACTION: 0,
    MATERIALITY_ERROR: 1,
    MATERIALITY_RESPONSE: 2,
    MATERIALITY_NONE: 3,
}

#: An unknown materiality sorts with `response` rather than last. Last would let a
#: mis-spelled weight hide a world-touching row at the bottom of the digest, which is the
#: one placement the ranking exists to prevent.
_UNKNOWN_MATERIALITY_RANK = MATERIALITY_ORDER[MATERIALITY_RESPONSE]


def materiality_rank(value: str) -> int:
    """Sort key for a materiality weight; unknown values sort with `response`."""
    return MATERIALITY_ORDER.get((value or "").strip().lower(), _UNKNOWN_MATERIALITY_RANK)


@dataclass(frozen=True)
class CollectedItem:
    """One thing that accumulated in the window, with the provenance a proposal needs.

    `ordinal` is empty until `build_manifest` assigns it. A collector must not invent one:
    the numbering is a property of the SET, and a collector only sees its own lane.
    """

    source: str
    source_id: str
    title: str
    detail: str = ""
    sender: str = ""
    materiality: str = MATERIALITY_RESPONSE
    permalink: str = ""
    ts: str = ""
    ordinal: str = ""

    @property
    def fingerprint(self) -> str:
        """Stable identity: source lane + source id, hashed.

        Deliberately NOT over the title. A re-collect that renders the same inbox row with a
        freshly resolved sender name must yield the same fingerprint, or the gate's per-item
        decisions never hit and every window pays full price.
        """
        raw = f"{self.source}\x1f{self.source_id}"
        return hashlib.md5(raw.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:12]


@dataclass(frozen=True)
class Manifest:
    """The numbered window. `items` is ordinal-ordered; `window_start` is provenance."""

    items: tuple[CollectedItem, ...] = ()
    window_start: str = ""
    #: Fingerprints dropped as duplicates of an item already in `items`. Kept rather than
    #: discarded so a collector that double-reports a lane is visible in the ledger row
    #: instead of silently halving its own count.
    duplicates: tuple[str, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def ordinals(self) -> frozenset[str]:
        """Exactly the ids a downstream stage may name. The proposal contract reads this."""
        return frozenset(item.ordinal for item in self.items)

    def by_ordinal(self, ordinal: str) -> CollectedItem | None:
        key = str(ordinal or "").strip()
        for item in self.items:
            if item.ordinal == key:
                return item
        return None

    def lane(self, source: str) -> tuple[CollectedItem, ...]:
        return tuple(item for item in self.items if item.source == source)

    def counts(self) -> dict[str, int]:
        """Per-lane counts, every declared lane present (a zero lane is a fact, not a gap)."""
        out = {name: 0 for name in COLLECT_SOURCES}
        for item in self.items:
            out[item.source] = out.get(item.source, 0) + 1
        return out

    def projection(self) -> list[dict[str, str]]:
        """The ordinal→provenance rows a LATER surface needs to act on this window.

        The ordinal contract is only worth what it can be *redeemed* for. An ordinal that
        outlives the process which minted it — the digest card a user opens tomorrow, a channel
        reply that arrives after a restart — has to resolve to a store id without re-collecting,
        because a re-collect renumbers the window and `3 yes` then acts on a different item
        (criterion 9's wrong-target execution, reached with no adversary). So the map travels
        with the run's own output rather than being rebuilt from a fresh read.

        Provenance only: source lane, store id, title, permalink, materiality. Never `detail`,
        which is the fenced, model-facing body — a read model for a card must not become a
        second copy of untrusted item text.
        """
        return [
            {
                "ordinal": item.ordinal,
                "source": item.source,
                "source_id": item.source_id,
                "title": item.title,
                "permalink": item.permalink,
                "materiality": item.materiality,
            }
            for item in self.items
        ]


def manifest_from_projection(
    rows: Sequence[Mapping[str, Any]], *, window_start: str = ""
) -> Manifest:
    """Rebuild a manifest from :meth:`Manifest.projection` rows, in the recorded order.

    The inverse of the projection, and deliberately NOT a re-collect: numbering is taken from
    the rows verbatim, so a manifest restored here has the ordinals the digest was delivered
    with even if the underlying stores have moved on. Rows with no ordinal or no `source_id`
    are dropped — an item that cannot be addressed cannot be acted on, and admitting it would
    put an un-actionable ordinal back into the id space a reply is checked against.
    """
    items: list[CollectedItem] = []
    for row in rows:
        ordinal = str(row.get("ordinal", "") or "").strip()
        source_id = str(row.get("source_id", "") or "").strip()
        if not ordinal or not source_id:
            continue
        items.append(
            CollectedItem(
                source=str(row.get("source", "") or ""),
                source_id=source_id,
                title=str(row.get("title", "") or ""),
                permalink=str(row.get("permalink", "") or ""),
                materiality=str(row.get("materiality", "") or MATERIALITY_RESPONSE),
                ordinal=ordinal,
            )
        )
    return Manifest(items=tuple(items), window_start=window_start)


def _sort_key(item: CollectedItem) -> tuple[int, str, str]:
    return (
        _LANE_ORDER.get(item.source, len(_LANE_ORDER)),
        item.ts,
        item.source_id,
    )


def build_manifest(
    items: list[CollectedItem] | tuple[CollectedItem, ...],
    *,
    window_start: str = "",
) -> Manifest:
    """Number a collected set into a stable ordinal manifest.

    Sorted before numbered, deduplicated by fingerprint (first occurrence wins), ordinals
    assigned `"1"`…`"N"` as decimal strings — strings because that is what crosses the JSON
    boundary, and a stage that compared `3` to `"3"` would refuse every proposal.
    """
    ordered = sorted(items, key=_sort_key)
    kept: list[CollectedItem] = []
    dupes: list[str] = []
    seen: set[str] = set()
    for item in ordered:
        fp = item.fingerprint
        if fp in seen:
            dupes.append(fp)
            continue
        seen.add(fp)
        kept.append(replace(item, ordinal=str(len(kept) + 1)))
    return Manifest(items=tuple(kept), window_start=window_start, duplicates=tuple(dupes))


def render_manifest_lines(manifest: Manifest) -> str:
    """The manifest as the model sees it: one fenced line per item, ordinal first.

    Each item's untrusted text is fenced INDIVIDUALLY, with its own provenance attributes,
    rather than the whole block being wrapped once. Two reasons, both learned the hard way:
    a single fence around a composed block lets one crafted item's content read as
    commentary on its neighbours, and per-item provenance is what makes `source_id`
    available to a reader auditing which row produced a proposal.
    """
    from personalclaw.security import fence_untrusted

    lines: list[str] = []
    for item in manifest.items:
        body = item.title if not item.detail else f"{item.title} — {item.detail}"
        fenced = fence_untrusted(
            body,
            source=item.sender or item.source,
            source_type=f"triage_{item.source}",
            source_id=item.source_id,
            transformation_path="collect",
        )
        lines.append(f"{item.ordinal}. [{item.source}] {fenced}")
    return "\n".join(lines)


__all__ = [
    "COLLECT_SOURCES",
    "MATERIALITY_ACTION",
    "MATERIALITY_ERROR",
    "MATERIALITY_NONE",
    "MATERIALITY_ORDER",
    "MATERIALITY_RESPONSE",
    "SOURCE_CHANNEL",
    "SOURCE_INBOX",
    "SOURCE_RUN",
    "CollectedItem",
    "Manifest",
    "build_manifest",
    "materiality_rank",
    "render_manifest_lines",
]
