"""A mid-flight edit reaches a run parked at a gate — and nothing waits in a queue no loop reads.

`submit_mutation` QUEUES a batch; the tick loop applies it at its drain point (WF2-R10). A run
that parks on a gate has no tick loop: `_step` finishes it as `needs_input` and the loop exits,
because nothing will wake it but an answer. So a rewind confirmed while the run waited at its gate
answered `queued: true` and then sat in `_pending_mutations` for as long as the gate did. Measured
on a dev gateway before this change: 12 s after the confirmed rewind the node still read `done`
with its first attempt, and the run page showed nothing having happened.

What these pin, each against the real lifecycle (no hand-called `drain_mutations`, which is what
kept the defect invisible — every earlier test drained the queue itself):

* a confirmed rewind on a parked run applies at the next tick, re-runs its closure, and the run
  parks again on the SAME question (one token, one Inbox row);
* the same through the HTTP-facing service verb, with the controller the supervisor holds;
* a rewind whose closure includes the waiting gate withdraws that gate's question — its Inbox row
  closes with its token, rather than staying open carrying a token the rewind dropped;
* a finished run refuses an edit with a sentence instead of queueing one nothing will ever apply.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio

GATE = "root.children[1]"


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


def _spec(gate_prompt: str = "Publish the draft?") -> dict[str, Any]:
    """draft → approve (waits indefinitely) → publish, which binds the draft."""
    return {
        "name": "gate-rewind",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "draft", "config": {"expr": {"n": 1}}},
                {
                    "kind": "gate",
                    "id": "approve",
                    "config": {"kind": "approval", "prompt": gate_prompt, "timeout_secs": 0},
                },
                {
                    "kind": "transform",
                    "id": "publish",
                    "config": {"expr": "{{nodes.draft.output.n}}"},
                },
            ],
        },
    }


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


def _completions(run_id: str, node_id: str) -> int:
    return sum(
        1
        for e in J.ledger(run_id)
        if e.get("kind") == J.STEP_COMPLETED and e.get("node_id") == node_id
    )


async def _until(predicate, *, what: str, timeout: float = 6.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _parked(spec: dict[str, Any], *, state: Any = None) -> RunController:
    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices(attention_state=state))
    assert await c.run_to_completion(timeout=20) == RunStatus.NEEDS_INPUT
    assert c.instances[GATE].state == InstanceState.WAITING
    return c


def _only_token(run_id: str) -> str:
    pending = HI.list_continuations(run_id)
    assert len(pending) == 1, f"expected one open question, found {len(pending)}"
    return pending[0].token


async def test_a_rewind_confirmed_while_the_run_waits_at_a_gate_applies_at_once() -> None:
    state = _fake_state()
    c = await _parked(_spec(), state=state)
    token = _only_token(c.run.id)
    assert _completions(c.run.id, "draft") == 1

    body = c.submit_mutation([{"op": "rewind", "node_id": "draft"}], confirm=True)
    assert body["ok"] and body.get("queued"), body

    await _until(
        lambda: _completions(c.run.id, "draft") == 2,
        what="the rewound step to run again (the rewind stayed queued with no loop to apply it)",
    )
    await _until(
        lambda: c.run.status == RunStatus.NEEDS_INPUT and c._task is not None and c._task.done(),
        what="the run to park at its gate again",
    )
    assert c._pending_mutations == []
    assert [e["kind"] for e in J.ledger(c.run.id)].count(J.USER_EDITED_MID_FLIGHT) == 1
    # The gate did not read the rewound node, so it still waits on the SAME question: one token,
    # one Inbox row. A second ask for an unchanged question would be a duplicate notification.
    assert c.instances[GATE].state == InstanceState.WAITING
    assert _only_token(c.run.id) == token
    assert len(_open_rows(state, c.run.id)) == 1

    # And the answer still lands on the rewound run.
    assert c.resume(token, True, by=YOU)["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE
    assert _completions(c.run.id, "publish") == 1


async def test_the_rewind_route_applies_it_to_a_run_the_supervisor_holds_parked() -> None:
    """The path the run page takes: `POST …/rewind` → `service.rewind_run` → the controller the
    supervisor registered. The controller stays registered while it is parked (needs_input is not
    terminal), so the request reaches it; before, it reached it and stopped there."""
    spec = _spec()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    sup = WorkflowWatchdog(state=None, services=EngineServices())
    c = await sup.launch(run, copy.deepcopy(spec))
    assert await c.wait_for_terminal(timeout=10) == RunStatus.NEEDS_INPUT
    await asyncio.wait_for(c._terminal.wait(), timeout=10)

    body = service.rewind_run(run.id, "draft", supervisor=sup, confirm_cascade=True)
    assert body["ok"] and body.get("queued"), body

    await _until(lambda: _completions(run.id, "draft") == 2, what="the rewound step to run again")
    await _until(
        lambda: (store.get(run.id) or run).status == RunStatus.NEEDS_INPUT and c._task.done(),
        what="the run to park at its gate again",
    )


async def test_a_rewind_that_resets_the_waiting_gate_withdraws_its_old_question() -> None:
    """The gate's prompt BINDS the rewound node, so the rewind's closure includes the gate: the
    question is re-asked about the new draft. The dropped token's Inbox row must close with it —
    left open, it names a question the run no longer asks, and the deduped re-ask reuses that
    stale row instead of raising the real one.

    `wake()` is called here on purpose: it isolates THIS defect from the one above, so the test
    fails on the Inbox row even where a queued rewind would otherwise never be applied."""
    state = _fake_state()
    c = await _parked(_spec(gate_prompt="Publish draft {{nodes.draft.output.n}}?"), state=state)
    old = _only_token(c.run.id)
    rows = _open_rows(state, c.run.id)
    assert [r.refs["resume_token"] for r in rows] == [old]

    body = c.submit_mutation([{"op": "rewind", "node_id": "draft"}], confirm=True)
    assert body["ok"] and "approve" in body["preview"]["rerun"], body
    c.wake()

    await _until(lambda: _completions(c.run.id, "draft") == 2, what="the rewound step to re-run")
    await _until(
        lambda: c.run.status == RunStatus.NEEDS_INPUT and c._task.done(),
        what="the gate to ask again",
    )
    new = _only_token(c.run.id)
    assert new != old, "the re-asked gate kept the token the rewind dropped"
    open_rows = _open_rows(state, c.run.id)
    assert [r.refs["resume_token"] for r in open_rows] == [new], (
        "every open Inbox row must carry a token the run still honours; the rewind's dropped "
        f"token is still being offered: {[r.refs.get('resume_token') for r in open_rows]}"
    )


async def test_a_finished_run_refuses_an_edit_instead_of_queueing_it_forever() -> None:
    """A finished run is one attempt and is never re-entered — a retry is a fork. Its controller
    stays registered until the supervisor's next poll, and in that window an edit answered
    `queued: true` for a batch no loop would ever drain."""
    spec = {
        "name": "done-run",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "transform", "id": "draft", "config": {"expr": 1}}],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="done-run"))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices())
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE

    body = c.submit_mutation([{"op": "rewind", "node_id": "draft"}], confirm=True)
    assert body["ok"] is False, body
    assert body.get("queued") is not True
    assert body["code"] == "WF_RUN_ALREADY_TERMINAL"
    assert "fork" in body["message"].lower(), body["message"]
    assert [i["code"] for i in body["issues"]] == ["WF_RUN_ALREADY_TERMINAL"]
    assert c._pending_mutations == []
