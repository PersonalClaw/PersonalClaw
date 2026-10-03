"""A shell command reaching a host off the allowed hosts is put to a person, whatever answers the
rest: the chat's approval gate, a subagent's, the relay's and a one-shot's.

Each gate answers most calls without asking when a grant stands (Trust, YOLO, a hook's
auto-approve, a subagent's parent grant). A call reaching a host the owner did not list is the
one each sends to a person instead, with the host named; with nobody to ask it is refused. An
unattended chat turn is refused it outright. Hosts are reserved names (RFC 2606) or this machine.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

UNLISTED = "pkgs.invalid"


@pytest.fixture(autouse=True)
def _temporary_folder_of_its_own(tmp_path, monkeypatch):
    """A run's own temporary folder is made under this test's folder, not the machine's."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(folder))


def _allow_hosts(*hosts: str) -> None:
    from personalclaw.config.loader import config_dir

    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["egress"] = {
        "allow_hosts": list(hosts),
        "deny_hosts": [],
        "allow_private": False,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


async def _events(items):
    for item in items:
        yield item


def _shell_ask(command: str, *, native: bool) -> LLMEvent:
    """A shell call's ask, as the built-in agent raises it and as an agent CLI does."""
    if native:
        return LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="bash",
            risk_level="destructive",
            tool_input={"command": command},
            request_id="req-1",
        )
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=f"Running: {command}",
        tool_kind="execute",
        tool_input=json.dumps({"command": command}),
        request_id="req-1",
    )


def _state(tmp_path, hook: ToolHookResult = ToolHookResult.allow()):
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = hook
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    hook_store = MagicMock()
    hook_store.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hook_store
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


@contextmanager
def _quiet_sel():
    with patch("personalclaw.dashboard.chat.sel") as sel:
        sel.return_value = MagicMock()
        yield


def _answer(session: _ChatSession, decision: str):
    async def responder() -> None:
        for _ in range(400):
            fut = session._approval_futures.get("req-1")
            if fut is not None:
                if not fut.done():
                    fut.set_result(decision)
                return
            await asyncio.sleep(0.005)
        raise AssertionError("the call was never put to a person")

    return asyncio.create_task(responder())


def _asked(session: _ChatSession) -> list[dict]:
    return [m for m in session.messages if m.get("role") == "permission"]


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_trust_does_not_answer_a_call_to_an_unlisted_host(tmp_path, native):
    _allow_hosts("localhost")
    state, client = _state(tmp_path)
    session = _ChatSession("chat-1-bounds")
    session._trust = True
    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask(f"curl -s https://{UNLISTED}/simple/", native=native),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )
    answering = _answer(session, "approved")

    with _quiet_sel():
        await run_chat(state, session, "hello")
    await answering

    asked = _asked(session)
    assert len(asked) == 1, "Trust answered a call to a host off the allowed hosts"
    assert UNLISTED in json.loads(asked[0]["cls"])["reach"]
    client.approve_tool.assert_called_once()  # the person's Allow


@pytest.mark.asyncio
async def test_trust_still_answers_a_call_to_a_listed_host(tmp_path):
    _allow_hosts("localhost")
    state, client = _state(tmp_path)
    session = _ChatSession("chat-1-bounds")
    session._trust = True
    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask("curl -s http://localhost:8080/health", native=True),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )

    with _quiet_sel():
        await run_chat(state, session, "hello")

    assert _asked(session) == []
    client.approve_tool.assert_called_once()


@pytest.mark.asyncio
async def test_a_hooks_auto_approve_does_not_answer_it_either(tmp_path):
    _allow_hosts()
    state, client = _state(tmp_path, hook=ToolHookResult.auto_approve())
    session = _ChatSession("chat-1-bounds")
    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask(f"wget https://{UNLISTED}/x.tgz", native=False),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )
    answering = _answer(session, "rejected")

    with _quiet_sel():
        await run_chat(state, session, "hello")
    await answering

    assert len(_asked(session)) == 1
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
async def test_an_unattended_turn_is_refused_a_call_to_an_unlisted_host(tmp_path):
    _allow_hosts("localhost")
    state, client = _state(tmp_path)
    session = _ChatSession("chat-1-bounds")
    session._trust = True
    session._unattended = True
    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask(f"curl https://{UNLISTED}/", native=False),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )

    with _quiet_sel():
        await run_chat(state, session, "hello")

    assert _asked(session) == []
    client.approve_tool.assert_not_called()
    client.reject_tool.assert_called_once()
    refused = [m for m in session.messages if m.get("role") == "tool"]
    assert any(UNLISTED in m["content"] for m in refused)


@pytest.mark.asyncio
async def test_an_unattended_turn_is_refused_a_write_outside_its_folders(tmp_path):
    """An agent CLI's unattended write elsewhere is refused, and the transcript says where it may
    write instead; its own shell keeps its own temporary folder, so no `mktemp` is promised."""
    state, client = _state(tmp_path)
    session = _ChatSession("chat-1-bounds")
    session._trust = True
    session._unattended = True
    session.workspace_dir = str(tmp_path / "work")
    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask("echo findings > /srv/elsewhere/notes.md", native=False),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )

    with _quiet_sel():
        await run_chat(state, session, "hello")

    client.approve_tool.assert_not_called()
    client.reject_tool.assert_called_once()
    said = " ".join(m["content"] for m in session.messages if m.get("role") == "tool")
    assert "it writes /srv/elsewhere/notes.md, outside the folders this run works in" in said
    # Where it may write instead: the folders the run works in, its own temporary folder among
    # them (the steps to take are the agent's, in its refusal).
    assert str(tmp_path / "tmp") in said and "mktemp" not in said


@pytest.mark.asyncio
async def test_a_one_shot_with_nobody_to_ask_refuses_it(tmp_path):
    from personalclaw.llm_helpers import ToolApprovalPolicy, _resolve_permission

    _allow_hosts()
    provider = AsyncMock()

    approved = await _resolve_permission(
        provider,
        _shell_ask(f"curl https://{UNLISTED}/", native=True),
        ToolApprovalPolicy.AUTO_APPROVE,
        None,
    )

    assert approved is False
    provider.reject_tool.assert_awaited_once()
    provider.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_hooks_refusal_still_answers_it_before_anyone_is_asked(tmp_path):
    """Past every grant, but never past a refusal: the hook's deny half (the shell denylist, for a
    CLI's request) answers a call to an unlisted host first, so nobody is asked about a command
    nothing could let run. With no refusal, the same call goes to the person, not to the hook's
    auto-approve."""
    from personalclaw.llm_helpers import ToolApprovalPolicy, _resolve_permission

    _allow_hosts()
    call = _shell_ask(f"curl https://{UNLISTED}/", native=False)
    hooks = MagicMock()
    hooks.on_tool_call = MagicMock(
        return_value=ToolHookResult.refuse("denied", control="shell_denylist", rule="curl")
    )
    asked, provider = AsyncMock(return_value=True), AsyncMock()

    approved = await _resolve_permission(
        provider, call, ToolApprovalPolicy.HOOK_BASED, hooks, on_tool_approval=asked
    )

    assert approved is False
    asked.assert_not_awaited()
    provider.reject_tool.assert_awaited_once()

    hooks.on_tool_call = MagicMock(return_value=ToolHookResult.auto_approve())
    asked, provider = AsyncMock(return_value=False), AsyncMock()

    approved = await _resolve_permission(
        provider, call, ToolApprovalPolicy.HOOK_BASED, hooks, on_tool_approval=asked
    )

    assert approved is False
    asked.assert_awaited_once()
    provider.approve_tool.assert_not_awaited()


# ── a background agent ───────────────────────────────────────────────────────────────────────────


async def _background(request: LLMEvent):
    """One background agent's turn whose parent chat's grant stands ("auto"): every call it asks
    about is answered by that grant, unless it is put to the relay (which answers no)."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentInfo, SubagentManager

    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="auto")
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
    info = SubagentInfo(id="bg0002", task="check the release", parent_session_key="")
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:bg0002")
    return client, relayed


@pytest.mark.asyncio
async def test_a_background_agents_grant_does_not_answer_an_unlisted_host():
    _allow_hosts("localhost")

    client, relayed = await _background(_shell_ask(f"curl https://{UNLISTED}/", native=False))

    relayed.assert_awaited_once()
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_background_agents_grant_still_answers_a_listed_host():
    _allow_hosts("localhost")

    client, relayed = await _background(_shell_ask("curl http://localhost:9/", native=False))

    relayed.assert_not_awaited()
    client.approve_tool.assert_awaited_once()
