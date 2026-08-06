"""PLATFORM-REACH PR-1 — one SQLite binding + capability probe.

Covers the probe against the REAL resolved driver (whatever CI ships) and the
capability logic against faked drivers, so the doctor line + the six consumers
that now import ``sqlite3`` from here all share one honest answer.
"""

from __future__ import annotations

import sqlite3 as _stdlib_sqlite

from personalclaw import sqlite_compat


def test_all_consumers_share_this_binding():
    """The six former per-module imports now resolve to this module's driver, so a
    test that patches SQLite has ONE bind point instead of seven."""
    from personalclaw import memory, portability, snapshot, vector_memory
    from personalclaw.knowledge import retrieval
    from personalclaw.loop import store

    for mod in (snapshot, memory, portability, vector_memory, retrieval, store):
        assert mod.sqlite3 is sqlite_compat.sqlite3, f"{mod.__name__} bound a different sqlite3"


def test_probe_reports_the_real_driver_and_version():
    cap = sqlite_compat.probe()
    assert cap.driver in ("pysqlite3", "sqlite3")
    assert cap.driver == sqlite_compat.driver_name()
    # A real version string like "3.45.1" (never the "unknown" fallback on a healthy env).
    assert cap.version and cap.version[0].isdigit()


def test_probe_detects_fts5_and_json1_on_the_real_driver():
    """The stdlib build on CI has both; assert the probe agrees with a direct check
    rather than hard-coding True (so it stays honest on a stripped build)."""
    cap = sqlite_compat.probe()
    conn = sqlite_compat.sqlite3.connect(":memory:")
    try:
        try:
            conn.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
            real_fts5 = True
        except sqlite_compat.sqlite3.Error:
            real_fts5 = False
        try:
            conn.execute("SELECT json_extract('{\"a\":1}', '$.a')")
            real_json1 = True
        except sqlite_compat.sqlite3.Error:
            real_json1 = False
    finally:
        conn.close()
    assert cap.fts5 is real_fts5
    assert cap.json1 is real_json1


def test_probe_is_memoized():
    """One process-lifetime probe (lru_cache) — the same object every call."""
    assert sqlite_compat.probe() is sqlite_compat.probe()


class _FakeError(Exception):
    pass


class _FakeConn:
    """An in-memory connection stub whose execute() succeeds/fails per a rule."""

    def __init__(self, *, fts5: bool, json1: bool):
        self._fts5, self._json1 = fts5, json1

    def execute(self, sql: str):
        if "fts5" in sql and not self._fts5:
            raise _FakeError("no fts5")
        if "json_extract" in sql and not self._json1:
            raise _FakeError("no json1")
        return None

    def close(self):
        pass


def _fake_driver(monkeypatch, *, version, fts5, json1, name="fake"):
    """Swap the module's bound ``sqlite3`` for a fake with a chosen capability set."""
    fake = type("_FakeSqlite", (), {})()
    fake.sqlite_version = version
    fake.Error = _FakeError
    fake.connect = lambda *a, **k: _FakeConn(fts5=fts5, json1=json1)
    monkeypatch.setattr(sqlite_compat, "sqlite3", fake)
    monkeypatch.setattr(sqlite_compat, "_DRIVER", name)
    sqlite_compat.probe.cache_clear()


def test_faked_full_driver_reports_all_capabilities(monkeypatch):
    _fake_driver(monkeypatch, version="3.99.0", fts5=True, json1=True, name="pysqlite3")
    cap = sqlite_compat.probe()
    assert cap == sqlite_compat.SqliteCapabilities(
        driver="pysqlite3", version="3.99.0", fts5=True, json1=True
    )
    sqlite_compat.probe.cache_clear()


def test_faked_stripped_driver_reports_missing_extensions(monkeypatch):
    _fake_driver(monkeypatch, version="3.20.0", fts5=False, json1=False, name="sqlite3")
    cap = sqlite_compat.probe()
    assert cap.driver == "sqlite3" and cap.version == "3.20.0"
    assert cap.fts5 is False and cap.json1 is False
    sqlite_compat.probe.cache_clear()


def test_probe_never_raises_on_a_broken_connect(monkeypatch):
    """A connect() that blows up degrades to all-absent, never propagates."""
    fake = type("_FakeSqlite", (), {})()
    fake.sqlite_version = "3.0.0"
    fake.Error = _FakeError

    def _boom(*a, **k):
        raise _FakeError("cannot open")

    fake.connect = _boom
    monkeypatch.setattr(sqlite_compat, "sqlite3", fake)
    sqlite_compat.probe.cache_clear()
    cap = sqlite_compat.probe()
    assert cap.fts5 is False and cap.json1 is False
    sqlite_compat.probe.cache_clear()


def test_resolved_driver_is_a_real_sqlite_module():
    """Whatever resolved, it behaves like the DB-API sqlite3 (connect + Error)."""
    assert hasattr(sqlite_compat.sqlite3, "connect")
    assert issubclass(sqlite_compat.sqlite3.Error, Exception)
    # If the stdlib resolved, it IS the stdlib module (sanity on the fallback path).
    if sqlite_compat.driver_name() == "sqlite3":
        assert sqlite_compat.sqlite3 is _stdlib_sqlite
