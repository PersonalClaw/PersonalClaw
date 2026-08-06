"""One SQLite binding + capability probe for the whole codebase (PLATFORM-REACH PR-1).

Six modules each carried their own ``try: import pysqlite3 as sqlite3 / except
ImportError: import sqlite3`` (and one carried a bare ``import sqlite3``), so the
driver choice was decided seven times and a test that patched the stdlib module
missed the modules that had bound ``pysqlite3``. This module makes that choice
ONCE: import :data:`sqlite3` from here and every caller shares the same driver.

``pysqlite3`` (the ``pysqlite3-binary`` wheel) ships a newer SQLite than some
platforms' bundled stdlib build — notably one WITH FTS5 + JSON1 — which the
knowledge/memory search paths need. Preferring it when present, falling back to
the stdlib otherwise, is the platform-portability the plan is about; the probe
below reports what actually resolved so the doctor can show it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

try:  # the newer bundled build (FTS5 + JSON1) when the wheel is installed
    import pysqlite3 as sqlite3  # type: ignore[import-not-found]

    _DRIVER = "pysqlite3"
except ImportError:  # the platform's stdlib build
    import sqlite3  # type: ignore[no-redef]

    _DRIVER = "sqlite3"

__all__ = ["sqlite3", "SqliteCapabilities", "probe", "driver_name"]


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
