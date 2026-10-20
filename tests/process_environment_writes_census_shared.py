"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_process_environment_writes_census.py``

Its checks:
``tests/rails/test_process_environment_writes_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

_MUTATORS = {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def writes(tree: ast.Module) -> list[tuple[str, int, list[str | None]]]:
    """``(qualname, line, names)`` for every write to the process environment in *tree*; a name
    is ``None`` where the code computes it."""
    found: list[tuple[str, int, list[str | None]]] = []

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.q: list[str] = []

        def _scope(self, n: ast.AST) -> None:
            self.q.append(n.name)  # type: ignore[attr-defined]
            self.generic_visit(n)
            self.q.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

        def _add(self, n: ast.stmt | ast.expr, names: list[str | None]) -> None:
            found.append((".".join(self.q) or "<module>", n.lineno, names))

        def _targets(self, n: ast.AST, targets: list[ast.AST]) -> None:
            for t in targets:
                if isinstance(t, ast.Subscript) and _is_environ(t.value):
                    self._add(n, [_literal(t.slice)])

        def visit_Assign(self, n: ast.Assign) -> None:
            self._targets(n, n.targets)
            self.generic_visit(n)

        def visit_AugAssign(self, n: ast.AugAssign) -> None:
            self._targets(n, [n.target])
            if _is_environ(n.target):
                self._add(n, [None])
            self.generic_visit(n)

        def visit_Delete(self, n: ast.Delete) -> None:
            self._targets(n, n.targets)
            self.generic_visit(n)

        def visit_Call(self, n: ast.Call) -> None:
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in _MUTATORS and _is_environ(f.value):
                if f.attr == "update":
                    arg = n.args[0] if n.args else None
                    names: list[str | None] = (
                        [_literal(k) for k in arg.keys] if isinstance(arg, ast.Dict) else [None]
                    )
                    names += [k.arg for k in n.keywords]
                else:
                    names = [_literal(n.args[0]) if n.args else None]
                self._add(n, names)
            elif (
                isinstance(f, ast.Attribute)
                and f.attr in {"putenv", "unsetenv"}
                and isinstance(f.value, ast.Name)
                and f.value.id == "os"
            ):
                self._add(n, [_literal(n.args[0]) if n.args else None])
            self.generic_visit(n)

    V().visit(tree)
    return found
