"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_warning_prints_on_stderr.py``

Its checks:
``tests/rails/test_a_warning_prints_on_stderr_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class _Site:
    module: str
    function: str
    line: int
    text: str


#: The calls that pass their one argument on to `print`, cleaned for a terminal or made a string.
_WRAPPERS = frozenset({"terminal_text", "_shown", "str"})


def _opens(text: str) -> bool:
    return text.lstrip().startswith("⚠")


def warning_constants(tree: ast.Module) -> frozenset[str]:
    """The module-level string constants in *tree* whose text opens with ⚠, such as
    ``constants.DATA_WARNING``: a name bound to one prints a warning."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and _opens(value.value):
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return frozenset(names)


def _opens_with_a_warning(node: ast.AST | None, warned: frozenset[str] = frozenset()) -> bool:
    """Whether the text *node* prints opens, after any whitespace, with ⚠.

    It reads a string, an f-string past its whitespace-only leading text, the left side of a
    ``+``, either branch of a conditional, a one-argument wrapper (:data:`_WRAPPERS`), and a name
    in *warned* (:func:`warning_constants`) or an attribute or a method call on one.
    """
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and _opens(node.value)
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.Constant) and not str(part.value).strip():
                continue
            if isinstance(part, ast.FormattedValue):
                return _opens_with_a_warning(part.value, warned)
            return _opens_with_a_warning(part, warned)
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _opens_with_a_warning(node.left, warned)
    if isinstance(node, ast.IfExp):
        return _opens_with_a_warning(node.body, warned) or _opens_with_a_warning(
            node.orelse, warned
        )
    if isinstance(node, ast.Name):
        return node.id in warned
    if isinstance(node, ast.Attribute):
        return node.attr in warned or _opens_with_a_warning(node.value, warned)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _WRAPPERS and len(node.args) == 1:
            return _opens_with_a_warning(node.args[0], warned)
        if isinstance(func, ast.Attribute):
            return _opens_with_a_warning(func.value, warned)
    return False


def _to_stdout(call: ast.Call) -> bool:
    """Whether *call* (a ``print``) writes to stdout: no ``file``, ``file=None`` or
    ``file=sys.stdout``."""
    for keyword in call.keywords:
        if keyword.arg != "file":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and value.value is None:
            return True
        return (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "sys"
            and value.attr == "stdout"
        )
    return True


def _warnings_on_stdout(
    source: str | ast.Module, module: str, warned: frozenset[str] | None = None
) -> set[_Site]:
    """Each ``print`` in *source* (text, or its tree) whose text opens with ⚠ and that writes to
    stdout, named by the qualified name of the function it is in (``_check_pip.update``).

    *warned* is the warning constants a name may be bound to: the whole package's, or by default
    the ones *source* defines."""
    sites: set[_Site] = set()
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    if warned is None:
        warned = warning_constants(tree)
    stack: list[str] = []

    class _Walk(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "print"
                and node.args
                and _opens_with_a_warning(node.args[0], warned)
                and _to_stdout(node)
            ):
                where = ".".join(stack) or "<module>"
                sites.add(_Site(module, where, node.lineno, ast.unparse(node)))
            self.generic_visit(node)

    _Walk().visit(tree)
    return sites
