"""A tool approval for a call that arrives without an id gets an id of its own.

The native runtime names a call's approval by the call's id, and a model may send its tool calls
with none. The approval was then named by the tool alone, so every such ask of one tool carried the
same id. Now a call with no id of its own gets one, and a call the model named keeps its name.

Driven through two native runtimes, each asked to make one call of the same tool with no id, both
held at their approval at once.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

pytestmark = pytest.mark.security

_TOOL = "mcp/notes/append"


class _Model:
    """Replays scripted turns, one per inference."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


class _Notes(ToolProvider):
    """One tool that asks before it runs."""

    def __init__(self) -> None:
        self.invoked: list[dict] = []

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "MCP Servers"

    async def list_tools(self):
        return [
            ToolDefinition(
                name=_TOOL,
                description="Append a line to a note.",
                provider="mcp",
                parameters={"type": "object", "properties": {"line": {"type": "string"}}},
                requires_approval=True,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        return ToolResult(success=True, output="ok")


def _turns(call_id: str, line: str) -> list[list[AgentEvent]]:
    call = AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id=call_id, title=_TOOL, tool_input={"line": line}
    )
    done = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]
    return [[call, AgentEvent(kind=EVENT_COMPLETE)], done]


async def _held(
    call_id: str, line: str
) -> tuple[NativeAgentRuntime, _Notes, AgentEvent, asyncio.Task]:
    """One chat's runtime, streamed until its call waits at its approval."""
    tool = _Notes()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model(_turns(call_id, line)),
        tool_providers=[tool],
    )
    await rt.start()
    asked: asyncio.Future[AgentEvent] = asyncio.get_running_loop().create_future()

    async def pump() -> None:
        async for ev in rt.stream("note it"):
            if ev.kind == EVENT_PERMISSION_REQUEST and not asked.done():
                asked.set_result(ev)

    task = asyncio.create_task(pump())
    return rt, tool, await asyncio.wait_for(asked, timeout=5), task


@pytest.mark.asyncio
async def test_two_calls_of_one_tool_with_no_ids_wait_under_ids_of_their_own() -> None:
    first_rt, first_tool, first, first_turn = await _held("", "first")
    second_rt, second_tool, second, second_turn = await _held("", "second")

    assert first.request_id and second.request_id
    assert first.request_id != second.request_id, "one answer would reach both calls"

    assert await first_rt.approve_tool(first.request_id) is not False
    await asyncio.wait_for(first_turn, timeout=5)
    assert first_tool.invoked == [{"line": "first"}]
    assert second_tool.invoked == [], "the answer for the first call ran nothing else"

    await second_rt.reject_tool(second.request_id)
    await asyncio.wait_for(second_turn, timeout=5)
    assert second_tool.invoked == []


@pytest.mark.asyncio
async def test_a_call_the_model_named_keeps_its_id() -> None:
    rt, _tool, asked, turn = await _held("call_42", "x")

    assert asked.request_id == "call_42"
    await rt.reject_tool(asked.request_id)
    await asyncio.wait_for(turn, timeout=5)
