"""The source-tree checks split from
``tests/test_computer_use_approval_ladder.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import computer_use_approval_ladder_shared

from personalclaw.computer_use import enable_state

_isolated = computer_use_approval_ladder_shared._isolated


def test_the_unattended_field_has_exactly_one_reader(_isolated):
    """``EnableState.unattended`` may be read only inside ``unattended_tools()``, for the reason
    ``apps`` has the same rail: a second place that reaches into it is a second place that can
    default or widen it differently, and the divergence is invisible until it matters.

    Walks the AST rather than grepping — this module's prose names the field several times, and a
    text scanner reads comments as code.
    """
    tree = ast.parse(Path(enable_state.__file__).read_text(encoding="utf-8"))
    readers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr == "unattended":
                readers.append(node.name)
    assert sorted(set(readers)) == ["unattended_tools"], readers
