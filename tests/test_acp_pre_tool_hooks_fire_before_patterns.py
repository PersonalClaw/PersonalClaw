"""An agent CLI's call meets the pre-tool hooks before anything can approve it.

On the agent CLI path the chat runner decides a call the CLI asks about: the task mode and the deny
list refuse first, then something may approve it (an operator's auto-approve pattern, what the
call declares, Trust reads, Trust, YOLO, the owner on a card). A call an operator's pattern matched
was approved and the loop moved on before any `PreToolUse` hook fired, so a hook that would have
answered `BLOCKED:` never ran, while the built-in runtime fires its hooks ahead of every approval.

The hooks now run once, at that one step, before any approval: a hook that blocks the call, or that
fails to run, refuses it there, for an agent CLI's call and a built-in one alike. The last test runs
the same cases through both runtimes and asks for the same answers.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import HOOK_EVENT_PRE_TOOL_USE, HookManager, HooksConfig
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

WRITE = '{"path": "notes.md", "content": "x"}'
#: Each turn below is its own chat, with a history of its own: no turn reads another's.
_CHATS = itertools.count(1)


def _blocking_hook() -> MagicMock:
    return MagicMock(exit_code=2, stdout="", stderr="no writes today", hook_name="deny-writes")


# ── the chat runner, over one agent CLI permission request ──────────────────────────────────────


async def _iter(items):
    for item in items:
        yield item


@dataclass
class Turn:
    state: DashboardState
    client: AsyncMock
    session: _ChatSession
    audit: MagicMock
    #: The tool each `PreToolUse` fire was for.
    pre_tool_use: list[str]

    @property
    def outcomes(self) -> list[str]:
        calls = self.audit.return_value.log_tool_invocation.call_args_list
        return [c.kwargs.get("outcome", "") for c in calls]

    @property
    def asked_a_person(self) -> bool:
        return any(m.get("role") == "permission" for m in self.session.messages)


async def _chat_turn(
    tmp_path,
    *,
    patterns=(),
    hooks=None,
    hook_error=None,
    trust=False,
    task_mode="agent",
    title="fs_write",
    kind="edit",
    answer="rejected",
    on_hook=None,
) -> Turn:
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = "acp:agent-cli"
    del client.cancel_session
    client.undelivered_steers = MagicMock(return_value=[])  # a plain method on the real one
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    chat = f"chat-hooks-first-{next(_CHATS)}"
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / chat),
    )
    builder = MagicMock()
    builder.hooks = HookManager(HooksConfig(auto_approve_tools=list(patterns)))
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    session = _ChatSession(chat)
    session._trust = trust
    session._task_mode = task_mode

    pre_tool_use: list[str] = []

    async def fire_for_ids(event, *args, **kwargs):
        if event != HOOK_EVENT_PRE_TOOL_USE:
            return []  # the turn's other lifecycle events: nothing here answers them
        pre_tool_use.append(kwargs.get("tool_name", ""))
        if on_hook is not None:
            on_hook(session)
        if hook_error is not None:
            raise hook_error
        return list(hooks or [])

    state._hook_store = MagicMock(fire_for_ids=AsyncMock(side_effect=fire_for_ids))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title=title,
                    tool_kind=kind,
                    request_id="req-1",
                    tool_input=WRITE,
                ),
                # It answers, as an agent CLI's turn does: a turn that wrote nothing is sent
                # again on its own, and that run would be a second call behind this one.
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="done"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )

    async def answer_the_card():
        for _ in range(300):
            fut = session._approval_futures.get("req-1")
            if fut and not fut.done():
                fut.set_result(answer)
                return
            await asyncio.sleep(0.01)

    asyncio.get_event_loop().create_task(answer_the_card())
    audit = MagicMock()
    with patch("personalclaw.dashboard.chat_runner.sel", audit):
        await asyncio.wait_for(run_chat(state, session, "hello"), timeout=20)
    assert session.task is None, "the turn left another run of itself behind"
    return Turn(state, client, session, audit, pre_tool_use)


def _hooks_asked(turn: Turn) -> int:
    return len(turn.pre_tool_use)


@pytest.mark.asyncio
async def test_a_pattern_approved_cli_call_still_meets_a_blocking_hook(tmp_path):
    """The defect: the operator's pattern approved the write and no hook was asked."""
    passes = await _chat_turn(tmp_path, patterns=["fs_write"])
    passes.client.approve_tool.assert_awaited_once_with("req-1")
    assert _hooks_asked(passes) == 1 and not passes.asked_a_person, "premise: the pattern approves"

    blocked = await _chat_turn(tmp_path, patterns=["fs_write"], hooks=[_blocking_hook()])
    blocked.client.approve_tool.assert_not_awaited()
    blocked.client.reject_tool.assert_awaited_once_with("req-1")
    assert "hook_blocked" in blocked.outcomes and "auto_approved" not in blocked.outcomes
    said = " ".join(str(m.get("content", "")) for m in blocked.session.messages)
    assert "no writes today" in said, said


@pytest.mark.asyncio
async def test_a_hook_that_fails_to_run_refuses_a_pattern_approved_call(tmp_path):
    turn = await _chat_turn(
        tmp_path, patterns=["fs_write"], hook_error=RuntimeError("the hook could not start")
    )
    turn.client.approve_tool.assert_not_awaited()
    turn.client.reject_tool.assert_awaited_once_with("req-1")
    assert "hook_error" in turn.outcomes and "auto_approved" not in turn.outcomes


@pytest.mark.asyncio
async def test_the_hooks_run_once_and_before_the_owner_is_asked(tmp_path):
    """A call put to the owner meets the hooks at the same one step, before the card: a hook that
    would refuse it does so before anyone is asked, and an allowed call is not asked twice."""
    seen: list[bool] = []
    turn = await _chat_turn(
        tmp_path,
        answer="approved",
        on_hook=lambda session: seen.append(bool(session._approval_futures)),
    )
    turn.client.approve_tool.assert_awaited_once_with("req-1")
    assert turn.asked_a_person, "premise: nothing approved it but the owner"
    assert seen == [False], f"the hooks ran {len(seen)} times, with a card already open: {seen}"

    blocked = await _chat_turn(tmp_path, hooks=[_blocking_hook()], answer="approved")
    blocked.client.approve_tool.assert_not_awaited()
    assert not blocked.asked_a_person, "a call a hook refuses is not put to anyone"


# ── the built-in runtime, over the same cases ───────────────────────────────────────────────────


class _Model:
    supports_tools = True
    _model = "scripted"

    def __init__(self, tool: str) -> None:
        self._tool, self.calls = tool, 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id="c1", title=self._tool, tool_input=WRITE
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


class _Tool(ToolProvider):
    def __init__(self, name: str) -> None:
        self._name = name
        self.invoked: list[dict] = []

    @property
    def name(self) -> str:
        return "parity"

    @property
    def display_name(self) -> str:
        return "Parity"

    async def list_tools(self):
        return [ToolDefinition(name=self._name, description="d", parameters={"type": "object"})]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        return ToolResult(success=True, output="written")


async def _native_turn(*, tool, hooks=None, hook_error=None, task_mode="agent") -> tuple[str, bool]:
    """One built-in call under YOLO, which approves whatever is left to approve."""
    asked: list[str] = []

    async def hook_fire(tool_name, args_json):
        asked.append(tool_name)
        if hook_error is not None:
            raise hook_error
        return ["BLOCKED:deny-writes:no writes today"] if hooks else []

    provider = _Tool(tool)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="P", provider="native", model="scripted"),
        model_provider=_Model(tool),
        tool_providers=[provider],
        hook_fire=hook_fire,
    )
    rt.set_approval_policy("yolo")
    rt.set_task_mode(task_mode)
    await rt.start()
    events = [ev async for ev in rt.stream("go")]
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events), "YOLO asks nobody"
    return ("ran" if provider.invoked else "refused"), bool(asked)


#: (case, how it is set up) — the same on both runtimes.
CASES = {
    "nothing objects": {"tool": "fs_write"},
    "a hook blocks": {"tool": "fs_write", "hooks": True},
    "a hook fails to run": {"tool": "fs_write", "hook_error": RuntimeError("no store")},
    "the deny list refuses": {"tool": "get_secret_value"},
    "the task mode refuses": {"tool": "fs_write", "task_mode": "ask"},
}

#: The step the hooks sit at, as both runtimes must answer it: (outcome, whether a hook was asked).
EXPECTED = {
    "nothing objects": ("ran", True),
    "a hook blocks": ("refused", True),
    "a hook fails to run": ("refused", True),
    "the deny list refuses": ("refused", False),
    "the task mode refuses": ("refused", False),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(CASES))
async def test_both_runtimes_ask_the_hooks_at_the_same_step(tmp_path, case):
    """Past the deny list and the task mode, before every approval: the built-in runtime under
    YOLO, and the chat runner under an operator's pattern and under Trust, answer alike."""
    setup = CASES[case]
    native = await _native_turn(
        tool=setup["tool"],
        hooks=setup.get("hooks"),
        hook_error=setup.get("hook_error"),
        task_mode=setup.get("task_mode", "agent"),
    )
    assert native == EXPECTED[case], f"built-in runtime: {native}"
    for grant in ("pattern", "trust"):
        turn = await _chat_turn(
            tmp_path,
            title=setup["tool"],
            patterns=[setup["tool"]] if grant == "pattern" else (),
            trust=grant == "trust",
            hooks=[_blocking_hook()] if setup.get("hooks") else None,
            hook_error=setup.get("hook_error"),
            task_mode=setup.get("task_mode", "agent"),
        )
        assert not turn.asked_a_person, f"{grant}: a grant approves this call or nothing does"
        chat = (
            "ran" if turn.client.approve_tool.await_count else "refused",
            _hooks_asked(turn) > 0,
        )
        assert chat == EXPECTED[case], f"agent CLI under {grant}: {chat}"
