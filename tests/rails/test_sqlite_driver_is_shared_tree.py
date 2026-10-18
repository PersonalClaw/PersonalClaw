"""The source-tree checks split from
``tests/test_sqlite_driver_is_shared.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
import source_index
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


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
    tree = source_index.tree(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("sqlite_compat"):
            if any(a.name == "sqlite3" for a in node.names):
                return True
    return False


def _declares_its_own_driver(path: pathlib.Path) -> bool:
    """Whether the module names `pysqlite3` in an import of its own."""
    tree = source_index.tree(path)
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
    for path in source_index.package_files():
        if path.name == "sqlite_compat.py":
            continue
        if _declares_its_own_driver(path):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, (
        "these modules make their own SQLite driver decision: "
        f"{offenders}. Import `sqlite3` from personalclaw.sqlite_compat instead."
    )
