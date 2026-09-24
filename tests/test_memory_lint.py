"""Periodic memory-health lint — auto-fix safe issues, flag the rest."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.memory_lint import lint_memory
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def vs():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return store


def _age(vs, key, *, updated=None, invalidated=None):
    if updated:
        vs.db.execute("UPDATE semantic_memory SET updated_at=? WHERE key=?", (updated, key))
    if invalidated:
        vs.db.execute("UPDATE semantic_memory SET invalidated_at=? WHERE key=?", (invalidated, key))
    vs.db.commit()


def test_clean_store_has_no_flags(vs):
    vs.set_semantic("pref.x", "a clear, recent fact about the user", 1.0, "user_explicit")
    vs.record_recall(["pref.x"])  # recalled → not stale
    report = lint_memory(vs)
    assert report.flags == []


def test_flags_stale_facts(vs):
    vs.set_semantic("pref.old", "some old never-recalled fact", 1.0, "user_explicit")
    _age(vs, "pref.old", updated="2020-01-01T00:00:00+00:00")
    report = lint_memory(vs)
    assert any(f["check"] == "stale" and f["key"] == "pref.old" for f in report.flags)


def test_recalled_fact_not_stale(vs):
    vs.set_semantic("pref.live", "old but used", 1.0, "user_explicit")
    _age(vs, "pref.live", updated="2020-01-01T00:00:00+00:00")
    vs.record_recall(["pref.live"])  # recall_count > 0 → not stale
    report = lint_memory(vs)
    assert not any(f["key"] == "pref.live" for f in report.flags if f["check"] == "stale")


def test_flags_near_duplicates(vs):
    vs.set_semantic("pref.a", "prefers the vim text editor for all coding", 1.0, "user_explicit")
    vs.set_semantic(
        "pref.b", "prefers the vim text editor for all coding tasks", 1.0, "user_explicit"
    )
    report = lint_memory(vs)
    assert any(f["check"] == "near_dup" for f in report.flags)


def test_flags_sparse(vs):
    vs.set_semantic("pref.empty", "x", 1.0, "user_explicit")
    report = lint_memory(vs)
    assert any(f["check"] == "sparse" and f["key"] == "pref.empty" for f in report.flags)


def test_auto_purges_long_superseded(vs):
    vs.set_semantic("pref.gone", "old", 1.0, "user_explicit")
    vs.set_semantic("pref.new", "new", 1.0, "user_explicit")
    vs.supersede_semantic("pref.gone", "pref.new", "user_explicit")
    _age(vs, "pref.gone", invalidated="2020-01-01T00:00:00+00:00")
    report = lint_memory(vs)
    assert report.auto_fixed["superseded_purged"] == 1
    assert (
        vs.db.execute("SELECT COUNT(*) FROM semantic_memory WHERE key='pref.gone'").fetchone()[0]
        == 0
    )


def test_recent_superseded_not_purged(vs):
    vs.set_semantic("pref.gone", "old", 1.0, "user_explicit")
    vs.set_semantic("pref.new", "new", 1.0, "user_explicit")
    vs.supersede_semantic("pref.gone", "pref.new", "user_explicit")  # invalidated_at = now
    report = lint_memory(vs)
    assert report.auto_fixed["superseded_purged"] == 0
    assert (
        vs.db.execute("SELECT COUNT(*) FROM semantic_memory WHERE key='pref.gone'").fetchone()[0]
        == 1
    )


def test_lessons_excluded_from_checks(vs):
    vs.write_lesson("a lesson rule that is quite old and never recalled", "tool")
    # Age it; lessons must not be flagged stale by this scan (they ride their own path).
    vs.db.execute(
        "UPDATE semantic_memory SET updated_at='2020-01-01T00:00:00+00:00' WHERE key LIKE 'lesson.%'"  # noqa: E501
    )
    vs.db.commit()
    report = lint_memory(vs)
    assert not any(f["key"].startswith("lesson.") for f in report.flags)


def test_contradiction_scan_uses_judge(vs):
    vs.set_semantic("pref.a", "always deploy on friday afternoon", 1.0, "user_explicit")
    vs.set_semantic("pref.b", "never deploy on friday afternoon", 1.0, "user_explicit")
    # These overlap heavily → near-dup candidate → judge consulted.
    report = lint_memory(vs, judge=lambda a, b: True)
    assert any(f["check"] == "contradiction" for f in report.flags)


def test_no_judge_no_contradiction_flags(vs):
    vs.set_semantic("pref.a", "always deploy on friday afternoon", 1.0, "user_explicit")
    vs.set_semantic("pref.b", "never deploy on friday afternoon", 1.0, "user_explicit")
    report = lint_memory(vs, judge=None)
    assert not any(f["check"] == "contradiction" for f in report.flags)


def test_report_to_dict(vs):
    vs.set_semantic("pref.empty", "x", 1.0, "user_explicit")
    d = lint_memory(vs).to_dict()
    assert "auto_fixed" in d and "flags" in d and d["flag_count"] == len(d["flags"])
