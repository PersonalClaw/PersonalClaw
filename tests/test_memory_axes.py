"""M5a: the TIER × SCOPE axes — migration v6 + axis-aware persistence.

Asserts the axes round-trip through the provider's put()/get()/query() and that
legacy writes keep today's global/durable defaults (no behavior change).
"""

from __future__ import annotations

import pytest

from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def store(tmp_path):
    s = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    s.init()
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return s


def test_migration_v6_applied(store):
    versions = {r[0] for r in store.db.execute("SELECT version FROM schema_version").fetchall()}
    assert 6 in versions


def test_axis_columns_exist_on_both_tables(store):
    for table in ("semantic_memory", "episodic_memories"):
        cols = {r[1] for r in store.db.execute(f"PRAGMA table_info({table})").fetchall()}
        assert {"tier", "scope", "scope_ref", "category", "visit_count"} <= cols


def test_legacy_write_keeps_global_durable_defaults(store):
    # The legacy set_semantic path is untouched → global + durable.
    store.set_semantic("pref.editor", "vim", 0.9, "user_explicit")
    rec = store.get_record("pref.editor")
    assert rec.scope == MemoryScope.GLOBAL
    assert rec.tier == MemoryTier.SEMANTIC
    assert rec.scope_ref is None
    assert rec.category is None


def test_put_persists_scope_and_category(store):
    store.put([
        MemoryRecord(
            id="pref.session.note", kind=MemoryKind.SEMANTIC, value="ephemeral",
            confidence=0.9, source="service",
            scope=MemoryScope.SESSION, scope_ref="sess-123", category="event",
            tier=MemoryTier.EPISODIC, visit_count=2,
        )
    ])
    rec = store.get_record("pref.session.note")
    assert rec.scope == MemoryScope.SESSION
    assert rec.scope_ref == "sess-123"
    assert rec.category == "event"
    assert rec.tier == MemoryTier.EPISODIC
    assert rec.visit_count == 2


def test_put_episodic_persists_scope(store):
    store.put([
        MemoryRecord(
            id="", kind=MemoryKind.EPISODIC, text="a workspace-scoped fragment to keep",
            source="service", scope=MemoryScope.WORKSPACE, scope_ref="/repo/x",
            category="decision",
        )
    ])
    epis = [r for r in store.iter_records(kinds={"episodic"})]
    assert epis
    match = [r for r in epis if r.scope == MemoryScope.WORKSPACE]
    assert match and match[0].scope_ref == "/repo/x" and match[0].category == "decision"


def test_query_filters_by_scope(store):
    # Keys must be allow-listed (pref.*/project.*/user.*/lesson.*).
    store.put([
        MemoryRecord(id="pref.global_one", kind=MemoryKind.SEMANTIC, value="global one",
                     confidence=0.9, source="user_explicit", scope=MemoryScope.GLOBAL),
        MemoryRecord(id="pref.session_one", kind=MemoryKind.SEMANTIC, value="session one",
                     confidence=0.9, source="user_explicit", scope=MemoryScope.SESSION,
                     scope_ref="sess-9"),
    ])
    sess = store.query(scope="session")
    assert any(r.id == "pref.session_one" for r in sess)
    assert all(r.scope == MemoryScope.SESSION for r in sess)
    glob = store.query(scope="global")
    assert any(r.id == "pref.global_one" for r in glob)


def test_query_filters_by_scope_ref(store):
    store.put([
        MemoryRecord(id="pref.repo_a", kind=MemoryKind.SEMANTIC, value="x", confidence=0.9,
                     source="user_explicit", scope=MemoryScope.WORKSPACE, scope_ref="/repo/a"),
        MemoryRecord(id="pref.repo_b", kind=MemoryKind.SEMANTIC, value="y", confidence=0.9,
                     source="user_explicit", scope=MemoryScope.WORKSPACE, scope_ref="/repo/b"),
    ])
    only_a = store.query(scope_ref="/repo/a")
    assert [r.id for r in only_a] == ["pref.repo_a"]
