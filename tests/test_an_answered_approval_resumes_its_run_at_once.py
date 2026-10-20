"""An answered approval moves its run on at once, not on the controller's next tick.

A gate with an unattended deadline keeps its run's tick loop alive while it waits: the loop
keeps ticking so the deadline can fire. Before, an answer to such a gate was only noticed on
that loop's next tick, up to `TICK_WAKE_SECS` later. Now the answer wakes the loop. The tick
stays the cadence for deadlines and the backstop if a wake is ever missed.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


_SPEC = {
    "name": "gated",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "prep", "config": {"expr": {"v": 1}}},
            {
                "kind": "gate",
                "id": "approve",
                # The deadline is what keeps the loop alive while the gate waits.
                "config": {"kind": "approval", "prompt": "Ship it?", "timeout_secs": 600},
            },
            {"kind": "transform", "id": "after", "config": {"expr": "shipped"}},
        ],
    },
}


async def _until(predicate, *, within: float) -> None:
    deadline = time.monotonic() + within
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"not true within {within}s")
        await asyncio.sleep(0.005)


def _state(c: RunController, path: str) -> InstanceState | None:
    # A step has no instance until the frontier first reaches it.
    inst = c.instances.get(path)
    return inst.state if inst else None


async def _parked_on_a_live_loop(started: dict[str, float] | None = None) -> RunController:
    def publish(event: str, body: dict) -> None:
        # When each step started, read from the run's own event stream: the run's end does
        # synchronous work that a poll from the test would be stuck behind.
        if started is not None and event == "workflow_node_started":
            started.setdefault(body["node_id"], time.monotonic())

    run = store.create(WorkflowRun(id="", workflow_name="gated"))
    store.write_spec(run.id, _SPEC)
    c = RunController(run, _SPEC, services=EngineServices(publish=publish))
    await c.start()
    await _until(
        lambda: c.run.status == RunStatus.NEEDS_INPUT and HI.list_continuations(run.id),
        within=10,
    )
    # The point of the test: the loop is still running, waiting out its tick.
    assert c._task is not None and not c._task.done()
    return c


async def test_an_answer_to_a_gate_on_a_live_loop_starts_the_next_step_at_once() -> None:
    assert controller_mod.TICK_WAKE_SECS == 5.0, "the shipped tick, not a shortened one"
    started: dict[str, float] = {}
    c = await _parked_on_a_live_loop(started)
    token = HI.list_continuations(c.run.id)[0].token
    assert c.resume(token, True, by=YOU)["ok"]
    answered = time.monotonic()
    await _until(lambda: "after" in started, within=10)
    took = started["after"] - answered
    assert took < 0.1, f"the next step started {took:.2f}s after the answer"
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE
    assert _state(c, "root.children[2]") == InstanceState.DONE


#: A run whose loop is alive on its tick: a long wait keeps it ticking toward its deadline.
_WAITS = {
    "name": "waits",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "wait", "id": "hold", "config": {"duration_secs": 600}},
            {"kind": "transform", "id": "after", "config": {"expr": "went on"}},
        ],
    },
}


class _Supervisor:
    def __init__(self, c: RunController) -> None:
        self._c = c

    def controller(self, run_id: str) -> RunController | None:
        return self._c if run_id == self._c.run.id else None


async def _waiting_on_a_live_loop() -> RunController:
    run = store.create(WorkflowRun(id="", workflow_name="waits"))
    store.write_spec(run.id, _WAITS)
    c = RunController(run, _WAITS, services=EngineServices())
    await c.start()
    await _until(lambda: _state(c, "root.children[0]") == InstanceState.WAITING, within=10)
    # Let the loop settle into its tick-long wait, so the action below lands mid-wait.
    await asyncio.sleep(0.05)
    assert c._task is not None and not c._task.done()
    return c


async def test_a_stop_ends_a_live_run_at_once() -> None:
    from personalclaw.workflows import service

    c = await _waiting_on_a_live_loop()
    asked = time.monotonic()
    assert service.cancel_run(c.run.id, supervisor=_Supervisor(c))["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    took = time.monotonic() - asked
    assert c.run.status == RunStatus.CANCELLED
    assert took < 0.5, f"the run ended {took:.2f}s after its Stop"


async def test_a_pause_parks_a_live_run_at_once() -> None:
    from personalclaw.workflows import service

    c = await _waiting_on_a_live_loop()
    asked = time.monotonic()
    assert service.pause_run(c.run.id, supervisor=_Supervisor(c))["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    took = time.monotonic() - asked
    assert c.run.status == RunStatus.PAUSED
    assert took < 0.5, f"the run paused {took:.2f}s after it was asked"


async def test_an_edit_to_a_live_run_is_applied_at_once() -> None:
    c = await _waiting_on_a_live_loop()
    asked = time.monotonic()
    body = c.submit_mutation([{"op": "skip", "node_id": "hold"}], confirm=True)
    assert body.get("queued"), body
    await _until(lambda: _state(c, "root.children[0]") == InstanceState.SKIPPED, within=10)
    took = time.monotonic() - asked
    assert took < 0.5, f"the edit was applied {took:.2f}s after it was queued"
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    await c.stop()


async def test_a_blocking_start_returns_the_ask_as_soon_as_its_run_parks() -> None:
    parked: list[float] = []

    def publish(event: str, body: dict) -> None:
        if event == "workflow_run_update" and body.get("status") == RunStatus.NEEDS_INPUT.value:
            parked.append(time.monotonic())

    run = store.create(WorkflowRun(id="", workflow_name="gated"))
    store.write_spec(run.id, _SPEC)
    c = RunController(run, _SPEC, services=EngineServices(publish=publish))
    try:
        assert await c.wait_for_terminal(timeout=20) == RunStatus.NEEDS_INPUT
        returned = time.monotonic()
        assert parked, "the run never surfaced its ask"
        took = returned - parked[0]
        # The loop is alive (the gate has a deadline), so only the park itself can end the wait.
        assert not c._terminal.is_set()
        assert took < 0.3, f"the blocking start returned {took:.2f}s after its run parked"
    finally:
        await c.stop()


class _NeverSet(asyncio.Event):
    def set(self) -> None:  # a wake that is lost
        return None


async def test_a_missed_wake_still_resumes_the_run_on_the_next_tick(monkeypatch) -> None:
    # A short tick so the backstop is cheap to watch; the event never fires.
    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.3)
    c = await _parked_on_a_live_loop()
    c._woken = _NeverSet()
    token = HI.list_continuations(c.run.id)[0].token
    assert c.resume(token, True, by=YOU)["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=5)
    assert c.run.status == RunStatus.COMPLETE
    assert c.instances["root.children[2]"].state == InstanceState.DONE
