"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-g — sync-only whole-DB copies in the shard format.

The diffable row shards store embedding/byte columns as size placeholders, so they can't
rebuild a DB losslessly. A SYNC export (include_databases=True) additionally stages the real
DB file under db/<entry>.db; the hourly incremental backup leaves it off, so its byte-identical
determinism is untouched. validate() checks the DB copies; import_shards surfaces them.
"""

from __future__ import annotations

import json
import sqlite3

from personalclaw.durability import shards


def _home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # A row entry, so a mixed export exercises both paths.
    (home / "tasks").mkdir()
    (home / "tasks" / "t1.json").write_text('{"id": "t1"}', encoding="utf-8")
    # A sqlite entry with a byte column the row shards would placeholder-ize.
    conn = sqlite3.connect(str(home / "memory.db"))
    conn.execute("CREATE TABLE semantic_memory(key TEXT PRIMARY KEY, embedding BLOB)")
    conn.execute("INSERT INTO semantic_memory VALUES (?, ?)", ("k", b"\x00\x01\x02vector"))
    conn.commit()
    conn.close()
    return home


class TestDbCopyExport:
    def test_default_export_stages_no_databases(self, tmp_path):
        # The hourly-backup default must NOT write db copies.
        home = _home(tmp_path)
        out = tmp_path / "s"
        result = shards.export_shards(home, out)
        assert result.databases == []
        assert not (out / "db").exists()

    def test_sync_export_stages_the_real_db(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        result = shards.export_shards(home, out, include_databases=True)
        assert len(result.databases) == 1
        db = result.databases[0]
        assert db.entry_id == "memory_db" and db.path == "db/memory_db.db"
        staged = out / "db" / "memory_db.db"
        assert staged.is_file() and staged.stat().st_size == db.bytes
        # The staged copy is a real, openable database carrying the byte column intact.
        conn = sqlite3.connect(f"file:{staged}?mode=ro", uri=True)
        row = conn.execute("SELECT embedding FROM semantic_memory WHERE key='k'").fetchone()
        conn.close()
        assert bytes(row[0]) == b"\x00\x01\x02vector"  # lossless — not a {__bytes__} placeholder

    def test_row_shards_still_written_alongside_the_db(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out, include_databases=True)
        # The diffable rows are still there (human review); the db is the merge source.
        assert (out / "memory_db" / "semantic_memory.jsonl").is_file()
        assert (out / "tasks" / "entities.jsonl").is_file()


class TestManifestAndValidate:
    def test_manifest_declares_databases(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out, include_databases=True)
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["databases"][0]["entry_id"] == "memory_db"
        assert "sha256" in manifest["databases"][0]

    def test_validate_passes_a_db_export(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out, include_databases=True)
        assert shards.validate(out).ok

    def test_validate_catches_a_corrupted_db_copy(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out, include_databases=True)
        # Flip a byte in the staged DB — validate must name it.
        db = out / "db" / "memory_db.db"
        data = bytearray(db.read_bytes())
        data[-1] ^= 0xFF
        db.write_bytes(bytes(data))
        report = shards.validate(out)
        assert not report.ok
        assert any("memory_db.db" in p for p in report.problems)

    def test_row_only_export_validates_without_a_databases_key(self, tmp_path):
        # A manifest with no `databases` field (incremental/row-only) is still valid.
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)  # no include_databases
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["databases"] == []  # present but empty
        assert shards.validate(out).ok


class TestImportSurfacesDatabases:
    def test_import_maps_entry_to_db_path(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out, include_databases=True)
        imported = shards.import_shards(out)
        assert imported.databases == {"memory_db": "db/memory_db.db"}
        # And the row shards still import as rows for the same entry.
        assert "memory_db" in imported.rows

    def test_import_respects_entries_filter_for_databases(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out, include_databases=True)
        imported = shards.import_shards(out, entries=["tasks"])
        assert imported.databases == {}  # memory_db filtered out


class TestDeterminismUntouched:
    def test_row_export_stays_byte_identical(self, tmp_path):
        # The property the whole format rests on must survive the new field.
        home = _home(tmp_path)
        a, b = tmp_path / "a", tmp_path / "b"
        shards.export_shards(home, a)
        shards.export_shards(home, b)
        for path in sorted(a.rglob("*.jsonl")):
            rel = path.relative_to(a)
            assert path.read_bytes() == (b / rel).read_bytes(), f"{rel} differs"
