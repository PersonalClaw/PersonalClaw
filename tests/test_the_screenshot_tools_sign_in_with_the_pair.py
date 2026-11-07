"""The screenshot tools sign their browser in with the pair a browser's sign-in now needs.

``scripts/lib/named_home.mjs``'s ``signIn`` is how ``docs/screenshots/capture.mjs`` and
``docs/screenshots/baseline.mjs`` sign a Playwright context in. It carries unit tests of its own
(``scripts/lib/named_home.test.mjs``, run under ``node --test``); this module runs them from pytest
so they sit inside the suite CI runs. Red before: ``signIn`` returned nothing and kept no pair, so
every page a capture took was the signed-out screen.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGN_IN_TESTS = REPO_ROOT / "scripts" / "lib" / "named_home.test.mjs"


def test_the_sign_in_helpers_own_tests_pass() -> None:
    """``node --test`` over ``named_home.mjs``'s sign-in suite."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH — the sign-in helper's JS unit tests cannot run here")
    proc = subprocess.run(
        [node, "--test", str(SIGN_IN_TESTS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, f"node --test failed:\n{combined[-4000:]}"
    assert re.search(r"\bpass 3\b", combined), combined[-2000:]
    assert re.search(r"\bfail 0\b", combined), combined[-2000:]
