"""An automation's own agent can tell the owner what it found, and do nothing more.

A trigger that runs an agent task gives that agent a `read` grant: it may read and file proposals,
and nothing else, because nobody is watching it. `notify` is a change (it sends a message), so the
grant refused it, and an automation that found something had no way to say so: "every morning,
check my inbox and message me what matters" read the inbox and told nobody.

`notify` now declares the arguments a call may carry and still only tell the owner (its text, a
title and the chat channel to use). An automation's agent is granted those calls. A call that
names a channel id, a user, a thread or a chat to inject into still reaches someone else, so it is
still refused, and a research run that is not an automation's gets nothing new.
"""

from __future__ import annotations

from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.mcp_servers import core_tool_declaration
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.guardrails.policy import (
    TOOL_READ,
    SafetyProfile,
    declared_tool_grant_denial,
)
from personalclaw.hooks import TOOL_AUTO_APPROVE, ToolHookResult
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)
from personalclaw.llm.events import AgentEvent
from personalclaw.mcp_core import OWNER_NOTICE_ARGS
from personalclaw.subagent import SubagentManager
from personalclaw.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
    only_tells_the_owner,
)

RESEARCH = SafetyProfile(name="spawn_research", tool_grants=TOOL_READ)


# ── what a call that only tells the owner is ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        {"text": "Two new invoices."},
        {"text": "Two new invoices.", "title": "Inbox", "via": "telegram"},
        '{"text": "Two new invoices."}',
        {"text": "Two new invoices.", "channel": "", "thread_ts": None, "reply_broadcast": False},
    ],
)
def test_a_notice_to_the_owner_sets_no_other_argument(args):
    assert only_tells_the_owner(OWNER_NOTICE_ARGS, args) is True


@pytest.mark.parametrize(
    "args",
    [
        {"text": "hi", "channel": "C0123ABC456"},
        {"text": "hi", "user": "U0123ABC456"},
        {"text": "hi", "session": "origin"},
        {"text": "hi", "blocks": '[{"type": "section"}]'},
        {"text": "hi", "thread_ts": "1712793600.1"},
        "not json",
        ["text"],
    ],
)
def test_a_call_that_reaches_anyone_else_is_not_one(args):
    assert only_tells_the_owner(OWNER_NOTICE_ARGS, args) is False


def test_a_tool_that_declares_nothing_never_only_tells_the_owner():
    assert only_tells_the_owner((), {"text": "hi"}) is False


@pytest.mark.asyncio
async def test_notify_is_the_one_core_tool_that_declares_it():
    """The declaration reaches the native runtime from the tool's own definition."""
    tools = await InProcessMcpToolProvider().list_tools()
    declaring = {t.name: t.tells_owner for t in tools if t.tells_owner}
    assert declaring == {"notify": OWNER_NOTICE_ARGS}
    (notify,) = [t for t in tools if t.name == "notify"]
    assert set(OWNER_NOTICE_ARGS) <= set(notify.parameters["properties"])
    assert notify.risk_level is RiskLevel.CAUTION, "notify is still a change: every posture asks"


# ── the grant ─────────────────────────────────────────────────────────────────────────────────


def test_an_automations_grant_admits_a_notice_and_nothing_more():
    deny = partial(declared_tool_grant_denial, RESEARCH, owner_notices=True)
    assert deny("notify", "caution", "", {"text": "hi"}, tells_owner=True) == ""
    assert "write-class" in deny("notify", "caution", "", {"text": "hi", "channel": "C1"})
    assert "write-class" in deny("write_file", "caution", "", {"path": "x"})


def test_any_other_read_grant_still_refuses_a_notice():
    """A research subagent the chat spawned, a workflow's research leaf, a room's critic."""
    assert "write-class" in declared_tool_grant_denial(
        RESEARCH, "notify", "caution", "", {"text": "hi"}, tells_owner=True
    )


# ── an ACP agent's call to our own notify ─────────────────────────────────────────────────────


def test_an_acp_call_to_notify_says_whether_it_only_tells_the_owner():
    title = "mcp__personalclaw-core__notify"
    assert core_tool_declaration(title, "", {"text": "hi", "via": "telegram"})[3] is True
    assert core_tool_declaration(title, "", {"text": "hi", "channel": "C0123ABC456"})[3] is False
    kiro = "Running: @personalclaw-core/notify"
    assert (
        core_tool_declaration(kiro, "execute", {"text": "hi"})[3] is False
    ), "a shell call can wear kiro's title: it is no notice"


# ── the subagent an automation starts ─────────────────────────────────────────────────────────


@pytest.fixture()
def agent_root(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path)
    return tmp_path


def _manager(event: LLMEvent) -> tuple[SubagentManager, AsyncMock]:
    """A subagent whose ACP agent asks about one call; the hook would approve anything the
    grant admits, so the grant alone decides."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    provider = AsyncMock()
    provider.context_usage_pct = lambda: 0.0

    async def _stream(*_a, **_kw):
        yield event
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield LLMEvent(kind=EVENT_COMPLETE)

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.has_session = MagicMock(return_value=False)
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("msg", None))
    ctx.hooks.on_tool_call = MagicMock(return_value=ToolHookResult(action=TOOL_AUTO_APPROVE))
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.hooks.auto_approve_subagent_tools = False
    return SubagentManager(sessions=sessions, ctx_builder=ctx), provider


def _asks_to_notify(*, tells_owner: bool) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="mcp__personalclaw-core__notify",
        request_id=1,
        risk_level="caution",
        tells_owner=tells_owner,
    )


async def _run(manager: SubagentManager, *, trigger_id: str) -> None:
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
        patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
    ):
        info = manager.spawn(
            "check the inbox", parent_session_key="", approval_mode="auto", trigger_id=trigger_id
        )
        assert info is not None
        await manager._tasks[info.id]


@pytest.mark.asyncio
async def test_an_automations_agent_tells_the_owner(agent_root):
    """🔴 Before: refused, "notify is write-class and the 'spawn_research' profile grants 'read'
    tools only", and the owner heard nothing."""
    manager, provider = _manager(_asks_to_notify(tells_owner=True))
    await _run(manager, trigger_id="clock:morning-inbox")
    provider.approve_tool.assert_awaited_once_with(1)
    provider.reject_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_automations_agent_may_not_post_anywhere_else(agent_root):
    manager, provider = _manager(_asks_to_notify(tells_owner=False))
    await _run(manager, trigger_id="clock:morning-inbox")
    provider.reject_tool.assert_awaited_once_with(1)
    provider.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_research_run_that_is_no_automations_gets_nothing_new(agent_root):
    manager, provider = _manager(_asks_to_notify(tells_owner=True))
    await _run(manager, trigger_id="")
    provider.reject_tool.assert_awaited_once_with(1)
    provider.approve_tool.assert_not_awaited()


# ── the native runtime, which answers its own approvals ───────────────────────────────────────


class _Notifier(ToolProvider):
    """A `notify` that declares its notice arguments, as the real one does; `sent` proves a call
    ran rather than being refused."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    @property
    def name(self) -> str:
        return "core"

    @property
    def display_name(self) -> str:
        return "Core"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="notify",
                description="d",
                parameters={"type": "object"},
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                tells_owner=OWNER_NOTICE_ARGS,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.sent.append(arguments)
        return ToolResult(success=True, output="sent")


class _Scripted:
    supports_tools = True
    _model = "scripted"

    def __init__(self, args: str) -> None:
        self._args = args
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id="c1", title="notify", tool_input=self._args
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield AgentEvent(kind=EVENT_COMPLETE)


async def _native(args: str, *, owner_notices: bool) -> tuple[_Notifier, list]:
    notifier = _Notifier()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Scripted(args),
        tool_providers=[notifier],
    )
    await rt.start()
    rt.set_tool_grants(partial(declared_tool_grant_denial, RESEARCH, owner_notices=owner_notices))
    events = [ev async for ev in rt.stream("go")]
    return notifier, [e for e in events if e.kind == EVENT_TOOL_RESULT]


@pytest.mark.asyncio
async def test_the_native_runtime_hands_the_grant_what_the_call_does():
    notifier, _ = await _native('{"text": "Two new invoices."}', owner_notices=True)
    assert notifier.sent == [{"text": "Two new invoices."}]

    notifier, results = await _native('{"text": "hi", "channel": "C0123"}', owner_notices=True)
    assert notifier.sent == [], "a post to a channel ran under a read grant"
    assert "write-class" in str(results[0].tool_output)
