"""A turn is read by the id of the message that started it: queued, running or settled.

``GET /api/chat/sessions/{session}/turns/{message_id}`` answers shared contracts §3.9's turn
document, ``{session, message_id, state, outcome, reply, attempts, usage, error, run_state}``: a
message still in the queue is ``queued``; the running turn is ``running``, with its run state; a
turn that ended is ``settled``, with how it ended and its shown answer. A steer's id, and a merged
message's, answer the turn it ran in. The turn's start row records how it ended (``meta.turn``), so
a gateway that restarted reads it from the chat's file.

Driven through the real handlers, the real turn engine, the real session manager and a real native
runtime answered by a scripted model (``chat_send_world``).
"""

from __future__ import annotations

import asyncio

import pytest
from chat_send_world import Model, World, configure, new_id, rows_of

ASKED = "Write the loader for the garden config."
LATER = "Then add a test for it."


async def _turn(client, key: str, message_id: str) -> tuple[int, dict]:
    resp = await client.get(f"/api/chat/sessions/{key}/turns/{message_id}")
    return resp.status, await resp.json()


class _Failing(Model):
    """A model whose every answer fails before its first word."""

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        raise RuntimeError("the model's endpoint answered 500")
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_the_turn_document_answers_each_state(tmp_path):
    world = World(tmp_path, "The loader, first part.", "And the test.", hold=True)
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        later = await world.send(
            client, LATER, session=key, queue_mode="followup", client_message_id=new_id()
        )

        status, running = await _turn(client, key, asked["message_id"])
        assert status == 200, running
        assert running["state"] == "running" and running["outcome"] is None
        assert running["run_state"]["turn"]["message_id"] == asked["message_id"]
        status, queued = await _turn(client, key, later["message_id"])
        assert status == 200, queued
        assert queued["state"] == "queued" and queued["run_state"] is None
        assert queued["reply"] is None and queued["attempts"] == 0

        world.model.go_on.set()
        await world.settled(key)
        status, first = await _turn(client, key, asked["message_id"])
        _, second = await _turn(client, key, later["message_id"])

    assert status == 200, first
    assert first["session"] == key and first["message_id"] == asked["message_id"]
    assert first["state"] == "settled" and first["outcome"] == "complete"
    assert first["run_state"] is None and first["error"] is None and first["attempts"] == 1
    assert [p["text"] for p in first["reply"]["parts"] if p["kind"] == "text"] == [
        "The loader, first part."
    ]
    assert second["state"] == "settled" and second["outcome"] == "complete"
    assert second["message_id"] == later["message_id"]


@pytest.mark.asyncio
async def test_a_stopped_turn_and_a_failed_one_say_how_they_ended(tmp_path):
    world = World(tmp_path, "Half an answer", hold=True)
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        stop = await client.post(
            f"/api/chat/sessions/{key}/stop", json={"message_id": asked["message_id"]}
        )
        assert stop.status == 200, await stop.text()
        session = await world.settled(key)
        _, stopped = await _turn(client, key, asked["message_id"])

    assert stopped["state"] == "settled" and stopped["outcome"] == "stopped", stopped
    (row,) = rows_of(session.messages, ASKED)
    assert row["meta"]["turn"]["outcome"] == "stopped"
    assert row["meta"]["turn"]["settled_at"]

    failing = World(tmp_path / "other")
    failing.model = _Failing()
    async with failing.client() as client:
        sent = await failing.send(client, ASKED, client_message_id=new_id())
        await failing.settled(sent["session"])
        _, failed = await _turn(client, sent["session"], sent["message_id"])

    assert failed["state"] == "settled" and failed["outcome"] == "error", failed
    assert failed["error"]["code"] == "turn_failed"


@pytest.mark.asyncio
async def test_a_steers_id_answers_the_turn_it_joined(tmp_path):
    world = World(tmp_path, "The loader.", "With every case.", hold=True)
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        steer = await world.send(
            client,
            "Also check the empty file.",
            session=key,
            queue_mode="steer",
            expected_message_id=asked["message_id"],
            client_message_id=new_id(),
        )
        assert steer["disposition"] == "steered", steer
        status, joined = await _turn(client, key, steer["message_id"])
        world.model.go_on.set()
        await world.settled(key)

    assert status == 200, joined
    assert joined["message_id"] == asked["message_id"] and joined["state"] == "running"


@pytest.mark.asyncio
async def test_a_merged_messages_id_answers_the_turn_it_ran_in(tmp_path):
    configure({"dashboard": {"merge_queued_messages": True}})
    world = World(tmp_path, "The loader.", "Both done.", hold=True)
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        one = await world.send(
            client, LATER, session=key, queue_mode="followup", client_message_id=new_id()
        )
        two = await world.send(
            client,
            "And document it.",
            session=key,
            queue_mode="followup",
            client_message_id=new_id(),
        )
        world.model.go_on.set()
        await world.settled(key)
        _, by_one = await _turn(client, key, one["message_id"])
        _, by_two = await _turn(client, key, two["message_id"])

    assert by_one["state"] == "settled" and by_one["outcome"] == "complete", by_one
    assert by_one["message_id"] == by_two["message_id"]
    assert by_one["message_id"] not in (one["message_id"], two["message_id"])
    assert [p["text"] for p in by_one["reply"]["parts"] if p["kind"] == "text"] == ["Both done."]


@pytest.mark.asyncio
async def test_a_settled_turn_reads_the_same_after_a_restart(tmp_path):
    world = World(tmp_path, "The loader.")
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await world.settled(key)
        _, before = await _turn(client, key, asked["message_id"])

    restarted = World(tmp_path)
    async with restarted.client() as client:
        status, after = await _turn(client, key, asked["message_id"])

    assert status == 200, after
    assert after == before and after["outcome"] == "complete"


@pytest.mark.asyncio
async def test_an_unknown_message_id_is_not_found(tmp_path):
    world = World(tmp_path, "The loader.")
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        status, answer = await _turn(client, key, new_id())
        missing_status, missing = await _turn(client, "no-such-chat", new_id())

    assert status == 404 and answer["error"]["code"] == "turn_not_found", answer
    assert missing_status == 404 and missing["error"]["code"] == "session_not_found", missing


@pytest.mark.asyncio
async def test_a_malformed_id_is_refused(tmp_path):
    world = World(tmp_path, "The loader.")
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        status, answer = await _turn(client, key, "msg_1")

    assert status == 400 and answer["error"]["code"] == "invalid_message_id", answer


def test_a_long_result_is_cut_in_the_document():
    """Each result the reply carries is its first 8,000 characters."""
    from personalclaw.dashboard import chat_turns

    long = "x" * 9_000
    reply = {"parts": [{"kind": "tool", "result": {"text": long, "truncated": False}}]}
    cut = chat_turns.cut_results(reply)

    result = cut["parts"][0]["result"]
    assert len(result["text"]) == chat_turns.RESULT_CHARS == 8_000
    assert result["truncated"] is True and result["original_length"] == 9_000
    assert len(reply["parts"][0]["result"]["text"]) == 9_000, "the reply read is left whole"


def test_a_turns_end_skips_a_row_it_cannot_read():
    """A chat's rows are mappings, but a turn's end never fails over one that is not: it writes how
    the turn ended on the row the id names, and its document reads it back past the other."""
    from types import SimpleNamespace

    from personalclaw.dashboard import chat_phase, chat_turns

    mid = new_id()
    started = {"role": "user", "content": ASKED, "meta": {"message_id": mid}}
    session = SimpleNamespace(messages=[started, ("assistant", "a row of another shape", "x")])

    chat_phase._record_ending(session, mid, "completed")

    assert started["meta"]["turn"]["outcome"] == "completed"
    assert chat_turns._recorded_outcome(session, mid) == "completed"


@pytest.mark.asyncio
async def test_a_name_that_would_leave_the_sessions_folder_is_no_chat_and_reads_nothing(
    tmp_path, monkeypatch
):
    """The route holds a chat's name to the folder the chats are kept in before it reads anything:
    a name that would leave it is no chat, 404 ``session_not_found``, and nothing is read for it."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard import chat_persistence
    from personalclaw.dashboard.chat_turns import api_chat_turn

    world = World(tmp_path, "The loader.")
    (tmp_path / "outside.jsonl").write_text('{"_type": "metadata", "title": "Not a chat"}\n')
    read: list[str] = []
    real = chat_persistence._rehydrate_session_from_history

    def rehydrate(state, name, **kwargs):
        read.append(name)
        return real(state, name, **kwargs)

    monkeypatch.setattr(chat_persistence, "_rehydrate_session_from_history", rehydrate)
    app = web.Application()
    app["state"] = world.state
    mid = new_id()
    for name in ("..", ".", "../outside", "../history/../outside", "a/b", "./x"):
        request = make_mocked_request(
            "GET",
            f"/api/chat/sessions/x/turns/{mid}",
            match_info={"session": name, "message_id": mid},
            app=app,
        )
        resp = await api_chat_turn(request)
        assert resp.status == 404, name
        assert b'"session_not_found"' in resp.body, name

    assert read == [], "nothing is read for a name outside the folder"
