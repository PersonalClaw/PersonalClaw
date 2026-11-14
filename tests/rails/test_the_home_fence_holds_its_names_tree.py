"""The source-tree check split from ``tests/test_the_home_fence_holds_its_names.py``.

It runs once per CI run, in the rails job. The module named above says what it holds to.
"""

from __future__ import annotations

import ast

from rails_repo import REPO

from personalclaw import owner_only

#: The modules that keep the security log's entries under a home.
_KEEPERS = ("src/personalclaw/sel.py", "src/personalclaw/sel_intake.py")
#: The expressions that name a home the log's entries are joined onto.
_HOMES = ("self._dir", "Path(home)", "config_dir()", "home")


def _names(tree: ast.Module) -> dict[str, str]:
    """Each module-level name bound to a string: a literal, or a name imported from
    ``owner_only``."""
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "personalclaw.owner_only":
            for alias in node.names:
                value = getattr(owner_only, alias.name, None)
                if isinstance(value, str):
                    names[alias.asname or alias.name] = value
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if not isinstance(target, ast.Name):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                names[target.id] = value.value
            elif isinstance(value, ast.Name) and value.id in names:
                names[target.id] = names[value.id]
    return names


def _joined() -> list[tuple[str, str]]:
    """``(where, name)`` for each entry the log's modules join onto a home (``home / X``) or list
    in one (``home.glob(X)``), the name resolved to its text, or ``?`` where it cannot be."""
    found: list[tuple[str, str]] = []
    for rel in _KEEPERS:
        if not (REPO / rel).exists():
            continue
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        names = _names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                home, entry = node.left, node.right
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "glob"
                and node.args
            ):
                home, entry = node.func.value, node.args[0]
            else:
                continue
            if ast.unparse(home) not in _HOMES:
                continue
            if isinstance(entry, ast.Constant) and isinstance(entry.value, str):
                name = entry.value
            elif isinstance(entry, ast.Name):
                name = names.get(entry.id, "?")
            else:
                name = "?"
            found.append((f"{rel}:{node.lineno}", name))
    return found


def test_the_fence_holds_every_file_the_audit_log_declares():
    """🔴 Red on main: the security log kept its key, log and archive under the home by names of
    its own, and no declaration the sandbox reads named any of them, so the sandbox fenced none.
    Every entry the log's code keeps under a home is now one of ``owner_only.AUDIT_NAMES``, which
    the fence is built from: a new file of the log's cannot be added outside the fence."""
    declared = set(getattr(owner_only, "AUDIT_NAMES", ()))
    joined = _joined()
    assert len(joined) >= 5, f"found only {joined}: this check would pass vacuously"
    undeclared = [(where, name) for where, name in joined if name not in declared]
    assert undeclared == [], f"kept under a home outside the fence's declaration: {undeclared}"
    # And every declared name is one the log keeps: a stale entry would fence a file for nobody.
    unused = sorted(declared - {name for _where, name in joined})
    assert unused == [], f"declared, and kept by nothing: {unused}"
