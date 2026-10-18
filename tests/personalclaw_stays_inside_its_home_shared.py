"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_personalclaw_stays_inside_its_home.py``

Its checks:
``tests/rails/test_personalclaw_stays_inside_its_home_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _is_home_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value[:1] == "~"


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _names_the_real_home(node: ast.AST) -> bool:
    """``Path.home()``, ``expanduser("~…")``, ``Path("~…")`` or a read of ``$HOME``."""
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        first = node.args[0] if node.args else None
        if name in ("Path.home", "pathlib.Path.home"):
            return True
        if name in ("os.path.expanduser", "expanduser", "Path", "pathlib.Path") and first:
            return _is_home_literal(first)
        if name in ("os.getenv", "getenv", "os.environ.get", "environ.get") and first:
            return isinstance(first, ast.Constant) and first.value == "HOME"
    if isinstance(node, ast.Subscript) and _dotted(node.value) in ("os.environ", "environ"):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "HOME"
    return False


def _sites(tree: ast.AST) -> set[str]:
    """The enclosing function (``<module>`` at top level) of every site in *tree*."""
    found: set[str] = set()

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
            if _names_the_real_home(child):
                found.add(scope)
            visit(child, inner)

    visit(tree, "<module>")
    return found
