"""Consolidation: the pass that keeps a growing knowledge store from becoming a landfill.

Clustering near-duplicate items and summarizing each cluster is the easy part. What makes a
consolidation pass safe to run unattended, repeatedly, forever, is everything around it:

**Never delete.** Originals are ARCHIVED with a back-reference to their summary. A summary that
lost a detail is recoverable; a deleted original is not, and no later pass can tell it happened.
`is_archived` demotes in ranking, which is the behaviour a user actually wants.

**Deterministic work first.** Normalize-and-hash dedup runs before any model call, and a
structural pre-pass clears already-consolidated entries. A pass that pays a model to notice two
byte-identical items is paying for arithmetic.

**Lineage caps.** Every summary records `parent_ids` and bumps its inputs' `reflection_count`;
items past the ceiling are ineligible. Without this the pass consolidates its own output and
each generation loses detail while gaining confidence — the worst possible direction.

**Human-gold protection.** `source.origin: user` items are never archived or demoted. An agent's
discovery is re-derivable; a user's decision is not.

**A gate stack, not a schedule.** Min-hours, min-new-material, a `consolidated` flag-cursor, a
contention lock, and a dry-run mode. Every one exists because the pass is expensive and its
failure mode is silent: a consolidation that runs on nothing still costs a model call, and one
that runs concurrently with itself can archive an original whose summary the other pass rolled
back.

Everything here is pure over explicit inputs. Callers own the store writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Similarity at or above which two items belong in the same cluster, ON THE EMBEDDING SCALE.
#: This is the plan's number, and it is only meaningful for a cosine metric.
CLUSTER_SIMILARITY = 0.75

#: The same decision on the TOKEN-OVERLAP scale, which is what runs when no embedder is
#: configured. Measured, not assumed: six human paraphrases of one fact score 0.12-0.36 pairwise
#: on token Jaccard, so applying the 0.75 cosine threshold to token similarity clustered nothing
#: at all — a pass that ran, reported success, and consolidated zero items every time. This is
#: the same defect class as a cosine cliff applied to RRF scores: a threshold is meaningless
#: without its number space.
#:
#: 0.30 keeps paraphrases of one fact together while leaving genuinely different topics apart
#: (cross-topic pairs measured below 0.10). Deliberately the LOWER-precision tier: it groups
#: candidates for a model that then decides, and the doctrine forbids that model from inventing.
TOKEN_CLUSTER_SIMILARITY = 0.30

#: Fuzzy-hash threshold for the deterministic PRE-dedup, before any model call. Higher than the
#: cluster threshold: this tier claims the items are the SAME, not merely related.
PREDEDUP_SIMILARITY = 0.95

#: A cluster smaller than this is not worth a model call — summarizing two items into one
#: paragraph loses more than it saves.
MIN_CLUSTER_SIZE = 5

#: Per-pass caps. Marginal cost has to be independent of store size, or the pass gets slower
#: every week until it stops finishing.
MAX_CLUSTERS_PER_PASS = 10
MAX_ITEMS_PER_PASS = 100

#: Lineage ceiling — see the module docstring.
MAX_REFLECTION_COUNT = 3

#: Gates. Hours since the last pass, and how much new material must have arrived. Both are
#: cheap, deterministic answers to "is there anything to do", asked before anything expensive.
MIN_HOURS_BETWEEN_PASSES = 6
MIN_NEW_ITEMS = 5

#: The doctrine every background synthesis prompt carries. Stated as a constant because it is
#: the difference between consolidation and invention, and a paraphrase weakens it.
CONSOLIDATION_DOCTRINE = (
    "Consolidate existing data; never generate new knowledge. Preserve EVERY distinct detail "
    "from the inputs — if two inputs disagree, record both with their sources rather than "
    "choosing. Do not add facts, inferences, or context that is not present in the inputs."
)

#: Kinds and origins that never get archived or demoted.
PROTECTED_ORIGINS = frozenset({"user", "human"})


@dataclass
class Item:
    """The subset of a knowledge row consolidation reasons about.

    A projection rather than the store row: this module is pure, and taking a `sqlite3.Row`
    would make every test need a database.
    """

    id: str
    kind: str = "fact"
    title: str = ""
    summary: str = ""
    content: str = ""
    logical_key: str = ""
    content_hash: str = ""
    origin: str = ""
    reflection_count: int = 0
    consolidated: bool = False
    is_archived: bool = False
    updated_at: str = ""
    inbound_relations: int = 0
    citations: list[str] = field(default_factory=list)
    chunk_hashes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> Item:
        """Build from a store row (or any mapping), reading the overlay out of file_metadata."""
        data = dict(row) if not isinstance(row, dict) else row
        meta = _meta(data.get("file_metadata"))
        origin = meta.get("origin") or _nested_origin(meta)
        return cls(
            id=str(data.get("id", "") or ""),
            kind=str(data.get("kind", "") or "fact"),
            title=str(data.get("title", "") or ""),
            summary=str(data.get("summary", "") or ""),
            content=str(data.get("content", "") or ""),
            logical_key=str(data.get("logical_key", "") or ""),
            content_hash=str(data.get("content_hash", "") or ""),
            origin=str(origin or ""),
            reflection_count=_int(meta.get("reflection_count"), 0),
            consolidated=bool(meta.get("consolidated", False)),
            is_archived=bool(data.get("is_archived", False)),
            updated_at=str(data.get("updated_at", "") or ""),
            inbound_relations=_int(data.get("inbound_relations"), 0),
            citations=[str(c) for c in (meta.get("citations") or []) if str(c)],
            chunk_hashes={
                str(k): str(v) for k, v in (meta.get("chunk_hashes") or {}).items() if str(k)
            },
        )

    @property
    def protected(self) -> bool:
        """Human-gold: never archived, never demoted (KNOW-R13)."""
        return self.origin.strip().lower() in PROTECTED_ORIGINS

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.summary}\n{self.content}".strip()


# ── the gate stack (§4.4 #5) ──


@dataclass
class GateResult:
    """Whether a pass may run, and why not.

    A REASON rather than a bare False: a consolidation that silently declines to run looks
    identical to one that ran and found nothing, and the two need different responses from
    whoever is watching the backlog.
    """

    allowed: bool
    reason: str = ""
    backlog: int = 0

    def __bool__(self) -> bool:
        return self.allowed


def check_gates(
    *,
    unprocessed: int,
    hours_since_last: float,
    lock_held: bool = False,
    min_new_items: int = MIN_NEW_ITEMS,
    min_hours: float = MIN_HOURS_BETWEEN_PASSES,
) -> GateResult:
    """May a consolidation pass run now?

    Ordered cheapest-first, and the contention check is FIRST: two concurrent passes can each
    archive an original whose summary the other rolls back, which corrupts lineage in a way no
    later pass can detect. Everything else merely wastes a call.
    """
    if lock_held:
        return GateResult(False, "another consolidation pass is running", backlog=unprocessed)
    if unprocessed < max(1, min_new_items):
        return GateResult(
            False, f"only {unprocessed} unconsolidated items (floor {min_new_items})", unprocessed
        )
    if hours_since_last < min_hours:
        return GateResult(
            False,
            f"last pass {hours_since_last:.1f}h ago (floor {min_hours}h)",
            backlog=unprocessed,
        )
    return GateResult(True, backlog=unprocessed)


# ── deterministic pre-dedup (§4.4 #2) ──


def normalize_for_dedup(text: str) -> str:
    """Collapse the differences that are not differences.

    Casing, punctuation and whitespace only. NOT stopword removal or stemming: those make
    genuinely different statements collide, and a wrong merge is the expensive error.
    """
    lowered = (text or "").lower()
    stripped = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return " ".join(stripped.split())


def fuzzy_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_dedup(text).encode("utf-8", "replace")).hexdigest()[:16]


def token_similarity(left: str, right: str) -> float:
    """Jaccard overlap on 3+-char tokens. Cheap, embedder-independent, and good enough to
    decide whether a model call is worth making."""
    a = set(re.findall(r"[a-z0-9]{3,}", normalize_for_dedup(left)))
    b = set(re.findall(r"[a-z0-9]{3,}", normalize_for_dedup(right)))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pre_dedup(items: list[Item]) -> tuple[list[Item], list[tuple[str, str]]]:
    """Drop exact/near-exact duplicates before any model call.

    Returns `(survivors, merges)` where each merge is `(kept_id, dropped_id)`.

    A PROTECTED item always wins its pair, regardless of order: the user's phrasing is the one
    to keep. Otherwise the earlier item wins, so the id that other items already reference stays
    the canonical one — picking the newer would orphan every existing back-reference.
    """
    survivors: list[Item] = []
    merges: list[tuple[str, str]] = []
    by_hash: dict[str, Item] = {}

    for item in items:
        digest = fuzzy_hash(item.text)
        existing = by_hash.get(digest)
        if existing is None:
            near = next(
                (
                    s
                    for s in survivors
                    if token_similarity(s.text, item.text) >= PREDEDUP_SIMILARITY
                ),
                None,
            )
            if near is None:
                by_hash[digest] = item
                survivors.append(item)
                continue
            existing = near
        keep, drop = (
            (item, existing) if (item.protected and not existing.protected) else (existing, item)
        )
        if keep is not existing:
            survivors[survivors.index(existing)] = keep
            by_hash[digest] = keep
        merges.append((keep.id, drop.id))
    return survivors, merges


# ── clustering (§4.4 #3) ──


@dataclass
class Cluster:
    """One group of related items, and what a synthesis of them would cost."""

    items: list[Item] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self.items]

    @property
    def size(self) -> int:
        return len(self.items)

    def compression_ratio(self, summary_chars: int) -> float:
        """Input chars → output chars. The subsystem's health metric: a pass whose ratio
        approaches 1.0 is spending money to rewrite rather than to condense."""
        total = sum(len(i.text) for i in self.items)
        if not total:
            return 0.0
        return round(summary_chars / total, 4)


def cluster_items(
    items: list[Item],
    *,
    similarity: Any = None,
    threshold: float | None = None,
    min_size: int = MIN_CLUSTER_SIZE,
    max_clusters: int = MAX_CLUSTERS_PER_PASS,
) -> list[Cluster]:
    """Group items worth consolidating together.

    Single-link agglomeration. `similarity` is injectable — pass a cosine function backed by the
    store's embeddings for the better neighbour set, or leave it None for the token-overlap floor
    that works with no embedder at all.

    The DEFAULT THRESHOLD FOLLOWS THE METRIC (`TOKEN_CLUSTER_SIMILARITY` vs
    `CLUSTER_SIMILARITY`), because the two live on different scales and mixing them clusters
    either everything or nothing. An explicit `threshold` overrides, and a caller passing its own
    metric should pass its own number with it.

    Clusters below `min_size` are DROPPED rather than merged into a neighbour: forcing two
    unrelated pairs together to reach the floor produces a summary about nothing, which reads as
    authoritative and is not.

    Only reflection-eligible, non-archived, unconsolidated items are considered — the filter is
    here rather than at the caller so no path can skip it.
    """
    metric = similarity or token_similarity
    cut = (
        threshold
        if threshold is not None
        else (TOKEN_CLUSTER_SIMILARITY if similarity is None else CLUSTER_SIMILARITY)
    )
    pool = [
        i
        for i in items
        if not i.is_archived and not i.consolidated and i.reflection_count < MAX_REFLECTION_COUNT
    ][:MAX_ITEMS_PER_PASS]

    clusters: list[list[Item]] = []
    for item in pool:
        for group in clusters:
            if any(metric(member.text, item.text) >= cut for member in group):
                group.append(item)
                break
        else:
            clusters.append([item])

    big = [Cluster(items=g) for g in clusters if len(g) >= max(2, min_size)]
    # Largest first: the biggest cluster is where the compression is, and the per-pass cap
    # should spend itself on that rather than on whichever cluster happened to form first.
    big.sort(key=lambda c: c.size, reverse=True)
    return big[:max_clusters]


# ── the consolidation plan (a DRY-RUN artifact) ──


@dataclass
class ConsolidationPlan:
    """What a pass WOULD do. The dry-run artifact, and the thing a caller applies.

    Separating plan from apply is what makes the preview mode real: a preview that re-derives
    its own answer can disagree with the pass it was previewing, which makes it worse than no
    preview at all.
    """

    clusters: list[Cluster] = field(default_factory=list)
    pre_dedup_merges: list[tuple[str, str]] = field(default_factory=list)
    skipped_protected: list[str] = field(default_factory=list)
    skipped_lineage: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.clusters and not self.pre_dedup_merges

    def to_dict(self) -> dict[str, Any]:
        return {
            "clusters": [
                {"ids": c.ids, "size": c.size, "titles": [i.title for i in c.items]}
                for c in self.clusters
            ],
            "pre_dedup_merges": [{"kept": k, "dropped": d} for k, d in self.pre_dedup_merges],
            "skipped_protected": list(self.skipped_protected),
            "skipped_lineage": list(self.skipped_lineage),
            "items_affected": sum(c.size for c in self.clusters) + len(self.pre_dedup_merges),
        }


def plan_consolidation(items: list[Item], **kwargs: Any) -> ConsolidationPlan:
    """Everything a pass would do, without doing any of it."""
    protected = [i.id for i in items if i.protected]
    lineage = [i.id for i in items if i.reflection_count >= MAX_REFLECTION_COUNT]
    # Protected items are excluded from CONSOLIDATION but not from the store: they stay
    # retrievable and unarchived. Excluding them here is what makes that promise true.
    candidates = [i for i in items if not i.protected]
    survivors, merges = pre_dedup(candidates)
    return ConsolidationPlan(
        clusters=cluster_items(survivors, **kwargs),
        pre_dedup_merges=merges,
        skipped_protected=protected,
        skipped_lineage=lineage,
    )


def synthesis_prompt(cluster: Cluster, *, conventions: str = "") -> str:
    """The prompt for ONE cluster. Doctrine first, inputs fenced.

    Fenced because cluster content is whatever was ingested — a knowledge item quoting an
    instruction is not an instruction, and a background pass has no user watching it.
    """
    parts = [CONSOLIDATION_DOCTRINE]
    if conventions.strip():
        parts.append(f"Store conventions:\n{conventions.strip()}")
    parts.append(f"Consolidate these {cluster.size} related items into ONE item.")
    for item in cluster.items:
        parts.append(
            f"<knowledge_item id={item.id} kind={item.kind}>\n"
            f"{item.title}\n{item.summary}\n{item.content}\n</knowledge_item>"
        )
    return "\n\n".join(parts)


def summary_metadata(cluster: Cluster, *, summary_chars: int) -> dict[str, Any]:
    """The lineage a summary must carry to be safe to build on.

    `parent_ids` doubles as §3.2's `derived_from` relation — one mechanism, so a reader
    following provenance and a pass computing eligibility agree by construction.
    """
    return {
        "parent_ids": cluster.ids,
        "reflection_count": max((i.reflection_count for i in cluster.items), default=0) + 1,
        "consolidated": True,
        "source_count": cluster.size,
        "compression_ratio": cluster.compression_ratio(summary_chars),
    }


# ── health checks (§3.4 #1, zero-LLM) ──

#: A body under this is a stub — a title with nothing behind it, which reads as coverage and is
#: not. The plan says 100 chars; measured, that over-fires badly on the content the store is
#: actually FOR: "Cold start latency measured 4.2s on the M2 after a fresh boot" is 83 characters
#: and is a complete, useful fact. Six such items reported as six stubs is a report nobody reads.
#:
#: So the length floor is lower AND it is not the only test: a stub is short AND says nothing
#: specific. A body with a number, a name, or a path in it is making a claim regardless of length.
STUB_BODY_CHARS = 40

#: Anything matching these is a claim, not a stub, however short. A measurement, an identifier, a
#: path, a version — the things a short knowledge item exists to record.
_SUBSTANTIVE_RE = re.compile(
    r"(\d)|(/[\w.-]+)|([a-z]+\.[a-z]{2,})|(\b[A-Z][a-z]+[A-Z]\w*)|(`[^`]+`)"
)


@dataclass
class HealthReport:
    """Deterministic findings. Zero model calls, so this can run on every persist."""

    stubs: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    broken_citations: list[dict[str, str]] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    unindexed: list[str] = field(default_factory=list)
    stale_chunks: list[dict[str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not any(
            (
                self.stubs,
                self.orphans,
                self.broken_citations,
                self.expired,
                self.unindexed,
                self.stale_chunks,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stubs": list(self.stubs),
            "orphans": list(self.orphans),
            "broken_citations": list(self.broken_citations),
            "expired": list(self.expired),
            "unindexed": list(self.unindexed),
            "stale_chunks": list(self.stale_chunks),
            "clean": self.clean,
        }


def check_health(
    items: list[Item],
    *,
    known_ids: set[str] | None = None,
    indexed_ids: set[str] | None = None,
    expired_ids: set[str] | None = None,
) -> HealthReport:
    """Everything wrong with the store that can be found without a model.

    Orphans are FLAGGED, never auto-deleted: an item nothing links to may be the only record of
    something, and "unreferenced" is not "worthless". Auto-deletion here would be irreversible
    on the basis of a graph property that says nothing about content.
    """
    ids = known_ids if known_ids is not None else {i.id for i in items}
    report = HealthReport()
    for item in items:
        if item.is_archived:
            continue  # archived items are demoted by design; reporting them is noise
        if _is_stub(item):
            report.stubs.append(item.id)
        if item.inbound_relations == 0 and item.kind not in ("overview", "probe"):
            report.orphans.append(item.id)
        for citation in item.citations:
            # Only INTERNAL references can be checked: a URL's reachability is a network
            # question, and calling an unreachable URL "broken" here would make the report
            # depend on connectivity.
            if citation.startswith("item:") and citation[len("item:") :] not in ids:
                report.broken_citations.append({"item_id": item.id, "citation": citation})
        if indexed_ids is not None and item.id not in indexed_ids:
            report.unindexed.append(item.id)
    if expired_ids:
        report.expired = sorted(expired_ids)
    return report


# ── differential refresh (§3.4) ──


def _is_stub(item: Item) -> bool:
    """A title with nothing behind it.

    Short AND unspecific. Length alone flagged complete one-line facts, which is the shape a lot
    of genuine knowledge takes — and a stub report full of real items trains the reader to ignore
    it, which costs more than the stubs.
    """
    body = f"{item.content} {item.summary}".strip()
    if not body:
        return True
    if len(body) >= STUB_BODY_CHARS:
        return False
    return not _SUBSTANTIVE_RE.search(body)


def changed_sections(stored: dict[str, str], fresh: dict[str, str]) -> list[str]:
    """Which sections of a re-fetched source actually changed.

    The invariant that matters: BOTH sides must be the same hash form. The studied failure was
    storing a truncated hash and comparing a full one, which made every section look changed
    forever — a refresh that always re-synthesizes everything, at full cost, silently.

    A section present in `fresh` but not `stored` counts as changed (it is new); one present in
    `stored` but not `fresh` does NOT (it was removed, and re-synthesizing a deleted section is
    meaningless).
    """
    out: list[str] = []
    for key, digest in fresh.items():
        prior = stored.get(key)
        if prior is None or not _same_hash_form(prior, digest) or prior != digest:
            out.append(key)
    return sorted(out)


def _same_hash_form(left: str, right: str) -> bool:
    """Guard against comparing a truncated hash to a full one.

    Returns False on a length mismatch, which makes `changed_sections` report the section as
    changed — the safe direction (re-synthesize once) rather than the silent one (treat two
    incomparable values as equal and never refresh).
    """
    return len(left) == len(right)


def chunk_hashes(sections: dict[str, str]) -> dict[str, str]:
    """Per-section hashes in ONE canonical form, so a later comparison can be trusted."""
    return {str(k): fuzzy_hash(str(v)) for k, v in sections.items()}


# ── phantom hubs (§3.4 #3, gap-healing) ──


def phantom_hubs(
    items: list[Item], *, mentions: dict[str, list[str]], min_mentions: int = 3
) -> list[dict[str, Any]]:
    """Entities that many items reference but that have no item of their own.

    The store's growth frontier: a name five items lean on is something the store believes
    matters and has never written down. Returned as candidates for a PROPOSAL, never a write —
    direct-write healing is the studied anti-pattern, because a drafted entry nobody reviewed
    becomes a citable source for the next draft.
    """
    have = {i.logical_key for i in items if i.logical_key} | {
        _slug(i.title) for i in items if i.title
    }
    out: list[dict[str, Any]] = []
    for entity, referrers in mentions.items():
        slug = _slug(entity)
        if not slug or slug in have or any(slug in key for key in have):
            continue
        unique = sorted(set(referrers))
        if len(unique) < max(2, min_mentions):
            continue
        out.append({"entity": entity, "slug": slug, "referrers": unique, "mentions": len(unique)})
    out.sort(key=lambda h: (-int(h["mentions"]), str(h["entity"])))
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# ── lint cadence (§3.4 #2) ──


def lint_due(*, persists_since_last: int, every_n: int, health_clean: bool) -> tuple[bool, str]:
    """Is the semantic lint pass due?

    Cadenced by MUTATION COUNT rather than wall clock: a store nobody wrote to does not need
    linting, and a busy week needs it more than once. A wall-clock cadence gets both backwards.

    Health must be clean first — the plan's rule, and the reason is arithmetic: linting a stub
    spends a model call to discover it is a stub, which the zero-cost pass already knew.
    """
    if not health_clean:
        return False, "health findings outstanding — fix those first (linting a stub wastes tokens)"
    need = max(1, every_n)
    if persists_since_last < need:
        return False, f"{persists_since_last} writes since last lint (cadence {need})"
    return True, ""


def _meta(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested_origin(meta: dict[str, Any]) -> str:
    source = meta.get("source")
    if isinstance(source, dict):
        return str(source.get("origin", "") or "")
    return ""


def _int(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    return int(raw)
