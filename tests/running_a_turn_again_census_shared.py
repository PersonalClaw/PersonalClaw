"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_running_a_turn_again_census.py``

Its checks:
``tests/rails/test_running_a_turn_again_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function in *tree* by its qualified name (``Class.method.inner``)."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = [*scope, child.name]
                if not isinstance(child, ast.ClassDef):
                    found[".".join(name)] = child
                visit(child, name)
            else:
                visit(child, scope)

    visit(tree, [])
    return found


def _called(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _send_again_calls(func: ast.AST) -> list[tuple[int, bool]]:
    """Each ``_send_again()`` call in *func*, and whether it sits behind the check that the turn
    made no call: a branch of an if-chain after one that tests ``_steps_made()``, or one that
    only an empty turn (``UNANSWERED_BLANK``, a turn that ran nothing) reaches."""

    def mentions(test: ast.expr, name: str) -> bool:
        return any(
            (isinstance(n, ast.Name) and n.id == name)
            or (isinstance(n, ast.Attribute) and n.attr == name)
            for n in ast.walk(test)
        )

    found: list[tuple[int, bool]] = []

    def visit(node: ast.AST, earlier: list[ast.expr], own: list[ast.expr]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, ast.If):
            visit(node.test, earlier, own)
            for child in node.body:
                visit(child, earlier, [*own, node.test])
            for child in node.orelse:
                visit(child, [*earlier, node.test], own)
            return
        if _called(node) == "_send_again":
            guarded = any(mentions(t, "_steps_made") for t in earlier) or any(
                mentions(t, "UNANSWERED_BLANK") for t in own
            )
            found.append((node.lineno, guarded))
        for child in ast.iter_child_nodes(node):
            visit(child, earlier, own)

    for child in ast.iter_child_nodes(func):
        visit(child, [], [])
    return found
