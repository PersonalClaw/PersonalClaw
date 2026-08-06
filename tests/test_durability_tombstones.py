"""DURABILITY-AND-SYNC §4 / DAS-6c-iii-a — the sync-only tombstone side-log (owner Fork B).

Hard-delete entity stores (tasks/projects) keep their unlink UX but append {id, deleted_at}
to a sync-only _tombstones.jsonl the exporter folds into the entity-dir shard, so a delete
propagates across machines without the store gaining a soft-delete surface. The log is
invisible to the store's own reads; a GC prunes it past the sync horizon.
"""

from __future__ import annotations

import json

from personalclaw.durability import shards
from personalclaw.durability import tombstones as tomb


class TestRecordRead:
    def test_record_then_read(self, tmp_path):
        tomb.record_tombstone(tmp_path, "task-1", now="2026-08-06T00:00:00Z")
        rows = tomb.read_tombstones(tmp_path)
        assert rows == [{"id": "task-1", "deleted_at": "2026-08-06T00:00:00Z"}]

    def test_missing_log_reads_empty(self, tmp_path):
        assert tomb.read_tombstones(tmp_path) == []

    def test_empty_row_id_is_ignored(self, tmp_path):
        tomb.record_tombstone(tmp_path, "", now="t")
        assert tomb.read_tombstones(tmp_path) == []

    def test_later_marker_wins_per_id(self, tmp_path):
        tomb.record_tombstone(tmp_path, "x", now="2026-08-01T00:00:00Z")
        tomb.record_tombstone(tmp_path, "x", now="2026-08-09T00:00:00Z")  # recreated, re-deleted
        rows = tomb.read_tombstones(tmp_path)
        assert rows == [{"id": "x", "deleted_at": "2026-08-09T00:00:00Z"}]  # one row, later ts

    def test_corrupt_lines_are_skipped(self, tmp_path):
        (tmp_path / tomb.TOMBSTONE_FILE).write_text(
            '{"id":"ok","deleted_at":"t"}\nnot json\n\n', encoding="utf-8"
        )
        assert tomb.read_tombstones(tmp_path) == [{"id": "ok", "deleted_at": "t"}]

    def test_log_file_is_underscore_jsonl_invisible_to_entity_glob(self, tmp_path):
        # The store globs *.json; the exporter's entity extraction does too. A _*.jsonl
        # side-log must not surface as a fake entity.
        tomb.record_tombstone(tmp_path, "gone", now="t")
        (tmp_path / "real.json").write_text('{"id": "real"}', encoding="utf-8")
        extracted = {r["id"] for r in shards._json_rows_from_entity_dir(tmp_path)}
        assert extracted == {"real"}  # the side-log did not leak in


class TestMergeIntoRows:
    def test_tombstone_appended_when_row_is_gone(self, tmp_path):
        tomb.record_tombstone(tmp_path, "deleted-1", now="2026-08-06")
        live = [{"id": "alive", "data": {}}]
        merged = tomb.merge_into_rows(tmp_path, live)
        ids = {r["id"] for r in merged}
        assert ids == {"alive", "deleted-1"}
        dead = next(r for r in merged if r["id"] == "deleted-1")
        assert dead["deleted_at"] == "2026-08-06"

    def test_tombstone_dropped_when_row_is_live_again(self, tmp_path):
        # Row was deleted, then recreated (same id now has a live file). The stale marker
        # must NOT delete the resurrected entity — drop it.
        tomb.record_tombstone(tmp_path, "recreated", now="2026-08-01")
        live = [{"id": "recreated", "data": {"title": "back"}}]
        merged = tomb.merge_into_rows(tmp_path, live)
        assert merged == live  # no tombstone row added

    def test_no_log_is_a_passthrough(self, tmp_path):
        live = [{"id": "a", "data": {}}]
        assert tomb.merge_into_rows(tmp_path, live) is live  # same object, no work


class TestPrune:
    def test_prunes_entries_at_or_before_horizon(self, tmp_path):
        tomb.record_tombstone(tmp_path, "old", now="2026-01-01T00:00:00Z")
        tomb.record_tombstone(tmp_path, "new", now="2026-08-06T00:00:00Z")
        removed = tomb.prune(tmp_path, keep_after="2026-06-01T00:00:00Z")
        assert removed == 1
        assert {r["id"] for r in tomb.read_tombstones(tmp_path)} == {"new"}

    def test_prune_empty_log_is_zero(self, tmp_path):
        assert tomb.prune(tmp_path, keep_after="2026-01-01") == 0


class TestExportFold:
    def test_hard_deleted_task_marker_rides_the_export(self, tmp_path):
        # A realistic tasks dir: one live task + a side-log for a hard-deleted one.
        home = tmp_path / "home"
        tasks = home / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "t-live.json").write_text('{"id": "t-live", "title": "here"}', encoding="utf-8")
        tomb.record_tombstone(tasks, "t-deleted", now="2026-08-06T00:00:00Z")

        out = tmp_path / "shards"
        shards.export_shards(home, out)
        shard = out / "tasks" / "entities.jsonl"
        rows = [json.loads(ln) for ln in shard.read_text().splitlines() if ln.strip()]
        by_id = {r["id"]: r for r in rows}
        assert "t-live" in by_id  # the live task
        assert by_id["t-deleted"].get("deleted_at")  # the delete marker rode along
