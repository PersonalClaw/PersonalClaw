"""A loop run in its second iteration survives a gateway restart: it carries on where it was.

Measured on a live General loop (driven 2026-09-25): paused in its second iteration, gateway
restarted, Resume — and five seconds later the run FAILED "run deadlocked: no runnable nodes and
none in flight". Two defects, and the restart needs both fixed:

* **The iteration counter was in memory only.** ``RunController._iterations`` is what the frontier
  reads to know WHICH body iteration is current, and a controller built by a new process started
  every loop at 0 — an iteration already terminal — so nothing was runnable.
* **A stage in flight at the restart was never re-run.** Its instance still read RUNNING with the
  dead process's subagent id; a fresh manager knows no ids, so the reconciler waited for a verdict
  that could never come (correctly refusing to invent one) until the stale-run audit.

The subagent manager is the only fake, and each "process" gets its own — a fresh manager knowing
no ids IS the post-restart shape.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

TEMPLATE = "general-project"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id, self.result = agent_id, result
        self.done, self.error, self.reaped, self.agent, self.cancelled = False, "", False, "", False


class _Subagents:
    """One PROCESS's manager: it knows only the ids it spawned. Judges always finish; a work step
    finishes unless it was spawned after ``hold_after_judges`` judges had been — which is how the
    test parks the run with its SECOND iteration's work in flight."""

    def __init__(self, name: str, *, hold_after_judges: int | None = None) -> None:
        self.name = name
        self.infos: dict[str, _Info] = {}
        self.spawned: list[tuple[str, bool]] = []  # (id, is_judge)
        self.held: set[str] = set()
        self.hold_after_judges = hold_after_judges

    def spawn(self, **kw: Any) -> _Info:
        judge = "You are verifying work you did not do" in str(kw.get("task"))
        payload: dict[str, Any] = {
            "summary": "wrote it",
            "meaningful_progress": True,
            "evidence": "x",
        }
        if judge:
            payload = {
                "reasoning": "ok",
                "verdict": "PASS",
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["x"],
                "proof": "x",
                "cannot_judge": "",
            }
        info = _Info(f"{self.name}{len(self.infos) + 1}", json.dumps(payload))
        judges_so_far = sum(1 for _, j in self.spawned if j)
        if (
            not judge
            and self.hold_after_judges is not None
            and judges_so_far >= self.hold_after_judges
        ):
            self.held.add(info.id)
        self.infos[info.id] = info
        self.spawned.append((info.id, judge))
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is None or info.cancelled:
            return info
        if agent_id not in self.held:
            info.done = True
        return info

    async def cancel(self, agent_id: str, *, reason: str = "Cancelled by user") -> bool:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return False
        info.cancelled = info.done = True
        info.error = reason
        return True


async def _until(predicate: Any, *, timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before the deadline")
        await asyncio.sleep(0.05)


def _new_run() -> tuple[WorkflowRun, dict[str, Any]]:
    spec = read_template(TEMPLATE).to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={"task": "write the checklist", "exit_condition": "it exists"},
            policy_overrides={"attended": False, "max_cycles": 4},
        )
    )
    store.write_spec(run.id, spec)
    return run, spec


def _running_work_paths(run_id: str) -> list[str]:
    return sorted(
        p
        for p, i in store.read_state(run_id).items()
        if i.state == InstanceState.RUNNING and i.subagent_id and p.endswith("children[0]")
    )


def test_a_run_restarted_mid_second_iteration_carries_on() -> None:
    """At `origin/main` the second process never dispatches anything: its stage stays RUNNING
    under a dead id, and its loop counter reads 0."""
    run, spec = _new_run()
    first = _Subagents("a", hold_after_judges=1)

    async def _go() -> None:
        # Process one: iteration 0 runs to completion, iteration 1's work step is in flight.
        a = RunController(run, spec, services=EngineServices(subagents=first))
        await a.start()
        await _until(lambda: any(p.startswith("root.body@1") for p in _running_work_paths(run.id)))
        await asyncio.sleep(0.3)
        # The process dies — its controller's task ends without any orderly shutdown of the run.
        a._task.cancel()  # type: ignore[union-attr]
        await asyncio.sleep(0.1)

        stranded = _running_work_paths(run.id)
        assert stranded == ["root.body@1.children[0]"], stranded

        # Process two: a fresh manager that knows no ids, and a controller built from the store.
        second = _Subagents("b")
        b = RunController(
            store.get(run.id), store.read_spec(run.id), services=EngineServices(subagents=second)
        )
        await b.start()
        await _until(lambda: len(second.spawned) >= 1)
        # The re-dispatched step is iteration 1's WORK — not iteration 0 again, and not a judge.
        state = store.read_state(run.id)
        assert state["root.body@1.children[0]"].subagent_id == second.spawned[0][0], state
        assert second.spawned[0][1] is False
        status = await asyncio.wait_for(b.run_to_completion(), timeout=30.0)
        assert status in (RunStatus.COMPLETE, RunStatus.ESCALATED), (
            status,
            store.get(run.id).error_message,
        )
        assert "deadlocked" not in (store.get(run.id).error_message or "")

    asyncio.run(_go())


def test_the_loop_counter_is_rebuilt_from_the_continue_records() -> None:
    """The unit: one past the last iteration that CONTINUED; a stop or a breaker row is not a
    continue, and a fresh run has no rows at all."""
    run, spec = _new_run()
    j = journal_mod.Journal(run.id)
    j.iteration("root", "project", iteration=0, outcome="continue")
    j.iteration("root", "project", iteration=1, outcome="breaker:identical_output")
    j.iteration("root", "project", iteration=1, outcome="continue")
    j.iteration("root", "project", iteration=2, outcome="dry_streak")
    c = RunController(run, spec, services=EngineServices(subagents=_Subagents("c")))
    c._rehydrate_loop_progress()
    assert c._iterations == {"root": 2}

    fresh, fresh_spec = _new_run()
    c2 = RunController(fresh, fresh_spec, services=EngineServices(subagents=_Subagents("d")))
    c2._rehydrate_loop_progress()
    assert c2._iterations == {}


def test_a_stage_this_process_knows_is_not_requeued() -> None:
    """The discrimination: only an id the manager has never seen is an orphan."""
    run, spec = _new_run()
    mgr = _Subagents("e")
    info = mgr.spawn(task="work")
    c = RunController(run, spec, services=EngineServices(subagents=mgr))
    inst = c._instance("root.body@0.children[0]")
    inst.state, inst.subagent_id, inst.attempt = InstanceState.RUNNING, info.id, 1
    assert c._requeue_orphaned_stages() == []
    assert inst.state is InstanceState.RUNNING and inst.subagent_id == info.id

    inst.subagent_id = "gone-with-the-last-process"
    assert c._requeue_orphaned_stages() == ["root.body@0.children[0]"]
    assert (inst.state, inst.subagent_id, inst.attempt) == (InstanceState.PENDING, "", 0)
