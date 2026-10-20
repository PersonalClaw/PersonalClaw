"""Every database the product opens goes through one SQLite: ``personalclaw.sqlite_compat``.

🔴 The defect. ``sqlite_compat`` prefers ``pysqlite3`` when it is installed, and on Linux x86_64 it
is (``pyproject.toml`` installs ``pysqlite3-binary`` there): a second copy of SQLite beside the
stdlib's. The stores open their databases through ``sqlite_compat``; eleven other modules imported
the stdlib ``sqlite3`` and opened the same files. Two copies of SQLite in one process on one
database is a way to corrupt it that SQLite's own documentation names. Each copy keeps track of
the process's locks on a file for its own connections only, and a POSIX lock belongs to the
process, so neither copy sees the other's. A connection the stdlib copy opened on a store the other
copy held open took itself for the last one: its close checkpointed the WAL and deleted it under
the store's live connection. The store's next commit then failed with a disk I/O error, or went to
a file no new connection reads. The export ran that checkpoint on every store every hour, took its
consistent copy the same way, and the reclaim ran ``VACUUM`` on every store through the other copy.

Now every module imports ``sqlite3`` from ``personalclaw.sqlite_compat``. What this file holds:

* the real paths that open a live store, the export's checkpoint and its consistent copy and the
  reclaim, each against a store with a live connection on the store's driver, which must still
  see its next commit. Where that driver is a second copy of SQLite (Linux x86_64), each of them
  cost the store that commit before. Where it is the stdlib's own, there is one copy, and they pass
  either way;
* the rail that holds the cause on every platform: no module under ``src/`` imports the stdlib
  driver, except ``sqlite_compat``, which is where the choice is made.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from every_database_opens_through_one_sqlite_shared import _stdlib_driver_imports

from personalclaw.durability import footprint, shards
from personalclaw.sqlite_compat import sqlite3


@contextmanager
def _live_store(path: Path) -> Iterator[sqlite3.Connection]:
    """A store's connection, opened the way every store opens one: on the store's driver, in WAL
    mode, and held open between the writes it makes."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
        conn.commit()
        yield conn
    finally:
        conn.close()


def _write(store: sqlite3.Connection, text: str) -> None:
    store.execute("INSERT INTO facts (text) VALUES (?)", (text,))
    store.commit()


def _read_anew(path: Path) -> list[str]:
    """What a connection opened now reads, as the next start of the gateway would."""
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT text FROM facts ORDER BY id")]
    finally:
        conn.close()


# ── the real paths ────────────────────────────────────────────────────────────────────────────


def test_the_exports_checkpoint_leaves_the_store_its_next_commit(tmp_path: Path) -> None:
    """The hourly export's change check folds each store's WAL into its file first."""
    db = tmp_path / "memory.db"
    with _live_store(db) as store:
        _write(store, "written before the export")
        shards._fold_wal(db)
        _write(store, "written after the export")
        assert _read_anew(db) == ["written before the export", "written after the export"]


def test_the_exports_consistent_copy_leaves_the_store_its_next_commit(tmp_path: Path) -> None:
    """The export copies each live store through the backup API before it reads the copy."""
    db = tmp_path / "knowledge.db"
    work = tmp_path / "export"
    work.mkdir()
    with _live_store(db) as store:
        _write(store, "written before the export")
        copy = shards._consistent_db_copy(db, work)
        _write(store, "written after the export")
        assert copy is not None
        assert _read_anew(copy) == ["written before the export"]
        assert _read_anew(db) == ["written before the export", "written after the export"]


def test_the_reclaim_leaves_the_store_its_next_commit(tmp_path: Path) -> None:
    """The daily reclaim runs ``VACUUM`` on every store while the gateway holds it open."""
    db = tmp_path / "runs.db"
    with _live_store(db) as store:
        _write(store, "written before the reclaim")
        footprint.reclaim_store(db)
        _write(store, "written after the reclaim")
        assert _read_anew(db) == ["written before the reclaim", "written after the reclaim"]


# ── the rail ──────────────────────────────────────────────────────────────────────────────────


_CAUGHT = {
    "a bare import": "import sqlite3\n",
    "an import under another name": "import sqlite3 as driver\n",
    "one of several": "import os, sqlite3\n",
    "a name from it": "from sqlite3 import Row\n",
    "its dbapi2 module": "from sqlite3.dbapi2 import connect\n",
    "its dbapi2 module, imported": "import sqlite3.dbapi2\n",
    "the extension under it": "import _sqlite3\n",
    "an import inside a function": "def read():\n    import sqlite3\n    return sqlite3\n",
    "importlib": "import importlib\ndriver = importlib.import_module('sqlite3')\n",
    "__import__": "driver = __import__('sqlite3')\n",
}

_PASSED = {
    "the shared driver": "from personalclaw.sqlite_compat import sqlite3\n",
    "the shared module": "from personalclaw import sqlite_compat\n",
    "another module with a like name": "import sqlite3_utils\n",
    "a relative import of a like name": "from .sqlite3 import helper\n",
    "the name in a string": "doc = 'import sqlite3'\n",
    "an attribute of that name": "driver = conn.sqlite3\n",
}


@pytest.mark.parametrize("shape", sorted(_CAUGHT))
def test_the_rail_catches(shape: str) -> None:
    assert _stdlib_driver_imports(ast.parse(_CAUGHT[shape])), f"{shape} was not caught"


@pytest.mark.parametrize("shape", sorted(_PASSED))
def test_the_rail_passes(shape: str) -> None:
    assert _stdlib_driver_imports(ast.parse(_PASSED[shape])) == [], f"{shape} was caught"
