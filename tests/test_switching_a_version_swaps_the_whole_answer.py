"""Switching an answer to an earlier version shows that version whole, its tool calls included.

Measured before this was written (dogfood, a scratch twin): a regenerated answer's versions were
kept as text on the answer's first message, and switching versions replaced only that message's
text. A tool-using answer is several rows (text, its tool calls, more text), so after a switch the
transcript held the chosen version's text, then the CURRENT answer's tool rows and its later text,
and a reload drew that mix: with 1/4 chosen the page showed the newest answer's "Worked for" fold
and its after-tool paragraph.

Now a version keeps its answer's rows as they were written (bounded; a version too large keeps its
text alone, as every version once did), and a switch swaps the turn's rows for the chosen
version's, keeping the ones it leaves as that version's. The page is told the answer changed whole
and reads the chat again. A version's rows never go out on the wire: they are the turn's own rows
when it is shown, served as rows.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

pytestmark = pytest.mark.asyncio

TOOL = {"tool_call_id": "c1", "kind": "read", "input": "{}", "done": True, "output": "ok"}


def _shape(session) -> list[tuple[str, str]]:
    """The turn after the question, as (role, content)."""
    return [(m["role"], m.get("content", "")) for m in session.messages[1:]]


async def _two_versions(tmp_path, monkeypatch, *, fresh_uses_a_tool: bool = False):
    """A chat whose answer was regenerated: a tool-using answer into one message, or (with
    *fresh_uses_a_tool*) one message into a tool-using answer."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    session = state.get_or_create_session("s1")
    session.append("user", "What's the weather in Lisbon?")
    if fresh_uses_a_tool:
        session.append("assistant", "Probably mild.")
    else:
        session.append("assistant", "Looking it up.")
        session.append("tool", "read_file", meta=dict(TOOL))
        session.append("assistant", "It's 18°C and sunny.")
    session.drain()

    async def _fresh_answer(_state, _session, _user_msg, regenerate_hint="", **_kw):
        # What the replayed turn writes; its first message takes the stashed versions as
        # `chat_runner._flush_segment` attaches them.
        if fresh_uses_a_tool:
            session.append("assistant", "Looking it up.")
        else:
            session.append("assistant", "About 18 degrees, clear skies.")
        fresh = session.messages[-1]
        fresh["variants"] = [
            *session._pending_variants,
            {"content": fresh["content"], "ts": fresh.get("ts", "")},
        ]
        fresh["variant_idx"] = len(fresh["variants"]) - 1
        session._pending_variants = []
        if fresh_uses_a_tool:
            session.append("tool", "read_file", meta=dict(TOOL))
            session.append("assistant", "It's 18°C and sunny.")

    app = _make_app(state)
    with patch("personalclaw.dashboard.chat_regenerate.run_chat", new=_fresh_answer):
        async with TestClient(TestServer(app)) as client:
            assert (await client.post("/api/chat/sessions/s1/regenerate")).status == 200
            await asyncio.sleep(0.05)
    return state, session, app


async def _switch(app, index: int) -> None:
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": index})
        assert resp.status == 200, await resp.text()


async def test_switching_to_the_tool_using_version_shows_it_whole(tmp_path, monkeypatch):
    """🔴 Red before: the earlier version's text alone, its tool call gone."""
    _state, session, app = await _two_versions(tmp_path, monkeypatch)
    assert _shape(session) == [("assistant", "About 18 degrees, clear skies.")]

    await _switch(app, 0)

    assert _shape(session) == [
        ("assistant", "Looking it up."),
        ("tool", "read_file"),
        ("assistant", "It's 18°C and sunny."),
    ]
    first = session.messages[1]
    assert first["variant_idx"] == 0 and len(first["variants"]) == 2


async def test_switching_from_a_tool_using_answer_leaves_none_of_its_rows(tmp_path, monkeypatch):
    """🔴 Red before: the earlier version's text, then the newer answer's tool row and its last
    line, which a reload drew as the newer answer."""
    _state, session, app = await _two_versions(tmp_path, monkeypatch, fresh_uses_a_tool=True)

    await _switch(app, 0)

    assert _shape(session) == [("assistant", "Probably mild.")]
    assert session.messages[1]["variant_idx"] == 0

    await _switch(app, 1)

    assert _shape(session) == [
        ("assistant", "Looking it up."),
        ("tool", "read_file"),
        ("assistant", "It's 18°C and sunny."),
    ]


async def test_a_switched_version_is_what_a_reload_draws(tmp_path, monkeypatch):
    """🔴 Red before: the record held the mix, so a reload drew it."""
    state, session, app = await _two_versions(tmp_path, monkeypatch, fresh_uses_a_tool=True)
    await _switch(app, 0)

    async with TestClient(TestServer(app)) as client:
        detail = await (await client.get("/api/chat/sessions/s1?all=1")).json()

    assert [(m["role"], m.get("content", "")) for m in detail["messages"]][1:] == [
        ("assistant", "Probably mild.")
    ]


async def test_a_versions_rows_never_go_out_on_the_wire(tmp_path, monkeypatch):
    _state, _session, app = await _two_versions(tmp_path, monkeypatch)
    await _switch(app, 0)

    async with TestClient(TestServer(app)) as client:
        detail = await (await client.get("/api/chat/sessions/s1?all=1")).json()
    served = [m for m in detail["messages"] if m.get("variants")]
    assert served, "the answer is served with its versions"
    for v in served[0]["variants"]:
        assert set(v) <= {"content", "ts"}, v


async def test_a_version_kept_whole_survives_a_restart(tmp_path, monkeypatch):
    """🔴 A version's rows are saved with the chat (redacted as its rows are) and come back with
    it: the chat reopens on the version it showed, alone, and the other comes back whole."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    state, session, app = await _two_versions(tmp_path, monkeypatch, fresh_uses_a_tool=True)
    await _switch(app, 0)
    save_session_to_history(state, session, force=True)
    state._sessions.pop("s1")

    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/api/chat/sessions/s1?all=1")).status == 200
        assert _shape(state._sessions["s1"]) == [("assistant", "Probably mild.")]
        resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": 1})
        assert resp.status == 200, await resp.text()

    back = state._sessions["s1"]
    assert [(m["role"], m.get("content", "")) for m in back.messages[1:]] == [
        ("assistant", "Looking it up."),
        ("tool", "read_file"),
        ("assistant", "It's 18°C and sunny."),
    ]
