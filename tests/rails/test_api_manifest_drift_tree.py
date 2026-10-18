"""The source-tree checks split from
``tests/test_api_manifest_drift.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

import personalclaw.dashboard.server as server_mod
from personalclaw.manifest_meta import MANIFEST_EXCLUDE, canonical_route

# aiohttp route-registration verbs whose FIRST string literal arg is the path
# (``add_route`` is the exception: it's ``add_route(method, path, handler)``).
_ADD_VERBS = {
    "add_get",
    "add_post",
    "add_put",
    "add_delete",
    "add_patch",
    "add_head",
    "add_options",
    "add_static",
}


def _literal_route_paths() -> set[str]:
    """Every literal route path registered under ``dashboard/``, gathered by AST.

    Static, like the house route-handler guard: it reasons about registrations in
    source without booting the security-critical startup path. Covers ``add_get``/
    ``add_post``/… (path is arg 0), ``add_route`` (path is arg 1), and ``add_static``
    (mount prefix is arg 0).
    """
    dash_dir = Path(server_mod.__file__).parent
    paths: set[str] = set()
    for py in source_index.under(source_index.package_files(), dash_dir.resolve()):
        tree = source_index.tree(py)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            args = node.args
            if fn.attr in _ADD_VERBS and args:
                first = args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    paths.add(canonical_route(first.value))
            elif fn.attr == "add_route" and len(args) >= 2:
                p = args[1]
                if isinstance(p, ast.Constant) and isinstance(p.value, str):
                    paths.add(canonical_route(p.value))
    return paths


def _non_api(paths: set[str]) -> set[str]:
    return {p for p in paths if not (p.startswith("/api/") or p == "/api")}


def test_every_non_api_route_is_excluded():
    """Every registered non-/api route path is in MANIFEST_EXCLUDE — a new one
    can't slip in as an undocumented, silently-ignored surface."""
    undocumented = sorted(_non_api(_literal_route_paths()) - set(MANIFEST_EXCLUDE))
    assert not undocumented, (
        "Non-/api routes registered but not in MANIFEST_EXCLUDE "
        f"(add each with a reason, or serve it under /api): {undocumented}"
    )


def test_no_stale_exclusions():
    """Every MANIFEST_EXCLUDE entry matches a route that still registers — the
    exclusion allowlist can't rot into a fiction."""
    stale = sorted(set(MANIFEST_EXCLUDE) - _non_api(_literal_route_paths()))
    assert not stale, (
        "MANIFEST_EXCLUDE lists paths that no longer register " f"(remove them): {stale}"
    )
