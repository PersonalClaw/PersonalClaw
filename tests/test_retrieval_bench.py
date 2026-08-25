"""ES-3 — the retrieval eval harness with per-arm P@k/R@k ablation (both stores).

What these tests are for, in order of what would actually break silently:

1. **A metric over an empty candidate set must not read as a score.** ``P@k`` of zero
   candidates is ``None`` with :data:`REASON_NO_CANDIDATES`; ``R@k`` of zero candidates is
   a real ``0.0``; ``R@k`` of an empty label set is ``None``. The three are asserted apart,
   and the ``VERIFIER_ABSENT`` mapping is asserted at the ``matrix.aggregate`` CALL SITE —
   a mean of ``None`` rather than ``0.0`` is the property, not the enum value.
2. **Every declared arm must RUN.** Each mask is driven against the REAL retriever classes
   (``type(...) is HybridRetriever`` / ``is VectorMemoryStore``), and the arms are asserted
   to return DIFFERENT id sets — a mask that parses but gates nothing makes every arm's
   delta zero, so it would read as "no arm contributes".
3. **Every guard is shown failing.** The control mask, the read-only rail, the empty
   benchmark and the dead-arm verdict each have a paired case that trips them.

Home isolation: ``conftest``'s autouse fixture already re-points every binding of
``config_dir``, so the live stores resolve under a tmp home. Nothing here patches it a
second time (that leaked a store between tests elsewhere);
:func:`test_stores_resolve_under_a_tmp_home` ASSERTS the redirect instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.evals import retrieval_bench as rb
from personalclaw.evals import store as evals_store
from personalclaw.evals.matrix import VERIFIER_ABSENT, MatrixSpec, aggregate
from personalclaw.knowledge import retrieval as knowledge_retrieval
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.memory_graph import MemoryGraph
from personalclaw.vector_memory import VectorMemoryStore

# ── fixtures: the real stores, seeded so no single arm can answer everything ───

_ITEMS = {
    "RRF fusion notes": "Reciprocal rank fusion blends the keyword and vector lists.",
    "Postgres vacuum runbook": "Autovacuum thresholds and the manual VACUUM FULL hatch.",
    "Sourdough hydration log": "78% hydration gave an open crumb.",
    "Terraform state recovery": "A locked state file needs force-unlock.",
    "Nginx TLS ciphers": "Prefer the ChaCha20 suites on mobile.",
}


@pytest.fixture()
def knowledge_store() -> KnowledgeStore:
    """A REAL KnowledgeStore at the redirected home, with items + entity mentions."""
    store = KnowledgeStore(str(knowledge_db_path()))
    ids = {
        title: store.create_typed_item(item_type="note", title=title, content=body)
        for title, body in _ITEMS.items()
    }
    # An entity whose mentions are the ONLY way the graph arm can reach these two — the
    # query text shares no words with either title, so a graph hit cannot be a keyword hit
    # in disguise.
    entity = store.add_entity("Orion", "concept")
    store.add_mention(ids["Terraform state recovery"], entity)
    store.add_mention(ids["Nginx TLS ciphers"], entity)
    store.db.commit()
    try:
        yield store
    finally:
        store.db.close()


_RECORDS = {
    "project.orion.deploy_window": "deploys land Tuesdays 10:00-12:00 UTC",
    "project.orion.oncall": "pages route to the platform rotation",
    "pref.baking.hydration": "higher hydration needs a stiffer starter",
    "user.tool.ripgrep": "rg --hidden is needed to search dotfiles",
}


@pytest.fixture()
def memory_store() -> VectorMemoryStore:
    """A REAL VectorMemoryStore at the redirected home, with graph links + volunteers."""
    store = VectorMemoryStore()
    store.init()
    for key, value in _RECORDS.items():
        store.set_semantic(key, value, 0.9, "test")
    graph = store.graph
    entity = graph.upsert_entity("Orion", "project")
    for ref in ("project.orion.deploy_window", "project.orion.oncall"):
        row = store.db.execute(
            "SELECT recall_count FROM semantic_memory WHERE key = ?", (ref,)
        ).fetchone()
        graph.log_volunteer(
            entity_id=entity,
            entity_name="Orion",
            arm="alias",
            confidence=0.9,
            from_kind="semantic",
            record_ref=ref,
            recall_at_volunteer=int((row["recall_count"] if row else 0) or 0),
        )
        graph.add_link(
            from_kind="semantic",
            from_ref=ref,
            link_type="mentions",
            to_entity=entity,
            source="test",
        )
    # One volunteered ref that is NEVER used, so the mining filter has a negative to drop.
    unused = graph.upsert_entity("ripgrep", "tool")
    graph.log_volunteer(
        entity_id=unused,
        entity_name="ripgrep",
        arm="alias",
        confidence=0.9,
        from_kind="semantic",
        record_ref="user.tool.ripgrep",
        recall_at_volunteer=0,
    )
    # The "used" signal: recall_count RISES after the volunteer, for Orion's refs only.
    store.record_recall(["project.orion.deploy_window", "project.orion.oncall"])
    store.db.commit()
    # `set_semantic`'s write-time linker already resolved `alias_index`, caching an EMPTY
    # matcher before these entities existed. Without this the graph arm silently matches
    # nothing — the documented contract on `invalidate_alias_index`, and what
    # `memory_service` does after every entity write.
    store.invalidate_alias_index()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture()
def bound_models():
    """Bind a chat + embedding model so the RunPin can complete.

    Provider-agnostic refs (no ``provider:`` prefix) survive
    ``_prune_removed_providers`` without a configured provider, which is what lets a test
    exercise the pin without standing up a model backend.
    """
    from personalclaw.providers.use_cases import save_active_models

    save_active_models({"chat": ["test-chat"], "embedding": ["test-embed"]})


# ── 0. home isolation is asserted, not assumed ───────────────────────────────


def test_stores_resolve_under_a_tmp_home():
    """Both store paths must be under the fixture's tmp home, not the real one.

    Asserted rather than re-patched: `conftest` already re-points every binding of
    `config_dir`, and stacking a second patch on top of it leaked a store between tests
    elsewhere in this suite.
    """
    real_home = Path.home() / ".personalclaw"
    for path in (knowledge_db_path(), rb.memory_db_path()):
        assert real_home not in Path(path).parents, path
        assert "pclaw-home" in str(path) or "tmp" in str(path).lower(), path


# ── 1. the metric contract: three distinct absences ──────────────────────────


def test_precision_over_no_candidates_is_undefined_not_a_score():
    """0/0 is None. NOT 0.0 (a measured miss) and NOT 1.0 (a measured hit)."""
    assert rb.precision_at_k([], ["a"], 5) is None


def test_recall_over_no_candidates_is_a_real_zero():
    """Deliberately NOT symmetric: the retriever found none of the known answers."""
    assert rb.recall_at_k([], ["a"], 5) == 0.0


def test_recall_over_no_relevant_ids_is_undefined():
    assert rb.recall_at_k(["x"], [], 5) is None


def test_precision_over_retrieved_but_wrong_is_a_measured_zero():
    """The case that must NOT collapse into the no-candidates one."""
    assert rb.precision_at_k(["x", "y"], ["a"], 5) == 0.0


def test_precision_denominator_is_the_candidates_returned_not_k():
    """A store that can only return two candidates must not be scored out of five."""
    assert rb.precision_at_k(["a", "x"], ["a"], 5) == 0.5


def test_score_query_reasons_separate_no_candidates_from_no_relevant():
    q_labelled = rb.QrelsQuery(query="q", relevant_ids=("a",))
    q_unlabelled = rb.QrelsQuery(query="q", relevant_ids=())

    empty = rb.score_query(q_labelled, [], mask="none", k=5)
    assert empty.reason == rb.REASON_NO_CANDIDATES
    assert empty.precision is None and empty.recall == 0.0

    wrong = rb.score_query(q_labelled, ["x"], mask="keyword", k=5)
    assert wrong.reason == rb.REASON_OK
    assert wrong.precision == 0.0 and wrong.recall == 0.0

    unlabelled = rb.score_query(q_unlabelled, ["x"], mask="keyword", k=5)
    assert unlabelled.reason == rb.REASON_NO_RELEVANT
    assert unlabelled.recall is None


def test_a_mask_that_retrieved_nothing_reports_none_not_zero_in_the_table():
    """The vacuity assertion on the REPORT, not just on the metric function."""
    scores = [
        rb.score_query(rb.QrelsQuery(query=f"q{i}", relevant_ids=("a",)), [], mask="none", k=5)
        for i in range(3)
    ]
    row = rb.build_table(scores, k=5)[0]
    assert row.mask == rb.MASK_NONE
    assert row.p_at_k is None, "a mask with no candidates must not report a precision"
    assert row.r_at_k == 0.0
    assert row.scored_queries == 0
    assert row.no_candidate_queries == row.queries == 3


def test_undefined_recall_is_counted_apart_from_no_candidates():
    """Both absences appear in the row, in different columns."""
    scores = [
        rb.score_query(rb.QrelsQuery(query="a", relevant_ids=("x",)), [], mask="keyword", k=5),
        rb.score_query(rb.QrelsQuery(query="b", relevant_ids=()), ["x"], mask="keyword", k=5),
    ]
    row = rb.build_table(scores, k=5)[0]
    assert row.no_candidate_queries == 1
    assert row.undefined_recall_queries == 1


def test_no_candidate_cells_cannot_be_averaged_in_as_zero():
    """The CALL SITE: `_cell_outcome` → `matrix.aggregate` must yield mean_score None.

    The property is the mean, not the enum: a `VERIFIER_ABSENT` label that still carried a
    0.0 score would average in and report the retriever as scoring zero.
    """
    from personalclaw.evals.matrix import CellResult

    score = rb.score_query(rb.QrelsQuery(query="q", relevant_ids=("a",)), [], mask="none", k=5)
    outcome, cell_score = rb._cell_outcome(score)
    assert outcome == VERIFIER_ABSENT and cell_score is None
    agg = aggregate([CellResult(coords={}, outcome=outcome, score=cell_score)])
    assert agg["mean_score"] is None
    assert agg["counts"][VERIFIER_ABSENT] == 1
    assert agg["scored_count"] == 0


# ── 2. one arm vocabulary, and mask spelling ─────────────────────────────────


def test_one_arm_vocabulary_across_both_stores():
    """Two spellings would make "the graph arm's contribution" mean two things."""
    import personalclaw.vector_memory as vector_memory

    assert rb.ARMS == tuple(knowledge_retrieval.ARMS) == tuple(vector_memory.RECALL_ARMS)


def test_mask_name_is_canonical_regardless_of_input_order():
    assert rb.mask_name(("vector", "keyword")) == rb.mask_name(("keyword", "vector"))
    assert rb.mask_name(()) == rb.MASK_NONE
    assert rb.parse_mask(rb.mask_name(("vector", "keyword"))) == ("keyword", "vector")
    assert rb.parse_mask(rb.MASK_NONE) == ()
    assert rb.parse_mask("keyword+nonsense") == ("keyword",)


def test_ablation_masks_carry_a_control_a_full_and_every_leave_one_out():
    names = [rb.mask_name(m) for m in rb.ablation_masks()]
    assert names[0] == rb.MASK_NONE, "the control must be present and first"
    assert rb.mask_name(rb.ARMS) in names
    for arm in rb.ARMS:
        assert rb.mask_name(tuple(a for a in rb.ARMS if a != arm)) in names
        assert rb.mask_name((arm,)) in names


# ── 3. each arm RUNS — driven against the real retriever classes ─────────────


def test_knowledge_arm_mask_gates_the_real_hybrid_retriever(knowledge_store):
    """Each knowledge arm runs and returns a DIFFERENT set; the empty mask returns none."""
    retriever = knowledge_retrieval.HybridRetriever(knowledge_store)
    assert type(retriever) is knowledge_retrieval.HybridRetriever

    keyword_only = {h["id"] for h in retriever.search("vacuum runbook", limit=5, arms=("keyword",))}
    graph_only = {h["id"] for h in retriever.search("Orion", limit=5, arms=("graph",))}
    assert keyword_only, "the keyword arm did not run"
    assert graph_only, "the graph arm did not run"
    assert keyword_only != graph_only, "both arms returned the same set — is the mask applied?"

    # The control: all arms off retrieves nothing. This is the vacuity floor under every
    # per-arm delta the harness reports.
    assert retriever.search("Orion", limit=5, arms=()) == []


def test_knowledge_default_arms_is_the_full_mask(knowledge_store):
    """`arms=None` must leave every production caller's ranking untouched."""
    retriever = knowledge_retrieval.HybridRetriever(knowledge_store)
    default = [h["id"] for h in retriever.search("Orion vacuum", limit=5)]
    full = [h["id"] for h in retriever.search("Orion vacuum", limit=5, arms=rb.ARMS)]
    assert default == full


def test_a_masked_knowledge_arm_never_issues_its_query(knowledge_store, monkeypatch):
    """ "Masked" means NOT RUN, not "run and discarded" — an ablation of the arm's absence."""
    retriever = knowledge_retrieval.HybridRetriever(knowledge_store)
    calls: list[str] = []
    real_graph = retriever._graph_search
    monkeypatch.setattr(
        retriever, "_graph_search", lambda *a, **k: (calls.append("graph"), real_graph(*a, **k))[1]
    )
    retriever.search("Orion", limit=5, arms=("keyword",))
    assert calls == [], "the graph arm ran under a keyword-only mask"
    retriever.search("Orion", limit=5, arms=("keyword", "graph"))
    assert calls == ["graph"]


def test_memory_arm_mask_gates_the_real_rank_semantic(memory_store):
    """Each memory arm runs; the empty mask returns none."""
    assert type(memory_store) is VectorMemoryStore

    keyword_only = {
        r["key"]
        for r in memory_store.rank_semantic("hydration starter", limit=5, arms=("keyword",))
    }
    graph_only = {r["key"] for r in memory_store.rank_semantic("Orion", limit=5, arms=("graph",))}
    assert keyword_only, "the memory keyword arm did not run"
    assert graph_only, "the memory graph arm did not run"
    assert keyword_only != graph_only

    assert memory_store.rank_semantic("Orion", limit=5, arms=()) == []


def test_a_masked_memory_graph_arm_never_traverses(memory_store, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(memory_store, "_graph_boosts", lambda text: (calls.append(text), {})[1])
    memory_store.rank_semantic("Orion", limit=5, arms=("keyword",))
    assert calls == []
    memory_store.rank_semantic("Orion", limit=5, arms=("keyword", "graph"))
    assert calls == ["Orion"]


def test_get_semantic_context_still_renders_through_rank_semantic(memory_store, monkeypatch):
    """The extraction's call site: the formatter must go THROUGH the ranking function."""
    seen: list[str] = []
    real = memory_store.rank_semantic
    monkeypatch.setattr(
        memory_store,
        "rank_semantic",
        lambda q, **kw: (seen.append(q), real(q, **kw))[1],
    )
    block = memory_store.get_semantic_context(query_text="hydration starter", cap=4000)
    assert seen == ["hydration starter"], "get_semantic_context bypassed rank_semantic"
    assert "pref.baking.hydration" in block


# ── 4. qrels mining, asserted in BOTH directions ─────────────────────────────


def test_volunteer_qrels_keeps_used_and_drops_unused(memory_store):
    graph = memory_store.graph
    assert type(graph) is MemoryGraph
    qrels = graph.volunteer_qrels()
    assert "Orion" in qrels, "a volunteered-then-used record was not mined"
    assert sorted(qrels["Orion"]) == [
        "project.orion.deploy_window",
        "project.orion.oncall",
    ]
    assert "ripgrep" not in qrels, "a volunteered-but-never-used record was mined as a positive"


def test_volunteer_qrels_shares_the_used_predicate_with_the_health_panel(memory_store):
    """One definition of "used": the mined set and the live precision must agree."""
    graph = memory_store.graph
    mined = sum(len(v) for v in graph.volunteer_qrels().values())
    assert mined == graph.volunteer_precision()["overall"]["used"]


def test_mine_knowledge_qrels_keeps_named_intents_and_drops_unnamed(knowledge_store):
    item_id = next(iter(rb.mine_knowledge_qrels.__doc__ or ""), None)  # placeholder, replaced below
    del item_id
    rows = knowledge_store.db.execute("SELECT id, title FROM items ORDER BY title").fetchall()
    keep_id = rows[0]["id"]
    drop_id = rows[1]["id"]
    knowledge_store.record_intent_outcome(
        "i-keep", intent_name="a real standing question", item_id=keep_id
    )
    knowledge_store.record_intent_outcome("i-drop", intent_name="   ", item_id=drop_id)
    knowledge_store.db.commit()

    mined = rb.mine_knowledge_qrels(knowledge_store)
    queries = {q.query: q for q in mined}
    assert "a real standing question" in queries
    assert queries["a real standing question"].relevant_ids == (keep_id,)
    assert queries["a real standing question"].source == rb.SOURCE_MINED_INTENT
    assert all(q.query.strip() for q in mined), "a blank-named intent was mined as a query"


def test_an_empty_benchmark_refuses_rather_than_scoring_nothing(knowledge_store, bound_models):
    """A store with no labels has no P@k. Returning a zero here would file "retrieval is
    broken" as a finding about the retriever."""
    with pytest.raises(rb.EmptyBenchmarkError):
        rb.run_retrieval_bench(
            rb.STORE_KNOWLEDGE,
            handle=knowledge_store,
            db_path=knowledge_store.db_path,
            benchmark=rb.RetrievalBenchmark(name="empty", store=rb.STORE_KNOWLEDGE),
        )


# ── 5. the control mask is the harness's own falsifier ───────────────────────


def _seeded_benchmark(knowledge_store) -> rb.RetrievalBenchmark:
    rows = knowledge_store.db.execute("SELECT id, title FROM items ORDER BY title").fetchall()
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
            rb.QrelsQuery(
                query="Orion",
                relevant_ids=(
                    by_title["Nginx TLS ciphers"],
                    by_title["Terraform state recovery"],
                ),
                source=rb.SOURCE_MINED_INTENT,
            ),
        ),
        corpus_snapshot_ref=rb.corpus_snapshot_ref(rb.STORE_KNOWLEDGE, knowledge_store),
    )


def test_a_mask_that_gates_nothing_refuses_to_publish(knowledge_store, bound_models, monkeypatch):
    """The falsification the harness performs on ITSELF.

    A retriever that ignores the mask makes every arm score identically, so the report
    would read "no arm contributes anything" — a conclusion about the retriever drawn from
    a bug in the harness. The control cell is what catches it.
    """
    retriever = knowledge_retrieval.HybridRetriever(knowledge_store)

    def _ignores_the_mask(query: str, k: int, arms):
        return [h["id"] for h in retriever.search(query, limit=k)]

    monkeypatch.setattr(rb, "retriever_for", lambda store_kind, handle: _ignores_the_mask)
    with pytest.raises(rb.MaskNotAppliedError):
        rb.run_retrieval_bench(
            rb.STORE_KNOWLEDGE,
            handle=knowledge_store,
            db_path=knowledge_store.db_path,
            benchmark=_seeded_benchmark(knowledge_store),
        )


def test_a_run_with_no_control_cell_is_unfalsifiable_and_refuses():
    with pytest.raises(rb.MaskNotAppliedError):
        rb._assert_mask_applied(
            [rb.score_query(rb.QrelsQuery(query="q"), ["a"], mask="keyword", k=5)]
        )


# ── 6. the read-only rail, shown holding AND failing ─────────────────────────


def test_store_unchanged_passes_when_nothing_writes(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"before")
    with rb.store_unchanged(db):
        db.read_bytes()


def test_store_unchanged_raises_when_the_body_writes(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"before")
    with pytest.raises(rb.StoreMutatedError):
        with rb.store_unchanged(db):
            db.write_bytes(b"after")


def test_store_unchanged_still_checks_a_body_that_raised(tmp_path):
    """The case the obvious implementation gets wrong: drift on the exception path."""
    db = tmp_path / "x.db"
    db.write_bytes(b"before")
    with pytest.raises(rb.StoreMutatedError) as excinfo:
        with rb.store_unchanged(db):
            db.write_bytes(b"after")
            raise ValueError("boom")
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_store_files_excludes_the_shm_sidecar(tmp_path):
    """`-shm` bytes change on a plain READ, so hashing it would fire the rail every run."""
    names = [p.name for p in rb.store_files(tmp_path / "memory.db")]
    assert names == ["memory.db", "memory.db-wal"]


# ── 7. per-arm contribution + the dark-ship verdict ──────────────────────────


def test_arm_verdict_reads_its_floor_from_the_module_constant():
    at_floor = rb.MIN_ARM_CONTRIBUTION
    below = rb.MIN_ARM_CONTRIBUTION / 2
    assert rb.arm_verdict(at_floor, rb.MIN_SCORED_QUERIES)[0] == rb.ARM_ENABLE
    assert rb.arm_verdict(below, rb.MIN_SCORED_QUERIES)[0] == rb.ARM_HOLD


def test_arm_verdict_is_unmeasured_below_the_power_floor():
    verdict, reasons = rb.arm_verdict(1.0, rb.MIN_SCORED_QUERIES - 1)
    assert verdict == rb.ARM_UNMEASURED
    assert "low power" in reasons[0]


def test_arm_verdict_is_unmeasured_when_there_is_no_delta():
    assert rb.arm_verdict(None, 100)[0] == rb.ARM_UNMEASURED


def test_an_arm_with_no_executor_is_unmeasured_not_worthless():
    """The exact prior failure mode: a declared arm that never ran scores identically to
    its own absence, so its delta is 0.0 and would read as a confident "remove it"."""
    verdict, reasons = rb.arm_verdict(0.0, 100, has_executor=False)
    assert verdict == rb.ARM_UNMEASURED
    assert "no executor" in reasons[0]
    # ...and a POSITIVE delta over a dead arm is still unmeasured, not an enable.
    assert rb.arm_verdict(0.9, 100, has_executor=False)[0] == rb.ARM_UNMEASURED


def test_contribution_is_the_leave_one_out_delta():
    rows = [
        rb.ArmMaskRow(rb.mask_name(rb.ARMS), 5, 0.80, 0.70, 10, 10, 0, 0),
        rb.ArmMaskRow(rb.mask_name(("graph", "vector")), 5, 0.50, 0.40, 10, 10, 0, 0),
        rb.ArmMaskRow(rb.mask_name(("keyword",)), 5, 0.45, 0.30, 10, 10, 0, 0),
    ]
    keyword = next(c for c in rb.contributions(rows) if c.arm == "keyword")
    assert keyword.contribution_p == pytest.approx(0.30)
    assert keyword.contribution_r == pytest.approx(0.30)
    assert keyword.solo_p_at_k == 0.45
    assert keyword.verdict == rb.ARM_ENABLE


def test_contribution_power_is_the_weaker_of_the_two_differenced_masks():
    rows = [
        rb.ArmMaskRow(rb.mask_name(rb.ARMS), 5, 0.80, 0.70, 10, 10, 0, 0),
        rb.ArmMaskRow(rb.mask_name(("graph", "vector")), 5, 0.50, 0.40, 10, 2, 8, 0),
    ]
    keyword = next(c for c in rb.contributions(rows) if c.arm == "keyword")
    assert keyword.scored_queries == 2
    assert keyword.verdict == rb.ARM_UNMEASURED


def test_a_dead_arm_is_marked_unmeasured_through_contributions():
    rows = [
        rb.ArmMaskRow(rb.mask_name(rb.ARMS), 5, 0.80, 0.70, 10, 10, 0, 0),
        rb.ArmMaskRow(rb.mask_name(("keyword", "graph")), 5, 0.50, 0.40, 10, 10, 0, 0),
    ]
    vector = next(c for c in rb.contributions(rows, {"vector": False}) if c.arm == "vector")
    assert vector.verdict == rb.ARM_UNMEASURED
    assert "no executor" in vector.reasons[0]


def test_arm_executors_reports_the_vector_arm_dead_without_an_embedder(memory_store):
    memory_store.embed_fn = None
    executors = rb.arm_executors(rb.STORE_MEMORY, memory_store)
    assert executors[rb.ARM_VECTOR] is False
    assert executors[rb.ARM_KEYWORD] is True
    memory_store.embed_fn = lambda text: [0.1, 0.2, 0.3]
    assert rb.arm_executors(rb.STORE_MEMORY, memory_store)[rb.ARM_VECTOR] is True


# ── 8. corpus versioning by reference ────────────────────────────────────────


def test_corpus_snapshot_ref_moves_when_the_corpus_grows(knowledge_store):
    before = rb.corpus_snapshot_ref(rb.STORE_KNOWLEDGE, knowledge_store)
    assert before.startswith(f"{rb.STORE_KNOWLEDGE}:{len(_ITEMS)}:")
    knowledge_store.create_typed_item(item_type="note", title="new thing", content="x")
    knowledge_store.db.commit()
    after = rb.corpus_snapshot_ref(rb.STORE_KNOWLEDGE, knowledge_store)
    assert after != before
    bench = rb.RetrievalBenchmark(name="b", store=rb.STORE_KNOWLEDGE, corpus_snapshot_ref=before)
    assert rb.corpus_drifted(bench, after) is True
    assert rb.corpus_drifted(bench, before) is False


def test_an_unknown_corpus_ref_is_not_reported_as_drift():
    bench = rb.RetrievalBenchmark(name="b", store=rb.STORE_KNOWLEDGE, corpus_snapshot_ref="")
    assert rb.corpus_drifted(bench, "knowledge:1:abc") is False


def test_the_subject_hash_ignores_the_corpus_ref_and_the_timestamp():
    """Re-mining the same labels must NOT look like a new benchmark to `pin_diff`."""
    queries = (rb.QrelsQuery(query="q", relevant_ids=("a",)),)
    a = rb.RetrievalBenchmark("n", rb.STORE_MEMORY, queries, "memory:1:aaa", "2026-01-01")
    b = rb.RetrievalBenchmark("n", rb.STORE_MEMORY, queries, "memory:9:zzz", "2026-06-06")
    assert a.sha256 == b.sha256
    changed = rb.RetrievalBenchmark(
        "n", rb.STORE_MEMORY, (rb.QrelsQuery(query="q", relevant_ids=("b",)),)
    )
    assert changed.sha256 != a.sha256


# ── 9. the hand-label card ───────────────────────────────────────────────────


def test_an_empty_hand_label_is_a_real_judgement_not_a_missing_one(knowledge_store):
    """ "None of these answer it" must survive, and must beat the mined label.

    The bug this pins: reading the card with `or already_relevant` makes `[]` falsy, so the
    query silently re-inherits the weak label the human just overruled.
    """
    bench = rb.RetrievalBenchmark(
        name="b",
        store=rb.STORE_KNOWLEDGE,
        queries=(rb.QrelsQuery(query="q", relevant_ids=("mined",), source=rb.SOURCE_MINED_INTENT),),
    )
    updated = rb.apply_hand_labels(bench, {"q": []})
    assert updated.queries[0].relevant_ids == ()
    assert updated.queries[0].source == rb.SOURCE_HAND_LABEL


def test_build_benchmark_prefers_a_hand_label_over_a_remined_one(knowledge_store):
    rows = knowledge_store.db.execute("SELECT id FROM items LIMIT 1").fetchall()
    knowledge_store.record_intent_outcome("i", intent_name="q", item_id=rows[0]["id"])
    knowledge_store.db.commit()
    rb.save_benchmark(
        rb.RetrievalBenchmark(
            name="retrieval-knowledge",
            store=rb.STORE_KNOWLEDGE,
            queries=(rb.QrelsQuery(query="q", relevant_ids=(), source=rb.SOURCE_HAND_LABEL),),
        )
    )
    rebuilt = rb.build_benchmark(rb.STORE_KNOWLEDGE, knowledge_store)
    hand = next(q for q in rebuilt.queries if q.query == "q")
    assert hand.source == rb.SOURCE_HAND_LABEL
    assert hand.relevant_ids == ()


def test_the_card_offers_the_weakest_labelled_queries_first(knowledge_store):
    bench = rb.RetrievalBenchmark(
        name="b",
        store=rb.STORE_KNOWLEDGE,
        queries=(
            rb.QrelsQuery(query="rich", relevant_ids=("a", "b"), source=rb.SOURCE_MINED_INTENT),
            rb.QrelsQuery(query="thin", relevant_ids=(), source=rb.SOURCE_MINED_INTENT),
            rb.QrelsQuery(query="done", relevant_ids=("c",), source=rb.SOURCE_HAND_LABEL),
        ),
    )
    card = rb.hand_label_card(bench, lambda q, k, arms: ["cand"], limit=2)
    assert [entry["query"] for entry in card["queries"]] == ["thin", "rich"]
    assert card["candidates_per_query"] == rb.HAND_LABEL_CANDIDATES
    assert all(entry["candidates"] == ["cand"] for entry in card["queries"])


# ── 10. the whole run: artifacts, ledger, scorer:qrels ───────────────────────


def test_a_run_lands_in_matrices_via_scorer_qrels(knowledge_store, bound_models):
    result = rb.run_retrieval_bench(
        rb.STORE_KNOWLEDGE,
        handle=knowledge_store,
        db_path=knowledge_store.db_path,
        benchmark=_seeded_benchmark(knowledge_store),
    )
    assert type(result.spec) is MatrixSpec
    assert result.spec.scorer == rb.SCORER_QRELS == "qrels"
    assert result.spec.axes[rb.ARM_AXIS] == [rb.mask_name(m) for m in rb.ablation_masks()]

    run_dir = evals_store.matrix_dir(result.bench_id)
    for name in (
        "experiment.json",
        "aggregates.json",
        "trials.json",
        "observations.json",
        "table.json",
        "table.tsv",
        "contributions.json",
        "benchmark.json",
        "pin.json",
    ):
        assert (run_dir / name).is_file(), f"{name} was not written"

    table = json.loads((run_dir / "table.json").read_text(encoding="utf-8"))
    assert table["store"] == rb.STORE_KNOWLEDGE
    assert table["floors"]["min_arm_contribution"] == rb.MIN_ARM_CONTRIBUTION
    control = next(r for r in table["rows"] if r["mask"] == rb.MASK_NONE)
    assert control["p_at_k"] is None, "the control published a precision"


def test_a_run_appends_one_pinned_ledger_row(knowledge_store, bound_models):
    result = rb.run_retrieval_bench(
        rb.STORE_KNOWLEDGE,
        handle=knowledge_store,
        db_path=knowledge_store.db_path,
        benchmark=_seeded_benchmark(knowledge_store),
    )
    rows = [r for r in evals_store.read_results() if r["study_id"] == result.bench_id]
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == rb.LEDGER_KIND == "retrieval_bench"
    assert row["verdict"] == "pass"
    assert row["model_fp"], "the ledger row landed without a model fingerprint"
    assert row["scenario_sha256"] == _seeded_benchmark(knowledge_store).sha256


def test_a_run_measures_every_arm_and_the_masks_do_not_all_agree(knowledge_store, bound_models):
    """The end-to-end version of "each arm RUNS".

    Asserted on the RETRIEVED SETS rather than on the aggregate P@k: two arms can coincide
    on a small qrels set by accident, and an aggregate comparison would then flake. What
    must never be true is that every mask returned the SAME candidates — that is the
    signature of a mask the retriever ignored.
    """
    result = rb.run_retrieval_bench(
        rb.STORE_KNOWLEDGE,
        handle=knowledge_store,
        db_path=knowledge_store.db_path,
        benchmark=_seeded_benchmark(knowledge_store),
    )
    by_mask = {row.mask: row for row in result.table}
    for arm in rb.ARMS[:2]:  # keyword + graph; the vector arm has no embedder under test
        assert by_mask[rb.mask_name((arm,))].scored_queries >= 1, f"{arm} scored nothing"
    assert by_mask[rb.MASK_NONE].p_at_k is None

    retrieved_per_mask = {}
    for score in result.scores:
        retrieved_per_mask.setdefault(score.mask, set()).update(score.retrieved)
    assert (
        len(set(map(frozenset, retrieved_per_mask.values()))) > 1
    ), "every mask retrieved the same candidates — the mask reached nothing"
    assert retrieved_per_mask[rb.MASK_NONE] == set()


def test_run_refuses_an_unknown_store_rather_than_defaulting_to_one():
    with pytest.raises(rb.RetrievalBenchError):
        rb.run_retrieval_bench("both")


def test_the_ledger_verdict_is_about_measurability_not_the_score():
    """A personal corpus with some P@5 = 0 queries is a normal measurement, not a `fail`."""
    zero = rb.ArmMaskRow(rb.mask_name(rb.ARMS), 5, 0.0, 0.0, 10, 10, 0, 0)
    assert rb._bench_verdict(zero) == "pass"
    unmeasured = rb.ArmMaskRow(rb.mask_name(rb.ARMS), 5, None, None, 10, 0, 10, 0)
    assert rb._bench_verdict(unmeasured) == VERIFIER_ABSENT
    assert rb._bench_verdict(None) == VERIFIER_ABSENT


def test_a_benchmark_file_declaring_the_wrong_store_is_refused():
    rb.save_benchmark(rb.RetrievalBenchmark(name="b", store=rb.STORE_MEMORY))
    path = rb.benchmark_path(rb.STORE_MEMORY)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["store"] = rb.STORE_KNOWLEDGE
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(rb.RetrievalBenchError):
        rb.load_benchmark(rb.STORE_MEMORY)


def test_each_store_has_its_own_benchmark_file():
    """§5.1's "never share a corpus", enforced by the filename rather than by a field."""
    assert rb.benchmark_path(rb.STORE_KNOWLEDGE) != rb.benchmark_path(rb.STORE_MEMORY)
    with pytest.raises(rb.RetrievalBenchError):
        rb.benchmark_path("both")


# ── 11. the shared matrices/ sink: two writers, one table.json ────────────────


def test_a_retrieval_run_is_not_claimed_by_the_judge_bench(knowledge_store, bound_models):
    """🔴 The collision this atom actually caused, measured in a browser first.

    ES-4's `list_bench_runs` claimed every `matrices/<id>/` dir that had a `table.json` —
    a working proxy for "is a judge bench" only while ES-4 was the ONLY writer of one.
    ES-3 is a second writer, so the newest retrieval run was served as the newest judge
    bench, and `JudgeBenchPanel` read `row.wall_secs` off a P@k row and took the whole
    Learning page down with `Cannot read properties of undefined`.
    """
    from personalclaw.evals import judge_bench as jb

    result = rb.run_retrieval_bench(
        rb.STORE_KNOWLEDGE,
        handle=knowledge_store,
        db_path=knowledge_store.db_path,
        benchmark=_seeded_benchmark(knowledge_store),
    )
    assert (evals_store.matrix_dir(result.bench_id) / "table.json").is_file()
    assert result.bench_id not in jb.list_bench_runs()
    assert jb.latest_bench_view() is None, "the judge panel would render retrieval rows"


def test_the_two_consumers_own_their_runs_by_the_artifacts_stamp(knowledge_store, bound_models):
    """The vacuity floor under the test above: with a REAL judge run present, the judge
    consumer finds its own and still refuses the retrieval one — and symmetrically.

    Ownership is the ``kind`` the ``table.json`` DECLARES, not the run id. A caller may pass
    its own ``bench_id`` (the judge bench's own suite does), so a prefix rule would strand
    every run whose id someone chose — a second bug of the same shape as the one being fixed.
    """
    from personalclaw.evals import judge_bench as jb

    retrieval = rb.run_retrieval_bench(
        rb.STORE_KNOWLEDGE,
        handle=knowledge_store,
        db_path=knowledge_store.db_path,
        benchmark=_seeded_benchmark(knowledge_store),
    )
    # A caller-chosen id with NO judge prefix, to prove the id is not what identifies it.
    judge_id = "bench-custom-id"
    evals_store.matrix_dir(judge_id).joinpath("table.json").write_text(
        json.dumps({"kind": jb.TABLE_KIND, "columns": [], "rows": [], "floors": {}}),
        encoding="utf-8",
    )
    runs = jb.list_bench_runs()
    assert judge_id in runs, "the judge consumer stopped finding its OWN run"
    assert retrieval.bench_id not in runs
    # ...and symmetrically: the retrieval consumer must not claim the judge run.
    assert rb.latest_bench_id(rb.STORE_KNOWLEDGE) == retrieval.bench_id
    assert rb.latest_bench_id() == retrieval.bench_id


def test_an_unstamped_table_is_claimed_by_neither_consumer(knowledge_store):
    """A table.json with no `kind` belongs to nobody. Silently adopting it is exactly how the
    judge bench came to serve a retrieval run."""
    from personalclaw.evals import judge_bench as jb

    orphan = "matrix-with-no-owner"
    evals_store.matrix_dir(orphan).joinpath("table.json").write_text(
        json.dumps({"columns": [], "rows": []}), encoding="utf-8"
    )
    assert orphan not in jb.list_bench_runs()
    assert rb.latest_bench_id() != orphan


def test_the_retrieval_latest_is_scoped_to_the_store_asked_for(knowledge_store, bound_models):
    """A knowledge run must never be served as the memory store's report — §5.1 again."""
    retrieval = rb.run_retrieval_bench(
        rb.STORE_KNOWLEDGE,
        handle=knowledge_store,
        db_path=knowledge_store.db_path,
        benchmark=_seeded_benchmark(knowledge_store),
    )
    assert rb.latest_bench_id(rb.STORE_KNOWLEDGE) == retrieval.bench_id
    assert rb.latest_bench_id(rb.STORE_MEMORY) == ""
    with pytest.raises(rb.RetrievalBenchError):
        rb.latest_bench_id("both")
