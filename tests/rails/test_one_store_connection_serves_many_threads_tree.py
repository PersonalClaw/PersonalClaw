"""The source-tree checks split from
``tests/test_one_store_connection_serves_many_threads.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

_SRC = REPO / "src"


#: Modules that open a connection for several threads and serialize EVERY use of it with a lock of
#: their own, around whole transactions. Nothing else may open one without ``connect_shared``.
_OWN_LOCK = {
    "personalclaw/session_search.py",
    "personalclaw/learning/staging.py",
}


def _raw_shared_opens(source: str | ast.Module) -> int:
    """How many ``connect(..., check_same_thread=False)`` calls ``source`` makes."""
    count = 0
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "connect":
            continue
        for kw in node.keywords:
            if (
                kw.arg == "check_same_thread"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
            ):
                count += 1
    return count


def test_a_connection_shared_between_threads_is_opened_shared():
    """The rail: a store that keeps one connection for several threads opens it with
    ``connect_shared`` (or serializes every use itself, and is listed above)."""
    # Positive control on the predicate: it must still see the raw form it exists to refuse.
    assert _raw_shared_opens("sqlite3.connect(p, check_same_thread=False)") == 1
    assert _raw_shared_opens("connect_shared(p, isolation_level=None)") == 0

    found: dict[str, int] = {}
    for path, tree in source_index.package_trees():
        rel = path.relative_to(_SRC).as_posix()
        if rel == "personalclaw/sqlite_compat.py":
            continue  # where the shared connection is made
        n = _raw_shared_opens(tree)
        if n:
            found[rel] = n
    # And on the population: the two lock-holding modules are found, so the scan is not vacuous.
    assert _OWN_LOCK <= set(found), f"the scan no longer finds the lock-holding modules: {found}"
    stray = sorted(set(found) - _OWN_LOCK)
    assert (
        stray == []
    ), f"these open a connection for several threads without connect_shared: {stray}"
