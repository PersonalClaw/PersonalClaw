"""A gate inside a loop asks a NEW question each cycle, so each cycle's answer is its own.

A confirmation id names one ask: the pending row, the answer and the escalation's graded outcome
pair up by it. It was keyed on the gate's NODE id, and a loop repeats its body's node ids — every
cycle's ``park`` is the node ``park`` — while the ask's ordinal was counted per instance path,
which starts again at zero in each cycle. Measured on ``main``: two cycles of one gate minted the
same id, so the second cycle's question read as already answered to anything pairing by id, and
its outcome could not be told from the first one's.

Now the id is keyed on the step's instance path, which names the cycle (``…body@1…``).

The run is stopped before its loop closes, as the gateway stops each of its runs
(``WorkflowWatchdog.stop``). The second answer is recorded before cycle 2's step runs, so that step
can still be starting its ``true`` when the test is done with it, and a loop closed on a live run
cancels that start together with asyncio's own task connecting the child's pipes: on Python 3.12
the start then never ends (``cancellation.cancel_and_wait``). Breadth's macOS 3.12 leg waited on the
step's 900 s ceiling that way (run 40266675190). The last test holds that start where the leg
caught it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows.journal import CONFIRMATION_PENDING, CONFIRMATION_RESOLVED, ledger

RUN = "r-loop"

SPEC = {
    "name": "t",
    "root": {
        "kind": "loop",
        "id": "watch",
        "config": {"mode": "counted", "n": 2},
        "body": {
            "kind": "sequence",
            "id": "cycle",
            "children": [
                {"kind": "gate", "id": "park", "config": {"kind": "approval", "prompt": "go?"}},
                {
                    "kind": "action",
                    "id": "after",
                    "config": {"provider": "bash", "with": {"command": "true"}},
                },
            ],
        },
    },
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    from personalclaw.action_providers import registry as apreg

    apreg._ensure_default_providers_registered()


def _rows(kind: str) -> list[dict]:
    return [r for r in ledger(RUN) if r["kind"] == kind]


async def _until_waiting(run, count: int) -> None:
    """Until the run has asked ``count`` questions and is waiting on the last one."""
    for _ in range(200):
        await asyncio.sleep(0.05)
        if len(_rows(CONFIRMATION_PENDING)) >= count and run.status.value == "needs_input":
            return
    raise AssertionError(f"the run never asked question {count}: {run.status.value}")


def _open_continuation(path_part: str):
    from personalclaw.workflows.human_input import list_continuations

    (cont,) = [c for c in list_continuations(RUN) if path_part in c.instance_path]
    return cont


def _two_cycles(after_answers: Callable[[], Awaitable[None]] | None = None) -> None:
    """Run the loop through both of its asks, answer each, and stop the run; *after_answers* runs
    once the second answer is given."""

    async def go() -> None:
        from personalclaw.workflows import store as wstore
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.models import WorkflowRun

        run = WorkflowRun(id=RUN, workflow_name="t")
        wstore.create(run)
        controller = RunController(run, SPEC, services=EngineServices())
        await controller.start()
        try:
            await _until_waiting(run, 1)
            first = _open_continuation("body@0")
            controller.resume(first.token, True, by=YOU)

            await _until_waiting(run, 2)
            second = _open_continuation("body@1")
            controller.resume(second.token, True, by=YOU)
            if after_answers is not None:
                await after_answers()
            for _ in range(200):
                await asyncio.sleep(0.05)
                if len(_rows(CONFIRMATION_RESOLVED)) >= 2:
                    break
        finally:
            await controller.stop()

    asyncio.run(go())


def test_each_cycle_of_a_gate_in_a_loop_asks_its_own_question():
    _two_cycles()

    pending = _rows(CONFIRMATION_PENDING)
    assert [r["node_id"] for r in pending] == [
        "park",
        "park",
    ], "vacuity floor: two asks of one gate"
    paths = [r["instance_path"] for r in pending]
    assert "body@0" in paths[0] and "body@1" in paths[1], paths
    ids = [r["confirmation_id"] for r in pending]
    # 🔴 Red on main: both cycles minted one id.
    assert ids[0] != ids[1], f"two cycles of one gate asked under one id: {ids}"

    # Each answer cites the question it answered, and no other.
    resolved = {r["instance_path"]: r["confirmation_id"] for r in _rows(CONFIRMATION_RESOLVED)}
    assert resolved == dict(zip(paths, ids, strict=True)), resolved


def test_a_gate_outside_a_loop_keeps_one_id_per_ask():
    """The floor: keying on the path changes nothing for a step that runs once — its id is still
    minted once and still cited by its answer."""
    from personalclaw.workflows.gate_answers import stable_confirmation_id

    path = "root.children[1].body@3.children[0]"
    assert stable_confirmation_id(RUN, path, 0) == stable_confirmation_id(RUN, path, 0)
    assert stable_confirmation_id(RUN, path, 0) != stable_confirmation_id(
        RUN, "root.children[1].body@4.children[0]", 0
    )


def test_a_run_stopped_while_its_step_starts_a_command_lets_its_loop_close(monkeypatch):
    """Cycle 2's step held where breadth's leg caught it: asyncio has started its ``true``, has not
    connected the child's pipes and has not heard it end. The leg was there for milliseconds; here
    the pipes stay unconnected until the loop's close cancels their connect, and the end is heard
    only after that, as the leg heard it. Stopped there, the run lets its loop close. Closed on the
    live run, the loop waited on the step's 900 s ceiling on Python 3.12 (3.13 wakes the start when
    it hears the end, so only a 3.12 leg goes red)."""
    from asyncio import base_subprocess

    transport_type = base_subprocess.BaseSubprocessTransport
    connect, ended = transport_type._connect_pipes, transport_type._process_exited
    holding = False
    held: list[object] = []
    unheard: list[tuple[object, int]] = []

    async def connect_when_let(transport, waiter):
        if not holding:
            return await connect(transport, waiter)
        held.append(transport)
        try:
            await asyncio.Event().wait()  # never set: only a cancel ends it
        finally:
            for late in unheard:
                asyncio.get_running_loop().call_soon(ended, *late)

    def ended_when_heard(transport, returncode):
        if transport in held:
            unheard.append((transport, returncode))
        else:
            ended(transport, returncode)

    monkeypatch.setattr(transport_type, "_connect_pipes", connect_when_let)
    monkeypatch.setattr(transport_type, "_process_exited", ended_when_heard)

    async def hold_cycle_2s_start() -> None:
        nonlocal holding
        holding = True
        for _ in range(200):
            if held:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("vacuity floor: cycle 2's step never started its command")

    _two_cycles(after_answers=hold_cycle_2s_start)
    assert len(held) == 1, f"one start held, cycle 2's: {len(held)}"
    for transport in held:  # the child's pipes, which asyncio never connected
        child = transport._proc
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()
