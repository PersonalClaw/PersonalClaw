"""The source-tree checks split from
``tests/test_api_version_one_origin.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import source_index
from api_version_one_origin_shared import ORIGIN_TS, REPO, SRC

ORIGIN_PY = SRC / "api_version.py"


GATE_PY = SRC / "dashboard" / "api_version_gate.py"


#: A name is an api-version binding when it mentions "api version" in any casing
#: or separator style. Deliberately broad: the rail's job is to catch a *new*
#: spelling of the same number, and a near-miss name is exactly how one hides.
_NAME_RE = re.compile(r"api_?version", re.IGNORECASE)


#: The only two api-version names allowed to be bound to an integer literal, and
#: the only file allowed to bind them.
ALLOWED_PY_BINDINGS = {"API_VERSION", "MIN_SUPPORTED_API_VERSION"}


ALLOWED_TS_BINDINGS = {"CLIENT_API_VERSION"}


def _py_files() -> list[Path]:
    """Every package module, from the suite's one source index."""
    return list(source_index.package_files())


def _ts_files() -> list[Path]:
    return [
        p
        for p in source_index.web_files()
        if p.suffix in {".ts", ".tsx"} and "node_modules" not in p.parts
    ]


def _int_literal_bindings(tree: ast.AST) -> list[tuple[str, int]]:
    """Every ``NAME = <int>`` / ``NAME: T = <int>`` in a module, at any nesting."""
    out: list[tuple[str, int]] = []

    def _record(target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Constant):
            return
        if isinstance(value.value, bool) or not isinstance(value.value, int):
            return
        out.append((target.id, value.value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _record(t, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            _record(node.target, node.value)
    return out


class TestSingleOriginPython:
    def test_only_api_version_py_binds_an_api_version_literal(self):
        offenders: list[str] = []
        for path in _py_files():
            tree = source_index.tree(path)
            for name, value in _int_literal_bindings(tree):
                if not _NAME_RE.search(name):
                    continue
                if path == ORIGIN_PY and name in ALLOWED_PY_BINDINGS:
                    continue
                offenders.append(f"{path.relative_to(REPO)}: {name} = {value}")
        assert not offenders, (
            "an api-version integer literal is bound outside its one origin "
            f"({ORIGIN_PY.relative_to(REPO)}): " + "; ".join(offenders)
        )

    def test_the_origin_binds_both_window_ends_and_nothing_else(self):
        bound = {
            name
            for name, _ in _int_literal_bindings(source_index.tree(ORIGIN_PY))
            if _NAME_RE.search(name)
        }
        assert bound == ALLOWED_PY_BINDINGS


class TestSingleOriginTypeScript:
    def test_only_api_version_ts_binds_an_api_version_literal(self):
        # `=` covers `const CLIENT_API_VERSION = 1`; `:` covers an object-literal
        # `apiVersion: 1`. Neither matches the `apiVersion: number` type in the
        # `Manifest` interface, which carries no digit.
        pat = re.compile(r"([A-Za-z_$][\w$]*)\s*(?::\s*number)?\s*[:=]\s*(\d+)")
        offenders: list[str] = []
        for path in _ts_files():
            for lineno, line in enumerate(source_index.text(path).splitlines(), 1):
                for name, value in pat.findall(line):
                    if not _NAME_RE.search(name):
                        continue
                    if path == ORIGIN_TS and name in ALLOWED_TS_BINDINGS:
                        continue
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {name} = {value}")
        assert not offenders, (
            "an api-version literal is bound outside the SPA's one declaration file "
            f"({ORIGIN_TS.relative_to(REPO)}); import CLIENT_API_VERSION instead: "
            + "; ".join(offenders)
        )


class TestSingleChokepoint:
    def test_negotiate_has_exactly_one_non_test_caller(self):
        callers: list[str] = []
        for path in _py_files():
            tree = source_index.tree(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = (
                        fn.id
                        if isinstance(fn, ast.Name)
                        else fn.attr if isinstance(fn, ast.Attribute) else ""
                    )
                    if name == "negotiate":
                        callers.append(str(path.relative_to(REPO)))
        assert callers == [str(GATE_PY.relative_to(REPO))], (
            "the version comparison must happen at ONE chokepoint; a second "
            f"comparison site is the defect this rail keeps out. Callers: {callers}"
        )

    def test_the_gate_holds_no_version_literal_of_its_own(self):
        bound = [
            name
            for name, _ in _int_literal_bindings(source_index.tree(GATE_PY))
            if _NAME_RE.search(name)
        ]
        assert bound == []


class TestBumpRuleLivesInOnePlace:
    def test_no_other_module_restates_it(self):
        # Two statements of a rule are two rules. Other modules may POINT at the
        # origin; none may re-derive what counts as a breaking wire change.
        offenders = []
        for path in _py_files():
            if path == ORIGIN_PY:
                continue
            text = path.read_text(encoding="utf-8")
            if "Do NOT bump for" in text or "Bump :data:`API_VERSION` when" in text:
                offenders.append(str(path.relative_to(REPO)))
        assert not offenders, f"the bump rule is restated outside its origin: {offenders}"
