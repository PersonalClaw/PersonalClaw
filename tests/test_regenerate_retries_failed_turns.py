"""Rail: Regenerate on a FAILED turn is a working Retry, never a 400 (#254).

A failed chat turn persists as a ``role: "error"`` message with no assistant
text. The regenerate endpoint anchored on the last ``assistant`` message only,
which broke both ways on a failed turn:

- **No prior assistant** (first turn failed): HTTP 400 ``no assistant message
  to regenerate`` — the orphaned-bubble bug, a Regenerate affordance that can
  never work.
- **A prior assistant exists** (``[u1, a1, u2, error]``): the scan anchored on
  ``a1`` and truncated from AFTER ``u1`` — silently deleting the failed turn's
  own user message ``u2`` and replaying the already-answered ``u1``.

The fix anchors on the last *answer-shaped* turn (``assistant`` OR ``error``).
An error anchor is a clean RETRY: re-run that turn's own user message, stash no
variant (an error bubble is not an alternative answer), and send no
"vary your previous answer" hint (there is no previous answer). This rail pins
each of those edges plus the unchanged classic-regenerate behaviour.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state


class TestRegenerateRetriesFailedTurns:
    @pytest.mark.asyncio
    async def test_failed_first_turn_retries_instead_of_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("error", "Provider error: HTTP 500")
        session.drain()
        ran: list[tuple[str, str]] = []

        async def _capture(_state, _session, user_msg, regenerate_hint="", **kw):
            ran.append((user_msg, regenerate_hint))

        with patch("personalclaw.dashboard.chat_regenerate.run_chat", new=_capture):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200, "a failed turn must be retryable, not a 400"
                await asyncio.sleep(0)

        # The error bubble is gone; the user message is re-run verbatim.
        assert [m["role"] for m in session.messages] == ["user"]
        assert ran == [("hi", "")], "retry re-runs the failed user message with NO vary-hint"
        assert session._pending_variants == [], "an error bubble is never stashed as a variant"

    @pytest.mark.asyncio
    async def test_failed_turn_after_a_real_exchange_retries_its_own_message(
        self, tmp_path, monkeypatch
    ):
        """[u1, a1, u2, error] must retry u2 — not truncate it and replay u1."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "first question")
        session.append("assistant", "first answer")
        session.append("user", "second question")
        session.append("error", "Provider error: HTTP 500")
        session.drain()
        ran: list[str] = []

        async def _capture(_state, _session, user_msg, **kw):
            ran.append(user_msg)

        with patch("personalclaw.dashboard.chat_regenerate.run_chat", new=_capture):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                await asyncio.sleep(0)

        assert ran == ["second question"], "the FAILED turn's message is retried, not u1"
        # The first exchange survives intact; only the failed tail is cut.
        assert [m["role"] for m in session.messages] == ["user", "assistant", "user"]
        assert session.messages[1]["content"] == "first answer"

    @pytest.mark.asyncio
    async def test_classic_regenerate_still_stashes_variant_and_hints(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "hello v1")
        session.drain()
        ran: list[tuple[str, str]] = []
        stashed: list[dict] = []

        async def _capture(_state, _session, user_msg, regenerate_hint="", **kw):
            ran.append((user_msg, regenerate_hint))
            stashed.extend(list(session._pending_variants))

        with patch("personalclaw.dashboard.chat_regenerate.run_chat", new=_capture):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                await asyncio.sleep(0)

        assert len(ran) == 1 and ran[0][0] == "hi"
        assert "regenerated the previous response" in ran[0][1], "classic path keeps the hint"
        assert [v["content"] for v in stashed] == ["hello v1"], "real answers are stashed"

    @pytest.mark.asyncio
    async def test_error_with_no_preceding_user_still_400s(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("error", "startup failure")
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/regenerate")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_no_answer_shaped_turn_still_400s(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "only user")
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/regenerate")
            assert resp.status == 400
