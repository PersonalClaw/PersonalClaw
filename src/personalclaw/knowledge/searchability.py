"""Searchability verdicts — an ingest that yielded nothing retrievable says so (RET-2).

🔴 WHY THIS EXISTS. The failure this module names is *a write that reported success and
did not land*. Measured on ``origin/main`` before this module existed:

* an **image-only PDF** ingested to ``processing_status='done'`` with
  ``node_phases['document_read']='done'`` — while ``pdfplumber.extract_text()`` returned
  ``''`` for every page. The item's persisted content was the synthesized structural
  descriptor (``"Document: scan.pdf (pdf, 1 pages, 3 KB)"``), i.e. **none of the
  document's words**, and a search for a word plainly visible on the page returned
  nothing at all.
* a document ingested with **no embedding provider bound** also reached
  ``processing_status='done'`` with **zero rows** in ``chunks`` and no item vector — the
  whole semantic half of retrieval silently absent.

Both are the AnythingLLM #6143 shape ("the embedding step silently writes nothing… RAG
retrieval returns no sources, while the app reports success") and PersonalClaw's own OU-3
finding that model-dependent write paths fail OPEN and silently.

**The contract.** One vocabulary, in one file, read by all three surfaces so they can
never disagree:

* the **ingest runner** persists :data:`UNSEARCHABLE` as the item's
  ``processing_status`` (never ``done``) plus the typed reason under
  ``file_metadata['unsearchable_reason']``;
* the **Doctor** row (``knowledge.searchability``) lists one row per affected item;
* **``knowledge_search``** reports the same typed reason instead of a bare empty result
  set — the reason is *read off the persisted fact*, not re-derived, so there is exactly
  one place a reason can be minted.

Deliberately NOT a new wire error code: no request failed. An item that is present but
unretrievable is a *state* the surfaces report, so it lives in the status vocabulary the
store already carries (``queued`` / ``processing`` / ``done`` / ``partial`` / ``failed``
/ ``unreachable``) rather than in ``http_errors.HTTP_ERROR_CODES``.

**RET-4 extends the vocabulary with one READ-TIME reason.** :data:`STALE_INDEX` names an
item whose chunk vectors came from a different embedding model than the one bound now
(:mod:`personalclaw.knowledge.embedding_fingerprint`). It is minted by a comparison at
query time rather than persisted at ingest, because the fact that changed is the *bound
model*, not the item — so it must never be written into the item's ``processing_status``.
:data:`INGEST_REASONS` is the subset an ingest may persist; :data:`REASONS` is every reason
a surface may display.

**What a surface may CLAIM about these items.** "Unsearchable" is the status token, not the
fact: three of the four reasons remove only the *semantic* half of retrieval, and the item's
text is still in ``items_fts``, so keyword search reaches it. Measured in live validation on a
home with no provider bound, Doctor read "3 ingested items cannot be found by search … no query
can reach them" while the ``knowledge_search`` tool printed the same claim and then LISTED the
match. So the count-bearing sentence is minted here, once per reason
(:attr:`Degradation.summary`), and both surfaces print it verbatim — neither composes its own
claim, so neither can drift back to the false one.

**Counts are the library's.** A row's ``shelf`` says where the product SHOWS it. An artifact's
search mirror and a report's finding are indexed for search and deliberately never listed in
the library (:func:`shelf_of`), so counting them as "items" put 3 beside a library of 2. They
are counted apart, under their own nouns, rather than dropped: an artifact that semantic search
cannot reach is still a fact worth reporting — it is just not one of "your items".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from personalclaw.knowledge.artifact_ingest import ARTIFACT_ITEM_TYPE
from personalclaw.knowledge.semantics import DEFAULT_LIST_EXCLUDED_KINDS

logger = logging.getLogger(__name__)

#: The named ``processing_status`` value for an item that persisted but is not retrievable.
#: A distinct value rather than reusing ``partial``: ``partial`` already means "some
#: OPTIONAL step was skipped" and is routinely benign (the UI suppresses its
#: ``"Skipped (optional steps unavailable):"`` prefix), so folding "nothing about this item
#: can be found" into it would hide the loud case inside the quiet one.
UNSEARCHABLE = "unsearchable"

#: A text-extraction node reported SUCCESS and produced no text, so the item's persisted
#: content holds none of the source document's words (the image-only-PDF case).
NO_EXTRACTABLE_TEXT = "no_extractable_text"

#: No embedding provider was bound, so no vector and no chunk was written — the item has
#: keyword reach at best and no semantic reach at all.
NO_EMBEDDING_PROVIDER = "no_embedding_provider"

#: An embedding provider WAS bound and the item still ended with no vector and no chunk
#: (the embed attempt errored, or the provider returned nothing).
NOT_INDEXED = "not_indexed"

#: The item's chunk vectors were written by a DIFFERENT embedding model than the one bound
#: now, so they are not comparable to the current query vector (RET-4). Unlike the three
#: above, this reason is NOT persisted on the item at ingest time — it is a fact about the
#: bound model, which changes under an item that never changed, so it is derived at read
#: time from the chunk fingerprints (:mod:`personalclaw.knowledge.embedding_fingerprint`).
#: The item is otherwise healthy; a re-index fixes it without re-ingesting anything.
STALE_INDEX = "stale_index"

#: The closed reason vocabulary. Matched explicitly by every consumer: an unknown value
#: must read as "unknown reason", never fall into a default branch that reports health.
#: :data:`STALE_INDEX` is last because it is the only member no ingest can mint — see
#: :data:`INGEST_REASONS` for the subset ``verdict_for_ingest`` can return.
REASONS: tuple[str, ...] = (
    NO_EXTRACTABLE_TEXT,
    NO_EMBEDDING_PROVIDER,
    NOT_INDEXED,
    STALE_INDEX,
)

#: The subset an INGEST can persist under ``file_metadata['unsearchable_reason']``. Split
#: out so a test can assert the read-time-only reason never reaches the persisted status
#: vocabulary — a stale index is not a broken ingest and must not mark the item
#: ``unsearchable``, because the very next re-index makes it retrievable again.
INGEST_REASONS: tuple[str, ...] = (NO_EXTRACTABLE_TEXT, NO_EMBEDDING_PROVIDER, NOT_INDEXED)

#: What the user can do about each reason. Written without pronouns on purpose: the same
#: sentence follows a claim about ONE item (the item's status line) and about many (the
#: ``knowledge_search`` note), and "re-ingest it" after "3 items …" is the kind of seam a
#: reader trips on. Every member of :data:`REASONS` has one — a reason with no remedy is a
#: diagnosis with no next step.
REASON_REMEDY: dict[str, str] = {
    NO_EXTRACTABLE_TEXT: (
        "Install an OCR app or bind a vision model and re-ingest, or add a text version."
    ),
    NO_EMBEDDING_PROVIDER: "Bind an embedding model in Settings → Models, then re-index.",
    NOT_INDEXED: "Re-ingest, and check the embedding provider's health in Doctor.",
    STALE_INDEX: (
        "Run the embedding re-index (Settings → Models) to rebuild the vectors; nothing "
        "needs re-ingesting."
    ),
}

#: How many item ids a report carries as a sample. The COUNT is always exact; the id list
#: is a sample so a library with thousands of unsearchable items still produces a readable
#: row (and a bounded response).
SAMPLE_LIMIT = 5

#: Where the product SHOWS a row. The library lists almost everything; the other two are
#: INDEXED, not LISTED (PEP-7's artifact mirror, WF2KNO-12's report findings), and a count a
#: user compares with their library must not include them as "items".
LIBRARY_SHELF = "library"
ARTIFACT_SHELF = "artifact"
FINDING_SHELF = "finding"

#: The noun a count uses per shelf, singular and plural. Library rows are "items" because
#: that is the word the library's own header uses for them.
_SHELF_NOUNS: dict[str, tuple[str, str]] = {
    LIBRARY_SHELF: ("item", "items"),
    ARTIFACT_SHELF: ("artifact", "artifacts"),
    FINDING_SHELF: ("report finding", "report findings"),
}


def shelf_of(item_type: Any, kind: Any) -> str:
    """Which shelf a row is shown on — the library's own "is this listed" rule, per row.

    The same two facts ``GET /api/knowledge/items`` filters on and nothing else: an
    ``artifact`` item type is an artifact's search mirror, and a kind in
    :data:`~personalclaw.knowledge.semantics.DEFAULT_LIST_EXCLUDED_KINDS` (today exactly the
    report-finding kind) is a report's finding. Archived rows never reach here — every reader
    of this vocabulary already excludes them, as the library's default view does.
    """
    if str(item_type or "") == ARTIFACT_ITEM_TYPE:
        return ARTIFACT_SHELF
    if str(kind or "") in DEFAULT_LIST_EXCLUDED_KINDS:
        return FINDING_SHELF
    return LIBRARY_SHELF


def _reach_sentence(reason: str, subject: str, count: int) -> str:
    """What search CAN and cannot do for *count* rows sharing *reason* — the one claim.

    No trailing full stop, so a caller can join several with ``"; "`` or follow one with its
    remedy. Three reasons leave keyword search intact and say so in exactly those words; only
    :data:`NO_EXTRACTABLE_TEXT` takes the words themselves away.
    """
    one = count == 1
    has, them = ("has", "it") if one else ("have", "them")
    if reason == NO_EMBEDDING_PROVIDER:
        return (
            f"{subject} {has} no embeddings because no embedding model is bound — keyword "
            f"search finds {them}, semantic search cannot"
        )
    if reason == NOT_INDEXED:
        return (
            f"{subject} {has} no embeddings because the embedding step produced no vector — "
            f"keyword search finds {them}, semantic search cannot"
        )
    if reason == STALE_INDEX:
        return (
            f"{subject} {has} embeddings from a different embedding model than the one bound "
            f"now — keyword search finds {them}, semantic search skips {them} until a re-index"
        )
    if reason == NO_EXTRACTABLE_TEXT:
        what, name = (
            ("a scan or an image-only PDF", "its file name")
            if one
            else ("scans or image-only PDFs", "their file names")
        )
        return (
            f"{subject} had no text that could be extracted ({what}) — search can match "
            f"{name}, never the words on the page"
        )
    return f"{subject} {'is' if one else 'are'} not fully searchable ({reason or 'unknown reason'})"


def _count_of(count: int, shelf: str) -> str:
    singular, plural = _SHELF_NOUNS.get(shelf, _SHELF_NOUNS[LIBRARY_SHELF])
    return f"{count} {singular if count == 1 else plural}"


def reach_summary(reason: str, item_count: int, unlisted: Mapping[str, int] | None = None) -> str:
    """The count-bearing claim for one reason: "2 items and 1 artifact have no embeddings …".

    *item_count* is the LIBRARY's count (rows it lists); *unlisted* counts the rest per shelf,
    each under its own noun, so the leading number is the one the user can check against the
    library and nothing is silently dropped.
    """
    extra = {s: int(n) for s, n in (unlisted or {}).items() if n and s != LIBRARY_SHELF}
    known = [s for s in (ARTIFACT_SHELF, FINDING_SHELF) if s in extra]
    order = known + sorted(s for s in extra if s not in known)
    parts = [_count_of(item_count, LIBRARY_SHELF)] if item_count or not extra else []
    parts += [_count_of(extra[s], s) for s in order]
    subject = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return _reach_sentence(reason, subject, item_count + sum(extra.values()))


def reason_detail(reason: str) -> str:
    """The item-level sentence for *reason*: what search can still do, then what to do.

    This is what an item's status line reads, so it is a sentence a person reads — it starts
    with a capital and carries no token. The token lives beside it in
    ``file_metadata['unsearchable_reason']``, which is where machines read it.
    """
    remedy = REASON_REMEDY.get(reason)
    if remedy is None:
        return f"This item is not fully searchable ({reason or 'unknown reason'})."
    return f"{_reach_sentence(reason, 'This item', 1)}. {remedy}"


def verdict_for_ingest(
    *,
    chunk_count: int,
    has_item_vector: bool,
    has_text: bool,
    embedder_bound: bool,
    empty_success_extractors: Iterable[str] = (),
) -> Optional[str]:
    """The typed reason this ingest is not retrievable, or ``None`` when it is fine.

    Ordered most-specific first, because the reasons are not independent: a scan ingested
    on a home with no embedding provider fails BOTH ways, and the extraction gap is the
    root cause the user must fix first (binding an embedder would embed the descriptor,
    not the document).

    *empty_success_extractors* names pooled nodes that reported ``success`` and yielded no
    text — the node LIED, which is a different fact from a node that was skipped because
    its model is absent. An image uploaded with no vision model skips its extractors, so it
    is a *declared* degradation and is deliberately not flagged here; a PDF whose
    ``document_read`` returned ``done`` with empty text is the silent failure this atom
    exists for.
    """
    if any(empty_success_extractors):
        return NO_EXTRACTABLE_TEXT
    if chunk_count <= 0 and not has_item_vector and has_text:
        return NO_EMBEDDING_PROVIDER if not embedder_bound else NOT_INDEXED
    return None


@dataclass(frozen=True)
class UnsearchableItem:
    """One row on an attention surface: an item that persisted and search cannot fully reach.

    ``shelf`` is where the product shows it (:func:`shelf_of`). Required, never defaulted: a
    default of "library" would silently count every mirror a new reader forgot to classify.
    """

    item_id: str
    title: str
    reason: str
    shelf: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "reason": self.reason,
            "shelf": self.shelf,
        }


@dataclass(frozen=True)
class Degradation:
    """The typed answer a search gives instead of a bare empty result set.

    ``item_count`` is exact and counts LIBRARY rows only — the number a user can check against
    their library. ``unlisted`` counts the indexed-but-not-listed rows sharing the reason, per
    shelf. ``item_ids`` is a library sample capped at :data:`SAMPLE_LIMIT`.
    """

    reason: str
    detail: str
    item_count: int
    item_ids: tuple[str, ...] = ()
    unlisted: tuple[tuple[str, int], ...] = ()

    @property
    def summary(self) -> str:
        """The count-bearing claim every surface prints verbatim (see the module docstring)."""
        return reach_summary(self.reason, self.item_count, dict(self.unlisted))

    @property
    def remedy(self) -> str:
        """What to do about it, or ``""`` for a reason outside the vocabulary."""
        return REASON_REMEDY.get(self.reason, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "summary": self.summary,
            "item_count": self.item_count,
            "item_ids": list(self.item_ids),
            "unlisted": dict(self.unlisted),
        }


@dataclass(frozen=True)
class SearchOutcome:
    """A search's hits PLUS why the library may not have been able to answer.

    The pair is the whole point: an empty ``results`` with an empty ``degradations`` is a
    library that genuinely holds nothing matching, and an empty ``results`` WITH a
    degradation is a library that holds the answer and cannot reach it. Those are
    different facts, and collapsing them into "no results" is the defect RET-2 attacks.
    """

    results: list[dict]
    degradations: tuple[Degradation, ...] = ()

    @property
    def degraded(self) -> bool:
        return bool(self.degradations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": self.results,
            "degradations": [d.to_dict() for d in self.degradations],
        }


def _reason_of(file_metadata: Any) -> str:
    """The typed reason recorded on an item, from its ``file_metadata`` (dict or JSON)."""
    meta = file_metadata
    if isinstance(meta, (str, bytes)):
        try:
            meta = json.loads(meta or "{}")
        except (ValueError, TypeError):
            return ""
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("unsearchable_reason") or "")


#: The columns every reader selects, in :func:`rows_from`'s order — one list, so the live
#: store and the Doctor's read-only connection cannot select different facts.
ROW_FIELDS: tuple[str, ...] = ("id", "title", "file_metadata", "item_type", "kind")


def row_select(present: Iterable[str] | None = None) -> str:
    """The SELECT list for :data:`ROW_FIELDS`.

    *present* is the ``items`` table's actual columns, for a reader that cannot migrate (the
    Doctor opens ``mode=ro``): a column an older database never gained reads as NULL instead
    of failing the whole probe. ``kind`` is the one that matters — it arrived after
    ``processing_status``, so a home last opened by an older build has the rows and not it.
    """
    have = set(ROW_FIELDS) if present is None else set(present)
    return ", ".join(f if f in have else f"NULL AS {f}" for f in ROW_FIELDS)


def rows_from(records: Iterable[tuple[Any, ...]]) -> list[UnsearchableItem]:
    """Build attention rows from ``(id, title, file_metadata, item_type, kind)`` records.

    Split out from the two readers below (the live store and the Doctor's read-only
    sqlite connection) so both produce identical rows from identical data — the reason
    and shelf resolution cannot drift between the surface a user sees and the one a test
    asserts.
    """
    rows: list[UnsearchableItem] = []
    for item_id, title, meta, item_type, kind in records:
        rows.append(
            UnsearchableItem(
                item_id=str(item_id),
                title=str(title or ""),
                reason=_reason_of(meta) or "unknown",
                shelf=shelf_of(item_type, kind),
            )
        )
    return rows


def unsearchable_rows(store) -> list[UnsearchableItem]:
    """Every active item persisted :data:`UNSEARCHABLE`, one row each, oldest first.

    Archived items are excluded: the user deliberately put those away, and an attention
    surface that keeps nagging about content someone retired is noise, not attention.
    """
    try:
        cursor = store.db.execute(
            f"SELECT {row_select()} FROM items "  # noqa: S608 — a fixed column list
            "WHERE processing_status = ? AND COALESCE(is_archived, 0) = 0 "
            "ORDER BY created_at, id",
            (UNSEARCHABLE,),
        )
        records = [tuple(r) for r in cursor.fetchall()]
    except Exception:  # noqa: BLE001 — reporting must never break the caller it reports to
        logger.debug("unsearchable inventory read failed", exc_info=True)
        return []
    return rows_from(records)


def degradations_from(rows: Iterable[UnsearchableItem]) -> tuple[Degradation, ...]:
    """Group attention rows into one :class:`Degradation` per typed reason.

    Reason order follows :data:`REASONS` so the same library always reports the same
    order (a set-iteration order would make the search output differ run to run), with
    unknown tokens last rather than dropped. Within a reason, library rows are counted and
    sampled; every other shelf is tallied beside them, never folded into the count.
    """
    listed: dict[str, list[str]] = {}
    unlisted: dict[str, dict[str, int]] = {}
    for row in rows:
        listed.setdefault(row.reason, [])
        if row.shelf == LIBRARY_SHELF:
            listed[row.reason].append(row.item_id)
        else:
            per_shelf = unlisted.setdefault(row.reason, {})
            per_shelf[row.shelf] = per_shelf.get(row.shelf, 0) + 1
    ordered = [r for r in REASONS if r in listed] + sorted(r for r in listed if r not in REASONS)
    return tuple(
        Degradation(
            reason=reason,
            detail=reason_detail(reason),
            item_count=len(listed[reason]),
            item_ids=tuple(listed[reason][:SAMPLE_LIMIT]),
            unlisted=tuple(sorted(unlisted.get(reason, {}).items())),
        )
        for reason in ordered
    )
