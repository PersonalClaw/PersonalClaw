"""A run the deadline reaper reaps in this process is ended, and recorded once, as cut off.

🔴 The defect (Backlog: "An automation's run the reaper calls timed out keeps running beside the
next one"). The reaper (`reaper.reap_one`) found a run past its deadline, released its claim and
wrote its `timeout` row, and the run went on: its action was still running in the gateway that
started it. With the claim gone the next fire started a second run beside it, and when the first
ended it wrote a row of its own, two rows for one run. The reaper could not end it, because the
action ran inside the tick loop's own task (`loop._execute_delivery` awaits the fire), and a Run
now's inside its request's.

Now the action runs as a task of its own (`triggers.inflight`), and the reaper ends it alone: the
run's one row says it was cut off at the deadline, nothing chains after it, and the tick loop that
awaited it goes on to its next fire. A stop still reads as a stop. A run beside it that started
since is left running, and a claim another live process holds is left to that claim's expiry.

Driven through the real fire dispatch (`GatewayOrchestrator._fire_store_trigger`, reached from the
tick's own `loop._execute_delivery`), the real Run now (`trigger_runs._dispatch_store_action`), the
real sweep (`reaper.sweep_once`, from a thread as `reaper.run_forever` runs it) and the real claims
and run history, in a home of the test's own.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest
import test_a_stop_records_the_run_it_cut_off as _stop

from personalclaw.action_providers.base import ActionResult
from personalclaw.triggers import claims, inflight
from personalclaw.triggers import loop as clock_loop
from personalclaw.triggers import reaper
from personalclaw.triggers import wakeup as wk
from personalclaw.triggers.scheduling import Claim
from personalclaw.triggers.store import TriggerStore

TID = _stop.TID
DEADLINE = reaper.RUN_DEADLINE_SECS
home = _stop.home


class _Hangs:
    """An action that runs until it is ended, then one that returns at once (the next fire)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.hang = True
        self.runs = 0
        self.ended = 0

    async def execute(self, config: Any, ctx: Any, timeout: float = 30) -> Any:
        self.runs += 1
        if not self.hang:
            return ActionResult(success=True, exit_code=0, stdout="done", summary="done")
        self.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.ended += 1
            raise


def _delivery() -> Any:
    """A tick's delivery of one fire to a trigger with no session inbox: every scheduled fire on a
    real gateway (`loop._execute_delivery`)."""
    payload = {"trigger_id": TID}
    wakeup = SimpleNamespace(trigger_id=TID, session_key=f"cron:{TID}", payload=payload)
    return SimpleNamespace(wakeup=wakeup, disposition=wk.Disposition.NO_SESSION.value)


def _ticks(orch: Any, home_dir: Any) -> Any:
    """The gateway's clock runner (`GatewayOrchestrator._clock_loop._runner`)."""

    async def _runner(payload: dict[str, Any]) -> Any:
        row = TriggerStore(base_dir=home_dir).get(str(payload["trigger_id"]))
        await orch._fire_store_trigger(row.trigger, payload)
        return {"status": "launched"}

    return _runner


def _claim(home_dir: Any, **over: Any) -> None:
    claims.write_claim(
        Claim(trigger_id=TID, holder="tick:1", claimed_at=time.time(), **over), base_dir=home_dir
    )


async def _sweep(home_dir: Any, now: float) -> list[dict[str, Any]]:
    """One sweep, from a thread, as `reaper.run_forever` runs it."""
    store = TriggerStore(base_dir=home_dir)
    return await asyncio.to_thread(reaper.sweep_once, store=store, now=now, base_dir=home_dir)


def test_a_fire_past_its_deadline_is_ended_and_its_one_row_says_so(home, monkeypatch):
    """🔴 Before: the action ran on after the reap, and its own record wrote a second row."""
    provider = _Hangs()
    orch, _notes, chained = _stop._gateway(monkeypatch, provider)
    runner = _ticks(orch, home)

    async def _drive() -> tuple[Any, list[dict[str, Any]], Any]:
        _claim(home)
        fire = asyncio.ensure_future(
            clock_loop._execute_delivery(_delivery(), runner, sessions=None, base_dir=home)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        records = await _sweep(home, time.time() + DEADLINE + 5)
        outcomes = await asyncio.wait_for(fire, timeout=10)
        return fire, records, outcomes

    fire, (record,), outcomes = asyncio.run(_drive())

    assert provider.ended == 1, "the action was ended"
    assert (record["released"], record["ended"], record["recorded"]) == (True, 1, True)
    (row,) = _stop._rows(home)
    assert row["status"] == reaper.DEADLINE_STATUS
    assert row["error"].startswith(f"Cut off at the {int(DEADLINE)}s deadline after "), row
    assert "its action was still running, and was ended" in row["error"]
    assert chained == [], "nothing runs after a run cut off"
    assert not claims.is_running(TID, base_dir=home)
    # The tick that awaited it was not cancelled: it went on, with the fire's outcome.
    assert not fire.cancelled() and [o.trigger_id for o in outcomes] == [TID]


def test_the_tick_goes_on_to_the_next_fire(home, monkeypatch):
    """The next fire runs, in the same loop, and writes its own row beside the cut one."""
    provider = _Hangs()
    orch, _notes, _chained = _stop._gateway(monkeypatch, provider)
    runner = _ticks(orch, home)

    async def _drive() -> None:
        _claim(home)
        fire = asyncio.ensure_future(
            clock_loop._execute_delivery(_delivery(), runner, sessions=None, base_dir=home)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        await _sweep(home, time.time() + DEADLINE + 5)
        await asyncio.wait_for(fire, timeout=10)
        provider.hang = False
        _claim(home)
        await clock_loop._execute_delivery(_delivery(), runner, sessions=None, base_dir=home)

    asyncio.run(_drive())

    assert provider.runs == 2
    newest, cut = _stop._rows(home)
    assert (newest["status"], cut["status"]) == ("success", reaper.DEADLINE_STATUS)


def test_a_run_now_past_its_deadline_is_ended_and_says_so(home, monkeypatch):
    """A Run now runs its action in its request's task; the cut ends the action, and the request
    answers that it was cut off, with the one row the reaper wrote, which says it was yours."""
    from personalclaw.dashboard.handlers import trigger_runs

    provider = _Hangs()
    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda name: provider)
    trigger = TriggerStore(base_dir=home).get(TID).trigger

    async def _drive() -> Any:
        run = asyncio.ensure_future(
            trigger_runs._dispatch_store_action(trigger, {"trigger_id": TID, "manual": True})
        )
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        await _sweep(home, time.time() + DEADLINE + 5)
        return await asyncio.wait_for(run, timeout=10)

    ran, note = asyncio.run(_drive())

    assert provider.ended == 1
    assert (ran, note) == (False, trigger_runs._CUT_AT_THE_DEADLINE)
    (row,) = _stop._rows(home)
    assert row["status"] == reaper.DEADLINE_STATUS and row["source"] == "you"
    assert not claims.is_running(TID, base_dir=home)


def test_a_stop_still_reads_as_a_stop(home, monkeypatch):
    """A stop cancels the tick itself, which cancels the action; its row is the stop's, and the
    stop is not taken for a deadline cut."""
    _stop._restarting(monkeypatch, False)
    provider = _Hangs()
    orch, _notes, _chained = _stop._gateway(monkeypatch, provider)
    runner = _ticks(orch, home)

    async def _drive() -> None:
        _claim(home)
        fire = asyncio.ensure_future(
            clock_loop._execute_delivery(_delivery(), runner, sessions=None, base_dir=home)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        fire.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fire

    asyncio.run(_drive())

    assert provider.ended == 1
    (row,) = _stop._rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS, row
    assert asyncio.run(_sweep(home, time.time() + DEADLINE + 5)) == []


def test_a_run_beside_it_that_started_since_is_left_running(home):
    """Two actions under way for one trigger (an answer's run beside a tick's): the cut ends the
    one past the deadline only."""
    seen: dict[str, Any] = {}

    async def _act(started: asyncio.Event) -> None:
        started.set()
        await asyncio.sleep(3600)

    async def _drive() -> None:
        _claim(home)
        first, second = asyncio.Event(), asyncio.Event()
        old = asyncio.ensure_future(inflight.acting(TID, _act(first)))
        await first.wait()
        await asyncio.sleep(0.01)
        between = time.time()
        await asyncio.sleep(0.01)
        new = asyncio.ensure_future(inflight.acting(TID, _act(second)))
        await second.wait()
        (record,) = await _sweep(home, between + DEADLINE)
        seen["ended"] = record["ended"]
        with pytest.raises(inflight.CutAtDeadline):
            await asyncio.wait_for(old, timeout=10)
        await asyncio.sleep(0.05)
        seen["new_running"] = not new.done()
        new.cancel()
        with pytest.raises(asyncio.CancelledError):
            await new

    asyncio.run(_drive())

    assert seen == {"ended": 1, "new_running": True}


def _alive_elsewhere() -> int:
    """A process other than this one that is running: this test's parent."""
    pid = os.getppid()
    assert pid > 1 and pid != os.getpid()
    return pid


def _gone() -> int:
    """The pid of a process that has ended."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_a_claim_another_live_process_holds_is_left_to_its_expiry(home):
    """This process cannot end that run, and freeing its claim would only let a second run start
    beside it: no release, no row, and it reads as running until the claim expires."""
    _claim(home, owner_pid=_alive_elsewhere())
    held = claims.read_claim(TID, base_dir=home)
    later = held.claimed_at + DEADLINE + 5

    (record,) = asyncio.run(_sweep(home, later))

    assert (record["released"], record["ended"], record["recorded"]) == (False, 0, False)
    assert _stop._rows(home) == []
    assert claims.is_running(TID, now=later, base_dir=home)
    expired = held.claimed_at + held.max_duration_secs + 1
    assert not claims.is_running(TID, now=expired, base_dir=home)


def test_a_claim_whose_owner_has_ended_is_still_reaped(home):
    """The control: an owner that is gone holds nothing, and its claim is freed at the deadline
    as before, with its row."""
    _claim(home, owner_pid=_gone())

    (record,) = asyncio.run(_sweep(home, time.time() + DEADLINE + 5))

    assert (record["released"], record["ended"], record["recorded"]) == (True, 0, True)
    (row,) = _stop._rows(home)
    assert row["error"].startswith("Reaped after "), row
    assert not claims.is_running(TID, base_dir=home)


def test_a_stop_that_lands_as_the_cut_does_is_still_a_stop(home, monkeypatch):
    """The stop wins: the tick it cancels must stop, not go on as if only its action were cut."""
    _stop._restarting(monkeypatch, False)
    provider = _Hangs()
    orch, _notes, _chained = _stop._gateway(monkeypatch, provider)
    runner = _ticks(orch, home)

    async def _drive() -> None:
        _claim(home)
        fire = asyncio.ensure_future(
            clock_loop._execute_delivery(_delivery(), runner, sessions=None, base_dir=home)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        assert inflight.cut(TID, older_than=time.time() + 1) == 1
        fire.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fire

    asyncio.run(_drive())

    (row,) = _stop._rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS, row


def test_an_action_that_ends_as_the_cut_lands_is_still_the_cut_one(home):
    """The reaper has written the run as cut off by then, so the run records nothing of its own:
    two rows for one run is the defect."""

    async def _drive() -> None:
        go = asyncio.Event()

        async def _act() -> str:
            await go.wait()
            return "done"

        acting = asyncio.ensure_future(inflight.acting(TID, _act()))
        await asyncio.sleep(0)
        # It runs to its end before the cut's cancel does: it was scheduled first.
        go.set()
        assert inflight.cut(TID, older_than=time.time() + 1) == 1
        with pytest.raises(inflight.CutAtDeadline):
            await asyncio.wait_for(acting, timeout=10)

    asyncio.run(_drive())
