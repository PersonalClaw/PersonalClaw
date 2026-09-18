"""RET-4 — a same-dimension embedding-model swap is DETECTED, and the re-index cannot lie.

**The defect, measured on ``origin/main`` @ ``4341bc4ee`` before this rail existed.**
``chunks`` carried ``id, item_id, chunk_index, text, embedding, section, line_start,
line_end`` and **no model column** (``git grep -n embedding_model_id -- src/`` returned
zero hits). Two guards existed and neither could see this case:

* ``knowledge/vector_index.py`` partitions the ANN index per DIMENSION with the dimension
  in the table name (``chunk_vec_384``);
* ``HybridRetriever._consider`` skips any stored vector whose length differs from the live
  query vector.

Both are dimension tests. ``all-MiniLM-L6-v2``, ``bge-small-en`` and ``gte-small`` are all
384-dimensional, so swapping between them produced vectors that were **exactly as long as
the guard wanted and from a different vector space**. Worse, the re-index could not have
fixed it even if it noticed: ``clear_embeddings`` + ``reembed_all`` rewrite the ITEM
vectors and never touch ``chunks``, and the chunk backfill only visits items with NO chunk
rows — so every passage vector survived a model switch untouched, forever.

**What this rail asserts, and why each clause is not cheatable:**

1. every chunk row records the model + provider that embedded it — asserted against the
   COLUMNS, so storing the fingerprint nowhere fails;
2. after a swap to a DIFFERENT MODEL OF THE SAME DIMENSION, a query whose only possible
   hit is a stale chunk vector returns **nothing from the vector arm** and
   ``knowledge_search`` names ``stale_index`` — the lazy "store the fingerprint but never
   compare it at query time" implementation fails here, because
   ``test_the_rail_reds_when_the_query_side_comparison_is_removed`` removes exactly that
   comparison and watches the hit come back;
3. the re-index reports the **integer count of rows it changed** and leaves **zero**
   old-fingerprint chunks, read back from the table — and
   ``test_a_reindex_that_cannot_embed_everything_refuses_to_report_done`` proves the count
   comes from the rows rather than from a job-status field, by failing one chunk's embed
   and watching the job go red with the remainder named;
4. the re-embed writes the NEW model's vector, not just its label: the post-re-index query
   that hits is the one embedded by model B, and the model-A query stops hitting.

**Its own negative cases** (a rail that cannot fail is not a rail): with nothing bound
there is no staleness concept and chunks still score (that state is RET-2's
``no_embedding_provider``); a chunk written under the model that is still bound is never
flagged; and ``stale_index`` is never persisted as an ingest status, because the fact that
changed is the bound model and not the item.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from personalclaw.knowledge.chunking import Chunk
from personalclaw.knowledge.embedding_fingerprint import (
    EmbeddingFingerprint,
    active_fingerprint,
    count_stale_chunks,
    has_fingerprint_columns,
)
from personalclaw.knowledge.retrieval import HybridRetriever
from personalclaw.knowledge.searchability import (
    INGEST_REASONS,
    REASONS,
    STALE_INDEX,
    verdict_for_ingest,
)
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

#: Two DIFFERENT models at the SAME dimension — the whole point of the atom. Both produce
#: 4-component vectors below, so every dimension guard in the product is satisfied by both
#: and only the fingerprint can tell them apart.
MODEL_A = ("all-minilm-l6-v2", "native")
MODEL_B = ("bge-small-en", "native")

#: Orthogonal unit vectors, one per model. Orthogonality is load-bearing: it makes "the
#: vector was re-embedded by B" and "the vector is still A's" distinguishable by a query
#: instead of only by reading a column.
VEC_A = [1.0, 0.0, 0.0, 0.0]
VEC_B = [0.0, 1.0, 0.0, 0.0]

#: A token that appears NOWHERE in the corpus, so the keyword and graph arms contribute
#: nothing and the only possible hit is the chunk vector arm. Without this the test would
#: pass on FTS alone and assert nothing about vectors.
UNMATCHABLE_QUERY = "zzqqxx"


def _bind(monkeypatch, spec: tuple[str, str] | None) -> None:
    """Bind (or unbind) the active embedding selection the fingerprint accessor reads.

    Patches the ONE accessor ``active_fingerprint`` resolves through, so the write path,
    the query path, the re-index and the Doctor all see the same swap a user performs in
    Settings → Models.
    """
    provider_model = None if spec is None else (spec[1], spec[0])
    monkeypatch.setattr(
        "personalclaw.embedding_providers.registry._active_embedding_spec",
        lambda: provider_model,
    )


class _Embedder:
    """A stand-in for a bound embedding provider that returns ONE model's vector."""

    def __init__(self, vector, *, fail_texts: frozenset[str] = frozenset()):
        self._vector = list(vector)
        self._fail_texts = fail_texts

    def embed(self, text):
        if text in self._fail_texts:
            return None
        return list(self._vector)

    def embed_for_item(self, title, summary, content=None):
        return list(self._vector)

    def is_available(self):
        return True


def _store(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(str(tmp_path / "k.db"))


def _item_with_one_chunk(store, embedder, *, title="Quarterly plan", text="the passage") -> str:
    """One item whose ONLY vector is a chunk vector (the item row keeps no embedding)."""
    item_id = store.create_typed_item(item_type="note", title=title, content="body")
    chunk = Chunk(text=text, section=None, line_start=1, line_end=1)
    chunk.embedding = _blob(embedder.embed(text))
    store.replace_chunks(item_id, [chunk])
    return item_id


def _blob(vector):
    from personalclaw.knowledge.embedder import floats_to_bytes

    return floats_to_bytes(vector)


def _fingerprints(store) -> list[tuple]:
    rows = store.db.execute(
        "SELECT embedding_model_id, embedding_provider FROM chunks ORDER BY id"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _retriever(store, vector) -> HybridRetriever:
    """A retriever whose query embedder is one model's vector — the query side of a swap."""
    return HybridRetriever(store, embedder=lambda _q: list(vector))


# ── Clause 1: the fingerprint is recorded ────────────────────────────────────────


def test_every_chunk_vector_records_its_model_and_provider(tmp_path, monkeypatch):
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    _item_with_one_chunk(store, _Embedder(VEC_A))

    assert _fingerprints(store) == [("all-minilm-l6-v2", "native")]
    assert has_fingerprint_columns(store.db)


def test_an_upgraded_database_leaves_its_old_chunks_unstamped_rather_than_guessing(
    tmp_path, monkeypatch
):
    """The migration must not back-stamp: nothing knows which model wrote those vectors.

    Simulates a database written before the columns existed by dropping them, then reopens
    the store so ``_migrate_chunk_fingerprint`` runs. A back-stamping migration would make
    the whole library read as fresh — the exact silent comparison this atom removes.
    """
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    _item_with_one_chunk(store, _Embedder(VEC_A))
    store.db.execute("ALTER TABLE chunks DROP COLUMN embedding_model_id")
    store.db.execute("ALTER TABLE chunks DROP COLUMN embedding_provider")
    store.db.commit()
    store.db.close()

    reopened = _store(tmp_path)
    assert has_fingerprint_columns(reopened.db)
    assert _fingerprints(reopened) == [(None, None)]
    # NULL provenance reads as stale, so the library reports work to do rather than health.
    assert reopened.count_stale_chunk_vectors() == 1


# ── Clause 2: the query side refuses to score a stale vector ─────────────────────


def test_a_same_dimension_model_swap_stops_the_stale_chunk_from_scoring(tmp_path, monkeypatch):
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    item_id = _item_with_one_chunk(store, _Embedder(VEC_A))

    # Before the swap the chunk vector is the only reason this item can be found, and it is.
    before = _retriever(store, VEC_A).search(UNMATCHABLE_QUERY, limit=5)
    assert [r["id"] for r in before] == [item_id], "precondition: the chunk arm is the only hit"

    # Swap to a DIFFERENT model of the SAME dimension. Nothing about the item changed.
    _bind(monkeypatch, MODEL_B)
    assert store.count_stale_chunk_vectors() == 1

    after = _retriever(store, VEC_A).search(UNMATCHABLE_QUERY, limit=5)
    assert after == [], "a vector from the previous model must not be scored against the new one"


def test_knowledge_search_names_stale_index_instead_of_answering_nothing_found(
    tmp_path, monkeypatch
):
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    item_id = _item_with_one_chunk(store, _Embedder(VEC_A))
    _bind(monkeypatch, MODEL_B)

    outcome = _retriever(store, VEC_B).search_with_diagnostics(UNMATCHABLE_QUERY, limit=5)

    assert outcome.results == []
    assert outcome.degraded
    reasons = {d.reason: d for d in outcome.degradations}
    assert STALE_INDEX in reasons, "an empty result set with a stale index must say so"
    degradation = reasons[STALE_INDEX]
    assert degradation.item_count == 1
    assert degradation.item_ids == (item_id,)
    assert "different embedding model" in degradation.detail
    # The vocabulary is RET-2's, extended — not a second one minted here.
    assert STALE_INDEX in REASONS


def test_the_rail_reds_when_the_query_side_comparison_is_removed(tmp_path, monkeypatch):
    """The lazy implementation this rail exists to reject.

    "Store the fingerprint but never compare it at query time" is exactly what removing the
    fresh-predicate from the chunk arm produces. With the comparison gone the stale chunk
    scores again — so the assertion above is testing the comparison, not the column.
    """
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    item_id = _item_with_one_chunk(store, _Embedder(VEC_A))
    _bind(monkeypatch, MODEL_B)

    assert _retriever(store, VEC_A).search(UNMATCHABLE_QUERY, limit=5) == []

    # Neuter only the query-time comparison; the column and its value stay exactly as they
    # are. This is the state of the code before this atom.
    monkeypatch.setattr(
        "personalclaw.knowledge.retrieval.active_fingerprint",
        lambda: None,
    )
    leaked = _retriever(store, VEC_A).search(UNMATCHABLE_QUERY, limit=5)
    assert [r["id"] for r in leaked] == [
        item_id
    ], "with the comparison removed the stale vector scores again — the rail is not vacuous"


# ── Clause 3 + 4: the re-index reports a count from the rows, and cannot lie ─────


def test_the_reindex_reports_the_integer_count_it_changed_and_leaves_zero_stale_rows(
    tmp_path, monkeypatch
):
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    embedder_a = _Embedder(VEC_A)
    first = _item_with_one_chunk(store, embedder_a, title="Alpha", text="passage one")
    second = _item_with_one_chunk(store, embedder_a, title="Beta", text="passage two")
    assert store.count_stale_chunk_vectors() == 0

    _bind(monkeypatch, MODEL_B)
    assert store.count_stale_chunk_vectors() == 2

    report = store.reembed_stale_chunks(_Embedder(VEC_B))

    assert report["reembedded"] == 2, "the count is the number of rows it actually re-embedded"
    assert report["failed"] == 0
    assert report["stale_remaining"] == 0
    assert report["fingerprint"] == "native:bge-small-en"
    # Read back from the store: ZERO chunks still carry the previous model's fingerprint.
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM chunks c "
            "WHERE COALESCE(c.embedding_model_id,'') != 'bge-small-en'"
        ).fetchone()[0]
        == 0
    )
    assert _fingerprints(store) == [("bge-small-en", "native")] * 2

    # And the vectors themselves moved into B's space: B's query hits, A's no longer does.
    found = _retriever(store, VEC_B).search(UNMATCHABLE_QUERY, limit=5)
    assert sorted(r["id"] for r in found) == sorted([first, second])
    assert _retriever(store, VEC_A).search(UNMATCHABLE_QUERY, limit=5) == []
    # The typed reason is gone with the staleness it described.
    assert (
        _retriever(store, VEC_B).search_with_diagnostics(UNMATCHABLE_QUERY, limit=5).degradations
        == ()
    )


def test_chunk_ids_survive_a_reindex(tmp_path, monkeypatch):
    """An UPDATE, not a delete+insert: a citation to a chunk id must not be orphaned."""
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    _item_with_one_chunk(store, _Embedder(VEC_A))
    before = [r[0] for r in store.db.execute("SELECT id FROM chunks ORDER BY id")]

    _bind(monkeypatch, MODEL_B)
    store.reembed_stale_chunks(_Embedder(VEC_B))

    after = [r[0] for r in store.db.execute("SELECT id FROM chunks ORDER BY id")]
    assert after == before


def test_a_reindex_that_cannot_embed_everything_refuses_to_report_done(tmp_path, monkeypatch):
    """The count comes from the ROWS, so a partial pass cannot report success.

    Drives the real job (``ReindexRegistry``), not just the store primitive, because the
    clause is about what the re-index OPERATION reports to the user.
    """
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry

    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    embedder_a = _Embedder(VEC_A)
    _item_with_one_chunk(store, embedder_a, title="Alpha", text="passage one")
    _item_with_one_chunk(store, embedder_a, title="Beta", text="passage two")
    _bind(monkeypatch, MODEL_B)

    # Model B can embed the first passage and not the second.
    embedder_b = _Embedder(VEC_B, fail_texts=frozenset({"passage two"}))
    registry = ReindexRegistry()

    async def _run():
        job, error = registry.start(
            model="native:bge-small-en",
            knowledge_store=store,
            vector_store=None,
            embedder=embedder_b,
            embed_fn=lambda _t: list(VEC_B),
        )
        assert error is None
        while registry.active() is not None:
            await asyncio.sleep(0.01)
        return job

    job = asyncio.run(_run())

    assert job.chunks == 1, "one row was re-embedded, and the job says one"
    assert job.chunks_stale == 1, "the remainder is read back from the table"
    assert job.status == "error", "a re-index must not report done over a half-converted layer"
    assert "still on the previous embedding model" in job.error
    assert job.to_dict()["chunks"] == 1
    assert store.count_stale_chunk_vectors() == 1


def test_a_clean_reindex_job_reports_done_with_its_chunk_count(tmp_path, monkeypatch):
    """The job's own negative case: nothing stale left, so the terminal state IS done."""
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry

    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    _item_with_one_chunk(store, _Embedder(VEC_A), title="Alpha", text="passage one")
    _bind(monkeypatch, MODEL_B)
    registry = ReindexRegistry()

    async def _run():
        job, _ = registry.start(
            model="native:bge-small-en",
            knowledge_store=store,
            vector_store=None,
            embedder=_Embedder(VEC_B),
            embed_fn=lambda _t: list(VEC_B),
        )
        while registry.active() is not None:
            await asyncio.sleep(0.01)
        return job

    job = asyncio.run(_run())
    assert (job.status, job.phase) == ("done", "done")
    assert (job.chunks, job.chunks_stale) == (1, 0)


def test_the_ann_index_holds_the_new_vectors_after_a_reindex(tmp_path, monkeypatch):
    """Same dimension means the index's row-count reconciliation sees nothing wrong.

    The vectors changed under UNCHANGED chunk ids and the counts still match, so an index
    that is not explicitly re-synced would keep serving the previous model's neighbours.
    """
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    if not store.vec_index.enabled:
        pytest.skip("sqlite-vec cannot load on this interpreter")
    _item_with_one_chunk(store, _Embedder(VEC_A))
    _bind(monkeypatch, MODEL_B)
    store.reembed_stale_chunks(_Embedder(VEC_B))

    indexed = store.db.execute("SELECT embedding FROM chunk_vec_4").fetchall()
    assert [bytes(r[0]) for r in indexed] == [_blob(VEC_B)]


# ── Negative cases: the rail must not flag everything ────────────────────────────


def test_a_chunk_written_under_the_still_bound_model_is_never_flagged(tmp_path, monkeypatch):
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    item_id = _item_with_one_chunk(store, _Embedder(VEC_A))

    assert store.count_stale_chunk_vectors() == 0
    assert store.stale_chunk_item_rows() == []
    outcome = _retriever(store, VEC_A).search_with_diagnostics(UNMATCHABLE_QUERY, limit=5)
    assert [r["id"] for r in outcome.results] == [item_id]
    assert outcome.degradations == ()


def test_nothing_bound_is_not_staleness(tmp_path, monkeypatch):
    """Unbinding the model must not mint a second reason for RET-2's state.

    With no embedding selection there is nothing to compare against, the vector arm is not
    running at all, and ``no_embedding_provider`` already names that state. Reporting
    ``stale_index`` here would be two reasons for one fact.
    """
    _bind(monkeypatch, None)
    store = _store(tmp_path)
    item_id = _item_with_one_chunk(store, _Embedder(VEC_A))

    assert active_fingerprint() is None
    assert _fingerprints(store) == [(None, None)]
    assert store.count_stale_chunk_vectors() == 0
    assert store.stale_chunk_item_rows() == []
    # And the unstamped vector still scores — the filter is omitted, not inverted.
    assert [r["id"] for r in _retriever(store, VEC_A).search(UNMATCHABLE_QUERY, limit=5)] == [
        item_id
    ]
    assert store.reembed_stale_chunks(_Embedder(VEC_A)) == {
        "reembedded": 0,
        "failed": 0,
        "total": 0,
        "stale_remaining": 0,
        "fingerprint": None,
    }


def test_an_unembedded_chunk_is_not_counted_as_stale(tmp_path, monkeypatch):
    """A row with no vector is unscoreable either way — a re-index cannot 'fix' it here."""
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    item_id = store.create_typed_item(item_type="note", title="No vector", content="body")
    store.replace_chunks(item_id, [Chunk(text="t", section=None, line_start=1, line_end=1)])
    _bind(monkeypatch, MODEL_B)

    assert store.count_stale_chunk_vectors() == 0


def test_stale_index_is_never_persisted_as_an_ingest_status(tmp_path):
    """The fact that changed is the bound model, not the item, so it is read-time only."""
    assert STALE_INDEX not in INGEST_REASONS
    assert STALE_INDEX in REASONS
    for chunk_count in (0, 3):
        for has_vector in (False, True):
            for has_text in (False, True):
                for bound in (False, True):
                    assert (
                        verdict_for_ingest(
                            chunk_count=chunk_count,
                            has_item_vector=has_vector,
                            has_text=has_text,
                            embedder_bound=bound,
                        )
                        != STALE_INDEX
                    )


# ── The provider half of the identity ────────────────────────────────────────────


def test_the_provider_is_part_of_the_identity(tmp_path, monkeypatch):
    """Same model id, different provider, is a different fingerprint.

    Two providers advertising ``all-minilm-l6-v2`` are not guaranteed to normalize or pool
    identically, so the provider is part of the identity rather than decoration.
    """
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    _item_with_one_chunk(store, _Embedder(VEC_A))

    _bind(monkeypatch, ("all-minilm-l6-v2", "some-hosted-endpoint"))
    assert store.count_stale_chunk_vectors() == 1


def test_the_fingerprint_predicates_and_label(tmp_path):
    fp = EmbeddingFingerprint(model_id="m", provider="p")
    assert fp.params == ("m", "p")
    assert str(fp) == "p:m"
    assert fp.to_dict() == {"embedding_model_id": "m", "embedding_provider": "p"}
    assert str(EmbeddingFingerprint(model_id="m", provider="")) == "m"


def test_a_read_only_reader_on_an_old_database_reports_cannot_tell(tmp_path, monkeypatch):
    """``has_fingerprint_columns`` is the guard the Doctor's ``mode=ro`` connection needs."""
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    _item_with_one_chunk(store, _Embedder(VEC_A))
    store.db.execute("ALTER TABLE chunks DROP COLUMN embedding_model_id")
    store.db.commit()

    assert has_fingerprint_columns(store.db) is False
    assert (
        has_fingerprint_columns(
            SimpleNamespace(execute=lambda *_a: (_ for _ in ()).throw(RuntimeError("read-only")))
        )
        is False
    )


def test_count_stale_chunks_takes_the_fingerprint_it_is_given(tmp_path, monkeypatch):
    """The helper is parameterised, so the Doctor and the store cannot drift on it."""
    _bind(monkeypatch, MODEL_A)
    store = _store(tmp_path)
    _item_with_one_chunk(store, _Embedder(VEC_A))

    same = EmbeddingFingerprint(model_id=MODEL_A[0], provider=MODEL_A[1])
    other = EmbeddingFingerprint(model_id=MODEL_B[0], provider=MODEL_B[1])
    assert count_stale_chunks(store.db, same) == 0
    assert count_stale_chunks(store.db, other) == 1


# ── The Doctor row a `personalclaw doctor` run shows ─────────────────────────────


def test_the_doctor_row_names_the_items_whose_passages_are_on_the_old_model(tmp_path, monkeypatch):
    from personalclaw.resilience.doctor import DoctorContext, all_probes

    _bind(monkeypatch, MODEL_A)
    home = tmp_path / "home"
    home.mkdir()
    store = KnowledgeStore(str(knowledge_db_path(home)))
    item_id = _item_with_one_chunk(store, _Embedder(VEC_A), title="Quarterly plan")
    store.db.commit()
    _bind(monkeypatch, MODEL_B)

    probe = next(p for p in all_probes() if p.id == "knowledge.searchability")
    result = asyncio.run(probe.run(DoctorContext(home=home)))

    assert result.ok is False
    ev = result.evidence
    assert ev["stale_chunk_vectors"] == 1
    assert ev["active_embedding_model"] == "native:bge-small-en"
    assert ev["by_reason"] == {STALE_INDEX: 1}
    assert [row["item_id"] for row in ev["items"]] == [item_id]
    assert "run the embedding re-index" in ev["remedy"]

    # Its own negative case: under the model that wrote them, the row is clean.
    _bind(monkeypatch, MODEL_A)
    clean = asyncio.run(probe.run(DoctorContext(home=home)))
    assert clean.ok is True
    assert clean.evidence.get("stale_chunk_vectors") == 0
