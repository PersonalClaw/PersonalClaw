"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_personalclaw_git_is_neutral.py``

Its checks:
``tests/rails/test_personalclaw_git_is_neutral_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _tail(call: ast.Call) -> str:
    from spawn_ceiling_audit_shared import _callee

    return _callee(call).split(".")[-1]


def _argv_kind(node: ast.AST, bound: dict[str, str]) -> str:
    """``neutral`` for an argv ``git_argv`` built, ``raw`` for one that starts with a literal
    ``"git"``, ``""`` for anything else. *bound* maps the names a function set to one of those."""
    if isinstance(node, ast.Starred):
        return _argv_kind(node.value, bound)
    if isinstance(node, ast.Call) and _tail(node) == "spawn_shim_argv" and node.args:
        return _argv_kind(node.args[0], bound)
    if isinstance(node, ast.Call) and _tail(node) == "git_argv":
        return "neutral"
    if isinstance(node, ast.Name):
        return bound.get(node.id, "")
    if isinstance(node, ast.BinOp):  # ["git", ...] + args
        return _argv_kind(node.left, bound)
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _argv_kind(node.elts[0], bound)
    if isinstance(node, ast.Constant) and node.value == "git":
        return "raw"
    return ""


def _bound_argvs(fn: ast.AST) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            kind = _argv_kind(node.value, {})
            for target in node.targets:
                if kind and isinstance(target, ast.Name):
                    out[target.id] = kind
    return out
