"""Reversible memory WAL — undo_event reverses logged mutations."""

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


def _event_id(vs, etype, key):
    return vs.db.execute(
        "SELECT id FROM memory_events WHERE event_type=? AND memory_key=? ORDER BY id DESC LIMIT 1",
        (etype, key),
    ).fetchone()[0]


def test_undone_at_column_migrated(vs):
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    row = vs.db.execute("SELECT undone_at FROM memory_events LIMIT 1").fetchone()
    assert row["undone_at"] is None


def test_undo_create_soft_deletes(vs):
    vs.set_semantic("pref.x", "A", 1.0, "user_explicit")
    ok, _ = vs.undo_event(_event_id(vs, "create", "pref.x"))
    assert ok
    assert vs.get_semantic("pref.x") is None


def test_undo_update_restores_old_value(vs):
    vs.set_semantic("pref.x", "A", 1.0, "user_explicit")
    vs.set_semantic("pref.x", "B", 1.0, "user_explicit")  # update
    ok, _ = vs.undo_event(_event_id(vs, "update", "pref.x"))
    assert ok
    import json
    assert json.loads(vs.get_semantic("pref.x")["value_json"]) == "A"


def test_undo_delete_restores_row(vs):
    vs.set_semantic("pref.x", "A", 1.0, "user_explicit")
    vs.delete_semantic("pref.x", "user_explicit")
    assert vs.get_semantic("pref.x") is None
    ok, _ = vs.undo_event(_event_id(vs, "delete", "pref.x"))
    assert ok
    assert vs.get_semantic("pref.x") is not None


def test_undo_supersede_restores_and_clears_pointer(vs):
    vs.set_semantic("pref.a", "1", 1.0, "user_explicit")
    vs.set_semantic("pref.b", "2", 1.0, "user_explicit")
    vs.supersede_semantic("pref.a", "pref.b", "user_explicit")
    ok, _ = vs.undo_event(_event_id(vs, "supersede", "pref.a"))
    assert ok
    row = vs.db.execute(
        "SELECT is_deleted, superseded_by FROM semantic_memory WHERE key='pref.a'"
    ).fetchone()
    assert row["is_deleted"] == 0 and row["superseded_by"] is None


def test_undo_is_idempotent(vs):
    vs.set_semantic("pref.x", "A", 1.0, "user_explicit")
    eid = _event_id(vs, "create", "pref.x")
    assert vs.undo_event(eid)[0] is True
    ok, msg = vs.undo_event(eid)
    assert ok is True and "already undone" in msg


def test_undo_unknown_event(vs):
    ok, msg = vs.undo_event(999999)
    assert ok is False and "not found" in msg


def test_undo_marks_undone_at(vs):
    vs.set_semantic("pref.x", "A", 1.0, "user_explicit")
    eid = _event_id(vs, "create", "pref.x")
    vs.undo_event(eid)
    row = vs.db.execute("SELECT undone_at FROM memory_events WHERE id=?", (eid,)).fetchone()
    assert row["undone_at"] is not None


def test_undo_logs_an_undo_event(vs):
    vs.set_semantic("pref.x", "A", 1.0, "user_explicit")
    vs.undo_event(_event_id(vs, "create", "pref.x"))
    undo_events = vs.db.execute("SELECT * FROM memory_events WHERE event_type='undo'").fetchall()
    assert undo_events


def test_non_semantic_event_not_reversible(vs):
    # Manually log an episodic event; it must not be reversible via this path.
    vs._log_event("create", "episodic", "ep.1", None, "x", "test")
    eid = _event_id(vs, "create", "ep.1")
    ok, msg = vs.undo_event(eid)
    assert ok is False and "not reversible" in msg
