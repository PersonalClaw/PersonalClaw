"""Conversation-turn rollback — /undo N (power-user-surfaces P7).

Truncates the session's message history to a prior turn boundary (in-memory AND the
persisted transcript), and is honest that side effects are NOT reverted.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chat_test_helpers import _make_app, _make_state


async def _client(state) -> TestClient:
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    return client


def _seed_turns(state, n_turns: int):
    """A persistent session with n_turns user→assistant turns."""
    session = state.get_or_create_session(name=None)
    for i in range(n_turns):
        session.append("user", f"q{i}", f"msg u{i}", broadcast=False)
        session.append("assistant", f"a{i}", f"msg a{i}", broadcast=False)
    session.drain()
    return session


@pytest.mark.asyncio
async def test_undo_one_turn(tmp_path) -> None:
    state = _make_state(tmp_path)
    session = _seed_turns(state, 3)  # 6 messages, 3 turns
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": 1})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True and body["turns_undone"] == 1
        assert "NOT reverted" in body["notice"]  # honest side-effect notice
        # one turn (user q2 + assistant a2) removed → 4 messages, 2 turns remain
        assert len(session.messages) == 4
        assert session.messages[-1]["content"] == "a1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_undo_multiple_turns(tmp_path) -> None:
    state = _make_state(tmp_path)
    session = _seed_turns(state, 4)
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": 2})
        body = await resp.json()
        assert body["turns_undone"] == 2
        assert len(session.messages) == 4  # 4 turns → undo 2 → 2 turns (4 msgs)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_undo_caps_at_available_turns(tmp_path) -> None:
    state = _make_state(tmp_path)
    session = _seed_turns(state, 2)
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": 99})
        body = await resp.json()
        assert body["turns_undone"] == 2  # capped at what exists
        assert len(session.messages) == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_undo_persists_across_reload(tmp_path) -> None:
    """The truncation must reach disk — a reload must not resurrect undone turns."""
    state = _make_state(tmp_path)
    session = _seed_turns(state, 3)
    from personalclaw.dashboard.chat_utils import _history_key_for
    hk = _history_key_for(session.key)
    client = await _client(state)
    try:
        await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": 1})
        # on-disk transcript reflects the truncation (4 messages, not 6)
        on_disk = state.conversation_log.read_messages(hk)
        assert len([m for m in on_disk if m.get("role") in ("user", "assistant")]) == 4
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_undo_bad_n_rejected(tmp_path) -> None:
    state = _make_state(tmp_path)
    session = _seed_turns(state, 1)
    client = await _client(state)
    try:
        for bad in (0, -1, "two"):
            resp = await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": bad})
            assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_undo_empty_session(tmp_path) -> None:
    state = _make_state(tmp_path)
    session = state.get_or_create_session(name=None)
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": 1})
        assert resp.status == 400  # no turns to undo
    finally:
        await client.close()
