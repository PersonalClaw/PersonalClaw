"""The chat search index holds each chat once, under its transcript's file name.

A chat's save indexed it under its session key (``dashboard:chat-7``) while the background
indexer, which reads the transcripts, indexed the same chat under its file's name
(``dashboard_chat-7``, the key the chat list uses). The two never met: each check swept the
save's entry as a key no transcript has and read the chat again under the other spelling, and
the next save wrote the first spelling back. Between the two, one chat answered a search twice,
once from an entry older than its transcript, and a forget under one spelling left the other
findable.

The index now keys an entry by the file name alone, whichever spelling a caller hands it, and
an index written before is moved to that key when it is opened (``_key_by_file``).

Driven through the real save path (``save_session_to_history``) and the indexer's own passes,
over a home of the test's own.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from personalclaw import session_search as ss
from personalclaw.history import ConversationLog


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Own home + a fresh connection and indexer, since the module keeps one process-wide."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    ss.reset_for_tests()
    ss.INDEXER.reset()
    if not ss.probe().fts5:  # pragma: no cover — a build without FTS5
        pytest.skip("sqlite build lacks FTS5")
    yield
    ss.INDEXER.reset()
    ss.reset_for_tests()


@pytest.fixture
def home_log() -> ConversationLog:
    log = ConversationLog()
    assert log.is_home_log()
    return log


def _state(log: ConversationLog):
    from personalclaw.dashboard.state import DashboardState

    return DashboardState(sessions=MagicMock(count=0), start_time=0.0, conversation_log=log)


def _say(state, name: str, text: str) -> None:
    """One turn of chat *name*, saved the way the gateway saves it."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = state.get_or_create_session(name)
    session.append("user", text)
    session.append("assistant", "Noted.")
    save_session_to_history(state, session)


def _catch_up() -> None:
    """What the indexer's thread does between two checks: read what was written."""
    ss.INDEXER._catch_up(threading.Event())


def _entries() -> list[tuple[str, str]]:
    """``(table, key)`` for every row of both tables."""
    conn = ss._connect()
    assert conn is not None
    with ss._LOCK:
        return sorted(
            [("indexed", r[0]) for r in conn.execute("SELECT session_key FROM indexed")]
            + [("fts", r[0]) for r in conn.execute("SELECT session_key FROM sessions_fts")]
        )


def _keys(word: str) -> list[str]:
    return [hit["key"] for hit in ss.search_sessions(word)]


# ── the writer ─────────────────────────────────────────────────────────────────


class TestOneEntryPerChat:
    def test_a_saved_chat_is_indexed_under_the_key_the_chat_list_uses(self, home_log) -> None:
        state = _state(home_log)
        ss.INDEXER.check()  # the gateway's first count, before any chat was saved
        _say(state, "chat-7", "watermelon pricing for the quarterly plan")
        _catch_up()

        listed = [entry["key"] for entry in home_log.list_sessions()]
        assert listed == ["dashboard_chat-7"], "premise: the chat list names the chat this way"
        assert _entries() == [("fts", "dashboard_chat-7"), ("indexed", "dashboard_chat-7")]
        assert _keys("watermelon") == listed

    def test_a_chat_saved_across_a_check_answers_a_search_once(self, home_log) -> None:
        """A chat's life in a gateway: a turn, a check (at most five minutes later), a turn."""
        state = _state(home_log)
        ss.INDEXER.check()
        _say(state, "chat-7", "watermelon pricing for the quarterly plan")
        _catch_up()
        ss.INDEXER.check()
        _catch_up()
        _say(state, "chat-7", "and the pineapple crates for the fair")
        _catch_up()

        assert _keys("watermelon") == ["dashboard_chat-7"]
        assert _keys("pineapple") == ["dashboard_chat-7"]
        assert ss.stats()["sessions"] == 1

    def test_a_check_reads_nothing_again_after_a_save(self, home_log) -> None:
        state = _state(home_log)
        ss.INDEXER.check()
        _say(state, "chat-7", "watermelon pricing for the quarterly plan")
        _catch_up()

        ss.INDEXER.check()

        # Nothing changed since the save indexed the chat, so the index answers for it whole.
        assert ss.INDEXER.progress() == {"indexed": 1, "of": 1, "building": False}
        answer = ss.search("watermelon", log=home_log)
        assert answer.complete and [hit["key"] for hit in answer.hits] == ["dashboard_chat-7"]


class TestForgetUnderEitherSpelling:
    @pytest.mark.parametrize(
        ("indexed_as", "forgotten_as"),
        [("dashboard:chat-5", "dashboard_chat-5"), ("dashboard_chat-5", "dashboard:chat-5")],
    )
    def test_forgetting_a_chat_by_either_spelling_removes_it(
        self, indexed_as: str, forgotten_as: str
    ) -> None:
        assert ss.index_session(indexed_as, "Harvest", "the kiwi harvest schedule")
        assert _keys("kiwi"), "premise: the chat was findable"

        ss.forget_session(forgotten_as)

        assert _keys("kiwi") == []
        assert _entries() == []


# ── the backfill ───────────────────────────────────────────────────────────────


def _write_old_entry(key: str, title: str, body: str, *, stamp: tuple[float, int]) -> int:
    """An entry as the save wrote it before: under the session key, stamped with its file."""
    conn = ss._connect()
    assert conn is not None
    with ss._LOCK:
        conn.execute(
            "INSERT INTO indexed (session_key, title, mtime, size, chars, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (key, title, stamp[0], stamp[1], len(body)),
        )
        rowid = conn.execute("SELECT rowid FROM indexed WHERE session_key = ?", (key,)).fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO sessions_fts (rowid, session_key, title, body) VALUES (?, ?, ?, ?)",
            (rowid, key, title, body),
        )
    return int(rowid)


def _stamp(log: ConversationLog, key: str) -> tuple[float, int]:
    stat = log._path(key).stat()
    return float(stat.st_mtime), int(stat.st_size)


def _reopen() -> None:
    """The next start: the process opens the index afresh."""
    ss.reset_for_tests()
    ss.INDEXER.reset()


class TestAnIndexWrittenBefore:
    def test_an_entry_under_the_session_key_moves_to_the_file_name_and_stays(
        self, home_log
    ) -> None:
        home_log.append("dashboard:chat-8", "user", "the orchard lease renewal")
        rowid = _write_old_entry(
            "dashboard:chat-8",
            "Orchard",
            "the orchard lease renewal",
            stamp=_stamp(home_log, "dashboard:chat-8"),
        )

        _reopen()

        assert _entries() == [("fts", "dashboard_chat-8"), ("indexed", "dashboard_chat-8")]
        assert _keys("orchard") == ["dashboard_chat-8"]
        conn = ss._connect()
        assert conn is not None
        with ss._LOCK:
            kept = conn.execute(
                "SELECT rowid FROM indexed WHERE session_key = 'dashboard_chat-8'"
            ).fetchone()[0]
        assert kept == rowid, "moved, not dropped and read again"
        ss.INDEXER.check()
        assert ss.INDEXER.progress() == {"indexed": 1, "of": 1, "building": False}

    def test_a_chat_under_both_spellings_keeps_one_entry(self, home_log) -> None:
        home_log.append("dashboard:chat-8", "user", "the orchard lease renewal")
        stamp = _stamp(home_log, "dashboard:chat-8")
        _write_old_entry("dashboard:chat-8", "Orchard", "the orchard lease renewal", stamp=stamp)
        _write_old_entry("dashboard_chat-8", "Orchard", "the orchard lease renewal", stamp=stamp)
        assert len(_keys("orchard")) == 2, "premise: the chat answered twice"

        _reopen()

        assert _entries() == [("fts", "dashboard_chat-8"), ("indexed", "dashboard_chat-8")]
        assert _keys("orchard") == ["dashboard_chat-8"]

    def test_a_text_row_left_without_its_entry_under_the_session_key_is_dropped(
        self, home_log
    ) -> None:
        """Drift from an older forget: text in ``sessions_fts`` with no bookkeeping row."""
        home_log.append("dashboard:chat-9", "user", "the quince jam recipe")
        conn = ss._connect()
        assert conn is not None
        with ss._LOCK:
            conn.execute(
                "INSERT INTO sessions_fts (session_key, title, body) VALUES (?, ?, ?)",
                ("dashboard:chat-9", "Jam", "the quince jam recipe"),
            )

        _reopen()

        assert _entries() == []
        # The indexer, finding the chat missing, reads it in under its file's name.
        ss.INDEXER.check()
        _catch_up()
        assert _keys("quince") == ["dashboard_chat-9"]

    def test_the_backfill_changes_nothing_the_second_time(self, home_log) -> None:
        home_log.append("dashboard:chat-8", "user", "the orchard lease renewal")
        _write_old_entry(
            "dashboard:chat-8",
            "Orchard",
            "the orchard lease renewal",
            stamp=_stamp(home_log, "dashboard:chat-8"),
        )
        conn = ss._connect()
        assert conn is not None

        with ss._LOCK:
            first = ss._key_by_file(conn)
        before = _entries()
        with ss._LOCK:
            second = ss._key_by_file(conn)

        assert (first, second) == (1, 0)
        assert (
            _entries() == before == [("fts", "dashboard_chat-8"), ("indexed", "dashboard_chat-8")]
        )

    def test_a_check_sweeps_an_entry_under_another_spelling_and_keeps_the_chats_own(
        self, home_log
    ) -> None:
        """An entry written under the session key after the index was opened (an older build
        writing to the same home) is swept as the key no transcript has, and only that one: a
        sweep that forgot it by the chat's name would drop the chat's own entry instead."""
        home_log.append("dashboard:chat-8", "user", "the orchard lease renewal")
        stamp = _stamp(home_log, "dashboard:chat-8")
        own = _write_old_entry(
            "dashboard_chat-8", "Orchard", "the orchard lease renewal", stamp=stamp
        )
        _write_old_entry("dashboard:chat-8", "Orchard", "the orchard lease renewal", stamp=stamp)

        ss.INDEXER.check()

        assert _entries() == [("fts", "dashboard_chat-8"), ("indexed", "dashboard_chat-8")]
        conn = ss._connect()
        assert conn is not None
        with ss._LOCK:
            kept = conn.execute(
                "SELECT rowid FROM indexed WHERE session_key = 'dashboard_chat-8'"
            ).fetchone()[0]
        assert kept == own
        assert ss.INDEXER.progress() == {"indexed": 1, "of": 1, "building": False}
