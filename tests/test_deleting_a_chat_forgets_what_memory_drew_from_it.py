"""Deleting a chat removes what memory drew from it alone, and keeps what came from elsewhere too.

The defect this pins: the Delete dialog says a chat "and its history will be permanently removed",
and the delete removed the transcript and its files, but nothing memory had drawn from the chat:
the episodes and the facts its consolidation wrote, and its sealed summary. Another chat still
recalled them, in its prompt and through the memory tool.

The rule now, every half of it driven against the real stores, consolidation and seal, the real
delete routes and the real recall paths:

* what memory filed under the chat alone goes from every memory that holds it (the global memory
  and a folder's), with the daily-history entries that repeat it and the digests that quoted it;
* a record other work also stands behind stays: a lesson she taught in another chat too, a fact
  she edited in Memory, what consolidation's maintenance drew from every chat;
* a record the chat had replaced is live again;
* the history routes delete as the Delete button does, and a pass still waiting on a model when
  the chat is deleted keeps nothing it would have written.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from chat_test_helpers import _make_app

from personalclaw import memory_writes
from personalclaw.context import ContextBuilder
from personalclaw.context_engine import assemble_context
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.memory import MemoryStore
from personalclaw.memory_service import MemoryService
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore

#: What the consolidation model keeps from a chat about a ski trip. Invented content.
SKI = {
    "history_entry": "Booked the ski week at the lodge in the valley; the lift pass is six days.",
    "episodic": [
        {
            "text": "The ski lodge booking is for the second week of February, six day pass.",
            "tags": [],
        }
    ],
    "semantic": [
        {"key": "user.pref.ski_resort", "value": "the lodge in the valley", "confidence": 0.9}
    ],
}
SKI_EPISODE = SKI["episodic"][0]["text"]
SKI_FACT = "the lodge in the valley"
SKI_SUMMARY = SKI["history_entry"]
#: What it keeps from another chat, which stays.
BIKE = {
    "history_entry": "Serviced the road bike: new chain and brake pads before the spring rides.",
    "episodic": [
        {
            "text": "The road bike got a new chain and brake pads before the spring rides.",
            "tags": [],
        }
    ],
    "semantic": [
        {"key": "user.pref.bike_shop", "value": "the shop by the river bridge", "confidence": 0.9}
    ],
}
BIKE_EPISODE = BIKE["episodic"][0]["text"]
BIKE_FACT = "the shop by the river bridge"
#: What another chat asks. The keyword search reads a question's first five words.
ASKED = "Which ski lodge did I book in February?"
ASKED_BIKE = "Which bike shop serviced the road bike?"
#: Two of her chats, kept on disk only (a gateway restart restores neither).
TRAIN_CHAT = "dashboard:chat-41-1790800001"
TRIP_CHAT = "dashboard:chat-42-1790800002"


@pytest.fixture
def gw(tmp_path: Path):
    """A gateway's memory wiring over the test's home: the chat state that saves and deletes
    transcripts, the global memory (markdown and memory database) the context builder and the
    consolidator share, and a consolidation model that answers each chat with what it keeps."""
    log = ConversationLog()
    log.init()
    main = MemoryStore()
    main.init()
    store = VectorMemoryStore(confidence_threshold=0.0)
    store.init()
    main.vector_store = store
    builder = ContextBuilder(
        memory=main,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    builder.conversation_log = log
    consolidator = HistoryConsolidator(
        log=log, memory=main, vector_store=store, migrated=True, history_idle_secs=0
    )
    answers: dict[str, dict] = {}
    model = AsyncMock(
        side_effect=lambda _prompt, key, **_kw: json.loads(json.dumps(answers.get(key, {})))
    )
    consolidator._call_llm = model  # type: ignore[method-assign]
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.destroy = AsyncMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        context_builder=builder,
        conversation_log=log,
        consolidator=consolidator,
    )
    yield SimpleNamespace(
        log=log,
        main=main,
        store=store,
        builder=builder,
        consolidator=consolidator,
        answers=answers,
        state=state,
    )
    store.close()


def _chat(gw: SimpleNamespace, answer: dict | None = None, folder: str = "") -> tuple[str, object]:
    """A chat saved as the dashboard saves a running chat, working in *folder*, whose
    consolidation the model answers with *answer*. Returns ``(history key, session)``."""
    session = gw.state.get_or_create_session(name=None, workspace_dir=folder)
    session.append("user", "Here is what I worked out today.", broadcast=False)
    session.append("assistant", "Noted, I will keep it in mind.", broadcast=False)
    session.drain()
    save_session_to_history(gw.state, session, force=True)
    key = _history_key_for(session.key)
    gw.answers[key] = answer or {}
    return key, session


def _scoped(key: str):
    """The work of chat *key*, a chat that keeps memory, as its consolidation and its turns run."""
    return memory_writes.derived_from(key, memory_mode="persistent")


def _all_rows(store: VectorMemoryStore) -> str:
    """Every text the memory database holds anywhere: live and deleted records, and the history
    events, which carry what each record said."""
    texts: list[str] = []
    for sql in (
        "SELECT text FROM episodic_memories",
        "SELECT value_json FROM semantic_memory",
        "SELECT COALESCE(old_value, '') || ' ' || COALESCE(new_value, '') FROM memory_events",
    ):
        texts += [str(r[0]) for r in store.db.execute(sql).fetchall()]
    return "\n".join(texts)


def _daily_history(memory: MemoryStore) -> str:
    folder = memory._history_dir
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md")))


def _turn_context(gw: SimpleNamespace, key: str, text: str) -> str:
    """The prompt the first turn of chat *key* is assembled with."""
    assembled = assemble_context(
        gw.builder, text, is_new_session=True, session_key=key, cwd=None, memory_store=None
    )
    return assembled.message


async def _memory_tool(gw: SimpleNamespace, caller: str, query: str) -> str:
    """What the agent's memory tool answers in chat *caller*."""
    from personalclaw.dashboard.handlers.memory import api_memory_recall

    app = web.Application()
    app["state"] = gw.state
    request = make_mocked_request(
        "GET",
        f"/api/memory/recall?{urlencode({'q': query})}",
        headers={"X-Internal-Secret": "the-gateways-own", "X-Session-Key": caller},
        app=app,
    )
    resp = await api_memory_recall(request)
    return json.loads(resp.body.decode())["result"]


async def _recalled(gw: SimpleNamespace, question: str) -> str:
    """What a new chat's first prompt and its memory tool recall for *question*."""
    asker, _ = _chat(gw)
    return _turn_context(gw, asker, question) + await _memory_tool(gw, asker, question)


async def _delete(gw: SimpleNamespace, name: str) -> int:
    """Delete chat *name* as the dialog's Delete button does."""
    client = TestClient(TestServer(_make_app(gw.state)))
    await client.start_server()
    try:
        resp = await client.delete(f"/api/chat/sessions/{name}")
        return resp.status
    finally:
        await client.close()


async def _press_delete(state: DashboardState, key: str) -> int:
    """The Delete button on chat *key*, by the bare name the chat list gives it."""
    from personalclaw.dashboard.chat_handlers import api_chat_session_delete

    name = key.removeprefix("dashboard:")
    app = web.Application()
    app["state"] = state
    request = make_mocked_request(
        "DELETE", f"/api/chat/sessions/{name}", match_info={"session": name}, app=app
    )
    return (await api_chat_session_delete(request)).status


def _kept_chat(gw: SimpleNamespace, key: str) -> None:
    """A chat of hers kept on disk, as a restart leaves one: its transcript, not open here."""
    path = gw.log._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"_type": "metadata", "created_at": "2026-10-02T15:00:00+00:00"}
    turn = {"role": "user", "ts": "2026-10-02T15:00:00+00:00", "content": "Planning the trip."}
    path.write_text(json.dumps(meta) + "\n" + json.dumps(turn) + "\n", encoding="utf-8")


def _semantic_keys(store: VectorMemoryStore) -> set[str]:
    return {str(r[0]) for r in store.db.execute("SELECT key FROM semantic_memory")}


# ── what memory drew from the chat alone goes ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_chat_removes_its_episodes_facts_and_summary_from_memory(gw) -> None:
    ski, ski_chat = _chat(gw, SKI)
    bike, _ = _chat(gw, BIKE)
    assert await gw.consolidator.consolidate_session(ski)
    assert await gw.consolidator.consolidate_session(bike)
    # What the precondition rests on: memory drew all three from the chat, and another chat
    # recalls them.
    held = _all_rows(gw.store)
    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text in held, f"consolidation kept no {text!r}"
    assert SKI_SUMMARY in _daily_history(gw.main)
    before = await _recalled(gw, ASKED)
    assert SKI_EPISODE in before and SKI_FACT in before
    assert BIKE_EPISODE in await _recalled(gw, ASKED_BIKE)

    assert await _delete(gw, ski_chat.key) == 200

    held = _all_rows(gw.store)
    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text not in held, f"memory still holds {text!r} from the deleted chat"
    assert SKI_SUMMARY not in _daily_history(gw.main)
    after = await _recalled(gw, ASKED)
    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text not in after, f"another chat recalled {text!r} from the deleted chat"
    # The other chat's memory is untouched, and still recalled.
    for text in (BIKE_EPISODE, BIKE_FACT, BIKE["history_entry"]):
        assert text in _all_rows(gw.store)
    assert BIKE["history_entry"] in _daily_history(gw.main)
    bike_after = await _recalled(gw, ASKED_BIKE)
    assert BIKE_EPISODE in bike_after and BIKE_FACT in bike_after


@pytest.mark.asyncio
async def test_every_summary_a_chat_was_consolidated_into_leaves_the_daily_history(gw) -> None:
    """A chat consolidated twice wrote two summaries to the daily history, and its running summary
    holds only the second: the first goes too."""
    ski, ski_chat = _chat(gw, SKI)
    first = "Asked about lift passes for the valley; no booking yet."
    gw.answers[ski] = {**SKI, "history_entry": first}
    await gw.consolidator.consolidate_now(ski)
    ski_chat.append("user", "Book it for February, please.", broadcast=False)
    ski_chat.drain()
    save_session_to_history(gw.state, ski_chat, force=True)
    gw.answers[ski] = SKI
    assert await gw.consolidator.consolidate_session(ski)
    history = _daily_history(gw.main)
    assert first in history and SKI_SUMMARY in history

    assert await _delete(gw, ski_chat.key) == 200

    history = _daily_history(gw.main)
    assert first not in history and SKI_SUMMARY not in history


@pytest.mark.asyncio
async def test_a_folder_chats_memory_goes_from_its_folders_partition(gw, tmp_path) -> None:
    folder = tmp_path / "trip-notes"
    folder.mkdir()
    ski, ski_chat = _chat(gw, SKI, folder=str(folder))
    assert await gw.consolidator.consolidate_session(ski)
    partition = ContextBuilder.get_memory_for(str(folder))
    assert partition.vector_store is not None and partition.vector_store is not gw.store
    assert SKI_EPISODE in _all_rows(partition.vector_store)

    assert await _delete(gw, ski_chat.key) == 200

    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text not in _all_rows(partition.vector_store), f"the folder's memory kept {text!r}"
    assert SKI_SUMMARY not in _daily_history(partition)


@pytest.mark.asyncio
async def test_a_days_digest_no_longer_quotes_a_deleted_chats_episode(gw) -> None:
    train = "Booked the sleeper train north for the second week of February."
    trip = "Packed the ski bag and checked the bindings before the trip."
    _kept_chat(gw, TRAIN_CHAT)
    with _scoped(TRAIN_CHAT):
        assert gw.store.write_episodic(train, conversation_id=TRAIN_CHAT)
    with _scoped(TRIP_CHAT):
        assert gw.store.write_episodic(trip, conversation_id=TRIP_CHAT)
    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
    gw.store.db.execute("UPDATE episodic_memories SET created_at = ?", (yesterday,))
    gw.store.db.commit()
    svc = MemoryService.over_vector_store(gw.store)
    assert svc.build_daily_digest() == 1
    digest = svc.daily_digests()[0]["text"]
    assert train in digest and trip in digest

    assert await _press_delete(gw.state, TRAIN_CHAT) == 200

    digests = svc.daily_digests()
    assert len(digests) == 1
    assert train not in digests[0]["text"], "the day's digest still quotes the deleted chat"
    assert trip in digests[0]["text"]
    assert train not in _all_rows(gw.store)


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted_on_call", [1, 2])
async def test_a_chat_deleted_while_its_consolidation_waits_on_the_model_keeps_nothing(
    gw, deleted_on_call
) -> None:
    """She deletes the chat while its consolidation waits for the model: on the call that extracts
    what to keep, or on the one that decides how the facts meet what memory holds. Nothing the
    pass would write after its chat is gone is kept, and the model is not asked again."""
    # A fact under the same key, so formation asks the model to decide.
    with _scoped("dashboard:chat-99-1790000099"):
        assert gw.store.set_semantic("user.pref.ski_resort", "the old lodge", 0.9, "c") is None
    ski, ski_chat = _chat(gw, SKI)
    calls = 0

    async def answer(_prompt: str, key: str, **_kw: object) -> dict:
        nonlocal calls
        calls += 1
        if calls == deleted_on_call:
            assert await _press_delete(gw.state, ski_chat.key) == 200
        return json.loads(json.dumps(SKI)) if calls == 1 else {"decisions": []}

    gw.consolidator._call_llm = answer  # type: ignore[method-assign]

    await gw.consolidator.consolidate_session(ski)

    assert calls == deleted_on_call, "the pass called the model again after its chat was gone"
    held = _all_rows(gw.store)
    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text not in held, f"the pass kept {text!r} after its chat was deleted"
    assert SKI_SUMMARY not in _daily_history(gw.main)


@pytest.mark.asyncio
async def test_deleting_through_the_history_routes_forgets_the_same(gw) -> None:
    """``DELETE /api/sessions/{key}`` and ``DELETE /api/sessions`` delete a chat the way the
    Delete button does: its tool results and what memory drew from it alone go too."""
    from personalclaw.dashboard.handlers.sessions import api_session_delete, api_sessions_clear
    from personalclaw.tool_providers import result_store

    ski, _ = _chat(gw, SKI)
    bike, bike_chat = _chat(gw, BIKE)
    assert await gw.consolidator.consolidate_session(ski)
    assert await gw.consolidator.consolidate_session(bike)
    kept = result_store.store_result(ski, "the lodge booking page", content_type="log", tool="bash")
    app = web.Application()
    app["state"] = gw.state

    request = make_mocked_request(
        "DELETE", f"/api/sessions/{ski}", match_info={"key": ski}, app=app
    )
    assert (await api_session_delete(request)).status == 200

    assert not result_store.fetch_slice(ski, kept).get("ok"), "its tool results stayed"
    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text not in _all_rows(gw.store)
    # The bulk delete takes the chats not open here; the bike chat is closed now.
    gw.state._sessions.pop(bike_chat.key, None)
    request = make_mocked_request("DELETE", "/api/sessions", app=app)
    body = json.loads((await api_sessions_clear(request)).body)
    assert body["cleared"] >= 1 and body["failed"] == 0
    for text in (BIKE_EPISODE, BIKE_FACT, BIKE["history_entry"]):
        assert text not in _all_rows(gw.store)
    assert BIKE["history_entry"] not in _daily_history(gw.main)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["the Delete button", "the history route"])
async def test_a_chat_whose_history_cannot_be_removed_is_said_to_stay(gw, monkeypatch, route):
    """A delete that cannot remove the chat's transcript (a file the gateway may not unlink)
    answers that the chat is still kept, not that it is gone, and the audit log records it as
    failed. What else the chat kept goes all the same."""
    from personalclaw.dashboard.chat_handlers import api_chat_session_delete
    from personalclaw.dashboard.handlers.sessions import api_session_delete
    from personalclaw.sel import sel

    ski, ski_chat = _chat(gw, SKI)
    assert await gw.consolidator.consolidate_session(ski)
    gw.state._sessions.pop(ski_chat.key, None)

    def refuse(_log, _key):
        raise PermissionError("the folder may not be written")

    monkeypatch.setattr(ConversationLog, "delete_session", refuse)
    app = web.Application()
    app["state"] = gw.state
    if route == "the Delete button":
        request = make_mocked_request(
            "DELETE",
            f"/api/chat/sessions/{ski_chat.key}",
            match_info={"session": ski_chat.key},
            app=app,
        )
        resp = await api_chat_session_delete(request)
    else:
        request = make_mocked_request(
            "DELETE", f"/api/sessions/{ski}", match_info={"key": ski}, app=app
        )
        resp = await api_session_delete(request)

    assert resp.status == 500, "a chat still on disk was answered as deleted"
    assert json.loads(resp.body)["error"]["code"] == "session_not_deleted"
    assert gw.log.has_log(ski), "the test's refusal did not hold"
    said = [
        (e.get("outcome"), e.get("resources"))
        for e in sel().recent(limit=50)
        if e.get("operation") == "chat.deleted"
    ]
    assert said and said[0][0] == "failure", said
    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text not in _all_rows(gw.store)


# ── what cannot be traced to the chat alone stays ───────────────────────────────────────────

#: A lesson she teaches in both chats, and one she teaches in each alone.
BOTH = "Book the window seat on long train rides."
ONLY_IN = {
    TRAIN_CHAT: "Keep the rail cards in the blue wallet.",
    TRIP_CHAT: "Keep the ski boots in the hallway closet.",
}


def _lessons_held(home: Path) -> dict[str, dict]:
    """Every lesson row in the gateway's memory, deleted ones included, by its rule."""
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    try:
        rows = store.db.execute(
            "SELECT key, value_json, is_deleted, source_session FROM semantic_memory "
            "WHERE key LIKE 'lesson.%'"
        ).fetchall()
        return {str(json.loads(r["value_json"])): dict(r) for r in rows}
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [TRAIN_CHAT, TRIP_CHAT])
async def test_a_lesson_taught_in_two_chats_survives_deleting_either(
    tmp_path, monkeypatch, deleted
) -> None:
    """She teaches the window-seat rule in both chats, and one rule in each chat alone, through the
    agent's memory tool. Deleting either chat keeps the rule she gave both and the other chat's
    own; the rule she taught only in the deleted chat goes with it, and another chat's agent no
    longer lists it."""
    from test_memory_is_read_only_where_its_work_may_read_it import HERE, _call
    from test_memory_is_read_only_where_its_work_may_read_it import _chat as _transcript
    from test_memory_is_read_only_where_its_work_may_read_it import _gateway

    kept = TRIP_CHAT if deleted == TRAIN_CHAT else TRAIN_CHAT
    async with _gateway(tmp_path, monkeypatch) as gw:
        # The gateway's session manager lets a chat's runtime go with a coroutine.
        gw.state.sessions.remove = AsyncMock()
        gw.state.sessions.destroy = AsyncMock()
        for chat in (TRAIN_CHAT, TRIP_CHAT):
            _transcript(gw.home, chat, "Planning the trip.")
            for rule in (BOTH, ONLY_IN[chat]):
                ok, said = await _call(
                    "memory_remember", {"rule": rule, "category": "preference"}, asked_from=chat
                )
                assert ok, said
        held = _lessons_held(gw.home)
        assert held[ONLY_IN[deleted]]["source_session"] == deleted, "filed under its chat"

        assert await _press_delete(gw.state, deleted) == 200

        held = _lessons_held(gw.home)
        ok, listed = await _call("memory_list", {}, asked_from=HERE)
        assert ok, listed
        for rule in (BOTH, ONLY_IN[kept]):
            assert rule in held and not held[rule]["is_deleted"], f"{rule!r} went"
            assert rule in listed
        assert ONLY_IN[deleted] not in held, "a rule taught only in the deleted chat stayed"
        assert ONLY_IN[deleted] not in listed


@pytest.mark.asyncio
async def test_a_fact_you_edited_in_memory_survives_deleting_the_chat_it_came_from(gw) -> None:
    """A fact the chat's work wrote is its alone until other work writes it too: her own edit on
    the Memory page (work outside any session) makes it hers as well, and it stays."""
    _kept_chat(gw, TRAIN_CHAT)
    with _scoped(TRAIN_CHAT):
        assert gw.store.set_semantic("user.pref.seat", "window", 0.9, "consolidation:x") is None
        assert gw.store.set_semantic("user.pref.carriage", "quiet", 0.9, "consolidation:x") is None
    # Her edit, as the Memory page writes it.
    assert (
        gw.store.set_semantic("user.pref.seat", "window, facing forward", 1.0, "user_explicit")
        is None
    )

    assert await _press_delete(gw.state, TRAIN_CHAT) == 200

    kept = gw.store.get_semantic("user.pref.seat")
    assert kept is not None and json.loads(kept["value_json"]) == "window, facing forward"
    assert "user.pref.carriage" not in _semantic_keys(gw.store), "the chat's own fact stayed"


@pytest.mark.asyncio
async def test_a_fact_the_deleted_chat_had_replaced_is_live_again(gw) -> None:
    """The trip chat said the old fact was out of date and replaced it. Deleting that chat brings
    the fact back as it was, rather than leaving it retired toward a fact that is gone."""
    _kept_chat(gw, TRIP_CHAT)
    with _scoped(TRAIN_CHAT):
        assert gw.store.set_semantic("user.fact.home_station", "Northgate", 0.9, "c") is None
    with _scoped(TRIP_CHAT):
        assert gw.store.set_semantic("user.fact.station", "Riverside", 0.9, "c") is None
        assert gw.store.supersede_semantic("user.fact.home_station", "user.fact.station", "c")
    assert gw.store.get_semantic("user.fact.home_station") is None

    assert await _press_delete(gw.state, TRIP_CHAT) == 200

    back = gw.store.get_semantic("user.fact.home_station")
    assert back is not None and json.loads(back["value_json"]) == "Northgate"
    assert "user.fact.station" not in _semantic_keys(gw.store)


@pytest.mark.asyncio
async def test_what_consolidations_maintenance_writes_is_no_one_chats(gw) -> None:
    """The maintenance a chat's consolidation runs works over every chat's memory: the digest it
    builds of another chat's day is filed under no chat, and deleting the chat that happened to
    run it keeps that digest while its own episode goes."""
    bike, _ = _chat(gw, BIKE)
    with _scoped(bike):
        assert gw.store.write_episodic(BIKE_EPISODE, conversation_id=bike)
    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
    gw.store.db.execute("UPDATE episodic_memories SET created_at = ?", (yesterday,))
    gw.store.db.commit()
    ski, ski_chat = _chat(gw, SKI)
    assert await gw.consolidator.consolidate_session(ski)
    digests = gw.store.db.execute(
        "SELECT text, source_session FROM episodic_memories WHERE conversation_id LIKE 'daily-%'"
    ).fetchall()
    assert len(digests) == 1 and BIKE_EPISODE in digests[0]["text"]
    assert digests[0]["source_session"] is None, "the digest was filed under the chat that ran it"

    assert await _delete(gw, ski_chat.key) == 200

    assert SKI_EPISODE not in _all_rows(gw.store)
    texts = [d["text"] for d in MemoryService.over_vector_store(gw.store).daily_digests()]
    assert len(texts) == 1 and BIKE_EPISODE in texts[0]


def test_what_an_earlier_version_filed_under_a_chat_from_its_maintenance_is_no_one_chats(
    tmp_path,
) -> None:
    """An earlier version ran the maintenance as the chat's work: a promoted pattern, a collapsed
    run of tool failures and a day's digest were filed under that chat. Opening the store files
    them under none, and leaves the chat's own records filed under it."""
    path = tmp_path / "memory.db"
    store = VectorMemoryStore(db_path=path, confidence_threshold=0.0)
    store.init()
    with _scoped(TRAIN_CHAT):
        assert store.set_semantic("pref.general", "window seats", 0.9, "promotion") is None
        assert (
            store.set_semantic("user.procedural.synth.ab12", "slow", 0.9, "failure_synthesis")
            is None
        )
        assert store.set_semantic("user.pref.seat", "window", 0.9, "consolidation:x") is None
        assert store.write_episodic(
            "Daily digest for 2026-09-01: two memory events on the train trip.",
            conversation_id="daily-digest:2026-09-01",
        )
    store.db.execute("DELETE FROM schema_version WHERE version >= 13")
    store.db.commit()
    store.close()

    store = VectorMemoryStore(db_path=path, confidence_threshold=0.0)
    store.init()
    try:
        filed = {
            str(r[0]): r[1]
            for r in store.db.execute("SELECT key, source_session FROM semantic_memory")
        }
        assert filed == {
            "pref.general": None,
            "user.procedural.synth.ab12": None,
            "user.pref.seat": TRAIN_CHAT,
        }
        digest = store.db.execute(
            "SELECT source_session FROM episodic_memories WHERE conversation_id LIKE 'daily-%'"
        ).fetchone()
        assert digest[0] is None
    finally:
        store.close()


def test_a_record_other_work_also_stands_behind_is_filed_under_no_one_chat(tmp_path) -> None:
    """The provenance the deletion reads: a record is filed under the one session whose work
    wrote it, and under none once other work writes it again or finds it already holds it."""
    from personalclaw import memory_formation as mf
    from personalclaw.vector_memory import SHARED

    store = VectorMemoryStore(db_path=tmp_path / "memory.db", confidence_threshold=0.0)
    store.init()

    def filed(table: str, column: str, ref: str) -> object:
        sql = f"SELECT source_session FROM {table} WHERE {column} = ?"
        return store.db.execute(sql, (ref,)).fetchone()[0]

    try:
        with _scoped(TRAIN_CHAT):
            assert store.write_lesson(BOTH, "preference")
            assert store.set_semantic("user.pref.seat", "window", 0.9, "c") is None
            assert store.set_semantic("project.trip.dates", "February", 0.9, "c") is None
            assert store.write_episodic(SKI_EPISODE, conversation_id=TRAIN_CHAT)
            # Its own work again changes nothing.
            assert not store.write_lesson(BOTH, "preference")
            assert store.set_semantic("user.pref.seat", "window", 0.9, "c") is None
        lesson = next(r["key"] for r in store.get_lessons())
        episode = store.db.execute("SELECT id FROM episodic_memories").fetchone()[0]
        assert filed("semantic_memory", "key", lesson) == TRAIN_CHAT
        assert filed("semantic_memory", "key", "user.pref.seat") == TRAIN_CHAT
        assert filed("episodic_memories", "id", episode) == TRAIN_CHAT
        with _scoped(TRIP_CHAT):
            assert not store.write_lesson(BOTH, "preference"), "a lesson said again"
            assert not store.write_episodic(SKI_EPISODE, conversation_id=TRIP_CHAT)
            candidates = mf.gather(
                store, [mf.Candidate(index=0, key="project.trip.dates", value="February")]
            )
            mf.apply_decisions(
                store, candidates, {0: mf.Decision(index=0, verdict=mf.VERDICT_NOOP)}, source="c"
            )
        # Her own edit, outside any chat.
        assert store.set_semantic("user.pref.seat", "window seat", 1.0, "user_explicit") is None
        assert filed("semantic_memory", "key", lesson) == SHARED
        assert filed("episodic_memories", "id", episode) == SHARED
        assert filed("semantic_memory", "key", "project.trip.dates") == SHARED
        assert filed("semantic_memory", "key", "user.pref.seat") == SHARED
    finally:
        store.close()


@pytest.mark.asyncio
async def test_the_memory_vault_keeps_no_page_of_what_a_deleted_chat_left(
    gw, tmp_path, monkeypatch
) -> None:
    """With the vault on, memory is also written out as pages: the deleted chat's go too."""
    from personalclaw import memory_vault

    vault = tmp_path / "vault"
    monkeypatch.setattr(memory_vault, "vault_mode_from_config", lambda: "mirror")
    monkeypatch.setattr(memory_vault, "vault_path_from_config", lambda: vault)
    ski, ski_chat = _chat(gw, SKI)
    bike, _ = _chat(gw, BIKE)
    assert await gw.consolidator.consolidate_session(ski)
    assert await gw.consolidator.consolidate_session(bike)

    def pages() -> str:
        return "\n".join(p.read_text(encoding="utf-8") for p in sorted(vault.rglob("*.md")))

    assert SKI_EPISODE in pages() and BIKE_EPISODE in pages()

    assert await _delete(gw, ski_chat.key) == 200

    for text in (SKI_EPISODE, SKI_FACT, SKI_SUMMARY):
        assert text not in pages(), f"a vault page still holds {text!r}"
    assert BIKE_EPISODE in pages()
