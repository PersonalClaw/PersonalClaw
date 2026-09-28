"""Which embedding model produced a stored chunk vector.

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

**The same rule for every vector the library compares — memory's.** An item records the
fingerprint of its whole-item vector in the same two columns. A query's vector is compared only
with the fresh ones (:data:`ITEM_FRESH_PREDICATE`: search's whole-item arm, the structural rank),
and two stored vectors only when both record the same model (:meth:`EmbeddingFingerprint.recorded`:
dedup, and the similarity edges between two items' passages). Vectors that record no model are one
space of their own, as memory treats the vectors that name none: comparable with each other, and
with a model bound, stale.

**Nothing bound is not staleness.** :func:`active_fingerprint` returns ``None`` when no
embedding model is selected, and every caller then treats staleness as inapplicable — the
vector arm is not running at all in that state, and "no embedding provider" is already
The named reason (:mod:`personalclaw.knowledge.searchability`). Reporting a stale
index when the real fact is an unbound provider would be a second reason for one state,
which is the drift the single-vocabulary rule exists to prevent.
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
    "stale_vector_items",
]

#: The two chunk columns that carry the fingerprint. Named once so the schema block, the
#: migration, the write path and the read-only Doctor probe cannot spell them differently.
FINGERPRINT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("embedding_model_id", "TEXT"),
    ("embedding_provider", "TEXT"),
)


def fingerprint_predicate(alias: str, *, fresh: bool) -> str:
    """The SQL predicate saying a row's recorded fingerprint is (``fresh``) or is not the one
    its two parameters name — the model id, then the provider — over the table ``alias`` names
    (``""`` for an unaliased one). The constants below are this predicate; a query over another
    alias builds its own here, so no spelling of the rule can drift from another."""
    col = f"{alias}." if alias else ""
    op, join = ("=", "AND") if fresh else ("!=", "OR")
    return (
        f"(COALESCE({col}embedding_model_id, '') {op} ? {join} "
        f"COALESCE({col}embedding_provider, '') {op} ?)"
    )


#: The staleness predicate over a ``chunks`` alias ``c``, as SQL. A NULL fingerprint is
#: STALE, not "assume fine": a chunk written before this column existed (or written while
#: nothing was bound) has an unknown provenance, and scoring an unknown-provenance vector
#: against a bound model is exactly the silent comparison this change removes. Takes two
#: parameters — the active model id and provider, in that order.
STALE_PREDICATE = fingerprint_predicate("c", fresh=False)

#: The matching FRESH predicate, used by the retrieval arms to exclude stale rows in SQL
#: rather than decoding their BLOBs and dropping them in Python.
FRESH_PREDICATE = fingerprint_predicate("c", fresh=True)

#: :data:`STALE_PREDICATE` over an ``items`` row, unaliased: its whole-item vector was written by
#: another model, or before the model was recorded. An item carries the same two columns, so the
#: re-index re-embeds only the items the model bound now has not embedded, as it does chunks.
ITEM_STALE_PREDICATE = fingerprint_predicate("", fresh=False)

#: :data:`FRESH_PREDICATE` over an unaliased ``items`` row: the whole-item vector is one a query
#: embedded by the model named can be compared with. Search's whole-item arm and the structural
#: rank read only these, as the chunk arm reads only fresh chunks.
ITEM_FRESH_PREDICATE = fingerprint_predicate("", fresh=True)


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

    @classmethod
    def recorded(cls, model_id: Any, provider: Any) -> "EmbeddingFingerprint":
        """The fingerprint a row RECORDS for its vector, as the predicates read it (``COALESCE``
        to ``''``). A vector that records no model is in the ``('', '')`` space: comparable with
        the others that record none, never with a model's — memory's rule for the vectors that
        name no model (``vector_memory._OF_MODEL``). :func:`active_fingerprint` is never that
        space, so with a model bound a query is compared with none of them."""
        return cls(model_id=str(model_id or ""), provider=str(provider or ""))

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


def has_fingerprint_columns(conn: Any, table: str = "chunks") -> bool:
    """Does this ``chunks`` (or ``items``) table carry the fingerprint columns?

    Needed because the Doctor opens ``knowledge.db`` ``mode=ro`` and therefore cannot run
    the migration the live store runs on open. A read-only reader on a database written by
    an older build must report "cannot tell" rather than raise. Items gained the columns
    after chunks did, so a database can carry them on one table and not the other.
    """
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
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


def stale_vector_items(
    conn: Any, fingerprint: EmbeddingFingerprint, *, include_archived: bool = False
) -> list[tuple[str, str, str, str]]:
    """``(item_id, title, item_type, kind)`` for every ACTIVE item holding a stale vector: a
    passage (chunk) vector, or its whole-item vector, that the model *fingerprint* names did not
    write. Search skips both (the chunk arm and the whole-item arm compare fresh vectors only), so
    both are what a search could not compare, and the item is named once either way.

    One row per ITEM, not per chunk: the attention surfaces (search degradations, the
    Doctor row) name documents a user can act on, and a 60-chunk document would otherwise
    drown a 1-chunk one out of the sample. ``item_type`` and ``kind`` ride along so the
    row can say which shelf it belongs to — an artifact's mirror is not a library item.
    (Reading ``kind`` unguarded is safe here: callers reach this only past
    :func:`has_fingerprint_columns`, and the fingerprint columns are far younger than it.)
    An ``items`` table a ``mode=ro`` reader finds without the columns records no model for any
    whole-item vector, so each of those is stale, which is the true statement.
    """
    archived = "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0 "
    params: tuple[str, ...] = fingerprint.params
    if has_fingerprint_columns(conn, "items"):
        item_stale = fingerprint_predicate("i", fresh=False)
        params = (*params, *fingerprint.params)
    else:
        item_stale = "1"
    rows = conn.execute(
        "SELECT i.id, i.title, i.item_type, i.kind FROM items i "
        f"WHERE i.status = 'active' {archived}AND ("
        "EXISTS (SELECT 1 FROM chunks c WHERE c.item_id = i.id AND c.embedding IS NOT NULL "
        f"AND {STALE_PREDICATE}) "  # noqa: S608 — clauses are fixed literals
        f"OR (i.embedding IS NOT NULL AND {item_stale})) "
        "ORDER BY i.created_at, i.id",
        params,
    ).fetchall()
    return [(str(r[0]), str(r[1] or ""), str(r[2] or ""), str(r[3] or "")) for r in rows]


def stale_rows(records: Iterable[tuple[str, str, str, str]]) -> list:
    """Attention rows for :func:`stale_vector_items` output, in RET-2's row shape.

    Lives here rather than in ``searchability`` so the reason token and the query that
    finds its subjects sit together; the ROW TYPE, the shelf rule and the grouping stay
    RET-2's.
    """
    from personalclaw.knowledge.searchability import STALE_INDEX, UnsearchableItem, shelf_of

    return [
        UnsearchableItem(
            item_id=item_id, title=title, reason=STALE_INDEX, shelf=shelf_of(item_type, kind)
        )
        for item_id, title, item_type, kind in records
    ]
