"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-c — apply merged rows back to the live store.

The inverse of shards.py's row extraction: after merge reconciles a peer's rows, write the
merged set back into each entry's native on-disk shape. The core property is round-trip —
extract → apply (with an empty remote) reproduces the same files — plus tombstone deletes
propagate and non-row kinds raise.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.durability import writeback
from personalclaw.durability.shards import (
    _json_rows_from_entity_dir,
    _jsonl_rows_by_year,
)


class TestEntityDir:
    def test_writes_one_file_per_row(self, tmp_path):
        dest = tmp_path / "tasks"
        rows = [{"id": "t1", "data": {"title": "a"}}, {"id": "t2", "data": {"title": "b"}}]
        r = writeback.apply_rows(inv.KIND_JSON_ENTITY_DIR, dest, rows)
        assert r.written == 2
        assert json.loads((dest / "t1.json").read_text())["title"] == "a"
        assert json.loads((dest / "t2.json").read_text())["title"] == "b"

    def test_round_trips_the_extractor(self, tmp_path):
        # Build a live entity dir, extract rows the way export does, wipe, re-apply:
        # the reconstructed dir must contain the same entities.
        src = tmp_path / "live"
        src.mkdir()
        (src / "a.json").write_text(json.dumps({"n": 1}), encoding="utf-8")
        (src / "b.json").write_text(json.dumps({"n": 2}), encoding="utf-8")
        rows = _json_rows_from_entity_dir(src)  # [{"id":"a","data":{"n":1}}, ...]
        dest = tmp_path / "restored"
        writeback.apply_rows(inv.KIND_JSON_ENTITY_DIR, dest, rows)
        assert _json_rows_from_entity_dir(dest) == rows  # same rows back out

    def test_tombstone_removes_the_file(self, tmp_path):
        dest = tmp_path / "tasks"
        writeback.apply_rows(inv.KIND_JSON_ENTITY_DIR, dest, [{"id": "t1", "data": {"x": 1}}])
        assert (dest / "t1.json").exists()
        r = writeback.apply_rows(
            inv.KIND_JSON_ENTITY_DIR, dest, [{"id": "t1", "deleted_at": "2026"}]
        )
        assert not (dest / "t1.json").exists() and r.removed == 1

    def test_tombstone_for_absent_file_is_a_noop(self, tmp_path):
        r = writeback.apply_rows(
            inv.KIND_JSON_ENTITY_DIR, tmp_path / "d", [{"id": "gone", "deleted_at": "x"}]
        )
        assert r.removed == 0 and r.written == 0

    def test_row_without_id_is_skipped_not_fatal(self, tmp_path):
        r = writeback.apply_rows(
            inv.KIND_JSON_ENTITY_DIR, tmp_path / "d", [{"data": {}}, {"id": "ok", "data": {}}]
        )
        assert r.written == 1 and r.skipped == 1


class TestJsonFile:
    def test_writes_the_single_document(self, tmp_path):
        dest = tmp_path / "value.json"
        r = writeback.apply_rows(
            inv.KIND_JSON_FILE, dest, [{"id": "value.json", "data": {"k": "v"}}]
        )
        assert r.written == 1 and json.loads(dest.read_text()) == {"k": "v"}

    def test_empty_rows_writes_nothing(self, tmp_path):
        r = writeback.apply_rows(inv.KIND_JSON_FILE, tmp_path / "v.json", [])
        assert r.written == 0 and not (tmp_path / "v.json").exists()

    def test_tombstone_removes_the_file(self, tmp_path):
        dest = tmp_path / "v.json"
        writeback.apply_rows(inv.KIND_JSON_FILE, dest, [{"id": "v.json", "data": {"k": 1}}])
        writeback.apply_rows(inv.KIND_JSON_FILE, dest, [{"id": "v.json", "deleted_at": "x"}])
        assert not dest.exists()

    def test_last_row_wins_for_a_single_doc(self, tmp_path):
        dest = tmp_path / "v.json"
        writeback.apply_rows(
            inv.KIND_JSON_FILE,
            dest,
            [{"id": "v", "data": {"n": 1}}, {"id": "v", "data": {"n": 2}}],
        )
        assert json.loads(dest.read_text())["n"] == 2


class TestJsonl:
    def test_single_file_stream_is_rewritten(self, tmp_path):
        dest = tmp_path / "notifications.jsonl"
        rows = [{"ts": "2026-01-01", "m": "a"}, {"ts": "2026-01-02", "m": "b"}]
        r = writeback.apply_rows(inv.KIND_JSONL_APPEND, dest, rows)
        assert r.written == 2
        lines = [json.loads(x) for x in dest.read_text().splitlines()]
        assert [x["m"] for x in lines] == ["a", "b"]  # order preserved

    def test_directory_stream_is_year_sharded(self, tmp_path):
        dest = tmp_path / "sessions"  # no suffix, does not exist → directory-shaped
        rows = [{"ts": "2025-06-01", "id": "x"}, {"ts": "2026-06-01", "id": "y"}]
        r = writeback.apply_rows(inv.KIND_JSONL_APPEND, dest, rows)
        assert r.written == 2
        assert (dest / "2025.jsonl").is_file() and (dest / "2026.jsonl").is_file()

    def test_round_trips_a_year_sharded_stream(self, tmp_path):
        dest = tmp_path / "sessions"
        rows = [{"ts": "2026-01-01", "id": "a"}, {"ts": "2026-02-01", "id": "b"}]
        writeback.apply_rows(inv.KIND_JSONL_APPEND, dest, rows)
        # Re-extract the written shard and confirm the same rows come back.
        back = _jsonl_rows_by_year(dest / "2026.jsonl")["2026"]
        assert back == rows


class TestGuards:
    def test_sqlite_and_tree_raise(self, tmp_path):
        with pytest.raises(ValueError, match="not row-applied"):
            writeback.apply_rows(inv.KIND_SQLITE, tmp_path / "db", [])
        with pytest.raises(ValueError, match="not row-applied"):
            writeback.apply_rows(inv.KIND_TREE, tmp_path / "t", [])

    def test_unknown_kind_raises(self, tmp_path):
        with pytest.raises(ValueError, match="unknown inventory kind"):
            writeback.apply_rows("nonsense", tmp_path / "x", [])
