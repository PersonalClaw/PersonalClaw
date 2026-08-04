"""The executor — drain, run, classify (§3 / §1.3 — S90).

§3's fire path ends: "… engine executes under the `headless` profile → **outcome
classification (§1.3)** →
delivery contract → health rollup + failure policy." S86 built the gate order, S87 the store,
S88 the
tick, S89 the dispatcher; this is the last link.

**Two honesty contracts this module inherits rather than invents**, both already fought for in
shipped
code and both re-verified here:

1. **The `_STATUS_PENDING` sentinel.** `schedule._execute` seeds `last_status = "_pending"`
and defaults
   to `"ok"` ONLY if the sentinel survived — its own comment: "so a failed action's 'error' is
   no longer
   CLOBBERED by an unconditional 'ok' (the honest-status bug T7 set out to kill: a failed run
   recorded as
   success)".
2. **`launched` is not success.** `engine.dispatch_action`: "'launched' means background work
STARTED, not
   that it succeeded … Reporting it as success would make a fire-and-forget action look
   verified." S84's
   history projection maps it to `deferred` too. This is the third surface to preserve it.

Every drain test uses a real `SessionManager` inbox and the real `dequeue` (which skips
cancelled rows).
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import executor as E
from personalclaw.triggers import wakeup as W
from personalclaw.triggers.models import FIRE_OUTCOMES, Outcome, Trigger

NOW = 1_800_000_000.0


class _Provider:
    async def shutdown(self):
        return None


def _manager(*keys):
    from personalclaw.session import SessionManager, _Session

    manager = SessionManager.__new__(SessionManager)
    manager._sessions = {}
    for key in keys:
        manager._sessions[key] = _Session(provider=_Provider())
    return manager


async def _ok(_payload):
    return {"status": "ok", "run_id": "r1"}


async def _error(_payload):
    return {"status": "error"}


async def _raises(_payload):
    raise ValueError("provider exploded")


async def _launched(_payload):
    class _JobLike:
        last_status = "launched"
        run_id = "r9"

    return _JobLike()


# ── 🔴 the T7 sentinel rule ──


def test_a_surviving_sentinel_defaults_to_success():
    """🔴 The three-state logic `schedule._execute` established: only a SURVIVING sentinel
    becomes `ok`.
    An unconditional default would clobber a runner's own `"error"` — T7's honest-status bug."""
    assert E.classify("")[0] == Outcome.RAN.value
    assert E.classify(E.STATUS_PENDING)[0] == Outcome.RAN.value


def test_a_reported_error_is_never_clobbered_by_the_default():
    """The failure mode the sentinel exists to prevent: a failed run recorded as a success."""
    outcome, reason = E.classify("error")
    assert outcome == Outcome.FAILED.value
    assert reason


def test_the_sentinel_constant_matches_the_shipped_one():
    """Two different sentinels would let this module and `ScheduleService` disagree about "nothing
    reported yet", and the disagreement would surface as inconsistent statuses."""
    from personalclaw.schedule import _STATUS_PENDING

    assert E.STATUS_PENDING == _STATUS_PENDING


# ── 🔴 launched ≠ succeeded ──


def test_launched_maps_to_DEFERRED_not_RAN():
    """🔴 `engine.dispatch_action`: "Reporting it as success would make a fire-and-forget action look
    verified." S84 preserved this in history; this is the third surface."""
    outcome, reason = E.classify("launched")
    assert outcome == Outcome.DEFERRED.value
    assert outcome != Outcome.RAN.value
    assert "not yet known" in reason


def test_a_deferred_outcome_is_NOT_settled():
    """Neither a success nor a failure — a rollup counting it either way would be lying in one
    direction."""
    deferred = E.RunOutcome(trigger_id="t", session_key="s", outcome=Outcome.DEFERRED.value)
    assert deferred.settled is False
    assert deferred.ok is False


# ── classification, generally ──


def test_an_exception_wins_over_any_reported_status():
    """A runner that reported `ok` and then raised did NOT succeed."""
    outcome, reason = E.classify("ok", RuntimeError("boom"))
    assert outcome != Outcome.RAN.value
    assert "RuntimeError" in reason


def test_an_exception_reason_names_the_type_and_message():
    """ "it failed" is not actionable; the exception type is the first thing a user needs."""
    _outcome, reason = E.classify("", ValueError("bad config"))
    assert "ValueError" in reason and "bad config" in reason


def test_an_unrecognized_status_becomes_failed_not_ran():
    """A status this build cannot classify must not count as a success, because a success is what a
    health rollup treats as nothing to look at."""
    outcome, reason = E.classify("wat")
    assert outcome == Outcome.FAILED.value
    assert "unrecognized" in reason


@pytest.mark.parametrize("status", sorted(E.STATUS_TO_OUTCOME))
def test_every_mapped_status_yields_a_typed_outcome(status):
    """A suppression outside `FIRE_OUTCOMES` would be unfilterable in the runs inbox."""
    outcome, _reason = E.classify(status)
    assert outcome in FIRE_OUTCOMES


def test_a_refusal_is_distinct_from_a_failure():
    """`autopause` thresholds on TRUE failures; a refusal is a policy decision, not a broken
    automation."""
    assert E.classify("refused")[0] == Outcome.REFUSED.value
    assert E.classify("error")[0] == Outcome.FAILED.value


# ── run_one: the injected runner ──


def test_a_runner_reporting_via_a_dict_is_honoured():
    outcome = asyncio.run(E.run_one({"trigger_id": "t1"}, _ok, session_key="cron:t1", now=NOW))
    assert outcome.ok is True
    assert outcome.reported == "ok"
    assert outcome.run_id == "r1"


def test_a_runner_reporting_via_an_attribute_is_honoured():
    """The shipped `ScheduleJob` carries `last_status`, so a runner shaped like one must work
    without
    the caller translating."""
    outcome = asyncio.run(E.run_one({"trigger_id": "t1"}, _launched, now=NOW))
    assert outcome.outcome == Outcome.DEFERRED.value
    assert outcome.run_id == "r9"


def test_a_raising_runner_becomes_a_failed_outcome_not_a_crash():
    """The outcome IS the error; re-raising would lose the row, and a lost row is the silent drop §7
    crit 8 bans."""
    outcome = asyncio.run(E.run_one({"trigger_id": "t1"}, _raises, now=NOW))
    assert outcome.outcome == Outcome.FAILED.value
    assert "provider exploded" in outcome.reason


def test_the_runners_own_status_is_kept_verbatim():
    """A user debugging an automation wants the provider's word for it, not only this module's
    translation."""
    outcome = asyncio.run(E.run_one({"trigger_id": "t1"}, _error, now=NOW))
    assert outcome.reported == "error"
    assert outcome.outcome == Outcome.FAILED.value


def test_the_run_is_timed():
    outcome = asyncio.run(E.run_one({"trigger_id": "t1"}, _ok, now=NOW))
    assert outcome.duration_secs >= 0.0


def test_the_session_key_falls_back_to_the_payload():
    outcome = asyncio.run(
        E.run_one({"trigger_id": "t1", "session_key": "cron:from-payload"}, _ok, now=NOW)
    )
    assert outcome.session_key == "cron:from-payload"


# ── drain: against a real inbox ──


def _queue_fire(manager, key, trigger_id="schedule:j1"):
    trigger = Trigger(
        id=trigger_id,
        name="N",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 3600},
        workflow={"provider": "run-prompt", "config": {}},
        capabilities={"providers": ["run-prompt"]},
    )

    class _Fire:
        pass

    fire = _Fire()
    fire.trigger = trigger
    fire.scheduled_for = NOW - 5
    fire.reason = "due"
    W.deliver(manager, W.wakeup_for(fire, seq=1, now=NOW))
    return key


def test_a_drain_runs_every_queued_fire():
    manager = _manager("cron:j1")
    _queue_fire(manager, "cron:j1")
    seen: list[str] = []

    async def spy(payload):
        seen.append(payload.get("trigger_id", ""))
        return {"status": "ok"}

    result = asyncio.run(E.drain(manager, "cron:j1", spy, now=NOW))
    assert result.ran == 1
    assert seen == ["schedule:j1"]
    assert len(manager._sessions["cron:j1"].queue) == 0


def test_an_empty_inbox_drains_to_nothing():
    manager = _manager("cron:j1")
    result = asyncio.run(E.drain(manager, "cron:j1", _ok, now=NOW))
    assert result.outcomes == []
    assert result.truncated is False


def test_a_non_trigger_row_is_SKIPPED_not_run():
    """🔴 A chat nudge shares the queue. Executing an unrecognized payload as if it were a fire
    is how
    one subsystem's message becomes another's action."""
    manager = _manager("cron:x")
    manager.enqueue("cron:x", "ts", "a chat nudge", force=True)
    ran: list[str] = []

    async def spy(payload):
        ran.append("ran")
        return {"status": "ok"}

    result = asyncio.run(E.drain(manager, "cron:x", spy, now=NOW))
    assert result.outcomes == []
    assert result.skipped == 1
    assert ran == []


def test_the_cap_is_REPORTED_not_silent():
    """A partial drain that looked complete would make a backed-up queue invisible — the S65
    rule this
    program keeps re-learning on new surfaces."""
    manager = _manager("cron:y")
    for i in range(5):
        W.deliver(
            manager,
            W.resume_for(
                trigger_id=f"t{i}",
                session_key="cron:y",
                answer={"trigger_id": f"t{i}"},
                now=NOW,
            ),
        )
    result = asyncio.run(E.drain(manager, "cron:y", _ok, limit=2, now=NOW))
    assert len(result.outcomes) == 2
    assert result.truncated is True


def test_a_full_drain_reports_no_truncation():
    manager = _manager("cron:j1")
    _queue_fire(manager, "cron:j1")
    result = asyncio.run(E.drain(manager, "cron:j1", _ok, limit=10, now=NOW))
    assert result.truncated is False


def test_no_session_manager_is_survivable():
    result = asyncio.run(E.drain(None, "cron:j1", _ok, now=NOW))
    assert result.outcomes == []


def test_a_failing_runner_does_not_stop_the_drain():
    """One bad fire must not strand the rest of the inbox."""
    manager = _manager("cron:z")
    for i in range(3):
        W.deliver(
            manager,
            W.resume_for(
                trigger_id=f"t{i}",
                session_key="cron:z",
                answer={"trigger_id": f"t{i}"},
                now=NOW,
            ),
        )
    calls = {"n": 0}

    async def flaky(_payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return {"status": "ok"}

    result = asyncio.run(E.drain(manager, "cron:z", flaky, now=NOW))
    assert len(result.outcomes) == 3
    assert result.ran == 2
    assert result.failed == 1


# ── the delivery seam (S85) ──


def test_a_settled_run_produces_a_delivery():
    outcome = E.RunOutcome(
        trigger_id="schedule:j1", session_key="cron:j1", outcome=Outcome.RAN.value, run_id="r1"
    )
    delivery = E.delivery_for(outcome, trigger_name="Nightly")
    assert delivery is not None
    assert delivery.ok is True
    assert delivery.status_url == "#/workflows/runs/r1"


def test_a_failed_run_produces_a_FAILED_event():
    outcome = E.RunOutcome(
        trigger_id="schedule:j1",
        session_key="cron:j1",
        outcome=Outcome.FAILED.value,
        reason="boom",
    )
    assert E.delivery_for(outcome).event == "automation.run.failed"


def test_a_DEFERRED_run_produces_NO_delivery():
    """🔴 A "finished" notification for work nobody has seen would be the fire-and-forget lie
    `dispatch_action`'s docstring warns about. The delivery goes out when the background turn
    reports."""
    deferred = E.RunOutcome(
        trigger_id="schedule:j1", session_key="cron:j1", outcome=Outcome.DEFERRED.value
    )
    assert E.delivery_for(deferred) is None


# ── the ledger + health rollup ──


def test_every_executed_payload_yields_a_ledger_row():
    """S86 writes a row per fire EVALUATED; this writes one per fire that actually ran. Both
    halves are
    needed — a fire that passed every gate then died in the executor would otherwise leave a
    `ran` row
    from the gate walk and nothing else."""
    result = E.DrainResult(
        session_key="cron:j1",
        outcomes=[
            E.RunOutcome(trigger_id="a", session_key="cron:j1", outcome=Outcome.RAN.value),
            E.RunOutcome(trigger_id="b", session_key="cron:j1", outcome=Outcome.FAILED.value),
        ],
    )
    rows = E.ledger_rows(result)
    assert len(rows) == 2
    assert all(row["phase"] == "execute" for row in rows)


def test_a_deferred_run_counts_toward_NEITHER_health_bucket():
    """🔴 §3.7. Counting a launched-but-unverified run as a success marks a broken automation
    healthy;
    counting it as a failure autopauses one that works."""
    result = E.DrainResult(
        session_key="s",
        outcomes=[
            E.RunOutcome(trigger_id="a", session_key="s", outcome=Outcome.RAN.value),
            E.RunOutcome(trigger_id="b", session_key="s", outcome=Outcome.FAILED.value),
            E.RunOutcome(trigger_id="c", session_key="s", outcome=Outcome.DEFERRED.value),
        ],
    )
    health = E.health_delta(result)
    assert health == {
        "settled": 2,
        "succeeded": 1,
        "failed": 1,
        "deferred": 1,
        "consecutive_failures": 1,
    }


def test_only_true_failures_advance_the_autopause_counter():
    """S68's finding: a denylist BLOCK disabled a trigger because it counted as a failure. A
    refusal is
    a policy decision, not a broken automation."""
    result = E.DrainResult(
        session_key="s",
        outcomes=[E.RunOutcome(trigger_id="a", session_key="s", outcome=Outcome.REFUSED.value)],
    )
    assert E.health_delta(result)["consecutive_failures"] == 0


def test_the_drain_result_serializes_for_a_surface():
    result = E.DrainResult(
        session_key="cron:j1",
        outcomes=[E.RunOutcome(trigger_id="a", session_key="cron:j1", outcome=Outcome.RAN.value)],
    )
    payload = result.to_dict()
    assert payload["ran"] == 1
    assert payload["outcomes"][0]["settled"] is True


# ── the whole chain ──


def test_store_to_tick_to_dispatch_to_execute(tmp_path):
    """The substrate end to end, with only the LLM turn injected: store → tick → dispatch → drain.

    This is what every prior session's "NOT DONE: the service/executor" note was waiting for,
    and the
    only mock is the runner — because §3 puts the turn behind `SubagentManager.spawn`, which
    is the one
    piece that genuinely needs a model.
    """
    from personalclaw.triggers import service as SVC
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    store.save_all(
        [
            Trigger(
                id=f"t{i}",
                name=f"T{i}",
                kind="clock",
                enabled=True,
                spec={"kind": "interval", "interval_secs": 3600},
                workflow={"provider": "run-prompt", "config": {}},
                capabilities={"providers": ["run-prompt"]},
                next_fire_at=SVC.to_iso(NOW - 5),
            )
            for i in range(3)
        ]
    )

    result = asyncio.run(SVC.tick(store, now=NOW))
    assert len(result.fires) == 3

    keys = [W.session_key_for(f.trigger.id) for f in result.fires]
    manager = _manager(*keys)
    deliveries = W.dispatch_fires(manager, result.fires, now=NOW)
    assert W.summary(deliveries)["delivered"] == 3

    executed: list[str] = []

    async def runner(payload):
        executed.append(payload.get("trigger_id", ""))
        return {"status": "ok", "run_id": f"run-{payload.get('trigger_id')}"}

    for key in keys:
        drained = asyncio.run(E.drain(manager, key, runner, now=NOW))
        assert drained.ran == 1

    assert sorted(executed) == ["t0", "t1", "t2"]
    # And every trigger's next fire was persisted by the tick, so a crash now cannot double-fire.
    for i in range(3):
        assert SVC.to_epoch(store.get(f"t{i}").trigger.next_fire_at) > NOW
