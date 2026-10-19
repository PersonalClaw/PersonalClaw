"""The source-tree checks split from
``tests/test_apps_sdk_contract.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import posixpath

import local_actions
import pytest
import yaml
from apps_sdk_contract_shared import REPO

from scripts import ci_touches_sdk


@pytest.mark.parametrize(
    "path",
    [
        "src/personalclaw/sdk/channel.py",
        "src/personalclaw/sdk/signatures.json",
        "scripts/apps_sdk_contract.py",
        "scripts/sdk_signature_snapshot.py",
        ".github/workflows/ci.yml",
        # The local action the job sets its toolchain up with, which defines it as ci.yml does.
        ".github/actions/setup/action.yml",
    ],
)
def test_a_path_that_can_change_the_sdk_runs_the_job(path):
    assert ci_touches_sdk.touches_sdk([path])


def _ci() -> dict:
    return yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_the_workflow_wires_the_verdict_to_the_job():
    jobs = _ci()["jobs"]
    assert jobs["changes"]["outputs"]["sdk"] == "${{ steps.sdk.outputs.sdk }}"
    step = next(s for s in jobs["changes"]["steps"] if s.get("id") == "sdk")
    assert (
        "scripts/ci_touches_sdk.py" in step["run"] and 'verdict="sdk=true"' in step["run"]
    ), "the verdict must fall back to RUNNING the job when the classifier gives no answer"
    contract = jobs["apps-contract"]
    assert contract["needs"] == ["changes"]
    assert "needs.changes.outputs.sdk == 'true'" in contract["if"]
    runs = " ".join(str(s.get("run", "")) for s in contract["steps"])
    assert "scripts/apps_sdk_contract.py" in runs
    assert "--base-ref" in runs and "--install-deps" in runs
    repos = [s.get("with", {}).get("repository") for s in contract["steps"]]
    assert "PersonalClaw/PersonalClawApps" in repos


def test_every_local_action_the_job_calls_runs_it():
    """The job runs its own steps and those of each local action it calls, and of each one that
    action calls, so a change to the check is checked by it: a diff to any file of such an
    action alone runs the job, as a diff to ci.yml does."""
    steps = _ci()["jobs"]["apps-contract"].get("steps") or []
    folders = local_actions.called(steps, local_actions.actions_at(REPO))
    assert folders, "apps-contract calls no local action, so this check read nothing"
    # The action's file under each name GitHub reads it from, and a file beside it its steps
    # may run.
    paths = [
        posixpath.normpath(f"{folder}/{name}")
        for folder in folders
        for name in (*local_actions.ACTION_FILES, "run.sh")
    ]
    skipped = [rel for rel in paths if not ci_touches_sdk.touches_sdk([rel])]
    assert not skipped, f"a diff to one of {skipped} alone skips apps-contract, which runs it"
