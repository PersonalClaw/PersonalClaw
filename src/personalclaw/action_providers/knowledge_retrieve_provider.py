"""``knowledge-retrieve`` action provider — query the knowledge store from a workflow.

The paired half of `knowledge-persist`, and the reason both exist as ACTIONS rather than
tools: a three-node retrieve → synthesize → persist pattern spends exactly one model call,
on the synthesis. Doing the retrieve through a `stage` would spend a second one on a query.

Two things this returns that a plain search does not:

**`create_safety`** — `exists` / `probable` / `unknown`. This is what lets a workflow branch
update-vs-create WITHOUT an LLM duplicate check. An exact-title hit is `exists`; a strong
semantic hit is `probable`; a weak one is `unknown`. The persist provider keys off the same
logical identity, so the two agree by construction rather than by convention.

**Freshness metadata** — age, last verification, whether it has expired. Reported, never
enforced: whether a three-month-old fact still holds depends on the fact, and a retrieve
that silently hid stale items would make the store's own gaps invisible to the model that
needed to know about them.

**`detail` caps per-result size** rather than being a boolean. `top_k: 10` with full bodies
can blow a downstream stage's context window, and the caller who set `top_k` is rarely the
one who knows the window budget — so `brief`/`compact`/`full` make the cost explicit.

**A degradation ladder with telemetry.** vector → FTS → substring, and which tier answered
is in the output. A retrieve that quietly fell back to substring matching looks identical to
one that used embeddings, and the synthesis built on it would be trusted equally.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.knowledge.semantics import freshness, logical_key, normalize_title

logger = logging.getLogger(__name__)

#: Hard cap on results. `top_k` is advisory below this; above it the cap wins, because a
#: template asking for 200 items has misunderstood what a retrieve is for and would blow
#: whatever stage consumes it.
MAX_TOP_K = 25
DEFAULT_TOP_K = 5

#: Per-result content caps by `detail`. `brief` is title+summary only — enough for a
#: create-safety decision without paying for bodies nobody reads.
DETAIL_CAPS: dict[str, int] = {"brief": 0, "compact": 600, "full": 4_000}

#: The hybrid retriever fuses with RRF, so its scores are on a ~1/(60+rank) scale — a top
#: hit scores about 0.033, NOT 0.9. Measured: a 0.30 "relevance cliff" borrowed from cosine
#: space rejected every single hybrid result, and the provider silently returned nothing on a
#: store that clearly contained the answer. So the cliff applies only to tiers whose scores
#: ARE similarities, and rank decides create-safety for fused results.
RELEVANCE_CLIFF = 0.30

#: Ranks at or above this position are strong enough to call `probable` for create-safety.
#: Rank rather than score, because RRF position is meaningful where its absolute value is
#: not.
PROBABLE_RANK = 2


class KnowledgeRetrieveActionProvider(ActionProvider):
    """Query the knowledge store. Zero tokens.

    ``action_config`` shape::

        {
            "query": "cold start latency",   # required
            "mode": "semantic",              # semantic|fts
            "top_k": 5,                      # capped at MAX_TOP_K
            "detail": "compact",             # brief|compact|full
            "filters": {"kind": "fact", "tags": ["perf"]},
            "task_text": "…"                 # optional; matched against read_when triggers
        }
    """

    @property
    def name(self) -> str:
        return "knowledge-retrieve"

    @property
    def display_name(self) -> str:
        return "Retrieve Knowledge"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        query = str(cfg.get("query", "") or "").strip()
        if not query:
            return ActionResult(
                success=False,
                error="knowledge-retrieve is missing 'query' — bind it to a node's output",
            )

        detail = str(cfg.get("detail", "compact") or "compact")
        if detail not in DETAIL_CAPS:
            return ActionResult(
                success=False,
                error=f"detail {detail!r} must be one of: {', '.join(DETAIL_CAPS)}",
            )

        top_k = _bounded_top_k(cfg.get("top_k"))
        raw_filters = cfg.get("filters")
        filters: dict[str, Any] = raw_filters if isinstance(raw_filters, dict) else {}

        try:
            store = _open_store()
        except Exception as exc:  # pragma: no cover — environmental
            return ActionResult(success=False, error=f"knowledge store unavailable: {exc}")

        hits, strategy = _search(store, query, top_k=top_k, mode=str(cfg.get("mode", "semantic")))
        hits = _apply_filters(_enrich(store, hits), filters, strategy=strategy)
        items = [
            _shape_hit(store, hit, query=query, detail=detail, rank=index)
            for index, hit in enumerate(hits[:top_k])
        ]

        # An always-included `overview` for the topic, when one exists, and always FIRST.
        # A synthesis that starts from the overview writes something coherent with what is
        # already stored; one that starts from three unrelated facts writes a fourth.
        #
        # Promoted rather than merely inserted: when the overview was ALSO a search hit, an
        # insert-if-absent left it wherever the ranking happened to put it — measured, that
        # was second, behind a plain fact. "Always included" and "always first" are different
        # guarantees and the useful one is both.
        overview = _overview_for(store, query, detail=detail)
        if overview:
            items = [i for i in items if i["item_id"] != overview["item_id"]]
            items.insert(0, overview)

        truncated = f"{len(hits) - top_k} older items not consulted" if len(hits) > top_k else None
        payload = {
            "items": items,
            "strategy": strategy,
            "truncated": truncated,
            "coverage_gap": not items,
        }
        if not items:
            # A zero-result retrieve is a real signal, not an error: it is what the periodic
            # synthesizer turns into a persist proposal. Reported in the payload rather than
            # as a failure, so the run continues and the gap is still recorded.
            logger.info("knowledge coverage gap for query %r", query[:120])

        return ActionResult(
            success=True,
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


# ── search + degradation ladder ──


def _search(store, query: str, *, top_k: int, mode: str) -> tuple[list[dict], str]:
    """Search, recording WHICH tier answered.

    The strategy string is not decoration: a retrieve that quietly fell back to substring
    matching looks identical in its output to one that used embeddings, and a synthesis built
    on the former would be trusted exactly as much as one built on the latter.
    """
    over = max(top_k * 3, 10)

    if mode == "fts":
        rows = _fts(store, query, limit=over)
        return rows, "fts"

    try:
        from personalclaw.knowledge.retrieval import HybridRetriever

        retriever = HybridRetriever(store, embedder=_embedder())
        rows = retriever.search(query, limit=over)
        if rows:
            return rows, "hybrid"
    except Exception:
        logger.debug("hybrid retrieval unavailable — degrading", exc_info=True)

    rows = _fts(store, query, limit=over)
    if rows:
        return rows, "fts_fallback"

    rows = _substring(store, query, limit=over)
    return rows, "substring_fallback" if rows else "none"


def _embedder():
    """The active embedder, or None. None is a supported configuration — the ladder exists
    precisely so a store with no embedding model still answers."""
    try:
        from personalclaw.embedding_providers.registry import get_active_embed_fn

        return get_active_embed_fn()
    except Exception:
        return None


def _fts(store, query: str, *, limit: int) -> list[dict]:
    try:
        rows = list(
            store.db.execute(
                "SELECT i.id, i.title, i.summary, i.content, i.kind, i.updated_at, "
                "i.last_verified, i.expires_at, i.file_metadata "
                "FROM items_fts f JOIN items i ON i.id = f.rowid "
                "WHERE items_fts MATCH ? LIMIT ?",
                (query, limit),
            )
        )
    except Exception:
        logger.debug("FTS query failed", exc_info=True)
        return []
    return [dict(r) | {"score": 0.5, "match_type": "keyword"} for r in rows]


def _substring(store, query: str, *, limit: int) -> list[dict]:
    """The last rung. Crude on purpose — it exists so a store with no FTS index and no
    embedder still answers something rather than nothing."""
    like = f"%{query.strip()}%"
    try:
        rows = list(
            store.db.execute(
                "SELECT id, title, summary, content, kind, updated_at, last_verified, "
                "expires_at, file_metadata FROM items "
                "WHERE title LIKE ? OR content LIKE ? LIMIT ?",
                (like, like, limit),
            )
        )
    except Exception:
        logger.debug("substring query failed", exc_info=True)
        return []
    return [dict(r) | {"score": 0.35, "match_type": "substring"} for r in rows]


# ── shaping ──


def _bounded_top_k(raw: Any) -> int:
    """`top_k`, bounded. A bool is rejected explicitly: `True` is an int in Python and
    would silently become a request for one result."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_TOP_K
    return max(1, min(MAX_TOP_K, raw))


def _enrich(store, hits: list[dict]) -> list[dict]:
    """Fill in the columns the retriever does not return.

    `HybridRetriever.search` returns id/title/summary/content/score/match_type — but NOT
    `kind`, `updated_at`, `last_verified`, `expires_at` or `file_metadata`. Without this the
    kind filter matched nothing (every hit had `kind: None`) and every freshness reading was
    zero, both silently.
    """
    out: list[dict] = []
    for hit in hits:
        item_id = str(hit.get("id", "") or "")
        if not item_id or hit.get("kind") is not None:
            out.append(hit)
            continue
        try:
            rows = list(
                store.db.execute(
                    "SELECT kind, updated_at, last_verified, expires_at, file_metadata "
                    "FROM items WHERE id = ? LIMIT 1",
                    (item_id,),
                )
            )
        except Exception:
            rows = []
        out.append(dict(hit) | (dict(rows[0]) if rows else {}))
    return out


def _apply_filters(hits: list[dict], filters: dict[str, Any], *, strategy: str) -> list[dict]:
    """Filter by kind, and apply the relevance cliff ONLY where scores are similarities.

    Applied after search rather than in SQL, so a filtered-out hit still counted toward the
    ranking that produced it.
    """
    kind = str(filters.get("kind", "") or "").strip().lower()
    if kind:
        hits = [h for h in hits if str(h.get("kind", "") or "").lower() == kind]
    if strategy == "hybrid":
        # RRF scores are ~1/(60+rank); a similarity cliff here would reject everything.
        return hits
    return [h for h in hits if float(h.get("score", 0.0)) >= RELEVANCE_CLIFF]


def _shape_hit(store, hit: dict, *, query: str, detail: str, rank: int = 0) -> dict[str, Any]:
    """One result, with the evidence and safety fields a branching workflow needs."""
    cap = DETAIL_CAPS[detail]
    content = str(hit.get("content", "") or "")
    title = str(hit.get("title", "") or "")
    match_type = str(hit.get("match_type", "") or "vector")
    score = float(hit.get("score", 0.0) or 0.0)

    exact = normalize_title(title) == normalize_title(query)
    evidence = "exact_title" if exact else _evidence_for(match_type)
    fresh = freshness(
        updated_at=str(hit.get("updated_at", "") or ""),
        last_verified=str(hit.get("last_verified", "") or ""),
        expires_at=str(hit.get("expires_at", "") or ""),
    )
    meta = _meta(hit)
    raw_claims = meta.get("claims")
    claims: list[Any] = raw_claims if isinstance(raw_claims, list) else []

    return {
        "item_id": str(hit.get("id", "") or ""),
        "title": title,
        "summary": str(hit.get("summary", "") or ""),
        "content": content[:cap] if cap else "",
        "kind": str(hit.get("kind", "") or ""),
        "logical_key": logical_key(str(hit.get("kind", "fact") or "fact"), title),
        "relevance_score": round(score, 4),
        "evidence": evidence,
        "create_safety": _create_safety(exact=exact, rank=rank, evidence=evidence),
        "freshness": fresh.to_dict(),
        "support_count": max(
            (int(c.get("support_count", 0) or 0) for c in claims if isinstance(c, dict)),
            default=0,
        ),
        "read_when": meta.get("read_when") or [],
    }


def _evidence_for(match_type: str) -> str:
    """Map the retriever's match type onto the declared evidence vocabulary.

    `substring` maps to itself rather than to `keyword`: collapsing them would make a
    last-rung character match indistinguishable from a real FTS hit, and create-safety keys
    off exactly that distinction. `match_type` may be a fused "keyword+vector" string, so the
    strongest present tier wins.
    """
    raw = (match_type or "").lower()
    if "vector" in raw:
        return "vector"
    if "graph" in raw:
        return "graph"
    if "keyword" in raw:
        return "keyword"
    if "substring" in raw:
        return "substring"
    return "vector"


def _create_safety(*, exact: bool, rank: int, evidence: str) -> str:
    """Can the caller safely UPDATE this, or should it create a new item?

    Deliberately conservative. `unknown` on a weak hit means the caller creates, which leaves
    a duplicate for the curator to merge. The other error — updating an article that merely
    looked similar — silently overwrites unrelated knowledge, and no later pass can tell it
    happened. Asymmetric costs, so the threshold is asymmetric.

    Rank AND tier, not rank alone: measured, a substring hit that merely shared a common word
    ranked first for an unrelated query and was reported `probable`. A top-ranked SUBSTRING
    match is a coincidence of characters; a top-ranked semantic or keyword match is evidence.
    """
    if exact or evidence == "exact_title":
        return "exists"
    if rank < PROBABLE_RANK and evidence in ("vector", "graph", "keyword"):
        return "probable"
    return "unknown"


def _overview_for(store, query: str, *, detail: str) -> dict[str, Any] | None:
    """The matching `kind: overview`, when one exists.

    Always included because a synthesis that starts from the overview writes something
    coherent with what is already stored; one that starts from three unrelated facts writes
    a fourth unrelated fact.
    """
    key = logical_key("overview", query)
    if not key:
        return None
    try:
        rows = list(
            store.db.execute(
                "SELECT id, title, summary, content, kind, updated_at, last_verified, "
                "expires_at, file_metadata FROM items WHERE logical_key = ? LIMIT 1",
                (key,),
            )
        )
    except Exception:
        return None
    if not rows:
        return None
    hit = dict(rows[0]) | {"score": 1.0, "match_type": "exact_title"}
    return _shape_hit(store, hit, query=query, detail=detail, rank=0)


def _meta(hit: dict) -> dict[str, Any]:
    try:
        parsed = json.loads(hit.get("file_metadata") or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _open_store():
    from personalclaw.config.loader import config_dir
    from personalclaw.knowledge.store import KnowledgeStore

    path = config_dir() / "knowledge" / "knowledge.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeStore(db_path=str(path))
