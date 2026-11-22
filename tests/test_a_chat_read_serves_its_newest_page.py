"""A read of a chat that asks for no page serves its newest page, and redacts it off the event loop.

Measured before this was written: ``GET /api/chat/sessions/{session}`` with neither ``limit`` nor
``before`` served every message the chat ever had, and redacted each one on the gateway's event
loop. One chat of 885 messages was a 3.5 MB reply that took 17.5 s, and a script reading 28 chats
once a minute held health checks for 50 to 86 s. Redacting a chat of 3,000 messages here held the
loop for half a second.

Now a read that asks for no page serves the newest 200 messages, with ``has_more`` and ``total`` as
a ``limit`` read does, and ``before`` pages back from there. ``?all=1`` serves the whole history:
the dashboard's chat page asks for it, since it has no older pages yet. The rows are taken as they
are in the step that reads ``stream_seq``, and redacted off the event loop.
"""

from __future__ import annotations

import threading

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.dashboard import chat_handlers

pytestmark = pytest.mark.asyncio

MESSAGES = 450


def _long_chat(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    chat = state.get_or_create_session("long")
    for i in range(MESSAGES):
        chat.append("user" if i % 2 == 0 else "assistant", f"msg {i}", broadcast=False)
    return state, chat


async def _read(state, query: str = "") -> dict:
    async with TestClient(TestServer(_make_app(state))) as client:
        resp = await client.get(f"/api/chat/sessions/long{query}")
        assert resp.status == 200, await resp.text()
        return await resp.json()


async def test_a_read_that_asks_for_no_page_serves_the_newest_page(tmp_path, monkeypatch):
    """🔴 Red before: it served all 450."""
    state, _ = _long_chat(tmp_path, monkeypatch)

    data = await _read(state)

    assert len(data["messages"]) == 200
    assert data["messages"][-1]["content"] == f"msg {MESSAGES - 1}"
    assert data["messages"][0]["content"] == f"msg {MESSAGES - 200}"
    assert data["has_more"] is True
    assert data["total"] == MESSAGES


async def test_before_pages_back_from_the_newest_page(tmp_path, monkeypatch):
    state, _ = _long_chat(tmp_path, monkeypatch)

    older = await _read(state, f"?before={MESSAGES - 200}")
    oldest = await _read(state, f"?before={MESSAGES - 400}")

    assert [m["content"] for m in older["messages"]][::199] == ["msg 50", "msg 249"]
    assert older["has_more"] is True
    assert [m["content"] for m in oldest["messages"]] == [f"msg {i}" for i in range(50)]
    assert oldest["has_more"] is False


async def test_all_serves_the_whole_history(tmp_path, monkeypatch):
    state, _ = _long_chat(tmp_path, monkeypatch)

    data = await _read(state, "?all=1")

    assert [m["content"] for m in data["messages"]] == [f"msg {i}" for i in range(MESSAGES)]
    assert data["has_more"] is False
    assert data["total"] == MESSAGES


@pytest.fixture
def prepared_on(monkeypatch):
    """The thread each read's rows are redacted on, and a chunk that lands while they are."""
    seen: list[int] = []
    real = chat_handlers._prepare_messages

    def prepare(rows, running):
        seen.append(threading.get_ident())
        for hook in landing:
            hook()
        return real(rows, running)

    landing: list = []
    monkeypatch.setattr(chat_handlers, "_prepare_messages", prepare)
    return seen, landing


async def test_the_rows_are_redacted_off_the_event_loop(tmp_path, monkeypatch, prepared_on):
    """🔴 Red before: on the loop's own thread, which serves every other chat."""
    seen, _ = prepared_on
    state, _ = _long_chat(tmp_path, monkeypatch)

    await _read(state, "?all=1")

    assert seen and threading.get_ident() not in seen


async def test_a_chunk_that_lands_while_the_rows_are_redacted_is_not_served(
    tmp_path, monkeypatch, prepared_on
):
    """The rows are as they were when ``stream_seq`` was read: a page that resumes the answer
    from that stamp takes the chunk from the socket, and would show it twice if the row held it."""
    _, landing = prepared_on
    state, chat = _long_chat(tmp_path, monkeypatch)
    chat.messages.append({"role": "streaming", "content": "The manual covers", "cls": "msg msg-a"})
    live = chat.messages[-1]
    landing.append(lambda: live.update(content=live["content"] + " setup and firmware."))

    data = await _read(state)

    assert data["messages"][-1] == {
        "role": "streaming",
        "content": "The manual covers",
        "cls": "msg msg-a",
    }
