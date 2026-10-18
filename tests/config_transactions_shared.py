"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_config_transactions.py``

Its checks:
``tests/rails/test_config_transactions_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _calls(node: ast.AST | None, name: str) -> bool:
    """``name()`` or ``module.name()`` with no arguments — the home's own resolver.

    Zero arguments is the discriminator: ``ProviderSettings.config_path(extension)`` is a
    different file.
    """
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    func = node.func
    return (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")) == name


class _Scope:
    """The names one function (or the module body) binds to the home and to its config file."""

    def __init__(self, tree: ast.AST) -> None:
        self.homes: set[str] = set()
        self.configs: set[str] = set()
        # Twice, so a name bound from an earlier name (`home = config_dir()` then
        # `path = home / "config.json"`) is seen whatever order the walk visits them in.
        for _ in range(2):
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                bound = {t.id for t in targets if isinstance(t, ast.Name)}
                if _calls(node.value, "config_dir"):
                    self.homes |= bound
                elif self.is_config(node.value):
                    self.configs |= bound

    def is_home(self, node: ast.AST | None) -> bool:
        return _calls(node, "config_dir") or (isinstance(node, ast.Name) and node.id in self.homes)

    def is_config(self, node: ast.AST | None) -> bool:
        # `config_path()`, a name bound to it, or `<the home> / "config.json"`
        if _calls(node, "config_path") or (isinstance(node, ast.Name) and node.id in self.configs):
            return True
        return (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and self.is_home(node.left)
            and isinstance(node.right, ast.Constant)
            and node.right.value == "config.json"
        )


def _raw_config_writes(source: str | ast.Module) -> list[int]:
    """Lines that write the config file's path without going through the transaction.

    Scoped per function, so ``path = config_path()`` in one function does not make an
    unrelated ``atomic_write(path, …)`` in another an offender; a nested function sees the
    names of the function it is written in. A path that arrives as a parameter is out of a
    static scan's reach — the boot-time secret move is one, and it has its own behavioural test.
    """
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    functions = [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    module_level = ast.Module(
        body=[
            n
            for n in tree.body
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ],
        type_ignores=[],
    )
    offenders: set[int] = set()
    for tree_scope in [module_level, *functions]:
        scope = _Scope(tree_scope)
        for node in ast.walk(tree_scope):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"write_text", "write_bytes"}:
                if scope.is_config(func.value):
                    offenders.add(node.lineno)
                continue
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in {"atomic_write", "atomic_write_bytes", "atomic_json_write"}:
                if any(scope.is_config(arg) for arg in node.args):
                    offenders.add(node.lineno)
    return sorted(offenders)
