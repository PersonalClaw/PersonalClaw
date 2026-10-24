"""The source-tree checks split from
``tests/test_spawn_env_audit.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import pytest
import source_index
from rails_repo import REPO
from spawn_ceiling_audit_shared import _SPAWN_CALLEES, _callee, _census, _normalize, _src_root
from spawn_env_audit_shared import _BUILT, _GATEWAY_ENV, _PASS_THROUGH, _called_name, env_source


def _builders(trees: dict[str, ast.Module]) -> set[str]:
    """``build_child_env`` and every function named ``*env`` that reaches it (a fixpoint)."""
    calls: dict[str, set[str]] = {}
    for tree in trees.values():
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls.setdefault(fn.name, set()).update(
                    _called_name(c) for c in ast.walk(fn) if isinstance(c, ast.Call)
                )
    found = {"build_child_env"}
    grew = True
    while grew:
        grew = False
        for name, called in calls.items():
            if name not in found and name.endswith("env") and called & found:
                found.add(name)
                grew = True
    return found


def _trees() -> dict[str, ast.Module]:
    """Every module of the package, parsed, from the suite's one source index."""
    root = _src_root()
    return {path.relative_to(root).as_posix(): tree for path, tree in source_index.package_trees()}


def _sources(trees: dict[str, ast.Module]) -> dict[str, list[tuple[int, str]]]:
    """``file::qualname::callee`` → ``[(line, env source), …]`` for every spawn site."""
    builders = _builders(trees)
    out: dict[str, list[tuple[int, str]]] = {}
    for rel, tree in trees.items():

        class V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.q: list[str] = []
                self.funcs: list[ast.AST] = []

            def visit_FunctionDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.funcs.append(n)
                self.generic_visit(n)
                self.funcs.pop()
                self.q.pop()

            visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

            def visit_ClassDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.generic_visit(n)
                self.q.pop()

            def visit_Call(self, n: ast.Call) -> None:
                c = _normalize(_callee(n))
                if c in _SPAWN_CALLEES:
                    key = f"{rel}::{'.'.join(self.q) or '<module>'}::{c}"
                    func = self.funcs[-1] if self.funcs else None
                    out.setdefault(key, []).append((n.lineno, env_source(n, func, builders)))
                self.generic_visit(n)

        V().visit(tree)
    return out


def test_the_classification_is_what_the_code_does():
    """A built site passes `env=` from the builder; a gateway-env site does not; a pass-through
    seam leaves the choice to its caller."""
    sources = _sources(_trees())
    wrong: list[str] = []
    for key, sites in sorted(sources.items()):
        for line, source in sites:
            where = f"{key.split('::')[0]}:{line}"
            if key in _BUILT and source != "built":
                wrong.append(f"  {where} is in _BUILT but its env is {source}: {key}")
            elif key in _GATEWAY_ENV and source == "built":
                wrong.append(f"  {where} builds its env now; move it to _BUILT: {key}")
            elif key in _PASS_THROUGH and source not in {"passthrough", "inherited"}:
                wrong.append(f"  {where} chooses its own env ({source}); classify it: {key}")
    assert not wrong, "Spawn env classification disagrees with the code:\n" + "\n".join(wrong)


def test_the_builders_are_found_in_the_tree():
    """The wrappers the built sites go through are recognised, so a built site is not vacuous."""
    builders = _builders(_trees())
    for name in ("git_env", "guarded_git_env", "_git_env", "app_packages_env", "stdio_spawn_env"):
        assert name in builders, name
    assert "installer_env" not in builders  # a copy of the gateway's environment


@pytest.mark.timeout(40)
def test_the_census_is_the_ceiling_audits():
    """Same universe as the ceiling audit, so a new spawn is classified in both."""
    assert set(_sources(_trees())) == set(_census())
    assert (REPO / "tests" / "test_spawn_ceiling_audit.py").is_file()


def test_every_spawn_site_says_where_its_environment_comes_from():
    """Every spawn site is in exactly one class; a new or unmapped site reds by name."""
    census = set(_census())
    classified = set(_BUILT) | set(_GATEWAY_ENV) | set(_PASS_THROUGH)
    unmapped = sorted(census - classified)
    assert not unmapped, (
        "Unclassified spawn site(s). Give the child the allowlist (`env=sandbox.build_child_env("
        "site=...)`) and add it to _BUILT, or say in _GATEWAY_ENV why it may have the gateway's "
        "environment (tests/test_spawn_env_audit.py):\n" + "\n".join(f"  {k}" for k in unmapped)
    )
    stale = sorted(classified - census)
    assert not stale, "No such spawn site any more; remove:\n" + "\n".join(f"  {k}" for k in stale)
    counts = [len(set(_BUILT) & set(_GATEWAY_ENV)), len(set(_BUILT) & set(_PASS_THROUGH))]
    assert counts == [0, 0] and not set(_GATEWAY_ENV) & set(_PASS_THROUGH)
