"""What ``test_session_acquisition_census.py`` shares with the suite's other tests.

``REACQUIRES, _census, _functions``.

Kept here, beside the suite, so a test that is not a rail can import them without importing
a rail (rails live in ``tests/rails/``, which only the rails job collects)."""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


#: The manager's own re-acquisition: it rebuilds a stale runtime for the request just made.
REACQUIRES = frozenset({("session.py", "SessionManager.get_or_create")})


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function in *tree* by its qualified name (``Class.method.inner``)."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = [*scope, child.name]
                if not isinstance(child, ast.ClassDef):
                    found[".".join(name)] = child
                visit(child, name)
            else:
                visit(child, scope)

    visit(tree, [])
    return found


def _parsed(source: str | ast.Module) -> ast.Module:
    return source if isinstance(source, ast.Module) else ast.parse(source)


def acquisitions(source: str | ast.Module) -> list[str]:
    """The qualified name of the function around each ``….get_or_create(…)`` in *source* (text,
    or its tree), once per call."""
    tree = _parsed(source)
    out: list[str] = []
    for qualname, func in _functions(tree).items():
        inner = {
            id(n)
            for f in ast.walk(func)
            if f is not func and isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.walk(f)
        }
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_or_create"
                and id(node) not in inner
            ):
                out.append(qualname)
    return out


def _census() -> list[tuple[str, str]]:
    sites: list[tuple[str, str]] = []
    for path, tree in source_index.package_trees():
        rel = path.relative_to(SRC).as_posix()
        sites.extend((rel, q) for q in acquisitions(tree))
    return sites
