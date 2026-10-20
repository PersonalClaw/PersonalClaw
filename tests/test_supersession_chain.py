"""Supersession-by-pointer for semantic memory (never delete-on-conflict)."""

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


def test_supersede_columns_migrated(vs):
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    row = vs.db.execute(
        "SELECT superseded_by, invalidated_at FROM semantic_memory WHERE key='pref.x'"
    ).fetchone()
    assert row["superseded_by"] is None and row["invalidated_at"] is None


def test_supersede_sets_pointer_and_invalidates(vs):
    vs.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
    vs.set_semantic("pref.editor_v2", "neovim", 1.0, "user_explicit")
    assert vs.supersede_semantic("pref.editor", "pref.editor_v2", "user_explicit") is True
    row = vs.db.execute(
        "SELECT is_deleted, superseded_by, invalidated_at FROM semantic_memory WHERE key='pref.editor'"  # noqa: E501
    ).fetchone()
    assert row["is_deleted"] == 1
    assert row["superseded_by"] == "pref.editor_v2"
    assert row["invalidated_at"] is not None


def test_superseded_row_not_hard_deleted(vs):
    """The old value is still on disk (reversible), just invalidated."""
    vs.set_semantic("pref.old", "A", 1.0, "user_explicit")
    vs.set_semantic("pref.new", "B", 1.0, "user_explicit")
    vs.supersede_semantic("pref.old", "pref.new", "user_explicit")
    row = vs.db.execute("SELECT value_json FROM semantic_memory WHERE key='pref.old'").fetchone()
    assert row is not None and "A" in row["value_json"]


def test_supersede_unknown_key_returns_false(vs):
    assert vs.supersede_semantic("nonexistent", "x", "user_explicit") is False


def test_get_supersession_chain_follows_pointer(vs):
    vs.set_semantic("pref.a", "1", 1.0, "user_explicit")
    vs.set_semantic("pref.b", "2", 1.0, "user_explicit")
    vs.set_semantic("pref.c", "3", 1.0, "user_explicit")
    vs.supersede_semantic("pref.a", "pref.b", "user_explicit")
    vs.supersede_semantic("pref.b", "pref.c", "user_explicit")
    chain = vs.get_supersession_chain("pref.a")
    assert [c["key"] for c in chain] == ["pref.a", "pref.b", "pref.c"]


def test_chain_terminates_on_live_entry(vs):
    vs.set_semantic("pref.a", "1", 1.0, "user_explicit")
    vs.set_semantic("pref.b", "2", 1.0, "user_explicit")
    vs.supersede_semantic("pref.a", "pref.b", "user_explicit")
    # pref.b has no superseded_by → chain ends there.
    chain = vs.get_supersession_chain("pref.a")
    assert chain[-1]["key"] == "pref.b"
    assert chain[-1]["superseded_by"] is None


def test_chain_handles_cycle_safely(vs):
    """A pathological cycle (a→b→a) must not infinite-loop."""
    vs.set_semantic("pref.a", "1", 1.0, "user_explicit")
    vs.set_semantic("pref.b", "2", 1.0, "user_explicit")
    vs.supersede_semantic("pref.a", "pref.b", "user_explicit")
    # Force a cycle by hand.
    vs.db.execute("UPDATE semantic_memory SET superseded_by='pref.a' WHERE key='pref.b'")
    vs.db.commit()
    chain = vs.get_supersession_chain("pref.a")
    assert {c["key"] for c in chain} == {"pref.a", "pref.b"}  # each visited once


def test_supersede_logs_event(vs):
    vs.set_semantic("pref.a", "1", 1.0, "user_explicit")
    vs.set_semantic("pref.b", "2", 1.0, "user_explicit")
    vs.supersede_semantic("pref.a", "pref.b", "user_explicit")
    events = vs.db.execute(
        "SELECT event_type, new_value FROM memory_events WHERE memory_key='pref.a' AND event_type='supersede'"  # noqa: E501
    ).fetchall()
    assert events and events[-1]["new_value"] == "pref.b"
