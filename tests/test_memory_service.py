"""M1: MemoryService facade — pure indirection over the provider + vector layer.

Asserts the facade produces IDENTICAL results to reaching into vector_store
directly (the bypass M3 removes), and degrades cleanly when no vector store is
attached (the no-embedder case).
"""

from __future__ import annotations

import pytest

from personalclaw.memory import MemoryStore
from personalclaw.memory_service import MemoryService, service_for
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def store_with_vectors(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = MemoryStore(workspace=ws)
    store.init()
    vs = VectorMemoryStore(db_path=tmp_path / "mem.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    store.vector_store = vs
    return store, vs


@pytest.fixture
def store_no_vectors(tmp_path):
    ws = tmp_path / "ws2"
    ws.mkdir()
    store = MemoryStore(workspace=ws)
    store.init()
    return store  # no vector_store attached


# ── capabilities reflect the wiring ──


def test_capabilities_with_vectors(store_with_vectors):
    store, _ = store_with_vectors
    svc = MemoryService(store)
    assert svc.has_vector is True
    assert svc.capabilities().vector is True


def test_capabilities_without_vectors(store_no_vectors):
    svc = MemoryService(store_no_vectors)
    assert svc.has_vector is False
    caps = svc.capabilities()
    assert caps.vector is False
    assert caps.full_text_search is True


# ── facade delegates identically to direct vector_store access ──


def test_set_and_get_semantic_matches_direct(store_with_vectors):
    store, vs = store_with_vectors
    svc = MemoryService(store)
    assert svc.set_semantic("pref.editor", "vim", 0.9, "user_explicit") is None
    # facade read == direct read
    assert svc.get_semantic("pref.editor") == vs.get_semantic("pref.editor")
    assert any(e["key"] == "pref.editor" for e in svc.get_all_semantic())


def test_write_and_get_lessons_matches_direct(store_with_vectors):
    store, vs = store_with_vectors
    svc = MemoryService(store)
    assert svc.write_lesson("always run tests before pushing", category="process") is True
    assert svc.get_lessons() == vs.get_lessons()
    assert "tests" in svc.lessons_context()


def test_write_and_search_episodic(store_with_vectors):
    store, _ = store_with_vectors
    svc = MemoryService(store)
    assert svc.write_episodic("we discussed the rollout plan in detail", source="test") is True
    hits = svc.search_episodic(query_text="rollout plan", limit=5)
    assert isinstance(hits, list)


def test_records_view_through_service(store_with_vectors):
    store, _ = store_with_vectors
    svc = MemoryService(store)
    svc.set_semantic("pref.tabs", "spaces", 0.9, "user_explicit")
    svc.write_episodic("a fragment to keep around", source="test")
    recs = svc.get_records()
    kinds = {r.kind.value for r in recs}
    assert "semantic" in kinds and "episodic" in kinds


def test_events_and_undo_through_service(store_with_vectors):
    store, vs = store_with_vectors
    svc = MemoryService(store)
    svc.set_semantic("pref.editor", "vim", 0.9, "user_explicit")
    events = svc.get_events(limit=10)
    assert events == vs.get_events(limit=10)
    assert len(events) >= 1


# ── graceful degradation with no vector store ──


def test_degrades_without_vector_store(store_no_vectors):
    svc = MemoryService(store_no_vectors)
    assert svc.l1_manifest() == ""
    assert svc.active_recall("anything") == ""
    assert svc.lessons_context() == ""
    assert svc.get_all_semantic() == []
    assert svc.get_lessons() == []
    assert svc.write_lesson("x") is False
    assert svc.write_episodic("x" * 30) is False
    assert svc.search_episodic(query_text="x") == []
    assert svc.set_semantic("pref.x", "y", 0.9, "s") is None
    assert svc.memory_stats() == {}
    # get_context still works — it composes the markdown layers
    assert isinstance(svc.get_context(), str)


# ── service_for caching ──


def test_service_for_caches_per_provider(store_with_vectors):
    store, _ = store_with_vectors
    a = service_for(store)
    b = service_for(store)
    assert a is b
    assert a.provider is store


def test_get_context_composes_markdown_and_vector(store_with_vectors):
    store, _ = store_with_vectors
    svc = MemoryService(store)
    store.write_preferences("# User Preferences\n\n- likes concise answers\n")
    ctx = svc.get_context(query="")
    # composition includes the markdown projection block + the wrapper
    assert "User Preferences" in ctx
    assert "likes concise answers" in ctx
    assert ctx.startswith("[Memory")
    assert ctx.rstrip().endswith("[End of memory]")
