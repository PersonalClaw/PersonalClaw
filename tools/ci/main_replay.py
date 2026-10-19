"""What a push to ``main`` may replay from its pull request's run, and what it must run again.

A pull request's merge-tier run is its tree's one expensive execution. When the push that merges
it lands the very tree that run tested, a job that passed there need not run again; every other
job runs. :func:`decide` says which, per planned job, and writes the push's reuse receipt. It
replaces ``tools/ci/tree_tested.py``'s all-or-nothing answer with one per job (#3919).

Nothing calls it yet: the push's plan job in ``merge.yml`` will, once that workflow writes a
verdict file per job (#3915) and the controller App posts the merge verdict (#4584). The caller
passes in what only it can read: the planned jobs (from ``main``'s own catalogue), the verdict
files of a run's first attempt, and whether the controller's merge verdict on the head counts
(``tools/ci/merge_verdict.py``).

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
  empty quarantined-failure list and no rerun;
- a ``ttl`` job's attempt 1 started less than its TTL before ``now``, and not after it;
- every other shard of a sharded job replays too.

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

import hashlib
import json
import math
import re
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import verdict
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

#: Where a workflow file lives, as a run's ``path`` names it.
WORKFLOWS = ".github/workflows/"

_SHA = re.compile(r"[0-9a-f]{40}")
_OBJECT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

Records = Callable[[int], Sequence[bytes]]
"""The verdict files a run's attempt 1 wrote, as bytes, by the run's id."""

Verified = Callable[[str], "str | None"]
"""Whether the controller's merge verdict on a head counts: None when it does, else why not."""


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

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "job": self.job_id,
            "decision": "replayed" if self.replayed else "run",
            "reuse": self.reuse,
            "reason": self.reason,
            "run_url": self.run_url or None,
            "ttl_age_seconds": self.ttl_age_seconds,
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
    head: str
    tree: str
    run: int
    run_url: str
    started: float
    listed: Mapping[str, Mapping[str, Any]]
    jobs_digest: str


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


def _source(get: Get, *, repo: str, sha: str, before: str, workflow: str) -> _Source | str:
    """The pull request's first run of *workflow* and its attempt 1, or why there is none."""
    tree = _tree_of(get, repo, sha)
    pulls = get(f"/repos/{repo}/commits/{sha}/pulls")
    if not isinstance(pulls, list):
        raise Unanswered("the pull requests of the pushed commit are not a list")
    merged = [
        pr
        for pr in pulls
        if isinstance(pr, Mapping)
        and pr.get("merge_commit_sha") == sha
        and pr.get("merged_at")
        and isinstance(pr.get("base"), Mapping)
        and pr["base"].get("ref") == "main"
    ]
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
    run_id = _integer(first, "id", "the first run")
    attempt = _mapping(get(f"/repos/{repo}/actions/runs/{run_id}/attempts/1"), "attempt 1")
    if _integer(attempt, "run_attempt", "attempt 1") != 1:
        raise Unanswered(f"run {run_id}'s attempt 1 says it is attempt {attempt['run_attempt']}")
    status, conclusion = attempt.get("status"), attempt.get("conclusion")
    if status != "completed":
        return f"attempt 1 of {workflow} run {run_id} is {status or 'of no status'}"
    # A run with a red job concludes failure, and its green jobs are still judged one by one;
    # a run cut short (cancelled, timed out) is judged by nothing.
    if conclusion not in ("success", "failure"):
        return f"attempt 1 of {workflow} run {run_id} concluded {conclusion or 'nothing'}"
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
    return _Source(number, head, tree, run_id, url, started, listed, _digest(_canonical(listing)))


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


# ── one key ──────────────────────────────────────────────────────────────────────────────────


def _judge(
    reuse: Reuse,
    key: str,
    source: _Source,
    files: Mapping[str, list[_File]],
    now: float,
) -> tuple[bool, str, _File | None]:
    """Whether *key* may be replayed from *source*, why, and the file it stands on."""
    job = source.listed.get(key)
    if job is None:
        return False, f"attempt 1 of run {source.run} has no job named {key}", None
    if job.get("status") != "completed":
        return False, f"its job in attempt 1 is {job.get('status') or 'of no status'}", None
    if job.get("conclusion") != "success":
        concluded = job.get("conclusion") or "nothing"
        return False, f"it concluded {concluded} in attempt 1; a later attempt never counts", None
    mine = files.get(key, [])
    if not mine:
        return False, "attempt 1 left no verdict file for it", None
    if len(mine) > 1:
        return False, f"{len(mine)} verdict files name it in attempt 1", None
    [file] = mine
    result = file.result
    if result.attempt != 1:
        return False, f"its verdict file is attempt {result.attempt}'s, not attempt 1's", None
    if result.result != verdict.SUCCESS:
        return False, f"its verdict file says {result.result}", None
    identity = result.identity
    if identity is None or identity.head_sha != source.head:
        named = identity.head_sha if identity else "no head"
        return False, f"its verdict file names {named}, not #{source.pull_request}'s head", None
    if identity.external_revisions:
        return False, "its pass read a partner repository, whose head the push cannot hold", None
    if file.quarantined:
        return False, f"its pass waived {len(file.quarantined)} quarantined failure(s)", None
    if file.rerun is not None:
        return False, "its verdict file names a rerun", None
    if reuse.kind == "ttl":
        age = now - source.started
        if age < 0:
            return False, "attempt 1 started after now", None
        if reuse.hours is None or age >= reuse.hours * 3600:
            return False, f"attempt 1 started {age / 3600:.1f}h ago, past its {reuse}", None
    elif reuse.kind != "tree":
        return False, f"its class {reuse} is not one a push replays", None
    return True, f"a green verdict of attempt 1 ({reuse})", file


def _receipt(
    plan: Sequence[Job],
    source: _Source,
    decisions: Sequence[Decision],
    *,
    sha: str,
    workflow: str,
    controller_revision: str,
    toolchain: Mapping[str, str],
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
        },
        "jobs": [d.to_json() for d in decisions],
    }


# ── the decision ─────────────────────────────────────────────────────────────────────────────


def _check_inputs(
    plan: Sequence[object],
    texts: Mapping[str, object],
    controller_revision: object,
    toolchain: object,
    now: object,
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
    workflow: str = WORKFLOW,
) -> Replay:
    """Each planned job's decision for one push: replayed from the pull request's first run's
    attempt 1, or run. :class:`Refused` for the caller's own inputs; never raises after that."""
    texts = dict(event=event, ref=ref, repo=repo, sha=sha, before=before, workflow=workflow)
    planned = _check_inputs(tuple(plan), texts, controller_revision, toolchain, now)
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
                f"the merge-verdict on #{source.pull_request}'s head does not count: {refusal}",
                workflow,
            )
        files = _files(records(source.run), source.run)
    except Exception as exc:  # noqa: BLE001 - any error is a full run, never a replay
        return _everything(planned, f"could not find out: {exc}", workflow)

    decisions: list[Decision] = []
    for job in planned:
        reuse = reuse_class(job)
        mine: list[Decision] = []
        for key in keys_of(job):
            ttl_age = now - source.started if reuse.kind == "ttl" else None
            if reuse == NEVER:
                ok, reason, file = False, _never(job), None
            else:
                ok, reason, file = _judge(reuse, key, source, files, now)
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
                )
            )
        failed = [d for d in mine if not d.replayed]
        if failed and len(mine) > 1:
            why = f"another shard of {job.id} runs ({failed[0].key}), so every shard runs"
            mine = [
                (
                    d
                    if not d.replayed
                    else Decision(d.key, d.job_id, d.reuse, False, why, "", d.ttl_age_seconds)
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
    )
    reason = (
        f"#{source.pull_request}'s head has this tree and contains the previous main; its first"
        f" {workflow} run ({source.run}) is read on attempt 1 only, and its merge-verdict counts"
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
