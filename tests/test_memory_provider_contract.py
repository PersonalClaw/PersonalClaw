"""M2: the v2 MemoryProvider contract — record CRUD + vector ops + WAL +
capabilities, implemented by the native VectorMemoryStore.

These assert the SEAM (the swappable contract), distinct from the rich typed
methods the service drives. An alternate provider implements exactly these.
"""

from __future__ import annotations

import pytest

from personalclaw.memory_providers.base import MemoryProvider
from personalclaw.memory_record import MemoryKind, MemoryRecord
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def provider(tmp_path):
    p = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    p.init()
    p.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return p


def test_vectorstore_is_a_memoryprovider(provider):
    assert isinstance(provider, MemoryProvider)
    assert provider.name == "native-vector"
    assert not type(provider).__abstractmethods__  # concrete


def test_capabilities_contract(provider):
    caps = provider.capabilities()
    assert caps.vector is True  # embed_fn wired
    assert caps.event_log is True
    assert caps.transactional_batch is True


def test_put_get_semantic_and_episodic(provider):
    provider.put([
        MemoryRecord(id="pref.editor", kind=MemoryKind.SEMANTIC, value="vim",
                     confidence=0.9, source="user_explicit"),
        MemoryRecord(id="", kind=MemoryKind.EPISODIC,
                     text="we discussed the migration approach", source="test"),
    ])
    got = provider.get("pref.editor")
    assert got is not None and got.value == "vim" and got.kind == MemoryKind.SEMANTIC
    epis = provider.query(kinds={"episodic"})
    assert any("migration" in r.text for r in epis)


def test_delete_routes_to_right_table(provider):
    provider.put([MemoryRecord(id="pref.x", kind=MemoryKind.SEMANTIC, value="y",
                               confidence=0.9, source="user_explicit")])
    assert provider.get("pref.x") is not None
    assert provider.delete("pref.x") is True
    assert provider.get("pref.x") is None


def test_query_filters_by_kind(provider):
    provider.put([
        MemoryRecord(id="pref.a", kind=MemoryKind.SEMANTIC, value="1", confidence=0.9, source="user_explicit"),
    ])
    provider.write_lesson("always run tests", category="process")
    provider.put([MemoryRecord(id="", kind=MemoryKind.EPISODIC, text="a fragment here", source="t")])

    assert all(r.kind == MemoryKind.SEMANTIC for r in provider.query(kinds={"semantic"}))
    assert all(r.kind == MemoryKind.LESSON for r in provider.query(kinds={"lesson"}))
    assert all(r.kind == MemoryKind.EPISODIC for r in provider.query(kinds={"episodic"}))


def test_query_scope_defaults_to_global(provider):
    provider.put([MemoryRecord(id="pref.a", kind=MemoryKind.SEMANTIC, value="1",
                               confidence=0.9, source="user_explicit")])
    # records default to global today (M5+ adds real scope); a global filter finds it,
    # a session filter finds nothing yet.
    assert provider.query(scope="global")
    assert provider.query(scope="session") == []


def test_vector_query_and_embed(provider):
    provider.put([MemoryRecord(id="", kind=MemoryKind.EPISODIC,
                               text="the rollout plan was finalized today", source="t")])
    hits = provider.vector_query(text="rollout plan", k=5)
    assert isinstance(hits, list)
    assert provider.embed("anything") == [1.0, 0.0, 0.0]


def test_vector_query_empty_without_embedder(tmp_path):
    p = VectorMemoryStore(db_path=tmp_path / "m2.db", embedding_dim=3)
    p.init()  # no embed_fn
    assert p.capabilities().vector is False
    assert p.vector_query(text="x") == []
    assert p.embed("x") is None


def test_event_log_contract(provider):
    eid = provider.append_event(
        event_type="create", memory_type="semantic", memory_key="pref.k",
        old_value=None, new_value="v", source="test",
    )
    assert isinstance(eid, int) and eid >= 1
    events = provider.read_events(limit=10)
    assert any(e["memory_key"] == "pref.k" for e in events)
