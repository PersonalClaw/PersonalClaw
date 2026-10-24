"""Memory's keyword search reads what a memory says and its file's own name, never where it is.

The keyword index kept each memory file's absolute path in a column it searched, so a word in the
home's own path matched every memory file: a home under a folder named for a person, a project or
a tool found every memory for that word. CI's merge tier showed it, whose scratch folder's path
holds "python". The absolute path is now stored and not searched (``UNINDEXED``), and the file's
name inside the memory folder is a column of its own, so a memory is still found by its name. An
index an earlier schema made is rebuilt from the memory files when it is next opened, once, however
many stores open it: it is derived from them, so the rebuild loses nothing, and it drops the
index's own table alone, never another store's in the same database.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from personalclaw.memory import MemoryStore

# The driver the stores use: one copy of SQLite in the process.
from personalclaw.sqlite_compat import sqlite3

#: A word that is in the home's path and in no memory file.
PATH_WORD = "zebrafinch"
#: The schemas an earlier release made: 0.2.7's, which searched the absolute path, and the first
#: fix's, which searched no name at all.
OLD_SCHEMAS = {
    "0.2.7": (
        "CREATE VIRTUAL TABLE memory_fts USING fts5(path, content, tokenize='porter unicode61')"
    ),
    "path unsearched, no name": (
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "path UNINDEXED, content, tokenize='porter unicode61')"
    ),
}
CURRENT = "path UNINDEXED, content, name, tokenize='porter unicode61'"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = (tmp_path / f"python-{PATH_WORD}" / "home").resolve()
    home.mkdir(parents=True)
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _names(hits: list[dict]) -> list[str]:
    return [Path(h["path"]).name for h in hits]


def _schema(db: Path) -> str:
    conn = sqlite3.connect(str(db))
    try:
        (sql,) = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'memory_fts'").fetchone()
        return str(sql)
    finally:
        conn.close()


def test_a_word_in_the_home_path_finds_only_the_memories_that_say_it(home) -> None:
    store = MemoryStore()
    store.init()
    store.write_preferences("# Preferences\n\n- likes Python\n")
    assert "python" in str(home).lower() and PATH_WORD in str(home)
    assert _names(store.search("Python")) == ["preferences.md"]
    assert store.search(PATH_WORD) == [], "a word only the path holds finds no memory"
    assert CURRENT in _schema(home / "memory_index.db")


def test_a_memory_files_own_name_still_finds_it(home) -> None:
    store = MemoryStore()
    store.init()
    store.write_preferences("# What I like\n\n- green tea\n")
    assert _names(store.search("preferences")) == ["preferences.md"]
    assert _names(store.search("tea")) == ["preferences.md"]


def _old_index(db: Path, schema: str) -> None:
    """The index as an earlier schema made it, with a row no memory file backs, beside a table of
    another store's that the same database holds."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE IF EXISTS memory_fts")
        conn.execute(schema)
        conn.execute(
            "INSERT INTO memory_fts (path, content) VALUES (?, ?)",
            (f"/gone/{PATH_WORD}.md", "stale"),
        )
        conn.execute("CREATE TABLE IF NOT EXISTS another_stores (fact TEXT)")
        conn.execute("INSERT INTO another_stores VALUES ('kept')")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("made_by", sorted(OLD_SCHEMAS))
def test_an_index_an_earlier_schema_made_is_rebuilt_from_the_memory_files(home, made_by) -> None:
    store = MemoryStore()
    store.init()
    store.write_preferences("# Preferences\n\n- likes Python\n")
    db = home / "memory_index.db"
    _old_index(db, OLD_SCHEMAS[made_by])

    assert store.search(PATH_WORD) == [], "the old index is rebuilt before it is searched"
    assert _names(store.search("Python")) == ["preferences.md"]
    assert CURRENT in _schema(db)
    conn = sqlite3.connect(str(db))
    try:
        indexed = sorted(conn.execute("SELECT name FROM memory_fts").fetchall())
        kept = conn.execute("SELECT fact FROM another_stores").fetchall()
    finally:
        conn.close()
    assert indexed == [("preferences.md",), ("projects.md",)], "every memory file, no other"
    assert kept == [("kept",)], "the rebuild drops the index's own table alone"
    assert store.search_degraded() == ""


def test_two_stores_opening_an_old_index_at_once_rebuild_it_once(home, monkeypatch) -> None:
    MemoryStore().init()
    db = home / "memory_index.db"
    _old_index(db, OLD_SCHEMAS["0.2.7"])
    rebuilds: list[int] = []
    rebuild = MemoryStore._rebuild_in_place

    def counted(self: MemoryStore, **how: bool) -> object:
        rebuilds.append(1)
        return rebuild(self, **how)

    monkeypatch.setattr(MemoryStore, "_rebuild_in_place", counted)
    stores = [MemoryStore(), MemoryStore()]
    start = threading.Barrier(len(stores))
    found: list[list[str]] = []

    def search(store: MemoryStore) -> None:
        start.wait()
        found.append(_names(store.search("projects")))

    threads = [threading.Thread(target=search, args=(s,)) for s in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert found == [["projects.md"], ["projects.md"]], found
    assert rebuilds == [1], "one rebuild; the other store finds the index current"


def test_a_search_while_another_store_rebuilds_the_index_finds_what_the_files_say(
    home, monkeypatch
) -> None:
    """The guarantee: a search never reads a half-rebuilt index. One store is held inside its
    rebuild, its rows not yet written, while a second searches: the second finds the memory, after
    the rebuild or from the index as it was, and never an empty index. The hold is bounded, so a
    search that is kept waiting for the rebuild lets it go on."""
    MemoryStore().init()
    db = home / "memory_index.db"
    _old_index(db, OLD_SCHEMAS["0.2.7"])
    filling = threading.Event()
    searched = threading.Event()
    fill = MemoryStore._fill

    def held(self: MemoryStore, conn: object) -> int:
        if not filling.is_set():
            filling.set()
            searched.wait(timeout=0.3)
        return fill(self, conn)

    monkeypatch.setattr(MemoryStore, "_fill", held)
    found: dict[str, list[str]] = {}

    def rebuild() -> None:
        found["rebuilding"] = _names(MemoryStore().search("projects"))

    def search() -> None:
        assert filling.wait(timeout=30), "the first store never reached its rebuild"
        found["during"] = _names(MemoryStore().search("projects"))
        searched.set()

    threads = [threading.Thread(target=rebuild), threading.Thread(target=search)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert found == {"rebuilding": ["projects.md"], "during": ["projects.md"]}, found


def test_an_index_another_process_rebuilt_while_this_one_waited_is_used_as_it_is(
    home, monkeypatch
) -> None:
    """Another process does not share this one's repair lock, so the rebuild looks at the index
    again once it holds the database's write lock: a current index is used as it is, not rebuilt
    a second time."""
    MemoryStore().init()
    _old_index(home / "memory_index.db", OLD_SCHEMAS["0.2.7"])
    assert _names(MemoryStore().search("projects")) == ["projects.md"], "rebuilt, so current"
    fills: list[int] = []
    fill = MemoryStore._fill

    def counted(self: MemoryStore, conn: object) -> int:
        fills.append(1)
        return fill(self, conn)

    monkeypatch.setattr(MemoryStore, "_fill", counted)
    store = MemoryStore()
    store._rebuild_in_place(unless_current=True).close()
    assert fills == [], "a current index is not rebuilt"
    store._rebuild_in_place().close()
    assert fills == [1], "a repair rebuilds whatever the schema"
    assert _names(store.search("projects")) == ["projects.md"]
