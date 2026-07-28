"""Tests for cross-session FTS5 search (SESSION-MANAGEMENT S1).

Two properties carry this feature, so most tests assert one of them:

* **A restricted session is never findable.** Temporary/incognito exclusion is
  enforced when indexing, when re-indexing, and again when reading — a session
  reclassified after its rows were written must disappear from results immediately.
* **The index is disposable.** It derives entirely from the transcripts, so any
  failure degrades to the linear scan rather than losing data or raising.
"""

import sqlite3

import pytest

from personalclaw import session_restrictions
from personalclaw import session_search as ss
from personalclaw.history import ConversationLog


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Own home + a fresh connection, since the module caches one process-wide."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    ss.reset_for_tests()
    yield
    ss.reset_for_tests()


@pytest.fixture
def log(tmp_path):
    """A conversation log with three findable sessions."""
    log = ConversationLog(base_dir=tmp_path / "history")
    log.append("chat-1", "user", "How do I configure the Bedrock provider for Claude?")
    log.append("chat-1", "assistant", "Bind a model under Settings and set the region.")
    log.append("chat-2", "user", "Remind me about the quarterly planning document")
    log.append("chat-3", "user", "Bedrock throttling errors keep appearing in the logs")
    return log


# ── Indexing ──


class TestIndexing:
    def test_reindex_all_indexes_every_session(self, log):
        assert ss.reindex_all(log) == 3
        assert ss.stats()["sessions"] == 3

    def test_second_pass_is_incremental(self, log):
        ss.reindex_all(log)
        assert ss.reindex_all(log) == 0

    def test_changed_session_is_reindexed_and_findable(self, log):
        ss.reindex_all(log)
        log.append("chat-1", "user", "also compare Anthropic pricing tiers")
        assert ss.reindex_all(log) == 1
        assert [r["key"] for r in ss.search_sessions("pricing")] == ["chat-1"]

    def test_force_reindexes_everything(self, log):
        ss.reindex_all(log)
        assert ss.reindex_all(log, force=True) == 3

    def test_limit_caps_one_pass(self, log):
        assert ss.reindex_all(log, limit=2) == 2

    def test_limited_pass_does_not_prune(self, log):
        """A capped pass hasn't seen every session, so it must not delete rows."""
        ss.reindex_all(log)
        before = ss.stats()["sessions"]
        ss.reindex_all(log, limit=1, force=True)
        assert ss.stats()["sessions"] == before

    def test_deleted_session_is_pruned_on_a_full_pass(self, log, tmp_path):
        ss.reindex_all(log)
        assert ss.search_sessions("quarterly")
        for path in (tmp_path / "history").glob("chat-2*"):
            path.unlink()
        log._invalidate_cache("chat-2")
        ss.reindex_all(log)
        assert ss.search_sessions("quarterly") == []

    def test_index_session_directly(self):
        assert ss.index_session("k1", "Title", "some searchable body text") is True
        assert [r["key"] for r in ss.search_sessions("searchable")] == ["k1"]

    def test_reindexing_the_same_session_does_not_duplicate(self):
        for _ in range(4):
            ss.index_session("k1", "Title", "unique body text")
        assert len([r for r in ss.search_sessions("unique")]) == 1

    def test_empty_key_is_refused(self):
        assert ss.index_session("", "t", "b") is False
        assert ss.index_session("   ", "t", "b") is False

    def test_system_messages_are_not_indexed(self, tmp_path):
        """System prompts are scaffolding — matching them would surface every chat."""
        log = ConversationLog(base_dir=tmp_path / "h2")
        log.append("s1", "system", "you are a helpful assistant named zebra")
        log.append("s1", "user", "hello there")
        ss.reindex_all(log)
        assert ss.search_sessions("zebra") == []
        assert [r["key"] for r in ss.search_sessions("hello")] == ["s1"]

    def test_oversized_session_is_truncated_not_refused(self):
        assert ss.index_session("big", "t", "word " * 100_000) is True
        assert ss.stats()["indexed_chars"] <= 200_000


# ── Restrictions ──


class TestRestrictions:
    def test_incognito_session_is_never_indexed(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path / "h")
        session_restrictions.mark_incognito("secret")
        log.append("secret", "user", "my confidential surprise party plan")
        try:
            ss.reindex_all(log)
            assert ss.search_sessions("surprise") == []
        finally:
            session_restrictions.clear("secret")

    def test_temporary_session_is_never_indexed(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path / "h")
        session_restrictions.mark_temporary("ephemeral")
        log.append("ephemeral", "user", "throwaway pineapple question")
        try:
            ss.reindex_all(log)
            assert ss.search_sessions("pineapple") == []
        finally:
            session_restrictions.clear("ephemeral")

    def test_memory_mode_argument_blocks_indexing(self):
        for mode in ("incognito", "temporary", "INCOGNITO"):
            assert ss.index_session("k", "t", "sensitive body", memory_mode=mode) is False
        assert ss.search_sessions("sensitive") == []

    def test_persistent_mode_indexes_normally(self):
        assert ss.index_session("k", "t", "ordinary body", memory_mode="persistent") is True

    def test_reclassifying_purges_an_already_indexed_session(self):
        ss.index_session("k1", "Title", "previously indexed content")
        assert ss.search_sessions("previously")
        session_restrictions.mark_incognito("k1")
        try:
            # Hidden at READ time, before any reindex — the mode is honored as it is
            # NOW, not as it was when the rows were written.
            assert ss.search_sessions("previously") == []
            # And the rows are dropped on the next indexing attempt.
            assert ss.index_session("k1", "Title", "previously indexed content") is False
        finally:
            session_restrictions.clear("k1")

    def test_index_turn_refuses_a_restricted_session(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path / "h")
        log.append("t1", "user", "watermelon notes")
        ss.reindex_all(log)
        assert ss.search_sessions("watermelon")
        session_restrictions.mark_incognito("t1")
        try:
            ss.index_turn("t1", "user", "more text")
            session_restrictions.clear("t1")
            # The purge happened while restricted, so it's gone even after clearing.
            assert ss.search_sessions("watermelon") == []
        finally:
            session_restrictions.clear("t1")

    def test_is_restricted_helper(self):
        assert ss.is_restricted("x", memory_mode="incognito") is True
        assert ss.is_restricted("x", memory_mode="temporary") is True
        assert ss.is_restricted("x", memory_mode="persistent") is False
        assert ss.is_restricted("x", memory_mode="") is False


# ── Querying ──


class TestSearch:
    def test_finds_by_content(self, log):
        ss.reindex_all(log)
        keys = {r["key"] for r in ss.search_sessions("bedrock")}
        assert keys == {"chat-1", "chat-3"}

    def test_snippet_marks_the_match(self, log):
        ss.reindex_all(log)
        row = next(r for r in ss.search_sessions("throttling"))
        assert "<<throttling>>" in row["snippet"]

    def test_prefix_match_on_the_last_token(self, log):
        """Search must feel live while the user is still typing."""
        ss.reindex_all(log)
        assert {r["key"] for r in ss.search_sessions("quarter")} == {"chat-2"}

    def test_multi_token_query_requires_all_terms(self, log):
        ss.reindex_all(log)
        assert {r["key"] for r in ss.search_sessions("bedrock throttling")} == {"chat-3"}

    def test_case_insensitive(self, log):
        ss.reindex_all(log)
        assert ss.search_sessions("BEDROCK")

    def test_short_query_returns_nothing(self, log):
        ss.reindex_all(log)
        assert ss.search_sessions("a") == []
        assert ss.search_sessions("") == []
        assert ss.search_sessions("   ") == []

    def test_no_match_returns_empty(self, log):
        ss.reindex_all(log)
        assert ss.search_sessions("zzzzznotpresent") == []

    def test_limit_is_respected(self, log):
        ss.reindex_all(log)
        assert len(ss.search_sessions("bedrock", limit=1)) == 1

    def test_results_carry_both_key_shapes(self, log):
        """`key` keeps the existing endpoint contract; `session_key` is the plan's."""
        ss.reindex_all(log)
        row = ss.search_sessions("bedrock")[0]
        assert row["key"] == row["session_key"]
        assert "title" in row and "rank" in row

    @pytest.mark.parametrize(
        "hostile",
        [
            'foo" OR "bar',
            "AND OR NOT",
            "foo*",
            "((((",
            'NEAR("a" "b")',
            "a:b",
            "^start",
            "-minus",
            'unbalanced"quote',
        ],
    )
    def test_fts_syntax_in_user_input_cannot_break_the_query(self, log, hostile):
        """Every token is quoted, so operators arrive as literals, not syntax."""
        ss.reindex_all(log)
        ss.search_sessions(hostile)  # must not raise

    def test_query_with_no_indexable_tokens(self, log):
        ss.reindex_all(log)
        assert ss.search_sessions("!!!!") == []


# ── Degradation ──


class TestDegradation:
    def test_search_without_an_index_returns_empty(self):
        assert ss.search_sessions("anything") == []

    def test_stats_without_an_index(self):
        assert ss.stats()["sessions"] == 0

    def test_no_fts5_degrades_to_empty(self, monkeypatch):
        """An FTS5-less SQLite build must fall back, not crash."""
        ss.reset_for_tests()
        real_connect = sqlite3.connect

        def _no_fts5(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            real_executescript = conn.executescript

            def _fail(script):
                if "fts5" in script.lower():
                    raise sqlite3.OperationalError("no such module: fts5")
                return real_executescript(script)

            conn.executescript = _fail  # type: ignore[method-assign]
            return conn

        monkeypatch.setattr(sqlite3, "connect", _no_fts5)
        assert ss.search_sessions("anything") == []
        assert ss.index_session("k", "t", "b") is False
        assert ss.stats()["available"] is False

    def test_index_turn_never_raises(self, monkeypatch):
        monkeypatch.setattr(
            ss, "reindex_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        ss.index_turn("k", "user", "text")  # must not raise

    def test_reindex_all_survives_an_unreadable_log(self):
        class _Broken:
            def list_sessions(self):
                raise OSError("disk gone")

        assert ss.reindex_all(_Broken()) == 0

    def test_reindex_session_survives_an_unreadable_transcript(self):
        class _Broken:
            def get_metadata(self, key):
                return {}

            def read_messages(self, key):
                raise OSError("cannot read")

        assert ss.reindex_session("k", log=_Broken()) is False

    def test_forget_is_safe_on_an_unknown_key(self):
        ss.forget_session("never-existed")
        ss.forget_session("")


# ── The endpoint ──


class TestEndpoint:
    def _app(self, log):
        from types import SimpleNamespace

        from aiohttp import web

        from personalclaw.dashboard.handlers.sessions import api_sessions_search

        app = web.Application()
        app["state"] = SimpleNamespace(conversation_log=log)
        app.router.add_get("/api/sessions/search", api_sessions_search)
        return app

    @pytest.mark.asyncio
    async def test_index_answers_with_a_snippet(self, log):
        from aiohttp.test_utils import TestClient, TestServer

        ss.reindex_all(log)
        async with TestClient(TestServer(self._app(log))) as client:
            body = await (await client.get("/api/sessions/search?q=throttling")).json()
        assert body["source"] == "index"
        assert body["sessions"][0]["snippet"]

    @pytest.mark.asyncio
    async def test_falls_back_to_the_scan_when_the_index_is_empty(self, log):
        from aiohttp.test_utils import TestClient, TestServer

        # No reindex — the index has nothing, so the scan must answer.
        async with TestClient(TestServer(self._app(log))) as client:
            body = await (await client.get("/api/sessions/search?q=throttling")).json()
        assert body["source"] == "scan"
        assert body["sessions"]

    @pytest.mark.asyncio
    async def test_short_query_returns_empty(self, log):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app(log))) as client:
            body = await (await client.get("/api/sessions/search?q=a")).json()
        assert body["sessions"] == []

    @pytest.mark.asyncio
    async def test_limit_is_clamped(self, log):
        from aiohttp.test_utils import TestClient, TestServer

        ss.reindex_all(log)
        async with TestClient(TestServer(self._app(log))) as client:
            resp = await client.get("/api/sessions/search?q=bedrock&limit=99999")
            assert resp.status == 200
            resp = await client.get("/api/sessions/search?q=bedrock&limit=notanumber")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_no_conversation_log(self):
        from types import SimpleNamespace

        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from personalclaw.dashboard.handlers.sessions import api_sessions_search

        app = web.Application()
        app["state"] = SimpleNamespace(conversation_log=None)
        app.router.add_get("/api/sessions/search", api_sessions_search)
        async with TestClient(TestServer(app)) as client:
            body = await (await client.get("/api/sessions/search?q=hello")).json()
        assert body["sessions"] == []


# ── Heartbeat wiring ──


class TestHeartbeatWiring:
    def test_reindex_cadence_is_declared(self):
        from personalclaw import heartbeat

        assert heartbeat._SESSION_INDEX_TICKS >= 1
        assert heartbeat._SESSION_INDEX_MAX_PER_PASS >= 1

    @pytest.mark.asyncio
    async def test_beat_actually_calls_the_reindex(self, monkeypatch):
        """The periodic sweep is what catches channel appends and rewrites.

        Drives the real ``_beat`` rather than inspecting source, so deleting the
        call site fails this test.
        """
        from unittest.mock import MagicMock

        from personalclaw import heartbeat

        calls: list = []
        monkeypatch.setattr(ss, "reindex_all", lambda **kw: calls.append(kw) or 3)

        service = heartbeat.HeartbeatService.__new__(heartbeat.HeartbeatService)
        service._tick = heartbeat._SESSION_INDEX_TICKS  # a tick the sweep runs on
        service._processing = True  # skip the heartbeat-file work
        service._memory = MagicMock()
        service._consolidator = None
        service._on_due_commitments = None
        service._interval = 60
        monkeypatch.setattr(service, "_maybe_remediate", _noop_async, raising=False)

        await service._beat()
        assert calls, "the heartbeat must run the session-search reindex"
        assert calls[0]["limit"] == heartbeat._SESSION_INDEX_MAX_PER_PASS

    @pytest.mark.asyncio
    async def test_a_reindex_failure_does_not_kill_the_tick(self, monkeypatch):
        from unittest.mock import MagicMock

        from personalclaw import heartbeat

        def _boom(**kwargs):
            raise RuntimeError("index exploded")

        monkeypatch.setattr(ss, "reindex_all", _boom)
        service = heartbeat.HeartbeatService.__new__(heartbeat.HeartbeatService)
        service._tick = heartbeat._SESSION_INDEX_TICKS
        service._processing = True
        service._memory = MagicMock()
        service._consolidator = None
        service._on_due_commitments = None
        service._interval = 60
        monkeypatch.setattr(service, "_maybe_remediate", _noop_async, raising=False)

        await service._beat()  # must not raise


async def _noop_async(*args, **kwargs):
    return None


# ── Regressions found by driving the real gateway ────────────────────────────


class TestValidationRegressions:
    def test_scan_fallback_also_honors_the_live_registry(self, tmp_path):
        """The leak: a session marked restricted AFTER its lines were written still
        reads memory_mode="persistent" on disk, so the scan surfaced it."""
        log = ConversationLog(base_dir=tmp_path / "leak")
        log.append("leaky", "user", "pomegranate confidential salary discussion")
        assert [s["key"] for s in log.search_sessions("pomegranate")] == ["leaky"]
        session_restrictions.mark_incognito("leaky")
        try:
            assert log.search_sessions("pomegranate") == []
        finally:
            session_restrictions.clear("leaky")

    def test_scan_still_honors_the_persisted_mode(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path / "persisted")
        log.append("m1", "user", "watermelon notes here")
        log.rewrite_session("m1", log.read_messages("m1"))
        # Directly stamp the persisted mode, as an incognito session would carry.
        path = next((tmp_path / "persisted").glob("m1*"))
        lines = path.read_text().splitlines()
        import json as _json

        meta = _json.loads(lines[0])
        meta["memory_mode"] = "incognito"
        lines[0] = _json.dumps(meta)
        path.write_text("\n".join(lines) + "\n")
        log._invalidate_cache("m1")
        assert log.search_sessions("watermelon") == []

    def test_first_heartbeat_tick_builds_the_index(self, monkeypatch):
        """`_tick` starts at 1, so a plain modulo left a fresh install unsearchable
        for the first five minutes — including the initial history sweep."""
        import asyncio as _asyncio
        from unittest.mock import MagicMock

        from personalclaw import heartbeat

        calls: list = []
        monkeypatch.setattr(ss, "reindex_all", lambda **kw: calls.append(kw) or 0)
        service = heartbeat.HeartbeatService.__new__(heartbeat.HeartbeatService)
        service._tick = 1  # the very first wake-up
        service._processing = True
        service._memory = MagicMock()
        service._consolidator = None
        service._on_due_commitments = None
        service._interval = 60
        monkeypatch.setattr(service, "_maybe_remediate", _noop_async, raising=False)
        _asyncio.run(service._beat())
        assert calls, "the first tick must build the index"


class TestCoarseMtimeFilesystems:
    """An append inside one mtime tick must still be re-indexed.

    Caught only by CI: it stores mtimes at second granularity, so a test that
    appends and immediately re-indexes leaves the timestamp unchanged. The index had
    been storing `time.time()` (always newer than any file mtime), so the skip check
    compared an index time against a file time and skipped forever. macOS's
    sub-second timestamps hid it entirely.
    """

    def test_same_tick_append_is_still_indexed(self, tmp_path):
        import os

        log = ConversationLog(base_dir=tmp_path / "coarse")
        log.append("chat-1", "user", "first message about apples")
        ss.reindex_all(log)

        log.append("chat-1", "user", "second message about zebras")
        # Force the timestamps EQUAL, the condition CI produces naturally.
        path = log._path("chat-1")
        stat = path.stat()
        os.utime(path, (stat.st_atime, stat.st_mtime))
        log._invalidate_cache("chat-1")

        assert ss.reindex_all(log) == 1, "a same-tick append must be re-indexed"
        assert [r["key"] for r in ss.search_sessions("zebras")] == ["chat-1"]

    def test_the_stored_mtime_is_the_files_not_the_clock(self, tmp_path):
        """If it were the wall clock, it would always exceed the file mtime and the
        incremental check could never fire."""
        import time as _time

        log = ConversationLog(base_dir=tmp_path / "stamp")
        log.append("chat-1", "user", "hello")
        ss.reindex_all(log)
        conn = ss._connect()
        stored = conn.execute("SELECT mtime FROM indexed WHERE session_key = 'chat-1'").fetchone()[
            0
        ]
        file_mtime = log._path("chat-1").stat().st_mtime
        assert abs(float(stored) - file_mtime) < 0.001
        assert float(stored) <= _time.time()

    def test_an_untouched_session_is_still_skipped(self, tmp_path):
        """The fix must not turn every pass into a full re-index."""
        log = ConversationLog(base_dir=tmp_path / "quiet")
        log.append("chat-1", "user", "unchanged content")
        ss.reindex_all(log)
        assert ss.reindex_all(log) == 0
