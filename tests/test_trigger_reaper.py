"""The trigger reaper: bounding store-backed runs that blew their deadline (S106).

Replaces `test_cron_reaper.py` + `test_cron_reaper_ephemeral.py` (23 tests), which pinned
`ScheduleService`'s reaper. That reaper swept `_job_start_times`, a dict written ONLY by
`_run_job_isolated` — reachable only from the legacy timer the S100 cutover stopped arming — so it
had been provably inert for six sessions. Every one of those 23 tests passed against it the whole
time, because each one wrote the input dict BY HAND before sweeping. That is the lesson worth
keeping: a test that constructs the state its subject is supposed to observe cannot tell you whether
anything real ever produces that state.

So these tests drive the reaper through the seam the RUNTIME writes — S97's claim store, which the
tick populates when it grants a fire and the executor releases in its `finally`. Every meaningful
contract from the old files is ported (deadline respected, in-deadline runs untouched, health +
error recorded, SEL audit emitted, missing/unreadable state survived, cancellation propagates), and
the ones that only described deleted internals (`_reaped_jobs`, `_active_session_keys`, the
ephemeral-vs-stable session-key fallback) are gone with the mechanism they described.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.triggers import claims, reaper
from personalclaw.triggers.models import Trigger, TriggerHealth
from personalclaw.triggers.scheduling import Claim
from personalclaw.triggers.store import TriggerStore

NOW = 1_700_000_000.0
OVER = reaper.RUN_DEADLINE_SECS + 120  # comfortably past the deadline


@pytest.fixture
def home(tmp_path):
    """An isolated store home. Every claim + trigger read here resolves under it."""
    return tmp_path


@pytest.fixture
def store(home):
    return TriggerStore(base_dir=home)


def _trigger(store, trigger_id="clock:nightly", name="Nightly"):
    tr = Trigger(id=trigger_id, name=name, kind="clock")
    store.upsert(tr)
    return tr


def _claim(home, trigger_id="clock:nightly", *, age: float):
    """Write the claim a granted fire leaves behind, `age` seconds ago."""
    claims.write_claim(
        Claim(trigger_id=trigger_id, holder="tick:1", claimed_at=NOW - age), base_dir=home
    )


# ── overdue(): the pure read ──


def test_a_run_past_the_deadline_is_overdue(home):
    _claim(home, age=OVER)
    assert reaper.overdue(now=NOW, base_dir=home) == [("clock:nightly", OVER)]


def test_a_run_inside_the_deadline_is_left_alone(home):
    """Ported from `test_reaper_skips_jobs_within_deadline`: the deadline is a deadline, and a slow
    run is not a hung one."""
    _claim(home, age=60.0)
    assert reaper.overdue(now=NOW, base_dir=home) == []


def test_a_run_exactly_at_the_deadline_is_not_yet_overdue(home):
    """Strictly greater-than, so the boundary second belongs to the run, not the reaper."""
    _claim(home, age=reaper.RUN_DEADLINE_SECS)
    assert reaper.overdue(now=NOW, base_dir=home) == []


def test_an_idle_trigger_is_never_overdue(home, store):
    """No claim = no run in flight. A trigger that merely EXISTS must not be reaped."""
    _trigger(store)
    assert reaper.overdue(now=NOW, base_dir=home) == []


def test_a_self_expired_claim_is_not_reaped_again(home):
    """S97's claims expire at read time after 1h. That outer backstop already freed the trigger, so
    reaping it again would log a kill for a run nothing is holding."""
    _claim(home, age=7200.0)  # past CLAIM_MAX_DURATION_SECS
    assert reaper.overdue(now=NOW, base_dir=home) == []


def test_overdue_is_sorted_for_a_reproducible_sweep(home):
    for tid in ("clock:c", "clock:a", "clock:b"):
        _claim(home, tid, age=OVER)
    assert [t for t, _ in reaper.overdue(now=NOW, base_dir=home)] == [
        "clock:a",
        "clock:b",
        "clock:c",
    ]


def test_a_missing_claims_dir_is_not_an_error(tmp_path):
    """A home that has never fired anything has no `trigger-claims/` at all."""
    assert reaper.overdue(now=NOW, base_dir=tmp_path / "never-used") == []


# ── reap_one(): the effect ──


def test_reaping_releases_the_claim(home, store):
    """🔴 THE POINT. A stuck claim wedges `overlap: skip` until the 1h expiry, so the trigger would
    silently skip every fire for an hour. Releasing it is what makes the next fire possible."""
    _trigger(store)
    _claim(home, age=OVER)
    assert claims.is_running("clock:nightly", now=NOW, base_dir=home) is True

    with patch("personalclaw.sel.sel"):
        record = reaper.reap_one("clock:nightly", OVER, store=store, now=NOW, base_dir=home)

    assert claims.is_running("clock:nightly", now=NOW, base_dir=home) is False
    assert record["released"] is True


def test_reaping_records_degraded_health_and_the_reason(home, store):
    """Ported from `test_reaper_kills_expired_job`, which asserted `last_status == "error"`.

    DEGRADED rather than FAILING: `migrate.py`'s `_HEALTH_FROM_STATUS` maps a legacy `timeout` to
    DEGRADED, and that is the honest reading — the trigger is not broken, its last run did not
    finish. Written to `health_status`/`last_error_summary`, the fields a `Trigger` actually has;
    `last_status`/`last_error` are the LEGACY names the field map translates FROM, so writing those
    would set two attributes nothing reads and leave the health dot green on a reaped run.
    """
    _trigger(store)
    _claim(home, age=OVER)

    with patch("personalclaw.sel.sel"):
        record = reaper.reap_one("clock:nightly", OVER, store=store, now=NOW, base_dir=home)

    row = store.get("clock:nightly")
    assert row is not None
    assert row.trigger.health_status == TriggerHealth.DEGRADED.value
    assert "Reaped after" in row.trigger.last_error_summary
    assert "1800s deadline" in row.trigger.last_error_summary
    assert record["recorded"] is True


def test_reaping_emits_the_sel_audit_the_cron_reaper_emitted(home, store):
    """Same `tool_name`/`outcome`/`source` as before, so an operator's existing SEL query still
    finds reaps after the cutover."""
    _trigger(store)
    _claim(home, age=OVER)

    with patch("personalclaw.sel.sel") as mock_sel:
        reaper.reap_one("clock:nightly", OVER, store=store, now=NOW, base_dir=home)

    mock_sel().log_tool_invocation.assert_called_once_with(
        session_key="cron:clock:nightly",
        source="cron",
        tool_name="reaper_force_kill",
        outcome="reaped",
        metadata={"job_id": "clock:nightly", "elapsed": int(OVER)},
    )


def test_reaping_a_trigger_with_no_store_row_still_frees_it(home, store):
    """Ported from `test_force_reap_without_sessions`: the release is the load-bearing half, so a
    trigger whose row was deleted mid-run must still get its claim back."""
    _claim(home, "clock:ghost", age=OVER)

    with patch("personalclaw.sel.sel"):
        record = reaper.reap_one("clock:ghost", OVER, store=store, now=NOW, base_dir=home)

    assert record["released"] is True
    assert record["recorded"] is False
    assert claims.is_running("clock:ghost", now=NOW, base_dir=home) is False


def test_reaping_with_no_store_at_all_still_frees_the_claim(home):
    """`store=None` is the API-only case. The claim is on disk either way."""
    _claim(home, age=OVER)

    with patch("personalclaw.sel.sel"):
        record = reaper.reap_one("clock:nightly", OVER, store=None, now=NOW, base_dir=home)

    assert record["released"] is True
    assert claims.is_running("clock:nightly", now=NOW, base_dir=home) is False


def test_a_raising_store_does_not_stop_the_release(home):
    """One unreadable row must not turn the sweep into a no-op for everything after it."""
    _claim(home, age=OVER)
    broken = MagicMock()
    broken.get.side_effect = OSError("disk gone")

    with patch("personalclaw.sel.sel"):
        record = reaper.reap_one("clock:nightly", OVER, store=broken, now=NOW, base_dir=home)

    assert record["released"] is True
    assert record["recorded"] is False
    assert claims.is_running("clock:nightly", now=NOW, base_dir=home) is False


def test_a_failing_sel_audit_does_not_mask_the_reap(home, store):
    _trigger(store)
    _claim(home, age=OVER)

    with patch("personalclaw.sel.sel", side_effect=RuntimeError("no sel")):
        record = reaper.reap_one("clock:nightly", OVER, store=store, now=NOW, base_dir=home)

    assert record["released"] is True
    assert claims.is_running("clock:nightly", now=NOW, base_dir=home) is False


# ── sweep_once(): the whole pass ──


def test_a_sweep_reaps_every_overdue_run_and_spares_the_rest(home, store):
    _trigger(store, "clock:hung-a", "A")
    _trigger(store, "clock:hung-b", "B")
    _trigger(store, "clock:fine", "Fine")
    _claim(home, "clock:hung-a", age=OVER)
    _claim(home, "clock:hung-b", age=OVER)
    _claim(home, "clock:fine", age=30.0)

    with patch("personalclaw.sel.sel"):
        records = reaper.sweep_once(store=store, now=NOW, base_dir=home)

    assert [r["trigger_id"] for r in records] == ["clock:hung-a", "clock:hung-b"]
    assert claims.is_running("clock:fine", now=NOW, base_dir=home) is True
    assert store.get("clock:fine").trigger.health_status == TriggerHealth.OK.value


def test_a_sweep_with_nothing_overdue_does_nothing(home, store):
    _trigger(store)
    _claim(home, age=30.0)
    assert reaper.sweep_once(store=store, now=NOW, base_dir=home) == []


def test_a_shorter_deadline_reaps_a_younger_run(home, store):
    """The deadline is a parameter, so the doctor (and a future per-trigger override) can ask the
    same question with a different bound."""
    _trigger(store)
    _claim(home, age=100.0)

    with patch("personalclaw.sel.sel"):
        records = reaper.sweep_once(store=store, now=NOW, deadline_secs=50.0, base_dir=home)

    assert [r["trigger_id"] for r in records] == ["clock:nightly"]


def test_sweeping_twice_is_idempotent(home, store):
    """The second sweep finds nothing, because the first released the claim. Ported in spirit from
    the old `_job_start_times.pop(...)` "prevent repeated reaping" guard — the release IS that
    guard now, and it lives on disk rather than in a process's memory."""
    _trigger(store)
    _claim(home, age=OVER)

    with patch("personalclaw.sel.sel"):
        first = reaper.sweep_once(store=store, now=NOW, base_dir=home)
        second = reaper.sweep_once(store=store, now=NOW, base_dir=home)

    assert len(first) == 1
    assert second == []


# ── run_forever(): the loop ──


@pytest.mark.asyncio
async def test_the_loop_sweeps_on_its_interval(home, store):
    """Ported from `test_start_reaper_creates_task` + `test_reaper_loop_invokes_force_reap...`,
    which asserted a task object existed and that a hand-built dict got swept. This drives the real
    loop against a real claim and asserts the CLAIM IS GONE — the outcome, not the plumbing."""
    _trigger(store)
    _claim(home, age=OVER)

    with patch("personalclaw.sel.sel"):
        task = asyncio.create_task(
            reaper.run_forever(store=store, base_dir=home, interval_secs=0.01)
        )
        for _ in range(200):  # up to ~2s, but returns as soon as the sweep lands
            await asyncio.sleep(0.01)
            if not claims.is_running("clock:nightly", base_dir=home):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert claims.is_running("clock:nightly", base_dir=home) is False


@pytest.mark.asyncio
async def test_the_loop_propagates_cancellation(home, store):
    """Ported from `test_stop_cancels_reaper`. Shutdown has to be able to stop it."""
    task = asyncio.create_task(reaper.run_forever(store=store, base_dir=home, interval_secs=0.01))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_the_loop_outlives_a_failing_sweep(home, store):
    """A reaper that died on one bad sweep would silently stop bounding every run on the machine —
    and it would look exactly like a healthy one, which is how the loop it replaces stayed inert."""
    calls: list[int] = []
    real = reaper.sweep_once

    def flaky(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("transient")
        return real(**kwargs)

    _trigger(store)
    _claim(home, age=OVER)
    with patch.object(reaper, "sweep_once", flaky), patch("personalclaw.sel.sel"):
        task = asyncio.create_task(
            reaper.run_forever(store=store, base_dir=home, interval_secs=0.01)
        )
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(calls) >= 2:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(calls) >= 2  # it kept sweeping after the failure


# ── the deadline agrees with the reapers it sits beside ──


def test_the_deadline_matches_the_cron_and_subagent_reapers():
    """The plan keeps the reaper "as defense-in-depth over ALL trigger-fired runs", so the number a
    user already reasons about for a cron has to be the number a store-backed trigger gets. Three
    deadlines that drifted apart would make "why did my run stop at 30 minutes" unanswerable."""
    from personalclaw import subagent
    from personalclaw.schedule import _JOB_TIMEOUT_SECS

    assert reaper.RUN_DEADLINE_SECS == float(_JOB_TIMEOUT_SECS)
    assert reaper.RUN_DEADLINE_SECS == float(subagent._TIMEOUT_SECS)
    assert reaper.REAPER_INTERVAL_SECS == float(subagent._REAPER_INTERVAL)


def test_the_legacy_reaper_is_gone(home):
    """🔴 The clean break, completed. S106 deleted the inert reaper (two reapers, one of which reaped
    nothing and said so nowhere); S112 deleted the class that carried it, which is a stronger
    statement than any per-method check could make."""
    with pytest.raises(ImportError):
        from personalclaw.schedule import ScheduleService  # noqa: F401


def test_boot_starts_the_new_reaper_and_not_the_old_one():
    """Source-level, like S100's `test_triggers_loop.py` check: the wiring is the whole point of the
    session, and a boot test that mocks `ScheduleService` cannot see which loop was armed."""
    import pathlib

    import personalclaw.gateway as G

    src = pathlib.Path(G.__file__).read_text()
    assert "self._trigger_reaper_loop()" in src
    assert "cron_svc.start_reaper" not in src
