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

import pytest
from ci_pr_feedback_shared import REPO

SCRIPT_TESTS = REPO / "scripts" / "ci_pr_feedback.test.mjs"


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
