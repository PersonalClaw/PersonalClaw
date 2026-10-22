"""A chat's bookmarked messages (#4776): one message in or out, only messages you sent, kept."""

from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state

from personalclaw.dashboard.chat_bookmarks import MAX_BOOKMARKS, api_chat_session_bookmarks
from personalclaw.dashboard.state import _ChatSession

ASKED = "2026-10-22T07:00:00+00:00"
ASKED_AGAIN = "2026-10-22T07:05:00+00:00"
ANSWERED = "2026-10-22T07:00:09+00:00"


def _app(state):
    app = _api_app(state)
    app.router.add_put("/api/chat/sessions/{session}/bookmarks", api_chat_session_bookmarks)
    return app


def _chat(state) -> _ChatSession:
    session = _ChatSession("s1")
    session.messages = [
        {"role": "user", "content": "Plan the garden", "ts": ASKED},
        {"role": "assistant", "content": "Beds by the fence.", "ts": ANSWERED},
        {"role": "user", "content": "And the shed?", "ts": ASKED_AGAIN},
    ]
    state._sessions["s1"] = session
    return session


@pytest.mark.asyncio
async def test_a_message_goes_in_and_out_and_the_others_stay(tmp_path):
    state = _make_state(tmp_path)
    session = _chat(state)
    with patch("personalclaw.dashboard.chat_bookmarks.save_session_to_history") as saved:
        async with TestClient(TestServer(_app(state))) as client:
            url = "/api/chat/sessions/s1/bookmarks"
            first = await client.put(url, json={"add": [ASKED_AGAIN, ASKED, ASKED_AGAIN]})
            assert first.status == 200, await first.text()
            assert (await first.json())["bookmarks"] == [ASKED_AGAIN, ASKED]
            # Taking one off leaves the other, and taking off one that is not there changes nothing.
            off = await client.put(url, json={"remove": [ASKED_AGAIN, "2026-01-01T00:00:00+00:00"]})
            assert (await off.json())["bookmarks"] == [ASKED]
    assert session.bookmarks == [ASKED]
    assert saved.call_count == 2


@pytest.mark.asyncio
async def test_only_a_message_you_sent_here_is_bookmarked_and_a_refusal_changes_nothing(tmp_path):
    state = _make_state(tmp_path)
    session = _chat(state)
    session.bookmarks = [ASKED]
    with patch("personalclaw.dashboard.chat_bookmarks.save_session_to_history") as saved:
        async with TestClient(TestServer(_app(state))) as client:
            url = "/api/chat/sessions/s1/bookmarks"
            cases = [
                ({"add": [ANSWERED]}, "unknown_message"),  # the agent's reply, not yours
                ({"add": ["2026-01-01T00:00:00+00:00"]}, "unknown_message"),
                ({"remove": [ASKED], "add": [7]}, "invalid_field_type"),
                ({"add": ["x" * 65]}, "invalid_field_type"),
                ({"add": ""}, "invalid_field_type"),
                ({}, "invalid_request"),
                ({"bookmarks": [ASKED_AGAIN]}, "invalid_request"),
            ]
            for body, code in cases:
                resp = await client.put(url, json=body)
                assert resp.status == 400, body
                assert (await resp.json())["error"]["code"] == code, body
            gone = await client.put("/api/chat/sessions/nope/bookmarks", json={"add": [ASKED]})
            assert gone.status == 404
    assert session.bookmarks == [ASKED]
    saved.assert_not_called()


@pytest.mark.asyncio
async def test_a_conversation_keeps_a_bounded_number(tmp_path):
    state = _make_state(tmp_path)
    session = _chat(state)
    session.bookmarks = [f"2026-01-01T00:00:{i:02d}.{i:06d}+00:00" for i in range(MAX_BOOKMARKS)]
    with patch("personalclaw.dashboard.chat_bookmarks.save_session_to_history"):
        async with TestClient(TestServer(_app(state))) as client:
            resp = await client.put("/api/chat/sessions/s1/bookmarks", json={"add": [ASKED]})
            assert resp.status == 400
    assert ASKED not in session.bookmarks


def test_every_window_reads_them_on_the_session():
    session = _ChatSession("s1")
    session.bookmarks = [ASKED]
    assert session.to_dict()["bookmarks"] == [ASKED]
