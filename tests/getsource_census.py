"""No new ``inspect.getsource`` call lands in a test outside ``tests/rails``.

A test that reads the product's source and asserts a literal in it passes on a comment, a dead
branch or a renamed symbol, and fails on a harmless refactor: it pins the text, not what the
code does. Such a test becomes a behaviour test, or an AST rail in ``tests/rails`` with a stated
reason (``docs/maintainers/testing.md``). The calls still outside ``tests/rails`` are listed, one
line each, in :data:`BASELINE`, a site baseline (``tests/site_baseline.py``) that only shrinks:
a converted call's line is deleted, and a call the list does not hold fails the CI contract row
that reads this module (``tests/rails/test_ci_contract.py``).

The rule, over every ``.py`` file under ``tests/`` outside ``tests/rails``:

- a **call** whose callee is named ``getsource``: ``inspect.getsource(x)``, ``getsource(x)``
  after ``from inspect import getsource``, or through any alias of the module;
- a **reference** to ``getsource`` that is not called where it stands (``read =
  inspect.getsource``), since the call it makes later is under another name;
- an **import** of the name (``from inspect import getsource as read``), for the same reason.

The word in a comment or a string is not a site. Each site is named ``path::scope::<source>``
(``site_baseline.code_site``), repeats in one function as ``#2``, ``#3``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from site_baseline import code_site, numbered, scoped

#: The list of the calls still allowed, from the repository's root.
BASELINE = "tests/rails/baselines/getsource.sites.jsonl"

#: Where a ``getsource`` call is allowed: the AST rails, each with its reason in its docstring.
ALLOWED = "tests/rails/"

_NAME = "getsource"


def _is_reference(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == _NAME) or (
        isinstance(node, ast.Name) and node.id == _NAME
    )


def module_sites(rel: str, source: str) -> list[str]:
    """The sites in one module's *source*, named for *rel*, in source order."""
    if _NAME not in source:
        return []
    nodes = list(scoped(ast.parse(source, filename=rel)))
    called = {id(node.func) for node, _ in nodes if isinstance(node, ast.Call)}
    names: list[str] = []
    for node, scope in nodes:
        if isinstance(node, ast.Call) and _is_reference(node.func):
            names.append(code_site(rel, scope, node))
        elif _is_reference(node) and id(node) not in called:
            names.append(code_site(rel, scope, node))
        elif isinstance(node, ast.ImportFrom) and any(a.name == _NAME for a in node.names):
            names.append(code_site(rel, scope, node))
    return numbered(names)


def sites(root: Path) -> list[str]:
    """Every site under *root*'s ``tests/`` outside ``tests/rails``, by path."""
    found: list[str] = []
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(ALLOWED) or "__pycache__" in path.parts:
            continue
        found += module_sites(rel, path.read_text(encoding="utf-8"))
    return found
