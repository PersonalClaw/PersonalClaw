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


def _nonzero(node: ast.AST | None) -> bool:
    """Whether the status *node* gives can be a failing one: a non-zero int, or a conditional
    with one as either branch (``1 if left else 0``)."""
    if isinstance(node, ast.IfExp):
        return _nonzero(node.body) or _nonzero(node.orelse)
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value != 0


def _nonzero_exit(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Attribute) and func.attr == "exit":
            if isinstance(func.value, ast.Name) and func.value.id == "sys" and stmt.value.args:
                return _nonzero(stmt.value.args[0])
    if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
        if getattr(stmt.exc.func, "id", "") == "SystemExit" and stmt.exc.args:
            return _nonzero(stmt.exc.args[0])
    if isinstance(stmt, ast.Return):
        return _nonzero(stmt.value)
    return False


def _stderr_print(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "id", "") == "print"
        and any(k.arg == "file" for k in stmt.value.keywords)
    )


def _runs_into_a_failure(stmt: ast.stmt) -> bool:
    """An ``if`` whose body runs, through prints on stderr alone, into a non-zero exit."""
    if not isinstance(stmt, ast.If):
        return False
    k = 0
    while k < len(stmt.body) and _stderr_print(stmt.body[k]):
        k += 1
    return k < len(stmt.body) and _nonzero_exit(stmt.body[k])


def _ends(block: list[ast.stmt]) -> bool:
    """Whether *block* never falls through to what follows it: it ends in a return, a raise or
    an exit."""
    last = block[-1] if block else None
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    return (
        isinstance(last, ast.Expr)
        and isinstance(last.value, ast.Call)
        and isinstance(last.value.func, ast.Attribute)
        and last.value.func.attr == "exit"
    )


def _stdout_prints_in(stmt: ast.stmt) -> list[ast.stmt]:
    """The stdout prints an ``if``/``else`` or a ``for`` holds, nested ones included: of an
    ``if``, those of each branch that falls through to the statement after it."""
    if isinstance(stmt, ast.If):
        reach = [b for b in (stmt.body, stmt.orelse) if b and not _ends(b)]
        return [p for block in reach for each in block for p in _stdout_prints_in_node(each)]
    return _stdout_prints_in_node(stmt, skip=stmt)


def _stdout_prints_in_node(stmt: ast.stmt, skip: ast.stmt | None = None) -> list[ast.stmt]:
    found: list[ast.stmt] = []
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.stmt) and node is not skip and _stdout_print(node):
            found.append(node)
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.append(child)
    return found


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
                    fails = _runs_into_a_failure(stmt)
                    if not (fails or _nonzero_exit(stmt)):
                        continue
                    j = i - 1
                    while j >= 0:
                        before = block[j]
                        if _stdout_print(before):
                            printed = [before]
                        elif fails and isinstance(before, (ast.If, ast.For)):
                            # A report or plan printed by an if/else or a loop, then the refusal.
                            printed = _stdout_prints_in(before)
                        else:
                            printed = []
                        if not printed:
                            break
                        for each in printed:
                            sites.add(_Site(module, fn.name, each.lineno, ast.unparse(each)))
                        j -= 1
    return sites
