"""Rewind to here, and a resend she did not change, run her message as its row was stored.

Measured before this was written: Rewind to here and Edit & resend sent her message as its bubble
showed it, with the blocks she pasted, and the gateway wrote her row again from those alone. So a
widget's action ran again as its label, an optimized message as her words before the optimize, and
the files she attached, the knowledge items and artifacts she @-mentioned and how she said it
(voice) were gone from the row: the turn run again read none of them, and a reload lost their chips.

Now a message sent again as it is (``again``, or the row's own words) is written again as it was
stored, its own words and everything it carried, and its turn runs on those words. An edited
message keeps its attachments, mentions and how it was said; only its text is new, so what
described the old text (the widget's label, the words before an optimize, ``own_words``) goes.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat import api_chat_session_edit_resend
from personalclaw.dashboard.chat_runner import _turn_attachments

pytestmark = pytest.mark.asyncio

STORED = "Summarise the router manual I attached, with the notes on firmware."
SHOWN = "Summarise the manual"  # what her bubble showed: the words before the optimize
CARRIED = {
    "files": ["/attachments/router-manual.pdf"],
    "knowledge": ["k1"],
    "artifacts": ["firmware-notes"],
    "input_origin": "voice",
}
OF_ITS_TEXT = {"ui_label": "Summarise", "original": SHOWN, "own_words": SHOWN}


@pytest.fixture
def ran(monkeypatch):
    asked: list[str] = []

    async def run_chat(_state, _session, message, **_kw):
        asked.append(message)

    monkeypatch.setattr("personalclaw.dashboard.chat_regenerate.run_chat", run_chat)
    return asked


def _app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend)
    return app


async def _resend(tmp_path, monkeypatch, body: dict):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    chat = state.get_or_create_session("s1")
    chat.append("user", STORED, "msg msg-u", meta={**CARRIED, **OF_ITS_TEXT})
    chat.append("assistant", "It covers setup and firmware.", "msg msg-a")
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.post("/api/chat/sessions/s1/edit-resend", json=body)
        assert resp.status == 200, await resp.text()
    row = next(m for m in chat.messages if m["role"] == "user")
    return chat, row


async def test_a_rewind_runs_the_row_as_it_was_stored(tmp_path, monkeypatch, ran):
    """🔴 Red before: the row was her bubble's words and nothing it carried, and the turn ran on
    the bubble's words."""
    chat, row = await _resend(
        tmp_path, monkeypatch, {"content": SHOWN, "index": 0, "again": True, "rewind": True}
    )

    assert row["content"] == STORED
    assert {k: row["meta"].get(k) for k in CARRIED} == CARRIED
    assert {k: row["meta"].get(k) for k in OF_ITS_TEXT} == OF_ITS_TEXT
    assert ran == [STORED]


async def test_an_edited_message_keeps_what_it_carried_and_loses_what_described_its_old_text(
    tmp_path, monkeypatch, ran
):
    """🔴 Red before: its files, mentions and how it was said were gone."""
    _, row = await _resend(
        tmp_path, monkeypatch, {"content": "Summarise only the firmware notes.", "index": 0}
    )

    assert row["content"] == "Summarise only the firmware notes."
    assert {k: row["meta"].get(k) for k in CARRIED} == CARRIED
    assert not any(k in row["meta"] for k in OF_ITS_TEXT), row["meta"]
    assert ran == ["Summarise only the firmware notes."]


async def test_the_turn_run_again_reads_the_files_and_mentions_the_row_carries(
    tmp_path, monkeypatch, ran
):
    """🔴 Red before: the turn's readers found nothing on the row it wrote again."""
    from types import SimpleNamespace

    from personalclaw.chat_traces import attachment_roots
    from personalclaw.dashboard import referenced_content

    attached = attachment_roots()[0] + "router-manual.pdf"
    monkeypatch.setitem(CARRIED, "files", [attached])
    chat, _ = await _resend(tmp_path, monkeypatch, {"content": SHOWN, "index": 0, "again": True})

    assert _turn_attachments(chat) == [attached]
    items = {"k1": {"title": "Router manual", "content": "Hold reset for ten seconds."}}
    state = SimpleNamespace(knowledge_store=SimpleNamespace(get_item=items.get))
    asked = referenced_content._inject_knowledge_content(state, chat, STORED)
    assert "Hold reset for ten seconds." in asked
