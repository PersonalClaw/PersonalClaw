"""Health-scored remediation engine tests (PLATFORM-RESILIENCE §4).

Pins the deficit→score math (reachable ceilings, unreachable-deficit exclusion), the
dependency-ordered plan, the three stop conditions (target/cost/exhausted), the
cooldown storm-guard, and the ledger.
"""

from __future__ import annotations

import pytest

from personalclaw.resilience import remediation as rem
from personalclaw.resilience.remediation import Deficit, RemediationJob


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate the doctor/ ledger + jobs.json under tmp, and snapshot/restore the job
    registry so test jobs don't leak."""
    monkeypatch.setattr("personalclaw.resilience.remediation.config_dir", lambda: tmp_path)
    saved = dict(rem._JOBS)
    yield
    rem._JOBS.clear()
    rem._JOBS.update(saved)


# ── deficit → score ───────────────────────────────────────────────────────────


def test_penalty_is_capped_at_max_penalty():
    d = Deficit(key="k", count=1000, weight=1.0, max_penalty=10.0)
    assert d.penalty == 10.0  # capped, not 1000


def test_health_score_subtracts_reachable_penalties():
    ds = [
        Deficit(key="a", count=5, weight=1.0, max_penalty=20.0),  # penalty 5
        Deficit(key="b", count=10, weight=2.0, max_penalty=10.0),  # penalty 10 (capped)
    ]
    assert rem.health_score(ds) == 85.0  # 100 - 5 - 10


def test_unreachable_deficit_excluded_from_score():
    ds = [
        Deficit(key="a", count=10, weight=1.0, max_penalty=20.0, reachable=False),  # ignored
        Deficit(key="b", count=3, weight=1.0, max_penalty=20.0),  # penalty 3
    ]
    assert rem.health_score(ds) == 97.0  # only b counts (unfixable → not held against us)


def test_health_score_clamped():
    ds = [Deficit(key="a", count=999, weight=1.0, max_penalty=200.0)]
    assert rem.health_score(ds) == 0.0  # never negative


# ── dependency ordering ───────────────────────────────────────────────────────


def test_ordered_respects_after_edges():
    a = RemediationJob(id="a", title="a", run=lambda: "a", after=("b",))
    b = RemediationJob(id="b", title="b", run=lambda: "b")
    ordered = rem._ordered([a, b])
    assert [j.id for j in ordered].index("b") < [j.id for j in ordered].index("a")


def test_ordered_tolerates_cycle():
    a = RemediationJob(id="a", title="a", run=lambda: "a", after=("b",))
    b = RemediationJob(id="b", title="b", run=lambda: "b", after=("a",))
    ordered = rem._ordered([a, b])  # must not hang/raise
    assert {j.id for j in ordered} == {"a", "b"}


# ── run: stop conditions + execution ──────────────────────────────────────────


def _stub_deficits(monkeypatch, deficits):
    monkeypatch.setattr(rem, "measure_deficits", lambda: deficits)


def test_run_stops_when_already_healthy(monkeypatch):
    _stub_deficits(monkeypatch, [Deficit(key="a", count=0, weight=1.0, max_penalty=10.0)])
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert result.stopped_reason == "target_score already met"
    assert result.jobs == []


def test_run_executes_job_and_improves_score(monkeypatch):
    ran = {"n": 0}

    def _job():
        ran["n"] += 1
        return "fixed"

    rem.register_job(RemediationJob(id="fix.a", title="Fix A", run=_job, fixes_deficit="a"))
    # First measure: deficit present (score 80); after the job runs, healthy.
    calls = {"n": 0}

    def _measure():
        calls["n"] += 1
        if calls["n"] == 1:
            return [Deficit(key="a", count=20, weight=1.0, max_penalty=20.0, job_id="fix.a")]
        return [Deficit(key="a", count=0, weight=1.0, max_penalty=20.0, job_id="fix.a")]

    monkeypatch.setattr(rem, "measure_deficits", _measure)
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 1
    assert result.score_before == 80.0 and result.score_after == 100.0
    assert result.stopped_reason == "target_score reached"
    assert result.jobs[0]["status"] == "ok"


def test_run_skips_unreachable_deficit_job(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.b", title="Fix B", run=lambda: ran.__setitem__("n", 1) or "x", fixes_deficit="b"
        )
    )
    # deficit present but UNREACHABLE → job not a candidate, score unaffected by it.
    _stub_deficits(
        monkeypatch,
        [Deficit(key="b", count=50, weight=1.0, max_penalty=20.0, reachable=False, job_id="fix.b")],
    )
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 0  # never ran — unfixable now
    # Unreachable deficit doesn't count → already at target.
    assert result.stopped_reason == "target_score already met"


def test_run_respects_cooldown(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.c",
            title="Fix C",
            run=lambda: ran.__setitem__("n", ran["n"] + 1) or "x",
            fixes_deficit="c",
            cooldown_hours=24.0,
        )
    )
    _stub_deficits(
        monkeypatch,
        [Deficit(key="c", count=20, weight=1.0, max_penalty=20.0, job_id="fix.c")],
    )
    # First run executes it.
    rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 1
    # A run 1 hour later → within the 24h cooldown → skipped.
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0 + 3600)
    assert ran["n"] == 1  # not re-run
    assert any(j["status"] == "skipped_cooldown" for j in result.jobs)


def test_dry_run_does_not_execute_or_change_score(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.d", title="Fix D", run=lambda: ran.__setitem__("n", 1) or "x", fixes_deficit="d"
        )
    )
    _stub_deficits(
        monkeypatch,
        [Deficit(key="d", count=20, weight=1.0, max_penalty=20.0, job_id="fix.d")],
    )
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0, dry_run=True)
    assert ran["n"] == 0  # dry-run never executes
    assert result.score_after == result.score_before
    assert all(j["status"] == "would_run" for j in result.jobs)


# ── ledger ────────────────────────────────────────────────────────────────────


def test_ledger_written_and_read_back(monkeypatch):
    rem.register_job(
        RemediationJob(id="fix.e", title="Fix E", run=lambda: "done", fixes_deficit="e")
    )
    calls = {"n": 0}

    def _measure():
        calls["n"] += 1
        return [
            Deficit(
                key="e",
                count=(20 if calls["n"] == 1 else 0),
                weight=1.0,
                max_penalty=20.0,
                job_id="fix.e",
            )
        ]

    monkeypatch.setattr(rem, "measure_deficits", _measure)
    rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1234.0)
    runs = rem.recent_runs()
    assert len(runs) == 1
    assert runs[0]["ts"] == 1234.0
    assert runs[0]["stopped_reason"] in ("target_score reached", "plan exhausted")


def test_builtin_deterministic_jobs_registered():
    ids = {j.id for j in rem.all_jobs()}
    assert {"serving-fs.prune-orphans", "skills.age", "knowledge.reindex-embeddings"} <= ids
    # all built-ins are the deterministic ($0) lane
    for j in rem.all_jobs():
        if j.id in ("serving-fs.prune-orphans", "skills.age", "knowledge.reindex-embeddings"):
            assert j.lane == "deterministic"
