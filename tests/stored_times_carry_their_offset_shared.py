"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_stored_times_carry_their_offset.py``

Its checks:
``tests/rails/test_stored_times_carry_their_offset_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


def _is_zone_less_clock(node: ast.AST) -> bool:
    """``datetime.now()`` or ``datetime.fromtimestamp(x)``: this machine's wall clock, no zone."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.keywords:
        return False
    if node.func.attr == "now":
        return not node.args
    if node.func.attr == "fromtimestamp":
        return len(node.args) == 1
    return False


def _receivers(node: ast.AST) -> list[ast.AST]:
    """The values an expression can evaluate to: both sides of an ``or``, both arms of an ``if``."""
    if isinstance(node, ast.BoolOp):
        return [v for value in node.values for v in _receivers(value)]
    if isinstance(node, ast.IfExp):
        return _receivers(node.body) + _receivers(node.orelse)
    return [node]


def zone_less_writers(tree: ast.AST) -> list[int]:
    """Lines that turn a zone-less clock reading into text: ``….isoformat()`` on one, or a
    ``strftime`` that spells a date-time with no zone."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "isoformat" and any(
            _is_zone_less_clock(r) for r in _receivers(node.func.value)
        ):
            lines.append(node.lineno)
        elif (
            node.func.attr == "strftime"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("%Y-%m-%dT%H:%M")
            and not node.args[0].value.endswith(("Z", "%z"))
        ):
            lines.append(node.lineno)
    return lines
