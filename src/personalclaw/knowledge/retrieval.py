"""HybridRetriever -- FTS5 keyword + graph + optional vector, fused with RRF."""

import math
import re
import struct
from collections import defaultdict

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from .store import KnowledgeStore

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
        vec = self._vector_search(query, limit=over, include_archived=include_archived)

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
                    # P12: per-item citation locator (source_type/section/line_range/deep_link),
                    # derived from the item's own content + the query terms already computed above.
                    **_attach_locator(item, q_terms),
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
        self, query: str, limit: int = 20, *, include_archived: bool = False
    ) -> list[tuple[str, int]] | None:
        """Brute-force cosine similarity against stored embeddings. Returns None if no embedder."""
        if self.embedder is None:
            return None

        query_vec = self.embedder(query)
        if not query_vec:
            return None
        q_dim = len(query_vec)
        archived_clause = "" if include_archived else "AND COALESCE(is_archived, 0) = 0"
        rows = self.store.db.execute(
            "SELECT id, embedding FROM items WHERE embedding IS NOT NULL AND status = 'active' "
            f"{archived_clause}"  # noqa: S608 (clause is a fixed literal)
        ).fetchall()

        scored = []
        for row in rows:
            item_vec = _bytes_to_floats(row["embedding"])
            # Skip vectors from a different embedding model: a stored vec whose dimension
            # differs from the current query vec can't be compared (cosine over zip() would
            # silently truncate to the shorter and score a meaningless prefix). Such items
            # fall back to keyword/graph retrieval until re-embedded with the active model.
            if item_vec and len(item_vec) == q_dim:
                sim = self._cosine_similarity(query_vec, item_vec)
                # Floor: drop near-orthogonal noise so precise keyword/tag queries
                # aren't polluted by weak semantic neighbors.
                if sim >= _VECTOR_MIN_SIMILARITY:
                    scored.append((row["id"], sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [(item_id, rank + 1) for rank, (item_id, _) in enumerate(scored[:limit])]

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


# ── P12 per-ITEM citation locators ──────────────────────────────────────────────
# A retrieval hit gains WHERE-in-the-item its match sits, so a consumer can cite +
# deep-link into the source instead of just naming the document. Everything is
# per-ITEM (no chunk rows — VISION forbids them): the locator is derived at read
# time from the item's own content + in-text structural markers the readers already
# emit (## Slide N / ## {sheet} / # headings) or file_metadata.page_count. Never
# fabricates structure it can't find — section/line_range stay null for a
# structureless type (image/audio), which is honest, not a guess.

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _attach_locator(item: dict, q_terms: set[str]) -> dict:
    """Return the four per-item citation fields for a result: ``source_type`` (the item
    kind), ``section`` (nearest structural header / slide / sheet / page above the best
    match, or None), ``line_range`` (1-based [start,end] of the best-matching line span in
    ``content``, or None), and ``deep_link`` (``/knowledge/items/{id}?loc=…``). Pure: reads
    only the item dict + the already-computed query terms; no DB, no I/O."""
    iid = item.get("id") or ""
    source_type = str(item.get("item_type") or "").strip() or "item"
    content = item.get("content") or ""
    lines = content.split("\n") if content else []

    # Find the line with the most query-term hits (the match anchor). Structureless or
    # empty content → no line/section locator (image/audio: honest null, never faked).
    best_line = -1
    best_hits = 0
    if q_terms and lines:
        for i, ln in enumerate(lines):
            toks = {t for t in re.findall(r"[a-z0-9]+", ln.lower()) if len(t) > 1}
            hits = len(q_terms & toks)
            if hits > best_hits:
                best_hits, best_line = hits, i

    section: str | None = None
    line_range: list[int] | None = None
    if best_line >= 0 and best_hits > 0:
        # line_range: the matched line, widened by one neighbour each side for context,
        # clamped to the content. 1-based inclusive for human-facing citation.
        start = max(0, best_line - 1)
        end = min(len(lines) - 1, best_line + 1)
        line_range = [start + 1, end + 1]
        # section: nearest markdown/slide/sheet header at or above the match. The readers
        # emit '## Slide N: …', '## {sheet}', and '# …' headings in-text — one scan covers
        # all three (they're all '#'-led lines).
        for j in range(best_line, -1, -1):
            m = _HEADER_RE.match(lines[j])
            if m:
                section = m.group(2).strip()[:120]
                break

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
