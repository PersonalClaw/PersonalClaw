"""The second confirmation is decided where a call is raised, from what its tool declares.

`approval_confirms.call_confirms` is the one reading, and these are the places that hand it what
a call's tool declares: the native runtime's catalog (`declared_consequences`, which classifies
PersonalClaw's own tools and takes an MCP server's, an app's and an installed app's at their
word), an agent CLI's call (`core_tool_declaration`, `acp_event_to_agent_event`), the chat runner,
which records the decision on the permission row and the pending row, and the gateway, which
records it for a background call. A declaration this reading cannot follow is no declaration: the
call asks, whoever ships the tool.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from test_acp_permission_authority import _context_builder, _make_state, _session, _set_stream
from test_approval_threading import _make_gateway

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import declared_consequences
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.approval_confirms import (
    DELETES,
    OUTSIDE_WORKSPACE,
    SENDS,
    SPENDS,
    UNDECLARED,
    call_confirms,
)
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers import registry
from personalclaw.tool_providers.base import (
    ToolDefinition,
    ToolProvider,
    ToolResult,
    consequences_of,
)

pytestmark = pytest.mark.security


class _Provider(ToolProvider):
    """A provider of *tools*, each asking before it runs unless it declares itself a read."""

    def __init__(self, name: str, tools: list[ToolDefinition]) -> None:
        self._name = name
        self._tools = tools

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    async def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools)

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="ok")


def _tool(name: str, consequences: Any = None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} does something.",
        provider="p",
        requires_approval=True,
        consequences=consequences,
    )


def _providers() -> dict[str, _Provider]:
    """One of each standing: PersonalClaw's own (registered at core's standing), the MCP Tool
    Servers app's provider (`mcp`), app-routes, an installed app's, and the platform's (never
    registered)."""
    from personalclaw.tool_providers.app_routes import PROVIDER_NAME as APP_ROUTES

    out = {
        "core": _Provider("demo-core", [_tool("core_change"), _tool("core_send", ("sends",))]),
        "mcp": _Provider(registry.EXTERNAL_MCP_PROVIDER, [_tool("mcp/srv/create_page")]),
        "routes": _Provider(APP_ROUTES, [_tool("app_notes_save")]),
        "app": _Provider("demo-installed", [_tool("app_shop_buy"), _tool("app_shop_list", ())]),
        "platform": _Provider("demo-platform", [_tool("platform_change")]),
    }
    from personalclaw.providers.mcp_instances import MCP_TOOLS_EXTENSION

    registry.register_provider(out["core"])
    # The MCP Tool Servers app ships with PersonalClaw, at core's standing, and alone serves mcp/.
    registry.register_provider(out["mcp"], app=MCP_TOOLS_EXTENSION, core=True)
    registry.register_provider(out["routes"], core=True)
    registry.register_provider(out["app"], app="demo-app")
    return out


def _index(providers: dict[str, _Provider]) -> tuple[list[ToolDefinition], dict[str, ToolProvider]]:
    defs: list[ToolDefinition] = []
    index: dict[str, ToolProvider] = {}
    for prov in providers.values():
        for t in prov._tools:
            defs.append(t)
            index[t.name] = prov
    return defs, index


# ── The native runtime's catalog ─────────────────────────────────────────────────────────────────


def test_personalclaws_own_tools_are_classified_and_everyone_elses_are_taken_at_their_word():
    defs, index = _index(_providers())
    got = declared_consequences(defs, index)
    # PersonalClaw's own, and the platform's: nothing declared declares neither.
    assert got["core_change"] == ()
    assert got["platform_change"] == ()
    assert got["core_send"] == (SENDS,)
    # An MCP server's tool, app-routes' and an installed app's: only what they declare.
    assert "mcp/srv/create_page" not in got
    assert "app_notes_save" not in got
    assert "app_shop_buy" not in got
    assert got["app_shop_list"] == ()


@pytest.mark.parametrize(
    "declared",
    ["sends", ("Sends",), ("send",), ("bogus",), ["SENDS"], ("sends", "bogus"), 3, {"sends"}],
    ids=["bare-string", "case", "near-miss", "unknown", "upper", "one-unknown", "number", "set"],
)
def test_a_declaration_this_reading_cannot_follow_asks_whoever_ships_the_tool(declared):
    assert consequences_of(declared) is None
    for standing in ("core", "app"):
        prov = _Provider(f"demo-{standing}-odd", [_tool("odd_tool", declared)])
        if standing == "core":
            registry.register_provider(prov)
        else:
            registry.register_provider(prov, app="demo-app")
        got = declared_consequences(prov._tools, {"odd_tool": prov})
        assert "odd_tool" not in got, standing
        registry.unregister_provider(prov)
    assert call_confirms(
        "caution", "odd_tool", "", {"x": 1}, cwd="/tmp/chat", consequences=None
    ) == (UNDECLARED,)


@pytest.mark.parametrize(
    ("declared", "read"),
    [((), ()), ([], ()), (("sends",), (SENDS,)), (["spends", "sends"], (SENDS, SPENDS))],
)
def test_a_declaration_of_known_kinds_is_read_as_it_says(declared, read):
    assert consequences_of(declared) == read


class _CallsEach:
    """A model that calls each of *tools* in turn, then answers."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, tools: list[str]) -> None:
        self._tools = tools
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls <= len(self._tools):
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{self.calls}",
                title=self._tools[self.calls - 1],
                tool_input='{"title": "x"}',
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.asyncio
async def test_the_runtimes_approval_request_carries_what_the_tool_declares():
    providers = _providers()
    names = ["core_change", "core_send", "mcp/srv/create_page", "app_shop_buy", "app_shop_list"]
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_CallsEach(names),
        tool_providers=list(providers.values()),
    )
    await rt.start()
    asks: dict[str, Any] = {}

    async def pump() -> None:
        async for ev in rt.stream("do the five things"):
            if ev.kind == EVENT_PERMISSION_REQUEST:
                asks[ev.title] = ev.consequences
                await rt.reject_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    assert asks == {
        "core_change": (),
        "core_send": (SENDS,),
        "mcp/srv/create_page": None,
        "app_shop_buy": None,
        "app_shop_list": (),
    }


# ── An agent CLI's call ──────────────────────────────────────────────────────────────────────────


def test_an_agent_clis_call_to_a_core_tool_carries_its_declaration_and_its_own_tool_none():
    from personalclaw.acp.adapter import acp_event_to_agent_event
    from personalclaw.acp.mcp_servers import CORE_SERVER_NAME, core_tool_declaration
    from personalclaw.acp.types import AcpEvent

    core = f"mcp__{CORE_SERVER_NAME}__"
    notify = core_tool_declaration(f"{core}notify", "other", {"text": "hi", "user": "U1"})
    assert notify.consequences == (SENDS,)
    assert (
        core_tool_declaration(f"{core}artifact_delete", "other", {"slug": "x"}).consequences == ()
    )
    assert core_tool_declaration("Frobnicate the build", "other", {}).consequences is None
    own = acp_event_to_agent_event(
        AcpEvent(kind="permission_request", title="Frobnicate the build", tool_kind="other")
    )
    assert own.consequences is None
    asked = acp_event_to_agent_event(
        AcpEvent(
            kind="permission_request",
            title=f"{core}notify",
            tool_kind="other",
            tool_input='{"text": "hi", "user": "U1"}',
        )
    )
    assert asked.consequences == (SENDS,)


# ── The chat runner records it on the permission row and the pending row ─────────────────────────


def _permission(title: str, tool_input: Any, **kw: Any) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST, title=title, request_id="req-1", tool_input=tool_input, **kw
    )


async def _chat_asks(tmp_path, event: LLMEvent) -> tuple[list[str], list[str]]:
    """Run one chat turn that asks *event*: the confirms on its permission row and its pending
    row, read while the approval waits, before it is denied."""
    from personalclaw.dashboard.chat_runner import run_chat

    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=False)
    _set_stream(client, [event, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    pending: dict[str, Any] = {}

    async def answer() -> None:
        for _ in range(300):
            fut = session._approval_futures.get("req-1")
            row = state._pending_approvals.get(chat_approval_id(session.key, "req-1"))
            if fut is not None and not fut.done() and row is not None:
                pending.update(row)
                fut.set_result("rejected")
                return
            await asyncio.sleep(0.01)

    waiter = asyncio.get_running_loop().create_task(answer())
    with (
        patch("personalclaw.dashboard.chat_runner.sel"),
        patch("personalclaw.acp.ungated.sel"),
    ):
        await run_chat(state, session, "hello")
    await waiter
    [row] = [m for m in session.messages if m.get("role") == "permission"]
    # The permission row keeps its meta as the JSON a transcript row's `cls` column carries.
    meta = json.loads(row.get("cls") or "{}")
    return meta.get("confirms"), pending.get("confirms")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "expected"),
    [
        # A built-in change that is none of the kinds: classified by the runtime, decides at once.
        (
            lambda: _permission(
                "task_create", {"title": "x"}, risk_level="caution", consequences=()
            ),
            [],
        ),
        (
            lambda: _permission(
                "bash", {"command": "rm -rf build"}, risk_level="destructive", consequences=()
            ),
            [DELETES],
        ),
        # An external MCP server's tool, which declares nothing.
        (
            lambda: _permission(
                "mcp/srv/create_page", {"title": "x"}, risk_level="caution", consequences=None
            ),
            [UNDECLARED],
        ),
        # `notify` with someone else to tell sends; to the owner only it does not.
        (
            lambda: _permission(
                "notify",
                {"text": "hi", "user": "U1"},
                risk_level="caution",
                consequences=(SENDS,),
                tells_owner=False,
            ),
            [SENDS],
        ),
        (
            lambda: _permission(
                "notify",
                {"text": "hi"},
                risk_level="caution",
                consequences=(SENDS,),
                tells_owner=True,
            ),
            [],
        ),
        # An agent CLI's edit outside the chat's folder.
        (
            lambda: _permission(
                "Edit hosts",
                "{}",
                tool_kind="edit",
                named_files=("/etc/hosts",),
            ),
            [OUTSIDE_WORKSPACE],
        ),
    ],
    ids=["built-in", "delete", "mcp", "send", "tells-owner", "cli-edit-outside"],
)
async def test_a_chats_permission_row_and_pending_row_carry_the_same_decision(
    tmp_path, event, expected
):
    on_row, pending = await _chat_asks(tmp_path, event())
    assert on_row == expected
    assert pending == expected


@pytest.mark.asyncio
async def test_a_chats_row_recorded_without_a_decision_fails_closed(tmp_path):
    """The runner always hands its decision over; a row recorded without one asks."""
    state, _client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=False)
    session._approval_futures["r-9"] = asyncio.get_running_loop().create_future()
    await state.hold_session_approval(
        session,
        "r-9",
        tool="task_create",
        tool_input='{"title": "x"}',
        tool_purpose="",
        agent="PersonalClaw",
        risk="caution",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
    )
    assert state._pending_approvals[chat_approval_id(session.key, "r-9")]["confirms"] == [
        UNDECLARED
    ]
    session._approval_futures["r-9"].cancel()


# ── The gateway records it for a background call ─────────────────────────────────────────────────


async def _background(event: LLMEvent, monkeypatch) -> Any:
    """A background call the gateway asks with no channel to ask on: the dashboard's request."""
    monkeypatch.setenv("PERSONALCLAW_OWNER_ID", "U000")
    gateway = _make_gateway()
    gateway._channel_delivery = None
    gateway.dashboard_state.request_approval = AsyncMock(return_value=False)
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        await gateway._interactive_approval("subagent")(event, "")
    gateway.dashboard_state.request_approval.assert_awaited_once()
    return gateway.dashboard_state.request_approval.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            lambda: _permission("mcp/srv/create_page", {"title": "x"}, risk_level="caution"),
            [UNDECLARED],
        ),
        (
            lambda: _permission(
                "task_create", {"title": "x"}, risk_level="caution", consequences=()
            ),
            [],
        ),
        (
            lambda: _permission(
                "notify",
                {"text": "hi", "user": "U1"},
                risk_level="caution",
                consequences=(SENDS,),
            ),
            [SENDS],
        ),
        (
            lambda: _permission(
                "notify",
                {"text": "hi"},
                risk_level="caution",
                consequences=(SENDS,),
                tells_owner=True,
            ),
            [],
        ),
    ],
    ids=["undeclared", "classified", "send", "tells-owner"],
)
async def test_the_gateway_hands_the_registry_the_decision_for_a_background_call(
    event, expected, monkeypatch
):
    kwargs = await _background(event(), monkeypatch)
    assert list(kwargs["confirms"]) == expected
