"""A widget's message sent while a turn runs keeps saying it came from a widget.

The page marks a message that came from a widget (``own_words.WIDGET_ACTION``), and the chat
route takes none of its links as the user's. Sent mid-turn it is steered into the running turn or
queued behind it, and either way it becomes a row later. That row keeps the marker, so Edit and
resend or Rewind on it later grants nothing either (``chat_regenerate``), and the queue tells the
page, so the composer's Edit gives a queued one back still marked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard import chat_intake, running_turn
from personalclaw.dashboard.chat_queue import shown
from personalclaw.own_words import WIDGET_ACTION, came_from_a_widget


def test_a_queued_widget_message_keeps_its_mark_on_the_queue_and_on_its_row(tmp_path) -> None:
    """A queued widget's message is marked on the queue, so the page's Edit gives it back
    marked, and on the row it runs as, so its later Edit and resend grant none of its links."""
    state = _make_state(tmp_path)
    session = state.get_or_create_session("queued")
    qid = session.queue_append("[UI] open", attended=True, from_a_widget=True)
    item = session.queued(qid)
    assert shown(item)[WIDGET_ACTION] is True
    assert came_from_a_widget(chat_intake.carried([item]))
    mine = session.queue_append("my own words", attended=True)
    assert WIDGET_ACTION not in shown(session.queued(mine))
    assert not came_from_a_widget(chat_intake.carried([session.queued(mine)]))
    # Several messages run as one row: a widget's among them marks it.
    assert came_from_a_widget(chat_intake.carried([session.queued(mine), item]))


def test_a_steered_widget_message_keeps_its_mark_on_its_row(tmp_path) -> None:
    """A steered widget's message is marked on the row the running turn writes for it."""
    state = _make_state(tmp_path)
    state.sessions = MagicMock()
    state.sessions.add_steer = MagicMock(return_value=True)
    state.broadcast_ws = MagicMock()
    session = state.get_or_create_session("steered")
    assert running_turn.steer(state, session, "[UI] open", from_a_widget=True)
    row = running_turn.take_steer(state, session, "[UI] open")
    assert came_from_a_widget(row.get("meta"))
    assert running_turn.steer(state, session, "my own words")
    row = running_turn.take_steer(state, session, "my own words")
    assert not came_from_a_widget(row.get("meta"))


def test_a_steer_the_turn_takes_says_live_whether_it_came_from_a_widget(tmp_path) -> None:
    """The ``chat_user_message`` a taken steer is broadcast as says it came from a widget,
    as its row does on a reload, so a quote of that bubble live is the widget's too. The owner's
    own steer says nothing of the kind."""
    state = _make_state(tmp_path)
    state.sessions = MagicMock()
    state.sessions.add_steer = MagicMock(return_value=True)
    state.broadcast_ws = MagicMock()
    session = state.get_or_create_session("steered-live")

    def said(text: str) -> dict:
        frames = [
            c.args[1]
            for c in state.broadcast_ws.call_args_list
            if c.args[0] == "chat_user_message" and c.args[1].get("content") == text
        ]
        assert len(frames) == 1, state.broadcast_ws.call_args_list
        return frames[0]

    assert running_turn.steer(state, session, "[UI] open", from_a_widget=True)
    running_turn.take_steer(state, session, "[UI] open")
    assert said("[UI] open").get(WIDGET_ACTION) is True
    assert running_turn.steer(state, session, "my own words")
    running_turn.take_steer(state, session, "my own words")
    assert WIDGET_ACTION not in said("my own words")


@pytest.mark.asyncio
async def test_a_widgets_steer_sent_over_http_keeps_its_mark_when_the_turn_ends_without_it(
    tmp_path,
) -> None:
    """Sent through the chat route as a steer, a widget's message is steered in marked, and when
    the turn ends without taking it, the steer requeued in its place is still marked, on the queue
    item and in the ``queue_push`` the page hears. The owner's own steer beside it is requeued
    unmarked."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard import chat

    state = _make_state(tmp_path)
    state.sessions = MagicMock()
    state.sessions.add_steer = MagicMock(return_value=True)
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    session = state.get_or_create_session("steering")
    session.append("user", "fix the parser", "msg msg-u")
    session.task = asyncio.get_running_loop().create_future()  # its turn is running
    session._takes_steers = True
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat", chat.api_chat)
    text = "[UI] open: {}"
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/chat?ws=1",
            json={
                "session": "steering",
                "message": text,
                "queue_mode": "steer",
                "meta": {WIDGET_ACTION: True},
            },
        )
        answer = await resp.json()
        assert resp.status == 200, answer
        assert answer["disposition"] == "steered", answer
        assert session._steers[-1]["meta"].get(WIDGET_ACTION) is True
        resp = await client.post(
            "/api/chat?ws=1",
            json={"session": "steering", "message": "check the tests too", "queue_mode": "steer"},
        )
        answer = await resp.json()
        assert answer["disposition"] == "steered", answer
    assert WIDGET_ACTION not in session._steers[-1].get("meta", {})
    state.sessions.set_steer_drains = MagicMock(return_value=[text, "check the tests too"])
    running_turn.end_steers(state, session, "steering", None)
    widgets, own = session._queue[-2], session._queue[-1]
    assert widgets.get(WIDGET_ACTION) is True, widgets
    assert WIDGET_ACTION not in own, own
    pushed = [c.args[1] for c in state.broadcast_ws.call_args_list if c.args[0] == "queue_push"]
    assert len(pushed) >= 2, pushed
    assert pushed[-2].get(WIDGET_ACTION) is True, pushed
    assert WIDGET_ACTION not in pushed[-1], pushed
    session.task.cancel()


@pytest.mark.asyncio
async def test_a_queued_message_the_drain_starts_says_live_whether_it_came_from_a_widget(
    tmp_path,
) -> None:
    """🔴 The ``chat_user_message`` a queued message is broadcast as, when the drain starts it,
    says it came from a widget, as its row does on a reload. One of the owner's own says nothing
    of the kind. Driven through the real chat handler and turn engine."""
    from test_her_paste_reaches_the_agent import FIRST, SECOND, _World

    async def drained(where: str, text: str, meta: dict) -> dict:
        """What the drain broadcast for *text*, queued behind a running turn."""
        world = _World(tmp_path / where, FIRST, SECOND)
        async with world.client() as client:
            key = (await world.send(client, "Help me tidy the parser."))["session"]
            await asyncio.wait_for(world.model.answering.wait(), timeout=10)
            receipt = await world.send(client, text, session=key, queue_mode="followup", meta=meta)
            assert receipt["disposition"] == "queued", receipt
            world.model.go_on.set()
            await world.settled(key)
        (told,) = [f for f in world.frames_of("chat_user_message") if f["content"] == text]
        return told

    widget = await drained("widget", "[UI] open", {WIDGET_ACTION: True})
    assert widget.get(WIDGET_ACTION) is True, widget
    assert WIDGET_ACTION not in await drained("own", "my own words", {})


def test_a_rewound_tail_row_keeps_a_widgets_mark_on_the_wire_and_on_disk() -> None:
    """A widget's message in a rewound tail keeps its marker in the copy the page is sent and in
    the copy the chat's file keeps, so the tail's row is a widget's after a reload too."""
    from personalclaw.dashboard.chat_persistence import _redact_rewound
    from personalclaw.dashboard.chat_utils import _prepare_messages

    tail = [
        {"role": "user", "content": "[UI] open", "meta": {WIDGET_ACTION: True}},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "[UI] Show the view", "meta": {"ui_label": "Show the view"}},
    ]
    msgs = [{"role": "user", "content": "again", "rewound": [{"messages": tail, "ts": "t"}]}]
    wire = _prepare_messages(msgs, running=False)[0]["rewound"][0]["messages"]
    disk = _redact_rewound(msgs[0]["rewound"])[0]["messages"]
    for copy in (wire, disk):
        assert (copy[0].get("meta") or {}).get(WIDGET_ACTION) is True, copy
        assert came_from_a_widget(copy[2].get("meta")), copy
        assert not came_from_a_widget(copy[1].get("meta")), copy
