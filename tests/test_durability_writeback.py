"""Write a store's merged rows back to its files: only what changed, and only over what was read.

The inverse of the shard readers (``shards.read_entity_dir``, ``shards.read_json_file``): after a
merge reconciles a peer's rows, the rows that changed are written back in the store's own shape —
a JSON file for a row with ``data``, any other file as it was for one with ``text`` or ``base64``.

🔴 A pull used to rewrite every file of a synced folder from the rows it read a moment before, so a
write the store made to any of them in between was lost, and a folder of append-only files (one per
chat, one per job) was rewritten as a file per year beside them. Now a row that is what was read is
not written, a file is replaced only while it still holds what was read (``moved`` otherwise), a
one-file stream is only appended to, and a folder of append-only files is refused.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.durability import writeback
from personalclaw.durability.shards import Read, read_entity_dir, read_json_file

TASKS = inv.by_id("tasks")
PROMPTS = inv.by_id("prompts")
WORKFLOWS = inv.by_id("workflows")
SPEND = inv.by_id("spend")
NOTIFICATIONS = inv.by_id("notifications")
SESSIONS = inv.by_id("sessions")


class TestEntityDir:
    def test_writes_one_file_per_row(self, tmp_path):
        dest = tmp_path / "tasks"
        rows = [{"id": "t1", "data": {"title": "a"}}, {"id": "t2", "data": {"title": "b"}}]
        r = writeback.apply_rows(TASKS, dest, rows, read=Read())
        assert r.written == 2
        assert json.loads((dest / "t1.json").read_text())["title"] == "a"
        assert json.loads((dest / "t2.json").read_text())["title"] == "b"

    def test_every_file_of_the_store_round_trips(self, tmp_path):
        # A JSON file comes back as the same data, and any other file byte for byte: a saved
        # prompt is YAML, and a voice clip is not text at all.
        src = tmp_path / "live"
        (src / "nested").mkdir(parents=True)
        (src / "a.json").write_text(json.dumps({"n": 1}), encoding="utf-8")
        (src / "nested" / "b.json").write_text(json.dumps({"n": 2}), encoding="utf-8")
        (src / "review.yaml").write_text("name: review\nkind: user\n", encoding="utf-8")
        (src / "clip.wav").write_bytes(b"RIFF\x00\xff\xfe binary")
        read = read_entity_dir(PROMPTS, src)
        assert read.left_out == {}
        dest = tmp_path / "restored"
        r = writeback.apply_rows(PROMPTS, dest, read.rows, read=Read())
        assert r.written == 4 and r.skipped == 0
        assert (dest / "review.yaml").read_bytes() == (src / "review.yaml").read_bytes()
        assert (dest / "clip.wav").read_bytes() == (src / "clip.wav").read_bytes()
        assert json.loads((dest / "nested" / "b.json").read_text()) == {"n": 2}
        assert read_entity_dir(PROMPTS, dest).rows == read.rows  # same rows back out

    def test_a_row_that_is_what_was_read_is_not_written(self, tmp_path):
        dest = tmp_path / "tasks"
        writeback.apply_rows(TASKS, dest, [{"id": "t1", "data": {"x": 1}}], read=Read())
        before = (dest / "t1.json").stat()
        read = read_entity_dir(TASKS, dest)
        r = writeback.apply_rows(TASKS, dest, read.rows, read=read)
        assert (r.written, r.unchanged) == (0, 1)
        after = (dest / "t1.json").stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

    def test_a_file_changed_after_the_read_is_left_as_it_now_is(self, tmp_path):
        dest = tmp_path / "tasks"
        writeback.apply_rows(TASKS, dest, [{"id": "t1", "data": {"title": "old"}}], read=Read())
        read = read_entity_dir(TASKS, dest)
        # The store writes the file between the pull's read and its write.
        (dest / "t1.json").write_text(json.dumps({"title": "the store's"}), encoding="utf-8")
        r = writeback.apply_rows(
            TASKS, dest, [{"id": "t1", "data": {"title": "merged"}}], read=read
        )
        assert r.moved == ["t1"] and r.written == 0
        assert json.loads((dest / "t1.json").read_text()) == {"title": "the store's"}

    def test_a_file_made_after_the_read_is_left_as_it_now_is(self, tmp_path):
        dest = tmp_path / "tasks"
        dest.mkdir()
        read = read_entity_dir(TASKS, dest)
        (dest / "t1.json").write_text(json.dumps({"title": "made here"}), encoding="utf-8")
        r = writeback.apply_rows(
            TASKS, dest, [{"id": "t1", "data": {"title": "theirs"}}], read=read
        )
        assert r.moved == ["t1"]
        assert json.loads((dest / "t1.json").read_text()) == {"title": "made here"}

    def test_a_tombstone_removes_the_file_only_as_it_was_read(self, tmp_path):
        dest = tmp_path / "tasks"
        writeback.apply_rows(TASKS, dest, [{"id": "t1", "data": {"x": 1}}], read=Read())
        read = read_entity_dir(TASKS, dest)
        (dest / "t1.json").write_text(json.dumps({"x": 2}), encoding="utf-8")
        r = writeback.apply_rows(TASKS, dest, [{"id": "t1", "deleted_at": "2026"}], read=read)
        assert r.moved == ["t1"] and r.removed == 0 and (dest / "t1.json").is_file()
        read = read_entity_dir(TASKS, dest)
        r = writeback.apply_rows(TASKS, dest, [{"id": "t1", "deleted_at": "2026"}], read=read)
        assert r.removed == 1 and not (dest / "t1.json").exists()

    def test_tombstone_for_absent_file_is_a_noop(self, tmp_path):
        r = writeback.apply_rows(
            TASKS, tmp_path / "d", [{"id": "gone", "deleted_at": "x"}], read=Read()
        )
        assert r.removed == 0 and r.written == 0

    def test_row_without_id_is_skipped_not_fatal(self, tmp_path):
        r = writeback.apply_rows(
            TASKS, tmp_path / "d", [{"data": {}}, {"id": "ok", "data": {}}], read=Read()
        )
        assert r.written == 1 and r.skipped == 1

    def test_a_folder_of_the_store_that_is_a_symlink_out_is_never_written_or_deleted_through(
        self, tmp_path
    ):
        """What another machine's row — or an archive's, in a merge restore — names under a
        folder of the store that leads out of it: neither its write nor its delete goes there."""
        prompts = inv.by_id("prompts")
        dest = tmp_path / "home" / "prompts"
        dest.mkdir(parents=True)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "gone.json").write_text("{}", encoding="utf-8")
        (dest / "shared").symlink_to(elsewhere, target_is_directory=True)
        rows = [
            {"id": "shared/x.yaml", "text": "planted"},
            {"id": "shared/gone", "deleted_at": "x"},
        ]
        r = writeback.apply_rows(prompts, dest, rows, read=Read())
        assert sorted(p.name for p in elsewhere.iterdir()) == ["gone.json"], "written through"
        assert (r.written, r.removed, r.refused) == (0, 0, [])
        # The write is refused at the link; the delete finds nothing to remove, since a store is
        # read without following one (``shards.read_entity_dir``).
        assert {rel: link.rel for rel, link in r.linked.items()} == {"shared/x.yaml": "shared"}

    @pytest.mark.parametrize(
        "row,outside",
        [
            ({"id": "../escape", "data": {"x": 1}}, True),
            ({"id": "../../escape", "text": "x"}, True),
            ({"id": "/abs/olute", "text": "x"}, True),
            ({"id": "sub/../../escape", "data": {}}, True),
            ({"id": "runs/run-1/state", "data": {"status": "running"}}, False),  # the run records'
            ({"id": "runs.db", "base64": "AAAA"}, False),
            ({"id": "defs/x/.lock", "text": ""}, False),
            ({"id": "a\\b", "text": "x"}, True),
            ({"id": "../escape", "deleted_at": "2026"}, True),
        ],
    )
    def test_a_row_naming_a_path_that_is_not_the_stores_is_never_written(
        self, tmp_path, row, outside
    ):
        """Never written, and said apart: a path out of the store is refused (named in
        ``refused``, which a pull reports), a file of the folder that is not the store's is
        skipped."""
        dest = tmp_path / "home" / "workflows"
        dest.mkdir(parents=True)
        (tmp_path / "home" / "escape.json").write_text("{}", encoding="utf-8")
        before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
        r = writeback.apply_rows(WORKFLOWS, dest, [row], read=Read())
        assert r.written == 0 and r.removed == 0
        if outside:
            assert (r.refused, r.skipped) == ([writeback.row_rel(row)], 0)
        else:
            assert (r.refused, r.skipped) == ([], 1)
        assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before

    def test_a_folder_where_the_file_would_be_is_left_alone(self, tmp_path):
        dest = tmp_path / "prompts"
        (dest / "notes.md").mkdir(parents=True)
        r = writeback.apply_rows(PROMPTS, dest, [{"id": "notes.md", "text": "x"}], read=Read())
        assert r.moved == ["notes.md"] and (dest / "notes.md").is_dir()


class TestJsonFile:
    def test_writes_the_single_document(self, tmp_path):
        dest = tmp_path / "spend.json"
        r = writeback.apply_rows(
            SPEND, dest, [{"id": "spend.json", "data": {"k": "v"}}], read=Read()
        )
        assert r.written == 1 and json.loads(dest.read_text()) == {"k": "v"}

    def test_a_file_that_is_not_json_round_trips_as_it_is(self, tmp_path):
        src = tmp_path / "a" / "workspace_dir"
        src.parent.mkdir()
        src.write_text("/Users/someone/work\n", encoding="utf-8")
        pointer = inv.by_id("workspace_dir")  # the bound workspace's path, as the file holds it
        read = read_json_file(pointer, src)
        assert read.rows == [{"id": "workspace_dir", "text": "/Users/someone/work\n"}]
        dest = tmp_path / "b" / "workspace_dir"
        writeback.apply_rows(pointer, dest, read.rows, read=Read())
        assert dest.read_bytes() == src.read_bytes()

    def test_empty_rows_writes_nothing(self, tmp_path):
        r = writeback.apply_rows(SPEND, tmp_path / "v.json", [], read=Read())
        assert r.written == 0 and not (tmp_path / "v.json").exists()

    def test_tombstone_removes_the_file(self, tmp_path):
        dest = tmp_path / "v.json"
        writeback.apply_rows(SPEND, dest, [{"id": "v.json", "data": {"k": 1}}], read=Read())
        read = read_json_file(SPEND, dest)
        writeback.apply_rows(SPEND, dest, [{"id": "v.json", "deleted_at": "x"}], read=read)
        assert not dest.exists()

    def test_last_row_wins_for_a_single_doc(self, tmp_path):
        dest = tmp_path / "v.json"
        writeback.apply_rows(
            SPEND,
            dest,
            [{"id": "v", "data": {"n": 1}}, {"id": "v", "data": {"n": 2}}],
            read=Read(),
        )
        assert json.loads(dest.read_text())["n"] == 2

    def test_a_file_changed_after_the_read_is_left_as_it_now_is(self, tmp_path):
        dest = tmp_path / "v.json"
        writeback.apply_rows(SPEND, dest, [{"id": "v.json", "data": {"n": 1}}], read=Read())
        read = read_json_file(SPEND, dest)
        dest.write_text(json.dumps({"n": "the store's"}), encoding="utf-8")
        r = writeback.apply_rows(SPEND, dest, [{"id": "v.json", "data": {"n": 3}}], read=read)
        assert r.moved and json.loads(dest.read_text()) == {"n": "the store's"}


class TestJsonl:
    def test_a_one_file_stream_is_appended_the_rows_it_lacks(self, tmp_path):
        from personalclaw.durability.reconcile import read_local

        dest = tmp_path / "notifications.jsonl"
        dest.write_text(
            json.dumps({"ts": "2026-01-01", "m": "a"})
            + "\n"
            + json.dumps({"ts": "2026-01-02", "m": "b"})
            + "\n",
            encoding="utf-8",
        )
        read = read_local(NOTIFICATIONS, dest)
        # The store appends a line between the pull's read and its write.
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "2026-01-03", "m": "c"}) + "\n")
        merged = [*read.rows, {"ts": "2026-01-04", "m": "d"}]
        r = writeback.apply_rows(NOTIFICATIONS, dest, merged, read=read)
        assert (r.written, r.unchanged) == (1, 2)
        lines = [json.loads(x)["m"] for x in dest.read_text().splitlines()]
        assert lines == ["a", "b", "c", "d"]  # the store's line stays, the new one follows

    def test_a_folder_of_append_only_files_is_refused(self, tmp_path):
        dest = tmp_path / "sessions"
        dest.mkdir()
        (dest / "chat-1.jsonl").write_text(
            json.dumps({"ts": "2026-06-01"}) + "\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="folder of append-only files"):
            writeback.apply_rows(SESSIONS, dest, [{"ts": "2026-06-02", "id": "y"}], read=Read())
        assert sorted(p.name for p in dest.iterdir()) == ["chat-1.jsonl"]


class TestGuards:
    def test_sqlite_and_tree_raise(self, tmp_path):
        with pytest.raises(ValueError, match="not row-applied"):
            writeback.apply_rows(inv.by_id("memory_db"), tmp_path / "db", [], read=Read())
        with pytest.raises(ValueError, match="not row-applied"):
            writeback.apply_rows(inv.by_id("skills"), tmp_path / "t", [], read=Read())

    def test_unknown_kind_raises(self, tmp_path):
        nonsense = dataclasses.replace(TASKS, kind="nonsense")
        with pytest.raises(ValueError, match="unknown inventory kind"):
            writeback.apply_rows(nonsense, tmp_path / "x", [], read=Read())
