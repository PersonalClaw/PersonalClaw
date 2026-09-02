"""Family rail: routine events must not kill turns or log ERROR (#369, #312).

The class: a completely ordinary event on a best-effort path — a user pasting a
giant token, a client navigating away mid-broadcast — escalating into a dead
chat turn or an unretrieved-task ERROR traceback. Best-effort paths degrade
quietly; they never take the turn down with them.
"""

import asyncio
import logging
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)


# ── #369: episodic LIKE fallback survives pathological user text ─────────────


@pytest.fixture()
def store(tmp_path):
    from personalclaw.vector_memory import VectorMemoryStore

    s = VectorMemoryStore(db_path=tmp_path / "mem.db")
    s.init()
    return s


class TestEpisodicKeywordFallbackRobustness:
    def _seed(self, store):
        assert store.write_episodic(
            "the deploy pipeline broke on tuesday afternoon", conversation_id="c1"
        )

    def test_giant_token_does_not_raise(self, store):
        # A single token past SQLite's 50k LIKE-pattern cap used to raise
        # "LIKE or GLOB pattern too complex" out of the recall path and kill
        # the chat turn that triggered context assembly.
        self._seed(store)
        giant = "A" * 60_000
        assert store._fts5_episodic_search(giant, limit=5) == []

    def test_giant_token_amid_normal_words_still_matches(self, store):
        # The oversized token is dropped; the human words still recall.
        self._seed(store)
        giant = "B" * 60_000
        rows = store._fts5_episodic_search(f"{giant} deploy pipeline", limit=5)
        assert len(rows) == 1

    def test_normal_query_unchanged(self, store):
        self._seed(store)
        rows = store._fts5_episodic_search("deploy tuesday", limit=5)
        assert len(rows) == 1

    def test_operational_error_degrades_to_empty(self, store, monkeypatch):
        # The invariant, independent of the word cap: recall degrades to
        # no-matches on ANY SQLite operational refusal, never propagates.
        from personalclaw.sqlite_compat import sqlite3

        class _BoomDB:
            def execute(self, *_a, **_k):
                raise sqlite3.OperationalError("LIKE or GLOB pattern too complex")

        monkeypatch.setattr(store, "_db", _BoomDB())
        assert store._fts5_episodic_search("anything at all", limit=5) == []


# ── #312: a WS client disconnecting mid-broadcast is DEBUG + reap, not ERROR ─


class _ExplodingWS:
    """Stands in for an aiohttp WS whose peer vanished mid-send."""

    closed = False

    async def send_str(self, _msg):
        raise ConnectionResetError("Cannot write to closing transport")


class TestWsSendGuarded:
    def _state(self):
        from personalclaw.dashboard.state import DashboardState

        state = DashboardState.__new__(DashboardState)
        state._ws_clients = []
        state._ws_app = {}
        state._ws_log_subscribers = set()
        state._ws_subagent_subscribers = set()
        state._ws_loop = None
        return state

    @pytest.mark.asyncio
    async def test_disconnect_mid_send_is_quiet_and_reaps(self, caplog):
        state = self._state()
        ws = _ExplodingWS()
        state._ws_clients.append(ws)

        with caplog.at_level(logging.DEBUG):
            ok = state._schedule_ws_send(ws.send_str("{}"), ws)
            # Let the guarded task run to completion.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert ok is True  # scheduling succeeded; the FAILURE is absorbed
        assert ws not in state._ws_clients  # reaped immediately, not next broadcast
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == []  # nothing above DEBUG for a routine disconnect

    @pytest.mark.asyncio
    async def test_successful_send_keeps_client(self):
        state = self._state()

        class _OkWS:
            closed = False

            async def send_str(self, _msg):
                return None

        ws = _OkWS()
        state._ws_clients.append(ws)
        assert state._schedule_ws_send(ws.send_str("{}"), ws) is True
        await asyncio.sleep(0)
        assert ws in state._ws_clients

    def test_no_loop_closes_coro_without_warning(self, recwarn):
        # Sync startup/tests: no running loop, no captured loop — the coroutine
        # must be closed cleanly (no "never awaited" RuntimeWarning).
        state = self._state()
        ws = _ExplodingWS()
        assert state._schedule_ws_send(ws.send_str("{}"), ws) is True
        assert not [w for w in recwarn.list if "never awaited" in str(w.message)]
