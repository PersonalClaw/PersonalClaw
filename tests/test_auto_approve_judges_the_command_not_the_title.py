"""An auto-approve pattern is decided on the command a call runs, never on a shortened title.

An agent CLI's permission frame carries a human title ("Running: ls -la", "`git log -3`", "Run
command", "Terminal") beside the call's input, which holds the command that will run. The title can
be shorter than the command, so a pattern read on it approved what the title left off: `ls*` on
"Running: ls -la" ran `ls -la; curl … | sh`.

A shell call's approval is decided on its command (`task_modes.shell_call_command`): the pattern
must name that command, and the command must be one command as the shell reads it. A title decides
only when it is the command. What did not change:

* a pattern naming the shell tool itself (`Terminal`, `bash`) approves every command of it;
* a call that runs no shell command is matched on its title, its name, and a `command` among its
  arguments is data, so it approves nothing more than before;
* a shell call whose command never arrived is approved only by a pattern that names no command, or
  one naming the tool.

Every path that acts on the approval hands the screen what the call is (its kind and what its tool
declares); the last test fails a new one that does not.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.permission_authority import screen_tool_call
from personalclaw.hooks import TOOL_AUTO_APPROVE, TOOL_DENY, HookManager, HooksConfig

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
#: A second command the operator never named, one nothing else refuses or asks about (the deny
#: list lets it run, and it reaches no host): only the pattern decides.
CHAIN = "ls -la; touch notes-copy"
#: Each chat turn below is its own chat, with a history of its own.
_CHATS = itertools.count(1)


def _screen(patterns, title, tool_input, **what_the_call_is):
    """The screen's verdict, told what the call is (``tool_kind``, ``declared``) when given."""
    hooks = HookManager(HooksConfig(auto_approve_tools=list(patterns)))
    return screen_tool_call(hooks, title, tool_input, **what_the_call_is).action


def _approved(*args, **kwargs) -> bool:
    return _screen(*args, **kwargs) == TOOL_AUTO_APPROVE


# ── the screen every approval path asks ─────────────────────────────────────────────────────────


def test_a_title_shorter_than_its_command_does_not_approve_what_it_leaves_off():
    """The defect: the title's verdict approved, and the command it was cut from ran."""
    assert not _approved(["ls*"], "Running: ls -la", json.dumps({"command": CHAIN}))
    assert _approved(["ls*"], "Running: ls -la", json.dumps({"command": "ls -la"})), "control"


@pytest.mark.parametrize(
    ("title", "kind", "tool_input", "pattern"),
    [
        ("`git log -3`", "execute", {"command": "git log -3"}, "git log*"),
        ("Run command", "execute", {"command": ["git", "status", "--short"]}, "git status*"),
        ("unknown", "execute", {"command": "npm test"}, "npm test"),
    ],
    ids=["backticked-title", "words-under-another-title", "untitled"],
)
def test_a_pattern_is_matched_against_the_command_whatever_the_title_says(
    title, kind, tool_input, pattern
):
    """The command decides both ways: a pattern naming it approves it under a title that does not
    spell it, and a joined command under the same title is not approved."""
    assert _approved([pattern], title, json.dumps(tool_input), tool_kind=kind)
    words = tool_input["command"]
    joined = (words if isinstance(words, str) else " ".join(words)) + " && rm -rf build"
    assert not _approved([pattern], title, json.dumps({"command": joined}), tool_kind=kind)


def test_a_command_given_as_words_is_read_as_its_shell_would_read_them():
    """A list of words is argv: no shell splits it, so a `;` inside a word is that word's text."""
    words = {"command": ["git", "commit", "-m", "one; two"]}
    assert _approved(["git commit*"], "Run command", json.dumps(words), tool_kind="execute")
    script = {"command": ["bash", "-lc", CHAIN]}
    assert not _approved(["ls*"], "Run command", json.dumps(script), tool_kind="execute")


def test_the_built_in_shell_is_decided_on_its_command():
    assert _approved(["ls*"], "bash", {"command": "ls -la"}, declared="destructive")
    assert not _approved(["ls*"], "bash", {"command": "ls -la > notes"}, declared="destructive")
    assert not _approved(["ls*"], "bash", {"command": CHAIN}, declared="destructive")


def test_a_pattern_naming_the_shell_tool_approves_every_command_of_it():
    """As before: the operator named the tool, not a command."""
    assert _approved(["Terminal"], "Terminal", {"command": CHAIN}, tool_kind="execute")
    assert _approved(["bash"], "bash", {"command": CHAIN}, declared="destructive")


def test_a_shell_call_whose_command_never_arrived_is_approved_only_by_a_pattern_naming_no_command():
    assert not _approved(["ls*"], "Terminal", "{}", tool_kind="execute")
    assert not _approved(["*ls*"], "`ls -la`", "", tool_kind="execute")
    assert _approved(["Terminal"], "Terminal", "{}", tool_kind="execute")
    assert _approved(["*"], "Terminal", "{}", tool_kind="execute")


def test_a_call_that_runs_no_shell_command_is_matched_on_its_name_only():
    """A `command` among another tool's arguments is data: a command pattern never approves that
    tool, and the tool's own pattern approves it whatever the argument holds."""
    args = {"command": "ls"}
    assert not _approved(["ls*"], "notes_search", args, tool_kind="other")
    assert not _approved(["ls*"], "run_script", args, declared="caution")
    sql = {"command": "select 1; select 2"}
    assert _approved(["notes_search"], "notes_search", sql, tool_kind="other")
    assert _approved(["run_script"], "run_script", sql, declared="caution")


def test_the_deny_list_still_reads_the_command_of_any_call():
    """The refusal half reads more than the approval half, so it can only refuse more."""
    push = {"command": "git push origin main --force"}
    assert _screen(["*"], "notes_search", push, tool_kind="other") == TOOL_DENY
    assert _screen(["*"], "Running: git status", push) == TOOL_DENY


# ── each path that acts on the approval ─────────────────────────────────────────────────────────


def _event(title, tool_input, *, kind="", risk=""):
    return SimpleNamespace(
        title=title,
        tool_kind=kind,
        risk_level=risk,
        tool_input=json.dumps(tool_input),
        request_id="req-1",
        tool_call_id="call-1",
        tool_purpose="",
        work_asks=False,
        proposes=False,
        tells_owner=False,
        builds=False,
        annotations=None,
    )


def test_a_subagents_screen_decides_on_the_command(tmp_path):
    from personalclaw.run_bounds import screen

    hooks = HookManager(HooksConfig(auto_approve_tools=["ls*"]))
    chained = _event("`ls -la`", {"command": CHAIN}, kind="execute")
    plain = _event("`ls -la`", {"command": "ls -la"}, kind="execute")
    assert screen(hooks, chained, "dashboard:c1", str(tmp_path))[0].action != TOOL_AUTO_APPROVE
    assert screen(hooks, plain, "dashboard:c1", str(tmp_path))[0].action == TOOL_AUTO_APPROVE


@pytest.mark.asyncio
async def test_the_background_helper_asks_about_what_a_shortened_title_leaves_off():
    from personalclaw.llm_helpers import ToolApprovalPolicy, _resolve_permission

    hooks = HookManager(HooksConfig(auto_approve_tools=["ls*"]))
    for tool_input, asked_expected in (({"command": CHAIN}, True), ({"command": "ls -la"}, False)):
        provider = SimpleNamespace(approve_tool=AsyncMock(), reject_tool=AsyncMock())
        asked = AsyncMock(return_value=False)
        await _resolve_permission(
            provider,
            _event("Running: ls -la", tool_input),
            ToolApprovalPolicy.HOOK_BASED,
            hooks,
            on_tool_approval=asked,
        )
        assert asked.await_count == (1 if asked_expected else 0), tool_input
        assert provider.approve_tool.await_count == (0 if asked_expected else 1), tool_input


def test_a_channels_own_turn_is_decided_on_the_command(tmp_path, monkeypatch):
    from personalclaw.chat_trust import chat_grant
    from personalclaw.config import loader

    (loader.config_dir() / "config.json").write_text(
        json.dumps({"hooks": {"auto_approve_tools": ["ls*"]}})
    )
    assert chat_grant("thread-1", _event("Running: ls -la", {"command": CHAIN})) == ""
    assert chat_grant("thread-1", _event("Running: ls -la", {"command": "ls -la"})) == (
        "hook_pattern"
    )


async def _iter(items):
    for item in items:
        yield item


def _chat(tmp_path, patterns):
    """The chat runner over one agent CLI permission request, with the operator's patterns."""
    from personalclaw.dashboard.state import DashboardState
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
        conversation_log=ConversationLog(base_dir=tmp_path / f"history-{next(_CHATS)}"),
    )
    builder = MagicMock()
    builder.hooks = HookManager(HooksConfig(auto_approve_tools=list(patterns)))
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


async def _drive(state, client, tool_input):
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import _ChatSession
    from personalclaw.llm.base import (
        EVENT_COMPLETE,
        EVENT_PERMISSION_REQUEST,
        EVENT_TEXT_CHUNK,
        LLMEvent,
    )

    session = _ChatSession(f"chat-command-decides-{next(_CHATS)}")
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _iter(
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="Running: ls -la",
                    request_id="req-1",
                    tool_input=json.dumps(tool_input),
                ),
                # It answers: a turn that wrote nothing is sent again on its own (a second call).
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="done"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )

    async def answer():
        for _ in range(200):
            fut = session._approval_futures.get("req-1")
            if fut and not fut.done():
                fut.set_result("rejected")
                return
            await asyncio.sleep(0.01)

    asyncio.get_event_loop().create_task(answer())
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await asyncio.wait_for(run_chat(state, session, "hello"), timeout=20)
    assert session.task is None, "the turn left another run of itself behind"
    return session


@pytest.mark.asyncio
async def test_the_chat_runner_decides_on_the_command(tmp_path):
    """A chat's call: the command the title leaves off is put to the owner, who declines it here;
    the command the pattern names runs without a card."""
    state, client = _chat(tmp_path, ["ls*"])
    session = await _drive(state, client, {"command": CHAIN})
    client.approve_tool.assert_not_awaited()
    client.reject_tool.assert_awaited_once_with("req-1")
    assert any(m.get("role") == "permission" for m in session.messages), "it was asked"

    state, client = _chat(tmp_path, ["ls*"])
    session = await _drive(state, client, {"command": "ls -la"})
    client.approve_tool.assert_awaited_once_with("req-1")
    assert not any(m.get("role") == "permission" for m in session.messages), "and asked nobody"


# ── the paths hand over what the call is ────────────────────────────────────────────────────────

#: Every call of the screen in the package, by ``file::function``, and whether its verdict can
#: approve a call. One that can must say what the call is, or a shell call would be read as a name.
APPROVES = {
    "dashboard/chat_runner.py::run_chat": "a chat's call: an operator's pattern answers it",
    "llm_helpers.py::_resolve_permission": "the background helper's hook-based policy",
    "chat_trust.py::_hook_verdict": "a channel's own turn (chat_grant)",
    "run_bounds.py::screen": "a subagent's call, and anything else that screens an event",
}
REFUSES_ONLY = {
    "agents/native/approval.py::deny_list_refusal": "the native runtime's deny step",
    "dashboard/handlers/tools.py::api_tool_invoke": "Tools → Try it, by the tool's name",
    "eval/runner.py::EvalRunner._decide_permission": "an evaluation approves by its allowlist",
}


def _screen_calls() -> dict[str, list[ast.Call]]:
    sites: dict[str, list[ast.Call]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()

        def visit(node, scope):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit(child, [*scope, child.name])
                    continue
                if isinstance(child, ast.Call):
                    func = child.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name == "screen_tool_call":
                        sites.setdefault(f"{rel}::{'.'.join(scope)}", []).append(child)
                visit(child, scope)

        visit(tree, [])
    return sites


def test_every_screen_that_can_approve_is_told_what_the_call_is():
    sites = _screen_calls()
    assert set(sites) == set(APPROVES) | set(REFUSES_ONLY), (
        "classify each call of screen_tool_call: "
        f"{sorted(set(sites) ^ (set(APPROVES) | set(REFUSES_ONLY)))}"
    )
    for site in APPROVES:
        for call in sites[site]:
            given = {kw.arg for kw in call.keywords}
            assert {"tool_kind", "declared"} <= given, f"{site} does not say what the call is"
