"""Incident mode holds a workflow run, the way it holds a loop, instead of failing its stages.

Measured with the switch on: a stage the run reached was refused by the subagent manager ("spawn
refused: incident mode active") and the engine filed that as the stage's failure, so the run
failed; and a stage already running went on working through the incident. A general loop is a
workflow run, so the same happened to its stages.

Now a run honours the switch as a loop does: while it is on, the stage in flight is stopped the
way a Pause stops it and goes back in the queue, nothing new starts, and the run stays ``running``
and says it is held; once the switch is off, it carries on by itself.

The subagent manager is the only fake, as in ``test_run_pause_withdraws_in_flight_work``. The
incident switch is the real one, under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.guardrails import incident
from personalclaw.workflows import service, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

TEMPLATE = "general-project"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    incident.reset_incident_mirror()
    yield home
    incident.reset_incident_mirror()


class _Info:
    def __init__(self, agent_id: str, result: str, error: str = "") -> None:
        self.id = agent_id
        self.done = bool(error)
        self.error = error
        self.result = result
        self.reaped = False
        self.agent = ""
        self.cancelled = False


class _Subagents:
    """Spawns stay RUNNING until `hold` is lifted; `cancel` stops one and is recorded. It refuses a
    spawn while incident mode is on, in the words the real manager uses."""

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}
        self.spawned: list[str] = []
        self.refused = 0
        self.cancelled: list[tuple[str, str]] = []
        self.hold = True
        #: Turns the switch on as a spawn arrives: the switch moving between the run's look at it
        #: and the spawn.
        self.switch_on_at_spawn = False

    def spawn(self, **kw: Any) -> _Info:
        if self.switch_on_at_spawn:
            self.switch_on_at_spawn = False
            incident.activate("rollback in progress")
        if incident.incident_active():
            self.refused += 1
            return _Info(
                f"refused{self.refused}",
                "",
                error="spawn refused: incident mode active "
                "(resume with `personalclaw incident off`)",
            )
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
        info = _Info(f"sub{len(self.spawned) + 1}", json.dumps(payload))
        self.infos[info.id] = info
        self.spawned.append(info.id)
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not self.hold and not info.cancelled:
            info.done = True
        return info

    async def cancel(self, agent_id: str, *, reason: str = "Cancelled by user") -> bool:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return False
        info.cancelled = True
        info.done = True
        info.error = reason
        self.cancelled.append((agent_id, reason))
        return True


def _new_run() -> tuple[WorkflowRun, dict[str, Any]]:
    spec = read_template(TEMPLATE).to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={"task": "write the checklist", "exit_condition": "it exists"},
            policy_overrides={"attended": False},
            loop_kind="general",
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


def _running_stages(run_id: str) -> list[str]:
    return [
        p
        for p, inst in store.read_state(run_id).items()
        if inst.state == InstanceState.RUNNING and inst.subagent_id
    ]


def _failed(run_id: str) -> list[str]:
    return [p for p, i in store.read_state(run_id).items() if i.state == InstanceState.FAILED]


def _views_say_held(run: WorkflowRun) -> tuple[str, str]:
    from personalclaw.workflows.loop_view import run_loop_view

    return service.status(run.id).get("held", ""), run_loop_view(store.get(run.id)).get("held", "")


def test_a_running_stage_is_stopped_and_the_run_carries_on_once_the_switch_is_off() -> None:
    run, spec = _new_run()
    fake = _Subagents()
    controller = RunController(run, spec, services=EngineServices(subagents=fake))

    async def _go() -> None:
        await controller.start()
        await _until(lambda: fake.spawned == ["sub1"] and _running_stages(run.id))

        incident.activate("rollback in progress")
        # The stage in flight is STOPPED, not left to work through the incident, and goes back in
        # the queue; the run is held, not failed and not paused.
        await _until(lambda: fake.cancelled == [("sub1", "Stopped: incident mode is on")])
        await _until(lambda: not _running_stages(run.id))
        assert store.get(run.id).status == RunStatus.RUNNING
        assert _failed(run.id) == []
        run_view, loop_view = _views_say_held(run)
        assert run_view.startswith("Held: incident mode is on"), run_view
        assert loop_view.startswith("Held: incident mode is on"), loop_view
        # Nothing starts while the switch is on, however long it stays on.
        await asyncio.sleep(1.0)
        assert fake.spawned == ["sub1"] and fake.refused == 0, (fake.spawned, fake.refused)

        fake.hold = False
        incident.resume()
        # The SAME stage runs again, by itself.
        await _until(lambda: len(fake.spawned) >= 2)
        assert "You are verifying" not in str(fake.infos["sub2"].result)
        assert _views_say_held(run) == ("", "")
        status = await asyncio.wait_for(controller.run_to_completion(), timeout=20.0)
        assert status in (RunStatus.COMPLETE, RunStatus.ESCALATED), status

    asyncio.run(_go())


def test_a_stage_the_run_reaches_while_the_switch_is_on_waits_for_it_instead_of_failing() -> None:
    """🔴 Before: the subagent manager refused the spawn and the stage, and the run, failed."""
    run, spec = _new_run()
    fake = _Subagents()
    fake.hold = False
    incident.activate("rollback in progress")
    controller = RunController(run, spec, services=EngineServices(subagents=fake))

    async def _go() -> None:
        await controller.start()
        await asyncio.sleep(1.0)
        assert fake.spawned == [] and fake.refused == 0, (fake.spawned, fake.refused)
        assert store.get(run.id).status == RunStatus.RUNNING
        assert _failed(run.id) == []

        incident.resume()
        await _until(lambda: len(fake.spawned) >= 1)
        status = await asyncio.wait_for(controller.run_to_completion(), timeout=20.0)
        assert status in (RunStatus.COMPLETE, RunStatus.ESCALATED), status

    asyncio.run(_go())


def test_a_spawn_the_switch_refuses_as_it_arrives_is_held_and_not_counted_as_an_attempt() -> None:
    """The switch can move between the run's look at it and the spawn. The refusal that follows is
    the switch, not the stage: the stage waits, and the attempt it never ran is not spent."""
    run, spec = _new_run()
    fake = _Subagents()
    fake.hold = False
    fake.switch_on_at_spawn = True
    controller = RunController(run, spec, services=EngineServices(subagents=fake))

    async def _go() -> None:
        await controller.start()
        await _until(lambda: fake.refused == 1)
        await asyncio.sleep(0.5)
        assert _failed(run.id) == []
        assert store.get(run.id).status == RunStatus.RUNNING
        assert all(i.attempt == 0 for i in store.read_state(run.id).values()), {
            p: i.attempt for p, i in store.read_state(run.id).items()
        }

        incident.resume()
        await _until(lambda: len(fake.spawned) >= 1)
        status = await asyncio.wait_for(controller.run_to_completion(), timeout=20.0)
        assert status in (RunStatus.COMPLETE, RunStatus.ESCALATED), status

    asyncio.run(_go())


def test_the_run_list_says_a_running_run_is_held_while_the_switch_is_on() -> None:
    """The run list reads a held run as Held, as its page does: its status stays ``running``, so a
    list row without the sentence read Running while nothing ran."""
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.workflows import handlers
    from personalclaw.workflows.incident_hold import INCIDENT_HOLD

    running, _spec = _new_run()
    running.status = RunStatus.RUNNING
    store.save(running)
    done, _spec = _new_run()
    done.status = RunStatus.COMPLETE
    store.save(done)

    def _listed() -> dict[str, str]:
        resp = asyncio.run(
            handlers.api_runs_list(make_mocked_request("GET", "/api/workflows/runs"))
        )
        return {r["id"]: r["held"] for r in json.loads(resp.body.decode())["runs"]}

    incident.activate("rollback in progress")
    assert _listed() == {running.id: INCIDENT_HOLD, done.id: ""}
    incident.resume()
    assert _listed() == {running.id: "", done.id: ""}
