"""Staleness of a SYNTHESIZED knowledge item — the count a "sources changed" banner names.

WF2KNO-11 (KNOWLEDGE-SYNTHESIS, synthesis legibility), clause A. A synthesis is a claim
about a slice of the corpus at one moment. The corpus keeps moving; the document does not.
Serving it unchanged and unmarked is the failure this module exists to prevent — a reader
cannot tell a two-minute-old overview from one written before the three items that
contradict it arrived.

**What "new material" means here.** An item whose ``item_type`` is in
:data:`personalclaw.knowledge.semantics.SYNTHESIZED_KINDS` is stale when either:

(a) an item it CITES has an ``updated_at`` later than the synthesis' own ``updated_at`` —
    a source it quoted was rewritten underneath it; or
(b) an ACTIVE, non-synthesized item that shares at least one of the synthesis' tags, other
    than the synthesis itself, was created or updated after it — material the synthesis
    would plausibly have drawn on had it existed at the time.

``new_source_items`` is the distinct item count from (b) — the number the banner says out
loud. ``changed_sources`` is (a).

The wrong alternative, and the tempting one because it is one query with no join, is
corpus-wide recency: "anything created since the synthesis". That marks every synthesis
stale within minutes of any unrelated ingest, and a banner that is always lit is a banner
the reader learns to click past — strictly worse than no banner, because it also spends the
user's trust. Tag overlap is the cheapest honest proxy for "would this document have been
written differently", and it is the same signal the synthesizing stage used to pick inputs.

Two smaller choices that are also easy to get wrong:

* The reference stamp is the synthesis' ``updated_at``, never its ``created_at``. Using
  ``created_at`` would leave a just-regenerated document permanently stale: the banner would
  survive the one action it offers.
* (b) excludes synthesized kinds. Two overviews sharing a tag would otherwise hold each
  other stale forever, and regenerating either one would re-stale the other.

**A non-synthesized item is never stale.** :func:`staleness_for` returns ``stale=False``
with both counts zero for a note, fact, bookmark or file, and says so in ``scope``. That is
a deliberate answer, not an unhandled case: "stale" on an OBSERVED item would mean something
else entirely — that the world has moved on from a recorded fact — and its remedy would be
re-fetching the source, not regenerating a document out of the corpus. Answering both
questions under one word would give one banner two jobs.

Timestamps go through :func:`personalclaw.knowledge.semantics._parse`, the parser the rest
of the knowledge layer already uses, rather than a second hand-rolled one. The store writes
every ``created_at``/``updated_at`` with the same naive-local ``datetime.now().isoformat()``,
so running both sides of every comparison through one coercion is what keeps the ordering
honest.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone

from personalclaw.knowledge.semantics import SYNTHESIZED_KINDS, _parse
from personalclaw.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Staleness:
    """One synthesized item's answer to "has the corpus moved under this document?"."""

    item_id: str
    stale: bool
    #: Distinct items from rule (b) — the count the banner names.
    new_source_items: int
    #: Cited sources from rule (a) whose own ``updated_at`` moved after the synthesis.
    changed_sources: int
    checked_at: str
    #: One short human phrase naming what "new material" meant for this item, so a reader
    #: can argue with the number instead of just believing it.
    scope: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def is_synthesized(item_type: str) -> bool:
    """Whether ``item_type`` names a synthesized (rather than observed) kind.

    The one place both this module and the HTTP layer ask the question, so a route can
    reject "regenerate a bookmark" without re-listing the kinds.
    """
    return str(item_type or "") in SYNTHESIZED_KINDS


def _touched_after(row: sqlite3.Row, since: datetime) -> bool:
    """Was this item created or updated after ``since``?

    Both stamps, because either one moving is new material: a brand-new item and an edited
    old one are equally things the synthesis never saw.
    """
    for column in ("created_at", "updated_at"):
        stamp = _parse(str(row[column] or ""))
        if stamp is not None and stamp > since:
            return True
    return False


def _citations_table_present(db: sqlite3.Connection) -> bool:
    try:
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'item_citations'"
        ).fetchone()
    except sqlite3.Error:
        logger.debug("could not inspect sqlite_master for item_citations", exc_info=True)
        return False
    return row is not None


def _changed_cited_sources(
    store: KnowledgeStore, item_id: str, since: datetime
) -> tuple[int, bool]:
    """Rule (a): cited sources whose own ``updated_at`` moved after the synthesis.

    Returns ``(count, known)``. Per-marker citation rows live in ``item_citations``; a store
    written before that table existed simply has none, which degrades to "no cited sources
    known" (``0, False``) rather than a 500 on the one surface whose whole job is to tell the
    reader something. A missing table is not evidence that nothing was cited, so the caller
    reports the difference in ``scope`` instead of presenting zero as a fact.
    """
    if not _citations_table_present(store.db):
        return 0, False
    try:
        rows = store.db.execute(
            "SELECT DISTINCT c.source_item_id AS source_item_id, i.updated_at AS updated_at "
            "FROM item_citations c JOIN items i ON i.id = c.source_item_id "
            "WHERE c.item_id = ?",
            (item_id,),
        ).fetchall()
    except sqlite3.Error:
        logger.debug("item_citations unreadable for %s", item_id, exc_info=True)
        return 0, False
    changed = 0
    for row in rows:
        stamp = _parse(str(row["updated_at"] or ""))
        if stamp is not None and stamp > since:
            changed += 1
    return changed, True


def _new_tagged_items(
    store: KnowledgeStore, item_id: str, tag_ids: list[int], since: datetime
) -> int:
    """Rule (b): distinct active, non-synthesized, tag-sharing items newer than ``since``."""
    if not tag_ids:
        return 0
    placeholders = ",".join("?" for _ in tag_ids)
    rows = store.db.execute(
        "SELECT DISTINCT i.id AS id, i.item_type AS item_type, "
        "i.created_at AS created_at, i.updated_at AS updated_at "
        "FROM item_tags it JOIN items i ON i.id = it.item_id "
        f"WHERE it.tag_id IN ({placeholders}) AND i.id != ? "
        "AND COALESCE(i.status, 'active') = 'active'",
        (*tag_ids, item_id),
    ).fetchall()
    return sum(
        1
        for row in rows
        if not is_synthesized(str(row["item_type"] or "")) and _touched_after(row, since)
    )


def staleness_for(store: KnowledgeStore, item_id: str) -> Staleness:
    """Whether ``item_id``'s sources have moved since it was written, and by how much.

    Raises :class:`KeyError` for an unknown item. That is the caller's cue to 404 rather
    than to render a confident "not stale" for a document that does not exist.
    """
    item = store.get_item(item_id)
    if not item:
        raise KeyError(item_id)
    checked_at = datetime.now(timezone.utc).isoformat()
    fresh = Staleness(
        item_id=item_id,
        stale=False,
        new_source_items=0,
        changed_sources=0,
        checked_at=checked_at,
        scope="",
    )

    if not is_synthesized(str(item.get("item_type") or "")):
        return replace(fresh, scope="not a synthesized item — staleness does not apply")

    since = _parse(str(item.get("updated_at") or ""))
    if since is None:
        # An unreadable stamp makes every comparison meaningless. Saying so beats inventing
        # a reference time, which would make the banner's count arbitrary.
        return replace(fresh, scope="no readable timestamp on this item")

    tag_rows = store.db.execute(
        "SELECT t.id AS tag_id, t.name AS name FROM item_tags it "
        "JOIN tags t ON t.id = it.tag_id WHERE it.item_id = ? ORDER BY t.name",
        (item_id,),
    ).fetchall()
    tag_ids = [int(r["tag_id"]) for r in tag_rows]
    tag_names = [str(r["name"]) for r in tag_rows]

    new_source_items = _new_tagged_items(store, item_id, tag_ids, since)
    changed_sources, cited_known = _changed_cited_sources(store, item_id, since)

    if tag_names:
        scope = "items tagged " + ", ".join(tag_names)
    else:
        scope = "cited sources only — this synthesis carries no tags"
    if not cited_known:
        scope += "; no cited sources known"

    return Staleness(
        item_id=item_id,
        stale=bool(new_source_items or changed_sources),
        new_source_items=new_source_items,
        changed_sources=changed_sources,
        checked_at=checked_at,
        scope=scope,
    )
