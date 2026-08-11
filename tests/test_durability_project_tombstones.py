"""DURABILITY-AND-SYNC §4 / DAS-6c-iii-c — project-subtree delete tombstones.

A project is a SUBTREE (projects/<id>/project.json + context/*.json), and the exporter emits one
row per *.json (id = relpath under the `projects` entry dir), so delete_project must tombstone
EVERY synced row it removes — not one project-id marker. worktrees/ is derived (never exported),
so it gets no tombstone.
"""

from __future__ import annotations

import json

from personalclaw.config.loader import config_dir
from personalclaw.durability import shards
from personalclaw.durability import tombstones as tomb


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert config_dir() == tmp_path
    from personalclaw.tasks.hierarchy import HierarchyStore

    return HierarchyStore()


class TestProjectSubtreeTombstones:
    def test_every_synced_row_gets_a_tombstone(self, tmp_path, monkeypatch):
        store = _store(tmp_path, monkeypatch)
        proj = store.create_project(name="doomed")
        # Give it context + a worktree (derived) so we can prove the worktree is skipped.
        ctx = store.context_dir(proj.id)
        (ctx / "brief.json").write_text('{"note": "x"}', encoding="utf-8")
        wt = tmp_path / "projects" / proj.id / "worktrees"
        wt.mkdir(parents=True, exist_ok=True)
        (wt / "checkout.json").write_text('{"git": "owned"}', encoding="utf-8")

        assert store.delete_project(proj.id) is True

        markers = {t["id"] for t in tomb.read_tombstones(tmp_path / "projects")}
        assert f"{proj.id}/project" in markers  # the project entity row
        assert f"{proj.id}/context/brief" in markers  # the context row
        assert f"{proj.id}/worktrees/checkout" not in markers  # derived — no tombstone

    def test_deleted_project_markers_ride_the_export(self, tmp_path, monkeypatch):
        store = _store(tmp_path, monkeypatch)
        keep = store.create_project(name="keep")
        drop = store.create_project(name="drop")
        store.delete_project(drop.id)

        out = tmp_path / "shards"
        shards.export_shards(tmp_path, out)
        rows = [
            json.loads(ln)
            for ln in (out / "projects" / "entities.jsonl").read_text().splitlines()
            if ln.strip()
        ]
        by_id = {r["id"]: r for r in rows}
        # The kept project's row is live; the dropped project's row is a tombstone.
        assert f"{keep.id}/project" in by_id and not by_id[f"{keep.id}/project"].get("deleted_at")
        assert by_id[f"{drop.id}/project"].get("deleted_at")

    def test_default_project_cannot_be_deleted(self, tmp_path, monkeypatch):
        import pytest

        store = _store(tmp_path, monkeypatch)
        default = next(p for p in store.list_projects() if p.is_builtin_project())
        with pytest.raises(ValueError):
            store.delete_project(default.id)
        # And no tombstone was written for a delete that didn't happen.
        assert tomb.read_tombstones(tmp_path / "projects") == []
