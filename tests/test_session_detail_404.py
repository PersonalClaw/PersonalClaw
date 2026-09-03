"""GET /api/sessions/{key} — a missing session is a 404, not an empty transcript.

The endpoint used to answer ``200 []`` for ANY unknown key, so a polling client
could not tell a mistyped or deleted session from a real session with no
messages — it silently read emptiness as truth (the exact failure the loop
endpoints already 404 on). The one deliberate exception: a LIVE session whose
file has not been written yet (a just-opened tab) still answers ``[]`` —
answering 404 there would break polling a brand-new chat.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.handlers.sessions import api_session_detail


def _app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/sessions/{key}", api_session_detail)
    return app


@pytest.mark.asyncio
async def test_missing_session_is_a_coded_404(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.get("/api/sessions/nonexistent-xyz")
        assert resp.status == 404
        body = await resp.json()
        assert body["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_live_session_without_a_file_still_answers_empty(tmp_path, monkeypatch):
    """A just-opened tab has a live session and no file yet — that is a real
    session with no messages, not a 404."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.get_or_create_session("chat-1-123")
    async with TestClient(TestServer(_app(state))) as client:
        # Exact live key, and the prefixed history form that maps to it.
        for key in ("chat-1-123", "dashboard_chat-1-123"):
            resp = await client.get(f"/api/sessions/{key}")
            assert resp.status == 200, key
            assert await resp.json() == []


@pytest.mark.asyncio
async def test_saved_session_round_trips_and_deletion_becomes_404(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    log = state.conversation_log
    key = "dashboard_chat-9-999"
    log.append(key, "user", "hello")
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.get(f"/api/sessions/{key}")
        assert resp.status == 200
        msgs = await resp.json()
        assert msgs and msgs[0]["content"] == "hello"

        # The polling-client story: once deleted, the key must stop reading as
        # an empty-but-real session.
        assert log.delete_session(key) is True
        resp = await client.get(f"/api/sessions/{key}")
        assert resp.status == 404
        assert (await resp.json())["error"]["code"] == "session_not_found"
