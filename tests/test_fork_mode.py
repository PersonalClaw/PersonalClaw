"""Fork-mode inheritance on the session fork handler.

A forked session inherits the parent session's ``mode`` — the fork carries the
parent's conversational mode forward unchanged.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chat_test_helpers import _make_app, _make_state


async def _client(state) -> TestClient:
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    return client


def _seed_session(state, *, mode=""):
    """Create a persistent session with one user + one assistant message."""
    session = state.get_or_create_session(name=None, mode=mode)
    session.append("user", "hello", "msg msg-u", broadcast=False)
    session.append("assistant", "hi there", "msg msg-a", broadcast=False)
    session.drain()
    return session


@pytest.mark.asyncio
async def test_fork_inherits_parent_mode(tmp_path) -> None:
    state = _make_state(tmp_path)
    parent = _seed_session(state, mode="steer")
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{parent.key}/fork", json={})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        new_session = state._sessions[body["key"]]
        assert new_session.mode == "steer"  # inherited from parent
        assert new_session.forked_from  # set
        assert body["messages"] == 2  # full history copied
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fork_inherits_default_mode(tmp_path) -> None:
    state = _make_state(tmp_path)
    parent = _seed_session(state, mode="")
    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{parent.key}/fork", json={})
        body = await resp.json()
        assert state._sessions[body["key"]].mode == ""
    finally:
        await client.close()
