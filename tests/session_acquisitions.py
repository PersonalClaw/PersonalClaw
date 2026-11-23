"""What ``test_session_acquisition_census.py`` shares with the suite's other tests.

``REACQUIRES, _census, _functions, acquires, parents``.

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


def _named(func: ast.AST) -> str:
    """The bare name *func* calls: ``f`` and ``mod.f`` alike; ``""`` for anything else."""
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def parents(tree: ast.AST) -> dict[int, ast.AST]:
    """Every node's parent in *tree*, by the child's ``id``."""
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


#: Where an acquisition outside every function sits (:func:`acquisitions`).
MODULE = "<module>"


def acquires(node: ast.AST, parent: ast.AST | None = None) -> bool:
    """Whether *node* (whose parent is *parent*) acquires a session, once for each way it can:

    - a call of ``….get_or_create(…)``;
    - one that binds it for later (``functools.partial(….get_or_create, …)``), whose call acquires
      with what the bind holds;
    - ``getattr(…, "get_or_create")``, called at once or kept;
    - any other read of a ``….get_or_create`` attribute: an alias (``acquire =
      sessions.get_or_create``), a bind under another name (``partial as bind``), the bound method
      handed on as a callback. Its call acquires with keywords nothing here reads, so it counts as
      one that names no axis;
    - the method named by a string anywhere else: ``operator.attrgetter("get_or_create")``,
      ``operator.methodcaller("get_or_create", …)``, ``….__getattribute__("get_or_create")``, a
      name a later ``getattr`` reads. Read as one that names no axis too. A bare string statement
      (a docstring) names nothing.

    An acquisition counts where it is made or bound, once, however often it is called."""
    if isinstance(node, ast.Constant):
        if node.value != "get_or_create" or isinstance(parent, ast.Expr):
            return False
        # getattr's own name, which the call counts above.
        return not (
            isinstance(parent, ast.Call)
            and _named(parent.func) == "getattr"
            and len(parent.args) > 1
            and parent.args[1] is node
        )
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get_or_create":
            return True
        first = node.args[0] if node.args else None
        if (
            _named(func) == "partial"
            and isinstance(first, ast.Attribute)
            and first.attr == "get_or_create"
        ):
            return True
        second = node.args[1] if len(node.args) > 1 else None
        return (
            _named(func) == "getattr"
            and isinstance(second, ast.Constant)
            and second.value == "get_or_create"
        )
    if not (
        isinstance(node, ast.Attribute)
        and node.attr == "get_or_create"
        and isinstance(node.ctx, ast.Load)
    ):
        return False
    # The read a call above already counts: the call's own function, or a partial's first argument.
    if isinstance(parent, ast.Call):
        if parent.func is node:
            return False
        if _named(parent.func) == "partial" and parent.args and parent.args[0] is node:
            return False
    return True


def acquisitions(source: str | ast.Module) -> list[str]:
    """The qualified name of the function around each acquisition in *source* (text, or its
    tree): each ``….get_or_create(…)`` call and each bind of it (:func:`acquires`), once each;
    :data:`MODULE` for one outside every function."""
    tree = _parsed(source)
    up = parents(tree)
    out: list[str] = []
    for qualname, func in _functions(tree).items():
        inner = {
            id(n)
            for f in ast.walk(func)
            if f is not func and isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.walk(f)
        }
        for node in ast.walk(func):
            if acquires(node, up.get(id(node))) and id(node) not in inner:
                out.append(qualname)
    # Code outside every function runs once, at import: what it acquires or names is its module's.
    in_one = {id(n) for f in _functions(tree).values() for n in ast.walk(f)}
    for node in ast.walk(tree):
        if id(node) not in in_one and acquires(node, up.get(id(node))):
            out.append(MODULE)
    return out


def _census() -> list[tuple[str, str]]:
    sites: list[tuple[str, str]] = []
    for path, tree in source_index.package_trees():
        rel = path.relative_to(SRC).as_posix()
        sites.extend((rel, q) for q in acquisitions(tree))
    return sites
