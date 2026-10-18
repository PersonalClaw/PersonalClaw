"""The source-tree check split from ``tests/test_ci_catalogue.py``.

It runs once per CI run, in the rails job.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from tools.ci import catalogue


def test_it_imports_only_the_standard_library() -> None:
    """ci.yml's shard planner runs ``python -m tools.ci`` on the runner image's own python3,
    before anything is installed, and that imports this module."""
    tree = ast.parse(Path(catalogue.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    imported.discard("__future__")
    assert imported, "the scan read no imports"
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
