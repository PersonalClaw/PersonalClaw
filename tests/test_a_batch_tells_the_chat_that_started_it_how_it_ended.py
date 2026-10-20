"""A finished batch tells the chat that started it how each of its tasks ended.

`subagent_run` runs two or more tasks as one workflow run, and its tasks' completions were consumed
by that run alone: the chat whose agent started it heard nothing, so the agent either waited on
`workflow_observe` or answered without the work it asked for. A single task's result arrives as a
completion event the agent reads on its next step; a batch's now arrives the same way, in one turn
of the chat that started it: each task by its name, with what it returned or why it did not.

* Through the real supervisor and controller, with scripted subagents: the run hands its tasks'
  endings to the conversation its origin names, when it ends.
* A run no conversation started (a workflow started from the API, a sub-run of another run) tells
  none, and neither does one someone stopped or one whose loop has ended: whoever stopped it knows,
  and an ended loop's worker has nobody left to report to.
* Through the gateway's own completion delivery: those endings become one turn of that chat.
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.config.loader import AppConfig
from personalclaw.subagent import SubagentInfo
from personalclaw.workflows import batch_compile, run_finish, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

CHAT_KEY = "dashboard:chat-a"

FIX = {
    "task": "Raise the retry ceiling in a scratch copy of the retry module",
    "title": "Raise the retry ceiling",
    "objective": "make the retry ceiling five everywhere it is decided",
    "output_format": "the diff you made, then one sentence on why",
    "boundary": "change only the retry module and its test",
}
FIND = {
    "task": "List every caller of the retry helper",
    "title": "Find the retry callers",
    "objective": "know every place that depends on the retry ceiling",
    "output_format": "a numbered list of file:line, one sentence each",
    "boundary": "read only: change no file",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


class _ScriptedSubagents:
    """Subagents that end as scripted the first time their run asks how they are doing: each task
    answers, or fails, by the words its prompt carries."""

    def __init__(self, script: dict[str, tuple[str, str]]) -> None:
        self._script = script
        self._infos: dict[str, SubagentInfo] = {}

    def spawn(self, task: str, **kw: Any) -> SubagentInfo:
        info = SubagentInfo(
            id=f"sa{len(self._infos) + 1}",
            task=task,
            parent_session_key=str(kw.get("parent_session_key") or ""),
            agent=str(kw.get("agent") or ""),
        )
        self._infos[info.id] = info
        return info

    def get(self, agent_id: str) -> SubagentInfo | None:
        info = self._infos.get(agent_id)
        if info is not None and not info.done:
            for words, (result, error) in self._script.items():
                if words in info.task:
                    info.result, info.error = result, error
            info.done = True
        return info


class _Told:
    def __init__(self) -> None:
        self.calls: list[list[SubagentInfo]] = []

    def __call__(self, infos: list[SubagentInfo]) -> None:
        self.calls.append(list(infos))


def _spec(name: str) -> dict:
    leaves = [batch_compile.leaf_from_item(FIX), batch_compile.leaf_from_item(FIND)]
    result = batch_compile.compile_batch(leaves, run_name=name)
    assert result.compiled and result.ok, result.findings
    return result.spec


async def _run_batch(origin: RunOrigin, told: _Told, script: dict) -> RunController:
    spec = _spec("subagent-batch-7")
    supervisor = WorkflowWatchdog(
        state=None,
        services=EngineServices(subagents=_ScriptedSubagents(script), announce=told),
    )
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], origin=origin))
    store.write_spec(run.id, copy.deepcopy(spec))
    controller: RunController = await supervisor.launch(run, copy.deepcopy(spec))
    await asyncio.wait_for(controller._terminal.wait(), timeout=20)
    return controller


# ── the run tells the conversation that started it ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_finished_batch_tells_its_chat_how_each_task_ended(home):
    told = _Told()
    controller = await _run_batch(
        RunOrigin(kind=OriginKind.SUBAGENT_TOOL, session_key=CHAT_KEY),
        told,
        {
            "Raise the retry ceiling": ("", "the retry module is not in the scratch copy"),
            "List every caller": ("1. src/app/fetch.py:12 reads the ceiling", ""),
        },
    )
    assert controller.run.status == RunStatus.COMPLETE, controller.run.error_message

    assert len(told.calls) == 1, f"the chat was told {len(told.calls)} times"
    endings = {info.task: info for info in told.calls[0]}
    assert set(endings) == {"Raise the retry ceiling", "Find the retry callers"}, endings
    for info in endings.values():
        assert info.parent_session_key == CHAT_KEY
        assert info.done is True
    assert endings["Find the retry callers"].result == "1. src/app/fetch.py:12 reads the ceiling"
    assert not endings["Find the retry callers"].error
    assert endings["Raise the retry ceiling"].error == (
        "the retry module is not in the scratch copy"
    )


@pytest.mark.asyncio
async def test_a_run_no_chat_started_tells_none(home):
    told = _Told()
    await _run_batch(
        RunOrigin(kind=OriginKind.API, session_key=CHAT_KEY),
        told,
        {"List every caller": ("found nothing", "")},
    )
    assert told.calls == [], "a workflow started from the API reported to a chat"


def test_a_sub_run_tells_its_parent_step_not_a_chat():
    """A subworkflow's own run carries the subagent origin too, naming its parent's node: its
    ending is its parent's step, and no chat started it."""
    told = _Told()
    run = WorkflowRun(
        id="child-1",
        workflow_name="child",
        parent_run_id="parent-1",
        origin=RunOrigin(kind=OriginKind.SUBAGENT_TOOL, trigger_id="node-1"),
    )
    run_finish.report_to_its_chat(EngineServices(announce=told), run, RunStatus.COMPLETE)
    assert told.calls == []


# ── the chat hears it in one turn ───────────────────────────────────────────────────────────────


def _gateway() -> Any:
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.sessions = MagicMock()
    orch.ctx_builder = MagicMock()
    session = MagicMock(running=False, task=None, title="Retry ceiling")
    orch.dashboard_state = MagicMock(_sessions={}, _background_tasks=set())
    orch.dashboard_state.get_session = MagicMock(
        side_effect=lambda name: session if name == "chat-a" else None
    )
    orch.dashboard_state.is_yolo_active.return_value = False
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(
            running=[], running_agents_for=MagicMock(return_value=[]), get=MagicMock()
        )
        manager.return_value.get.return_value = None
        orch._init_subagents()
    return orch, session


def _ending(agent_id: str, task: str, *, result: str = "", error: str = "") -> SubagentInfo:
    return SubagentInfo(
        id=agent_id,
        task=task,
        parent_session_key=CHAT_KEY,
        done=True,
        result=result,
        error=error,
        started=time.time() - 5,
    )


@pytest.mark.asyncio
async def test_its_chat_hears_every_task_in_one_turn():
    orch, session = _gateway()
    turns: list[tuple[Any, str]] = []

    async def _turn(_state, chat, message, **_kw):
        turns.append((chat, message))

    with patch("personalclaw.gateway.run_chat", new=AsyncMock(side_effect=_turn)):
        orch._announce_ended_work(
            [
                _ending("sa1", "Raise the retry ceiling", error="the module was not there"),
                _ending("sa2", "Find the retry callers", result="1. src/app/fetch.py:12"),
            ]
        )
        for _ in range(400):
            if turns:
                break
            await asyncio.sleep(0.005)

    assert len(turns) == 1, "the chat did not get one turn for the batch"
    chat, message = turns[0]
    assert chat is session
    assert message.startswith("[Subagent completion batch"), message
    assert "Raise the retry ceiling" in message and "the module was not there" in message
    assert "Find the retry callers" in message and "src/app/fetch.py:12" in message


def _chat_run(session_key: str = CHAT_KEY) -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="subagent-batch-7",
            origin=RunOrigin(kind=OriginKind.SUBAGENT_TOOL, session_key=session_key),
        )
    )
    store.write_spec(run.id, _spec("subagent-batch-7"))
    return run


def test_a_batch_someone_stopped_tells_no_one(home):
    told = _Told()
    services = EngineServices(announce=told)
    run_finish.report_to_its_chat(services, _chat_run(), RunStatus.CANCELLED)
    assert told.calls == [], "a stopped batch was handed to its chat"
    # The control: the same batch failing is told.
    run_finish.report_to_its_chat(services, _chat_run(), RunStatus.FAILED)
    assert len(told.calls) == 1


def test_a_batch_whose_loop_has_ended_tells_no_one(home, monkeypatch):
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    loop = loop_store.create(Loop(id="", name="Release notes", kind="goal", task="tidy the notes"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    told = _Told()
    services = EngineServices(announce=told)
    run = _chat_run(f"dashboard:loop-{loop.id}")

    run_finish.report_to_its_chat(services, run, RunStatus.FAILED)
    assert len(told.calls) == 1, "a running loop's batch was not told how it ended"
    loop_store.update_status(loop.id, LoopStatus.STOPPED)
    run_finish.report_to_its_chat(services, run, RunStatus.FAILED)
    assert len(told.calls) == 1, "a stopped loop's worker was handed its batch's ending"
