"""A message the owner sends runs once, however many times it is sent.

A double click, a flaky network, a script's retry or an outbox resend sends the same message again.
Each send carries the message's id (``client_message_id``), and the chat takes a message in one way
(``chat_intake.take_in``): the first send of an id is answered with its receipt (what became of it,
and when), and a send of an id the chat already took, with the same words, answers that receipt
again and runs nothing. The same id with other words is refused. Counted here by the turns the
transcript holds, not by the answer alone.

Driven through the real chat handler, the real turn engine, the real session manager and a real
native runtime answered by a scripted model (``chat_send_world``).
"""

from __future__ import annotations

import asyncio

import pytest
from chat_send_world import World, configure, new_id, rows_of, turn_starts

from personalclaw import api_version, ids, turns
from personalclaw.dashboard import turn_rows

ASKED = "Write the loader for the garden config."
LATER = "Now add a test for it."
FIRST = "I'll indent it with four spaces."

RECEIPT = {"session", "message_id", "accepted_at", "disposition", "turn_message_id", "duplicate"}


def _again(receipt: dict) -> dict:
    """The receipt a repeat of the send that was answered *receipt* is answered."""
    return {**receipt, "duplicate": True}


# ── a repeated send answers its receipt and runs nothing ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_repeated_send_answers_its_receipt_and_runs_nothing_in_ws_mode(tmp_path):
    world = World(tmp_path)
    sent = new_id()
    async with world.client() as client:
        first = await world.send(client, ASKED, client_message_id=sent)
        key = first["session"]
        await world.settled(key)
        again = await world.send(client, ASKED, session=key, client_message_id=sent)
        session = await world.settled(key)

    assert set(first) == RECEIPT, first
    assert first["message_id"] == sent and first["turn_message_id"] == sent
    assert first["disposition"] == "started" and first["duplicate"] is False
    assert again == _again(first)
    assert len(world.model.requests) == 1
    assert len(turn_starts(session.messages)) == 1
    (row,) = turn_starts(world.file_rows(key))
    assert row["meta"]["message_id"] == sent
    assert row["meta"]["receipt"]["disposition"] == "started"
    assert row["meta"]["receipt"]["accepted_at"] == first["accepted_at"] == row["ts"]
    assert len(row["meta"]["receipt"]["body_digest"]) == 64
    assert "client_ts" not in row["meta"]


@pytest.mark.asyncio
async def test_a_repeated_send_answers_its_receipt_and_runs_nothing_in_sse_mode(tmp_path):
    world = World(tmp_path)
    sent = new_id()
    async with world.client() as client:
        status, events = await world.post(client, ASKED, ws=False, client_message_id=sent)
        assert status == 200, events
        name, first = events[0]
        assert name == "receipt", events
        key = first["session"]
        await world.settled(key)
        status, again = await world.post(
            client, ASKED, ws=False, session=key, client_message_id=sent
        )
        session = await world.settled(key)

    assert set(first) == RECEIPT and first["disposition"] == "started", first
    assert events[-1] == ("message", "[DONE]"), "the first send streamed its turn"
    assert status == 200 and again == _again(first)
    assert len(world.model.requests) == 1
    assert len(turn_starts(session.messages)) == len(turn_starts(world.file_rows(key))) == 1


@pytest.mark.asyncio
async def test_a_repeated_send_answers_its_receipt_and_runs_nothing_after_a_restart(tmp_path):
    before = World(tmp_path)
    sent = new_id()
    async with before.client() as client:
        first = await before.send(client, ASKED, client_message_id=sent)
        key = first["session"]
        await before.settled(key)

    # The gateway restarts: the chat is read back from its file alone.
    after = World(tmp_path)
    assert key not in after.state._sessions
    async with after.client() as client:
        again = await after.send(client, ASKED, session=key, client_message_id=sent)
        await after.settled(key)

    assert again == _again(first)
    assert after.model.requests == []
    assert len(turn_starts(after.file_rows(key))) == 1


# ── a page built for the answers before receipts is refused ────────────────────────────────


@pytest.mark.asyncio
async def test_a_page_built_for_version_2_is_refused_and_sends_nothing(tmp_path):
    """A page or script built for version 2 reads ``{ok, session}`` or ``{ok, steered}`` from a
    send, and would misread a receipt. The gateway's version check refuses it, in its own words,
    before the chat takes anything in; one built for this version is answered its receipt."""
    world = World(tmp_path)
    async with world.client(versioned=True) as client:
        resp = await client.post(
            "/api/chat?ws=1",
            json={"message": ASKED, "client_message_id": new_id()},
            headers={api_version.VERSION_HEADER: "2"},
        )
        assert resp.status == 400, await resp.text()
        refused = (await resp.json())["error"]
        assert world.state._sessions == {} and world.log.list_sessions() == []
        resp = await client.post(
            "/api/chat?ws=1",
            json={"message": ASKED, "client_message_id": new_id()},
            headers={api_version.VERSION_HEADER: str(api_version.API_VERSION)},
        )
        assert resp.status == 200, await resp.text()
        taken = await resp.json()
        await world.settled(taken["session"])

    assert api_version.API_VERSION == 3 and api_version.MIN_SUPPORTED_API_VERSION == 3
    assert refused["code"] == "api_version_unsupported"
    assert refused["message"] == api_version.negotiate("2").refusal.message
    assert refused["message"].startswith("This client was built for API version 2;")
    assert "reload the page" in refused["message"]
    assert (refused["client_version"], refused["upgrade"]) == (2, "client")
    assert taken["disposition"] == "started"
    assert len(world.model.requests) == 1, "only the current page's send ran"


# ── the same id with other words is refused ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reused_message_id_with_other_text_is_refused(tmp_path):
    world = World(tmp_path)
    sent = new_id()
    async with world.client() as client:
        key = (await world.send(client, ASKED, client_message_id=sent))["session"]
        await world.settled(key)
        rows = [dict(r) for r in world.state._sessions[key].messages]
        status, refused = await world.post(client, LATER, session=key, client_message_id=sent)
        session = await world.settled(key)

    assert status == 409, refused
    assert refused["error"]["code"] == "message_id_conflict"
    assert "different text" in refused["error"]["message"]
    assert session.messages == rows, "nothing of the refused send is kept"
    assert len(world.model.requests) == 1


@pytest.mark.asyncio
async def test_two_identical_sends_racing_run_one_turn(tmp_path):
    world = World(tmp_path)
    sent = new_id()
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        answers = await asyncio.gather(
            world.send(client, LATER, session=key, client_message_id=sent),
            world.send(client, LATER, session=key, client_message_id=sent),
        )
        session = await world.settled(key)

    assert sorted(a["duplicate"] for a in answers) == [False, True], answers
    taken = next(a for a in answers if not a["duplicate"])
    assert all(a == {**taken, "duplicate": a["duplicate"]} for a in answers)
    assert len(world.model.requests) == 2
    assert len(rows_of(session.messages, LATER)) == 1
    assert len(turn_starts(world.file_rows(key))) == 2


@pytest.mark.asyncio
async def test_two_identical_sends_racing_move_the_chat_to_their_agent_once(tmp_path):
    """The one wait before a message is taken in is moving the chat to the agent it names. A repeat
    sent while the first send waits on that move waits for its answer: the chat is moved once, the
    message runs once, and the repeat changes nothing."""
    world = World(tmp_path)
    resets: list[str] = []
    taken_meanwhile: list[int] = []
    reset = world.sessions.reset

    async def slowly(history_key: str):
        resets.append(history_key)
        await asyncio.sleep(0.05)  # the move waits on the runtime, as it does on a real one
        # Nothing of the message was taken in, by either send, while the chat was being moved.
        taken_meanwhile.append(len(rows_of(world.state._sessions[key].messages, LATER)))
        return await reset(history_key)

    world.sessions.reset = slowly
    sent = new_id()
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        resets.clear()
        answers = await asyncio.gather(
            *(
                world.send(client, LATER, session=key, agent="helper", client_message_id=sent)
                for _ in range(2)
            )
        )
        session = await world.settled(key)

    assert sorted(a["duplicate"] for a in answers) == [False, True], answers
    assert len(resets) == 1, resets
    assert taken_meanwhile == [0], "a repeat took the message in while the chat was being moved"
    assert session.agent == "helper"
    assert len(world.frames_of("session_binding")) == 1, "the chat was moved once"
    assert len(rows_of(session.messages, LATER)) == 1
    assert len(world.model.requests) == 2


# ── a malformed id is refused before anything is kept ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "msg_short",
        "msg_" + "0" * 25,
        "msg_" + "U" * 26,  # not a Crockford letter
        "run_" + "0" * 26,
        "MSG_" + "0" * 26,
        "",
        42,
        ["msg_" + "0" * 26],
    ],
)
async def test_a_malformed_message_id_is_refused_and_records_nothing(tmp_path, bad):
    world = World(tmp_path)
    async with world.client() as client:
        # Into a new chat: none is made.
        status, refused = await world.post(client, ASKED, client_message_id=bad)
        assert status == 400, refused
        assert refused["error"]["code"] == "invalid_message_id"
        assert refused["error"]["message"].startswith("A message id must be `msg_` followed by 26")
        assert world.state._sessions == {}
        assert world.log.list_sessions() == []

        # Into a chat that is there: nothing is added to it.
        key = (await world.send(client, ASKED))["session"]
        await world.settled(key)
        rows = [dict(r) for r in world.state._sessions[key].messages]
        status, refused = await world.post(client, LATER, session=key, client_message_id=bad)
        session = await world.settled(key)

    assert status == 400 and refused["error"]["code"] == "invalid_message_id", refused
    assert session.messages == rows
    assert len(world.model.requests) == 1


@pytest.mark.asyncio
async def test_a_send_with_no_id_is_given_one(tmp_path):
    world = World(tmp_path)
    async with world.client() as client:
        first = await world.send(client, ASKED)
        key = first["session"]
        await world.settled(key)

    assert ids.is_id("msg", first["message_id"]), first
    (row,) = turn_starts(world.file_rows(key))
    assert row["meta"]["message_id"] == first["message_id"]


# ── each disposition answers its receipt ───────────────────────────────────────────────────


async def _while_it_answers(world: World, client, **body) -> tuple[str, dict, dict]:
    """The owner asks, and while the answer is being written sends again with *body*: the chat,
    the first send's receipt and the second's."""
    asked = await world.send(client, ASKED, client_message_id=new_id())
    key = asked["session"]
    await asyncio.wait_for(world.model.answering.wait(), timeout=10)
    later = await world.send(client, LATER, session=key, client_message_id=new_id(), **body)
    world.model.go_on.set()
    return key, asked, later


@pytest.mark.asyncio
async def test_each_disposition_answers_its_receipt_started(tmp_path):
    world = World(tmp_path)
    async with world.client() as client:
        receipt = await world.send(client, ASKED, client_message_id=(sent := new_id()))
        await world.settled(receipt["session"])

    assert receipt["disposition"] == "started"
    assert receipt["turn_message_id"] == receipt["message_id"] == sent


@pytest.mark.asyncio
async def test_each_disposition_answers_its_receipt_steered(tmp_path):
    world = World(tmp_path, FIRST, "Switching to tabs.", hold=True)
    async with world.client() as client:
        key, asked, later = await _while_it_answers(world, client, queue_mode="steer")
        session = await world.settled(key)

    assert later["disposition"] == "steered", later
    assert later["turn_message_id"] == asked["message_id"], "a steer names the turn it joined"
    (row,) = rows_of(session.messages, LATER)
    assert row["meta"]["message_id"] == later["message_id"]
    assert row["meta"]["steered_into"] == asked["message_id"]
    assert row["meta"]["receipt"]["disposition"] == "steered"
    assert row["meta"]["receipt"]["accepted_at"] == later["accepted_at"] == row["ts"]


@pytest.mark.asyncio
async def test_each_disposition_answers_its_receipt_queued(tmp_path):
    world = World(tmp_path, FIRST, "A test, then.", hold=True)
    async with world.client() as client:
        key, _asked, later = await _while_it_answers(world, client, queue_mode="followup")
        session = await world.settled(key)

    assert later["disposition"] == "queued", later
    assert later["turn_message_id"] == later["message_id"]
    (row,) = rows_of(session.messages, LATER)
    assert row["meta"]["receipt"]["disposition"] == "queued"


@pytest.mark.asyncio
async def test_each_disposition_answers_its_receipt_replaced(tmp_path):
    configure(
        {
            "resilience": {
                "mid_turn_policy": "cancel_and_replace",
                "cancel_replace_min_interval_secs": 0,
            }
        }
    )
    world = World(tmp_path, FIRST, "A test, then.", hold=True)
    async with world.client() as client:
        key, _asked, later = await _while_it_answers(world, client)
        session = await world.settled(key)

    assert later["disposition"] == "replaced", later
    assert later["turn_message_id"] == later["message_id"]
    (row,) = rows_of(session.messages, LATER)
    assert row["meta"]["message_id"] == later["message_id"]
    assert row["meta"]["receipt"]["disposition"] == "replaced"
    superseded = [f for f in world.frames_of("chat_done") if f.get("superseded")]
    assert len(superseded) == 1, world.frames_of("chat_done")


# ── a repeated steer runs nothing ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_repeated_steer_runs_nothing_before_the_runtime_takes_it(tmp_path):
    world = World(tmp_path, FIRST, "Switching to tabs.", hold=True)
    steer = new_id()
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        first = await world.send(
            client, LATER, session=key, queue_mode="steer", client_message_id=steer
        )
        again = await world.send(
            client, LATER, session=key, queue_mode="steer", client_message_id=steer
        )
        world.model.go_on.set()
        session = await world.settled(key)

    assert first["disposition"] == "steered" and again == _again(first)
    assert len(rows_of(session.messages, LATER)) == 1
    assert world.model.read().count(LATER) == 1, "the turn was handed it once"
    assert len(world.model.requests) == 2, "and nothing ran after it"


@pytest.mark.asyncio
async def test_a_repeated_steer_runs_nothing_after_the_runtime_took_it(tmp_path):
    world = World(tmp_path, FIRST, "Switching to tabs.", hold=True)
    steer = new_id()
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        first = await world.send(
            client, LATER, session=key, queue_mode="steer", client_message_id=steer
        )
        world.model.go_on.set()
        await world.settled(key)
        again = await world.send(
            client, LATER, session=key, queue_mode="steer", client_message_id=steer
        )
        session = await world.settled(key)

    assert again == _again(first)
    assert len(rows_of(session.messages, LATER)) == 1
    assert len(world.model.requests) == 2
    assert len(turn_starts(world.file_rows(key))) == 1


# ── a queued message keeps its id ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_queued_message_keeps_its_id_when_it_runs(tmp_path):
    world = World(tmp_path, FIRST, "A test, then.", hold=True)
    async with world.client() as client:
        key, _asked, later = await _while_it_answers(world, client, queue_mode="followup")
        await world.settled(key)
        again = await world.send(
            client, LATER, session=key, queue_mode="followup", client_message_id=later["message_id"]
        )
        session = await world.settled(key)

    (row,) = rows_of(session.messages, LATER)
    assert row["meta"]["message_id"] == later["message_id"]
    assert row["meta"]["receipt"]["accepted_at"] == later["accepted_at"]
    assert again == _again(later), "a repeat after it ran answers the receipt it was queued with"
    assert len(world.model.requests) == 2
    pushed = [f for f in world.frames_of("queue_push") if f.get("message_id")]
    assert [f["message_id"] for f in pushed] == [later["message_id"]]
    shown = [f for f in world.frames_of("chat_user_message") if f.get("message_id")]
    assert [f["message_id"] for f in shown] == [later["message_id"]]


@pytest.mark.asyncio
async def test_a_repeat_of_a_queued_message_while_it_waits_runs_nothing(tmp_path):
    world = World(tmp_path, FIRST, "A test, then.", hold=True)
    queued = new_id()
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        first = await world.send(
            client, LATER, session=key, queue_mode="followup", client_message_id=queued
        )
        again = await world.send(
            client, LATER, session=key, queue_mode="followup", client_message_id=queued
        )
        assert len(world.state._sessions[key]._queue) == 1
        world.model.go_on.set()
        session = await world.settled(key)

    assert again == _again(first)
    assert len(rows_of(session.messages, LATER)) == 1
    assert len(world.model.requests) == 2


@pytest.mark.asyncio
async def test_a_merged_turn_names_each_message(tmp_path):
    configure({"dashboard": {"merge_queued_messages": True}})
    world = World(tmp_path, FIRST, "Both done.", hold=True)
    also = "And document the loader."
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        one = await world.send(
            client, LATER, session=key, queue_mode="followup", client_message_id=new_id()
        )
        two = await world.send(
            client, also, session=key, queue_mode="followup", client_message_id=new_id()
        )
        world.model.go_on.set()
        await world.settled(key)
        again = await world.send(
            client, also, session=key, queue_mode="followup", client_message_id=two["message_id"]
        )
        session = await world.settled(key)

    (merged,) = [r for r in session.messages if "queued messages merged" in str(r["content"])]
    meta = merged["meta"]
    assert [m["message_id"] for m in meta["merged"]] == [one["message_id"], two["message_id"]]
    assert all(
        m["source"] == {"source_thread": "dashboard", "source_user": "dashboard"}
        for m in meta["merged"]
    )
    assert ids.is_id("msg", meta["message_id"])
    assert meta["message_id"] not in (one["message_id"], two["message_id"])
    assert "queued_from" not in meta
    assert again == _again(two), "a repeat of a merged message runs nothing"
    assert len(world.model.requests) == 2


@pytest.mark.asyncio
async def test_the_turn_model_reads_each_receipt_and_each_merged_message(tmp_path):
    configure({"dashboard": {"merge_queued_messages": True}})
    world = World(tmp_path, FIRST, "Both done.", hold=True)
    async with world.client() as client:
        asked = await world.send(client, ASKED, client_message_id=new_id())
        key = asked["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        one = await world.send(client, LATER, session=key, queue_mode="followup")
        two = await world.send(client, "And document it.", session=key, queue_mode="followup")
        world.model.go_on.set()
        session = await world.settled(key)

    messages = turn_rows.from_rows(session.messages, session_key=key)
    first = next(m for m in messages if m.id == asked["message_id"])
    assert first.facts.receipt is not None
    assert first.facts.receipt["accepted_at"] == asked["accepted_at"]
    assert first.facts.receipt["disposition"] == "started"
    (merged,) = [m for m in messages if m.facts.merged]
    assert [m["message_id"] for m in merged.facts.merged] == [one["message_id"], two["message_id"]]
    assert [m["receipt"]["disposition"] for m in merged.facts.merged] == ["queued", "queued"]
    # And the conversation a model reads is as it was: messages from one place name no sources.
    assert all("meta" not in line for line in turns.dialogue(messages) if line["role"] == "user")
