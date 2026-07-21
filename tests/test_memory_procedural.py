"""M5d: procedural memory (how-to-work priors) + failure-pattern synthesis."""

from __future__ import annotations

import pytest

from personalclaw.memory_record import MemoryKind, MemoryScope
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def svc(tmp_path):
    s = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    s.init()
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(s)


def test_record_procedural_creates_record(svc):
    key = svc.record_procedural(tool="grep", task_shape="find symbol", outcome="success")
    assert key is not None
    rec = svc.get_record(key)
    assert rec.kind == MemoryKind.PROCEDURAL
    assert "grep" in rec.text and "success" in rec.text
    assert rec.scope == MemoryScope.SESSION


def test_record_procedural_reinforces_on_recurrence(svc):
    k1 = svc.record_procedural(tool="grep", task_shape="find symbol", outcome="success")
    k2 = svc.record_procedural(tool="grep", task_shape="find symbol", outcome="success")
    assert k1 == k2  # same observation → same key
    rec = svc.get_record(k1)
    assert rec.recall_count == 2  # reinforced


def test_record_procedural_distinct_outcomes_distinct_records(svc):
    ks = svc.record_procedural(tool="bash", task_shape="run tests", outcome="success")
    kf = svc.record_procedural(tool="bash", task_shape="run tests", outcome="failed")
    assert ks != kf


def test_procedural_priors_returns_global_sorted_by_heat(svc):
    # promote one to global by hand (the heat gate would normally do this)
    k = svc.record_procedural(tool="grep", task_shape="x", outcome="success")
    svc._vs.db.execute(
        "UPDATE semantic_memory SET scope='global', recall_count=9 WHERE key=?", (k,)
    )
    svc._vs.db.commit()
    priors = svc.procedural_priors()
    assert any(p["key"] == k for p in priors)


def test_procedural_priors_excludes_session_scoped(svc):
    svc.record_procedural(tool="grep", task_shape="x", outcome="success")  # stays session
    assert svc.procedural_priors() == []


# ── failure-pattern synthesis (the anti-noise mechanism) ──


def test_synthesize_failures_collapses_cluster(svc):
    # 3+ failures of the same tool → one synthesized prior
    for shape in ("task a", "task b", "task c"):
        svc.record_procedural(tool="flaky_tool", task_shape=shape, outcome="failed")
    n = svc.synthesize_failures(min_cluster=3)
    assert n == 1
    # the scattered rows are retired; one synthesized global prior remains
    proc = svc.get_records(kinds={MemoryKind.PROCEDURAL.value})
    synth = [r for r in proc if r.source == "failure_synthesis"]
    assert len(synth) == 1
    assert "unreliable" in synth[0].text
    assert synth[0].scope == MemoryScope.GLOBAL
    # original failure rows are gone
    assert not [r for r in proc if r.source == "procedural" and "flaky_tool" in r.text]


def test_synthesize_failures_below_threshold_noop(svc):
    for shape in ("task a", "task b"):  # only 2 — below min_cluster=3
        svc.record_procedural(tool="rare_tool", task_shape=shape, outcome="failed")
    n = svc.synthesize_failures(min_cluster=3)
    assert n == 0
    # the individual rows survive
    proc = svc.get_records(kinds={MemoryKind.PROCEDURAL.value})
    assert len([r for r in proc if "rare_tool" in r.text]) == 2


def test_synthesize_ignores_successes(svc):
    for shape in ("a", "b", "c", "d"):
        svc.record_procedural(tool="good_tool", task_shape=shape, outcome="success")
    assert svc.synthesize_failures(min_cluster=3) == 0  # successes never synthesized


# ── capture wiring (after-turn review mines runtime outcomes) ──


def test_record_procedural_outcomes_dedupes_per_tool_outcome(svc):
    from personalclaw import after_turn_review as atr

    outcomes = [("grep", False), ("grep", False), ("bash", True), ("bash", True)]
    n = atr.record_procedural_outcomes(svc, outcomes)
    # one record per DISTINCT (tool, outcome): grep/success + bash/failed
    assert n == 2


def test_record_procedural_outcomes_noop_without_service(svc):
    from personalclaw import after_turn_review as atr

    assert atr.record_procedural_outcomes(None, [("x", False)]) == 0
    assert atr.record_procedural_outcomes(svc, []) == 0
