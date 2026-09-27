"""A tool call is audited once, when it is decided, and the row says what was decided and by whom.

🔴 The card of a call (``EVENT_TOOL_CALL``) arrives BEFORE any gate runs: the native loop yields it
and only then checks its deny-list, its task mode and the approval. Every runtime wrote a row there
anyway. The subagent manager and the background helper (``llm_helpers.stream_and_collect``, which
runs heartbeat tasks and a subagent's announce turn) wrote ``auto_approved`` for every call — a call
the deny-list refused, and one a person allowed, read the same. The chat wrote ``invoked``, so a
refused call read as run. And a call the native runtime refused, or declined because nobody could be
asked, got no decision row at all.

Now each call gets ONE row, written where it is decided:

* asked — where the answer lands: ``approved`` or ``rejected`` by ``you``, ``expired`` or
  ``cancelled`` by ``nobody``, or ``auto_approved`` by the grant that answered it;
* not asked — at its result, from what the runtime stamped (``llm.events.unasked_outcome``):
  ``denied`` by the gate that refused it, ``auto_approved`` by the policy that waived its ask, or
  ``invoked`` for a tool that asks nobody.

Driven through the REAL ``NativeAgentRuntime`` in each host — the chat's turn engine
(``run_chat``, which a channel's turn also runs), the subagent manager (which runs trigger agents
and every workflow stage), and the background helper — and read back from the real audit log.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.approval_answer import YOU
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.hooks import HookManager
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.llm_helpers import ToolApprovalPolicy, stream_and_collect
from personalclaw.memory import MemoryStore
from personalclaw.sel import sel
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

#: A tool the built-in deny-list refuses before anything asks (`security.is_denied`).
REFUSED = "delete_stack"
#: A tool that asks before it runs.
ASKS = "write_note"


class _Tools(ToolProvider):
    """Both tools, each asking first, recording what actually ran."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "probe"

    @property
    def display_name(self) -> str:
        return "Probe"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=n, description="d", parameters={"type": "object"}, requires_approval=True
            )
            for n in (REFUSED, ASKS)
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output="done")


def _calls(tool: str, *, turns: int = 1, each: int = 1) -> _ScriptedModel:
    """A model that, in each of *turns* turns, calls *tool* *each* times, one after the other,
    and then answers."""
    script: list[list[AgentEvent]] = []
    for turn in range(1, turns + 1):
        for n in range(1, each + 1):
            call = AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"call-{turn}-{n}",
                title=tool,
                tool_input='{"text": "hello"}',
            )
            script.append([call, AgentEvent(kind=EVENT_COMPLETE)])
        script.append(
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)]
        )
    return _ScriptedModel(script)


def _rows(tool: str) -> list[dict[str, Any]]:
    """Every audit row about *tool*, oldest first."""
    return [
        r
        for r in reversed(sel().recent(500))
        if r.get("event_type") == "tool_invocation" and r.get("operation") == tool
    ]


def _decided(row: dict[str, Any]) -> tuple[str, str]:
    return row.get("outcome", ""), (row.get("metadata") or {}).get("decided_by", "")


# ── the chat's turn engine (a channel's turn runs it too) ──────────────────────────────────────


async def _chat(
    tmp_path: Path, tool: str, *, turns: int = 1
) -> tuple[DashboardState, SessionManager, _Tools]:
    tools = _Tools()

    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=_calls(tool, turns=turns),
            tool_providers=[tools],
            cwd=tmp_path,
        )

    # The real session manager, so what the chat pushes to its runtime (its Trust as the
    # runtime's policy, its task mode) takes the path it takes in production.
    sessions = SessionManager(AppConfig(), provider_factory=factory)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    # A hook store with no hooks: an absent store BLOCKS every PreToolUse (fail closed).
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, sessions, tools


async def _answer_when_asked(state: DashboardState, session, action: str) -> None:
    """A person answers the chat's approval card, once it is asked."""
    for _ in range(400):
        pending = [k for k, f in session._approval_futures.items() if not f.done()]
        if pending:
            state.decide_session_approval(session, pending[0], action, by=YOU)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never asked")


async def _turn(state: DashboardState, session, *, answer: str = "") -> None:
    session.append("user", "go", "msg msg-u")
    answering = (
        asyncio.ensure_future(_answer_when_asked(state, session, answer)) if answer else None
    )
    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)
    if answering is not None:
        await answering


@pytest.mark.asyncio
async def test_chat_a_call_the_deny_list_refused_is_one_denied_row(tmp_path):
    state, _, tools = await _chat(tmp_path, REFUSED)
    await _turn(state, state.get_or_create_session("c-refused"))
    assert tools.ran == []
    assert [_decided(r) for r in _rows(REFUSED)] == [("denied", "deny_list")], _rows(REFUSED)


@pytest.mark.asyncio
async def test_chat_a_call_a_person_allowed_is_one_approved_row(tmp_path):
    state, _, tools = await _chat(tmp_path, ASKS)
    await _turn(state, state.get_or_create_session("c-asked"), answer="approved")
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("approved", "you")], _rows(ASKS)


@pytest.mark.asyncio
async def test_chat_a_call_nobody_answered_is_one_expired_row(tmp_path):
    state, _, tools = await _chat(tmp_path, ASKS)
    state.approval_window_secs = lambda: 0.2  # type: ignore[method-assign]
    await _turn(state, state.get_or_create_session("c-expired"))
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("expired", "nobody")], _rows(ASKS)


@pytest.mark.asyncio
async def test_chat_a_call_its_trust_approved_is_one_auto_approved_row(tmp_path):
    state, _, tools = await _chat(tmp_path, ASKS)
    session = state.get_or_create_session("c-trusted")
    session._trust = True
    await _turn(state, session)
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("auto_approved", "trust")], _rows(ASKS)


@pytest.mark.asyncio
async def test_a_channel_s_answer_is_a_person_s_too(tmp_path):
    """A channel answers through the registry door (`resolve_approval`), and it is one row."""
    from personalclaw.dashboard.approval_state import chat_approval_id

    state, _, tools = await _chat(tmp_path, ASKS)
    session = state.get_or_create_session("c-channel")

    async def from_the_channel() -> None:
        for _ in range(400):
            pending = [k for k, f in session._approval_futures.items() if not f.done()]
            if pending:
                assert state.resolve_approval(
                    chat_approval_id(session.key, pending[0]), True, by=YOU
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the chat never asked")

    session.append("user", "go", "msg msg-u")
    answering = asyncio.ensure_future(from_the_channel())
    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)
    await answering
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("approved", "you")], _rows(ASKS)


# ── the subagent manager: trigger agents, workflow stages ──────────────────────────────────────


def _subagents(
    tool: str, *, relay: Any = None, each: int = 1, tools: _Tools | None = None
) -> tuple[SubagentManager, _Tools]:
    probe = tools or _Tools()

    def factory(_key: Any = None, **kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=_calls(tool, each=each),
            tool_providers=[probe],
            unattended=bool(kw.get("unattended")),
        )

    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks = HookManager()
    manager = SubagentManager(
        sessions=SessionManager(AppConfig(), provider_factory=factory),
        ctx_builder=ctx,
        on_tool_approval=relay,
        is_yolo=lambda: False,
    )
    return manager, probe


async def _run(manager: SubagentManager, info: SubagentInfo) -> None:
    await asyncio.wait_for(manager._run_inner(info, f"subagent:{info.id}"), timeout=20)


@pytest.mark.asyncio
async def test_subagent_a_call_the_deny_list_refused_is_one_denied_row():
    manager, tools = _subagents(REFUSED)
    await _run(manager, SubagentInfo(id="sa-ref", task="t", parent_session_key="dashboard:chat-a"))
    assert tools.ran == []
    assert [_decided(r) for r in _rows(REFUSED)] == [("denied", "deny_list")], _rows(REFUSED)


def _answered(approved: bool, outcome: str, by: str) -> Any:
    """The gateway relay's answer: what was decided and who decided (`approval_grants`)."""
    from personalclaw.approval_grants import ToolDecision

    return AsyncMock(return_value=ToolDecision(approved, outcome, by))


@pytest.mark.asyncio
async def test_subagent_a_call_a_person_allowed_is_one_approved_row():
    manager, tools = _subagents(ASKS, relay=_answered(True, "approved", "you"))
    await _run(manager, SubagentInfo(id="sa-ask", task="t", parent_session_key="dashboard:chat-a"))
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("approved", "you")], _rows(ASKS)


@pytest.mark.asyncio
async def test_a_workflow_step_s_call_nobody_answered_is_one_expired_row():
    """A stage runs as a subagent whose parent is its run; its expired approval says `expired`."""
    manager, tools = _subagents(ASKS, relay=_answered(False, "expired", "nobody"))
    await _run(
        manager, SubagentInfo(id="sa-exp", task="t", parent_session_key="workflow:run-7:write")
    )
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("expired", "nobody")], _rows(ASKS)


@pytest.mark.asyncio
async def test_subagent_a_call_its_own_approval_mode_approved_is_one_auto_approved_row():
    """A trigger agent spawned `approval_mode: auto` and granted writes: its runtime answers each
    ask from the grant, and the row names the grant."""
    manager, tools = _subagents(ASKS)
    await _run(
        manager,
        SubagentInfo(id="sa-auto", task="t", approval_mode="auto", capability_class="mutating"),
    )
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("auto_approved", "approval_mode")], _rows(ASKS)


# ── the background helper: heartbeat tasks, a subagent's announce turn ────────────────────────


async def _background(tool: str, **kw: Any) -> _Tools:
    tools = _Tools()
    runtime = NativeAgentRuntime(
        definition=_defn(), model_provider=_calls(tool), tool_providers=[tools]
    )
    await runtime.start()
    await asyncio.wait_for(stream_and_collect(runtime, "go", **kw), timeout=20)
    return tools


@pytest.mark.asyncio
async def test_background_a_call_the_deny_list_refused_is_one_denied_row():
    tools = await _background(REFUSED, approval_policy=ToolApprovalPolicy.AUTO_APPROVE)
    assert tools.ran == []
    assert [_decided(r) for r in _rows(REFUSED)] == [("denied", "deny_list")], _rows(REFUSED)


@pytest.mark.asyncio
async def test_background_a_call_its_callback_allowed_is_one_approved_row():
    tools = await _background(
        ASKS,
        approval_policy=ToolApprovalPolicy.HOOK_BASED,
        on_tool_approval=AsyncMock(return_value=True),
    )
    assert tools.ran == [ASKS]
    # A plain True from a callback does not say who answered, so the row does not guess.
    assert [_decided(r) for r in _rows(ASKS)] == [("approved", "")], _rows(ASKS)


@pytest.mark.asyncio
async def test_background_a_call_its_policy_approved_is_one_auto_approved_row():
    tools = await _background(ASKS, approval_policy=ToolApprovalPolicy.AUTO_APPROVE)
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("auto_approved", "session_policy")], _rows(ASKS)
    assert _rows(ASKS)[0]["metadata"]["reason"] == "auto_approve"


@pytest.mark.asyncio
async def test_background_nothing_runs_under_reject_all_and_the_row_says_so():
    tools = await _background(ASKS, approval_policy=ToolApprovalPolicy.REJECT_ALL)
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("rejected", "reject_all_policy")], _rows(ASKS)
