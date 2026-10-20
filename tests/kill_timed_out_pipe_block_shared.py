"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_kill_timed_out_pipe_block.py``

Its checks:
``tests/rails/test_kill_timed_out_pipe_block_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _parsed(source: str | ast.Module) -> ast.Module:
    return source if isinstance(source, ast.Module) else ast.parse(source)


def _spawns_by_target(source: str | ast.Module, callee: str) -> dict[str, set[str]]:
    """Map assignment-target name -> set of keyword names, for each *callee* spawn."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(_parsed(source)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        fn = value.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != callee:
            continue
        found[target.id] = {kw.arg for kw in value.keywords if kw.arg}
    return found


def _timeout_kill_style(source: str | ast.Module) -> dict[str, str]:
    """Map killed-variable name -> ``"pid"`` or ``"group"``, per timeout handler.

    Keyed by NAME, not line, so ordinary edits above a handler don't churn the rail.
    """
    style: dict[str, str] = {}
    for node in ast.walk(_parsed(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if "TimeoutError" not in (ast.dump(node.type) if node.type else ""):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            fn = inner.func
            if isinstance(fn, ast.Attribute) and fn.attr == "kill":
                if isinstance(fn.value, ast.Name):
                    style[fn.value.id] = "pid"
            elif isinstance(fn, ast.Name) and fn.id == "kill_timed_out":
                if inner.args and isinstance(inner.args[0], ast.Name):
                    style[inner.args[0].id] = "group"
    return style


def _spawn_functions(source: str | ast.Module) -> dict[str, dict[str, object]]:
    """Map function name -> {kill: "group"|"pid"|None, own_session: bool} per spawn site."""
    out: dict[str, dict[str, object]] = {}
    for fn in ast.walk(_parsed(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dumped = ast.dump(fn)
        if "create_subprocess" not in dumped:
            continue
        kill: str | None = None
        for node in ast.walk(fn):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if "TimeoutError" not in (ast.dump(node.type) if node.type else ""):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                f = inner.func
                if isinstance(f, ast.Attribute) and f.attr in {"kill", "terminate"}:
                    kill = "pid"
                elif isinstance(f, ast.Name) and f.id in {"kill_timed_out", "terminate_and_reap"}:
                    kill = "group"
        out[fn.name] = {"kill": kill, "own_session": "start_new_session" in dumped}
    return out


_ASYNC_SPAWNS = {
    "create_subprocess_exec",
    "create_subprocess_shell",
    "create_subprocess_limited",
}


def _iter_functions(tree: ast.AST):
    """Yield ``(qualname, node)`` for every function in *tree*."""
    out: list[tuple[str, ast.AST]] = []

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _fn(self, n):
            self.stack.append(n.name)
            out.append((".".join(self.stack), n))
            self.generic_visit(n)
            self.stack.pop()

        visit_FunctionDef = _fn
        visit_AsyncFunctionDef = _fn

        def visit_ClassDef(self, n):
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

    V().visit(tree)
    return out


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _async_spawn_vars(fn: ast.AST) -> set[str]:
    """Names assigned from an async spawn in *fn*; ``<unassigned>`` for a bare call."""
    named: set[str] = set()
    assigned: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            v = node.value
            if isinstance(v, ast.Await):
                v = v.value
            if (
                isinstance(v, ast.Call)
                and _callee(v) in _ASYNC_SPAWNS
                and isinstance(node.targets[0], ast.Name)
            ):
                named.add(node.targets[0].id)
                assigned.add(id(v))
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _callee(node) in _ASYNC_SPAWNS:
            if id(node) not in assigned:
                named.add("<unassigned>")
    return named


def _timeout_handlers(fn: ast.AST) -> list[ast.ExceptHandler]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.ExceptHandler)
        and n.type is not None
        and "TimeoutError" in ast.dump(n.type)
    ]


def _reaped_by_owner(fn: ast.AST) -> set[str]:
    """Variables handed to the OWNER inside a ``TimeoutError`` handler of *fn*."""
    reaped: set[str] = set()
    for handler in _timeout_handlers(fn):
        for inner in ast.walk(handler):
            if not isinstance(inner, ast.Call):
                continue
            if _callee(inner) not in {"kill_timed_out", "terminate_and_reap"}:
                continue
            if inner.args and isinstance(inner.args[0], ast.Name):
                reaped.add(inner.args[0].id)
    return reaped
