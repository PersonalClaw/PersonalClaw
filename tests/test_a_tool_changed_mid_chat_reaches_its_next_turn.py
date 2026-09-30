"""A tool installed, removed or switched off while a chat is open reaches that chat's next turn.

An agent runtime lives as long as its chat does, and it built its tool catalog once, when its
first turn started. Install Web Tools while a chat is open and that chat went on answering from a
catalog with no ``web_search`` in it, with nothing saying so, while a new chat searched. The
other direction was worse: a tool whose app was removed, or which was switched off on the Tools
page, stayed on offer in every open chat, and a call to it still ran the removed app's code.

Now the runtime checks, as each turn starts, whether the tools it can use have changed since its
last turn (an app's tools registered or withdrawn, a tool switched off or on, an MCP server added
or removed), and rebuilds its catalog when they have. And a call to a tool whose app left the
surface while the turn was under way is refused, never run.

Every runtime here is built by the real native factory (``provider_bridge``), over the real
registry an app's tools register into.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, EVENT_TOOL_RESULT, AgentEvent
from personalclaw.providers import provider_bridge as pb
from personalclaw.tool_providers import registry as tool_registry
from personalclaw.tool_providers import tool_prefs
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult


class _AppTools(ToolProvider):
    """An installed app's tool provider: one read-only tool, and every call it received."""

    def __init__(self, provider: str, tool: str) -> None:
        self._provider = provider
        self._tool = tool
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._provider

    @property
    def display_name(self) -> str:
        return self._provider

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self._tool,
                description=f"{self._tool} does its one thing",
                provider=self._provider,
                parameters={"type": "object", "properties": {}},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append(arguments)
        return ToolResult(success=True, output=f"{tool_name} ran")


class _Chat:
    """A model for a chat's turns: each turn either answers, or calls one tool and then answers.

    Records the tool names each request offered."""

    supports_tools = True
    _model = "m"

    def __init__(self) -> None:
        self.offered: list[list[str]] = []
        self._script: list[list[AgentEvent]] = []
        self.before_call: Any = None

    def next_turn(self, call: str | None) -> None:
        if call is None:
            self._script = [
                [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)]
            ]
        else:
            self._script = [
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL, tool_call_id="c1", title=call, tool_input="{}"
                    ),
                    AgentEvent(kind=EVENT_COMPLETE),
                ],
                [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
            ]

    async def complete(self, messages, *, tools=None, **_: Any):
        self.offered.append(
            [
                str((t.get("function", t) if isinstance(t, dict) else {}).get("name", ""))
                for t in tools or []
            ]
        )
        step = self._script.pop(0) if self._script else [AgentEvent(kind=EVENT_COMPLETE)]
        if self.before_call is not None and any(e.kind == EVENT_TOOL_CALL for e in step):
            hook, self.before_call = self.before_call, None
            hook()
        for event in step:
            yield event


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})
    monkeypatch.setattr(tool_registry, "_registrations", {})
    monkeypatch.setattr(tool_registry, "_claims", {})
    return home


async def _open_chat(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _Chat]:
    """A chat's runtime, from the real native factory, started as a chat starts it."""
    chat = _Chat()
    monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: chat)
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **k: "m")
    runtime = pb._build_native_runtime(
        use_case="chat",
        session_key="dashboard:chat-1",
        agent=None,
        model_override=None,
        cwd=None,
    )
    runtime.set_approval_policy("yolo")
    await runtime.start()
    return runtime, chat


async def _turn(runtime: Any, chat: _Chat, call: str | None = None) -> list[str]:
    """One turn of the open chat. Returns what each tool call answered."""
    chat.next_turn(call)
    events = [event async for event in runtime.stream("go")]
    return [str(e.tool_output or "") for e in events if e.kind == EVENT_TOOL_RESULT]


@pytest.mark.asyncio
async def test_a_tool_installed_while_a_chat_is_open_is_offered_on_its_next_turn(home, monkeypatch):
    runtime, chat = await _open_chat(monkeypatch)
    await _turn(runtime, chat)
    assert "web_search" not in chat.offered[-1]

    web = _AppTools("personalclaw-web", "web_search")
    tool_registry.register_provider(web, app="web-tools")
    answers = await _turn(runtime, chat, "web_search")

    # A turn that calls a tool makes two requests; the first is where the tool is offered.
    assert "web_search" in chat.offered[-2], "the next turn was not offered the new tool"
    assert answers == ["web_search ran"]
    assert web.calls == [{}]


@pytest.mark.asyncio
async def test_a_tool_whose_app_was_removed_is_gone_from_the_next_turn(home, monkeypatch):
    web = _AppTools("personalclaw-web", "web_search")
    tool_registry.register_provider(web, app="web-tools")
    runtime, chat = await _open_chat(monkeypatch)
    await _turn(runtime, chat)
    assert "web_search" in chat.offered[-1]

    tool_registry.unregister_provider(web)
    answers = await _turn(runtime, chat, "web_search")

    assert "web_search" not in chat.offered[-2], "the next turn still offered a removed app's tool"
    assert answers and answers[0].startswith("Error"), answers
    assert web.calls == [], "a removed app's tool still ran"


@pytest.mark.asyncio
async def test_a_tool_switched_off_while_a_chat_is_open_is_gone_from_its_next_turn(
    home, monkeypatch
):
    web = _AppTools("personalclaw-web", "web_search")
    tool_registry.register_provider(web, app="web-tools")
    runtime, chat = await _open_chat(monkeypatch)
    await _turn(runtime, chat)
    assert "web_search" in chat.offered[-1]

    assert tool_prefs.set_enabled("personalclaw-web", "web_search", False)["ok"]
    answers = await _turn(runtime, chat, "web_search")

    assert "web_search" not in chat.offered[-2], "the next turn still offered a switched-off tool"
    assert web.calls == []
    assert answers and answers[0].startswith("Error"), answers

    assert tool_prefs.set_enabled("personalclaw-web", "web_search", True)["ok"]
    assert await _turn(runtime, chat, "web_search") == ["web_search ran"]


@pytest.mark.asyncio
async def test_an_app_removed_during_a_turn_does_not_run_that_turns_call(home, monkeypatch):
    """The turn began with the tool on offer; the app left before the model's call arrived."""
    web = _AppTools("personalclaw-web", "web_search")
    tool_registry.register_provider(web, app="web-tools")
    runtime, chat = await _open_chat(monkeypatch)

    chat.before_call = lambda: tool_registry.unregister_provider(web)
    answers = await _turn(runtime, chat, "web_search")

    assert web.calls == [], "a call reached an app that had been removed"
    assert answers and "no longer" in answers[0], answers


@pytest.mark.asyncio
async def test_an_mcp_server_added_while_a_chat_is_open_reaches_its_next_turn(home, monkeypatch):
    """An MCP server's tools come from the one provider the MCP Tool Servers app registers, and
    adding a server changes that provider's list, not the registry: ``mcp.json`` does."""
    from personalclaw.config.secret_refs import write_mcp_document

    servers: dict[str, list[str]] = {}

    class _McpServers(_AppTools):
        async def list_tools(self) -> list[ToolDefinition]:
            return [
                ToolDefinition(
                    name=f"mcp/{server}/{tool}",
                    description=f"{tool} on {server}",
                    provider="mcp",
                    parameters={"type": "object", "properties": {}},
                    requires_approval=False,
                    risk_level=RiskLevel.SAFE,
                )
                for server, tools in servers.items()
                for tool in tools
            ]

    tool_registry.register_provider(_McpServers("mcp", ""), app="mcp-tools")
    runtime, chat = await _open_chat(monkeypatch)
    await _turn(runtime, chat)
    assert not any(n.startswith("mcp/") for n in chat.offered[-1])

    servers["notes"] = ["read_note"]
    write_mcp_document(
        home / "mcp.json", {"mcpServers": {"notes": {"command": "/nonexistent/fixture"}}}
    )
    await _turn(runtime, chat)

    assert "mcp/notes/read_note" in chat.offered[-1], json.dumps(chat.offered[-1])


@pytest.mark.asyncio
async def test_a_turn_with_nothing_changed_keeps_its_catalog(home, monkeypatch):
    """Nothing is rebuilt when nothing changed: the provider is listed once, at the start."""
    listed: list[int] = []

    class _Counted(_AppTools):
        async def list_tools(self) -> list[ToolDefinition]:
            listed.append(1)
            return await super().list_tools()

    tool_registry.register_provider(_Counted("personalclaw-web", "web_search"), app="web-tools")
    runtime, chat = await _open_chat(monkeypatch)
    before = len(listed)
    await _turn(runtime, chat)
    await _turn(runtime, chat)

    assert len(listed) == before
