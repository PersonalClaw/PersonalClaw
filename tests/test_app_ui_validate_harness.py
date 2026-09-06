"""Gate the app-bundle UI validation harness's own reporting logic.

The harness (``scripts/app_ui_validate.mjs``) is the thing that decides whether an
app bundle's "driven in the real UI" clause is satisfied, so the part of it that can
quietly lie — a leg that never ran reading as green, a SKIPPED leg with no reason —
carries unit tests of its own (``scripts/lib/app_validate_report.test.mjs``, run
under ``node --test``). This module runs those from pytest so they sit inside the
same gate as everything else, and adds the two static checks that keep the browser
driver honest: it must route every status through the tested module rather than
writing its own, and it must drive every leg the module declares.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_MODULE = REPO_ROOT / "scripts" / "lib" / "app_validate_report.mjs"
REPORT_TESTS = REPO_ROOT / "scripts" / "lib" / "app_validate_report.test.mjs"
DRIVER = REPO_ROOT / "scripts" / "app_ui_validate.mjs"


def _leg_ids() -> list[str]:
    """The leg ids the report module declares, read out of its ``LEGS`` literal."""
    text = REPORT_MODULE.read_text(encoding="utf-8")
    block = text.split("export const LEGS = [", 1)[1].split("]", 1)[0]
    return re.findall(r"id:\s*'([^']+)'", block)


def test_report_module_unit_tests_pass() -> None:
    """``node --test`` over the report module's own suite."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH — the harness's JS unit tests cannot run here")
    proc = subprocess.run(
        [node, "--test", str(REPORT_TESTS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, f"node --test failed:\n{combined[-4000:]}"
    assert re.search(r"^# fail 0$", combined, re.MULTILINE) or "fail 0" in combined, combined[
        -2000:
    ]


def test_declared_legs_are_the_six_standard_ones() -> None:
    """The leg catalogue is the acceptance clause, so a silent shrink is a defect."""
    assert _leg_ids() == [
        "store-source",
        "store-card",
        "ui-install",
        "library-and-tools",
        "tool-invoke",
        "reactivate",
    ]


def test_driver_settles_every_declared_leg() -> None:
    """Every declared leg must be named by the driver — a leg nothing drives would
    always report SKIPPED/not-reached and quietly stop meaning anything."""
    driver = DRIVER.read_text(encoding="utf-8")
    missing = [leg for leg in _leg_ids() if f"'{leg}'" not in driver]
    assert not missing, f"the driver never settles these legs: {missing}"


def test_driver_owns_no_status_vocabulary_of_its_own() -> None:
    """Statuses come from the tested module. A bare ``status: 'PASS'`` in the driver
    would be a second, untested path to a green result."""
    driver = DRIVER.read_text(encoding="utf-8")
    for helper in ("passLeg", "failLeg", "skipLeg", "shapeBundleReport", "shapeReport"):
        assert helper in driver, f"the driver does not use {helper} from the report module"
    assert not re.search(r"status:\s*['\"](PASS|FAIL|SKIPPED)['\"]", driver), (
        "the driver assigns a leg status literally instead of going through the "
        "report module, which is what enforces the reason-required rule"
    )


def test_report_module_refuses_a_reasonless_skip() -> None:
    """The reason-required rule is the harness's core honesty property — assert it
    from Python too, so it cannot be lost by deleting the JS suite."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH — the harness's JS unit tests cannot run here")
    script = (
        "import {newLegs, skipLeg, finalizeLegs, bundleVerdict} "
        f"from {json.dumps(REPORT_MODULE.as_uri())};"
        "let threw=false;"
        "const legs=newLegs();"
        "try{skipLeg(legs,'tool-invoke','')}catch{threw=true}"
        "finalizeLegs(legs);"
        "console.log(JSON.stringify({threw,verdict:bundleVerdict(legs),"
        "reasons:legs.map(l=>l.reason)}))"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["threw"] is True
    assert out["verdict"] == "PARTIAL"
    assert all(r for r in out["reasons"]), "an unreached leg was left with no reason"
