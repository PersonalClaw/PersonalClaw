"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_session_share.py``

Its checks:
``tests/rails/test_session_share_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _share_call_sites(sources: dict[str, str | ast.Module]) -> set[tuple[str, str]]:
    """``{(file, enclosing function)}`` for every call to ``share_session``.

    Counts both ``session_share.share_session(...)`` and a bare ``share_session(...)``, so
    an ``from ... import share_session`` cannot slip past the attribute form.
    """
    found: set[tuple[str, str]] = set()
    for label, src in sources.items():
        tree = src if isinstance(src, ast.Module) else ast.parse(src)
        parents: dict[ast.AST, str] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    parents.setdefault(node, fn.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = (
                f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            )
            if name == "share_session":
                found.add((label, parents.get(node, "<module>")))
    return found
