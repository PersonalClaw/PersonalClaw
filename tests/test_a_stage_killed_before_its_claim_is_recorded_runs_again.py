"""A stage killed before its claim was recorded runs again after a restart, and is not read done.

🔴 The defect (Backlog: "A stage killed before its claim is recorded is refused by its own lease
after restart"). ``engine.dispatch_stage`` takes the step's no-double-execution claim, with a
fresh holder, before it spawns the step's helper, and the controller records who holds it only
when it applies the dispatch's result. A gateway that died in between left the claim on disk with
nothing recording it. After the restart the step was re-queued (``run_start.requeue_lost_steps``),
its new attempt had a holder of its own, and the claim refused it as a duplicate for its whole
900 s: "another worker holds the claim on this node — not executing twice", settled DEGRADED,
which is a success, so the run went on as if the step had run. A path needed no crash at all: the
step's ``max_turns`` was read after the claim was taken, so a value that is not a number raised
with the claim held and nothing to release it, and the step's retry met its own lease.

Now a re-queued stage's lost attempt gives its claim back when the run resumes, and a step's
config is read before its claim is taken. A claim another worker holds still turns the step away.

Driven through a real ``RunController`` and the real lease files under a scratch home, with only
the helper manager faked; the kill is the dispatch's result never reaching the controller.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import engine, leases, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeInstance,
    RunStatus,
    WorkflowRun,
    now_stamp,
)

STAGE_PATH = "root.children[0]"


class _Info:
    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _Helpers:
    """The run's step helpers: each spawn is recorded, and finishes at once when *settles*."""

    def __init__(self, *, settles: bool = True) -> None:
        self.spawns: list[dict[str, Any]] = []
        self.infos: dict[str, _Info] = {}
        self.settles = settles

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.spawns) + 1}")
        self.spawns.append(kw)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and self.settles:
            info.done = True
            info.result = "the stage's answer"
        return info


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    return tmp_path


def _spec(config: dict[str, Any] | None = None) -> dict[str, Any]:
    stage = {"kind": "stage", "id": "work", "config": {"prompt": "do the thing", **(config or {})}}
    return {"name": "stage-claim", "root": {"kind": "sequence", "id": "root", "children": [stage]}}


def _killed_mid_dispatch(spec: dict[str, Any]) -> WorkflowRun:
    """A run whose gateway died after the stage took its claim and spawned, before the controller
    recorded the dispatch: the step reads running, with no helper and no claim recorded."""
    run = store.create(WorkflowRun(id="", workflow_name="stage-claim"))
    store.write_spec(run.id, spec)
    lost = asyncio.run(
        engine.dispatch_stage(
            Node.from_dict(spec["root"]["children"][0]),
            BindingContext(),
            subagents=_Helpers(settles=False),
            run_id=run.id,
            instance_path=STAGE_PATH,
        )
    )
    assert lost.state is InstanceState.RUNNING and lost.claim_holder, lost
    held = leases.read_claim(engine.claim_key(run.id, STAGE_PATH))
    assert held is not None and held.holder == lost.claim_holder, "vacuity: the claim is on disk"
    store.write_state(
        run.id,
        {
            STAGE_PATH: NodeInstance(
                path=STAGE_PATH, state=InstanceState.RUNNING, attempt=1, started_at=now_stamp()
            )
        },
    )
    run.started_at = now_stamp()
    run.status = RunStatus.RUNNING
    store.save(run)
    return run


def _restart(run: WorkflowRun, helpers: _Helpers) -> tuple[RunStatus, NodeInstance]:
    run = store.get(run.id)
    controller = RunController(
        run, store.read_spec(run.id), services=EngineServices(subagents=helpers)
    )
    status = asyncio.run(controller.run_to_completion(timeout=10.0))
    return status, controller.instances[STAGE_PATH]


def test_a_stage_killed_before_its_claim_was_recorded_runs_again_after_a_restart():
    """🔴 Before: the re-queued step met its own lost attempt's claim, settled DEGRADED with
    "another worker holds the claim", and the run completed with the step never run."""
    run = _killed_mid_dispatch(_spec())
    target = engine.claim_key(run.id, STAGE_PATH)
    lost = leases.read_claim(target)
    assert lost is not None
    helpers = _Helpers()
    status, inst = _restart(run, helpers)
    assert len(helpers.spawns) == 1, "the step ran again"
    assert inst.state is InstanceState.DONE, (inst.state, inst.degraded_reason)
    assert status is RunStatus.COMPLETE
    # A settled success keeps its own claim (`stage_settlement`); the lost attempt's is gone.
    held = leases.read_claim(target)
    assert held is not None and held.holder == inst.claim_holder != lost.holder, (held, lost)


def test_a_claim_another_worker_holds_still_turns_the_step_away():
    """The control the claim exists for: a holder that is not this run's attempt at this step."""
    run = _killed_mid_dispatch(_spec())
    target = engine.claim_key(run.id, STAGE_PATH)
    lost = leases.read_claim(target)
    assert lost is not None
    leases.release_claim(target, lost.holder)
    other = "workflow:another-run:work#0123456789ab"
    granted, _ = leases.acquire_claim(target, other)
    assert granted is not None
    helpers = _Helpers()
    _status, inst = _restart(run, helpers)
    assert helpers.spawns == [], "a claim held by another worker is not taken from it"
    assert inst.state is InstanceState.DEGRADED and "another worker holds the claim" in (
        inst.degraded_reason
    ), (inst.state, inst.degraded_reason)
    held = leases.read_claim(target)
    assert held is not None and held.holder == other, "and it still holds it"


def test_a_stage_whose_config_cannot_be_read_leaves_no_claim_behind():
    """🔴 Before: `max_turns` was read after the claim was taken, so a value that is not a number
    raised with the claim held, and the step's retry met its own lease."""
    run = store.create(WorkflowRun(id="", workflow_name="stage-claim"))
    spec = _spec({"max_turns": "lots"})
    store.write_spec(run.id, spec)
    with pytest.raises(ValueError):
        asyncio.run(
            engine.dispatch_stage(
                Node.from_dict(spec["root"]["children"][0]),
                BindingContext(),
                subagents=_Helpers(),
                run_id=run.id,
                instance_path=STAGE_PATH,
            )
        )
    assert leases.read_claim(engine.claim_key(run.id, STAGE_PATH)) is None
