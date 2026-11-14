"""The source-tree check split from
``tests/test_a_lesson_is_written_without_a_category.py``.

It runs once per CI run, in the rails job. The module named above says what it holds to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

import personalclaw

PACKAGE = Path(personalclaw.__file__).parent


def test_no_writer_passes_a_category():
    """Every ``write_lesson`` call in the package names the rule and nothing in its place."""
    calls = 0
    passing = []
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            if name != "write_lesson":
                continue
            calls += 1
            if len(node.args) > 1 or any(k.arg == "category" for k in node.keywords):
                passing.append(f"{path.relative_to(PACKAGE).as_posix()}:{node.lineno}")
    assert calls, "the walk reads no write_lesson call"
    assert passing == []
