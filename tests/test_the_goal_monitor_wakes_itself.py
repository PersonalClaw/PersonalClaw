"""The goal-pursuit monitor wakes itself: the trigger it armed answers its park, and it watches on.

The monitor parks on an ``event`` gate between checks. Each check arms the next wake with
``set_onetime_task(resume_run_id="self", message=...)``, and that tool keeps the message as the
resume target's ``answer``, so the fire answers the park with a STRING. The engine gave every gate
an approval ask, the event gate included, and an approval takes only a yes or a no: measured on
``main``, the first fire was refused ``WF_RESUME_INVALID_ANSWER``, the park kept waiting, and the
monitor never ran a second check.

Driven with real code at every link a wake passes through:

    ScriptedProvider (the shipped offline model; ONE instance, so its turns walk the stages)
      -> SubagentManager (real) -> each stage's result
      -> set_onetime_task (the real automation tool, run with the stage's own leaf env)
      -> a real trigger row in the store
      -> dispatch_fires -> _execute_delivery -> _apply_resume -> resume_run -> RunController.resume
      -> the check behind the park, which cannot run unless the park took the wake.

Two things stand in:

* the session manager, only to hand the real manager the scripted model (as
  ``test_workflows_stage_usage_end_to_end.py`` does);
* the agent CLI a stage runs in, only for what a bare model provider never does: RUN the tool call
  the model makes. It runs it through the automation tool server's own handler with the stage's leaf
  env as that server's environment, which is where ``resume_run_id: "self"`` finds its run.

The stage's ``when`` ("in 1 hour") is read without a model, into a one-time trigger; each fire is
dispatched directly, so the time it names never decides anything here.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.llm.events import EVENT_TOOL_CALL
from personalclaw.triggers import loop as tl
from personalclaw.triggers import wakeup as W
from personalclaw.triggers.models import Outcome
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import store as wstore
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

TEMPLATE = "goal-pursuit-monitor"

INTAKE = "root.children[0]"
REPORT = "root.children[2]"


def _park(cycle: int) -> str:
    return f"root.children[1].body@{cycle}.children[0]"


def _check(cycle: int) -> str:
    return f"root.children[1].body@{cycle}.children[1]"


#: What each wake tells the next check to look at first: the `message` the stage passes.
FIRST_WAKE = "Start from the checks tab: CI was still running at the baseline."
SECOND_WAKE = "Look at the merge box first: the last check saw one approval."


def _arm(call_id: str, message: str) -> dict[str, Any]:
    """The tool call a stage makes to arm its next wake, as the template tells it to."""
    return {
        "id": call_id,
        "name": "set_onetime_task",
        "input": {
            "name": "pr-123-watch: next check",
            "when": "in 1 hour",
            "message": message,
            "resume_run_id": "self",
        },
    }


#: The model's turns, one per stage, in the order the run dispatches them. `expect_prompt` pins
#: each turn to the stage it is written for, so a stage that ran out of order fails loudly here
#: instead of consuming another stage's answer.
SCRIPT = {
    "version": 1,
    "on_exhausted": "error",
    "turns": [
        {
            "expect_prompt": "You are starting a MONITOR",
            "text": json.dumps(
                {
                    "baseline_summary": "PR #123 is open, CI running, no approvals.",
                    "goal_met": False,
                    "next_check_when": "in 1 hour",
                    "scheduled": True,
                }
            ),
            "tool_calls": [_arm("arm-1", FIRST_WAKE)],
        },
        {
            "expect_prompt": "You are one wake of a parked MONITOR run",
            "text": json.dumps(
                {
                    "summary": "CI is green; one approval, still open.",
                    "changed": True,
                    "goal_met": False,
                    "evidence": "",
                    "next_check_when": "in 1 hour",
                }
            ),
            "tool_calls": [_arm("arm-2", SECOND_WAKE)],
        },
        {
            "expect_prompt": "You are one wake of a parked MONITOR run",
            "text": json.dumps(
                {
                    "summary": "Merged.",
                    "changed": True,
                    "goal_met": True,
                    "evidence": "PR #123 state: MERGED",
                    "next_check_when": "",
                }
            ),
        },
        {
            "expect_prompt": "The monitor's goal is met",
            "text": json.dumps(
                {"report": "Watched PR #123 over two checks.", "goal_evidence": "state: MERGED"}
            ),
        },
    ],
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home with the scripted model's opt-in pointed at :data:`SCRIPT`.

    The scripted model refuses to construct unless ``PERSONALCLAW_HOME`` is set and is not the real
    home (``llm/scripted.py``), so the home is set here, per test.
    """
    from personalclaw.llm.registry import SCRIPTED_PROVIDER_ENV

    home = tmp_path / "home"
    home.mkdir()
    script = tmp_path / "script.json"
    script.write_text(json.dumps(SCRIPT))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


@contextmanager
def _process_env(env: dict[str, str]):
    """*env* as this process's environment for the block, as a tool server spawned with it sees."""
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _AgentCli:
    """The agent CLI a stage runs in, reduced to running the tool calls its model makes.

    Each call goes through the automation tool server's own handler (``mcp_automation._call_tool``)
    with the stage's leaf env (``engine.leaf_spawn_env``: its lineage and posture) as the process
    environment, which is how the CLI's tool server runs. What each call answered is kept for the
    test to read.
    """

    def __init__(self, model: Any, leaf_env: dict[str, str] | None, results: list[str]) -> None:
        self._model = model
        self._env = {k: v for k, v in (leaf_env or {}).items() if k.startswith("__wf_")}
        self._results = results
        self.session_id = ""

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)

    async def stream(self, message: str):
        from personalclaw import mcp_automation

        async for event in self._model.stream(message):
            if event.kind == EVENT_TOOL_CALL:
                with _process_env(self._env):
                    self._results.append(
                        str(mcp_automation._call_tool(event.title, dict(event.tool_input or {})))
                    )
            yield event


def _manager(tool_results: list[str]) -> tuple[Any, Any]:
    """A real SubagentManager whose every session is the ONE scripted model, run by `_AgentCli`.
    Returns the manager and the model, whose turn counter says how many stages it answered."""
    from personalclaw.llm.scripted import ScriptedProvider
    from personalclaw.subagent import SubagentManager

    model = ScriptedProvider()

    async def _session(key: str, **kwargs: Any) -> tuple[Any, bool, bool]:
        return _AgentCli(model, kwargs.get("extra_env"), tool_results), True, False

    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(side_effect=_session)
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    ctx = MagicMock()
    # The message each stage builds reaches the model as it is, so `expect_prompt` reads the
    # stage's own prompt.
    ctx.build_message = MagicMock(side_effect=lambda message, *a, **k: (message, None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    return SubagentManager(sessions=sessions, ctx_builder=ctx, is_yolo=lambda: True), model


def _attach(monkeypatch: pytest.MonkeyPatch, watchdog: WorkflowWatchdog) -> None:
    """Publish the supervisor where the gateway does, so the trigger loop finds the live run."""
    from personalclaw.action_providers import services as svc_mod

    services = svc_mod.get_action_services()
    if services is None:
        services = svc_mod.ActionServices(state=None)
        monkeypatch.setattr(svc_mod, "get_action_services", lambda: services)
    monkeypatch.setattr(services, "workflows", watchdog, raising=False)


class _Fire:
    """The due fire `dispatch_fires` takes, for a trigger read back from the store."""

    def __init__(self, trigger: Any) -> None:
        self.trigger = trigger
        self.scheduled_for = time.time() - 1
        self.reason = ""


async def _never_runs(_payload: Any) -> Any:
    raise AssertionError("a wake must never run the trigger's ordinary action")


async def _fire(trigger: Any) -> Any:
    """Fire *trigger* through the trigger loop's own dispatch, as a due clock fire is."""
    (delivery,) = W.dispatch_fires(None, [_Fire(trigger)], now=time.time())
    outcomes = await tl._execute_delivery(delivery, _never_runs, sessions=None, now=time.time())
    assert len(outcomes) == 1, outcomes
    return outcomes[0]


def _wakes_for(home: Path, run_id: str) -> list[Any]:
    """The triggers in the store that wake *run_id*, oldest first."""
    rows = [
        row.trigger
        for row in TriggerStore(base_dir=home).load()
        if row.ok and W.resume_target_of(row.trigger).get("run_id") == run_id
    ]
    return sorted(rows, key=lambda t: t.id)


@pytest.mark.anyio
async def test_the_monitor_wakes_on_the_trigger_it_armed_and_watches_until_the_goal_is_met(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_results: list[str] = []
    manager, model = _manager(tool_results)
    run = wstore.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={
                "task": "PR #123 is merged",
                "check_instructions": "Open PR #123 and read its state, CI and approvals.",
                "success_criteria": "The PR's state reads MERGED.",
                "cadence": "in 1 hour",
                "scope": ".",
            },
        )
    )
    spec = read_template(TEMPLATE).to_dict()
    wstore.write_spec(run.id, spec)
    watchdog = WorkflowWatchdog(None, EngineServices(subagents=manager, cwd=""))
    _attach(monkeypatch, watchdog)
    controller = await watchdog.launch(run, spec)

    # The baseline check ran, armed the first wake, and the run parked on its event gate.
    assert await controller.wait_for_terminal(timeout=30) == RunStatus.NEEDS_INPUT
    assert wstore.read_output(run.id, INTAKE)["scheduled"] is True
    assert controller.instances[_park(0)].state == InstanceState.WAITING
    (first,) = _wakes_for(home, run.id)
    assert W.resume_target_of(first)["gate_answer"] == FIRST_WAKE, tool_results

    outcome = await _fire(first)
    # 🔴 The wake itself. On `main` this was `refused` / `WF_RESUME_INVALID_ANSWER`: the park's ask
    # was an approval, which takes a yes or a no, and the wake carries the message it was armed
    # with.
    assert outcome.outcome == Outcome.RAN.value, (outcome.reason, outcome.reported)
    assert outcome.reported == "gate answered"
    assert wstore.read_output(run.id, _park(0))["answer"] == FIRST_WAKE

    # The check behind the park ran — the proof the run moved — and armed the next wake.
    assert await controller.wait_for_terminal(timeout=30) == RunStatus.NEEDS_INPUT
    assert wstore.read_output(run.id, _check(0))["goal_met"] is False
    assert controller.instances[_park(1)].state == InstanceState.WAITING
    second = [t for t in _wakes_for(home, run.id) if t.id != first.id]
    assert len(second) == 1, tool_results
    assert W.resume_target_of(second[0])["gate_answer"] == SECOND_WAKE

    outcome = await _fire(second[0])
    assert outcome.outcome == Outcome.RAN.value, (outcome.reason, outcome.reported)
    assert wstore.read_output(run.id, _park(1))["answer"] == SECOND_WAKE

    # The second check saw the goal met, so the watch ended and the close-out ran.
    status = await controller.wait_for_terminal(timeout=30)
    assert status == RunStatus.COMPLETE, "; ".join(
        [f"error={wstore.get(run.id).error_message!r}"]
        + [
            f"{path}={inst.state.value}:{(inst.failure.cause_plain if inst.failure else '')}"
            for path, inst in controller.instances.items()
        ]
    )
    assert wstore.read_output(run.id, _check(1))["goal_met"] is True
    assert wstore.read_output(run.id, REPORT)["goal_evidence"] == "state: MERGED"
    assert wstore.get(run.id).status == RunStatus.COMPLETE
    # Every scripted turn was served, each to the stage it was written for.
    assert model.turn_index == len(SCRIPT["turns"])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
