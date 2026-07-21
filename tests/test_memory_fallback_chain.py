"""M4: the real fallback chain — capability-aware degradation.

VISION's "Qdrant primary → filesystem plain-text fallback" expressed through the
contract: when the primary record provider has no vector capability (no
embedder), the service degrades retrieval to the filesystem provider's FTS,
exactly like Knowledge degrades to FTS. No more vector_store-is-None fiction
inside one class.
"""

from __future__ import annotations

from personalclaw.memory import MemoryStore
from personalclaw.memory_providers.base import MemoryProvider
from personalclaw.memory_providers.filesystem import FilesystemMemoryProvider
from personalclaw.memory_record import MemoryKind
from personalclaw.memory_service import MemoryService, service_for
from personalclaw.vector_memory import VectorMemoryStore

# ── the filesystem fallback provider implements the contract ──


def test_filesystem_provider_is_a_memoryprovider(tmp_path):
    store = MemoryStore(workspace=tmp_path)
    store.init()
    fp = FilesystemMemoryProvider(store)
    assert isinstance(fp, MemoryProvider)
    assert fp.name == "filesystem"
    assert not type(fp).__abstractmethods__  # concrete


def test_filesystem_provider_caps_are_ftsonly(tmp_path):
    store = MemoryStore(workspace=tmp_path)
    store.init()
    caps = FilesystemMemoryProvider(store).capabilities()
    assert caps.vector is False
    assert caps.full_text_search is True
    assert caps.event_log is False


def test_filesystem_provider_vector_query_degrades_to_fts(tmp_path):
    store = MemoryStore(workspace=tmp_path)
    store.init()
    store.write_preferences("# User Preferences\n\n- prefers dark mode and vim keybindings\n")
    store.rebuild_index()
    fp = FilesystemMemoryProvider(store)
    # No vectors, but FTS finds the keyword.
    hits = fp.vector_query(text="vim", k=5)
    assert isinstance(hits, list)
    assert any("vim" in (h.get("text", "").lower()) for h in hits)


def test_filesystem_provider_query_yields_markdown_records(tmp_path):
    store = MemoryStore(workspace=tmp_path)
    store.init()
    store.write_preferences("# User Preferences\n\n- concise answers\n")
    store.write_projects("Building the memory re-arch")
    fp = FilesystemMemoryProvider(store)
    recs = fp.query()
    kinds = {r.kind for r in recs}
    assert MemoryKind.PREFERENCE in kinds
    assert any("concise" in r.text for r in recs)


# ── the service degrades through the chain ──


def test_service_can_vector_search_reflects_embedder(tmp_path):
    store = MemoryStore(workspace=tmp_path)
    store.init()
    vs = VectorMemoryStore(db_path=tmp_path / "v.db", embedding_dim=3)
    vs.init()
    store.vector_store = vs

    svc = service_for(store)
    assert svc.has_vector is True  # store IS wired
    assert svc.can_vector_search is False  # but no embedder → can't vector-search
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    # rebuild the service (capabilities changed)
    svc2 = MemoryService(store, fallback=FilesystemMemoryProvider(store))
    assert svc2.can_vector_search is True


def test_active_recall_degrades_to_fts_without_record_store(tmp_path):
    # When there is NO record/vector store at all, active recall degrades to the
    # markdown FTS fallback — the bottom of the chain. (A store WITH no embedder
    # self-degrades internally, so we defer to it; this tests the no-store case.)
    store = MemoryStore(workspace=tmp_path)
    store.init()
    store.write_preferences(
        "# User Preferences\n\n- the deployment runbook lives in docs/deploy.md\n"
    )
    store.rebuild_index()
    # no vector_store attached → primary is absent

    svc = service_for(store)
    assert svc.has_vector is False
    block = svc.active_recall("deployment runbook")
    assert "deploy" in block.lower()


def test_service_for_attaches_filesystem_fallback(tmp_path):
    store = MemoryStore(workspace=tmp_path)
    store.init()
    svc = service_for(store)
    # the fallback is wired (a FilesystemMemoryProvider over the same store)
    assert svc._fallback is not None
    assert isinstance(svc._fallback, FilesystemMemoryProvider)


def test_fts_fallback_search_empty_without_fallback(tmp_path):
    vs = VectorMemoryStore(db_path=tmp_path / "v.db", embedding_dim=3)
    vs.init()
    svc = MemoryService.over_vector_store(vs)  # no fallback
    assert svc.fts_fallback_search("anything") == []
