"""The operator's blocking hook holds on every path that runs a tool call.

A ``PreToolUse`` hook bound to an agent is the operator's own rule about that agent's calls, and it
holds only where it is asked. The chat's gate over an agent CLI and the built-in runtime asked it,
at one step before anything could approve a call. These did not:

* a subagent on an agent CLI: its gate approved a call an operator's pattern named, or put it to
  the owner, and no hook was asked;
* a channel's own turn: the channel asked the deny-list and then who approves, and core gave it no
  way to ask the hooks at all;
* the background helper: no hook was asked, and under a hook-based policy with nobody to ask, a
  call no pattern named was approved;
* the built-in runtime in a chat that names no agent (the default agent's): the runtime was built
  with no hooks, because they were read only for an agent named.

And a hook whose command never exited (it timed out, or could not start) let the call through on
every path, as if it had exited cleanly.

Each test below drives the real hook store over a hook bound in the test's own home; only the
action the hook runs is a stand-in, answering as its command would.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import approval_grants
from personalclaw.action_providers.base import ActionResult
from personalclaw.approval_grants import ToolDecision
from personalclaw.hooks import (
    HOOK_EVENT_PRE_TOOL_USE,
    HookManager,
    HooksConfig,
    ScriptHook,
    ScriptHookStore,
)
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

WRITE = '{"path": "notes.md", "content": "x"}'
#: The agent every turn below runs as: the default agent, which a turn that names none runs as.
AGENT = "keeper"


class _Command:
    """The hook's action, answering as its command would: ``exit 0`` lets the call go on,
    ``exit 2`` refuses it, ``timeout`` never exits."""

    def __init__(self) -> None:
        self.answer = "exit 0"
        self.runs = 0

    async def execute(self, config, ctx, timeout=30):
        self.runs += 1
        if self.answer == "exit 2":
            return ActionResult(success=False, exit_code=2, blocked=True, stderr="no writes today")
        if self.answer == "timeout":
            return ActionResult(success=False, error=f"Timed out after {timeout}s")
        return ActionResult(success=True, exit_code=0)


@pytest.fixture
def hook(monkeypatch) -> _Command:
    """A blocking hook the operator bound to the default agent, and an operator's pattern that
    would approve ``fs_write`` without asking anyone."""
    import personalclaw.action_providers as action_providers
    from personalclaw.config.loader import config_dir

    command = _Command()
    monkeypatch.setattr(action_providers, "get_action_provider", lambda name: command)
    (config_dir() / "config.json").write_text(
        json.dumps(
            {
                "default_agent": AGENT,
                "agents": {AGENT: {"triggers": ["no-writes"]}},
                "hooks": {"auto_approve_tools": ["fs_write"]},
                "updates": {"check_enabled": False},
            }
        ),
        encoding="utf-8",
    )
    store = ScriptHookStore()
    store.create(
        ScriptHook(
            id="no-writes",
            name="no-writes",
            event=HOOK_EVENT_PRE_TOOL_USE,
            provider="bash",
            provider_config={"command": "/nonexistent/pc-fixture-hook"},
            enabled=True,
            # The owner's yes to what the hook runs (`triggers.grants`).
            capabilities={"providers": ["bash"]},
        ).to_dict()
    )
    monkeypatch.setattr("personalclaw.hooks._global_script_hook_store", store)
    return command


def _request(tool: str = "fs_write", **fields: Any) -> LLMEvent:
    """The permission request an agent CLI sends for one call."""
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=tool,
        tool_kind="edit",
        request_id="req-1",
        tool_call_id="c1",
        tool_input=WRITE,
        **fields,
    )


async def _iter(items):
    for item in items:
        yield item


def _rows(audit: MagicMock) -> list[dict]:
    return [c.kwargs for c in audit.return_value.log_tool_invocation.call_args_list]


# ── a subagent on an agent CLI ──────────────────────────────────────────────────────────────────


async def _subagent(*, relay: AsyncMock | None = None):
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentInfo, SubagentManager

    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    client = sessions.get_or_create.return_value[0]
    client.provider_id = "acp:agent-cli"
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [_request(), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]
        )
    )
    ctx = _mock_ctx_builder()
    ctx.hooks = HookManager(HooksConfig(auto_approve_tools=["fs_write"]))
    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False, on_tool_approval=relay
    )
    info = SubagentInfo(id="sa0001", task="tidy the notes", parent_session_key="")
    audit = MagicMock()
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel", audit):
        await manager._run_inner(info, "subagent:sa0001")
    return client, audit


@pytest.mark.asyncio
async def test_a_blocking_hook_refuses_a_subagents_call_on_an_agent_cli(hook):
    """🔴 Before: the operator's pattern approved the write, and the hook never ran."""
    hook.answer = "exit 2"
    client, audit = await _subagent()
    client.approve_tool.assert_not_awaited()
    client.reject_tool.assert_awaited_once_with("req-1")
    assert hook.runs == 1
    (row,) = [r for r in _rows(audit) if r.get("request_id") == "req-1"]
    assert row["outcome"] == "hook_blocked" and row["metadata"]["decided_by"] == "hook", row


@pytest.mark.asyncio
async def test_a_subagents_call_a_hook_refuses_is_never_put_to_the_owner(hook):
    hook.answer = "exit 2"
    relay = AsyncMock(return_value=ToolDecision(True, "approved", approval_grants.YOU))
    from personalclaw.config.loader import config_dir

    (config_dir() / "config.json").write_text(
        json.dumps({"default_agent": AGENT, "agents": {AGENT: {"triggers": ["no-writes"]}}}),
        encoding="utf-8",
    )
    client, _audit = await _subagent(relay=relay)
    relay.assert_not_awaited()
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_subagents_ordinary_call_still_runs_on_its_grant(hook):
    client, audit = await _subagent()
    client.approve_tool.assert_awaited_once_with("req-1")
    assert hook.runs == 1, "the hook was asked once"
    decided = [r["metadata"]["decided_by"] for r in _rows(audit) if r["outcome"] == "auto_approved"]
    assert decided == [approval_grants.HOOK_PATTERN]


# ── a channel's own turn ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_blocking_hook_refuses_a_call_in_a_channels_own_turn(hook):
    """🔴 Before: the SDK had no way to ask the hooks, so the pattern approved the write."""
    from personalclaw.sdk.channel import ask_pre_tool_hooks, chat_grant

    event = _request()
    assert chat_grant("relay:T0CHAN:C0CHAN:1700000000.0001", event) == approval_grants.HOOK_PATTERN
    hook.answer = "exit 2"
    said = await ask_pre_tool_hooks(event, agent=AGENT)
    assert said.refused and said.note == "a pre-tool hook blocked it (no-writes:no writes today)"
    row = said.audit_row(channel="relay")
    assert row["outcome"] == "hook_blocked" and row["metadata"] == {
        "channel": "relay",
        "decided_by": "hook",
    }

    hook.answer = "exit 0"
    assert not (await ask_pre_tool_hooks(event, agent=AGENT)).refused
    assert hook.runs == 2


# ── the background helper ───────────────────────────────────────────────────────────────────────


async def _helper(request: LLMEvent, *, policy=None, patterns=(), callback=None):
    from personalclaw.llm_helpers import ToolApprovalPolicy, stream_and_collect

    provider = AsyncMock()
    provider.provider_id = "acp:agent-cli"
    provider.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter([request, LLMEvent(kind=EVENT_COMPLETE)])
    )
    audit = MagicMock()
    with patch("personalclaw.sel.sel", audit):
        await stream_and_collect(
            provider,
            "go",
            approval_policy=policy or ToolApprovalPolicy.HOOK_BASED,
            hooks=HookManager(HooksConfig(auto_approve_tools=list(patterns))),
            on_tool_approval=callback,
            session_key="cron:tidy",
        )
    return provider, _rows(audit)


@pytest.mark.asyncio
async def test_a_blocking_hook_refuses_a_background_call_its_pattern_names(hook):
    hook.answer = "exit 2"
    provider, rows = await _helper(_request(), patterns=["fs_write"])
    provider.approve_tool.assert_not_awaited()
    provider.reject_tool.assert_awaited_once_with("req-1")
    assert [r["outcome"] for r in rows] == ["hook_blocked"]


@pytest.mark.asyncio
async def test_a_background_call_no_pattern_names_is_refused_with_nobody_to_ask():
    """🔴 Before: under a hook-based policy with nobody to ask, a call no pattern named ran."""
    provider, rows = await _helper(_request("fs_delete"), patterns=["fs_write"])
    provider.approve_tool.assert_not_awaited()
    provider.reject_tool.assert_awaited_once_with("req-1")
    assert [(r["outcome"], r["metadata"]["decided_by"]) for r in rows] == [
        ("denied", "unattended_no_one_to_ask")
    ]


@pytest.mark.asyncio
async def test_with_nobody_to_ask_a_pattern_and_a_declared_read_still_answer():
    named, rows = await _helper(_request("fs_write"), patterns=["fs_write"])
    named.approve_tool.assert_awaited_once_with("req-1")
    assert rows[-1]["metadata"]["decided_by"] == approval_grants.HOOK_PATTERN

    read, rows = await _helper(_request("memory_recall", risk_level="safe"))
    read.approve_tool.assert_awaited_once_with("req-1")
    assert rows[-1]["metadata"]["decided_by"] == approval_grants.DECLARED_READ


@pytest.mark.asyncio
async def test_an_auto_approving_background_run_is_unchanged():
    from personalclaw.llm_helpers import ToolApprovalPolicy

    provider, rows = await _helper(_request("fs_delete"), policy=ToolApprovalPolicy.AUTO_APPROVE)
    provider.approve_tool.assert_awaited_once_with("req-1")
    assert rows[-1]["metadata"]["decided_by"] == approval_grants.SESSION_POLICY


# ── a heartbeat task, the background helper's one run that approves more ───────────────────────


async def _heartbeat(request: LLMEvent):
    from test_gateway import _make_orchestrator, _mock_sessions

    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    client = AsyncMock()
    client.provider_id = "acp:agent-cli"
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [request, LLMEvent(kind=EVENT_TEXT_CHUNK, text="tidied"), LLMEvent(kind=EVENT_COMPLETE)]
        )
    )
    orch.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.ctx_builder.hooks = HookManager(HooksConfig())
    orch.dashboard_state = None
    orch._deliver_result = AsyncMock()
    audit = MagicMock()
    with patch("personalclaw.sel.sel", audit):
        await orch._run_heartbeat_task("tidy the notes", "")
    return client, _rows(audit)


@pytest.mark.asyncio
async def test_a_heartbeat_tasks_call_runs_on_its_allow_once_the_hooks_let_it(hook):
    """The task's own Allow approves what it does, and says so, never a call no hook named; a
    blocking hook still refuses it first."""
    client, rows = await _heartbeat(_request("fs_delete"))
    client.approve_tool.assert_awaited_once_with("req-1")
    assert [(r["outcome"], r["metadata"]["decided_by"]) for r in rows] == [
        ("auto_approved", approval_grants.HEARTBEAT_TASK)
    ]

    hook.answer = "exit 2"
    client, rows = await _heartbeat(_request("fs_delete"))
    client.approve_tool.assert_not_awaited()
    assert [r["outcome"] for r in rows] == ["hook_blocked"]


# ── a hook that never exits ─────────────────────────────────────────────────────────────────────


async def _chat_write(tmp_path, name: str):
    """One write an agent CLI asks a chat's gate about, which an operator's pattern names, with the
    hook store the gateway runs."""
    import asyncio

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.history import ConversationLog

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = "acp:agent-cli"
    del client.cancel_session
    client.undelivered_steers = MagicMock(return_value=[])  # a plain method on the real one
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / name),
    )
    builder = MagicMock()
    builder.hooks = HookManager(HooksConfig(auto_approve_tools=["fs_write"]))
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    state._hook_store = ScriptHookStore()
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    session = _ChatSession(name)
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [
                _request(),
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="done"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )
    audit = MagicMock()
    with patch("personalclaw.dashboard.chat_runner.sel", audit):
        await asyncio.wait_for(run_chat(state, session, "hello"), timeout=20)
    said = " ".join(str(m.get("content", "")) for m in session.messages)
    return client, said, [r["outcome"] for r in _rows(audit)]


@pytest.mark.asyncio
async def test_a_hook_whose_command_never_exits_refuses_the_call(hook, tmp_path):
    """🔴 Before: a timed-out hook read as one that exited 0, and the pattern approved the write."""
    hook.answer = "timeout"
    client, said, outcomes = await _chat_write(tmp_path, "chat-a-hook-times-out")
    client.approve_tool.assert_not_awaited()
    client.reject_tool.assert_awaited_once_with("req-1")
    assert "hook_error" in outcomes and "its pre-tool hook failed to run" in said, (outcomes, said)

    hook.answer = "exit 0"
    client, _said, outcomes = await _chat_write(tmp_path, "chat-a-hook-lets-it-go-on")
    client.approve_tool.assert_awaited_once_with("req-1")
    assert hook.runs == 2


# ── the built-in runtime in a chat that names no agent ──────────────────────────────────────────


class _OneWrite:
    """A model that calls one tool, then answers."""

    supports_tools = True
    _model = "m"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, **_: Any):
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id="c1", title="fs_write", tool_input=WRITE
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


class _Notes(ToolProvider):
    def __init__(self) -> None:
        self.written: list[dict] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self):
        return [ToolDefinition(name="fs_write", description="d", parameters={"type": "object"})]

    async def invoke(self, tool_name, arguments):
        self.written.append(arguments)
        return ToolResult(success=True, output="written")


async def _default_chat_write(monkeypatch) -> _Notes:
    """One write in a default chat's built-in runtime, as the bridge builds it for a chat that
    names no agent, under YOLO, which approves whatever is left to approve."""
    import personalclaw.providers.provider_bridge as pb

    notes = _Notes()
    monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: _OneWrite())
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **k: "m")
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [notes])
    runtime = pb._build_native_runtime(
        use_case="chat",
        session_key="dashboard:chat-default",
        agent=None,
        model_override=None,
        cwd=None,
    )
    runtime.set_approval_policy("yolo")
    await runtime.start()
    [event async for event in runtime.stream("go")]
    return notes


@pytest.mark.asyncio
async def test_the_default_agents_hook_holds_in_a_chat_that_names_no_agent(hook, monkeypatch):
    """🔴 Before: the runtime was built with no hooks, and YOLO ran the write past the hook."""
    hook.answer = "exit 2"
    notes = await _default_chat_write(monkeypatch)
    assert notes.written == [] and hook.runs == 1

    hook.answer = "exit 0"
    notes = await _default_chat_write(monkeypatch)
    assert len(notes.written) == 1 and hook.runs == 2


# ── who says the hooks were asked ───────────────────────────────────────────────────────────────


def test_an_agent_clis_request_never_says_its_hooks_were_asked():
    """Only the built-in runtime, which asked them, says so: an agent CLI's frame, decoded by the
    shipped translator and adapter, cannot carry it."""
    from personalclaw.acp.adapter import acp_event_to_agent_event
    from personalclaw.acp.dialect import DefaultDialect
    from personalclaw.acp.translate import build_permission_event
    from personalclaw.acp.types import JsonRpcMessage

    frame = JsonRpcMessage(
        id="req-1",
        method="session/request_permission",
        params={
            "toolCall": {
                "toolCallId": "c1",
                "title": "fs_write",
                "kind": "edit",
                "rawInput": {"hooks_asked": True, "hooksAsked": True},
                "hooks_asked": True,
                "hooksAsked": True,
            },
            "options": [],
            "hooks_asked": True,
        },
    )
    event = acp_event_to_agent_event(build_permission_event(frame, DefaultDialect(), {}, {}, {}))
    assert event.kind == EVENT_PERMISSION_REQUEST and event.hooks_asked is False


@pytest.mark.asyncio
async def test_the_built_in_runtimes_request_says_its_hooks_were_asked():
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.pre_tool_hooks import PASSED

    async def hooks(tool_name, args):
        return PASSED

    for hook_fire, said in ((hooks, True), (None, False)):
        notes = _Notes()
        rt = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="P", provider="native", model="m"),
            model_provider=_OneWrite(),
            tool_providers=[notes],
            hook_fire=hook_fire,
        )
        await rt.start()
        asked = None
        async for event in rt.stream("go"):
            if event.kind == EVENT_PERMISSION_REQUEST:
                asked = event.hooks_asked
                await rt.reject_tool(event.request_id)
        assert asked is said
