"""The source-tree checks split from
``tests/test_update_checks_follow_the_setting.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
import update_checks_follow_the_setting_shared

import personalclaw

_fresh_update_state = update_checks_follow_the_setting_shared._fresh_update_state


class _Readers(ast.NodeVisitor):
    """``(function, line)`` for every read of a ``check_enabled`` attribute, by its function."""

    def __init__(self) -> None:
        self.found: list[tuple[str, int]] = []
        self._where: list[str] = ["<module>"]

    def _enter(self, node: ast.AST, name: str) -> None:
        self._where.append(name)
        self.generic_visit(node)
        self._where.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "check_enabled":
            self.found.append((self._where[-1], node.lineno))
        self.generic_visit(node)


def test_one_function_decides_whether_an_update_check_may_reach_out() -> None:
    """Three places read the setting each for itself, and the one that forgot it (a checkout's
    commits-behind count) fetched with checks off. Now one function answers, read when it is
    asked; the only other read is the value the Updates screen shows its switch with."""
    root = Path(personalclaw.__file__).parent
    readers: set[tuple[str, str]] = set()
    for path in source_index.package_files():
        rel = path.relative_to(root.resolve()).as_posix()
        text = source_index.text(path)
        if rel.startswith("config/") or "check_enabled" not in text:
            continue
        visitor = _Readers()
        visitor.visit(source_index.tree(path))
        readers |= {(rel, where) for where, _line in visitor.found}
    assert readers, "the scan found no reader at all: it is not reading the tree"
    assert readers == {
        ("self_update.py", "may_check_for_updates"),
        ("dashboard/handlers/updates.py", "update_status"),
    }, sorted(readers)
