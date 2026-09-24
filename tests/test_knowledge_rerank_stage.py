"""KBVS-2 — the relevance-rerank stage on `HybridRetriever.search()`.

What these tests are for:

1. **Off unless configured or forced.** The done_when clause is verbatim "OFF unless
   configured" — proven here by asserting the model seam is never even CALLED when
   ``rerank`` is unspecified and the config default (off) is in force.
2. **Fails OPEN, never breaks a search.** A model/transport failure, an empty response
   (the small-output-budget trap), or a response that names no real candidate id must all
   degrade to the un-reranked RRF order — reranking is a relevance stage, not a security
   control (core AGENTS.md "Shared conventions" — fail-open/closed).
3. **Rides the existing use-case seam with no vendor branch.** The model call goes through
   ``personalclaw.llm_helpers.one_shot_completion(use_case="reasoning", ...)`` — the same
   seam every other one-shot judgment call in this codebase uses.
4. **The candidate window is real.** A tail item beyond the configured window keeps its
   RRF position untouched.

Direct calls to the private `_apply_rerank`/`_rerank_score`/`_rerank_wanted` methods mirror
this module's own convention (`test_retrieval_arm_qualification.py` calls
`HybridRetriever._rrf_fuse` directly) — these are the unit of behavior the done_when clause
is actually about, independent of FTS5/vector ranking mechanics.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.knowledge import retrieval as knowledge_retrieval
from personalclaw.knowledge.retrieval import HybridRetriever
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path):
    s = KnowledgeStore(str(tmp_path / "rerank.db"))
    yield s
    s.close()


def _mock_one_shot(return_value=None, side_effect=None):
    kwargs = {}
    if side_effect is not None:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = return_value
    return patch("personalclaw.llm_helpers.one_shot_completion", new=AsyncMock(**kwargs))


# ── 1. off unless configured or forced ─────────────────────────────────────────────────────


def test_rerank_off_by_default_never_calls_the_model(store):
    """`rerank=None` (every production caller) + the shipped config default (off) must
    never reach the model seam at all — not "call it and ignore the answer"."""
    retriever = HybridRetriever(store)
    with _mock_one_shot(return_value='[{"id": "x", "relevance": 10}]') as mocked:
        retriever.search("anything", limit=5)
    mocked.assert_not_awaited()
    assert retriever.last_rerank_executed is False


def test_rerank_wanted_resolves_from_config_when_unspecified(store, monkeypatch):
    """`rerank=None` reads the live config: off by default, on once configured."""
    from personalclaw.config.loader import AppConfig

    off = AppConfig()
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: off))
    retriever = HybridRetriever(store)
    assert retriever._rerank_wanted(None) is False

    on = AppConfig()
    on.knowledge.rerank_enabled = True
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: on))
    assert retriever._rerank_wanted(None) is True


def test_rerank_forced_flag_overrides_config(store, monkeypatch):
    """`True`/`False` FORCE the stage regardless of what config says — the retrieval
    bench's own knob, so it can measure both arms on every run."""
    from personalclaw.config.loader import AppConfig

    on = AppConfig()
    on.knowledge.rerank_enabled = True
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: on))
    retriever = HybridRetriever(store)
    assert retriever._rerank_wanted(False) is False
    assert retriever._rerank_wanted(True) is True


def test_search_end_to_end_reranks_when_forced(store):
    """The full `search()` path: real FTS5 retrieval, mocked model relevance, forced on."""
    a = store.create_typed_item(
        item_type="note", title="Sourdough starter", content="feed the starter daily"
    )
    b = store.create_typed_item(
        item_type="note", title="Sourdough hydration", content="78 percent hydration crumb"
    )
    store.db.commit()
    response = f'[{{"id": "{a}", "relevance": 1}}, {{"id": "{b}", "relevance": 9}}]'
    with _mock_one_shot(return_value=response):
        hits = HybridRetriever(store).search("sourdough", limit=5, rerank=True)
    assert [h["id"] for h in hits] == [b, a]


# ── 2. fails open ────────────────────────────────────────────────────────────────────────────


def _fused_and_items():
    fused = [("a", 5.0), ("b", 4.0), ("c", 3.0), ("d", 2.0), ("e", 1.0)]
    items = {
        iid: {"title": iid.upper(), "content": f"content for {iid}", "summary": None}
        for iid, _ in fused
    }
    return fused, items


def test_rerank_reorders_by_model_relevance(store):
    """A model response that inverts the RRF order must be honored — proving the stage
    actually reorders rather than merely running and discarding the answer."""
    retriever = HybridRetriever(store)
    fused, items = _fused_and_items()
    # RRF had a > b > c > d > e; the model prefers e most, a least.
    response = (
        '[{"id": "a", "relevance": 0}, {"id": "b", "relevance": 2}, '
        '{"id": "c", "relevance": 4}, {"id": "d", "relevance": 6}, '
        '{"id": "e", "relevance": 8}]'
    )
    with _mock_one_shot(return_value=response) as mocked:
        out = retriever._apply_rerank("q", fused, items, limit=5)
    mocked.assert_awaited_once()
    assert mocked.await_args.kwargs.get("use_case") == "reasoning"
    assert [iid for iid, _ in out] == ["e", "d", "c", "b", "a"]
    assert retriever.last_rerank_executed is True


def test_rerank_falls_back_to_rrf_order_on_model_exception(store):
    """A model/transport failure must fall back to the un-reranked order, not raise."""
    retriever = HybridRetriever(store)
    fused, items = _fused_and_items()
    with _mock_one_shot(side_effect=RuntimeError("no model bound")):
        out = retriever._apply_rerank("q", fused, items, limit=5)
    assert out == fused
    assert retriever.last_rerank_executed is False


def test_rerank_falls_back_on_empty_response(store):
    """The thinking-model-small-budget trap: empty content must degrade, not crash or
    read as 'every candidate scored zero'."""
    retriever = HybridRetriever(store)
    fused, items = _fused_and_items()
    with _mock_one_shot(return_value=""):
        out = retriever._apply_rerank("q", fused, items, limit=5)
    assert out == fused
    assert retriever.last_rerank_executed is False


def test_rerank_falls_back_when_response_names_no_real_candidate(store):
    """A syntactically valid response that names none of the actual candidate ids is a
    degenerate answer, not a real measurement — must fall back exactly like a parse miss."""
    retriever = HybridRetriever(store)
    fused, items = _fused_and_items()
    with _mock_one_shot(return_value='[{"id": "not-a-real-id", "relevance": 9}]'):
        out = retriever._apply_rerank("q", fused, items, limit=5)
    assert out == fused
    assert retriever.last_rerank_executed is False


def test_rerank_falls_back_on_non_list_response(store):
    """`output_type=list` is a request, not a guarantee — a dict response must also
    fall back rather than raise."""
    retriever = HybridRetriever(store)
    fused, items = _fused_and_items()
    with _mock_one_shot(return_value='{"id": "a", "relevance": 10}'):
        out = retriever._apply_rerank("q", fused, items, limit=5)
    assert out == fused
    assert retriever.last_rerank_executed is False


# ── 3. the candidate window ──────────────────────────────────────────────────────────────────


def test_rerank_window_leaves_the_tail_untouched(store, monkeypatch):
    """Only the configured window is sent to the model; anything beyond it keeps its RRF
    position exactly, appended back unscored."""
    from personalclaw.config.loader import AppConfig

    stub = AppConfig()
    stub.knowledge.rerank_candidates = 3
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: stub))

    retriever = HybridRetriever(store)
    fused, items = _fused_and_items()  # 5 candidates: a, b, c, d, e
    # Invert only what the model is shown (a, b, c) — d and e are the tail.
    response = (
        '[{"id": "a", "relevance": 0}, {"id": "b", "relevance": 5}, {"id": "c", "relevance": 10}]'
    )
    with _mock_one_shot(return_value=response) as mocked:
        out = retriever._apply_rerank("q", fused, items, limit=2)
    # Window sizing takes max(limit, candidates) = max(2, 3) = 3 candidates shown.
    sent_prompt = mocked.await_args.args[0]
    assert 'id="d"' not in sent_prompt and 'id="e"' not in sent_prompt
    assert [iid for iid, _ in out] == ["c", "b", "a", "d", "e"]


# ── 4. no vendor branch, and the arm vocabulary stays 3 ──────────────────────────────────────


def test_rerank_is_not_a_fourth_fusion_arm():
    """The rerank stage must never widen ARMS — it is a post-fusion stage, not a parallel
    retrieval source (KBVS-2's own framing: 'a stage, not a fourth arm')."""
    assert tuple(knowledge_retrieval.ARMS) == ("keyword", "graph", "vector")
