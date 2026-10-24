"""``tools/ci/release_approval.py``: a gated publish job publishes only what its approval saw.

GitHub's approval record has no time and no attempt, so it cannot say whether the owner approved
before or after a rebuild. What does is the deployment GitHub makes for each gated job: its
statuses go ``waiting`` (the job reached the gate) and then ``queued`` (the owner approved), each
naming the job. A gated job publishes only when its own deployment waited and was approved after
this attempt's manifest was written, by a required reviewer (#3911). Here against planted API
answers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import release_approval as ra

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parent.parent
REPO = "owner/project"
RUN, ATTEMPT, JOB = 4000, 2, 777
MANIFEST_DONE = "2026-10-24T06:18:30Z"
WAITED = "2026-10-24T06:18:31Z"
APPROVED = "2026-10-24T06:26:23Z"


def _jobs(*, manifest_done: str | None = MANIFEST_DONE, job_status: str = "in_progress") -> dict:
    jobs = [{"id": JOB, "name": "pypi", "status": job_status, "run_attempt": ATTEMPT}]
    jobs.append({"id": 5, "name": "pypi-client", "status": "in_progress", "run_attempt": ATTEMPT})
    if manifest_done is not None:
        jobs.append(
            {
                "id": 9,
                "name": "artifacts / manifest",
                "status": "completed",
                "conclusion": "success",
                "completed_at": manifest_done,
            }
        )
    return {"total_count": len(jobs), "jobs": jobs}


def _status(state: str, at: str, job: int = JOB) -> dict:
    url = f"https://github.com/{REPO}/actions/runs/{RUN}/job/{job}"
    return {"state": state, "created_at": at, "log_url": url, "target_url": url}


def _deployments(*ids: int) -> list[dict]:
    return [{"id": i, "environment": "release", "ref": "v0.3.0", "task": "deploy"} for i in ids]


class Api:
    """A planted GitHub API: answers by path; every path asked is kept."""

    def __init__(
        self,
        *,
        jobs: object = None,
        deployments: object = None,
        statuses: dict[int, object] | None = None,
        approvals: object = None,
        environment: object = None,
        fail: str = "",
    ) -> None:
        self.answers = {
            f"/repos/{REPO}/actions/runs/{RUN}/attempts/{ATTEMPT}/jobs?per_page=100": (
                jobs if jobs is not None else _jobs()
            ),
            f"/repos/{REPO}/deployments?environment=release&ref=v0.3.0&per_page=100": (
                deployments if deployments is not None else _deployments(31)
            ),
            f"/repos/{REPO}/actions/runs/{RUN}/approvals": (
                approvals if approvals is not None else [_approval()]
            ),
            f"/repos/{REPO}/environments/release": (
                environment if environment is not None else _environment()
            ),
        }
        for deployment, listed in (
            statuses or {31: [_status("waiting", WAITED), _status("queued", APPROVED)]}
        ).items():
            self.answers[f"/repos/{REPO}/deployments/{deployment}/statuses?per_page=100"] = listed
        self.fail = fail
        self.asked: list[str] = []

    def __call__(self, path: str) -> tuple[int, bytes]:
        self.asked.append(path)
        if self.fail and self.fail in path:
            return 502, b"bad gateway"
        if path not in self.answers:
            return 404, b"{}"
        return 200, json.dumps(self.answers[path]).encode()


def _approval(state: str = "approved", who: str = "keyurgolani", env: str = "release") -> dict:
    return {"state": state, "user": {"login": who}, "environments": [{"name": env}], "comment": ""}


def _environment(*reviewers: str) -> dict:
    listed = [{"type": "User", "reviewer": {"login": who}} for who in reviewers or ("keyurgolani",)]
    return {
        "name": "release",
        "protection_rules": [{"type": "required_reviewers", "reviewers": listed}],
    }


def _check(api: Api) -> ra.Approval:
    return ra.check(
        REPO, run_id=RUN, attempt=ATTEMPT, job="pypi", environment="release", ref="v0.3.0", get=api
    )


def test_a_job_whose_own_deployment_was_approved_after_the_manifest_publishes() -> None:
    got = _check(Api())
    assert (got.manifest, got.waited, got.approved, got.reviewer) == (
        MANIFEST_DONE,
        WAITED,
        APPROVED,
        "keyurgolani",
    )


def test_a_job_with_no_deployment_of_its_own_refuses() -> None:
    other_job = {31: [_status("waiting", WAITED, job=5), _status("queued", APPROVED, job=5)]}
    with pytest.raises(ra.Refused, match="no deployment of its own"):
        _check(Api(statuses=other_job))
    with pytest.raises(ra.Refused, match="no deployment of its own"):
        _check(Api(deployments=[]))


def test_a_job_that_never_waited_at_the_gate_refuses() -> None:
    """An approval carried into a re-run lets the job through without a wait of its own."""
    carried = {31: [_status("queued", APPROVED), _status("in_progress", APPROVED)]}
    with pytest.raises(ra.Refused, match="never waited"):
        _check(Api(statuses=carried))


def test_an_approval_before_this_attempts_manifest_refuses_naming_both_times() -> None:
    before = {
        31: [_status("waiting", "2026-10-24T06:00:00Z"), _status("queued", "2026-10-24T06:10:00Z")]
    }
    with pytest.raises(ra.Refused) as refused:
        _check(Api(statuses=before))
    said = str(refused.value)
    assert "2026-10-24T06:10:00Z" in said and MANIFEST_DONE in said, said


def test_another_environments_deployment_is_not_this_jobs() -> None:
    elsewhere = [{"id": 31, "environment": "release-client", "ref": "v0.3.0", "task": "deploy"}]
    with pytest.raises(ra.Refused, match="no deployment of its own"):
        _check(Api(deployments=elsewhere))


def test_an_approval_by_someone_who_is_not_a_required_reviewer_refuses() -> None:
    with pytest.raises(ra.Refused, match="required reviewer"):
        _check(Api(approvals=[_approval(who="someone-else")]))
    with pytest.raises(ra.Refused, match="required reviewer"):
        _check(Api(environment=_environment("another-owner")))


def test_a_rejection_alone_refuses_and_a_rejection_then_an_approval_passes() -> None:
    with pytest.raises(ra.Refused, match="no approval"):
        _check(Api(approvals=[_approval(state="rejected")]))
    with pytest.raises(ra.Refused, match="no approval"):
        _check(Api(approvals=[_approval(env="release-client")]))
    both = [_approval(state="rejected"), _approval()]
    assert _check(Api(approvals=both)).approved == APPROVED


def test_an_attempt_whose_manifest_job_has_not_finished_refuses() -> None:
    with pytest.raises(ra.Refused, match="manifest"):
        _check(Api(jobs=_jobs(manifest_done=None)))


def test_the_job_must_be_this_attempts_running_job_of_that_name() -> None:
    with pytest.raises(ra.Refused, match="no running job named pypi"):
        _check(Api(jobs=_jobs(job_status="completed")))


@pytest.mark.parametrize(
    "part", ["/attempts/", "/deployments?", "/statuses", "/approvals", "/environments/"]
)
def test_an_api_answer_it_cannot_read_refuses_and_never_passes(part: str) -> None:
    with pytest.raises(ra.Refused, match="cannot read"):
        _check(Api(fail=part))


def test_names_that_are_not_ones_are_refused_before_anything_is_asked() -> None:
    api = Api()
    for repository, job, environment, ref in [
        ("owner project", "pypi", "release", "v0.3.0"),
        (REPO, "pypi; x", "release", "v0.3.0"),
        (REPO, "pypi", "release/../x", "v0.3.0"),
        (REPO, "pypi", "release", "v0.3.0 x"),
    ]:
        with pytest.raises(ra.Refused):
            ra.check(
                repository,
                run_id=RUN,
                attempt=ATTEMPT,
                job=job,
                environment=environment,
                ref=ref,
                get=api,
            )
    assert api.asked == []


def test_it_runs_in_isolated_mode_on_the_standard_library_alone(tmp_path: Path) -> None:
    run = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools/ci/release_approval.py")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert run.returncode == 2 and "release-approval" in run.stderr, run.stderr
