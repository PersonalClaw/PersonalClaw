"""How far you have seen each chat is the gateway's record, so every window and the phone agree.

A chat is unread while its last turn ended after you last had it open in front of you. That
instant is kept by the gateway (``chat_seen``, ``chat_seen.json``), advanced by
``POST /api/chat/sessions/{session}/seen`` and, for every chat at once (⇧Esc),
``POST /api/chat/seen``, each said in a ``chat_seen`` frame. Every ``GET /api/chat/sessions`` row
carries ``attention: {unread, needs_you, your_turn}``, derived on the read:

* advancing is monotonic, and the all-chats advance moves every chat;
* a reply settled after ``seen_through`` makes its chat unread, and a turn you stopped doesn't;
* the record begins seen through the moment it is first read, so replies from before it existed
  are not all unread at once;
* advancing a chat with something waiting on you in it leaves ``needs_you`` true, and a helper's
  waiting approval makes the chat that started it ``needs_you``;
* only you advance it: an app's token and an agent's credential are refused on both routes;
* and it is yours alone: an app reading the chats it started gets no ``unread`` (and hears no
  ``chat_seen`` frame, ``test_apps_see_only_their_own_conversations``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_apps_cannot_post_into_your_chats import _gateway
from test_apps_cannot_run_code_or_bypass_approvals import _home, _install
from test_chat_turn_outcome import _session, _streaming, _turn

from personalclaw import chat_seen
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

APP = "probe-seen"


def _routes() -> list[tuple[str, str, Any]]:
    from personalclaw.dashboard.chat_handlers import api_chat_sessions
    from personalclaw.dashboard.chat_seen_routes import api_chat_seen_all, api_chat_session_seen

    return [
        ("GET", "/api/chat/sessions", api_chat_sessions),
        ("POST", "/api/chat/seen", api_chat_seen_all),
        ("POST", "/api/chat/sessions/{session}/seen", api_chat_session_seen),
    ]


async def _call(state, method: str, path: str, caller: str = "", **kw) -> tuple[int, Any]:
    async with TestClient(TestServer(_gateway(state, caller, _routes()))) as http:
        resp = await http.request(method, path, **kw)
        body = await resp.json() if resp.content_type == "application/json" else await resp.text()
        return resp.status, body


async def _rows(state) -> dict[str, dict]:
    status, body = await _call(state, "GET", "/api/chat/sessions")
    assert status == 200, body
    return {r["key"]: r for r in body}


def _reply(text: str = "All done.", reason: str = "end_turn"):
    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason=reason)

    return stream


def _frames(state, kind: str) -> list[dict]:
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args and c.args[0] == kind]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(chat_seen, "path", lambda: tmp_path / chat_seen.FILE)


# ── the record ───────────────────────────────────────────────────────────────────────────────


def test_advancing_is_monotonic_and_never_past_now(monkeypatch):
    monkeypatch.setattr(chat_seen, "_now", lambda: 1_000.0)
    chat_seen.read()  # the record begins, seen through 1000
    monkeypatch.setattr(chat_seen, "_now", lambda: 2_000.0)
    assert chat_seen.advance("c1", 1_500.0) == 1_500.0
    assert chat_seen.advance("c1", 1_200.0) is None, "an older through changes nothing"
    assert chat_seen.read().through("c1") == 1_500.0
    assert chat_seen.advance("c1", 9_999.0) == 2_000.0, "never seen past now"


def test_every_chat_advances_at_once(monkeypatch):
    monkeypatch.setattr(chat_seen, "_now", lambda: 1_000.0)
    chat_seen.read()
    monkeypatch.setattr(chat_seen, "_now", lambda: 5_000.0)
    chat_seen.advance("ahead", 4_500.0)
    chat_seen.advance("behind", 2_000.0)
    assert chat_seen.advance_every(3_000.0) == 3_000.0
    seen = chat_seen.read()
    assert seen.through("ahead") == 4_500.0, "a chat seen further keeps its own"
    assert seen.through("behind") == 3_000.0
    assert seen.through("never-opened") == 3_000.0
    assert chat_seen.advance_every(2_500.0) is None, "monotonic for every chat too"


def test_the_record_begins_seen_through_its_first_read(monkeypatch):
    monkeypatch.setattr(chat_seen, "_now", lambda: 7_000.0)
    assert chat_seen.read().through("old-chat") == 7_000.0
    monkeypatch.setattr(chat_seen, "_now", lambda: 9_000.0)
    assert chat_seen.read().through("old-chat") == 7_000.0, "begun once, then kept"


# ── what a row says ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reply_settled_after_you_saw_it_is_unread_until_you_see_it(tmp_path):
    state = _streaming(tmp_path, _reply())
    chat_seen.read()  # the record began before the reply
    await asyncio.sleep(0.01)
    await _turn(state, _session(state), "hello")

    row = (await _rows(state))["s1"]
    assert row["attention"] == {"unread": True, "needs_you": False, "your_turn": True}

    status, body = await _call(state, "POST", "/api/chat/sessions/s1/seen", json={})
    assert status == 200 and body["moved"] == ["s1"], body
    assert _frames(state, "chat_seen")[-1]["session"] == "s1", "other windows hear it at once"
    assert (await _rows(state))["s1"]["attention"]["unread"] is False


@pytest.mark.asyncio
async def test_a_turn_you_stopped_never_makes_its_chat_unread(tmp_path):
    state = _streaming(tmp_path, _reply("Half an ans", "stopped_by_user"))
    chat_seen.read()
    await asyncio.sleep(0.01)
    await _turn(state, _session(state), "hello")
    row = (await _rows(state))["s1"]
    assert row["run_state"]["last_outcome"] == "stopped"
    assert row["attention"]["unread"] is False


@pytest.mark.asyncio
async def test_seeing_every_chat_clears_every_mark_and_says_so_once(tmp_path):
    state = _streaming(tmp_path, _reply())
    chat_seen.read()
    await asyncio.sleep(0.01)
    for key in ("s1", "s2"):
        await _turn(state, _session(state, key), "hello")
    assert all(r["attention"]["unread"] for r in (await _rows(state)).values())

    status, body = await _call(state, "POST", "/api/chat/seen", json={})
    assert status == 200 and body["moved"] is True, body
    assert [f["session"] for f in _frames(state, "chat_seen")] == ["*"]
    assert not any(r["attention"]["unread"] for r in (await _rows(state)).values())


@pytest.mark.asyncio
async def test_seeing_a_chat_leaves_what_waits_on_you_in_it(tmp_path):
    state = _streaming(tmp_path, _reply())
    await _turn(state, _session(state), "hello")
    ask = asyncio.create_task(state.request_approval("a1", "chat", "shell", session="dashboard:s1"))
    helper = asyncio.create_task(
        state.request_approval("spawn:h1", "subagent", "spawn_agent", session="dashboard:s2")
    )
    _session(state, "s2")
    try:
        for _ in range(200):
            if {"a1", "spawn:h1"} <= set(state._pending_approvals):
                break
            await asyncio.sleep(0.005)
        await _call(state, "POST", "/api/chat/sessions/s1/seen", json={})
        rows = await _rows(state)
        assert rows["s1"]["attention"]["needs_you"] is True, "seeing it answers nothing"
        assert rows["s1"]["attention"]["your_turn"] is False
        assert rows["s2"]["attention"]["needs_you"] is True, "a helper's ask is its chat's"
    finally:
        for t in (ask, helper):
            t.cancel()
        await asyncio.gather(ask, helper, return_exceptions=True)


# ── only you ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_you_mark_a_chat_seen(tmp_path):
    agent = {"X-Internal-Secret": "the gateway's", "X-Session-Key": "dashboard:s1"}
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/chat"]})
        state = _streaming(tmp_path, _reply())
        chat_seen.read()
        await asyncio.sleep(0.01)
        await _turn(state, _session(state), "hello")
        for path in ("/api/chat/sessions/s1/seen", "/api/chat/seen"):
            status, body = await _call(state, "POST", path, APP, json={})
            assert status == 403, (path, status, body)
            status, body = await _call(state, "POST", path, "", json={}, headers=agent)
            assert status == 403, (path, status, body)
            assert "seen_owner_only" in str(body)
        assert (await _rows(state))["s1"]["attention"]["unread"] is True, "nothing was marked"
        assert _frames(state, "chat_seen") == []


@pytest.mark.asyncio
async def test_an_app_reading_its_own_chats_is_told_nothing_of_when_you_read_them(tmp_path):
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/chat"]})
        state = _streaming(tmp_path, _reply())
        chat_seen.read()
        await asyncio.sleep(0.01)
        ours = state.get_or_create_session("ours", created_by_app=APP)
        ours._trust = True
        ours._titled = True
        await _turn(state, ours, "hello")

        status, body = await _call(state, "GET", "/api/chat/sessions", APP)
        assert status == 200, body
        (row,) = body
        assert row["key"] == "ours"
        assert set(row["attention"]) == {"needs_you", "your_turn"}, "the app read your receipt"
        # The control: you are told it is unread.
        assert (await _rows(state))["ours"]["attention"]["unread"] is True


@pytest.mark.asyncio
async def test_a_chat_that_does_not_exist_is_not_marked(tmp_path):
    state = _streaming(tmp_path, _reply())
    status, body = await _call(state, "POST", "/api/chat/sessions/nobody/seen", json={})
    assert status == 404, body
    status, body = await _call(
        state, "POST", "/api/chat/sessions/s1/seen", json={"through": "yesterday"}
    )
    assert status in (400, 404), body
