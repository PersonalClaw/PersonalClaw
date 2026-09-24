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
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

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

#: One human sentence per reason — what happened, and what the user can do about it.
#: Shared so the Doctor row, the item's ``processing_error`` and the ``knowledge_search``
#: note all say the same thing in the same words.
REASON_DETAIL: dict[str, str] = {
    NO_EXTRACTABLE_TEXT: (
        "no text could be extracted from this document (a scan or image-only PDF), so "
        "only its filename and shape were stored — searching its visible words cannot "
        "find it. Install an OCR app or bind a vision model, then re-ingest it; or add a "
        "text version."
    ),
    NO_EMBEDDING_PROVIDER: (
        "no embedding provider is bound, so this item has no vector and no chunks — "
        "keyword search can still reach it, semantic search cannot. Bind an embedding "
        "model in Settings → Providers, then re-index."
    ),
    NOT_INDEXED: (
        "the embedding step produced no vector for this item, so it is missing from the "
        "chunk index — semantic search cannot reach it. Re-ingest it, and check the "
        "embedding provider's health in Doctor."
    ),
    STALE_INDEX: (
        "this item's passage vectors were written by a different embedding model than the "
        "one bound now, so they cannot be compared to your query — semantic search skips "
        "them rather than scoring them against the wrong model. Run the embedding "
        "re-index (Settings → Models) to rebuild them; nothing needs re-ingesting."
    ),
}

#: How many item ids a report carries as a sample. The COUNT is always exact; the id list
#: is a sample so a library with thousands of unsearchable items still produces a readable
#: row (and a bounded response).
SAMPLE_LIMIT = 5


def reason_detail(reason: str) -> str:
    """The human sentence for *reason*, or a legible fallback for an unknown token."""
    return REASON_DETAIL.get(reason) or f"not searchable ({reason or 'unknown reason'})"


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
    """One row on an attention surface: an item that persisted and cannot be found."""

    item_id: str
    title: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "title": self.title, "reason": self.reason}


@dataclass(frozen=True)
class Degradation:
    """The typed answer a search gives instead of a bare empty result set.

    ``item_count`` is exact; ``item_ids`` is capped at :data:`SAMPLE_LIMIT`.
    """

    reason: str
    detail: str
    item_count: int
    item_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "item_count": self.item_count,
            "item_ids": list(self.item_ids),
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


def rows_from(records: Iterable[tuple[str, str, Any]]) -> list[UnsearchableItem]:
    """Build attention rows from ``(id, title, file_metadata)`` triples.

    Split out from the two readers below (the live store and the Doctor's read-only
    sqlite connection) so both produce identical rows from identical data — the reason
    resolution cannot drift between the surface a user sees and the one a test asserts.
    """
    rows: list[UnsearchableItem] = []
    for item_id, title, meta in records:
        rows.append(
            UnsearchableItem(
                item_id=str(item_id),
                title=str(title or ""),
                reason=_reason_of(meta) or "unknown",
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
            "SELECT id, title, file_metadata FROM items "
            "WHERE processing_status = ? AND COALESCE(is_archived, 0) = 0 "
            "ORDER BY created_at, id",
            (UNSEARCHABLE,),
        )
        records = [(r[0], r[1], r[2]) for r in cursor.fetchall()]
    except Exception:  # noqa: BLE001 — reporting must never break the caller it reports to
        logger.debug("unsearchable inventory read failed", exc_info=True)
        return []
    return rows_from(records)


def degradations_from(rows: Iterable[UnsearchableItem]) -> tuple[Degradation, ...]:
    """Group attention rows into one :class:`Degradation` per typed reason.

    Reason order follows :data:`REASONS` so the same library always reports the same
    order (a set-iteration order would make the search output differ run to run), with
    unknown tokens last rather than dropped.
    """
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row.reason, []).append(row.item_id)
    ordered = [r for r in REASONS if r in grouped] + sorted(r for r in grouped if r not in REASONS)
    return tuple(
        Degradation(
            reason=reason,
            detail=reason_detail(reason),
            item_count=len(grouped[reason]),
            item_ids=tuple(grouped[reason][:SAMPLE_LIMIT]),
        )
        for reason in ordered
    )
