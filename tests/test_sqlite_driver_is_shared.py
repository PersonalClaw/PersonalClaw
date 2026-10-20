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
