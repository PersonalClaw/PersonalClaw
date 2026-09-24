"""PLATFORM-RESILIENCE PR-2 — every FTS5-dependent module guards its capability
AT INIT with the one fixed remedy, never a mid-query traceback.

Each FTS5 module imported ``probe`` from :mod:`personalclaw.sqlite_compat`, so a test
patches the NAME the module bound (``<module>.probe``) with a fake that reports
``fts5=False`` — the rare stripped SQLite build. The assertions are per the recorded
per-module decision:

* ``knowledge.store.KnowledgeStore`` — RAISE (FTS5 is essential; no fallback): opening
  the store raises ``RuntimeError`` carrying :data:`FTS5_REMEDY` before any table is
  touched.
* ``memory.MemoryStore`` — DEGRADE (markdown projection works without search): the
  store opens, search is a clean no-op, the rest still works, and the remedy is logged.
* ``session_search`` — DEGRADE (disposable index; callers fall back to the linear
  scan): ``_connect`` returns ``None`` and ``search_sessions`` returns ``[]``, remedy
  logged once.

Each module is also exercised on the happy path (``fts5=True``) to prove the guard is a
no-op on a normal build — the "enforcing a dead control is an outage" hazard.
"""

from __future__ import annotations

import logging

import pytest

from personalclaw import memory as memory_mod
from personalclaw import session_search as ss
from personalclaw.knowledge import store as store_mod
from personalclaw.sqlite_compat import FTS5_REMEDY, SqliteCapabilities


def _caps(*, fts5: bool) -> SqliteCapabilities:
    """A capability record for a build with/without FTS5 (JSON1 present either way)."""
    return SqliteCapabilities(driver="sqlite3", version="3.20.0", fts5=fts5, json1=True)


def _patch_probe(monkeypatch, module, *, fts5: bool) -> None:
    """Replace ``module.probe`` with one reporting the chosen FTS5 availability.

    Patches the name the module imported (not the memoized source function), so no
    ``cache_clear`` is needed and the fake is honored exactly once at init.
    """
    monkeypatch.setattr(module, "probe", lambda: _caps(fts5=fts5))


# ── knowledge.store.KnowledgeStore — RAISE ──────────────────────────────────────


class TestKnowledgeStoreRaises:
    def test_init_raises_with_remedy_when_fts5_absent(self, monkeypatch, tmp_path):
        _patch_probe(monkeypatch, store_mod, fts5=False)
        with pytest.raises(RuntimeError) as exc:
            store_mod.KnowledgeStore(str(tmp_path / "knowledge.db"))
        assert FTS5_REMEDY in str(exc.value)

    def test_no_db_file_created_when_it_raises(self, monkeypatch, tmp_path):
        """RAISE happens BEFORE connect/schema — the failure is at init, not mid-query."""
        _patch_probe(monkeypatch, store_mod, fts5=False)
        db = tmp_path / "knowledge.db"
        with pytest.raises(RuntimeError):
            store_mod.KnowledgeStore(str(db))
        assert not db.exists()

    def test_happy_path_opens_and_searches(self, monkeypatch, tmp_path):
        """With FTS5 present the guard is a no-op: the store opens and search works."""
        _patch_probe(monkeypatch, store_mod, fts5=True)
        store = store_mod.KnowledgeStore(str(tmp_path / "knowledge.db"))
        # Empty query short-circuits; a real one exercises the items_fts MATCH path.
        assert store.search_items_fts("anything") == []


# ── memory.MemoryStore — DEGRADE ─────────────────────────────────────────────────


class TestMemoryStoreDegrades:
    def test_init_succeeds_and_logs_remedy(self, monkeypatch, tmp_path, caplog):
        _patch_probe(monkeypatch, memory_mod, fts5=False)
        with caplog.at_level(logging.WARNING, logger=memory_mod.logger.name):
            store = memory_mod.MemoryStore(workspace=tmp_path)
        assert store._fts_available is False
        assert FTS5_REMEDY in caplog.text

    def test_non_search_functionality_still_works(self, monkeypatch, tmp_path):
        """The markdown projection — the module's real job — is unaffected by no FTS5."""
        _patch_probe(monkeypatch, memory_mod, fts5=False)
        store = memory_mod.MemoryStore(workspace=tmp_path)
        store.init()
        store.write_preferences("# User Preferences\n- likes concise answers\n")
        assert "concise" in store.read_preferences()

    def test_search_paths_are_clean_noops(self, monkeypatch, tmp_path):
        """search/rebuild/index degrade without raising and without touching a DB."""
        _patch_probe(monkeypatch, memory_mod, fts5=False)
        store = memory_mod.MemoryStore(workspace=tmp_path)
        store.init()
        store.write_preferences("# User Preferences\n- searchable token here\n")
        assert store.search("searchable") == []
        assert store.rebuild_index() == 0
        assert not store._index_db.exists()

    def test_happy_path_search_finds_content(self, monkeypatch, tmp_path):
        """With FTS5 present the guard is a no-op: indexing and search work normally."""
        _patch_probe(monkeypatch, memory_mod, fts5=True)
        store = memory_mod.MemoryStore(workspace=tmp_path)
        store.init()
        store.write_preferences("# User Preferences\n- zebra crossing detail\n")
        results = store.search("zebra")
        assert any("zebra" in r["snippet"].lower() for r in results)


# ── session_search — DEGRADE ─────────────────────────────────────────────────────


class TestSessionSearchDegrades:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        ss.reset_for_tests()
        yield
        ss.reset_for_tests()

    def test_connect_returns_none_and_logs_once(self, monkeypatch, caplog):
        _patch_probe(monkeypatch, ss, fts5=False)
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            assert ss._connect() is None
            assert ss._connect() is None  # second call must NOT re-log
        assert caplog.text.count(FTS5_REMEDY) == 1

    def test_search_returns_empty_without_raising(self, monkeypatch):
        """A no-FTS5 build degrades to [] so the caller falls back to the linear scan."""
        _patch_probe(monkeypatch, ss, fts5=False)
        assert ss.search_sessions("bedrock provider") == []

    def test_indexing_is_a_clean_noop(self, monkeypatch, tmp_path):
        """index_session must not raise and must not create the index DB."""
        _patch_probe(monkeypatch, ss, fts5=False)
        assert ss.index_session("k1", "title", "searchable body text") is False
        assert not ss.db_path().exists()

    def test_happy_path_indexes_and_finds(self, monkeypatch):
        """With FTS5 present the guard is a no-op: indexing and search work normally."""
        _patch_probe(monkeypatch, ss, fts5=True)
        assert ss.index_session("k1", "Bedrock", "how to configure the bedrock provider")
        assert [r["key"] for r in ss.search_sessions("bedrock")] == ["k1"]
