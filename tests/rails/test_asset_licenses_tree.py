"""The source-tree checks split from
``tests/test_asset_licenses.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
from asset_licenses_shared import _CHECK, REPO_ROOT, _asset_checks
from ci_catalogue_steps import read

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


def _catalogue_checks() -> list[tuple[bool, bool]]:
    """``(built_web, after_a_web_build)`` for each catalogue step that runs the check: what both
    of CI's shapes run (``tests/ci_catalogue_steps.py``), a step after its job's build."""
    checks = []
    for job in read().jobs:
        built_at = None
        for index, step in enumerate(job.command):
            line = " ".join(step.argv)
            if line == "npm run build" and built_at is None:
                built_at = index
            match = _CHECK.match(line)
            if match:
                checks.append((bool(match.group(1)), built_at is not None and built_at < index))
    return checks


@pytest.mark.parametrize("workflow", ["the catalogue", ".github/workflows/release.yml"])
def test_each_workflow_checks_the_tree_and_the_web_build_it_made(workflow):
    if workflow == "the catalogue":
        checks = _catalogue_checks()
    else:
        checks = _asset_checks((REPO_ROOT / workflow).read_text(encoding="utf-8"))
    assert (False, False) in checks or (False, True) in checks, f"{workflow}: no tree check"
    built = [after_build for built_web, after_build in checks if built_web]
    assert built, f"{workflow}: no --built-web check"
    assert all(built), f"{workflow}: --built-web runs before `npm run build`"
