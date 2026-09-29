"""A workflow step the owner allowed is not asked again when a restart resumes it.

A `stage` step asks its owner to allow its start. The approval registry holds her answer in memory
only, so when the gateway restarted while the step's agent worked, the resumed run started the step
again, asked again, with a new approval on every surface, and waited for her again: measured on
every restart of a long run, one ask per restart for a step she had already allowed.

Her Allow is kept with the step's instance now, in the state a restart reads back, against what
the start asked. The resumed attempt starts on it when it asks the same thing. Every other start
still asks: another request, a new attempt, an answer older than the step's time limit, a rewind,
and any start under an operator ceiling that says a person answers every approval.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_subagent import _mock_ctx_builder, _mock_sessions

from personalclaw.approval_grants import YOU, ToolDecision
from personalclaw.guardrails import ceiling as C
from personalclaw.sel import sel
from personalclaw.subagent import SubagentManager
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

STAGE = "root.children[0]"
PROMPT = "Judge the draft in two sentences."


def _spec(prompt: str = PROMPT) -> dict[str, Any]:
    return {
        "name": "judge-once",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "judge", "config": {"prompt": prompt}}],
        },
    }


class _Owner:
    """The person the gateway asks: every ask she is shown, and her answer to it."""

    def __init__(self, *, allows: bool = True) -> None:
        self.asked: list[str] = []
        self._allows = allows

    async def __call__(self, request_id: str, description: str, parent: str = "") -> ToolDecision:
        self.asked.append(request_id)
        if self._allows:
            return ToolDecision(True, "approved", YOU)
        return ToolDecision(False, "rejected", YOU)


class _Work:
    """What a started agent does: the first process's never finishes (the restart cuts it off),
    and every later one answers at once."""

    def __init__(self) -> None:
        self.hanging: set[int] = set()

    async def __call__(self, manager: SubagentManager, info: Any) -> None:
        if id(manager) in self.hanging:
            await asyncio.Event().wait()
        info.result = "The draft holds up."
        info.done = True


def _manager(owner: _Owner) -> SubagentManager:
    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    return SubagentManager(
        sessions=sessions,
        ctx_builder=_mock_ctx_builder(),
        on_spawn_approval=owner,
        is_yolo=lambda: False,
    )


async def _until(predicate: Any, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before the deadline")
        await asyncio.sleep(0.02)


def _stage(run_id: str) -> Any:
    return store.read_state(run_id)[STAGE]


def _resumed_starts() -> list[dict[str, Any]]:
    return [
        r
        for r in sel().recent(500)
        if r.get("outcome") == "auto_approved_spawn"
        and "approved_before_resume" in json.dumps(r.get("metadata") or {})
    ]


async def _restart(run: WorkflowRun, owner: _Owner, work: _Work, *, spec=None) -> RunStatus:
    """The first process starts the step, she allows it, and the gateway stops while its agent
    works. A second process — a fresh manager that knows no agent, a controller built from the
    store — resumes the run and drives it to its end."""
    first = _manager(owner)
    work.hanging.add(id(first))
    a = RunController(run, _spec(), services=EngineServices(subagents=first))
    await a.start()
    await _until(lambda: owner.asked and _stage(run.id).state == InstanceState.RUNNING)
    await asyncio.sleep(0.3)  # the controller's next steps: it reads her answer off the agent
    a._task.cancel()  # type: ignore[union-attr]
    await asyncio.sleep(0.05)
    if spec is not None:
        store.write_spec(run.id, spec)
    second = _manager(owner)
    b = RunController(
        store.get(run.id), store.read_spec(run.id), services=EngineServices(subagents=second)
    )
    await b.start()
    return await asyncio.wait_for(b.run_to_completion(), timeout=20)


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    runner = _Work()
    with patch.object(SubagentManager, "_run", new=lambda self, info: runner(self, info)):
        yield runner


def _new_run() -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="judge-once"))
    store.write_spec(run.id, _spec())
    return run


@pytest.mark.asyncio
async def test_a_restart_resumes_a_step_she_allowed_without_asking_her_again(work):
    owner = _Owner()
    run = _new_run()
    status = await _restart(run, owner, work)
    assert status is RunStatus.COMPLETE, store.get(run.id).error_message
    assert len(owner.asked) == 1, f"she was asked {len(owner.asked)} times: {owner.asked}"
    assert _resumed_starts(), "the resumed start is audited as started on her earlier Allow"
    assert _stage(run.id).approved_request == "", "the settled attempt keeps no answer"


@pytest.mark.asyncio
async def test_only_the_request_she_allowed_resumes_on_her_answer(work):
    """Two runs, one restart each. In the second the step's prompt changed between the two
    processes: it asks something she was never asked, so she is asked."""
    same = _Owner()
    run = _new_run()
    assert await _restart(run, same, work) is RunStatus.COMPLETE, store.get(run.id).error_message
    assert len(same.asked) == 1, same.asked

    changed = _Owner()
    run = _new_run()
    status = await _restart(run, changed, work, spec=_spec("Judge the draft and rewrite it."))
    assert status is RunStatus.COMPLETE, store.get(run.id).error_message
    assert len(changed.asked) == 2, changed.asked


# ── the grant, at the spawn gate ─────────────────────────────────────────────────────────────


async def _spawned(manager: SubagentManager, **kw: Any) -> Any:
    info = manager.spawn(PROMPT, parent_session_key="workflow:r1:judge", **kw)
    assert info is not None and not info.error, info
    await asyncio.wait_for(manager._tasks[info.id], timeout=10)
    return info


@pytest.mark.asyncio
async def test_her_answer_stands_only_for_its_own_time_limit(work):
    owner = _Owner()
    limit = _manager(owner)._default_timeout
    await _spawned(_manager(owner), request_key="k1", approved_at=time.time() - 60)
    assert owner.asked == [], "a recent answer for this start stands"
    await _spawned(_manager(owner), request_key="k1", approved_at=time.time() - limit - 60)
    assert len(owner.asked) == 1, "an answer older than the step's time limit asks again"
    await _spawned(_manager(owner), request_key="k1", approved_at=time.time() + 3600)
    assert len(owner.asked) == 2, "a time in the future is no answer anyone gave"
    await _spawned(_manager(owner), request_key="", approved_at=time.time())
    assert len(owner.asked) == 3, "an answer for no request stands for nothing"


@pytest.mark.asyncio
async def test_her_allow_is_kept_and_a_standing_grant_leaves_nothing_to_keep(work):
    owner = _Owner()
    asked = await _spawned(_manager(owner), request_key="k2")
    assert asked.approved_at > 0, "her Allow is kept on the start it answered"
    granted = await _spawned(_manager(owner), request_key="k3", approval_mode="auto")
    assert granted.approved_at == 0, "a grant is not an answer of hers"
    assert owner.asked == [f"spawn:{asked.id}"]


@pytest.mark.asyncio
async def test_her_earlier_answer_does_not_stand_under_an_ask_ceiling(work, tmp_path, monkeypatch):
    path = tmp_path / "operator" / "ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "scopes": {"approval": {"value": "ask"}}}))
    monkeypatch.setenv(C.CEILING_PATH_ENV, str(path))
    C.reset_ceiling()
    try:
        owner = _Owner()
        await _spawned(_manager(owner), request_key="k4", approved_at=time.time())
        assert len(owner.asked) == 1, "under an ask ceiling a person answers every start"
    finally:
        C.reset_ceiling()


# ── what ends her answer ─────────────────────────────────────────────────────────────────────


def test_a_rewind_drops_her_answer(work):
    """A rewound step re-runs to be reconsidered, so it asks her again, as a remembered "always
    allow" does not survive a rewind either."""
    from personalclaw.workflows import mid_flight, mutations

    run = _new_run()
    ctl = RunController(run, _spec(), services=EngineServices(subagents=_manager(_Owner())))
    inst = ctl._instance(STAGE)
    inst.state = InstanceState.RUNNING
    inst.approved_request, inst.approved_at = "the start she allowed", time.time()
    mid_flight._apply_reentry(
        ctl,
        mutations.Op(kind=mutations.OpKind.REWIND, node_id="judge", force=True),
        mutations.CascadePreview(rerun=["judge"]),
    )
    assert (inst.approved_request, inst.approved_at) == ("", 0.0)
    assert inst.state is InstanceState.PENDING
