"""M0: the typed MemoryRecord — round-trips the two legacy row shapes + the
embedding blob encoding, with zero behavior change to the store.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.memory_record import (
    MemoryCapabilities,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryTier,
    blob_to_embedding,
    embedding_to_blob,
)


# ── embedding blob encoding (must match vector_memory's on-disk format) ──


def test_embedding_blob_roundtrip_normalizes():
    emb = [3.0, 4.0]  # norm 5 → normalized [0.6, 0.8]
    blob = embedding_to_blob(emb)
    back = blob_to_embedding(blob)
    assert back is not None
    assert abs(back[0] - 0.6) < 1e-6
    assert abs(back[1] - 0.8) < 1e-6


def test_embedding_blob_none():
    assert embedding_to_blob(None) is None
    assert blob_to_embedding(None) is None
    assert blob_to_embedding(b"") is None


def test_embedding_blob_matches_store_encoding(tmp_path):
    # The store writes embeddings via the same normalize→float32 path; a record
    # built from a written episodic row must decode to the normalized vector.
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    store.init()
    store.embed_fn = lambda t: [1.0, 0.0, 0.0]
    assert store.write_episodic("a fragment to remember", source="test") is True
    rows = store.db.execute("SELECT * FROM episodic_memories WHERE is_deleted=0").fetchall()
    rec = MemoryRecord.from_episodic_row(rows[0])
    assert rec.kind == MemoryKind.EPISODIC
    assert rec.embedding is not None
    # normalized unit vector along x
    assert abs(rec.embedding[0] - 1.0) < 1e-6
    store.close()


# ── semantic row → record ──


def test_from_semantic_row_fact():
    row = {
        "key": "pref.editor", "value_json": json.dumps("vim"), "confidence": 0.9,
        "source": "user_explicit", "recall_count": 3, "superseded_by": None,
        "invalidated_at": None, "is_deleted": 0,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-02T00:00:00+00:00",
        "embedding": None,
    }
    rec = MemoryRecord.from_semantic_row(row)
    assert rec.kind == MemoryKind.SEMANTIC
    assert rec.id == "pref.editor"
    assert rec.value == "vim"
    assert rec.text == "vim"
    assert rec.confidence == 0.9
    assert rec.recall_count == 3
    # default axes preserve today's global/durable behavior
    assert rec.scope == MemoryScope.GLOBAL
    assert rec.tier == MemoryTier.SEMANTIC


def test_from_semantic_row_lesson_kind_by_key():
    row = {
        "key": "lesson.abc123", "value_json": json.dumps("always run tests before pushing"),
        "confidence": 0.8, "source": "consolidation", "is_deleted": 0,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    }
    rec = MemoryRecord.from_semantic_row(row)
    assert rec.kind == MemoryKind.LESSON
    assert "tests" in rec.text


def test_from_semantic_row_non_string_value():
    row = {
        "key": "pref.config", "value_json": json.dumps({"a": 1, "b": [2, 3]}),
        "confidence": 0.7, "source": "x", "is_deleted": 0,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    }
    rec = MemoryRecord.from_semantic_row(row)
    assert rec.value == {"a": 1, "b": [2, 3]}
    # text is the JSON projection for search/embed
    assert json.loads(rec.text) == {"a": 1, "b": [2, 3]}


# ── episodic row → record ──


def test_from_episodic_row():
    row = {
        "id": "uuid-1", "conversation_id": "conv-9", "text": "discussed the migration plan",
        "embedding": None, "tags": json.dumps(["migration", "plan"]), "importance": 0.7,
        "created_at": "2026-01-01T00:00:00+00:00", "last_accessed_at": "2026-01-03T00:00:00+00:00",
        "is_deleted": 0,
    }
    rec = MemoryRecord.from_episodic_row(row)
    assert rec.kind == MemoryKind.EPISODIC
    assert rec.id == "uuid-1"
    assert rec.text == "discussed the migration plan"
    assert rec.tags == ["migration", "plan"]
    assert rec.importance == 0.7
    assert rec.conversation_id == "conv-9"
    assert rec.tier == MemoryTier.EPISODIC


def test_kind_and_axis_string_coercion():
    rec = MemoryRecord(id="x", kind="semantic", tier="working", scope="session")
    assert rec.kind is MemoryKind.SEMANTIC
    assert rec.tier is MemoryTier.WORKING
    assert rec.scope is MemoryScope.SESSION


def test_to_public_dict_omits_embedding_bytes():
    rec = MemoryRecord(id="x", kind=MemoryKind.SEMANTIC, text="hi", embedding=[1.0, 0.0])
    d = rec.to_public_dict()
    assert "embedding" not in d
    assert d["kind"] == "semantic"
    assert d["scope"] == "global"


# ── capabilities ──


def test_capabilities_defaults_and_dict():
    caps = MemoryCapabilities(vector=True, event_log=True)
    d = caps.to_dict()
    assert d == {"vector": True, "transactional_batch": False, "event_log": True, "full_text_search": True}


# ── store-level typed-record view (M0, read-only over both tables) ──


def _store(tmp_path):
    from personalclaw.vector_memory import VectorMemoryStore

    s = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    s.init()
    return s


def test_store_capabilities_track_embed_fn(tmp_path):
    s = _store(tmp_path)
    caps = s.capabilities()
    assert caps.vector is False  # no embed_fn yet → degrade to FTS
    assert caps.event_log is True and caps.transactional_batch is True
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    assert s.capabilities().vector is True
    s.close()


def test_store_get_record_semantic_and_episodic(tmp_path):
    s = _store(tmp_path)
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    s.set_semantic("pref.editor", "vim", confidence=0.9, source="user_explicit")
    assert s.write_episodic("a fragment worth keeping", source="test") is True

    rec = s.get_record("pref.editor")
    assert rec is not None and rec.kind == MemoryKind.SEMANTIC and rec.value == "vim"

    # episodic id
    eid = s.db.execute("SELECT id FROM episodic_memories WHERE is_deleted=0").fetchone()["id"]
    erec = s.get_record(eid)
    assert erec is not None and erec.kind == MemoryKind.EPISODIC

    assert s.get_record("does.not.exist") is None
    s.close()


def test_store_iter_records_filters_by_kind(tmp_path):
    s = _store(tmp_path)
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    s.set_semantic("pref.editor", "vim", confidence=0.9, source="user_explicit")
    s.write_lesson("always run tests before pushing", category="process")
    s.write_episodic("a discrete fragment", source="test")

    all_recs = s.iter_records()
    kinds = {r.kind for r in all_recs}
    assert MemoryKind.SEMANTIC in kinds
    assert MemoryKind.LESSON in kinds
    assert MemoryKind.EPISODIC in kinds

    only_lessons = s.iter_records(kinds={"lesson"})
    assert only_lessons and all(r.kind == MemoryKind.LESSON for r in only_lessons)

    only_epi = s.iter_records(kinds={"episodic"})
    assert only_epi and all(r.kind == MemoryKind.EPISODIC for r in only_epi)
    s.close()
