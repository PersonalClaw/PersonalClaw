"""A run's Pause STOPS its work, stays paused across a restart, and Resume picks the work back up.

Two defects, both measured at `origin/main`:

* **Pause did nothing at all (#370).** `service.pause_run` wrote `run.extra["pause_requested"]` on a
  fresh copy of the row, the live controller's next save overwrote it, and nothing in the engine
  ever read the key. The run page's Pause button answered 200 and the run went on driving.
* **"Pause — in-flight steps finish" is not a pause for a loop.** A stage runs for minutes and
  writes files the whole time; the loop measured 2026-09-25 wrote a finding 3.5 minutes after the
  cockpit said "Paused". So a pause now stops the in-flight stage's subagent and re-queues the
  stage.

And one on Cancel: a DISPATCHED stage was never stopped by a cancel — its subagent kept working, and
a spawn still waiting on approval stayed approvable (measured 2026-09-25: approving a cancelled
run's `spawn:` approval spawned a subagent for it).

The subagent manager is the only fake: it holds its spawns RUNNING until told, records every
`cancel`, and reports a cancelled spawn done — the two facts the controller reads.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import service, store
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
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""
        self.cancelled = False


class _HeldSubagents:
    """Spawns stay RUNNING until `release()`; `cancel` stops one and is recorded."""

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}
        self.spawned: list[str] = []
        self.cancelled: list[str] = []
        self.hold = True

    def spawn(self, **kw: Any) -> _Info:
        payload = {"summary": "working", "meaningful_progress": False, "evidence": "x"}
        if "You are verifying work you did not do" in str(kw.get("task")):
            payload = {
                "reasoning": "ok",
                "verdict": "PASS",
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["x"],
                "proof": "x",
                "cannot_judge": "",
            }
        info = _Info(f"sub{len(self.infos) + 1}", json.dumps(payload))
        self.infos[info.id] = info
        self.spawned.append(info.id)
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not self.hold and not info.cancelled:
            info.done = True
        return info

    async def cancel(self, agent_id: str) -> bool:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return False
        info.cancelled = True
        info.done = True
        info.error = "Cancelled by user"
        self.cancelled.append(agent_id)
        return True


class _Supervisor:
    """What `service` reaches a live controller through."""

    def __init__(self, controller: RunController) -> None:
        self._c = controller

    def controller(self, run_id: str) -> RunController | None:
        return self._c if self._c.run.id == run_id else None


def _new_run() -> tuple[WorkflowRun, dict[str, Any]]:
    spec = read_template(TEMPLATE).to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={"task": "write the checklist", "exit_condition": "it exists"},
            policy_overrides={"attended": False},
        )
    )
    store.write_spec(run.id, spec)
    return run, spec


async def _until(predicate: Any, *, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before the deadline")
        await asyncio.sleep(0.05)


def _running_stage(run_id: str) -> list[str]:
    return [
        p
        for p, inst in store.read_state(run_id).items()
        if inst.state == InstanceState.RUNNING and inst.subagent_id
    ]


def test_pause_stops_the_running_stage_and_resume_redispatches_it() -> None:
    """The whole claim, end to end on the real spec.

    At `origin/main` the run stays `running` with the stage's subagent never cancelled — the
    `_until(PAUSED)` wait below times out.
    """
    run, spec = _new_run()
    fake = _HeldSubagents()
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    sup = _Supervisor(controller)

    async def _go() -> None:
        await controller.start()
        await _until(lambda: len(fake.spawned) == 1 and _running_stage(run.id))
        assert service.pause_run(run.id, supervisor=sup)["ok"]
        await _until(lambda: store.get(run.id).status == RunStatus.PAUSED)
        # The in-flight stage was STOPPED, not left to finish, and went back in the queue.
        assert fake.cancelled == ["sub1"], fake.cancelled
        assert not _running_stage(run.id), store.read_state(run.id)
        # Nothing is dispatched while paused, however long it stays paused.
        await asyncio.sleep(1.0)
        assert fake.spawned == ["sub1"], f"a paused run dispatched {fake.spawned}"

        fake.hold = False
        assert service.resume_run(run.id, supervisor=sup)["ok"]
        await _until(lambda: len(fake.spawned) >= 2)
        # The SAME stage came back: it is the work step, not the judge that follows it.
        assert "You are verifying" not in str(fake.infos["sub2"].result)
        status = await asyncio.wait_for(controller.run_to_completion(), timeout=20.0)
        assert status in (RunStatus.COMPLETE, RunStatus.ESCALATED), status

    asyncio.run(_go())


def test_a_paused_run_is_not_readopted_by_the_watchdog() -> None:
    """Sticky across a restart: a paused run whose pause intent is on disk is left alone, where
    adopting it would launch a controller that flips it back to RUNNING."""
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    run, _spec = _new_run()
    run.status = RunStatus.PAUSED
    run.started_at = "2026-09-25T00:00:00Z"
    store.save(run)
    store.request_pause(run.id)
    watchdog = WorkflowWatchdog(services=EngineServices(subagents=_HeldSubagents()))
    watchdog._swept = True
    asyncio.run(watchdog._poll_once())
    assert watchdog.controller(run.id) is None, "a paused run was re-adopted"
    assert store.get(run.id).status == RunStatus.PAUSED

    store.clear_pause(run.id)

    async def _adopt_then_stop() -> None:
        await watchdog._poll_once()
        assert watchdog.controller(run.id) is not None, "a resumed run was not adopted"
        await watchdog.stop()

    asyncio.run(_adopt_then_stop())


def test_cancel_stops_a_dispatched_stage() -> None:
    """At `origin/main` `_cancel_inflight` touched `_inflight` only, which never holds a dispatched
    stage, so the subagent was never cancelled — `fake.cancelled` stays empty."""
    run, spec = _new_run()
    fake = _HeldSubagents()
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    sup = _Supervisor(controller)

    async def _go() -> None:
        await controller.start()
        await _until(lambda: _running_stage(run.id))
        assert service.cancel_run(run.id, supervisor=sup)["ok"]
        await _until(lambda: store.get(run.id).status == RunStatus.CANCELLED)
        assert fake.cancelled == ["sub1"], fake.cancelled

    asyncio.run(_go())


def test_cancelling_a_paused_run_is_applied() -> None:
    """A paused run's tick loop is not running, so a sticky cancel must WAKE it to be applied —
    else the run stays paused forever with a cancel on disk that nothing reads."""
    run, spec = _new_run()
    fake = _HeldSubagents()
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    sup = _Supervisor(controller)

    async def _go() -> None:
        await controller.start()
        await _until(lambda: _running_stage(run.id))
        service.pause_run(run.id, supervisor=sup)
        await _until(lambda: store.get(run.id).status == RunStatus.PAUSED)
        assert service.cancel_run(run.id, supervisor=sup)["ok"]
        await _until(lambda: store.get(run.id).status == RunStatus.CANCELLED)
        assert not store.pause_requested(run.id), "an ended run kept its pause intent"

    asyncio.run(_go())


def test_a_draft_cannot_be_paused() -> None:
    run = store.create(WorkflowRun(id="", workflow_name=TEMPLATE))
    result = service.pause_run(run.id)
    assert result["ok"] is False and result["code"] == "WF_RUN_NOT_LIVE", result
    assert not store.pause_requested(run.id)
