"""No test walks one of the source roots itself: it reads them through ``tests/source_index.py``.

The rule, as ``tests/test_ci_contract.py`` holds it over every module under ``tests/``:

a **recursive walk** (``.rglob(…)``, ``os.walk(…)``, ``glob.glob(…, recursive=True)``, a
``.glob("**…")``) that starts at the package (``src/personalclaw``, or a path built from one of
its modules' ``__file__``), ``web/src``, ``docs`` or ``.github/workflows`` is a private walker:
ask ``source_index`` for the walk instead, and for the texts and trees of what it returns. A walk
is where the time goes (each one reads, and usually parses, a whole root), so it is the walk the
row holds; a test that reads and parses one named module costs milliseconds and is left to the
suite census.

A walk of anything else (a test's ``tmp_path``, a home it seeded, ``tests/``, ``deploy/``,
``web/e2e``) is not under the index and is left alone, and so is a listing of one folder
(``.glob("*.py")``), which reads no tree. Where a test must reach a root itself, the reason is
recorded in :data:`NOT_THROUGH_THE_INDEX`, by ``file::function``.

The path a walk starts from is read off the source: string constants, ``/`` joins, ``Path(…)``,
``.parent``, ``.parents[n]``, ``__file__``, and names bound in the module, the function or the
class. A function's own parameter is a path its caller chose, so it is never read as a root.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where a test reaches a root itself, by ``file::function``, and why it must.
NOT_THROUGH_THE_INDEX: dict[str, str] = {
    "tests/test_selfqa_companion.py::TestTheInterimScriptStaysRetired"
    ".test_no_commit_watch_script_ships": (
        "an existence check by file name over all of src/, the generated egg-info included"
    ),
    "tests/test_backend_bundle_manifest.py::TestDataFilesReachTheBundle"
    ".test_every_package_data_glob_is_carried": (
        "evaluates pyproject's package-data glob patterns, every file type; the glob is the subject"
    ),
}

#: The roots, as a test spells a path under the repository. ``src`` holds the package.
_ROOTS = ("src/personalclaw", "src", "web/src", "docs", ".github/workflows")

#: What a path spelled from a package module's ``__file__`` stands for.
_PACKAGE = "<package>"
#: What the test file's own ``__file__`` stands for.
_THIS = "<this>"
_REPO = "<repo>"

_PATH_TYPES = {"Path", "PurePath", "PosixPath", "PurePosixPath"}
_SAME = {"resolve", "absolute", "expanduser"}


class _Scopes:
    """The bindings a name in a function can be read through: the function's own, its class's
    (for ``self.X``), and the module's; and each function's parameters."""

    def __init__(self, tree: ast.Module) -> None:
        self.module: dict[str, list[ast.expr]] = {}
        self.functions: dict[ast.AST, dict[str, list[ast.expr]]] = {}
        self.classes: dict[ast.AST, dict[str, list[ast.expr]]] = {}
        self.params: dict[ast.AST, set[str]] = {}
        self.parent: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                owner = self._owner(node)
                table = (
                    self.module
                    if owner is None
                    else (
                        self.classes if isinstance(owner, ast.ClassDef) else self.functions
                    ).setdefault(owner, {})
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        table.setdefault(target.id, []).append(node.value)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                self.params[node] = {
                    a.arg
                    for a in [
                        *args.posonlyargs,
                        *args.args,
                        *args.kwonlyargs,
                        args.vararg,
                        args.kwarg,
                    ]
                    if a is not None
                }

    def _owner(self, node: ast.AST) -> ast.AST | None:
        up = self.parent.get(node)
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return up
            up = self.parent.get(up)
        return None

    def enclosing(self, node: ast.AST) -> list[ast.AST]:
        """The functions and classes around *node*, innermost first."""
        out = []
        up = self.parent.get(node)
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                out.append(up)
            up = self.parent.get(up)
        return out

    def qualname(self, node: ast.AST) -> str:
        names = [
            n.name
            for n in reversed(self.enclosing(node))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        return ".".join(names) or "<module>"


def _join(left: str | None, right: str | None) -> str | None:
    if left is None or right is None:
        return None
    if right.startswith("<"):
        return right
    parts = [p for p in f"{left}/{right}".split("/") if p and p != "."]
    out: list[str] = []
    for part in parts:
        if part == ".." and out and not out[-1].startswith("<"):
            out.pop()
        else:
            out.append(part)
    return "/".join(out)


def _parent(path: str | None) -> str | None:
    if path is None:
        return None
    if path == _THIS:
        return f"{_REPO}/tests"
    if path.startswith(_PACKAGE):
        return _PACKAGE
    if "/" not in path:
        return None if path.startswith("<") else ""
    return path.rsplit("/", 1)[0]


class _Spelling:
    """The path an expression builds, as text, or None when the source cannot say."""

    def __init__(self, scopes: _Scopes) -> None:
        self.scopes = scopes

    def __call__(self, expr: ast.AST, at: ast.AST, seen: frozenset = frozenset()) -> set[str]:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return {expr.value.strip("/") if not expr.value.startswith("/") else "<absolute>"}
        if isinstance(expr, ast.Name):
            if expr.id == "__file__":
                return {_THIS}
            return self._bound(expr.id, at, seen)
        if isinstance(expr, ast.Attribute):
            if expr.attr == "__file__":
                return {_PACKAGE}
            if expr.attr == "parent":
                return {p for p in map(_parent, self(expr.value, at, seen)) if p is not None}
            if isinstance(expr.value, ast.Name) and expr.value.id in ("self", "cls"):
                return self._class_bound(expr.attr, at, seen)
            return set()
        if isinstance(expr, ast.Subscript):
            base = expr.value
            if isinstance(base, ast.Attribute) and base.attr == "parents":
                index = expr.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, int):
                    out = set()
                    for p in self(base.value, at, seen):
                        for _ in range(index.value + 1):
                            p = _parent(p)
                        if p is not None:
                            out.add(p)
                    return out
            return set()
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            # A part the source cannot say (a loop variable, a value read from a file) still
            # lies under what it is joined to, so the walk starts inside that root.
            rights = self(expr.right, at, seen) or {"?"}
            return {
                j
                for left in self(expr.left, at, seen)
                for right in rights
                if (j := _join(left, right)) is not None
            }
        if isinstance(expr, ast.Call):
            return self._call(expr, at, seen)
        return set()

    def _call(self, call: ast.Call, at: ast.AST, seen: frozenset) -> set[str]:
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _PATH_TYPES or (name in ("join",) and _is(func, "os", "path")):
            out = {""}
            for arg in call.args:
                out = {
                    j for o in out for a in self(arg, at, seen) if (j := _join(o, a)) is not None
                }
            return out if call.args else set()
        if isinstance(func, ast.Attribute) and name in _SAME:
            return self(func.value, at, seen)
        if isinstance(func, ast.Attribute) and name == "joinpath":
            out = self(func.value, at, seen)
            for arg in call.args:
                out = {
                    j for o in out for a in self(arg, at, seen) if (j := _join(o, a)) is not None
                }
            return out
        if name in ("str", "fspath", "realpath", "abspath") and call.args:
            return self(call.args[0], at, seen)
        if name == "dirname" and call.args:
            return {p for p in map(_parent, self(call.args[0], at, seen)) if p is not None}
        if name in ("getfile", "getsourcefile") and _is(func, "inspect"):
            return {_PACKAGE}
        return set()

    def _bound(self, name: str, at: ast.AST, seen: frozenset) -> set[str]:
        if name in seen:
            return set()
        for scope in self.scopes.enclosing(at):
            if name in self.scopes.params.get(scope, set()):
                return set()  # its caller chose it
            values = self.scopes.functions.get(scope, {}).get(name)
            if values:
                return self._each(values, name, seen)
        return self._each(self.scopes.module.get(name, []), name, seen)

    def _class_bound(self, name: str, at: ast.AST, seen: frozenset) -> set[str]:
        for scope in self.scopes.enclosing(at):
            if isinstance(scope, ast.ClassDef):
                return self._each(self.scopes.classes.get(scope, {}).get(name, []), name, seen)
        return set()

    def _each(self, values: list[ast.expr], name: str, seen: frozenset) -> set[str]:
        return {s for v in values for s in self(v, v, seen | {name})}


def _is(func: ast.AST, *dotted: str) -> bool:
    """Whether *func* is an attribute of the module ``.``-path *dotted* (``os.path.join``)."""
    if not isinstance(func, ast.Attribute):
        return False
    node: ast.AST = func.value
    for part in reversed(dotted):
        if isinstance(node, ast.Attribute) and node.attr == part:
            node = node.value
        elif isinstance(node, ast.Name) and node.id == part:
            node = ast.Name(id="")
        else:
            return False
    return True


def _root(path: str) -> str | None:
    """The source root *path* lies in, or None."""
    if path.startswith(_PACKAGE):
        return "src/personalclaw"
    path = path.removeprefix(f"{_REPO}/").removeprefix(_REPO)
    for root in _ROOTS:
        if path == root or path.startswith(root + "/"):
            return "src/personalclaw" if root == "src" else root
    return None


def _walks(tree: ast.Module):
    """``(call, where it starts)`` for each recursive walk in *tree*."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        func = node.func
        if func.attr == "rglob":
            yield node, func.value
        elif func.attr == "walk" and isinstance(func.value, ast.Name) and func.value.id == "os":
            if node.args:
                yield node, node.args[0]
        elif func.attr in ("glob", "iglob") and node.args:
            pattern = node.args[0]
            if isinstance(func.value, ast.Name) and func.value.id == "glob":
                if any(k.arg == "recursive" for k in node.keywords):
                    yield node, pattern
            elif isinstance(pattern, ast.Constant) and "**" in str(pattern.value):
                yield node, func.value


def private_walks(repo: Path) -> list[str]:
    """Every private walker under ``repo/tests``, as ``file:line: what``, and every exemption in
    :data:`NOT_THROUGH_THE_INDEX` that names no walk any more (a stale one is a hole that looks
    like a decision)."""
    found: list[str] = []
    exempted: set[str] = set()
    tests = repo / "tests"
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        if path.name in ("source_index.py", "private_walkers.py"):
            continue
        text = path.read_text(encoding="utf-8")
        # Every walk this reads spells one of these, so a module without them holds none: a
        # complete prefilter (`ast.walk(` is not `os.walk(`), which spares parsing the modules that
        # walk nothing.
        if not any(word in text for word in ("rglob(", "os.walk(", "recursive", "**")):
            continue
        tree = ast.parse(text, filename=rel)
        walks = list(_walks(tree))
        if not walks:
            continue
        scopes = _Scopes(tree)
        spell = _Spelling(scopes)
        for call, start in walks:
            roots = {r for s in spell(start, call) if (r := _root(s)) is not None}
            if not roots:
                continue
            where = f"{rel}::{scopes.qualname(call)}"
            if where in NOT_THROUGH_THE_INDEX:
                exempted.add(where)
                continue
            found.append(
                f"{rel}:{call.lineno}: {scopes.qualname(call)} walks {sorted(roots)[0]} itself; "
                "read it through tests/source_index.py, or record why it cannot in "
                "private_walkers.NOT_THROUGH_THE_INDEX"
            )
    found += [
        f"{where} is exempted in private_walkers.NOT_THROUGH_THE_INDEX but walks no root now: "
        "drop it"
        for where in sorted(set(NOT_THROUGH_THE_INDEX) - exempted)
        if (repo / where.split("::")[0]).is_file() or repo == _REPO_ROOT
    ]
    return found
