"""A tool call's audit row says what it ran: a shell call's command, the file a write changes.

Every ``bash`` row in the security log named its tool and nothing else: ``resources`` was empty,
and the metadata said who decided and the risk. Only the session's transcript held the command,
so the tamper-evident log could not answer "what did it run". Now each path that audits a tool
call hands the log the call's arguments, and the row records the command of a shell call and the
path of a call that changes a file: masked the way a call's title is masked where it is shown (a
credential in a command is stored as its mask), on one line, and cut at 500 characters with a
marker saying how much was cut.

Driven through the real native runtime in each host that audits its calls (the chat, the subagent
manager, the background helper), the eval runner, an agent CLI's permission frame in the chat, an
agent CLI's call that never asked, Tools → Try it, the in-process tool servers, a refused command
and a refused action, and read back from the real audit log on disk. Every command is an invented
program that cannot resolve, and every credential an invented one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw.approval_answer import YOU
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.sel import sel
from personalclaw.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

#: An invented credential, in the shape a name-keyed assignment takes.
SECRET = "pcfixture-not-a-real-key"
#: A command carrying it, for a program that cannot resolve.
COMMAND = f"pcfixture-deployctl --api-key={SECRET} status"
#: The same command as the audit log keeps it.
SHOWN = "pcfixture-deployctl --[REDACTED: credential] status"
#: The file a write names.
NOTE = "notes/plan.md"


def _log_text() -> str:
    """The audit log as it is on disk, every byte of it."""
    from personalclaw.config.loader import config_dir

    path = config_dir() / "security_events.jsonl"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _tool_rows(tool: str = "") -> list[dict[str, Any]]:
    """Every tool-call row on disk (about *tool*, when named), oldest first."""
    rows = [json.loads(line) for line in _log_text().splitlines() if line.strip()]
    return [
        r
        for r in rows
        if r.get("event_type") == "tool_invocation" and (not tool or r.get("operation") == tool)
    ]


# ── the row ─────────────────────────────────────────────────────────────────────────────────────


def test_a_shell_calls_row_records_its_command_with_its_credential_masked():
    """🔴 Before: ``resources`` was ``""`` and the command was in no row at all."""
    sel().log_tool_invocation(
        session_key="dashboard:c1",
        tool_name="bash",
        outcome="approved",
        tool_input={"command": COMMAND, "timeout": 30},
    )
    [row] = _tool_rows("bash")
    assert row["resources"] == SHOWN
    assert SECRET not in _log_text(), "the credential reached the audit log on disk"
    # Masked BEFORE it was signed: the record still verifies.
    assert sel().verify_integrity() == (1, 1)


@pytest.mark.parametrize(
    "tool, tool_input, recorded",
    [
        ("bash", {"command": "git status --short"}, "git status --short"),
        ("write_file", {"path": NOTE, "content": "the plan"}, NOTE),
        ("edit_file", {"path": NOTE, "old_str": "a", "new_str": "b"}, NOTE),
        # A call that neither runs a command nor changes a file names its tool alone.
        ("read_file", {"path": NOTE}, ""),
        ("knowledge_search", {"query": "plans", "command": "not a shell command"}, ""),
    ],
    ids=["shell", "write", "edit", "read", "other"],
)
def test_ordinary_commands_and_paths_are_kept_as_they_are(tool, tool_input, recorded):
    sel().log_tool_invocation(
        session_key="dashboard:c1", tool_name=tool, outcome="invoked", tool_input=tool_input
    )
    assert [r["resources"] for r in _tool_rows(tool)] == [recorded]


@pytest.mark.parametrize(
    "title, kind, tool_input, recorded",
    [
        ("`git log -3`", "execute", json.dumps({"command": "git log -3"}, indent=2), "git log -3"),
        (
            "Run command",
            "execute",
            json.dumps({"command": ["git", "show", "--stat", "HEAD"]}),
            "git show --stat HEAD",
        ),
        ("Running: npm test", "", "", "npm test"),
        ("Edit plan.md", "edit", json.dumps({"file_path": "/work/plan.md"}), "/work/plan.md"),
        ("Write src/a.py", "edit", "--- src/a.py\n+++ src/a.py\n@@ -0,0 +1 @@\n+x", "src/a.py"),
    ],
    ids=["json", "words", "running-title", "file-path", "declared-diff"],
)
def test_an_agent_clis_calls_record_what_they_ran(title, kind, tool_input, recorded):
    """An agent CLI hands its arguments as JSON text, a command as a list of words or inside its
    title, and an edit it declared as the diff it is shown as."""
    sel().log_tool_invocation(
        session_key="dashboard:c1",
        tool_name=title,
        tool_kind=kind,
        outcome="approved",
        tool_input=tool_input,
    )
    assert [r["resources"] for r in _tool_rows(title)] == [recorded]


def test_an_agent_cli_title_that_is_its_command_is_stored_masked():
    """An agent CLI can title a shell call with the command itself, so the tool's name is stored
    the way its command is."""
    title = f"`{COMMAND}`"
    sel().log_tool_invocation(
        session_key="dashboard:c1",
        tool_name=title,
        tool_kind="execute",
        outcome="approved",
        tool_input=json.dumps({"command": COMMAND}),
    )
    [row] = _tool_rows()
    assert (row["operation"], row["resources"]) == (f"`{SHOWN}`", SHOWN)
    assert SECRET not in _log_text()


def test_a_long_command_is_cut_at_the_bound_and_says_how_much():
    command = "pcfixture-batchctl run " + " ".join(f"--item={n:04d}" for n in range(200))
    sel().log_tool_invocation(
        session_key="dashboard:c1",
        tool_name="bash",
        outcome="approved",
        tool_input={"command": command},
    )
    [row] = _tool_rows("bash")
    kept, _, marker = row["resources"].partition("…[cut: ")
    assert len(row["resources"]) == 500
    assert command.startswith(kept)
    assert marker == f"{len(command) - len(kept):,} more characters]"


def test_a_command_is_stored_on_one_line():
    """A command cannot start a line that reads as a record of its own in a viewer or a terminal:
    a line break and a control character are written as visible escapes."""
    sel().log_tool_invocation(
        session_key="dashboard:c1",
        tool_name="bash",
        outcome="approved",
        tool_input={"command": "printf 'a'\nprintf '\x1b[2J'"},
    )
    [row] = _tool_rows("bash")
    assert row["resources"] == "printf 'a'\\nprintf '\\x1b[2J'"


def test_a_command_its_masker_fails_on_is_withheld_and_the_row_still_written(monkeypatch):
    from personalclaw import security

    def broken(_text: str) -> str:
        raise RuntimeError("masker broke")

    monkeypatch.setattr(security, "redact", broken)
    sel().log_tool_invocation(
        session_key="dashboard:c1",
        tool_name="bash",
        outcome="approved",
        tool_input={"command": COMMAND},
    )
    [row] = _tool_rows()
    assert row["resources"] == security.WITHHELD_TEXT
    assert SECRET not in _log_text()


# ── the hosts that run a native agent's calls ───────────────────────────────────────────────────


class _Tools(ToolProvider):
    """A shell tool and a file write, declared as the platform's are, that run nothing."""

    def __init__(self, *, asks: bool = True) -> None:
        self.ran: list[str] = []
        self._asks = asks

    @property
    def name(self) -> str:
        return "probe"

    @property
    def display_name(self) -> str:
        return "Probe"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="bash",
                description="d",
                parameters={"type": "object"},
                requires_approval=self._asks,
                risk_level=RiskLevel.DESTRUCTIVE,
            ),
            ToolDefinition(
                name="write_file",
                description="d",
                parameters={"type": "object"},
                requires_approval=self._asks,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output="done")


def _calls(tool: str, args: dict[str, Any]) -> _ScriptedModel:
    """A model that calls *tool* with *args* once, then answers."""
    call = AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id="call-1", title=tool, tool_input=json.dumps(args)
    )
    return _ScriptedModel(
        [
            [call, AgentEvent(kind=EVENT_COMPLETE)],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )


_CALLS = [("bash", {"command": COMMAND}, SHOWN), ("write_file", {"path": NOTE}, NOTE)]
_IDS = ["shell", "write"]


def _chat(tmp_path: Path, tool: str, args: dict[str, Any]):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.config import AppConfig
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore
    from personalclaw.session import SessionManager
    from personalclaw.skills import SkillsLoader

    tools = _Tools()

    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=_calls(tool, args),
            tool_providers=[tools],
            cwd=tmp_path,
        )

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, tools


async def _chat_turn(state, session, *, answer: str = "") -> None:
    from personalclaw.dashboard.chat_runner import run_chat

    async def answering() -> None:
        for _ in range(400):
            pending = [k for k, f in session._approval_futures.items() if not f.done()]
            if pending:
                state.decide_session_approval(session, pending[0], answer, by=YOU)
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the chat never asked")

    session.append("user", "go", "msg msg-u")
    waiter = asyncio.ensure_future(answering()) if answer else None
    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)
    if waiter is not None:
        await waiter


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args, recorded", _CALLS, ids=_IDS)
async def test_chat_a_call_a_person_allowed(tmp_path, tool, args, recorded):
    state, tools = _chat(tmp_path, tool, args)
    await _chat_turn(state, state.get_or_create_session("c-asked"), answer="approved")
    assert tools.ran == [tool]
    assert [(r["outcome"], r["resources"]) for r in _tool_rows(tool)] == [("approved", recorded)]
    assert SECRET not in _log_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args, recorded", _CALLS, ids=_IDS)
async def test_chat_a_call_its_trust_answered_at_its_result(tmp_path, tool, args, recorded):
    """A call nobody was asked about is audited at its result, which carries no arguments: the
    row takes them from the call's card."""
    state, tools = _chat(tmp_path, tool, args)
    session = state.get_or_create_session("c-trusted")
    session._trust = True
    await _chat_turn(state, session)
    assert tools.ran == [tool]
    rows = [(r["outcome"], r["resources"]) for r in _tool_rows(tool)]
    assert rows == [("auto_approved", recorded)]


def _subagents(tool: str, args: dict[str, Any], *, relay: Any = None):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.config import AppConfig
    from personalclaw.hooks import HookManager
    from personalclaw.session import SessionManager
    from personalclaw.subagent import SubagentManager

    tools = _Tools()

    def factory(_key: Any = None, **kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=_calls(tool, args),
            tool_providers=[tools],
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
    return manager, tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args, recorded", _CALLS, ids=_IDS)
async def test_a_workflow_steps_call_a_person_allowed(tool, args, recorded):
    """A workflow's stage, and the agent an automation starts, run as subagents."""
    from personalclaw.approval_grants import ToolDecision
    from personalclaw.subagent import SubagentInfo

    relay = AsyncMock(return_value=ToolDecision(True, "approved", "you"))
    manager, tools = _subagents(tool, args, relay=relay)
    info = SubagentInfo(id="sa-1", task="t", parent_session_key="workflow:run-7:write")
    await asyncio.wait_for(manager._run_inner(info, "subagent:sa-1"), timeout=20)
    assert tools.ran == [tool]
    assert [(r["outcome"], r["resources"]) for r in _tool_rows(tool)] == [("approved", recorded)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, args, outcome, recorded",
    [
        ("bash", {"command": COMMAND}, "auto_approved", SHOWN),
        # Unattended, it writes only inside its own folders: refused, and the row says what.
        ("write_file", {"path": NOTE}, "denied", NOTE),
    ],
    ids=_IDS,
)
async def test_an_automations_agent_whose_grant_answered_at_its_result(
    tool, args, outcome, recorded
):
    from personalclaw.subagent import SubagentInfo

    manager, tools = _subagents(tool, args)
    info = SubagentInfo(id="sa-2", task="t", approval_mode="auto", capability_class="mutating")
    await asyncio.wait_for(manager._run_inner(info, "subagent:sa-2"), timeout=20)
    assert tools.ran == ([tool] if outcome == "auto_approved" else [])
    assert [(r["outcome"], r["resources"]) for r in _tool_rows(tool)] == [(outcome, recorded)]


async def _background(tool: str, args: dict[str, Any], *, asks: bool, **kw: Any) -> _Tools:
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm_helpers import stream_and_collect

    tools = _Tools(asks=asks)
    runtime = NativeAgentRuntime(
        definition=_defn(), model_provider=_calls(tool, args), tool_providers=[tools]
    )
    await runtime.start()
    await asyncio.wait_for(stream_and_collect(runtime, "go", **kw), timeout=20)
    return tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args, recorded", _CALLS, ids=_IDS)
async def test_background_a_call_its_policy_approved(tool, args, recorded):
    from personalclaw.llm_helpers import ToolApprovalPolicy

    tools = await _background(
        tool, args, asks=True, approval_policy=ToolApprovalPolicy.AUTO_APPROVE
    )
    assert tools.ran == [tool]
    rows = [(r["outcome"], r["resources"]) for r in _tool_rows(tool)]
    assert rows == [("auto_approved", recorded)]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args, recorded", _CALLS, ids=_IDS)
async def test_background_a_call_that_asks_nobody(tool, args, recorded):
    tools = await _background(tool, args, asks=False)
    assert tools.ran == [tool]
    assert [(r["outcome"], r["resources"]) for r in _tool_rows(tool)] == [("invoked", recorded)]


@pytest.mark.asyncio
async def test_the_eval_runner_records_what_each_call_ran():
    """Its allowlist's decision, and a call nobody was asked about, from its card."""
    from personalclaw.eval.runner import EvalRunner
    from personalclaw.eval.scenario import Turn

    class _Provider:
        async def stream(self, message: str):
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c1",
                title="bash",
                tool_input=json.dumps({"command": COMMAND}),
            )
            yield AgentEvent(
                kind=EVENT_PERMISSION_REQUEST,
                tool_call_id="c1",
                title="bash",
                tool_input=json.dumps({"command": COMMAND}),
                request_id="r1",
            )
            yield AgentEvent(kind=EVENT_TOOL_RESULT, tool_call_id="c1", title="bash")
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c2",
                title="write_file",
                tool_input=json.dumps({"path": NOTE}),
            )
            yield AgentEvent(kind=EVENT_TOOL_RESULT, tool_call_id="c2", title="write_file")
            yield AgentEvent(kind=EVENT_COMPLETE)

        async def approve_tool(self, request_id) -> None:
            return None

        async def reject_tool(self, request_id) -> None:
            return None

    runner = EvalRunner(provider_factory=lambda key, **kw: _Provider())
    await runner._run_turn(_Provider(), Turn(user="go"), "eval_s1")
    rows = [(r["operation"], r["outcome"], r["resources"]) for r in _tool_rows()]
    assert rows == [("bash", "denied", SHOWN), ("write_file", "invoked", NOTE)]


# ── an agent CLI ────────────────────────────────────────────────────────────────────────────────


def _asked_by_a_cli() -> Any:
    """The permission request an agent CLI sends for a shell call it titles with the command,
    decoded by the shipped translator and adapter."""
    from personalclaw.acp.adapter import acp_event_to_agent_event
    from personalclaw.acp.dialect import DefaultDialect
    from personalclaw.acp.translate import build_permission_event
    from personalclaw.acp.types import JsonRpcMessage

    msg = JsonRpcMessage(
        id="req-1",
        method="session/request_permission",
        params={
            "toolCall": {
                "toolCallId": "c1",
                "title": f"`{COMMAND}`",
                "kind": "execute",
                "rawInput": {"command": COMMAND, "description": "Check the deploy"},
            },
            "options": [],
        },
    )
    return acp_event_to_agent_event(build_permission_event(msg, DefaultDialect(), {}, {}, {}))


@pytest.mark.asyncio
async def test_chat_an_agent_cli_call_a_person_allowed(tmp_path):
    """The agent CLI's own permission frame, answered on the chat's card."""
    from test_acp_permission_authority import _context_builder, _make_state, _session, _set_stream

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.llm.base import LLMEvent

    request = _asked_by_a_cli()
    assert SECRET in request.title, "precondition: the frame's title carries the command"
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=False)
    _set_stream(client, [request, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])

    async def answering() -> None:
        for _ in range(400):
            fut = session._approval_futures.get("req-1")
            if fut and not fut.done():
                fut.set_result("approved")
                return
            await asyncio.sleep(0.01)

    waiter = asyncio.ensure_future(answering())
    await asyncio.wait_for(run_chat(state, session, "hello"), timeout=20)
    await waiter
    client.approve_tool.assert_awaited_once_with("req-1")
    rows = [(r["operation"], r["outcome"], r["resources"]) for r in _tool_rows()]
    assert rows == [(f"`{SHOWN}`", "approved", SHOWN)]
    assert _tool_rows()[0]["metadata"]["offered"], "the row keeps the answers the agent offered"
    assert SECRET not in _log_text()


def test_an_agent_cli_call_that_never_asked(tmp_path):
    """A call the CLI ran without asking the host: its row is the record of what it ran."""
    from personalclaw.dashboard.state import _ChatSession
    from personalclaw.dashboard.ungated_calls import report_ungated_call

    session = _ChatSession("c-ungated")
    report_ungated_call(
        MagicMock(),
        session,
        agent="pcfixture-agent",
        session_key="dashboard:c-ungated",
        acp_cli="pcfixture-cli",
        title=f"`{COMMAND}`",
        tool_kind="execute",
        tool_input=json.dumps({"command": COMMAND}),
        request_id="c9",
    )
    rows = [(r["outcome"], r["resources"]) for r in _tool_rows()]
    assert rows == [("ungated", SHOWN)]
    assert SECRET not in _log_text()


@pytest.mark.asyncio
async def test_an_unattended_refusal_keeps_what_it_refused_beside_the_answers_offered(monkeypatch):
    """A decision row on an agent CLI's call names the answers the agent offered
    (``turn_endings.offered``) and what the call would have run, on the same row."""
    from types import SimpleNamespace

    from personalclaw import security
    from personalclaw.dashboard import chat_refusals

    monkeypatch.setattr(chat_refusals.auto_denials, "note_unattended", MagicMock())
    event = SimpleNamespace(
        title=f"`{COMMAND}`",
        tool_kind="execute",
        request_id="7",
        tool_call_id="c7",
        tool_input=json.dumps({"command": COMMAND}),
        options=[{"id": "allow_once", "label": "Yes, proceed", "kind": "allow_once"}],
    )
    await chat_refusals.refuse_unattended(
        MagicMock(),
        MagicMock(key="s1"),
        "dashboard:s1",
        event,
        AsyncMock(),
        agent="pcfixture-agent",
        said="refused: no one to approve",
        reason="unattended",
        decided_by="unattended",
        why="the run is unattended: no one to approve",
        kind=security.DENY_KIND_POLICY,
    )
    [row] = _tool_rows()
    assert (row["outcome"], row["resources"]) == ("denied", SHOWN)
    assert row["metadata"]["offered"] == [
        {"id": "allow_once", "kind": "allow_once", "name": "Yes, proceed"}
    ]
    assert SECRET not in _log_text()


# ── Tools → Try it, the in-process tool servers, refused commands and actions ───────────────────


@pytest.mark.asyncio
async def test_try_it_a_refused_command(tmp_path, monkeypatch):
    """Refused by the shell denylist before it ran: the row keeps what was asked to run."""
    from test_the_shell_denylist_binds_every_command_path import (
        DENIED,
        _add_pattern,
        _invoke,
        _tools_route,
    )

    _add_pattern()
    command = f"{DENIED} --api-key={SECRET}"
    async with _tools_route(tmp_path, monkeypatch) as http:
        status, body = await _invoke(http, tool="bash", arguments={"command": command})
    assert status == 200 and body["ok"] is False
    rows = [(r["outcome"], r["resources"]) for r in _tool_rows("bash")]
    assert rows == [("refused", f"{DENIED} --[REDACTED: credential]")]
    assert SECRET not in _log_text()


def test_an_in_process_tool_servers_call_keeps_its_arguments_masked_and_bounded():
    from personalclaw.mcp_shared import call_tool_with_logging

    args = {"note": f"api_key={SECRET}", "body": "x" * 900}
    call_tool_with_logging(
        "memory_remember",
        args,
        lambda _name, raw: raw,
        lambda _name, _args: "ok",
        "dashboard:c1",
        "personalclaw-core",
    )
    [row] = _tool_rows("memory_remember")
    assert len(row["resources"]) == 500 and "…[cut: " in row["resources"]
    assert "[REDACTED: credential]" in row["resources"]
    assert SECRET not in _log_text()


def test_a_refused_commands_row_bounds_the_command_it_keeps():
    """A command refused before it ran (a workflow step, a loop's check, a bash action) keeps it
    the way a tool call's row does."""
    from personalclaw.command_audit import audit_command_refusal

    command = f"{COMMAND} " + "--flag " * 200
    audit_command_refusal(command, "a reason", source="workflow", operation="step", control="x")
    rows = [json.loads(line) for line in _log_text().splitlines() if line.strip()]
    [row] = [r for r in rows if r.get("event_type") == "command_refused"]
    kept = row["metadata"]["command"]
    assert len(kept) == 500 and kept.startswith(SHOWN) and "…[cut: " in kept
    assert SECRET not in _log_text()


def test_an_action_the_denylist_held_names_the_command_it_would_have_run():
    """🔴 Before: the row named the pattern and the reason, and not the command."""
    from test_the_shell_denylist_binds_every_command_path import DENIED, _add_pattern

    from personalclaw.guardrails.denylist import enforce_action

    _add_pattern()
    decision = enforce_action("bash", {"command": f"{DENIED} --api-key={SECRET}"})
    assert decision.blocked
    rows = [json.loads(line) for line in _log_text().splitlines() if line.strip()]
    [row] = [r for r in rows if r.get("operation") == "guardrails.denylist"]
    assert row["metadata"]["command"] == f"{DENIED} --[REDACTED: credential]"
    assert SECRET not in _log_text()


def test_the_cli_shows_what_each_call_ran(capsys):
    """``personalclaw security events`` prints the command a row records."""
    import argparse

    from personalclaw.cli_commands import _security

    sel().log_tool_invocation(
        session_key="cli_chat",
        tool_name="bash",
        outcome="approved",
        tool_input={"command": COMMAND},
    )
    _security(argparse.Namespace(sec_action="events", limit=5))
    out = capsys.readouterr().out
    assert f"resources: {SHOWN}" in out
    assert SECRET not in out
