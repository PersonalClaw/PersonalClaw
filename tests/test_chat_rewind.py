"""True rewind — edit ANY past user message and replay from there (CHAT-CRAFT S1).

Upgrades edit-resend's ``rewind: true`` from destructive truncate to fork-and-swap
under the same slot: the discarded tail is snapshotted onto the edited user message
(``rewound`` chain, capped), the transcript is truncated, and the provider is reset
so the next turn rebuilds context from the truncated transcript. The old timeline
survives in the message dict; a reload restores it. Pre-rewind sessions load
unchanged (the field is a clean break under the pre-1.0 banner — tolerant reads).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

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
    monkeypatch.setattr("personalclaw.dashboard.chat_regenerate._run_chat", _noop_run_chat)


def _seed(state, name: str, n_turns: int):
    """A persistent session with n_turns user→assistant turns, stamped by index."""
    session = state.get_or_create_session(name)
    for i in range(n_turns):
        session.append("user", f"q{i}", "msg msg-u", ts=f"2026-06-30T05:0{i}:00+00:00")
        session.append("assistant", f"a{i}", "msg msg-a", ts=f"2026-06-30T05:0{i}:30+00:00")
    session.drain()
    return session


class TestRewind:
    @pytest.mark.asyncio
    async def test_rewind_at_earlier_turn_retains_tail_and_resets_provider(
        self, tmp_path, monkeypatch
    ):
        """Editing turn 0 of a 3-turn chat replays under the SAME slot; the old tail
        survives on the edited message; the provider is reset so context rebuilds."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        session = _seed(state, "s1", 3)  # 6 messages
        broadcasts: list[tuple[str, object]] = []
        monkeypatch.setattr(
            state, "broadcast_ws", lambda t, d: broadcasts.append((t, d)), raising=True
        )

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"ts": "2026-06-30T05:00:00+00:00", "content": "q0-edited", "rewind": True},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            # retained = messages AFTER turn 0's user msg: a0, q1, a1, q2, a2 = 5
            assert body["rewound"] == 5

        # same slot key preserved
        assert session.key == "s1"
        # only the edited user turn remains in the live transcript
        assert len(session.messages) == 1
        edited = session.messages[0]
        assert edited["role"] == "user" and edited["content"] == "q0-edited"
        # the discarded tail (old edited-turn content + everything after) is retained
        assert len(edited["rewound"]) == 1
        retained_msgs = edited["rewound"][0]["messages"]
        assert [m["content"] for m in retained_msgs] == ["q0", "a0", "q1", "a1", "q2", "a2"]
        # provider was reset (fork-and-swap) and clients were told to re-hydrate
        state.sessions.reset.assert_awaited_once()
        assert any(t == "chat_rewound" for t, _ in broadcasts)

    @pytest.mark.asyncio
    async def test_rewind_survives_reload(self, tmp_path, monkeypatch):
        """The rewound tail must reach disk and rehydrate on a fresh read."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        _seed(state, "s1", 3)
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: None, raising=True)

        async with TestClient(TestServer(_make_app(state))) as client:
            r = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"ts": "2026-06-30T05:00:00+00:00", "content": "q0-edited", "rewind": True},
            )
            assert r.status == 200

        from personalclaw.dashboard.chat_persistence import _rehydrate_session_from_history

        # drop from memory, reload from disk
        state._sessions.pop("s1", None)
        reloaded = _rehydrate_session_from_history(state, "s1")
        assert reloaded is not None
        assert len(reloaded.messages) == 1
        assert reloaded.messages[0].get("rewound")
        retained = reloaded.messages[0]["rewound"][0]["messages"]
        assert [m["content"] for m in retained] == ["q0", "a0", "q1", "a1", "q2", "a2"]

    @pytest.mark.asyncio
    async def test_rewind_snapshot_cap(self, tmp_path, monkeypatch):
        """Repeated rewinds on the same turn are capped at _MAX_REWIND_SNAPSHOTS."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat_regenerate import _MAX_REWIND_SNAPSHOTS

        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        session = _seed(state, "s1", 2)
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: None, raising=True)

        async with TestClient(TestServer(_make_app(state))) as client:
            for i in range(_MAX_REWIND_SNAPSHOTS + 3):
                # each rewind edits the (now-single) user turn, then re-adds a turn
                # to have a tail to retain on the next rewind
                r = await client.post(
                    "/api/chat/sessions/s1/edit-resend",
                    json={"content": f"e{i}", "rewind": True},
                )
                assert r.status == 200
                session.append("assistant", f"ans{i}", "msg msg-a")

        edited = [m for m in session.messages if m["role"] == "user"][-1]
        assert len(edited.get("rewound", [])) <= _MAX_REWIND_SNAPSHOTS

    @pytest.mark.asyncio
    async def test_no_rewind_flag_is_byte_identical(self, tmp_path, monkeypatch):
        """Without rewind, the last-turn path is unchanged: no tail, no provider reset."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        session = _seed(state, "s1", 1)
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: None, raising=True)

        async with TestClient(TestServer(_make_app(state))) as client:
            r = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"content": "q0-edited"},  # no rewind flag
            )
            assert r.status == 200
            assert (await r.json())["rewound"] == 0

        users = [m for m in session.messages if m["role"] == "user"]
        assert len(users) == 1 and users[0]["content"] == "q0-edited"
        assert "rewound" not in users[0]
        state.sessions.reset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rewind_refused_while_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        session = _seed(state, "s1", 2)

        import asyncio

        async def _never():
            await asyncio.sleep(60)

        session.task = asyncio.ensure_future(_never())
        try:
            async with TestClient(TestServer(_make_app(state))) as client:
                r = await client.post(
                    "/api/chat/sessions/s1/edit-resend",
                    json={"content": "x", "rewind": True},
                )
                assert r.status == 409
        finally:
            session.task.cancel()
