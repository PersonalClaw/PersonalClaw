"""The source-tree checks split from
``tests/test_guardrails_ladder.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import guardrails_ladder_shared
import source_index

from personalclaw.guardrails import autonomy as au

_clean_registries = guardrails_ladder_shared._clean_registries
_isolated_home = guardrails_ladder_shared._isolated_home


def test_nothing_but_the_api_handler_grants_a_rung():
    """The upward path has exactly ONE call site, asserted from the source.

    A grant is the only thing in this subsystem that increases what runs unattended. Pinning
    the call site is what keeps "promotion is always a click" true as the tree grows — a
    background job that called `grant_rung` would satisfy every other test in this file.
    """
    import ast

    root = Path(au.__file__).resolve().parent.parent
    callers: set[str] = set()
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            if name == "grant_rung":
                callers.add(str(path.relative_to(root)))
    assert callers == {"dashboard/handlers/autonomy.py"}, callers
