"""An Incognito or Temporary chat's words are kept nowhere but its transcript, the learning log too.

The defect this pins: every turn that surfaced a skill wrote one row per offered skill to the
learning log's ``surfacing_events`` table, and each row held the turn's message word for word
(``query``) beside the chat it came from (``session``). Nothing asked the chat's mode, so every
Incognito chat that surfaced a skill left its message there for ninety days, and in the learning
log's shards, which a backup and a sync carry, though the chat promises that nothing from it is
written back and only its transcript is kept.

The behaviour now, driven through the real turn engine over a fake model:

* a turn of an Incognito or Temporary chat records nothing in the surfacing log, so neither
  ``learning.db`` nor its shards hold any of its words, while a persistent chat's turn records its
  offers as before (the positive control every negative here stands on);
* nor does any other work that may change none of your memory: a turn someone else asked for;
* what an earlier version kept of such a chat's turns is removed when the gateway starts, while a
  row of a kept chat, or one that names no chat, stays;
* deleting a chat removes what the learning log kept of its turns, with the rest of its history.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import memory_writes
from personalclaw.config import loader as config_loader
from personalclaw.learning.surfacing_events import SurfacingEvent, SurfacingEventStore
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK
from personalclaw.llm.events import AgentEvent

#: What she writes. Invented content, in the shape of a garden plan.
_ASKED = "Plan the courgette beds behind the shed for Saturday morning"
#: The words of it no other text in the test holds.
_MARKER = "courgette beds behind the shed"

#: A skill her message surfaces by its trigger words.
_SKILL = """---
name: garden-beds
description: How to lay out vegetable beds in a small garden.
triggers: courgette beds, raised beds
---
# Garden beds
Measure the plot, mark the paths, then dig one bed at a time.
"""

INCOGNITO = "dashboard:chat-41-1790800000"
TEMPORARY = "dashboard:chat-42-1790800100"
KEPT = "dashboard:chat-43-1790800200"


class _ChatModel:
    """The chat's own model: it reads the turn and answers in one line."""

    supports_tools = True
    _model = "tiny"
    served_ref = "here:tiny"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.prompts.append(str(messages))
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Two beds fit; start with the sunnier one.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _turn_state(tmp_path: Path, runtime):
    """A dashboard whose chats run on ``runtime`` with the real turn engine, over a skills
    library holding the one skill her message surfaces."""
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    skill = tmp_path / "skills" / "garden-beds"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    await runtime.start()
    runtime.set_approval_policy("auto")
    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    sessions.record_failure = AsyncMock()
    log = ConversationLog()
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


async def _turn(tmp_path: Path, name: str, mode: str) -> _ChatModel:
    """One turn of the chat *name* in *mode*, her message the one that surfaces the skill."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.dashboard.chat_runner import run_chat

    model = _ChatModel()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="tiny"),
        model_provider=model,
        tool_providers=[],
    )
    state = await _turn_state(tmp_path, runtime)
    session = state.get_or_create_session(name, memory_mode=mode)
    session.append("user", _ASKED, "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, _ASKED)
    return model


def _events() -> list[SurfacingEvent]:
    store = SurfacingEventStore()
    try:
        return store.read(days=None)
    finally:
        store.close()


def _learning_log_bytes() -> bytes:
    """Every byte the learning log keeps on disk: the database and its write-ahead log."""
    home = config_loader.config_dir()
    return b"".join(
        path.read_bytes()
        for path in (home / "learning.db", home / "learning.db-wal")
        if path.is_file()
    )


def _shards(tmp_path: Path) -> bytes:
    """The learning log's shards, exported as the hourly export writes them."""
    from personalclaw.durability import shards

    out = tmp_path / "shards"
    shards.export_shards(config_loader.config_dir(), out, entries=["learning_db"])
    return b"\n".join(p.read_bytes() for p in sorted(out.rglob("*.jsonl")))


# ── a turn of a chat that keeps nothing ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("name, mode", [(INCOGNITO, "incognito"), (TEMPORARY, "temporary")])
async def test_a_private_turn_that_surfaces_a_skill_keeps_none_of_its_words(tmp_path, name, mode):
    """🔴 Red before the fix: the turn's message was kept in ``surfacing_events.query``, in
    ``learning.db`` and in the learning log's shards."""
    model = await _turn(tmp_path, name.removeprefix("dashboard:"), mode)

    assert model.prompts and _MARKER in model.prompts[-1], "premise: the turn ran on its message"
    assert "[Skill: garden-beds]" in model.prompts[-1], "premise: her message surfaced the skill"
    assert _events() == [], "the surfacing log recorded a turn of a chat that keeps nothing"
    assert _MARKER.encode() not in _learning_log_bytes()
    assert _MARKER.encode() not in _shards(tmp_path)


@pytest.mark.asyncio
async def test_a_kept_chats_turn_still_records_what_it_was_offered(tmp_path):
    """The positive control: the same turn in a persistent chat lands its row, words and all, and
    the shards carry it, so the negatives above can see what they say is absent."""
    await _turn(tmp_path, KEPT.removeprefix("dashboard:"), "persistent")

    (event,) = _events()
    assert (event.entity, event.arm, event.used) == ("garden-beds", "skill_surfaced", True)
    assert event.query == _ASKED, "the benchmark miner reads a kept chat's message"
    assert _MARKER.encode() in _learning_log_bytes()
    assert _MARKER.encode() in _shards(tmp_path)


def _offer(session: str, query: str = _ASKED) -> SurfacingEvent:
    return SurfacingEvent(
        kind="skill",
        entity="garden-beds",
        arm="skill_surfaced",
        confidence=0.9,
        used=True,
        query=query,
        session=session,
    )


def _colleague() -> dict[str, str]:
    from personalclaw.turn_source import arrived_on

    return arrived_on("1790800300.000100", "U0RIVERCOL", "teamchat")


@pytest.mark.parametrize("why", ["incognito", "temporary", "someone else asked"])
def test_no_work_that_may_change_none_of_your_memory_records_an_offer(why):
    """The store refuses for every kind of work that may change none of your memory, as the
    memory reflex's own offer log does: it is asked once, where every writer of the log writes."""
    store = SurfacingEventStore()
    try:
        with memory_writes.derived_from(
            KEPT, memory_mode=why if why in ("incognito", "temporary") else "persistent"
        ):
            if why == "someone else asked":
                memory_writes.asked_for(_colleague())
            assert memory_writes.changes_no_memory(), "premise: the work keeps nothing"
            assert store.record([_offer(KEPT)]) == 0
        # The control, through the same store: her own turn of a kept chat is recorded.
        with memory_writes.derived_from(KEPT, memory_mode="persistent"):
            memory_writes.asked_for({})
            assert store.record([_offer(KEPT)]) == 1
        assert [e.session for e in store.read(days=None)] == [KEPT]
    finally:
        store.close()


# ── what an earlier version kept ────────────────────────────────────────────────────────────


def _chat(key: str, mode: str | None) -> None:
    """A chat's transcript as the dashboard saves it, its mode in its metadata."""
    from personalclaw.history import ConversationLog

    log = ConversationLog()
    log.append(key, "user", _ASKED)
    log.append(key, "assistant", "Two beds fit; start with the sunnier one.")
    if mode is not None:
        log.update_metadata(key, {"memory_mode": mode})


def test_what_an_earlier_version_kept_of_a_private_chats_turns_is_removed_at_start(tmp_path):
    """🔴 Red before the fix: nothing removed the rows an earlier version wrote. They are found by
    the mode the chat's transcript records, as the start's sweep of memory finds what such a chat
    left there: a kept chat's row stays, and so does one whose chat no transcript names."""
    from personalclaw.learning.surfacing_events import forget_what_restricted_sessions_left

    _chat(INCOGNITO, "incognito")
    _chat(TEMPORARY, "temporary")
    _chat(KEPT, "persistent")
    store = SurfacingEventStore()
    try:
        store.record(
            [
                _offer(INCOGNITO),
                _offer(TEMPORARY),
                _offer(KEPT, "Which beds get the most sun?"),
                _offer("", "a row that names no chat"),
                _offer("dashboard:chat-44-1790800400", "a chat no transcript names"),
            ]
        )
    finally:
        store.close()
    assert _MARKER.encode() in _shards(tmp_path / "before"), "premise: the shards carried it"
    assert _MARKER.encode() in _learning_log_bytes(), "premise: the file held it"

    assert forget_what_restricted_sessions_left() == 2

    left = {e.session: e.query for e in _events()}
    assert left == {
        KEPT: "Which beds get the most sun?",
        "": "a row that names no chat",
        "dashboard:chat-44-1790800400": "a chat no transcript names",
    }
    assert _MARKER.encode() not in _shards(tmp_path / "after")
    # Not left in the file's free space either, which a backup's or a sync's copy of the
    # database would carry.
    assert _MARKER.encode() not in _learning_log_bytes()
    assert forget_what_restricted_sessions_left() == 0, "a second sweep finds nothing"


def test_the_gateway_start_sweeps_the_learning_log_before_anything_reads_it():
    """The sweep is reached from production: the start runs it beside the sweep of memory."""
    import inspect

    from personalclaw import gateway

    source = inspect.getsource(gateway)
    memory = source.index("forget_what_restricted_sessions_left(self.vector_memory, memory)")
    learning = source.index("surfacing_events.forget_what_restricted_sessions_left()")
    assert 0 < learning - memory < 600, "the learning log's sweep left the start's memory sweep"


# ── deleting a chat ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_chat_removes_what_the_learning_log_kept_of_its_turns(tmp_path):
    """🔴 Red before the fix: a deleted chat's messages stayed in the learning log for ninety
    days, though the Delete dialog says its history is permanently removed. Another chat's stay."""
    from personalclaw.dashboard.chat_forget import delete_chats

    model = await _turn(tmp_path, KEPT.removeprefix("dashboard:"), "persistent")
    assert model.prompts, "premise: the turn ran"
    other = SurfacingEventStore()
    try:
        other.record([_offer("dashboard:chat-45-1790800500", "Which beds get the most sun?")])
    finally:
        other.close()
    assert {e.session for e in _events()} == {KEPT, "dashboard:chat-45-1790800500"}
    state = _deleting_state()

    deleted = await delete_chats(state, [KEPT.removeprefix("dashboard:")], by="dashboard")

    assert deleted.deleted == (KEPT.removeprefix("dashboard:"),)
    assert [e.session for e in _events()] == ["dashboard:chat-45-1790800500"]
    assert _MARKER.encode() not in _shards(tmp_path)


#: A lesson a chat's turn taught, as the after-turn review captures it, and another chat's.
_LESSON = "Give every distance on the allotment walk in kilometres"
_LESSON_MARK = b"allotment walk in kilometres"
_OTHERS_LESSON = "Write dates in the garden diary day first"


def _capture(session: str, lesson: str) -> None:
    from personalclaw.learning.staging import StagingStore

    store = StagingStore()
    try:
        assert store.stage(cadence="per_turn", kind="lesson", content=lesson, session_key=session)
    finally:
        store.close()


def _captures() -> dict[str, str | None]:
    """Each capture the learning log keeps, by its words: the batch it was compiled into, if any."""
    with sqlite3.connect(config_loader.config_dir() / "learning.db") as db:
        return dict(db.execute("SELECT content, consumed_by FROM staging").fetchall())


async def _drain() -> list[str]:
    """What the drain files when a model comes back: the bodies of the pending lesson batches."""
    from personalclaw.learning import proposals, staging
    from personalclaw.resilience.degraded import _memory_staging_drain

    staging.reset_store()  # the shared store over this test's home, not one an earlier test left
    try:
        await _memory_staging_drain()
    finally:
        staging.reset_store()
    return [p.body for p in proposals.list_pending()]


@pytest.mark.asyncio
async def test_deleting_a_chat_removes_the_lessons_it_captured(tmp_path):
    """The after-turn review captures the lesson a turn taught under its chat, for the next batch
    pass. 🔴 Red before the fix: deleting the chat left the capture, which no prune removes while it
    waits, and which every backup and export carried, and the next drain offered to keep it again.
    Another chat's capture stays."""
    from personalclaw.dashboard.chat_forget import delete_chats

    _chat(KEPT, None)
    _capture(KEPT, _LESSON)
    _capture("dashboard:chat-45-1790800500", _OTHERS_LESSON)

    deleted = await delete_chats(_deleting_state(), [KEPT.removeprefix("dashboard:")], by="you")

    assert deleted.deleted == (KEPT.removeprefix("dashboard:"),)
    assert _captures() == {_OTHERS_LESSON: None}
    assert _LESSON_MARK not in _learning_log_bytes()
    assert _LESSON_MARK not in _shards(tmp_path)
    (body,) = await _drain()
    assert _LESSON not in body and _OTHERS_LESSON in body


@pytest.mark.asyncio
async def test_a_lesson_batch_compiled_from_a_deleted_chat_is_withdrawn_and_the_rest_filed_again():
    """A capture already compiled into a pending lesson batch is quoted in its body and its Inbox
    row. Deleting its chat withdraws the batch, with no decision recorded, and gives its other
    captures back to the queue: the next drain files them again, and nothing quotes the deleted
    chat's lesson. 🔴 Red before the fix: the batch stayed, and Accept would keep the lesson."""
    from personalclaw.dashboard.chat_forget import delete_chats
    from personalclaw.inbox import InboxStore
    from personalclaw.learning import proposals

    _chat(KEPT, None)
    _capture(KEPT, _LESSON)
    _capture("dashboard:chat-45-1790800500", _OTHERS_LESSON)
    (compiled,) = await _drain()
    assert _LESSON in compiled and _OTHERS_LESSON in compiled, "premise: one batch holds both"
    assert all(_captures().values()), "premise: both captures are compiled"

    await delete_chats(_deleting_state(), [KEPT.removeprefix("dashboard:")], by="you")

    assert proposals.list_pending() == []
    assert proposals.load_decisions() == {}, "withdrawn, not rejected"
    assert _captures() == {_OTHERS_LESSON: None}
    inbox = InboxStore()
    inbox.load()
    assert _LESSON not in json.dumps([item.to_dict() for item in inbox.items.values()])
    (refiled,) = await _drain()
    assert _LESSON not in refiled and _OTHERS_LESSON in refiled


@pytest.mark.asyncio
async def test_a_superseded_batch_naming_a_deleted_chat_goes_and_gives_nothing_back():
    """A superseded batch is kept for lineage and names captures its replacement may hold. Deleting
    the chat removes it, and its other captures stay with the batch that holds them now: given back,
    the next drain would file their lessons a second time."""
    import dataclasses

    from personalclaw.dashboard.chat_forget import delete_chats
    from personalclaw.learning import proposals, staging

    _chat(KEPT, None)
    _capture(KEPT, _LESSON)
    _capture("dashboard:chat-45-1790800500", _OTHERS_LESSON)
    (_compiled,) = await _drain()
    (first,) = proposals.list_pending()
    with sqlite3.connect(config_loader.config_dir() / "learning.db") as db:
        ((kept_uid,),) = db.execute("SELECT uid FROM staging WHERE content = ?", (_OTHERS_LESSON,))
    # Its replacement holds the other chat's capture now, and the first is kept as superseded.
    second = dataclasses.replace(
        first, id=f"{first.id}-next", staging_refs=[kept_uid], supersedes=first.id
    )
    assert proposals._save(second)
    assert proposals._save(dataclasses.replace(first, status=proposals.Status.SUPERSEDED.value))
    with sqlite3.connect(config_loader.config_dir() / "learning.db") as db:
        db.execute(
            "UPDATE staging SET consumed_by = ? WHERE content = ?",
            (staging.compiled_by(second.id), _OTHERS_LESSON),
        )

    await delete_chats(_deleting_state(), [KEPT.removeprefix("dashboard:")], by="you")

    assert proposals.get(first.id) is None, "the superseded record that named it is gone"
    assert _captures() == {_OTHERS_LESSON: staging.compiled_by(second.id)}
    assert [p.id for p in proposals.list_pending()] == [second.id]
    await _drain()
    assert [p.id for p in proposals.list_pending()] == [second.id], "filed once, not twice"


def _deleting_state() -> Any:
    """A gateway's chat state over the test's home, its transcripts where a real one keeps them."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.destroy = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(sessions=sessions, start_time=0.0, conversation_log=ConversationLog())


def test_a_reader_holding_the_log_open_does_not_hold_up_a_delete():
    """A chat is deleted on the gateway's loop, so folding the log back into the file waits for
    a reader only a moment: the rows still go at once, and the fold happens at a later checkpoint.
    Waiting as long as the connection otherwise would (five seconds) held every other request."""
    import time

    store = SurfacingEventStore()
    try:
        store.record([_offer(INCOGNITO)])
    finally:
        store.close()
    reader = sqlite3.connect(config_loader.config_dir() / "learning.db")
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM surfacing_events").fetchone() == (1,)
        started = time.monotonic()
        forgetting = SurfacingEventStore()
        try:
            assert forgetting.forget_sessions({INCOGNITO}.__contains__) == 1
        finally:
            forgetting.close()
        assert time.monotonic() - started < 4.0, "the delete waited out the reader"
    finally:
        reader.rollback()
        reader.close()
    assert _events() == []


# ── a chat that keeps nothing is called by its mode ─────────────────────────────────────────


def _saved_before_its_title(mode: str):
    """A chat in *mode* saved after its first turn and before its title chore ran, as a gateway
    that stopped or died in between leaves it. Returns the session."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history
    from personalclaw.dashboard.chat_utils import _history_key_for

    stopped = _deleting_state()
    session = stopped.get_or_create_session(
        name=None, memory_mode=None if mode == "persistent" else mode
    )
    session.append("user", _ASKED, broadcast=False)
    session.append("assistant", "Two beds fit; start with the sunnier one.", broadcast=False)
    session.drain()
    save_session_to_history(stopped, session, force=True)
    meta = stopped.conversation_log.get_metadata(_history_key_for(session.key))
    assert not meta.get("title"), "premise: saved before its title chore ran"
    return session


def test_a_private_chat_that_comes_back_untitled_is_called_by_its_mode(caplog):
    """🔴 Red before the fix: the chat came back named by the chat list's fallback, its first
    message, in its header and tab, in the next save's record and in the log's restore line."""
    import logging

    from personalclaw.dashboard.chat_persistence import restore_recent_sessions

    session = _saved_before_its_title("incognito")
    started = _deleting_state()
    with caplog.at_level(logging.INFO, logger="personalclaw.dashboard.chat_persistence"):
        restore_recent_sessions(started, window_minutes=0)

    restored = started._sessions[session.key]
    assert restored.memory_mode == "incognito", "premise: it came back as itself"
    assert restored.title == "Incognito chat"
    assert _MARKER not in caplog.text, "the restore's log line named the chat by its words"


def test_a_kept_chat_that_comes_back_untitled_is_still_called_by_its_first_message():
    """The control: a persistent chat keeps the chat list's fallback until a title is made."""
    from personalclaw.dashboard.chat_persistence import restore_recent_sessions

    session = _saved_before_its_title("persistent")
    started = _deleting_state()
    restore_recent_sessions(started, window_minutes=0)

    assert _MARKER in started._sessions[session.key].title


def test_the_learning_log_is_read_with_plain_sqlite_where_the_store_writes_it():
    """The byte reads above read the file the store writes: a check of another file reads empty
    and passes for the wrong reason."""
    store = SurfacingEventStore()
    try:
        assert store.path == config_loader.config_dir() / "learning.db"
        store.record([_offer(KEPT)])
    finally:
        store.close()
    with sqlite3.connect(config_loader.config_dir() / "learning.db") as conn:
        assert conn.execute("SELECT query FROM surfacing_events").fetchall() == [(_ASKED,)]
