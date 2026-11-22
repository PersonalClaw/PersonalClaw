"""One automation's long action holds no other automation's fire.

🔴 The defect (Backlog: "One automation's long action holds every other scheduled automation until
it ends"). The clock loop ran each fire it granted to its end before it ticked again:
`loop.tick_once` awaited `_execute_delivery`, which awaited the action. While one automation's
action ran, no other scheduled automation fired. Measured on a twin, a 2-minute automation fired 0
times in the 30 minutes a long command beside it ran, and its slots came back as one missed card.

Now the loop runs each fire beside the tick (`tick_once`'s `fires`), and keeps it until it ends.
Each automation's own rules hold: one that runs one at a time (`overlap: skip`) is not started
again while its fire runs, and one that may run beside itself (`overlap: parallel`) has its fires
under way counted toward its hourly cap. A stop of the loop stops each fire still under way, and
waits while it records itself.

Driven through the real tick (`loop.tick_once`, `service.tick`), the real dispatch and executor,
the real claims and the real loop (`loop.run_forever`), with the action runner injected as the
gateway injects it, in a store of the test's own.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from test_triggers_loop import NOW, _clock, _manager

from personalclaw.triggers import claims
from personalclaw.triggers import loop as L
from personalclaw.triggers import service as SVC
from personalclaw.triggers.store import TriggerStore


class _Actions:
    """The action runner: *slow* runs until it is stopped, every other one returns at once."""

    def __init__(self, *slow: str) -> None:
        self.slow = set(slow)
        self.started: dict[str, int] = {}
        self.stopped: dict[str, int] = {}
        self.first_slow = asyncio.Event()

    async def __call__(self, payload: dict[str, Any]) -> Any:
        tid = str(payload.get("trigger_id") or "")
        self.started[tid] = self.started.get(tid, 0) + 1
        if tid not in self.slow:
            return {"status": "ok"}
        self.first_slow.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Ending takes a moment, as a command's does (it is ended and collected first).
            await asyncio.sleep(0.05)
            self.stopped[tid] = self.stopped.get(tid, 0) + 1
            raise
        return {"status": "ok"}


async def _settle() -> None:
    """Let the fires a tick started take their next steps."""
    for _ in range(20):
        await asyncio.sleep(0)


async def _tick(store: TriggerStore, now: float, tick: dict[str, Any]) -> Any:
    """One tick, which must not wait for the fires it starts."""
    result = await asyncio.wait_for(L.tick_once(store, now=now, **tick), timeout=5)
    await _settle()
    return result


async def _stop(fires: set[asyncio.Task[Any]]) -> None:
    for task in list(fires):
        task.cancel()
    await asyncio.gather(*fires, return_exceptions=True)


def _store(tmp_path, *triggers) -> TriggerStore:
    store = TriggerStore(base_dir=tmp_path)
    for trigger in triggers:
        store.upsert(trigger)
    return store


def test_another_automation_fires_while_a_long_action_runs(tmp_path):
    """🔴 Before: the tick awaited the long action, so the next tick, and the ping's next fire,
    came only once it ended."""
    store = _store(tmp_path, _clock("slow"), _clock("ping"))
    actions = _Actions("slow")
    seen: dict[str, Any] = {}

    async def _drive() -> None:
        fires: set[asyncio.Task[Any]] = set()
        tick = dict(runner=actions, sessions=_manager(), base_dir=tmp_path, fires=fires)
        await _tick(store, NOW, tick)
        await asyncio.wait_for(actions.first_slow.wait(), timeout=5)
        await _tick(store, NOW + 61, tick)
        seen["started"] = dict(actions.started)
        seen["slow_running"] = claims.is_running("slow", now=NOW + 61, base_dir=tmp_path)
        await _stop(fires)

    asyncio.run(_drive())

    assert seen["started"].get("ping") == 2, seen
    assert seen["slow_running"], "the long action is still under way"


def test_one_that_runs_one_at_a_time_is_not_started_again_while_its_fire_runs(tmp_path):
    """`overlap: skip` keeps its claim until its own fire ends: its next slot does not start a
    second run beside the first."""
    store = _store(tmp_path, _clock("slow"))
    actions = _Actions("slow")

    async def _drive() -> None:
        fires: set[asyncio.Task[Any]] = set()
        tick = dict(runner=actions, sessions=_manager(), base_dir=tmp_path, fires=fires)
        await _tick(store, NOW, tick)
        await asyncio.wait_for(actions.first_slow.wait(), timeout=5)
        await _tick(store, NOW + 61, tick)
        await _tick(store, NOW + 122, tick)
        await _stop(fires)

    asyncio.run(_drive())

    assert actions.started == {"slow": 1}


def test_one_that_may_run_beside_itself_still_keeps_to_its_hourly_cap(tmp_path):
    """`overlap: parallel` with `max_runs_per_hour: 2`: its fires under way have no row yet, and
    the cap counts them, so a third does not start while two run."""
    parallel = _clock("par", overlap="parallel")
    parallel.gates = {"max_runs_per_hour": 2}
    store = _store(tmp_path, parallel)
    actions = _Actions("par")

    async def _drive() -> None:
        fires: set[asyncio.Task[Any]] = set()
        tick = dict(runner=actions, sessions=_manager(), base_dir=tmp_path, fires=fires)
        for step in range(4):
            await _tick(store, NOW + 61 * step, tick)
        await _stop(fires)

    asyncio.run(_drive())

    assert actions.started == {"par": 2}


def test_one_that_may_run_beside_itself_runs_beside_itself(tmp_path):
    """The control for the cap above: with no cap, each slot starts its own run."""
    store = _store(tmp_path, _clock("par", overlap="parallel"))
    actions = _Actions("par")

    async def _drive() -> None:
        fires: set[asyncio.Task[Any]] = set()
        tick = dict(runner=actions, sessions=_manager(), base_dir=tmp_path, fires=fires)
        for step in range(3):
            await _tick(store, NOW + 61 * step, tick)
        await _stop(fires)

    asyncio.run(_drive())

    assert actions.started == {"par": 3}


def test_a_fire_that_has_ended_is_no_longer_counted_as_under_way(tmp_path):
    """Once a fire ends its count goes: from then its row, which the real dispatch writes, is
    what the cap counts. The runner here writes no row, so with a cap of 2 all three slots fire
    only if each ended fire's count went; a count that stayed would hold the third."""
    capped = _clock("quick", overlap="parallel")
    capped.gates = {"max_runs_per_hour": 2}
    store = _store(tmp_path, capped)
    actions = _Actions()

    async def _drive() -> None:
        fires: set[asyncio.Task[Any]] = set()
        tick = dict(runner=actions, sessions=_manager(), base_dir=tmp_path, fires=fires)
        for step in range(3):
            await _tick(store, NOW + 61 * step, tick)
        await _stop(fires)

    asyncio.run(_drive())

    assert actions.started == {"quick": 3}


def test_the_running_loop_starts_the_next_fire_while_a_long_one_runs(tmp_path):
    """The loop itself, not only a tick handed a set: two automations due in one tick, the long
    one first, and the second starts while the first runs."""
    slow, ping = _clock("slow"), _clock("ping")
    slow.next_fire_at = SVC.to_iso(time.time() - 2)
    ping.next_fire_at = SVC.to_iso(time.time() - 1)
    store = _store(tmp_path, slow, ping)
    actions = _Actions("slow")
    seen: dict[str, Any] = {}

    async def _drive() -> None:
        loop = asyncio.ensure_future(
            L.run_forever(store, runner=actions, sessions=_manager(), base_dir=tmp_path)
        )
        await asyncio.wait_for(actions.first_slow.wait(), timeout=10)
        for _ in range(100):
            if actions.started.get("ping"):
                break
            await asyncio.sleep(0.02)
        seen["started"] = dict(actions.started)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)

    asyncio.run(_drive())

    assert seen["started"] == {"slow": 1, "ping": 1}, seen


def test_a_stop_of_the_loop_stops_each_fire_under_way_first(tmp_path):
    """The loop's own stop waits while each fire it started is stopped, so each records itself as
    a run a stop cut off before the gateway goes on stopping."""
    slow = _clock("slow")
    slow.next_fire_at = SVC.to_iso(time.time() - 1)
    store = _store(tmp_path, slow)
    actions = _Actions("slow")
    seen: dict[str, Any] = {}

    async def _drive() -> None:
        loop = asyncio.ensure_future(
            L.run_forever(store, runner=actions, sessions=_manager(), base_dir=tmp_path)
        )
        await asyncio.wait_for(actions.first_slow.wait(), timeout=10)
        loop.cancel()
        try:
            await loop
        except asyncio.CancelledError:
            seen["stopped_when_the_loop_ended"] = dict(actions.stopped)

    asyncio.run(_drive())

    assert seen["stopped_when_the_loop_ended"] == {"slow": 1}


def test_a_tick_with_no_set_of_fires_runs_each_to_its_end(tmp_path):
    """One iteration driven on its own (a test, the doctor) still reads every fire's outcome."""
    store = _store(tmp_path, _clock("a"), _clock("b"))
    actions = _Actions()

    asyncio.run(L.tick_once(store, runner=actions, sessions=_manager(), base_dir=tmp_path, now=NOW))

    assert actions.started == {"a": 1, "b": 1}
    assert not claims.is_running("a", now=NOW, base_dir=tmp_path)
