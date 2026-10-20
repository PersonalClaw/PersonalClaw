"""The source-tree checks split from
``tests/test_a_database_is_private_from_its_first_byte.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import a_database_is_private_from_its_first_byte_shared
import source_index
from rails_repo import REPO

_the_usual_umask = a_database_is_private_from_its_first_byte_shared._the_usual_umask


_SRC = REPO / "src" / "personalclaw"


#: Where the choice of driver is made, and where the private opener lives.
_OPENER = "sqlite_compat.py"


#: The durability tooling. Each opens a database a store keeps — to checkpoint, vacuum, copy or
#: merge it — or writes the copy it takes into a private temporary folder, and none of them makes a
#: store's database.
_TOOLING = frozenset(
    {"snapshot.py", "portability.py", "durability/shards.py", "durability/footprint.py"}
)


def _driver_names(tree: ast.AST) -> set[str]:
    """The names a module binds the driver to (``from …sqlite_compat import sqlite3``)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "personalclaw.sqlite_compat":
            names |= {a.asname or a.name for a in node.names if a.name == "sqlite3"}
    return names


def _file_opens(path: Path) -> list[int]:
    """The lines that open a database FILE with the driver's own ``connect``: not ``:memory:``,
    and not a URI (``uri=True``, which every read-only open passes)."""
    tree = source_index.tree(path)
    driver = _driver_names(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in driver
        ):
            continue
        if any(kw.arg == "uri" for kw in node.keywords):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and first.value == ":memory:":
            continue
        lines.append(node.lineno)
    return sorted(lines)


def test_a_store_opens_its_database_through_the_private_opener():
    """🔴 Red on integration: the staging log, the memory index, session search, the loop store and
    the workflow store opened theirs with the driver's own ``connect``."""
    found = {}
    for path in source_index.package_files():
        rel = path.relative_to(_SRC).as_posix()
        if rel == _OPENER or rel in _TOOLING:
            continue
        lines = _file_opens(path)
        if lines:
            found[rel] = lines
    assert found == {}, (
        "open a store's database with personalclaw.sqlite_compat.connect (or connect_shared), "
        f"which makes it private before SQLite first opens it: {found}"
    )


def test_the_rail_sees_a_file_open():
    """The positive control: the tooling it exempts does open files with the driver's own
    ``connect``, so a scan that finds none of them is blind."""
    assert all(_file_opens(_SRC / rel) for rel in _TOOLING)
