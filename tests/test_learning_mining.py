"""The §3.2 PRODUCERS — embeddings, intent inversion, positive-path traces (WF2LEA-7).

`learning.detectors` holds pure verdicts fed from outside, and three of its inputs had no producer
at all. That is the failure shape this suite prevents recurring: a detector that is
written, tested, reachable, and permanently returning its "nothing to see" branch because the
argument it needs is never computed.

What is pinned here, in the order the defects would reappear:

* the producer returns REAL ``(run_id, cosine, age_days)`` triples from the real
  ``VectorMemoryStore``, and ``similarity_verdict`` reaches its AUTO_FILE branch on them — the
  branch that could not fire before;
* a registry MISS is a typed, recorded reason and never an empty list read as "no repetition" —
  every miss variant, because a blind detector that looks calibrated is the specific harm;
* the run's OWN spec is excluded from its own matches (a plan is maximally similar to itself, so
  counting it would let one run clear ``min_priors`` alone);
* intent inversion synthesizes a user-register sentence from execution, and reports drift;
* a positive trace is order-sensitive, gated on COMPLETE runs and on min_frequency, and files a
  PENDING proposal — never an install;
* the wiring itself: `run_end.capture` calls these producers on a real terminal run.

Every test drives the REAL store/ledger/proposal path under a tmp `PERSONALCLAW_HOME` — the stores
bind `config_dir` at import, so the env var is the isolation that actually holds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.learning import mining
from personalclaw.learning import proposals as P
from personalclaw.learning import run_end
from personalclaw.learning.detectors import Action, similarity_verdict
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate the proposal store, workflows store and staging ledger to a tmp home.

    `workflows.store` and the staging store bind `config_dir` at module IMPORT, so patching the
    loader symbol alone leaves them pointed at the real `~/.personalclaw`. `config_dir()` re-reads
    `PERSONALCLAW_HOME` on every call, so the env var is what actually isolates the write path.

    The staging singleton is reset around every test as well. `staging.get_store()` caches a
    process-global `_INSTANCE` (staging.py:526), so the FIRST test in a worker binds the store
    to its own tmp home and every later test in that worker writes there — miss counts then
    accumulate across tests and an exact-count assertion fails for a reason unrelated to the
    code under test. Measured here: two tests each recording one miss read a count of 2.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)

    from personalclaw.learning import staging as staging_mod

    monkeypatch.setattr(staging_mod, "_INSTANCE", None)
    yield tmp_path
    staging_mod._INSTANCE = None


def _fake_embed(text: str) -> list[float]:
    """A deterministic bag-of-words embedding over a fixed vocabulary.

    Real enough to produce MEANINGFUL cosines — two runs sharing steps score high, unrelated ones
    score low — without pulling a model into a unit test. A random or constant vector would make
    every similarity assertion vacuous, which is the trap: a test that passes on noise proves the
    plumbing runs, not that the detector discriminates.
    """
    vocab = [
        "fetch",
        "transform",
        "publish",
        "report",
        "daily",
        "cleanup",
        "archive",
        "purge",
        "invoice",
        "email",
    ]
    words = text.lower()
    vec = [float(words.count(v)) for v in vocab]
    if not any(vec):
        vec = [1.0] + [0.0] * (len(vocab) - 1)
    return vec


@pytest.fixture
def svc():
    """A REAL MemoryService with a real store and a real (fake-model) embedder wired."""
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    store.embed_fn = _fake_embed
    return MemoryService.over_vector_store(store)


@pytest.fixture
def svc_no_embedder():
    """A real store with NO embedder — the degraded box the miss reasons exist for."""
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return MemoryService.over_vector_store(store)


def _run(name: str = "daily-report", intent: str = "", status: RunStatus = RunStatus.COMPLETE):
    run = store_mod.create(WorkflowRun(id="", workflow_name=name, intent=intent))
    run.status = status
    return store_mod.save(run)


def _complete(run, node: str) -> None:
    journal_mod.Journal(run.id).step_completed(
        f"root.{node}",
        node,
        epoch=1,
        cache_key="",
        state=InstanceState.DONE,
    )


# ── clause A: the producer exists and returns real triples ──


def test_the_producer_returns_the_triples_the_verdict_consumes(svc, home):
    """The headline: `similarity_verdict` had NO producer, so it could only ever skip.

    Three prior runs take the same path; the fourth must see them as matches shaped exactly as the
    verdict's signature demands — `(run_id, cosine, age_days)`.
    """
    priors = []
    for _ in range(3):
        prior = _run()
        for node in ("fetch", "transform", "publish"):
            _complete(prior, node)
        mining.index_run_spec(prior, svc, journal=journal_mod)
        priors.append(prior.id)

    current = _run()
    for node in ("fetch", "transform", "publish"):
        _complete(current, node)

    found = mining.similar_run_matches(current, svc, journal=journal_mod)
    assert not found.blind, f"producer went blind: {found.miss}"
    assert found.matches, "the producer returned no triples — the detector is still starved"
    for run_id, cosine, age_days in found.matches:
        assert isinstance(run_id, str) and run_id
        assert 0.0 <= float(cosine) <= 1.0001
        assert float(age_days) >= 0.0
    assert set(m[0] for m in found.matches) <= set(priors)


def test_the_verdict_reaches_auto_file_on_produced_matches(svc, home):
    """End to end: the branch that could never fire, firing on real produced data."""
    for _ in range(3):
        prior = _run()
        for node in ("fetch", "transform", "publish"):
            _complete(prior, node)
        mining.index_run_spec(prior, svc, journal=journal_mod)

    current = _run()
    for node in ("fetch", "transform", "publish"):
        _complete(current, node)
    found = mining.similar_run_matches(current, svc, journal=journal_mod)
    verdict = similarity_verdict(matches=found.matches, now=__import__("time").time())
    assert verdict.action == Action.AUTO_FILE.value, verdict.reason


def test_a_run_is_not_its_own_prior(svc, home):
    """A plan is maximally similar to itself. Counting it would let ONE run clear min_priors."""
    run = _run()
    for node in ("fetch", "transform"):
        _complete(run, node)
    mining.index_run_spec(run, svc, journal=journal_mod)
    found = mining.similar_run_matches(run, svc, journal=journal_mod)
    assert run.id not in [m[0] for m in found.matches]


def test_unrelated_plans_do_not_clear_the_threshold(svc, home):
    """The discriminating half: the producer must not report everything as similar.

    Without this, a producer returning uniformly high cosines would pass every test above while
    proposing a template for every run ever executed.
    """
    other = _run(name="purge-archive")
    for node in ("cleanup", "archive", "purge"):
        _complete(other, node)
    mining.index_run_spec(other, svc, journal=journal_mod)

    current = _run(name="invoice-email")
    for node in ("invoice", "email"):
        _complete(current, node)
    found = mining.similar_run_matches(current, svc, journal=journal_mod)
    verdict = similarity_verdict(matches=found.matches, now=__import__("time").time())
    assert verdict.action == Action.SKIP.value


# ── clause A: a registry miss is TYPED, never a silent empty list ──


@pytest.mark.parametrize(
    "miss",
    [
        mining.Miss.STORE_UNAVAILABLE,
        mining.Miss.NO_EMBEDDER,
        mining.Miss.EMPTY_SPEC,
        mining.Miss.NOT_INDEXED,
        mining.Miss.NO_JOURNAL,
    ],
)
def test_every_miss_reason_is_recorded_to_the_ledger(miss, home):
    """Each variant writes a row keyed by its typed value, under this module's own prefix."""
    assert mining.record_miss(miss, detail="run-1") is True
    counts = mining.miss_counts()
    assert counts.get(miss.value) == 1, counts


def test_no_embedder_is_a_named_miss_not_an_empty_list(svc_no_embedder, home):
    """The exact harm: a store with no embedder degrades vector search to FTS, whose scores are NOT
    cosines. Reporting that as "no similar plans" makes a blind detector look calibrated."""
    run = _run()
    for node in ("fetch", "transform"):
        _complete(run, node)
    found = mining.similar_run_matches(run, svc_no_embedder, journal=journal_mod)
    assert found.matches == []
    assert found.blind is True
    assert found.miss is mining.Miss.NO_EMBEDDER
    assert mining.miss_counts().get(mining.Miss.NO_EMBEDDER.value) == 1


def test_no_store_is_a_named_miss(home):
    run = _run()
    found = mining.similar_run_matches(run, None, journal=journal_mod)
    assert found.blind and found.miss is mining.Miss.STORE_UNAVAILABLE
    assert mining.miss_counts().get(mining.Miss.STORE_UNAVAILABLE.value) == 1


def test_an_empty_index_is_not_indexed_not_no_repetition(svc, home):
    """Nothing has been indexed yet — a capability gap, not the observation "plans don't repeat"."""
    run = _run()
    for node in ("fetch", "transform"):
        _complete(run, node)
    found = mining.similar_run_matches(run, svc, journal=journal_mod)
    assert found.blind and found.miss is mining.Miss.NOT_INDEXED


def test_an_unembeddable_spec_is_a_named_miss(svc, home):
    """A run with no intent, no name and no completed steps has nothing to embed."""
    run = store_mod.create(WorkflowRun(id="", workflow_name=""))
    found = mining.similar_run_matches(run, svc, journal=journal_mod)
    assert found.blind and found.miss is mining.Miss.EMPTY_SPEC


def test_a_blind_producer_never_looks_like_a_calibrated_one(svc_no_embedder, home):
    """The invariant stated directly: blindness and emptiness must be DISTINGUISHABLE.

    Both return no matches. If the only signal were `matches == []` the caller could not tell them
    apart, which is the whole defect. `blind` is what separates them.
    """
    run = _run()
    _complete(run, "fetch")
    _complete(run, "transform")
    blind = mining.similar_run_matches(run, svc_no_embedder, journal=journal_mod)

    other = _run(name="purge-archive")
    for node in ("cleanup", "archive"):
        _complete(other, node)
    mining.index_run_spec(other, svc_no_embedder, journal=journal_mod)

    assert blind.matches == []
    assert blind.blind is True and blind.miss is not None


# ── clause B: intent inversion ──


def test_inversion_synthesizes_a_user_register_intent_from_execution(home):
    """plan → intent, the INVERSE direction. It must exist even with no declared intent, which is
    exactly the run that contributes nothing to an intent-keyed index otherwise."""
    run = _run(name="daily-report", intent="")
    for node in ("fetch_data", "transform_rows", "publish_report"):
        _complete(run, node)
    inv = mining.invert_intent(run, journal=journal_mod)
    assert inv.synthesized, "a run with no declared intent produced no synthesized one"
    # De-slugged so it shares a vocabulary with prose intents rather than embedding opaque tokens.
    assert "fetch data" in inv.synthesized
    assert "_" not in inv.synthesized


def test_inversion_reports_drift_when_the_run_did_something_else(home):
    run = _run(name="cleanup", intent="Reconcile the quarterly invoices with the ledger")
    for node in ("purge_tmp", "archive_logs"):
        _complete(run, node)
    inv = mining.invert_intent(run, journal=journal_mod)
    assert inv.inverted is True, inv.to_dict()
    assert inv.drift >= mining.INVERSION_DRIFT_THRESHOLD
    assert "invoices" in inv.unaddressed


def test_a_run_that_did_what_was_asked_does_not_invert(home):
    run = _run(name="fetch-transform", intent="fetch and transform the daily report")
    for node in ("fetch", "transform", "daily", "report"):
        _complete(run, node)
    inv = mining.invert_intent(run, journal=journal_mod)
    assert inv.inverted is False, inv.to_dict()


def test_the_synthesized_intent_reaches_the_embedded_spec(home):
    """Clause B feeds clause A: the plan's design is synthesize → embed → cluster. If the
    synthesized sentence never reaches `spec_text`, the inversion is a value nobody reads."""
    run = _run(name="daily-report", intent="")
    for node in ("fetch_data", "publish_report"):
        _complete(run, node)
    inv = mining.invert_intent(run, journal=journal_mod)
    assert inv.synthesized in mining.spec_text(run, journal=journal_mod)


# ── clause C: positive-path trace mining ──


def test_a_recurring_successful_path_is_mined_and_filed(home):
    """§3.2's positive half, through the SAME PENDING proposal path the negative signals use."""
    for _ in range(mining.MIN_TRACE_FREQUENCY):
        run = _run(name="daily-report")
        for node in ("fetch", "transform", "publish"):
            _complete(run, node)

    traces, miss = mining.positive_path_candidates(workflow_name="daily-report")
    assert miss is None
    assert traces, "a repeated successful path was not mined"
    assert traces[0].frequency >= mining.MIN_TRACE_FREQUENCY

    pid = mining.file_positive_trace(traces[0])
    assert pid, "the mined trace filed no draft"
    pending = P.list_pending(kind=P.Kind.TEMPLATE.value)
    assert any(p.id == pid for p in pending), "the draft is not PENDING — never a self-install"


def test_a_failed_run_contributes_no_positive_trace(home):
    """The outcome-quality gate. A sequence mined from failures is a recipe for failing."""
    for _ in range(mining.MIN_TRACE_FREQUENCY + 1):
        run = _run(name="flaky", status=RunStatus.FAILED)
        for node in ("fetch", "transform", "publish"):
            _complete(run, node)
    traces, _miss = mining.positive_path_candidates(workflow_name="flaky")
    assert traces == []


def test_a_one_off_success_is_not_a_pattern(home):
    """min_frequency: one success is an event, a repeat is a pattern."""
    run = _run(name="once")
    for node in ("fetch", "transform", "publish"):
        _complete(run, node)
    traces, _miss = mining.positive_path_candidates(workflow_name="once")
    assert traces == []


def test_a_single_step_run_is_a_command_not_a_procedure(home):
    for _ in range(mining.MIN_TRACE_FREQUENCY + 1):
        run = _run(name="one-step")
        _complete(run, "fetch")
    traces, _miss = mining.positive_path_candidates(workflow_name="one-step")
    assert traces == []


def test_the_trace_signature_is_order_sensitive():
    """ "fetch → publish" and "publish → fetch" are different procedures. A set-based key would
    merge them into one template that matches neither."""
    assert mining.trace_signature(["fetch", "publish"]) != mining.trace_signature(
        ["publish", "fetch"]
    )


def test_the_trace_signature_collapses_the_same_step_across_runs():
    assert mining.trace_signature(["Fetch#1", "fetch"]) == mining.trace_signature(
        ["fetch", "Fetch"]
    )


# ── the WIRING: a test is not a consumer, so pin the real production path ──


def test_run_end_capture_drives_the_producers(svc, home):
    """The clause that matters most: `run_end.capture` is the REAL terminal-run path, and it must
    call these producers. A producer nothing calls is the same defect as a detector nothing feeds.
    """
    calls: dict[str, int] = {"index": 0, "similar": 0, "invert": 0, "traces": 0}
    real_index = mining.index_run_spec

    def index(run, service, **kw):
        calls["index"] += 1
        return real_index(run, service, **kw)

    real_similar = mining.similar_run_matches
    real_invert = mining.invert_intent
    real_traces = mining.positive_path_candidates

    def similar(run, service, **kw):
        calls["similar"] += 1
        return real_similar(run, service, **kw)

    def invert(run, **kw):
        calls["invert"] += 1
        return real_invert(run, **kw)

    def traces(**kw):
        calls["traces"] += 1
        return real_traces(**kw)

    import unittest.mock as m

    with (
        m.patch.object(mining, "index_run_spec", index),
        m.patch.object(mining, "similar_run_matches", similar),
        m.patch.object(mining, "invert_intent", invert),
        m.patch.object(mining, "positive_path_candidates", traces),
    ):
        run = _run(name="daily-report", intent="publish the daily report")
        for node in ("fetch", "transform", "publish"):
            _complete(run, node)
        report = run_end.capture(run, svc, journal=journal_mod)

    assert calls == {"index": 1, "similar": 1, "invert": 1, "traces": 1}, calls
    assert "mined" in report


def test_capture_files_a_similarity_draft_on_real_repetition(svc, home):
    """Driven end to end through the production entry point, not the producer directly."""
    for _ in range(3):
        prior = _run(name="daily-report")
        for node in ("fetch", "transform", "publish"):
            _complete(prior, node)
        mining.index_run_spec(prior, svc, journal=journal_mod)

    current = _run(name="daily-report")
    for node in ("fetch", "transform", "publish"):
        _complete(current, node)
    report = run_end.capture(current, svc, journal=journal_mod)

    assert report["mined"] >= 1, report
    pending = P.list_pending(kind=P.Kind.TEMPLATE.value)
    assert pending, "capture filed no template draft on repeated work"
    assert all(p.status == P.Status.PENDING.value for p in pending)


def test_capture_files_nothing_when_the_detector_is_blind(svc_no_embedder, home):
    """The refusal path. Proposing on an unmeasured signal is worse than not proposing, so a blind
    pass must file NOTHING — and say why in the ledger."""
    run = _run(name="daily-report")
    for node in ("fetch", "transform", "publish"):
        _complete(run, node)
    report = run_end.capture(run, svc_no_embedder, journal=journal_mod)
    assert report["mined"] == 0, report
    assert not [
        p for p in P.list_pending(kind=P.Kind.TEMPLATE.value) if "Repeated plan shape" in p.title
    ]
    assert mining.miss_counts().get(mining.Miss.NO_EMBEDDER.value)


def test_capture_still_mines_on_a_run_with_no_failures(svc, home):
    """The positive half must not be gated behind a failure: a clean run is the ONLY kind that can
    carry a successful trace, and the old early-return on `not events` would have skipped it."""
    for _ in range(3):
        prior = _run(name="clean")
        for node in ("fetch", "transform", "publish"):
            _complete(prior, node)
        mining.index_run_spec(prior, svc, journal=journal_mod)
    current = _run(name="clean")
    for node in ("fetch", "transform", "publish"):
        _complete(current, node)
    report = run_end.capture(current, svc, journal=journal_mod)
    assert report["mined"] >= 1, report
