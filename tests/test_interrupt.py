"""/interrupt — stop the turn, keep the queue.

Unlike /stop (which clears the queue), /interrupt soft-cancels the current turn
with preserve_queue=True so the _run_chat finally-block dequeue picks up the
next queued message. Preconditions: running + non-empty queue. An optional
queue_id promotes a message to the front (id-preserving).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat import api_chat_session_interrupt, api_chat_session_stop


class _FakeTask:
    """A task that looks running (not done) to session.running."""

    def done(self) -> bool:
        return False


async def _client(state) -> TestClient:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/interrupt", api_chat_session_interrupt)
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _running_session(state, *, queue=("msg B",)):
    session = state.get_or_create_session(name=None)
    session.task = _FakeTask()  # → session.running is True
    for c in queue:
        session.queue_append(c)
    return session


@pytest.mark.asyncio
async def test_interrupt_preserves_queue(tmp_path) -> None:
    state = _make_state(tmp_path)
    state.sessions.stop_turn = AsyncMock(return_value="soft")
    session = _running_session(state, queue=("queued one",))
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/interrupt", json={})
        assert resp.status == 200
        assert (await resp.json())["ok"] is True
        # stop_turn called with preserve_queue=True
        state.sessions.stop_turn.assert_awaited_once()
        assert state.sessions.stop_turn.call_args.kwargs["preserve_queue"] is True
        # Queue NOT cleared.
        assert len(session._queue) == 1
        # An "interrupting" stop_event was appended.
        assert any('"interrupting"' in m.get("content", "") for m in session.messages)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stop_still_clears_queue_by_default(tmp_path) -> None:
    """/stop clears the queue (preserve_queue defaults False)."""
    state = _make_state(tmp_path)
    state.sessions.stop_turn = AsyncMock(return_value="soft")
    session = _running_session(state, queue=("queued one",))
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/stop", json={})
        assert resp.status == 200
        # The stop handler clears the queue itself (session._queue.clear()).
        assert len(session._queue) == 0
        # And stop_turn was NOT told to preserve.
        kwargs = state.sessions.stop_turn.call_args.kwargs
        assert kwargs.get("preserve_queue", False) is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_interrupt_empty_queue_400(tmp_path) -> None:
    state = _make_state(tmp_path)
    state.sessions.stop_turn = AsyncMock(return_value="soft")
    session = _running_session(state, queue=())
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/interrupt", json={})
        assert resp.status == 400
        state.sessions.stop_turn.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_interrupt_not_running_noop(tmp_path) -> None:
    state = _make_state(tmp_path)
    state.sessions.stop_turn = AsyncMock(return_value="soft")
    session = state.get_or_create_session(name=None)  # no task → not running
    session.queue_append("x")
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/interrupt", json={})
        assert resp.status == 200
        assert (await resp.json()).get("info") == "not running"
        state.sessions.stop_turn.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_interrupt_queue_id_promotes_preserving_id(tmp_path) -> None:
    state = _make_state(tmp_path)
    state.sessions.stop_turn = AsyncMock(return_value="soft")
    session = state.get_or_create_session(name=None)
    session.task = _FakeTask()
    id_a = session.queue_append("first")
    id_b = session.queue_append("second")
    client = await _client(state)
    try:
        resp = await client.post(
            f"/api/chat/sessions/{session.key}/interrupt", json={"queue_id": id_b}
        )
        assert resp.status == 200
        # B promoted to front, id preserved.
        assert session._queue[0]["id"] == id_b
        assert session._queue[0]["content"] == "second"
        assert session._queue[1]["id"] == id_a
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_interrupt_unknown_queue_id_404(tmp_path) -> None:
    state = _make_state(tmp_path)
    state.sessions.stop_turn = AsyncMock(return_value="soft")
    session = _running_session(state, queue=("only one",))
    client = await _client(state)
    try:
        resp = await client.post(
            f"/api/chat/sessions/{session.key}/interrupt", json={"queue_id": "nonexistent"}
        )
        assert resp.status == 404
        state.sessions.stop_turn.assert_not_awaited()
    finally:
        await client.close()
