"""A read-only run is told which MCP servers' reads it was not shown, and where they are trusted.

A read-only subagent is shown only the tools some call of which its tier admits. A tool of an MCP
server whose read-only labels the owner has not trusted counts as a change whatever it says
(`tool_providers.base.risk_from_annotations`), so such a server's reads were left out of a reading
run's tools with nothing said: the subagent sent to read the owner's notes reported it had no way
to, and nobody learned that one setting stood in the way. The run is now told, beside its tools,
which servers' tools say they only read and were not shown for that, and where the owner trusts
them. A server whose labels are trusted is shown, and a tool that does not say it reads is left out
as before, with no such note.

Driven through PersonalClaw's own loop behind the shipped `SubagentManager`, with a scripted model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.hooks import TOOL_ALLOW, ToolHookResult
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.subagent import SubagentManager
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path / "home")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(
        "personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "agents"
    )
    # The owner trusts one server's read-only labels, and not the other's.
    monkeypatch.setattr(
        "personalclaw.mcp_client.read_only_labels_trusted", lambda server: server == "wiki"
    )
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


class _ScriptedModel:
    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.tools_seen: list[list[str]] = []
        self.messages_seen: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.tools_seen.append([t["function"]["name"] for t in tools or []])
        self.messages_seen.append(list(messages))
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Here is what I could read.")
        yield AgentEvent(kind=EVENT_COMPLETE)


def _tool(name: str, *, says_it_reads: bool, believed: bool) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"The {name} tool.",
        provider="mcp",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        risk_level=RiskLevel.SAFE if (says_it_reads and believed) else RiskLevel.CAUTION,
        annotations={"readOnlyHint": True} if says_it_reads else {},
    )


class _Servers(ToolProvider):
    """Two MCP servers' tools as the MCP adapter lists them: their labels as sent, and what each is
    taken to do (a read-only label believed only from a server the owner trusts)."""

    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = tools

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "MCP Servers"

    async def list_tools(self):
        return list(self._tools)

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="ok")


class _Sessions:
    def __init__(self, workspace: Path, model: _ScriptedModel, provider: ToolProvider) -> None:
        self.workspace, self.model, self.provider = workspace, model, provider

    async def get_or_create(self, key, agent=None, **kwargs):
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name=agent or "", provider="native"),
            model_provider=self.model,
            tool_providers=[self.provider],
            cwd=self.workspace,
            session_key=key,
        )
        await runtime.start()
        if kwargs.get("approval_source") is not None:
            runtime.set_approval_source(kwargs["approval_source"])
        return runtime, True, False

    def get_pid(self, _key):
        return None

    def get_agent(self, _key):
        return ""

    def has_session(self, _key):
        return False

    def get_approval_policy(self, _key):
        return ""

    def record_success(self, _key):
        pass

    def release(self, _key, *, cleanup=False):
        pass

    async def reset(self, _key):
        pass


def _drive(tmp_path: Path, tools: list[ToolDefinition]) -> _ScriptedModel:
    """One read-only subagent nobody is asked about, run to its end over *tools*."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    model = _ScriptedModel()
    sessions = _Sessions(workspace, model, _Servers(tools))
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("Read my notes on retries.", None))
    ctx.hooks.on_tool_call = MagicMock(return_value=ToolHookResult(action=TOOL_ALLOW))
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.hooks.auto_approve_subagent_tools = False
    manager = SubagentManager(sessions=sessions, ctx_builder=ctx)

    async def _go():
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
        ):
            info = manager.spawn(
                "Read my notes on retries.", parent_session_key="", approval_mode="auto"
            )
            assert info is not None and not info.error, info
            await manager._tasks[info.id]

    asyncio.run(_go())
    return model


def _what_it_was_told(model: _ScriptedModel) -> str:
    return "\n".join(json.dumps(m.get("content", "")) for m in model.messages_seen[0])


def test_it_is_told_which_servers_reads_it_was_not_shown_and_where_to_trust_them(tmp_path):
    model = _drive(
        tmp_path,
        [
            _tool("mcp/notes-vault/read_note", says_it_reads=True, believed=False),
            _tool("mcp/notes-vault/write_note", says_it_reads=False, believed=False),
            _tool("mcp/wiki/search", says_it_reads=True, believed=True),
        ],
    )

    offered = model.tools_seen[0]
    assert "mcp_wiki_search" in offered, offered
    assert not any("notes-vault" in name for name in offered), offered
    told = _what_it_was_told(model)
    assert "notes-vault" in told, told
    assert "Tools page" in told and "read-only labels" in told, told
    assert "wiki" not in told.replace("mcp_wiki_search", ""), "a trusted server was named"


def test_a_server_whose_tools_do_not_say_they_read_is_not_named(tmp_path):
    model = _drive(
        tmp_path,
        [
            _tool("mcp/notes-vault/write_note", says_it_reads=False, believed=False),
            _tool("mcp/wiki/search", says_it_reads=True, believed=True),
        ],
    )

    assert "notes-vault" not in _what_it_was_told(model)
