"""Item-similarity edges: a resumable kNN pass over the chunk vector index (KL-13).

**What it closes.** The store had no similarity-derived edge of any kind. The chunk vector
index (`KL-11`) was queried at READ time and never materialised into a graph, so every
"related items" surface had to answer the question some other way — in practice an
unthresholded shared-entity COUNT, which ranks two documents as related because they both
happened to mention one common name. This module derives real edges and
`store.similar_items` serves them with a real threshold.

**Where it runs.** On `KL-14`'s deferred maintenance host, never inline on the write path.
Embedding an item's chunks is already the slow part of ingest; adding a kNN sweep over the
whole corpus to it would make every save wait on the library's size. `similarity_pass`
therefore has the host's `batched=True` shape — one bounded batch per call, returning how
many items it claimed so the host can drive it to 0.

**Resume state is the ROWS, not a cursor.** The backlog is a query
(`store.count_items_missing_similarity_sweep` / `store.items_missing_similarity_sweep`), so
an item leaves it the instant its sweep row commits. A killed run resumes by asking again —
nothing to persist and no crash window in which a stranded cursor could disagree with the
data. Same idiom as `link_backfill`, `chunk_backfill` and the ingest queue's
`recover_pending`.

🔴 **Why the backlog is NOT "items with no similarity edges".** That reading looks
equivalent and does not terminate. An item may legitimately have no neighbour above the
cosine floor, so it would never gain an edge, never leave the backlog, and be recomputed on
every tick; because the host re-invokes a batched pass until it returns 0, the head of the
backlog would absorb every sub-batch and the tail would never be reached. This exact defect
was measured on the entity linker (`link_backfill`, KL-14) — drain `[2, 2, 2, 2, ...]`
instead of `[2, 2, 1, 0]`. `item_similarity_edges` records what was FOUND;
`similarity_sweeps` records that the pass LOOKED. The backlog needs the second fact, so the
sweep row is written whether or not any neighbour cleared the floor.

**Known limitation, stated rather than hidden.** A sweep is once-per-item, so an item
ingested LATER does not pull earlier items back into the backlog: the new item's own pass
finds the pair and writes the edge (the edge is symmetric, so one side is enough), but an
earlier item's *stored* score is not refreshed and its top-K is not re-truncated against the
newcomer. Re-chunking an item does re-enter it (`store.replace_chunks` clears the marker),
so the case that actually loses information — an item whose content changed — is covered.
A full re-derivation is `store.clear_similarity_sweep` over the library, which is a
different shape of work.

**Config values are PARAMETERS.** `top_k`, `min_score`, `candidate_multiple` and
`degree_cap` are arguments with module defaults; `_resolve_tuning` is the only place that
reads `AppConfig`, and every field it reads is optional with a documented fallback, so this
module cannot become a live reader of a key nobody writes.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Neighbours kept per item. Small on purpose: the graph is for navigation, and a document
#: with 50 "related" documents has told the reader nothing.
DEFAULT_TOP_K = 8

#: Cosine floor for an edge to exist at all.
#:
#: Deliberately HIGHER than retrieval's `_VECTOR_MIN_SIMILARITY = 0.25`, and the difference
#: is not a disagreement: 0.25 floors a QUERY against a passage, where the query is a short
#: fragment and scores compress downward. Here both sides are full passages, so 0.25 would
#: connect nearly every pair in the library and the graph would be a hairball. ~0.55 is
#: where "the same topic" starts for the sentence-transformer family the embedder defaults
#: to. What IS reused verbatim is retrieval's *application* of a floor: per vector pair,
#: BEFORE the roll-up, so a weak chunk can never become an edge's cited evidence.
DEFAULT_MIN_SCORE = 0.55

#: ANN candidates fetched per chunk, as a multiple of `top_k`. Mirrors retrieval's
#: `_ANN_OVERFETCH = 4`: chunk pairs collapse to item pairs, so k nearest chunks yield
#: strictly fewer than k distinct items and asking for exactly top_k would under-fill.
DEFAULT_CANDIDATE_MULTIPLE = 4

#: GLOBAL maximum edges touching any one item, inbound and outbound. Distinct from `top_k`
#: and not implied by it — see `store.enforce_similarity_degree_cap`.
DEFAULT_DEGREE_CAP = 32

#: Items claimed per `similarity_pass` call. Lower than `link_backfill.BATCH_SIZE` because
#: one item here costs a kNN query per chunk rather than one string scan, and the host
#: relies on the store lock being released between sub-batches.
BATCH_SIZE = 10

#: `candidate_multiple` is deliberately NOT config-mapped: the atom round-trips three knobs
#: (floor, top-K, degree cap) and no config field exists for the overfetch, so mapping it
#: would make this a reader of a key nothing ever writes -- the exact shape this area keeps
#: finding. It stays a module constant.
#:
#: The `KnowledgeConfig` field names this pass reads, in ONE mapping so a rename is one
#: edit. Every one is optional: `_resolve_tuning` falls back to the module default and says
#: so at debug level, which is what keeps this from becoming a live reader of an unwritten
#: key if the config field lands under a different name.
_CONFIG_FIELDS = {
    "top_k": "similarity_top_k",
    "min_score": "similarity_min_score",
    "degree_cap": "similarity_degree_cap",
}

_DEFAULTS: dict[str, Any] = {
    "top_k": DEFAULT_TOP_K,
    "min_score": DEFAULT_MIN_SCORE,
    "candidate_multiple": DEFAULT_CANDIDATE_MULTIPLE,
    "degree_cap": DEFAULT_DEGREE_CAP,
}


def _open_store() -> Any:
    """Open the knowledge store the same way every other reader does.

    Through `knowledge_db_path`, never a locally composed path: composing it produces
    `<home>/knowledge/knowledge.db` while the real store is
    `<home>/workspace/knowledge/knowledge.db`, so the pass would build a graph in a second
    database no surface reads.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _resolve_tuning(**overrides: Any) -> dict[str, Any]:
    """Explicit override, else config, else module default — for each knob independently.

    A small default-resolver and nothing more: it never validates, never writes, and treats
    an absent config field exactly like an unset one. The config round-trip (dataclass +
    `_meta` + `load()` + `to_dict()` + write path + frontend control) is owned elsewhere;
    this is only the read.
    """
    resolved = dict(_DEFAULTS)
    section: Any = None
    try:
        from personalclaw.config.loader import AppConfig

        section = AppConfig.load().knowledge
    except Exception:  # noqa: BLE001 — an unreadable config means defaults, never a crash
        logger.debug("similarity edges: config unavailable, using defaults", exc_info=True)

    for key, field_name in _CONFIG_FIELDS.items():
        if section is not None:
            value = getattr(section, field_name, None)
            if value is not None:
                resolved[key] = value
            else:
                logger.debug(
                    "similarity edges: config field %s absent, using default %r",
                    field_name,
                    resolved[key],
                )
        override = overrides.get(key)
        if override is not None:
            resolved[key] = override

    resolved["top_k"] = max(0, int(resolved["top_k"]))
    resolved["min_score"] = float(resolved["min_score"])
    resolved["candidate_multiple"] = max(1, int(resolved["candidate_multiple"]))
    resolved["degree_cap"] = max(0, int(resolved["degree_cap"]))
    return resolved


def recompute_item_edges(
    store: Any,
    item_id: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    candidate_multiple: int = DEFAULT_CANDIDATE_MULTIPLE,
    degree_cap: int = DEFAULT_DEGREE_CAP,
) -> int:
    """Re-derive *item_id*'s similarity edges. Returns edges written for this item.

    The pipeline, in order:

    1. Read the item's embedded chunk vectors.
    2. Per chunk, ask the ANN index for `top_k * candidate_multiple` nearest chunks — plus
       the item's own chunk count, because a chunk's true nearest neighbours are the OTHER
       CHUNKS OF ITS OWN DOCUMENT (self-similarity ~1.0). Without that headroom a 30-chunk
       document spends its whole candidate budget on itself and finds no neighbour at all.
    3. Score each candidate pair with the store's one cosine (`dedup.cosine_similarity`)
       behind a dimension guard, and drop anything below `min_score` — per pair, before the
       roll-up, so a weak chunk can never become an edge's cited evidence.
    4. **Collapse chunk pairs to item pairs keeping the MAX**, which is the rule the vector
       retrieval arm already uses: the question is "do these two documents share content",
       which is a max over passages. A mean drags a 50-chunk document with one perfect
       passage below a 2-chunk document with two mediocre ones, and a sum just rewards
       length. Max also leaves the score on the identical scale as `min_score`, so the floor
       keeps its calibrated meaning.
    5. Truncate to `top_k` items and upsert in canonical (min, max) id order.
    6. Withdraw this item's claim on the pairs it no longer derives (never another writer's
       claim), then enforce the GLOBAL degree cap on this item and every neighbour touched.

    If the ANN index cannot serve a query it returns `None`, and this falls soft to the exact
    scan over every embedded chunk — the same contract retrieval honours. An unavailable
    index makes the pass slow, never broken, and never wrong: the exact scan is a superset of
    the candidates ANN would have returned.
    """
    from personalclaw.knowledge.dedup import cosine_similarity
    from personalclaw.knowledge.embedder import floats_to_bytes

    mine = [c for c in (store.get_chunks(item_id, with_embedding=True) or []) if c.get("embedding")]
    if not mine or top_k <= 0:
        # Nothing to compare from. Deliberately NOT an error and deliberately still a
        # "looked at it" outcome for the caller — see `similarity_pass`.
        return 0

    # other_item_id -> (best score, winning chunk of THIS item, winning chunk of the other)
    best: dict[str, tuple[float, Any, Any]] = {}

    def _consider(other_item: str, other_index: Any, other_vec: list, mine_chunk: dict) -> None:
        my_vec = mine_chunk["embedding"]
        # Dimension guard, same reasoning as the retrieval arm: a vector from a different
        # embedding model cannot be compared, and cosine over zip() would silently truncate
        # to the shorter and score a meaningless prefix. Such pairs fall out until re-embedded.
        if not other_vec or len(other_vec) != len(my_vec):
            return
        sim = cosine_similarity(my_vec, other_vec)
        if sim < min_score:
            return
        prev = best.get(other_item)
        if prev is None or sim > prev[0]:
            best[other_item] = (sim, mine_chunk.get("chunk_index"), other_index)

    index = getattr(store, "vec_index", None)
    if index is not None and not getattr(index, "enabled", False):
        index = None  # collapse "no index" and "index refused" to one branch
    served = index is not None

    if index is not None:
        # Self-hits are guaranteed, so the budget is (neighbour budget + own chunk count).
        k = max(1, top_k * candidate_multiple) + len(mine)
        for chunk in mine:
            vec = chunk["embedding"]
            candidates = index.candidate_chunk_ids(floats_to_bytes(vec), len(vec), k)
            if candidates is None:
                served = False  # no usable index for this dimension — take the exact scan
                break
            for row in store.chunk_vectors_by_ids(candidates):
                if row["item_id"] == item_id:
                    continue
                _consider(row["item_id"], row["chunk_index"], row["embedding"], chunk)

    if not served:
        # Discard the partial ANN roll-up so the fallback result does not depend on how far
        # the index got before it gave up. The exact scan is a superset, so this loses
        # nothing and makes the two paths return identically.
        best.clear()
        for row in store.iter_embedded_chunks(exclude_item_id=item_id):
            for chunk in mine:
                _consider(row["item_id"], row["chunk_index"], row["embedding"], chunk)

    # Score desc, then id asc so a tie resolves the same way on every run.
    ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))[:top_k]

    rows = [
        {
            "source_item_id": item_id,
            "target_item_id": other_id,
            "score": score,
            "source_chunk_index": my_index,
            "target_chunk_index": other_index,
            # Which pass derived this row. The store turns it into the by_source/by_target
            # claim flag that makes the next clause survivable.
            "claimed_by": item_id,
        }
        for other_id, (score, my_index, other_index) in ranked
    ]

    # 🔴 The non-destructive clause. `release_similarity_claims` withdraws only THIS item's
    # authorship of the pairs it no longer derives; a pair another item's pass vouches for
    # keeps that pass's flag and survives. A plain `DELETE WHERE source_item_id = item_id`
    # cannot express this — canonical ordering means an edge B discovered is stored with A
    # as its source whenever A < B, so that delete destroys B's finding.
    keep = {(min(item_id, other_id), max(item_id, other_id)) for other_id, _ in ranked}
    store.release_similarity_claims(item_id, keep)
    written = store.upsert_similarity_edges(rows)
    # The cap is applied to the neighbours too, not just this item: that is the whole
    # difference between a global cap and the top-K truncation two lines up.
    store.enforce_similarity_degree_cap(
        [item_id, *(other_id for other_id, _ in ranked)], degree_cap
    )
    return written


def count_similarity_backlog() -> int:
    """How many items the similarity pass has never looked at. 0 means the graph is current.

    Exposed so a caller (Health panel, digest, a CLI report) can state the backlog without
    running it. Never raises: an unavailable store reports no backlog, not a crash.
    """
    try:
        store = _open_store()
    except Exception:  # noqa: BLE001 — an unopenable store is not a caller's problem
        logger.debug("similarity edges: knowledge store unavailable", exc_info=True)
        return 0
    try:
        return int(store.count_items_missing_similarity_sweep())
    except Exception:  # noqa: BLE001
        logger.debug("similarity edges: backlog count failed", exc_info=True)
        return 0


def similarity_pass(
    *,
    batch_size: int = BATCH_SIZE,
    top_k: int | None = None,
    min_score: float | None = None,
    candidate_multiple: int | None = None,
    degree_cap: int | None = None,
) -> int:
    """Claim ONE bounded batch of the similarity backlog. Returns items processed; 0 == drained.

    The return value is remaining-work-shaped, which is what `batched=True` means to the
    maintenance host: it re-invokes this until it returns 0 (bounded by `max_batches`), so
    every call must make real progress. It does, because each item's sweep row commits before
    the next item starts.

    Never raises. A scoring hiccup must cost its own item, not the maintenance tick.
    """
    want = max(0, int(batch_size))
    if want <= 0:
        return 0

    try:
        store = _open_store()
    except Exception:  # noqa: BLE001
        logger.debug("similarity edges: knowledge store unavailable", exc_info=True)
        return 0

    tuning = _resolve_tuning(
        top_k=top_k,
        min_score=min_score,
        candidate_multiple=candidate_multiple,
        degree_cap=degree_cap,
    )

    processed = edges = 0
    try:
        # PRECONDITION, not a per-item concern: with fewer than two embedded items there is
        # no pair to score. Sweeping anyway would mint sweep rows for the whole library while
        # finding nothing, and because a sweep is once-per-item those first documents would
        # then never be compared once the library grew. Same shape as
        # `link_backfill._has_entities` refusing to sweep against an empty entity graph.
        if store.count_items_with_embedded_chunks() < 2:
            return 0

        batch = store.items_missing_similarity_sweep(limit=want)
        if not batch:
            return 0

        for item_id in batch:
            if not item_id:
                continue
            try:
                edges += recompute_item_edges(
                    store,
                    item_id,
                    top_k=tuning["top_k"],
                    min_score=tuning["min_score"],
                    candidate_multiple=tuning["candidate_multiple"],
                    degree_cap=tuning["degree_cap"],
                )
            except Exception:  # noqa: BLE001 — one bad item must not end the sweep
                logger.debug("similarity edges: recompute failed for %s", item_id, exc_info=True)

            # OUTSIDE the recompute, deliberately: the sweep row records that the pass
            # LOOKED, and it must land whether that found no neighbour or raised. Inside the
            # try, a permanently-failing item would be re-claimed on every tick and starve
            # every item behind it — the same non-termination the sweep table exists to
            # prevent, reached by the error path instead. Its own statement too, so a kill
            # mid-batch keeps the items already swept out of the next run's backlog.
            #
            # `processed` counts items that LEFT the backlog, not items looked at: if the
            # marker cannot be written the store is unwritable, and reporting progress the
            # rows contradict would make the host re-claim the same batch `max_batches` times.
            try:
                store.record_similarity_sweep(item_id)
            except Exception:  # noqa: BLE001
                logger.debug("similarity edges: sweep marker failed for %s", item_id, exc_info=True)
                continue
            processed += 1
    except Exception:  # noqa: BLE001 — a fault leaves the graph exactly as it was
        logger.debug("similarity edges: batch failed", exc_info=True)
        return 0

    if processed:
        logger.debug("similarity edges: swept %d item(s), writing %d edge(s)", processed, edges)
    return processed
