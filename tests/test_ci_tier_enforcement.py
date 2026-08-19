"""Every npm workspace that HAS tests must be a tier CI actually runs.

Measured gap (2026-08-18): ``desktop/`` had been an npm workspace member with a
``test`` script and 92 ``node --test`` cases since it landed, and **no workflow ran
them** — ``grep -rln desktop .github/workflows/`` found nothing, the root
``package.json`` had ``test:web`` but no ``test:desktop``, and
``scripts/run_prepush.sh`` did not run them either. The tier was enforced only by
whoever remembered to type ``npm test`` inside ``desktop/``. That includes
``desktop/test/packaging.test.js``, which is what catches an ``electron-builder``
``build.files`` list that no longer matches ``main.js``'s requires — a dmg that
crashes on launch.

The rails below make that class of gap loud instead of silent: a new workspace
member carrying a ``test`` script must also get a root ``test:<name>`` script and a
CI step that runs it, or this file reds.

Parsed from source, not executed — node is not a test dependency of the Python
suite (same convention as ``tests/test_desktop_seam.py``'s vocabulary rail).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_PKG = REPO_ROOT / "package.json"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _root_manifest() -> dict:
    return json.loads(ROOT_PKG.read_text(encoding="utf-8"))


def _tested_workspaces() -> list[str]:
    """Workspace members whose own package.json declares a ``test`` script."""
    tested = []
    for name in _root_manifest()["workspaces"]:
        manifest = REPO_ROOT / name / "package.json"
        assert manifest.is_file(), f"workspace {name!r} has no package.json"
        if json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {}).get("test"):
            tested.append(name)
    return tested


def _ci_run_commands() -> list[str]:
    """Every single-line ``run:`` command in ci.yml, whitespace-normalized.

    A deliberately dumb line scan rather than a YAML parse: PyYAML is not a
    declared test dependency, and the shape being asserted is "a step's command
    is exactly this string", which the raw line carries.
    """
    # Single-line `run:` only. Block scalars (`run: |`) are the multi-command steps;
    # none of the tier invocations below live in one, and the vacuity floor proves
    # the scan still sees the real steps.
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        m = re.match(r"\s*run:\s*(\S.*?)\s*$", line)
        if m and m.group(1) not in {"|", ">", ">-", "|-"}:
            out.append(m.group(1))
    return out


def test_the_run_command_scan_is_not_vacuous():
    """Vacuity floor: if the scan below matched nothing, every rail here would pass.

    ``npm run test:web`` and ``uv run pytest`` are both long-standing single-line
    steps on main, so their absence means the scan broke, not that CI changed.
    """
    commands = _ci_run_commands()
    assert len(commands) >= 10, f"only {len(commands)} run: commands found — scan broke"
    assert "npm run test:web" in commands
    assert "uv run pytest" in commands


def test_every_tested_workspace_has_a_root_test_script():
    """npm runs from the REPO ROOT (single-root lockfile, npm/cli#4828).

    So a workspace's tests are only reachable in CI through a root
    ``test:<name>`` script — there is no ``cd desktop && npm ci``.
    """
    tested = _tested_workspaces()
    # Vacuity floor: both known tiers must be discovered, or the walk found nothing.
    assert set(tested) >= {"web", "desktop"}, f"workspace discovery broke: {tested}"

    scripts = _root_manifest()["scripts"]
    for name in tested:
        key = f"test:{name}"
        assert key in scripts, (
            f"{name}/package.json declares a `test` script but the root package.json "
            f"has no `{key}` — nothing in CI can reach that tier."
        )
        assert f"--workspace={name}" in scripts[key], (
            f"root script `{key}` must run the tier via `--workspace={name}` "
            f"(npm runs from the repo root), got: {scripts[key]!r}"
        )


def test_ci_runs_every_tested_workspace_tier():
    """A root script nobody invokes is not enforcement."""
    commands = _ci_run_commands()
    for name in _tested_workspaces():
        expected = f"npm run test:{name}"
        assert expected in commands, (
            f"no ci.yml step runs `{expected}` — {name}/'s tests are enforced only by "
            f"whoever remembers to run them locally."
        )
