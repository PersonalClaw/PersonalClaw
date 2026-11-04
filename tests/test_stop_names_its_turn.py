"""Stop stops the turn it names, and only that turn; a soft Stop says what it took back unsent.

``POST /api/chat/sessions/{session}/stop`` takes ``{message_id}``, the turn the page means (its
``run_state.turn.message_id``). A Stop naming a turn that is not running (it ended, or another
runs) answers ``409 turn_not_current {expected, actual}`` and changes nothing: the running turn
keeps going, the queue keeps its messages, no stop card is written and the chat keeps its Trust. A
steer's or a merged message's id names the turn it ran in, as the turn document reads it. Without
an id, Stop stops whatever runs, as before. A soft Stop's answer names the queued messages it
removed unsent (``withdrawn``), and a repeat of one of them runs nothing.

Driven through the real handlers, the real turn engine, the real session manager and a real native
runtime answered by a scripted model (``chat_send_world``).
"""

from __future__ import annotations

import asyncio

import pytest
from chat_send_world import World, configure, new_id

ASKED = "Write the loader for the garden config."
NEXT = "Now write the saver."


async def _stop(client, key: str, **body) -> tuple[int, dict]:
    resp = await client.post(f"/api/chat/sessions/{key}/stop", json=body)
    return resp.status, await resp.json()


def _stop_cards(session) -> list[dict]:
    """The stop cards in the chat (``chat_handlers._open_stop_event``): a ``system`` row whose class
    is the card's JSON, of kind ``stop_event``."""
    return [
        m
        for m in session.messages
        if m.get("role") == "system" and '"stop_event"' in str(m.get("cls") or "")
    ]


@pytest.mark.asyncio
async def test_a_stop_for_an_ended_turn_stops_nothing(tmp_path):
    world = World(tmp_path, "The loader.", "The saver, first part.", "The rest.")
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await world.settled(key)
        world.model.hold()
        second = await world.send(client, NEXT, session=key, client_message_id=new_id())
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        session = world.state._sessions[key]
        trust = session._trust
        rows = len(session.messages)

        status, refused = await _stop(client, key, message_id=first["message_id"])
        still_running = session.running
        cards = _stop_cards(session)
        world.model.go_on.set()
        session = await world.settled(key)

    assert status == 409, refused
    assert refused["error"]["code"] == "turn_not_current"
    assert refused["error"]["expected"] == first["message_id"]
    assert refused["error"]["actual"] == second["message_id"]
    assert refused["error"]["state"] == "settled" and refused["error"]["outcome"] == "complete"
    assert refused["error"]["turn_message_id"] == first["message_id"]
    assert still_running, "the turn that runs keeps running"
    assert cards == [] and session._trust == trust
    assert len(session.messages) >= rows and session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "The saver, first part."


@pytest.mark.asyncio
async def test_a_stop_for_another_turn_keeps_the_queue(tmp_path):
    world = World(tmp_path, "The loader.", "The saver.", "The test.")
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await world.settled(key)
        world.model.hold()
        await world.send(client, NEXT, session=key, client_message_id=new_id())
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        waits = await world.send(
            client, "Then the test.", session=key, queue_mode="followup", client_message_id=new_id()
        )
        session = world.state._sessions[key]

        status, _ = await _stop(client, key, message_id=first["message_id"])
        queue = [item["message_id"] for item in session._queue]
        world.model.go_on.set()
        await world.settled(key)

    assert status == 409
    assert queue == [waits["message_id"]], "the queue keeps its message"
    assert len(world.model.requests) == 3, "the queued message still ran"


@pytest.mark.asyncio
async def test_a_stop_with_nothing_running_answers_who_runs(tmp_path):
    world = World(tmp_path, "The loader.")
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await world.settled(key)
        status, refused = await _stop(client, key, message_id=first["message_id"])

    assert status == 409 and refused["error"]["code"] == "turn_not_current", refused
    assert refused["error"]["actual"] == "" and refused["error"]["state"] == "settled"


@pytest.mark.asyncio
async def test_a_stop_without_an_id_stops_what_runs(tmp_path):
    world = World(tmp_path, "Half an answer", hold=True)
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        status, stopped = await _stop(client, key)
        await world.settled(key)

    assert status == 200 and stopped["ok"] is True and stopped["stopped"] is True, stopped
    assert stopped["withdrawn"] == []


@pytest.mark.asyncio
async def test_a_stop_naming_a_steer_stops_the_turn_it_joined(tmp_path):
    world = World(tmp_path, "Half an answer", hold=True)
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        steer = await world.send(
            client,
            "Also check the empty file.",
            session=key,
            queue_mode="steer",
            expected_message_id=first["message_id"],
            client_message_id=new_id(),
        )
        status, stopped = await _stop(client, key, message_id=steer["message_id"])
        await world.settled(key)

    assert steer["disposition"] == "steered", steer
    assert status == 200 and stopped["stopped"] is True, stopped
    assert stopped["turn_message_id"] == first["message_id"]
    assert stopped["message_ids"] == [first["message_id"], steer["message_id"]]


@pytest.mark.asyncio
async def test_a_soft_stop_names_what_it_withdrew(tmp_path):
    world = World(tmp_path, "Half an answer", hold=True)
    later = "Then the test."
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        one = await world.send(
            client, NEXT, session=key, queue_mode="followup", client_message_id=new_id()
        )
        two = await world.send(
            client, later, session=key, queue_mode="followup", client_message_id=new_id()
        )
        status, stopped = await _stop(client, key, message_id=first["message_id"])
        session = await world.settled(key)
        again = await world.send(
            client, later, session=key, queue_mode="followup", client_message_id=two["message_id"]
        )
        session = await world.settled(key)

    assert status == 200 and stopped["stopped"] is True, stopped
    assert stopped["withdrawn"] == [one["message_id"], two["message_id"]]
    assert session._queue == []
    assert again["duplicate"] is True and again["message_id"] == two["message_id"], again
    assert len(world.model.requests) == 1, "nothing it withdrew ran, then or on its repeat"


@pytest.mark.asyncio
async def test_a_stop_for_a_queued_message_changes_nothing(tmp_path):
    """A message still in the queue is in no turn yet: naming it stops nothing and withdraws
    nothing."""
    world = World(tmp_path, "The loader.", "The saver.", hold=True)
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        waits = await world.send(
            client, NEXT, session=key, queue_mode="followup", client_message_id=new_id()
        )
        session = world.state._sessions[key]
        trust = session._trust

        status, refused = await _stop(client, key, message_id=waits["message_id"])
        queue = [item["message_id"] for item in session._queue]
        still_running = session.running
        cards = _stop_cards(session)
        world.model.go_on.set()
        session = await world.settled(key)

    assert status == 409 and refused["error"]["state"] == "queued", refused
    assert refused["error"]["actual"] == first["message_id"]
    assert queue == [waits["message_id"]] and still_running and cards == []
    assert session._trust == trust
    assert len(world.model.requests) == 2, "the queued message still ran, after the turn"


@pytest.mark.asyncio
async def test_a_stop_by_a_merged_id_stops_its_whole_turn_and_says_so(tmp_path):
    configure({"dashboard": {"merge_queued_messages": True}})
    world = World(tmp_path, "The loader.", "Half of both", hold=True)
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        one = await world.send(
            client, NEXT, session=key, queue_mode="followup", client_message_id=new_id()
        )
        two = await world.send(
            client, "And the test.", session=key, queue_mode="followup", client_message_id=new_id()
        )
        world.model.go_on.set()
        world.model.hold()  # the merged turn's answer waits after its first words
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        status, stopped = await _stop(client, key, message_id=two["message_id"])
        await world.settled(key)

    assert status == 200 and stopped["stopped"] is True, stopped
    merged = stopped["turn_message_id"]
    assert merged not in (one["message_id"], two["message_id"])
    assert stopped["message_ids"] == [merged, one["message_id"], two["message_id"]]


@pytest.mark.asyncio
async def test_a_second_stop_for_the_same_turn_says_it_is_stopping_or_stopped(tmp_path):
    world = World(tmp_path, "Half an answer", hold=True)
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=new_id())
        key = first["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        status, stopped = await _stop(client, key, message_id=first["message_id"])
        again_status, again = await _stop(client, key, message_id=first["message_id"])
        session = await world.settled(key)
        last_status, last = await _stop(client, key, message_id=first["message_id"])

    assert status == 200 and stopped["stopped"] is True, stopped
    if again_status == 200:
        assert again["stopped"] is False and again["state"] == "stopping", again
    else:
        assert again_status == 409 and again["error"]["outcome"] == "stopped", again
    assert last_status == 409 and last["error"]["state"] == "settled", last
    assert last["error"]["outcome"] == "stopped"
    assert len(_stop_cards(session)) == 1, "one stop, one card"


@pytest.mark.asyncio
async def test_an_id_from_another_chat_is_not_found_and_says_nothing_of_it(tmp_path):
    world = World(tmp_path, "The loader.", "Half an answer")
    async with world.client() as client:
        theirs = await world.send(client, ASKED, client_message_id=new_id())
        await world.settled(theirs["session"])
        world.model.hold()
        mine = await world.send(client, NEXT, client_message_id=new_id())
        key = mine["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        status, refused = await _stop(client, key, message_id=theirs["message_id"])
        read = await client.get(f"/api/chat/sessions/{key}/turns/{theirs['message_id']}")
        read_status, unread = read.status, await read.json()
        still_running = world.state._sessions[key].running
        world.model.go_on.set()
        await world.settled(key)

    assert key != theirs["session"]
    assert status == 404 and refused["error"]["code"] == "turn_not_found", refused
    assert read_status == 404 and unread["error"]["code"] == "turn_not_found", unread
    assert theirs["session"] not in str(refused) + str(unread)
    assert still_running, "nothing in this chat was stopped"


@pytest.mark.asyncio
async def test_a_stop_named_once_the_first_was_acknowledged_asks_for_no_second(
    tmp_path, monkeypatch
):
    """A runtime acknowledges a soft stop before its turn has ended, so the chat's stop state is
    back to idle while the turn winds down. A Stop naming that turn then says it is stopping and
    asks the runtime for nothing more: no second stop, no second card."""
    from unittest.mock import AsyncMock, MagicMock

    from aiohttp.test_utils import TestClient, TestServer
    from chat_test_helpers import _api_app

    from personalclaw.dashboard.chat import api_chat_session_stop
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.stop_turn = AsyncMock(return_value="soft")
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    session = state.get_or_create_session("garden")
    started = new_id()
    session.append("user", ASKED, "msg msg-u", meta={"message_id": started})
    winding_down = asyncio.Event()
    session.task = asyncio.create_task(winding_down.wait())  # its turn, still running
    session._stop_state = "soft_pending"  # the first Stop
    session._stop_state = "idle"  # acknowledged, while the turn winds down
    app = _api_app(state)
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    try:
        async with TestClient(TestServer(app)) as client:
            status, again = await _stop(client, "garden", message_id=started)
    finally:
        winding_down.set()
        await session.task

    assert status == 200 and again["stopped"] is False and again["state"] == "stopping", again
    sessions.stop_turn.assert_not_awaited()
    assert _stop_cards(session) == []
