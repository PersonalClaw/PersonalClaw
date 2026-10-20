"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_cli_refusal_prints_on_stderr.py``

Its checks:
``tests/rails/test_a_cli_refusal_prints_on_stderr_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class _Site:
    module: str
    function: str
    line: int
    text: str


def _nonzero_exit(stmt: ast.stmt) -> bool:
    def code(node: ast.AST | None) -> int | None:
        return node.value if isinstance(node, ast.Constant) and type(node.value) is int else None

    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Attribute) and func.attr == "exit":
            if isinstance(func.value, ast.Name) and func.value.id == "sys" and stmt.value.args:
                return code(stmt.value.args[0]) not in (None, 0)
    if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
        if getattr(stmt.exc.func, "id", "") == "SystemExit" and stmt.exc.args:
            return code(stmt.exc.args[0]) not in (None, 0)
    if isinstance(stmt, ast.Return):
        return code(stmt.value) not in (None, 0)
    return False


def _stdout_print(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "id", "") == "print"
        and not any(k.arg == "file" for k in stmt.value.keywords)
    )


def _own_nodes(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    """``fn`` and its nodes, without those of a function nested in it (walked on its own)."""
    stack: list[ast.AST] = [fn]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.append(child)


def _refusals_on_stdout(source: str | ast.Module, module: str) -> set[_Site]:
    """Each stdout print in a block that runs straight into a non-zero exit, in *source* (text,
    or its tree)."""
    sites: set[_Site] = set()
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in _own_nodes(fn):
            blocks = [getattr(node, f, None) for f in ("body", "orelse", "finalbody")]
            blocks += [h.body for h in getattr(node, "handlers", []) or []]
            for block in blocks:
                if not (isinstance(block, list) and block and isinstance(block[0], ast.stmt)):
                    continue
                for i, stmt in enumerate(block):
                    if not _nonzero_exit(stmt):
                        continue
                    j = i - 1
                    while j >= 0 and _stdout_print(block[j]):
                        sites.add(_Site(module, fn.name, block[j].lineno, ast.unparse(block[j])))
                        j -= 1
    return sites
