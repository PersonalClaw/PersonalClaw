"""A tool call's arguments are checked against the schema its tool declares, and nothing else.

Two checks run before a call: the native runtime's, for an argument its tool requires, before
anyone is asked (``NativeAgentRuntime._preflight``), and an MCP server tool's, for an argument of
a type its schema does not declare (``argument_refusal``, which the MCP tools' pre-flight reads
through ``personalclaw.sdk.mcp``). Each reads the input schema it is handed. A reference inside
that schema (``#/$defs/...``) is followed. One that names any other document is never opened, and
the check then refuses nothing, as for any schema it cannot read: the call goes on to be asked
about and checked by the tool itself.

No network and no document outside the test's own folder: the standard library's opener is
stubbed to record and refuse, and each row says it was never called.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

import pytest
from test_a_call_its_tool_would_refuse_is_not_put_to_the_owner import (
    _DONE,
    _asks,
    _Model,
    _results,
)

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.sdk.mcp import argument_refusal
from personalclaw.tool_providers.arguments import missing_arguments
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

_TOOL = "mcp/notes/add_note"
FORMS = ["file", "web"]


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    """Every document anything tries to open through the standard library's opener; it opens
    none."""
    tried: list[str] = []

    def _urlopen(request, *args, **kwargs):
        tried.append(getattr(request, "full_url", str(request)))
        raise OSError("nothing is opened in this test")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return tried


@pytest.fixture
def outside(tmp_path) -> dict[str, str]:
    """Two documents a schema may name: a file in the test's own folder, which requires an
    argument and types it, and a web address."""
    doc = tmp_path / "note.json"
    doc.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["owner"],
                "properties": {"owner": {"type": "string"}},
            }
        ),
        encoding="utf-8",
    )
    return {"file": doc.as_uri(), "web": "https://schemas.example.com/note.json"}


# ── the native runtime's check, before anyone is asked ─────────────────────────────────────────


def _offered(where: str) -> dict:
    """A schema the native runtime offers to models whose root names *where*."""
    return {
        "type": "object",
        "$dynamicRef": where,
        "properties": {"note": {"type": "object", "properties": {"title": {"type": "string"}}}},
        "required": ["note"],
    }


class _Notes(ToolProvider):
    """One tool that asks before it runs, with the schema *schema*."""

    def __init__(self, schema: dict) -> None:
        self.schema = schema
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
                description="Add a note.",
                provider="mcp",
                parameters=self.schema,
                requires_approval=True,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        return ToolResult(success=True, output="added")


async def _turn(schema: dict, arguments: str) -> tuple[list[AgentEvent], _Notes]:
    """One native turn that calls the tool with *arguments*, approving whatever is asked."""
    tool = _Notes(schema)
    call = [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title=_TOOL, tool_input=arguments),
        AgentEvent(kind=EVENT_COMPLETE),
    ]
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([call, _DONE]),
        tool_providers=[tool],
    )
    await rt.start()
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in rt.stream("add a note"):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await rt.approve_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=5)
    return seen, tool


@pytest.mark.asyncio
@pytest.mark.parametrize("form", FORMS)
async def test_a_native_turns_check_opens_no_other_document(opened, outside, form) -> None:
    """The call is asked about and runs as it did, and nothing is opened to decide."""
    seen, tool = await _turn(_offered(outside[form]), '{"note": {}}')

    assert opened == []
    assert len(_asks(seen)) == 1
    assert tool.invoked == [{"note": {}}]
    assert len(_results(seen)) == 1


@pytest.mark.asyncio
async def test_a_native_turns_check_still_reads_a_reference_inside_the_schema(opened) -> None:
    """The control: a reference inside the schema is followed, and what it requires is asked
    for before anyone is asked."""
    schema = {
        "type": "object",
        "$defs": {
            "note": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            }
        },
        "properties": {"note": {"$ref": "#/$defs/note"}},
        "required": ["note"],
    }
    seen, tool = await _turn(schema, '{"note": {}}')

    assert _asks(seen) == [] and tool.invoked == []
    assert "'title' is a required property" in str(_results(seen)[0].tool_output)
    assert missing_arguments({"note": {"title": "Seeds"}}, schema) == []
    assert opened == []


# ── an MCP server tool's check, through the SDK ────────────────────────────────────────────────


def _naming(where: str) -> dict:
    return {"type": "object", "properties": {"note": {"$ref": where}}, "required": ["note"]}


@pytest.mark.parametrize("form", FORMS)
def test_an_mcp_tools_check_opens_no_other_document(opened, outside, form) -> None:
    assert argument_refusal(_TOOL, {"note": {"owner": 7}}, _naming(outside[form])) == ""
    assert opened == []


def test_an_mcp_tools_check_still_reads_a_reference_inside_the_schema(opened) -> None:
    """The control: a type a reference inside the schema declares is held to it."""
    schema = {
        "type": "object",
        "$defs": {"note": {"type": "object", "properties": {"title": {"type": "string"}}}},
        "properties": {"note": {"$ref": "#/$defs/note"}},
    }

    assert "note/title: 7 is not of type 'string'" in argument_refusal(
        _TOOL, {"note": {"title": 7}}, schema
    )
    assert argument_refusal(_TOOL, {"note": {"title": "Seeds"}}, schema) == ""
    assert opened == []
