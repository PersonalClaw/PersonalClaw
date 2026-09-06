"""Gate the app-bundle UI validation harness's own reporting logic.

The harness (``scripts/app_ui_validate.mjs``) is the thing that decides whether an
app bundle's "driven in the real UI" clause is satisfied, so the part of it that can
quietly lie — a leg that never ran reading as green, a SKIPPED leg with no reason —
carries unit tests of its own (``scripts/lib/app_validate_report.test.mjs``, run
under ``node --test``). This module runs those from pytest so they sit inside the
same gate as everything else, and adds the static checks that keep the browser
driver honest: it must route every status through the tested module rather than
writing its own, it must drive every leg the module declares, and it must not go
back to locating a tool's argument fields by ``name`` attribute — a selector that
matched nothing, ran every tool with EMPTY arguments, and failed the leg with the
tool's own "needs an X" error attributed to the BUNDLE. The behavioural pin for
that fill path renders the real form and drives the real function
(``web/src/pages/tools/harnessFillsToolArgs.test.tsx``); these are the cheap
source-level rails beside it.
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
FORM_MODULE = REPO_ROOT / "scripts" / "lib" / "app_validate_form.mjs"
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


def test_driver_locates_tool_arguments_by_accessible_name() -> None:
    """A tool argument must be found by the name the inspector RENDERS.

    ``SchemaField`` emits no ``name`` attribute — it binds ``<label htmlFor>`` to a
    React ``useId()``. An interpolated ``[name="${key}"]`` selector therefore matched
    nothing, the fill was skipped, and the tool ran with no arguments at all. Because
    the tool then reported its own missing-argument error, the resulting FAIL looked
    exactly like a real bundle defect.
    """
    driver = DRIVER.read_text(encoding="utf-8")
    form = FORM_MODULE.read_text(encoding="utf-8")
    assert "fillRequiredArgs" in driver, (
        "the driver must fill tool arguments through scripts/lib/app_validate_form.mjs, "
        "which is the part pinned against the real rendered form"
    )
    # An interpolated name selector is the defect's signature. A LITERAL one is fine:
    # `input[name="app-local-source"]` targets a field that really does set `name`.
    offenders = [
        line.strip()
        for line in (driver + form).splitlines()
        if re.search(r'\[name="\$\{', line) and not line.lstrip().startswith(("//", "*", "/*"))
    ]
    assert not offenders, f"a tool argument is being located by name attribute again: {offenders}"
    assert "getByLabel" in form, "the fill path must resolve fields by accessible name"


def test_driver_blocks_the_leg_when_an_argument_could_not_be_entered() -> None:
    """Running the tool anyway is what misattributed the failure to the bundle."""
    driver = DRIVER.read_text(encoding="utf-8")
    block = driver.split("const { args, unfilled } = await fillRequiredArgs", 1)
    assert len(block) == 2, "the driver no longer collects unfilled required arguments"
    after = block[1][:800]
    assert "unfilled.length" in after and "'blocked'" in after, (
        "the driver must return a BLOCKED status when it could not enter a required "
        "argument, instead of invoking the tool with arguments it never typed"
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
