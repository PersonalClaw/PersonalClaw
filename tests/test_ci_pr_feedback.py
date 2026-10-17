"""The lint relay that ``pr-feedback.yml`` runs, from the default branch, after CI.

``scripts/ci_pr_feedback.mjs`` reads a candidate run's artifact and comments with a token that
can write, so its own tests (``scripts/ci_pr_feedback.test.mjs``, under ``node --test`` against
a stubbed API) run here, inside the suite. The workflow tests below hold it to running that
file from the default branch and keeping the artifact out of the checkout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT_TESTS = REPO / "scripts" / "ci_pr_feedback.test.mjs"
WORKFLOW = REPO / ".github" / "workflows" / "pr-feedback.yml"


def _steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["lint-help"]["steps"]
    assert isinstance(steps, list)
    return steps


def test_the_relays_own_tests_pass() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so the relay's node tests cannot run here")
    proc = subprocess.run(
        # TAP, named, so the counts below read the same whatever node's default reporter is.
        [node, "--test", "--test-reporter=tap", str(SCRIPT_TESTS)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, combined[-4000:]
    assert re.search(r"^# fail 0$", combined, re.MULTILINE), combined[-2000:]
    passed = re.search(r"^# pass (\d+)$", combined, re.MULTILINE)
    assert passed and int(passed.group(1)) >= 9, combined[-2000:]


def test_the_workflow_runs_the_relay_from_the_default_branch() -> None:
    steps = _steps()
    checkout = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkout) == 1
    with_ = checkout[0].get("with")
    assert isinstance(with_, dict)
    assert "ref" not in with_ and "repository" not in with_, "the default branch, nothing else"
    assert with_.get("persist-credentials") is False
    assert "scripts/ci_pr_feedback.mjs" in str(with_.get("sparse-checkout"))
    scripts = [s for s in steps if str(s.get("uses", "")).startswith("actions/github-script@")]
    assert len(scripts) == 1
    script = str(scripts[0]["with"]["script"])  # type: ignore[index]
    assert "scripts/ci_pr_feedback.mjs" in script and "relay(" in script
    assert "readFileSync" not in script, "the logic lives in the tested module, not inline"


def test_the_artifact_lands_outside_the_checkout() -> None:
    download = [s for s in _steps() if "download-artifact" in str(s.get("uses", ""))]
    assert len(download) == 1
    assert str(download[0]["with"]["path"]).startswith("${{ runner.temp }}/")  # type: ignore[index]
