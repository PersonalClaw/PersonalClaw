"""``python -m tools.ci merge-controller``: the merge verdict's poster (#3910).

``.github/workflows/merge-verdict.yml`` runs this when a Merge run (``merge.yml``) completes.
GitHub runs that listener from the default branch, so this is ``main``'s copy whatever the
candidate brings. It reads the API only, by the run's id: never the event's text, an artifact's
contents or anything of the candidate on disk. Both trees are read from the API, so the
candidate's files are never fetched or checked out (``trust.compare_trees``).

``decide`` judges the run and writes the check run it would post. The rules, each failing
closed; a red names every rule the head fails, so one verdict says all it must fix:

1. The run is this repository's ``.github/workflows/merge.yml``, completed, on a ``pull_request``,
   a dispatch, or a queue commit of main's merge queue (``merge_group``, judged by the rules under
   "A queue commit" below). Any other run posts nothing: another workflow named Merge, which a
   candidate can add, a push to ``main`` or a schedule. Only a run of merge.yml itself can bring a
   verdict.
2. A head that changes a definition (``trust``), compared with ``main``'s tip, is judged by two
   things, both green (#4584 step 4): its own run, by every rule here, which shows the change
   works as written; and ``main``'s CI contract over its definitions, which shows it weakens
   nothing ``main``'s contract holds CI to, whatever its own copy of the contract says
   (``tools/ci/contract.py``). The contract is run only from the head's own ``pull_request``
   run once that run has passed the fork approval gate, so no head's code runs in the base
   repository without the approval its own run needed: when the run has started a job on a
   runner (``decide --start``, beside the run, read from the jobs API), and otherwise when it
   completes, if the attempt it completed ran a job on a runner. The contract's verdict is its
   run's ``contract`` job's, not the run's, so a failing ``wake`` never reads as a failed
   contract. Until the contract run reports, the head is a red that waits on it, which a
   required check reads as not passed. The poster dispatches it with the job's token, whose
   events start no workflow, so its completion raises no ``workflow_run``: the contract run's
   own last job dispatches the poster for the head (``decide --head``, the same judge), and the
   schedule's sweep of waiting reds stays behind it.
3. The head is not behind ``main``, so the squash commit lands the tree the run tested, and the
   contract judged the head against the ``main`` it would land on. A head that is behind is
   red whatever else holds, and its definitions are not compared: against ``main``'s tip, a head
   ``main`` has moved past would read as changing every definition ``main`` changed since. So no
   contract runs for it until it updates. Once main's rules queue its merges (a ``merge_queue``
   rule, :func:`queue_live`) this rule is the queue's: a head may be behind, its definitions are
   compared with the commit it was built on, and the tree that lands is its queue commit's, which
   has a verdict of its own.

**The contract run.** ``contract-branch`` points the branch ``contract-run/<head>`` at
``main``'s tip with the controller App's token, and ``dispatch`` runs ``main``'s
``contract.yml`` there with the job's own token. So the workflow that runs is ``main``'s, and
what it writes to the Actions cache is that branch's, never ``main``'s: it runs the head's
``tools/ci``, which is what it tests. It lays out the head's tree beside its checkout and runs
``python -m tools.ci contract``. A contract run counts for a head only when it is
``contract.yml`` dispatched on the head's branch at ``main``'s tip and named for the head, so
``main``'s workflow ran it on this head against the ``main`` it would land on; one at an older
tip runs again. Once the head's verdict is posted its branch is deleted, and every completed
run also sweeps away each contract branch no longer at ``main``'s tip (and, after a day, any
trusted-run branch the old path left).
4. The ``results`` job, which judges the run with ``main``'s ``verdict.py``, concluded
   ``success`` on every attempt, by the API's job list. Missing, cancelled, skipped or red on any
   attempt is red: a rerun never turns a red into a green.
5. The head's dependency review passed (#4585 step 6), and so did its sign-off check (#3908):
   the newest ``pull_request_target`` run of ``dependency-review.yml``, and of ``dco.yml``, on the
   head, main's copy of each, concluded ``success``. One that has not run, is still running or
   did not pass is red. Each is read by its path, its event and the head, so a candidate's own
   workflow named for either, which runs under ``pull_request``, never stands in for it, and a
   run on an older head of the pull request does not count. A red sign-off check names its fix.
   GitHub's Update branch makes a merge commit with no sign-off, and rule 1 of
   ``scripts/check_commit_provenance.py`` refuses it on purpose, with no exemption: a commit's
   committer is text anyone can write, so naming GitHub proves nothing, and a merge commit can
   carry a conflict resolution no one signed off. The head is updated with rebase. When a Dependency
   review or DCO run completes, the poster judges its head again from the head's own completed
   Merge run (and, for a head that changes a definition, its contract run), so they may finish
   in any order.
6. CodeQL analysed the head in every language ``main``'s CodeQL analyses name, and the pull
   request brings no alert of its own: the newest run of GitHub's dynamic CodeQL workflow on the
   head finished, with an ``Analyze (<language>)`` job that concluded ``success`` for each of
   ``main``'s languages; each language's analysis of the head is processed on the pull request's
   ref (a job's success means its results were uploaded, not yet read); and no alert is open on
   that ref that is not open on ``main``. An alert there that main does not have counts as moved,
   not brought, when it pairs one to one with an alert open on main and not reported on the pull
   request: the same rule id, the same path and the same source lines at each one's place,
   whitespace read as one space, the head's file read at the head and main's at the main commit its
   alert names (#5610: a re-indented line gets a new fingerprint, so a new number). The pull
   request's ref reports only the alerts in its diff, so for each rule, path and block the pairs
   are capped at main's copies of the block less the head's copies outside its diff: a copy added
   beside one that stays is brought. A file that cannot be read is unread, never a pair; anything
   unpaired is brought; the verdict's title names every moved pair. The ``CodeQL`` summary check is
   never read for its conclusion: GitHub computes it when the first language's analysis uploads, so
   it reads ``neutral`` on most pull requests. A red with a reason that waits (CodeQL still running
   or still processing, or the head's contract run not reported) is marked waiting, whatever its
   other reasons, and the poster judges such heads again until nothing waits, so no red says it
   waits on what has finished: by the sweep of the open pull requests whose newest verdict is a
   waiting red (on its schedule, and a few heads on every other run), posting only what changed;
   and when GitHub's CodeQL workflow or its summary check completes. The workflow's wake fires
   (proved on main), but at the run's completion, which can come before its last upload is
   processed, so the sweep stays for that gap.
7. A green carries the receipt ``verify-check-run`` reads (``merge_verdict.render_output``):
   this controller's commit on ``main``, the head and its tree, the runner and the definitions
   commit (``main``'s tip, or the head for a head that changes them, with the contract run that
   judged them), and the digests of the run's ``ci-record-*`` artifacts as the API reports them.
   A run with no record artifact has no evidence, so no green.

**A queue commit** (0.4.0 A1). GitHub builds one for each entry of main's merge queue: the entry's
pull request squashed on main's tip, or on the queue commit of the entry ahead. Its merge.yml run
(``merge_group``) is judged by rules 2, 4 and 7 as a pull request's run is, and by these, each
failing closed, all read through the API:

- **Which entry.** The run's branch is ``gh-readonly-queue/main/pr-<n>-<commit>``
  (:data:`QUEUE_BRANCH`), whose commit is the one the queue built it on, never the pull request's
  head. Main's merge queue, read once (:data:`QUEUE_QUERY`), lists an entry whose queue commit is
  the run's head, for pull request ``<n>``, open into main, built on that commit. A queue commit
  no entry lists any more (removed, or its group rebuilt) is judged by nothing: no verdict waits
  for it.
- **What it is built on.** Its one parent is that commit, and that commit is main's tip or the
  queue commit of the entry directly ahead. Each of those is on main already or carries its own
  verdict before GitHub merges past it, so every tree main reaches was judged (this replaces
  rule 3).
- **What it brings.** Its change against that parent is exactly its pull request's: the same
  ``+/-`` lines in the same files (:func:`change_reasons`).
- **Its pull request's head.** The App's newest verdict on the pull request's head is green. That
  verdict holds the head's dependency review, its sign-off check and CodeQL, each of the pull
  request's own change, which the queue commit's change is.
- **Its definitions** are compared with main's tip, so a definition an entry ahead changed counts,
  and main's CI contract runs over the queue commit itself whenever they differ.
- **Once.** A queue commit's verdict that waits on nothing is final: it is never posted again, so
  no later look can turn a commit already on main red.

**The retry note** (0.4.0 A5). A change gets :data:`RETRIES` retries, and the red after them goes
to a person. A red verdict on a pull request's run whose reasons do not all wait says which retry
of its change it is and whether its red is flake-class (:func:`retry`), in its title right after
``Red`` and in its summary; the lane's tooling retries a flake-class red by itself while the budget
lasts. The count is the run's attempts after the first, each earlier head of the pull request
(read from its force-pushes) whose newest verdict is a red that waits on nothing and that has the
head's tree or its ``+/-`` lines, and each removal of the head as it is now from main's merge
queue for failed checks. A red is flake-class only when every reason is the ``results`` job's,
every job that did not pass is a suite, end-to-end or web-unit job's, failed in tests its
``results`` job names (``verdict.FAILED_NOTICE``, all of them, by its count) or cancelled or timed
out with none, and each failed test was classified by its rerun, reads no file the pull request
changes, and has an open flaky-test issue the workflow or a maintainer filed
(:func:`flake_class`). A queue commit's red carries the same note (:func:`_queue_retry_note`).
The note changes no conclusion, and one it cannot read says so.

The verdict is ``success`` or ``failure``, never neutral, and an answer the API would not give is
``failure``, named as unread. ``post`` sends the check run with the controller App's token, and
refuses a body that is not one completed ``merge-verdict`` check run.

It is a required check: ``main-protect`` requires ``merge-verdict`` from the controller App, on
a head up to date with ``main``, since 2026-10-25 22:55Z (#3910 piece 5), so the verdict says
nothing of being advisory. Standard library only. See docs/maintainers/ci.md, "The merge
controller's poster".
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime
import hashlib
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import merge_verdict, trust, verdict, workflows

#: The run that brings a verdict: merge.yml, by its path in this repository.
MERGE_WORKFLOW = f".github/workflows/{workflows.MERGE}"
#: The events whose merge.yml run judges a head: the pull request itself, a dispatch of the
#: head's branch (how a merge run is started until #3915's flip), and a queue commit of main's
#: merge queue.
JUDGED_EVENTS = frozenset({"pull_request", "workflow_dispatch", "merge_group"})
#: A queue commit's run, by its event.
QUEUE_EVENT = "merge_group"
#: A queue commit's branch, as its run's ``head_branch`` names it: main's queue, the entry's pull
#: request, and the commit the queue built it on, its one parent (main's tip or the queue commit of
#: the entry ahead). It is never the pull request's head.
QUEUE_BRANCH = re.compile(
    r"gh-readonly-queue/main/pr-(?P<number>[1-9][0-9]*)-(?P<built_on>[0-9a-f]{40})"
)
#: The dependency review: a pull_request_target workflow, so the run is main's copy.
DEPENDENCY_REVIEW = ".github/workflows/dependency-review.yml"
#: The sign-off check: a pull_request_target workflow too, so the run is main's copy.
DCO_CHECK = ".github/workflows/dco.yml"
#: What a red sign-off check tells the contributor to do, short enough to stay whole in the title.
DCO_FIX = (
    "its run names each commit and rule it refused. A merge commit from GitHub's Update branch"
    " has no Signed-off-by, so update with rebase, or rebase locally, and push again"
)
#: GitHub's dynamic CodeQL workflow (code scanning's default setup), by its run path.
CODEQL_WORKFLOW = "dynamic/github-code-scanning/codeql"
#: The analysis key default setup's CodeQL analyses carry.
CODEQL_ANALYSIS = "dynamic/github-code-scanning/codeql:analyze"
#: The App that posts the ``CodeQL`` summary check, whose completion wakes the poster.
CODEQL_APP_ID = 57789
_CODEQL_CHECK = "CodeQL"
_PULL_HEAD = re.compile(r"refs/pull/(?P<number>[1-9][0-9]*)/head")
#: main's CI contract over a head's definitions (#4584 step 4): its workflow, by path.
CONTRACT_WORKFLOW = ".github/workflows/contract.yml"
#: The branch a head's contract runs on: this prefix, then the head's full id. It points at
#: main's tip, so the workflow that runs there is main's, and its cache is the branch's.
CONTRACT_PREFIX = "contract-run/"
#: What contract.yml's ``run-name`` names each run: the head it judges.
CONTRACT_TITLE = "Contract {head}"
#: contract.yml's job that runs main's contract: its conclusion is the contract's.
CONTRACT_JOB = "contract"
#: The old path's trusted-run branches, which the sweep still deletes.
TRUSTED_PREFIX = "trusted-run/"
#: A reason that waits, on CodeQL to finish or to process or on the head's contract run to
#: report, which a later look clears.
WAITING = "waiting: "
#: The waiting reason of a head whose contract run has not reported.
WAITING_CONTRACT = f"{WAITING}main's CI contract over its definitions "
#: The fenced block a red's output text carries while any of its reasons waits.
WAITING_FENCE = "merge-verdict-waiting"
#: A trusted-run branch the old path left, older than this, is swept away.
SWEEP_AFTER = datetime.timedelta(days=1)
#: merge.yml's job that judges the run with main's verdict.py.
RESULTS_JOB = "results"
#: The artifacts each planned entry uploads: its record, the evidence a green names.
RECORD_PREFIX = "ci-record-"
API = "https://api.github.com"
_PER_PAGE = 100
_MAX_PAGES = 30
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_BODY_KEYS = frozenset(
    {"name", "head_sha", "status", "conclusion", "output", "details_url", "external_id"}
)

Get = merge_verdict.Get
#: ``post(path, body)``: the API's status and its decoded answer; status 0 is no answer.
Post = Callable[[str, Mapping[str, object]], tuple[int, object]]
#: ``api(method, path, body)``: the same, for any method; ``body`` None sends none.
Api = Callable[[str, str, "Mapping[str, object] | None"], tuple[int, object]]
#: ``query(text, variables)``: one GraphQL read's decoded answer; raises OSError for no answer.
Query = Callable[[str, Mapping[str, object]], Any]


class Unread(Exception):
    """An answer the API would not give, or gave in a shape this does not read."""


def _read(get: Get, path: str) -> Any:
    try:
        return get(path)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise Unread(f"GET {path}: {exc}") from exc


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _pages(get: Get, path: str, key: str) -> list[Mapping[str, Any]]:
    """Every item of a paged listing, against the count its first page gives."""
    items: list[Mapping[str, Any]] = []
    total: int | None = None
    joiner = "&" if "?" in path else "?"
    for page in range(1, _MAX_PAGES + 1):
        answer = _read(get, f"{path}{joiner}per_page={_PER_PAGE}&page={page}")
        count = _int(answer.get("total_count")) if isinstance(answer, Mapping) else None
        batch = answer.get(key) if isinstance(answer, Mapping) else None
        if count is None or not isinstance(batch, list):
            raise Unread(f"{path} gave no count or no {key}")
        if total is None:
            total = count
        elif count != total:
            raise Unread(f"{path}'s count changed between pages")
        if not all(isinstance(item, Mapping) for item in batch):
            raise Unread(f"one of {path}'s {key} is not an object")
        items += batch
        if len(items) > total:
            raise Unread(f"{path} gave more {key} than its count")
        if len(items) == total:
            return items
        if not batch:
            raise Unread(f"{path} counted {total} {key} but gave {len(items)}")
    raise Unread(f"{path} has more than {_MAX_PAGES} pages")


# ── The run ───────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Run:
    id: int
    attempt: int
    head_sha: str
    event: str
    url: str
    #: The branch the run was dispatched on.
    branch: str = ""
    #: The pull request of this repository the run lists with the run's head as its head, for
    #: the retry note (0.4.0 A5); None for a run that lists none (a fork's, a dispatch, a queue's).
    pull: int | None = None


def read_run(get: Get, repo: str, run_id: int) -> Run | str:
    """The merge.yml run that brings a verdict, or why this run brings none (rule 1)."""
    run = _read(get, f"/repos/{repo}/actions/runs/{run_id}")
    if not isinstance(run, Mapping):
        raise Unread(f"run {run_id} is not an object")
    repository = run.get("repository")
    owner = repository.get("full_name") if isinstance(repository, Mapping) else None
    if owner != repo:
        return f"run {run_id} is of {owner!r}, not {repo}"
    if run.get("path") != MERGE_WORKFLOW:
        return f"run {run_id} is of {run.get('path')!r}, not {MERGE_WORKFLOW}"
    if run.get("event") not in JUDGED_EVENTS:
        return f"run {run_id} is a {run.get('event')!r} run, which judges no pull request"
    if run.get("status") != "completed":
        return f"run {run_id} is {run.get('status')!r}, not completed"
    head, attempt = run.get("head_sha"), _int(run.get("run_attempt"))
    if not isinstance(head, str) or not _SHA.fullmatch(head) or attempt is None or attempt < 1:
        raise Unread(f"run {run_id} names no head or no attempt")
    branch = str(run.get("head_branch") or "")
    if run.get("event") == "workflow_dispatch" and branch.startswith(TRUSTED_PREFIX):
        return f"run {run_id} is a trusted run of the old path, which #4584 step 4 replaced"
    if run.get("event") == QUEUE_EVENT and not QUEUE_BRANCH.fullmatch(branch):
        return f"run {run_id} is a queue run on {branch!r}, which names no entry of main's queue"
    event, url = str(run.get("event")), str(run.get("html_url") or "")
    pulls = run.get("pull_requests")
    numbers = [
        _int(p.get("number"))
        for p in (pulls if isinstance(pulls, list) else [])
        if isinstance(p, Mapping)
        and isinstance(p.get("head"), Mapping)
        and p["head"].get("sha") == head
    ]
    pull = numbers[0] if len(numbers) == 1 and numbers[0] else None
    return Run(run_id, attempt, head, event, url, branch, pull)


def results_reasons(get: Get, repo: str, run: Run) -> tuple[list[str], list[str]]:
    """Why the run's ``results`` are not green on every attempt (rule 4), and the labels of the
    last attempt's ``results`` runner."""
    reasons: list[str] = []
    labels: list[str] = []
    for attempt in range(1, run.attempt + 1):
        jobs = _pages(get, f"/repos/{repo}/actions/runs/{run.id}/attempts/{attempt}/jobs", "jobs")
        found = [j for j in jobs if j.get("name") == RESULTS_JOB]
        if len(found) != 1:
            reasons.append(f"attempt {attempt} has {len(found)} `{RESULTS_JOB}` jobs, not one")
            continue
        job = found[0]
        if job.get("status") != "completed":
            reasons.append(f"attempt {attempt}'s `{RESULTS_JOB}` is {job.get('status')!r}")
        elif job.get("conclusion") != "success":
            reasons.append(f"attempt {attempt}'s `{RESULTS_JOB}` is {job.get('conclusion')!r}")
        raw = job.get("labels")
        labels = [str(x) for x in raw] if isinstance(raw, list) else []
    return reasons, labels


def passed_flakes(get: Get, repo: str, run: Run) -> list[str]:
    """The tests the flake rule passed in the run, each as its ``results`` job names it: one
    ``::notice`` titled ``verdict.FLAKE_NOTICE`` per test, read from that job's annotations on
    every attempt. Names more than ``verdict.MAX_FLAKES`` on one attempt, a notice with no text,
    or annotations that cannot be read, are unread: the controller names what main's verdict
    passed, and a results job that says more is not main's verdict."""
    names: list[str] = []
    for attempt in range(1, run.attempt + 1):
        jobs = _pages(get, f"/repos/{repo}/actions/runs/{run.id}/attempts/{attempt}/jobs", "jobs")
        found = [j for j in jobs if j.get("name") == RESULTS_JOB]
        job_id = _int(found[0].get("id")) if len(found) == 1 else None
        if job_id is None:
            raise Unread(f"attempt {attempt} has no one `{RESULTS_JOB}` job to read flakes from")
        notes = _all_pages(get, f"/repos/{repo}/check-runs/{job_id}/annotations")
        these = [
            n.get("message")
            for n in notes
            if n.get("title") == verdict.FLAKE_NOTICE and n.get("annotation_level") == "notice"
        ]
        if len(these) > verdict.MAX_FLAKES:
            raise Unread(
                f"attempt {attempt}'s `{RESULTS_JOB}` names {len(these)} flakes passed, more than"
                f" the {verdict.MAX_FLAKES} the flake rule passes"
            )
        if not all(isinstance(m, str) and m.strip() for m in these):
            raise Unread(f"attempt {attempt}'s `{RESULTS_JOB}` names a flake passed with no test")
        names += [str(m).strip() for m in these]
    return names


#: The most of one flake's name a green title holds: the summary holds it whole.
_FLAKE_TITLE_CHARS = 120


def flakes_note(names: list[str]) -> str:
    """The green title's note of the flakes it passed: empty for none."""
    if not names:
        return ""
    short = [
        n if len(n) <= _FLAKE_TITLE_CHARS else n[: _FLAKE_TITLE_CHARS - 1] + "…" for n in names
    ]
    return f" · passed {len(names)} flake{'s' if len(names) > 1 else ''}: {'; '.join(short)}"


# ── The trees ─────────────────────────────────────────────────────────────────────────────────


def api_tree(get: Get, repo: str, commit: str) -> tuple[str, trust.Tree]:
    """The commit's tree id and its tree, read from the API: every path but the trees, as
    ``git ls-tree -r`` lists them, and each regular file's text read on demand."""
    answer = _read(get, f"/repos/{repo}/git/commits/{commit}")
    tree = answer.get("tree") if isinstance(answer, Mapping) else None
    oid = tree.get("sha") if isinstance(tree, Mapping) else None
    if not isinstance(oid, str) or not _SHA.fullmatch(oid):
        raise Unread(f"commit {commit} names no tree")
    listing = _read(get, f"/repos/{repo}/git/trees/{oid}?recursive=1")
    if not isinstance(listing, Mapping) or listing.get("truncated") is not False:
        raise Unread(f"the tree of {commit} is truncated or unreadable, so a path may be unseen")
    entries = listing.get("tree")
    if not isinstance(entries, list):
        raise Unread(f"the tree of {commit} lists no paths")
    blobs: dict[str, trust.Entry] = {}
    for item in entries:
        if not isinstance(item, Mapping):
            raise Unread(f"one path of {commit}'s tree is not an object")
        path, mode, kind, sha = (item.get(k) for k in ("path", "mode", "type", "sha"))
        if kind == "tree":
            continue
        if not all(isinstance(v, str) and v for v in (path, mode, sha)) or kind not in (
            "blob",
            "commit",
        ):
            raise Unread(f"a path of {commit}'s tree has no path, mode or object")
        blobs[str(path)] = trust.Entry(mode=str(mode), oid=str(sha))
    texts: dict[str, str] = {}

    def read(entry: trust.Entry) -> str:
        if entry.oid not in texts:
            blob = _read(get, f"/repos/{repo}/git/blobs/{entry.oid}")
            content = blob.get("content") if isinstance(blob, Mapping) else None
            if not isinstance(content, str) or blob.get("encoding") != "base64":
                raise Unread(f"blob {entry.oid} has no base64 content")
            try:
                texts[entry.oid] = base64.b64decode(content).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise Unread(f"blob {entry.oid} is not UTF-8 text: {exc}") from exc
        return texts[entry.oid]

    return oid, trust.Tree(blobs=blobs, read=read)


def main_tip(get: Get, repo: str) -> str:
    ref = _read(get, f"/repos/{repo}/git/ref/heads/main")
    obj = ref.get("object") if isinstance(ref, Mapping) else None
    sha = obj.get("sha") if isinstance(obj, Mapping) else None
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise Unread("main names no commit")
    return sha


def behind_by(get: Get, repo: str, base: str, head: str) -> int:
    answer = _read(get, f"/repos/{repo}/compare/{base}...{head}")
    count = _int(answer.get("behind_by")) if isinstance(answer, Mapping) else None
    if count is None or count < 0:
        raise Unread(f"the comparison of {head} with main gives no count")
    return count


def queue_live(get: Get, repo: str) -> bool:
    """Whether main's rules queue its merges: a ``merge_queue`` rule among the rules that apply to
    main. Read from the rules themselves, so it is true exactly when GitHub queues them. An answer
    this cannot read is no: rule 3 then holds, the stricter reading."""
    try:
        rules = _read(get, f"/repos/{repo}/rules/branches/main?per_page={_PER_PAGE}")
    except Unread:
        return False
    return isinstance(rules, list) and any(
        isinstance(r, Mapping) and r.get("type") == "merge_queue" for r in rules
    )


def merge_base(get: Get, repo: str, base: str, head: str) -> str:
    """The commit a head was built on: its merge base with ``base``, as the comparison names it."""
    answer = _read(get, f"/repos/{repo}/compare/{base}...{head}")
    found = answer.get("merge_base_commit") if isinstance(answer, Mapping) else None
    sha = found.get("sha") if isinstance(found, Mapping) else None
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise Unread(f"the comparison of {head} with main names no merge base")
    return sha


def evidence(get: Get, repo: str, run: Run) -> dict[str, str]:
    """Each ``ci-record-*`` artifact of the run, by name, to the digest the API reports."""
    found: dict[str, str] = {}
    for item in _pages(get, f"/repos/{repo}/actions/runs/{run.id}/artifacts", "artifacts"):
        name, digest = item.get("name"), item.get("digest")
        if not isinstance(name, str) or not name.startswith(RECORD_PREFIX):
            continue
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise Unread(f"artifact {name} reports no sha256 digest")
        if found.setdefault(name, digest) != digest:
            raise Unread(f"artifact {name} is listed twice with two digests")
    return found


# ── The decision ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Posting:
    """The one check run a run's verdict posts."""

    head_sha: str
    conclusion: str
    output: Mapping[str, str]
    details_url: str
    external_id: str

    def body(self) -> dict[str, object]:
        return {
            "name": merge_verdict.CHECK_NAME,
            "head_sha": self.head_sha,
            "status": "completed",
            "conclusion": self.conclusion,
            "output": dict(self.output),
            "details_url": self.details_url,
            "external_id": self.external_id,
        }


@dataclass(frozen=True)
class Outcome:
    """What a Merge run's completion does: the check run to post, on the head it judges; the
    head whose contract run is started, and on which commit of ``main``; and the contract
    branch to delete once its verdict is posted. ``why`` says why nothing is posted."""

    posting: Posting | None = None
    why: str = ""
    dispatch: str | None = None
    base: str | None = None
    cleanup: str | None = None


@dataclass(frozen=True)
class Queue:
    """What judging under main's merge queue needs beyond the REST reads: the queue's one read,
    the App whose verdicts count, and whether main's rules queue its merges (:func:`queue_live`,
    which drops rule 3 for a pull request's head)."""

    query: Query
    app: int
    live: bool = False


def _red(
    run: Run,
    reasons: list[str],
    head: str,
    moved: list[Moved] | None = None,
    retried: Retry | str | None = None,
) -> Posting:
    note = [f"- {moved_note(moved)}"] if moved else []
    if isinstance(retried, Retry):
        note += [f"- {line}" for line in retried.lines()]
    elif retried:
        note.append(f"- retries not counted: {retried}")
    summary = "\n".join(
        ["This head's merge verdict is red:", "", *[f"- {r}" for r in reasons], *note, ""]
        + [f"From Merge run {run.url or run.id}, attempt {run.attempt}."]
    )
    label = f" ({retried.label()})" if isinstance(retried, Retry) else ""
    title = f"Red{label}: {reasons[0]}" + (f" ({moved_note(moved)})" if moved else "")
    output = {"title": title[:255], "summary": summary}
    waiting = [r for r in reasons if r.startswith(WAITING)]
    if waiting:
        # What it waits on, one per line: the sweep reads only the fence. A red for another
        # reason too is marked as well, so that once the wait ends its reasons are read again:
        # the fence goes with the last wait, so no head is judged again for ever.
        waits = ["contract" if r.startswith(WAITING_CONTRACT) else "codeql" for r in waiting]
        output["text"] = f"```{WAITING_FENCE}\n" + "\n".join(sorted(set(waits))) + "\n```\n"
    return Posting(head, "failure", output, run.url, f"{run.id}/{run.attempt}")


def _names(paths: list[str]) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:10])
    return shown + (f" and {len(paths) - 10} more" if len(paths) > 10 else "")


@dataclass(frozen=True)
class Contract:
    """A run of main's contract over a head's definitions."""

    id: int
    url: str
    status: str
    conclusion: str | None


def contract_run(get: Get, repo: str, head: str, base: str) -> Contract | None:
    """The newest run of ``contract.yml`` dispatched on the head's contract branch at ``base``,
    ``main``'s tip, and named for the head, in any state; None when there is none. Only such a
    run is ``main``'s workflow judging this head against the ``main`` it would land on: a run on
    the branch dispatched for another head is named for that head, and its job does not run."""
    branch = CONTRACT_PREFIX + head
    query = urllib.parse.urlencode(
        {"branch": branch, "event": "workflow_dispatch", "per_page": _PER_PAGE}
    )
    name = CONTRACT_WORKFLOW.rsplit("/", 1)[-1]
    answer = _read(get, f"/repos/{repo}/actions/workflows/{name}/runs?{query}")
    runs = answer.get("workflow_runs") if isinstance(answer, Mapping) else None
    if not isinstance(runs, list):
        raise Unread(f"{head}'s contract runs are not a list")
    ours = [
        r
        for r in runs
        if isinstance(r, Mapping)
        and r.get("path") == CONTRACT_WORKFLOW
        and r.get("event") == "workflow_dispatch"
        and r.get("head_branch") == branch
        and r.get("head_sha") == base
        and r.get("display_title") == CONTRACT_TITLE.format(head=head)
        and _int(r.get("id")) is not None
    ]
    if not ours:
        return None
    newest = max(ours, key=lambda r: int(r["id"]))
    run_id, attempt = int(newest["id"]), _int(newest.get("run_attempt"))
    if attempt is None or attempt < 1:
        raise Unread(f"contract run {run_id} names no attempt")
    # Its `contract` job's state, not the run's: the run's last job, `wake`, can fail after a
    # green contract, and a contract judged by the run would then read as failed.
    jobs = _pages(get, f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    found = [j for j in jobs if j.get("name") == CONTRACT_JOB]
    url = str(newest.get("html_url") or "")
    if not found and newest.get("status") != "completed":
        return Contract(run_id, url, str(newest.get("status")), None)
    if len(found) != 1:
        raise Unread(f"contract run {run_id} has {len(found)} `{CONTRACT_JOB}` jobs, not one")
    conclusion = found[0].get("conclusion")
    return Contract(
        run_id,
        url,
        str(found[0].get("status")),
        str(conclusion) if conclusion is not None else None,
    )


def _newest_target_run(get: Get, repo: str, head: str, path: str, what: str) -> Mapping[str, Any]:
    """The newest ``pull_request_target`` run of main's *path* on *head*, or an empty mapping:
    read by its path, its event and the head, so no other run stands in for it."""
    name = path.rsplit("/", 1)[-1]
    query = urllib.parse.urlencode(
        {"head_sha": head, "event": "pull_request_target", "per_page": _PER_PAGE}
    )
    answer = _read(get, f"/repos/{repo}/actions/workflows/{name}/runs?{query}")
    runs = answer.get("workflow_runs") if isinstance(answer, Mapping) else None
    if not isinstance(runs, list):
        raise Unread(f"{head}'s {what}s are not a list")
    ours = [
        r
        for r in runs
        if isinstance(r, Mapping)
        and r.get("path") == path
        and r.get("event") == "pull_request_target"
        and r.get("head_sha") == head
        and _int(r.get("id")) is not None
    ]
    return max(ours, key=lambda r: int(r["id"])) if ours else {}


def dependency_review_reasons(get: Get, repo: str, head: str) -> list[str]:
    """Why the head's dependency review is not green (rule 5), or nothing."""
    newest = _newest_target_run(get, repo, head, DEPENDENCY_REVIEW, "dependency review")
    if not newest:
        return ["the head has no dependency review (dependency-review.yml) yet"]
    if newest.get("status") != "completed":
        return [f"the head's dependency review is {newest.get('status')!r}, not finished"]
    if newest.get("conclusion") != "success":
        return [f"the head's dependency review is {newest.get('conclusion')!r}"]
    return []


def sign_off_reasons(get: Get, repo: str, head: str) -> list[str]:
    """Why the head's sign-off check is not green (rule 5), or nothing: a red one names its fix."""
    newest = _newest_target_run(get, repo, head, DCO_CHECK, "sign-off check")
    if not newest:
        return ["the head has no sign-off check (dco.yml) yet"]
    if newest.get("status") != "completed":
        return [f"the head's sign-off check (DCO) is {newest.get('status')!r}, not finished"]
    if newest.get("conclusion") != "success":
        return [f"the head's sign-off check (DCO) is {newest.get('conclusion')!r}: {DCO_FIX}"]
    return []


def _all_pages(get: Get, path: str) -> list[Mapping[str, Any]]:
    """Every item of a listing that is a bare list, page by page until a short page."""
    found: list[Mapping[str, Any]] = []
    joiner = "&" if "?" in path else "?"
    for page in range(1, _MAX_PAGES + 1):
        batch = _read(get, f"{path}{joiner}per_page={_PER_PAGE}&page={page}")
        if not isinstance(batch, list) or not all(isinstance(i, Mapping) for i in batch):
            raise Unread(f"{path} is not a list of objects")
        found += batch
        if len(batch) < _PER_PAGE:
            return found
    raise Unread(f"{path} has more than {_MAX_PAGES} pages")


def codeql_languages(get: Get, repo: str) -> frozenset[str]:
    """The languages main's default-setup CodeQL analyses name (``/language:<name>``)."""
    analyses = _read(get, f"/repos/{repo}/code-scanning/analyses?ref=refs/heads/main&per_page=100")
    if not isinstance(analyses, list):
        raise Unread("main's code scanning analyses are not a list")
    languages = {
        str(a["category"]).removeprefix("/language:")
        for a in analyses
        if isinstance(a, Mapping)
        and a.get("analysis_key") == CODEQL_ANALYSIS
        and str(a.get("category", "")).startswith("/language:")
    }
    if not languages:
        raise Unread("main has no default-setup CodeQL analysis, so no language to require")
    return frozenset(languages)


@dataclass(frozen=True)
class Moved:
    """An alert the pull request seems to bring that is one main has, moved with its line: the
    same rule, the same path and the same source text (whitespace aside) at a new place."""

    number: int
    was: int
    rule: str
    path: str


@dataclass(frozen=True)
class CodeQL:
    """What CodeQL's analysis of the head says: why it does not pass, and the alerts that moved."""

    reasons: list[str]
    moved: list[Moved]


def _whole_lines(text: str, start: int, end: int) -> str | None:
    """Lines *start* to *end* (1-based, inclusive) of *text*, each with its whitespace read as one
    space and trimmed, or None if the file has no such lines."""
    lines = text.splitlines()
    if start < 1 or end < start or end > len(lines):
        return None
    return "\n".join(" ".join(line.split()) for line in lines[start - 1 : end])


def _file_at(get: Get, repo: str, path: str, sha: str) -> str:
    """A file's text at a commit, through the contents API (the blob, for a file too large for
    it). One that cannot be read is Unread: a pair is never made on a guess."""
    quoted = urllib.parse.quote(path)
    answer = _read(get, f"/repos/{repo}/contents/{quoted}?ref={sha}")
    if not isinstance(answer, Mapping) or answer.get("type") != "file":
        raise Unread(f"{path} at {sha} is not a file")
    content, encoding = answer.get("content"), answer.get("encoding")
    if encoding != "base64" or not content:
        blob = _read(get, f"/repos/{repo}/git/blobs/{answer.get('sha')}")
        content = blob.get("content") if isinstance(blob, Mapping) else None
        encoding = blob.get("encoding") if isinstance(blob, Mapping) else None
    if encoding != "base64" or not isinstance(content, str):
        raise Unread(f"{path} at {sha} has no base64 content")
    try:
        return base64.b64decode(content).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise Unread(f"{path} at {sha} is not UTF-8 text: {exc}") from exc


def _where(alert: Mapping[str, Any]) -> tuple[str, str, int, int, str] | None:
    """An alert's rule, path, lines and the commit its instance is at, or None for one that
    names no such place (it can pair with nothing)."""
    rule = alert.get("rule")
    instance = alert.get("most_recent_instance")
    location = instance.get("location") if isinstance(instance, Mapping) else None
    if not isinstance(rule, Mapping) or not isinstance(location, Mapping):
        return None
    rule_id, path = rule.get("id"), location.get("path")
    start, end = _int(location.get("start_line")), _int(location.get("end_line"))
    sha = instance.get("commit_sha") if isinstance(instance, Mapping) else None
    if not (isinstance(rule_id, str) and rule_id and isinstance(path, str) and path):
        return None
    if start is None or end is None or not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return None
    return rule_id, path, start, end, sha


def _occurrences(text: str, block: str) -> int:
    """How many times *block*, whitespace-normalised whole lines, stands in *text*."""
    want = block.split("\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return sum(lines[i : i + len(want)] == want for i in range(len(lines) - len(want) + 1))


#: The most files the compare API lists: past it, a file it leaves out may have changed.
_COMPARE_FILES = 300
_HUNK = re.compile(r"@@ -(?P<start>[0-9]+)(?:,[0-9]+)? \+[0-9]+(?:,[0-9]+)? @@")


def removed_lines(get: Get, repo: str, base: str, head: str, path: str) -> frozenset[int]:
    """The lines of *path* at *base* that the head removes or rewrites, read from
    ``compare/{base}...{head}``'s patch of the file: none for a file the compare does not list as
    changed. A file it lists with no patch (too large to show), or a compare that lists as many
    files as it can show and not this one, is unread: lines it cannot see are never taken as
    changed, nor as untouched."""
    answer = _read(get, f"/repos/{repo}/compare/{base}...{head}")
    files = answer.get("files") if isinstance(answer, Mapping) else None
    if not isinstance(files, list):
        raise Unread(f"the compare of {base}...{head} lists no files")
    listed = [f for f in files if isinstance(f, Mapping) and f.get("filename") == path]
    if not listed:
        if len(files) >= _COMPARE_FILES:
            raise Unread(f"the compare of {base}...{head} lists {len(files)} files, not {path}")
        return frozenset()
    patch = listed[0].get("patch")
    if not isinstance(patch, str) or not patch:
        raise Unread(f"the compare of {base}...{head} shows no patch of {path}")
    removed: set[int] = set()
    old: int | None = None
    for line in patch.splitlines():
        hunk = _HUNK.match(line)
        if hunk:
            old = int(hunk.group("start"))
        elif old is None or line.startswith("\\"):
            continue
        elif line.startswith("-"):
            removed.add(old)
            old += 1
        elif not line.startswith("+"):
            old += 1
    return frozenset(removed)


def changed_on_main(get: Get, repo: str, since: str, base: str, path: str) -> bool:
    """Whether main changed *path* between the commit a main alert names and main's tip, by
    ``compare/{since}...{base}``. A compare that lists as many files as it can show and not this
    one is unread."""
    answer = _read(get, f"/repos/{repo}/compare/{since}...{base}")
    files = answer.get("files") if isinstance(answer, Mapping) else None
    if not isinstance(files, list):
        raise Unread(f"the compare of {since}...{base} lists no files")
    if any(isinstance(f, Mapping) and f.get("filename") == path for f in files):
        return True
    if len(files) >= _COMPARE_FILES:
        raise Unread(f"the compare of {since}...{base} lists {len(files)} files, not {path}")
    return False


def moved_alerts(
    get: Get,
    repo: str,
    head: str,
    base: str,
    brought: list[Mapping[str, Any]],
    unreported: list[Mapping[str, Any]],
) -> tuple[list[Moved], list[str]]:
    """Each alert the pull request seems to bring that pairs, one to one, with an alert open on
    main and not reported on the pull request: the same rule id, the same path, and the same
    source lines at each one's location, whitespace read as one space. The pull request's alert is
    read at the head, main's at the main commit its instance names.

    The pull request's ref reports only the alerts in its diff, so "not reported" holds nearly all
    of main's alerts, not only those the pull request removed. A line that stands many times in a
    file (``os.path.isfile(path)``) would let a new copy pair with an untouched one, so the pairs
    of one rule, path and block are capped by what the head's file shows moved: no more than main's
    file holds that block, less the copies the head keeps outside its diff (the head's copies
    less its alerts on that block). And a main alert pairs only when the pull request took its
    lines away: each of its lines at its main commit is one the compare's patch of the file
    removes or rewrites (:func:`removed_lines`), so a new copy never pairs with an alert whose
    line still stands, however many unalerted copies the head took away. A re-indent or a move
    still pairs; a copy added beside one that stays is brought. Anything unpaired stays brought.

    The compare from a main alert's commit to the head also holds main's own commits since then,
    so the removal must be the pull request's: a main alert at a commit before main's tip *base*
    pairs only when main has not changed its file since (:func:`changed_on_main`). One whose file
    main changed pairs with nothing, and gives a reason that waits for main's CodeQL to analyse
    its tip; the pairs and those reasons are returned."""
    texts: dict[tuple[str, str], str] = {}

    def text_at(path: str, sha: str) -> str:
        if (path, sha) not in texts:
            texts[(path, sha)] = _file_at(get, repo, path, sha)
        return texts[(path, sha)]

    keys = {place[:2] for place in map(_where, brought) if place is not None}
    #: (rule, path, block) -> main's alerts on it, and the main commit they name.
    candidates: dict[tuple[str, str, str], list[int]] = {}
    main_sha: dict[tuple[str, str, str], str] = {}
    #: (rule, path, block) -> each of main's alerts on it, and its lines at its main commit.
    places: dict[tuple[str, str, str], dict[int, tuple[int, int]]] = {}
    gone: dict[tuple[str, str], frozenset[int]] = {}
    since: dict[tuple[str, str], bool] = {}
    waiting: list[str] = []

    def mains_own(path: str, sha: str) -> bool:
        """Whether main changed the file after *sha*, so a removal there may be main's."""
        if sha == base:
            return False
        if (path, sha) not in since:
            since[(path, sha)] = changed_on_main(get, repo, sha, base, path)
        return since[(path, sha)]

    def taken(path: str, sha: str, start: int, end: int) -> bool:
        if (path, sha) not in gone:
            gone[(path, sha)] = removed_lines(get, repo, sha, head, path)
        return all(line in gone[(path, sha)] for line in range(start, end + 1))

    for alert in unreported:
        place, number = _where(alert), _int(alert.get("number"))
        if place is None or number is None or place[:2] not in keys:
            continue
        rule_id, path, start, end, sha = place
        block = _whole_lines(text_at(path, sha), start, end)
        if block is None:
            continue
        key = (rule_id, path, block)
        if main_sha.setdefault(key, sha) != sha:
            raise Unread(f"main's {rule_id} alerts on one line of {path} name two commits")
        candidates.setdefault(key, []).append(number)
        places.setdefault(key, {})[number] = (start, end)
    seeming: dict[tuple[str, str, str], list[int]] = {}
    for alert in sorted(brought, key=lambda a: _int(a.get("number")) or 0):
        place, number = _where(alert), _int(alert.get("number"))
        if place is None or number is None:
            continue
        rule_id, path, start, end, sha = place
        if not any(k[:2] == (rule_id, path) for k in candidates):
            continue
        if sha != head:
            raise Unread(f"alert #{number}'s instance on the pull request is at {sha}, not {head}")
        block = _whole_lines(text_at(path, head), start, end)
        if block is not None and (rule_id, path, block) in candidates:
            seeming.setdefault((rule_id, path, block), []).append(number)
    moved: list[Moved] = []
    for key, numbers in sorted(seeming.items()):
        rule_id, path, block = key
        on_main = _occurrences(text_at(path, main_sha[key]), block)
        on_head = _occurrences(text_at(path, head), block)
        allowed = on_main - (on_head - len(numbers))
        sha = main_sha[key]
        if mains_own(path, sha):
            reason = f"{WAITING}main's CodeQL has not analysed its tip's change to {path}"
            if reason not in waiting:
                waiting.append(reason)
            continue
        left = [n for n in sorted(candidates[key]) if taken(path, sha, *places[key][n])]
        pairs = zip(numbers, left)
        moved += [Moved(n, was, rule_id, path) for n, was in list(pairs)[: max(allowed, 0)]]
    return sorted(moved, key=lambda m: m.number), waiting


def moved_note(moved: list[Moved]) -> str:
    """The moved pairs, as a verdict's title and summary name them."""
    pairs = ", ".join(f"#{m.number} (was #{m.was})" for m in moved)
    return f"{len(moved)} CodeQL alerts moved with their lines, not brought: {pairs}"


def codeql_reasons(get: Get, repo: str, head: str, base: str) -> list[str]:
    """Why CodeQL's analysis of the head does not pass (rule 6), or nothing."""
    return codeql_reading(get, repo, head, base).reasons


def codeql_reading(get: Get, repo: str, head: str, base: str) -> CodeQL:
    """Why CodeQL's analysis of the head does not pass (rule 6), and the alerts that moved;
    *base* is main's tip, which the head is judged against."""
    query = urllib.parse.urlencode({"head_sha": head, "per_page": _PER_PAGE})
    answer = _read(get, f"/repos/{repo}/actions/runs?{query}")
    runs = answer.get("workflow_runs") if isinstance(answer, Mapping) else None
    if not isinstance(runs, list):
        raise Unread(f"{head}'s runs are not a list")
    ours = [
        r
        for r in runs
        if isinstance(r, Mapping)
        and str(r.get("path", "")).startswith(CODEQL_WORKFLOW)
        and r.get("head_sha") == head
        and _int(r.get("id")) is not None
    ]
    if not ours:
        return CodeQL([f"{WAITING}CodeQL has not analysed the head yet"], [])
    newest = max(ours, key=lambda r: int(r["id"]))
    if newest.get("status") != "completed":
        status = newest.get("status")
        return CodeQL([f"{WAITING}CodeQL's analysis of the head is {status!r}, not finished"], [])
    pull = _PULL_HEAD.fullmatch(str(newest.get("head_branch") or ""))
    if pull is None:
        return CodeQL(["CodeQL's analysis of the head is not a pull request's"], [])
    jobs = _pages(get, f"/repos/{repo}/actions/runs/{newest['id']}/jobs", "jobs")
    done = {str(j.get("name")): j.get("conclusion") for j in jobs}
    languages = sorted(codeql_languages(get, repo))
    reasons = [
        f"CodeQL's `Analyze ({lang})` is {done.get(f'Analyze ({lang})')!r}"
        for lang in languages
        if done.get(f"Analyze ({lang})") != "success"
    ]
    if reasons:
        return CodeQL(reasons, [])
    ref = f"refs/pull/{pull['number']}/head"
    processed = _read(get, f"/repos/{repo}/code-scanning/analyses?ref={ref}&per_page=100")
    if not isinstance(processed, list):
        raise Unread(f"the analyses of {ref} are not a list")
    ready = {
        str(a.get("category", "")).removeprefix("/language:")
        for a in processed
        if isinstance(a, Mapping)
        and a.get("analysis_key") == CODEQL_ANALYSIS
        and a.get("commit_sha") == head
        and not a.get("error")
    }
    waiting = [
        f"{WAITING}CodeQL's {lang} results are still processing"
        for lang in languages
        if lang not in ready
    ]
    if waiting:
        return CodeQL(waiting, [])
    alerts = "/repos/{repo}/code-scanning/alerts?state=open&ref={ref}"
    pull_alerts = _all_pages(get, alerts.format(repo=repo, ref=ref))
    main_alerts = _all_pages(get, alerts.format(repo=repo, ref="refs/heads/main"))
    on_pull = {_int(a.get("number")) for a in pull_alerts}
    on_main = {_int(a.get("number")) for a in main_alerts}
    if None in on_pull:
        raise Unread("an alert on the pull request names no number")
    # The pull request's ref reports only the alerts in its diff: what main has and it does not
    # report is not what it removed.
    seeming = [a for a in pull_alerts if _int(a.get("number")) not in on_main]
    unreported = [a for a in main_alerts if _int(a.get("number")) not in on_pull]
    moved, waiting = (
        moved_alerts(get, repo, head, base, seeming, unreported)
        if seeming and unreported
        else ([], [])
    )
    paired = {m.number for m in moved}
    brought = sorted(n for n in on_pull - on_main - paired if n is not None)
    if brought:
        named = ", ".join(f"#{n}" for n in brought[:10])
        return CodeQL(
            [*waiting, f"CodeQL finds {len(brought)} alerts this pull request brings ({named})"],
            moved,
        )
    return CodeQL(waiting, moved)


def _latest_merge_run(get: Get, repo: str, **query: str) -> int | None:
    params = urllib.parse.urlencode({**query, "status": "completed", "per_page": _PER_PAGE})
    answer = _read(get, f"/repos/{repo}/actions/workflows/{workflows.MERGE}/runs?{params}")
    runs = answer.get("workflow_runs") if isinstance(answer, Mapping) else None
    if not isinstance(runs, list):
        raise Unread("the Merge runs are not a list")
    ids = [
        int(r["id"])
        for r in runs
        if isinstance(r, Mapping) and r.get("path") == MERGE_WORKFLOW and _int(r.get("id"))
    ]
    return max(ids) if ids else None


def decide_review(
    get: Get,
    repo: str,
    run: Mapping[str, Any],
    controller_revision: str,
    queue: Queue | None = None,
) -> Outcome:
    """A completed Dependency review or DCO run: judge its head again (:func:`rejudge`)."""
    head = run.get("head_sha")
    if run.get("event") != "pull_request_target" or not isinstance(head, str):
        return Outcome(
            why=f"run {run.get('id')} is not a pull request's dependency review or sign-off check"
        )
    if not _SHA.fullmatch(head):
        raise Unread(f"run {run.get('id')} of {run.get('path')} names no head")
    return rejudge(get, repo, head, controller_revision, queue)


def judge_check(
    get: Get, repo: str, check_id: int, controller_revision: str, queue: Queue | None = None
) -> Outcome:
    """A completed check run: only the ``CodeQL`` summary check, from GitHub's code scanning
    App, wakes the poster, and only to judge its head again. Its conclusion is never read."""
    check = _read(get, f"/repos/{repo}/check-runs/{check_id}")
    app = check.get("app") if isinstance(check, Mapping) else None
    if not isinstance(app, Mapping) or _int(app.get("id")) != CODEQL_APP_ID:
        return Outcome(why=f"check run {check_id} is not code scanning's")
    if check.get("name") != _CODEQL_CHECK or check.get("status") != "completed":
        return Outcome(why=f"check run {check_id} is not a completed {_CODEQL_CHECK} check")
    head = check.get("head_sha")
    if not isinstance(head, str) or not _SHA.fullmatch(head):
        raise Unread(f"check run {check_id} names no head")
    return rejudge(get, repo, head, controller_revision, queue)


def rejudge(
    get: Get, repo: str, head: str, controller_revision: str, queue: Queue | None = None
) -> Outcome:
    """Judge a head again from its own completed pull_request Merge run, or a queue commit from
    its own merge_group run, and for one that changes a definition, the contract run that run's
    verdict waits for."""
    own = _latest_merge_run(get, repo, head_sha=head, event="pull_request")
    if own is None and queue is not None:
        own = _latest_merge_run(get, repo, head_sha=head, event=QUEUE_EVENT)
    if own is None:
        return Outcome(why=f"{head} has no completed Merge run of its own to judge yet")
    return decide(get, repo, own, controller_revision, queue)


# ── A Merge run that has started: its contract beside it ──────────────────────────────────────

#: Every field of the run :func:`start_contract` reads, from the API by the run's id. Nothing of
#: the event's payload is read, and of a fork's run nothing its author names (its branch, its
#: repository, its pull requests).
START_READS = frozenset({"repository", "path", "event", "status", "head_sha", "run_attempt"})
#: A run's or a job's states once it runs: one that waits (for a maintainer's approval, or an
#: environment's) or is only queued is neither.
_RUNNING = frozenset({"in_progress", "completed"})


def started(get: Get, repo: str, run_id: int, attempt: int) -> str:
    """Why the run's ``attempt`` has not passed the approval gate, or ``""`` when it has: one of
    its jobs ran on a runner (a ``runner_id`` and a ``runner_name``, a ``started_at``, running or
    done). A run from a fork that waits for a maintainer's approval runs no job on a runner, so
    one that did is the gate passed. It is read from the jobs API, never from the event: a job
    that waits, a queued one (which may carry a ``started_at``), or one ``skipped`` or
    ``action_required`` does not count."""
    jobs = _pages(get, f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    for job in jobs:
        runner_id, runner, at = (
            _int(job.get("runner_id")),
            job.get("runner_name"),
            job.get("started_at"),
        )
        if (
            job.get("status") in _RUNNING
            and job.get("conclusion") not in ("skipped", "action_required")
            and runner_id is not None
            and runner_id > 0
            and isinstance(runner, str)
            and runner
            and isinstance(at, str)
            and at
        ):
            return ""
    return (
        f"attempt {attempt} of run {run_id} ran no job on a runner, so it may wait for a"
        " maintainer's approval"
    )


def start_contract(get: Get, repo: str, run_id: int) -> Outcome:
    """A pull request's Merge run that has started: start main's contract over the head's
    definitions beside it, so the verdict at the run's completion does not wait for it. It never
    posts: only a completed run is judged (:func:`decide`), and that run still starts the contract
    when none ran for the head at main's tip. It starts one only when all of these hold, each read
    through the API by the run's id (only :data:`START_READS` of the run):

    1. the run is this repository's merge.yml, on ``pull_request`` or a queue commit's
       ``merge_group``, attempt 1, running or done (a re-run's start starts nothing: its
       completion does, if no contract ran);
    2. a job of attempt 1 ran on a runner (:func:`started`), so the head's own run passed the
       approval gate, as the contract has always needed;
    3. the head is not behind main, and it changes a CI definition against main's tip;
    4. the head has no contract run at main's tip, and its contract branch is not already there
       (another poster run is starting one)."""
    raw = _read(get, f"/repos/{repo}/actions/runs/{run_id}")
    if not isinstance(raw, Mapping):
        raise Unread(f"run {run_id} is not an object")
    run = {key: raw.get(key) for key in START_READS}
    repository = run["repository"]
    owner = repository.get("full_name") if isinstance(repository, Mapping) else None
    if owner != repo or run["path"] != MERGE_WORKFLOW:
        return Outcome(why=f"run {run_id} is not {repo}'s {MERGE_WORKFLOW}")
    if run["event"] not in ("pull_request", QUEUE_EVENT):
        return Outcome(
            why=f"run {run_id} is a {run['event']!r} run: a contract starts only from a pull"
            " request's own run or a queue commit's"
        )
    if _int(run["run_attempt"]) != 1:
        return Outcome(
            why=f"run {run_id} is attempt {run['run_attempt']!r}: a re-run starts no contract,"
            " and its completion starts one only if none ran"
        )
    if run["status"] not in _RUNNING:
        return Outcome(why=f"run {run_id} is {run['status']!r}, not started")
    head = run["head_sha"]
    if not isinstance(head, str) or not _SHA.fullmatch(head):
        raise Unread(f"run {run_id} names no head")
    gate = started(get, repo, run_id, 1)
    if gate:
        return Outcome(why=gate)
    base = main_tip(get, repo)
    if behind_by(get, repo, base, head):
        return Outcome(why=f"{head} is behind main: no contract runs for it until it updates")
    _, head_tree = api_tree(get, repo, head)
    _, base_tree = api_tree(get, repo, base)
    if trust.compare_trees(base_tree, head_tree, base=base, head=head).trusted:
        return Outcome(why=f"{head} changes no CI definition, so it needs no contract")
    if contract_run(get, repo, head, base) is not None:
        return Outcome(why=f"{head} already has a contract run at main's tip {base}")
    branch = CONTRACT_PREFIX + head
    if (branch, base) in _branches(get, repo, CONTRACT_PREFIX):
        return Outcome(why=f"{branch} is already at main's tip: its contract run is starting")
    return Outcome(
        why=f"{head} changes a CI definition: its contract starts beside its run",
        dispatch=head,
        base=base,
    )


def wake(
    get: Get, repo: str, head: str, controller_revision: str, queue: Queue | None = None
) -> Outcome:
    """A contract run's ``wake`` job: judge its head again, by the one judge a Merge run's
    completion calls (:func:`rejudge`, then :func:`decide`). ``wake`` runs after the contract job
    has completed, and the contract is judged by that job (:func:`contract_run`), so nothing is
    waited for. The head is only which one to judge: everything is read again through the API."""
    if not _SHA.fullmatch(head):
        return Outcome(why=f"{head!r} is not a full commit id")
    return rejudge(get, repo, head, controller_revision, queue)


def judge(
    get: Get, repo: str, run_id: int, controller_revision: str, queue: Queue | None = None
) -> Outcome:
    """What a completed run of a workflow the poster follows does: a Merge run is judged; a
    Dependency review, DCO, CodeQL or contract run judges its head again."""
    run = _read(get, f"/repos/{repo}/actions/runs/{run_id}")
    if not isinstance(run, Mapping):
        raise Unread(f"run {run_id} is not an object")
    repository = run.get("repository")
    owner = repository.get("full_name") if isinstance(repository, Mapping) else None
    if owner == repo and run.get("path") in (DEPENDENCY_REVIEW, DCO_CHECK):
        if run.get("status") != "completed":
            return Outcome(why=f"run {run_id} is {run.get('status')!r}, not completed")
        return decide_review(get, repo, run, controller_revision, queue)
    if owner == repo and run.get("path") == CONTRACT_WORKFLOW:
        branch = str(run.get("head_branch") or "")
        named = branch.removeprefix(CONTRACT_PREFIX)
        if run.get("status") != "completed" or run.get("event") != "workflow_dispatch":
            return Outcome(why=f"run {run_id} is not a completed dispatch of the contract")
        if not branch.startswith(CONTRACT_PREFIX) or not _SHA.fullmatch(named):
            return Outcome(why=f"run {run_id} is on {branch!r}, which names no head")
        return rejudge(get, repo, named, controller_revision, queue)
    if owner == repo and str(run.get("path", "")).startswith(CODEQL_WORKFLOW):
        head = run.get("head_sha")
        if run.get("status") != "completed" or not isinstance(head, str):
            return Outcome(why=f"run {run_id} is not a completed CodeQL analysis")
        if not _SHA.fullmatch(head):
            raise Unread(f"CodeQL run {run_id} names no head")
        return rejudge(get, repo, head, controller_revision, queue)
    return decide(get, repo, run_id, controller_revision, queue)


def _newest_verdict(get: Get, repo: str, head: str, app: int) -> Mapping[str, Any] | None:
    query = urllib.parse.urlencode(
        {"check_name": merge_verdict.CHECK_NAME, "app_id": app, "filter": "latest", "per_page": 100}
    )
    answer = _read(get, f"/repos/{repo}/commits/{head}/check-runs?{query}")
    runs = answer.get("check_runs") if isinstance(answer, Mapping) else None
    if not isinstance(runs, list):
        raise Unread(f"{head}'s merge verdicts are not a list")
    ours = [
        r
        for r in runs
        if isinstance(r, Mapping)
        and _int(r.get("id")) is not None
        and isinstance(r.get("app"), Mapping)
        and _int(r["app"].get("id")) == app
    ]
    return max(ours, key=lambda r: int(r["id"])) if ours else None


def _waiting(check: Mapping[str, Any]) -> bool:
    output = check.get("output")
    text = output.get("text") if isinstance(output, Mapping) else None
    return (
        check.get("conclusion") == "failure"
        and isinstance(text, str)
        and f"```{WAITING_FENCE}" in text
    )


#: More open pull requests than this are not looked at again in one sweep.
SWEEP_LIMIT = 100

#: A poster run woken by anything but the schedule also judges again this many waiting heads,
#: the oldest waiting verdict first. The schedule alone is no backstop: in the 24 hours to
#: 2026-10-25 09:47 UTC it fired 7 times of its 144, 110 to 283 minutes apart.
WAITING_PER_RUN = 3

#: The sweep's first question in one read: each open pull request into main, its head, and the
#: controller's merge verdicts on it. ``checkType`` is GitHub's default, the newest of each suite.
WAITING_QUERY = """
query($owner: String!, $name: String!, $app: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, baseRefName: "main", first: 100) {
      totalCount
      nodes {
        commits(last: 1) {
          nodes {
            commit {
              oid
              checkSuites(first: 10, filterBy: {appId: $app}) {
                nodes {
                  checkRuns(first: 100, filterBy: {checkName: "merge-verdict"}) {
                    nodes { databaseId conclusion text }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def waiting_heads(query: Query, repo: str, app: int) -> list[str] | None:
    """The heads of the open pull requests into main whose newest merge verdict of the App's is a
    red that still waits (on CodeQL or on its contract run), the oldest such verdict first, from
    one GraphQL read. None when that read gives no answer this reads, or more pull requests than
    a sweep looks at: the sweep then reads each head itself."""
    owner, name = repo.split("/", 1)
    try:
        answer = query(WAITING_QUERY, {"owner": owner, "name": name, "app": app})
        pulls = answer["data"]["repository"]["pullRequests"]
        if int(pulls["totalCount"]) > SWEEP_LIMIT:
            return None
        heads: list[tuple[int, str]] = []
        for pull in pulls["nodes"]:
            (node,) = pull["commits"]["nodes"]
            commit = node["commit"]
            if not _SHA.fullmatch(str(commit["oid"])):
                return None
            runs = [r for s in commit["checkSuites"]["nodes"] for r in s["checkRuns"]["nodes"]]
            newest = max(runs, key=lambda r: int(r["databaseId"]), default=None)
            if (
                newest is not None
                and newest["conclusion"] == "FAILURE"
                and f"```{WAITING_FENCE}" in (newest["text"] or "")
            ):
                heads.append((int(newest["databaseId"]), str(commit["oid"])))
        return [oid for _, oid in sorted(heads)]
    except (OSError, KeyError, TypeError, ValueError):
        return None


def judge_waiting(
    get: Get,
    repo: str,
    app: int,
    controller_revision: str,
    query: Query | None = None,
    *,
    limit: int | None = None,
    skip: frozenset[str] = frozenset(),
    queue: Queue | None = None,
) -> list[Outcome]:
    """Every open pull request into main whose newest merge verdict, the controller App's, is a
    red that still waits (on CodeQL, or on its contract run, whose completion wakes nothing): judge
    its head again, and keep only what changed. A head whose contract run has reported comes back
    with its verdict and its contract branch to delete.

    With ``query``, one GraphQL read first names the heads that wait (:func:`waiting_heads`): none,
    and the sweep ends there; some, and only those are read again, through the REST reads below,
    which stay the gate. A read that gives no answer leaves the sweep reading every head.

    With ``limit`` (a poster run woken by something else), only the first ``limit`` heads the one
    read names, past ``skip``, are judged again, and a read that gives no answer judges none: the
    schedule's own sweep reads every head.

    Once main's merges are queued (``queue.live``), each built queue commit of main's merge queue
    whose verdict waits is judged again first, by the same ``limit`` and ``skip``."""
    changed: list[Outcome] = []
    if queue is not None and queue.live:
        # A queue commit whose verdict waits on its contract run: the run's wake judges it, and
        # this is the sweep behind that wake, as it is for a pull request's head.
        built = [e.queue_commit for e in queue_entries(queue.query, repo) if e.queue_commit]
        for commit in [c for c in built if c not in skip][: limit if limit is not None else None]:
            newest = _newest_verdict(get, repo, commit, app)
            if newest is not None and _waiting(newest):
                outcome = rejudge(get, repo, commit, controller_revision, queue)
                if outcome.posting is not None:
                    changed.append(outcome)
    waiting = waiting_heads(query, repo, app) if query is not None else None
    if limit is not None:
        if waiting is None:
            print("the one read of the open heads gave no answer: no waiting head judged here")
            return changed
        waiting = [h for h in waiting if h not in skip][:limit]
    if waiting is not None and not waiting:
        print("no open pull request's merge verdict still waits: nothing to judge again")
        return changed
    if query is not None and waiting is None:
        print("the one read of the open heads gave no answer: reading each head")
    pulls = _all_pages(get, f"/repos/{repo}/pulls?state=open&base=main")[:SWEEP_LIMIT]
    for pull in pulls:
        head = pull.get("head")
        sha = head.get("sha") if isinstance(head, Mapping) else None
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise Unread(f"pull request {pull.get('number')} names no head")
        if waiting is not None and sha not in waiting:
            continue
        newest = _newest_verdict(get, repo, sha, app)
        if newest is None or not _waiting(newest):
            continue
        outcome = rejudge(get, repo, sha, controller_revision, queue)
        output = newest.get("output")
        was: Mapping[str, Any] = output if isinstance(output, Mapping) else {}
        # The summary too: a head red for another reason keeps its title when its wait ends.
        same = outcome.posting is not None and (
            outcome.posting.conclusion,
            outcome.posting.output.get("title"),
            outcome.posting.output.get("summary"),
        ) == (newest.get("conclusion"), was.get("title"), was.get("summary"))
        if outcome.posting is not None and not same:
            changed.append(outcome)
    return changed


def _branches(get: Get, repo: str, prefix: str) -> list[tuple[str, str]]:
    """Each branch under ``prefix`` that names a head, with the commit it points at."""
    refs = _read(get, f"/repos/{repo}/git/matching-refs/heads/{prefix}")
    if not isinstance(refs, list):
        raise Unread(f"the {prefix} branches are not a list")
    found: list[tuple[str, str]] = []
    for ref in refs:
        name = ref.get("ref") if isinstance(ref, Mapping) else None
        target = ref.get("object") if isinstance(ref, Mapping) else None
        sha = target.get("sha") if isinstance(target, Mapping) else None
        if not isinstance(name, str) or not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise Unread(f"a {prefix} branch names no commit")
        branch = name.removeprefix("refs/heads/")
        if _SHA.fullmatch(branch.removeprefix(prefix)):
            found.append((branch, sha))
    return found


def stale_branches(get: Get, repo: str, now: datetime.datetime, base: str) -> list[str]:
    """Every contract branch that points at a commit other than ``base``, ``main``'s tip (its run
    no longer counts, and a head judged again gets a new one), and every trusted-run branch the
    old path left whose commit was made more than :data:`SWEEP_AFTER` ago."""
    stale = [b for b, sha in _branches(get, repo, CONTRACT_PREFIX) if sha != base]
    for branch, sha in _branches(get, repo, TRUSTED_PREFIX):
        commit = _read(get, f"/repos/{repo}/git/commits/{sha}")
        made = commit.get("committer") if isinstance(commit, Mapping) else None
        when = made.get("date") if isinstance(made, Mapping) else None
        try:
            at = datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError:
            raise Unread(f"{branch}'s commit names no date") from None
        if now - at > SWEEP_AFTER:
            stale.append(branch)
    return sorted(stale)


def decide(
    get: Get, repo: str, run_id: int, controller_revision: str, queue: Queue | None = None
) -> Outcome:
    """What this run's completion does (the module's rules)."""
    if not _SHA.fullmatch(controller_revision):
        raise ValueError(f"the controller revision must be a full commit id: {controller_revision}")
    found = read_run(get, repo, run_id)
    if isinstance(found, str):
        return Outcome(why=found)
    run = found
    if run.event == QUEUE_EVENT:
        return decide_queue(get, repo, run, controller_revision, queue)
    head = run.head_sha
    changed: list[str] = []
    contract: Contract | None = None
    # Why main's contract is not started from this run, though it has none yet (:func:`started`).
    gate = ""
    # Every reason that holds, so one verdict names all a head must fix.
    reasons: list[str] = []
    moved: list[Moved] = []
    try:
        base = main_tip(get, repo)
        tree_oid, head_tree = api_tree(get, repo, head)
        behind = behind_by(get, repo, base, head)
        if behind and queue is not None and queue.live:
            # Under main's merge queue a head may be behind: the tree that lands is its queue
            # commit's, judged on its own. Its definitions are compared with the commit it was
            # built on, so it reads as changing only what it changes.
            built_on = merge_base(get, repo, base, head)
            _, base_tree = api_tree(get, repo, built_on)
            comparison = trust.compare_trees(base_tree, head_tree, base=built_on, head=head)
            changed = [] if comparison.trusted else [c.path for c in comparison.changes]
        elif behind:
            # Its definitions are not compared: against main's tip, a head main has moved past
            # reads as changing every definition main changed since. It updates before any run
            # of it counts, so no contract runs for it either.
            reasons.append(f"the head is {behind} commits behind main: update the branch")
        else:
            _, base_tree = api_tree(get, repo, base)
            comparison = trust.compare_trees(base_tree, head_tree, base=base, head=head)
            changed = [] if comparison.trusted else [c.path for c in comparison.changes]
        if changed and run.event != "pull_request":
            return Outcome(
                why=(
                    f"{head} changes {len(changed)} CI definitions, and this is not its "
                    "own pull_request run, so main's contract is not run from it"
                )
            )
        failing, labels = results_reasons(get, repo, run)
        reasons += failing
        reasons += dependency_review_reasons(get, repo, head)
        reasons += sign_off_reasons(get, repo, head)
        codeql = codeql_reading(get, repo, head, base)
        reasons += codeql.reasons
        moved = codeql.moved
        if changed:
            contract = contract_run(get, repo, head, base)
            if contract is None:
                gate = started(get, repo, run.id, run.attempt)
            if contract is not None and contract.status == "completed":
                if contract.conclusion != "success":
                    reasons.append(
                        "main's CI contract fails on its definitions: contract run "
                        f"{contract.url or contract.id} is {contract.conclusion!r}"
                    )
        records = evidence(get, repo, run) if not reasons else {}
        if not reasons and not records:
            reasons.append(f"the run has no `{RECORD_PREFIX}*` artifact, so no evidence")
        flakes = passed_flakes(get, repo, run) if not reasons else []
    except (Unread, trust.Refused) as exc:
        return Outcome(posting=_red(run, [*reasons, f"unread: {exc}"], head, moved))
    # A head that changes a definition: its contract run is started once, at main's tip, and its
    # verdict waits for it. Once that run has reported, its branch goes with the posted verdict.
    # Until it reports, the head is a red that waits on it: a contract run dispatched with the
    # job's token raises no workflow_run when it completes, so the schedule's sweep, which judges
    # every waiting red again, is what posts the head's verdict once it has.
    start = head if changed and contract is None and not gate else None
    judged = changed and contract is not None and contract.status == "completed"
    cleanup = CONTRACT_PREFIX + head if judged else None
    if changed and contract is None and gate:
        reasons.append(f"main's CI contract over its definitions is not started from it: {gate}")
    elif changed and not judged:
        running = (
            "is started"
            if contract is None
            else f"is {contract.status!r}: contract run {contract.url or contract.id}"
        )
        reasons.append(f"{WAITING_CONTRACT}{running}")
    if reasons:
        retried = _retry_note(get, repo, run, tree_oid, failing, reasons, queue)
        return Outcome(
            posting=_red(run, reasons, head, moved, retried),
            dispatch=start,
            base=base,
            cleanup=cleanup,
        )
    toolchain = {"runner": ",".join(labels) or "unnamed", "definitions": base}
    ran = [f"- the definitions that ran are main's at `{base}`"]
    if changed and contract is not None:
        toolchain = {**toolchain, "definitions": head, "contract": contract.url or str(contract.id)}
        ran = [
            f"- the definitions that ran are its own: it changes {len(changed)} "
            f"({_names(changed)})",
            f"- main's CI contract at `{base}` passed over them: contract run "
            f"{contract.url or contract.id}",
        ]
    receipt = {
        "controller_revision": controller_revision,
        "head_sha": head,
        "tree_oid": tree_oid,
        "external_revisions": {},
        "toolchain": toolchain,
        "evidence_digests": records,
    }
    summary = "\n".join(
        [
            f"Judged by its own Merge run: every attempt of Merge run {run.url or run.id} "
            "was green.",
            *ran,
            (
                "- the head is up to date with main"
                if not behind
                else "- the head is behind main, which the merge queue tests it on before it lands"
            ),
            "- its dependency review passed",
            "- its sign-off check (DCO) passed",
            "- CodeQL analysed every language main has, and found no alert it brings",
            *([f"- {moved_note(moved)}"] if moved else []),
            f"- {len(records)} job records, their digests in the receipt below",
            *(
                [
                    f"- the flake rule passed {len(flakes)} failed test(s), each of which passed"
                    " alone in a new process, in a file this head does not change: "
                    + ", ".join(f"`{n}`" for n in flakes)
                ]
                if flakes
                else []
            ),
        ]
    )
    output = merge_verdict.render_output(
        receipt,
        title=(
            "Green: every planned job passed on this head"
            + (f" ({moved_note(moved)})" if moved else "")
            + flakes_note(flakes)
        )[:255],
        summary=summary,
    )
    posting = Posting(head, "success", output, run.url, f"{run.id}/{run.attempt}")
    return Outcome(posting=posting, cleanup=cleanup)


# ── The retry budget (0.4.0 A5) ────────────────────────────────────────────────────────────
#
# A change gets at most RETRIES retries, and the red after them goes to a person. A red pull
# request verdict says which retry it is and whether its red is flake-class, so the lane's
# tooling retries a flake-class red by itself while the budget lasts. It is a note only: no count
# turns a red green or a green red, and every rule above stands as it is.

#: Retries a change gets; the red after them goes to a person (quarantine, fix or infrastructure).
RETRIES = 2
#: The pull request's earlier heads read for the count, newest first.
RETRY_HEADS = 10
#: The jobs whose failed test, or cancelled or timed-out shard, a red may be flake-class for: the
#: suites, the end-to-end tests and the web unit tests, each by its catalogue id.
FLAKE_CLASS_JOBS = ("python-suite", "python-suite-macos", "e2e", "e2e-shard", "web-unit")
#: The failed tests' standings a flake-class red may hold: a rail's, a security test's and one no
#: rerun classified are never flake-class, nor is a quarantined failure the change reads.
_FLAKE_STANDINGS = frozenset({verdict.FLAKE, verdict.REPEATED_FAILURE})
#: The flaky-test issues ``flaky-tests.yml`` keeps (``tools/ci/flaky_issues.py``): their label,
#: their title's start and its limit. A failing test has a flaky history when one is open.
FLAKY_LABEL = "flaky-test"
FLAKY_PREFIX = "Flaky test: "
_FLAKY_TITLE_LIMIT = 256
#: Who may file a flaky-test issue that counts: the flaky-tests workflow's account, or a
#: maintainer (as ``release_tag`` reads a leg's issue). Anyone can open an issue of any title.
FLAKY_BOT = "github-actions"
_MAINTAINERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
#: Why main's merge queue removes an entry whose checks failed (``RemovedFromMergeQueueEvent``).
QUEUE_REMOVED_RED = "failed_checks"
#: A pull request's earlier heads (each one its force-pushes replaced) and its removals from main's
#: merge queue, in the order they came, and the open flaky-test issues, in one read.
RETRY_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $label: String!, $heads: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefOid
      timelineItems(
        itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT, REMOVED_FROM_MERGE_QUEUE_EVENT], last: $heads
      ) {
        nodes {
          __typename
          ... on HeadRefForcePushedEvent { beforeCommit { oid tree { oid } } }
          ... on RemovedFromMergeQueueEvent { reason }
        }
      }
    }
    issues(states: OPEN, labels: [$label], first: 100) {
      totalCount
      nodes { title authorAssociation author { login __typename } }
    }
  }
}
"""


def flaky_title(test: str) -> str:
    """A test's flaky-test issue title, as ``flaky_issues.title`` writes it."""
    whole = FLAKY_PREFIX + test
    if len(whole) <= _FLAKY_TITLE_LIMIT:
        return whole
    tail = f"… ({hashlib.sha256(test.encode('utf-8')).hexdigest()[:16]})"
    return FLAKY_PREFIX + test[: _FLAKY_TITLE_LIMIT - len(FLAKY_PREFIX) - len(tail)] + tail


@dataclass(frozen=True)
class Retry:
    """What a red verdict says of its retries: which one this red is, and whether it is
    flake-class and why."""

    count: int
    flake_class: bool
    why: str

    def lines(self) -> list[str]:
        spent = self.count >= RETRIES
        return [
            (
                f"retry budget spent: {self.count} retries of this change were red, so a person"
                " looks (quarantine, fix or infrastructure)"
                if spent
                else f"retry {self.count} of {RETRIES}"
            ),
            ("flake-class: " if self.flake_class else "not flake-class: ") + self.why,
        ]

    def label(self) -> str:
        """What the red's title says, right after ``Red``, where no long reason cuts it off."""
        count = (
            "retry budget spent" if self.count >= RETRIES else f"retry {self.count} of {RETRIES}"
        )
        return count + (", flake-class" if self.flake_class else "")


@dataclass(frozen=True)
class Timeline:
    """What the retry note reads of a pull request: its earlier heads with their trees, newest
    first; how many times main's merge queue removed its head for failed checks since the head
    last changed; and the titles of the open flaky-test issues the workflow or a maintainer
    filed."""

    heads: tuple[tuple[str, str], ...]
    removed: int
    flaky: frozenset[str]


def _counted_issue(issue: Mapping[str, Any]) -> bool:
    author = issue.get("author") or {}
    by_bot = author.get("login") == FLAKY_BOT and author.get("__typename") == "Bot"
    return by_bot or issue.get("authorAssociation") in _MAINTAINERS


def _retry_read(query: Query, repo: str, number: int) -> Timeline:
    """The pull request's :class:`Timeline`, from one GraphQL read."""
    owner, name = repo.split("/", 1)
    variables = {"owner": owner, "name": name, "number": number, "label": FLAKY_LABEL}
    try:
        answer = query(RETRY_QUERY, {**variables, "heads": RETRY_HEADS})
        repository = answer["data"]["repository"]
        heads: list[tuple[str, str]] = []
        removed = 0
        # Newest first: a removal before any force-push read so far was of the head as it is now.
        for node in reversed(repository["pullRequest"]["timelineItems"]["nodes"]):
            if node.get("__typename") == "RemovedFromMergeQueueEvent":
                red = str(node.get("reason") or "").lower() == QUEUE_REMOVED_RED
                removed += 1 if red and not heads else 0
                continue
            before = node.get("beforeCommit") or {}
            sha = str(before.get("oid") or "")
            tree = str((before.get("tree") or {}).get("oid") or "")
            if not (_SHA.fullmatch(sha) and _SHA.fullmatch(tree)):
                raise Unread("a force-push of the pull request names no earlier head")
            heads.append((sha, tree))
        issues = repository["issues"]
        if int(issues["totalCount"]) != len(issues["nodes"]):
            raise Unread("more open flaky-test issues than one read lists")
        titles = frozenset(str(i["title"]) for i in issues["nodes"] if _counted_issue(i))
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise Unread(f"the pull request's heads gave no answer this reads: {exc}") from exc
    return Timeline(tuple(heads), removed, titles)


def failed_tests(get: Get, repo: str, job_id: int) -> list[Mapping[str, Any]] | str:
    """The failed tests a ``results`` job names (one ``verdict.FAILED_NOTICE`` each), or why they
    cannot all be named."""
    named: list[Mapping[str, Any]] = []
    counts: list[object] = []
    for note in _all_pages(get, f"/repos/{repo}/check-runs/{job_id}/annotations"):
        if note.get("title") != verdict.FAILED_NOTICE or note.get("annotation_level") != "notice":
            continue
        try:
            item = json.loads(str(note.get("message") or ""))
        except ValueError:
            return "a failed test's notice is not one the verdict writes"
        if isinstance(item, Mapping) and set(item) == {"failed"}:
            counts.append(item["failed"])
            continue
        if not isinstance(item, Mapping) or not all(
            isinstance(item.get(k), str) and item[k] for k in ("key", "test", "standing")
        ):
            return "a failed test's notice is not one the verdict writes"
        named.append(item)
    # A step's annotations are capped, so the count is what says every failed test was read.
    if len(counts) != 1 or not isinstance(counts[0], int) or isinstance(counts[0], bool):
        return "its results job names no one count of its failed tests"
    if counts[0] != len(named):
        return f"its results job names {len(named)} of its {counts[0]} failed tests"
    return named


def _flake_job(name: str) -> bool:
    return any(name == j or name.startswith(f"{j} (") for j in FLAKE_CLASS_JOBS)


def flake_class(
    get: Get,
    repo: str,
    run: Run,
    others: list[str],
    changed: Mapping[tuple[str, str], object],
    flaky: frozenset[str],
) -> tuple[bool, str]:
    """Whether the run's red is flake-class (0.4.0 A5, with CORE's first condition), and why:
    every reason is its ``results`` job's; every job of the last attempt that did not pass is a
    suite, end-to-end or web-unit job's, failed in its tests or cancelled or timed out with none
    failed; and each failed test was classified by its rerun, reads no file the pull request
    changes, and has a flaky history: an open flaky-test issue. Anything else goes to a person."""
    if others:
        return False, f"its red is not its tests' alone: {others[0]}"
    jobs = _pages(get, f"/repos/{repo}/actions/runs/{run.id}/attempts/{run.attempt}/jobs", "jobs")
    judges = [_int(j.get("id")) for j in jobs if j.get("name") == RESULTS_JOB]
    if len(judges) != 1 or judges[0] is None:
        return (
            False,
            f"attempt {run.attempt} has no one `{RESULTS_JOB}` job naming its failed tests",
        )
    failed: dict[str, str] = {}
    for job in jobs:
        name, conclusion = str(job.get("name") or ""), job.get("conclusion")
        if name == RESULTS_JOB or conclusion in ("success", "skipped", "neutral"):
            continue
        if not _flake_job(name):
            return False, f"`{name}` is {conclusion!r}, and only a test job's red is flake-class"
        failed[name] = str(conclusion)
    if not failed:
        return False, "no test job failed, was cancelled or timed out"
    tests = failed_tests(get, repo, judges[0])
    if isinstance(tests, str):
        return False, tests
    keys = {str(t["key"]) for t in tests}
    for name, conclusion in sorted(failed.items()):
        if conclusion == "failure" and name not in keys:
            return False, f"`{name}` failed with no failed test named"
        if conclusion not in ("failure", "cancelled", "timed_out"):
            return False, f"`{name}` is {conclusion!r}"
        if conclusion != "failure" and name in keys:
            return False, f"`{name}` is {conclusion} with a failed test in it"
    paths = {p for key in changed for p in key}
    for t in tests:
        test, standing = str(t["test"]), str(t["standing"])
        if standing not in _FLAKE_STANDINGS:
            return False, f"`{test}` is {standing}"
        hit = verdict.changed_reads(test, paths)
        if isinstance(hit, str):
            return False, hit
        if hit:
            return False, f"`{test}` reads `{hit[0]}`, which the pull request changes"
        if flaky_title(test) not in flaky:
            return False, (
                f"`{test}` has no flaky history (no open flaky-test issue the workflow or a"
                " maintainer filed)"
            )
    if not tests:
        return True, f"{len(failed)} test job(s) cancelled or timed out, with no failed test"
    return True, (
        f"{len(tests)} failed test(s), each with an open flaky-test issue and reading no file the"
        " pull request changes"
    )


def _retry_note(
    get: Get,
    repo: str,
    run: Run,
    tree_oid: str,
    failing: list[str],
    reasons: list[str],
    queue: Queue | None,
) -> Retry | str | None:
    """The retry note on a pull request's red (:func:`retry`): none on another run's red, or on a
    red whose every reason waits, which is no red to retry yet; why it is unread, when it is."""
    if run.event != "pull_request" or all(r.startswith(WAITING) for r in reasons):
        return None
    try:
        return retry(get, repo, run, tree_oid, [r for r in reasons if r not in failing], queue)
    except (Unread, trust.Refused) as exc:
        return f"unread: {exc}"


def retry(
    get: Get,
    repo: str,
    run: Run,
    tree_oid: str,
    others: list[str],
    queue: Queue | None,
    *,
    pull: int | None = None,
    head: str = "",
    changed: Mapping[tuple[str, str], object] | None = None,
) -> Retry | str:
    """What the red verdict on a pull request's run says of its retries, or why it says nothing
    but that. The count is the run's attempts after the first, and each earlier head of the pull
    request whose newest verdict is a red that waits on nothing and that has the head's tree or
    makes its change, the same ``+/-`` lines in the same files; and each time main's merge queue
    removed the head as it is now for failed checks, since a re-enqueue is a retry.

    A queue commit's red (:func:`_queue_retry_note`) names its pull request (*pull*) and that
    pull request's *head*, whose tree and change the earlier heads are held to, and the queue
    commit's own change against its parent (*changed*), which its failed tests are read against.
    """
    if queue is None:
        return "no GraphQL read was given"
    number = run.pull if pull is None else pull
    if number is None:
        return "the run names no pull request of this repository"
    head = head or run.head_sha
    timeline = _retry_read(queue.query, repo, number)
    own = _change(get, repo, "main", head)
    count = run.attempt - 1 + timeline.removed
    for sha, tree in timeline.heads:
        if sha == head:
            continue
        check = _newest_verdict(get, repo, sha, queue.app)
        if check is None or check.get("conclusion") != "failure" or _waiting(check):
            continue
        if tree == tree_oid or _change(get, repo, "main", sha) == own:
            count += 1
    ok, why = flake_class(
        get, repo, run, others, own if changed is None else changed, timeline.flaky
    )
    return Retry(count, ok, why)


def _commit_tree(get: Get, repo: str, sha: str) -> str:
    commit = _read(get, f"/repos/{repo}/git/commits/{sha}")
    tree = commit.get("tree") if isinstance(commit, Mapping) else None
    oid = tree.get("sha") if isinstance(tree, Mapping) else None
    if not isinstance(oid, str) or not _SHA.fullmatch(oid):
        raise Unread(f"commit {sha} names no tree")
    return oid


def _queue_retry_note(
    get: Get,
    repo: str,
    run: Run,
    entry: Entry,
    built_on: str,
    failing: list[str],
    reasons: list[str],
    queue: Queue,
) -> Retry | str | None:
    """The retry note on a queue commit's red (0.4.0 A5, R2's re-enqueue reads it): its pull
    request's removals from the queue and the run's attempts counted, with the pull request's
    earlier heads as on a pull request's red, and flake-class read from the queue commit's own
    run, its failed tests held to the queue commit's change against its parent. None on a red
    whose every reason waits."""
    if all(r.startswith(WAITING) for r in reasons):
        return None
    try:
        return retry(
            get,
            repo,
            run,
            _commit_tree(get, repo, entry.pr_head),
            [r for r in reasons if r not in failing],
            queue,
            pull=entry.number,
            head=entry.pr_head,
            changed=_change(get, repo, built_on, run.head_sha),
        )
    except (Unread, trust.Refused) as exc:
        return f"unread: {exc}"


# ── A queue commit of main's merge queue (0.4.0 A1) ─────────────────────────────────────────

#: Main's merge queue in one read: each entry's place, its queue commit and the commit it was
#: built on, and its pull request's number, state, base and head. An entry the queue has not
#: built yet has no commits.
QUEUE_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    mergeQueue(branch: "main") {
      entries(first: 100) {
        totalCount
        nodes {
          position
          headCommit { oid }
          baseCommit { oid }
          pullRequest { number state baseRefName headRefOid }
        }
      }
    }
  }
}
"""
#: A comparison that changes this many files or more is more than the API lists whole.
COMPARE_FILES = 300


@dataclass(frozen=True)
class Entry:
    """One entry of main's merge queue, as GitHub lists it."""

    position: int
    #: The commit the queue built for it, and the commit it built it on ("" before it is built).
    queue_commit: str
    built_on: str
    number: int
    state: str
    base: str
    pr_head: str


def queue_entries(query: Query, repo: str) -> list[Entry]:
    """Main's merge queue, in its order, from one GraphQL read; :class:`Unread` for any answer
    this does not read whole."""
    owner, name = repo.split("/", 1)
    try:
        answer = query(QUEUE_QUERY, {"owner": owner, "name": name})
        queue = answer["data"]["repository"]["mergeQueue"]
        if queue is None:
            return []
        listed = queue["entries"]
        if int(listed["totalCount"]) != len(listed["nodes"]):
            raise Unread("main's merge queue holds more entries than one read lists")
        found = []
        for node in listed["nodes"]:
            pull = node["pullRequest"]
            queue_commit, built_on = (
                str((node[key] or {}).get("oid") or "") for key in ("headCommit", "baseCommit")
            )
            if not all(c == "" or _SHA.fullmatch(c) for c in (queue_commit, built_on)):
                raise Unread("an entry of main's merge queue names a commit that is no commit id")
            head = str(pull["headRefOid"])
            if not _SHA.fullmatch(head):
                raise Unread(f"pull request {pull['number']} in main's merge queue names no head")
            found.append(
                Entry(
                    int(node["position"]),
                    queue_commit,
                    built_on,
                    int(pull["number"]),
                    str(pull["state"]),
                    str(pull["baseRefName"]),
                    head,
                )
            )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise Unread(f"main's merge queue gave no answer this reads: {exc}") from exc
    return sorted(found, key=lambda e: e.position)


def commit_parents(get: Get, repo: str, sha: str) -> list[str]:
    commit = _read(get, f"/repos/{repo}/git/commits/{sha}")
    parents = commit.get("parents") if isinstance(commit, Mapping) else None
    if not isinstance(parents, list):
        raise Unread(f"commit {sha} lists no parents")
    found = [p.get("sha") if isinstance(p, Mapping) else None for p in parents]
    if not all(isinstance(p, str) and _SHA.fullmatch(p) for p in found):
        raise Unread(f"commit {sha} names a parent that is no commit id")
    return [str(p) for p in found]


def _change(get: Get, repo: str, base: str, head: str) -> dict[tuple[str, str], object]:
    """Each file the comparison ``base...head`` changes, by its path and the path it came from:
    its ``+/-`` lines, or, where the API gives no patch (a binary file, or a patch too long to
    send), the blob it ends as."""
    answer = _read(get, f"/repos/{repo}/compare/{base}...{head}")
    files = answer.get("files") if isinstance(answer, Mapping) else None
    if not isinstance(files, list):
        raise Unread(f"the comparison {base}...{head} lists no files")
    if len(files) >= COMPARE_FILES:
        raise Unread(f"the comparison {base}...{head} changes {COMPARE_FILES} files or more")
    found: dict[tuple[str, str], object] = {}
    for item in files:
        path = item.get("filename") if isinstance(item, Mapping) else None
        if not isinstance(path, str) or not path:
            raise Unread(f"the comparison {base}...{head} names a file without a path")
        key = (path, str(item.get("previous_filename") or path))
        patch = item.get("patch")
        if isinstance(patch, str):
            lines = [line for line in patch.splitlines() if line[:1] in ("+", "-")]
            found[key] = sorted(lines)
        else:
            found[key] = ("blob", str(item.get("sha") or ""))
    return found


def change_reasons(
    get: Get, repo: str, built_on: str, queue_commit: str, pr_head: str
) -> list[str]:
    """Why the queue commit's change against the commit it was built on is not exactly its pull
    request's own change (the head against its merge base with main): the same files, each with
    the same ``+/-`` lines. Context lines are not compared, so a change that applies on a moved
    base is still the same change."""
    queued = _change(get, repo, built_on, queue_commit)
    own = _change(get, repo, "main", pr_head)
    reasons: list[str] = []
    for key in sorted(set(queued) | set(own)):
        path = key[0]
        if key not in own:
            reasons.append(f"the queue commit changes `{path}`, which its pull request does not")
        elif key not in queued:
            reasons.append(f"the queue commit leaves out its pull request's change to `{path}`")
        elif queued[key] != own[key]:
            reasons.append(f"the queue commit changes `{path}` otherwise than its pull request")
    return reasons


def decide_queue(
    get: Get, repo: str, run: Run, controller_revision: str, queue: Queue | None
) -> Outcome:
    """A queue commit's run (the module's "A queue commit" rules)."""
    if queue is None:
        return Outcome(why=f"run {run.id} is a queue run, judged only with the queue's read")
    named = QUEUE_BRANCH.fullmatch(run.branch)
    if named is None:
        return Outcome(why=f"run {run.id} is on {run.branch!r}, which names no queue entry")
    number, built_on, sha = int(named["number"]), named["built_on"], run.head_sha
    try:
        newest = _newest_verdict(get, repo, sha, queue.app)
    except Unread as exc:
        return Outcome(why=f"queue commit {sha}'s verdicts are unread, so none is posted: {exc}")
    if newest is not None and not _waiting(newest):
        return Outcome(why=f"queue commit {sha} has its verdict: it is never posted again")
    contract: Contract | None = None
    changed: list[str] = []
    reasons: list[str] = []
    try:
        entries = queue_entries(queue.query, repo)
        mine = [e for e in entries if e.queue_commit == sha]
        if not mine:
            return Outcome(why=f"{sha} is no entry of main's merge queue now: nothing waits on it")
        if len(mine) != 1:
            raise Unread(f"main's merge queue lists {len(mine)} entries for the one commit {sha}")
        (entry,) = mine
        base = main_tip(get, repo)
        ahead = [e for e in entries if e.position < entry.position and e.queue_commit]
        expected = ahead[-1].queue_commit if ahead else base
        if entry.number != number:
            reasons.append(f"the queue lists it for #{entry.number}, its branch names #{number}")
        if (entry.state, entry.base) != ("OPEN", "main"):
            reasons.append(
                f"#{entry.number} is {entry.state} into {entry.base}, not open into main"
            )
        parents = commit_parents(get, repo, sha)
        if parents != [built_on] or entry.built_on != built_on:
            reasons.append(
                f"it is built on {', '.join(parents) or 'nothing'}, the queue says "
                f"{entry.built_on or 'nothing'} and its branch {built_on}: not one commit"
            )
        if built_on != expected:
            reasons.append(
                f"it is built on {built_on}, which is neither main's tip nor the queue commit of"
                " the entry ahead, so a tree main would reach was never judged"
            )
        verdict = _newest_verdict(get, repo, entry.pr_head, queue.app)
        if verdict is None or verdict.get("conclusion") != "success":
            said = "missing" if verdict is None else repr(verdict.get("conclusion"))
            reasons.append(
                f"its pull request's head {entry.pr_head} has no green merge verdict ({said})"
            )
        reasons += change_reasons(get, repo, built_on, sha, entry.pr_head)
        tree_oid, queue_tree = api_tree(get, repo, sha)
        _, base_tree = api_tree(get, repo, base)
        comparison = trust.compare_trees(base_tree, queue_tree, base=base, head=sha)
        changed = [] if comparison.trusted else [c.path for c in comparison.changes]
        failing, labels = results_reasons(get, repo, run)
        reasons += failing
        if changed:
            contract = contract_run(get, repo, sha, base)
            if contract is not None and contract.status == "completed":
                if contract.conclusion != "success":
                    reasons.append(
                        "main's CI contract fails on its definitions: contract run "
                        f"{contract.url or contract.id} is {contract.conclusion!r}"
                    )
        records = evidence(get, repo, run) if not reasons else {}
        if not reasons and not records:
            reasons.append(f"the run has no `{RECORD_PREFIX}*` artifact, so no evidence")
        flakes = passed_flakes(get, repo, run) if not reasons else []
    except (Unread, trust.Refused) as exc:
        return Outcome(posting=_red(run, [*reasons, f"unread: {exc}"], sha))
    # A queue commit that changes a definition: main's contract runs over it, at main's tip, and
    # its verdict waits for it, as a pull request's head's does. GitHub built it from an entry a
    # maintainer queued, so no approval gate stands before it.
    start = sha if changed and contract is None else None
    judged = changed and contract is not None and contract.status == "completed"
    cleanup = CONTRACT_PREFIX + sha if judged else None
    if changed and not judged:
        running = (
            "is started"
            if contract is None
            else f"is {contract.status!r}: contract run {contract.url or contract.id}"
        )
        reasons.append(f"{WAITING_CONTRACT}{running}")
    if reasons:
        retried = _queue_retry_note(get, repo, run, entry, built_on, failing, reasons, queue)
        return Outcome(
            posting=_red(run, reasons, sha, None, retried),
            dispatch=start,
            base=base,
            cleanup=cleanup,
        )
    toolchain = {
        "runner": ",".join(labels) or "unnamed",
        "definitions": base,
        "queue_pull_request": str(entry.number),
        "queue_pull_request_head": entry.pr_head,
        "queue_built_on": built_on,
        "queue_pull_request_verdict": str(verdict.get("id") if verdict else ""),
    }
    ran = [f"- the definitions that ran are main's at `{base}`"]
    if changed and contract is not None:
        toolchain = {**toolchain, "definitions": sha, "contract": contract.url or str(contract.id)}
        ran = [
            f"- the definitions that ran are its own: it changes {len(changed)} "
            f"({_names(changed)}) against main's tip",
            f"- main's CI contract at `{base}` passed over them: contract run "
            f"{contract.url or contract.id}",
        ]
    receipt = {
        "controller_revision": controller_revision,
        "head_sha": sha,
        "tree_oid": tree_oid,
        "external_revisions": {},
        "toolchain": toolchain,
        "evidence_digests": records,
    }
    summary = "\n".join(
        [
            f"Judged by its own Merge run: every attempt of Merge run {run.url or run.id} "
            "was green.",
            f"- the queue commit of #{entry.number}, built on `{built_on}`, which is "
            + ("main's tip" if built_on == base else "the queue commit of the entry ahead"),
            "- its change is its pull request's own, file by file and line by line",
            f"- its pull request's head `{entry.pr_head}` has a green merge verdict, which holds"
            " its dependency review, its sign-off check and CodeQL",
            *ran,
            f"- {len(records)} job records, their digests in the receipt below",
            *(
                [
                    f"- the flake rule passed {len(flakes)} failed test(s), each of which passed"
                    " alone in a new process, in a file this change does not touch: "
                    + ", ".join(f"`{n}`" for n in flakes)
                ]
                if flakes
                else []
            ),
        ]
    )
    output = merge_verdict.render_output(
        receipt,
        title=(
            f"Green: every planned job passed on #{entry.number}'s queue commit"
            + flakes_note(flakes)
        )[:255],
        summary=summary,
    )
    posting = Posting(sha, "success", output, run.url, f"{run.id}/{run.attempt}")
    return Outcome(posting=posting, cleanup=cleanup)


# ── The contract run ──────────────────────────────────────────────────────────────────────────


def _call(api: Api, method: str, path: str, body: Mapping[str, object] | None, ok: int) -> Any:
    status, answer = api(method, path, body)
    if status != ok:
        raise RuntimeError(f"{method} {path} answered {status}: {answer!r}")
    return answer


def _branch(head: str) -> str:
    if not _SHA.fullmatch(head):
        raise ValueError(f"a contract run is made for a full commit id, not {head!r}")
    return CONTRACT_PREFIX + head


def make_contract_branch(api: Api, repo: str, head: str, base: str) -> str:
    """Point the head's contract branch at ``base``, ``main``'s tip, making it or moving it; the
    branch. No commit is made: the branch is ``main``'s tip itself, so every push trigger there is
    ``main``'s, and none starts on a branch but ``main``."""
    branch = _branch(head)
    if not _SHA.fullmatch(base):
        raise ValueError(f"--base must be a full commit id: {base}")
    status, _ = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", None)
    if status == 404:
        _call(
            api,
            "POST",
            f"/repos/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": base},
            201,
        )
    elif status == 200:
        moved = {"sha": base, "force": True}
        _call(api, "PATCH", f"/repos/{repo}/git/refs/heads/{branch}", moved, 200)
    else:
        raise RuntimeError(f"the branch {branch} could not be read ({status})")
    return branch


def dispatch(api: Api, repo: str, branch: str) -> None:
    """Run ``main``'s contract.yml on the head's contract branch, naming the head."""
    head = branch.removeprefix(CONTRACT_PREFIX)
    if _branch(head) != branch:
        raise ValueError(f"{branch} is not a contract branch")
    name = CONTRACT_WORKFLOW.rsplit("/", 1)[-1]
    path = f"/repos/{repo}/actions/workflows/{name}/dispatches"
    _call(api, "POST", path, {"ref": branch, "inputs": {"head": head}}, 204)


def delete_branch(api: Api, repo: str, branch: str) -> None:
    """Delete a judged or stale contract branch, or a trusted-run branch the old path left; one
    already gone is fine."""
    prefix = next((p for p in (CONTRACT_PREFIX, TRUSTED_PREFIX) if branch.startswith(p)), None)
    if prefix is None or not _SHA.fullmatch(branch.removeprefix(prefix)):
        raise ValueError(f"{branch} is not a contract or trusted-run branch of a full commit id")
    status, answer = api("DELETE", f"/repos/{repo}/git/refs/heads/{branch}", None)
    if status not in (204, 404, 422):
        raise RuntimeError(f"DELETE {branch} answered {status}: {answer!r}")


# ── Posting ───────────────────────────────────────────────────────────────────────────────────


def check_body(body: object) -> Mapping[str, Any]:
    """The body, if it is one completed ``merge-verdict`` check run this module wrote."""
    if not isinstance(body, Mapping) or set(body) != _BODY_KEYS:
        raise ValueError("the body is not one merge-verdict check run")
    if body["name"] != merge_verdict.CHECK_NAME or body["status"] != "completed":
        raise ValueError("the body is not a completed merge-verdict check run")
    if body["conclusion"] not in ("success", "failure"):
        raise ValueError(f"the body's conclusion is {body['conclusion']!r}, not success or failure")
    if not isinstance(body["head_sha"], str) or not _SHA.fullmatch(body["head_sha"]):
        raise ValueError("the body names no head")
    output = body["output"]
    if not isinstance(output, Mapping) or not {"title", "summary"} <= set(output):
        raise ValueError("the body's output has no title or summary")
    if body["conclusion"] == "success":
        merge_verdict.read_receipt(body)
    return body


def github_api(token: str) -> Api:
    def api(method: str, path: str, body: Mapping[str, object] | None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(API + path, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"null")
            except ValueError:
                return error.code, None
        except (OSError, http.client.HTTPException) as error:
            return 0, f"{type(error).__name__}: {error}"

    return api


def github_post(token: str) -> Post:
    api = github_api(token)
    return lambda path, body: api("POST", path, body)


def github_graphql(token: str) -> Query:
    api = github_api(token)

    def query(text: str, variables: Mapping[str, object]) -> Any:
        status, answer = api("POST", "/graphql", {"query": text, "variables": dict(variables)})
        if status != 200:
            raise OSError(f"GraphQL answered {status}")
        return answer

    return query


def post(repo: str, body: Mapping[str, Any], send: Post) -> int:
    """Post the check run; its id."""
    status, answer = send(f"/repos/{repo}/check-runs", body)
    check_id = _int(answer.get("id")) if isinstance(answer, Mapping) else None
    if status != 201 or check_id is None:
        raise RuntimeError(f"the check run was not created ({status}): {answer!r}")
    return check_id


# ── The command ───────────────────────────────────────────────────────────────────────────────


def _output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"{name}={value}\n")


def main(
    argv: list[str] | None = None,
    *,
    get: Get | None = None,
    api: Api | None = None,
    query: Query | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci merge-controller", description=(__doc__ or "").split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("decide", help="judge a Merge run and write the check run to post")
    one.add_argument("--controller-revision", required=True)
    which = one.add_mutually_exclusive_group(required=True)
    which.add_argument("--run-id", type=int, help="a completed run of Merge or Dependency review")
    which.add_argument("--check-run-id", type=int, help="a completed CodeQL check run")
    which.add_argument(
        "--start",
        type=int,
        metavar="RUN_ID",
        help="a pull request's Merge run that has started: start its head's contract beside it;"
        " posts nothing",
    )
    which.add_argument(
        "--head", help="a head whose contract run has reported (its wake): judge it again"
    )
    which.add_argument(
        "--waiting", action="store_true", help="judge again every open head whose red still waits"
    )
    one.add_argument(
        "--controller-app-id",
        type=int,
        help="whose verdicts to read: with --waiting every waiting head is judged again; with a "
        f"run or a check run, also up to {WAITING_PER_RUN} of them",
    )
    one.add_argument("--out", required=True, type=Path)
    two = sub.add_parser("post", help="post the check run with the App's token (GH_TOKEN)")
    two.add_argument("--body", required=True, type=Path)
    three = sub.add_parser(
        "contract-branch", help="point a head's contract branch at main's tip (the App's token)"
    )
    three.add_argument("--head", required=True)
    three.add_argument("--base", required=True)
    four = sub.add_parser("dispatch", help="run main's contract.yml on a contract branch")
    four.add_argument("--branch", required=True)
    five = sub.add_parser(
        "cleanup", help="delete contract and trusted-run branches (the App's token)"
    )
    five.add_argument("--branch", required=True, action="append")
    for each in (one, two, three, four, five):
        each.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    if not _REPO.fullmatch(args.repo):
        parser.error(f"--repo must be OWNER/NAME: {args.repo}")
    token = os.environ.get("GH_TOKEN", "")
    reader = get or merge_verdict.github_get(API, token)
    writer = api or github_api(token)
    if args.command == "decide" and args.start is not None:
        # A run that has started: at most its contract's dispatch. Nothing is posted, nothing is
        # swept and no waiting head is judged again here: only a completed run is judged.
        try:
            started = start_contract(reader, args.repo, args.start)
        except (Unread, trust.Refused) as exc:
            print(f"::warning title=no contract started::{exc}")
            started = Outcome(why=f"unread: {exc}")
        print(started.why)
        _output("dispatch", started.dispatch or "")
        _output("base", started.base or "")
        _output("cleanup", "")
        _output("post", "false")
        return 0
    if args.command == "decide":
        if args.waiting and not args.controller_app_id:
            parser.error("--waiting needs --controller-app-id")
        # The one read is the live API's; a test's reader, given alone, answers every read.
        graph = query if query is not None else None if get else github_graphql(token)
        # A queue commit is judged with the queue's read and the App whose verdicts count, and
        # whether main's rules queue merges is read from those rules.
        queue = (
            Queue(graph, args.controller_app_id, queue_live(reader, args.repo))
            if graph is not None and args.controller_app_id
            else None
        )
        try:
            if args.run_id is not None:
                outcomes = [judge(reader, args.repo, args.run_id, args.controller_revision, queue)]
            elif args.head is not None:
                outcomes = [wake(reader, args.repo, args.head, args.controller_revision, queue)]
            elif args.check_run_id is not None:
                outcomes = [
                    judge_check(
                        reader, args.repo, args.check_run_id, args.controller_revision, queue
                    )
                ]
            else:
                outcomes = judge_waiting(
                    reader,
                    args.repo,
                    args.controller_app_id,
                    args.controller_revision,
                    graph,
                    queue=queue,
                )
        except Unread as exc:
            print(f"::error title=merge verdict unread::{exc}")
            return 2
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            sweep = stale_branches(reader, args.repo, now, main_tip(reader, args.repo))
        except Unread as exc:
            print(f"::warning title=no sweep::the contract branches could not be read: {exc}")
            sweep = []
        # Only one head's own outcome starts a contract run; a sweep never does.
        single = outcomes[0] if len(outcomes) == 1 and not args.waiting else None
        if single is not None and args.controller_app_id:
            # Whatever woke this run, a few heads whose red still waits are judged again too: the
            # schedule fires far less often than it says, and a contract run wakes nothing.
            mine = frozenset({single.posting.head_sha} if single.posting else set())
            try:
                outcomes += judge_waiting(
                    reader,
                    args.repo,
                    args.controller_app_id,
                    args.controller_revision,
                    graph,
                    limit=WAITING_PER_RUN,
                    skip=mine,
                    queue=queue,
                )
            except Unread as exc:
                print(f"::warning title=no waiting heads::{exc}")
        dispatch_head = single.dispatch if single else None
        _output("dispatch", dispatch_head or "")
        _output("base", (single.base if single else None) or "")
        doomed = set(sweep) | {o.cleanup for o in outcomes if o.cleanup}
        if dispatch_head:
            doomed.discard(CONTRACT_PREFIX + dispatch_head)
        _output("cleanup", " ".join(sorted(doomed)))
        postings = [o.posting for o in outcomes if o.posting is not None]
        for o in outcomes:
            if o.posting is None:
                print(f"no verdict to post: {o.why}")
        if not postings:
            _output("post", "false")
            return 0
        bodies = [found.body() for found in postings]
        args.out.write_text(json.dumps(bodies, indent=2) + "\n", encoding="utf-8")
        for found in postings:
            title = found.output["title"]
            print(f"merge-verdict on {found.head_sha}: {found.conclusion}: {title}")
        _output("post", "true")
        return 0
    if not token and api is None:
        print(f"::error title=no token::{args.command} needs GH_TOKEN")
        return 1
    try:
        if args.command == "post":
            bodies = json.loads(args.body.read_text(encoding="utf-8"))
            if not isinstance(bodies, list) or not bodies:
                raise ValueError("the body file is not a list of check runs")
            for body in [check_body(b) for b in bodies]:
                check_id = post(args.repo, body, lambda path, b: writer("POST", path, b))
                said = f"{body['conclusion']} on {body['head_sha']} (check run {check_id})"
                print(f"posted merge-verdict {said}")
        elif args.command == "contract-branch":
            branch = make_contract_branch(writer, args.repo, args.head, args.base)
            print(f"{branch} points at main's tip {args.base}")
            _output("branch", branch)
        elif args.command == "dispatch":
            dispatch(writer, args.repo, args.branch)
            print(f"dispatched {CONTRACT_WORKFLOW} on {args.branch}")
        else:
            for branch in args.branch:
                delete_branch(writer, args.repo, branch)
                print(f"deleted {branch}")
    except (OSError, ValueError, RuntimeError, Unread, trust.Refused) as exc:
        print(f"::error title=merge controller {args.command} failed::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
