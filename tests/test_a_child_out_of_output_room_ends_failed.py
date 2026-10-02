"""A subagent, a workflow step and a chat turn whose model ran out of output room end failed, and
say why; a subagent's live state names the runtime and the calls that serve it.

Measured: a loop delegated an audit to two review subagents on PersonalClaw's own runtime. Their
model stopped at 8,192 output tokens on every call with nothing written, and each ended
"completed" with "_No response._", so the loop's worker learned only that the reviewer "returned
nothing". While they ran, their ``state.json`` read provider "acp", 0 turns and no last tool.

Now the subagent ends "Couldn't do its task: the model ran out of output room before it answered
(8,192 tokens)…", its workflow step fails with that cause (and a fix about output room, not
tools), a chat turn ends in the notice that says so, and ``state.json`` names the native runtime,
its model and each call as it happens. Scripted models only.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_a_turn_with_no_answer_says_so import _ask_in_the_dashboard, _gateway, _turn_rows

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import AppConfig
from personalclaw.hooks import HookManager
from personalclaw.ledger import STEP_COMPLETED, STEP_FAILED
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TOOL_CALL,
    STOP_MAX_TOKENS,
    AgentEvent,
)
from personalclaw.session import SessionManager
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.subagent_persistence import create_agent_folder, read_state
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from personalclaw.workflows import store
from personalclaw.workflows.batch_compile import LeafTask, compile_batch
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.journal import ledger
from personalclaw.workflows.models import FailureClass, InstanceState, RunStatus, WorkflowRun

CAP = 8_192
OUT_OF_ROOM = "the model ran out of output room before it answered (8,192 tokens)"


class _Model:
    """Calls ``look`` once, then spends its whole output cap on nothing, every time."""

    supports_tools = True
    _model = "m-1"
    served_ref = "fake-cloud:m-1"

    def __init__(self) -> None:
        self.requests = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests += 1
        if self.requests == 1:
            yield AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="look", tool_input="{}")
            yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="tool_use", output_tokens=30)
            return
        yield AgentEvent(
            kind=EVENT_COMPLETE, stop_reason=STOP_MAX_TOKENS, input_tokens=39_311, output_tokens=CAP
        )

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Look(ToolProvider):
    """A read; while it runs it reads the agent's ``state.json``, which is what a person sees."""

    def __init__(self) -> None:
        self.state_seen: list[dict] = []
        self.agent_id = ""

    @property
    def name(self) -> str:
        return "look"

    @property
    def display_name(self) -> str:
        return "Look"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="look",
                description="Read something.",
                parameters={"type": "object"},
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        if self.agent_id:
            self.state_seen.append(dict(read_state(self.agent_id) or {}))
        return ToolResult(success=True, output="notes.md: 3 lines changed")


@pytest.fixture
def agent_root(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0))
    return tmp_path


def _manager(look: _Look, *, starts_unasked: bool = False) -> SubagentManager:
    def factory(_key: Any = None, **kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="reviewer", provider="native", model="m-1"),
            model_provider=_Model(),
            tool_providers=[look],
            unattended=bool(kw.get("unattended")),
        )

    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks = HookManager()
    return SubagentManager(
        sessions=SessionManager(AppConfig(), provider_factory=factory),
        ctx_builder=ctx,
        # A workflow's steps start without asking, as an owner's Allow of the run lets them.
        is_yolo=lambda: starts_unasked,
    )


async def _run(look: _Look, agent_id: str = "sa-room") -> SubagentInfo:
    manager = _manager(look)
    info = SubagentInfo(id=agent_id, task="review the diff", parent_session_key="dashboard:chat-a")
    create_agent_folder(info.id, task=info.task)
    look.agent_id = info.id
    with patch("personalclaw.subagent.Stats"):
        await asyncio.wait_for(manager._run_inner(info, f"subagent:{info.id}"), timeout=20)
    return info


# ── the subagent ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_subagent_out_of_room_ends_failed_and_says_why(agent_root):
    """🔴 Before: no error, so it ended "completed" with "_No response._" as its result."""
    info = await _run(_Look())

    assert info.done
    assert info.error.startswith(f"Couldn't do its task: {OUT_OF_ROOM}"), info.error
    tombstone = json.loads((agent_root / info.id / "tombstone.json").read_text())
    assert tombstone["cause"] == "output_cap"


@pytest.mark.asyncio
async def test_its_live_state_names_the_runtime_its_model_and_each_call(agent_root):
    """🔴 Before: provider "acp", turns 0 and last_tool "" while it was making calls."""
    look = _Look()
    await _run(look)

    (live,) = look.state_seen
    assert live["provider"] == "native", live
    assert live["model"] == "fake-cloud:m-1", live
    assert (live["turns"], live["last_tool"]) == (1, "look"), live


@pytest.mark.asyncio
async def test_its_budget_counts_each_call_its_runtime_makes(agent_root):
    """``max_turns`` is a spawn's tool-call budget; a native run's calls never reached it."""
    look = _Look()
    manager = _manager(look)
    info = SubagentInfo(id="sa-budget", task="t", parent_session_key="dashboard:c", max_turns=0)
    create_agent_folder(info.id, task=info.task)
    look.agent_id = info.id
    with (
        patch.object(SubagentManager, "_default_turn_limit", 0),
        patch("personalclaw.subagent._TURN_LIMIT", 0),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent_tier.Stats"),
    ):
        await asyncio.wait_for(manager._run_inner(info, f"subagent:{info.id}"), timeout=20)
    assert info.error == "turn_limit:0"
    assert look.state_seen == [], "the call past the budget ran"


# ── the workflow step it ran for ──────────────────────────────────────────────────────────────


def _leaf(task: str) -> LeafTask:
    return LeafTask(
        task=task,
        objective="decide whether the change is safe for every output path",
        output_format="a numbered list of findings with file and line",
        boundary="read only; do not commit or push anything",
    )


@pytest.mark.asyncio
async def test_its_step_fails_for_want_of_output_room_and_the_ledger_says_so(
    agent_root, tmp_path, monkeypatch
):
    """🔴 Before: each step read done with "_No response._", and the batch read complete."""
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    compiled = compile_batch([_leaf("review the fix"), _leaf("review the test")])
    assert compiled.ok, compiled.findings
    spec = {"name": "subagent-batch-1", "root": compiled.spec["root"]}
    run = store.create(WorkflowRun(id="", workflow_name="subagent-batch-1"))
    store.write_spec(run.id, spec)
    manager = _manager(_Look(), starts_unasked=True)
    controller = RunController(run, spec, services=EngineServices(subagents=manager))
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
    ):
        status = await controller.run_to_completion(timeout=30.0)

    leaves = [controller.instances[f"root.children[{i}]"] for i in range(2)]
    for leaf in leaves:
        assert leaf.state is InstanceState.FAILED, leaf.state
        assert leaf.failure is not None
        assert leaf.failure.failure_class is FailureClass.USER, leaf.failure.to_dict()
        assert OUT_OF_ROOM in leaf.failure.cause_plain
        assert "output limit" in leaf.failure.remediation, leaf.failure.remediation
    assert status is RunStatus.FAILED, status
    kinds = [rec.get("kind") for rec in ledger(run.id)]
    assert kinds.count(STEP_FAILED) == 2 and STEP_COMPLETED not in kinds, kinds


# ── a chat turn ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_turn_out_of_room_ends_in_the_notice_and_is_not_resent(tmp_path):
    from personalclaw.llm.events import out_of_room_notice

    model = _Model()
    gateway, sessions = await _gateway(tmp_path, model)  # type: ignore[arg-type]
    state = gateway.dashboard_state
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            chat = state.get_or_create_session()
            await _ask_in_the_dashboard(state, chat)
    finally:
        await sessions.close_all()

    assert model.requests == 3, "the capped silence was not asked again, or asked twice"
    notice = out_of_room_notice(CAP)
    assert notice.startswith("The model ran out of output room before it answered (8,192 tokens)")
    rows = _turn_rows(chat)
    assert [m["content"] for m in rows if m["role"] == "error"] == [notice]
    assert chat._last_turn_outcome == "error"
    assert len([m for m in chat.messages if m["role"] == "user"]) == 1, "it was resent"


def test_the_notice_names_no_number_it_was_not_told():
    from personalclaw.llm.events import out_of_room_notice

    assert "tokens" not in out_of_room_notice(0)
