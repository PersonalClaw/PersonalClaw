"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_requests_work_is_the_caller_its_sign_in_proves.py``

Its checks:
``tests/rails/test_a_requests_work_is_the_caller_its_sign_in_proves_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _header_reads(source: str | ast.Module) -> list[int]:
    """The lines of *source* that read ``X-Session-Key`` off a request's headers: a
    ``….headers.get("X-Session-Key"…)`` call, a ``….headers["X-Session-Key"]`` load, or a
    ``"X-Session-Key" in ….headers`` test. Sending the header (a client building its headers)
    is not a read."""
    lines: list[int] = []
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                node.func.attr == "get"
                and isinstance(owner, ast.Attribute)
                and owner.attr == "headers"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).lower() == "x-session-key"
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "headers"
                and isinstance(node.slice, ast.Constant)
                and str(node.slice.value).lower() == "x-session-key"
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant):
            if str(node.left.value).lower() == "x-session-key" and any(
                isinstance(c, ast.Attribute) and c.attr == "headers" for c in node.comparators
            ):
                lines.append(node.lineno)
    return lines
