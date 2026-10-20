"""The source-tree checks split from ``tests/test_terminal_default_cwd.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index

_TERMINAL = source_index.REPO / "src" / "personalclaw" / "dashboard" / "handlers" / "terminal.py"


def _called(fn: ast.AST) -> set[str]:
    return {
        n.func.id if isinstance(n.func, ast.Name) else n.func.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }


def _reads_the_cwd_itself(fn: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "cwd"
        for n in ast.walk(fn)
    )


def test_the_spawn_and_the_create_response_take_the_cwd_from_the_one_resolver():
    """The WebSocket spawn (``api_terminal_ws``) and the REST create (``api_terminal_create``)
    each take the default folder from ``default_terminal_cwd``, and neither reads the configured
    ``cwd`` itself.

    Why a rail: the PTY spawn runs behind a WebSocket upgrade a unit test cannot drive together
    with the create call, and the defect is the two drifting apart. A second derivation in one of
    them makes the create response advertise a folder the terminal does not open in, which is
    what the duplicated ``cfg.get("cwd") or $HOME`` they both used to carry did."""
    tree = source_index.tree(_TERMINAL)
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert _reads_the_cwd_itself(fns["default_terminal_cwd"]), "the resolver was not read"
    for name in ("api_terminal_ws", "api_terminal_create"):
        assert "default_terminal_cwd" in _called(fns[name]), f"{name} skips the resolver"
        assert not _reads_the_cwd_itself(fns[name]), f"{name} derives the cwd itself"
