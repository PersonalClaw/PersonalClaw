"""The source-tree checks split from
``tests/test_harness_learning_verdict.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

from harness_learning_verdict_shared import MODULE


def test_the_sibling_defines_no_threshold_of_its_own():
    """The structural half: an AST census, so a future edit that re-derives a threshold reds here
    rather than passing because the number happened to match."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    numeric_consts = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float))
        if not isinstance(node.value.value, bool)
    }
    assert (
        numeric_consts == {}
    ), f"the sibling minted its own numeric threshold(s): {numeric_consts}"
