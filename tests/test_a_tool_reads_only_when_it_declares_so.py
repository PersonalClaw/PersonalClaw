"""A tool reads only when it DECLARES that it only reads — never because of what it is called.

Measured on `main` with a scratch gateway: the read-only classifier judged a tool by its name,
and 75 of the agent's 115 tools passed as read-only — among them ``computer_click`` (clicks on
the desktop), ``workflow_start`` (starts a run) and ``memory_remember`` (writes to memory). Each
posture that runs "reads" without asking then ran them. One test per posture below, each red on
`main`:

* Ask and Plan mode, and ``personalclaw run`` without ``--allow`` (which is Ask mode);
* a dry run, which executes what it takes for a read for real;
* Trust reads, which approves a read without a card;
* ``--approval reads`` for background work;
* Ask and Build mode over an ACP CLI;
* the ``read`` tool grant of a research workflow step (the research subagent and the room critic
  hold the same grant: `test_profile_trust`, `test_rooms_posture`);
* what a tool is taken to declare when it says nothing: the in-process tools, a bare
  ``ToolDefinition``, an app's routes, and an external MCP server's tools on the Tools page.

Now each tool states its effect in its own definition — the MCP spec's ``readOnlyHint``, or
``RiskLevel.SAFE`` — and a tool that states nothing is a change, so it asks. An MCP server's
read-only label counts only for a server the owner trusts; its destructive label counts from
anyone, because it only adds a question.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_acp_permission_authority import (
    _context_builder,
    _drive,
    _make_state,
    _session,
    _set_stream,
)
from test_approval_threading import _make_gateway

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolResult

#: Three tools that change something and carry no write-shaped word, from three registries.
_CHANGES = [
    ("personalclaw.mcp_memory", "memory_remember", '{"rule": "always x"}'),
    ("personalclaw.computer_use.tools", "computer_click", '{"x": 1, "y": 1}'),
    ("personalclaw.mcp_workflows", "workflow_start", '{"name": "nightly"}'),
]
#: ...and a read from each, the floor: a posture that refused everything would pass the above.
_READS = [
    ("personalclaw.mcp_memory", "memory_recall", '{"query": "x"}'),
    ("personalclaw.computer_use.tools", "computer_snapshot", "{}"),
    ("personalclaw.mcp_workflows", "workflow_status", '{"run_id": "r"}'),
]


class _Recording(InProcessMcpToolProvider):
    """A real in-process registry — its real definitions, which are what is under test — that
    records a call instead of making it."""

    def __init__(self, module: str) -> None:
        super().__init__(module=module, provider_name="under-test")
        self.invoked: list[str] = []

    async def invoke(self, tool_name, arguments):
        self.invoked.append(tool_name)
        return ToolResult(success=True, output="ran")


class _OneCall:
    """A model that calls one tool, then answers."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, tool: str, args: str) -> None:
        self._turns = [
            [
                AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title=tool, tool_input=args),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for ev in turn:
            yield ev


async def _run_native(module: str, tool: str, args: str, **kw) -> list[str]:
    """One native turn in which the model calls *tool*; the tools that actually ran."""
    provider = _Recording(module)
    task_mode = kw.pop("task_mode", "agent")
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall(tool, args),
        tool_providers=[provider],
        **kw,
    )
    rt.set_approval_policy("yolo")  # nothing asks: the posture under test is the only gate
    rt.set_task_mode(task_mode)
    await rt.start()
    [ev async for ev in rt.stream("go")]
    return provider.invoked


async def _declared(module: str, tool: str) -> str:
    """What the real registry declares for *tool*, as its event carries it."""
    for t in await InProcessMcpToolProvider(module=module, provider_name="x").list_tools():
        if t.name == tool:
            return getattr(t.risk_level, "value", t.risk_level)
    raise AssertionError(f"{tool} is not in {module}")


# ── Ask and Plan mode, and `personalclaw run` without --allow ──


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "plan"])
@pytest.mark.parametrize("module,tool,args", _CHANGES, ids=[c[1] for c in _CHANGES])
async def test_a_read_only_mode_refuses_a_change_whatever_it_is_called(mode, module, tool, args):
    """🔴 Red on main: the name had no write-shaped word, so Ask and Plan ran it."""
    assert await _run_native(module, tool, args, task_mode=mode) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("module,tool,args", _READS, ids=[r[1] for r in _READS])
async def test_a_read_only_mode_still_runs_a_declared_read(module, tool, args):
    assert await _run_native(module, tool, args, task_mode="ask") == [tool]


class _DecisionTools(_Recording):
    """The real decision-journal definitions: they declare CAUTION and need no approval, so on a
    run with nobody to ask the task mode is the only thing between a call and the write."""

    def __init__(self) -> None:
        self.invoked = []

    @property
    def name(self) -> str:
        return "under-test"

    async def list_tools(self):
        from personalclaw.agents.native.decision_tool_defs import decision_tool_definitions

        return decision_tool_definitions(self.name, {"type": "object"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("log_decision", '{"summary": "x", "expectation": "y", "confidence": 0.5}'),
        ("decision_resolve", '{"id": "d1", "outcome": "it rained"}'),
    ],
)
async def test_ask_mode_refuses_a_declared_change_that_needs_no_approval(tool, args):
    """🔴 Red on main: the Ask gate read the NAME and ignored the declaration — both tools
    declare CAUTION — and, needing no approval, each wrote to the decision journal in Ask mode
    and in `personalclaw run` without `--allow`."""
    provider = _DecisionTools()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall(tool, args),
        tool_providers=[provider],
    )
    rt.set_task_mode("ask")
    await rt.start()
    [ev async for ev in rt.stream("go")]
    assert provider.invoked == []


def test_personalclaw_run_without_allow_is_ask_mode():
    """So the two tests above are its gate: a headless run with no grant is Ask mode."""
    from personalclaw.cli_run import task_mode_for

    assert task_mode_for(False) == "ask" and task_mode_for(True) == "agent"


# ── a dry run ──


@pytest.mark.asyncio
@pytest.mark.parametrize("module,tool,args", _CHANGES, ids=[c[1] for c in _CHANGES])
async def test_a_dry_run_does_not_make_a_change(module, tool, args):
    """🔴 Red on main: a dry run executes what it takes for a read, and it took these for one."""
    assert await _run_native(module, tool, args, dry_run=True) == []


@pytest.mark.asyncio
async def test_a_dry_run_still_reads_real_state():
    assert await _run_native(*_READS[0], dry_run=True) == ["memory_recall"]


# ── Trust reads ──


def _permission(title: str, *, risk_level: str = "", tool_kind: str = "", args: str = "{}"):
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=title,
        tool_kind=tool_kind,
        request_id="req-1",
        tool_input=args,
        risk_level=risk_level,
    )


async def _trust_reads_turn(tmp_path: Path, event: LLMEvent):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=False)
    session._trust_reads = True
    _set_stream(client, [event, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    await _drive(state, session, answer="denied")
    return session, client


@pytest.mark.asyncio
@pytest.mark.parametrize("module,tool,args", _CHANGES, ids=[c[1] for c in _CHANGES])
async def test_trust_reads_asks_before_a_change(tmp_path, module, tool, args):
    """🔴 Red on main: the call carried its registry's `safe`, so it ran with no card."""
    declared = await _declared(module, tool)
    session, client = await _trust_reads_turn(
        tmp_path, _permission(tool, risk_level=declared, args=args)
    )

    client.approve_tool.assert_not_awaited()
    assert any(m.get("role") == "permission" for m in session.messages), "no card was raised"


@pytest.mark.asyncio
async def test_trust_reads_still_approves_a_declared_read(tmp_path):
    module, tool, args = _READS[0]
    declared = await _declared(module, tool)
    _session_, client = await _trust_reads_turn(
        tmp_path, _permission(tool, risk_level=declared, args=args)
    )
    client.approve_tool.assert_awaited_once_with("req-1")


# ── `--approval reads` ──


@pytest.mark.asyncio
async def test_approval_reads_asks_for_a_tool_that_declares_nothing():
    """🔴 Red on main: a leading read verb and no write word — `list_and_archive` ran unasked."""
    gateway = _make_gateway()
    gateway._approval_mode = "reads"
    # The owner says no wherever they are asked, so a True can only be the auto-approval.
    gateway._channel_delivery.request_approval = AsyncMock(return_value=False)
    gateway.dashboard_state.request_approval = AsyncMock(return_value=False)
    audit = MagicMock()
    with (
        patch("personalclaw.gateway.sel", return_value=audit),
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
    ):
        approve = gateway._interactive_approval("subagent")
        decided = await approve(_permission("list_and_archive"), "")

    # Asked, and the person said no: not approved by the `--approval` flag.
    assert (decided.approved, decided.decided_by) == (False, "you")
    assert not [
        c
        for c in audit.log_api_access.call_args_list
        if "cli_approval_auto_approve" in c.kwargs.get("operation", "")
    ]


@pytest.mark.asyncio
async def test_approval_reads_still_approves_a_declared_read():
    gateway = _make_gateway()
    gateway._approval_mode = "reads"
    with (
        patch("personalclaw.gateway.sel", return_value=MagicMock()),
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
    ):
        approve = gateway._interactive_approval("subagent")
        decided = await approve(_permission("memory_recall", risk_level="safe"), "")
    assert (decided.approved, decided.decided_by) == (True, "cli")


# ── Ask and Build mode over an ACP CLI ──


async def _acp_turn(tmp_path: Path, task_mode: str, event: LLMEvent):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode=task_mode, trust=True)  # trust on: the task mode still decides
    _set_stream(client, [event, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    await _drive(state, session)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_mode,title",
    [
        # An ACP CLI's call to another MCP server's tool: it declares nothing.
        ("ask", "mcp__acme__search_things"),
        ("plan", "mcp__acme__get_or_archive"),
        # Build mode runs the deliverable's producers; a name is not one.
        ("build", "mcp__acme__publish_document"),
    ],
)
async def test_an_acp_tool_that_declares_nothing_is_refused_by_a_restrictive_mode(
    tmp_path, task_mode, title
):
    """🔴 Red on main: the name read as a read (or as a Build-mode producer), so it ran."""
    client = await _acp_turn(
        tmp_path, task_mode, _permission(title, tool_kind="other", args='{"q": "x"}')
    )
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_acp_call_to_our_own_read_still_runs_in_ask_mode(tmp_path):
    """The floor, through the adapter: PersonalClaw's own `memory_recall`, called by claude-code,
    carries the declaration the tool makes (`acp.mcp_servers.core_tool_declaration`)."""
    from personalclaw.acp.adapter import acp_event_to_agent_event
    from personalclaw.acp.types import AcpEvent

    event = acp_event_to_agent_event(
        AcpEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="mcp__personalclaw-core__memory_recall",
            tool_kind="other",
            request_id="req-1",
            tool_input='{"query": "x"}',
        )
    )
    client = await _acp_turn(tmp_path, "ask", event)
    client.approve_tool.assert_awaited_once()
    client.reject_tool.assert_not_awaited()


# ── the `read` grant of a research workflow step ──


@pytest.fixture
def read_only_leaf(monkeypatch):
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "1")
    monkeypatch.setenv(mcp_shared.LEAF_READ_ONLY_KEY, "1")
    return mcp_shared


@pytest.mark.parametrize("tool", ["computer_click", "computer_type", "computer_scroll"])
def test_a_research_step_is_refused_the_desktop(read_only_leaf, tool):
    """🔴 Red on main: the write-word list had nothing to match, so a research step clicked."""
    assert read_only_leaf.leaf_tool_denial(tool)


def test_a_research_step_still_reads(read_only_leaf):
    assert read_only_leaf.leaf_tool_denial("computer_snapshot") == ""
    assert read_only_leaf.leaf_tool_denial("memory_recall") == ""


# ── the `read` grant of a research subagent whose runtime answers its own approvals ──


class _Ran(_Recording):
    """`_Recording` under the name the subagent helpers read."""

    @property
    def ran(self) -> list[str]:
        return self.invoked


async def _research_run(tool: str) -> list[str]:
    from test_a_tool_call_is_audited_as_it_was_decided import _subagents

    from personalclaw.subagent import SubagentInfo

    tools = _Ran("personalclaw.computer_use.tools")
    manager, _ = _subagents(tool, tools=tools)
    # An auto-fired spawn is a research run, and its standing grant answers every ask.
    info = SubagentInfo(id=f"sa-{tool}", task="t", approval_mode="auto")
    await asyncio.wait_for(manager._run_inner(info, f"subagent:{info.id}"), timeout=20)
    return tools.ran


@pytest.mark.asyncio
async def test_a_research_run_s_standing_grant_does_not_admit_a_change_without_a_write_word():
    """🔴 Red on main: the grant the native runtime is handed read the NAME, and `computer_click`
    has no write word in it, so an auto-fired research run clicked on the desktop."""
    assert await _research_run("computer_click") == []


@pytest.mark.asyncio
async def test_a_research_run_still_runs_a_declared_read():
    assert await _research_run("computer_snapshot") == ["computer_snapshot"]


# ── what a tool that says nothing is taken to declare ──


@pytest.mark.asyncio
@pytest.mark.parametrize("module,tool,args", _CHANGES, ids=[c[1] for c in _CHANGES])
async def test_an_in_process_change_does_not_declare_a_read(module, tool, args):
    """🔴 Red on main: the in-process provider inferred `safe` from each name."""
    assert await _declared(module, tool) != "safe"


def test_a_tool_definition_that_declares_nothing_is_a_change():
    """🔴 Red on main: the default was `safe`, so an app's or an SDK tool's silence was a read."""
    assert ToolDefinition(name="t", description="d").risk_level is not RiskLevel.SAFE


@pytest.mark.asyncio
async def test_an_app_route_that_declares_nothing_is_a_change(tmp_path):
    """🔴 Red on main: every GET route was a read, whatever it did."""
    from test_app_routes import _install

    from personalclaw.tool_providers import app_routes

    _install(tmp_path)
    tools = {t.name: t for t in await app_routes.AppRoutesToolProvider().list_tools()}

    assert tools["app_demo_get_item"].risk_level is not RiskLevel.SAFE
    assert tools["app_demo_delete_item"].risk_level is RiskLevel.DESTRUCTIVE


class _Server:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return self._tools


class _Registry:
    def __init__(self, servers):
        self._servers = servers

    def items(self):
        return list(self._servers.items())


def _spec(name: str, annotations: dict | None = None):
    from personalclaw.mcp_client import McpToolSpec

    spec = McpToolSpec(name=name, description="d", input_schema={"type": "object"})
    if annotations is not None:
        spec.annotations = annotations  # type: ignore[attr-defined]
    return spec


async def _catalog_risk(monkeypatch) -> dict[str, str]:
    from personalclaw.dashboard.handlers import tools as tools_mod

    registry = _Registry(
        {
            "acme": _Server(
                [
                    _spec("frobnicate"),
                    _spec("search_docs", {"readOnlyHint": True}),
                    _spec("wipe", {"readOnlyHint": False, "destructiveHint": True}),
                ]
            )
        }
    )
    monkeypatch.setattr("personalclaw.mcp_client.get_mcp_client_registry", lambda: registry)

    async def _no_registry_tools(*_a, **_kw):
        return []

    # The catalog's other sources are not what this asks about.
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_all_tools", _no_registry_tools, raising=False
    )
    import json

    resp = await asyncio.wait_for(tools_mod.api_tools_list(SimpleNamespace()), timeout=30)
    rows = json.loads(resp.body.decode())["tools"]
    return {r["name"]: r["risk_level"] for r in rows if r["name"].startswith("mcp/acme/")}


@pytest.mark.asyncio
async def test_the_tools_page_takes_no_mcp_tool_for_a_read_on_its_name_or_its_word(monkeypatch):
    """🔴 Red on main: `frobnicate` (no label) and `search_docs` were both shown as reads, from
    their names. A server may label anything read-only, so its label counts only once the owner
    trusts that server; a destructive label counts from anyone."""
    from personalclaw import mcp_client

    monkeypatch.setattr(mcp_client, "read_only_labels_trusted", lambda server: False)
    risk = await _catalog_risk(monkeypatch)

    assert risk["mcp/acme/frobnicate"] != "safe"
    assert risk["mcp/acme/search_docs"] != "safe"
    assert risk["mcp/acme/wipe"] == "destructive"


@pytest.mark.asyncio
async def test_a_trusted_servers_read_label_is_believed(monkeypatch):
    from personalclaw import mcp_client

    monkeypatch.setattr(mcp_client, "read_only_labels_trusted", lambda server: server == "acme")
    risk = await _catalog_risk(monkeypatch)

    assert risk["mcp/acme/search_docs"] == "safe"
    assert risk["mcp/acme/frobnicate"] == "caution", "trust believes a label; it invents none"


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["add", "remove"])
async def test_a_change_to_the_trust_reaches_the_running_sessions(tmp_path, monkeypatch, op):
    """A session lists its tools when it starts, so it keeps the answer it started with. The
    revoking direction is the one that matters: a server the owner stopped trusting because it
    lied must not keep running its "reads" unasked in the chats already open. Every session is
    reset, as an MCP server change already does."""
    import json

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"security": {"mcp_read_only_servers": ["acme"] if op == "remove" else []}})
    )
    resets: list[str] = []

    async def _reset(_request):
        resets.append("reset")
        return 0

    monkeypatch.setattr("personalclaw.dashboard.handlers.sessions._reset_all_sessions", _reset)
    app = web.Application()
    app["state"] = SimpleNamespace()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    async with TestClient(TestServer(app)) as client:
        resp = await client.patch(
            "/api/config/personalclaw",
            json={"path": "security.mcp_read_only_servers", op: "acme", "confirm": True},
        )
        assert resp.status == 200, await resp.text()
    stored = json.loads((tmp_path / "config.json").read_text())["security"]
    assert stored["mcp_read_only_servers"] == (["acme"] if op == "add" else [])
    assert resets == ["reset"]
