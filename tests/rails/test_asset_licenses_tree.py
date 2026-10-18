"""The source-tree checks split from
``tests/test_asset_licenses.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
from asset_licenses_shared import REPO_ROOT, _asset_checks


@pytest.mark.parametrize("workflow", [".github/workflows/ci.yml", ".github/workflows/release.yml"])
def test_each_workflow_checks_the_tree_and_the_web_build_it_made(workflow):
    checks = _asset_checks((REPO_ROOT / workflow).read_text(encoding="utf-8"))
    assert (False, False) in checks or (False, True) in checks, f"{workflow}: no tree check"
    built = [after_build for built_web, after_build in checks if built_web]
    assert built, f"{workflow}: no --built-web check"
    assert all(built), f"{workflow}: --built-web runs before `npm run build`"
