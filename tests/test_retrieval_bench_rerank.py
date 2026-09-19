"""KBVS-2 — the relevance-reranker's row in the retrieval bench's published table.

The bench's own contract (`retrieval_bench.py` module docstring, §5) is unchanged: still
TWO stores, still the SAME ``ARMS`` vocabulary for the RRF ablation. This file proves the
one addition — a ``rerank`` row, measured only for the knowledge store, generic over the
SAME table/CLI/handler machinery every other row already uses (so neither needed a code
change), and reading "not measured" rather than a copy of the baseline's own P@k when the
reranker itself never got a usable model response.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.evals import retrieval_bench as rb
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.vector_memory import VectorMemoryStore

_ITEMS = {
    "RRF fusion notes": "Reciprocal rank fusion blends the keyword and vector lists.",
    "Postgres vacuum runbook": "Autovacuum thresholds and the manual VACUUM FULL hatch.",
}


@pytest.fixture()
def knowledge_store() -> KnowledgeStore:
    store = KnowledgeStore(str(knowledge_db_path()))
    for title, body in _ITEMS.items():
        store.create_typed_item(item_type="note", title=title, content=body)
    store.db.commit()
    try:
        yield store
    finally:
        store.db.close()


@pytest.fixture()
def bound_models():
    """Bind chat + embedding so the RunPin can complete (provider-agnostic refs survive
    pruning without a configured provider)."""
    from personalclaw.providers.use_cases import save_active_models

    save_active_models({"chat": ["test-chat"], "embedding": ["test-embed"]})


def _seeded_benchmark(store: KnowledgeStore) -> rb.RetrievalBenchmark:
    rows = store.db.execute("SELECT id, title FROM items ORDER BY title").fetchall()
    by_title = {r["title"]: r["id"] for r in rows}
    return rb.RetrievalBenchmark(
        name="retrieval-knowledge",
        store=rb.STORE_KNOWLEDGE,
        queries=(
            rb.QrelsQuery(
                query="vacuum runbook",
                relevant_ids=(by_title["Postgres vacuum runbook"],),
                source=rb.SOURCE_MINED_INTENT,
            ),
        ),
    )


def _echo_relevance_mock() -> AsyncMock:
    """A fake model that names EVERY candidate the prompt actually sent, so the reranker
    always counts as having 'run' without hardcoding the store's generated item ids."""

    async def _reply(prompt: str, **_kwargs) -> str:
        ids = re.findall(r'id="([^"]+)"', prompt)
        return "[" + ", ".join(f'{{"id": "{i}", "relevance": 5}}' for i in ids) + "]"

    return AsyncMock(side_effect=_reply)


#: Two documents that BOTH match the query below, so the rerank window genuinely has
#: something to reorder. A single-candidate window cannot express an ordering at all, so a
#: falsifier built on one would pass no matter what the stage did.
_RIVAL_ITEMS = {
    "Postgres vacuum runbook": "Autovacuum thresholds and the manual VACUUM FULL hatch.",
    "Postgres backup runbook": "Base backup plus WAL archiving for the postgres cluster.",
}
_RIVAL_QUERY = "postgres runbook"


@pytest.fixture()
def rival_store() -> KnowledgeStore:
    store = KnowledgeStore(str(knowledge_db_path()))
    for title, body in _RIVAL_ITEMS.items():
        store.create_typed_item(item_type="note", title=title, content=body)
    store.db.commit()
    try:
        yield store
    finally:
        store.db.close()


def _rank_mock(top_id: str) -> AsyncMock:
    """A fake model that ranks ONE named candidate top and every other one bottom."""

    async def _reply(prompt: str, **_kwargs) -> str:
        ids = re.findall(r'id="([^"]+)"', prompt)
        scored = ", ".join(f'{{"id": "{i}", "relevance": {9 if i == top_id else 0}}}' for i in ids)
        return f"[{scored}]"

    return AsyncMock(side_effect=_reply)


def test_the_rerank_row_is_computed_from_the_reranked_order_not_the_baseline(
    rival_store, bound_models
):
    """The published number's FALSIFIER: the row must MOVE when the model's order moves.

    ``test_rerank_row_appears_in_the_published_table_when_the_model_runs`` proves the arm
    RAN, but it scores every candidate equally, so the reranked order equals the RRF order
    and the row's P@k equals the baseline's. A bug that published the un-reranked
    baseline's own number under the ``rerank`` mask would pass it — the exact "a live model
    call plus a right-looking artifact hides a path whose output is never consumed" shape.

    So the same corpus and the same qrels are scored TWICE at ``k=1``, changing only which
    of the two rival documents the model ranks first. A row derived from the reranked order
    must read 1.0 when the relevant one is promoted and 0.0 when it is demoted; a row
    secretly carrying the baseline's own number could not differ between the two runs, no
    matter which way RRF happened to order them. Deliberately NOT asserted against the
    baseline row: that would make the test depend on RRF's own tie-break between two
    near-identical documents, which is not what this is measuring.
    """
    rows = rival_store.db.execute("SELECT id, title FROM items").fetchall()
    by_title = {r["title"]: r["id"] for r in rows}
    relevant_id = by_title["Postgres vacuum runbook"]
    rival_id = by_title["Postgres backup runbook"]
    benchmark = rb.RetrievalBenchmark(
        name="retrieval-knowledge",
        store=rb.STORE_KNOWLEDGE,
        queries=(
            rb.QrelsQuery(
                query=_RIVAL_QUERY, relevant_ids=(relevant_id,), source=rb.SOURCE_MINED_INTENT
            ),
        ),
    )

    def _rerank_p_at_k(top_id: str) -> float | None:
        with patch("personalclaw.llm_helpers.one_shot_completion", new=_rank_mock(top_id)):
            result = rb.run_retrieval_bench(
                rb.STORE_KNOWLEDGE,
                handle=rival_store,
                db_path=rival_store.db_path,
                benchmark=benchmark,
                k=1,
            )
        by_mask = {row.mask: row for row in result.table}
        assert by_mask[rb.mask_name(rb.ARMS)].p_at_k is not None, (
            "both rival documents must be retrievable for this query, else the rerank "
            "window has nothing to reorder and the falsifier is vacuous"
        )
        return by_mask[rb.RERANK_MASK].p_at_k

    promoted = _rerank_p_at_k(relevant_id)
    demoted = _rerank_p_at_k(rival_id)
    assert promoted == 1.0, f"promoting the relevant doc must score it at rank 1: {promoted}"
    assert demoted == 0.0, f"demoting the relevant doc must drop it out of k=1: {demoted}"


def test_rerank_row_appears_in_the_published_table_when_the_model_runs(
    knowledge_store, bound_models
):
    with patch("personalclaw.llm_helpers.one_shot_completion", new=_echo_relevance_mock()):
        result = rb.run_retrieval_bench(
            rb.STORE_KNOWLEDGE,
            handle=knowledge_store,
            db_path=knowledge_store.db_path,
            benchmark=_seeded_benchmark(knowledge_store),
        )
    by_mask = {row.mask: row for row in result.table}
    assert rb.RERANK_MASK in by_mask, "the rerank row never made it into the published table"
    row = by_mask[rb.RERANK_MASK]
    assert row.p_at_k is not None, "the model ran; this must be a real measurement"
    assert row.scored_queries == 1


def test_rerank_row_reads_not_measured_when_the_model_never_runs(knowledge_store, bound_models):
    """No provider bound in this test environment: `one_shot_completion` fails naturally, so
    the row must read 'not measured' (p_at_k/r_at_k None) rather than silently publishing the
    un-reranked fallback's own P@k as if it were a measurement of the reranker."""
    result = rb.run_retrieval_bench(
        rb.STORE_KNOWLEDGE,
        handle=knowledge_store,
        db_path=knowledge_store.db_path,
        benchmark=_seeded_benchmark(knowledge_store),
    )
    by_mask = {row.mask: row for row in result.table}
    assert rb.RERANK_MASK in by_mask
    row = by_mask[rb.RERANK_MASK]
    assert row.p_at_k is None
    assert row.r_at_k is None
    rerank_scores = [s for s in result.scores if s.mask == rb.RERANK_MASK]
    assert rerank_scores and all(s.reason == rb.REASON_RERANK_UNAVAILABLE for s in rerank_scores)


def test_rerank_arm_does_not_widen_the_three_arm_ablation():
    """The RRF ablation's own vocabulary — ARMS, its masks, and the leave-one-out
    contributions — is untouched by the rerank row riding along in the same table."""
    assert rb.RERANK_MASK not in rb.ARMS
    assert rb.RERANK_MASK not in [rb.mask_name(m) for m in rb.ablation_masks()]


def test_rerank_arm_is_knowledge_only_the_memory_store_gets_no_row():
    store = VectorMemoryStore()
    store.init()
    store.set_semantic("pref.test", "a memory fact", 0.9, "test")
    store.db.commit()
    try:
        from personalclaw.providers.use_cases import save_active_models

        save_active_models({"chat": ["test-chat"], "embedding": ["test-embed"]})
        benchmark = rb.RetrievalBenchmark(
            name="retrieval-memory",
            store=rb.STORE_MEMORY,
            queries=(
                rb.QrelsQuery(query="pref", relevant_ids=("pref.test",), source="hand_label"),
            ),
        )
        result = rb.run_retrieval_bench(rb.STORE_MEMORY, handle=store, benchmark=benchmark)
    finally:
        store.close()
    by_mask = {row.mask: row for row in result.table}
    assert rb.RERANK_MASK not in by_mask
