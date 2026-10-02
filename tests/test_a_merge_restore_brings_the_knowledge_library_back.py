"""A merge restore brings a snapshot's knowledge library back, and its last line says what it left.

The knowledge library keeps a sqlite-vec index of its chunk vectors beside its full-text index. The
merge sent the full-text index's ``rebuild`` command to every virtual table, the vector index too,
so the statement failed ("no such module: vec0"), the library's whole merge rolled back, and the
restore still ended "✅ Merge complete." with status 0. Nothing of the snapshot's library came in,
in the terminal, on the Backups page (which runs the same merge) or in a folder sync (the same
executor, whose verdict said the database was consumed).
"""

from __future__ import annotations

import io
import sqlite3
import struct
import tarfile
from pathlib import Path

import pytest

from personalclaw import snapshot
from personalclaw.durability import inventory as inv
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD
from personalclaw.durability.db_merge import make_db_merger
from personalclaw.knowledge.chunking import Chunk
from personalclaw.knowledge.store import KnowledgeStore

LIBRARY = "workspace/knowledge/knowledge.db"


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", lambda: None)


def _blob(*values: float) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _library(path: Path, title: str, vector: tuple[float, ...]) -> None:
    """A knowledge library holding one note whose one chunk is embedded, as the store writes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    store = KnowledgeStore(str(path))
    try:
        item = store.create_typed_item(item_type="note", title=title, content=f"{title} notes")
        store.replace_chunks(
            item,
            [
                Chunk(
                    text=f"{title} notes",
                    section=None,
                    line_start=1,
                    line_end=1,
                    embedding=_blob(*vector),
                )
            ],
        )
        assert store.vec_index.enabled, "sqlite-vec is a core dependency and must load here"
    finally:
        store.close()


def _titles(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(r[0] for r in conn.execute("SELECT title FROM items"))
    finally:
        conn.close()


def _index_and_search(path: Path) -> tuple[dict, list[str]]:
    """The vector index's coverage as the store reads it, and the notes full-text search finds."""
    store = KnowledgeStore(str(path))
    try:
        coverage = store.vec_index.coverage()["dimensions"]
        found = sorted(r["title"] for r in store.search_items_fts("notes"))
        return coverage, found
    finally:
        store.close()


def test_a_merge_brings_the_snapshots_notes_and_their_vectors_in(tmp_path):
    snap, live = tmp_path / "snap.db", tmp_path / "live.db"
    _library(snap, "Garden plan", (1.0, 0.0, 0.0, 0.0))
    _library(live, "Kitchen quotes", (0.0, 1.0, 0.0, 0.0))

    for _ in range(2):  # a restore drill is run twice; the second changes nothing
        left: list[str] = []
        snapshot._merge_sqlite_attach(snap, live, LIBRARY, left_unchanged=left)
        assert left == []
        assert _titles(live) == ["Garden plan", "Kitchen quotes"]
        coverage, found = _index_and_search(live)
        assert coverage == {"4": {"indexed": 2, "live": 2}}
        assert found == ["Garden plan", "Kitchen quotes"]


def _home_with_library(home: Path, title: str, vector: tuple[float, ...], monkeypatch) -> None:
    home.mkdir(parents=True)
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    _library(home / LIBRARY, title, vector)


def _snapshot_of(home: Path, out: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    out.mkdir()
    assert snapshot.snapshot_main([str(out)]) == 0
    return next(out.glob("personalclaw-snapshot-*.tar.gz"))


def _with_unreadable_library(tarball: Path, tmp_path: Path) -> Path:
    """The same archive, its knowledge library replaced by bytes that are no database."""
    work = tmp_path / "unpacked"
    with tarfile.open(str(tarball)) as tar:
        tar.extractall(work, filter="data")
    root = next(work.iterdir())
    (root / LIBRARY).write_bytes(b"not a database")
    damaged = tmp_path / "damaged.tar.gz"
    with tarfile.open(str(damaged), "w:gz") as tar:
        tar.add(str(root), arcname=root.name)
    return damaged


def test_the_terminal_restore_brings_the_library_back_and_ends_complete(
    tmp_path, monkeypatch, capsys
):
    _home_with_library(tmp_path / "before", "Garden plan", (1.0, 0.0, 0.0, 0.0), monkeypatch)
    tarball = _snapshot_of(tmp_path / "before", tmp_path / "snapshots", monkeypatch)
    _home_with_library(tmp_path / "now", "Kitchen quotes", (0.0, 1.0, 0.0, 0.0), monkeypatch)

    assert snapshot.restore_main([str(tarball), "--mode", "merge"]) == 0

    out = capsys.readouterr().out
    assert "merge failed" not in out
    assert "✅ Merge complete." in out
    assert _titles(tmp_path / "now" / LIBRARY) == ["Garden plan", "Kitchen quotes"]


def test_a_restore_that_leaves_a_part_unchanged_says_which_and_fails(tmp_path, monkeypatch, capsys):
    _home_with_library(tmp_path / "before", "Garden plan", (1.0, 0.0, 0.0, 0.0), monkeypatch)
    tarball = _snapshot_of(tmp_path / "before", tmp_path / "snapshots", monkeypatch)
    damaged = _with_unreadable_library(tarball, tmp_path)
    _home_with_library(tmp_path / "now", "Kitchen quotes", (0.0, 1.0, 0.0, 0.0), monkeypatch)

    assert snapshot.restore_main([str(damaged), "--mode", "merge"]) == 1

    out = capsys.readouterr().out
    assert "✅ Merge complete." not in out
    assert f"⚠️  Merge finished, but 1 part was left unchanged: {LIBRARY}." in out
    assert _titles(tmp_path / "now" / LIBRARY) == ["Kitchen quotes"]


def test_the_backups_page_merge_names_what_it_left_unchanged(tmp_path, monkeypatch):
    _home_with_library(tmp_path / "before", "Garden plan", (1.0, 0.0, 0.0, 0.0), monkeypatch)
    tarball = _snapshot_of(tmp_path / "before", tmp_path / "snapshots", monkeypatch)
    damaged = _with_unreadable_library(tarball, tmp_path)
    _home_with_library(tmp_path / "now", "Kitchen quotes", (0.0, 1.0, 0.0, 0.0), monkeypatch)

    whole = snapshot.restore_merge(tarball, None)
    assert whole["ok"] is True and whole["left_unchanged"] == []
    assert _titles(tmp_path / "now" / LIBRARY) == ["Garden plan", "Kitchen quotes"]

    partial = snapshot.restore_merge(damaged, None)
    assert partial["ok"] is True and partial["left_unchanged"] == [LIBRARY]


def _library_entry() -> inv.StateEntry:
    return next(e for e in inv.sqlite_entries() if e.path == LIBRARY)


def test_a_folder_sync_merges_the_library_and_says_when_it_could_not(tmp_path):
    home = tmp_path / "home"
    _library(home / LIBRARY, "Kitchen quotes", (0.0, 1.0, 0.0, 0.0))
    entry = _library_entry()
    shard = tmp_path / "shard"
    (shard / "db").mkdir(parents=True)
    _library(shard / "db" / f"{entry.id}.db", "Garden plan", (1.0, 0.0, 0.0, 0.0))

    assert make_db_merger(home)(entry, shard) == CONSUMED
    assert _titles(home / LIBRARY) == ["Garden plan", "Kitchen quotes"]

    (shard / "db" / f"{entry.id}.db").write_bytes(b"not a database")
    assert make_db_merger(home)(entry, shard) == PAYLOAD_BAD


def test_an_archive_import_says_when_it_left_the_memories_unchanged(tmp_path, monkeypatch):
    """Settings → Import / Export's merge runs the memory merge too, and listed "memory (merged)"
    after skipping a memory store it could not read."""
    import zipfile

    from personalclaw.portability import apply_import_zip, create_export_zip

    source = tmp_path / "before"
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")
    sqlite3.connect(str(source / "memory.db")).close()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(source))
    archive, _ = create_export_zip()
    damaged = tmp_path / "damaged.zip"
    with zipfile.ZipFile(io.BytesIO(archive)) as zin, zipfile.ZipFile(damaged, "w") as zout:
        for info in zin.infolist():
            body = b"not a database" if info.filename.endswith("memory.db") else zin.read(info)
            zout.writestr(info, body)

    target = tmp_path / "now"
    target.mkdir()
    (target / "config.json").write_text("{}", encoding="utf-8")
    sqlite3.connect(str(target / "memory.db")).close()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))

    items = apply_import_zip(damaged, "merge")["items"]
    assert "memory (left unchanged)" in items and "memory (merged)" not in items
