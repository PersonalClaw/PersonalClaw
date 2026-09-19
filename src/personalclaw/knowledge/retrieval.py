"""HybridRetriever -- FTS5 keyword + graph + optional vector, fused with RRF."""

import logging
import math
import re
import struct
from collections import defaultdict

from personalclaw.sqlite_compat import sqlite3

from .embedder import floats_to_bytes
from .embedding_fingerprint import FRESH_PREDICATE, active_fingerprint
from .searchability import SearchOutcome, degradations_from, unsearchable_rows
from .store import KnowledgeStore

logger = logging.getLogger(__name__)

# ── why there is no post-fusion relevance cut ─────────────────────────────────
# There used to be one ("the relevance cliff"): walk the score-sorted fused list and stop
# at the first consecutive pair whose drop exceeded 0.30 × the top score, on the theory
# that the elbow between the relevant cluster and the weak tail would show up there.
#
# It cannot. RRF fuses RANKS: a fused score is Σ 1/(k + rank) over the arms that returned
# the item, so it is a function of POSITION and ARM COUNT and carries no information at all
# about how good any single match is. Two items with the same arm membership at the same
# ranks score identically whether one is the definitive answer and the other mentions the
# query once in a footnote. Any threshold on that number — a consecutive-pair gap, or a
# floor relative to the top score — is therefore a threshold on position, and measuring one
# is measuring the shape of 1/(k+rank), not the corpus.
#
# Measured on a 26-item library over 22 real queries (#392): the consecutive-gap cut fired
# on 12 of them, and in all 12 the firing drop was `_TITLE_BOOST` (1/61 = 0.01639) — the
# arms' own contribution landed between -0.00190 and +0.00126, never once producing the drop
# by itself. So in practice it was a title-match detector, and which way it went depended
# only on whether some title happened to match: with one matching it returned 1 of 23
# candidates for `scrub`/`raidz`/`smart`, 3 for `resilver`, 9 for `draid`; with none matching
# it returned all 23 for `zfs` and all 25 for `a`. Both directions are the same defect.
# A relative-to-top floor — the other obvious repair — is the same error: on a
# single-arm list `score[i] < (1-gap)·score[0]` reduces to a CONSTANT top-K (rank 28 at
# gap = 0.30) independent of the corpus, while on a multi-arm list the identical constant
# instead means "drop every single-arm hit at any rank".
#
# Absolute match quality survives in exactly one place: inside an arm, before its hits are
# flattened into ranks. That is where each arm qualifies them (`ARM_QUALIFICATION` below).
# Fusion orders what the arms already vouched for, and the caller's `limit` caps it.

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

# ── the arm vocabulary (EVALUATION-SUBSTRATE §5.1) ────────────────────────────
# These are the SAME strings the results' ``match_type`` already reports, so the
# ablation harness and the per-hit attribution a user sees in the UI name the arms
# identically. A second spelling here would let a mask silently gate nothing.
ARM_KEYWORD = "keyword"
ARM_GRAPH = "graph"
ARM_VECTOR = "vector"
#: Every arm :meth:`HybridRetriever.search` fuses, in ``match_type`` order.
ARMS = (ARM_KEYWORD, ARM_GRAPH, ARM_VECTOR)

#: Each arm's PRE-FUSION qualification, on that arm's own native scale — the contract that
#: replaced the post-fusion relevance cut (see the block comment at the top of this module for
#: the measurement that killed it). An arm must never hand fusion its unbounded top-K: RRF
#: cannot tell a strong hit from a weak one, so whatever the arm knows about match quality has
#: to be spent before its list becomes ranks. Keyed by :data:`ARMS` precisely so a fourth arm
#: cannot be added without declaring one — ``tests/test_retrieval_arm_qualification.py``
#: derives its completeness check from :data:`ARMS`, not from a hand-kept list here.
ARM_QUALIFICATION: dict[str, str] = {
    ARM_KEYWORD: (
        "FTS5 MATCH: an item qualifies only by containing a query term (or its prefix) in "
        "its indexed text — items with no term occurrence are never in the arm's list."
    ),
    ARM_GRAPH: (
        "hop distance: an item qualifies by mentioning an entity the query matched DIRECTLY. "
        "Items reached only through the depth-2 traversal are the arm's fallback, used when "
        "nothing mentions a direct match — never as padding alongside one."
    ),
    ARM_VECTOR: (
        f"cosine ≥ _VECTOR_MIN_SIMILARITY ({_VECTOR_MIN_SIMILARITY}), applied per vector "
        "before the max roll-up, so a near-orthogonal chunk cannot become an item's evidence."
    ),
}

# ── relevance rerank stage (KBVS-2) ───────────────────────────────────────────
# A stage AFTER `_rrf_fuse`, not a fourth arm fed INTO it: RRF fuses RANKS from
# retrieval sources queried in parallel, and a rerank call needs the candidates
# already assembled to judge them — it cannot be one more list handed to the same
# fusion. It rides the existing model USE-CASE seam (`providers.use_cases`) rather
# than a new one: reranking is exactly the "explicit one-shot judgment call" that
# axis's own docstring already names, and the removal doctrine
# (`summarization`/`planning` were pulled for having no real consumer) is a standing
# warning against minting an axis for just this one feature.
_RERANK_USE_CASE = "reasoning"
#: Default candidate window when `knowledge.rerank_candidates` is unset/unreadable —
#: mirrors the dataclass default in `config.loader.KnowledgeConfig`.
_RERANK_DEFAULT_CANDIDATES = 20


class HybridRetriever:
    """FTS5 keyword + graph traversal + optional vector search, fused with RRF."""

    def __init__(self, store: KnowledgeStore, embedder=None):
        """store: KnowledgeStore instance. embedder: optional callable(str) -> list[float]."""
        self.store = store
        self.embedder = embedder
        #: Whether the MOST RECENT `search()` call that requested reranking actually got
        #: a usable model response (as opposed to falling back to the un-reranked RRF
        #: order). Read by the retrieval bench (KBVS-2) to tell "the reranker ran and
        #: matched the baseline" apart from "the reranker never ran" — two very
        #: different facts a plain P@k number cannot distinguish on its own.
        self.last_rerank_executed = False

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        include_archived: bool = False,
        arms: "tuple[str, ...] | list[str] | set[str] | None" = None,
        rerank: "bool | None" = None,
    ) -> list[dict]:
        """Hybrid search with RRF fusion. Returns [{id, title, summary, content, score, source, match_type}].  # noqa: E501

        ``include_archived`` defaults False — archived items never surface to agents or
        chat context-injection. The Archived UI view sets it True so a search *within*
        that view can find archived items (matching the no-query Archived list).

        ``arms`` masks which of :data:`ARMS` contribute (EVALUATION-SUBSTRATE §5.1's
        ablation knob). ``None`` — the default every production caller uses — runs all
        three, so the live ranking is unchanged by construction. A masked arm is not
        merely dropped from fusion: its query is **never issued**, so an ablation cell
        measures the arm's absence rather than its cost. An empty mask is legal and
        returns ``[]``: that is the harness's control cell, and a control that came back
        with hits is how you learn the mask was not applied.

        ``rerank`` (KBVS-2) controls the post-fusion relevance stage. ``None`` — every
        production caller — reads ``knowledge.rerank_enabled`` (off by default).
        ``True``/``False`` FORCE the stage on or off regardless of config: the
        retrieval bench's own knob, so it can measure the reranked arm on every run
        independent of whatever is currently configured. See
        :attr:`last_rerank_executed` for whether a forced/enabled call actually got a
        usable model response.
        """
        active = ARMS if arms is None else tuple(a for a in ARMS if a in set(arms))
        over = limit * 2
        kw = (
            self._keyword_search(query, limit=over, include_archived=include_archived)
            if ARM_KEYWORD in active
            else []
        )
        gr = (
            self._graph_search(query, limit=over, include_archived=include_archived)
            if ARM_GRAPH in active
            else []
        )
        # chunk_locs collects, per item, the span of the chunk whose vector won for it —
        # filled by the vector arm as a by-product of its roll-up so the ranked list it
        # hands to fusion stays exactly [(item_id, rank)].
        chunk_locs: dict[str, dict] = {}
        vec = (
            self._vector_search(
                query, limit=over, include_archived=include_archived, chunk_locators=chunk_locs
            )
            if ARM_VECTOR in active
            else []
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

        # Relevance rerank (KBVS-2), OFF unless configured or forced by the caller. This
        # REORDERS the already-fused list — it never drops a candidate, so it does not
        # reintroduce the post-fusion relevance CUT the module block comment above rules
        # out; a rerank score is a different question ("which of these already-vouched-for
        # candidates is most relevant") than a cut threshold ("is this candidate good
        # enough to keep").
        self.last_rerank_executed = False
        if fused and self._rerank_wanted(rerank):
            fused = self._apply_rerank(query, fused, items_cache, limit=limit)

        # No post-fusion relevance cut — `limit` is the only cap. Everything still here was
        # already vouched for by the arm that retrieved it (`ARM_QUALIFICATION`), on that arm's
        # own absolute scale; a second cut on the fused score would only re-cut by position.
        # See the module block comment for the measurement.

        # Track which lists each item appeared in
        kw_ids = {i for i, _ in kw}
        gr_ids = {i for i, _ in gr}
        vec_ids = {i for i, _ in (vec or [])}

        results = []
        for item_id, score in fused[:limit]:
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

    def search_with_diagnostics(
        self,
        query: str,
        limit: int = 10,
        *,
        include_archived: bool = False,
        arms: "tuple[str, ...] | list[str] | set[str] | None" = None,
    ) -> SearchOutcome:
        """:meth:`search`, plus the typed reasons the library could not answer (RET-2).

        The hits are byte-identical to :meth:`search` — this adds a report, it does not
        change ranking. The degradations are grouped from the items the ingest runner
        already PERSISTED as unsearchable, so the reason a search reports and the reason
        the Doctor row reports are literally the same recorded fact; there is no second
        place a reason can be minted and therefore no way for the two to disagree.

        Callers that only want hits keep using :meth:`search`. Callers that must not
        answer "nothing found" when the truth is "found nothing it can reach" — the
        ``knowledge_search`` tool — use this.

        **RET-4 adds one derived reason to the persisted ones.** ``stale_index`` names items
        whose chunk vectors came from a different embedding model than the one bound now:
        :meth:`_vector_search` refused to score those vectors (scoring them would produce a
        meaningless number), so a search that returns nothing because of them must say so.
        It is derived from the chunk fingerprints rather than read off the item, because the
        thing that changed is the bound model and the item is otherwise healthy.
        """
        results = self.search(query, limit, include_archived=include_archived, arms=arms)
        rows = unsearchable_rows(self.store) + self.store.stale_chunk_item_rows(
            include_archived=include_archived
        )
        return SearchOutcome(results=results, degradations=degradations_from(rows))

    def _rerank_wanted(self, rerank: "bool | None") -> bool:
        """Resolve the effective on/off for THIS call.

        ``None`` (every production caller) reads the live config — off unless an
        operator turned it on. ``True``/``False`` FORCE the stage regardless of config:
        the retrieval bench's own knob (KBVS-2), so it can measure the reranked arm on
        every run independent of whatever ``config.json`` currently says, and a test can
        force it on deterministically.
        """
        if rerank is not None:
            return rerank
        try:
            from personalclaw.config.loader import AppConfig

            return bool(AppConfig.load().knowledge.rerank_enabled)
        except Exception:  # noqa: BLE001 - unreadable config means OFF, the shipped default
            logger.debug("rerank: config unavailable, defaulting to off", exc_info=True)
            return False

    def _apply_rerank(
        self,
        query: str,
        fused: "list[tuple[str, float]]",
        items_cache: "dict[str, dict]",
        *,
        limit: int,
    ) -> "list[tuple[str, float]]":
        """Re-score the top candidates by relevance, after RRF has already ranked them.

        Only the WINDOW (at least ``limit``, else the configured candidate count) is
        sent to the model — the tail beyond it keeps its RRF order untouched and is
        appended back as-is. Fails OPEN on any model/parse trouble (:meth:`_rerank_score`
        returning ``None``): reranking is a relevance stage, not a security control, so
        an outage must degrade to "unreranked" rather than break the search (the
        fail-open/closed convention, core ``AGENTS.md`` "Shared conventions").
        """
        candidates_n = _RERANK_DEFAULT_CANDIDATES
        try:
            from personalclaw.config.loader import AppConfig

            candidates_n = (
                int(AppConfig.load().knowledge.rerank_candidates) or _RERANK_DEFAULT_CANDIDATES
            )
        except Exception:  # noqa: BLE001 - an unreadable config keeps the module default
            logger.debug(
                "rerank: candidate-window config unavailable, using default", exc_info=True
            )
        window_n = min(len(fused), max(int(limit), max(1, candidates_n)))
        window, tail = fused[:window_n], fused[window_n:]

        scored = self._rerank_score(query, window, items_cache)
        if scored is None:
            return fused
        self.last_rerank_executed = True
        reordered = sorted(scored, key=lambda pair: pair[1], reverse=True)
        return reordered + tail

    def _rerank_score(
        self,
        query: str,
        window: "list[tuple[str, float]]",
        items_cache: "dict[str, dict]",
    ) -> "list[tuple[str, float]] | None":
        """Ask the bound model to relevance-score ``window``'s items. ``None`` on failure.

        ``None`` covers a model/transport failure AND a degenerate-but-successful
        response (empty text, or valid JSON that names none of the candidates) — a
        thinking model on a small output budget returns empty content, and constrained
        decoding can return a valid-but-empty document; both must read as "the call did
        not usefully happen", never as "every candidate scored zero".
        """
        ids = [iid for iid, _ in window]
        if not ids:
            return None
        lines = []
        for rank, (iid, _score) in enumerate(window, start=1):
            item = items_cache.get(iid) or {}
            title = str(item.get("title") or "")[:200]
            snippet = str(item.get("summary") or item.get("content") or "")[:400]
            lines.append(f'{rank}. id="{iid}"\ntitle: {title}\nsnippet: {snippet}')
        prompt = (
            "Rate how relevant each candidate document is to the search query, on a "
            "0-10 scale (10 = directly answers the query, 0 = unrelated). Respond with "
            'ONLY a JSON array, one entry per candidate: [{"id": "<id>", "relevance": '
            "<0-10>}, ...]. Score EVERY candidate listed below, using its id exactly as "
            f"given.\n\nQuery: {query}\n\nCandidates:\n" + "\n\n".join(lines)
        )
        try:
            raw = _run_rerank_prompt(prompt)
        except Exception:  # noqa: BLE001 - a model/transport failure falls back, never raises
            logger.debug("rerank: model call failed", exc_info=True)
            return None
        if not isinstance(raw, list) or not raw:
            return None
        valid_ids = set(ids)
        relevance: dict[str, float] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            iid = str(entry.get("id", ""))
            if iid not in valid_ids:
                continue
            try:
                relevance[iid] = float(entry.get("relevance", 0))
            except (TypeError, ValueError):
                continue
        if not relevance:
            return None
        # A candidate the model never mentioned keeps a floor BELOW the lowest scored
        # one, not a manufactured 0.0 — the model may have simply omitted a weak
        # candidate from its list, and that omission must not outrank one it explicitly
        # rated 0/10.
        fallback_floor = min(relevance.values()) - 1.0
        return [(iid, relevance.get(iid, fallback_floor)) for iid in ids]

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
        """Find entities matching query terms, traverse the graph, rank items by mention count.

        Qualification (:data:`ARM_QUALIFICATION`) — **hop distance is this arm's native
        relevance scale**, in the same sense cosine is the vector arm's, and it used to be
        computed and then thrown away: the direct name matches and their depth-2 neighbours
        were folded into one ``IN (...)`` and every mention counted the same.

        On a real library that makes the arm return a query-INDEPENDENT constant: a depth-2
        traversal over an entity graph where the topic hubs are all linked reaches the whole
        connected component. Measured (#392) on a 26-item library, ``draid``, ``resilver``,
        ``scrub`` and ``smart`` each resolved to one entity, reached all 8 through the
        traversal, and so returned the same 23 of 26 items — of which 5, 17, 19 and 21
        respectively contained the query term nowhere in title, summary, content OR tags.
        That tail is what made a search for a present term look like "the entire library", and no
        post-fusion threshold could remove it, because by then it was indistinguishable from a
        genuine low-ranked hit (both are ``1/(k+rank)``).

        So the distance is kept: an item mentioning an entity the query matched **directly** is
        about the query and qualifies. An item reached only through a neighbour is about
        something adjacent — it is the arm's **fallback**, ranked only when nothing mentions a
        direct match, which is exactly the role ``_VECTOR_MIN_SIMILARITY`` gives a
        near-orthogonal neighbour. The traversal itself is unchanged and still does its job
        (finding an item that discusses the entity without naming it, via its own mention row).
        """
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

        # Expand via graph neighbors (depth=2), keeping the two hop classes apart.
        all_entity_ids = set(entity_ids)
        for eid in entity_ids:
            for neighbor in self.store.get_neighbors(eid, depth=2):
                all_entity_ids.add(neighbor["id"])

        # Count item mentions (active items; archived hidden by default — matching the
        # default list semantics — unless the Archived view asked to include them), splitting
        # mentions of a DIRECTLY matched entity from mentions of a traversal neighbour.
        # `direct_cnt` leads the ORDER BY so the SQL `LIMIT` can never spend the arm's budget
        # on neighbour-only items while a directly-mentioning one is still unranked.
        direct_ids = sorted(entity_ids)
        reachable_ids = sorted(all_entity_ids)
        direct_ph = ",".join("?" * len(direct_ids))
        placeholders = ",".join("?" * len(reachable_ids))
        archived_clause = "" if include_archived else "AND COALESCE(i.is_archived, 0) = 0 "
        rows = self.store.db.execute(
            f"SELECT m.item_id, COUNT(*) as cnt, "  # noqa: S608
            f"SUM(CASE WHEN m.entity_id IN ({direct_ph}) THEN 1 ELSE 0 END) as direct_cnt "
            f"FROM mentions m "
            f"JOIN items i ON i.id = m.item_id "
            f"WHERE m.entity_id IN ({placeholders}) AND i.status = 'active' "
            f"{archived_clause}"
            f"GROUP BY m.item_id ORDER BY direct_cnt DESC, cnt DESC LIMIT ?",
            (*direct_ids, *reachable_ids, limit),
        ).fetchall()

        # The qualification. Traversal-only items are the fallback, not padding: they rank
        # only when no item mentions a directly-matched entity at all (an entity that exists
        # in the graph but is mentioned by nothing), so the arm keeps its recall floor without
        # spending it on the connected component every time.
        qualified = [row for row in rows if row["direct_cnt"]]
        ranked = qualified or list(rows)
        return [(row["item_id"], rank + 1) for rank, row in enumerate(ranked)]

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

        # RET-4: only score chunk vectors the ACTIVE embedding model produced. The dimension
        # guard in `_consider` cannot see a same-dimension model swap — two 384-dim models
        # produce equally-long, mutually meaningless vectors — so the filter is on the
        # recorded fingerprint, applied in SQL rather than after decoding the BLOB. With
        # nothing bound there is no fingerprint to compare and the clause is omitted
        # entirely: that state is RET-2's `no_embedding_provider`, not staleness (and the
        # vector arm has already returned above, since `self.embedder` is None).
        fp = active_fingerprint()
        fresh_clause = f"AND {FRESH_PREDICATE} " if fp is not None else ""
        fresh_params: tuple[str, ...] = fp.params if fp is not None else ()

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
                            f"{chunk_archived} {fresh_clause}",
                            (*batch, *fresh_params),
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
                f"{chunk_archived} {fresh_clause}",  # noqa: S608 (clauses are fixed literals)
                fresh_params,
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


def _run_rerank_prompt(prompt: str) -> "list | None":
    """Bridge the async model-use-case seam into this synchronous call path.

    Every :meth:`HybridRetriever.search` caller today is synchronous (dashboard
    handlers, the ``knowledge_search`` agent tool, action providers) — making
    ``search`` itself async would ripple into every one of them, well past this
    atom's scope. Mirrors ``triggers/web_poll.py::_await_maybe``: ``asyncio.run`` when
    nothing already owns this thread's event loop, else a worker thread, so a caller
    that DOES hold a running loop (an async test, an async caller added later) can
    never deadlock on itself.

    Returns the parsed JSON array, or ``None`` when the response was empty or did not
    parse as one — :func:`personalclaw.llm_helpers.parse_llm_json_list` already treats
    an empty string as unparseable, which is what a thinking model on a too-small
    output budget returns.
    """
    import asyncio

    from personalclaw.llm_helpers import one_shot_completion, parse_llm_json_list

    async def _call() -> str:
        return await one_shot_completion(prompt, use_case=_RERANK_USE_CASE, output_type=list)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        text = asyncio.run(_call())
    else:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            text = pool.submit(asyncio.run, _call()).result(timeout=90)
    return parse_llm_json_list(text)


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
