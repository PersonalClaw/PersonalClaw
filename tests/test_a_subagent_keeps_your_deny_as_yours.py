"""A subagent keeps a call its owner declined as hers, not as one its tools could not make.

A workflow stage, a trigger's agent and a batch's task each run as a subagent, and a call it may not
make on its own asks its owner. Her Deny used to be tallied as one more refusal ("it was declined")
and then dropped: it never reached what the subagent ended with, so the stage settled done with no
trace of it, and a subagent whose only call she declined ended "Couldn't do its task: every tool
call it made was refused", with the remedy to give the step more tools. Now her Deny is kept as
hers (`SubagentInfo.declined_calls`, in the shape `declined_calls.declined_step` keeps it), and only
hers: an ask nobody answered in time is still refused for that, and one there was nowhere to put to
her is refused by the run's own limits.

Driven through the REAL `SubagentManager` and `NativeAgentRuntime`, with a scripted model and the
relay's answer as the gateway gives it (`approval_grants.ToolDecision`).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw import approval_grants
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.approval_grants import ToolDecision
from personalclaw.config import AppConfig
from personalclaw.declined_calls import declined_step, named, said
from personalclaw.hooks import HookManager
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.session import SessionManager
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

WRITE = "write_note"
READ = "read_note"
WROTE = {"tool": WRITE, "names": ["notes/plan.md", "# Plan"]}


class _Tools(ToolProvider):
    """A write that asks before it runs, and a read that does not; records what ran."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "probe"

    @property
    def display_name(self) -> str:
        return "Probe"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=WRITE, description="d", parameters={"type": "object"}, requires_approval=True
            ),
            ToolDefinition(
                name=READ, description="d", parameters={"type": "object"}, risk_level=RiskLevel.SAFE
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output="done")


def _model(*tools: str) -> _ScriptedModel:
    """A model that calls each of *tools* in turn, then answers."""
    inputs = {WRITE: '{"path": "notes/plan.md", "content": "# Plan\\nWeek one"}', READ: "{}"}
    script: list[list[AgentEvent]] = [
        [
            AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id=f"call-{n}", title=tool, tool_input=inputs[tool]
            ),
            AgentEvent(kind=EVENT_COMPLETE),
        ]
        for n, tool in enumerate(tools, 1)
    ]
    script.append([AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)])
    return _ScriptedModel(script)


async def _ran(*tools: str, answer: ToolDecision) -> tuple[SubagentInfo, _Tools]:
    probe = _Tools()

    def factory(_key: Any = None, **kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=_model(*tools),
            tool_providers=[probe],
            unattended=bool(kw.get("unattended")),
        )

    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks = HookManager()
    manager = SubagentManager(
        sessions=SessionManager(AppConfig(), provider_factory=factory),
        ctx_builder=ctx,
        on_tool_approval=AsyncMock(return_value=answer),
        is_yolo=lambda: False,
    )
    info = SubagentInfo(id="sa-1", task="draft the plan", parent_session_key="workflow:run-7:work")
    await asyncio.wait_for(manager._run_inner(info, f"subagent:{info.id}"), timeout=20)
    return info, probe


def _deny() -> ToolDecision:
    return ToolDecision(False, "rejected", approval_grants.YOU)


@pytest.mark.asyncio
async def test_the_one_call_you_declined_is_yours_not_a_run_that_could_do_nothing() -> None:
    """🔴 Before: "Couldn't do its task: every tool call it made was refused — write_note: it was
    declined", and nothing of her Deny on what the subagent ended with."""
    info, probe = await _ran(WRITE, answer=_deny())
    assert probe.ran == []
    assert info.done and info.error == "", info.error
    assert info.declined_calls == [WROTE]
    assert info.refused == []


@pytest.mark.asyncio
async def test_a_run_that_did_other_work_carries_what_you_declined() -> None:
    info, probe = await _ran(READ, WRITE, answer=_deny())
    assert probe.ran == [READ]
    assert info.error == ""
    assert info.declined_calls == [WROTE]


@pytest.mark.asyncio
async def test_an_ask_nobody_answered_is_not_yours() -> None:
    info, _ = await _ran(WRITE, answer=ToolDecision(False, "expired", approval_grants.NOBODY))
    assert info.declined_calls == []
    assert info.error.endswith(f"{WRITE}: nobody answered it in time"), info.error


@pytest.mark.asyncio
async def test_an_ask_with_nowhere_to_go_is_refused_by_the_runs_own_limits() -> None:
    """Nowhere to ask her (no dashboard, no channel) is no Deny of hers: it is refused as a call
    nobody could approve, which its trigger's history records as refused."""
    info, _ = await _ran(WRITE, answer=ToolDecision(False, "rejected", approval_grants.NO_SURFACE))
    assert info.declined_calls == []
    assert info.refused == [WRITE]
    assert info.error.endswith(f"{WRITE}: nobody could approve it"), info.error


def test_the_record_and_how_a_sentence_names_it() -> None:
    step = declined_step("write_file", '{"path": "notes/plan.md", "content": "# Plan\\nmore"}')
    assert step == {"tool": "write_file", "names": ["notes/plan.md", "# Plan"]}
    assert named(step) == "write_file (notes/plan.md)"
    # A tool whose own name says what it touches is not said twice.
    assert named(declined_step("Write notes/plan.md", {"path": "notes/plan.md"})) == (
        "Write notes/plan.md"
    )
    bash = declined_step("bash", {"command": "rm -rf build\necho done"})
    assert said([step]) == "write_file (notes/plan.md)"
    assert said([step, bash]) == "write_file (notes/plan.md) and bash (rm -rf build)"
    assert said([step, step, bash]) == "write_file (notes/plan.md) and bash (rm -rf build)"
    many = [declined_step(f"tool_{n}", {}) for n in range(4)]
    assert said(many) == "tool_0, tool_1 and 2 more"
