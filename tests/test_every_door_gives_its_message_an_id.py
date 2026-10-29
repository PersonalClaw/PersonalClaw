"""Every door that puts a message into a chat gives it an id and a receipt, as the chat's own does.

The chat's send is one door (``tests/test_chat_receipts.py``). The others take a message in the same
way (``chat_intake.take_in``, held to it by the rail
``tests/rails/test_every_door_takes_messages_in_one_way_tree.py``): edit-and-resend, which names
its message as a send does, so a resend sent twice runs once; a prompt run on the chat (an Inbox
reply, a heartbeat's result) and the rest, under an id the gateway mints. Each row a turn starts
with keeps its id and its receipt, and so does a message that waits in the queue until it runs, a
steer its turn never took included.

Driven through the real handlers, the real turn engine, the real session manager and a real native
runtime answered by a scripted model (``chat_send_world``).
"""

from __future__ import annotations

import asyncio

import pytest
from chat_send_world import World, new_id, rows_of, turn_starts

from personalclaw import ids
from personalclaw.agents.native.runtime import _MAX_STEERS_PER_TURN
from personalclaw.dashboard.chat_runner import run_chat

ASKED = "Write the loader for the garden config."
EDITED = "Write the loader for the garden config, in YAML."
REPORT = "The nightly check found two failures."


async def _resend(client, key: str, **body) -> tuple[int, dict]:
    resp = await client.post(f"/api/chat/sessions/{key}/edit-resend", json=body)
    return resp.status, await resp.json()


# ── edit-and-resend ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_resend_sent_twice_runs_once(tmp_path):
    world = World(tmp_path)
    sent = new_id()
    # A page from before ids also sent its own clock for the row: no longer read.
    body = {"content": EDITED, "client_message_id": sent, "client_ts": "2026-01-01T00:00:00+00:00"}
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        status, first = await _resend(client, key, **body)
        assert status == 200, first
        await world.settled(key)
        status, again = await _resend(client, key, **body)
        session = await world.settled(key)

    assert first["message_id"] == sent and first["disposition"] == "started", first
    assert status == 200 and again["duplicate"] is True and again["message_id"] == sent, again
    assert len(world.model.requests) == 2, "the resend ran once"
    (row,) = rows_of(session.messages, EDITED)
    assert row["meta"]["message_id"] == sent
    assert row["ts"] == first["accepted_at"] == row["meta"]["receipt"]["accepted_at"]
    assert "client_ts" not in row["meta"]
    assert len(turn_starts(world.file_rows(key))) == 1


@pytest.mark.asyncio
async def test_a_resend_with_a_malformed_id_changes_nothing(tmp_path):
    world = World(tmp_path)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        rows = [dict(r) for r in world.state._sessions[key].messages]
        status, refused = await _resend(client, key, content=EDITED, client_message_id="msg_1")
        session = await world.settled(key)

    assert status == 400 and refused["error"]["code"] == "invalid_message_id", refused
    assert session.messages == rows, "the chat is not cut for a resend that was refused"
    assert len(world.model.requests) == 1


# ── a prompt run on the chat ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_prompt_run_on_an_idle_chat_starts_its_turn_with_an_id(tmp_path):
    world = World(tmp_path)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        session = world.state._sessions[key]
        assert session.enqueue_or_run_prompt(REPORT, run_chat, world.state) is True
        session = await world.settled(key)

    (row,) = rows_of(session.messages, REPORT)
    assert ids.is_id("msg", row["meta"]["message_id"])
    assert row["meta"]["receipt"]["disposition"] == "started"
    assert row["meta"]["receipt"]["accepted_at"] == row["ts"]
    assert len(world.model.requests) == 2


@pytest.mark.asyncio
async def test_a_prompt_run_on_a_busy_chat_waits_under_its_id(tmp_path):
    world = World(tmp_path, "The loader.", "Two failures, then.", hold=True)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        session = world.state._sessions[key]
        assert session.enqueue_or_run_prompt(REPORT, run_chat, world.state) is False
        (item,) = session._queue
        world.model.go_on.set()
        session = await world.settled(key)

    assert ids.is_id("msg", item["message_id"]) and item["receipt"]["disposition"] == "queued"
    (row,) = rows_of(session.messages, REPORT)
    assert row["meta"]["message_id"] == item["message_id"]
    assert row["meta"]["receipt"] == item["receipt"]


# ── a steer its turn never took ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_undelivered_steer_keeps_its_id(tmp_path):
    """One turn takes a few steers; the one past them waits in the queue for the next turn, and
    runs under the id and the receipt its send was answered."""
    world = World(tmp_path, "The loader.", "With every case.", "And that one.", hold=True)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        steers = [
            await world.send(client, f"Also check case {n}.", session=key, queue_mode="steer")
            for n in range(_MAX_STEERS_PER_TURN + 1)
        ]
        world.model.go_on.set()
        session = await world.settled(key)

    owed = steers[-1]
    assert all(s["disposition"] == "steered" for s in steers), steers
    (row,) = rows_of(session.messages, f"Also check case {_MAX_STEERS_PER_TURN}.")
    assert row["meta"]["message_id"] == owed["message_id"]
    assert row["meta"]["receipt"]["disposition"] == "steered"
    assert row["meta"]["receipt"]["accepted_at"] == owed["accepted_at"]
