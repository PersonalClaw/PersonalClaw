"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_database_opens_through_one_sqlite.py``

Its checks:
``tests/rails/test_every_database_opens_through_one_sqlite_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: The stdlib driver, and the extension module under it.
_STDLIB_DRIVER = {"sqlite3", "_sqlite3"}


def _top(name: str | None) -> str:
    return (name or "").split(".")[0]


def _stdlib_driver_imports(tree: ast.AST) -> list[int]:
    """The lines that import the stdlib driver, however it is spelled."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_top(alias.name) in _STDLIB_DRIVER for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and _top(node.module) in _STDLIB_DRIVER:
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if (
                name in {"import_module", "__import__"}
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _top(first.value) in _STDLIB_DRIVER
            ):
                lines.append(node.lineno)
    return sorted(lines)
