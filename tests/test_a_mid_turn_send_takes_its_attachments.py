"""A message sent while a turn runs takes its attachments and mentions with it.

Measured before this was written: a message sent into a running turn, steered or queued, carried
its words and its pastes only (``chat_intake.take_in``). The files the request's meta named, and
the knowledge items and artifacts it mentioned, were dropped: the queue kept a channel's files and
nothing of the dashboard's, and the drained row carried files alone. So the turn that ran the
message never read them.

Now what a message carries besides its words (``chat_intake.what_it_carries``) is queued with it,
and the row its turn runs as carries all of it (``chat_intake.what_they_carry``), which the turn's
readers take it from. A steer joins a turn whose inputs were put together already, so a message
that carries any of it is queued instead, whatever it asked; one that carries none still steers.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat_send_world import World, new_id

from personalclaw.config import loader as config_loader
from personalclaw.dashboard import chat_intake

pytestmark = pytest.mark.asyncio

ASKED = "Summarise the incident."
TYPED = "also use this"


def _upload(name: str, data: bytes) -> str:
    uploads = config_loader.config_dir() / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / f"{'e' * 32}_{name}"
    path.write_bytes(data)
    return str(path)


def _reading(text: str):
    from personalclaw.knowledge.extract import Extracted

    extractor = MagicMock()
    extractor.get = AsyncMock(return_value=Extracted(text, True))
    return patch("personalclaw.dashboard.attachment_extract.get_extractor", return_value=extractor)


async def _running(world: World, client) -> str:
    """The chat with a turn running, held while it answers; its key."""
    world.model.hold()
    key = (await world.send(client, ASKED, client_message_id=new_id()))["session"]
    await asyncio.wait_for(world.model.answering.wait(), timeout=10)
    return key


def _row_of(session, words: str) -> dict:
    return next(
        m for m in session.messages if m.get("role") == "user" and m.get("content") == words
    )


async def test_a_queued_send_carries_its_files_knowledge_and_artifacts_into_its_turn(tmp_path):
    """🔴 Red before: the queued row carried none of them, and the turn read no file."""
    path = _upload("timeline.txt", b"09:12 alarm\n")
    world = World(tmp_path, "Looking.", "Read it.")
    with _reading("TIMELINE-TEXT"):
        async with world.client() as client:
            key = await _running(world, client)
            taken = await world.send(
                client,
                TYPED,
                session=key,
                queue_mode="followup",
                client_message_id=new_id(),
                meta={"files": [path], "knowledge": ["k1"], "artifacts": ["plan"]},
            )
            assert taken["disposition"] == "queued"
            world.model.go_on.set()
            session = await world.settled(key)

    meta = _row_of(session, TYPED)["meta"]
    assert meta["files"] == [path]
    assert meta["knowledge"] == ["k1"]
    assert meta["artifacts"] == ["plan"]
    assert "TIMELINE-TEXT" in world.model.read(), "the turn never read the file"


async def test_a_steer_that_carries_a_file_is_queued_and_its_turn_reads_it(tmp_path):
    """🔴 Red before: the steer went in with its words alone, and the file went nowhere."""
    path = _upload("timeline.txt", b"09:12 alarm\n")
    world = World(tmp_path, "Looking.", "Read it.")
    with _reading("TIMELINE-TEXT"):
        async with world.client() as client:
            key = await _running(world, client)
            taken = await world.send(
                client,
                TYPED,
                session=key,
                queue_mode="steer",
                client_message_id=new_id(),
                meta={"files": [path]},
            )
            assert taken["disposition"] == "queued"
            world.model.go_on.set()
            session = await world.settled(key)

    assert _row_of(session, TYPED)["meta"]["files"] == [path]
    assert "TIMELINE-TEXT" in world.model.read()


async def test_a_steer_that_carries_nothing_still_steers(tmp_path):
    world = World(tmp_path, "Looking.", "Read it.")
    async with world.client() as client:
        key = await _running(world, client)
        taken = await world.send(
            client, TYPED, session=key, queue_mode="steer", client_message_id=new_id()
        )
        world.model.go_on.set()
        await world.settled(key)

    assert taken["disposition"] == "steered"


async def test_a_send_that_replaces_the_running_turn_keeps_what_it_carries(tmp_path):
    """🔴 Red before: under ``cancel_and_replace`` the message that stops the turn was queued with
    its words alone, so the turn that ran it read none of its files."""
    path = _upload("timeline.txt", b"09:12 alarm\n")
    world = World(tmp_path, "Looking.", "Read it.")
    async with world.client() as client:
        key = await _running(world, client)
        session = world.state._sessions[key]
        taken = chat_intake.take_in(
            world.state,
            session,
            chat_intake.Intake(
                message=TYPED,
                message_id=new_id(),
                body_digest="d",
                queue_mode="followup",
                replaces=True,
                meta={"files": [path], "knowledge": ["k1"]},
            ),
        )
        queued = dict(session._queue[-1])
        world.model.go_on.set()
        await world.settled(key)

    assert taken.disposition == chat_intake.REPLACED
    assert queued["files"] == [path]
    assert queued["knowledge"] == ["k1"]


def test_what_queued_messages_carry_is_merged_in_order_each_once():
    items = [
        {"files": ["/a", "/b"], "knowledge": ["k1"]},
        {"files": ["/b", "/c"], "artifacts": ["plan"]},
        {"content": "no extras"},
    ]

    assert chat_intake.what_they_carry(items) == {
        "files": ["/a", "/b", "/c"],
        "knowledge": ["k1"],
        "artifacts": ["plan"],
    }
