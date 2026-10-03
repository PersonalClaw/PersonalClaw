"""A workflow an agent starts from a chat runs, the way one its owner starts does.

The native runtime calls the workflow tools off the event loop, in a worker thread, and the tools
made each service call on a loop of their own (`asyncio.run`). A run is driven by its controller's
tick loop, a task on the loop the run was launched on, so a run an agent started was launched on
that private loop, which closed as the call returned: the tick loop was cancelled on the run's first
step. The supervisor still held that controller as the run's driver and never took the run over,
so the run read "running" on its first step for good, while the same definition started by its
owner (`POST /api/workflows/runs`, on the gateway's loop) ran normally. A pause an agent lifted
stayed on for the same reason: the tool's thread had no loop to restart the run's tick loop on.

Every call the tools make into a run is now handed to the supervisor's loop and waited for
(`WorkflowWatchdog.run_threadsafe`), and a start names the chat that made it, as the owner's route
does: that chat's memory posture goes with the run.

Driven through the real seam: a real native runtime whose scripted model calls the real tool, run in
the executor thread by the real in-process provider, under a real supervisor started on this loop.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from test_a_declared_read_asks_nobody import _OneCall

from personalclaw import session_restrictions
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, EVENT_TOOL_RESULT
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.models import TERMINAL_RUN_STATUSES, RunStatus, WorkflowRun
from personalclaw.workflows.ownership import MemoryMode, run_mode
from personalclaw.workflows.watchdog import WorkflowWatchdog

#: The chat whose turn calls the tool.
KEY = "dashboard:chat-agent-start"

#: Two steps, the second bound to the first's output, so a run that completes ran both in order.
TWO_STEPS = {
    "name": "two-steps",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 1}}},
            {"kind": "transform", "id": "after", "config": {"expr": "saw {{nodes.seed.output.n}}"}},
        ],
    },
}

#: A run that waits a moment between its steps, long enough to be paused in the middle.
WAITS = {
    "name": "waits-a-moment",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": "first"}},
            {"kind": "wait", "id": "pause", "config": {"duration_secs": 1}},
            {"kind": "transform", "id": "after", "config": {"expr": "carried on"}},
        ],
    },
}


class _Defs(defs_mod.WorkflowDefProvider):
    """The definitions this file runs, read-only, as a shipped pack serves them."""

    @property
    def name(self) -> str:
        return "agent-start-defs"

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [TWO_STEPS, WAITS], 2

    async def get_def(self, name: str):
        return {TWO_STEPS["name"]: TWO_STEPS, WAITS["name"]: WAITS}.get(name)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    defs_mod.register_provider(_Defs())
    # A tick wakes often, so a pause lands on the step after it is asked rather than seconds later.
    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.02)
    try:
        yield home
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


@pytest_asyncio.fixture
async def supervisor(monkeypatch):
    """The gateway's supervisor: started on this loop, and published where the tools find it."""
    watchdog = WorkflowWatchdog(None, EngineServices())
    watchdog.start()
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=watchdog),
    )
    try:
        yield watchdog
    finally:
        await watchdog.stop()


async def _turn(tool: str, arguments: dict[str, Any]) -> str:
    """One native chat turn whose model calls *tool* with *arguments*, the call approved as its
    owner approves it. Returns the call's result as the model got it."""
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall(tool, json.dumps(arguments)),
        tool_providers=[
            InProcessMcpToolProvider(
                module="personalclaw.mcp_workflows", provider_name="personalclaw-workflows"
            )
        ],
        session_key=KEY,
    )
    runtime.set_task_mode("agent")
    await runtime.start()
    results: list[str] = []

    async def drain() -> None:
        async for event in runtime.stream("go"):
            if event.kind == EVENT_PERMISSION_REQUEST:
                await runtime.approve_tool(event.request_id)
            elif event.kind == EVENT_TOOL_RESULT:
                results.append(str(event.tool_output))

    await asyncio.wait_for(drain(), timeout=20)
    assert len(results) == 1, results
    return results[0]


def _run_id(result: str) -> str:
    assert not result.startswith("Error"), result
    return str(json.loads(result.split("\n", 1)[1])["run_id"])


async def _ends(run_id: str, *, within: float = 10.0) -> RunStatus:
    """The status the run ends with, or the one it still reads once *within* seconds are up."""
    deadline = time.monotonic() + within
    while True:
        run = store.get(run_id)
        assert run is not None
        if run.status in TERMINAL_RUN_STATUSES or time.monotonic() > deadline:
            return run.status
        await asyncio.sleep(0.05)


def _state(run_id: str, path: str) -> str:
    instance = store.read_state(run_id).get(path)
    return instance.state.value if instance is not None else ""


async def _until(condition: Any, *, within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_workflow_an_agent_starts_from_a_chat_runs_to_completion(supervisor) -> None:
    """🔴 Before: the run read `running` on its first step for good."""
    run_id = _run_id(await _turn("workflow_start", {"name": TWO_STEPS["name"]}))

    assert await _ends(run_id) == RunStatus.COMPLETE
    assert store.read_output(run_id, "root.children[1]") == "saw 1"


@pytest.mark.asyncio
async def test_the_run_names_the_chat_that_started_it_and_keeps_its_memory_posture(
    supervisor,
) -> None:
    """🔴 Before: the start named no chat, so a run an Incognito chat's agent started was an
    ordinary one, free to write what the chat keeps out of memory."""
    session_restrictions.clear(KEY)
    session_restrictions.mark_incognito(KEY)
    try:
        run_id = _run_id(await _turn("workflow_start", {"name": TWO_STEPS["name"]}))
        run = store.get(run_id)
        assert run is not None
        assert run.origin.session_key == KEY
        assert run_mode(run) is MemoryMode.INCOGNITO
        assert await _ends(run_id) == RunStatus.COMPLETE
    finally:
        session_restrictions.clear(KEY)


@pytest.mark.asyncio
async def test_a_draft_an_agent_starts_runs_to_completion(supervisor) -> None:
    """`workflow_start_draft`, the launch after `workflow_fork`, launched the same way.
    🔴 Before: the draft read `running` on its first step for good."""
    draft = store.create(
        WorkflowRun(id="", workflow_name=TWO_STEPS["name"], status=RunStatus.DRAFT)
    )
    store.write_spec(draft.id, TWO_STEPS)

    result = await _turn("workflow_start_draft", {"run_id": draft.id})

    assert not result.startswith("Error"), result
    assert await _ends(draft.id) == RunStatus.COMPLETE


@pytest.mark.asyncio
async def test_a_pause_an_agent_lifts_lets_the_run_carry_on(supervisor) -> None:
    """Lifting a pause restarts the run's tick loop. 🔴 Before: the tool's thread had no loop to
    restart it on, and the supervisor holds a paused run's controller as its driver, so the run
    stayed paused."""
    started = await service.start_run(
        name=WAITS["name"], supervisor=supervisor, skip_preflight=True
    )
    run_id = str(started["run_id"])
    await _until(lambda: _state(run_id, "root.children[1]") == "waiting")
    assert service.pause_run(run_id)["ok"]
    await _until(lambda: store.get(run_id).status == RunStatus.PAUSED)

    result = await _turn("workflow_resume", {"run_id": run_id})

    assert '"resumed": true' in result, result
    assert await _ends(run_id) == RunStatus.COMPLETE
    assert store.read_output(run_id, "root.children[2]") == "carried on"


@pytest.mark.asyncio
async def test_a_blocking_start_answers_with_the_runs_ending(supervisor) -> None:
    """A guard on the hand-over: a blocking start waits on the supervisor's loop for the run to
    end, and the call's answer is that ending."""
    result = await _turn("workflow_start", {"name": TWO_STEPS["name"], "mode": "blocking"})

    body = json.loads(result.split("\n", 1)[1])
    assert body["blocking"] is True and body["status"] == RunStatus.COMPLETE.value, body
