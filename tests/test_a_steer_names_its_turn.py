"""A steer names the turn it is for, and lands nowhere when that turn is not the one running.

The composer sends a message typed while an answer streams as a steer of that answer. By the time
it arrives the turn may have ended, or another may run, and a steer for the turn the owner was
reading must not land in a turn they never saw. So the page sends the id of the message that
started the turn it steers (``expected_message_id``), and the chat refuses, keeping nothing, a
steer whose turn is not running (``no_running_turn``), one whose turn is not the running one
(``turn_not_current``, naming both), and one into a turn that takes none (``turn_not_steerable``):
the page offers to send or queue it instead. A steer that names no turn (a script's, a channel's)
is taken in as before.

Driven through the real chat handler, the real turn engine, the real session manager and a real
native runtime answered by a scripted model (``chat_send_world``).
"""

from __future__ import annotations

import asyncio

import pytest
from chat_send_world import World, new_id, rows_of, turn_starts

from personalclaw.agents.native.runtime import NativeAgentRuntime

ASKED = "Write the loader for the garden config."
STEER = "Use tabs, not spaces."
NEXT = "Now add a test for it."


async def _steer(world: World, client, key: str, expected: str) -> tuple[int, dict]:
    return await world.post(
        client,
        STEER,
        session=key,
        queue_mode="steer",
        client_message_id=new_id(),
        expected_message_id=expected,
    )


def _nothing_kept(world: World, key: str, rows: list[dict]) -> None:
    session = world.state._sessions[key]
    assert session.messages == rows, "the refused steer wrote nothing"
    assert session._queue == [] and session._steers == [], "and nothing waits to run"


@pytest.mark.asyncio
async def test_a_steer_for_an_ended_turn_is_refused_and_lands_nowhere(tmp_path):
    world = World(tmp_path, "The loader.", "A test, then.")
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await world.settled(key)
        rows = [dict(r) for r in world.state._sessions[key].messages]
        status, refused = await _steer(world, client, key, asked["message_id"])
        _nothing_kept(world, key, rows)
        # The next turn runs without it.
        await world.send(client, NEXT, session=key, client_message_id=new_id())
        session = await world.settled(key)

    assert status == 409, refused
    assert refused["error"]["code"] == "no_running_turn"
    assert refused["error"]["message"] == (
        "No turn is running in this chat, so there was nothing to steer; send it as a message."
    )
    assert rows_of(session.messages, STEER) == []
    assert STEER not in world.model.read()


@pytest.mark.asyncio
async def test_a_steer_for_another_turn_answers_both_ids(tmp_path):
    world = World(tmp_path, "The loader.", "A test, then.")
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await world.settled(key)
        # Another turn runs now, held while it answers.
        world.model.hold()
        running = await world.send(client, NEXT, session=key, client_message_id=new_id())
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        rows = [dict(r) for r in world.state._sessions[key].messages]
        status, refused = await _steer(world, client, key, asked["message_id"])
        _nothing_kept(world, key, rows)
        world.model.go_on.set()
        session = await world.settled(key)

    assert status == 409, refused
    assert refused["error"]["code"] == "turn_not_current"
    assert refused["error"]["message"] == "That turn is no longer running, so nothing was changed."
    assert refused["error"]["expected"] == asked["message_id"]
    assert refused["error"]["actual"] == running["message_id"]
    assert rows_of(session.messages, STEER) == []
    assert STEER not in world.model.read()


@pytest.mark.asyncio
async def test_a_steer_with_nothing_running_is_refused(tmp_path):
    world = World(tmp_path, "The loader.")
    async with world.client() as client:
        key = (await world.send(client, ASKED, client_message_id=new_id()))["session"]
        await world.settled(key)
        rows = [dict(r) for r in world.state._sessions[key].messages]
        status, refused = await _steer(world, client, key, new_id())
        _nothing_kept(world, key, rows)

    assert status == 409 and refused["error"]["code"] == "no_running_turn", refused
    assert len(world.model.requests) == 1


class _SlowCompact(NativeAgentRuntime):
    """A native runtime whose ``/compact`` waits for ``go_on`` before it compacts, so a steer can
    arrive while it runs."""

    compacting: asyncio.Event
    go_on: asyncio.Event

    async def stream_command(self, command):
        if command.split()[:1] == ["/compact"]:
            type(self).compacting.set()
            await type(self).go_on.wait()
        async for event in super().stream_command(command):
            yield event


@pytest.mark.asyncio
async def test_a_turn_that_takes_no_steer_refuses_one(tmp_path):
    _SlowCompact.compacting, _SlowCompact.go_on = asyncio.Event(), asyncio.Event()
    world = World(tmp_path, "The loader.", runtime=_SlowCompact)
    async with world.client() as client:
        key = (await world.send(client, ASKED, client_message_id=new_id()))["session"]
        await world.settled(key)
        compact = await world.send(client, "/compact", session=key, client_message_id=new_id())
        await asyncio.wait_for(_SlowCompact.compacting.wait(), timeout=10)
        rows = [dict(r) for r in world.state._sessions[key].messages]
        status, refused = await _steer(world, client, key, compact["message_id"])
        _nothing_kept(world, key, rows)
        _SlowCompact.go_on.set()
        session = await world.settled(key)

    assert status == 409, refused
    assert refused["error"]["code"] == "turn_not_steerable"
    assert (
        refused["error"]["message"]
        == "This turn cannot take a message while it runs; queue it instead."
    )
    assert refused["error"]["kind"] == "compact"
    assert rows_of(session.messages, STEER) == []


@pytest.mark.asyncio
async def test_a_steer_without_an_expected_turn_keeps_todays_rule(tmp_path):
    world = World(tmp_path, "I'll indent it.", "Switching to tabs.", hold=True)
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        steered = await world.send(
            client, STEER, session=key, queue_mode="steer", client_message_id=new_id()
        )
        world.model.go_on.set()
        session = await world.settled(key)

        # With nothing running, a steer that names no turn is a message: it runs as one.
        alone = await world.send(
            client, NEXT, session=key, queue_mode="steer", client_message_id=new_id()
        )
        session = await world.settled(key)

    assert steered["disposition"] == "steered"
    assert steered["turn_message_id"] == asked["message_id"]
    assert len(rows_of(session.messages, STEER)) == 1
    assert alone["disposition"] == "started"
    assert len(turn_starts(session.messages)) == 2
