"""P6a — the native always-on inbox source: post_to_inbox push, source/can_reply
attribution, native reply routing, and per-source /status health."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.inbox_providers.native_source as ns
from personalclaw.dashboard import handlers_inbox as H
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus
from personalclaw.request_validation import RequestValidationError


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
    item = InboxItem(
        id="agent_1",
        channel="agent",
        channel_name="agent",
        thread_ts=None,
        message="hi",
        sender_id="coder",
        sender_name="coder",
        source="native",
        can_reply=True,
        reply_target="cron:x",
    )
    rt = InboxItem.from_dict(item.to_dict())
    assert rt.source == "native" and rt.can_reply is True and rt.reply_target == "cron:x"


# ── native source push ──


def test_post_notification_is_fyi_no_reply(state):
    item = ns.post_to_inbox("done with X", kind="notification", sender_name="coder")
    assert item.source == "native"
    assert item.classification == "fyi" and item.can_reply is False
    assert state.events[-1][0] == "inbox_new_item"


def test_post_question_needs_reply_routes(state):
    item = ns.post_to_inbox(
        "approve deploy?", kind="question", sender_name="coder", reply_target="chat:1"
    )
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


async def _coro_raise(exc: BaseException):
    """An `await request.json()` that FAILS, which is how aiohttp reports an absent body.

    Paired with a `read()` returning no bytes it is the "sent nothing" case `json_object_body`
    answers `{}` for — as distinct from bytes that failed to parse, which it refuses.
    """
    raise exc


def test_send_routes_native_reply_to_live_session(state, monkeypatch):
    item = ns.post_to_inbox("approve?", kind="question", sender_name="coder", reply_target="chat:1")
    session = MagicMock()
    state.get_session = lambda key: session if key == "chat:1" else None
    # Stub the chat runner so no real turn is dispatched. MUST be monkeypatch-scoped: a bare
    # `chat_runner.run_chat = MagicMock()` is never undone, so it outlived this test and every
    # later test on the same xdist worker that reads that attribute — including run_chat's own
    # queue-processing recursion — then awaited a MagicMock and died with "object MagicMock
    # can't be used in 'await' expression". CI-only, because worksteal decides co-location.
    monkeypatch.setattr("personalclaw.dashboard.chat_runner.run_chat", MagicMock())
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


# ── malformed bodies are client errors, not crashes (#339) ──


@pytest.mark.parametrize("body", [None, [], 5, "text"])
def test_send_rejects_a_non_object_body(state, body):
    """A body that isn't an object parses fine, then used to 500 on body.get()."""
    resp = _run(H.api_inbox_send(_send_req(state, body)))
    assert resp.status == 400
    assert json.loads(resp.body)["error"] == "body must be an object"


@pytest.mark.parametrize(
    "body", [{"id": 123, "text": "x"}, {"id": "a", "text": ["x"]}, {"id": "a", "draft": {}}]
)
def test_send_rejects_a_non_string_id_or_text(state, body):
    """`(body.get("id") or "").strip()` raised AttributeError on a number/list."""
    resp = _run(H.api_inbox_send(_send_req(state, body)))
    assert resp.status == 400
    assert "must be a string" in json.loads(resp.body)["error"]


def _favorite_req(state, item_id, body):
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(
        "POST", f"/api/inbox/{item_id}/favorite", app=app, match_info={"id": item_id}
    )
    req.json = lambda: _coro(body)
    return req


@pytest.mark.parametrize("body", [None, [], 5, "text"])
def test_favorite_refuses_a_non_object_body(state, body):
    """A body that is present and NOT an object is the shared 400, not a defaulted write.

    This route used to read the junk as "favorite it" — the sensible-default argument taken
    one step too far, because the default belongs to an ABSENT body (asserted below), not to
    a body whose serialiser produced ``5``. A caller in that state got a 200 and a write it
    never asked for, which is #2923's shape at a second door. It now travels through the one
    reader, so the refusal is `invalid_body` with a sentence naming what arrived.

    The property the old tolerance actually protected — never a 500 — is asserted here too,
    and the item must be left ALONE: a refused request is not a partial write.
    """
    item = ns.post_to_inbox("look at this", kind="fyi")
    with pytest.raises(RequestValidationError) as caught:
        _run(H.api_inbox_favorite(_favorite_req(state, item.id, body)))
    assert caught.value.code == "invalid_body"
    assert caught.value.status == 400, "a malformed body is the caller's fault, never a 500"
    assert caught.value.response.status == 400
    assert state._inbox_store.items[item.id].favorited is False


def test_favorite_still_defaults_an_absent_body_to_favoriting(state):
    """The vacuity floor for the refusal above: the DEFAULT the route exists for survives.

    `POST /api/inbox/{id}/favorite` with no body at all is still "favorite it". Without this,
    the test above would pass just as well against a handler that refused every body, and the
    unification would have quietly taken a working route away.
    """
    item = ns.post_to_inbox("look at this", kind="fyi")
    req = _favorite_req(state, item.id, None)
    req.json = lambda: _coro_raise(ValueError("no body"))
    req.read = lambda: _coro(b"")
    resp = _run(H.api_inbox_favorite(req))
    assert resp.status == 200
    assert json.loads(resp.body)["favorited"] is True
    assert state._inbox_store.items[item.id].favorited is True


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
