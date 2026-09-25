"""The resume point a chat continues a live answer from: ``stream_seq``.

A chat rebuilds its transcript from ``GET /api/chat/sessions/{key}`` on a reload, a reconnect
and the session-create remount, and the answer still being written arrives in that snapshot as
one ``streaming`` message. The socket keeps delivering ``chat_chunk`` frames while the read is in
flight, so the client has to know, per chunk, whether the snapshot already holds it — or it
paints the overlap twice. Measured on a reload (day56b s22) the client instead dropped the
partial entirely: 170 and 209 chars cut from the start of answers of 6,580 and 3,857.

The contract under test, through the REAL chat runner and the REAL detail handler:

* every ``chat_chunk`` carries a stamp, and a detail snapshot reports the newest stamp its
  messages hold — so its ``streaming`` partial is EXACTLY the chunks stamped ``<= stream_seq``;
* stamps are process-wide and only ever grow — across turns, sessions and gateway restarts — so
  a watermark from one snapshot stays meaningful against any later chunk (a session evicted and
  rehydrated, or a restarted gateway, cannot restart the numbering under an open tab).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent


def _state(tmp_path, stream):
    """A DashboardState whose provider streams from ``stream()`` (an async generator)."""
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
    client = AsyncMock()
    client.provider_id = "anthropic"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    cb = MagicMock()
    from personalclaw.hooks import ToolHookResult

    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    state.context_builder = cb
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


def _chunk_frames(state) -> list[dict]:
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args[0] == "chat_chunk"]


@pytest.mark.asyncio
async def test_a_mid_answer_snapshot_holds_exactly_the_chunks_stamped_at_or_below_its_watermark(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    paused = asyncio.Event()
    resume = asyncio.Event()

    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="one, ")
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="two, ")
        paused.set()
        await resume.wait()  # the tab reloads here, mid-answer
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="three.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    state = _state(tmp_path, stream)
    session = state.get_or_create_session("s1")
    session._trust = True
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        turn = asyncio.create_task(run_chat(state, session, "count"))
        session.task = turn
        await asyncio.wait_for(paused.wait(), timeout=10)

        async with TestClient(TestServer(_make_app(state))) as client:
            r = await client.get("/api/chat/sessions/s1")
            assert r.status == 200
            snap = await r.json()

        before = _chunk_frames(state)
        assert [f["content"] for f in before] == ["one, ", "two, "]
        # The resume point is the newest stamp broadcast before the read, and the partial the
        # snapshot carries is exactly those chunks — no more, no fewer.
        assert snap["running"] is True
        assert snap["stream_seq"] == before[-1]["seq"]
        assert snap["messages"][-1] == {
            "role": "streaming",
            "content": "one, two, ",
            "cls": "msg msg-a",
        }

        resume.set()
        await asyncio.wait_for(turn, timeout=10)

    after = _chunk_frames(state)[len(before) :]
    assert [f["content"] for f in after] == ["three."]
    # A chunk the snapshot does not hold is stamped above its watermark — the client keeps it.
    assert all(f["seq"] > snap["stream_seq"] for f in after)


@pytest.mark.asyncio
async def test_chunk_stamps_are_process_wide_and_never_restart_across_turns_or_sessions(
    tmp_path, monkeypatch
):
    # Per-turn numbering (what this replaced) restarted at 1 on every turn, so a watermark read
    # during turn N would silently drop turn N+1's first chunks as "already shown".
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)

    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="a")
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="b")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    state = _state(tmp_path, stream)
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        for key in ("s1", "s1", "s2"):  # two turns in one session, then another session
            session = state.get_or_create_session(key)
            session._trust = True
            await run_chat(state, session, "go")

    stamps = [f["seq"] for f in _chunk_frames(state)]
    assert len(stamps) == 6
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps), stamps
    assert state.stream_seq == stamps[-1]


def test_chunk_stamps_never_restart_across_a_gateway_restart(tmp_path):
    # A tab outlives the gateway process and keeps the watermark it resumed from. A count that
    # restarted at zero would refuse every chunk of the new process as "already shown" until a
    # snapshot re-based it, and the reconnect's re-read is a request that can fail.
    before = _state(tmp_path, lambda: iter(()))
    last = [before.next_stream_seq() for _ in range(3)][-1]
    after = _state(tmp_path, lambda: iter(()))  # the next process
    assert after.next_stream_seq() > last
