"""An unreadable seen record reads as nothing seen: every finished reply shows unread.

``chat_seen.json`` is what clears a chat's unread mark. A file that cannot be read must never hide
a finished reply, so it reads as nothing seen and the failure is logged, and the file is kept as it
is (with a copy) rather than overwritten by the next mark.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from test_chat_turn_outcome import _session, _streaming, _turn
from test_seen_is_kept_by_the_gateway import _call, _reply, _rows

from personalclaw import chat_seen


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(chat_seen, "path", lambda: tmp_path / chat_seen.FILE)


@pytest.mark.asyncio
async def test_an_unreadable_record_serves_every_finished_reply_as_unread(tmp_path, caplog):
    state = _streaming(tmp_path, _reply())
    for key in ("s1", "s2"):
        await _turn(state, _session(state, key), "hello")
    # Every chat was seen after its reply, then the record was damaged.
    chat_seen.advance_every(chat_seen._now())
    (tmp_path / chat_seen.FILE).write_text("{ not json")

    with caplog.at_level(logging.WARNING):
        rows = await _rows(state)
    assert all(r["attention"]["unread"] for r in rows.values()), "a finished reply was hidden"
    assert any("chat_seen.json" in r.getMessage() for r in caplog.records), "the failure is said"

    status, body = await _call(state, "POST", "/api/chat/sessions/s1/seen", json={})
    assert status == 409 and "seen_unreadable" in str(body), body
    assert (tmp_path / chat_seen.FILE).read_text() == "{ not json", "kept as it is"
    await asyncio.sleep(0)
