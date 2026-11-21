"""Every release-verify leg ends in one verdict, and the workflow listens to three triggers.

`release-verify.yml` (#4330, plan item 2) verifies what a user DOWNLOADS from a published
release. Its failure mode is not a red check — it is a leg that verifies NOTHING and says
nothing, which on the board is indistinguishable from a release that was proven good. The
rails here pin the two properties that make that failure impossible:

1. every leg job either writes a verdict through `tools/ci/release_verdict.py` and bounds itself
   with `timeout-minutes`, or delegates to a workflow that writes one — the wheel leg calls
   `clean-machine-walkthroughs.yml`, and that job's own bound and verdict are asserted here
   too, because a reusable-call job has no steps (and no `timeout-minutes`) of its own;
2. the workflow fires exactly where #4330 says: the Release workflow's completion, a manual
   dispatch carrying a tag, and the weekly schedule.

The first check derives its subjects from the jobs that EXIST rather than from a list, so a
planted leg — a new job with neither a bound nor a verdict — fails it by construction. The
vocabulary itself (passed / failed / incomplete) lives in `tools/ci/release_verdict.py`; the
write is exercised through the CLI, the way every leg calls it.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys

import yaml
from rails_repo import REPO

REPO_ROOT = REPO
RELEASE_VERIFY = REPO_ROOT / ".github" / "workflows" / "release-verify.yml"
WALKTHROUGHS = REPO_ROOT / ".github" / "workflows" / "clean-machine-walkthroughs.yml"
VERDICT_PY = REPO_ROOT / "tools" / "ci" / "release_verdict.py"

#: Jobs that are structure rather than legs: `resolve` decides WHAT to verify, and `report`
#: keeps the issue. Every other job must carry a bound and a verdict.
NON_LEG_JOBS = frozenset({"resolve", "report"})

#: The legs `report` reads back, by artifact name. A leg renamed in the workflow without this
#: list following would read as "no verdict was written" on a release that was verified.
REPORTED_LEGS = ("v1-wheel", "images-amd64", "images-arm64", "desktop-linux", "desktop-mac")


def _load(path: pathlib.Path) -> dict:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path} is not a workflow"
    return workflow


def _leg_problems(workflow: dict) -> list[str]:
    """Everything wrong with a leg job, as a list — empty means the workflow holds.

    Returned rather than asserted so the planted-leg case can reuse THE SAME judgement the
    real workflow passes through.
    """
    problems: list[str] = []
    triggers = workflow.get("on") or workflow.get(True) or {}
    for trigger in ("workflow_run", "workflow_dispatch", "schedule"):
        if trigger not in triggers:
            problems.append(f"trigger missing: {trigger}")
    jobs = workflow.get("jobs") or {}
    for name, job in jobs.items():
        if name in NON_LEG_JOBS or not isinstance(job, dict):
            continue
        if "uses" in job:
            # A delegated leg (the wheel calls the walkthroughs); its bound and verdict live
            # with the callee and are asserted in test_the_wheel_leg... below.
            continue
        if "timeout-minutes" not in job:
            problems.append(f"leg without timeout-minutes: {name}")
        steps = job.get("steps") or []
        runs = " ".join(str(step.get("run", "")) for step in steps if isinstance(step, dict))
        if "tools/ci/release_verdict.py" not in runs:
            problems.append(f"leg never writes a verdict: {name}")
    return problems


def test_every_release_verify_leg_writes_a_verdict() -> None:
    workflow = _load(RELEASE_VERIFY)
    assert _leg_problems(workflow) == []

    # The planted legs, both shapes of the hole: no bound at all, and a bound but no verdict.
    planted = (
        {"runs-on": "ubuntu-24.04", "steps": [{"run": "echo planted"}]},
        {"runs-on": "ubuntu-24.04", "timeout-minutes": 10, "steps": [{"run": "echo planted"}]},
    )
    for leg in planted:
        mutated = copy.deepcopy(workflow)
        mutated["jobs"]["planted-leg"] = leg
        assert _leg_problems(mutated) != [], f"a planted leg passed: {leg}"


def test_the_wheel_leg_delegates_and_the_walkthroughs_carry_its_verdict() -> None:
    workflow = _load(RELEASE_VERIFY)
    wheel = workflow["jobs"]["wheel"]
    assert "clean-machine-walkthroughs.yml" in wheel["uses"]
    assert wheel["with"]["which"] == "v1-wheel"
    # The version is pinned by `resolve` — an unpinned call would silently verify the newest
    # release instead of the one whose run just finished.
    assert wheel["with"]["version"] == "${{ needs.resolve.outputs.version }}"

    walkthroughs = _load(WALKTHROUGHS)
    job = walkthroughs["jobs"]["v1-wheel"]
    assert "timeout-minutes" in job, "the delegated leg's bound lives here, not on the call"
    runs = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert (
        "tools/ci/release_verdict.py" in runs
    ), "the wheel leg's verdict must be written by the walkthroughs"
    # And it must reach the report: the verdict leaves the job as an artifact.
    uploads = [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert any(step.get("with", {}).get("name") == "verdict-v1-wheel" for step in uploads)


def test_the_report_reads_back_every_leg() -> None:
    workflow = _load(RELEASE_VERIFY)
    report = workflow["jobs"]["report"]
    runs = " ".join(str(step.get("run", "")) for step in report["steps"])
    for leg in REPORTED_LEGS:
        assert f'"{leg}"' in runs, f"the report does not read back the {leg} verdict"


def _run_verdict(
    tmp_path: pathlib.Path, *args: str, summary: bool = False
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    if summary:
        env["GITHUB_STEP_SUMMARY"] = str(tmp_path / "summary.md")
        (tmp_path / "summary.md").write_text("", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(VERDICT_PY), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_the_verdict_writer_records_the_word_the_reason_and_the_summary(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_verdict(
        tmp_path,
        "--leg",
        "images",
        "--verdict",
        "passed",
        "--reason",
        "the gateway image pulls",
        "--release",
        "0.2.0",
        "--out-dir",
        str(tmp_path / "verdict"),
        summary=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "verdict" / "verdict.json").read_text(encoding="utf-8"))
    assert payload == {
        "leg": "images",
        "verdict": "passed",
        "reason": "the gateway image pulls",
        "release": "0.2.0",
    }
    # The job summary is the human surface; a verdict that skips it hides from the run page.
    assert "**images**: passed" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_a_word_outside_the_vocabulary_is_refused(tmp_path: pathlib.Path) -> None:
    result = _run_verdict(
        tmp_path,
        "--leg",
        "images",
        "--verdict",
        "green",
        "--reason",
        "a word nobody reads",
        "--out-dir",
        str(tmp_path / "verdict"),
    )
    # argparse refuses the word before any file is written: a fourth category must not exist.
    assert result.returncode != 0
    assert not (tmp_path / "verdict" / "verdict.json").exists()
