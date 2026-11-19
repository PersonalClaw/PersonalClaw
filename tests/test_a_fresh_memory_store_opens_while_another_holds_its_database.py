"""A memory store opening a fresh ``memory.db`` waits out another connection's lock (#6172).

Switching a database still in rollback mode to WAL takes its exclusive lock, and while another
connection holds the write lock, or is switching the same database, SQLite answers "database is
locked" at once: it calls no busy handler there, to avoid a deadlock, so the 5 s busy timeout never
applied. A new home's first start opens its memory store from more than one place, and a request
that opened it on its own path (``handlers/_shared._get_memory``) answered 500. Once a database is
in WAL mode the switch never waits, so only a fresh one ever raced.
"""

from __future__ import annotations

import threading
import time

import pytest

from personalclaw import sqlite_compat
from personalclaw.sqlite_compat import sqlite3
from personalclaw.vector_memory import VectorMemoryStore

#: Two stores opening one fresh database at once were refused in about one trial in fifteen before
#: the fix, so 150 trials all opening is a pass the old switch fails with near certainty (~5 s).
RACES = 150


def _holding_the_write_lock(path, *, for_secs: float) -> threading.Thread:
    """A connection in its own thread that takes *path*'s write lock in rollback mode, holds it for
    *for_secs* and lets go. Returns once the lock is held."""
    held = threading.Event()

    def hold() -> None:
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            held.set()
            time.sleep(for_secs)
            conn.execute("COMMIT")
        finally:
            conn.close()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert held.wait(5), "the holder never took the lock"
    return thread


def _fresh(path) -> None:
    """A database in rollback mode with nothing in WAL yet, as a new home's is."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS untouched (x)")
    conn.commit()
    conn.close()


def test_a_store_opening_while_another_connection_writes_its_fresh_database_opens(tmp_path):
    path = tmp_path / "memory.db"
    _fresh(path)
    holder = _holding_the_write_lock(path, for_secs=0.4)
    store = VectorMemoryStore(db_path=path)
    store.init()
    holder.join(5)
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_two_stores_opening_one_fresh_database_at_once_both_open(tmp_path):
    refused: list[str] = []
    for n in range(RACES):
        path = tmp_path / f"memory-{n}.db"
        _fresh(path)
        start = threading.Barrier(2)

        def open_one(path=path, start=start) -> None:
            store = VectorMemoryStore(db_path=path)
            start.wait()
            try:
                store.init()
            except sqlite3.OperationalError as exc:
                refused.append(str(exc))

        racers = [threading.Thread(target=open_one) for _ in range(2)]
        for racer in racers:
            racer.start()
        for racer in racers:
            racer.join(30)
    assert refused == [], f"{len(refused)} of {RACES * 2} opens were refused: {refused[:3]}"


def test_the_switch_waits_only_so_long_then_says_the_database_is_locked(tmp_path):
    path = tmp_path / "memory.db"
    _fresh(path)
    holder = _holding_the_write_lock(path, for_secs=1.5)
    conn = sqlite3.connect(str(path), isolation_level=None)
    began = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        sqlite_compat.use_wal(conn, within=0.3)
    waited = time.monotonic() - began
    conn.close()
    holder.join(5)
    assert 0.25 <= waited < 1.2, f"waited {waited:.2f} s for a bound of 0.3 s"
