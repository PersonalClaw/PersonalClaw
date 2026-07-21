"""Edit & resend (chat_regenerate.api_chat_session_edit_resend) regression tests.

The bug: a user turn sent live this session has no ts (the server skips
broadcasting the user echo — "FE adds optimistically"), so Edit & resend's
by-ts lookup found nothing and the endpoint 400'd "index or ts required". The
fix is a graceful cascade (ts → index → last user message) + a client-supplied
ts stored on the (re-)appended message so a repeat edit still matches.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat import api_chat_session_edit_resend


def _make_app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend)
    return app


async def _noop_run_chat(state, session, msg, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _mock_run_chat(monkeypatch):
    # Edit-resend spawns _run_chat (real LLM turn); stub it so the test exercises
    # only the message-location + re-append logic.
    monkeypatch.setattr("personalclaw.dashboard.chat_regenerate._run_chat", _noop_run_chat)


class TestEditResend:
    @pytest.mark.asyncio
    async def test_falls_back_to_last_user_when_no_ts_or_index(self, tmp_path, monkeypatch):
        """The bug: a live user turn has no ts. Edit & resend with neither ts nor a
        valid index must still work — fall back to the last user message."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "original question", "msg msg-u")  # server-stamped ts

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"content": "edited question"},  # NO ts, NO index — the bug repro
            )
            assert resp.status == 200
            assert (await resp.json())["ok"] is True

        users = [m for m in session.messages if m["role"] == "user"]
        assert len(users) == 1  # original truncated, edited re-appended
        assert users[0]["content"] == "edited question"

    @pytest.mark.asyncio
    async def test_client_ts_stored_enables_repeat_edit(self, tmp_path, monkeypatch):
        """A client_ts is stored on the re-appended message, so an immediate SECOND
        edit-resend locates it by ts (the live-turn case that used to 400)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "q1", "msg msg-u")

        async with TestClient(TestServer(_make_app(state))) as client:
            r1 = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"content": "q2", "client_ts": "2026-06-30T05:00:00+00:00"},
            )
            assert r1.status == 200
            # the re-appended message carries the client ts verbatim
            assert [m for m in session.messages if m["role"] == "user"][-1][
                "ts"
            ] == "2026-06-30T05:00:00+00:00"

            # a SECOND edit, now locating by that ts, succeeds (the original bug)
            r2 = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={
                    "content": "q3",
                    "ts": "2026-06-30T05:00:00+00:00",
                    "client_ts": "2026-06-30T05:01:00+00:00",
                },
            )
            assert r2.status == 200
            users = [m for m in session.messages if m["role"] == "user"]
            assert len(users) == 1 and users[0]["content"] == "q3"
            assert users[0]["ts"] == "2026-06-30T05:01:00+00:00"

    @pytest.mark.asyncio
    async def test_stale_ts_falls_through_to_last_user(self, tmp_path, monkeypatch):
        """A ts that matches nothing degrades gracefully to the last user message
        rather than 400 'user message not found for ts'."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "q1", "msg msg-u")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"content": "q2", "ts": "1999-01-01T00:00:00+00:00"},  # stale/no-match
            )
            assert resp.status == 200
        assert [m for m in session.messages if m["role"] == "user"][-1]["content"] == "q2"

    @pytest.mark.asyncio
    async def test_empty_content_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "q1", "msg msg-u")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/edit-resend", json={"content": "   "})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_no_user_message_to_edit(self, tmp_path, monkeypatch):
        """A session with no user message yet → clean 400, not a crash."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/edit-resend", json={"content": "x"})
            assert resp.status == 400
