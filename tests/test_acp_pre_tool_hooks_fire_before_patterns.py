"""Every path that runs a tool call asks the operator's blocking hooks at the same one step.

On the agent CLI path the chat runner decides a call the CLI asks about: the task mode and the deny
list refuse first, then something may approve it (an operator's auto-approve pattern, what the
call declares, Trust reads, Trust, YOLO, the owner on a card). A call an operator's pattern matched
was approved and the loop moved on before any `PreToolUse` hook fired, so a hook that would have
answered `BLOCKED:` never ran, while the built-in runtime fires its hooks ahead of every approval.

The hooks now run once, at that one step (`pre_tool_hooks`), before any approval: a hook that
blocks the call, or that fails to run, refuses it there. Every other path that runs a call asks
them at the same step: a subagent's gate over an agent CLI, the background helper's (a heartbeat
task, a subagent's report, a room member's turn), an evaluation's, and a channel's own turn, which
asks through the SDK. The last test runs the same cases through every path and asks for the same
answers.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import approval_grants, pre_tool_hooks
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.approval_grants import ToolDecision
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import (
    HOOK_EVENT_PRE_TOOL_USE,
    HookManager,
    HooksConfig,
    ScriptHookResult,
)
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

WRITE = '{"path": "notes.md", "content": "x"}'
#: Each turn below is its own chat, with a history of its own: no turn reads another's.
_CHATS = itertools.count(1)


def _blocking_hook() -> MagicMock:
    return MagicMock(exit_code=2, stdout="", stderr="no writes today", hook_name="deny-writes")


def _hook_that_did_not_run() -> ScriptHookResult:
    """A hook whose command never exited: it timed out (``hooks.run_script_hook``)."""
    return ScriptHookResult(
        hook_id="h1",
        hook_name="deny-writes",
        event=HOOK_EVENT_PRE_TOOL_USE,
        exit_code=-1,
        error="Timed out after 30s",
    )


class _Hooks:
    """The hook store every path fires through, answering each pre-tool fire as a case says:
    nothing against the call (``""``), a hook that ``blocks`` it, one that ``fails`` to run (the
    fire raises), or one that ``does not run`` to an exit of its own."""

    def __init__(self, answer: str = "", on_fire=None) -> None:
        self.answer = answer
        self.on_fire = on_fire
        #: The tool each pre-tool fire was for.
        self.asked: list[str] = []
        self.fire_for_ids = AsyncMock(side_effect=self._fire_for_ids)
        self.fire = AsyncMock(return_value=[])

    async def _fire_for_ids(self, event, hook_ids, context="", **kwargs):
        if event != HOOK_EVENT_PRE_TOOL_USE:
            return []  # the turn's other lifecycle events: nothing here answers them
        self.asked.append(kwargs.get("tool_name", ""))
        if self.on_fire is not None:
            self.on_fire()
        if self.answer == "fails":
            raise RuntimeError("the hook could not start")
        if self.answer == "blocks":
            return [_blocking_hook()]
        if self.answer == "does not run":
            return [_hook_that_did_not_run()]
        return []


@pytest.fixture
def the_store(monkeypatch):
    """The gateway's own store, which every path that names none fires through."""
    store = _Hooks()
    monkeypatch.setattr("personalclaw.hooks._global_script_hook_store", store)
    return store


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
    store: _Hooks

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
    store: _Hooks | None = None,
    patterns=(),
    trust=False,
    task_mode="agent",
    title="fs_write",
    kind="edit",
    answer="rejected",
    on_hook=None,
) -> Turn:
    store = store or _Hooks()
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
    if on_hook is not None:
        store.on_fire = lambda: on_hook(session)
    state._hook_store = store
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
    return Turn(state, client, session, audit, store)


@pytest.mark.asyncio
async def test_a_pattern_approved_cli_call_still_meets_a_blocking_hook(tmp_path):
    """The defect: the operator's pattern approved the write and no hook was asked."""
    passes = await _chat_turn(tmp_path, patterns=["fs_write"])
    passes.client.approve_tool.assert_awaited_once_with("req-1")
    assert len(passes.store.asked) == 1 and not passes.asked_a_person, "premise: it approves"

    blocked = await _chat_turn(tmp_path, store=_Hooks("blocks"), patterns=["fs_write"])
    blocked.client.approve_tool.assert_not_awaited()
    blocked.client.reject_tool.assert_awaited_once_with("req-1")
    assert "hook_blocked" in blocked.outcomes and "auto_approved" not in blocked.outcomes
    said = " ".join(str(m.get("content", "")) for m in blocked.session.messages)
    assert "no writes today" in said, said


@pytest.mark.asyncio
async def test_a_hook_that_fails_to_run_refuses_a_pattern_approved_call(tmp_path):
    for answer in ("fails", "does not run"):
        turn = await _chat_turn(tmp_path, store=_Hooks(answer), patterns=["fs_write"])
        turn.client.approve_tool.assert_not_awaited()
        turn.client.reject_tool.assert_awaited_once_with("req-1")
        assert "hook_error" in turn.outcomes and "auto_approved" not in turn.outcomes, answer
        said = " ".join(str(m.get("content", "")) for m in turn.session.messages)
        assert pre_tool_hooks.HOOK_FAILED in said, said


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

    blocked = await _chat_turn(tmp_path, store=_Hooks("blocks"), answer="approved")
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


async def _native_path(tool: str, store: _Hooks, *, earlier: bool = False) -> str:
    """One built-in call under YOLO, which approves whatever is left to approve. Its hooks are
    the step the bridge hands the runtime (`provider_bridge`), firing through *store*; *earlier*
    is a task mode that refuses the write first."""
    provider = _Tool(tool)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="P", provider="native", model="scripted"),
        model_provider=_Model(tool),
        tool_providers=[provider],
        hook_fire=partial(pre_tool_hooks.on_tool, agent=None, store=store),
    )
    rt.set_approval_policy("yolo")
    rt.set_task_mode("ask" if earlier else "agent")
    await rt.start()
    events = [ev async for ev in rt.stream("go")]
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events), "YOLO asks nobody"
    return "ran" if provider.invoked else "refused"


# ── the other host gates, each over one agent CLI permission request ────────────────────────────


def _request(tool: str, kind: str = "edit", tool_input: str = WRITE) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=tool,
        tool_kind=kind,
        request_id="req-1",
        tool_call_id="c1",
        tool_input=tool_input,
    )


async def _chat_path(tmp_path, tool: str, store: _Hooks, *, grant: str, earlier=False) -> str:
    turn = await _chat_turn(
        tmp_path,
        store=store,
        title=tool,
        patterns=[tool] if grant == "pattern" else (),
        trust=grant == "Trust",
        task_mode="ask" if earlier else "agent",
    )
    assert not turn.asked_a_person, f"{grant}: a grant approves this call or nothing does"
    return "ran" if turn.client.approve_tool.await_count else "refused"


async def _subagent_path(tool: str, store: _Hooks, *, grant: str, earlier=False) -> str:
    """A subagent's turn over an agent CLI: an operator's pattern or the owner, through the relay,
    would approve its call. *earlier* is a research tier, which refuses a write first."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentInfo, SubagentManager

    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    client = sessions.get_or_create.return_value[0]
    client.provider_id = "acp:agent-cli"
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [_request(tool), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]
        )
    )
    ctx = _mock_ctx_builder()
    ctx.hooks = HookManager(HooksConfig(auto_approve_tools=[tool] if grant == "pattern" else []))
    relayed = AsyncMock(return_value=ToolDecision(True, "approved", approval_grants.YOU))
    manager = SubagentManager(
        sessions=sessions,
        ctx_builder=ctx,
        is_yolo=lambda: False,
        on_tool_approval=relayed if grant == "the owner" else None,
    )
    manager.hook_store = store
    info = SubagentInfo(
        id="hk0001",
        task="tidy the notes",
        parent_session_key="",
        capability_class="research" if earlier else "",
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:hk0001")
    if grant == "pattern":
        relayed.assert_not_awaited()
    return "ran" if client.approve_tool.await_count else "refused"


async def _helper_path(tool: str, store: _Hooks, *, grant: str, earlier=False) -> str:
    """The background helper over an agent CLI (``stream_and_collect``): an operator's pattern
    under a hook-based policy, a callback (a room's gate) or an auto-approving policy would approve
    its call. *earlier* is a policy that lets nothing run."""
    from personalclaw.llm_helpers import ToolApprovalPolicy, stream_and_collect

    provider = AsyncMock()
    provider.provider_id = "acp:agent-cli"
    provider.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter([_request(tool), LLMEvent(kind=EVENT_COMPLETE)])
    )
    policy = {
        "pattern": ToolApprovalPolicy.HOOK_BASED,
        "a callback": ToolApprovalPolicy.HOOK_BASED,
        "its policy": ToolApprovalPolicy.AUTO_APPROVE,
    }[grant]
    callback = AsyncMock(return_value=ToolDecision(True, "approved", approval_grants.YOU))
    await stream_and_collect(
        provider,
        "go",
        approval_policy=ToolApprovalPolicy.REJECT_ALL if earlier else policy,
        hooks=HookManager(HooksConfig(auto_approve_tools=[tool] if grant == "pattern" else [])),
        on_tool_approval=callback if grant == "a callback" else None,
        session_key="cron:tidy",
    )
    return "ran" if provider.approve_tool.await_count else "refused"


async def _channel_path(tool: str, store: _Hooks, *, grant: str = "pattern", earlier=False) -> str:
    """A channel's own turn, as a channel app runs it through the SDK: the deny-list, the blocking
    hooks, then who approves without asking (here the operator's pattern, as the hook settings
    read now)."""
    from personalclaw.config.loader import config_dir
    from personalclaw.sdk.channel import (
        TOOL_DENY,
        ask_pre_tool_hooks,
        chat_grant,
        screen_tool_call,
    )

    (config_dir() / "config.json").write_text(
        json.dumps({"hooks": {"auto_approve_tools": [tool]}}), encoding="utf-8"
    )
    event = _request(tool)
    if screen_tool_call(None, event.title, event.tool_input).action == TOOL_DENY:
        return "refused"
    said = await ask_pre_tool_hooks(event, agent=None)
    if said.refused:
        return "refused"
    granted = chat_grant("relay:T0CHAN:C0CHAN:1700000000.0001", event)
    assert granted == approval_grants.HOOK_PATTERN, "premise: the pattern approves the call"
    return "ran"


async def _eval_path(tool: str, store: _Hooks, *, grant: str = "its allowlist", earlier=False):
    """An evaluation's turn over an agent CLI: its allowlist approves a read."""
    from personalclaw.eval.runner import EvalRunner

    runner = EvalRunner(provider_factory=MagicMock())
    provider = AsyncMock()
    await runner._decide_permission(
        provider, _request(tool, kind="read", tool_input='{"query": "notes"}'), "eval_s_1"
    )
    return "ran" if provider.approve_tool.await_count else "refused"


#: Each path that runs a call: its gate, the grant that approves its ordinary call, that call's
#: tool, and whether the path has a refusal of its own that comes before the hooks (a task mode, a
#: tier, a policy that lets nothing run).
PATHS = {
    "built-in runtime, YOLO": ("native", "YOLO", "fs_write", True),
    "agent CLI chat, an operator's pattern": ("chat", "pattern", "fs_write", True),
    "agent CLI chat, its Trust": ("chat", "Trust", "fs_write", True),
    "subagent on an agent CLI, an operator's pattern": ("subagent", "pattern", "fs_write", True),
    "subagent on an agent CLI, the owner asked": ("subagent", "the owner", "fs_write", True),
    "background helper, an operator's pattern": ("helper", "pattern", "fs_write", True),
    "background helper, a room's gate": ("helper", "a callback", "fs_write", True),
    "background helper, its policy": ("helper", "its policy", "fs_write", True),
    "channel turn, an operator's pattern": ("channel", "pattern", "fs_write", False),
    "evaluation, its allowlist": ("eval", "its allowlist", "knowledge_search", False),
}

#: (what the hooks answer, the call's tool when it is not the path's ordinary one, whether the
#: path's own earlier refusal applies)
CASES = {
    "nothing objects": ("", "", False),
    "a hook blocks": ("blocks", "", False),
    "a hook fails to run": ("fails", "", False),
    "a hook does not run to an exit": ("does not run", "", False),
    "the deny list refuses": ("", "get_secret_value", False),
    "its own earlier refusal": ("", "", True),
}

#: The step the hooks sit at, as every path must answer it: (outcome, how many times they ran).
EXPECTED = {
    "nothing objects": ("ran", 1),
    "a hook blocks": ("refused", 1),
    "a hook fails to run": ("refused", 1),
    "a hook does not run to an exit": ("refused", 1),
    "the deny list refuses": ("refused", 0),
    "its own earlier refusal": ("refused", 0),
}


async def _run(path: str, tmp_path, tool: str, store: _Hooks, earlier: bool) -> str:
    kind, grant, _ordinary, _has_earlier = PATHS[path]
    if kind == "native":
        return await _native_path(tool, store, earlier=earlier)
    if kind == "chat":
        return await _chat_path(tmp_path, tool, store, grant=grant, earlier=earlier)
    take = {
        "subagent": _subagent_path,
        "helper": _helper_path,
        "channel": _channel_path,
        "eval": _eval_path,
    }[kind]
    return await take(tool, store, grant=grant, earlier=earlier)


#: Every path with every case it has: a path with no refusal of its own before the hooks (a
#: channel's turn, an evaluation) has no such case to run.
_EACH = [(path, case) for path in PATHS for case in CASES if PATHS[path][3] or not CASES[case][2]]


@pytest.mark.asyncio
@pytest.mark.parametrize("path, case", _EACH)
async def test_every_path_asks_the_hooks_at_the_same_step(tmp_path, the_store, path, case):
    """Past the deny list and a path's own refusals, before every approval: each path that runs a
    call, under the grant that would approve it, answers alike."""
    answer, tool, earlier = CASES[case]
    _kind, _grant, ordinary, _has_earlier = PATHS[path]
    the_store.answer = answer
    outcome = await _run(path, tmp_path, tool or ordinary, the_store, earlier)
    assert (outcome, len(the_store.asked)) == EXPECTED[case], f"{path}: {outcome}"


def test_the_table_covers_every_place_that_decides_a_call():
    """A place that approves a call (the deny-list census's `_DECIDES` and its gates) is a path
    here, so a new one is run through the cases above before it can approve anything."""
    from test_every_approval_path_asks_the_deny_list_first import _DECIDES, _GATES

    covered = {
        "dashboard/chat_runner.py::run_chat._let_through": "agent CLI chat",
        "llm_helpers.py::_resolve_permission": "background helper",
        "subagent.py::SubagentManager._approve_and_log": "subagent on an agent CLI",
        "eval/runner.py::EvalRunner._decide_permission": "evaluation",
        "agents/native/runtime.py::NativeAgentRuntime._guard_and_invoke": "built-in runtime",
    }
    #: A call nobody's agent makes: a scheduled script's own tool call and Tools → Try it, which
    #: name no agent whose hooks could be bound.
    no_agent = {"dashboard/handlers/tools.py::api_tool_invoke"}
    for site in set(_DECIDES) | set(_GATES):
        if site in no_agent:
            continue
        assert site in covered, f"{site} decides a call and is no path of this table"
        assert any(name.startswith(covered[site]) for name in PATHS), site
