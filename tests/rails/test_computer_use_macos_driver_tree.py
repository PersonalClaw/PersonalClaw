"""The source-tree checks split from ``tests/test_computer_use_macos_driver.py``.

They run once per CI run, in the rails job. The module named above says what they hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

import personalclaw.computer_use as computer_use

#: The one call that moves the owner's real pointer.
WARP = "CGWarpMouseCursorPosition"


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of the docstring constants in *tree*: prose, not code."""
    held = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, held)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _names(tree: ast.Module) -> set[str]:
    """Every name *tree*'s code holds: names, attributes, imported names and strings (a symbol
    looked up by name, ``getattr(lib, "...")``), its docstrings left out."""
    prose = _docstrings(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.update({node.name, node.asname or ""})
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in prose:
                names.add(node.value)
    return names


def test_no_other_module_in_the_package_warps_the_cursor():
    """The pointer boundary is drawn in one file, so it can be audited in one file.

    The defect this structure prevents: a second module that binds ``CGWarpMouseCursorPosition``
    moves the owner's real pointer from a place no audit of ``macos_ffi.py`` looks at, so a
    review of the one file that is meant to hold every pointer move no longer covers them all.
    Read as code (a name, an attribute, an import or a string a symbol is looked up by), with
    docstrings left out, so prose that names the call is not a binding of it.
    """
    folder = Path(computer_use.__file__).resolve().parent
    files = source_index.under(source_index.package_files(), folder, (".py",))
    binders = sorted(path.name for path in files if WARP in _names(source_index.tree(path)))
    assert binders == ["macos_ffi.py"], binders


def test_the_name_reader_sees_a_binding_by_string_and_by_attribute_but_not_in_a_docstring():
    """The vacuity proof for the rail above: a reader that saw nothing would pass it."""
    by_string = ast.parse(f'"""Calls {WARP}."""\nf = getattr(lib, "{WARP}")\n')
    by_attribute = ast.parse(f"lib.{WARP}(point)\n")
    in_prose = ast.parse(f'"""{WARP}"""\ndef f():\n    """{WARP}"""\n')
    assert WARP in _names(by_string) and WARP in _names(by_attribute)
    assert WARP not in _names(in_prose)
