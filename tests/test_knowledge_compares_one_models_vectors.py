"""A knowledge vector is compared only with vectors the same embedding model wrote.

Memory keeps to one rule (``vector_memory._OF_MODEL``): a query is compared with the vectors the
model bound now wrote, and two stored vectors only when both record the same model. Knowledge kept
to it for passage (chunk) vectors alone. Measured on the base before this change, five
comparisons crossed models at one width, where no width guard can see the difference:

1. search's whole-item arm scored every item vector of the query's width, whoever wrote it;
2. the ingest dedup compared a new item with every same-titled item's vector and ARCHIVED the
   loser, so a cosine between two models' vectors could take a document out of the library;
3. the duplicates panel did the same and offered a merge, which deletes one of the two;
4. the similarity edges scored the passages of two items two different models embedded;
5. the structural rank scored a candidate's item vector whoever wrote it.

And the surfaces that say what search skips said less than it skips: search's ``stale_index`` note
and the Doctor row named only the items whose PASSAGES were stale, and the Knowledge page's stale
count compared whole-item widths alone, so it named neither a same-width switch nor a passage.

Every exclusion below is asserted beside its positive control, a same-model twin that IS compared,
so a comparison that was switched off entirely fails these tests instead of passing them.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.knowledge import structural as S
from personalclaw.knowledge.chunking import Chunk
from personalclaw.knowledge.embedder import floats_to_bytes
from personalclaw.knowledge.retrieval import HybridRetriever
from personalclaw.knowledge.searchability import STALE_INDEX
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

#: Two different models at one width: every width guard passes both, only the record differs.
MODEL_A = ("all-minilm-l6-v2", "native")
MODEL_B = ("bge-small-en", "native")

#: The vector every fixture embedder returns. Identical vectors from two models are the sharpest
#: form of the defect: a cosine of exactly 1.0 between two spaces, which means nothing.
VEC = [1.0, 0.0, 0.0, 0.0]

#: Matches nothing in the corpus, so keyword and graph arms add nothing and only vectors can hit.
UNMATCHABLE_QUERY = "zzqqxx"


def _bind(monkeypatch, spec: tuple[str, str] | None) -> None:
    """Bind the embedding selection every fingerprint reader and writer resolves through."""
    provider_model = None if spec is None else (spec[1], spec[0])
    monkeypatch.setattr(
        "personalclaw.embedding_providers.registry._active_embedding_spec",
        lambda: provider_model,
    )


class _Embedder:
    """A bound embedding provider returning one vector for every text."""

    def __init__(self, vector=VEC):
        self._vector = list(vector)

    def embed(self, text):
        return list(self._vector)

    def embed_for_item(self, title, summary, content=None):
        return list(self._vector)

    def is_available(self):
        return True

    def dim(self):
        return len(self._vector)


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "k.db"))


def _note(store, title: str, *, content: str, tags=None) -> str:
    item_id = store.create_typed_item(item_type="note", title=title, content=content, tags=tags)
    assert item_id, "fixture item was not created"
    return item_id


def _embed_items(store, monkeypatch, model, vector=VEC) -> None:
    """Embed every item without a vector while *model* is bound, through the product's writer
    (``reembed_all``), which records that model on each."""
    _bind(monkeypatch, model)
    store.reembed_all(_Embedder(vector), only_missing=True)


def _embed_chunks(store, monkeypatch, item_id: str, model, vector=VEC) -> None:
    """Give *item_id* one passage vector, written while *model* is bound (``replace_chunks``
    records the model, and writes the ANN index as ingest does)."""
    _bind(monkeypatch, model)
    chunk = Chunk(
        text=f"passage of {item_id}", section=None, line_start=1, line_end=1, chunk_index=0
    )
    chunk.embedding = floats_to_bytes(vector)
    assert store.replace_chunks(item_id, [chunk]) == 1


def _model_of(store, item_id: str) -> tuple:
    row = store.db.execute(
        "SELECT embedding_model_id, embedding_provider FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    return (row[0], row[1])


def _request(store, method: str, match_info=None):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    return make_mocked_request(method, "/", app=app, match_info=match_info or {})


def _index_mode(store, mode: str) -> None:
    """``ann`` keeps the sqlite-vec index (asserted loaded, so the case cannot pass on the other
    path); ``exact`` removes it, so the full-scan fallback answers."""
    if mode == "ann":
        assert store.vec_index is not None and store.vec_index.enabled, "sqlite-vec must load"
    else:
        store.vec_index = None


# ── 1. search's whole-item arm ─────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["ann", "exact"])
def test_search_scores_an_item_vector_only_if_the_bound_model_wrote_it(store, monkeypatch, mode):
    theirs = _note(store, "Travel notes", content="packing list")
    _embed_items(store, monkeypatch, MODEL_B)
    ours = _note(store, "Quarterly plan", content="hiring and budget")
    _embed_items(store, monkeypatch, MODEL_A)
    assert (_model_of(store, theirs), _model_of(store, ours)) == (MODEL_B, MODEL_A)
    _index_mode(store, mode)

    outcome = HybridRetriever(store, embedder=lambda _q: list(VEC)).search_with_diagnostics(
        UNMATCHABLE_QUERY, limit=5
    )

    assert [r["id"] for r in outcome.results] == [ours], (
        "the query was embedded by the model bound now, so another model's vector is skipped; "
        "the item it wrote is the positive control"
    )
    stale = {d.reason: d for d in outcome.degradations}[STALE_INDEX]
    assert stale.item_ids == (theirs,), "what search skipped is named, and only that"
    assert "or with no model recorded" in stale.detail


# ── 2. the ingest dedup, which archives ────────────────────────────────────────────


def test_the_ingest_dedup_never_archives_on_a_cosine_between_two_models(store, monkeypatch):
    from personalclaw.knowledge.pipeline.runner import _dedup

    older = _note(store, "Quarterly plan", content="first copy")
    _embed_items(store, monkeypatch, MODEL_B)
    newer = _note(store, "Quarterly plan", content="second copy")
    _embed_items(store, monkeypatch, MODEL_A)

    phase, verdict = _dedup(store, newer, _Embedder())
    assert (phase.status, verdict) == ("done", None)
    archived = {r[0] for r in store.db.execute("SELECT id FROM items WHERE is_archived = 1")}
    assert archived == set(), "a score between two models' vectors archived a document"

    # Positive control: a third copy embedded by the model that embedded `newer` is compared,
    # found to duplicate it, and one of the two is archived.
    third = _note(store, "Quarterly plan", content="third copy")
    _embed_items(store, monkeypatch, MODEL_A)
    phase, verdict = _dedup(store, third, _Embedder())
    assert phase.status == "done" and verdict is not None
    assert {verdict["winner_id"], verdict["loser_id"]} == {newer, third}
    assert older not in {verdict["winner_id"], verdict["loser_id"]}


# ── 3. the duplicates panel, which offers a merge that deletes ─────────────────────


def test_the_duplicates_panel_offers_no_merge_across_models_and_says_what_it_skipped(
    store, monkeypatch
):
    from personalclaw.dashboard.handlers import knowledge as H

    elsewhere = _note(store, "Quarterly plan", content="first copy")
    _embed_items(store, monkeypatch, MODEL_B)
    anchor = _note(store, "Quarterly plan", content="second copy")
    twin = _note(store, "Quarterly plan", content="third copy")
    _embed_items(store, monkeypatch, MODEL_A)

    resp = asyncio.run(H.get_item_duplicates(_request(store, "GET", {"id": anchor})))
    body = json.loads(resp.body)

    assert [d["id"] for d in body["duplicates"]] == [
        twin
    ], "the same-model twin is the positive control; the other model's copy is never scored"
    assert elsewhere not in {d["id"] for d in body["duplicates"]}
    assert body["not_compared"] == 1
    assert body["not_compared_note"].startswith(
        "1 item titled like this one was not compared with it: a different embedding model"
    )

    # With nothing skipped the note is empty, so the panel says nothing extra.
    _bind(monkeypatch, MODEL_A)
    solo = _note(store, "Unrelated", content="nothing alike")
    _embed_items(store, monkeypatch, MODEL_A)
    none = json.loads(asyncio.run(H.get_item_duplicates(_request(store, "GET", {"id": solo}))).body)
    assert (none["duplicates"], none["not_compared"], none["not_compared_note"]) == ([], 0, "")


# ── 4. the similarity edges ────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["ann", "exact"])
def test_similarity_edges_link_passages_only_when_one_model_wrote_both(store, monkeypatch, mode):
    from personalclaw.knowledge import similarity_edges

    a = _note(store, "A", content="body of A")
    b = _note(store, "B", content="body of B")
    c = _note(store, "C", content="body of C")
    _embed_chunks(store, monkeypatch, a, MODEL_A)
    _embed_chunks(store, monkeypatch, b, MODEL_B)
    _embed_chunks(store, monkeypatch, c, MODEL_A)
    _bind(monkeypatch, MODEL_A)
    _index_mode(store, mode)

    written = similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.5)

    pairs = {
        frozenset((r[0], r[1]))
        for r in store.db.execute(
            "SELECT source_item_id, target_item_id FROM item_similarity_edges"
        )
    }
    assert pairs == {frozenset((a, c))}, "C is the positive control; B's passage is another space"
    assert written == 1


# ── 5. the structural rank ─────────────────────────────────────────────────────────


def test_the_structural_rank_scores_only_the_bound_models_item_vectors(store, monkeypatch):
    theirs = _note(store, "Runbook one", content="first", tags=["ops"])
    _embed_items(store, monkeypatch, MODEL_B)
    ours = _note(store, "Runbook two", content="second", tags=["ops"])
    _embed_items(store, monkeypatch, MODEL_A)

    answer = S.StructuralRetriever(store, embedder=lambda _q: list(VEC)).query(
        S.TAG_SUBTREE, origin="ops", rank_query="anything"
    )

    assert answer.rank_mode == S.RANK_VECTOR
    scores = {h.item_id: h.score for h in answer.hits}
    assert scores[ours] == pytest.approx(1.0), "positive control: the bound model's vector scores"
    assert scores[theirs] == 0.0, "another model's vector scores as no vector: nothing to compare"
    assert {h.item_id for h in answer.hits} == {ours, theirs}, "ranking never drops a member"


# ── 6. the Doctor row ──────────────────────────────────────────────────────────────


def test_the_doctor_row_names_an_item_whose_whole_item_vector_is_stale(tmp_path, monkeypatch):
    from personalclaw.resilience.doctor import DoctorContext, all_probes

    home = tmp_path / "home"
    home.mkdir()
    store = KnowledgeStore(str(knowledge_db_path(home)))
    theirs = _note(store, "Travel notes", content="packing list")
    _embed_items(store, monkeypatch, MODEL_B)
    _bind(monkeypatch, MODEL_A)

    probe = next(p for p in all_probes() if p.id == "knowledge.searchability")
    result = asyncio.run(probe.run(DoctorContext(home=home)))

    assert result.ok is False
    assert result.evidence["by_reason"] == {STALE_INDEX: 1}
    assert [row["item_id"] for row in result.evidence["items"]] == [theirs]

    # Its negative case: under the model that wrote the vector, the row is clean.
    _bind(monkeypatch, MODEL_B)
    assert asyncio.run(probe.run(DoctorContext(home=home))).ok is True


# ── 7. the Knowledge page's stale count ────────────────────────────────────────────


def test_the_knowledge_pages_stale_count_is_what_search_skips(store, monkeypatch):
    from personalclaw.dashboard.handlers import knowledge as H

    whole_item = _note(store, "Reading list", content="three books")
    _embed_items(store, monkeypatch, MODEL_B)
    passage_only = _note(store, "Travel notes", content="packing list")
    current = _note(store, "Quarterly plan", content="hiring and budget")
    _embed_items(store, monkeypatch, MODEL_A)
    _embed_chunks(store, monkeypatch, passage_only, MODEL_B)
    _embed_chunks(store, monkeypatch, current, MODEL_A)
    _bind(monkeypatch, MODEL_A)

    named = {
        d.reason: d
        for d in HybridRetriever(store, embedder=lambda _q: list(VEC))
        .search_with_diagnostics(UNMATCHABLE_QUERY, limit=5)
        .degradations
    }[STALE_INDEX].item_ids

    assert set(named) == {whole_item, passage_only}, "the positive control: search's own note"
    assert H._stale_embedding_count(store, _Embedder()) == len(
        named
    ), "the chip counts the items the note names, a stale passage included"
