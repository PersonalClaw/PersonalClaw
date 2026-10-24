"""``python -m tools.ci select``: the suite's test files a change can break, nearest first (#3945).

``make check`` runs the change tier, which holds no Python suite, then ``select --run``: the test
files this names, through the run's memory admission. A pull request's ``python-suite`` still
runs every file: the selection is local alone.

**The graph.** Every ``.py`` under :data:`ROOTS` is a node, named as ``pythonpath = ["src", "."]``
and pytest's rootdir insertion import it: ``src/personalclaw/a/b.py`` is ``personalclaw.a.b``, a
file under ``tests/`` is both ``tests.x.y`` and the bare name its folder gives it, and the rest are
named from the repository root. Each node is read with :mod:`ast`, never imported, and an edge runs
from importer to imported for:

- ``import a.b`` and ``from a.b import c`` (to ``a.b``, and to ``a.b.c`` when that is a module),
  relative imports resolved against the importer's package, and every package on the way, since
  importing ``a.b.c`` runs ``a/__init__.py`` and ``a/b/__init__.py``;
- imports inside functions and ``if TYPE_CHECKING:`` blocks too: a lazy import still runs in some
  test;
- ``importlib.import_module`` and ``__import__`` of a name the module always spells: a literal, a
  name its top level binds to one, or an f-string of those;
- in a module with a dynamic import (below), what its literals name: a dotted module name in a
  table, a ``.py`` file by its path, every node under a folder;
- a fixture: a test file that asks for a conftest's fixture (by a parameter name or
  ``usefixtures``) reaches what that fixture's body imports or names, and what the fixtures it asks
  for reach. An autouse fixture adds no edge: every test runs it, so a break in what it reaches
  fails the change's own tests too.

**Where the walk starts** (:func:`starts`): a changed module, or a test file at distance 0; a
deleted module's importers, by its name. A changed ``__init__.py`` starts from its package, which
every importer of a module in it reaches. A non-``.py`` file under ``src/personalclaw/`` starts from
the modules beside it and the package modules that name it. Any changed path starts from the
modules under ``tests/`` that name it, or a folder holding it, in a path literal. The walk runs
backwards over importers, and a test file's distance is the fewest hops from a change.

**The whole suite** (every file :func:`tools.ci.shards.universe` lists, never "no paths") is the
answer, with its reason, for a change to a ``conftest.py``, to the harness it loads from ``tests/``
at its top level, to :data:`WHOLE`, to a path that is not a plain repository path, and whenever a
node cannot be parsed or a dynamic import is not listed.

**Dynamic imports.** An import whose target the parser cannot fold (``import_module(name)``,
``pkgutil.iter_modules``, a file loaded by a computed path) is listed in :data:`DYNAMIC_FILE`,
with the module prefix it may import, ``named`` when its module's literals already reach the
target, or ``outside`` when it loads only code outside this repository. A ``prefix`` site's module
starts the walk on any change under its prefix. ``tests/rails/test_select_tree.py`` holds the list
to the tree, so a new site fails there until it is listed, and an unlisted one selects the whole
suite here. The rails' own sites are not listed: a rail is never selected.

**Always added:** :data:`WHOLE_TREE_READERS`, the tests that read every tracked file, and every
module that carries the ``security`` marker, which is never selected out.

**Left out:** ``tests/rails`` (the change tier's ``rails`` job runs every rail, and ``python -m
tools.ci rails select`` names the ones a change can break) and the files a catalogue job owns by
name (:data:`tools.ci.shards.JOB_OWNED`).

**Recall.** :func:`recall_misses` holds the selection to the coverage selection map
(``tools/ci/selection_map.py``, #3946) where one is present: each test file the map says ran a
source file must be selected by a change to that file. ``tests/rails/test_select_tree.py`` runs
it on a planted map, and on this clone's map when it has one.

Standard library only, like every file in ``tools/ci``. The parse of each node is kept in
:data:`CACHE`, keyed by its path, size and modification time and by this file's own hash; a cache
that cannot be read is rebuilt.
"""

from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import tomllib
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import shards

#: The folders whose ``.py`` files are the graph's nodes.
ROOTS = ("src/personalclaw/", "tests/", "tools/ci/", "harness/", "scripts/")
#: The folder ``pythonpath`` names for the package; the rest are named from the root.
SRC = "src/"
#: The rails, which the suite's shards never run (``tools/ci/rails_census.py``).
RAILS = "tests/rails/"
#: Changed paths whose change reaches every test: pytest's configuration, the dependency set,
#: and the catalogue that names the suite's markers and environment.
WHOLE = frozenset({"pyproject.toml", "uv.lock", "setup.cfg", "setup.py", "tools/ci/catalogue.toml"})
#: The tests that read every tracked file (``tests/rails/test_ci_python_suite_inputs_tree.py``
#: holds the whole-tree reader it models to this list).
WHOLE_TREE_READERS = ("tests/test_publication_hygiene_baseline.py",)
#: The listed dynamic imports.
DYNAMIC_FILE = "tools/ci/select-dynamic.toml"
#: Where the parse of each node is kept between runs.
CACHE = ".local/select/graph.json"
#: Past this many selected files, ``--run`` runs the whole suite and says so.
OVER = 800
#: ``select``'s exit when nothing reaches the change: no list is written.
NOTHING = 3

#: The calls that import a module by its name, and the argument that names it.
_BY_NAME = {"import_module": 0, "__import__": 0, "run_module": 0}
#: The calls that load or run a file by its path, or list a package's modules.
_BY_PATH = frozenset(
    {"spec_from_file_location", "run_path", "SourceFileLoader", "SourcelessFileLoader"}
)
_LISTS = frozenset({"iter_modules", "walk_packages"})
_FIXTURE_DECORATORS = frozenset({"fixture", "yield_fixture"})
#: A dotted module name in a literal, with an optional ``:attr`` after it.
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?::[A-Za-z_]\w*)?")


# ── one node's facts ──────────────────────────────────────────────────────────────────────────


@dataclass
class Facts:
    """What one module says, read from its source."""

    #: Imported names, absolute (``a.b`` for ``import a.b``; ``a.b`` and ``a.b.c`` for
    #: ``from a.b import c``), each with whether it was imported at the module's top level.
    imports: list[tuple[str, bool]] = field(default_factory=list)
    #: The dynamic imports: (the call as written, the literal module prefix it names or "").
    dynamic: list[tuple[str, str]] = field(default_factory=list)
    #: Path-like literals: each string, and each ``/``- or ``join``-joined chain of strings.
    paths: list[str] = field(default_factory=list)
    #: Fixtures defined here: name -> (imported names its body uses, fixtures it asks for, autouse).
    fixtures: dict[str, tuple[list[str], list[str], bool]] = field(default_factory=dict)
    #: The names this module's functions take as parameters, and the fixtures ``usefixtures``
    #: names: what it may ask a conftest for.
    asks: list[str] = field(default_factory=list)
    #: The names its top level binds by an import, each to what it imports.
    bindings: dict[str, str] = field(default_factory=dict)
    #: Whether its own ``pytestmark`` holds ``pytest.mark.security``.
    security: bool = False
    #: Dotted module names its string literals spell (``"a.b"``, ``"a.b:attr"``): a module it
    #: imports by a name it keeps in a table.
    modules: list[str] = field(default_factory=list)
    #: Why the module could not be read, or "".
    error: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "imports": self.imports,
            "dynamic": self.dynamic,
            "paths": self.paths,
            "fixtures": {k: list(v) for k, v in self.fixtures.items()},
            "asks": self.asks,
            "bindings": self.bindings,
            "security": self.security,
            "modules": self.modules,
            "error": self.error,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Facts:
        return cls(
            imports=[(str(n), bool(top)) for n, top in data["imports"]],
            dynamic=[(str(c), str(p)) for c, p in data["dynamic"]],
            paths=[str(p) for p in data["paths"]],
            fixtures={
                str(k): ([str(x) for x in v[0]], [str(x) for x in v[1]], bool(v[2]))
                for k, v in data["fixtures"].items()
            },
            asks=[str(a) for a in data["asks"]],
            bindings={str(k): str(v) for k, v in data["bindings"].items()},
            security=bool(data["security"]),
            modules=[str(m) for m in data["modules"]],
            error=str(data["error"]),
        )


def _called(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _head(node: ast.expr) -> str:
    """The literal text a computed module name starts with: an f-string's first part, or the
    left side of a ``+``."""
    if isinstance(node, ast.JoinedStr) and node.values:
        first = _literal(node.values[0]) if isinstance(node.values[0], ast.Constant) else None
        return first or ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal(node.left) or _head(node.left)
    return ""


def _chain(node: ast.expr) -> list[str] | None:
    """The string parts of ``a / "b" / "c"``, left to right: ``[]`` for a computed root
    (``REPO``, ``Path(__file__).parent``), whose parts after it are relative to it, and None once
    a part is computed, since what follows it names no path."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _chain(node.left)
        right = _literal(node.right)
        if left is None or right is None:
            return None
        return [*left, right]
    text = _literal(node)
    return [text] if text is not None else []


def _resolve_relative(module: str, is_package: bool, level: int, name: str | None) -> str | None:
    parts = module.split(".") if module else []
    base = parts if is_package else parts[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)] if len(base) >= level - 1 else []
    if not base and not name:
        return None
    return ".".join([*base, *([name] if name else [])])


def parse(source: str, module: str, is_package: bool) -> Facts:
    """The facts of one module's source, named ``module``."""
    facts = Facts()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        facts.error = f"{type(exc).__name__}: {exc}"
        return facts
    top_level = {id(n) for n in tree.body}
    _top_nested(tree.body, top_level)
    constants = {
        t.id: n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    # A chain's inner joins (``REPO / "src"`` inside ``REPO / "src" / "x.py"``) name no path of
    # their own: only the whole chain is read.
    # Filled as the walk goes: ast.walk reaches a node before its children.
    inner: set[int] = set()

    def add(name: str | None, node: ast.AST) -> None:
        if name:
            facts.imports.append((name, id(node) in top_level))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name, node)
                if id(node) in top_level:
                    bound = alias.asname or alias.name.split(".")[0]
                    facts.bindings[bound] = alias.name if alias.asname else bound
        elif isinstance(node, ast.ImportFrom):
            base = (
                _resolve_relative(module, is_package, node.level, node.module)
                if node.level
                else node.module
            )
            if base is None:
                continue
            add(base, node)
            for alias in node.names:
                if alias.name != "*":
                    add(f"{base}.{alias.name}", node)
                    if id(node) in top_level:
                        facts.bindings[alias.asname or alias.name] = f"{base}.{alias.name}"
        elif isinstance(node, ast.Call):
            _call(node, facts, module, is_package, constants)
            parts = _joined(node)
            if parts:
                facts.paths.append("/".join(parts))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            inner.add(id(node.left))
            if id(node) in inner:
                continue
            parts = _chain(node)
            if parts:
                facts.paths.append("/".join(parts))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "/" in node.value or node.value.endswith(".py"):
                facts.paths.append(node.value)
            elif _DOTTED.fullmatch(node.value):
                facts.modules.append(node.value.partition(":")[0])
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _function(node, facts)
    facts.security = _carries_security(tree)
    facts.imports = sorted(set(facts.imports))
    facts.paths = sorted(set(facts.paths))
    facts.modules = sorted(set(facts.modules))
    facts.asks = sorted(set(facts.asks))
    facts.dynamic = sorted(facts.dynamic)
    return facts


def _is_security(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "security"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _carries_security(tree: ast.Module) -> bool:
    """Whether the module's ``pytestmark``, as last assigned at its top level, holds the
    ``security`` marker, read as ``tests/security_census.py`` reads it (a rail holds the two
    together): a module that carries it is never selected out."""
    value: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "pytestmark"
        ):
            value = node.value
    if value is None:
        return False
    if isinstance(value, ast.List | ast.Tuple):
        return any(_is_security(e) for e in value.elts)
    return _is_security(value)


def _top_nested(body: Sequence[ast.stmt], into: set[int]) -> None:
    """Statements a module runs at import: its body, and the bodies of its top-level ``if``,
    ``try`` and ``with`` blocks."""
    for stmt in body:
        into.add(id(stmt))
        if isinstance(stmt, ast.If):
            _top_nested(stmt.body, into)
            _top_nested(stmt.orelse, into)
        elif isinstance(stmt, ast.Try | ast.TryStar):
            _top_nested(stmt.body, into)
            for handler in stmt.handlers:
                _top_nested(handler.body, into)
            _top_nested(stmt.orelse, into)
            _top_nested(stmt.finalbody, into)
        elif isinstance(stmt, ast.With):
            _top_nested(stmt.body, into)


def _joined(node: ast.Call) -> list[str] | None:
    """The string arguments of ``os.path.join`` / ``Path(...)`` / ``joinpath``, when two or more."""
    if _called(node) not in {"join", "Path", "joinpath", "PurePosixPath"}:
        return None
    parts = [_literal(a) for a in node.args]
    named = [p for p in parts if p is not None]
    return named if len(named) > 1 else None


def _folded(node: ast.expr, constants: Mapping[str, str]) -> str | None:
    """A string the expression always is: a literal, a name the module binds to one at its top
    level, or an f-string or ``+`` of those."""
    text = _literal(node)
    if text is not None:
        return text
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                if value.format_spec is not None or value.conversion != -1:
                    return None
                part = _folded(value.value, constants)
            else:
                part = _literal(value)
            if part is None:
                return None
            parts.append(part)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left, constants), _folded(node.right, constants)
        return None if left is None or right is None else left + right
    return None


def _call(
    node: ast.Call, facts: Facts, module: str, is_package: bool, constants: Mapping[str, str]
) -> None:
    name = _called(node)
    if name in _BY_NAME and node.args:
        arg = node.args[_BY_NAME[name]]
        literal = _folded(arg, constants)
        package = next((_literal(k.value) for k in node.keywords if k.arg == "package"), None)
        if literal is not None and not literal.startswith("."):
            facts.imports.append((literal, False))
        elif literal is not None and literal.startswith("."):
            level = len(literal) - len(literal.lstrip("."))
            resolved = _resolve_relative(
                package or module, package is not None or is_package, level, literal.lstrip(".")
            )
            if resolved:
                facts.imports.append((resolved, False))
            else:
                facts.dynamic.append((ast.unparse(node), ""))
        else:
            facts.dynamic.append((ast.unparse(node), _head(arg).rstrip(".")))
    elif name in _BY_PATH or name in _LISTS:
        facts.dynamic.append((ast.unparse(node), ""))
    if name == "usefixtures":
        facts.asks.extend(a for a in (_literal(x) for x in node.args) if a)


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef, facts: Facts) -> None:
    params = [a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]]
    facts.asks.extend(p for p in params if p not in {"self", "cls"})
    autouse = False
    is_fixture = False
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        tail = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if tail in _FIXTURE_DECORATORS:
            is_fixture = True
            if isinstance(deco, ast.Call):
                for kw in deco.keywords:
                    if kw.arg == "autouse" and isinstance(kw.value, ast.Constant):
                        autouse = bool(kw.value.value)
    if not is_fixture:
        return
    used: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Import):
            used.update(a.name for a in inner.names)
        elif isinstance(inner, ast.ImportFrom) and inner.module and not inner.level:
            used.add(inner.module)
            used.update(f"{inner.module}.{a.name}" for a in inner.names if a.name != "*")
        elif isinstance(inner, ast.Name | ast.Attribute):
            used.add(_dotted(inner))
    facts.fixtures[node.name] = (sorted(u for u in used if u), sorted(set(params)), autouse)


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


# ── the nodes ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Node:
    path: str
    #: The dotted names it is imported by: its first is its own.
    names: tuple[str, ...]
    is_package: bool


def names_of(path: str, has_init: Mapping[str, bool]) -> tuple[str, ...]:
    """The dotted names a node is imported by (see the module's docstring)."""
    stem = path[: -len(".py")]
    parts = stem.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if path.startswith(SRC):
        return (".".join(parts[1:]),)
    rooted = ".".join(parts)
    if not path.startswith("tests/"):
        return (rooted,)
    # rootdir insertion: the file's folder, or the first folder above it with no __init__.py,
    # goes on sys.path, so the module is named from there.
    folder = posixpath.dirname(path)
    while folder and has_init.get(folder, False):
        folder = posixpath.dirname(folder)
    inserted = stem[len(folder) + 1 :] if folder else stem
    tail = inserted.split("/")
    if tail[-1] == "__init__":
        tail = tail[:-1]
    bare = ".".join(tail)
    return (rooted, bare) if bare and bare != rooted else (rooted,)


def node_paths(root: Path, working_tree: bool) -> list[str]:
    """Every ``.py`` under :data:`ROOTS` git knows, and with ``working_tree`` the untracked ones."""
    args = ["ls-files", "-z", "--cached"]
    if working_tree:
        args += ["--others", "--exclude-standard"]
    listed = _git(root, *args, "--", *ROOTS)
    return sorted(
        p
        for p in set(listed.split("\0"))
        if p.endswith(".py") and (root / p).is_file() and "/__pycache__/" not in p
    )


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, timeout=120, check=False
    )
    if done.returncode != 0:
        raise Refused(
            f"git {' '.join(args[:2])} failed: {done.stderr.decode(errors='replace').strip()}"
        )
    return done.stdout.decode("utf-8", "replace")


class Refused(Exception):
    """The selection cannot be made at all (git cannot be read, an argument is wrong)."""


def _self_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def read_facts(root: Path, paths: Sequence[str], has_init: Mapping[str, bool]) -> dict[str, Facts]:
    """Each node's facts, from :data:`CACHE` where its path, size and mtime still match. One
    process at a time reads and writes it, so a second that starts during a cold build waits
    and reads the warm cache rather than parsing every node again."""
    lock_file = root / (CACHE + ".lock")
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_file.open("a")
    except OSError:
        return _read_facts(root, paths, has_init)
    with handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            return _read_facts(root, paths, has_init)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read_facts(root: Path, paths: Sequence[str], has_init: Mapping[str, bool]) -> dict[str, Facts]:
    cache_file = root / CACHE
    own = _self_hash()
    cached: dict[str, Any] = {}
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if (
            isinstance(data, dict)
            and data.get("select") == own
            and isinstance(data.get("nodes"), dict)
        ):
            cached = data["nodes"]
    except (OSError, ValueError):
        cached = {}
    out: dict[str, Facts] = {}
    fresh: dict[str, Any] = {}
    for path in paths:
        st = (root / path).stat()
        key = [st.st_size, st.st_mtime_ns]
        entry = cached.get(path)
        facts: Facts | None = None
        if isinstance(entry, dict) and entry.get("key") == key:
            try:
                facts = Facts.from_json(entry["facts"])
            except (KeyError, TypeError, ValueError, IndexError):
                facts = None
        if facts is None:
            try:
                source = (root / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                facts = Facts(error=f"{type(exc).__name__}: {exc}")
            else:
                facts = parse(source, names_of(path, has_init)[0], path.endswith("/__init__.py"))
        out[path] = facts
        fresh[path] = {"key": key, "facts": facts.as_json()}
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"select": own, "nodes": fresh}), encoding="utf-8")
        tmp.replace(cache_file)
    except OSError:
        pass  # no cache: the next run parses again
    return out


# ── the graph ─────────────────────────────────────────────────────────────────────────────────


@dataclass
class Graph:
    nodes: dict[str, Node]
    facts: dict[str, Facts]
    #: Each dotted name to the nodes it names.
    by_name: dict[str, list[str]]
    #: Each dotted name to the nodes that import it, or reach it by a path or a fixture.
    importers: dict[str, set[str]]
    #: Each path literal, normalized, to the nodes that name it.
    named: dict[str, set[str]]
    #: The conftests and the modules under ``tests/`` they load at their top level, and theirs.
    harness: frozenset[str]
    #: The dynamic imports found: (node, the call as written, the literal head it names).
    dynamic: list[tuple[str, str, str]]


def _normal(literal: str) -> str | None:
    """A path literal as a repository path: no ``./``, no trailing ``/``, cut at a glob."""
    text = literal.strip().replace("\\", "/")
    for i, ch in enumerate(text):
        if ch in "*?[{":
            text = text[:i]
            text = text[: text.rfind("/")] if "/" in text else ""
            break
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    if not text or text.startswith(("/", "../")) or "\n" in text or " " in text:
        return None
    return posixpath.normpath(text)


def _prefixes(name: str) -> Iterator[str]:
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        yield ".".join(parts[:i])


def build(
    root: Path, paths: Sequence[str], facts: Mapping[str, Facts], tops: frozenset[str]
) -> Graph:
    """The graph over ``paths`` (each node's ``facts``); ``tops`` are the repository's top-level
    entries, the first part a path literal must have to name a repository path."""
    has_init = {posixpath.dirname(p): True for p in paths if p.endswith("/__init__.py")}
    nodes = {p: Node(p, names_of(p, has_init), p.endswith("/__init__.py")) for p in paths}
    by_name: dict[str, list[str]] = {}
    for node in nodes.values():
        for name in node.names:
            by_name.setdefault(name, []).append(node.path)
    importers: dict[str, set[str]] = {}
    under_folder: dict[str, list[str]] = {}
    for p in nodes:
        for folder in _ancestors(p):
            under_folder.setdefault(folder, []).append(p)

    def edge(importer: str, name: str) -> None:
        for prefix in _prefixes(name):
            importers.setdefault(prefix, set()).add(importer)

    named: dict[str, set[str]] = {}
    for path, fact in facts.items():
        if posixpath.basename(path) == "conftest.py":
            continue  # its fixtures carry its edges (below); its top level is the harness
        for name, _top in fact.imports:
            edge(path, name)
        # A module with a dynamic import loads what its literals name: a module in a table, a
        # file by its path, any file under a folder. Elsewhere a literal is data, read below.
        loads = bool(fact.dynamic)
        for literal in fact.modules if loads else ():
            if literal in by_name:
                edge(path, literal)
        for literal in fact.paths:
            text = _normal(literal)
            if text is None:
                continue
            if text.split("/", 1)[0] in tops:
                named.setdefault(text, set()).add(path)
                if not loads:
                    continue
                if text in nodes:
                    edge(path, nodes[text].names[0])
                else:
                    for under in under_folder.get(text, ()):
                        edge(path, nodes[under].names[0])
            elif path.startswith(SRC):
                # A package module's own data, named from its folder.
                named.setdefault(posixpath.join(posixpath.dirname(path), text), set()).add(path)
    _fixture_edges(nodes, facts, edge)
    dynamic = [(path, call, head) for path, fact in facts.items() for call, head in fact.dynamic]
    return Graph(
        nodes=nodes,
        facts=dict(facts),
        by_name=by_name,
        importers=importers,
        named=named,
        harness=_harness(nodes, facts, by_name),
        dynamic=sorted(dynamic),
    )


def _resolve(name: str, bindings: Mapping[str, str]) -> str:
    head, _, rest = name.partition(".")
    bound = bindings.get(head)
    if bound is None:
        return name
    return f"{bound}.{rest}" if rest else bound


def _fixture_edges(nodes: Mapping[str, Node], facts: Mapping[str, Facts], edge: Any) -> None:
    """An edge from each node under a conftest's folder to what each fixture it asks for reaches,
    and what the fixtures that fixture asks for reach; autouse fixtures add none."""
    conftests = sorted((p for p in facts if posixpath.basename(p) == "conftest.py"), key=len)
    if not conftests:
        return
    for path, fact in facts.items():
        if not path.startswith("tests/") or posixpath.basename(path) == "conftest.py":
            continue
        scope = [c for c in conftests if path.startswith(posixpath.dirname(c) + "/")]
        if not scope:
            continue
        # The nearest conftest's fixture of a name overrides the farther ones'.
        fixtures: dict[str, tuple[str, tuple[list[str], list[str], bool]]] = {}
        for conftest in scope:
            for name, entry in facts[conftest].fixtures.items():
                fixtures[name] = (conftest, entry)
        seen: set[str] = set()
        todo = [a for a in fact.asks if a in fixtures]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            conftest, (used, params, autouse) = fixtures[name]
            if autouse:
                continue
            bindings = facts[conftest].bindings
            for item in used:
                resolved = _resolve(item, bindings)
                if resolved != item or "." in item:
                    edge(path, resolved)
            todo.extend(p for p in params if p in fixtures)


def _harness(
    nodes: Mapping[str, Node], facts: Mapping[str, Facts], by_name: Mapping[str, list[str]]
) -> frozenset[str]:
    found = {p for p in facts if posixpath.basename(p) == "conftest.py"}
    todo = list(found)
    while todo:
        path = todo.pop()
        for name, top in facts[path].imports:
            if posixpath.basename(path) == "conftest.py" and not top:
                continue  # inside a fixture: the fixture's edges carry it
            for target in by_name.get(name, []):
                if target.startswith("tests/") and target not in found:
                    found.add(target)
                    todo.append(target)
    return frozenset(found)


# ── the selection ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Listed:
    """A listed dynamic import: the module it is in, the call as written, and what it may
    import: any module under ``prefix``; with ``named``, a module or file its module already
    imports or names in a literal; with ``outside``, only code outside this repository."""

    file: str
    call: str
    prefix: str
    named: bool
    outside: bool
    why: str

    def problem(self) -> str:
        """What is wrong with the entry itself, or ""."""
        kinds = [bool(self.prefix), self.named, self.outside]
        if sum(kinds) != 1:
            return "it must say exactly one of prefix, named and outside"
        if not self.why:
            return "it must say why"
        return ""


def read_listed(root: Path) -> list[Listed]:
    """:data:`DYNAMIC_FILE`'s sites; a file that cannot be read lists none."""
    try:
        data = tomllib.loads((root / DYNAMIC_FILE).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for entry in data.get("site", []):
        if not isinstance(entry, dict):
            continue
        out.append(
            Listed(
                file=str(entry.get("file", "")),
                call=str(entry.get("call", "")),
                prefix=str(entry.get("prefix", "")),
                named=entry.get("named", False) is True,
                outside=entry.get("outside", False) is True,
                why=str(entry.get("why", "")),
            )
        )
    return out


def unlisted(graph: Graph, listed: Sequence[Listed]) -> list[tuple[str, str]]:
    """The dynamic imports in the tree that no listed site covers, as (file, call)."""
    left: dict[tuple[str, str], int] = {}
    for site in listed:
        if site.problem():
            continue
        left[(site.file, site.call)] = left.get((site.file, site.call), 0) + 1
    out = []
    for path, call, _head_ in graph.dynamic:
        if path.startswith(RAILS):
            continue  # a rail is never selected, so what it imports decides nothing
        if left.get((path, call), 0) > 0:
            left[(path, call)] -= 1
        else:
            out.append((path, call))
    return out


@dataclass
class Selection:
    #: The selected test files, nearest first, each with its distance and why.
    files: list[tuple[int, str, str]]
    #: Why the whole suite was selected, or "".
    whole: str = ""
    #: What the walk left out or noticed, to say.
    notes: list[str] = field(default_factory=list)


def _module_names(path: str, graph: Graph) -> list[str]:
    node = graph.nodes.get(path)
    if node is not None:
        return list(node.names)
    if path.endswith(".py") and path.startswith(ROOTS):
        has_init = {posixpath.dirname(p): True for p in graph.nodes if p.endswith("/__init__.py")}
        return list(names_of(path, has_init))
    return []


def _plain(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        return False
    norm = posixpath.normpath(path)
    return norm == path and norm != "." and not norm.startswith("../")


def starts(
    changed: Sequence[str], graph: Graph, listed: Sequence[Listed]
) -> tuple[dict[str, str], dict[str, str], str]:
    """Where the walk starts: nodes at distance 0 and names whose importers are at distance 1,
    each with why; or, as the third value, why the whole suite is the answer."""
    nodes: dict[str, str] = {p: "changed" for p in changed if p in graph.nodes}
    names: dict[str, str] = {}
    for path in changed:
        if not _plain(path):
            return {}, {}, f"{path!r} is not a plain repository path"
        if posixpath.basename(path) == "conftest.py":
            return {}, {}, f"{path} is a conftest: it reaches every test under it"
        if path in WHOLE:
            return {}, {}, f"{path} changed: it configures every test"
        if path in graph.harness:
            return {}, {}, f"{path} is part of the harness every test loads (conftest's imports)"
        for name in _module_names(path, graph):
            names.setdefault(name, f"{path} changed")
        if path in graph.nodes:
            nodes.setdefault(path, "changed")
        elif path.startswith("src/personalclaw/") and not path.endswith(".py"):
            folder = posixpath.dirname(path)
            for other in graph.nodes:
                if posixpath.dirname(other) == folder:
                    nodes.setdefault(other, f"beside {path}, the package data that changed")
        for at in [path, *_ancestors(path)]:
            for namer in graph.named.get(at, ()):
                # The tests that name it read it. A package module that names its own data reads
                # that; any other module's literal is its own business, reached by its importers
                # only when the module itself changes.
                data = namer.startswith(SRC) and path.startswith("src/personalclaw/")
                if namer.startswith("tests/") or data:
                    nodes.setdefault(namer, f"names {at} in a path literal")
    for site in listed:
        if not site.prefix or site.file not in graph.nodes:
            continue
        for name in names:
            if name == site.prefix or name.startswith(site.prefix + "."):
                nodes.setdefault(
                    site.file, f"imports under {site.prefix or 'anything'} by {site.call}"
                )
                break
    return nodes, names, ""


def _ancestors(path: str) -> list[str]:
    out = []
    folder = posixpath.dirname(path)
    while folder:
        out.append(folder)
        folder = posixpath.dirname(folder)
    return out


def walk(
    graph: Graph, nodes: Mapping[str, str], names: Mapping[str, str]
) -> dict[str, tuple[int, str]]:
    """Every node a start reaches backwards over importers, with its distance and the chain's
    first step."""
    reached: dict[str, tuple[int, str]] = {p: (0, why) for p, why in nodes.items()}
    queue: deque[str] = deque(sorted(nodes))
    for name, why in sorted(names.items()):
        for importer in sorted(graph.importers.get(name, ())):
            if importer not in reached:
                reached[importer] = (1, f"imports {name} ({why})")
                queue.append(importer)
    while queue:
        path = queue.popleft()
        distance = reached[path][0]
        for name in graph.nodes[path].names if path in graph.nodes else ():
            for importer in sorted(graph.importers.get(name, ())):
                if importer not in reached:
                    reached[importer] = (distance + 1, f"imports {name}")
                    queue.append(importer)
    return reached


def select(
    changed: Sequence[str], graph: Graph, listed: Sequence[Listed], universe: Sequence[str]
) -> Selection:
    """The test files of ``universe`` a change to ``changed`` reaches, nearest first."""
    every = sorted(universe)
    whole = [(0, f, "") for f in every]
    broken = sorted(p for p, f in graph.facts.items() if f.error)
    if broken:
        return Selection(
            whole, whole=f"{broken[0]} cannot be parsed ({graph.facts[broken[0]].error})"
        )
    missing = unlisted(graph, listed)
    if missing:
        file, call = missing[0]
        return Selection(
            whole,
            whole=f"{file}: {call} is a dynamic import {DYNAMIC_FILE} does not list"
            + (f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""),
        )
    nodes, names, why_whole = starts(changed, graph, listed)
    if why_whole:
        return Selection(whole, whole=why_whole)
    reached = walk(graph, nodes, names)
    members = set(every)
    files = [(d, p, why) for p, (d, why) in reached.items() if p in members]
    notes = sorted(
        f"{p} changed, and the suite's shards do not run it (tests/rails, or a job's own file)"
        for p in changed
        if p.startswith("tests/") and shards_test(p) and p not in members and (p in graph.nodes)
    )
    picked = {p for _, p, _ in files}
    for reader in WHOLE_TREE_READERS:
        if reader in members and reader not in picked:
            files.append((0, reader, "reads every tracked file"))
            picked.add(reader)
    for path in every:
        fact = graph.facts.get(path)
        if fact is not None and fact.security and path not in picked:
            files.append((0, path, "carries the security marker: never selected out"))
    files.sort(key=lambda t: (t[0], t[1]))
    return Selection(files, notes=notes)


def shards_test(path: str) -> bool:
    name = posixpath.basename(path)
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


# ── the command ───────────────────────────────────────────────────────────────────────────────


def changed_paths(root: Path, base: str, working_tree: bool) -> list[str]:
    """The paths that differ from where ``base`` and HEAD meet: the commits since, and with
    ``working_tree`` the checkout as it stands too, its untracked files among them. A rename is
    its two paths."""
    meet = _git(root, "merge-base", base, "HEAD").strip()
    if working_tree:
        listed = _git(root, "diff", "--name-only", "--no-renames", "-z", meet)
        listed += "\0" + _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    else:
        listed = _git(root, "diff", "--name-only", "--no-renames", "-z", meet, "HEAD")
    return sorted({p for p in listed.split("\0") if p})


def load_graph(root: Path, working_tree: bool) -> Graph:
    paths = node_paths(root, working_tree)
    has_init = {posixpath.dirname(p): True for p in paths if p.endswith("/__init__.py")}
    facts = read_facts(root, paths, has_init)
    tops = frozenset(p.split("/", 1)[0] for p in _git(root, "ls-files", "-z").split("\0") if p)
    return build(root, paths, facts, tops)


def _suite_job(root: Path) -> Any:
    from . import catalogue

    return catalogue.read(root).job("python-suite")


@dataclass
class Runner:
    """How ``--run`` runs pytest and admits it: the host's, or a test's."""

    run: Any = subprocess.call
    #: The machine's memory (``admission.Memory``); ``None`` reads this machine's.
    memory: Any = None
    cpus: int = field(default_factory=lambda: os.cpu_count() or 1)
    on_github: bool = field(default_factory=lambda: os.environ.get("GITHUB_ACTIONS") == "true")
    #: The catalogue job whose workers, memory and environment the run takes.
    job: Any = _suite_job
    #: Where the jobs' peak memory is kept, or ``None`` to find it beside the verdict store.
    peaks: Path | None = None
    clock: Any = time.monotonic


#: The worker count when admission cannot apply (no memory reading, no peaks to reckon from).
BARE_WORKERS = 2


#: Where ``--run`` keeps how long its runs here took, to estimate the next.
TIMES = ".local/select/runs.json"
#: The whole suite's cost in worker-minutes before any run here was timed: CI's three Linux
#: shards on main's run 40137259195 (2026-10-24) took 27 minutes at four workers each.
CI_WORKER_MINUTES = 108.0
#: How many of this machine's runs the estimate reads.
TIMED_RUNS = 10


def _timed(root: Path) -> list[dict[str, float]]:
    try:
        data = json.loads((root / TIMES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        r
        for r in data
        if isinstance(r, dict)
        and all(
            isinstance(r.get(k), int | float) and r[k] > 0 for k in ("files", "workers", "seconds")
        )
    ]


def expected(root: Path, files: int, workers: int, universe: int) -> str:
    """How long ``files`` test files take at ``workers``: from this machine's timed runs, or
    from CI's measured cost of the whole suite before there are any."""
    runs = _timed(root)
    if runs:
        rates = sorted(r["seconds"] * r["workers"] / r["files"] for r in runs)
        minutes = rates[len(rates) // 2] * files / workers / 60
        basis = f"from the last {len(runs)} timed run(s) here"
    else:
        minutes = CI_WORKER_MINUTES * files / max(universe, 1) / workers
        basis = "from CI's shard time; no run here is timed yet"
    return f"about {max(1, round(minutes))} minutes at {workers} workers ({basis})"


def _record_time(root: Path, files: int, workers: int, seconds: float) -> None:
    runs = [*_timed(root), {"files": files, "workers": workers, "seconds": round(seconds, 1)}]
    try:
        (root / TIMES).parent.mkdir(parents=True, exist_ok=True)
        (root / TIMES).write_text(json.dumps(runs[-TIMED_RUNS:]) + "\n", encoding="utf-8")
    except OSError:
        pass  # untimed: the next estimate reads CI's figure or the older runs


def run_tests(
    root: Path,
    listed: Path,
    workers: int | None,
    runner: Runner,
    say: Any,
    whole: str = "",
) -> int:
    """pytest on the files ``listed`` names, with ``python-suite``'s environment, through
    admission (``tools/ci/admission.py``) with ``--workers`` or the catalogue's, ``auto`` read as
    every local run reads it (two here, the runner's CPU count on GitHub); :data:`BARE_WORKERS`
    when this machine's memory cannot be read. ``2`` when admission gives the run up."""
    from . import admission, jobs, verdict_store

    job = runner.job(root)
    wanted: int | str | None = workers if workers is not None else job.workers
    if wanted in (None, "auto"):
        # As every local run reads it (jobs.LOCAL_AUTO_WORKERS): the owner's machine rule.
        count = runner.cpus if runner.on_github else jobs.LOCAL_AUTO_WORKERS
    else:
        count = int(wanted)
    memory = runner.memory
    if memory is None and not runner.on_github:
        memory = admission.Memory.this()
    if memory is not None:
        if memory.free() is None:
            count = workers if workers is not None else BARE_WORKERS
            say(f"select: this machine's free memory cannot be read, so {count} workers run")
        else:
            peaks_at = runner.peaks
            if peaks_at is None:
                try:
                    peaks_at = verdict_store.Store.for_repo(root).root.parent / admission.PEAKS_FILE
                except verdict_store.Refused:
                    peaks_at = None
            need, how = admission.need_mb(
                workers=count,
                peak_mb=admission.Peaks(peaks_at).get(job.id),
                declared_gb=job.mem_gb,
            )
            refused = admission.admit("select", need, how, memory, say=say)
            if refused is not None:
                say(f"select: the selected tests did not run: {refused}")
                return 2
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-n",
        str(count),
        "--dist",
        "worksteal",
        "-q",
        "tests",
        "--shard-list",
        str(listed),
    ]
    env = {**os.environ, **dict(job.env)}
    files = sum(1 for line in listed.read_text(encoding="utf-8").splitlines() if line.strip())
    if whole:
        eta = expected(root, files, count, len(shards.universe(root)))
        say(f"select: the whole suite runs: {whole}; expect {eta}")
    say(f"select: {' '.join(argv[1:])}")
    began = runner.clock()
    try:
        code = int(runner.run(argv, cwd=str(root), env=env))
        if code in (0, 1):  # a run that ran: passed, or a test failed
            _record_time(root, files, count, runner.clock() - began)
        return code
    finally:
        if memory is not None and memory.free() is not None:
            admission.release(memory)


def main(argv: Sequence[str] | None = None, runner: Runner | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tools.ci select")
    parser.add_argument("--base", default="origin/main", help="what the change is measured from")
    parser.add_argument("--working-tree", action="store_true", help="the checkout as it stands")
    parser.add_argument("--paths", nargs="+", help="the changed paths, instead of git's")
    parser.add_argument("--out", type=Path, help="write the list here, one file per line")
    parser.add_argument("--why", action="store_true", help="each file's distance and why")
    parser.add_argument("--run", action="store_true", help="run pytest on the list")
    parser.add_argument("--workers", type=int, help="pytest's workers for --run")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    root = args.root

    def say(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        changed = (
            sorted(set(args.paths))
            if args.paths
            else changed_paths(root, args.base, args.working_tree)
        )
        graph = load_graph(root, args.working_tree or bool(args.paths))
    except Refused as exc:
        say(f"select: {exc}")
        return 2
    universe = shards.universe(root)
    chosen = select(changed, graph, read_listed(root), universe)
    out = args.out or (root / ".local/select/tests.txt" if args.run else None)
    for note in chosen.notes:
        say(f"select: {note}")
    if not chosen.files:
        if out is not None and out.exists():
            out.unlink()
        say(f"select: no test file reaches the {len(changed)} changed path(s): nothing to run")
        return 0 if args.run else NOTHING
    if chosen.whole:
        if not args.run:  # --run says it with the time to expect, before it starts
            say(f"select: the whole suite ({len(chosen.files)} files): {chosen.whole}")
    else:
        far = chosen.files[-1][0]
        say(
            f"select: {len(chosen.files)} of {len(universe)} test files reach the"
            f" {len(changed)} changed path(s), at distances 0 to {far}"
        )
    files = chosen.files
    whole = chosen.whole
    if args.run and not chosen.whole and len(files) > OVER:
        whole = (
            f"{len(files)} of {len(universe)} files reach the change, past {OVER}"
            f" ({', '.join(changed[:3])}{', …' if len(changed) > 3 else ''}"
            f" reach them through the modules that import them, inside functions too)"
        )
        reached = {p for _, p, _ in files}
        files = files + [(-1, f, f"past {OVER}") for f in sorted(universe) if f not in reached]
    lines = [f"{p}\t{d}\t{why}" if args.why else p for d, p, why in files]
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(f"{p}\n" for _, p, _ in files), encoding="utf-8")
    if out is None or args.why:
        print("\n".join(lines))
    if not args.run:
        if out is not None:
            say(
                f"select: run them with: pytest -n {BARE_WORKERS} --dist worksteal tests"
                f" --shard-list {out}"
            )
        return 0
    if out is None:  # --run always writes a list, above
        return 2
    return run_tests(root, out, args.workers, runner or Runner(), say, whole=whole)


def recall_misses(
    graph: Graph,
    listed: Sequence[Listed],
    universe: Sequence[str],
    coverage: Mapping[str, Sequence[str]],
) -> list[tuple[str, str]]:
    """Each (source file, test file) the coverage selection map (``tools/ci/selection_map.py``)
    says ran the source, that a change to the source alone would not select: what the import
    graph misses, measured."""
    members = set(universe)
    misses: list[tuple[str, str]] = []
    for source, tests in sorted(coverage.items()):
        chosen = {p for _, p, _ in select([source], graph, listed, universe).files}
        misses.extend((source, t) for t in sorted(tests) if t in members and t not in chosen)
    return misses
