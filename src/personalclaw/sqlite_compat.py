"""One SQLite binding + capability probe for the whole codebase.

Six modules each carried their own ``try: import pysqlite3 as sqlite3 / except
ImportError: import sqlite3`` (and one carried a bare ``import sqlite3``), so the
driver choice was decided seven times and a test that patched the stdlib module
missed the modules that had bound ``pysqlite3``. This module makes that choice
ONCE: import :data:`sqlite3` from here and every caller shares the same driver.

Every module that opens a database does, and none imports the stdlib's
(``tests/test_every_database_opens_through_one_sqlite.py`` holds that). Where
``pysqlite3`` is installed it is a second copy of SQLite in the process, and two
copies on one database corrupt it: each keeps track of the process's locks on the
file for its own connections only, so a connection one copy closes takes itself
for the last one, checkpoints the WAL and deletes it under the other copy's live
connection. The store's next commit then fails, or goes where no new connection
reads it.

``pysqlite3`` (the ``pysqlite3-binary`` wheel) ships a newer SQLite than some
platforms' bundled stdlib build — notably one WITH FTS5 + JSON1 — which the
knowledge/memory search paths need. Preferring it when present, falling back to
the stdlib otherwise, is the platform-portability the plan is about; the probe
below reports what actually resolved so the doctor can show it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

try:  # the newer bundled build (FTS5 + JSON1) when the wheel is installed
    import pysqlite3 as sqlite3  # type: ignore[import-not-found]

    _DRIVER = "pysqlite3"
except ImportError:  # the platform's stdlib build
    import sqlite3  # type: ignore[no-redef]

    _DRIVER = "sqlite3"

__all__ = [
    "sqlite3",
    "SqliteCapabilities",
    "probe",
    "driver_name",
    "FTS5_REMEDY",
    "SharedConnection",
    "connect_shared",
]

# The one actionable remedy every FTS5 guard shows when the active SQLite build has
# no FTS5 compiled in. Names the concrete fix — the
# ``pysqlite3-binary`` wheel bundles a SQLite with FTS5 — so a user (or the doctor)
# sees what to install rather than a bare "FTS5 missing". Reused by every guard.
FTS5_REMEDY = (
    "This SQLite build has no FTS5 (full-text search) module compiled in. "
    "Install the 'pysqlite3-binary' wheel (pip install pysqlite3-binary), which "
    "bundles a SQLite built with FTS5, or run PersonalClaw against a SQLite build "
    "that has FTS5 enabled."
)


def driver_name() -> str:
    """Which driver resolved — ``"pysqlite3"`` or ``"sqlite3"`` (the stdlib)."""
    return _DRIVER


@dataclass(frozen=True)
class SqliteCapabilities:
    """What the resolved SQLite driver can do (probed once, memoized)."""

    driver: str  # "pysqlite3" | "sqlite3"
    version: str  # the SQLite library version, e.g. "3.45.1"
    fts5: bool  # full-text search 5 compiled in (knowledge/memory search need it)
    json1: bool  # the JSON1 extension (json_extract etc.)


def _has_module(conn: "sqlite3.Connection", create_sql: str) -> bool:
    """True if ``create_sql`` (a CREATE VIRTUAL TABLE / json() probe) runs.

    Runs against an in-memory connection and rolls nothing back — the temp DB is
    discarded with the connection, so the probe has no side effects.
    """
    try:
        conn.execute(create_sql)
        return True
    except sqlite3.Error:
        return False


@lru_cache(maxsize=1)
def probe() -> SqliteCapabilities:
    """Probe the resolved driver's version + FTS5 + JSON1 (memoized per process).

    Never raises: a probe failure reports the capability as absent rather than
    breaking a caller that only wanted the driver/version.
    """
    version = getattr(sqlite3, "sqlite_version", "unknown")
    fts5 = json1 = False
    try:
        conn = sqlite3.connect(":memory:")
        try:
            fts5 = _has_module(conn, "CREATE VIRTUAL TABLE _probe_fts USING fts5(x)")
            json1 = _has_module(conn, "SELECT json_extract('{\"a\":1}', '$.a')")
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return SqliteCapabilities(driver=_DRIVER, version=version, fts5=fts5, json1=json1)


# ── One connection, several threads ─────────────────────────────────────────────────────────


class _SharedCursor(sqlite3.Cursor):
    """A cursor of a :class:`SharedConnection`: every call into the driver holds the connection's
    lock. Stepping happens on ``execute`` AND on every fetch, so both are held, and so is
    iteration (``for row in cursor``)."""

    def execute(self, sql: str, parameters: Any = (), /) -> "_SharedCursor":
        with self.connection._serial:
            return super().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> "_SharedCursor":
        with self.connection._serial:
            return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script: str, /) -> "_SharedCursor":
        with self.connection._serial:
            return super().executescript(sql_script)

    def fetchone(self) -> Any:
        with self.connection._serial:
            return super().fetchone()

    def fetchmany(self, size: int | None = None) -> list[Any]:
        with self.connection._serial:
            return super().fetchmany(self.arraysize if size is None else size)

    def fetchall(self) -> list[Any]:
        with self.connection._serial:
            return super().fetchall()

    def __next__(self) -> Any:
        with self.connection._serial:
            return super().__next__()

    def close(self) -> None:
        with self.connection._serial:
            super().close()


class SharedConnection(sqlite3.Connection):
    """A connection one store keeps for the life of the process and several threads use.

    The stores hold ONE connection each and are reached from the event loop, from every request a
    page makes (each in an executor thread), from the agent's tools and from background workers.
    ``check_same_thread=False`` only switches the driver's ownership check off; the driver is not
    safe for two threads inside it on one connection at once. Measured on the memory store: a page
    reload that reads its entities, their graph and its summary side by side answered 500 with
    ``IndexError: tuple index out of range`` from a row whose columns were not there, a
    ``COUNT(*)`` that returned no row at all, and ``InterfaceError: bad parameter or other API
    misuse`` — several hundred times in five seconds of four threads reading.

    So every call into the driver on this connection — a statement, a fetch, a commit, a rollback,
    a script, a close — takes this connection's lock, one at a time. That is the parallelism
    SQLite already allowed one connection (its own mutex serializes the calls); what it adds is
    that the driver's bookkeeping around them can no longer interleave. The lock is reentrant, so a
    fetch the driver makes inside its own call does not wait on itself.

    Open one with :func:`connect_shared`, or pass ``factory=SharedConnection`` to ``connect``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._serial = threading.RLock()

    def cursor(self, factory: Any = _SharedCursor) -> Any:
        return super().cursor(factory)

    # The connection's own shortcuts build a cursor in C, where the override above is not seen, so
    # each one goes through a cursor of this connection instead.
    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any, /) -> Any:
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql_script: str, /) -> Any:
        return self.cursor().executescript(sql_script)

    def commit(self) -> None:
        with self._serial:
            super().commit()

    def rollback(self) -> None:
        with self._serial:
            super().rollback()

    def close(self) -> None:
        with self._serial:
            super().close()

    def __exit__(self, *exc: Any) -> Any:
        with self._serial:
            return super().__exit__(*exc)

    # A build without loadable extensions has neither method, and ``super()`` raises the same
    # AttributeError the base connection would, which is what the callers already catch.
    def enable_load_extension(self, enabled: bool, /) -> None:
        with self._serial:
            super().enable_load_extension(enabled)

    def load_extension(self, path: str, /, **kwargs: Any) -> None:
        with self._serial:
            super().load_extension(path, **kwargs)


def connect_shared(database: str, **kwargs: Any) -> SharedConnection:
    """Open ``database`` as a :class:`SharedConnection`: the way a store opens the connection it
    keeps and shares between threads (``check_same_thread=False`` is implied)."""
    kwargs["check_same_thread"] = False
    return sqlite3.connect(database, factory=SharedConnection, **kwargs)
