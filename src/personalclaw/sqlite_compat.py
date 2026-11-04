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

The stores need a SQLite of at least :data:`_MIN_SQLITE` with FTS5 and JSON1 in it, and a
driver that loads extensions (the vector index's). The driver is the stdlib's wherever it has all
of that, asked once, at import, of an in-memory connection; every Python the product ships or is
tested on does (python-build-standalone's, which uv installs for CI and the desktop build, and
Debian's, which the Docker image's Python links). There ``pysqlite3`` is not imported at all.
Only where the stdlib's lacks one of them is ``pysqlite3`` (the ``pysqlite3-binary`` wheel, which
bundles a newer SQLite with all of it) taken, when it is installed; the probe below reports what
resolved so the doctor can show it.

``pysqlite3`` closes a database holding the interpreter lock: its ``Connection.close`` calls
``sqlite3_close_v2`` without releasing it, where the stdlib's releases it around both the open and
the close. The browser harness's gateway, on ``pysqlite3`` then (it was preferred wherever it was
installed, which on Linux x86_64 is always), stopped answering for good with its event loop's
thread inside the driver's ``connect`` of the workflow run store and a worker thread inside its
``close`` of the same database, every other thread waiting for the interpreter. So where
``pysqlite3`` is the driver, every open through it holds :data:`_OPEN_CLOSE`, and so does every
close of a connection it made: one the program asks for, a shared connection's, and the one the
driver makes when a connection nobody closed is let go. The two never run side by side
(``tests/test_a_pysqlite3_close_never_runs_beside_an_open.py``).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from functools import lru_cache
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable

from personalclaw.atomic_write import make_private_database

if TYPE_CHECKING:
    # What a type checker reads, as before the stdlib's became the driver: the names unchecked
    # (the fallback's wheel ships no types, and either driver's are the DB-API's).
    import pysqlite3 as sqlite3  # type: ignore[import-not-found]
else:
    import sqlite3

#: The oldest SQLite the stores run on: 3.38.0, the first with ``sqlite3_vtab_in``, which the
#: vector index's extension (sqlite-vec's ``vec0``, built against a newer SQLite) calls as it plans
#: a query. The newest of what the stores' own SQL uses is ``ALTER TABLE … DROP COLUMN`` (3.35.0),
#: which the knowledge store's migration does without where it is missing, then the upsert
#: (``ON CONFLICT … DO UPDATE``, 3.24.0).
_MIN_SQLITE = (3, 38, 0)

#: What a SQLite build is asked to answer whether it has FTS5 and JSON1, on an in-memory
#: connection, so nothing is written anywhere.
_FTS5_PROBE = "CREATE VIRTUAL TABLE _probe_fts USING fts5(x)"
_JSON1_PROBE = "SELECT json_extract('{\"a\":1}', '$.a')"

#: Held around every open and every close while the driver is ``pysqlite3`` (the module
#: docstring says why). The stdlib's driver needs none, and never takes it.
_OPEN_CLOSE = threading.RLock()


def _runs(conn: Any, sql: str, error: type[BaseException]) -> bool:
    try:
        conn.execute(sql)
        return True
    except error:
        return False


def _serves_the_stores(driver: Any) -> bool:
    """Whether *driver* has what the stores use: a SQLite of at least :data:`_MIN_SQLITE`, with
    FTS5 and JSON1 in it (asked of an in-memory connection), and the loading of extensions. Never
    raises: a build that cannot open a connection has none of it."""
    if tuple(getattr(driver, "sqlite_version_info", ()))[:3] < _MIN_SQLITE:
        return False
    if not hasattr(driver.Connection, "enable_load_extension"):
        return False
    try:
        conn = driver.connect(":memory:")
    except Exception:  # noqa: BLE001
        return False
    try:
        return _runs(conn, _FTS5_PROBE, driver.Error) and _runs(conn, _JSON1_PROBE, driver.Error)
    finally:
        conn.close()


@lru_cache(maxsize=None)
def _closing_under_the_lock(factory: type[Any]) -> type[Any]:
    """*factory*, a ``pysqlite3`` connection class, as a class whose connections close holding
    :data:`_OPEN_CLOSE`: on ``close``, and when one nobody closed is let go, which the driver
    would otherwise close itself, holding the interpreter lock and no other."""

    def close(self: Any) -> None:
        # A shared connection's own lock first, the order its statements take the two in when
        # one of them opens a database.
        with getattr(self, "_serial", nullcontext()), _OPEN_CLOSE:
            factory.close(self)

    def __del__(self: Any) -> None:
        try:
            close(self)
        except Exception:  # noqa: BLE001 — another thread's connection: the driver closes it
            pass

    return type(factory.__name__, (factory,), {"close": close, "__del__": __del__})


def _opens_and_closes_one_at_a_time(driver: ModuleType) -> ModuleType:
    """*driver* (``pysqlite3``) as a module of its own names whose ``connect`` holds
    :data:`_OPEN_CLOSE` while the driver opens, and makes connections that close holding it,
    whichever factory the caller names."""
    module = ModuleType(driver.__name__, driver.__doc__)
    module.__dict__.update({k: v for k, v in vars(driver).items() if not k.startswith("__")})
    driver_connect = driver.connect

    def connect(database: Any, *args: Any, **kwargs: Any) -> Any:
        kwargs["factory"] = _closing_under_the_lock(kwargs.get("factory", driver.Connection))
        with _OPEN_CLOSE:
            return driver_connect(database, *args, **kwargs)

    module.__dict__["connect"] = connect
    return module


_DRIVER = "sqlite3"
if not TYPE_CHECKING and not _serves_the_stores(sqlite3):
    try:
        import pysqlite3 as _pysqlite3
    except ImportError:  # no wheel: the stdlib's, without what it lacks (``probe`` says what)
        pass
    else:
        sqlite3 = _opens_and_closes_one_at_a_time(_pysqlite3)
        _DRIVER = "pysqlite3"

__all__ = [
    "sqlite3",
    "SqliteCapabilities",
    "probe",
    "driver_name",
    "FTS5_REMEDY",
    "SharedConnection",
    "connect",
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
            fts5 = _has_module(conn, _FTS5_PROBE)
            json1 = _has_module(conn, _JSON1_PROBE)
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
            self.connection._check(sql)
            return super().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> "_SharedCursor":
        with self.connection._serial:
            self.connection._check(sql)
            return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script: str, /) -> "_SharedCursor":
        with self.connection._serial:
            self.connection._check(sql_script, script=True)
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

    ``statement_check``, when a store sets one, runs before every statement, with ``script=True``
    for a script; it refuses a statement by raising. A store whose rows another session may be
    shown (memory, knowledge, vocabulary) sets :func:`personalclaw.memory_writes.check_statement`.

    The lock is taken per call, so a change made of several statements is one transaction only
    under :meth:`transaction`, which holds it from the first statement to the last.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._serial = threading.RLock()
        self.statement_check: Callable[..., None] | None = None

    def _check(self, sql: str, *, script: bool = False) -> None:
        check = self.statement_check
        if check is not None:
            check(sql, script=script)

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

    @contextmanager
    def held(self) -> Iterator[None]:
        """Hold the connection's lock across several calls, so no other thread's statement runs
        between them: a check and the write it decides, made together in one worker thread.
        Unlike :meth:`transaction` it opens no transaction, so what runs inside may commit."""
        with self._serial:
            yield

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the enclosed statements as one transaction: all of them are stored, or, when the
        enclosed work raises, none is.

        The connection's lock is held throughout, so no other thread's statement lands inside it:
        between two of its statements another thread's commit would store half of it, and its
        rollback would undo the half already run. It begins ``IMMEDIATE``, taking the database's
        write lock before anything in it is read. When another thread's statements are already
        open on the connection (run, and not yet committed), it runs as a savepoint inside them,
        stored when they are. Nothing inside may commit or roll back itself.
        """
        with self._serial:
            if self.in_transaction:
                self.execute("SAVEPOINT shared_transaction")
                try:
                    yield
                except BaseException:
                    self.execute("ROLLBACK TO shared_transaction")
                    self.execute("RELEASE shared_transaction")
                    raise
                self.execute("RELEASE shared_transaction")
                return
            self.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.rollback()
                raise
            self.commit()

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


def connect(database: str, **kwargs: Any) -> "sqlite3.Connection":
    """Open the database a store keeps: the driver's ``connect``, with the database's folder made
    and, under the PersonalClaw home, the database private before SQLite first opens it
    (``atomic_write.make_private_database``). Every store opens its database through this or
    :func:`connect_shared` (``tests/test_a_database_is_private_from_its_first_byte.py``)."""
    make_private_database(database)
    return sqlite3.connect(database, **kwargs)


def connect_shared(database: str, **kwargs: Any) -> SharedConnection:
    """Open ``database`` as a :class:`SharedConnection`: the way a store opens the connection it
    keeps and shares between threads (``check_same_thread=False`` is implied), private as
    :func:`connect` makes it."""
    kwargs["check_same_thread"] = False
    make_private_database(database)
    return sqlite3.connect(database, factory=SharedConnection, **kwargs)
