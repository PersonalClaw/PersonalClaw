"""A judge gate that needs your input asks you: a continuation, an Inbox row, a run needing input.

🔴 The defect (Backlog: "A judge gate that needs your input waits out its timeout instead of asking
you"). A judge gate whose verdict is NEEDS_INPUT parks with the same typed ask an approval parks
with (`engine.dispatch_gate`), but `gate_answers.awaits_human` counted only approval and event
gates as waiting on a person. So no continuation was minted, no Inbox row was raised, the run
never showed as needing input, and the gate waited out its timeout before the run ended at it.

Now the gate kinds a person is asked by are one list (`engine_support.ASKING_GATES`), read by both
the park and the wait. Driven through the real controller with a judge model that answers
NEEDS_INPUT, and a dashboard state whose Inbox is a real `InboxStore`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import live_runs
import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

GATE = "root.children[1]"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def _stopped_runs(anyio_backend):
    yield
    await live_runs.stop_started()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


def _spec(kind: str) -> dict[str, Any]:
    """draft → a gate of *kind*, which waits until it is answered → publish."""
    return {
        "name": "judged-draft",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "transform", "id": "draft", "config": {"expr": {"n": 1}}},
                {
                    "kind": "gate",
                    "id": "accept",
                    "config": {"kind": kind, "prompt": "Is the draft right?", "timeout_secs": 0},
                },
                {"kind": "transform", "id": "publish", "config": {"expr": {"done": True}}},
            ],
        },
    }


async def _needs_input(prompt: str, **_kw: Any) -> str:
    """A judge that cannot rule without a person's answer."""
    return json.dumps(
        {
            "verdict": "NEEDS_INPUT",
            "reasoning": "whether the draft's tone is right is the owner's to say",
            "scores": {},
            "cannot_judge": "",
        }
    )


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


async def _parked(kind: str) -> tuple[RunController, Any]:
    spec = _spec(kind)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    state = _fake_state()
    services = EngineServices(attention_state=state, completion=_needs_input)
    ctl = live_runs.started(RunController(run, spec, services=services))
    await ctl.run_to_completion(timeout=5)
    return ctl, state


@pytest.mark.parametrize("kind", ["judge", "approval"], ids=["judge gate", "approval control"])
async def test_a_gate_that_needs_a_person_asks_them(kind):
    """🔴 Before, for the judge: no continuation, no Inbox row, and the run not needing input."""
    ctl, state = await _parked(kind)
    assert ctl.instances[GATE].state == InstanceState.WAITING, ctl.instances[GATE].state
    assert ctl.run.status == RunStatus.NEEDS_INPUT, (ctl.run.status, ctl.run.error_message)
    (pending,) = HI.list_continuations(ctl.run.id)
    assert pending.instance_path == GATE and pending.ask, pending
    (row,) = _open_rows(state, ctl.run.id)
    assert (row.refs or {}).get("resume_token") == pending.token, row.refs


async def test_your_answer_to_a_parked_judge_carries_the_run_on():
    ctl, _state = await _parked("judge")
    (pending,) = HI.list_continuations(ctl.run.id)
    assert ctl.resume(pending.token, True, by=YOU)["ok"]
    await asyncio.wait_for(ctl._terminal.wait(), timeout=10)
    assert ctl.run.status == RunStatus.COMPLETE, (ctl.run.status, ctl.run.error_message)
    assert ctl._outputs["publish"] == {"done": True}
    assert HI.list_continuations(ctl.run.id) == []
