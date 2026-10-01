"""What an approval says about a tool that is not a shell: its name's words and its server's labels.

A tool's name describes what a change MAY touch, word by word: ``list_commits`` lists commits and
writes nothing, so "commit" inside it is not a write. A server's MCP annotations are its own
claim about the tool: a read-only label is shown as the server's word ("Server says it only
reads"), and it decides whether the call is a read only when the owner trusts that server's
labels on the Tools page. A destructive label and an open-world label describe what it can touch.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from test_acp_permission_authority import (
    _context_builder,
    _drive,
    _make_state,
    _session,
    _set_stream,
)

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.approval_brief import compose_approval_brief, derive_blast_radius
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.task_modes import resolve_effective_risk
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult


def _writes(tool: str, risk: str = "caution") -> bool:
    radius = derive_blast_radius(tool, risk=risk)
    return bool(radius and radius["writes"])


# ── A name is read word by word ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool",
    [
        "mcp/github/list_commits",
        "mcp/github/listCommits",
        "mcp/notes/get_updates",
        "mcp/files/read_settings",
        "mcp/files/rewrite_check_status",
    ],
)
def test_a_word_inside_another_word_is_not_a_write(tool):
    assert _writes(tool) is False


@pytest.mark.parametrize(
    "tool",
    [
        "mcp/git/git_commit",
        "mcp/github/createIssue",
        "mcp/files/write-file",
        "memory_forget",
        "artifact_delete",
        "task_create",
    ],
)
def test_a_word_that_names_a_change_is_described_as_one(tool):
    assert _writes(tool) is True


def test_a_network_word_is_read_as_a_word():
    assert derive_blast_radius("web_fetch")["network"] is True
    assert derive_blast_radius("mcp/x/httpRequest")["network"] is True
    assert derive_blast_radius("mcp/x/curling_scores") is None


# ── A server's labels ────────────────────────────────────────────────────────────────────────────


def _mcp_permission(tool: str, *, risk_level: str, annotations: dict) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=tool,
        request_id="req-1",
        tool_input=json.dumps({"repo": "example/notes"}),
        risk_level=risk_level,
        annotations=annotations,
    )


def test_an_untrusted_read_label_is_shown_as_the_servers_word():
    brief = compose_approval_brief(
        _mcp_permission(
            "mcp/github/list_commits", risk_level="caution", annotations={"readOnlyHint": True}
        )
    )
    assert brief is not None
    assert brief["blastRadius"] == {
        "writes": False,
        "shell": False,
        "network": False,
        "saysReadOnly": True,
        "readOnly": False,
    }
    assert brief["summary"] == "Server says it only reads · Risk: Caution"


def test_a_servers_read_label_outweighs_a_guess_from_the_name():
    """`get_commit` carries the word "commit"; the server that offers it says it only reads."""
    brief = compose_approval_brief(
        _mcp_permission(
            "mcp/github/get_commit", risk_level="caution", annotations={"readOnlyHint": True}
        )
    )
    assert brief is not None
    assert brief["blastRadius"]["writes"] is False
    assert brief["blastRadius"]["saysReadOnly"] is True


def test_a_trusted_read_label_makes_the_call_a_read():
    brief = compose_approval_brief(
        _mcp_permission(
            "mcp/github/list_commits", risk_level="safe", annotations={"readOnlyHint": True}
        )
    )
    assert brief is not None
    assert brief["blastRadius"]["readOnly"] is True
    assert brief["blastRadius"]["saysReadOnly"] is False
    assert brief["summary"] == "Reads only · Risk: Safe"
    assert resolve_effective_risk("safe", "mcp/github/list_commits", "", {}) == "safe"


def test_a_read_label_is_believed_only_from_a_trusted_server():
    from personalclaw.mcp_client import McpToolSpec, declared_risk

    tool = McpToolSpec(name="list_commits", description="d", annotations={"readOnlyHint": True})
    assert declared_risk("github", tool, trusted=True).value == "safe"
    assert declared_risk("github", tool, trusted=False).value == "caution"


@pytest.mark.parametrize(
    "annotations,facet",
    [
        ({"destructiveHint": True}, "writes"),
        ({"openWorldHint": True}, "network"),
    ],
)
def test_a_servers_other_labels_describe_what_it_can_touch(annotations, facet):
    brief = compose_approval_brief(
        _mcp_permission("mcp/x/frobnicate", risk_level="caution", annotations=annotations)
    )
    assert brief is not None
    assert brief["blastRadius"][facet] is True


def test_a_tools_annotations_reach_the_agents_tool_definitions():
    """The MCP Tool Servers app PersonalClaw ships hands the agent each tool's labels."""
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
    from personalclaw.mcp_client import McpToolSpec

    module = load_bundle_module(NATIVE_DIR / "mcp-tools", "mcp-tools", "provider")

    class _Conn:
        async def list_tools(self):
            return [
                McpToolSpec(
                    name="list_commits", description="d", annotations={"readOnlyHint": True}
                )
            ]

    provider = module.McpToolProvider(lambda: {"github": _Conn()})
    (definition,) = asyncio.run(provider.list_tools())
    assert definition.annotations == {"readOnlyHint": True}


class _OneTool(ToolProvider):
    """One tool that asks before it runs, carrying a server's labels."""

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "MCP Servers"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="mcp/github/list_commits",
                description="List the recent commits of a repository.",
                provider="mcp",
                requires_approval=True,
                annotations={"readOnlyHint": True, "openWorldHint": True},
            )
        ]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="3 commits")


class _CallsIt:
    """A model that calls the tool once, then answers."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c1",
                title="mcp/github/list_commits",
                tool_input='{"repo": "example/notes"}',
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.asyncio
async def test_the_agents_approval_request_carries_the_tools_labels():
    """The labels on its definition reach the request the owner is asked, so the card says them."""
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_CallsIt(),
        tool_providers=[_OneTool()],
    )
    await rt.start()
    asks: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in rt.stream("what changed in the notes repo?"):
            if ev.kind == EVENT_PERMISSION_REQUEST:
                asks.append(ev)
                await rt.reject_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=5)
    (ask,) = asks
    assert ask.annotations == {"readOnlyHint": True, "openWorldHint": True}


# ── End to end: the card says the server's word, and Trust reads still asks ──────────────────────


@pytest.mark.asyncio
async def test_trust_reads_asks_for_an_untrusted_read_label_and_shows_it(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=False)
    session._trust_reads = True
    event = _mcp_permission(
        "mcp/github/list_commits", risk_level="caution", annotations={"readOnlyHint": True}
    )
    _set_stream(client, [event, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    await _drive(state, session, answer="denied")

    client.approve_tool.assert_not_awaited()
    (card,) = [m for m in session.messages if m.get("role") == "permission"]
    meta = json.loads(card["cls"])
    assert meta["risk"] == "caution"
    assert meta["blast_radius"]["saysReadOnly"] is True
    assert meta["blast_radius"]["writes"] is False
