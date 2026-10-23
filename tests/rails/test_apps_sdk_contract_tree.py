"""The source-tree checks split from
``tests/test_apps_sdk_contract.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to: here, which diffs plan the apps contract, and how merge.yml runs the planned job.
"""

from __future__ import annotations

import posixpath

import local_actions
import pytest
import yaml
from apps_sdk_contract_shared import REPO

from tools.ci import catalogue
from tools.ci import plan as planner

_CAT = catalogue.read(REPO)
_AREAS = catalogue.read_areas(REPO, _CAT)


def _planned(*paths: str) -> planner.Plan:
    return planner.plan(_CAT, "merge", list(paths), overrides=_AREAS, root=REPO)


@pytest.mark.parametrize(
    "path",
    [
        "src/personalclaw/sdk/channel.py",
        "src/personalclaw/sdk/signatures.json",
        "scripts/apps_sdk_contract.py",
        "scripts/sdk_signature_snapshot.py",
        # The workflow that runs the job, and the local action it sets its toolchain up with:
        # either defines the job's run, so any workflow plans the whole tier (areas.toml).
        ".github/workflows/merge.yml",
        ".github/actions/setup/action.yml",
    ],
)
def test_a_path_that_can_change_the_sdk_plans_the_job(path):
    assert "apps-contract" in _planned(path).jobs


@pytest.mark.parametrize("path", ["docs/guides/x.md", "tests/t.py", "README.md"])
def test_a_known_path_outside_the_sdk_leaves_the_job_out(path):
    """`context.py` is not among them yet: a path only a whole-tree input reaches is unknown and
    plans the whole tier, apps-contract included, until a narrower input names it."""
    plan = _planned(path)
    assert not plan.full and "apps-contract" not in plan.jobs, plan.full_reasons


def test_an_unenumerated_diff_plans_the_job():
    assert "apps-contract" in _planned().jobs


def _merge() -> dict:
    return yaml.safe_load((REPO / ".github/workflows/merge.yml").read_text(encoding="utf-8"))


def test_the_workflow_runs_the_planned_job_against_the_partner():
    """merge.yml's stage job runs each planned entry through run-job, so the plan alone decides
    whether apps-contract runs. Its catalogue entry runs the contract against the base with the
    apps' dependencies, and reads the apps partner, which the stage job checks out at the head
    `changes` published."""
    job = _CAT.job("apps-contract")
    words = [w for step in job.command for w in step.argv]
    assert "scripts/apps_sdk_contract.py" in words
    assert "--base-ref" in words and "--install-deps" in words
    assert "apps-partner" in job.external_inputs
    stage = _merge()["jobs"]["test"]
    partner = [
        s
        for s in stage["steps"]
        if (s.get("with") or {}).get("repository") == "PersonalClaw/PersonalClawApps"
    ]
    assert len(partner) == 1 and "apps-partner" in str(partner[0].get("if"))
    assert "needs.changes.outputs.apps-partner" in str(partner[0]["with"].get("ref"))


def test_every_local_action_the_job_calls_runs_it():
    """The job runs its own steps and those of each local action it calls, and of each one that
    action calls, so a change to the check is checked by it: a diff to any file of such an
    action alone runs the job, as a diff to merge.yml does."""
    steps = _merge()["jobs"]["test"].get("steps") or []
    folders = local_actions.called(steps, local_actions.actions_at(REPO))
    assert folders, "apps-contract calls no local action, so this check read nothing"
    # The action's file under each name GitHub reads it from, and a file beside it its steps
    # may run.
    paths = [
        posixpath.normpath(f"{folder}/{name}")
        for folder in folders
        for name in (*local_actions.ACTION_FILES, "run.sh")
    ]
    skipped = [rel for rel in paths if "apps-contract" not in _planned(rel).jobs]
    assert not skipped, f"a diff to one of {skipped} alone skips apps-contract, which runs it"
