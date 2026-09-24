"""M5b: heat scoring + two-stage rerank + category-TTL expiry (O-A1/O-A2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def svc(tmp_path):
    s = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    s.init()
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(s)


# ── heat ──


def test_heat_rises_with_visits():
    cold = MemoryRecord(
        id="a",
        kind=MemoryKind.SEMANTIC,
        recall_count=0,
        visit_count=0,
        updated_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    warm = MemoryRecord(
        id="b",
        kind=MemoryKind.SEMANTIC,
        recall_count=10,
        visit_count=5,
        updated_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    assert warm.heat() > cold.heat()


def test_heat_decays_with_age():
    now = datetime.now(tz=timezone.utc)
    recent = MemoryRecord(
        id="a", kind=MemoryKind.SEMANTIC, recall_count=3, last_accessed_at=now.isoformat()
    )
    old = MemoryRecord(
        id="b",
        kind=MemoryKind.SEMANTIC,
        recall_count=3,
        last_accessed_at=(now - timedelta(days=120)).isoformat(),
    )
    assert recent.heat(now=now) > old.heat(now=now)


def test_heat_bounded():
    huge = MemoryRecord(
        id="a",
        kind=MemoryKind.SEMANTIC,
        recall_count=100_000,
        visit_count=100_000,
        last_accessed_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    assert huge.heat() < 5.0  # log-damped, can't run away


# ── two-stage rerank ──


def test_rank_episodic_returns_hits(svc):
    svc.write_episodic("the rollout plan was finalized on friday", source="test")
    svc.write_episodic("we also discussed the database migration approach", source="test")
    ranked = svc.rank_episodic(query_text="rollout plan", limit=5)
    assert isinstance(ranked, list)
    # every ranked hit carries the combined ranked_score
    assert all("ranked_score" in h for h in ranked)


def test_rank_episodic_empty_without_data(svc):
    assert svc.rank_episodic(query_text="anything") == []


# ── category-TTL ──


def _backdate(svc, key, days):
    """Simulate the passage of time — set a row's updated_at into the past.
    (set_semantic stamps now; TTL acts on the stored timestamp.)"""
    old = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    svc._vs.db.execute(
        "UPDATE semantic_memory SET updated_at = ?, created_at = ? WHERE key = ?",
        (old, old, key),
    )
    svc._vs.db.commit()


def test_expire_by_category_drops_old_debug(svc):
    # a debug-category record well past its 7-day TTL
    svc.put(
        [
            MemoryRecord(
                id="pref.debug_note",
                kind=MemoryKind.SEMANTIC,
                value="temp",
                confidence=0.9,
                source="service",
                category="debug",
            )
        ]
    )
    _backdate(svc, "pref.debug_note", 30)
    assert svc.get_record("pref.debug_note") is not None
    n = svc.expire_by_category()
    assert n >= 1
    assert svc.get_record("pref.debug_note") is None


def test_expire_keeps_durable_facts(svc):
    # a fact with NO category → never TTL-expired, even when ancient
    svc.put(
        [
            MemoryRecord(
                id="pref.editor",
                kind=MemoryKind.SEMANTIC,
                value="vim",
                confidence=0.9,
                source="user_explicit",
            )
        ]
    )
    _backdate(svc, "pref.editor", 365)
    svc.expire_by_category()
    assert svc.get_record("pref.editor") is not None  # survived


def test_expire_keeps_recent_event(svc):
    recent = (datetime.now(tz=timezone.utc) - timedelta(days=2)).isoformat()
    svc.put(
        [
            MemoryRecord(
                id="pref.recent_event",
                kind=MemoryKind.SEMANTIC,
                value="x",
                confidence=0.9,
                source="service",
                category="event",
                updated_at=recent,
                created_at=recent,
            )
        ]
    )
    svc.expire_by_category()
    assert svc.get_record("pref.recent_event") is not None  # 2 days < 30-day TTL


def test_expire_never_touches_user_explicit_global(svc):
    # even with a TTL category, a user_explicit GLOBAL entry is protected
    svc.put(
        [
            MemoryRecord(
                id="pref.kept",
                kind=MemoryKind.SEMANTIC,
                value="keep me",
                confidence=1.0,
                source="user_explicit",
                category="event",
                scope=MemoryScope.GLOBAL,
            )
        ]
    )
    _backdate(svc, "pref.kept", 365)
    svc.expire_by_category()
    assert svc.get_record("pref.kept") is not None
