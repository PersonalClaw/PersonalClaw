"""Follow-up chips — after a completed turn, one cheap background
call suggests 2-3 next messages, broadcast over the chat_followups WS event.

Gated OFF for restricted (temporary/incognito) sessions, a non-empty queue, an
errored turn, or config-disabled; silent (no event) when no model is bound.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_followups import (
    _build_exchange,
    _maybe_followups,
    _parse_followups,
)


def _mock_bg_stream(monkeypatch, text):
    """Answer the follow-up chore (``chores.run_chore``) with *text*."""

    async def _run_chore(prompt, **_kw):
        _run_chore.last_prompt = prompt
        return text

    monkeypatch.setattr("personalclaw.chores.run_chore", _run_chore)
    return _run_chore


def _seeded_session(state):
    session = state.get_or_create_session("s1")
    session.append("user", "How do I read a file in Python?", "msg msg-u", broadcast=False)
    session.append("assistant", "Use open() with a context manager.", "msg msg-a", broadcast=False)
    session.drain()
    return session


class TestParse:
    def test_parses_json_array_capped_and_length_bounded(self):
        raw = '["Show me an example", "How do I test this?", "' + "x" * 80 + '", "d", "e"]'
        out = _parse_followups(raw)
        assert out == ["Show me an example", "How do I test this?", "d"]  # >60 dropped, capped 3

    def test_strips_fences(self):
        assert _parse_followups('```json\n["a", "b"]\n```') == ["a", "b"]

    def test_garbage_is_empty(self):
        assert _parse_followups("not json at all") == []
        assert _parse_followups("{}") == []


class TestBuildExchange:
    def test_uses_last_user_and_assistant(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        ex = _build_exchange(session)
        assert "user: How do I read a file" in ex
        assert "assistant: Use open()" in ex


class TestMaybeFollowups:
    @pytest.mark.asyncio
    async def test_emits_chips_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        _mock_bg_stream(monkeypatch, '["Show a code example", "How do I handle errors?"]')
        events: list[tuple[str, object]] = []
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

        await _maybe_followups(state, session)

        followups = [d for t, d in events if t == "chat_followups"]
        assert followups and followups[0]["items"] == [
            "Show a code example",
            "How do I handle errors?",
        ]

    @pytest.mark.asyncio
    async def test_disabled_config_emits_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_followups._followups_enabled", lambda: False
        )
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        get_or_create = AsyncMock()
        state.sessions.get_or_create = get_or_create
        events: list[tuple[str, object]] = []
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

        await _maybe_followups(state, session)

        assert not events
        get_or_create.assert_not_awaited()  # no generation task even created

    @pytest.mark.asyncio
    async def test_restricted_session_emits_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        session.memory_mode = "incognito"  # → is_restricted True
        state.sessions.get_or_create = AsyncMock()
        events: list[tuple[str, object]] = []
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

        await _maybe_followups(state, session)
        assert not events

    @pytest.mark.asyncio
    async def test_queued_message_emits_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        session.queue_append("next thing")
        state.sessions.get_or_create = AsyncMock()
        events: list[tuple[str, object]] = []
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

        await _maybe_followups(state, session)
        assert not events

    @pytest.mark.asyncio
    async def test_errored_turn_emits_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        session._last_turn_errored = True
        state.sessions.get_or_create = AsyncMock()
        events: list[tuple[str, object]] = []
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

        await _maybe_followups(state, session)
        assert not events

    @pytest.mark.asyncio
    async def test_no_model_bound_is_silent(self, tmp_path, monkeypatch):
        """get_or_create raising (no model bound) must be swallowed — no event, no crash."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        state.sessions.get_or_create = AsyncMock(side_effect=RuntimeError("no model bound"))
        state.sessions.release = MagicMock()
        events: list[tuple[str, object]] = []
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

        await _maybe_followups(state, session)  # must not raise
        assert not events

    @pytest.mark.asyncio
    async def test_empty_result_emits_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seeded_session(state)
        _mock_bg_stream(monkeypatch, "[]")
        events: list[tuple[str, object]] = []
        monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

        await _maybe_followups(state, session)
        assert not events
