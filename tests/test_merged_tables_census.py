"""Every table a merge brings another home's rows into has a key that tells one row from another.

A merge restore, an archive import and a folder sync merge each database the inventory declares
``sqlite_attach_ignore`` (but ``memory.db``, which keeps its own merge) one table at a time
(``snapshot._merge_sqlite_attach``): a row comes in unless the table already holds it, and only a
key can say whether it does. A table whose rows the store numbers itself, with nothing else to name
them, was merged by number, so another home's row whose number this home had used was dropped: the
learning log lost a random part of every other home's captures, passes and journal that way.

So each such table needs a key: a primary key or unique index of its own, or, for a table the
store numbers, a unique key over required columns that the merge matches rows by
(``durability.numbered_rows``). This census opens every merged store the way its module opens one,
in a fresh home, and reads every table the merge would take in. A store added to the inventory with
this merge must be opened here too, so it is read before it ships.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.durability import inventory as inv
from personalclaw.durability import numbered_rows
from personalclaw.sqlite_compat import sqlite3


def _library(path: Path) -> None:
    from personalclaw.knowledge.store import KnowledgeStore

    KnowledgeStore(str(path)).close()


def _vocabulary(path: Path) -> None:
    from personalclaw.lexicon.store import LexiconStore

    LexiconStore(str(path)).db.close()


def _declared(entry_id: str):
    """A store that declares how a database file of it is opened (``StateEntry.open_database``)."""

    def _open(path: Path) -> None:
        entry = next(e for e in inv.INVENTORY if e.id == entry_id)
        assert entry.open_database is not None, entry_id
        entry.open_database(path)

    return _open


#: How each store the merge takes in row by row creates a database file of its own.
_OPENS = {
    "knowledge_db": _library,
    "knowledge_root_db": _library,
    "lexicon_db": _vocabulary,
    "learning_db": _declared("learning_db"),
}


def _merged_row_by_row() -> set[str]:
    return {
        e.id
        for e in inv.sqlite_entries()
        if e.merge == inv.MERGE_SQLITE_ATTACH_IGNORE and e.path != "memory.db" and e.merged_in
    }


def _merged_tables(conn: "sqlite3.Connection") -> list[str]:
    """The tables the merge takes in: every real table but a virtual table's own."""
    from personalclaw.snapshot import _virtual_tables

    virtual = _virtual_tables(conn, "main")
    skip = tuple(virtual) + tuple(v + "_" for v in virtual)
    return [
        name
        for (name,) in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
        if not (name.startswith("sqlite_") or name in skip or name.startswith(skip))
    ]


def _without_a_key(conn: "sqlite3.Connection") -> list[str]:
    """Each table the merge would take in by row number, or with nothing to tell rows apart."""
    tables = _merged_tables(conn)
    numbered = numbered_rows.numbered_tables(conn, tables)
    out: list[str] = []
    for table in tables:
        if table in numbered:
            continue
        info = conn.execute(f'PRAGMA main.table_info("{table}")').fetchall()
        key = [r for r in info if r[5]]
        sql = conn.execute(
            "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        by_number = (
            len(key) == 1
            and (key[0][2] or "").strip().upper() == "INTEGER"
            and "WITHOUT ROWID" not in (sql or "").upper()
        )
        unique = [r for r in conn.execute(f'PRAGMA main.index_list("{table}")').fetchall() if r[2]]
        if by_number or not (key or unique):
            out.append(table)
    return out


def test_every_store_merged_row_by_row_is_opened_here():
    assert _merged_row_by_row() == set(_OPENS)


def test_every_table_a_merge_takes_in_has_a_key(tmp_path):
    seen: dict[str, list[str]] = {}
    for entry_id, opens in _OPENS.items():
        path = tmp_path / entry_id / "store.db"
        path.parent.mkdir()
        opens(path)
        conn = sqlite3.connect(str(path))
        try:
            seen[entry_id] = _merged_tables(conn)
            assert _without_a_key(conn) == [], entry_id
        finally:
            conn.close()
    # Read for real: each store's own tables, the learning log's numbered ones among them.
    assert {"items", "tags", "sources"} <= set(seen["knowledge_db"])
    assert {"terms", "corrections"} <= set(seen["lexicon_db"])
    assert {"staging", "flush_records", "curator_mutations", "surfacing_events"} <= set(
        seen["learning_db"]
    )


def test_the_census_finds_a_table_merged_by_number(tmp_path):
    """The control: a numbered table with nothing else to name its rows, and a table with no key at
    all, are each found."""
    conn = sqlite3.connect(str(tmp_path / "control.db"))
    try:
        conn.execute("CREATE TABLE numbered (id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
        conn.execute("CREATE TABLE loose (note TEXT)")
        conn.execute("CREATE TABLE named (id TEXT PRIMARY KEY, note TEXT)")
        assert _without_a_key(conn) == ["numbered", "loose"]
    finally:
        conn.close()
