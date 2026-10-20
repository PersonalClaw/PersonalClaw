"""The source-tree checks split from
``tests/test_app_quality_enforcement.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index


def test_the_verifier_has_no_gateway_call_site():
    """The verifier spawns pytest, and it is allowed to do so UNCEILINGED because it is a
    CI/CLI tool no request path can reach. That exemption is recorded in
    ``tests/test_spawn_ceiling_audit.py::_OPERATOR_EXEMPT``, and an exemption whose
    premise nobody checks is exactly this change's defect class. So pin the premise: if
    ``apps.quality`` ever gains a runtime importer, this reds and the classification has
    to be re-argued instead of quietly inherited.

    Docstrings and comments that merely NAME the module are not importers, so the scan
    is for real import statements only.
    """
    import ast

    import personalclaw as pkg

    src = Path(pkg.__file__).resolve().parent
    importers: list[str] = []
    for path, tree in source_index.package_trees():
        if path.name == "quality.py" and path.parent.name == "apps":
            continue
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                mods = [base] + [f"{base}.{a.name}" for a in node.names]
            if any(m.endswith("apps.quality") or m == "personalclaw.apps.quality" for m in mods):
                importers.append(f"{path.relative_to(src).as_posix()}:{node.lineno}")
    assert importers == [], (
        "apps.quality is now imported by runtime code, so its uncapped `subprocess.run` "
        "is reachable from the gateway. Re-argue the spawn classification (ceiling-wrap "
        f"it or justify the exemption anew) — importers: {importers}"
    )
    # Vacuity floor: the scan really walks the package and really detects an import.
    assert len(source_index.package_files()) > 100
    probe = ast.parse("from personalclaw.apps.quality import verify_app\n")
    assert any(isinstance(n, ast.ImportFrom) for n in ast.walk(probe))
