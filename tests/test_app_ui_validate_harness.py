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


def test_declared_legs_are_the_seven_standard_ones() -> None:
    """The leg catalogue is the acceptance clause, so a silent shrink is a defect."""
    assert _leg_ids() == [
        "store-source",
        "store-card",
        "ui-install",
        "library-and-tools",
        "tool-invoke",
        "reactivate",
        # Issue #2588. Deactivate is strictly weaker than removal, so with the first six
        # legs alone nothing in the harness could distinguish an app removed with its data
        # preserved from one removed and wiped from one merely switched off — and
        # "data survives removal" was therefore provable only by a bespoke hand-written
        # script that nothing else could reuse.
        "uninstall-preserves-data",
    ]


def test_the_removal_leg_reads_back_through_the_registry_not_the_filesystem() -> None:
    """The substitution this leg exists to refuse, pinned as source-level rails.

    A removal check written with ``Path.exists()`` proves a FILE is on disk; it does not
    prove the provider still resolves and still answers with the user's data. On 2026-09-06
    those two questions gave different answers for the same app, which is the whole reason
    the leg's read-back goes through ``/api/tools`` and the tool inspector.
    """
    driver = DRIVER.read_text(encoding="utf-8")
    leg = driver.split("async function uninstallPreservesData", 1)
    assert len(leg) == 2, "the driver no longer carries the removal leg"
    # Bounded to the leg's own body: reading to end of file would sweep in the model-wiring
    # and main() helpers, and an `existsSync` THERE is not this leg probing the filesystem.
    body = leg[1].split("// ── model wiring", 1)[0]
    assert len(body) < len(
        leg[1]
    ), "the removal leg is no longer followed by the model-wiring section"
    assert "waitForToolInRegistry" in body, (
        "the removal leg does not re-resolve the app's tool through the gateway's registry "
        "after reinstalling, so it cannot tell a restored provider from a restored directory"
    )
    assert "runToolFromUi" in body, (
        "the removal leg does not read the data back through the app's own tool, which is "
        "the only surface that can answer whether the data is READABLE"
    )
    # A filesystem probe in the driver would be the defect itself, under any spelling.
    assert "existsSync" not in body, (
        "the removal leg probes the filesystem — that proves a path exists, not that the "
        "provider survived removal"
    )
    # Both arms, because a preservation check that only shows preservation cannot tell a
    # working preserve from a broken wipe.
    assert "rung: 'keep-data'" in body and "rung: 'force'" in body, (
        "the removal leg drives only one removal rung; the force arm is what keeps the "
        "preserve arm from passing vacuously"
    )


def test_the_removal_leg_clicks_the_real_control_and_its_confirm_dialog() -> None:
    """The browser is the only thing that can cover the dialog, which nothing covered."""
    driver = DRIVER.read_text(encoding="utf-8")
    block = driver.split("async function clickRemovalInLibrary", 1)
    assert len(block) == 2, "the driver no longer clicks removal from the Library"
    body = block[1][:4000]
    assert 'role="dialog"' in body, "the removal leg never waits for the confirm dialog"
    assert "dialog.getByRole('button'" in body, (
        "the confirm click is not scoped to the dialog — an unscoped lookup can re-click "
        "the panel's own control and never confirm anything"
    )
    assert "Force uninstall" in body and "'Advanced'" in body, (
        "the force rung must be reached through the Advanced expander a user opens, not by "
        "calling the endpoint behind it"
    )


def test_the_three_data_states_are_classified_in_the_tested_module() -> None:
    """``absent`` / ``empty`` / ``present`` are three promises, and this repo has
    collapsed the first two into one nine times. The discrimination therefore lives in
    the unit-tested module, not in browser plumbing only a live gateway can exercise."""
    report = REPORT_MODULE.read_text(encoding="utf-8")
    assert "export function classifyAppData" in report
    for state in ("absent", "empty", "present", "blocked", "unknown"):
        assert f"'{state}'" in report, f"the data-state vocabulary is missing {state!r}"
    driver = DRIVER.read_text(encoding="utf-8")
    assert "classifyAppData" in driver, (
        "the driver classifies the data states itself instead of going through the tested "
        "module, which is where the reason-required rule is enforced"
    )
    assert "APP_DATA.PRESENT" in driver, (
        "the driver must gate its preservation claim on the one observable state; a skip "
        "for the other states is the absence of an answer, not a pass"
    )


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
