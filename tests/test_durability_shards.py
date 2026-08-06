"""DURABILITY §2 — deterministic shard export + manifest verification.

Three properties carry the whole design, so each is tested directly:

1. **Determinism** — two exports of unchanged state are byte-identical. Without
   this, a sync re-uploads unchanged data and a git history fills with no-op
   commits.
2. **Verifiability** — `validate` catches a tampered, truncated, missing, or
   undeclared shard. A backup nobody has verified is a hope, not a backup.
3. **Secrets never shard** — shards are the representation that leaves the
   machine, unlike a local snapshot tar.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from personalclaw.durability import shards


def _home(tmp_path):
    """A home with one of every entry kind the exporter handles."""
    home = tmp_path / "home"
    (home / "tasks").mkdir(parents=True)
    (home / "tasks" / "t-2.json").write_text('{"id": "t-2", "title": "second"}')
    (home / "tasks" / "t-1.json").write_text('{"id": "t-1", "title": "first"}')
    (home / "sessions").mkdir()
    (home / "sessions" / "s.jsonl").write_text(
        '{"ts": "2026-03-04T00:00:00Z", "role": "user"}\n'
        '{"ts": "2025-11-01T00:00:00Z", "role": "user"}\n'
        '{"role": "no-timestamp"}\n'
    )
    (home / "config.json").write_text('{"agent": {"bot_name": "PC"}}')
    # A database, with content in a non-alphabetical insert order.
    conn = sqlite3.connect(str(home / "memory.db"))
    conn.execute("CREATE TABLE semantic_memory(key TEXT PRIMARY KEY, value_json TEXT)")
    conn.executemany(
        "INSERT INTO semantic_memory VALUES (?, ?)",
        [("pref.z", '"last"'), ("pref.a", '"first"')],
    )
    conn.commit()
    conn.close()
    # Secrets, which must never appear in an export.
    (home / ".env").write_text("OPENAI_API_KEY=sk-secret")
    (home / "sel_hmac.key").write_text("hmac-secret")
    return home


class TestDeterminism:
    def test_two_exports_are_byte_identical(self, tmp_path):
        """The property everything else rests on."""
        home = _home(tmp_path)
        first, second = tmp_path / "a", tmp_path / "b"
        shards.export_shards(home, first)
        shards.export_shards(home, second)
        for path in sorted(first.rglob("*.jsonl")):
            rel = path.relative_to(first)
            assert path.read_bytes() == (second / rel).read_bytes(), f"{rel} differs"

    def test_rows_are_sorted_by_id_not_filesystem_order(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        lines = (out / "tasks" / "entities.jsonl").read_text().strip().splitlines()
        ids = [json.loads(ln)["id"] for ln in lines]
        assert ids == sorted(ids) == ["t-1", "t-2"]

    def test_canonical_json_sorts_keys_and_stays_compact(self):
        assert shards.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_sqlite_rows_are_stably_ordered(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        lines = (out / "memory_db" / "semantic_memory.jsonl").read_text().strip().splitlines()
        keys = [json.loads(ln)["key"] for ln in lines]
        assert keys == ["pref.a", "pref.z"]  # ordered by key, not insert order


class TestSecretsNeverShard:
    def test_no_secret_entry_is_exported(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        blob = "\n".join(p.read_text(errors="replace") for p in out.rglob("*") if p.is_file())
        assert "sk-secret" not in blob
        assert "hmac-secret" not in blob
        assert not list(out.glob("env/**"))
        assert not list(out.glob("sel_hmac_key/**"))

    def test_manifest_machine_id_is_not_the_telemetry_salt(self, tmp_path):
        """The manifest needs a stable machine id, but telemetry_salt is a secret."""
        home = _home(tmp_path)
        (home / "telemetry_salt").write_text("salty-secret")
        out = tmp_path / "s"
        shards.export_shards(home, out)
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["machine_id"]
        assert manifest["machine_id"] != "salty-secret"

    def test_machine_id_is_stable_across_exports(self, tmp_path):
        home = _home(tmp_path)
        first = shards.machine_id(home)
        assert shards.machine_id(home) == first


class TestYearSharding:
    def test_append_only_store_shards_by_year(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        names = {p.name for p in (out / "sessions").glob("*.jsonl")}
        assert names == {"2025.jsonl", "2026.jsonl", "unknown.jsonl"}

    def test_undated_row_is_never_backdated(self, tmp_path):
        """A row with no parseable timestamp goes to `unknown`, not a guessed year."""
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        unknown = (out / "sessions" / "unknown.jsonl").read_text()
        assert "no-timestamp" in unknown
        assert "no-timestamp" not in (out / "sessions" / "2026.jsonl").read_text()


class TestValidate:
    def _exported(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        return out

    def test_fresh_export_validates(self, tmp_path):
        result = shards.validate(self._exported(tmp_path))
        assert result.ok, result.problems
        assert result.shards_checked > 0 and result.rows_checked > 0

    def test_tampered_content_is_caught(self, tmp_path):
        out = self._exported(tmp_path)
        target = out / "tasks" / "entities.jsonl"
        target.write_bytes(target.read_bytes() + b'{"id":"injected"}\n')
        result = shards.validate(out)
        assert not result.ok
        assert any("sha256 mismatch" in p for p in result.problems)

    def test_missing_shard_is_caught(self, tmp_path):
        out = self._exported(tmp_path)
        (out / "tasks" / "entities.jsonl").unlink()
        result = shards.validate(out)
        assert not result.ok
        assert any("missing on disk" in p for p in result.problems)

    def test_undeclared_shard_is_caught(self, tmp_path):
        """A stray file means the manifest and the export disagree."""
        out = self._exported(tmp_path)
        (out / "ghost.jsonl").write_text('{"id": "ghost"}\n')
        result = shards.validate(out)
        assert not result.ok
        assert any("not declared in the manifest" in p for p in result.problems)

    def test_unparseable_row_is_caught(self, tmp_path):
        out = self._exported(tmp_path)
        target = out / "tasks" / "entities.jsonl"
        body = target.read_bytes().replace(b'{"data"', b'{{{"data"', 1)
        target.write_bytes(body)
        result = shards.validate(out)
        assert not result.ok

    def test_missing_manifest_is_caught(self, tmp_path):
        out = self._exported(tmp_path)
        (out / "manifest.json").unlink()
        result = shards.validate(out)
        assert not result.ok and "missing manifest.json" in result.problems[0]

    def test_export_and_validate_round_trip(self, tmp_path):
        home = _home(tmp_path)
        exported, validated = shards.export_and_validate(home, tmp_path / "s")
        assert exported.entries > 0
        assert validated.ok, validated.problems


class TestIncremental:
    def test_dirty_entries_reports_only_what_changed(self, tmp_path):
        home = _home(tmp_path)
        state = tmp_path / "state.json"
        first = shards.dirty_entries(home, state)
        assert "tasks" in first  # everything is dirty on the first pass
        assert shards.dirty_entries(home, state) == []  # nothing changed
        (home / "tasks" / "t-3.json").write_text('{"id": "t-3"}')
        assert shards.dirty_entries(home, state) == ["tasks"]

    def test_incremental_export_still_validates(self, tmp_path):
        """REGRESSION: an incremental export used to rewrite the manifest with only
        the changed entry's shards, orphaning the rest — so a perfectly good export
        failed validation with "present on disk but not declared"."""
        home = _home(tmp_path)
        out = tmp_path / "s"
        state = tmp_path / "state.json"
        shards.export_shards(home, out)
        shards.dirty_entries(home, state)  # prime the fingerprint
        (home / "tasks" / "t-9.json").write_text('{"id": "t-9"}')
        changed = shards.dirty_entries(home, state)
        shards.export_shards(home, out, entries=changed)
        result = shards.validate(out)
        assert result.ok, result.problems
        # The untouched entries are still declared AND the change landed.
        manifest = json.loads((out / "manifest.json").read_text())
        paths = {s["path"] for s in manifest["shards"]}
        assert "config/value.jsonl" in paths and "sessions/2026.jsonl" in paths
        assert "t-9" in (out / "tasks" / "entities.jsonl").read_text()


class TestSqliteHandling:
    def test_tables_are_discovered_not_allowlisted(self, tmp_path):
        """snapshot.py's merge allowlist names knowledge_facts/knowledge_edges, which
        don't exist in memory.db. Discovery from the schema can't drift that way."""
        home = _home(tmp_path)
        conn = sqlite3.connect(str(home / "memory.db"))
        conn.execute("CREATE TABLE brand_new_table(id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO brand_new_table VALUES ('x')")
        conn.commit()
        conn.close()
        out = tmp_path / "s"
        shards.export_shards(home, out)
        assert (out / "memory_db" / "brand_new_table.jsonl").is_file()

    def test_live_wal_database_exports_completely(self, tmp_path):
        """The DB is read through the backup API, so an open WAL store is consistent."""
        home = _home(tmp_path)
        conn = sqlite3.connect(str(home / "memory.db"))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executemany(
            "INSERT INTO semantic_memory VALUES (?, ?)",
            [(f"pref.k{i}", f'"{i}"') for i in range(300)],
        )
        conn.commit()
        try:  # keep the handle OPEN with un-checkpointed WAL content
            out = tmp_path / "s"
            shards.export_shards(home, out)
            lines = (out / "memory_db" / "semantic_memory.jsonl").read_text().strip().splitlines()
            assert len(lines) == 302  # 2 seeded + 300
        finally:
            conn.close()

    def test_blob_columns_are_summarized_not_inlined(self, tmp_path):
        """An embedding blob would bloat a human-diffable shard; record its size."""
        home = _home(tmp_path)
        conn = sqlite3.connect(str(home / "memory.db"))
        conn.execute("CREATE TABLE vecs(id TEXT PRIMARY KEY, embedding BLOB)")
        conn.execute("INSERT INTO vecs VALUES ('a', ?)", (b"\x00" * 64,))
        conn.commit()
        conn.close()
        out = tmp_path / "s"
        shards.export_shards(home, out)
        row = json.loads((out / "memory_db" / "vecs.jsonl").read_text().strip())
        assert row["embedding"] == {"__bytes__": 64}


class TestPartSplit:
    def test_large_shard_splits_deterministically(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shards, "PART_SPLIT_BYTES", 2_000)
        home = _home(tmp_path)
        for i in range(60):
            (home / "tasks" / f"big-{i:03d}.json").write_text(
                json.dumps({"id": f"big-{i:03d}", "body": "x" * 200})
            )
        first, second = tmp_path / "a", tmp_path / "b"
        shards.export_shards(home, first)
        shards.export_shards(home, second)
        parts = sorted(p.name for p in (first / "tasks").glob("*.jsonl"))
        assert len(parts) > 1 and all("part-" in n for n in parts)
        # Same split points, same bytes — the split is a function of the content.
        assert parts == sorted(p.name for p in (second / "tasks").glob("*.jsonl"))
        for name in parts:
            assert (first / "tasks" / name).read_bytes() == (second / "tasks" / name).read_bytes()
        assert shards.validate(first).ok


class TestCli:
    def test_export_then_validate_via_cli(self, tmp_path, monkeypatch, capsys):
        home = _home(tmp_path)
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

        class _Args:
            backup_command = "export"
            out_dir = None
            incremental = False

        assert shards.backup_cmd(_Args()) == 0
        capsys.readouterr()

        class _Validate:
            backup_command = "validate"
            shard_dir = None

        assert shards.backup_cmd(_Validate()) == 0
        assert "valid" in capsys.readouterr().out

    def test_validate_returns_nonzero_on_corruption(self, tmp_path, monkeypatch, capsys):
        """Non-zero exit is what makes this usable from cron/CI."""
        home = _home(tmp_path)
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        out = shards.default_shard_dir(home)
        shards.export_shards(home, out)
        target = out / "tasks" / "entities.jsonl"
        target.write_bytes(target.read_bytes() + b"corrupt\n")

        class _Validate:
            backup_command = "validate"
            shard_dir = None

        assert shards.backup_cmd(_Validate()) == 1
        assert "INVALID" in capsys.readouterr().out

    def test_validate_with_no_export_is_a_clear_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(_home(tmp_path)))

        class _Validate:
            backup_command = "validate"
            shard_dir = None

        assert shards.backup_cmd(_Validate()) == 1
        assert "backup export" in capsys.readouterr().out  # tells you what to run


@pytest.mark.parametrize("missing", ["tasks", "memory.db", "sessions"])
def test_absent_store_is_skipped_not_fatal(tmp_path, missing):
    """A home without a given store exports fine — homes differ."""
    home = _home(tmp_path)
    target = home / missing
    if target.is_dir():
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
    else:
        target.unlink()
    result = shards.export_shards(home, tmp_path / "s")
    assert result.entries > 0
    assert shards.validate(tmp_path / "s").ok


# ── import (the read side; DAS-6) ───────────────────────────────────────────
# The property that carries the read half: an export→import round-trip returns
# every exported row unchanged, reassembled per inventory entry.


class TestImport:
    def test_round_trip_returns_every_exported_row(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        exported = shards.export_shards(home, out)
        imported = shards.import_shards(out)
        # Same total row count as was written (ExportResult.rows is the count), and
        # one bucket per exported entry.
        assert imported.total_rows == exported.rows
        assert imported.entries == exported.entries
        assert imported.machine_id == shards.machine_id(home)

    def test_entity_dir_rows_survive_by_id(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        imported = shards.import_shards(out)
        # entity-dir rows are {"id": <filename stem>, "data": <the json>}.
        tasks = {r["id"]: r["data"] for r in imported.rows["tasks"]}
        assert set(tasks) == {"t-1", "t-2"}
        assert tasks["t-1"]["title"] == "first" and tasks["t-2"]["title"] == "second"

    def test_sqlite_table_rows_reassemble_under_entry(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        imported = shards.import_shards(out)
        # memory_db's semantic_memory table rows come back under the entry id.
        rows = {r["key"]: r["value_json"] for r in imported.rows["memory_db"]}
        assert rows == {"pref.a": '"first"', "pref.z": '"last"'}

    def test_jsonl_years_merge_into_one_list(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        imported = shards.import_shards(out)
        # sessions had rows across 2025/2026 + one undated → all three reassemble.
        assert len(imported.rows["sessions"]) == 3

    def test_secret_entries_are_absent_from_import(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        imported = shards.import_shards(out)
        blob = json.dumps(imported.rows)
        assert "sk-secret" not in blob and "hmac-secret" not in blob

    def test_entries_filter_restricts_the_import(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        imported = shards.import_shards(out, entries=["tasks"])
        assert set(imported.rows) == {"tasks"}

    def test_invalid_export_refuses_to_import(self, tmp_path):
        home = _home(tmp_path)
        out = tmp_path / "s"
        shards.export_shards(home, out)
        # Corrupt a shard so validate() fails; import must refuse rather than
        # hand a merge/restore silently-wrong data.
        victim = next(out.rglob("*.jsonl"))
        victim.write_text(victim.read_text() + '{"injected": "row"}\n')
        with pytest.raises(ValueError, match="refusing to import an invalid shard export"):
            shards.import_shards(out)

    def test_round_trip_after_part_split(self, tmp_path, monkeypatch):
        """A shard large enough to split into part-NNNN files reassembles in order."""
        monkeypatch.setattr(shards, "PART_SPLIT_BYTES", 200)
        home = tmp_path / "home"
        (home / "sessions").mkdir(parents=True)
        rows = [f'{{"ts": "2026-01-01T00:00:0{i}Z", "n": {i}}}' for i in range(20)]
        (home / "sessions" / "s.jsonl").write_text("\n".join(rows) + "\n")
        out = tmp_path / "s"
        exported = shards.export_shards(home, out)
        # It actually split (more than one shard file for the one entry+year).
        assert len([s for s in exported.shards if s.path.startswith("sessions/")]) > 1
        imported = shards.import_shards(out)
        ns = [r["n"] for r in imported.rows["sessions"]]
        assert ns == list(range(20))  # every row, in write order
