"""Restore a rewind tail as a fork (CHAT-CRAFT S1 — restore = fork, never swap).

The rewind tail retained on an edited user message can be reconstructed into a
NEW session: pre-edit history + the retained tail. The active timeline is never
mutated (no in-place branch switch).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat import (
    api_chat_session_edit_resend,
    api_chat_session_fork_rewound,
)


def _make_app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend)
    app.router.add_post("/api/chat/sessions/{session}/fork-rewound", api_chat_session_fork_rewound)
    return app


async def _noop_run_chat(state, session, msg, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _mock_run_chat(monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.chat_regenerate._run_chat", _noop_run_chat)


def _seed(state, name: str, n_turns: int):
    session = state.get_or_create_session(name)
    for i in range(n_turns):
        session.append("user", f"q{i}", "msg msg-u", ts=f"2026-06-30T05:0{i}:00+00:00")
        session.append("assistant", f"a{i}", "msg msg-a", ts=f"2026-06-30T05:0{i}:30+00:00")
    session.drain()
    return session


@pytest.mark.asyncio
async def test_fork_rewound_reconstructs_pre_edit_plus_tail(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.sessions.reset = AsyncMock()
    _seed(state, "s1", 3)  # q0/a0, q1/a1, q2/a2
    monkeypatch.setattr(state, "broadcast_ws", lambda t, d: None, raising=True)

    async with TestClient(TestServer(_make_app(state))) as client:
        # rewind at turn index 2 (the user turn q1, visible index 2) → retains a1,q2,a2
        r = await client.post(
            "/api/chat/sessions/s1/edit-resend",
            json={"ts": "2026-06-30T05:01:00+00:00", "content": "q1-edited", "rewind": True},
        )
        assert r.status == 200
        # live transcript is now: q0, a0, q1-edited  (visible indices 0,1,2)
        fr = await client.post("/api/chat/sessions/s1/fork-rewound", json={"index": 2})
        assert fr.status == 200
        body = await fr.json()
        assert body["ok"] is True
        new_key = body["key"]

    forked = state._sessions[new_key]
    contents = [m["content"] for m in forked.messages if m["role"] in ("user", "assistant")]
    # pre-edit (q0, a0) + retained tail (q1, a1, q2, a2)
    assert contents == ["q0", "a0", "q1", "a1", "q2", "a2"]
    # original session untouched (restore = fork, never swap)
    orig = [
        m["content"] for m in state._sessions["s1"].messages if m["role"] in ("user", "assistant")
    ]
    assert orig == ["q0", "a0", "q1-edited"]


@pytest.mark.asyncio
async def test_fork_rewound_no_tail_400(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.sessions.reset = AsyncMock()
    _seed(state, "s1", 2)
    async with TestClient(TestServer(_make_app(state))) as client:
        r = await client.post("/api/chat/sessions/s1/fork-rewound", json={"index": 0})
        assert r.status == 400  # no rewound chain on that turn
