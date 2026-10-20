"""``tests/run_facts.py`` imports nothing from ``tools`` (#3948).

``tests/conftest.py`` imports run_facts, and a test that runs pytest on a tree of its own loads
that conftest with only ``tests/`` on its path. An import of ``tools`` there fails every such
test before it starts, as it did on main once run_facts read the collector's property names
from ``tools.ci.telemetry``.
"""

from __future__ import annotations

import ast

from rails_repo import REPO


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
    return roots


def test_run_facts_imports_nothing_from_tools() -> None:
    tree = ast.parse((REPO / "tests" / "run_facts.py").read_text(encoding="utf-8"))
    assert "tools" not in _import_roots(tree)


def test_the_check_sees_an_import_of_tools() -> None:
    """The positive control: the same check finds both spellings of the import."""
    assert "tools" in _import_roots(ast.parse("from tools.ci import telemetry\n"))
    assert "tools" in _import_roots(ast.parse("import tools.ci.telemetry\n"))
