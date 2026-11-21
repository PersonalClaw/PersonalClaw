"""After an /undo, a regenerate or an edit-and-resend, the messages sent next are consolidated into
memory, once each.

Measured before this was written: a chat's transcript records how many of its messages
consolidation has read (``last_consolidated``), and a pass reads only the messages after that
count. The three actions that rewrite a transcript save it with ``force``, and that save copied
the count from the old record. So a chat of 40 messages, all read, kept 40 after an /undo took it
to 36, and the next four messages, 40 again, were never read: what the owner said in them was not
learned. A pass that was waiting on its model when the transcript was rewritten marked the count it
had read when it ended, with the same effect.

Now a forced save lowers the count to where its messages first differ from the file's
(``chat_persistence._read_so_far``, ``transcript_rewrites.messages_unchanged``): the edited
message and each message sent after it are read by the next pass. A pass marks only the messages
it read that are still there (``ConversationLog.mark_consolidated``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.dashboard.chat import api_chat_session_edit_resend
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for

pytestmark = pytest.mark.asyncio

TURNS = 20  # 40 messages


def _user_time(i: int) -> str:
    return f"2026-06-30T05:{i:02d}:00+00:00"


def _seed(state, name: str = "trip"):
    """A kept chat of TURNS turns, saved, with every message read by consolidation."""
    session = state.get_or_create_session(name)
    for i in range(TURNS):
        session.append("user", f"q{i}", "msg msg-u", ts=_user_time(i), broadcast=False)
        session.append(
            "assistant", f"a{i}", "msg msg-a", ts=f"2026-06-30T05:{i:02d}:30+00:00", broadcast=False
        )
    session.drain()
    save_session_to_history(state, session)
    key = _history_key_for(session.key)
    state.conversation_log.mark_consolidated(key, 2 * TURNS)
    assert state.conversation_log.unconsolidated_count(key) == 0
    return session, key


def _say(state, session, *exchange: str) -> None:
    """The owner and the agent go on talking: each pair of *exchange* is a turn, saved."""
    for i in range(0, len(exchange), 2):
        session.append("user", exchange[i], "msg msg-u", broadcast=False)
        session.append("assistant", exchange[i + 1], "msg msg-a", broadcast=False)
    save_session_to_history(state, session)


def _consolidated(state, key: str) -> int:
    return state.conversation_log.get_metadata(key)["last_consolidated"]


def _unread_words(state, key: str) -> list[str]:
    unread, _total = state.conversation_log.get_unconsolidated(key)
    return [m["content"] for m in unread]


def _chat_app(state) -> web.Application:
    app = _make_app(state)
    app.router.add_post("/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend)
    return app


async def _no_turn(*_args, **_kwargs) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_model_turn(monkeypatch, tmp_path):
    """A regenerate and a resend start a turn: here it runs nothing, and the test says what the
    agent answered."""
    monkeypatch.setattr("personalclaw.dashboard.chat_regenerate.run_chat", _no_turn)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


# ── a forced save lowers the count to where the transcript changed ───────────────────────────


async def test_an_undo_lowers_the_consolidated_count(tmp_path):
    """🔴 Red before: the count stayed 40 after the undo, and the four messages sent next were
    never read."""
    state = _make_state(tmp_path)
    session, key = _seed(state)

    async with TestClient(TestServer(_chat_app(state))) as client:
        undone = await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": 2})
        assert undone.status == 200, await undone.text()

    assert len(state.conversation_log.read_messages(key)) == 36
    assert _consolidated(state, key) == 36

    _say(state, session, "I moved to Porto.", "Noted.", "I prefer the window seat.", "Noted.")
    assert _unread_words(state, key) == [
        "I moved to Porto.",
        "Noted.",
        "I prefer the window seat.",
        "Noted.",
    ]


async def test_a_regenerate_lowers_the_count_to_the_message_it_answers_again(tmp_path):
    """🔴 Red before: the count stayed 40, so the new answer, the 40th message again, was never
    read."""
    state = _make_state(tmp_path)
    session, key = _seed(state)

    async with TestClient(TestServer(_chat_app(state))) as client:
        again = await client.post(f"/api/chat/sessions/{session.key}/regenerate", json={})
        assert again.status == 200, await again.text()

    # The question it answers again stays, and was read; its old answer went.
    assert _consolidated(state, key) == 2 * TURNS - 1
    session.append("assistant", "a19, put another way", "msg msg-a", broadcast=False)
    save_session_to_history(state, session)
    assert _unread_words(state, key) == ["a19, put another way"]


async def test_an_edited_message_sent_again_is_read_by_the_next_pass(tmp_path):
    """🔴 Red before: the edit took the chat to 39 messages with the edited one last, the count
    stayed 40, and once the agent answered the edited words were never read. The resend writes
    the edited message before its save, so the count must stop where the messages differ, not at
    the new length."""
    state = _make_state(tmp_path)
    session, key = _seed(state)

    async with TestClient(TestServer(_chat_app(state))) as client:
        sent = await client.post(
            f"/api/chat/sessions/{session.key}/edit-resend",
            json={"ts": _user_time(TURNS - 1), "content": "I moved to Porto, not Lisbon."},
        )
        assert sent.status == 200, await sent.text()

    assert _consolidated(state, key) == 2 * TURNS - 2
    session.append("assistant", "Porto it is.", "msg msg-a", broadcast=False)
    save_session_to_history(state, session)
    assert _unread_words(state, key) == ["I moved to Porto, not Lisbon.", "Porto it is."]


async def test_a_forced_save_that_rewrites_nothing_keeps_the_count(tmp_path):
    """A rename, a tag or a folder saves the chat with ``force`` too. With every message as the
    file holds it, the count stands, and nothing is read twice."""
    state = _make_state(tmp_path)
    session, key = _seed(state)

    save_session_to_history(state, session, force=True)

    assert _consolidated(state, key) == 2 * TURNS


async def test_a_channel_turn_written_twice_is_not_read_again(tmp_path):
    """A channel that runs a conversation writes its turn to the file and to the chat open for
    it, each with its own time. A forced save of that chat finds the same words where the file
    has them, and keeps the count."""
    state = _make_state(tmp_path)
    session, key = _seed(state)
    log = state.conversation_log
    log.append(key, "user", "Booked the train.")
    log.append(key, "assistant", "Great.")
    session.append("user", "Booked the train.", "msg msg-u", broadcast=False)
    session.append("assistant", "Great.", "msg msg-a", broadcast=False)
    log.mark_consolidated(key, 2 * TURNS + 2)

    save_session_to_history(state, session, force=True)

    assert _consolidated(state, key) == 2 * TURNS + 2


# ── a pass that ends after a rewrite marks only what it read ─────────────────────────────────


async def test_a_pass_that_ends_after_an_undo_marks_nothing_past_the_end(tmp_path):
    """🔴 Red before: the pass read 40 messages, the chat was undone to 36 and four messages were
    sent while its model answered, and the pass then marked 40: the four were never read. Now it
    marks the 36 it read that are still there."""
    from personalclaw.history import HistoryConsolidator
    from personalclaw.memory import MemoryStore

    state = _make_state(tmp_path)
    session, key = _seed(state)
    _say(state, session, "I like trains.", "Noted.")
    memory = MemoryStore(workspace=tmp_path / "workspace")
    memory.init()
    consolidator = HistoryConsolidator(log=state.conversation_log, memory=memory)
    asked: list[str] = []

    async with TestClient(TestServer(_chat_app(state))) as client:

        async def answer(_prompt: str, _key: str, **_kw: object) -> dict:
            asked.append(_key)
            undone = await client.post(f"/api/chat/sessions/{session.key}/undo", json={"n": 3})
            assert undone.status == 200, await undone.text()
            _say(state, session, "I moved to Porto.", "Noted.", "Window seats, please.", "Sure.")
            return {"history_entry": "Talked about trains."}

        with patch.object(consolidator, "_call_llm", side_effect=answer):
            await consolidator._consolidate(key, include_history=True)

    assert asked == [key]
    assert len(state.conversation_log.read_messages(key)) == 2 * TURNS
    assert _consolidated(state, key) <= 2 * TURNS - 4
    assert _unread_words(state, key)[-4:] == [
        "I moved to Porto.",
        "Noted.",
        "Window seats, please.",
        "Sure.",
    ]


async def test_a_pass_marks_all_it_read_when_the_chat_only_grew(tmp_path):
    """The everyday case stays as it was: messages sent while the model answered are left for the
    next pass, and the ones the pass read are marked."""
    from personalclaw.history import ConversationLog

    log = ConversationLog(base_dir=tmp_path)
    for i in range(10):
        log.append("t1", "user", f"msg {i}")
    _unread, total = log.get_unconsolidated("t1")
    log.append("t1", "user", "sent while the model answered")

    log.mark_consolidated("t1", total)

    assert log.get_metadata("t1")["last_consolidated"] == 10
    assert [m["content"] for m in log.get_unconsolidated("t1")[0]] == [
        "sent while the model answered"
    ]


async def test_a_mark_past_the_end_is_held_to_the_end(tmp_path):
    """A mark with no read before it (an older caller's) never points past the transcript."""
    from personalclaw.history import ConversationLog

    log = ConversationLog(base_dir=tmp_path)
    for i in range(3):
        log.append("t1", "user", f"msg {i}")

    log.mark_consolidated("t1", 7)

    assert log.get_metadata("t1")["last_consolidated"] == 3
    log.append("t1", "user", "the next one")
    assert [m["content"] for m in log.get_unconsolidated("t1")[0]] == ["the next one"]


async def test_a_pass_keeps_nothing_it_read_once_it_is_over(tmp_path):
    """A pass that ends before its mark (here, with nothing of the owner's to read) lets go of
    the messages it read, as one that marks does: held past the pass, the read kept a second copy
    of the transcript alive once the next write replaced the cached one."""
    from personalclaw.history import HistoryConsolidator
    from personalclaw.memory import MemoryStore

    state = _make_state(tmp_path)
    _session, key = _seed(state)
    memory = MemoryStore(workspace=tmp_path / "workspace")
    memory.init()
    consolidator = HistoryConsolidator(log=state.conversation_log, memory=memory)

    with patch.object(consolidator, "_call_llm", side_effect=AssertionError("no model call")):
        await consolidator._consolidate(key, include_history=True)

    assert key not in state.conversation_log._consolidation_reads
