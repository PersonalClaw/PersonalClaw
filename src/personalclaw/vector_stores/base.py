"""The external vector-store backend seam (KBVS-1) — contract only, zero vendor code.

WHAT THIS IS. The seam that lets knowledge vector search run against a store the user
owns and runs — Qdrant, pgvector, Chroma — instead of the bundled ``sqlite-vec``/``vec0``
index. A provider app implements :class:`VectorStoreProvider`; core never learns the
vendor's name. The whole vendor surface (client library, endpoint shape, auth, collection
DDL) lives in the app bundle, which is what
``docs/architecture/provider-boundary.md`` requires: a Qdrant REST dialect is not a
de-facto multi-vendor protocol the way ``/v1/chat/completions`` is, so it earns no
core exception.

WHAT IS EXTERNALIZED, EXACTLY. The **chunk** vector search, and nothing else:

* **Chunk vectors** are mirrored into the external store as they are written, and when a
  backend is bound it — not ``vec0`` — answers the chunk half of the vector arm.
* **Whole-item vectors** (the title+summary document vector) stay local. They are one
  vector per item with no roll-up and no index to keep in step, so externalizing them
  buys nothing and would double the surface that can be unreachable.
* **Chunk ROWS stay local.** The external store is an INDEX, not the record store. The
  ``chunks`` table keeps the text, the section/line locator, the embedding BLOB and the
  RET-4 fingerprint, because re-chunking, re-embedding, staleness detection and the
  Doctor all read them. Externalizing the *search* is the whole ask; externalizing the
  record would make an unreachable store a data-loss event instead of a degradation.

SCORING HONESTY. ``vec0`` is a candidate generator only — ``HybridRetriever`` re-scores
its candidates with core's own cosine, so exact and ANN "cannot disagree on a similarity
value". An external store cannot be held to that: the point of pointing search at Qdrant
is that Qdrant does the distance computation. So the split is drawn where it is testable:

* the **backend** owns candidate generation AND the cosine, and must return hits in
  DESCENDING similarity (the walk's stop rule depends on it);
* **core** owns the ``_VECTOR_MIN_SIMILARITY`` floor, the MAX roll-up to the parent item,
  the archived/active/fingerprint liveness filter and the rank hand-off to RRF.

So the floor keeps its calibrated meaning and the fused arm keeps its exact shape; the
only thing that moved is who multiplies the vectors. A backend returning a metric that is
not cosine similarity in ``[-1, 1]`` will be silently mis-floored — which is why
:meth:`VectorStoreProvider.describe` exists and why a backend is expected to normalize.

FAIL SOFT, NEVER SUBSTITUTE. A bound-but-unreachable backend makes the chunk arm return
NOTHING, logged at WARNING. It does **not** quietly fall back to the local ``vec0``
index. Answering from a shadow local index while the store the user aimed at is down is
the silent-wrong-recall defect in another costume: the user would get plausible hits from
an index they did not choose and no way to tell. The whole-item vector arm, FTS5 and the
graph arm still answer, so a search degrades in recall and never fails — the same
capability-degradation shape as ``MemoryService``'s fallback chain
(``memory_service.py``), where degradation is an explicit named path rather than a
fiction inside one class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "VectorRecord",
    "VectorHit",
    "VectorStoreInfo",
    "VectorStoreProvider",
]


@dataclass(frozen=True)
class VectorRecord:
    """One chunk vector to index. ``chunk_id`` is the store's own primary key, so an
    upsert is idempotent and a re-chunk (which mints fresh chunk ids) is a delete of the
    item followed by an insert of its new ids, never a partial overwrite."""

    chunk_id: str
    item_id: str
    chunk_index: int
    vector: list[float]
    section: str = ""
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class VectorHit:
    """One candidate from the backend.

    ``similarity`` is COSINE similarity in ``[-1, 1]``, higher is nearer — not a distance
    and not a vendor-scaled score. A backend whose engine returns a distance converts it;
    core applies its calibrated floor to this number and cannot tell the difference.

    ``item_id`` is advisory: core resolves the authoritative parent item, the section and
    the line range from the local ``chunks`` row keyed by ``chunk_id``, so a backend whose
    payload has drifted cannot mis-attribute a hit. A hit whose ``chunk_id`` no longer
    joins to a live, active, in-scope chunk row is dropped — the same harmless-extra rule
    the ``vec0`` reader already applies.
    """

    chunk_id: str
    item_id: str
    similarity: float


@dataclass(frozen=True)
class VectorStoreInfo:
    """What a bound backend reports about itself — the Doctor/diagnostics surface.

    ``detail`` carries the human sentence (including the failure reason when
    ``reachable`` is False) and MUST NOT contain a secret: it is rendered in the UI and
    written to logs. The credential itself lives in the credential store and is resolved
    inside the app; core never sees it.
    """

    backend: str
    collection: str
    dimension: int | None = None
    count: int | None = None
    reachable: bool = False
    detail: str = ""


class VectorStoreProvider(ABC):
    """An external chunk-vector index contributed by an app (``provider.type ==
    "vector_store"``).

    Lifecycle: the app's factory builds one instance from its own
    ``ProviderSettings``-backed config (host/port/collection; any secret resolved from the
    credential store, never from ``config.json``). Enabling exactly one such provider IS
    the binding — there is no separate core config field to keep in step, so "which store
    is knowledge searching" has one answer and cannot drift from "which app is enabled".

    Every method must be safe to call on an unreachable backend: raise, or return
    empty/zero. Core treats an exception as "this arm cannot answer this query" and never
    lets it reach the caller of a search.
    """

    #: Registry key. Defaults to the app name when left empty.
    name: str = ""

    @abstractmethod
    def upsert(self, records: Sequence[VectorRecord]) -> int:
        """Index *records*, replacing any rows with the same ``chunk_id``. Returns the
        count written. An empty sequence is a legal no-op returning 0 — callers do not
        pre-check, so a document that produced no embedded chunks must not error."""

    @abstractmethod
    def delete_item(self, item_id: str) -> int:
        """Drop every vector belonging to *item_id*. Returns the count removed (or 0 when
        the backend cannot report one). Must be idempotent: deleting an item the store
        never held is a no-op, because the knowledge store calls this before a re-chunk
        whether or not anything was ever indexed."""

    @abstractmethod
    def query(self, vector: Sequence[float], *, k: int) -> list[VectorHit]:
        """The *k* nearest chunk vectors to *vector*, **sorted by descending
        similarity**.

        The ordering is load-bearing, not a convenience: core stops walking at the first
        hit below its floor, on the argument that every later hit is also below it. A
        backend that returns hits out of order will silently truncate its own recall.

        Core over-fetches (``k`` is a multiple of the requested result limit) because
        several chunks roll up to one item, and re-asks with a larger ``k`` if the
        surviving candidate set is too small.
        """

    @abstractmethod
    def describe(self) -> VectorStoreInfo:
        """Reachability + shape, for diagnostics. Must not raise: report
        ``reachable=False`` with the reason in ``detail`` instead."""
