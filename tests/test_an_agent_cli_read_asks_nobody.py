"""A call that declares it only reads asks nobody over an agent CLI too.

A native agent never asks about a tool that declares it only reads (``memory_recall``,
``workflow_status``): the declaration is the answer. An agent CLI (claude-code, codex) asks the
host about EVERY call, and a call to one of PersonalClaw's own tools carries that tool's
declaration (``acp.mcp_servers.core_tool_declaration``). The host approved such a read only under
Trust reads, so in a Normal chat the owner was asked about ``memory_recall`` over claude-code
while the same call in a native chat ran unasked, and a background agent's read was relayed to the
owner.

Now the host answers a declared read itself, at each place that would otherwise ask a person: the
chat's gate, a background agent's gate and a room member's gate, each past the refusals that come
first (task mode, deny-list, hooks, tool grants, a member's tier). What decides is the
DECLARATION, never the effective risk: a read-only shell command stays Trust reads' to approve, as
it is in a native chat, and a CLI's own tool, which declares nothing, still asks.
"""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_acp_permission_authority import (
    _context_builder,
    _drive,
    _make_state,
    _session,
    _set_stream,
)

from personalclaw import approval_grants
from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.dialect import DefaultDialect
from personalclaw.acp.translate import build_permission_event
from personalclaw.acp.types import JsonRpcMessage
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

_CORE = "personalclaw-core"


def _asked_by_claude_code(tool: str, args: dict) -> LLMEvent:
    """The permission request claude-code sends for a call to one of PersonalClaw's own tools,
    decoded by the shipped translator and adapter — so the declaration it carries is the one
    production attaches, not one this test wrote."""
    title = f"mcp__{_CORE}__{tool}"
    msg = JsonRpcMessage(
        id="req-1",
        method="session/request_permission",
        params={
            "toolCall": {"toolCallId": "c1", "title": title, "kind": "other", "rawInput": args},
            "options": [],
        },
    )
    return acp_event_to_agent_event(build_permission_event(msg, DefaultDialect(), {}, {}, {}))


def _read() -> LLMEvent:
    return _asked_by_claude_code("memory_recall", {"query": "the dishwasher"})


def _change() -> LLMEvent:
    return _asked_by_claude_code("memory_remember", {"rule": "always x", "category": "tool"})


def _shell_read() -> LLMEvent:
    """A read-only shell command, which declares nothing: Trust reads' to approve."""
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="ls",
        tool_kind="execute",
        request_id="req-1",
        tool_input='{"command": "ls -la /tmp"}',
    )


def _cli_own_read() -> LLMEvent:
    """A CLI's own read tool: kind ``read``, and no declaration."""
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="Read notes.md",
        tool_kind="read",
        request_id="req-1",
        tool_input='{"path": "/tmp/notes.md"}',
    )


def test_the_declarations_are_the_ones_production_attaches():
    """The premise, measured: without it every test below would pass on a request that carried
    no declaration at all."""
    assert _read().risk_level == "safe"
    assert _change().risk_level == "caution"
    assert _shell_read().risk_level == "" and _cli_own_read().risk_level == ""


# ── a chat ───────────────────────────────────────────────────────────────────


async def _turn(tmp_path, request: LLMEvent, *, trust_reads: bool = False, answer=None):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=False)
    session._trust_reads = trust_reads
    _set_stream(client, [request, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    rows = MagicMock()
    await _drive(state, session, answer=answer, sel_mock=rows)
    decided = [
        c.kwargs.get("metadata", {}).get("decided_by")
        for c in rows.return_value.log_tool_invocation.call_args_list
        if c.kwargs.get("outcome") == "auto_approved"
    ]
    asked = any(m.get("role") == "permission" for m in session.messages)
    return client, asked, decided


@pytest.mark.asyncio
async def test_a_normal_chat_is_not_asked_about_a_declared_read(tmp_path):
    """🔴 Before: the card was raised, and the call waited on the owner."""
    client, asked, decided = await _turn(tmp_path, _read(), answer="denied")
    assert asked is False
    client.approve_tool.assert_awaited_once_with("req-1")
    client.reject_tool.assert_not_awaited()
    assert decided == [approval_grants.DECLARED_READ]


@pytest.mark.asyncio
async def test_a_normal_chat_is_still_asked_about_a_declared_change(tmp_path):
    client, asked, decided = await _turn(tmp_path, _change(), answer="denied")
    assert asked is True
    client.reject_tool.assert_awaited_once()
    client.approve_tool.assert_not_awaited()
    assert decided == []


@pytest.mark.asyncio
async def test_a_cli_tool_that_declares_nothing_still_asks(tmp_path):
    """Its kind says ``read``, and a kind is the CLI's word, not a declaration."""
    client, asked, _ = await _turn(tmp_path, _cli_own_read(), answer="denied")
    assert asked is True
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_read_only_shell_command_is_trust_reads_to_approve(tmp_path):
    """The declaration decides, not the effective risk: in a Normal chat the command asks, as a
    native chat's does; under Trust reads it runs, and the audit row says which grant ran it."""
    client, asked, _ = await _turn(tmp_path, _shell_read(), answer="denied")
    assert asked is True
    client.approve_tool.assert_not_awaited()

    client, asked, decided = await _turn(tmp_path, _shell_read(), trust_reads=True)
    assert asked is False
    client.approve_tool.assert_awaited_once_with("req-1")
    assert decided == [approval_grants.TRUST_READS]


@pytest.mark.asyncio
async def test_ask_mode_still_refuses_a_declared_change_before_anything_approves(tmp_path):
    """The refusals that come first still come first."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="ask", trust=False)
    _set_stream(client, [_change(), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    await _drive(state, session)
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_hook_that_denies_the_read_still_denies_it(tmp_path):
    from personalclaw.hooks import ToolHookResult

    def _deny(title, *, cwd=None, command=None):
        if "memory_recall" in title:
            return ToolHookResult.deny("not this one")
        return ToolHookResult.allow()

    state, client = _make_state(tmp_path, context_builder=_context_builder(on_tool_call=_deny))
    session = _session(task_mode="agent", trust=False)
    _set_stream(client, [_read(), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    await _drive(state, session)
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()


# ── a background agent ───────────────────────────────────────────────────────


async def _background(request: LLMEvent):
    """One background agent's turn over an agent CLI, with nobody's grant standing: every call
    that asks reaches the relay, which records it and answers no."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    import personalclaw.config.loader as loader
    from personalclaw.hooks import ToolHookResult
    from personalclaw.subagent import SubagentInfo, SubagentManager

    (loader.config_dir() / "config.json").write_text(
        json.dumps({"agent": {"approval_mode": "interactive"}}), encoding="utf-8"
    )
    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    client = sessions.get_or_create.return_value[0]

    async def _stream(*_a, **_kw):
        yield request
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    ctx = _mock_ctx_builder()
    ctx.hooks.on_tool_call = MagicMock(return_value=ToolHookResult.allow())
    ctx.hooks.auto_approve_subagent_tools = False
    relayed = AsyncMock(return_value=False)
    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False, on_tool_approval=relayed
    )
    info = SubagentInfo(id="bg0001", task="look it up", parent_session_key="")
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:bg0001")
    return client, relayed


@pytest.mark.asyncio
async def test_a_background_agents_declared_read_is_not_relayed_to_you():
    """🔴 Before: the read was relayed to the owner, like a write."""
    client, relayed = await _background(_read())
    relayed.assert_not_awaited()
    client.approve_tool.assert_awaited_once_with("req-1")
    client.reject_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_background_agents_shell_read_is_still_relayed():
    """The control: a call that declares nothing reaches the person it always did."""
    client, relayed = await _background(replace(_shell_read(), request_id="req-1"))
    relayed.assert_awaited_once()
    client.approve_tool.assert_not_awaited()
