"""The source-tree checks split from
``tests/test_packs_fingerprint.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

from personalclaw.packs import fingerprint as fp


def test_reading_a_project_does_not_scan():
    """The negative asserted at the CALL SITE: only two functions call the scanner.

    An AST sweep over the whole package for ``scan_project(`` call sites. A read path that
    started scanning (``GET /api/projects/{id}``, a projection, a digest) would show up here as
    a third caller — which is the failure mode "on-create and on-demand only" describes, and it
    is invisible to any test that only exercises the scanner itself.
    """
    root = Path(fp.__file__).parents[1]
    callers: set[str] = set()
    for path, tree in source_index.package_trees():
        rel = path.relative_to(root).as_posix()
        # Attribute the call to its ENCLOSING FUNCTION, not just its file. Scoping this to
        # files would let a scan added to ``api_projects_get`` pass — same module as the
        # create handler — which is precisely the read path this rule forbids.
        for parent in ast.walk(tree):
            if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(parent):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name == "scan_project":
                    callers.add(f"{rel}::{parent.name}")
    # fingerprint.py itself never calls it; the only two are the on-demand route and the
    # project-create handler's proposal helper.
    assert sorted(callers) == [
        "dashboard/handlers/packs.py::api_pack_proposals",
        "tasks/hierarchy_handlers.py::_fingerprint_proposals",
    ], f"a third scan_project call site appeared: {sorted(callers)}"
