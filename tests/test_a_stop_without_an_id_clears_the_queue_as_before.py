"""A Stop that names no turn does what Stop always did; only its answer says more.

``POST /api/chat/sessions/{session}/stop`` with no ``message_id`` (an older page, a script) stops
what runs and empties the chat's queue, as it did before Stop could name its turn, whatever body it
carries. The first three tests here hold on the tree before that change too: the queue it leaves
is the same. The last is what the change adds: ``withdrawn`` names the messages the queue held,
which that Stop already removed unsent.

Driven through the real Stop handler, over a chat whose turn is running (a task not yet done) and a
session manager that acknowledges the stop.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw import ids


async def _stopped(tmp_path, **request):
    """Stop the running turn of a chat whose queue holds two messages: the answer's status and
    body, the chat, the two messages' ids and the session manager's stop."""
    state = _make_state(tmp_path)
    state.sessions.stop_turn = AsyncMock(return_value="soft")
    session = state.get_or_create_session("s1")
    running = asyncio.get_running_loop().create_future()
    session.task = running
    waiting = [ids.new_id("msg"), ids.new_id("msg")]
    for n, message_id in enumerate(waiting):
        session.queue_append(f"Queued message {n}.", message_id=message_id)
    try:
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/stop", **request)
            answer = await resp.json()
    finally:
        running.cancel()
    return resp.status, answer, session, waiting, state.sessions.stop_turn


@pytest.mark.asyncio
async def test_a_stop_with_no_body_stops_the_turn_and_empties_the_queue(tmp_path):
    status, answer, session, _, stop_turn = await _stopped(tmp_path)

    assert status == 200 and answer["ok"] is True and answer["stopped"] is True, answer
    assert session._queue == []
    stop_turn.assert_awaited_once()
    assert stop_turn.await_args.kwargs == {"force": False}


@pytest.mark.asyncio
async def test_a_stop_with_an_empty_body_does_the_same(tmp_path):
    status, answer, session, _, stop_turn = await _stopped(tmp_path, json={})

    assert status == 200 and answer["stopped"] is True, answer
    assert session._queue == []
    stop_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_stop_with_a_body_that_is_no_object_does_the_same(tmp_path):
    status, answer, session, _, stop_turn = await _stopped(
        tmp_path, data="stop", headers={"Content-Type": "text/plain"}
    )

    assert status == 200 and answer["stopped"] is True, answer
    assert session._queue == []
    stop_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_its_answer_names_what_the_queue_held(tmp_path):
    status, answer, session, waiting, _ = await _stopped(tmp_path)

    assert status == 200, answer
    assert answer["withdrawn"] == waiting
    assert session._queue == []
    assert "turn_message_id" not in answer and "state" not in answer, "it named no turn"
