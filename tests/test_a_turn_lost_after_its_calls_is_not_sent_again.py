"""A turn whose agent is lost after it made calls is not sent again on its own.

Measured: an agent CLI's connection closed in the middle of a turn that had already run commands,
and PersonalClaw sent her message again by itself ("⟳ Connection lost — retrying..."), so each of
those commands could run a second time with nobody asked. Sent again, a turn repeats what it did.
So a turn that made calls nobody refused ends saying that the connection closed after them and
that her message was not sent again, and the chat's Retry sends it because she chose to. A turn
that made no call is still sent again on its own, as before: nothing it did can repeat.

Each way a runtime is lost mid-turn is pinned: its process dying under the stream
(``AcpProcessDied``), the client answering that its process exited (``AcpError``), and the turn's
own completion naming the error (``stopReason`` ``error: …``). Everything runs through the real
runner; only the runtime is a fake.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.errors import AcpError, AcpProcessDied
from personalclaw.dashboard import turn_endings
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent

_CALL = AgentEvent(
    kind=EVENT_TOOL_CALL,
    tool_call_id="c1",
    title="Run command",
    tool_input='{"command": "git status"}',
)


def _client(stream) -> AsyncMock:
    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
    return client


async def _answers():
    yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Recovered answer.")
    yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


def _state(tmp_path, first_turn) -> tuple[DashboardState, AsyncMock]:
    """A state whose first runtime streams ``first_turn`` and whose next one answers."""
    acquire = AsyncMock(
        side_effect=[(_client(first_turn), True, False), (_client(_answers), True, False)]
    )
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
    sessions.get_or_create = acquire
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    from personalclaw.hooks import ToolHookResult

    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    return state, acquire


def _session(state: DashboardState) -> _ChatSession:
    session = state.get_or_create_session("s1")
    session._trust = True
    session._titled = True  # the auto-title call is a model call of its own; not this contract
    return session


async def _turn(state: DashboardState, session: _ChatSession) -> None:
    """Run one turn, and every turn it hands its message on to (a resend runs as the next)."""
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, "check the repo")
        while session.task is not None and not session.task.done():
            await asyncio.wait_for(session.task, timeout=10)


def _errors(session: _ChatSession) -> list[str]:
    return [m["content"] for m in session.messages if m.get("role") == "error"]


def _outcomes(state: DashboardState) -> list[str]:
    return [
        c.args[1].get("outcome")
        for c in state.broadcast_ws.call_args_list
        if c.args and c.args[0] == "chat_done"
    ]


async def _dies_after_a_call():
    yield _CALL
    raise AcpProcessDied("agent exited")


async def _exits_after_a_call():
    yield _CALL
    raise AcpError("ACP process exited unexpectedly")


async def _ends_in_an_error_after_a_call():
    yield _CALL
    yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="error: agent exited")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_turn",
    [_dies_after_a_call, _exits_after_a_call, _ends_in_an_error_after_a_call],
    ids=["process-died", "process-exited", "error-stop"],
)
async def test_a_turn_lost_after_a_call_ends_saying_so_and_is_not_sent_again(tmp_path, first_turn):
    state, acquire = _state(tmp_path, first_turn)
    session = _session(state)

    await _turn(state, session)

    assert acquire.await_count == 1, "her message was sent again by itself"
    said = turn_endings.lost_after_steps_notice("", 1)
    assert said == (
        "The connection to the agent closed after 1 step of this turn, so your message was not "
        "sent again: that could repeat them. Send it again to retry."
    )
    assert _errors(session) == [said], session.messages
    assert not any("retrying" in e for e in _errors(session))
    assert _outcomes(state) == ["error"]
    assert session._last_turn_errored is True


@pytest.mark.asyncio
async def test_a_turn_lost_before_any_call_is_still_sent_again(tmp_path):
    """The control: nothing ran, so nothing repeats, and the message is sent again on its own."""

    async def dies_at_once():
        raise AcpProcessDied("agent exited")
        yield  # an async generator that dies before its first event

    state, acquire = _state(tmp_path, dies_at_once)
    session = _session(state)

    await _turn(state, session)

    assert acquire.await_count == 2
    assert any("retrying" in e for e in _errors(session)), session.messages
    assert any(
        m.get("role") == "assistant" and "Recovered answer." in m.get("content", "")
        for m in session.messages
    ), session.messages
    assert _outcomes(state) == ["complete"]
