"""The source-tree checks split from
``tests/test_nix_channel.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import yaml
from nix_channel_shared import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nix.yml"


def _workflow() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def test_the_workflow_runs_on_every_file_that_can_move_a_bound() -> None:
    """The channel broke because a bound in pyproject.toml and uv.lock shipped before anything
    built the flake against it; a path dropped from these lists is a change that ships unbuilt."""
    triggers = _workflow().get("on") or _workflow()[True]
    for event in ("pull_request", "push"):
        paths = set(triggers[event]["paths"])
        assert {"flake.nix", "flake.lock", "nix/**", "pyproject.toml", "uv.lock"} <= paths, event
        assert {"scripts/nix_pins.py", "scripts/nix_smoke.py"} <= paths, event
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["schedule"], "nothing rebuilds the channel when the tree does not move"


def test_the_workflow_builds_runs_checks_and_asks_about_the_next_release() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {}
    (job,) = workflow["jobs"].values()
    assert job["permissions"] == {}, "the installer writes the job's token where the gateway reads"
    assert job["runs-on"] == "ubuntu-24.04"
    assert isinstance(job["timeout-minutes"], int)
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    for command in (
        "nix flake check --print-build-logs",
        "nix flake check --all-systems --no-build",
        "python3 scripts/nix_pins.py --check",
        "--file nix/next-release.nix",
    ):
        assert command in runs, command
