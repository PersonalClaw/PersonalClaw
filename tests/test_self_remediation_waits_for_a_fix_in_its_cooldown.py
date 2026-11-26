"""Self-remediation looks again when the fix it waits on is due, not every five minutes.

🔴 The defect (Backlog: "Self-remediation runs every 5 minutes all day when the one fix it plans
is in its cooldown"). Seen on a dogfood gateway: the health score sat at 28, every pass's plan
held one job, the orphan-lock prune, skipped for its cooldown, and each pass ended "plan
exhausted" with nothing run. The adaptive clock read what the engine's jobs could still win
back, which a job in its cooldown still can, so it stayed on its five-minute cadence all day:
about 280 passes, each measuring every deficit (0.6 s to 58 s), for a fix it could not run.

Now a pass whose every planned job was in its cooldown ran nothing, and its clock waits for the
soonest of them to be due, or for the long cadence, whichever comes first. A pass that ran a job
and still has work keeps the short cadence, as before.

Driven through the real engine (`run_remediation`, its cooldown state on disk) and the real
provider re-arming the real trigger store, in a home of the test's own.
"""

from __future__ import annotations

import asyncio
import time

import pytest
import test_resilience_remediation_trigger as _trigger_tests

from personalclaw.action_providers import remediation_provider as P
from personalclaw.action_providers.base import ActionContext
from personalclaw.resilience import remediation as rem
from personalclaw.resilience.remediation import Deficit, RemediationJob
from personalclaw.triggers.service import to_epoch

MIN = 60.0

#: The trigger tests' isolated home and its trigger store.
home = _trigger_tests.home
store = _trigger_tests.store
_patch_config = _trigger_tests._patch_config


@pytest.fixture(autouse=True)
def _only_these_jobs(home, monkeypatch):
    """The engine's cooldown state in the test's home, and only the jobs a test registers."""
    monkeypatch.setattr("personalclaw.resilience.remediation.config_dir", lambda: home)
    saved = dict(rem._JOBS)
    rem._JOBS.clear()
    yield
    rem._JOBS.clear()
    rem._JOBS.update(saved)


def _a_deficit_fixed_by(job_id: str, *, cooldown_hours: float, ran_ago: float, run=None):
    """A fixable deficit, and the one job that fixes it, last run *ran_ago* seconds ago."""
    rem.register_job(
        RemediationJob(
            id=job_id,
            title=job_id,
            run=run or (lambda: "pruned"),
            fixes_deficit=job_id,
            cooldown_hours=cooldown_hours,
        )
    )
    if ran_ago:
        state = rem._load_job_state()
        state[job_id] = {"last_success_ts": time.time() - ran_ago}
        rem._save_job_state(state)
    return Deficit(key=job_id, count=40, weight=1.0, max_penalty=40.0, job_id=job_id)


def _fire(store, monkeypatch, deficits):
    _patch_config(monkeypatch)
    P.reconcile_remediation_trigger(store)
    monkeypatch.setattr(rem, "measure_deficits", lambda: deficits)
    started = time.time()
    result = asyncio.run(P.SelfRemediationActionProvider().execute({}, ActionContext(event="cron")))
    assert result.success, result.error
    trigger = store.get(P.REMEDIATION_TRIGGER_ID).trigger
    return trigger, to_epoch(trigger.next_fire_at) - started, result.stdout


def test_a_pass_that_can_only_wait_looks_again_when_the_fix_is_due(store, monkeypatch):
    """🔴 Before: five minutes, every pass, while the prune waited out its 15-minute cooldown."""
    deficits = [_a_deficit_fixed_by("orphan_locks", cooldown_hours=0.25, ran_ago=1 * MIN)]
    trigger, wait, said = _fire(store, monkeypatch, deficits)
    assert 13 * MIN < wait <= 14 * MIN + 5, wait
    assert "next in 14m" in said, said


def test_a_fix_due_after_the_long_cadence_waits_the_long_cadence(store, monkeypatch):
    """🔴 Before: five minutes, every pass, for a day."""
    deficits = [_a_deficit_fixed_by("orphan_locks", cooldown_hours=24.0, ran_ago=60 * MIN)]
    trigger, wait, said = _fire(store, monkeypatch, deficits)
    assert 59 * MIN < wait <= 60 * MIN + 5, wait
    assert "next in 60m" in said, said


def test_the_soonest_of_several_waiting_fixes_sets_the_wait(store, monkeypatch):
    deficits = [
        _a_deficit_fixed_by("fts_rebuild", cooldown_hours=6.0, ran_ago=60 * MIN),
        _a_deficit_fixed_by("orphan_locks", cooldown_hours=0.5, ran_ago=10 * MIN),
    ]
    trigger, wait, said = _fire(store, monkeypatch, deficits)
    assert 19 * MIN < wait <= 20 * MIN + 5, wait
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=time.time())
    waited = {j["id"]: j["due_at"] for j in result.jobs if j["status"] == "skipped_cooldown"}
    assert result.next_due_at == min(waited.values())


def test_a_pass_that_ran_a_fix_and_still_has_work_keeps_the_short_cadence(store, monkeypatch):
    """Unchanged: the clock stays short while the engine can still do something."""
    deficits = [
        _a_deficit_fixed_by("orphan_locks", cooldown_hours=0.0, ran_ago=0),
        _a_deficit_fixed_by("fts_rebuild", cooldown_hours=6.0, ran_ago=60 * MIN),
    ]
    trigger, wait, said = _fire(store, monkeypatch, deficits)
    assert trigger.spec["health_state"] == "degraded"
    assert wait <= 5 * MIN + 5, wait
