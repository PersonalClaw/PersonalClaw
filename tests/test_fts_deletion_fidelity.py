"""SM-11 — FTS deletion fidelity.

Deleting a chat removed the transcript file but left its messages searchable
in the ``sessions_fts`` index — a privacy/consistency hole.  These tests pin
the two halves of the fix: the deletion seam (``ConversationLog.delete_session``
drops the FTS rows) and the backfill (``purge_orphans`` sweeps rows already
orphaned before the seam existed).
"""

from __future__ import annotations

import pytest

from personalclaw import session_search
from personalclaw.history import ConversationLog
from personalclaw.session_search import purge_orphans, search_sessions


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated conversation log + search index, both under tmp_path."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    session_search.reset_for_tests()
    if not session_search.probe().fts5:  # pragma: no cover — env without FTS5
        pytest.skip("sqlite build lacks FTS5")
    log = ConversationLog(base_dir=tmp_path / "history")
    yield log
    session_search.reset_for_tests()


def _make_session(log: ConversationLog, key: str, text: str) -> None:
    log.append(key, "user", text)
    assert log.has_log(key)
    assert session_search.index_session(key, title=key, body=text)


class TestDeleteThenSearch:
    def test_deleted_session_is_not_searchable(self, env) -> None:
        log = env
        _make_session(log, "dashboard:chat-77-123", "the zebra password is xyzzy")
        assert any(
            "zebra" in (r.get("snippet", "") + r.get("title", "")) for r in search_sessions("zebra")
        )

        assert log.delete_session("dashboard:chat-77-123") is True

        assert search_sessions("zebra") == []
        assert search_sessions("xyzzy") == []

    def test_delete_under_the_other_key_form_also_forgets(self, env) -> None:
        """``:`` and ``_`` forms map to one file; deleting via either must
        drop the index row stored under the other."""
        log = env
        _make_session(log, "dashboard:chat-88-456", "unique-fennec-content")
        # The handlers sometimes carry the underscore (filename) form.
        assert log.delete_session("dashboard_chat-88-456") is True
        assert search_sessions("fennec") == []

    def test_index_failure_never_blocks_deletion(self, env, monkeypatch) -> None:
        log = env
        _make_session(log, "dashboard:chat-99-789", "content")

        def boom(_key: str) -> None:
            raise RuntimeError("index unavailable")

        monkeypatch.setattr(session_search, "forget_session", boom)
        assert log.delete_session("dashboard:chat-99-789") is True
        assert not log.has_log("dashboard:chat-99-789")


class TestBackfill:
    def test_purge_orphans_sweeps_and_counts(self, env) -> None:
        log = env
        # One live session…
        _make_session(log, "dashboard:chat-1-100", "alive-otter")
        # …and two orphans: index rows whose transcript never/no-longer exists.
        assert session_search.index_session("dashboard:chat-2-200", "t", "dead-badger")
        assert session_search.index_session("dashboard:chat-3-300", "t", "dead-stoat")

        purged = purge_orphans(log)

        assert purged == 2
        assert search_sessions("badger") == []
        assert search_sessions("stoat") == []
        assert search_sessions("otter") != []

    def test_purge_sweeps_fts_only_drift_rows(self, env) -> None:
        """A row present only in sessions_fts (no `indexed` bookkeeping row —
        drift from the old non-transactional forget) must still be swept."""
        log = env
        conn = session_search._connect()
        assert conn is not None
        conn.execute(
            "INSERT INTO sessions_fts (session_key, title, body) VALUES (?, ?, ?)",
            ("dashboard:chat-4-400", "t", "drift-weasel"),
        )
        assert search_sessions("weasel") != []

        assert purge_orphans(log) == 1
        assert search_sessions("weasel") == []

    def test_purge_on_a_clean_index_is_zero(self, env) -> None:
        log = env
        _make_session(log, "dashboard:chat-5-500", "alive-heron")
        assert purge_orphans(log) == 0
        assert search_sessions("heron") != []


class TestReindexAllRunsTheBackfill:
    def test_complete_pass_sweeps_fts_only_orphans(self, env) -> None:
        log = env
        conn = session_search._connect()
        assert conn is not None
        conn.execute(
            "INSERT INTO sessions_fts (session_key, title, body) VALUES (?, ?, ?)",
            ("dashboard:chat-6-600", "t", "orphan-lynx"),
        )
        session_search.reindex_all(log)  # complete pass (limit=None)
        assert search_sessions("lynx") == []
