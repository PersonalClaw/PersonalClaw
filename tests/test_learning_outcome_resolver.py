"""The pending-outcome resolver — a decision's bet is graded once its horizon elapses (WF2LEA-4).

LEARNING-FLYWHEEL §3.3 (LEARN-R18). A decision-producing run journals a `pending_outcome` at
DECISION time — the subject it decided, the `metric` that will reveal whether it was right, the
`horizon_secs` after which that metric is meaningful, the `baseline` to beat. This module is the
other half: a one-shot on the curator tick that, for every open question whose horizon has passed,
measures ground truth from semantic memory, scores it against the baseline, journals an
`outcome_resolved` (closing the question), and files a graded lesson PROPOSAL.

The clauses WF2LEA-4's `done_when` names, each driven against the REAL `MemoryService`/
`VectorMemoryStore`, the REAL Run Ledger (`Journal` over `store`), and the REAL proposal store
(monkeypatched to a tmp home):

* INERT unless a memory service with a live vector store is injected — with no store there is
  nothing to measure;
* a question still INSIDE its horizon is left pending (not resolved early);
* past the horizon with a readable metric → resolved + a graded proposal citing the measured
  figure vs baseline, tagged `measured`;
* past the horizon with an UNreadable metric → inconclusive (an honest "could not tell", not a
  fabricated pass), tagged `inconclusive`;
* IDEMPOTENT — a second tick reads the `outcome_resolved` that cites the question's
  `pending_event_id` and skips it, filing nothing new;
* the graded lesson is a PROPOSAL, never a live lesson (propose-don't-install), and it is
  actually FILED (the `min_evidence=1` regression guard — the floor default of 3 would silently
  skip every once-per-decision outcome).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.learning import outcome_resolver
from personalclaw.learning import proposals as P
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import RunStatus, WorkflowRun

#: A metric key must be lowercase `lesson.*` (the semantic allowlist + key regex), and the
#: writing source must be trusted / high-confidence to clear the 0.8 floor for a non-user source.
_METRIC = "lesson.metric.plan_a_win"


@pytest.fixture
def svc():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return MemoryService.over_vector_store(store)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate the proposal store, workflows store, and inbox side effects under a tmp home.

    `personalclaw.workflows.store` binds `config_dir` at module import, so patching the loader
    symbol alone does NOT reach it — the resolver scans runs through that store and would see the
    real `~/.personalclaw`. `config_dir()` re-reads `PERSONALCLAW_HOME` live every call, so setting
    the env var isolates the import-bound store too.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    return tmp_path


def _run(name: str = "route-picker") -> WorkflowRun:
    run = store_mod.create(WorkflowRun(id="", workflow_name=name))
    run.status = RunStatus.COMPLETE
    return store_mod.save(run)


def _open_question(
    run: WorkflowRun, *, horizon: float, metric: str = _METRIC, baseline: float = 0.5
):
    """Journal a `pending_outcome` at decision time and return its written record."""
    return journal_mod.Journal(run.id).pending_outcome(
        "root.decide",
        "decide",
        epoch=1,
        subject="chose plan A over plan B",
        metric=metric,
        horizon_secs=horizon,
        baseline=baseline,
    )


def _set_metric(svc, value: float, *, key: str = _METRIC) -> None:
    """Write ground truth into semantic memory as a trusted, high-confidence lesson metric."""
    rej = svc.set_semantic(key, value, 0.9, "seal")
    assert rej is None, f"metric write rejected: {rej}"


# ── inert without a live vector store ──


def test_no_vector_store_is_a_noop(home):
    """Ground truth is read from semantic memory; with no store there is nothing to measure, so
    resolve returns an empty report and writes nothing."""
    run = _run()
    _open_question(run, horizon=0.0)  # would be past-horizon, but no store to measure with
    assert outcome_resolver.resolve(MemoryService.over_vector_store(None)) == {
        "resolved": 0,
        "inconclusive": 0,
        "pending": 0,
        "proposed": 0,
    }
    assert P.list_pending(kind=P.Kind.LESSON_BATCH.value) == []


def test_none_service_is_a_noop(home):
    assert outcome_resolver.resolve(None)["resolved"] == 0


def test_a_run_with_no_open_questions_resolves_nothing(svc, home):
    _run()  # a terminal run, but it journaled no pending_outcome
    assert outcome_resolver.resolve(svc) == {
        "resolved": 0,
        "inconclusive": 0,
        "pending": 0,
        "proposed": 0,
    }


# ── a question inside its horizon is left pending ──


def test_a_question_inside_its_horizon_stays_pending(svc, home):
    """The horizon is the whole point: a metric read before the decision's effect could show up is
    noise, so an open question younger than its horizon is counted pending and not resolved."""
    run = _run()
    q = _open_question(run, horizon=10_000.0)
    _set_metric(svc, 0.9)
    opened = outcome_resolver._epoch(q["ts"])
    # 'now' one second after the question opened — far inside the 10_000s horizon
    report = outcome_resolver.resolve(svc, now=opened + 1.0)
    assert report == {"resolved": 0, "inconclusive": 0, "pending": 1, "proposed": 0}
    assert P.list_pending(kind=P.Kind.LESSON_BATCH.value) == []


# ── past the horizon, measurable → resolved + graded proposal ──


def test_past_horizon_with_a_readable_metric_resolves_and_proposes(svc, home):
    run = _run()
    q = _open_question(run, horizon=100.0, baseline=0.5)
    _set_metric(svc, 0.8)
    opened = outcome_resolver._epoch(q["ts"])
    report = outcome_resolver.resolve(svc, now=opened + 1_000.0)  # well past the horizon
    assert report["resolved"] == 1
    assert report["inconclusive"] == 0
    assert report["proposed"] == 1

    (prop,) = P.list_pending(kind=P.Kind.LESSON_BATCH.value)
    assert prop.source_cadence == "run_end"
    assert prop.run_id == run.id
    assert prop.tags == ["run_end", "outcome", "measured"]
    # the body cites the measured figure against the baseline — a traceable claim
    assert "0.8" in prop.body and "0.5" in prop.body


def test_the_score_reflects_beating_the_baseline(svc, home):
    """`_score` is benchmark-relative in [-1, 1]: measured 0.8 vs baseline 0.5 → +0.23, and the
    proposal's confidence is |score|, so a decision that clearly beat its baseline is more
    confident than a marginal one."""
    run = _run()
    q = _open_question(run, horizon=100.0, baseline=0.5)
    _set_metric(svc, 0.8)
    opened = outcome_resolver._epoch(q["ts"])
    outcome_resolver.resolve(svc, now=opened + 1_000.0)
    (prop,) = P.list_pending(kind=P.Kind.LESSON_BATCH.value)
    assert prop.confidence == pytest.approx(abs(outcome_resolver._score(0.8, 0.5)), abs=1e-6)


def test_the_outcome_resolved_record_cites_the_pending_event(svc, home):
    """LEARN-R18: `outcome_resolved.pending_event_id` == the open question's `event_id`. This link
    is what makes the resolver idempotent."""
    run = _run()
    q = _open_question(run, horizon=100.0)
    _set_metric(svc, 0.8)
    opened = outcome_resolver._epoch(q["ts"])
    outcome_resolver.resolve(svc, now=opened + 1_000.0)
    resolved = journal_mod.ledger(run.id, kinds={journal_mod.OUTCOME_RESOLVED})
    assert len(resolved) == 1
    assert resolved[0]["pending_event_id"] == q["event_id"]
    assert resolved[0]["resolution"] == "measured"


# ── past the horizon, unmeasurable → inconclusive ──


def test_past_horizon_with_an_unreadable_metric_is_inconclusive(svc, home):
    """A metric that cannot be read after the horizon resolves as inconclusive — an honest "could
    not tell", never a fabricated pass. The proposal says the bet is unconfirmed, not validated."""
    run = _run()
    q = _open_question(run, horizon=100.0, metric="lesson.metric.never_written")
    # deliberately DO NOT write the metric
    opened = outcome_resolver._epoch(q["ts"])
    report = outcome_resolver.resolve(svc, now=opened + 1_000.0)
    assert report["inconclusive"] == 1
    assert report["resolved"] == 0
    assert report["proposed"] == 1
    (prop,) = P.list_pending(kind=P.Kind.LESSON_BATCH.value)
    assert prop.tags == ["run_end", "outcome", "inconclusive"]
    assert "inconclusive" in prop.body.lower()


# ── idempotency: a second tick resolves nothing new ──


def test_a_second_tick_is_idempotent(svc, home):
    """The resolver cites `pending_event_id`, so a second curator tick sees the question is already
    answered, files no duplicate proposal, and writes no second `outcome_resolved`."""
    run = _run()
    q = _open_question(run, horizon=100.0)
    _set_metric(svc, 0.8)
    opened = outcome_resolver._epoch(q["ts"])

    first = outcome_resolver.resolve(svc, now=opened + 1_000.0)
    assert first["resolved"] == 1 and first["proposed"] == 1

    second = outcome_resolver.resolve(svc, now=opened + 2_000.0)
    assert second == {"resolved": 0, "inconclusive": 0, "pending": 0, "proposed": 0}
    # exactly one resolution and one proposal survive
    assert len(journal_mod.ledger(run.id, kinds={journal_mod.OUTCOME_RESOLVED})) == 1
    assert len(P.list_pending(kind=P.Kind.LESSON_BATCH.value)) == 1


# ── a payload metric (value under a field) is read, not just a bare number ──


def test_a_metric_stored_as_a_payload_is_read(svc, home):
    """`_read_metric` unwraps a small payload's `value`/`score`/`measured` field, so a metric
    written as a structured record still measures rather than resolving inconclusive."""
    run = _run()
    q = _open_question(run, horizon=100.0, baseline=0.5)
    rej = svc.set_semantic(_METRIC, {"score": 0.9, "note": "measured downstream"}, 0.9, "seal")
    assert rej is None, f"metric write rejected: {rej}"
    opened = outcome_resolver._epoch(q["ts"])
    report = outcome_resolver.resolve(svc, now=opened + 1_000.0)
    assert report["resolved"] == 1
    assert report["proposed"] == 1
