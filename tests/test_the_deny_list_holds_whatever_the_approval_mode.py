"""A call the deny-list refuses is refused wherever a call is approved or asked about, whatever
the approval mode, and it is read on the command the call would run.

Settings → Security → Shell denylist, the operator's hook deny patterns (``hooks.auto_deny_tools``)
and the hook chain's other refusals are a floor: no grant, no approval mode and no person is asked
about a call they refuse. Four things let a call through. The background helper
(``llm_helpers.stream_and_collect``, which runs a subagent's result announced into a channel
conversation, a heartbeat, a room member's turn and a one-shot call) asked the deny-list only under
the ``hook_based`` mode with a hook manager handed to it, so under ``auto_approve`` (the announce
into a conversation you are in) an agent CLI's call the deny-list refuses was approved, and a room
member's was put to you as a question. The evaluation runner approved its allowlist's calls without
asking it. PersonalClaw's own agent read only the built-in tool patterns, so under a chat's Trust or
YOLO or a subagent's standing grant a call the operator's patterns name ran, and Tools → Try it
kept its own copy of that name check. And the screen every path asks read a command only when it
came as text behind a title that did not already contain it: a command an agent CLI hands as a list
of words, or under a title that is the bare command, was never read as a command at all.

Each path now asks the one screen (``acp.permission_authority.screen_tool_call``) before anything
else, and a refused call is refused, with the rule that refused it in the audit log. Each family
has its control: an ordinary call still runs, or is still asked about. The added pattern names a
program that cannot resolve (``pcfixture-cloudctl``), so a path that still ran it ran nothing.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm_helpers import ToolApprovalPolicy, _resolve_permission, stream_and_collect

ADDED = "pcfixture-cloudctl"
DENIED = f"{ADDED} status"
ORDINARY = "echo hello"
#: How every surface names an added pattern's rule.
RULE = "a pattern added to the shell denylist under Settings → Security"

#: The ways an agent CLI asks the host to run a command: under a title that does not carry it
#: (one sends none, another describes the command in its own words), as a list of words
#: (an argv), under the ``Running:`` title the hook chain reads as a command, and under a title
#: that is the bare command.
SHAPES = {
    "a title that does not carry it": ("unknown", {"command": "{cmd}"}),
    "a list of words": ("Run command", {"command": ["{prog}", "{arg}"]}),
    "a list of words a shell runs": ("Run command", {"command": ["bash", "-lc", "{cmd}"]}),
    "the Running title": ("Running: {cmd}", {"command": "{cmd}"}),
    "a title that is the command": ("{cmd}", {"command": "{cmd}"}),
    "the command in backticks": ("`{cmd}`", {"command": "{cmd}"}),
}


def _add_pattern(pattern: str = ADDED, **sections: Any) -> None:
    """Add *pattern* under Settings → Security → Shell denylist, in the home this test runs in."""
    from personalclaw.config.loader import config_dir

    doc = {"security": {"denied_commands": [pattern]}, **sections}
    (config_dir() / "config.json").write_text(json.dumps(doc), encoding="utf-8")


def _fill(value: Any, command: str) -> Any:
    prog, _, arg = command.partition(" ")
    if isinstance(value, str):
        return value.format(cmd=command, prog=prog, arg=arg)
    if isinstance(value, list):
        return [_fill(v, command) for v in value]
    return {k: _fill(v, command) for k, v in value.items()}


def _asks_to_run(shape: str, command: str, request_id: str = "req-1") -> LLMEvent:
    """A permission request an agent CLI sends for *command*, in *shape*."""
    title, tool_input = SHAPES[shape]
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=_fill(title, command),
        tool_kind="execute",
        request_id=request_id,
        tool_call_id=f"call-{request_id}",
        tool_input=json.dumps(_fill(tool_input, command)),
    )


class _Runtime:
    """An agent CLI's side of one asked call: what the host answered."""

    def __init__(self) -> None:
        self.approved: list[str] = []
        self.rejected: list[str] = []

    async def approve_tool(self, request_id) -> None:
        self.approved.append(str(request_id))

    async def reject_tool(self, request_id) -> None:
        self.rejected.append(str(request_id))


def _decisions() -> list[tuple[str, str, str]]:
    """Every tool-call row in the audit log, oldest first: (outcome, who decided, its rule)."""
    from personalclaw.sel import sel

    rows = [r for r in reversed(sel().recent(500)) if r.get("event_type") == "tool_invocation"]
    return [
        (
            r.get("outcome", ""),
            (r.get("metadata") or {}).get("decided_by", ""),
            (r.get("metadata") or {}).get("rule", ""),
        )
        for r in rows
    ]


# ── the background helper: every approval mode ─────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_under_auto_approve_a_command_the_deny_list_refuses_is_refused(shape):
    _add_pattern()
    runtime = _Runtime()
    ran = await _resolve_permission(
        runtime, _asks_to_run(shape, DENIED), ToolApprovalPolicy.AUTO_APPROVE, None
    )
    assert ran is False
    assert runtime.approved == [] and runtime.rejected == ["req-1"]
    assert _decisions() == [("refused", "shell_denylist", ADDED)]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_under_auto_approve_an_ordinary_command_still_runs(shape):
    _add_pattern()
    runtime = _Runtime()
    ran = await _resolve_permission(
        runtime, _asks_to_run(shape, ORDINARY), ToolApprovalPolicy.AUTO_APPROVE, None
    )
    assert ran is True
    assert runtime.approved == ["req-1"] and runtime.rejected == []
    assert _decisions() == [("auto_approved", "session_policy", "")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy, hooks_bound",
    [
        (ToolApprovalPolicy.AUTO_APPROVE, False),
        (ToolApprovalPolicy.AUTO_APPROVE, True),
        # A room member's turn: the hook branch is kept out, and the gate asks you.
        (ToolApprovalPolicy.HOOK_BASED, False),
        (ToolApprovalPolicy.REJECT_ALL, False),
    ],
)
async def test_a_command_the_deny_list_refuses_is_never_put_to_a_person(policy, hooks_bound):
    from personalclaw.hooks import live_hook_manager

    _add_pattern()
    hooks = live_hook_manager() if hooks_bound else None
    person = AsyncMock(return_value=True)
    runtime = _Runtime()
    event = _asks_to_run("a title that does not carry it", DENIED)
    assert (
        await _resolve_permission(runtime, event, policy, hooks, on_tool_approval=person) is False
    )
    person.assert_not_awaited()
    assert runtime.rejected == ["req-1"] and runtime.approved == []
    assert _decisions() == [("refused", "shell_denylist", ADDED)]


@pytest.mark.asyncio
async def test_with_a_person_to_ask_an_ordinary_command_is_still_asked():
    _add_pattern()
    for policy in (ToolApprovalPolicy.AUTO_APPROVE, ToolApprovalPolicy.HOOK_BASED):
        person = AsyncMock(return_value=True)
        runtime = _Runtime()
        event = _asks_to_run("a title that does not carry it", ORDINARY)
        assert await _resolve_permission(runtime, event, policy, None, on_tool_approval=person)
        person.assert_awaited_once()
        assert runtime.approved == ["req-1"]


class _AnnouncingCli:
    """An agent CLI answering a subagent's result announced in its chat: it asks to run one
    command, and says whether it ran."""

    def __init__(self, command: str) -> None:
        self.command = command
        self.answered: list[str] = []

    async def stream(self, message: str):
        yield _asks_to_run("a list of words a shell runs", self.command)
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    async def approve_tool(self, request_id) -> None:
        self.answered.append("approved")

    async def reject_tool(self, request_id) -> None:
        self.answered.append("rejected")


@pytest.mark.asyncio
async def test_an_announce_turn_in_a_chat_you_are_in_runs_no_command_the_deny_list_refuses():
    """The announce turn of a chat you are in approves its calls on its own (``auto_approve``), as
    the gateway runs it: with the gateway's hook manager and nobody to ask."""
    from personalclaw.gateway import injection_approval_policy
    from personalclaw.hooks import live_hook_manager

    _add_pattern()
    policy = injection_approval_policy("dashboard:main")
    assert policy is ToolApprovalPolicy.AUTO_APPROVE, "the premise: an attended chat's announce"

    refused = _AnnouncingCli(DENIED)
    await stream_and_collect(refused, "announce", approval_policy=policy, hooks=live_hook_manager())
    assert refused.answered == ["rejected"]

    ordinary = _AnnouncingCli(ORDINARY)
    await stream_and_collect(
        ordinary, "announce", approval_policy=policy, hooks=live_hook_manager()
    )
    assert ordinary.answered == ["approved"], "the control: an ordinary command still runs"


# ── a room member's call ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_room_members_command_the_deny_list_refuses_is_refused_before_you_are_asked():
    """A room's gate asks you about every call its member's tier allows; the deny-list refuses
    first, and the room is told why, in its own words for a refusal."""
    from personalclaw.guardrails.policy import profile_for_session
    from personalclaw.rooms import posture

    _add_pattern()
    member = SimpleNamespace(name="executor")
    profile = profile_for_session("room:r1:executor")
    you = posture.RoomApprover(identity="you", decide=AsyncMock(return_value=True))
    notes: list[Any] = []
    policy, gate = posture.approval_channel(member, profile, you, record=notes.append)

    runtime = _Runtime()
    event = _asks_to_run("a list of words", DENIED)
    assert not await _resolve_permission(
        runtime,
        event,
        policy,
        None,
        on_tool_approval=gate,
        on_refused=lambda call, why: notes.append(
            posture.ToolRefusal(member.name, call.title, why)
        ),
    )
    you.decide.assert_not_awaited()
    assert runtime.rejected == ["req-1"]
    [note] = notes
    assert note.sentence().startswith("executor was refused Run command — Blocked:")
    assert RULE in note.sentence()

    runtime = _Runtime()
    assert await _resolve_permission(
        runtime, _asks_to_run("a list of words", ORDINARY), policy, None, on_tool_approval=gate
    )
    you.decide.assert_awaited_once()
    assert runtime.approved == ["req-1"], "the control: an ordinary command is still yours"


# ── the evaluation runner's allowlist ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_evaluation_approves_no_call_the_deny_list_refuses():
    """Its allowlist approves a read-only tool on its own; one an operator's deny pattern names
    (``hooks.auto_deny_tools``) is refused instead."""
    from personalclaw.eval.runner import EvalRunner

    call = SimpleNamespace(
        title="knowledge_search",
        tool_kind="other",
        tool_input='{"query": "retry policy"}',
        request_id="req-1",
        risk_level="",
    )

    async def decided() -> tuple[_Runtime, dict]:
        runtime, audit = _Runtime(), MagicMock()
        with patch("personalclaw.eval.runner.sel", return_value=audit):
            await EvalRunner(provider_factory=lambda *a, **k: runtime)._decide_permission(
                runtime, call, "eval_s1"
            )
        (row,) = [c.kwargs for c in audit.log_tool_invocation.call_args_list]
        return runtime, row

    runtime, row = await decided()
    assert runtime.approved == ["req-1"], "the control: the allowlist approves the read"
    assert row["outcome"] == "auto_approved"

    _add_pattern(hooks={"auto_deny_tools": ["knowledge_*"]})
    runtime, row = await decided()
    assert runtime.approved == [] and runtime.rejected == ["req-1"]
    assert row["outcome"] == "denied"
    assert row["metadata"] == {
        "reason": "Blocked by security policy: knowledge_*",
        "decided_by": "hook_deny",
    }


# ── a native runtime's own gate, under a standing grant ────────────────────────────────────────


async def _native_turn(tool_name: str, args: dict, *, policy: str) -> tuple[list[dict], Any]:
    """One native turn whose model calls *tool_name* with *args* once, under approval *policy*
    (``auto``: a standing grant, the chat's Trust or YOLO, a subagent's grant), and nobody asked.
    Returns what the tool was invoked with and the call's result."""
    from test_native_runtime import _defn, _ScriptedModel, _Tool

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm.events import EVENT_TOOL_CALL, EVENT_TOOL_RESULT, AgentEvent

    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="c1",
                    title=tool_name,
                    tool_input=json.dumps(args),
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    tool = _Tool(name=tool_name, requires_approval=True)
    runtime = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    runtime.set_approval_policy(policy)
    await runtime.start()
    seen = [event async for event in runtime.stream("go")]
    assert EVENT_PERMISSION_REQUEST not in [e.kind for e in seen], "nobody is asked here"
    return tool.invoked, next(e for e in seen if e.kind == EVENT_TOOL_RESULT)


@pytest.mark.asyncio
async def test_a_native_call_the_operators_deny_patterns_name_never_runs_under_a_grant():
    """A tool ``hooks.auto_deny_tools`` names, called while a standing grant answers every call
    the runtime would ask about: refused before it runs, naming the refusal."""
    from personalclaw.llm.events import TOOL_META_REFUSED_BY

    _add_pattern(hooks={"auto_deny_tools": ["write_note"]})
    invoked, result = await _native_turn("write_note", {"text": "hello"}, policy="auto")
    assert invoked == [], "a tool the deny patterns name ran"
    assert "write_note" in str(result.tool_output)
    assert (result.tool_meta or {}).get(TOOL_META_REFUSED_BY) == "deny_list"

    invoked, _ = await _native_turn("read_note", {"text": "hello"}, policy="auto")
    assert invoked == [{"text": "hello"}], "the control: a tool no pattern names still runs"


@pytest.mark.asyncio
async def test_a_native_call_whose_command_the_deny_list_refuses_never_runs_under_a_grant():
    """A tool handed a command (an app's shell tool, not the platform's own) is read on that
    command too: the shell denylist and an operator's command pattern refuse it, naming the
    control, and an ordinary command still runs."""
    from personalclaw.llm.events import TOOL_META_REFUSED_BY, TOOL_META_REFUSED_RULE

    _add_pattern(hooks={"auto_deny_tools": ["echo pcfixture*"]})
    invoked, result = await _native_turn("run_shell", {"command": DENIED}, policy="auto")
    assert invoked == []
    assert RULE in str(result.tool_output)
    meta = result.tool_meta or {}
    assert meta.get(TOOL_META_REFUSED_BY) == "shell_denylist"
    assert meta.get(TOOL_META_REFUSED_RULE) == ADDED

    invoked, result = await _native_turn(
        "run_shell", {"command": "echo pcfixture-notes"}, policy="auto"
    )
    assert invoked == [] and "echo pcfixture*" in str(result.tool_output)

    invoked, _ = await _native_turn("run_shell", {"command": ORDINARY}, policy="auto")
    assert invoked == [{"command": ORDINARY}], "the control: an ordinary command still runs"


@pytest.mark.asyncio
async def test_a_subagent_on_a_standing_grant_runs_no_call_the_operators_deny_patterns_name():
    """A spawn whose own approval mode answers its calls (``approval_mode: auto``): its runtime
    never asks, so the screen in the runtime's own gate is the one that refuses."""
    from test_a_tool_call_is_audited_as_it_was_decided import ASKS, _run, _subagents

    from personalclaw.subagent import SubagentInfo

    def granted(agent_id: str) -> SubagentInfo:
        return SubagentInfo(
            id=agent_id, task="t", approval_mode="auto", capability_class="mutating"
        )

    manager, tools = _subagents(ASKS)
    await _run(manager, granted("sa-ran"))
    assert tools.ran == [ASKS], "the control: the grant runs a call no pattern names"

    _add_pattern(hooks={"auto_deny_tools": [ASKS]})
    manager, tools = _subagents(ASKS)
    await _run(manager, granted("sa-floor"))
    assert tools.ran == [], "a call the deny patterns name ran on the grant"
    assert _decisions()[-1] == ("denied", "deny_list", "")


@pytest.mark.asyncio
async def test_try_it_refuses_a_tool_the_operators_deny_patterns_name(tmp_path, monkeypatch):
    """Tools → Try it and a scheduled script's tool call are held to the name check the native
    runtime's gate makes: the operator's hook deny patterns too, not only the built-in ones."""
    from test_the_shell_denylist_binds_every_command_path import _invoke, _tools_route

    call = {"tool": "read_file", "arguments": {"path": "notes.md"}}
    async with _tools_route(tmp_path, monkeypatch) as http:
        (tmp_path / "ws" / "notes.md").write_text("the retry policy", encoding="utf-8")
        status, body = await _invoke(http, **call)
        assert (status, body.get("ok")) == (200, True), "the control: the read runs"

        _add_pattern(hooks={"auto_deny_tools": ["read_file"]})
        status, body = await _invoke(http, **call)
        assert status == 403 and body["error"]["code"] == "tool_denied_by_policy", body
        assert "read_file" in body["error"]["message"]


# ── the screen every path asks ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_screen_reads_the_command_in_every_shape_a_cli_sends_it(shape):
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_DENY

    _add_pattern()
    event = _asks_to_run(shape, DENIED)
    verdict = screen_tool_call(None, event.title, event.tool_input)
    assert verdict.action == TOOL_DENY and RULE in verdict.reason
    ordinary = _asks_to_run(shape, ORDINARY)
    assert screen_tool_call(None, ordinary.title, ordinary.tool_input).action != TOOL_DENY
