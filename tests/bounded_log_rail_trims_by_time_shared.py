"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_bounded_log_rail_trims_by_time.py``

Its checks:
``tests/rails/test_bounded_log_rail_trims_by_time_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
import re

HELPER = "bounded_log"


_WRITES = re.compile(r"(write|^_?save|^_?persist)", re.IGNORECASE)


#: A DELETE that keeps the highest row ids — the SQL form of cutting a file's tail.
_PRUNE_BY_ID = re.compile(
    r"DELETE\s+FROM\s+\S+\s+WHERE\s+(?:id|rowid)\s+(?:<=|<|IN|NOT\s+IN)\s*\(?\s*SELECT\s+"
    r"(?:MAX\(\s*(?:id|rowid)\s*\)|(?:id|rowid)\s+FROM\s+\S+\s+ORDER\s+BY\s+(?:id|rowid)\b)",
    re.IGNORECASE | re.DOTALL,
)


def _negative(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return True
    return isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value < 0


def _len_minus(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and isinstance(node.left, ast.Call)
        and getattr(node.left.func, "id", "") == "len"
    )


def _position_cuts(fn: ast.AST) -> list[int]:
    """The lines where *fn* cuts the tail off a list by position."""
    cuts: list[int] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            s = node.slice
            if s.lower is not None and s.upper is None and s.step is None and _negative(s.lower):
                cuts.append(node.lineno)  # rows[-cap:]
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Slice):
                    s = target.slice
                    if s.lower is None and s.upper is not None:
                        if _negative(s.upper) or _len_minus(s.upper):
                            cuts.append(node.lineno)  # del rows[:-cap] / del rows[: len - cap]
        elif (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Call)
            and getattr(node.test.left.func, "id", "") == "len"
        ):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "pop"
                    and len(inner.args) == 1
                    and isinstance(inner.args[0], ast.Constant)
                    and inner.args[0].value == 0
                ):
                    cuts.append(node.lineno)  # while len(rows) > cap: rows.pop(0)
    return cuts


def _called(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def _uses_helper(fn: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == HELPER
        for node in ast.walk(fn)
    )


def _parsed(source: str | ast.Module) -> ast.Module:
    return source if isinstance(source, ast.Module) else ast.parse(source)


def position_trims(source: str | ast.Module) -> dict[str, list[int]]:
    """Every function in *source* (text, or its tree) that trims by position and writes, by
    name: its cut lines."""
    found: dict[str, list[int]] = {}
    for fn in ast.walk(_parsed(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        cuts = _position_cuts(fn)
        if not cuts or _uses_helper(fn):
            continue
        if any(_WRITES.search(name) for name in _called(fn)):
            found[fn.name] = cuts
    return found
