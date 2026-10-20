"""A keyword index that cannot be built never takes the memories with it.

``MemoryStore`` keeps its keyword (full-text) index in ``memory_index.db``. In a working folder's
memory partition that file is also where the partition's vector store keeps its memories
(``context._attach_vector_store``): its facts and episodes, held nowhere else. When the index's
table could not be made, the store deleted the file and the files SQLite keeps beside it and
started again, so a full disk, a read-only file, a lock held too long or a name already taken
threw away every memory the partition held.

Now nothing is deleted. An index that cannot be made is rebuilt in place from the memory files.
A database SQLite reports damaged is moved aside whole, beside where it was, and a fresh one is
started, with a notice. Whatever cannot be repaired is left as it is and recorded as degraded
keyword search with its reason, which the Doctor's memory check (and the memory page, which shows
that check) says, and which is logged once.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

import pytest

from personalclaw.memory import MemoryStore

# The driver the stores use: one copy of SQLite in the process, and the errors it raises.
from personalclaw.sqlite_compat import sqlite3

PROJECT = "/srv/example/newsletter"
FACT = ("project.release.cadence", "a release every six weeks")
EPISODE = "Agreed with the editor that the newsletter goes out on Thursdays"
INDEX_NAME_TAKEN = "there is already an index named memory_fts"
#: A word only the history entries hold: the index reads each file's path too, and the
#: partition's folder is named for the project.
SHIPPED = "shipped"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = (tmp_path / "home").resolve()
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _remember_in_the_project(home: Path) -> tuple[Path, Path]:
    """A project's partition as a session leaves it: its markdown memory, and a fact and an
    episode its vector store keeps in the partition's database. Returns the partition and that
    database."""
    from personalclaw.config.loader import memory_dir_for_cwd
    from personalclaw.vector_memory import VectorMemoryStore

    partition = memory_dir_for_cwd(PROJECT)
    MemoryStore(workspace=partition).init()
    db = partition / "memory_index.db"
    store = VectorMemoryStore(db_path=db)
    store.init()
    try:
        assert store.set_semantic(FACT[0], FACT[1], 0.9, "user_explicit") is None
        assert store.write_episodic(EPISODE, source="test")
    finally:
        store.close()
    return partition, db


def _facts(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT key, value_json FROM semantic_memory WHERE is_deleted=0"
        ).fetchall()
    finally:
        conn.close()
    return {k: json.loads(v) for k, v in rows}


def _episodes(db: Path) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT text FROM episodic_memories WHERE is_deleted=0").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _take_the_index_name(db: Path) -> None:
    """Make the index's table impossible to create in a healthy database: SQLite refuses a table
    whose name an index already has, whatever ``IF NOT EXISTS`` says."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE INDEX memory_fts ON semantic_memory(key)")
        conn.commit()
    finally:
        conn.close()


def _give_the_index_name_back(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP INDEX memory_fts")
        conn.commit()
    finally:
        conn.close()


def _todays_history(partition: Path) -> str:
    return str(partition / "memory" / "history" / f"{date.today().isoformat()}.md")


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "personalclaw.memory"
    ]


# ── an index that cannot be made ────────────────────────────────────────────────────────────────


def test_an_index_that_cannot_be_made_keeps_every_memory_in_the_partition(home):
    """🔴 Red on integration: the store deleted the partition's database, its fact and its
    episode with it, and made an empty one."""
    partition, db = _remember_in_the_project(home)
    _take_the_index_name(db)
    store = MemoryStore(workspace=partition)

    store.append_history("Shipped the newsletter draft")
    store.add_preference("Prefers plain-text email")
    store.search(SHIPPED)
    store.rebuild_index()

    assert _facts(db) == {FACT[0]: FACT[1]}
    assert _episodes(db) == [EPISODE]
    # The memory files were written whatever the index could do.
    assert "Shipped the newsletter draft" in Path(_todays_history(partition)).read_text()
    assert "Prefers plain-text email" in store.read_preferences()


def test_search_says_it_is_degraded_and_why_and_it_is_logged_once(home, caplog):
    """🔴 Red on integration: nothing recorded that search had lost its index, so nothing could
    say so."""
    from personalclaw.memory import degraded_keyword_indexes

    partition, db = _remember_in_the_project(home)
    _take_the_index_name(db)
    store = MemoryStore(workspace=partition)

    with caplog.at_level(logging.DEBUG, logger="personalclaw.memory"):
        store.append_history("Shipped the newsletter draft")
        store.add_preference("Prefers plain-text email")
        assert store.search(SHIPPED) == []
        assert store.rebuild_index() == 0

    assert INDEX_NAME_TAKEN in store.search_degraded()
    assert INDEX_NAME_TAKEN in degraded_keyword_indexes()[str(db)]
    (warning,) = _warnings(caplog)
    assert "workspace/_ext/" in warning and INDEX_NAME_TAKEN in warning
    assert "Nothing was deleted" in warning


def test_once_the_cause_is_gone_the_index_is_rebuilt_from_the_memory_files(home, caplog):
    """What a degraded index missed comes back: the first use after the cause is gone rebuilds the
    index from every memory file, not only the one being saved."""
    partition, db = _remember_in_the_project(home)
    _take_the_index_name(db)
    store = MemoryStore(workspace=partition)
    store.append_history("Shipped the newsletter draft")  # not indexed: the index could not be made
    assert store.search_degraded()

    _give_the_index_name_back(db)
    with caplog.at_level(logging.INFO, logger="personalclaw.memory"):
        hits = store.search(SHIPPED)

    assert [h["path"] for h in hits] == [_todays_history(partition)]
    assert store.search_degraded() == ""
    assert any("keyword search is back" in r.getMessage() for r in caplog.records)
    assert _facts(db) == {FACT[0]: FACT[1]}
    assert _episodes(db) == [EPISODE]


#: The two records FTS5 keeps of its own among the index's rows in ``memory_fts_data``: the
#: averages (1) and the structure, which says where the index's pages are (10). Every other row is
#: a page of the index.
_AVERAGES, _STRUCTURE = 1, 10


def _garble_the_index(db: Path) -> None:
    """Damage the index and only the index: the pages of its terms, which SQLite reports as a
    corrupt index while every table of the database is sound. Every SQLite build still opens an
    index damaged this way, so each can drop it and make it again."""
    conn = sqlite3.connect(str(db))
    try:
        pages = conn.execute(
            "UPDATE memory_fts_data SET block = x'00ff00ff00ff' WHERE id NOT IN (?, ?)",
            (_AVERAGES, _STRUCTURE),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    assert pages, "the index had no pages to damage"


def _garble_the_index_structure(db: Path) -> None:
    """Damage the one record of the index that says where its pages are. SQLite reports the index
    corrupt and every table of the database sound, on a build that can open the index at all."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE memory_fts_data SET block = x'00ff00ff00ff' WHERE id = ?", (_STRUCTURE,)
        )
        conn.commit()
    finally:
        conn.close()


def _this_sqlite_opens_an_index_whose_structure_is_damaged(tmp_path: Path) -> bool:
    """Whether this SQLite build can open, and so drop, an index whose structure record is
    damaged. An FTS5 that reads the record as it opens the index (SQLite 3.45's does) cannot, and
    then cannot drop the index either; a later one reads it only when the index is used."""
    from personalclaw.memory import _CREATE_FTS

    db = tmp_path / "structure-probe.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(_CREATE_FTS)
        conn.execute("INSERT INTO memory_fts (path, content) VALUES ('a.md', 'a word')")
        conn.commit()
    finally:
        conn.close()
    _garble_the_index_structure(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE memory_fts")
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()
    return True


def _drop_the_index_settings(db: Path) -> None:
    """Take away the row FTS5 keeps its format in: the index can then be neither opened nor
    dropped, and SQLite cannot say whether the rest of the database is sound."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM memory_fts_config")
        conn.commit()
    finally:
        conn.close()


def test_a_damaged_index_is_rebuilt_in_place_beside_the_memories(home):
    """Damage confined to the index is the index's own: it is dropped and made again inside the
    same database, from the memory files, and the database is not moved."""
    partition, db = _remember_in_the_project(home)
    store = MemoryStore(workspace=partition)
    store.append_history("Shipped the newsletter draft")
    assert store.search(SHIPPED)
    _garble_the_index(db)

    store.append_history("Sent the newsletter")

    assert [h["path"] for h in store.search(SHIPPED)] == [_todays_history(partition)]
    assert store.search_degraded() == ""
    assert _set_aside_beside(db) == []
    assert _facts(db) == {FACT[0]: FACT[1]}
    assert _episodes(db) == [EPISODE]
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    finally:
        conn.close()


def test_an_index_this_sqlite_cannot_open_never_moves_the_database_aside(home, tmp_path):
    """🔴 Red on integration on a SQLite whose FTS5 reads the index's structure as it opens it
    (3.45's): the whole-database check stopped at the index it could not open, naming it, that
    was taken for SQLite's word that the DATABASE was damaged, and the partition's database was
    moved aside with its fact and its episode.

    A damaged structure record is the index's own on every build. A build that can open the index
    rebuilds it in place. One that cannot open it cannot drop it either, so keyword search says
    why it is off, as for any index SQLite can neither open nor drop. Neither moves the database.
    """
    partition, db = _remember_in_the_project(home)
    store = MemoryStore(workspace=partition)
    store.append_history("Shipped the newsletter draft")
    assert store.search(SHIPPED)
    _garble_the_index_structure(db)

    store.append_history("Sent the newsletter")
    hits = [h["path"] for h in store.search(SHIPPED)]

    assert _set_aside_beside(db) == []
    assert _facts(db) == {FACT[0]: FACT[1]}
    assert _episodes(db) == [EPISODE]
    assert "Sent the newsletter" in Path(_todays_history(partition)).read_text()
    if _this_sqlite_opens_an_index_whose_structure_is_damaged(tmp_path):
        assert hits == [_todays_history(partition)]
        assert store.search_degraded() == ""
    else:
        assert hits == []
        assert "memory_fts" in store.search_degraded()


# ── a damaged database ──────────────────────────────────────────────────────────────────────────


def _set_aside_beside(db: Path) -> list[Path]:
    """The copies of *db* moved aside beside it — the files SQLite keeps beside one not counted."""
    return sorted(
        p
        for p in db.parent.iterdir()
        if p.name.startswith(db.name + ".") and not p.name.endswith(("-wal", "-shm", "-journal"))
    )


def test_a_damaged_index_is_moved_aside_whole_and_rebuilt_from_the_memory_files(home, caplog):
    """🔴 Red on integration: the damaged file was deleted. Now its bytes are kept, beside where it
    was, and the index the store starts again is rebuilt from the memory files."""
    store = MemoryStore()  # the home's own index, ``memory_index.db`` at the top of the home
    store.init()
    store.write_preferences("# Preferences\n\n- likes Python\n")
    db = home / "memory_index.db"
    damaged = b"this is not a database " * 64
    db.write_bytes(damaged)

    with caplog.at_level(logging.WARNING, logger="personalclaw.memory"):
        assert store.rebuild_index() == 2  # preferences.md and projects.md

    (moved,) = _set_aside_beside(db)
    assert moved.read_bytes() == damaged
    assert moved.name.startswith("memory_index.db.broken-")
    assert [Path(h["path"]).name for h in store.search("Python")] == ["preferences.md"]
    assert store.search_degraded() == ""
    (warning,) = _warnings(caplog)
    assert moved.name in warning and "not deleted" in warning


def test_a_new_index_that_cannot_be_built_yet_is_said_so_not_claimed(home, caplog, monkeypatch):
    """The notice says what happened: a damaged index moved aside whose new one the disk has no
    room for yet is not "rebuilt", and keyword search is recorded degraded with that reason."""
    store = MemoryStore()
    store.init()
    db = home / "memory_index.db"
    db.write_bytes(b"this is not a database " * 64)

    def _no_room(self, conn):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(MemoryStore, "_fill", _no_room)
    with caplog.at_level(logging.WARNING, logger="personalclaw.memory"):
        assert store.rebuild_index() == 0

    (moved,) = _set_aside_beside(db)
    moved_notice, degraded_notice = _warnings(caplog)
    assert moved.name in moved_notice and "once it can be" in moved_notice
    assert "rebuilt from the memory files, so nothing is missing" not in moved_notice
    assert "the disk is full" in degraded_notice
    assert store.search_degraded() == "the disk is full"


def _blank_the_header(db: Path) -> tuple[bytes, bytes]:
    """SQLite's header says what the file is; without it SQLite reads no database at all. Returns
    the file as it was and as it is now."""
    whole = db.read_bytes()
    damaged = b"\0" * 16 + whole[16:]
    db.write_bytes(damaged)
    return whole, damaged


def _break_a_page_of_the_index(db: Path) -> tuple[bytes, bytes]:
    """A page of the database itself damaged — here one of the index's tables, the pages a
    keyword index writes most — which SQLite reports as a malformed database image."""
    conn = sqlite3.connect(str(db))
    try:
        root = conn.execute(
            "SELECT rootpage FROM sqlite_master WHERE name = 'memory_fts_content'"
        ).fetchone()[0]
        size = conn.execute("PRAGMA page_size").fetchone()[0]
    finally:
        conn.close()
    whole = db.read_bytes()
    at = (root - 1) * size
    damaged = whole[:at] + b"\xff" * 64 + whole[at + 64 :]
    db.write_bytes(damaged)
    return whole, damaged


@pytest.mark.parametrize(
    "damage, readable",
    [
        # What the header held is all the file lacks: put back, the database reads again.
        (_blank_the_header, lambda whole, moved: whole[:16] + moved[16:]),
        # The memories' own pages are sound, so they read straight from the moved copy.
        (_break_a_page_of_the_index, lambda whole, moved: moved),
    ],
    ids=["no-header", "damaged-page"],
)
def test_a_damaged_partition_database_is_moved_aside_with_its_memories(
    home, tmp_path, damage, readable
):
    """🔴 Red on integration: the partition's database was deleted, and its memories with it. Now
    it is moved aside byte for byte, so what it held can still be recovered from it, and the
    partition starts a new database."""
    partition, db = _remember_in_the_project(home)
    store = MemoryStore(workspace=partition)
    store.append_history("Drafted the newsletter")  # the index is made, then the file is damaged
    whole, damaged = damage(db)

    store.append_history("Shipped the newsletter draft")
    store.search(SHIPPED)

    (moved,) = _set_aside_beside(db)
    assert moved.read_bytes() == damaged
    from personalclaw.memory import set_aside_databases

    assert set_aside_databases(home) == [moved]
    recovered = tmp_path / "recovered.db"
    recovered.write_bytes(readable(whole, moved.read_bytes()))
    assert _facts(recovered) == {FACT[0]: FACT[1]}
    assert _episodes(recovered) == [EPISODE]
    # The partition goes on, on a new database whose index holds its memory files.
    assert [h["path"] for h in store.search(SHIPPED)] == [_todays_history(partition)]
    assert store.search_degraded() == ""


def test_a_damaged_page_beside_an_index_this_sqlite_cannot_open_is_still_found(home, tmp_path):
    """Where SQLite cannot open the index, its whole-database check stops there, so each other
    table is checked on its own: a damaged page of the database is found beside that index too,
    and the database is moved aside with its memories, as it is on a build that opens the index.
    """
    partition, db = _remember_in_the_project(home)
    store = MemoryStore(workspace=partition)
    store.append_history("Drafted the newsletter")  # the index is made, then the file is damaged
    _garble_the_index_structure(db)
    _, damaged = _break_a_page_of_the_index(db)

    store.append_history("Shipped the newsletter draft")
    store.search(SHIPPED)

    (moved,) = _set_aside_beside(db)
    assert moved.read_bytes() == damaged
    recovered = tmp_path / "recovered.db"
    recovered.write_bytes(damaged)  # the memories' own pages are sound
    assert _facts(recovered) == {FACT[0]: FACT[1]}
    assert _episodes(recovered) == [EPISODE]
    assert [h["path"] for h in store.search(SHIPPED)] == [_todays_history(partition)]
    assert store.search_degraded() == ""


def test_a_partitions_vector_store_is_closed_before_its_database_is_moved(home):
    """A connection left open on a moved database goes on writing into it after the store has a
    new one, so the store's own vector store on that file is closed and let go first; the next
    use opens one on the new database."""
    from personalclaw.vector_memory import VectorMemoryStore

    partition, db = _remember_in_the_project(home)
    store = MemoryStore(workspace=partition)
    vectors = VectorMemoryStore(db_path=db)
    vectors.init()
    store.vector_store = vectors
    with db.open("r+b") as f:
        f.write(b"\0" * 16)

    store.append_history("Shipped the newsletter draft")

    assert store.vector_store is None
    with pytest.raises(RuntimeError, match="not initialized"):
        vectors.db  # noqa: B018 — closed: reading it raises
    assert len(_set_aside_beside(db)) == 1


def test_the_store_kept_for_the_folder_lets_go_of_the_database_before_it_is_moved(home):
    """Any store may find the damage, not only the one kept for the folder: the kept one's vector
    store is closed and the folder's store dropped, so the next use opens both on the new
    database."""
    import personalclaw.context as context
    from personalclaw.vector_memory import VectorMemoryStore

    partition, db = _remember_in_the_project(home)
    kept = MemoryStore(workspace=partition)
    vectors = VectorMemoryStore(db_path=db)
    vectors.init()
    kept.vector_store = vectors
    context._memory_stores[str(partition)] = kept
    with db.open("r+b") as f:
        f.write(b"\0" * 16)

    MemoryStore(workspace=partition).append_history("Shipped the newsletter draft")

    assert str(partition) not in context._memory_stores
    with pytest.raises(RuntimeError, match="not initialized"):
        vectors.db  # noqa: B018 — closed: reading it raises
    assert len(_set_aside_beside(db)) == 1


@pytest.mark.parametrize(
    "cause, why",
    [
        (_take_the_index_name, INDEX_NAME_TAKEN),
        (_drop_the_index_settings, "invalid fts5 file format"),
    ],
    ids=["name-taken", "index-cannot-open"],
)
def test_a_database_sqlite_does_not_call_damaged_is_never_moved_aside(home, cause, why):
    """Only SQLite's word that a database is damaged moves it. An index that cannot be made, or
    one SQLite can neither open nor drop, leaves the database where it is, every memory in it,
    and keyword search says why it is off."""
    partition, db = _remember_in_the_project(home)
    store = MemoryStore(workspace=partition)
    store.append_history("Drafted the newsletter")
    if cause is _take_the_index_name:
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DROP TABLE memory_fts")
            conn.commit()
        finally:
            conn.close()
    cause(db)

    store.append_history("Shipped the newsletter draft")
    store.search(SHIPPED)

    assert _set_aside_beside(db) == []
    assert _facts(db) == {FACT[0]: FACT[1]}
    assert _episodes(db) == [EPISODE]
    assert why in store.search_degraded()


# ── what the Doctor says ────────────────────────────────────────────────────────────────────────


def _keyword_check(home: Path) -> dict:
    from personalclaw.resilience.doctor import DoctorContext, run_capability

    report = asyncio.run(run_capability("memory", DoctorContext(home=home)))
    (row,) = [p for p in report["probes"] if p["id"] == "memory.keyword-search"]
    return row


def test_the_doctor_says_keyword_search_is_degraded_and_why(home):
    """🔴 Red on integration: the memory checks had nothing to say about the keyword index."""
    partition, db = _remember_in_the_project(home)
    assert _keyword_check(home)["ok"]
    _take_the_index_name(db)
    MemoryStore(workspace=partition).append_history("Shipped the newsletter draft")

    row = _keyword_check(home)

    assert not row["ok"]
    assert INDEX_NAME_TAKEN in row["detail"]
    assert "workspace/_ext/" in row["detail"]
    assert "nothing was deleted" in row["remedy"].lower()
    assert not row.get("fix_id")


def test_the_doctor_tries_a_degraded_index_again_and_says_what_is_true_now(home):
    """A folder whose vector store has opened may not use its keyword index again for a long time,
    so the check rebuilds a recorded index from the memory files before it reports it."""
    partition, db = _remember_in_the_project(home)
    _take_the_index_name(db)
    store = MemoryStore(workspace=partition)
    store.append_history("Shipped the newsletter draft")
    assert not _keyword_check(home)["ok"]

    _give_the_index_name_back(db)
    row = _keyword_check(home)

    assert row["ok"], row
    assert store.search_degraded() == ""
    assert [h["path"] for h in store.search(SHIPPED)] == [_todays_history(partition)]


def test_the_doctor_names_a_partition_database_moved_aside(home):
    partition, db = _remember_in_the_project(home)
    db.write_bytes(b"\0" * 16 + db.read_bytes()[16:])
    MemoryStore(workspace=partition).append_history("Shipped the newsletter draft")
    (moved,) = _set_aside_beside(db)

    row = _keyword_check(home)

    assert not row["ok"]
    assert moved.name in row["detail"]
    assert "snapshot" in row["remedy"].lower()


def test_the_doctor_reads_only_its_own_homes_indexes(home, tmp_path):
    """The record is the process's; a check of one home says nothing about an index elsewhere."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    store = MemoryStore(workspace=elsewhere)
    store.init()
    conn = sqlite3.connect(str(elsewhere / "memory_index.db"))
    try:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("CREATE INDEX memory_fts ON t(x)")
        conn.commit()
    finally:
        conn.close()
    store.append_history("Shipped the newsletter draft")
    assert store.search_degraded()

    assert _keyword_check(home)["ok"]


# ── the maintenance pass ────────────────────────────────────────────────────────────────────────


def test_the_maintenance_pass_does_not_report_a_rebuild_that_did_not_happen(home):
    """🔴 Red on integration: the pass said "FTS index rebuilt" over an index it could not build,
    and the engine recorded the job as done."""
    from personalclaw.resilience.remediation import _job_rebuild_memory_fts

    store = MemoryStore()
    store.init()
    conn = sqlite3.connect(str(home / "memory_index.db"))
    try:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("CREATE INDEX memory_fts ON t(x)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match=INDEX_NAME_TAKEN):
        _job_rebuild_memory_fts()
