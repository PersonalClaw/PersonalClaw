"""How a chat turn ENDED is a fact the gateway states, never one a client infers.

The chat's screen-reader live region said "Response complete." whenever streaming stopped: after
Stop, after an error, after a mid-turn retry notice. The one terminal frame, ``chat_done``, carried
only the session, so a client could not tell a finished answer from a stopped or failed one and
said "complete" for all three. The gateway does know: the provider's stop reason, a cancelled task,
a stop in progress, the turn's error flag. So it says so, in three places that must agree:

* the final ``chat_done`` names its ``outcome``: ``complete``, ``stopped`` or ``error``;
* session detail serves the same fact as ``last_turn_outcome``, committed before the frame goes
  out, so a tab that missed the frame (a reconnect's re-read, the stall reconciler) reads the
  answer the frame carried;
* the Stop answer says whether it ``stopped`` a turn.

A turn that is still running sends no ``chat_done`` at all. The deferred-compaction path sent one
mid-turn and then waited up to 120 s for the compaction, so the page settled and announced a
finished answer while the turn was still going.

Everything runs through the REAL runner and the REAL handlers; only the providers are fakes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from personalclaw.acp.errors import AcpError, AcpProcessDied
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent


def _client(stream) -> AsyncMock:
    """A runtime whose turn streams from ``stream()`` (an async generator function)."""
    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
    return client


def _state(tmp_path, acquire: AsyncMock) -> DashboardState:
    """A state whose runtime acquisition is ``acquire``, with every broadcast recorded."""
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
    return state


def _streaming(tmp_path, stream) -> DashboardState:
    return _state(tmp_path, AsyncMock(return_value=(_client(stream), True, False)))


def _failing(tmp_path, exc: BaseException) -> DashboardState:
    return _state(tmp_path, AsyncMock(side_effect=exc))


def _frames(state: DashboardState, kind: str) -> list[dict]:
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args and c.args[0] == kind]


def _session(state: DashboardState, key: str = "s1") -> _ChatSession:
    session = state.get_or_create_session(key)
    session._trust = True
    session._titled = True  # the auto-title call is a model call of its own; not this contract
    return session


async def _turn(state: DashboardState, session: _ChatSession, message: str) -> None:
    """Run one turn, and every re-dispatch it queues (a retry runs as the session's next task)."""
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, message)
        while session.task is not None and not session.task.done():
            await asyncio.wait_for(session.task, timeout=10)


async def _detail(state: DashboardState, key: str = "s1") -> dict:
    async with TestClient(TestServer(_make_app(state))) as client:
        resp = await client.get(f"/api/chat/sessions/{key}")
        assert resp.status == 200
        return await resp.json()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


# ── 1. the final frame and session detail name the same outcome ──────────────────────────


@pytest.mark.asyncio
async def test_a_finished_answer_ends_complete_on_its_frame_and_in_session_detail(tmp_path):
    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="All done.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    state = _streaming(tmp_path, stream)
    await _turn(state, _session(state), "hello")

    assert _frames(state, "chat_done") == [{"session": "s1", "outcome": "complete"}]
    detail = await _detail(state)
    assert detail["running"] is False
    assert detail["last_turn_outcome"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stopped_by_user", "cancelled"])
async def test_a_turn_cut_short_ends_stopped_not_complete(tmp_path, reason):
    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Half an ans")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason=reason)

    state = _streaming(tmp_path, stream)
    await _turn(state, _session(state), "hello")

    assert _frames(state, "chat_done") == [{"session": "s1", "outcome": "stopped"}]
    assert (await _detail(state))["last_turn_outcome"] == "stopped"


@pytest.mark.asyncio
async def test_a_turn_whose_task_is_cancelled_ends_stopped(tmp_path):
    """A force stop can cancel the turn before the provider reports any stop reason."""

    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Half an ans")
        raise asyncio.CancelledError

    state = _streaming(tmp_path, stream)
    await _turn(state, _session(state), "hello")

    assert _frames(state, "chat_done") == [{"session": "s1", "outcome": "stopped"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [RuntimeError("upstream said no"), AcpError("Prompt error: {'code': -32000}")],
    ids=["provider-error", "agent-error"],
)
async def test_a_failed_turn_ends_error(tmp_path, exc):
    state = _failing(tmp_path, exc)
    session = _session(state)
    await _turn(state, session, "hello")

    assert _frames(state, "chat_done") == [{"session": "s1", "outcome": "error"}]
    # The same flag the autonudge re-arm and follow-up chips read: an agent error that ended
    # the turn with an error card is an errored turn for them too.
    assert session._last_turn_errored is True
    assert (await _detail(state))["last_turn_outcome"] == "error"


@pytest.mark.asyncio
async def test_a_stop_in_progress_when_the_turn_ends_makes_it_stopped_not_failed(tmp_path):
    """A stop that escalates kills the runtime, and what a dying stream raises depends on the
    runtime. The user asked for the stop and the turn ended while it was in progress, so the turn
    was stopped, whatever the stream's last words were."""
    holder: dict[str, _ChatSession] = {}

    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Half an ans")
        holder["session"]._stop_state = "soft_pending"  # what the Stop handler sets first
        raise RuntimeError("the killed runtime closed its pipe")

    state = _streaming(tmp_path, stream)
    holder["session"] = _session(state)
    await _turn(state, holder["session"], "hello")

    assert _frames(state, "chat_done") == [{"session": "s1", "outcome": "stopped"}]


@pytest.mark.asyncio
async def test_a_retry_notice_is_not_the_end_of_the_turn(tmp_path):
    """ "⟳ Connection lost — retrying..." is an error-role message, and the turn goes on: the
    gateway re-queues the message and runs it again. Only the retry's end is the turn's end, and
    that retry finished, so the turn is complete."""

    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Recovered answer.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    acquire = AsyncMock(
        side_effect=[AcpProcessDied("agent exited"), (_client(stream), True, False)]
    )
    state = _state(tmp_path, acquire)
    session = _session(state)
    await _turn(state, session, "hello")

    notices = [m["content"] for m in session.messages if m.get("role") == "error"]
    assert notices and "retrying" in notices[0], session.messages
    assert _frames(state, "chat_done") == [{"session": "s1", "outcome": "complete"}]
    assert (await _detail(state))["last_turn_outcome"] == "complete"


# ── 2. a turn still running sends no terminal frame ──────────────────────────────────────


@pytest.mark.asyncio
async def test_deferred_compaction_ends_the_turn_once_after_the_compaction(tmp_path):
    """An agent that acknowledges `/compact` and compacts asynchronously: the runner discards the
    streamed "Compacting…" text and waits for the result. That wait is still this turn."""

    async def stream_command(command):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Compacting conversation...")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = _client(lambda: stream_command("/compact"))
    client.supports_native_commands = True
    client.compacts_in_process = False
    client.stream_command = stream_command
    client.wait_for_compaction = AsyncMock(return_value={"type": "completed", "summary": "short"})
    state = _state(tmp_path, AsyncMock(return_value=(client, True, False)))
    await _turn(state, _session(state), "/compact")

    client.wait_for_compaction.assert_awaited_once()
    kinds = [c.args[0] for c in state.broadcast_ws.call_args_list if c.args]
    compacted = next(
        i
        for i, c in enumerate(state.broadcast_ws.call_args_list)
        if c.args
        and c.args[0] == "chat_message"
        and str(c.args[1].get("content", "")).startswith("Conversation compacted")
    )
    assert kinds.count("chat_done") == 1, kinds
    assert kinds.index("chat_done") > compacted, "the turn was said to be over mid-compaction"
    # The discarded text still closes as a segment, so the result opens a fresh one.
    assert "chat_segment" in kinds[:compacted], kinds
    assert _frames(state, "chat_done") == [{"session": "s1", "outcome": "complete"}]


# ── 3. no outcome outlives the turn it describes ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reply_answered_locally_says_its_own_outcome_not_the_previous_turns(tmp_path):
    """A blocked slash command is answered before any runtime is asked and never reaches the
    turn's terminal path. It must not leave the previous turn's outcome for a reader to find."""
    state = _failing(tmp_path, RuntimeError("upstream said no"))
    session = _session(state)
    await _turn(state, session, "hello")
    assert (await _detail(state))["last_turn_outcome"] == "error"

    await _turn(state, session, "/quit")

    assert (await _detail(state))["last_turn_outcome"] == "complete"


# ── 4. the Stop answer says whether it stopped anything ──────────────────────────────────


def _stop_state(tmp_path, stop_outcome: str) -> DashboardState:
    sessions = MagicMock(count=0)
    sessions.stop_turn = AsyncMock(return_value=stop_outcome)
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )


async def _press_stop(state: DashboardState, *, running: bool, query: str = "") -> dict:
    session = state.get_or_create_session("s1")
    if running:
        session.task = asyncio.ensure_future(asyncio.sleep(999))
    try:
        with patch("personalclaw.dashboard.chat_handlers.sel", MagicMock()):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(f"/api/chat/sessions/s1/stop{query}")
                assert resp.status == 200
                return await resp.json()
    finally:
        if session.task is not None:
            session.task.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_outcome", ["soft", "hard"])
async def test_the_stop_answer_says_it_stopped_a_running_turn(tmp_path, stop_outcome):
    state = _stop_state(tmp_path, stop_outcome)
    assert await _press_stop(state, running=True) == {"ok": True, "stopped": True}


@pytest.mark.asyncio
async def test_a_forced_stop_answer_says_it_stopped(tmp_path):
    state = _stop_state(tmp_path, "hard")
    state.get_or_create_session("s1")._stop_state = "soft_pending"
    body = await _press_stop(state, running=True, query="?force=true")
    assert body == {"ok": True, "stopped": True}


@pytest.mark.asyncio
async def test_the_stop_answer_says_nothing_stopped_when_the_runtime_had_no_turn(tmp_path):
    """The runtime answered that no turn was in flight: this press stopped nothing, so a client
    must not announce a stop. The turn's own terminal frame says how it really ended."""
    state = _stop_state(tmp_path, "idle")
    assert await _press_stop(state, running=True) == {"ok": True, "stopped": False}


@pytest.mark.asyncio
async def test_the_stop_answer_says_nothing_stopped_for_an_idle_chat(tmp_path):
    state = _stop_state(tmp_path, "soft")
    assert await _press_stop(state, running=False) == {"ok": True, "stopped": False}
    state.sessions.stop_turn.assert_not_awaited()


# ── 5. a superseded turn was stopped by the message that replaced it ─────────────────────


@pytest.mark.asyncio
async def test_a_superseded_turn_ends_stopped(tmp_path, monkeypatch):
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard import chat_handlers

    policy = SimpleNamespace(
        resilience=SimpleNamespace(
            mid_turn_policy="cancel_and_replace", cancel_replace_min_interval_secs=0.0
        )
    )
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: policy))
    state = _stop_state(tmp_path, "soft")
    state.broadcast_ws = MagicMock()
    session = state.get_or_create_session("superseded-turn")

    with patch.object(chat_handlers, "sel", MagicMock()):
        resp = await chat_handlers._maybe_cancel_and_replace(
            state, session, "do this instead", None
        )

    assert resp is not None
    [done] = _frames(state, "chat_done")
    assert done["outcome"] == "stopped"
    assert done["superseded"] is True
