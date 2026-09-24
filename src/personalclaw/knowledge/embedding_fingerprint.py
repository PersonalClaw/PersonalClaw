"""Which embedding model produced a stored chunk vector (RET-4).

🔴 WHY THIS EXISTS. The dimension case was already handled and is deliberately NOT
rebuilt here: :mod:`personalclaw.knowledge.vector_index` partitions the ANN index per
embedding dimension with the dimension in the table *name* (``chunk_vec_384``), and
``HybridRetriever._consider`` skips any stored vector whose length differs from the live
query vector. Together those make a 384→768 model swap safe: the old vectors become
unscoreable and fall back to keyword/graph retrieval.

Neither can see the case this module exists for — **two DIFFERENT models at the SAME
dimension**. `all-MiniLM-L6-v2`, `bge-small-en`, `gte-small` and `e5-small` are all 384
dimensions and all mutually incomparable: a vector from one scored against a query from
another passes every guard in the product and returns a number that means nothing. Before
this module ``chunks`` carried ``id, item_id, chunk_index, text, embedding, section,
line_start, line_end`` — no model column anywhere — so nothing in the system could tell
the difference between "this vector is comparable" and "this vector is from another
model's space".

**The contract.** A chunk row records the embedding selection that was active when its
vector was written. A vector is comparable iff that recorded fingerprint equals the
selection active NOW. One accessor (:func:`active_fingerprint`) is read by the write path,
the query path, the re-index and the Doctor, so the fingerprint a writer stamps and the
fingerprint a reader compares against can never drift.

**Nothing bound is not staleness.** :func:`active_fingerprint` returns ``None`` when no
embedding model is selected, and every caller then treats staleness as inapplicable — the
vector arm is not running at all in that state, and "no embedding provider" is already
RET-2's named reason (:mod:`personalclaw.knowledge.searchability`). Reporting a stale
index when the real fact is an unbound provider would be a second reason for one state,
which is the drift RET-2's single-vocabulary rule exists to prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "EmbeddingFingerprint",
    "active_fingerprint",
    "has_fingerprint_columns",
    "count_stale_chunks",
    "stale_chunk_items",
]

#: The two chunk columns that carry the fingerprint. Named once so the schema block, the
#: migration, the write path and the read-only Doctor probe cannot spell them differently.
FINGERPRINT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("embedding_model_id", "TEXT"),
    ("embedding_provider", "TEXT"),
)

#: The staleness predicate over a ``chunks`` alias ``c``, as SQL. A NULL fingerprint is
#: STALE, not "assume fine": a chunk written before this column existed (or written while
#: nothing was bound) has an unknown provenance, and scoring an unknown-provenance vector
#: against a bound model is exactly the silent comparison this atom removes. Takes two
#: parameters — the active model id and provider, in that order.
STALE_PREDICATE = (
    "(COALESCE(c.embedding_model_id, '') != ? OR COALESCE(c.embedding_provider, '') != ?)"
)

#: The matching FRESH predicate, used by the retrieval arms to exclude stale rows in SQL
#: rather than decoding their BLOBs and dropping them in Python.
FRESH_PREDICATE = (
    "(COALESCE(c.embedding_model_id, '') = ? AND COALESCE(c.embedding_provider, '') = ?)"
)


@dataclass(frozen=True)
class EmbeddingFingerprint:
    """The identity of an embedding model: its id plus the provider that served it.

    Both halves are load-bearing. Two providers can expose the same model id over
    different weights or a different pooling/normalization pipeline (a locally-downloaded
    ``all-MiniLM-L6-v2`` and a hosted endpoint advertising the same name are not
    guaranteed to produce the same vector), so the provider is part of the identity and
    not decoration.
    """

    model_id: str
    provider: str

    @property
    def params(self) -> tuple[str, str]:
        """The two bind parameters :data:`STALE_PREDICATE` / :data:`FRESH_PREDICATE` want."""
        return (self.model_id, self.provider)

    def to_dict(self) -> dict[str, str]:
        return {"embedding_model_id": self.model_id, "embedding_provider": self.provider}

    def __str__(self) -> str:  # the label a log line / job frame / Doctor row shows
        return f"{self.provider}:{self.model_id}" if self.provider else self.model_id


def active_fingerprint() -> Optional[EmbeddingFingerprint]:
    """The embedding selection active right now, or ``None`` when nothing is bound.

    Reads the same ``embedding`` use-case selection ``UnifiedEmbedder.model_name`` reads,
    through the same accessor, so a chunk's stamped fingerprint and the fingerprint a
    query compares it against come from one place.
    """
    try:
        from personalclaw.embedding_providers.registry import _active_embedding_spec

        spec = _active_embedding_spec()
    except Exception:  # noqa: BLE001 — an unresolvable selection is "nothing bound"
        logger.debug("embedding fingerprint: active selection unresolvable", exc_info=True)
        return None
    if not spec:
        return None
    provider, model_id = spec
    if not model_id:
        return None
    return EmbeddingFingerprint(model_id=str(model_id), provider=str(provider or ""))


def has_fingerprint_columns(conn: Any) -> bool:
    """Does this ``chunks`` table carry the fingerprint columns?

    Needed because the Doctor opens ``knowledge.db`` ``mode=ro`` and therefore cannot run
    the migration the live store runs on open. A read-only reader on a database written by
    an older build must report "cannot tell" rather than raise.
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    except Exception:  # noqa: BLE001 — a probe must never raise into its caller
        return False
    return all(col in cols for col, _decl in FINGERPRINT_COLUMNS)


def count_stale_chunks(conn: Any, fingerprint: EmbeddingFingerprint) -> int:
    """How many chunk vectors were written by a model other than *fingerprint*.

    Counts only rows that actually carry a vector: an un-embedded chunk is not scoreable
    by either retrieval path, so calling it stale would inflate the number a user is asked
    to act on with rows a re-index cannot fix by re-embedding.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks c "
        f"WHERE c.embedding IS NOT NULL AND {STALE_PREDICATE}",  # noqa: S608 — fixed literal
        fingerprint.params,
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def stale_chunk_items(
    conn: Any, fingerprint: EmbeddingFingerprint, *, include_archived: bool = False
) -> list[tuple[str, str]]:
    """``(item_id, title)`` for every ACTIVE item holding at least one stale chunk vector.

    One row per ITEM, not per chunk: the attention surfaces (search degradations, the
    Doctor row) name documents a user can act on, and a 60-chunk document would otherwise
    drown a 1-chunk one out of the sample.
    """
    archived = "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0 "
    rows = conn.execute(
        "SELECT i.id, i.title FROM items i JOIN chunks c ON c.item_id = i.id "
        f"WHERE c.embedding IS NOT NULL AND i.status = 'active' {archived}"
        f"AND {STALE_PREDICATE} "  # noqa: S608 — clauses are fixed literals
        "GROUP BY i.id ORDER BY MIN(i.created_at), i.id",
        fingerprint.params,
    ).fetchall()
    return [(str(r[0]), str(r[1] or "")) for r in rows]


def stale_rows(records: Iterable[tuple[str, str]]) -> list:
    """Attention rows for :func:`stale_chunk_items` output, in RET-2's row shape.

    Lives here rather than in ``searchability`` so the reason token and the query that
    finds its subjects sit together; the ROW TYPE and the grouping stay RET-2's.
    """
    from personalclaw.knowledge.searchability import STALE_INDEX, UnsearchableItem

    return [
        UnsearchableItem(item_id=item_id, title=title, reason=STALE_INDEX)
        for item_id, title in records
    ]
