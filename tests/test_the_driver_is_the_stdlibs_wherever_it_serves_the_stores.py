"""The SQLite driver is the standard library's wherever it has what the stores use.

🔴 The defect. ``sqlite_compat`` took ``pysqlite3`` wherever it was installed, and on Linux x86_64
it always is (``pyproject.toml`` installs ``pysqlite3-binary`` there): CI's Linux suites, the
gateway's Docker image on amd64, the desktop's Linux build. ``pysqlite3`` closes a database holding
the interpreter lock (its ``Connection.close`` calls ``sqlite3_close_v2`` with no
``Py_BEGIN_ALLOW_THREADS``). The browser harness's gateway stopped answering mid-run: its event
loop's thread was inside the driver's ``connect`` of the workflow run store, a worker thread was
inside the driver's ``close`` of the same database, and the stall watch's own thread could not get
the interpreter back from a one-second sleep for twenty seconds. The process slept from then on,
with every request accepted and none answered.

The standard library's driver releases the interpreter lock around both. What the stores use is a
SQLite of at least ``sqlite_compat._MIN_SQLITE`` (3.38.0, for the vector index's extension) with
FTS5 and JSON1 in it, and the loading of extensions; every Python the product ships or is tested on
has all of it (python-build-standalone's, which uv installs, and Debian's, which the Docker image's
Python links). So the driver is now the standard library's wherever it has all of it, asked once at
import, and ``pysqlite3`` is not imported there at all. Only where the standard library's lacks
something is ``pysqlite3`` taken, when it is installed
(``test_a_pysqlite3_close_never_runs_beside_an_open`` holds what it is made to do there).

Each choice runs in an interpreter of its own, since the choice is made once, at import. A
stand-in ``pysqlite3`` (the standard library's driver under that name) is put first on the path,
and while the choice is made the standard library's driver is made to lack one thing: its SQLite
refuses an FTS5 table or a JSON1 call, reports an older version, or its connections cannot load an
extension.
"""

from __future__ import annotations

import sqlite3 as _stdlib
import textwrap
from pathlib import Path

import pytest
from fresh_interpreter import measure

from personalclaw import sqlite_compat

#: Ubuntu 22.04's SQLite, which has FTS5 and JSON1 and is older than the stores need.
_OLDER = (3, 37, 2)

_CHOOSES = """
import sqlite3 as std
import sys

if VALUES["stand_in"]:
    sys.path.insert(0, VALUES["stand_in"])
else:  # none installed: not the stand-in, nor the wheel itself where it is (Linux x86_64)
    sys.modules["pysqlite3"] = None
lacks = VALUES["lacks"]
real = {"connect": std.connect, "version": std.sqlite_version_info, "class": std.Connection}
if lacks in ("fts5", "json_extract"):

    class Refusing(std.Connection):
        def execute(self, sql, *args):
            if lacks in sql:
                raise std.OperationalError(f"no {lacks} in this build (the test's)")
            return super().execute(sql, *args)

    std.connect = lambda *a, **k: real["connect"](*a, **{"factory": Refusing, **k})
elif lacks == "version":
    std.sqlite_version_info = tuple(VALUES["older"])
elif lacks == "extensions":
    std.Connection = type("Connection", (), {})  # a class with no enable_load_extension
from personalclaw import sqlite_compat

std.connect = real["connect"]
std.sqlite_version_info = real["version"]
std.Connection = real["class"]
print(json.dumps({
    "driver": sqlite_compat.driver_name(),
    "stdlib": sqlite_compat.sqlite3 is std,
    "imported": sys.modules.get("pysqlite3") is not None,
}))
"""

_STDLIB = {"driver": "sqlite3", "stdlib": True, "imported": False}
_STAND_IN = {"driver": "pysqlite3", "stdlib": False, "imported": True}


def _stand_in(tmp_path: Path) -> str:
    """A folder holding a ``pysqlite3`` that is the standard library's driver under that name."""
    package = tmp_path / "stand-in" / "pysqlite3"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        textwrap.dedent("""\
            from sqlite3 import *  # noqa: F403 - every name the driver has, as pysqlite3's own
            from sqlite3 import Connection, Error, connect, sqlite_version  # noqa: F401
        """),
        encoding="utf-8",
    )
    return str(package.parent)


def _choose(tmp_path: Path, *, stand_in: bool, lacks: str = "") -> dict:
    return measure(
        _CHOOSES,
        tmp_path,
        stand_in=_stand_in(tmp_path) if stand_in else "",
        lacks=lacks,
        older=list(_OLDER),
    )


def test_the_stdlibs_driver_is_taken_where_it_has_what_the_stores_use(tmp_path: Path):
    """🔴 Red before: ``pysqlite3`` was taken wherever it was installed. It needs a host whose
    standard library driver has all of it, as every Python CI runs does."""
    if not _stdlib_serves_the_stores():
        pytest.skip("this host's standard library driver lacks something the stores use")
    assert _choose(tmp_path, stand_in=True) == _STDLIB


@pytest.mark.parametrize("lacks", ["fts5", "json_extract", "version", "extensions"])
def test_pysqlite3_is_taken_where_the_stdlibs_driver_lacks_something(tmp_path: Path, lacks):
    assert _choose(tmp_path, stand_in=True, lacks=lacks) == _STAND_IN


def test_the_older_sqlite_the_test_reports_is_older_than_the_stores_need():
    assert _OLDER < sqlite_compat._MIN_SQLITE


def test_the_stdlibs_driver_is_kept_where_it_lacks_something_and_no_pysqlite3_is_installed(
    tmp_path: Path,
):
    assert _choose(tmp_path, stand_in=False, lacks="fts5") == _STDLIB


def _stdlib_serves_the_stores() -> bool:
    if _stdlib.sqlite_version_info < sqlite_compat._MIN_SQLITE:
        return False
    if not hasattr(_stdlib.Connection, "enable_load_extension"):
        return False
    conn = _stdlib.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe_fts USING fts5(x)")
        conn.execute("SELECT json_extract('{\"a\":1}', '$.a')")
        return True
    except _stdlib.Error:
        return False
    finally:
        conn.close()


def test_this_interpreter_runs_the_stdlibs_driver_when_it_has_what_the_stores_use():
    """🔴 Red before on Linux x86_64, where the suite's interpreter (python-build-standalone's) has
    all of it and ``pysqlite3-binary`` is installed beside it. A build that lacks something may run
    either driver, which the tests above hold."""
    if _stdlib_serves_the_stores():
        assert sqlite_compat.driver_name() == "sqlite3"
        assert sqlite_compat.sqlite3 is _stdlib
    else:
        assert sqlite_compat.driver_name() in {"sqlite3", "pysqlite3"}
