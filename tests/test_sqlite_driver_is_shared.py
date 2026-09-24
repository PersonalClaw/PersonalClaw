"""Every module that catches a SQLite exception must share ONE driver decision.

`sqlite_compat` exists because the driver choice was being made independently in
several modules, and on Linux/x86_64 the resolved driver is `pysqlite3`, whose
exception classes are DISTINCT objects from the standard library's. A module that
re-derives the driver with its own ``try: import pysqlite3`` can therefore end up
catching a class the connection never raises — and it fails only on the platform
where the two drivers differ, which is not the platform most contributors run.

The two assertions here are deliberately different in kind:

* the SOURCE assertion fails on every platform, including one with no `pysqlite3`
  installed at all, because it is about where the name comes from;
* the IDENTITY assertion is the semantic property the source assertion protects.
  On a machine without `pysqlite3` it passes either way — that is exactly the
  "same class by accident" case the original bug hid behind, and the reason the
  source assertion carries the weight.

The happy-path assertion at the end is the vacuity floor: without it, a suite that
somehow resolved no driver at all would satisfy everything above.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Modules that reference `sqlite3.<Error>` in an except clause. Each must take the
#: name from `sqlite_compat` rather than deciding for itself.
CATCHERS = (
    "memory_graph.py",
    "memory.py",
    "session_search.py",
    "knowledge/store.py",
    "knowledge/retrieval.py",
    "portability.py",
    "snapshot.py",
    "vector_memory.py",
    "loop/store.py",
)


def _imports_driver_from_compat(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("sqlite_compat"):
            if any(a.name == "sqlite3" for a in node.names):
                return True
    return False


def _declares_its_own_driver(path: pathlib.Path) -> bool:
    """Whether the module names `pysqlite3` in an import of its own."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "pysqlite3" for a in node.names):
            return True
    return False


@pytest.mark.parametrize("rel", CATCHERS)
def test_the_driver_name_comes_from_sqlite_compat(rel: str) -> None:
    path = SRC / rel
    assert path.exists(), f"{rel} moved — update this rail rather than deleting it"
    assert _imports_driver_from_compat(path), (
        f"{rel} does not import `sqlite3` from personalclaw.sqlite_compat. "
        "A module that catches a SQLite exception must use the same driver object the "
        "connection was opened with, or the except clause can silently never match."
    )


@pytest.mark.parametrize("rel", CATCHERS)
def test_no_module_re_derives_the_driver_for_itself(rel: str) -> None:
    path = SRC / rel
    assert not _declares_its_own_driver(path), (
        f"{rel} imports `pysqlite3` directly. sqlite_compat owns that decision; a second "
        "one can disagree with it, and it would disagree only on a platform with pysqlite3 "
        "installed."
    )


def test_sqlite_compat_is_the_only_place_that_names_pysqlite3() -> None:
    """The residue sweep, stated over the whole package rather than a fixed list."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "sqlite_compat.py":
            continue
        if _declares_its_own_driver(path):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, (
        "these modules make their own SQLite driver decision: "
        f"{offenders}. Import `sqlite3` from personalclaw.sqlite_compat instead."
    )


def test_the_shared_driver_is_the_same_object_every_caller_sees() -> None:
    """The semantic half. Passes trivially without pysqlite3 — see the module docstring."""
    from personalclaw import memory_graph, vector_memory
    from personalclaw.sqlite_compat import sqlite3 as compat_sqlite3

    assert memory_graph.sqlite3 is compat_sqlite3
    assert vector_memory.sqlite3 is compat_sqlite3
    assert memory_graph.sqlite3.IntegrityError is compat_sqlite3.IntegrityError


def test_a_driver_actually_resolved() -> None:
    """Vacuity floor: everything above is satisfiable by resolving nothing."""
    from personalclaw.sqlite_compat import driver_name, sqlite3

    assert driver_name() in {"sqlite3", "pysqlite3"}
    assert hasattr(sqlite3, "connect")
    assert issubclass(sqlite3.IntegrityError, Exception)
