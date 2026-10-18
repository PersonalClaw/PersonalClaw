"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_wait_for_background_work_ends_when_the_work_has.py``

Its checks:
``tests/rails/test_a_wait_for_background_work_ends_when_the_work_has_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _gathers(call: ast.Call) -> bool:
    func = call.func
    return (isinstance(func, ast.Attribute) and func.attr == "gather") or (
        isinstance(func, ast.Name) and func.id == "gather"
    )


def _waits_for_a_set_to_empty(source: str | ast.Module) -> list[int]:
    """The lines of each ``while S:`` loop whose body gathers ``*…S…``: a wait for S to empty."""
    found = []
    if isinstance(source, ast.Module):
        tree = source
    else:
        try:
            tree = ast.parse(source)
        except SyntaxError:  # a fixture that is not code runs no loop
            return []
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.While):
            continue
        held = ast.dump(loop.test)
        for call in ast.walk(loop):
            if (
                isinstance(call, ast.Call)
                and _gathers(call)
                and any(
                    isinstance(arg, ast.Starred)
                    and any(ast.dump(part) == held for part in ast.walk(arg.value))
                    for arg in call.args
                )
            ):
                found.append(loop.lineno)
                break
    return found
