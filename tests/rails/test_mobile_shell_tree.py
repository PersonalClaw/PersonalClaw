"""The source-tree checks split from
``tests/test_mobile_shell.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import json

from ci_catalogue_steps import read
from mobile_shell_shared import MOBILE, REPO_ROOT


def test_the_mobile_tier_is_wired_the_way_the_ci_rail_requires():
    """A companion to ``tests/rails/test_ci_tier_enforcement.py``, stated from this change's side.

    That file enforces the general rule; this one records that ``mobile`` is deliberately a
    workspace member *because* of it. Keeping the shell outside the workspace graph would have
    kept 37 node cases out of every gate — the exact gap that rail was written to close.
    """
    root = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert "mobile" in root["workspaces"]
    assert root["scripts"]["test:mobile"] == "npm run test --workspace=mobile"
    manifest = json.loads((MOBILE / "package.json").read_text(encoding="utf-8"))
    assert manifest["scripts"]["test"] == "node --test test/*.test.mjs"
    # The native builds are NOT a CI tier: they need Xcode and the Android SDK.
    # Read from the catalogue, which both of CI's shapes run (tests/ci_catalogue_steps.py).
    commands = [" ".join(s.argv) for job in read().jobs for s in job.command]
    assert "npm run test:mobile" in commands
    assert not [
        c for c in commands if "cap build" in c
    ], "a native build in CI would need Xcode and the Android SDK"
