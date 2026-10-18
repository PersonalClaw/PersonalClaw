"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_store_writer_uses_the_shared_writer.py``

Its checks:
``tests/rails/test_every_store_writer_uses_the_shared_writer_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

IN_PLACE = "writes a JSON document in place"


OWN_RENAME = "renames its own write into place"


_WRITES = ("write_text", "write_bytes")


_TEMP_FACTORIES = ("mkstemp", "NamedTemporaryFile")


_RENAMES = ("replace", "rename")


def _name(node: ast.AST) -> str:
    """A name or a dotted attribute chain (``path``, ``self._path``); ``""`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _name(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _is_dumps(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps"
        and _name(node.func.value) == "json"
    )


def _mode(call: ast.Call) -> str:
    mode: object = ""
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return mode if isinstance(mode, str) else ""


def _own(fn: ast.AST) -> list[ast.AST]:
    """*fn*'s nodes, without those of a function nested in it, which are that function's."""
    out: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(node)
        todo.extend(ast.iter_child_nodes(node))
    return out


def _shapes(fn: ast.AST) -> set[str]:
    """Which of the two shapes *fn* takes."""
    nodes = _own(fn)
    dumped: set[str] = set()
    written: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets = {_name(t) for t in node.targets} - {""}
            if any(_is_dumps(sub) for sub in ast.walk(node.value)):
                dumped |= targets
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr in _TEMP_FACTORIES
            ):
                for target in node.targets:
                    written |= {_name(sub) for sub in ast.walk(target)} - {""}
        if isinstance(node, ast.withitem):
            call = node.context_expr
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                if call.func.attr in _TEMP_FACTORIES and node.optional_vars is not None:
                    written.add(_name(node.optional_vars))
    shapes: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _WRITES:
            target = _name(func.value)
            if target:
                written.add(target)
            if node.args and any(
                _is_dumps(sub) or (isinstance(sub, ast.Name) and sub.id in dumped)
                for sub in ast.walk(node.args[0])
            ):
                shapes.add(IN_PLACE)
        if isinstance(func, ast.Attribute) and func.attr == "dump" and _name(func.value) == "json":
            shapes.add(IN_PLACE)
        if isinstance(func, ast.Name) and func.id == "open" and set(_mode(node)) & set("wx"):
            if node.args and _name(node.args[0]):
                written.add(_name(node.args[0]))
    for node in nodes:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _RENAMES:
            continue
        if _name(node.func.value) == "os" and node.args:
            source = _name(node.args[0])
        elif len(node.args) == 1 and not node.keywords:
            source = _name(node.func.value)  # `tmp.replace(path)`; `str.replace` takes two
        else:
            continue
        if source and source in written:
            shapes.add(OWN_RENAME)
    return shapes


def findings(tree: ast.AST) -> dict[str, set[str]]:
    """``function name → the shapes it takes`` for every function in *tree* that takes one."""
    out: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            shapes = _shapes(fn)
            if shapes:
                out.setdefault(fn.name, set()).update(shapes)
    return out
