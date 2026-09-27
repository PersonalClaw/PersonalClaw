"""A run that ends closes every question it was still asking (#3620's ended-owner rule).

A cancel stopped the work in flight (`_cancel_inflight`) and `_finish` closed the run's Inbox rows
and approvals — but nothing touched a node that was WAITING. Cancelling a run parked at a gate left
the gate reading `waiting` on the run page, its resume token pending on disk, and Introspect's
"what needs my approval" still listing it. Measured on a dev gateway before this change: the
cancelled run's `approve` node read `waiting`.

Two more cancel paths never reached the terminal writer at all, and both are pinned here:

* a cancel written without waking the controller — `run-workflow`'s `on_overlap:
  cancel_then_start` does exactly that to the prior run. The watchdog saw a registered controller
  and left the intent for "its own tick loop", which a parked run does not have: the prior run
  waited at its gate forever with a cancel it never read;
* a cancel of a run with no live controller (the gateway restarted) was written by the watchdog
  directly, closing none of the above.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import run_cockpit, service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio

GATE = "root.children[1]"
SPEC = {
    "name": "gate-cancel",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "draft", "config": {"expr": "a draft"}},
            {
                "kind": "gate",
                "id": "approve",
                "config": {"kind": "approval", "prompt": "Publish it?", "timeout_secs": 0},
            },
            {"kind": "transform", "id": "publish", "config": {"expr": "published"}},
        ],
    },
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


def _fake_state():
    """A dashboard state whose Inbox is a REAL `InboxStore`, read the way the live service is."""
    from personalclaw.inbox import InboxStore

    class Svc:
        def __init__(self) -> None:
            self.inbox = InboxStore()

    class State:
        def __init__(self) -> None:
            self._inbox_svc = Svc()

        def notify(self, *a: Any, **k: Any) -> None:
            pass

    return State()


def _open_rows(state: Any, run_id: str) -> list[Any]:
    from personalclaw.inbox import OPEN_STATUSES

    return [
        i
        for i in state._inbox_svc.inbox.items.values()
        if i.status in OPEN_STATUSES and (i.refs or {}).get("workflow") == run_id
    ]


async def _until(predicate, *, what: str, timeout: float = 6.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


def _status(run_id: str) -> RunStatus:
    run = store.get(run_id)
    assert run is not None
    return run.status


async def _parked_under(sup: WorkflowWatchdog) -> RunController:
    run = store.create(WorkflowRun(id="", workflow_name=SPEC["name"]))
    store.write_spec(run.id, copy.deepcopy(SPEC))
    c = await sup.launch(run, copy.deepcopy(SPEC))
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.NEEDS_INPUT
    assert HI.list_continuations(run.id), "no question was asked, so nothing is under test"
    return c


def _assert_every_question_closed(state: Any, run_id: str) -> None:
    gate = store.read_state(run_id)[GATE]
    assert (
        gate.state == InstanceState.CANCELLED
    ), f"the run ended and its gate still reads {gate.state.value!r}"
    assert HI.list_continuations(run_id) == [], "a resume token outlived the run it resumes"
    assert _open_rows(state, run_id) == [], "an Inbox row outlived the run that asked it"
    rows = [e for e in J.ledger(run_id) if e.get("kind") == J.STEP_CANCELLED]
    assert [e.get("instance_path") for e in rows] == [GATE]
    # The row that ends a wait records that the WAIT spent nothing, the same measurement a gate
    # that times out writes.
    assert rows[0].get("tokens") == 0


async def test_cancelling_a_run_parked_at_its_gate_closes_the_gate() -> None:
    state = _fake_state()
    sup = WorkflowWatchdog(state=state, services=EngineServices())
    c = await _parked_under(sup)
    run_id = c.run.id
    assert len(_open_rows(state, run_id)) == 1

    body = service.cancel_run(run_id, supervisor=sup)
    assert body["ok"], body
    await _until(lambda: _status(run_id) == RunStatus.CANCELLED, what="the cancel to land")

    _assert_every_question_closed(state, run_id)
    # The run page reads the node list from `status`; the gate is cancelled there too.
    nodes = {n["node_id"]: n["state"] for n in service.status(run_id)["nodes"]}
    assert nodes["approve"] == "cancelled", nodes
    # …and Introspect no longer claims the cancelled run needs an approval.
    answers = run_cockpit.introspect(run_id)["answers"]
    assert answers["approval"] == [], answers["approval"]
    assert answers["blocked"] == [], answers["blocked"]


async def test_a_run_ending_any_other_way_closes_its_waits_too() -> None:
    """The rule is about the ENDING, not the verb: whatever writes a terminal status answers the
    run's outstanding questions, the way #3620 made every ending cancel its approvals."""
    state = _fake_state()
    run = store.create(WorkflowRun(id="", workflow_name=SPEC["name"]))
    store.write_spec(run.id, copy.deepcopy(SPEC))
    c = RunController(run, copy.deepcopy(SPEC), services=EngineServices(attention_state=state))
    assert await c.run_to_completion(timeout=20) == RunStatus.NEEDS_INPUT

    async with c._lock:
        await c._finish(RunStatus.FAILED, error="engine error: the disk went away")

    _assert_every_question_closed(state, run.id)


async def test_a_cancel_nobody_woke_the_parked_controller_for_still_lands() -> None:
    """`run-workflow`'s `on_overlap: cancel_then_start` writes the prior run's cancel intent and
    moves on. A parked controller has no tick loop to read it, so the supervisor has to wake it."""
    state = _fake_state()
    sup = WorkflowWatchdog(state=state, services=EngineServices())
    c = await _parked_under(sup)
    run_id = c.run.id

    store.request_cancel(run_id)  # exactly what cancel_then_start does, and nothing more
    await sup._poll_once()

    await _until(
        lambda: _status(run_id) == RunStatus.CANCELLED,
        what="the prior run's cancel to land (it waited at its gate with the intent unread)",
    )
    _assert_every_question_closed(state, run_id)


async def test_a_cancel_with_no_live_controller_closes_the_gate_too() -> None:
    """After a restart the supervisor holds no controller for a parked run. Its cancel used to be
    written straight to the row, closing nothing: the gate stayed `waiting` with a live token
    and an open Inbox row on a run that was over."""
    state = _fake_state()
    first = WorkflowWatchdog(state=state, services=EngineServices())
    c = await _parked_under(first)
    run_id = c.run.id

    restarted = WorkflowWatchdog(state=state, services=EngineServices())
    store.request_cancel(run_id)
    await restarted._poll_once()

    await _until(lambda: _status(run_id) == RunStatus.CANCELLED, what="the cancel to land")
    await _until(
        lambda: store.read_state(run_id)[GATE].state == InstanceState.CANCELLED,
        what="the restarted gateway to close the gate",
    )
    _assert_every_question_closed(state, run_id)
    assert not store.cancel_requested(run_id), "the sticky cancel intent was left behind"
