"""HybridRetriever -- FTS5 keyword + graph + optional vector, fused with RRF."""

import logging
import math
import re
import struct
from collections import defaultdict

from personalclaw.sqlite_compat import sqlite3

from .embedder import floats_to_bytes
from .store import KnowledgeStore

logger = logging.getLogger(__name__)

# Relevance-cliff cutoff: walking the score-sorted results, stop at the first
# point where the score drops by more than this fraction of the running top
# score — the "cliff" between the relevant cluster and the long tail of weak
# matches. Returns the natural cluster instead of a fixed top-K (which either
# pads with weak hits or truncates a strong run). 0.30 is OpenForge's empirical
# value (§K5); tune from real queries.
_RELEVANCE_CLIFF_GAP = 0.30
_CLIFF_MIN_RESULTS = 1  # never cut below this when any match exists

# Minimum cosine similarity for a vector hit to count. Vector search otherwise always
# returns its top-K regardless of how weak the match is, so a precise keyword/tag query
# gets polluted with near-orthogonal semantic "neighbors". Unrelated text on
# all-MiniLM-L6-v2 scores well below this; genuine semantic matches clear it.
_VECTOR_MIN_SIMILARITY = 0.25

# Title-match boost, in RRF-score units. RRF contributions are ~1/(60+rank) ≈ 0.016
# per list, so a boost of one rank-step lets a full title match overtake a long document
# that merely mentions the query terms. Scaled by query-term-in-title overlap fraction.
_TITLE_BOOST = 1.0 / 61

# ── ANN candidate budget (KL-11) ───────────────────────────────────────────────
# The chunk ANN index returns the k nearest CHUNKS, but the arm ranks ITEMS, and several of
# an item's chunks can occupy the top of that list — so k chunks can collapse to far fewer
# than k items. Over-fetch, then escalate while the surviving item set is still short of the
# requested limit — the loop's own stop rule (below) usually ends it on the first attempt by
# proving the candidate set complete. This is the one place ANN can lose recall against the
# exact scan (candidate truncation — the scoring itself is shared), so the budget is generous.
_ANN_OVERFETCH = 4  # k = limit × this on the first attempt
_ANN_ESCALATION_FACTOR = 4
_ANN_MAX_ATTEMPTS = 4  # so k reaches limit × 256 before giving up on a pathological corpus

# SQLite's bound-parameter ceiling is 999 on older builds, so candidate ids are re-fetched in
# batches rather than one giant IN-list that would raise on exactly the escalated queries
# where the extra recall matters most.
_ID_BATCH = 400


def relevance_cliff_cut(
    scores: list[float],
    *,
    min_results: int = _CLIFF_MIN_RESULTS,
    max_results: int | None = None,
    gap: float = _RELEVANCE_CLIFF_GAP,
) -> int:
    """Return how many leading results to keep, cutting at the relevance cliff.

    ``scores`` must be sorted descending. Walks consecutive pairs and cuts before
    the first where the drop exceeds ``gap`` × (top score) — the elbow between
    the relevant cluster and the weak tail. The result is clamped to
    ``[min_results, max_results or len(scores)]``; a degenerate top score of 0
    (no signal) keeps everything up to the cap. Pure + side-effect-free so the
    cutoff is unit-testable apart from the DB-backed ranking path.
    """
    n = len(scores)
    cap = n if max_results is None else min(max_results, n)
    if n <= 1:
        return cap
    top = scores[0]
    if top <= 0:
        return cap
    threshold = gap * top
    cut = n
    for i in range(1, n):
        if scores[i - 1] - scores[i] > threshold:
            cut = i
            break
    return max(min(min_results, cap), min(cut, cap))


class HybridRetriever:
    """FTS5 keyword + graph traversal + optional vector search, fused with RRF."""

    def __init__(self, store: KnowledgeStore, embedder=None):
        """store: KnowledgeStore instance. embedder: optional callable(str) -> list[float]."""
        self.store = store
        self.embedder = embedder

    def search(self, query: str, limit: int = 10, *, include_archived: bool = False) -> list[dict]:
        """Hybrid search with RRF fusion. Returns [{id, title, summary, content, score, source, match_type}].  # noqa: E501

        ``include_archived`` defaults False — archived items never surface to agents or
        chat context-injection. The Archived UI view sets it True so a search *within*
        that view can find archived items (matching the no-query Archived list).
        """
        over = limit * 2
        kw = self._keyword_search(query, limit=over, include_archived=include_archived)
        gr = self._graph_search(query, limit=over, include_archived=include_archived)
        # chunk_locs collects, per item, the span of the chunk whose vector won for it —
        # filled by the vector arm as a by-product of its roll-up so the ranked list it
        # hands to fusion stays exactly [(item_id, rank)].
        chunk_locs: dict[str, dict] = {}
        vec = self._vector_search(
            query, limit=over, include_archived=include_archived, chunk_locators=chunk_locs
        )

        fused = self._rrf_fuse(kw, gr, vec)

        # Batch-fetch all candidate items once
        all_ids = [item_id for item_id, _ in fused]
        items_cache: dict[str, dict] = {}
        for item_id in all_ids:
            item = self.store.get_item(item_id)
            if item:
                items_cache[item_id] = item

        # Title-match boost: BM25 over the full corpus favors a long document with many
        # term occurrences over a short item whose TITLE is the query — yet a title match
        # is one of the strongest relevance signals a user expects. Add a boost scaled by
        # the fraction of query terms found in the title (full on a near-exact match), on
        # the order of one RRF rank step (~1/(k+1)), so a titled item out-ranks a doc that
        # merely mentions the terms in passing.
        q_terms = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}
        if q_terms:
            boosted = []
            for iid, sc in fused:
                title = (items_cache.get(iid, {}).get("title") or "").lower()
                t_terms = {t for t in re.findall(r"[a-z0-9]+", title) if len(t) > 1}
                if t_terms:
                    overlap = len(q_terms & t_terms) / len(q_terms)
                    sc += overlap * _TITLE_BOOST
                boosted.append((iid, sc))
            fused = boosted

        # Tie-break by recency (newer docs win)
        def _sort_key(item_score: tuple[str, float]) -> tuple[float, str]:
            item_id, score = item_score
            updated = items_cache.get(item_id, {}).get("updated_at", "")
            return (score, updated)

        fused.sort(key=_sort_key, reverse=True)

        # Relevance-cliff cutoff: keep the natural cluster of strong matches
        # instead of a fixed top-K, bounded by the caller's limit. A query with
        # one clearly-best hit returns just that; a broad query returns the whole
        # relevant run (up to limit).
        keep = relevance_cliff_cut([score for _, score in fused], max_results=limit)

        # Track which lists each item appeared in
        kw_ids = {i for i, _ in kw}
        gr_ids = {i for i, _ in gr}
        vec_ids = {i for i, _ in (vec or [])}

        results = []
        for item_id, score in fused[:keep]:
            item = items_cache.get(item_id)
            if not item:
                continue
            types = []
            if item_id in kw_ids:
                types.append("keyword")
            if item_id in gr_ids:
                types.append("graph")
            if item_id in vec_ids:
                types.append("vector")
            results.append(
                {
                    "id": item_id,
                    "title": item["title"],
                    "summary": item.get("summary"),
                    "content": item["content"],
                    "score": score,
                    "provider": item.get("provider", "native"),
                    "match_type": "+".join(types),
                    # P12: citation locator (source_type/section/line_range/deep_link),
                    # derived from the item's own content + the query terms already computed
                    # above, narrowed to the winning chunk's passage when the vector arm
                    # rolled one up for this item.
                    **_attach_locator(item, q_terms, chunk_locs.get(item_id)),
                }
            )
        return results

    def _keyword_search(
        self, query: str, limit: int = 20, *, include_archived: bool = False
    ) -> list[tuple[str, int]]:
        """FTS5 search. Returns [(item_id, rank)] where rank is position (1=best)."""
        safe_query = self._sanitize_fts5_query(query)
        if not safe_query:
            return []
        archived_clause = "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0 "
        try:
            rows = self.store.db.execute(
                "SELECT i.id FROM items_fts fts "
                "JOIN items i ON i.rowid = fts.rowid "
                "WHERE items_fts MATCH ? AND i.status = 'active' "
                f"{archived_clause}ORDER BY fts.rank LIMIT ?",  # noqa: S608,E501 (clause is a fixed literal)
                (safe_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(row["id"], rank + 1) for rank, row in enumerate(rows)]

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """Escape user input for FTS5 MATCH, OR-ing prefix-matched terms.

        OR (not the default implicit AND) so a conversational query
        ("how do we store refresh tokens") still matches docs that contain only
        some terms — RRF + rank then float the best overlap to the top. Each term
        is a ``"term"*`` prefix match so "token" also hits "tokens"/"tokenize".
        """
        terms = [t.replace('"', '""') for t in query.split() if t]
        return " OR ".join(f'"{t}"*' for t in terms)

    def _graph_search(
        self, query: str, limit: int = 20, *, include_archived: bool = False
    ) -> list[tuple[str, int]]:
        """Find entities matching query terms, traverse graph, rank items by mention count."""
        words = query.split()
        # Match entity names at several granularities: individual words, consecutive
        # pairs/triples, AND the full query — so a multi-word entity name like
        # "MAPLE Payments team" or "Distributed Tracing" is found, not just its words.
        candidates = list(words)
        for size in (2, 3):
            for i in range(len(words) - size + 1):
                candidates.append(" ".join(words[i : i + size]))
        if len(words) > 1:
            candidates.append(query.strip())

        entity_ids = set()
        for term in candidates:
            ent = self.store.find_entity(term)
            if ent:
                entity_ids.add(ent["id"])

        if not entity_ids:
            return []

        # Expand via graph neighbors (depth=2)
        all_entity_ids = set(entity_ids)
        for eid in entity_ids:
            for neighbor in self.store.get_neighbors(eid, depth=2):
                all_entity_ids.add(neighbor["id"])

        # Count item mentions (active items; archived hidden by default — matching the
        # default list semantics — unless the Archived view asked to include them).
        item_counts: dict[str, int] = defaultdict(int)
        placeholders = ",".join("?" * len(all_entity_ids))
        archived_clause = "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0 "
        rows = self.store.db.execute(
            f"SELECT m.item_id, COUNT(*) as cnt FROM mentions m "  # noqa: S608
            f"JOIN items i ON i.id = m.item_id "
            f"WHERE m.entity_id IN ({placeholders}) AND i.status = 'active' "
            f"{archived_clause}"
            f"GROUP BY m.item_id ORDER BY cnt DESC LIMIT ?",
            (*all_entity_ids, limit),
        ).fetchall()
        for row in rows:
            item_counts[row["item_id"]] = row["cnt"]

        sorted_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)
        return [(item_id, rank + 1) for rank, (item_id, _) in enumerate(sorted_items)]

    def _vector_search(
        self,
        query: str,
        limit: int = 20,
        *,
        include_archived: bool = False,
        chunk_locators: dict[str, dict] | None = None,
    ) -> list[tuple[str, int]] | None:
        """Brute-force cosine similarity over CHUNK vectors *and* whole-item vectors,
        rolled up to one score per item. Returns None if no embedder.

        The return type is deliberately still ``[(item_id, rank)]`` — the fusion contract.
        Chunks are an indexing detail that must not leak into ``_rrf_fuse``: a chunk hit is
        rolled up to its parent item BEFORE ranking, so the fused arm sees exactly the list
        shape it always saw and fusion needs no change at all (KNOWLEDGE-LIBRARY §Risks:
        "do not redesign fusion").

        **Roll-up rule: MAX.** An item's vector score is the single best above-floor
        similarity found for it, across its chunk vectors and its own whole-item vector.
        Max, not mean or sum, because:
        - the question retrieval asks is "does this document contain the answer", which is
          a max over passages — a mean drags a 50-chunk document with one perfect passage
          below a 2-chunk document with two mediocre ones, and a sum simply rewards length
          (the very bias the title boost below exists to counteract in BM25);
        - max is the only aggregate that leaves the score on the *identical* scale as the
          old item-level cosine, so ``_VECTOR_MIN_SIMILARITY`` keeps its calibrated
          meaning. Any averaging aggregate would silently re-scale that threshold, and
          retuning a threshold is out of scope for this task (escalation E6).

        Because the whole-item scan is kept unchanged and merely maxed against the chunk
        scan, an item with no chunk rows (or whose chunks are not embedded yet, mid-backfill)
        contributes only its whole-item vector: the fallback is a *consequence* of the max
        rather than a special case, so a partially-chunked library degrades in ranking
        quality and never loses an item. A chunked item also keeps its title+summary
        signal, which no chunk carries and which the keyword arm can only reach by literal
        term match.

        **KL-11: the scan is a fallback, not the plan.** When ``sqlite-vec`` loads, the chunk
        arm asks a ``vec0`` index for the k nearest chunk vectors and the item arm orders by
        ``vec_distance_cosine``, so neither arm reads every BLOB into Python. Both are pure
        candidate generation — ``_consider`` still scores — so the exact scan and the ANN path
        share one scoring implementation and cannot disagree on a similarity value. When the
        extension cannot load (a SQLite built without loadable extensions), both arms revert to
        the streamed exact scan above: slower on a large library, identical in what it returns,
        announced once at INFO and reported by the Doctor.

        ``chunk_locators`` is an optional sink: when supplied, it is filled with
        ``item_id -> {"section", "line_start", "line_end"}`` for every item whose winning
        signal was a chunk, so ``search`` can cite the passage that actually matched. It is
        an out-parameter rather than part of the return value precisely so the ranked list
        handed to fusion cannot drift.
        """
        if self.embedder is None:
            return None

        query_vec = self.embedder(query)
        if not query_vec:
            return None
        q_dim = len(query_vec)

        # Best above-floor similarity per item, and (when a chunk supplied it) where that
        # winning evidence sits in the document.
        best: dict[str, float] = {}
        best_loc: dict[str, dict] = {}

        def _consider(item_id: str, blob, locator: dict | None) -> float | None:
            """Score one vector into the roll-up. Returns the similarity, or ``None`` when the
            vector is unscoreable (dimension guard). The value is returned — not just applied —
            so the ANN candidate loop can see where the ``_VECTOR_MIN_SIMILARITY`` floor falls
            in an ordered candidate list without a second cosine implementation."""
            vec = _bytes_to_floats(blob)
            # Skip vectors from a different embedding model: a stored vec whose dimension
            # differs from the current query vec can't be compared (cosine over zip() would
            # silently truncate to the shorter and score a meaningless prefix). Such rows
            # fall back to keyword/graph retrieval until re-embedded with the active model.
            # This guard applies to chunk vectors exactly as it does to item vectors — a
            # half-re-embedded library has both old-model chunks and old-model item rows.
            if not vec or len(vec) != q_dim:
                return None
            sim = self._cosine_similarity(query_vec, vec)
            # Floor: drop near-orthogonal noise so precise keyword/tag queries aren't
            # polluted by weak semantic neighbors. Applied per vector, before the roll-up,
            # so a weak chunk can never become an item's cited passage.
            if sim < _VECTOR_MIN_SIMILARITY:
                return sim
            if sim > best.get(item_id, -1.0):
                best[item_id] = sim
                if locator is None:
                    best_loc.pop(item_id, None)
                else:
                    best_loc[item_id] = locator
            return sim

        # KL-11: sqlite-vec narrows both arms to a candidate set instead of reading every
        # BLOB. It is a CANDIDATE GENERATOR only — `_consider` above still does the scoring,
        # so the dimension guard, the cosine, the floor, and the max roll-up are byte-for-byte
        # the ones the exact scan uses, and the only way ANN can differ from exact is by
        # truncating candidates. `index.enabled` loads the extension into this connection on
        # first ask and reports False (once, at INFO) on any build that cannot load it.
        index = getattr(self.store, "vec_index", None)
        if index is not None and not index.enabled:
            index = None  # collapse "no index" and "index refused" to one branch
        q_blob = floats_to_bytes(query_vec) if index is not None else b""

        # Chunk arm. Row count is (embedded chunks) rather than (items) — strictly more
        # BLOBs than the item-only scan, bounded by chunking.MAX_CHARS (~1 chunk per 1500
        # content chars). With the ANN index it is (k candidates) instead; without it, both
        # cursors are STREAMED rather than .fetchall()-ed so peak memory stays O(1) rows
        # instead of O(corpus).
        chunk_archived = "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0"
        chunk_cols = (
            "SELECT c.id AS chunk_id, c.item_id, c.embedding, c.section, c.line_start, c.line_end "
        )

        def _consider_chunk_row(row) -> float | None:
            return _consider(
                row["item_id"],
                row["embedding"],
                {
                    "section": row["section"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                },
            )

        ann_served = False
        if index is not None:
            k = max(1, limit) * _ANN_OVERFETCH
            seen: set[str] = set()  # never re-score a candidate a smaller k already returned
            for _ in range(_ANN_MAX_ATTEMPTS):
                cand = index.candidate_chunk_ids(q_blob, q_dim, k)
                if cand is None:  # no usable index for this dimension — fall through to exact
                    break
                ann_served = True
                fresh = [cid for cid in cand if cid not in seen]
                seen.update(fresh)
                # STOP RULE. vec0 returns candidates in exact cosine order, so the FIRST
                # candidate that scores below `_VECTOR_MIN_SIMILARITY` proves every chunk after
                # it — including every chunk the index has not returned — is also below the
                # floor and can never contribute. At that point the candidate set is COMPLETE,
                # not truncated: scoring stops and escalation stops. Two earlier versions of
                # this loop were measurably SLOWER than the exact scan it replaces — one
                # escalated k until `limit` items were found (unreachable on a corpus with fewer
                # than `limit` above-floor items: 3,180 rows scored where the scan reads 1,500),
                # the other applied the rule per attempt instead of per candidate and so always
                # decoded the whole first over-fetch.
                reached_floor = False
                for start in range(0, len(fresh), _ID_BATCH):
                    batch = fresh[start : start + _ID_BATCH]
                    placeholders = ",".join("?" * len(batch))
                    # Keyed by chunk id, because `IN (...)` returns rows in STORAGE order and
                    # the stop rule is only sound while candidates are walked in the index's
                    # cosine order.
                    rows_by_id = {
                        row["chunk_id"]: row
                        for row in self.store.db.execute(
                            chunk_cols + "FROM chunks c JOIN items i ON i.id = c.item_id "
                            f"WHERE c.id IN ({placeholders}) "  # noqa: S608 (placeholders only)
                            "AND c.embedding IS NOT NULL AND i.status = 'active' "
                            f"{chunk_archived}",
                            batch,
                        )
                    }
                    for chunk_id in batch:
                        row = rows_by_id.get(chunk_id)
                        if row is None:
                            # A candidate the index still lists but the live table no longer
                            # offers (deleted item, archived, un-embedded). Unscored, so it
                            # says nothing about the floor — skip it and keep walking.
                            continue
                        sim = _consider_chunk_row(row)
                        if sim is not None and sim < _VECTOR_MIN_SIMILARITY:
                            reached_floor = True
                            break
                    if reached_floor:
                        break
                if reached_floor or len(best) >= limit or len(cand) < k:
                    break
                k *= _ANN_ESCALATION_FACTOR

        if not ann_served:
            for row in self.store.db.execute(
                chunk_cols + "FROM chunks c JOIN items i ON i.id = c.item_id "
                "WHERE c.embedding IS NOT NULL AND i.status = 'active' "
                f"{chunk_archived}"  # noqa: S608 (clause is a fixed literal)
            ):
                _consider_chunk_row(row)

        # Whole-item arm: the document-level (title + summary) vector. One vector per item, so
        # there is no roll-up to collapse candidates and no index to keep in step — ordering by
        # sqlite-vec's `vec_distance_cosine` over the LIVE column is exact and can never go
        # stale, which is why this arm gets the scalar function rather than a second vec0
        # table. `length(embedding) = ?` is the SQL spelling of `_consider`'s dimension guard,
        # and it is load-bearing: vec_distance_cosine RAISES on a dimension mismatch, so a
        # half-re-embedded library would otherwise fail the whole query instead of skipping
        # the unscoreable rows.
        archived_clause = "" if include_archived else "AND COALESCE(is_archived, 0) = 0"
        item_rows = None
        if index is not None:
            try:
                item_rows = self.store.db.execute(
                    "SELECT id, embedding FROM items "
                    "WHERE embedding IS NOT NULL AND status = 'active' "
                    f"{archived_clause} AND length(embedding) = ? "  # noqa: S608
                    "ORDER BY vec_distance_cosine(embedding, ?) LIMIT ?",
                    (q_dim * 4, q_blob, max(1, limit) * _ANN_OVERFETCH),
                ).fetchall()
            except sqlite3.Error as exc:  # fail soft to the exact scan, never into the search
                logger.debug("knowledge vector search: item-arm ANN query failed: %s", exc)
                item_rows = None
        if item_rows is not None:
            # Ordered by cosine, so the same completeness argument as the chunk arm applies:
            # the first row below the floor proves every later row is too. Stop there instead
            # of decoding the rest of the over-fetch.
            for row in item_rows:
                sim = _consider(row["id"], row["embedding"], None)
                if sim is not None and sim < _VECTOR_MIN_SIMILARITY:
                    break
        else:
            for row in self.store.db.execute(
                "SELECT id, embedding FROM items WHERE embedding IS NOT NULL "
                f"AND status = 'active' {archived_clause}"  # noqa: S608 (fixed literal)
            ):
                # Unordered: every row must be scored, so no early exit is available here.
                _consider(row["id"], row["embedding"], None)

        scored = sorted(best.items(), key=lambda x: x[1], reverse=True)[:limit]
        if chunk_locators is not None:
            for item_id, _ in scored:
                loc = best_loc.get(item_id)
                if loc is not None:
                    chunk_locators[item_id] = loc
        return [(item_id, rank + 1) for rank, (item_id, _) in enumerate(scored)]

    @staticmethod
    def _rrf_fuse(*ranked_lists, k: int = 60) -> list[tuple[str, float]]:
        """Reciprocal Rank Fusion across all non-None ranked lists."""
        scores: dict[str, float] = defaultdict(float)
        for rlist in ranked_lists:
            if rlist is None:
                continue
            for item_id, rank in rlist:
                scores[item_id] += 1.0 / (k + rank)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity. Returns 0.0 for zero vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)


def _bytes_to_floats(blob: bytes) -> list[float]:
    """Decode an embedding blob of ``struct``-packed 32-bit floats."""
    if not blob:
        return []
    if isinstance(blob, bytes) and len(blob) >= 16 and len(blob) % 4 == 0:
        try:
            n = len(blob) // 4
            return list(struct.unpack(f"{n}f", blob))
        except struct.error:
            pass
    return []


# ── P12 citation locators ───────────────────────────────────────────────────────
# A retrieval hit gains WHERE-in-the-item its match sits, so a consumer can cite +
# deep-link into the source instead of just naming the document. The result stays
# item-shaped: the locator is derived at read time from the item's own content +
# in-text structural markers the readers already emit (## Slide N / ## {sheet} /
# # headings). Never fabricates structure it can't find — section/line_range stay
# null for a structureless type (image/audio), which is honest, not a guess.
#
# KL-10: when the vector arm's winning evidence for an item was a CHUNK, that chunk's
# own span narrows the search window (and supplies the heading), so a semantic hit no
# longer has to be described by a whole-document term scan. The chunker numbers lines
# 1-based over the very same ``content`` string this function splits, so the spans are
# directly comparable.

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _attach_locator(item: dict, q_terms: set[str], chunk_locator: dict | None = None) -> dict:
    """Return the four citation fields for a result: ``source_type`` (the item kind),
    ``section`` (nearest structural header / slide / sheet above the best match, or None),
    ``line_range`` (1-based [start,end] of the best-matching line span in ``content``, or
    None), and ``deep_link`` (``/knowledge/items/{id}?loc=…``). Pure: reads only the item
    dict, the already-computed query terms, and the optional winning-chunk locator; no DB,
    no I/O.

    With ``chunk_locator`` the result is never LESS specific than without it: the term scan
    is narrowed to the winning chunk's lines but keeps its identical ±1-line window width,
    and when no query term appears literally inside that passage — the pure-semantic case
    that yields a null locator today — the chunk's own span and heading are cited instead
    of nothing.
    """
    iid = item.get("id") or ""
    source_type = str(item.get("item_type") or "").strip() or "item"
    content = item.get("content") or ""
    lines = content.split("\n") if content else []

    # Search window: the winning chunk's span when the vector arm rolled a chunk hit up to
    # this item, else the whole document (identical to the pre-chunk behaviour, since an
    # absent chunk_locator leaves the window at [0, len-1]).
    lo, hi = 0, len(lines) - 1
    chunk_span: list[int] | None = None
    if chunk_locator:
        c_start, c_end = chunk_locator.get("line_start"), chunk_locator.get("line_end")
        if isinstance(c_start, int) and isinstance(c_end, int) and 1 <= c_start <= len(lines):
            lo = c_start - 1
            hi = max(lo, min(len(lines) - 1, c_end - 1))
            chunk_span = [lo + 1, hi + 1]

    # Find the line with the most query-term hits (the match anchor). Structureless or
    # empty content → no line/section locator (image/audio: honest null, never faked).
    best_line = -1
    best_hits = 0
    if q_terms and lines:
        for i in range(lo, hi + 1):
            toks = {t for t in re.findall(r"[a-z0-9]+", lines[i].lower()) if len(t) > 1}
            hits = len(q_terms & toks)
            if hits > best_hits:
                best_hits, best_line = hits, i

    section: str | None = None
    line_range: list[int] | None = None
    if best_line >= 0 and best_hits > 0:
        # line_range: the matched line, widened by one neighbour each side for context,
        # clamped to the search window. 1-based inclusive for human-facing citation.
        start = max(lo, best_line - 1)
        end = min(hi, best_line + 1)
        line_range = [start + 1, end + 1]
        # section: nearest markdown/slide/sheet header at or above the match. The readers
        # emit '## Slide N: …', '## {sheet}', and '# …' headings in-text — one scan covers
        # all three (they're all '#'-led lines).
        for j in range(best_line, -1, -1):
            m = _HEADER_RE.match(lines[j])
            if m:
                section = m.group(2).strip()[:120]
                break
    elif chunk_span is not None:
        # Pure-semantic chunk hit: no query term appears literally in the winning passage,
        # so the term scan alone would yield the null locator it yields today. The chunk
        # knows exactly where it sits — cite its span rather than nothing.
        line_range = chunk_span

    # The chunker labels a section using a slightly wider heading rule than the read-time
    # scan above (it tolerates up to three leading spaces, per CommonMark), so a chunk can
    # name a heading the scan misses. Prefer any section already found; fill from the chunk.
    if section is None and chunk_locator and chunk_locator.get("section"):
        section = str(chunk_locator["section"]).strip()[:120] or None

    # Page fallback for a paged doc (PDF) with no in-text header: cite the page count so
    # the deep-link can at least land in the right document with a page hint.
    if section is None:
        fmeta = item.get("file_metadata") or {}
        if isinstance(fmeta, dict) and fmeta.get("page_count"):
            section = None  # no per-page offsets exist; leave section null, keep it honest

    # deep_link: the item route + an optional line-locator query the FE can honor.
    loc = f"L{line_range[0]}-{line_range[1]}" if line_range else ""
    deep_link = f"/knowledge/items/{iid}" + (f"?loc={loc}" if loc else "")

    return {
        "source_type": source_type,
        "section": section,
        "line_range": line_range,
        "deep_link": deep_link,
    }
