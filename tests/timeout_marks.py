"""Every per-test timeout under ``tests/`` is listed, with its cap and why the test needs it.

The suite's default cap is ``--timeout`` in ``pyproject.toml``'s addopts. A test that needs
another one says so with ``pytest.mark.timeout(N)``, and that mark is a **site**: wherever the
call is written, keyed by its file and the scope it sits in.

- a decorator on a test (``tests/test_x.py::test_y``, or ``::TestClass.test_y`` in a class);
- a module's ``pytestmark`` (``::pytestmark``), or a module-level name a decorator reuses
  (``::_EVERY_REAL_GATE``);
- a conftest hook's ``add_marker(pytest.mark.timeout(...))`` (``::pytest_collection_modifyitems``),
  which caps a whole family at once;
- a mark built inside a test as data (``::test_...``).

``tests/timeout-marks.toml`` lists each site with the cap it sets and a reason. The row in
``tests/rails/test_ci_contract.py`` fails on a site the list lacks, an entry whose site is gone,
a cap that differs from the listed one, an entry with no reason, and a site listed twice. So a
new mark arrives with its reason, in review, and a mark that is deleted takes its entry with it.

The cap is read off the source: a number, or a module-level name bound to one (or to a sum or
product of such names), so moving a constant moves the cap the row sees. A cap it cannot read
is reported as such. ``pytest.mark.timeout``, ``mark.timeout`` after ``from pytest import mark``
(under any alias), and ``pytest`` imported under another name are all read. A mark spelled any
other way (a string ``add_marker("timeout")``, a ``getattr``) is review's to catch.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

#: The list, relative to the repository root.
LIST = "tests/timeout-marks.toml"

Cap = int | float | str


def _mark_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names ``pytest`` and ``pytest.mark`` are bound to in a module."""
    pytest_names, mark_names = {"pytest"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            pytest_names |= {a.asname for a in node.names if a.name == "pytest" and a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            mark_names |= {a.asname or a.name for a in node.names if a.name == "mark"}
    return pytest_names, mark_names


def _is_timeout(call: ast.Call, pytest_names: set[str], mark_names: set[str]) -> bool:
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "timeout"):
        return False
    base = func.value
    if isinstance(base, ast.Name):
        return base.id in mark_names
    return (
        isinstance(base, ast.Attribute)
        and base.attr == "mark"
        and isinstance(base.value, ast.Name)
        and base.value.id in pytest_names
    )


def _constants(tree: ast.Module) -> dict[str, int | float]:
    """The module-level names bound to a number, or to a sum, difference or product of them."""
    known: dict[str, int | float] = {}

    def value(node: ast.expr) -> int | float | None:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            return known.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            left, right = value(node.left), value(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            return left * right
        return None

    for stmt in tree.body:
        target, expr = None, None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, expr = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, expr = stmt.target, stmt.value
        if isinstance(target, ast.Name) and expr is not None:
            found = value(expr)
            if found is not None:
                known[target.id] = found
    return known


def sites(root: Path) -> tuple[dict[str, Cap], list[str]]:
    """Every timeout site under ``root/tests``, by ``file::scope``, with its cap; and the
    problems a site has that no list entry can settle."""
    found: dict[str, Cap] = {}
    problems: list[str] = []
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pytest_names, mark_names = _mark_names(tree)
        constants = _constants(tree)

        def visit(node: ast.AST, scope: list[str]) -> None:
            for child in ast.iter_child_nodes(node):
                inner = scope
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    inner = [*scope, child.name]
                elif not scope and isinstance(child, (ast.Assign, ast.AnnAssign)):
                    target = child.targets[0] if isinstance(child, ast.Assign) else child.target
                    if isinstance(target, ast.Name):
                        inner = [target.id]
                if isinstance(child, ast.Call) and _is_timeout(child, pytest_names, mark_names):
                    site = f"{rel}::{'.'.join(scope) or '<module>'}"
                    arg = child.args[0] if child.args else None
                    if arg is None:
                        problems.append(f"{site} sets a timeout mark with no cap")
                    elif site in found:
                        problems.append(
                            f"{site} sets more than one timeout mark: give each its own scope, "
                            "so the list can tell them apart"
                        )
                    elif isinstance(arg, ast.Constant) and type(arg.value) in (int, float):
                        found[site] = arg.value
                    elif isinstance(arg, ast.Name) and arg.id in constants:
                        found[site] = constants[arg.id]
                    else:
                        found[site] = ast.unparse(arg)
                        problems.append(
                            f"{site} sets a cap the row cannot read ({ast.unparse(arg)}): use a "
                            "number, or a module-level name bound to one"
                        )
                visit(child, inner)

        visit(tree, [])
    return found, problems


def listed(root: Path) -> tuple[dict[str, tuple[Cap, str]], list[str]]:
    """The list's entries, by site, with their cap and reason; and what is wrong with it."""
    path = root / LIST
    if not path.is_file():
        return {}, [f"{LIST} is missing"]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return {}, [f"{LIST} does not parse: {exc}"]
    entries: dict[str, tuple[Cap, str]] = {}
    bad: list[str] = []
    for number, entry in enumerate(data.get("mark", []), start=1):
        site, cap, reason = entry.get("site"), entry.get("cap"), entry.get("reason")
        if not isinstance(site, str) or not site:
            bad.append(f"{LIST} entry {number} names no site")
            continue
        if site in entries:
            bad.append(f"{site} is listed twice in {LIST}")
            continue
        if not isinstance(reason, str) or not reason.strip():
            bad.append(f"{site} is listed in {LIST} with no reason")
        entries[site] = (cap, reason if isinstance(reason, str) else "")
    return entries, bad


def violations(root: Path) -> list[str]:
    """What keeps the tree's timeout marks and the list from matching, one line each."""
    found, bad = sites(root)
    entries, list_bad = listed(root)
    bad += list_bad
    for site, cap in sorted(found.items()):
        if site not in entries:
            bad.append(
                f"{site} sets a {cap} s timeout that {LIST} does not list: add it there with "
                "the cap and why this test needs more (or less) than the default"
            )
        elif entries[site][0] != cap:
            bad.append(f"{site} sets {cap} s where {LIST} lists {entries[site][0]} s")
    for site in sorted(set(entries) - set(found)):
        bad.append(f"{site} is listed in {LIST} but sets no timeout mark: delete its entry")
    return bad
