"""The whole-tree checks, and the calls in a test module that run one on the real tree.

A whole-tree check walks the repository and judges it: the six gates ``scripts/gate_report.py``
aggregates, ``harness validate`` and ``harness scan``, the asset-licence census, the licence
sweep, the rails census. :data:`CHECKS` names, for each, the job that owns its real-tree run, the
calls that run it, and the sources it reads the tree through. :func:`module_copies` reads one
test module with ``ast`` (never importing it) and names each test that calls one of those entry
points on the real tree without replacing every source the check reads it through.

Two readers share this one definition. ``tests/rails/test_each_tree_check_runs_in_one_place.py``
fails a suite test that does it (a copy of a job's run), and ``tools/ci/rails_census.py`` counts a
test that does it as reading the tree, so a module whose tests do is a rail.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

_GATES = "scripts.gate_report"


_CONFIG = "scripts.generate_config_baseline"


_INERT = "scripts.generate_inert_surface_baseline"


_DOCS = "scripts.generate_docs_lint_baseline"


_STRUCTURAL = "scripts.generate_structural_baseline"


_ASSETS = "scripts.check_asset_licenses"


_HARNESS = "harness.cli"


#: An entry that runs the check on the real tree whatever it is given.
ANY = "any"


#: An entry that reads the real tree when called with no arguments (an argument names a tree).
NO_ARGS = "no-args"


#: An entry that reads the tree it is handed: a real-tree run when an argument is the repository
#: root (:func:`_is_root`), or the tracked files of it.
ROOT = "root"


_LICENCE = "licence_governance_shared"


@dataclass(frozen=True)
class Check:
    """A whole-tree check: the job that owns its real-tree run, the calls that run it, and the
    sources it reads the tree through. A call runs the real tree unless, for each group of
    :attr:`sources`, the test replaces at least one of the group's names."""

    owner: str
    entries: Mapping[tuple[str, str], str]
    sources: tuple[frozenset[tuple[str, str]], ...]


def _group(module: str, *names: str) -> frozenset[tuple[str, str]]:
    return frozenset((module, name) for name in names)


_INERT_TREE = _group(_INERT, "_all_inert_surfaces", "_src_py_files", "_src_root", "_repo_root")


_DOCS_TREE = _group(_DOCS, "_tracked_files", "_tracked_md", "_repo_root")


_STRUCTURAL_TREE = _group(_STRUCTURAL, "_src_py_files", "_src_root", "_repo_root")


CHECKS: Mapping[str, Check] = {
    "gate-report": Check(
        owner="ci.yml rails-checks: python scripts/gate_report.py",
        entries={(_GATES, "run_all_gates"): ANY, (_GATES, "main"): ANY},
        sources=(
            _group(_CONFIG, "build_baseline"),
            _group(_INERT, "build_inventory") | _INERT_TREE,
            _group(_DOCS, "build_inventory") | _DOCS_TREE,
            _group(_STRUCTURAL, "build_inventory") | _STRUCTURAL_TREE,
            _group(_STRUCTURAL, "vacuity_failures") | _STRUCTURAL_TREE,
        ),
    ),
    "inert-surface": Check(
        owner="ci.yml rails-checks: python scripts/gate_report.py (the inert-surface gate)",
        entries={(_INERT, "build_inventory"): NO_ARGS, (_INERT, "build_baseline"): NO_ARGS},
        sources=(_INERT_TREE,),
    ),
    "docs-lint": Check(
        owner="ci.yml rails-checks: python scripts/gate_report.py (the docs-lint gate)",
        entries={(_DOCS, "build_inventory"): NO_ARGS, (_DOCS, "build_baseline"): NO_ARGS},
        sources=(_DOCS_TREE,),
    ),
    "structural": Check(
        owner="ci.yml rails-checks: python scripts/gate_report.py (the three structural gates)",
        entries={
            (_STRUCTURAL, name): NO_ARGS
            for name in (
                "build_inventory",
                "build_baseline",
                "vacuity_failures",
                "census_py_files",
                "census_packages",
            )
        },
        sources=(_STRUCTURAL_TREE,),
    ),
    "asset-licences": Check(
        owner="ci.yml rails-checks: python scripts/check_asset_licenses.py",
        entries={
            (_ASSETS, "violations"): NO_ARGS,
            (_ASSETS, "asset_paths"): NO_ARGS,
            (_ASSETS, "main"): ANY,
        },
        sources=(_group(_ASSETS, "REPO_ROOT"),),
    ),
    # The whole-repository readers that no job but the suite's shards ran. The licence sweep's
    # one place is now the rails job; the scanner's whole-tree scan is the next to move.
    "licence-sweep": Check(
        owner="ci.yml rails: tests/rails/test_licence_governance_tree.py",
        entries={
            **{
                (_LICENCE, name): ROOT
                for name in ("declaration_sites", "_walk", "_git_files", "cla_paths")
            },
            **{(_LICENCE, "check_no_cla_file"): ROOT},
            **{
                (_LICENCE, name): ROOT
                for name in (
                    "licence_files",
                    "check_every_declaration_is_mit",
                    "check_every_licence_file_carries_the_real_grant",
                    "check_every_licence_holder_is_a_real_holder",
                    "check_no_licence_holder_is_an_artefact_name",
                )
            },
        },
        sources=(_group(_LICENCE, "_ROOT"),),
    ),
    "harness-scanner": Check(
        owner="ci.yml rails: tests/rails/test_harness_scanner_tree.py (harness runs scan --diff)",
        entries={("harness.scanner", "scan"): ROOT},
        sources=(),
    ),
    # The census's verdict: every rail in tests/rails, and in exactly one of the rails job's
    # halves. It reads the tree it is handed, so only the root as its argument is a copy.
    "rails-census": Check(
        owner="ci.yml rails-checks: python -m tools.ci rails check",
        entries={
            ("tools.ci.rails_census", name): ROOT
            for name in ("census", "violations", "half_violations")
        },
        sources=(),
    ),
    "harness": Check(
        owner="ci.yml harness: python -m harness validate, and python -m harness scan",
        entries={(_HARNESS, "main"): "harness"},
        sources=(_group(_HARNESS, "_repo_root"),),
    ),
}


def _imports(tree: ast.Module) -> dict[str, str]:
    """Each name the module binds by import, to the dotted path it names."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


def _dotted(node: ast.AST, bound: Mapping[str, str]) -> str | None:
    """The dotted path an expression names through the module's imports, or None."""
    if isinstance(node, ast.Name):
        return bound.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, bound)
        return f"{base}.{node.attr}" if base else None
    return None


def _split(dotted: str) -> tuple[str, str]:
    module, _, name = dotted.rpartition(".")
    return module, name


def _literal_words(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
    ):
        return [e.value for e in node.elts]  # type: ignore[attr-defined]
    return None


#: Names a module binds the repository root to.
_ROOT_NAMES = frozenset({"_ROOT", "ROOT", "REPO", "_REPO", "REPO_ROOT", "_REPO_ROOT"})


#: Helpers that answer the repository root, or its tracked files, when called.
_ROOT_CALLS = frozenset({"_repo_root", "repo_root"})


_TRACKED_CALLS = frozenset({"_tracked_files", "tracked_files"})


def _is_root(node: ast.AST, local: frozenset[str]) -> bool:
    """Whether *node* is the repository root, or its tracked files: a root name, a name the
    function bound to one, a root helper's call, or the tracked files of the root."""
    if isinstance(node, ast.Name):
        return node.id in _ROOT_NAMES or node.id in local
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _ROOT_CALLS:
            return True
        if node.func.id in _TRACKED_CALLS:
            return any(_is_root(a, local) for a in node.args)
    return False


def _root_locals(fn: ast.AST) -> frozenset[str]:
    """The names a function binds to the repository root (``root = _repo_root()``)."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _is_root(node.value, frozenset()):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return frozenset(names)


def _runs_real_tree(kind: str, call: ast.Call, local: frozenset[str] = frozenset()) -> bool:
    if kind == ANY:
        return True
    if kind == NO_ARGS:
        return not call.args and not call.keywords
    if kind == ROOT:
        values = [*call.args, *(k.value for k in call.keywords)]
        return any(_is_root(v, local) for v in values)
    # ``harness``: ``main(["validate", ...])`` and ``main(["scan"])`` read the tree; ``scan
    # --diff`` reads a diff, and ``explain`` reads one spec.
    words = _literal_words(call.args[0]) if call.args else None
    if not words:
        return False
    return words[0] == "validate" or (words[0] == "scan" and "--diff" not in words)


def _patched(node: ast.Call, bound: Mapping[str, str]) -> tuple[str, str] | None:
    """What a ``monkeypatch.setattr``, ``mock.patch.object`` or ``mock.patch`` call replaces."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name in ("setattr", "object") and len(node.args) >= 2:
        target, attr = node.args[0], node.args[1]
        if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
            module = _dotted(target, bound)
            if module:
                return module, attr.value
    if name in ("setattr", "patch") and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and "." in first.value:
            return _split(first.value)
    return None


@dataclass(frozen=True)
class _Facts:
    calls: frozenset[tuple[str, tuple[str, str]]]
    patched: frozenset[tuple[str, str]]


def _facts(fn: ast.AST, bound: Mapping[str, str]) -> tuple[_Facts, set[str]]:
    """The real-tree entry calls and the patches in one function's own body, and the names of
    the module-level functions it calls."""
    calls: set[tuple[str, tuple[str, str]]] = set()
    patched: set[tuple[str, str]] = set()
    called: set[str] = set()
    local = _root_locals(fn)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        hit = _patched(node, bound)
        if hit:
            patched.add(hit)
        dotted = _dotted(node.func, bound)
        if not dotted:
            continue
        entry = _split(dotted)
        for name, check in CHECKS.items():
            kind = check.entries.get(entry)
            if kind and _runs_real_tree(kind, node, local):
                calls.add((name, entry))
    return _Facts(frozenset(calls), frozenset(patched)), called


def _tests(tree: ast.Module) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                yield node.name, node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test"):
                        yield f"{node.name}::{item.name}", item


def module_copies(rel: str, source: str) -> set[tuple[str, str]]:
    """``(check, "rel::test")`` for each test in the module that runs a check on the real tree."""
    tree = ast.parse(source)
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # A module's own functions are named by the module, as an import from it would name them.
    own = Path(rel).stem
    bound = {**{name: f"{own}.{name}" for name in helpers}, **_imports(tree)}
    found: set[tuple[str, str]] = set()
    for test, fn in _tests(tree):
        calls: set[tuple[str, tuple[str, str]]] = set()
        patched: set[tuple[str, str]] = set()
        todo = [fn]
        seen: set[str] = set()
        # A fixture the test takes runs with it: its parameters are followed as helpers too.
        todo += [helpers[a.arg] for a in fn.args.args if a.arg in helpers]
        while todo:
            current = todo.pop()
            key = getattr(current, "name", "")
            if current is not fn and key in seen:
                continue
            seen.add(key)
            facts, called = _facts(current, bound)
            calls |= facts.calls
            patched |= facts.patched
            for name in called:
                if name in helpers and name not in seen:
                    helper = helpers[name]
                    todo.append(helper)
                    todo += [helpers[a.arg] for a in helper.args.args if a.arg in helpers]
        for check, _entry in calls:
            groups = CHECKS[check].sources
            # A check with no replaceable source reads the tree it is handed: only the
            # argument the call is given (the root, above) decides.
            if not groups or not all(group & patched for group in groups):
                found.add((check, f"{rel}::{test}"))
    return found
