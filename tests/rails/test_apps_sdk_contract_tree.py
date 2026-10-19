"""The source-tree checks split from
``tests/test_apps_sdk_contract.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to: here, which diffs plan the apps contract, and how ci.yml wires the plan to the job.
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
        ".github/workflows/ci.yml",
        # The local action the job sets its toolchain up with, which defines it as ci.yml does.
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


def _ci() -> dict:
    return yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


#: The job's whole condition: run unless the planner answered `full=false` without it, and
#: when `changes` failed too (`!cancelled()`), since its outputs are then never written.
GATED_IF = (
    "${{ !cancelled() && (needs.changes.outputs.full != 'false' || "
    "contains(fromJSON(needs.changes.outputs.jobs || '[]'), 'apps-contract')) }}"
)


def test_the_workflow_wires_the_plan_to_the_job():
    jobs = _ci()["jobs"]
    contract = jobs["apps-contract"]
    assert contract["needs"] == ["changes"]
    assert " ".join(contract["if"].split()) == GATED_IF
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
    skipped = [rel for rel in paths if "apps-contract" not in _planned(rel).jobs]
    assert not skipped, f"a diff to one of {skipped} alone skips apps-contract, which runs it"
