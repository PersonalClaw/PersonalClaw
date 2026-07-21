"""auto-campaign-scratch-workspace: a loop's scratch dir lifecycle.

Default = KEEP (a completed loop's dir persists). Opt-in
``auto_teardown_on_complete`` reclaims the loop's OWN dir after completion — but
only after the deliverable has been graduated to a permanent artifact, and never
an externally-bound workspace_dir.
"""

from __future__ import annotations

import pytest

import personalclaw.loop.store as store_mod
from personalclaw.loop import lifecycle
from personalclaw.loop.loop import Loop


@pytest.fixture
def loop_home(tmp_path, monkeypatch):
    # Point the loop store + dirs at a temp home.
    monkeypatch.setattr(store_mod, "_db_path", lambda: tmp_path / "loops.db")
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    return tmp_path


def _mk(auto_teardown: bool) -> Loop:
    return Loop(
        id="",
        name="Scratch test",
        kind="goal",
        task="do a thing",
        auto_teardown_on_complete=auto_teardown,
    )


def test_flag_persists_through_store(loop_home):
    created = store_mod.create(_mk(True))
    got = store_mod.get(created.id)
    assert got.auto_teardown_on_complete is True
    # And a default loop keeps it False.
    other = store_mod.create(_mk(False))
    assert store_mod.get(other.id).auto_teardown_on_complete is False


def test_should_teardown_reflects_flag():
    assert lifecycle.should_teardown(_mk(True)) is True
    assert lifecycle.should_teardown(_mk(False)) is False


def test_teardown_removes_scratch_dir(loop_home):
    created = store_mod.create(_mk(True))
    d = store_mod.loop_dir(created.id)
    assert d is not None and d.is_dir()
    (d / "REPORT.md").write_text("the deliverable")
    assert lifecycle.teardown_scratch(created.id) is True
    assert not d.exists()


def test_teardown_is_safe_when_absent(loop_home):
    # No dir → no error, returns False.
    assert lifecycle.teardown_scratch("deadbeef") is False


def test_default_loop_dir_persists(loop_home):
    # A non-scratch loop's dir is NOT auto-removed (should_teardown gates it).
    created = store_mod.create(_mk(False))
    d = store_mod.loop_dir(created.id)
    assert d.is_dir()
    if lifecycle.should_teardown(store_mod.get(created.id)):
        lifecycle.teardown_scratch(created.id)
    assert d.is_dir()  # still there — never auto-torn-down


def test_migration_adds_column_to_legacy_db(loop_home, tmp_path):
    # Simulate a pre-existing DB without the column, then confirm _connect migrates it.
    import sqlite3

    db = tmp_path / "loops.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE loops (id TEXT PRIMARY KEY, name TEXT, kind TEXT, task TEXT, "
        "created_at REAL, status TEXT)"
    )
    conn.commit()
    conn.close()
    # _connect() runs the idempotent migration.
    conn2 = store_mod._connect()
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(loops)").fetchall()}
    conn2.close()
    assert "auto_teardown_on_complete" in cols
