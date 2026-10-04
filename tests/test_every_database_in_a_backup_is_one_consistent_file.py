"""Every SQLite database in the home is backed up as one consistent file, and no restore puts a
database's log beside another copy of it.

A database open in WAL mode is more than one file: its own, the write-ahead log beside it (`-wal`)
and that log's index (`-shm`). Its newest committed rows can be in the log and not yet in its own
file, and SQLite applies a log to whatever database file it finds beside it, since nothing in the
log names the database it was written for. So a backup copies each database through SQLite's
backup API, into one file with nothing beside it, and a restore never puts a log into the home.

What went wrong before, each held here:

* An installed app keeps its own database in its `data/` folder, open in WAL mode while its backend
  runs. Nothing declared it, so a snapshot copied the file as bytes, without its log, an export
  left it out, and the Doctor counted it as an undeclared database.
* The snapshot's folder copies left out every file NAMED like a database the state manifest
  declares, wherever it was: a `runs.db` of the user's own in the workspace was in no snapshot.
  And the pass that copies every other store copied a declared database again, as bytes, over the
  consistent copy made a moment before.
* A merge restore and an import took an archive's files one at a time, a database's log among
  them, so another home's `-wal` could land beside this home's database.
* A merge restore, an import and a sync's first copy of a store put a database in beside a log this
  home kept at its path with no database there, and SQLite read the log's pages in place of the
  copy's own.
* A replace restore moved a database aside without its log, so the restored copy opened with the
  log this home had left beside it; and it copied the workspace and the skills aside as bytes,
  leaving out every link in them, before removing them.
* An app's update and its keep-data uninstall copied its `data/` as bytes while its backend wrote,
  the database and its log each at its own instant.
* The snapshot read `memory.db` through the backup API with no way out, so one damaged file stopped
  every snapshot of the home.
* The restore drill read every `.db` file in a snapshot as a database, so a user's own file of
  that name failed it.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path

import pytest

from personalclaw.durability import inventory
from personalclaw.snapshot import restore_main, snapshot_main

APP = "field-notes"
APP_DB = f"apps/{APP}/data/notes.db"
SIDECARS = ("-wal", "-shm", "-journal")


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", lambda: None)


def _use_home(monkeypatch, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = _use_home(monkeypatch, (tmp_path / "home").resolve())
    (home / "config.json").write_text("{}", encoding="utf-8")
    return home


def _install_app(home: Path) -> Path:
    """An installed app's folder, as the Store leaves one: its manifest, and the `data/` folder its
    backend writes."""
    folder = home / "apps" / APP
    (folder / "data").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Field notes",
        "description": "Keeps notes in a database of its own.",
    }
    (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


class _LiveDatabase:
    """A database a running store holds open in WAL mode, as an app's backend does: every row is
    committed, and sits in the write-ahead log, where a reader still reading an older state keeps
    it (no checkpoint can move a row past what that reader sees into the database file)."""

    def __init__(self, path: Path, tag: str, rows: int = 300) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.tag = tag
        self.writer = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, body TEXT)")
        self.reader = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.reader.execute("BEGIN")
        self.reader.execute("SELECT count(*) FROM notes").fetchone()
        self.ids = {f"{tag}-{i}" for i in range(rows)}
        for note in sorted(self.ids):
            self.writer.execute("INSERT INTO notes VALUES (?, ?)", (note, "a line of notes " * 12))
        assert Path(f"{path}-wal").stat().st_size > 0, "the rows are not in the log"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def keep_writing(self) -> None:
        """Go on writing from another connection, as the app's backend does while a backup runs."""

        def _write() -> None:
            conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30)
            try:
                n = 0
                while not self._stop.wait(0.002):
                    conn.execute("INSERT INTO notes VALUES (?, ?)", (f"during-{n}", "more"))
                    n += 1
            finally:
                conn.close()

        self._thread = threading.Thread(target=_write, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self.reader.close()
        self.writer.close()


def _notes(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {row[0] for row in conn.execute("SELECT id FROM notes")}
    finally:
        conn.close()


def _sidecars_beside(db: Path) -> list[str]:
    return sorted(p.name for p in db.parent.iterdir() if p.name.startswith(db.name + "-"))


def _snapshot(out: Path) -> Path:
    assert snapshot_main([str(out)]) == 0
    (tarball,) = out.glob("personalclaw-snapshot-*.tar.gz")
    return tarball


def _members(tarball: Path) -> list[str]:
    with tarfile.open(tarball) as tar:
        return [m.name.split("/", 1)[1] for m in tar.getmembers() if "/" in m.name and m.isfile()]


# ── an app's database ───────────────────────────────────────────────────────────────────────────


def test_an_apps_database_written_during_a_snapshot_restores_whole(home, tmp_path, monkeypatch):
    """🔴 Red on integration: the snapshot copied the app's database file as bytes and left its
    log out, so the restored database held none of the notes."""
    _install_app(home)
    live = _LiveDatabase(home / APP_DB, "before")
    live.keep_writing()
    try:
        tarball = _snapshot(tmp_path / "out")
    finally:
        live.close()
    assert APP_DB in _members(tarball)
    assert not [m for m in _members(tarball) if m.startswith(APP_DB + "-")]

    fresh = _use_home(monkeypatch, tmp_path / "fresh")
    assert restore_main([str(tarball), "--mode", "replace"]) == 0

    restored = fresh / APP_DB
    assert _sidecars_beside(restored) == []
    assert live.ids <= _notes(restored)


def test_an_export_carries_an_apps_database_whole(home):
    """🔴 Red on integration: an export left every database inside a folder of files out, an
    app's own among them."""
    from personalclaw.portability import create_export_zip

    _install_app(home)
    live = _LiveDatabase(home / APP_DB, "before")
    try:
        data, _manifest = create_export_zip()
    finally:
        live.close()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n.split("/", 1)[1] for n in zf.namelist() if "/" in n]
        assert names.count(APP_DB) == 1, names
        assert not [n for n in names if n.startswith(APP_DB + "-")], names
        copy = home.parent / "exported.db"
        copy.write_bytes(zf.read(next(n for n in zf.namelist() if n.endswith("/" + APP_DB))))
    assert live.ids <= _notes(copy)


def test_the_manifest_declares_each_apps_database(home):
    """🔴 Red on integration: the Doctor counted an app's database, and the one a keep-data
    uninstall parks, as undeclared. A database anywhere else in an app's folder still counts."""
    _install_app(home)
    for rel in (APP_DB, f"apps/.{APP}.data/notes.db", f"apps/{APP}/cache.db"):
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(str(home / rel)).close()
    undeclared = inventory.audit_home(home).undeclared_dbs
    assert APP_DB not in undeclared
    assert f"apps/.{APP}.data/notes.db" not in undeclared
    assert f"apps/{APP}/cache.db" in undeclared


def test_the_doctor_says_what_is_true_of_a_users_own_database(home):
    """🔴 Red on integration: the Doctor's durability row said a user's own `runs.db` in the
    workspace was "in NO snapshot" while every snapshot carried it, and counted a text file named
    `notes.db` as a database."""
    import asyncio

    from personalclaw.resilience.doctor import DoctorContext, _probe_state_inventory

    (home / "workspace" / "notes").mkdir(parents=True)
    conn = sqlite3.connect(str(home / "workspace" / "runs.db"))
    conn.execute("CREATE TABLE runs (day TEXT)")
    conn.commit()
    conn.close()
    (home / "workspace" / "notes" / "notes.db").write_text("my reading list\n", encoding="utf-8")

    result = asyncio.run(_probe_state_inventory(DoctorContext(home=home)))

    assert result.evidence["undeclared_dbs"] == ["workspace/runs.db"]
    assert result.detail == "1 database no store declares"


# ── an app's data, carried forward ──────────────────────────────────────────────────────────────


def _bundle(tmp_path: Path, version: str) -> Path:
    """The app's bundle at *version*, as a local source the Store installs from."""
    folder = tmp_path / f"bundle-{version}" / APP
    folder.mkdir(parents=True)
    manifest = {"name": APP, "version": version, "displayName": "Field notes", "description": "x"}
    (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


@pytest.mark.parametrize("carry", ["update", "keep-data-uninstall"])
def test_an_apps_database_written_while_its_data_is_carried_forward_comes_through_whole(
    home, tmp_path, monkeypatch, carry
):
    """🔴 Red on integration: an update, and a keep-data uninstall, copied the app's `data/` as
    bytes while its backend wrote, the database and its log each at its own instant, and the copy
    is the one that survives. Each database comes through as one file, every row committed before
    the copy in it."""
    from personalclaw.apps import app_manager, manager

    monkeypatch.setattr(manager, "config_dir", lambda: home)
    assert app_manager.install(_bundle(tmp_path, "1.0.0"), confirm=True).ok
    live = _LiveDatabase(home / APP_DB, "kept")
    live.keep_writing()
    try:
        if carry == "update":
            assert app_manager.update(_bundle(tmp_path, "1.1.0"), confirm=True).ok
            carried = home / APP_DB
        else:
            assert app_manager.uninstall_keep_data(APP)
            carried = home / "apps" / f".{APP}.data" / "notes.db"
        assert _sidecars_beside(carried) == []
        assert live.ids <= _notes(carried)
    finally:
        live.close()


# ── a user's own file named like a store ────────────────────────────────────────────────────────


def test_a_users_own_files_named_like_stores_survive_a_snapshot_and_restore(
    home, tmp_path, monkeypatch
):
    """🔴 Red on integration: a file in the workspace whose NAME was a declared database's was
    left out of every snapshot, wherever it was."""
    (home / "workspace" / "research").mkdir(parents=True)
    (home / "workspace" / "runs.db").write_bytes(b"run 1: ok\nrun 2: ok\n")
    (home / "workspace" / "research" / "knowledge.db").write_bytes(b"my own reading list\n")
    live = _LiveDatabase(home / "workspace" / "notes" / "learning.db", "mine")
    try:
        tarball = _snapshot(tmp_path / "out")
    finally:
        live.close()
    assert not [m for m in _members(tarball) if m.startswith("workspace/notes/learning.db-")]

    fresh = _use_home(monkeypatch, tmp_path / "fresh")
    assert restore_main([str(tarball), "--mode", "replace"]) == 0
    assert (fresh / "workspace" / "runs.db").read_bytes() == b"run 1: ok\nrun 2: ok\n"
    assert (fresh / "workspace" / "research" / "knowledge.db").read_bytes() == (
        b"my own reading list\n"
    )
    assert _notes(fresh / "workspace" / "notes" / "learning.db") == live.ids

    other = _use_home(monkeypatch, tmp_path / "other")
    (other / "config.json").write_text("{}", encoding="utf-8")
    (other / "tasks").mkdir()
    assert restore_main([str(tarball), "--mode", "merge"]) == 0
    assert (other / "workspace" / "runs.db").read_bytes() == b"run 1: ok\nrun 2: ok\n"
    assert _notes(other / "workspace" / "notes" / "learning.db") == live.ids


# ── a restore never puts a log beside a database ────────────────────────────────────────────────


def _another_homes_app_files(tmp_path: Path) -> dict[str, bytes]:
    """The files of the app's database in another home whose backend has it open: the database,
    its log holding every note, and the log's index, as an archive written by an earlier version
    carried them, as files."""
    there = tmp_path / "there" / "notes.db"
    live = _LiveDatabase(there, "there")
    try:
        return {
            f"apps/{APP}/data/notes.db{suffix}": Path(f"{there}{suffix}").read_bytes()
            for suffix in ("", "-wal", "-shm")
        }
    finally:
        live.close()


def _snapshot_archive(path: Path, files: dict[str, bytes]) -> Path:
    """A snapshot archive holding *files*, as `personalclaw snapshot` lays one out."""
    files = {"MANIFEST.json": b'{"version": 3}', f"apps/{APP}/app.json": b"{}", **files}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        stage = Path(work) / "personalclaw-snapshot-20260101T000000Z"
        for rel, data in files.items():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            (stage / rel).write_bytes(data)
        with tarfile.open(path, "w:gz") as tar:
            tar.add(stage, arcname=stage.name)
    return path


def _export_archive(path: Path, files: dict[str, bytes]) -> Path:
    files = {"MANIFEST.json": b'{"version": 1}', f"apps/{APP}/app.json": b"{}", **files}
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(f"personalclaw-export-20260101T000000Z/{rel}", data)
    return path


def _merge_snapshot(archive: Path) -> None:
    assert restore_main([str(archive), "--mode", "merge"]) == 0


def _import_export(archive: Path) -> None:
    from personalclaw.portability import apply_import_zip

    apply_import_zip(archive, "merge")


@pytest.mark.parametrize(
    "build, bring_in",
    [(_snapshot_archive, _merge_snapshot), (_export_archive, _import_export)],
    ids=["merge-restore", "import"],
)
def test_a_merge_never_puts_another_homes_log_beside_a_database(home, tmp_path, build, bring_in):
    """🔴 Red on integration: the merge copied the archive's `-wal` and `-shm` in beside this home's
    database, whose next open read the other home's pages instead of its own notes."""
    _install_app(home)
    here = home / APP_DB
    conn = sqlite3.connect(str(here))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO notes VALUES (?, 'mine')", [(f"here-{i}",) for i in range(5)])
    conn.commit()
    conn.close()
    assert _sidecars_beside(here) == []

    archive = build(tmp_path / "archive", _another_homes_app_files(tmp_path))
    bring_in(archive)

    assert _sidecars_beside(here) == []
    assert _notes(here) == {f"here-{i}" for i in range(5)}


@pytest.mark.parametrize(
    "build, bring_in",
    [(_snapshot_archive, _merge_snapshot), (_export_archive, _import_export)],
    ids=["merge-restore", "import"],
)
def test_an_apps_database_this_home_lacks_comes_in_without_a_log(home, tmp_path, build, bring_in):
    """🔴 Red on integration. An app's database merges as the rest of its data does: one this home
    has stays as it is (above), and one it lacks comes in as the archive holds it, without the log
    an older archive carried beside it."""
    files = _another_homes_app_files(tmp_path)

    bring_in(build(tmp_path / "archive", files))

    here = home / APP_DB
    assert here.read_bytes() == files[APP_DB]
    assert _sidecars_beside(here) == []


def _a_log_without_its_database(tmp_path: Path, db: Path) -> None:
    """Leave at *db* what a store left when its database file was removed and its log was not:
    a `-wal` holding committed pages, with no database beside it."""
    files = _another_homes_app_files(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{db}-wal").write_bytes(files[f"{APP_DB}-wal"])


#: The rows of the database an archive holds, in the cases below where it meets a log of another.
ARCHIVED = {f"archived-{i}" for i in range(5)}


def _archived_database(path: Path) -> bytes:
    """A database as an archive holds one: one consistent file, its rows `ARCHIVED`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO notes VALUES (?, 'archived')", [(n,) for n in sorted(ARCHIVED)])
    conn.commit()
    conn.close()
    return path.read_bytes()


@pytest.mark.parametrize("rel", [APP_DB, "learning.db", "memory.db"])
@pytest.mark.parametrize(
    "build, bring_in",
    [(_snapshot_archive, _merge_snapshot), (_export_archive, _import_export)],
    ids=["merge-restore", "import"],
)
def test_a_database_comes_in_on_its_own_where_this_home_left_only_its_log(
    home, tmp_path, build, bring_in, rel
):
    """🔴 Red on integration: the merge put the archive's database in beside a log this home kept
    at its path with no database there, and its first open read the log's pages in place of the
    archive's rows. SQLite discards such a log the first time it opens a database with no pages,
    so it is removed before the archive's copy comes in, an app's database (one file of a folder)
    and a store kept as one file alike."""
    _a_log_without_its_database(tmp_path, home / rel)
    archived = _archived_database(tmp_path / "archived" / Path(rel).name)

    bring_in(build(tmp_path / "archive", {rel: archived}))

    assert _sidecars_beside(home / rel) == []
    assert _notes(home / rel) == ARCHIVED


@pytest.mark.parametrize(
    "build, bring_in",
    [(_snapshot_archive, _merge_snapshot), (_export_archive, _import_export)],
    ids=["merge-restore", "import"],
)
def test_a_merge_never_writes_through_a_link_this_home_has(home, tmp_path, build, bring_in):
    """🔴 Red on integration: a link this home had at a file's path, to a file that is not there,
    read as nothing there, and the merge wrote the archive's file through it, outside the home.
    What the home has at a path, a link included, stays as it is."""
    outside = tmp_path / "outside" / "today.md"
    outside.parent.mkdir()
    (home / "workspace" / "notes").mkdir(parents=True)
    (home / "workspace" / "notes" / "today.md").symlink_to(outside)

    bring_in(build(tmp_path / "archive", {"workspace/notes/today.md": b"the archive's notes\n"}))

    assert not outside.exists()
    assert (home / "workspace" / "notes" / "today.md").is_symlink()


def test_a_syncs_first_copy_of_a_store_comes_in_on_its_own(home, tmp_path):
    """🔴 Red on integration: a sync's first copy of a store into a home without it went in beside
    the log the home kept at its path, which SQLite read in place of the copy's rows."""
    from personalclaw.durability.cursor import CONSUMED
    from personalclaw.durability.db_merge import make_db_merger

    entry = next(e for e in inventory.sqlite_entries() if e.path == "learning.db")
    shard = tmp_path / "shard"
    _archived_database(shard / "db" / f"{entry.id}.db")
    _a_log_without_its_database(tmp_path, home / entry.path)

    assert make_db_merger(home)(entry, shard) == CONSUMED

    assert _sidecars_beside(home / entry.path) == []
    assert _notes(home / entry.path) == ARCHIVED


def test_a_replace_moves_aside_a_log_whose_database_is_gone(home, tmp_path):
    """A replace restore takes this home's log of a database aside even when the database file
    itself is gone, so the archive's copy never opens with it."""
    db = home / "learning.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes VALUES ('kept-0', 'kept')")
    conn.commit()
    conn.close()
    tarball = _snapshot(tmp_path / "out")
    db.unlink()
    _a_log_without_its_database(tmp_path, db)

    assert restore_main([str(tarball), "--mode", "replace"]) == 0

    assert _sidecars_beside(db) == []
    assert _notes(db) == {"kept-0"}
    (aside,) = home.glob("pre-restore-*")
    assert (aside / "learning.db-wal").is_file()


def test_the_merge_plan_says_what_becomes_of_an_apps_database(home, tmp_path):
    """🔴 Red on integration: the dry run's row for the installed apps read "per-file union" and
    did not say that an app's database comes in whole, or stays this home's."""
    from personalclaw.snapshot import merge_plan

    _install_app(home)
    unpacked = tmp_path / "unpacked"
    (unpacked / f"apps/{APP}/data").mkdir(parents=True)
    sqlite3.connect(str(unpacked / APP_DB)).close()
    (row,) = [r for r in merge_plan(unpacked, home, None) if r["path"] == "apps"]
    assert row["action"] == "merge"
    assert "a database whole, and this home's kept where it has one" in row["detail"]


@pytest.mark.parametrize("rel", ["learning.db", "memory.db"])
def test_a_replace_restore_moves_a_databases_log_aside_with_it(home, tmp_path, rel):
    """🔴 Red on integration: the replace moved the database aside and left the log a stopped
    gateway had left beside it, so the restored copy opened with this home's newer pages."""
    db = home / rel
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO notes VALUES (?, 'kept')", [(f"kept-{i}",) for i in range(5)])
    conn.close()
    tarball = _snapshot(tmp_path / "out")

    # What a gateway that stopped without closing the store left: the database, and a log
    # holding rows not yet moved into it, with the log's index.
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executemany("INSERT INTO notes VALUES (?, 'since')", [(f"since-{i}",) for i in range(50)])
    left = {suffix: Path(f"{db}{suffix}").read_bytes() for suffix in ("", "-wal", "-shm")}
    conn.close()
    for suffix, data in left.items():
        Path(f"{db}{suffix}").write_bytes(data)

    assert restore_main([str(tarball), "--mode", "replace"]) == 0

    assert _sidecars_beside(db) == []
    assert _notes(db) == {f"kept-{i}" for i in range(5)}
    (aside,) = home.glob("pre-restore-*")
    assert sorted(p.name for p in (aside / rel).parent.glob(Path(rel).name + "*")) == sorted(
        Path(rel).name + suffix for suffix in ("", "-wal", "-shm")
    )


def test_a_replace_sets_the_workspace_aside_as_it_is(home, tmp_path):
    """🔴 Red on integration: a replace copied the workspace into the pre-restore folder, leaving
    out each link in it, and then removed the folder, links and all; and it copied a database there
    as bytes while something still had it open, so what was written to it next went nowhere. The
    workspace is moved aside as it is, as every other store is."""
    (home / "workspace" / "notes").mkdir(parents=True)
    tarball = _snapshot(tmp_path / "out")
    kept_outside = tmp_path / "reading-list.md"
    kept_outside.write_text("a list kept outside the home\n", encoding="utf-8")
    (home / "workspace" / "notes" / "reading-list.md").symlink_to(kept_outside)
    live = _LiveDatabase(home / "workspace" / "notes" / "mine.db", "mine")
    try:
        assert restore_main([str(tarball), "--mode", "replace"]) == 0
        live.writer.execute("INSERT INTO notes VALUES ('after', 'written once the replace ran')")

        (aside,) = home.glob("pre-restore-*")
        assert (aside / "workspace" / "notes" / "reading-list.md").is_symlink()
        assert live.ids | {"after"} <= _notes(aside / "workspace" / "notes" / "mine.db")
    finally:
        live.close()
    assert not (home / "workspace" / "notes" / "mine.db").exists()


# ── a declared store, captured once and whole ───────────────────────────────────────────────────


def test_a_declared_store_held_open_is_in_the_snapshot_whole(home, tmp_path):
    """🔴 Red on integration: the pass that copies every other store copied these files again, as
    bytes, over their consistent copies; with a reader holding the log, the archive's copy had not
    even the table."""
    stores = [_LiveDatabase(home / rel, rel) for rel in ("learning.db", "loop/loops.db")]
    try:
        tarball = _snapshot(tmp_path / "out")
    finally:
        for store in stores:
            store.close()
    with tarfile.open(tarball) as tar:
        for store in stores:
            rel = store.path.relative_to(home).as_posix()
            (member,) = [m for m in tar.getmembers() if m.name.endswith("/" + rel)]
            copy = tmp_path / rel.replace("/", "_")
            copy.write_bytes(tar.extractfile(member).read())  # type: ignore[union-attr]
            assert _notes(copy) == store.ids, rel


def test_a_snapshot_carries_a_damaged_memory_database_as_it_is(home, tmp_path):
    """🔴 Red on integration: the pass that copies the core files read `memory.db` through the
    backup API with no way out, so one damaged file stopped every snapshot of the home. It is
    carried as the file it is, beside the rest of the home, and the restore drill names it."""
    (home / "memory.db").write_bytes(b"this memory store was damaged\n")
    (home / "workspace").mkdir()
    (home / "workspace" / "plans.md").write_text("the rest of the home\n", encoding="utf-8")

    tarball = _snapshot(tmp_path / "out")

    with tarfile.open(tarball) as tar:
        (member,) = [m for m in tar.getmembers() if m.name.endswith("/memory.db")]
        carried = tar.extractfile(member)
        assert carried is not None and carried.read() == b"this memory store was damaged\n"
    assert "workspace/plans.md" in _members(tarball)


def test_the_declared_databases_back_up_and_restore_as_before(home, tmp_path, monkeypatch):
    """The positive control: a declared database inside the workspace is copied through the backup
    API with its log folded in, comes back whole into an empty home, and merges row by row into a
    home that has its own."""
    rel = "workspace/lexicon/lexicon.db"
    db = home / rel
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO notes VALUES (?, 'term')", [(f"term-{i}",) for i in range(20)])
    try:
        tarball = _snapshot(tmp_path / "out")
    finally:
        conn.close()
    assert rel in _members(tarball)
    assert not [m for m in _members(tarball) if m.endswith(("-wal", "-shm", "-journal"))]

    fresh = _use_home(monkeypatch, tmp_path / "fresh")
    assert restore_main([str(tarball), "--mode", "replace"]) == 0
    assert _notes(fresh / rel) == {f"term-{i}" for i in range(20)}

    other = _use_home(monkeypatch, tmp_path / "other")
    (other / "config.json").write_text("{}", encoding="utf-8")
    (other / rel).parent.mkdir(parents=True)
    conn = sqlite3.connect(str(other / rel))
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes VALUES ('term-here', 'term')")
    conn.commit()
    conn.close()
    assert restore_main([str(tarball), "--mode", "merge"]) == 0
    assert _notes(other / rel) == {f"term-{i}" for i in range(20)} | {"term-here"}


# ── the restore drill reads only databases ──────────────────────────────────────────────────────


def test_the_restore_drill_reads_each_database_and_only_databases(home, tmp_path):
    """🔴 Red on integration: the drill read a user's own `notes.db` in the workspace as a database
    and failed the snapshot that carried it."""
    from personalclaw.durability import service

    (home / "workspace").mkdir()
    (home / "workspace" / "notes.db").write_bytes(b"not every .db file is a database\n")
    _install_app(home)
    conn = sqlite3.connect(str(home / APP_DB))
    conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.commit()
    conn.close()
    snapshot_main([str(home / "snapshots")])

    result = service.run_restore_drill()
    assert result.ok, result.detail
    assert result.extra["databases_checked"] >= 1


def test_the_restore_drill_still_fails_a_declared_database_that_is_not_one(home):
    """The drill's positive control: a declared store whose file in the archive is not a database
    any more is what a drill exists to report."""
    from personalclaw.durability import service

    _snapshot_archive(
        home / "snapshots" / "personalclaw-snapshot-20260101T000000Z.tar.gz",
        {"learning.db": b"this store was damaged before it was copied\n"},
    )
    result = service.run_restore_drill()
    assert result.ok is False
    assert any("learning.db" in problem for problem in result.extra["problems"])
