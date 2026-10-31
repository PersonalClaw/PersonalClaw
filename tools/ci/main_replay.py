"""What a push to ``main`` may replay from its pull request's run, and what it must run again.

A pull request's merge-tier run is its tree's one expensive execution. When the push that merges
it lands the very tree that run tested, a job that passed there need not run again; every other
job runs. :func:`decide` says which, per planned job, and writes the push's reuse receipt. It
replaces ``tools/ci/tree_tested.py``'s all-or-nothing answer with one per job (#3919).

``python -m tools.ci replay`` (:func:`main`) is the push stage's call, and nothing makes it yet:
``merge.yml`` will, once #3915's flip gives it its push trigger and #3910's controller App posts
the merge verdict. The caller passes in what only it can read: the planned jobs (from ``main``'s
own catalogue), the verdict files of a run's first attempt (:func:`source_records` reads them
from the run's artifacts), whether the controller's merge verdict on the head counts
(``tools/ci/merge_verdict.py``), and the image each runner label runs now.

**Nothing replays,** and every planned job runs, unless all of these hold:

1. the run is a push to ``main`` that names the commit it replaced. Any other event, a
   ``pull_request`` one above all, replays nothing and reads nothing: a pull request's own
   evidence never reaches its own verdict;
2. ``tree_tested``'s guards, in its order and with its helpers, held here by this module's own
   tests: exactly one pull request into ``main`` was merged as the pushed commit; the pushed
   tree is that pull request's head tree (the squash-tree rule); the head contains the commit
   the push replaced; the head's first ``pull_request`` run of the workflow was made after the
   pull request was opened; and its base never changed;
3. every run the workflow's listing holds is a run of this workflow file, the listing's count
   says it is whole, and the first of the head's runs (the lowest-numbered, whatever order the
   API lists them in) has an attempt 1 that is attempt 1, completed, concluding ``success`` or
   ``failure`` (a cancelled or timed-out attempt is judged by nothing), with a start time.
   Only attempt 1 of that run is read: never a later attempt, never a later run, so a red is
   never rerun into a green. Here this departs from ``tree_tested``, which needs every run on
   the head green on attempt 1: a later run's red does not stop this run's green from
   replaying. What a later run found reaches the push only through the merge verdict, whose
   newest run on the head decides (``merge_verdict.py``, rule 3), so a later red verdict stops
   every replay;
4. the controller's merge verdict on the head counts (the caller's ``verified``);
5. every verdict file attempt 1 left can be read as a job record (``verdict.from_job_records``).

**Then each planned job** (each shard, as ``run-job`` names it: ``id`` or ``id (i/N)``) is
replayed only when all of these hold, and runs otherwise:

- its reuse class (``verdict_store.reuse_class``) is ``tree`` or ``ttl:<hours>``: a security or
  live job, or one of class ``never``, always runs;
- attempt 1's job of that name completed with ``success``;
- exactly one verdict file names it, of attempt 1, a ``success``, on the head, reading no
  partner repository (the push cannot hold a partner's head to the pull request's), with an
  empty quarantined-failure list and no rerun. A ``pull_request`` run checks out
  ``refs/pull/<n>/merge``, so its records name that merge commit rather than the head: such a
  record is on the head when the commit has two parents, the second is the head, its tree is
  the head's (so the pushed tree), and the commit the push replaced contains the first. Each
  commit is looked up once per push (:class:`_MergeRefs`); one that cannot be read, or reads
  otherwise, runs the job and says why. The receipt says, per key, which of the two its record
  named (``record_names``);
- that file is the source run's own (its ``run_id``), and names what the job ran on and its
  toolchain (``job-record/3``): the runner label ``main``'s catalogue gives the job, the image
  that label runs now (the caller's ``images``, as the push read them; a container job's image
  is its catalogue's), never an empty one, the Python ``main``'s catalogue names, and an
  identity for every word of the job's toolchain, none of them absent. So a moved runner image,
  another toolchain, or a record that names neither runs the job: the tree alone is not every
  input a pass stood on;
- a ``ttl`` job's attempt 1 started less than its TTL before ``now``, and not after it;
- every other shard of a sharded job replays too.

**Under main's merge queue** (0.4.0 A1) the source is the pushed commit's own run: when a
``merge_group`` run of the workflow ran on the pushed commit, the first of them is read, on its
attempt 1 as above, when exactly one pull request into ``main`` was merged as that commit, the
commit contains the one the push replaced, and the run's branch names that pull request
(:data:`QUEUE_BRANCH`). A queue commit is what lands, so guard 2's squash-tree, opened-after and
base rules are not read, its records must name the pushed commit itself (never a merge ref), and
the merge verdict is asked of it. Once main's rules queue its merges (``tree_tested``'s reading),
a push with no queue run on its commit replays nothing.

**The receipt** carries ``trust.RECEIPT_FIELDS`` (the pushed commit, its tree, the toolchain,
and the sha256 of the plan, of attempt 1's job list as read and of each replayed verdict file),
the source run, and each planned job's decision and why. It is None when nothing could be
judged per job; the reason says why.

Standard library only, beside its siblings, so the trusted plan job can run it on the runner
image's own ``python3`` before anything of the candidate is installed. :func:`decide` never
raises once its own inputs are in order: any error reading the API, the files or the verdict is
"every job runs", with its reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import catalogue, merge_verdict, telemetry, tree_tested, verdict
from .capabilities import ABSENT
from .catalogue import NEVER, Job, Reuse
from .tree_tested import (
    _BASE_CHANGED,
    _PER_PAGE,
    MAIN,
    ZERO,
    Get,
    Unanswered,
    _integer,
    _mapping,
    _string,
    _tree_of,
)
from .verdict_store import _live, reuse_class

#: The workflow whose pull_request runs a push replays from (#3915's merge tier).
WORKFLOW = "merge.yml"
#: A queue commit's run of main's merge queue (0.4.0 A1), and the branch GitHub runs it on:
#: ``gh-readonly-queue/main/pr-<n>-<the commit it was built on>``.
QUEUE_EVENT = "merge_group"
QUEUE_BRANCH = re.compile(r"gh-readonly-queue/main/pr-(?P<number>[1-9][0-9]*)-[0-9a-f]{40}")

#: Where a workflow file lives, as a run's ``path`` names it.
WORKFLOWS = ".github/workflows/"

_SHA = re.compile(r"[0-9a-f]{40}")
_OBJECT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

Records = Callable[[int], Sequence[bytes]]
"""The verdict files a run's attempt 1 wrote, as bytes, by the run's id."""

Verified = Callable[[str], "str | None"]
"""Whether the controller's merge verdict on a head counts: None when it does, else why not."""

#: A record that names the pull request's head itself, as ``record_names`` says it.
HEAD_NAMED = "head"
#: A record that names the head's ``refs/pull/<n>/merge`` commit, as a pull_request run checks out.
MERGE_REF_NAMED = "merge-ref"


class Refused(ValueError):
    """The caller's own inputs are not what they must be. Raised before anything is read."""


@dataclass(frozen=True)
class Decision:
    """One planned key's decision: replayed from the source run, or run, and why."""

    key: str
    job_id: str
    reuse: str
    replayed: bool
    reason: str
    run_url: str = ""
    ttl_age_seconds: float | None = None
    record_digest: str | None = None
    #: The image the replayed pass ran on, which is the one its label runs now.
    image: str = ""
    #: What its record named as the commit it ran on: :data:`HEAD_NAMED` or
    #: :data:`MERGE_REF_NAMED`, once that was judged; empty before.
    record_names: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "job": self.job_id,
            "decision": "replayed" if self.replayed else "run",
            "reuse": self.reuse,
            "reason": self.reason,
            "run_url": self.run_url or None,
            "ttl_age_seconds": self.ttl_age_seconds,
            "image": self.image or None,
            "record_names": self.record_names or None,
        }


@dataclass(frozen=True)
class Replay:
    """The push's answer: why, each planned key's decision, the source it read, the receipt."""

    reason: str
    jobs: tuple[Decision, ...]
    workflow: str = WORKFLOW
    pull_request: int | None = None
    head: str = ""
    tree: str = ""
    run: int | None = None
    run_url: str = ""
    receipt: dict[str, Any] | None = field(default=None, compare=False)

    @property
    def replays(self) -> bool:
        """Whether any planned job is replayed."""
        return any(d.replayed for d in self.jobs)

    def lines(self) -> list[str]:
        """One line per planned key, as the push prints them."""
        out = []
        for d in self.jobs:
            if d.replayed:
                out.append(f"replayed {d.key} from {self.workflow} run {self.run}, attempt 1")
            else:
                out.append(f"ran {d.key}: {d.reason}")
        return out


def keys_of(job: Job) -> list[str]:
    """The keys ``run-job`` names a job's records by: its id, or each shard's ``id (i/N)``."""
    if job.shards <= 1:
        return [job.id]
    return [f"{job.id} ({i}/{job.shards})" for i in range(1, job.shards + 1)]


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _never(job: Job) -> str:
    if job.security == "security":
        return "security evidence is never replayed (class never)"
    if job.security != "ordinary":
        return f"its security is {job.security!r}, so it is never replayed (class never)"
    if _live(job):
        return "live evidence is never replayed (class never)"
    return "its class is never"


def _everything(plan: Sequence[Job], reason: str, workflow: str) -> Replay:
    """Every planned key runs, for a reason that holds for the whole run."""
    jobs = tuple(
        Decision(key, job.id, str(reuse_class(job)), False, reason)
        for job in plan
        for key in keys_of(job)
    )
    return Replay(reason, jobs, workflow)


# ── the source run ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Source:
    pull_request: int
    #: The commit the source run tested and its records must name: the pull request's head, or,
    #: for a queue run, the pushed commit itself.
    head: str
    tree: str
    run: int
    run_url: str
    started: float
    listed: Mapping[str, Mapping[str, Any]]
    jobs_digest: str
    #: The source run's event: ``pull_request``, or ``merge_group`` for a queue commit's run.
    event: str = "pull_request"


def _started(attempt: Mapping[str, Any]) -> float:
    text = _string(attempt, "run_started_at", "attempt 1")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Unanswered(f"attempt 1's start time {text!r} is not a time") from None
    if moment.tzinfo is None:
        raise Unanswered(f"attempt 1's start time {text!r} names no time zone")
    return moment.timestamp()


def _whole(page: Mapping[str, Any], listing: list[Any], what: str) -> None:
    """Refuse a page that may not be the whole listing: one with no count, or a count more than
    it holds, could leave a run or a job unseen."""
    total = page.get("total_count")
    if isinstance(total, bool) or not isinstance(total, int):
        raise Unanswered(f"{what} name no count, so one may be unseen")
    if total > len(listing):
        raise Unanswered(f"{total} {what} do not fit one page, so one may be unseen")


def _merged_as(get: Get, repo: str, sha: str) -> list[Mapping[str, Any]]:
    """The pull requests into main that GitHub merged as *sha*."""
    pulls = get(f"/repos/{repo}/commits/{sha}/pulls")
    if not isinstance(pulls, list):
        raise Unanswered("the pull requests of the pushed commit are not a list")
    return [
        pr
        for pr in pulls
        if isinstance(pr, Mapping)
        and pr.get("merge_commit_sha") == sha
        and pr.get("merged_at")
        and isinstance(pr.get("base"), Mapping)
        and pr["base"].get("ref") == "main"
    ]


def _queue_source(
    get: Get, *, repo: str, sha: str, before: str, workflow: str, tree: str
) -> _Source | str | None:
    """The pushed commit's own first ``merge_group`` run of *workflow* (0.4.0 A1) and its attempt
    1, or why it is none, or None when no queue run ran on it. A queue commit is what lands, so its
    run tested the pushed tree itself: no squash-tree or merge-ref rule is needed, and its records
    must name the pushed commit."""
    query = urllib.parse.urlencode({"head_sha": sha, "event": QUEUE_EVENT, "per_page": _PER_PAGE})
    page = _mapping(get(f"/repos/{repo}/actions/workflows/{workflow}/runs?{query}"), "the runs")
    runs = page.get("workflow_runs")
    if not isinstance(runs, list):
        raise Unanswered("the runs are not a list")
    _whole(page, runs, f"{workflow} runs")
    for run in runs:
        if not isinstance(run, Mapping):
            raise Unanswered(f"the {workflow} runs hold an entry that is not a run")
        if run.get("path") != f"{WORKFLOWS}{workflow}":
            raise Unanswered(f"the {workflow} runs hold a run of {run.get('path')!r}")
    ours = [r for r in runs if r.get("head_sha") == sha and r.get("event") == QUEUE_EVENT]
    if not ours:
        return None
    merged = _merged_as(get, repo, sha)
    if len(merged) != 1:
        found = "no pull request" if not merged else f"{len(merged)} pull requests"
        return f"{found} into main was merged as this queue commit"
    number = _integer(merged[0], "number", "the pull request")
    compare = _mapping(get(f"/repos/{repo}/compare/{before}...{sha}"), "the comparison")
    if _integer(compare, "behind_by", "the comparison") != 0:
        return "the queue commit does not contain the previous main"
    ours.sort(key=lambda run: (_integer(run, "run_number", "a run"), _integer(run, "id", "a run")))
    first = ours[0]
    branch = QUEUE_BRANCH.fullmatch(str(first.get("head_branch") or ""))
    if branch is None or int(branch["number"]) != number:
        return (
            f"the first {workflow} merge_group run on the queue commit is on"
            f" {first.get('head_branch')!r}, which names no queue entry of #{number}"
        )
    run_id, url, started, listed, digest = _attempt_one(get, repo, workflow, first)
    if isinstance(url, Refusal):
        return url.why
    return _Source(number, sha, tree, run_id, url, started, listed, digest, QUEUE_EVENT)


def _source(get: Get, *, repo: str, sha: str, before: str, workflow: str) -> _Source | str:
    """The source run and its attempt 1, or why there is none: the pushed commit's own queue run
    when one ran on it; once main's rules queue its merges, nothing else; and before that, the
    pull request's first run of *workflow*."""
    tree = _tree_of(get, repo, sha)
    queued = _queue_source(get, repo=repo, sha=sha, before=before, workflow=workflow, tree=tree)
    if queued is not None:
        return queued
    if tree_tested._queue_live(get, repo):
        return (
            f"main's merges are queued, and no {workflow} merge_group run ran on the pushed"
            " commit itself"
        )
    merged = _merged_as(get, repo, sha)
    if len(merged) != 1:
        found = "no pull request" if not merged else f"{len(merged)} pull requests"
        return f"{found} into main was merged as this commit"
    pr = merged[0]
    number = _integer(pr, "number", "the pull request")
    head = _string(_mapping(pr.get("head"), f"#{number}'s head"), "sha", f"#{number}'s head")
    if not _SHA.fullmatch(head):
        raise Unanswered(f"#{number}'s head {head!r} is not a full commit id")
    if _tree_of(get, repo, head) != tree:
        return f"the pushed tree is not #{number}'s head tree: the merge was not up to date"
    compare = _mapping(get(f"/repos/{repo}/compare/{before}...{head}"), "the comparison")
    behind = _integer(compare, "behind_by", "the comparison")
    if behind != 0:
        return f"#{number}'s head is {behind} commit(s) behind the previous main"
    query = urllib.parse.urlencode(
        {"head_sha": head, "event": "pull_request", "per_page": _PER_PAGE}
    )
    page = _mapping(get(f"/repos/{repo}/actions/workflows/{workflow}/runs?{query}"), "the runs")
    runs = page.get("workflow_runs")
    if not isinstance(runs, list):
        raise Unanswered("the runs are not a list")
    _whole(page, runs, f"{workflow} runs")
    # The listing is the workflow's own: an entry that is not a run of this workflow file, of
    # this head or another, is an answer this cannot trust, so nothing is replayed from it.
    for run in runs:
        if not isinstance(run, Mapping):
            raise Unanswered(f"the {workflow} runs hold an entry that is not a run")
        if run.get("path") != f"{WORKFLOWS}{workflow}":
            raise Unanswered(f"the {workflow} runs hold a run of {run.get('path')!r}")
    ours = [
        run for run in runs if run.get("head_sha") == head and run.get("event") == "pull_request"
    ]
    if not ours:
        return f"no {workflow} pull_request run of #{number}'s head"
    ours.sort(key=lambda run: (_integer(run, "run_number", "a run"), _integer(run, "id", "a run")))
    first = ours[0]
    opened = _string(pr, "created_at", f"#{number}")
    if _string(first, "created_at", "the first run") < opened:
        return (
            f"the first {workflow} run on #{number}'s head was made before #{number} was opened,"
            " for another pull request"
        )
    events = get(f"/repos/{repo}/issues/{number}/events?per_page={_PER_PAGE}")
    if not isinstance(events, list):
        raise Unanswered(f"#{number}'s events are not a list")
    if len(events) >= _PER_PAGE:
        raise Unanswered(f"#{number}'s events do not fit one page, so a base change may be unseen")
    if any(isinstance(e, Mapping) and e.get("event") in _BASE_CHANGED for e in events):
        return f"#{number}'s base was changed, so its run may have tested another base"
    run_id, url, started, listed, digest = _attempt_one(get, repo, workflow, first)
    if isinstance(url, Refusal):
        return url.why
    return _Source(number, head, tree, run_id, url, started, listed, digest)


@dataclass(frozen=True)
class Refusal:
    """Why a source run's attempt 1 is judged by nothing."""

    why: str


def _attempt_one(
    get: Get, repo: str, workflow: str, first: Mapping[str, Any]
) -> tuple[int, str | Refusal, float, dict[str, Mapping[str, Any]], str]:
    """Attempt 1 of the source run *first*: its id, its link (or why it is judged by nothing), its
    start, its jobs by name and the digest of their listing."""
    run_id = _integer(first, "id", "the first run")
    attempt = _mapping(get(f"/repos/{repo}/actions/runs/{run_id}/attempts/1"), "attempt 1")
    if _integer(attempt, "run_attempt", "attempt 1") != 1:
        raise Unanswered(f"run {run_id}'s attempt 1 says it is attempt {attempt['run_attempt']}")
    status, conclusion = attempt.get("status"), attempt.get("conclusion")
    if status != "completed":
        why = f"attempt 1 of {workflow} run {run_id} is {status or 'of no status'}"
        return run_id, Refusal(why), 0.0, {}, ""
    # A run with a red job concludes failure, and its green jobs are still judged one by one;
    # a run cut short (cancelled, timed out) is judged by nothing.
    if conclusion not in ("success", "failure"):
        why = f"attempt 1 of {workflow} run {run_id} concluded {conclusion or 'nothing'}"
        return run_id, Refusal(why), 0.0, {}, ""
    started = _started(attempt)
    url = str(attempt.get("html_url") or first.get("html_url") or "")
    page = _mapping(
        get(f"/repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page={_PER_PAGE}"),
        "attempt 1's jobs",
    )
    listing = page.get("jobs")
    if not isinstance(listing, list):
        raise Unanswered("attempt 1's jobs are not a list")
    _whole(page, listing, "attempt 1's jobs")
    listed: dict[str, Mapping[str, Any]] = {}
    for job in listing:
        job = _mapping(job, "a job of attempt 1")
        name = _string(job, "name", "a job of attempt 1")
        if _integer(job, "run_attempt", f"job {name}") != 1:
            raise Unanswered(f"job {name} in attempt 1's list is of attempt {job['run_attempt']}")
        if name in listed:
            raise Unanswered(f"attempt 1's jobs name {name} twice")
        listed[name] = job
    return run_id, url, started, listed, _digest(_canonical(listing))


# ── the verdict files ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _File:
    raw: bytes
    result: verdict.JobResult
    quarantined: tuple[str, ...]
    rerun: object


def _files(texts: object, run_id: int) -> dict[str, list[_File]]:
    """Each verdict file attempt 1 left, read as a job record, by its key. One file that cannot
    be read stops the replay: what it held is not known."""
    if not isinstance(texts, (list, tuple)):
        raise Unanswered(f"run {run_id}'s verdict files are not a list")
    out: dict[str, list[_File]] = {}
    for n, raw in enumerate(texts, 1):
        if not isinstance(raw, bytes):
            raise Unanswered(f"verdict file {n} of run {run_id} is not bytes")
        try:
            text = raw.decode("utf-8")
            [result] = verdict.from_job_records([text])
            data = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise Unanswered(f"verdict file {n} of run {run_id} cannot be read: {exc}") from None
        out.setdefault(result.key, []).append(
            _File(raw, result, tuple(data["quarantined"]), data["rerun"])
        )
    return out


# ── the commit a record names ────────────────────────────────────────────────────────────────


@dataclass
class _MergeRefs:
    """Whether a commit a record names in place of the head is that head's merge ref, each
    commit looked up once per push: every shard and job of one run names the same one.

    A ``pull_request`` run checks out ``refs/pull/<n>/merge``, a commit GitHub makes of the head
    merged into its base, so ``run-job`` records that commit (``git rev-parse HEAD``). It stands
    for the head only when it has two parents, the second is the head, its tree is the head's
    (which the push holds to the pushed tree), and the commit the push replaced contains the first
    (a merge onto a base ``main`` never had is another tree's test)."""

    get: Get
    repo: str
    source: _Source
    before: str
    answers: dict[str, str | None] = field(default_factory=dict)

    def why_not(self, sha: str) -> str | None:
        """None when *sha* is the head's merge ref, else why not: an answer that cannot be read
        (none, a refusal, a rate limit, garbage) is a why, never a pass."""
        if sha not in self.answers:
            self.answers[sha] = self._looked_up(sha)
        return self.answers[sha]

    def _looked_up(self, sha: str) -> str | None:
        number, head = self.source.pull_request, self.source.head
        what = f"commit {sha[:12]}"
        try:
            commit = _mapping(self.get(f"/repos/{self.repo}/commits/{sha}"), what)
            tree = _mapping(_mapping(commit.get("commit"), what).get("tree"), f"{what}'s tree")
            parents = commit.get("parents")
            if not isinstance(parents, list):
                raise Unanswered(f"{what} lists no parents")
            shas = [
                _string(_mapping(p, f"a parent of {what}"), "sha", f"a parent of {what}")
                for p in parents
            ]
            if len(shas) != 2:
                return f"{what} has {len(shas)} parent(s), so it is not #{number}'s merge ref"
            base, merged = shas
            if merged != head:
                return f"{what} merges {merged[:12]}, not #{number}'s head"
            if _string(tree, "sha", f"{what}'s tree") != self.source.tree:
                return (
                    f"{what}'s tree is not #{number}'s head tree: it merged the head onto another"
                    " base"
                )
            compare = _mapping(
                self.get(f"/repos/{self.repo}/compare/{base}...{self.before}"), "the comparison"
            )
            if _integer(compare, "behind_by", "the comparison") != 0:
                return f"{what} merges onto {base[:12]}, which the previous main does not contain"
        except Exception as exc:  # noqa: BLE001 - an answer it cannot read runs the job
            return f"{what} could not be read: {exc}"
        return None


# ── one key ──────────────────────────────────────────────────────────────────────────────────


def _ran_on(job: Job, result: verdict.JobResult, images: Mapping[str, str]) -> str | None:
    """Why *result* did not run on what *job* runs on now, or None when it did: the label
    ``main``'s catalogue gives it, the image that label runs now, the Python the catalogue names
    and an identity for every word of its toolchain."""
    runner, toolchain = result.runner, result.toolchain
    if runner is None or toolchain is None:
        return "its verdict file names no runner or toolchain, so what it ran on is not known"
    if runner["label"] != job.platform:
        return f"it ran on {runner['label']}, and main's catalogue runs it on {job.platform}"
    now_runs = job.container if job.container is not None else images.get(job.platform)
    if now_runs is None:
        return f"the push does not know the image {job.platform} runs now"
    if not runner["image"]:
        return "its verdict file names no image"
    if runner["image"] != now_runs:
        return f"it ran on {runner['image']}, and {job.platform} runs {now_runs} now"
    if toolchain.get("python") != (job.python or ""):
        ran = toolchain.get("python")
        return (
            f"it ran Python {ran or 'unnamed'}, and main's catalogue names {job.python or 'none'}"
        )
    for word in job.toolchain:
        identity = toolchain.get(word)
        if not identity or identity == ABSENT:
            return f"its verdict file names no identity for its toolchain's {word}"
    return None


def _judge(
    job: Job,
    reuse: Reuse,
    key: str,
    source: _Source,
    files: Mapping[str, list[_File]],
    now: float,
    images: Mapping[str, str],
    merge_refs: _MergeRefs,
) -> tuple[bool, str, _File | None, str]:
    """Whether *key* of *job* may be replayed from *source*, why, the file it stands on, and
    what that file named as the commit it ran on (empty until that is judged)."""
    listed = source.listed.get(key)
    if listed is None:
        return False, f"attempt 1 of run {source.run} has no job named {key}", None, ""
    if listed.get("status") != "completed":
        return False, f"its job in attempt 1 is {listed.get('status') or 'of no status'}", None, ""
    if listed.get("conclusion") != "success":
        concluded = listed.get("conclusion") or "nothing"
        why = f"it concluded {concluded} in attempt 1; a later attempt never counts"
        return False, why, None, ""
    mine = files.get(key, [])
    if not mine:
        return False, "attempt 1 left no verdict file for it", None, ""
    if len(mine) > 1:
        return False, f"{len(mine)} verdict files name it in attempt 1", None, ""
    [file] = mine
    result = file.result
    if result.attempt != 1:
        return False, f"its verdict file is attempt {result.attempt}'s, not attempt 1's", None, ""
    if result.result != verdict.SUCCESS:
        return False, f"its verdict file says {result.result}", None, ""
    identity = result.identity
    if identity is None:
        return False, f"its verdict file names no head, not #{source.pull_request}'s", None, ""
    if identity.head_sha == source.head:
        named = HEAD_NAMED
    elif source.event == QUEUE_EVENT:
        why = f"its verdict file names {identity.head_sha}, not the queue commit {source.head}"
        return False, why, None, ""
    else:
        why_not = merge_refs.why_not(identity.head_sha)
        if why_not is not None:
            why = (
                f"its verdict file names {identity.head_sha}, not #{source.pull_request}'s head"
                f" or its merge ref: {why_not}"
            )
            return False, why, None, ""
        named = MERGE_REF_NAMED
    if identity.external_revisions:
        return (
            False,
            "its pass read a partner repository, whose head the push cannot hold",
            None,
            named,
        )
    if result.run_id != source.run:
        ran = f"run {result.run_id}" if result.run_id is not None else "no run"
        return False, f"its verdict file names {ran}, not run {source.run}", None, named
    elsewhere = _ran_on(job, result, images)
    if elsewhere is not None:
        return False, elsewhere, None, named
    if file.quarantined:
        return False, f"its pass waived {len(file.quarantined)} quarantined failure(s)", None, named
    if file.rerun is not None:
        return False, "its verdict file names a rerun", None, named
    if reuse.kind == "ttl":
        age = now - source.started
        if age < 0:
            return False, "attempt 1 started after now", None, named
        if reuse.hours is None or age >= reuse.hours * 3600:
            return False, f"attempt 1 started {age / 3600:.1f}h ago, past its {reuse}", None, named
    elif reuse.kind != "tree":
        return False, f"its class {reuse} is not one a push replays", None, named
    return True, f"a green verdict of attempt 1 ({reuse})", file, named


def _receipt(
    plan: Sequence[Job],
    source: _Source,
    decisions: Sequence[Decision],
    *,
    sha: str,
    workflow: str,
    controller_revision: str,
    toolchain: Mapping[str, str],
    images: Mapping[str, str],
) -> dict[str, Any]:
    digests = {
        "plan": _digest(_canonical([[d.key, d.reuse] for d in decisions])),
        "attempt-1-jobs": source.jobs_digest,
    }
    for d in decisions:
        if d.replayed and d.record_digest:
            digests[f"record:{d.key}"] = d.record_digest
    return {
        "controller_revision": controller_revision,
        "head_sha": sha,
        "tree_oid": source.tree,
        "external_revisions": {},
        "toolchain": dict(toolchain),
        "evidence_digests": digests,
        "source_run": {
            "workflow": workflow,
            "id": source.run,
            "attempt": 1,
            "url": source.run_url,
            "pull_request": source.pull_request,
            "head": source.head,
            "event": source.event,
        },
        "images": dict(sorted(images.items())),
        "jobs": [d.to_json() for d in decisions],
    }


# ── the decision ─────────────────────────────────────────────────────────────────────────────


def _check_inputs(
    plan: Sequence[object],
    texts: Mapping[str, object],
    controller_revision: object,
    toolchain: object,
    now: object,
    images: object,
) -> tuple[Job, ...]:
    """The plan, once every input the caller gave is what it must be."""
    for name, value in texts.items():
        if not isinstance(value, str):
            raise Refused(f"the {name} {value!r} is not text")
    jobs = tuple(job for job in plan if isinstance(job, Job))
    if len(jobs) != len(plan):
        raise Refused("the plan holds something that is not a catalogue job")
    if not isinstance(controller_revision, str) or not _OBJECT.fullmatch(controller_revision):
        raise Refused(f"the controller revision {controller_revision!r} is not a full object id")
    if (
        not isinstance(toolchain, Mapping)
        or not toolchain
        or not all(
            isinstance(k, str) and k and isinstance(v, str) and v for k, v in toolchain.items()
        )
    ):
        raise Refused("the toolchain must map names to non-empty strings, and name one")
    if not isinstance(images, Mapping) or not all(
        isinstance(k, str) and k and isinstance(v, str) and v for k, v in images.items()
    ):
        raise Refused("the images must map runner labels to non-empty image names")
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise Refused(f"now {now!r} is not a finite time")
    workflow = str(texts["workflow"])
    if not workflow or "/" in workflow:
        raise Refused(f"the workflow {workflow!r} is not a workflow file's name")
    ids = [job.id for job in jobs]
    if len(set(ids)) != len(ids):
        raise Refused("the plan names a job twice")
    return jobs


def decide(
    get: Get,
    *,
    event: str,
    ref: str,
    repo: str,
    sha: str,
    before: str,
    plan: Iterable[Job],
    records: Records,
    verified: Verified,
    now: float,
    controller_revision: str,
    toolchain: Mapping[str, str],
    images: Mapping[str, str],
    workflow: str = WORKFLOW,
) -> Replay:
    """Each planned job's decision for one push: replayed from the pull request's first run's
    attempt 1, or run. *images* is the image each runner label runs now, as the push read it
    (``<ImageOS>/<ImageVersion>``); a job on a label it does not name runs. :class:`Refused` for
    the caller's own inputs; never raises after that."""
    texts = dict(event=event, ref=ref, repo=repo, sha=sha, before=before, workflow=workflow)
    planned = _check_inputs(tuple(plan), texts, controller_revision, toolchain, now, images)
    if event != "push" or ref != MAIN:
        why = f"a {event or 'unnamed'} run on {ref or 'no ref'} is not a push to main"
        return _everything(planned, f"{why}, so nothing is replayed", workflow)
    if not _SHA.fullmatch(sha):
        return _everything(planned, f"the pushed commit {sha!r} is not a full commit id", workflow)
    if before == ZERO or not _SHA.fullmatch(before):
        return _everything(
            planned, "the push names no previous commit of main, so nothing is replayed", workflow
        )
    try:
        source = _source(get, repo=repo, sha=sha, before=before, workflow=workflow)
        if isinstance(source, str):
            return _everything(planned, source, workflow)
        refusal = verified(source.head)
        if refusal is not None:
            return _everything(
                planned,
                f"the merge-verdict on #{source.pull_request}'s"
                f" {'queue commit' if source.event == QUEUE_EVENT else 'head'} does not count:"
                f" {refusal}",
                workflow,
            )
        files = _files(records(source.run), source.run)
    except Exception as exc:  # noqa: BLE001 - any error is a full run, never a replay
        return _everything(planned, f"could not find out: {exc}", workflow)

    merge_refs = _MergeRefs(get, repo, source, before)
    decisions: list[Decision] = []
    for job in planned:
        reuse = reuse_class(job)
        mine: list[Decision] = []
        for key in keys_of(job):
            ttl_age = now - source.started if reuse.kind == "ttl" else None
            if reuse == NEVER:
                ok, reason, file, named = False, _never(job), None, ""
            else:
                ok, reason, file, named = _judge(
                    job, reuse, key, source, files, now, images, merge_refs
                )
            mine.append(
                Decision(
                    key,
                    job.id,
                    str(reuse),
                    ok,
                    reason,
                    source.run_url if ok else "",
                    ttl_age,
                    _digest(file.raw) if ok and file else None,
                    (file.result.runner or {}).get("image", "") if ok and file else "",
                    named,
                )
            )
        failed = [d for d in mine if not d.replayed]
        if failed and len(mine) > 1:
            why = f"another shard of {job.id} runs ({failed[0].key}), so every shard runs"
            mine = [
                (
                    d
                    if not d.replayed
                    else Decision(
                        d.key,
                        d.job_id,
                        d.reuse,
                        False,
                        why,
                        "",
                        d.ttl_age_seconds,
                        record_names=d.record_names,
                    )
                )
                for d in mine
            ]
        decisions.extend(mine)
    receipt = _receipt(
        planned,
        source,
        decisions,
        sha=sha,
        workflow=workflow,
        controller_revision=controller_revision,
        toolchain=toolchain,
        images=images,
    )
    reason = (
        f"the pushed commit is #{source.pull_request}'s queue commit and contains the previous"
        f" main; its first {workflow} merge_group run ({source.run}) is read on attempt 1 only,"
        " and its merge-verdict counts"
        if source.event == QUEUE_EVENT
        else f"#{source.pull_request}'s head has this tree and contains the previous main; its"
        f" first {workflow} run ({source.run}) is read on attempt 1 only, and its merge-verdict"
        " counts"
    )
    return Replay(
        reason,
        tuple(decisions),
        workflow,
        source.pull_request,
        source.head,
        source.tree,
        source.run,
        source.run_url,
        receipt,
    )


# ── the source run's records ──────────────────────────────────────────────────────────────────

Binary = Callable[[str], bytes]
"""One API read of bytes: a path under the API root to what it answers (an artifact's zip)."""

#: The most pages of a run's artifacts read: a merge-tier run uploads a few per job.
_ARTIFACT_PAGES = 10


def source_records(get: Get, binary: Binary, *, repo: str, run_id: int) -> list[bytes]:
    """The job records attempt 1 of run *run_id* uploaded, each as the bytes of its one record.

    Every artifact the run lists is read, page by page against a count that holds from page to
    page. Of the record artifacts (``ci-record-``), each must name this run as the one that
    uploaded it; one named for a later attempt is that attempt's and is passed over, and an
    expired one too, so its job has no verdict file and runs. One named for attempt 1 must have
    been made while attempt 1 ran, within the archive bound, and hold the one record its own name
    names, of this run and attempt 1, read as the telemetry collector reads one
    (``telemetry.record_bytes``). Anything else raises, and the replay runs every job."""
    where = f"/repos/{repo}/actions/runs/{run_id}"
    attempt = _mapping(get(f"{where}/attempts/1"), "attempt 1")
    since = telemetry._at(attempt.get("run_started_at"))
    until = telemetry._at(attempt.get("updated_at"))
    if since is None or until is None:
        raise Unanswered(f"attempt 1 of run {run_id} names no start or no end")
    artifacts: list[object] = []
    total: int | None = None
    for page in range(1, _ARTIFACT_PAGES + 1):
        answer = _mapping(
            get(f"{where}/artifacts?per_page={_PER_PAGE}&page={page}"), f"run {run_id}'s artifacts"
        )
        count = answer.get("total_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise Unanswered(f"run {run_id}'s artifacts name no count, so one may be unseen")
        if total is not None and count != total:
            raise Unanswered(f"run {run_id}'s artifact count moved from {total} to {count}")
        total = count
        listed = answer.get("artifacts")
        if not isinstance(listed, list):
            raise Unanswered(f"run {run_id}'s artifacts are not a list")
        artifacts.extend(listed)
        if len(artifacts) >= total or not listed:
            break
    if len(artifacts) != total:
        raise Unanswered(f"{len(artifacts)} of run {run_id}'s {total} artifacts were listed")
    suffix = f"-{run_id}-1"
    records: list[bytes] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise Unanswered(f"run {run_id} lists an artifact that is not one")
        name = artifact.get("name")
        if not isinstance(name, str) or not name.startswith(verdict.RECORD_ARTIFACT_PREFIX):
            continue
        made_by = artifact.get("workflow_run")
        if not isinstance(made_by, Mapping) or made_by.get("id") != run_id:
            raise Unanswered(f"{name} was not uploaded by run {run_id}")
        if not name.endswith(suffix) or artifact.get("expired"):
            continue
        made = telemetry._at(artifact.get("created_at"))
        if made is None or not since <= made <= until:
            raise Unanswered(f"{name} is named for attempt 1 and was not made while it ran")
        size = artifact.get("size_in_bytes")
        bound = telemetry.MAX_RECORD_ARCHIVE_BYTES
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= bound:
            raise Unanswered(f"{name}: an archive of {size!r} bytes, not one within {bound}")
        identifier = artifact.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, int):
            raise Unanswered(f"{name} names no artifact id")
        data = binary(f"/repos/{repo}/actions/artifacts/{identifier}/zip")
        if not isinstance(data, bytes) or len(data) > bound:
            raise Unanswered(f"{name}: the archive read is not bytes within {bound}")
        try:
            raw, _ = telemetry.record_bytes(name, data, run_id, 1)
        except telemetry.CollectError as exc:
            raise Unanswered(str(exc)) from None
        records.append(raw)
    return records


# ── the command ───────────────────────────────────────────────────────────────────────────────


def _pairs(values: Sequence[str], what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in values:
        name, sep, text = value.partition("=")
        if not sep or not name or not text or name in out:
            raise Refused(f"{what} {value!r} is not NAME=VALUE, or names {name!r} twice")
        out[name] = text
    return out


def _verifier(api: str, token: str, repo: str, git_dir: Path) -> Verified:
    """Whether the controller's merge verdict on a head counts (``merge_verdict.verify``)."""

    def verified(head: str) -> str | None:
        result = merge_verdict.verify(
            merge_verdict.github_get(api, token), repo=repo, sha=head, git_dir=git_dir
        )
        return None if isinstance(result, merge_verdict.Accepted) else result.reason

    return verified


def main(
    argv: list[str] | None = None,
    *,
    get: Get | None = None,
    binary: Binary | None = None,
    verified: Verified | None = None,
    clock: Callable[[], float] = time.time,
) -> int:
    """``python -m tools.ci replay``: the push's plan job's answer, per planned job, from the
    run's own environment (``GITHUB_EVENT_NAME``, ``GITHUB_REF``, ``GITHUB_REPOSITORY``,
    ``GITHUB_SHA``, and ``BEFORE``, the commit the push replaced), with ``GH_TOKEN`` for the API.
    It prints a line per planned key, writes ``replayed=`` (the keys replayed, as JSON) and
    ``replays=`` to ``GITHUB_OUTPUT``, the reason and the lines to ``GITHUB_STEP_SUMMARY``, and
    the receipt to ``--receipt``. Exit 0 with an answer, which may be "every job runs"; 2 when
    the caller's own inputs are not what they must be, and then nothing is read."""
    parser = argparse.ArgumentParser(prog="python -m tools.ci replay", description=__doc__)
    planned = parser.add_mutually_exclusive_group(required=True)
    planned.add_argument("--plan", type=Path, help="a JSON list of planned job ids")
    planned.add_argument("--tier", help="every job of this tier of main's catalogue (the shadow)")
    parser.add_argument("--root", type=Path, default=catalogue.REPO, help="main's own checkout")
    parser.add_argument(
        "--git-dir",
        type=Path,
        help="a repository holding main's history, for the verdict's controller revision"
        " (default: --root)",
    )
    parser.add_argument(
        "--beside-tested",
        choices=("true", "false"),
        help="the tree check's answer for this push: tally each decision beside it (#3919 (b1))",
    )
    parser.add_argument("--image", action="append", default=[], metavar="LABEL=IMAGE")
    parser.add_argument("--toolchain", action="append", default=[], metavar="NAME=VERSION")
    parser.add_argument("--controller-revision", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--workflow", default=WORKFLOW)
    args = parser.parse_args(argv)
    env = os.environ
    repo = env.get("GITHUB_REPOSITORY", "")
    api, token = env.get("GITHUB_API_URL", "https://api.github.com"), env.get("GH_TOKEN", "")
    try:
        cat = catalogue.read(args.root)
        if args.tier is not None:
            ids = [job.id for job in cat.tier(args.tier)]
            if not ids:
                raise Refused(f"main's catalogue has no job in the {args.tier} tier")
        else:
            ids = json.loads(args.plan.read_text(encoding="utf-8"))
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise Refused(f"{args.plan} is not a JSON list of job ids")
        known = {job.id for job in cat.jobs}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise Refused(f"the plan names {', '.join(unknown)}, which main's catalogue lacks")
        plan = [cat.job(i) for i in ids]
        images = _pairs(args.image, "the image")
        toolchain = _pairs(args.toolchain, "the toolchain") or {"python": platform.python_version()}
    except (Refused, OSError, ValueError, catalogue.CatalogueError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    reader = get or tree_tested.github_get(api, token)
    fetch = binary or (lambda path: telemetry._gh_binary("api", path.lstrip("/")))
    try:
        replay = decide(
            reader,
            event=env.get("GITHUB_EVENT_NAME", ""),
            ref=env.get("GITHUB_REF", ""),
            repo=repo,
            sha=env.get("GITHUB_SHA", ""),
            before=env.get("BEFORE", ""),
            plan=plan,
            records=lambda run: source_records(reader, fetch, repo=repo, run_id=run),
            verified=verified or _verifier(api, token, repo, args.git_dir or args.root),
            now=clock(),
            controller_revision=args.controller_revision,
            toolchain=toolchain,
            images=images,
            workflow=args.workflow,
        )
    except Refused as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    lines = replay.lines()
    receipt = replay.receipt or {
        "reason": replay.reason,
        "jobs": [d.to_json() for d in replay.jobs],
    }
    args.receipt.write_text(json.dumps(receipt, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    replayed = sorted(d.key for d in replay.jobs if d.replayed)
    print(replay.reason)
    for line in lines:
        print(line)
    outputs = f"replayed={json.dumps(replayed)}\nreplays={'true' if replayed else 'false'}\n"
    summary = "\n".join([f"### {args.workflow}: the push's replay", "", replay.reason + ".", ""])
    summary += "".join(f"- {line}\n" for line in lines)
    if args.beside_tested is not None:
        beside = tally(replay, tested=args.beside_tested == "true", every_push=cat.every_push or ())
        for line in beside.lines():
            print(line)
        summary += "\n### the push's replay, in shadow, beside the tree check\n\n"
        summary += "".join(f"- {line}\n" for line in beside.lines())
    for name, content in (("GITHUB_OUTPUT", outputs), ("GITHUB_STEP_SUMMARY", summary)):
        target = env.get(name)
        if target:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(content)
    return 0


# ── the decision shadow (#3919 (b1)) ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tally:
    """A push's replay decisions beside its tree check's answer, which is what the push did.

    On a push the tree check called tested, the push ran ``[tier.merge] every_push`` and carried
    every other job from the pull request's run: ``would_run`` names each carried key the replay
    would run instead, with why. On an untested one the push ran every job: ``would_replay``
    names each key the replay would have carried. ``agreed`` counts the rest. It judges nothing:
    the push stands on the tree check's answer."""

    tested: bool
    agreed: int
    would_run: tuple[tuple[str, str], ...]
    would_replay: tuple[str, ...]

    def lines(self) -> list[str]:
        answer = "tested" if self.tested else "not tested"
        out = [
            f"the tree check: {answer}; the replay agrees on {self.agreed} key(s),"
            f" would run {len(self.would_run)} it carried and replay {len(self.would_replay)}"
            " it ran"
        ]
        out += [f"would run {key}: {why}" for key, why in self.would_run]
        out += [f"would replay {key}, which this push ran" for key in self.would_replay]
        return out


def tally(replay: Replay, *, tested: bool, every_push: Iterable[str]) -> Tally:
    """Each of *replay*'s decisions beside what the push did: on a tested push, a job of
    *every_push* ran and every other job was carried; on an untested one, every job ran."""
    always = frozenset(every_push)
    agreed = 0
    would_run: list[tuple[str, str]] = []
    would_replay: list[str] = []
    for decision in replay.jobs:
        ran = not tested or decision.job_id in always
        if decision.replayed == (not ran):
            agreed += 1
        elif decision.replayed:
            would_replay.append(decision.key)
        else:
            would_run.append((decision.key, decision.reason))
    return Tally(tested, agreed, tuple(sorted(would_run)), tuple(sorted(would_replay)))


# ── the outcome shadow, for the live checks (#3919 (b1)) ────────────────────────────────────


@dataclass(frozen=True)
class Shadow:
    """Each key a push's receipts replayed, against the push run's own live record of it.

    ``agreed`` passed live on the first attempt; ``disagreed`` did not, each with why; and
    ``reasons`` names each receipt that replayed nothing, with its own reason. The shadow judges
    nothing: the run is still judged by its live records."""

    agreed: tuple[str, ...]
    disagreed: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]

    def lines(self) -> list[str]:
        replayed = len(self.agreed) + len(self.disagreed)
        out = [
            f"{replayed} replayed, {len(self.agreed)} passed live, {len(self.disagreed)} did not"
        ]
        out += [f"disagreed: {key}: {why}" for key, why in self.disagreed]
        out += [f"replayed nothing: {why}" for why in self.reasons]
        return out


def shadow(
    receipts: Sequence[Mapping[str, Any]],
    live: Sequence[verdict.JobResult],
    *,
    sha: str,
    run_id: int,
) -> Shadow:
    """Each key the receipts replayed, against this run's first-attempt record of it: a
    ``success`` of this run agrees; a failure, no record, two records, or a record of another
    run disagrees. A receipt that replayed nothing adds its reason. A receipt of another pushed
    commit, or one naming no jobs, is refused, since it is no shadow of this run."""
    first: dict[str, list[verdict.JobResult]] = {}
    for result in live:
        if result.attempt == 1:
            first.setdefault(result.key, []).append(result)
    agreed: list[str] = []
    disagreed: list[tuple[str, str]] = []
    reasons: list[str] = []
    for receipt in receipts:
        head = receipt.get("head_sha")
        if head is not None and head != sha:
            raise Refused(f"a receipt names head {head!r}, not the pushed commit {sha}")
        decisions = receipt.get("jobs")
        if not isinstance(decisions, list) or not all(isinstance(d, Mapping) for d in decisions):
            raise Refused("a receipt names no list of decisions")
        replayed = [d for d in decisions if d.get("decision") == "replayed"]
        if not replayed:
            reasons.append(str(receipt.get("reason") or "no key was replayed"))
            continue
        for decision in replayed:
            key = decision.get("key")
            if not isinstance(key, str) or not key:
                raise Refused(f"a replayed decision names no key: {decision!r}")
            records = first.get(key, [])
            if not records:
                disagreed.append((key, "this run left no first-attempt record of it"))
            elif len(records) > 1:
                disagreed.append((key, f"this run left {len(records)} first-attempt records"))
            elif records[0].run_id != run_id:
                disagreed.append((key, f"its record names run {records[0].run_id}, not {run_id}"))
            elif records[0].result != "success":
                disagreed.append((key, f"it ran {records[0].result} here"))
            else:
                agreed.append(key)
    return Shadow(tuple(sorted(agreed)), tuple(sorted(disagreed)), tuple(reasons))


def shadow_main(argv: list[str] | None = None) -> int:
    """``python -m tools.ci replay-shadow``: the push's shadow tally (#3919 (b1)), from its
    receipts and the records this run's stage jobs uploaded (``--records``, laid out as
    ``download-artifact`` lays them), for ``GITHUB_SHA`` and ``GITHUB_RUN_ID``. It prints the
    tally and writes it to ``GITHUB_STEP_SUMMARY``. A receipt that is not there is a replay that
    did not answer. Exit 0 with a tally, whatever it says; 2 when the inputs cannot be read."""
    parser = argparse.ArgumentParser(prog="python -m tools.ci replay-shadow")
    parser.add_argument("--receipt", action="append", required=True, type=Path)
    parser.add_argument("--records", required=True)
    args = parser.parse_args(argv)
    env = os.environ
    receipts: list[Mapping[str, Any]] = []
    missing: list[str] = []
    try:
        run_id = int(env.get("GITHUB_RUN_ID", ""))
        attempt = int(env.get("GITHUB_RUN_ATTEMPT", "1"))
        for path in args.receipt:
            if not path.is_file():
                missing.append(f"no receipt at {path.name}: the replay did not answer")
                continue
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, Mapping):
                raise Refused(f"{path.name} is not a receipt")
            receipts.append(loaded)
        live = verdict.read_records(args.records, run_id=run_id, attempt=attempt)
        tally = shadow(receipts, live, sha=env.get("GITHUB_SHA", ""), run_id=run_id)
    except (Refused, verdict.Refused, OSError, ValueError) as exc:
        print(f"replay-shadow: {exc}", file=sys.stderr)
        return 2
    lines = tally.lines() + [f"replayed nothing: {why}" for why in missing]
    for line in lines:
        print(line)
    summary = "### the push's replay, in shadow\n\n" + "".join(f"- {line}\n" for line in lines)
    target = env.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(summary)
    return 0
