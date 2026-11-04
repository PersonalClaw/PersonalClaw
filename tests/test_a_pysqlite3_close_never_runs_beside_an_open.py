"""Where the driver is ``pysqlite3``, no database closes while another is being opened.

``pysqlite3`` closes a database holding the interpreter lock. The browser harness's gateway (on
``pysqlite3`` then, see ``test_the_driver_is_the_stdlibs_wherever_it_serves_the_stores``)
stopped answering with one thread inside the driver's ``connect`` of the workflow run store and
another inside its ``close`` of the same database, every other thread waiting for the interpreter,
and the process asleep. The driver is now the standard library's wherever it has what the stores
use. Where it lacks something and ``pysqlite3`` is taken, every open through ``sqlite_compat``'s
driver and every close holds one lock, so the two never run side by side: a close the program
makes, a store's shared connection's, and the close of a connection nobody closed, which the driver
makes when the connection is let go. Waiting for that lock releases the interpreter lock, so a
close waiting on an open holds nothing the open needs.

Not tested: the wedge itself. Which SQLite lock the close waits on is not known (the gateway's
dumps hold its Python frames only), and it needs ``pysqlite3``'s own build on Linux x86_64, where
this suite no longer runs it. So the test holds what prevents it: an open held inside the driver
keeps every close out of the driver until it returns.

The test runs in an interpreter of its own, on a stand-in ``pysqlite3`` (the standard library's
driver under that name) whose ``connect`` waits, while opening ``slow.db``, until the test lets it
go, and which records each close it runs and whether an open was inside its ``connect`` then. The
standard library's SQLite refuses an FTS5 table while the driver is chosen, so the stand-in is.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from fresh_interpreter import measure

_STAND_IN = """\
import sqlite3 as _std
import threading

from sqlite3 import *  # noqa: F403 - every name the driver has, as pysqlite3's own
from sqlite3 import Error, sqlite_version  # noqa: F401

OPENING = threading.Event()  # set while an open of slow.db is inside connect
GO = threading.Event()  # lets that open go on
CLOSES = []  # each close the driver ran: whether an open was inside connect then


class Connection(_std.Connection):
    def close(self):
        CLOSES.append(OPENING.is_set())
        super().close()


def connect(database, *args, factory=Connection, **kwargs):
    slow = str(database).endswith("slow.db")
    if slow:
        OPENING.set()
        GO.wait(10)
    try:
        return _std.connect(database, *args, factory=factory, **kwargs)
    finally:
        if slow:
            OPENING.clear()
"""

_OPENS_AND_CLOSES = """
import sqlite3 as std
import sys
import threading
import time

sys.path.insert(0, VALUES["stand_in"])
real_connect = std.connect


class NoFts5(std.Connection):
    def execute(self, sql, *args):
        if "fts5" in sql:
            raise std.OperationalError("no fts5 in this build (the test's)")
        return super().execute(sql, *args)


std.connect = lambda *a, **k: real_connect(*a, **{"factory": NoFts5, **k})
from personalclaw import sqlite_compat

std.connect = real_connect
import pysqlite3 as driver

assert sqlite_compat.driver_name() == "pysqlite3", sqlite_compat.driver_name()
folder = VALUES["folder"]
closed = sqlite_compat.connect(f"{folder}/a.db")
shared = sqlite_compat.connect_shared(f"{folder}/b.db")
dropped = [sqlite_compat.connect(f"{folder}/c.db")]  # its one reference: let go in a thread

opener = threading.Thread(target=lambda: sqlite_compat.connect(f"{folder}/slow.db").close())
opener.start()
assert driver.OPENING.wait(5), "the open never reached the driver"
closers = [
    threading.Thread(target=closed.close),
    threading.Thread(target=shared.close),
    threading.Thread(target=dropped.pop),
]
for closer in closers:
    closer.start()
time.sleep(0.5)
beside = list(driver.CLOSES)
driver.GO.set()
for thread in (opener, *closers):
    thread.join(10)
print(json.dumps({"beside": beside, "closes": driver.CLOSES}))
"""


def test_no_close_runs_in_the_driver_while_an_open_is_inside_it(tmp_path: Path):
    """🔴 Red before: the program's close and the shared connection's ran at once, beside the
    open, and the dropped connection's close went straight to the driver's own."""
    stand_in = tmp_path / "stand-in" / "pysqlite3"
    stand_in.mkdir(parents=True)
    (stand_in / "__init__.py").write_text(textwrap.dedent(_STAND_IN), encoding="utf-8")
    folder = tmp_path / "databases"
    folder.mkdir()

    seen = measure(_OPENS_AND_CLOSES, tmp_path, stand_in=str(stand_in.parent), folder=str(folder))

    assert seen["beside"] == [], "a close ran in the driver while an open was inside it"
    # Each of the three, and the opener's own close of what it opened, all after the open.
    assert seen["closes"] == [False] * 4, seen["closes"]


_STDLIB_UNWRAPPED = """
import sqlite3 as std
import sys

sys.modules["pysqlite3"] = None  # the stdlib's driver on any host: no wheel found, if one is there
from personalclaw import sqlite_compat

conn = sqlite_compat.connect(VALUES["database"])
try:
    print(json.dumps({
        "driver": sqlite_compat.driver_name(),
        "module": sqlite_compat.sqlite3 is std,
        "connection": type(conn) is std.Connection,
    }))
finally:
    conn.close()
"""


def test_the_stdlibs_driver_is_its_own_with_no_lock_around_it(tmp_path: Path):
    """Where the driver is the standard library's, nothing wraps it: its module, and the class of
    the connections it opens. ``pysqlite3`` is kept from the child, so the driver is the standard
    library's whatever the host has installed and whatever its SQLite lacks."""
    seen = measure(_STDLIB_UNWRAPPED, tmp_path, database=str(tmp_path / "a.db"))

    assert seen == {"driver": "sqlite3", "module": True, "connection": True}
