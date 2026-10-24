"""A gated publish job publishes only the build its owner approved (#3911).

``python3 -I tools/ci/release_approval.py --repository R --run-id N --attempt A --job J
--environment E --ref T`` runs in a publish job that waits on an environment's required reviewer,
after the gate and before its first write, and refuses (exit 2), naming why, unless the owner
approved this very job after this attempt's manifest was written.

GitHub's approval record for a run (``/actions/runs/{id}/approvals``) has no time and no attempt,
so it cannot tell an approval of this build from one given before ``artifacts`` was built again.
What can is the deployment GitHub makes for each gated job: its statuses go ``waiting`` when the
job reaches the gate and ``queued`` when the owner approves it, each naming the job by its id, so
by its attempt. The check requires, all read through the API:

- this attempt's job of that name, running, and this attempt's manifest job, finished green;
- the deployment to the environment whose statuses name this job, with a ``waiting`` status and a
  ``queued`` one after it, both after the manifest job finished: a job let through with no wait
  of its own, as an approval carried into a re-run would let it, has neither;
- an approval of that environment in this run by one of its required reviewers.

An answer it cannot read refuses, never passes. Standard library only, with no relative imports,
so a trusted job runs the default branch's copy with ``python3 -I``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

#: Asks one API path, and answers its status and body.
Get = Callable[[str], "tuple[int, bytes]"]

#: The job in release-artifacts.yml that writes the manifest, as a calling run names it.
MANIFEST_JOB = "artifacts / manifest"
API = "https://api.github.com"
_TIMEOUT = 60

_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
_JOB = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()/-]*")
_ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class Refused(Exception):
    """Not this build's approval, or an answer this cannot rely on: nothing publishes."""


@dataclass(frozen=True)
class Approval:
    """When this attempt's manifest was written, when the job waited and was approved, by whom."""

    manifest: str
    waited: str
    approved: str
    reviewer: str


def _named(value: object, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Refused(f"{what} {value!r} is not one")
    return value


def _ask(get: Get, path: str, what: str) -> object:
    try:
        status, body = get(path)
    except (OSError, ValueError) as error:
        raise Refused(f"cannot read {what}: {error}") from None
    if status != 200:
        raise Refused(f"cannot read {what}: it answered {status}")
    try:
        return json.loads(body)
    except ValueError:
        raise Refused(f"cannot read {what}: its answer is not JSON") from None


def _listed(answer: object, key: str | None, what: str) -> list[dict]:
    listed = answer.get(key) if key and isinstance(answer, dict) else answer
    if not isinstance(listed, list):
        raise Refused(f"cannot read {what}: its answer holds no list")
    return [item for item in listed if isinstance(item, dict)]


def _time(value: object, what: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise Refused(f"cannot read {what}: {value!r} is not a time") from None


def check(
    repository: str,
    *,
    run_id: int,
    attempt: int,
    job: str,
    environment: str,
    ref: str,
    get: Get,
) -> Approval:
    """Refuse unless *job*, in attempt *attempt* of run *run_id*, was approved for *environment*
    by a required reviewer after this attempt's manifest was written."""
    _named(repository, _REPOSITORY, "the repository")
    _named(job, _JOB, "the job")
    _named(environment, _ENVIRONMENT, "the environment")
    _named(ref, _TAG, "the ref")
    if not isinstance(run_id, int) or not isinstance(attempt, int) or run_id < 1 or attempt < 1:
        raise Refused(f"run {run_id!r} attempt {attempt!r} is not one")

    what = f"run {run_id}'s attempt {attempt} jobs"
    jobs = _listed(
        _ask(
            get,
            f"/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100",
            what,
        ),
        "jobs",
        what,
    )
    own = [j for j in jobs if j.get("name") == job and j.get("status") == "in_progress"]
    if len(own) != 1:
        raise Refused(f"this attempt has no running job named {job}, or more than one")
    job_id = own[0].get("id")
    manifests = [
        j
        for j in jobs
        if j.get("name") == MANIFEST_JOB
        and j.get("status") == "completed"
        and j.get("conclusion") == "success"
    ]
    if len(manifests) != 1 or not manifests[0].get("completed_at"):
        raise Refused(f"this attempt's manifest job ({MANIFEST_JOB}) has not finished green")
    manifest = str(manifests[0]["completed_at"])
    manifest_at = _time(manifest, "the manifest job's end")

    what = f"the deployments of {ref} to {environment}"
    deployments = _listed(
        _ask(
            get,
            f"/repos/{repository}/deployments?environment={environment}&ref={ref}&per_page=100",
            what,
        ),
        None,
        what,
    )
    mine: list[dict] = []
    for deployment in deployments:
        if deployment.get("environment") != environment:
            continue
        what = f"deployment {deployment.get('id')}'s statuses"
        statuses = _listed(
            _ask(
                get,
                f"/repos/{repository}/deployments/{deployment.get('id')}/statuses?per_page=100",
                what,
            ),
            None,
            what,
        )
        suffix = f"/actions/runs/{run_id}/job/{job_id}"
        mine = [
            s
            for s in statuses
            if str(s.get("log_url") or s.get("target_url") or "").endswith(suffix)
        ]
        if mine:
            break
    if not mine:
        raise Refused(f"{job} (job {job_id}) has no deployment of its own to {environment}")
    mine.sort(key=lambda s: _time(s.get("created_at"), "a deployment status's time"))
    waited = next((s for s in mine if s.get("state") == "waiting"), None)
    if waited is None:
        raise Refused(
            f"{job} never waited at the {environment} gate in this attempt: no approval of this "
            "build was asked"
        )
    waited_at = _time(waited.get("created_at"), "the wait")
    released = next(
        (
            s
            for s in mine
            if s.get("state") == "queued" and _time(s.get("created_at"), "the release") > waited_at
        ),
        None,
    )
    if released is None:
        raise Refused(f"{job} was never let through the {environment} gate after it waited")
    approved = str(released.get("created_at"))
    if waited_at < manifest_at or _time(approved, "the approval") <= manifest_at:
        raise Refused(
            f"{job} was approved at {approved}, waiting since {waited.get('created_at')}, not "
            f"after this attempt's manifest was written at {manifest}: that approval saw "
            "another build"
        )

    what = f"run {run_id}'s approvals"
    approvals = _listed(
        _ask(get, f"/repos/{repository}/actions/runs/{run_id}/approvals", what), None, what
    )
    what = f"the {environment} environment"
    settings = _ask(get, f"/repos/{repository}/environments/{environment}", what)
    rules = _listed(settings, "protection_rules", what)
    required = sorted(
        str((r.get("reviewer") or {}).get("login"))
        for rule in rules
        if rule.get("type") == "required_reviewers"
        for r in (rule.get("reviewers") or [])
        if isinstance(r, dict) and r.get("type") == "User"
    )
    if not required:
        raise Refused(f"the {environment} environment names no required reviewer")
    approvers = [
        str((a.get("user") or {}).get("login"))
        for a in approvals
        if a.get("state") == "approved"
        and environment in [str((e or {}).get("name")) for e in (a.get("environments") or [])]
    ]
    if not approvers:
        raise Refused(f"run {run_id} holds no approval of {environment}")
    reviewer = next((who for who in approvers if who in required), None)
    if reviewer is None:
        raise Refused(
            f"{environment} was approved by {', '.join(approvers)}, none of them a required "
            f"reviewer ({', '.join(required)})"
        )
    return Approval(manifest, str(waited.get("created_at")), approved, reviewer)


def github_get(token: str) -> Get:
    def get(path: str) -> tuple[int, bytes]:
        request = urllib.request.Request(
            API + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as answer:  # noqa: S310
                return answer.status, answer.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except urllib.error.URLError as error:
            raise OSError(str(error.reason)) from None

    return get


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-approval", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--attempt", required=True, type=int)
    parser.add_argument("--job", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--ref", required=True)
    args = parser.parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    try:
        if not token:
            raise Refused(
                "no GH_TOKEN: the job's token reads the run, its deployments and approvals"
            )
        got = check(
            args.repository,
            run_id=args.run_id,
            attempt=args.attempt,
            job=args.job,
            environment=args.environment,
            ref=args.ref,
            get=github_get(token),
        )
    except Refused as refused:
        print(f"release-approval: refused: {refused}", file=sys.stderr)
        print(f"::error title=not this build's approval::{refused}")
        return 2
    said = (
        f"{args.job} was approved for {args.environment} by {got.reviewer} at {got.approved} "
        f"(waiting since {got.waited}), after this attempt's manifest was written at "
        f"{got.manifest}\n"
    )
    print(said, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### This build's approval\n\n{said}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
