"""P6a — the native always-on inbox source: post_to_inbox push, source/can_reply
attribution, native reply routing, and per-source /status health."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.inbox_providers.native_source as ns
from personalclaw.dashboard import handlers_inbox as H
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def state(tmp_path):
    store = InboxStore(path=tmp_path / "inbox.json")
    store.load()
    st = MagicMock()
    st._inbox_svc = None
    st._inbox_store = store
    st._inbox_state = InboxState(path=tmp_path / "inbox_state.json")
    st.events = []
    st.broadcast_ws = lambda ev, payload: st.events.append((ev, payload))
    ns.set_dashboard_state(st)
    return st


# ── model ──

def test_item_source_can_reply_round_trip():
    item = InboxItem(id="agent_1", channel="agent", channel_name="agent", thread_ts=None,
                     message="hi", sender_id="coder", sender_name="coder",
                     source="native", can_reply=True, reply_target="cron:x")
    rt = InboxItem.from_dict(item.to_dict())
    assert rt.source == "native" and rt.can_reply is True and rt.reply_target == "cron:x"


# ── native source push ──

def test_post_notification_is_fyi_no_reply(state):
    item = ns.post_to_inbox("done with X", kind="notification", sender_name="coder")
    assert item.source == "native"
    assert item.classification == "fyi" and item.can_reply is False
    assert state.events[-1][0] == "inbox_new_item"

def test_post_question_needs_reply_routes(state):
    item = ns.post_to_inbox("approve deploy?", kind="question", sender_name="coder", reply_target="chat:1")
    assert item.classification == "needs_reply" and item.can_reply is True
    assert item.reply_target == "chat:1"

def test_post_persists_to_shared_store(state):
    ns.post_to_inbox("a", kind="fyi")
    ns.post_to_inbox("b", kind="notification")
    reloaded = InboxStore(path=state._inbox_store._path)
    reloaded.load()
    assert len(reloaded.items) == 2

def test_post_without_state_is_noop():
    ns.set_dashboard_state(None)
    assert ns.post_to_inbox("x") is None


# ── /send native routing ──

def _send_req(state, body):
    app = web.Application()
    app["state"] = state
    req = make_mocked_request("POST", "/api/inbox/send", app=app)
    req.json = lambda: _coro(body)
    return req

async def _coro(v):
    return v


def test_send_routes_native_reply_to_live_session(state):
    item = ns.post_to_inbox("approve?", kind="question", sender_name="coder", reply_target="chat:1")
    session = MagicMock()
    state.get_session = lambda key: session if key == "chat:1" else None
    # patch the chat runner import target
    import personalclaw.dashboard.chat_runner as cr
    cr._run_chat = MagicMock()
    resp = _run(H.api_inbox_send(_send_req(state, {"id": item.id, "text": "yes, go"})))
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["delivered_to_session"] is True
    session.enqueue_or_run_prompt.assert_called_once()
    # item marked handled
    assert state._inbox_store.items[item.id].status == ItemStatus.HANDLED.value

def test_send_rejects_non_replyable(state):
    item = ns.post_to_inbox("fyi only", kind="notification")
    resp = _run(H.api_inbox_send(_send_req(state, {"id": item.id, "text": "x"})))
    assert resp.status == 400

def test_send_captures_when_session_gone(state):
    item = ns.post_to_inbox("approve?", kind="question", reply_target="gone:1")
    state.get_session = lambda key: None
    resp = _run(H.api_inbox_send(_send_req(state, {"id": item.id, "text": "do it"})))
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["delivered_to_session"] is False
    assert state._inbox_store.items[item.id].status == ItemStatus.HANDLED.value
    assert state._inbox_store.items[item.id].draft == "do it"


# ── /status per-source health ──

def test_status_reports_native_source_active(state, monkeypatch):
    app = web.Application()
    app["state"] = state
    req = make_mocked_request("GET", "/api/inbox/status", app=app)
    resp = _run(H.api_inbox_status(req))
    body = json.loads(resp.body)
    assert body["native_source_active"] is True
    native = next(s for s in body["sources"] if s["name"] == "native")
    assert native["active"] is True and native["kind"] == "push"
