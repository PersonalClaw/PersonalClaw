"""A tool call missing an argument its tool requires is answered before anyone is asked about it.

The owner was asked, over a chat channel, to approve a notes server's `search_files` call that
lacked its required `path`. Each approved call then failed at the server's own input check, and each
identical retry asked again. The runtime holds every tool's declared input schema, so a call that
lacks what it requires is told so, with the schema, and never reaches an approval. The check is the
runtime's, so it holds for every surface the runtime serves (a chat channel, the web chat, a
subagent).

Only a missing required argument is refused, the one failure every tool that declares the schema
refuses. The check reads the schema the TOOL declares, not the portable copy a model request
carries; any other mismatch (a type a lax server coerces, an extra key, a value its pattern
decides) is left to the tool, as before.
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
    EVENT_TOOL_RESULT,
    TOOL_META_NOT_RUN,
    AgentEvent,
    unasked_outcome,
)
from personalclaw.tool_providers.arguments import missing_arguments
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

_SEARCH = "mcp/notes/search_files"

#: A notes server's search tool, declared as a typical MCP server declares it: both arguments
#: required, an optional depth, an optional nullable limit, and a pattern the server enforces.
_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "The folder to search."},
        "pattern": {"type": "string", "pattern": "^[^/]+$"},
        "depth": {"type": "integer"},
        "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
    },
    "required": ["path", "pattern"],
    "additionalProperties": False,
}


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
    """One tool that asks before it runs, with the schema above."""

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
                name=_SEARCH,
                description="Search the notes for files matching a pattern.",
                provider="mcp",
                parameters=_SEARCH_SCHEMA,
                requires_approval=True,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        return ToolResult(success=True, output="daily/2026-09-29.md")


def _call(cid: str, args: str) -> list[AgentEvent]:
    return [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=cid, title=_SEARCH, tool_input=args),
        AgentEvent(kind=EVENT_COMPLETE),
    ]


_DONE = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]


async def _run(turns: list[list[AgentEvent]]) -> tuple[list[AgentEvent], _Notes]:
    """Drive one turn, approving whatever is asked, and return every event it streamed."""
    tool = _Notes()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model(turns),
        tool_providers=[tool],
    )
    await rt.start()
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in rt.stream("what is on my plate today?"):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await rt.approve_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=5)
    return seen, tool


def _asks(seen: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in seen if e.kind == EVENT_PERMISSION_REQUEST]


def _results(seen: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in seen if e.kind == EVENT_TOOL_RESULT]


@pytest.mark.asyncio
async def test_a_call_missing_a_required_argument_is_answered_without_asking():
    """The identical call, twice: neither asks, neither runs, both say what is missing."""
    seen, tool = await _run(
        [_call("c1", '{"pattern": "*daily*"}'), _call("c2", '{"pattern": "*daily*"}'), _DONE]
    )

    assert _asks(seen) == []
    assert tool.invoked == []
    results = _results(seen)
    assert len(results) == 2
    for result in results:
        text = str(result.tool_output)
        assert text.startswith(f"Error: {_SEARCH} was not run")
        assert "- 'path' is a required property" in text
        # The schema rides the answer, so the model can correct the call without another lookup.
        assert '"required": ["path", "pattern"]' in text
        assert result.tool_meta.get("ok") is False
        assert result.tool_meta.get(TOOL_META_NOT_RUN) == "missing_arguments"
        # Audited as a call that could not be run, not as one a gate refused or someone approved.
        assert unasked_outcome(result.tool_meta) == "failed"


@pytest.mark.asyncio
async def test_a_call_that_has_what_it_requires_still_asks_and_runs():
    """The control: a complete call is put to the owner as before, and runs once approved."""
    seen, tool = await _run([_call("c1", '{"path": "Daily", "pattern": "*daily*"}'), _DONE])

    assert len(_asks(seen)) == 1
    assert tool.invoked == [{"path": "Daily", "pattern": "*daily*"}]
    assert _results(seen)[0].tool_meta.get("ok") is not False


@pytest.mark.asyncio
async def test_what_only_the_tool_can_judge_is_left_to_it():
    """A null for the nullable limit (the portable copy types it a bare integer), a depth sent as
    text (the MCP client turns it back into a number), a value the server's pattern decides, and a
    key the closed schema does not name (a generated schema closes every object for a server that
    drops such a key): each call is asked about and runs, as before."""
    seen, tool = await _run(
        [
            _call("c1", '{"path": "Daily", "pattern": "a/b", "limit": null}'),
            _call("c2", '{"path": "Daily", "pattern": "x", "depth": "5"}'),
            _call("c3", '{"path": "Daily", "pattern": "x", "recursive": true}'),
            _DONE,
        ]
    )

    assert len(_asks(seen)) == 3
    assert len(tool.invoked) == 3


def test_a_missing_nested_argument_is_named_where_it_is_missing():
    schema = {
        "type": "object",
        "properties": {
            "note": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                "required": ["title"],
            }
        },
        "required": ["note"],
    }

    assert missing_arguments({"note": {"body": "x"}}, schema) == [
        "note: 'title' is a required property"
    ]
    assert missing_arguments({"note": {"title": "t"}}, schema) == []


def test_a_type_mismatch_is_not_refused_here():
    """A lax server takes "true" for a boolean, and a built-in tool takes an object where it
    declares JSON text: only the tool can say whether it runs."""
    assert missing_arguments({"path": 7, "pattern": {"glob": "x"}}, _SEARCH_SCHEMA) == []


def test_a_schema_that_cannot_be_checked_refuses_nothing():
    """An unreadable schema, or one pointing somewhere this process will not fetch, is left to
    the tool: asking the owner is what happened before, and nothing is fetched to decide."""
    assert missing_arguments({}, {"type": "object", "required": "path"}) == []
    remote = {
        "type": "object",
        "properties": {"path": {"$ref": "https://schemas.example.com/path.json"}},
    }
    assert missing_arguments({"path": "x"}, remote) == []
    assert missing_arguments({"anything": 1}, {}) == []
    assert missing_arguments({"anything": 1}, None) == []
