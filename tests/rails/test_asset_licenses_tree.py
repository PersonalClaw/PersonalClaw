"""The source-tree checks split from
``tests/test_asset_licenses.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
from asset_licenses_shared import REPO_ROOT, _asset_checks

from scripts import check_asset_licenses as assets


def test_the_asset_census_is_not_vacuous():
    paths = assets.asset_paths()
    assert len(paths) > 200, f"only {len(paths)} assets found; the census is broken"
    for expected in (
        "desktop/icon.png",
        "src/personalclaw/tests_fixtures/empty/fixture.yaml",
        "tests/fixtures/word_authored.docx",
        "web/public/claw.svg",
        "web/public/fonts/dm-sans.woff2",
    ):
        assert expected in paths


@pytest.mark.parametrize("workflow", [".github/workflows/ci.yml", ".github/workflows/release.yml"])
def test_each_workflow_checks_the_tree_and_the_web_build_it_made(workflow):
    checks = _asset_checks((REPO_ROOT / workflow).read_text(encoding="utf-8"))
    assert (False, False) in checks or (False, True) in checks, f"{workflow}: no tree check"
    built = [after_build for built_web, after_build in checks if built_web]
    assert built, f"{workflow}: no --built-web check"
    assert all(built), f"{workflow}: --built-web runs before `npm run build`"
