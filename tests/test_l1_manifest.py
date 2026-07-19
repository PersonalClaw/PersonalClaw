"""L1 memory manifest + recall-count + memory_recall tool (D-MEM-INJECT half 1)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def vs():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return store


def test_recall_count_column_migrated(vs):
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    row = vs.db.execute("SELECT recall_count FROM semantic_memory WHERE key='pref.x'").fetchone()
    assert row[0] == 0  # default


def test_record_recall_bumps_count(vs):
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    vs.record_recall(["pref.x", "pref.x", "pref.x"])
    row = vs.db.execute("SELECT recall_count FROM semantic_memory WHERE key='pref.x'").fetchone()
    assert row[0] == 3


def test_record_recall_empty_is_noop(vs):
    vs.record_recall([])  # must not raise


def test_record_recall_ignores_unknown_keys(vs):
    vs.record_recall(["nonexistent.key"])  # no row to bump → silent


def test_l1_manifest_empty_when_no_facts(vs):
    assert vs.get_l1_manifest() == ""


def test_l1_manifest_ranks_by_recall_count(vs):
    vs.set_semantic("pref.color", "blue", 1.0, "user_explicit")
    vs.set_semantic("pref.lang", "python", 1.0, "user_explicit")
    vs.record_recall(["pref.lang", "pref.lang"])  # lang recalled more
    man = vs.get_l1_manifest()
    assert "pref.lang" in man and "pref.color" in man
    assert man.index("pref.lang") < man.index("pref.color")


def test_l1_manifest_excludes_lessons(vs):
    vs.set_semantic("lesson.foo", "bar", 1.0, "user_explicit")
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    man = vs.get_l1_manifest()
    assert "lesson.foo" not in man
    assert "pref.x" in man


def test_l1_manifest_respects_cap(vs):
    for i in range(50):
        vs.set_semantic(f"pref.k{i}", "x" * 100, 1.0, "user_explicit")
    man = vs.get_l1_manifest(cap=300)
    # The body (excluding the framing) must stay near the cap.
    assert len(man) < 800  # framing + ~300 cap, not all 50 entries


def test_l1_manifest_respects_limit(vs):
    for i in range(30):
        vs.set_semantic(f"pref.k{i}", "v", 1.0, "user_explicit")
    man = vs.get_l1_manifest(cap=100_000, limit=5)
    # At most `limit` fact lines (plus 2 framing lines).
    fact_lines = [ln for ln in man.splitlines() if ln.startswith("pref.")]
    assert len(fact_lines) <= 5


# ── get_context cutover (memory.py) ──


def test_get_context_uses_manifest_when_l1_on(tmp_path, monkeypatch):
    # Context composition (L1-manifest vs legacy full-block) is the MemoryService's
    # job (L3) post-M2; the MemoryStore is just the markdown projection.
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_service import MemoryService

    ms = MemoryStore(workspace=tmp_path / "ws")
    ms.init()
    # Attach a vector store with a fact.
    vs = VectorMemoryStore(db_path=tmp_path / "vec.db")
    vs.init()
    vs.set_semantic("pref.tone", "concise", 1.0, "user_explicit")
    ms._vector_store = vs
    svc = MemoryService(ms)

    ctx_l1 = svc.get_context(query="tone", l1_manifest=True)
    assert "Memory manifest" in ctx_l1
    # The full semantic block header is NOT used in L1 mode.
    assert "Semantic Memory — factual key-value pairs" not in ctx_l1

    # Legacy mode: empty query surfaces recent entries (the full semantic block).
    ctx_legacy = svc.get_context(query="", l1_manifest=False)
    assert "Memory manifest" not in ctx_legacy
    assert "Semantic Memory — factual key-value pairs" in ctx_legacy
