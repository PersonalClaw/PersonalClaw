"""M5c: session working memory + two-stage sealing → promotion.

Session working memory is always-injected (tier=working, scope=session); sealing
distills it to a durable session-scoped record and sweeps the rest; only the
heat gate promotes to global (never session-end straight to global — the
anti-noise invariant).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def svc(tmp_path):
    s = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    s.init()
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(s)


# ── working memory ──


def test_working_memory_round_trip(svc):
    svc.write_working_memory("dashboard:sess-1", "User is refactoring the memory layer.")
    block = svc.working_memory("dashboard:sess-1")
    assert "SESSION MEMORY" in block
    assert "refactoring the memory layer" in block


def test_working_memory_is_session_scoped(svc):
    svc.write_working_memory("dashboard:sess-1", "Summary A")
    # the underlying record is tier=working, scope=session
    rec = svc.get_record(MemoryService._working_key("dashboard:sess-1"))
    assert rec.tier == MemoryTier.WORKING
    assert rec.scope == MemoryScope.SESSION
    assert rec.scope_ref == "dashboard:sess-1"


def test_working_memory_empty_for_unknown_session(svc):
    assert svc.working_memory("dashboard:nope") == ""


def test_working_memory_overwrites(svc):
    svc.write_working_memory("s", "first")
    svc.write_working_memory("s", "second updated summary")
    block = svc.working_memory("s")
    assert "second updated summary" in block
    assert "first" not in block


def test_write_working_memory_noop_on_empty(svc):
    svc.write_working_memory("s", "   ")
    assert svc.working_memory("s") == ""


# ── sealing ──


def test_seal_session_distills_to_durable_episodic(svc):
    svc.write_working_memory("sess-x", "Decided to use SQLite+FAISS for the record store.")
    svc.seal_session("sess-x")
    # working note is gone (sealed)
    assert svc.get_record(MemoryService._working_key("sess-x")) is None
    # a durable episodic record carries the sealed content
    epis = svc.search_episodic(query_text="SQLite FAISS record store", limit=5)
    assert any("SQLite" in e.get("text", "") for e in epis)


def test_seal_sweeps_unpromoted_session_records(svc):
    # a session-scoped record that never earned promotion is swept on seal
    svc.put([MemoryRecord(id="pref.session_scratch", kind=MemoryKind.SEMANTIC,
                          value="scratch", confidence=0.9, source="service",
                          scope=MemoryScope.SESSION, scope_ref="sess-y")])
    assert svc.get_record("pref.session_scratch") is not None
    svc.seal_session("sess-y")
    assert svc.get_record("pref.session_scratch") is None  # swept


def test_seal_does_not_touch_global_records(svc):
    svc.put([MemoryRecord(id="pref.durable", kind=MemoryKind.SEMANTIC, value="keep",
                          confidence=0.9, source="user_explicit", scope=MemoryScope.GLOBAL)])
    svc.seal_session("sess-z")
    assert svc.get_record("pref.durable") is not None


# ── promotion (the conservative global gate) ──


def test_promote_by_heat_promotes_hot_recurring(svc):
    # a session-scoped record with high recall_count + recency → promotable
    svc.put([MemoryRecord(id="pref.hot", kind=MemoryKind.SEMANTIC, value="hot fact",
                          confidence=0.9, source="service", scope=MemoryScope.WORKSPACE,
                          scope_ref="/r", recall_count=5,
                          last_accessed_at=datetime.now(tz=timezone.utc).isoformat())])
    n = svc.promote_by_heat(threshold=0.5)
    assert n >= 1
    assert svc.get_record("pref.hot").scope == MemoryScope.GLOBAL


def test_promote_by_heat_skips_cold(svc):
    # session record with no recall + old → stays in scope (anti-noise)
    old = (datetime.now(tz=timezone.utc) - timedelta(days=200)).isoformat()
    svc.put([MemoryRecord(id="pref.cold", kind=MemoryKind.SEMANTIC, value="cold",
                          confidence=0.9, source="service", scope=MemoryScope.SESSION,
                          scope_ref="s", recall_count=0)])
    # semantic_memory has no last_accessed_at column; heat uses updated_at there.
    svc._vs.db.execute("UPDATE semantic_memory SET updated_at=? WHERE key='pref.cold'", (old,))
    svc._vs.db.commit()
    svc.promote_by_heat(threshold=1.0)
    assert svc.get_record("pref.cold").scope == MemoryScope.SESSION  # not promoted


def test_promote_never_touches_already_global(svc):
    svc.put([MemoryRecord(id="pref.g", kind=MemoryKind.SEMANTIC, value="g", confidence=0.9,
                          source="service", scope=MemoryScope.GLOBAL, recall_count=99,
                          last_accessed_at=datetime.now(tz=timezone.utc).isoformat())])
    # already global → no-op (count unaffected)
    n = svc.promote_by_heat(threshold=0.1)
    # n counts only newly-promoted; the global one isn't re-promoted
    assert svc.get_record("pref.g").scope == MemoryScope.GLOBAL
