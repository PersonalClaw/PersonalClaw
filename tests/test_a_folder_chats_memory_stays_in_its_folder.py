"""A chat working in a folder keeps its memory in that folder's, and no other chat recalls it.

Memory is partitioned by the folder a chat works in, and every turn of a folder chat reads that
folder's partition. The defect these tests pin: the consolidation pass looked the chat's folder up
under a metadata key no chat writes (a chat records it as ``workspace_dir``), and wrote what it
kept through the global memory's own store in any case. So everything it kept from a folder chat
(the session summary, the facts, the episodes, the lessons) went into the global memory, where
every other chat recalled it and the folder chat itself never read it again.

Asserted against the real stores, and the real save, consolidation and recall paths:

* a folder chat's consolidation lands in that folder's partition, and a global chat's (no folder,
  or the gateway's own workspace) in the global memory, also in a process with no context builder
  (``personalclaw consolidate``);
* a folder chat's memory is not recalled in a chat working in another folder, nor in a global
  chat, and is recalled in another chat in the same folder; the global memory still reaches every
  chat, labeled as coming from outside the folder;
* a lesson consolidation keeps from a folder chat applies in that folder only, and the lessons
  taught for everywhere, or for that folder, apply in a folder chat too;
* ``memory_recall`` answers from the asking chat's folder first, then from the global memory;
* the after-turn review keeps what it learns in a folder chat's partition before any embedding
  model is bound;
* a chat whose folder was one of PersonalClaw's own and is gone keeps nothing more;
* what an earlier version filed in the global memory for a folder chat (its episodes and its
  session summary) moves to the folder's partition when the gateway starts, once; a folder whose
  memory cannot take it leaves it where it was until a later start can.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import memory_locality, memory_writes
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.context_engine import assemble_context
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.memory import MemoryStore
from personalclaw.memory_service import MemoryService, normalize_workspace_ref, service_for
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore

#: What the consolidation model keeps from a chat about the garden notes. Invented content.
GARDEN = {
    "history_entry": "Planned the spring beds from the garden notes; seed order moves to March.",
    "episodic": [
        {
            "text": "The seed order for the raised beds goes out in the first week of March.",
            "tags": [],
        }
    ],
    "semantic": [
        {
            "key": "user.project.garden_beds",
            "value": "four raised beds by the fence",
            "confidence": 0.9,
        }
    ],
    "lessons": [
        {
            "rule": "Keep the planting plan as a table of bed, crop and week.",
            "category": "preference",
        }
    ],
}
#: What it keeps from a chat about dinner, which works in no folder.
DINNER = {
    "history_entry": "Planned Saturday's dinner: lentil soup with flatbread for six.",
    "episodic": [
        {"text": "Saturday's dinner is lentil soup with flatbread for six guests.", "tags": []}
    ],
    "semantic": [
        {"key": "user.pref.dinner_style", "value": "simple vegetarian dishes", "confidence": 0.9}
    ],
}

GARDEN_EPISODE = GARDEN["episodic"][0]["text"]
GARDEN_FACT = "four raised beds by the fence"
GARDEN_LESSON = GARDEN["lessons"][0]["rule"]
DINNER_EPISODE = DINNER["episodic"][0]["text"]
#: What an earlier version kept from a chat working in the tax papers folder.
TAXES_EPISODE = "The receipts for the tax year are filed by month in the blue folder."
TAXES_SUMMARY = "Sorted the year's receipts by month."


@pytest.fixture
def gw(tmp_path: Path):
    """A gateway's memory wiring over the test's home: the chat state that saves transcripts,
    the global memory (markdown and memory database) the context builder and the consolidator
    share, and a consolidation model that answers each chat with what it keeps from it."""
    garden = tmp_path / "garden-notes"
    taxes = tmp_path / "tax-papers"
    garden.mkdir()
    taxes.mkdir()
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
    model = AsyncMock(side_effect=lambda _prompt, key: json.loads(json.dumps(answers.get(key, {}))))
    consolidator._call_llm = model  # type: ignore[method-assign]
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        context_builder=builder,
        conversation_log=log,
        consolidator=consolidator,
    )
    yield SimpleNamespace(
        garden=str(garden),
        taxes=str(taxes),
        log=log,
        main=main,
        store=store,
        builder=builder,
        consolidator=consolidator,
        model=model,
        answers=answers,
        state=state,
    )
    store.close()


def _chat(gw: SimpleNamespace, folder: str, answer: dict | None = None) -> tuple[str, object]:
    """A chat working in *folder*, saved as the dashboard saves a running chat, whose
    consolidation the model answers with *answer*. Returns ``(history key, session)``."""
    session = gw.state.get_or_create_session(name=None, workspace_dir=folder)
    session.append("user", "Here is what I worked out today.", broadcast=False)
    session.append("assistant", "Noted, I will keep it in mind.", broadcast=False)
    session.drain()
    save_session_to_history(gw.state, session, force=True)
    key = _history_key_for(session.key)
    gw.answers[key] = answer or {}
    return key, session


def _episodes(store: VectorMemoryStore) -> list[str]:
    rows = store.db.execute("SELECT text FROM episodic_memories WHERE is_deleted = 0").fetchall()
    return [str(r["text"]) for r in rows]


def _everything(store: VectorMemoryStore) -> str:
    """Every live record's text in *store*: its episodes and its semantic rows' values."""
    rows = store.db.execute("SELECT value_json FROM semantic_memory WHERE is_deleted = 0")
    return "\n".join(_episodes(store) + [str(r["value_json"]) for r in rows.fetchall()])


def _daily_history(memory: MemoryStore) -> str:
    folder = memory._history_dir
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md")))


async def _turn_context(gw: SimpleNamespace, key: str, folder: str, text: str) -> str:
    """The prompt the first turn of chat *key*, working in *folder*, is assembled with."""
    assembled = assemble_context(
        gw.builder,
        text,
        is_new_session=True,
        session_key=key,
        cwd=folder or None,
        memory_store=None,
    )
    return assembled.message


# ── where consolidation keeps a chat's memory ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_folder_chats_consolidation_lands_in_that_folders_partition(gw) -> None:
    key, _session = _chat(gw, gw.garden, GARDEN)

    assert await gw.consolidator.consolidate_session(key)

    kept_globally = _everything(gw.store)
    for text in (GARDEN_EPISODE, GARDEN_FACT, GARDEN["history_entry"]):
        assert text not in kept_globally, f"the global memory kept {text!r} from a folder chat"
    assert GARDEN["history_entry"] not in _daily_history(gw.main)

    folder = ContextBuilder.get_memory_for(gw.garden)
    assert folder is not gw.main
    assert folder.vector_store is not None, "nothing was kept in the folder's partition"
    kept_there = _everything(folder.vector_store)
    assert GARDEN_EPISODE in kept_there
    assert GARDEN_FACT in kept_there
    # Sealed at the session's end: its summary became an episode of the folder's memory.
    assert GARDEN["history_entry"] in _episodes(folder.vector_store)
    assert GARDEN["history_entry"] in _daily_history(folder)
    assert folder.vector_store.db_path.parent == memory_locality.partition_for(gw.garden)


@pytest.mark.asyncio
async def test_a_global_chats_consolidation_lands_in_the_global_memory(gw) -> None:
    """A chat in no folder, and one in the gateway's own workspace, where every new chat starts,
    keep their memory in the global memory: the one the Memory page shows."""
    unfoldered, _ = _chat(gw, "", DINNER)
    workspace = config_loader.default_workspace_dir()
    assert workspace
    in_workspace, _ = _chat(gw, workspace, GARDEN)

    assert await gw.consolidator.consolidate_session(unfoldered)
    assert await gw.consolidator.consolidate_session(in_workspace)

    kept = _everything(gw.store)
    for text in (DINNER_EPISODE, GARDEN_EPISODE, GARDEN_FACT):
        assert text in kept
    assert DINNER["history_entry"] in _daily_history(gw.main)
    assert memory_locality.partition_for(workspace) == memory_locality.partition_for(None)
    assert not Path(config_loader.memory_dir_for_cwd(workspace)).exists()


@pytest.mark.asyncio
async def test_without_a_context_builder_the_workspace_chat_keeps_the_global_memory(gw) -> None:
    """``personalclaw consolidate`` runs with no context builder registering the global store:
    a chat in the gateway's workspace still keeps its memory in the consolidator's own store."""
    from personalclaw import context as context_mod

    workspace = config_loader.default_workspace_dir()
    key, _ = _chat(gw, workspace, DINNER)
    context_mod._memory_stores.clear()

    assert await gw.consolidator.consolidate_session(key)

    assert DINNER_EPISODE in _episodes(gw.store)
    assert not Path(config_loader.memory_dir_for_cwd(workspace)).exists()


@pytest.mark.asyncio
async def test_a_chat_whose_own_folder_is_gone_keeps_nothing_more(gw) -> None:
    """A task's worktree takes its memory with it when it goes; a pass over its chat after that
    neither brings the partition back nor files the chat in the global memory."""
    worktree = config_loader.config_dir() / "projects" / "p-1" / "worktrees" / "t-1"
    worktree.mkdir(parents=True)
    key, _ = _chat(gw, str(worktree), GARDEN)
    shutil.rmtree(worktree)

    await gw.consolidator.consolidate_session(key)

    assert gw.model.await_count == 0, "the transcript was handed to a model for nothing"
    assert not config_loader.memory_dir_for_cwd(str(worktree)).exists()
    assert GARDEN_EPISODE not in _everything(gw.store)


# ── what a chat recalls ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_folder_chats_memory_is_not_recalled_in_another_folders_chat(gw) -> None:
    garden, _ = _chat(gw, gw.garden, GARDEN)
    dinner, _ = _chat(gw, "", DINNER)
    assert await gw.consolidator.consolidate_session(garden)
    assert await gw.consolidator.consolidate_session(dinner)

    asked = "When does the seed order for the raised beds go out?"
    other_folder, _ = _chat(gw, gw.taxes)
    in_taxes = await _turn_context(gw, other_folder, gw.taxes, asked)
    no_folder, _ = _chat(gw, "")
    in_global = await _turn_context(gw, no_folder, "", asked)
    same_folder, _ = _chat(gw, gw.garden)
    in_garden = await _turn_context(gw, same_folder, gw.garden, asked)

    for context in (in_taxes, in_global):
        for text in (GARDEN_EPISODE, GARDEN_FACT, GARDEN["history_entry"]):
            assert text not in context, f"another chat recalled {text!r} from a folder chat"
    assert GARDEN_EPISODE in in_garden
    assert GARDEN_FACT in in_garden
    # The global memory still reaches a folder chat, said to come from outside its folder.
    dinner_asked = "What is for dinner on Saturday?"
    later_in_taxes, _ = _chat(gw, gw.taxes)
    for_dinner = await _turn_context(gw, later_in_taxes, gw.taxes, dinner_asked)
    assert DINNER_EPISODE in for_dinner
    assert memory_locality.CROSS_PARTITION_SOURCE in for_dinner
    later_in_global, _ = _chat(gw, "")
    assert DINNER_EPISODE in await _turn_context(gw, later_in_global, "", dinner_asked)


@pytest.mark.asyncio
async def test_memory_recall_reads_the_asking_chats_folder_first(gw) -> None:
    from personalclaw.dashboard.handlers.memory import api_memory_recall

    garden, _ = _chat(gw, gw.garden, GARDEN)
    dinner, _ = _chat(gw, "", DINNER)
    assert await gw.consolidator.consolidate_session(garden)
    assert await gw.consolidator.consolidate_session(dinner)

    app = web.Application()
    app["state"] = gw.state

    async def recall(caller: str, query: str) -> str:
        # The chat's memory tool, which presents the internal credential and names its chat.
        request = make_mocked_request(
            "GET",
            f"/api/memory/recall?{urlencode({'q': query})}",
            headers={"X-Internal-Secret": "the-gateways-own", "X-Session-Key": caller},
            app=app,
        )
        resp = await api_memory_recall(request)
        return json.loads(resp.body.decode())["result"]

    asker, _ = _chat(gw, gw.garden)
    from_garden = await recall(asker, "seed order raised beds lentil soup")
    assert GARDEN_EPISODE in from_garden
    assert DINNER_EPISODE in from_garden
    assert from_garden.index(GARDEN_EPISODE) < from_garden.index(DINNER_EPISODE)
    assert memory_locality.CROSS_PARTITION_SOURCE in from_garden

    elsewhere, _ = _chat(gw, gw.taxes)
    from_taxes = await recall(elsewhere, "seed order raised beds lentil soup")
    assert GARDEN_EPISODE not in from_taxes
    assert DINNER_EPISODE in from_taxes

    from_the_memory_page = await recall("dashboard:ui", "seed order raised beds")
    assert GARDEN_EPISODE not in from_the_memory_page


# ── lessons ─────────────────────────────────────────────────────────────────────────────────


def _every_lesson_is_followed() -> None:
    """Set the lesson confidence floor to 0 (``learning.min_lesson_confidence``). A lesson an
    inference pass observed once is held back below the default floor in every chat alike; with
    the floor at 0, which chats follow a lesson is decided by its reach alone."""
    config_loader.config_path().write_text(
        json.dumps({"learning": {"min_lesson_confidence": 0.0}}), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_a_folder_chats_lesson_applies_in_that_folder_only(gw) -> None:
    """Lessons live in the global memory's lesson list, each with the reach it was given: a
    lesson kept from a folder chat reaches that folder, where it is followed, and stays listed and
    removable in Settings, Memory, Lessons."""
    _every_lesson_is_followed()
    garden, _ = _chat(gw, gw.garden, GARDEN)
    assert await gw.consolidator.consolidate_session(garden)

    rows = [r for r in service_for(gw.main).get_lessons() if GARDEN_LESSON in r["value_json"]]
    assert len(rows) == 1, "the lesson is not in the global lesson list"
    assert rows[0]["scope"] == "workspace"
    assert rows[0]["scope_ref"] == normalize_workspace_ref(gw.garden)

    in_garden = gw.builder.build_session_context(session_key="dashboard:x", cwd=gw.garden)
    in_taxes = gw.builder.build_session_context(session_key="dashboard:y", cwd=gw.taxes)
    in_global = gw.builder.build_session_context(session_key="dashboard:z", cwd=None)
    assert GARDEN_LESSON in in_garden
    assert GARDEN_LESSON not in in_taxes
    assert GARDEN_LESSON not in in_global


def test_a_folder_chat_follows_the_lessons_for_everywhere_and_for_its_folder(gw) -> None:
    """A rule taught for every chat, and one taught for one folder, reach a chat working in that
    folder: it reads its own partition's lessons beside the global lesson list's."""
    lessons = service_for(gw.main)
    assert lessons.write_lesson("Give quantities in metric units.", source="user_explicit")
    from personalclaw.memory_record import MemoryScope

    assert lessons.write_lesson(
        "File receipts by tax year.",
        source="user_explicit",
        scope=MemoryScope.WORKSPACE,
        scope_ref=normalize_workspace_ref(gw.taxes),
    )

    in_taxes = gw.builder.build_session_context(session_key="dashboard:a", cwd=gw.taxes)
    in_garden = gw.builder.build_session_context(session_key="dashboard:b", cwd=gw.garden)

    assert "Give quantities in metric units." in in_taxes
    assert "Give quantities in metric units." in in_garden
    assert "File receipts by tax year." in in_taxes
    assert "File receipts by tax year." not in in_garden


# ── the after-turn review ───────────────────────────────────────────────────────────────────


def test_the_after_turn_review_keeps_a_folder_chats_learning_without_an_embedding_model(gw):
    from personalclaw.dashboard.chat_runner import _maybe_after_turn_review

    _key, session = _chat(gw, gw.garden)

    _maybe_after_turn_review(
        gw.state,
        session,
        user_message="please be more terse",
        assistant_text="Understood.",
        tool_calls=0,
    )

    folder = ContextBuilder.get_memory_for(gw.garden)
    assert folder.vector_store is not None, "the folder's partition had nowhere to keep it"
    facets = folder.vector_store.db.execute(
        "SELECT COUNT(*) FROM semantic_memory WHERE key LIKE 'pref.facet.%'"
    ).fetchone()[0]
    assert facets == 1
    assert "terse" not in _everything(gw.store)


# ── what an earlier version filed in the global memory ─────────────────────────────────────


def _filed_globally(gw: SimpleNamespace, key: str, episode: str, summary: str) -> None:
    """What an earlier version's consolidation of chat *key* left in the global memory."""
    svc = MemoryService.over_vector_store(gw.store)
    with memory_writes.derived_from(key, memory_mode="persistent"):
        assert svc.write_episodic(episode, conversation_id=key, source=f"consolidation:{key}")
        svc.write_working_memory(key, summary)
        assert (
            gw.store.set_semantic(
                f"user.note.n{hashlib.sha256(key.encode()).hexdigest()[:12]}",
                f"a fact from {summary}",
                0.9,
                f"consolidation:{key}",
            )
            is None
        )


def _values(store: VectorMemoryStore) -> list[str]:
    """Each live semantic row's value in *store*, as it was written."""
    rows = store.db.execute("SELECT value_json FROM semantic_memory WHERE is_deleted = 0")
    return [str(json.loads(r["value_json"])) for r in rows.fetchall()]


def _counts(store: VectorMemoryStore) -> tuple[int, int]:
    return (
        store.db.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0],
        store.db.execute("SELECT COUNT(*) FROM semantic_memory").fetchone()[0],
    )


def test_what_an_earlier_version_filed_globally_moves_to_the_folder_once(gw) -> None:
    garden, _ = _chat(gw, gw.garden)
    taxes, _ = _chat(gw, gw.taxes)
    dinner, _ = _chat(gw, "")
    worktree = config_loader.config_dir() / "code" / "worktrees" / "l-1" / "t-1"
    worktree.mkdir(parents=True)
    finished, _ = _chat(gw, str(worktree))
    shutil.rmtree(worktree)
    _filed_globally(gw, garden, GARDEN_EPISODE, GARDEN["history_entry"])
    _filed_globally(gw, taxes, TAXES_EPISODE, TAXES_SUMMARY)
    _filed_globally(gw, dinner, DINNER_EPISODE, DINNER["history_entry"])
    _filed_globally(gw, finished, "The task branch merged after its tests passed.", "Merged it.")
    before = {
        r["id"]: dict(r)
        for r in gw.store.db.execute(
            "SELECT * FROM episodic_memories WHERE conversation_id = ?", (garden,)
        )
    }

    moved = memory_locality.move_what_folder_chats_left(gw.store, gw.log)

    assert moved == 4, "each folder chat's episode and its session summary"
    episodes, values = _episodes(gw.store), _values(gw.store)
    assert GARDEN_EPISODE not in episodes and TAXES_EPISODE not in episodes
    assert GARDEN["history_entry"] not in values and TAXES_SUMMARY not in values
    assert f"a fact from {GARDEN['history_entry']}" in values, "a fact names only its last writer"
    assert DINNER_EPISODE in episodes and DINNER["history_entry"] in values
    assert "The task branch merged after its tests passed." in episodes
    assert not config_loader.memory_dir_for_cwd(str(worktree)).exists()

    folder = ContextBuilder.get_memory_for(gw.garden).vector_store
    assert folder is not None
    after = {
        r["id"]: dict(r)
        for r in folder.db.execute(
            "SELECT * FROM episodic_memories WHERE conversation_id = ?", (garden,)
        )
    }
    assert set(after) == set(before)
    for row_id, row in before.items():
        for column in ("text", "created_at", "embedding", "tags", "importance", "source_session"):
            assert after[row_id][column] == row[column], column
    assert service_for(ContextBuilder.get_memory_for(gw.garden)).working_memory(garden)
    other = ContextBuilder.get_memory_for(gw.taxes).vector_store
    assert other is not None and _episodes(other) == [TAXES_EPISODE]
    assert service_for(ContextBuilder.get_memory_for(gw.taxes)).working_memory(taxes)

    counts = (_counts(gw.store), _counts(folder), _counts(other))
    assert memory_locality.move_what_folder_chats_left(gw.store, gw.log) == 0
    assert (_counts(gw.store), _counts(folder), _counts(other)) == counts


def test_a_folder_whose_memory_cannot_take_its_records_keeps_them_until_it_can(
    gw, monkeypatch
) -> None:
    """A folder's memory that refuses what it is handed (its database locked, say) holds back
    neither the other folders' records nor the gateway: its own stay in the global memory, where
    its chat still reads them, and the next start moves them."""
    garden, _ = _chat(gw, gw.garden)
    taxes, _ = _chat(gw, gw.taxes)
    _filed_globally(gw, garden, GARDEN_EPISODE, GARDEN["history_entry"])
    _filed_globally(gw, taxes, TAXES_EPISODE, TAXES_SUMMARY)
    locked = ContextBuilder.get_memory_for(gw.garden, writes=True).vector_store
    assert locked is not None
    copy = VectorMemoryStore._copy_chat_records

    def refuse_the_locked_one(self, dest, chats):
        if dest is locked:
            raise sqlite3.OperationalError("database is locked")
        return copy(self, dest, chats)

    monkeypatch.setattr(VectorMemoryStore, "_copy_chat_records", refuse_the_locked_one)
    assert memory_locality.move_what_folder_chats_left(gw.store, gw.log) == 2
    assert GARDEN_EPISODE in _episodes(gw.store)
    assert TAXES_EPISODE not in _episodes(gw.store)
    assert _episodes(locked) == []

    monkeypatch.setattr(VectorMemoryStore, "_copy_chat_records", copy)
    assert memory_locality.move_what_folder_chats_left(gw.store, gw.log) == 2
    assert GARDEN_EPISODE not in _episodes(gw.store)
    assert _episodes(locked) == [GARDEN_EPISODE]
