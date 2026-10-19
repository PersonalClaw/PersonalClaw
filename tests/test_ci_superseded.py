"""``tools/ci/superseded.py``: a cancelled suite shard on main counts as superseded only when the
API proves GitHub's own concurrency replacement.

Decision 9 puts main's suite shards in one group per job and shard, with cancel-in-progress
false, so a newer commit's waiting job replaces an older one that has not started, and GitHub
reports the replaced job as cancelled. The ``test`` gate stays red either way. This module only
decides which red it names: ``superseded by <newer commit>``, or ``unproven``.

Superseded needs all of these, read from the API:

- the run is a push to ``main``;
- the shard job was cancelled before any step ran;
- its check run carries GitHub's replacement annotation, naming exactly the shard's own group;
- a later push run of the same workflow on ``main`` is for a commit that descends from this one,
  and has a job of the same name.

Every row below drops one of those and requires ``unproven`` with its reason, so no single
check can stand in for the others. On a pull request the word never exists.

Security-marked: this is a fail-closed boundary (rule 2 in ``docs/maintainers/testing.md``).
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from table_rows import row

from tools.ci import superseded as sp

pytestmark = pytest.mark.security

REPO = "PersonalClaw/PersonalClaw"
WORKFLOW = "ci.yml"
JOB = "test-shard"
PREFIX = "ci-suite-main-main-test-shard-"
SHA = "a" * 40
NEWER = "b" * 40
NEWEST = "c" * 40
RUN = 500
ATTEMPT = 1

_REPLACED = "Canceling since a higher priority waiting request for '{}' exists"


def _job(job_id: int, shard: int, conclusion: str, steps: list[dict[str, Any]] | None = None):
    return {
        "id": job_id,
        "name": f"{JOB} ({shard})",
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": SHA,
        "steps": steps if steps is not None else [],
    }


def _answers() -> dict[str, Any]:
    """Shard 2 of this run was replaced by the next commit's waiting shard 2; shard 1 passed."""
    ran = [{"name": "Runner identity", "conclusion": "success", "number": 1}]
    return {
        f"/repos/{REPO}/actions/runs/{RUN}/attempts/{ATTEMPT}/jobs": {
            "jobs": [_job(11, 1, "success", ran), _job(12, 2, "cancelled")]
        },
        f"/repos/{REPO}/check-runs/12/annotations": [
            {"annotation_level": "failure", "message": _REPLACED.format(PREFIX + "2")}
        ],
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
            "workflow_runs": [
                {"id": 502, "head_sha": NEWEST, "event": "push", "head_branch": "main"},
                {"id": 501, "head_sha": NEWER, "event": "push", "head_branch": "main"},
                {"id": RUN, "head_sha": SHA, "event": "push", "head_branch": "main"},
                {"id": 499, "head_sha": "d" * 40, "event": "push", "head_branch": "main"},
            ]
        },
        f"/repos/{REPO}/actions/runs/501/jobs": {
            "jobs": [{"id": 21, "name": f"{JOB} (2)", "status": "in_progress", "conclusion": None}]
        },
        f"/repos/{REPO}/actions/runs/502/jobs": {
            "jobs": [{"id": 31, "name": f"{JOB} (2)", "status": "queued", "conclusion": None}]
        },
        f"/repos/{REPO}/compare/{SHA}...{NEWER}": {"status": "ahead", "behind_by": 0},
        f"/repos/{REPO}/compare/{SHA}...{NEWEST}": {"status": "ahead", "behind_by": 0},
    }


def _getter(answers: dict[str, Any]):
    def get(path: str) -> Any:
        key = path.split("?", 1)[0]
        if key not in answers:
            raise OSError(f"no planted answer for {key}")
        value = answers[key]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    return get


def _decide(answers: dict[str, Any], **overrides: Any) -> sp.Answer:
    kwargs: dict[str, Any] = {
        "event": "push",
        "ref": "refs/heads/main",
        "repo": REPO,
        "sha": SHA,
        "run_id": RUN,
        "attempt": ATTEMPT,
        "workflow": WORKFLOW,
        "job": JOB,
        "group_prefix": PREFIX,
    }
    kwargs.update(overrides)
    return sp.decide(_getter(answers), **kwargs)


def test_a_shard_replaced_by_the_next_commits_waiting_shard_is_superseded() -> None:
    got = _decide(_answers())
    assert got.superseded == {f"{JOB} (2)": NEWER}, got
    assert got.unproven == {}, got
    # Only cancelled shards are judged: shard 1 passed and is in neither list.
    assert sp.output_line(got) == f"superseded={NEWER}"


def _set(path: str, value: Any):
    def edit(answers: dict[str, Any]) -> None:
        answers[path] = value

    return edit


def _edit(path: str, change):
    def edit(answers: dict[str, Any]) -> None:
        change(answers[path])

    return edit


_JOBS = f"/repos/{REPO}/actions/runs/{RUN}/attempts/{ATTEMPT}/jobs"
_NOTES = f"/repos/{REPO}/check-runs/12/annotations"
_RUNS = f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs"
_NEWER_JOBS = f"/repos/{REPO}/actions/runs/501/jobs"
_NEWER_COMPARE = f"/repos/{REPO}/compare/{SHA}...{NEWER}"


def _only_newer_run(answers: dict[str, Any]) -> None:
    answers[_RUNS]["workflow_runs"] = [r for r in answers[_RUNS]["workflow_runs"] if r["id"] != 502]


def _cancelled_job(change):
    def edit(answers: dict[str, Any]) -> None:
        change(answers[_JOBS]["jobs"][1])

    return edit


def _newer_run(change):
    def edit(answers: dict[str, Any]) -> None:
        _only_newer_run(answers)
        change(next(r for r in answers[_RUNS]["workflow_runs"] if r["id"] == 501))

    return edit


#: (name, how the API's answers change, the reason the shard is unproven)
UNPROVEN_ROWS: list[tuple[str, Any, str]] = [
    (
        "a cancelled shard that had started a step",
        _cancelled_job(lambda j: j.update(steps=[{"name": "Runner identity", "number": 1}])),
        "it started",
    ),
    ("no annotation on the cancelled shard", _set(_NOTES, []), "no replacement annotation"),
    (
        "the replacement annotation names another group",
        _set(_NOTES, [{"message": _REPLACED.format(PREFIX + "3")}]),
        "names the group",
    ),
    (
        "the replacement annotation names a pull request's group",
        _set(_NOTES, [{"message": _REPLACED.format("ci-suite-someone/x-branch-test-shard-2")}]),
        "names the group",
    ),
    (
        "a cancellation by hand (no replacement)",
        _set(_NOTES, [{"message": "The run was canceled by @someone."}]),
        "no replacement annotation",
    ),
    (
        "the annotation text only contains the replacement message",
        _set(_NOTES, [{"message": "note: " + _REPLACED.format(PREFIX + "2")}]),
        "no replacement annotation",
    ),
    (
        "no later run of the workflow on main",
        _edit(
            _RUNS,
            lambda v: v.update(workflow_runs=[r for r in v["workflow_runs"] if r["id"] <= RUN]),
        ),
        "no later push run",
    ),
    (
        "the later run is for a commit that does not descend from this one",
        lambda a: (_only_newer_run(a), a[_NEWER_COMPARE].update(status="diverged", behind_by=2)),
        "no later push run",
    ),
    (
        "the later run is for the same commit",
        _newer_run(lambda r: r.update(head_sha=SHA)),
        "no later push run",
    ),
    (
        "the later run is a pull request's",
        _newer_run(lambda r: r.update(event="pull_request")),
        "no later push run",
    ),
    (
        "the later run is on another branch",
        _newer_run(lambda r: r.update(head_branch="feature-a")),
        "no later push run",
    ),
    (
        "the later run has no job of the same name",
        lambda a: (
            _only_newer_run(a),
            a[_NEWER_JOBS].update(jobs=[{"id": 21, "name": f"{JOB} (1)"}]),
        ),
        "no later push run",
    ),
    (
        "the cancelled job's name is not a shard of the job",
        _cancelled_job(lambda j: j.update(name="test-shard-extra (2)")),
        "not a shard of",
    ),
    ("the API cannot be read", _set(_NOTES, OSError("rate limited")), "could not find out"),
    (
        "the API answers a shape this module cannot read",
        _set(_RUNS, {"runs": []}),
        "could not find out",
    ),
    (
        "this run's own jobs cannot be read",
        _set(_JOBS, OSError("rate limited")),
        "could not find out",
    ),
    (
        "this run's own jobs come back in a shape this module cannot read",
        _set(_JOBS, {"runs": []}),
        "could not find out",
    ),
    (
        "the replacement message followed by more text",
        _set(_NOTES, [{"message": _REPLACED.format(PREFIX + "2") + " (and more)"}]),
        "no replacement annotation",
    ),
    (
        "a cancelled job named like a shard with more after it",
        _cancelled_job(lambda j: j.update(name=f"{JOB} (2) extra")),
        "not a shard of",
    ),
    (
        "the later run's job of that name was skipped",
        lambda a: (
            _only_newer_run(a),
            a[_NEWER_JOBS].update(
                jobs=[
                    {"id": 21, "name": f"{JOB} (2)", "status": "completed", "conclusion": "skipped"}
                ]
            ),
        ),
        "no later push run",
    ),
    (
        "the later run's head is not a lowercase full object id",
        _newer_run(lambda r: r.update(head_sha=NEWER.upper())),
        "no later push run",
    ),
    (
        "the later commit is diverged (status alone changed)",
        lambda a: (_only_newer_run(a), a[_NEWER_COMPARE].update(status="diverged")),
        "no later push run",
    ),
    (
        "the later commit is behind by one (behind_by alone changed)",
        lambda a: (_only_newer_run(a), a[_NEWER_COMPARE].update(behind_by=1)),
        "no later push run",
    ),
]


def test_every_other_cancellation_is_unproven(subtests: object) -> None:
    assert len(UNPROVEN_ROWS) == 23, "a row was dropped from the table"
    for name, change, why in UNPROVEN_ROWS:
        with row(subtests, case=name):
            answers = _answers()
            change(answers)
            got = _decide(answers)
            assert got.superseded == {}, f"{name}: read superseded: {got}"
            reasons = " | ".join(got.unproven.values())
            assert why in reasons, f"{name}: unproven for another reason: {got}"
            assert sp.output_line(got) == "superseded=", f"{name}: {sp.output_line(got)}"


#: (name, event, ref): only a push to main can have a superseded shard.
NOT_A_MAIN_PUSH: list[tuple[str, str, str]] = [
    ("a pull request", "pull_request", "refs/pull/41/merge"),
    ("a push to another branch", "push", "refs/heads/feature-a"),
    ("a tag named main", "push", "refs/tags/main"),
    ("a scheduled run on main", "schedule", "refs/heads/main"),
]


def test_superseded_never_exists_off_a_push_to_main(subtests: object) -> None:
    assert len(NOT_A_MAIN_PUSH) == 4, "a row was dropped from the table"
    for name, event, ref in NOT_A_MAIN_PUSH:
        with row(subtests, case=name):

            def no_api(path: str) -> Any:
                raise AssertionError(f"{name}: read the API at {path}")

            got = sp.decide(
                no_api,
                event=event,
                ref=ref,
                repo=REPO,
                sha=SHA,
                run_id=RUN,
                attempt=ATTEMPT,
                workflow=WORKFLOW,
                job=JOB,
                group_prefix=PREFIX,
            )
            assert got.superseded == {}, f"{name}: {got}"
            assert "not a push to main" in got.note, f"{name}: {got}"
            assert sp.output_line(got) == "superseded="


def test_one_unproven_shard_leaves_the_run_unproven() -> None:
    answers = _answers()
    answers[_JOBS]["jobs"].append(_job(13, 3, "cancelled"))
    answers[f"/repos/{REPO}/check-runs/13/annotations"] = []
    got = _decide(answers)
    assert got.superseded == {f"{JOB} (2)": NEWER}
    assert set(got.unproven) == {f"{JOB} (3)"}
    # The gate names superseded only when every cancelled shard is proven.
    assert sp.output_line(got) == "superseded="


def test_shards_replaced_by_different_commits_name_no_one_commit() -> None:
    # Shard 1 is replaced only by the newest run, shard 2 by the next: the proof holds for each,
    # but no single commit superseded this one, so the gate names none (as decide_main_push).
    answers = _answers()
    answers[_JOBS]["jobs"] = [_job(11, 1, "cancelled"), _job(12, 2, "cancelled")]
    answers[f"/repos/{REPO}/check-runs/11/annotations"] = [
        {"message": _REPLACED.format(PREFIX + "1")}
    ]
    answers[f"/repos/{REPO}/actions/runs/502/jobs"]["jobs"].append(
        {"id": 32, "name": f"{JOB} (1)", "status": "queued", "conclusion": None}
    )
    got = _decide(answers)
    assert got.superseded == {f"{JOB} (1)": NEWEST, f"{JOB} (2)": NEWER}, got
    assert got.unproven == {}, got
    assert sp.output_line(got) == "superseded="


def test_the_earliest_later_descendant_is_named() -> None:
    answers = _answers()
    got = _decide(answers)
    assert got.superseded == {f"{JOB} (2)": NEWER}, "the next commit, not the newest one"


def test_a_cancelled_run_with_no_cancelled_shard_has_nothing_to_prove() -> None:
    answers = _answers()
    answers[_JOBS]["jobs"] = [_job(11, 1, "success", [{"name": "x", "number": 1}])]
    got = _decide(answers)
    assert got.superseded == {} and got.unproven == {}
    assert sp.output_line(got) == "superseded="


#: (name, a value of an argument, how the refusal begins): the arguments are checked before
#: any read, so a malformed one can never be argued into superseded.
BAD_ARGUMENTS: list[tuple[str, dict[str, Any], str]] = [
    ("a short commit", {"sha": "abc123"}, "the pushed commit"),
    ("a commit with a trailing newline", {"sha": SHA + "\n"}, "the pushed commit"),
    ("a run id that is not a number", {"run_id": "500"}, "the run id"),
    ("a run attempt of zero", {"attempt": 0}, "the run attempt"),
    ("an empty group prefix", {"group_prefix": ""}, "the group prefix"),
    ("an empty job id", {"job": ""}, "the job"),
]


def test_malformed_arguments_are_unproven(subtests: object) -> None:
    assert len(BAD_ARGUMENTS) == 6, "a refusal was dropped from the table"
    for name, override, why in BAD_ARGUMENTS:
        with row(subtests, case=name):
            got = _decide(_answers(), **override)
            assert got.superseded == {}, f"{name}: {got}"
            assert why in got.note, f"{name}: {got}"


def test_it_runs_as_one_standard_library_file(tmp_path: Path) -> None:
    # The way the gate runs it: `python3 -I tools/ci/superseded.py`, nothing installed. Off a push
    # to main it answers without reading the API at all, so this needs no network.
    output = tmp_path / "output.txt"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/41/merge",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SHA": SHA,
        "GITHUB_RUN_ID": str(RUN),
        "GITHUB_RUN_ATTEMPT": str(ATTEMPT),
        "GITHUB_OUTPUT": str(output),
    }
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            sp.__file__,
            "--workflow",
            WORKFLOW,
            "--job",
            JOB,
            "--group-prefix",
            PREFIX,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert output.read_text(encoding="utf-8") == "superseded=\n"
    assert "not a push to main" in done.stdout
