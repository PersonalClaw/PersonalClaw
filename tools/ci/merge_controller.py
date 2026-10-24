"""``python -m tools.ci merge-controller``: the merge verdict's poster, advisory for now (#3910).

``.github/workflows/merge-verdict.yml`` runs this when a Merge run (``merge.yml``) completes.
GitHub runs that listener from the default branch, so this is ``main``'s copy whatever the
candidate brings. It reads the API only, by the run's id: never the event's text, an artifact's
contents or anything of the candidate on disk. Both trees are read from the API, so the
candidate's files are never fetched or checked out (``trust.compare_trees``).

``decide`` judges the run and writes the check run it would post. The rules, in order, each
failing closed:

1. The run is this repository's ``.github/workflows/merge.yml``, completed, on a ``pull_request``
   or a dispatch. Any other run posts nothing: another workflow named Merge, which a candidate
   can add, a push to ``main`` or a schedule. Only a run of merge.yml itself can bring a verdict.
2. The head changes no definition (``trust``), compared with ``main``'s tip. A head that does is
   not judged by its own run, which used its own workflows and ``tools/ci``: that run posts
   nothing, and starts a run of ``main``'s definitions on the head's tree instead (#4584 step 4),
   but only from the head's own ``pull_request`` run, completed: that run passed the fork
   approval gate, so no head's code runs in the base repository without the approval its own run
   needed. A head with no such run gets no trusted run. Until that run reports, the head has no
   verdict, which a required check reads as not passed.
3. The head is not behind ``main``, so the squash commit lands the tree the run tested. This is
   read before the definitions: a head ``main`` has moved past is red, told to update, and starts
   no trusted run, since against ``main``'s tip it would read as changing every definition
   ``main`` changed since.

**The trusted run.** ``trusted`` builds one commit through the API with the controller App's
token: ``main``'s tip as its parent, ``main``'s definitions, and every other path as the head
has it. It points the branch ``trusted-run/<head>`` at it, and ``dispatch`` runs ``main``'s
``merge.yml`` there with the job's own token. The commit changes no workflow file against its
parent, and every workflow's push trigger is ``main``'s alone, so the push starts nothing and
needs no permission over workflow files. When that run completes, its verdict is the head's,
posted on the head, only when its commit is still exactly that: a parent on ``main``, the same
definitions as ``main``'s tip, and the head's every other path. One whose definitions ``main``
has moved past starts again, if the head still has its own completed run; any other is red.
Once its verdict is posted, red or green, its branch is deleted, and every completed Merge run
also sweeps away any trusted-run branch older than a day.
4. The ``results`` job, which judges the run with ``main``'s ``verdict.py``, concluded
   ``success`` on every attempt, by the API's job list. Missing, cancelled, skipped or red on any
   attempt is red: a rerun never turns a red into a green.
5. The head's dependency review passed (#4585 step 6): the newest ``pull_request_target`` run
   of ``dependency-review.yml`` on the head, main's copy, concluded ``success``. One that has
   not run, is still running or did not pass is red. When a Dependency review run completes, the
   poster judges its head again from the head's own completed Merge run (or its trusted run), so
   the two may finish in either order. It is the one input besides the run: CodeQL's PR check
   is not read until it reports reliably.
6. A green carries the receipt ``verify-check-run`` reads (``merge_verdict.render_output``):
   this controller's commit on ``main``, the head and its tree, the runner and the definitions
   commit, and the digests of the run's ``ci-record-*`` artifacts as the API reports them. A run
   with no record artifact has no evidence, so no green.

The verdict is ``success`` or ``failure``, never neutral, and an answer the API would not give is
``failure``, named as unread. ``post`` sends the check run with the controller App's token, and
refuses a body that is not one completed ``merge-verdict`` check run.

It is not a required check yet: ``main-protect`` names it only once #3915's flip is live and
posted verdicts have matched their pull requests' real results. Standard library only. See
docs/maintainers/ci.md, "The merge controller's poster".
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime
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

from . import merge_verdict, trust, workflows

#: The run that brings a verdict: merge.yml, by its path in this repository.
MERGE_WORKFLOW = f".github/workflows/{workflows.MERGE}"
#: The events whose merge.yml run judges a pull request's head: the pull request itself, and a
#: dispatch of the head's branch (how a merge run is started until #3915's flip).
JUDGED_EVENTS = frozenset({"pull_request", "workflow_dispatch"})
#: The dependency review: a pull_request_target workflow, so the run is main's copy.
DEPENDENCY_REVIEW = ".github/workflows/dependency-review.yml"
#: The branch a trusted run of a head is made on: this prefix, then the head's full id.
TRUSTED_PREFIX = "trusted-run/"
#: A trusted-run branch older than this is swept away, whatever became of its run.
SWEEP_AFTER = datetime.timedelta(days=1)
#: merge.yml's job that judges the run with main's verdict.py.
RESULTS_JOB = "results"
#: The artifacts each planned entry uploads: its record, the evidence a green names.
RECORD_PREFIX = "ci-record-"
#: Where the verdict says it is advisory, until main-protect requires it.
ADVISORY = (
    "Advisory: `merge-verdict` is not a required check yet (#3910). It becomes one once #3915's "
    "flip is live and posted verdicts have matched their pull requests' real results."
)

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
    #: For a trusted run, the head it judges; None for a head's own run.
    candidate: str | None = None


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
    candidate = None
    if run.get("event") == "workflow_dispatch" and branch.startswith(TRUSTED_PREFIX):
        candidate = branch.removeprefix(TRUSTED_PREFIX)
        if not _SHA.fullmatch(candidate):
            return f"run {run_id} is on {branch!r}, which names no head"
    event, url = str(run.get("event")), str(run.get("html_url") or "")
    return Run(run_id, attempt, head, event, url, branch, candidate)


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
    head a trusted run is started for, and on which commit of ``main``; and the trusted run's
    branch to delete once its verdict is posted. ``why`` says why nothing is posted."""

    posting: Posting | None = None
    why: str = ""
    dispatch: str | None = None
    base: str | None = None
    cleanup: str | None = None


def _red(run: Run, reasons: list[str], head: str) -> Posting:
    summary = "\n".join(
        ["This head's merge verdict is red:", "", *[f"- {r}" for r in reasons], ""]
        + [f"From Merge run {run.url or run.id}, attempt {run.attempt}.", "", ADVISORY]
    )
    return Posting(
        head,
        "failure",
        {"title": f"Red: {reasons[0]}"[:255], "summary": summary},
        run.url,
        f"{run.id}/{run.attempt}",
    )


def _names(paths: list[str]) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:10])
    return shown + (f" and {len(paths) - 10} more" if len(paths) > 10 else "")


def trusted_tree_problem(
    get: Get,
    repo: str,
    commit: str,
    tree: trust.Tree,
    base: str,
    base_tree: trust.Tree,
    head_tree: trust.Tree,
) -> tuple[str, bool]:
    """Why a trusted run's commit is not ``main``'s definitions on the head's tree, or ``""``;
    and whether making it again would mend it (``main``'s definitions moved past it)."""
    answer = _read(get, f"/repos/{repo}/git/commits/{commit}")
    parents = answer.get("parents") if isinstance(answer, Mapping) else None
    if not isinstance(parents, list) or len(parents) != 1 or not isinstance(parents[0], Mapping):
        return "its commit has not exactly one parent", False
    parent = parents[0].get("sha")
    if not isinstance(parent, str) or not _SHA.fullmatch(parent):
        raise Unread(f"commit {commit}'s parent names no commit")
    if behind_by(get, repo, parent, base):
        return f"its parent {parent} is not on main", False
    if not trust.compare_trees(base_tree, tree, base=base, head=commit).trusted:
        return "main's definitions have moved since its commit was made", True
    definitions = (
        trust.tree_definitions(base_tree)
        | trust.tree_definitions(tree)
        | trust.tree_definitions(head_tree)
    )
    differ = sorted(
        p
        for p in (set(tree.blobs) | set(head_tree.blobs)) - definitions
        if tree.blobs.get(p) != head_tree.blobs.get(p)
    )
    if differ:
        return f"its tree is not the head's outside the definitions: {_names(differ)}", False
    return "", False


def dependency_review_reasons(get: Get, repo: str, head: str) -> list[str]:
    """Why the head's dependency review is not green (rule 5), or nothing."""
    name = DEPENDENCY_REVIEW.rsplit("/", 1)[-1]
    query = urllib.parse.urlencode(
        {"head_sha": head, "event": "pull_request_target", "per_page": _PER_PAGE}
    )
    answer = _read(get, f"/repos/{repo}/actions/workflows/{name}/runs?{query}")
    runs = answer.get("workflow_runs") if isinstance(answer, Mapping) else None
    if not isinstance(runs, list):
        raise Unread(f"{head}'s dependency reviews are not a list")
    ours = [
        r
        for r in runs
        if isinstance(r, Mapping)
        and r.get("path") == DEPENDENCY_REVIEW
        and r.get("head_sha") == head
        and _int(r.get("id")) is not None
    ]
    if not ours:
        return ["the head has no dependency review (dependency-review.yml) yet"]
    newest = max(ours, key=lambda r: int(r["id"]))
    if newest.get("status") != "completed":
        return [f"the head's dependency review is {newest.get('status')!r}, not finished"]
    if newest.get("conclusion") != "success":
        return [f"the head's dependency review is {newest.get('conclusion')!r}"]
    return []


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


def decide_review(get: Get, repo: str, run: Mapping[str, Any], controller_revision: str) -> Outcome:
    """A completed Dependency review run: judge its head again from the head's own completed
    pull_request Merge run, or, for a head that changes a definition, from its trusted run.
    Nothing is dispatched from here; the head's own Merge run does that."""
    head = run.get("head_sha")
    if run.get("event") != "pull_request_target" or not isinstance(head, str):
        return Outcome(why=f"run {run.get('id')} is not a pull request's dependency review")
    if not _SHA.fullmatch(head):
        raise Unread(f"dependency review run {run.get('id')} names no head")
    own = _latest_merge_run(get, repo, head_sha=head, event="pull_request")
    if own is None:
        return Outcome(why=f"{head} has no completed Merge run of its own to judge yet")
    outcome = decide(get, repo, own, controller_revision)
    if outcome.dispatch is None:
        return outcome
    trusted = _latest_merge_run(get, repo, branch=TRUSTED_PREFIX + head, event="workflow_dispatch")
    if trusted is None:
        return Outcome(why=f"{head} changes a CI definition and its trusted run has not completed")
    return decide(get, repo, trusted, controller_revision)


def judge(get: Get, repo: str, run_id: int, controller_revision: str) -> Outcome:
    """What a completed run of a workflow the poster follows does: a Merge run is judged; a
    Dependency review run judges its head again."""
    run = _read(get, f"/repos/{repo}/actions/runs/{run_id}")
    if not isinstance(run, Mapping):
        raise Unread(f"run {run_id} is not an object")
    repository = run.get("repository")
    owner = repository.get("full_name") if isinstance(repository, Mapping) else None
    if owner == repo and run.get("path") == DEPENDENCY_REVIEW:
        if run.get("status") != "completed":
            return Outcome(why=f"run {run_id} is {run.get('status')!r}, not completed")
        return decide_review(get, repo, run, controller_revision)
    return decide(get, repo, run_id, controller_revision)


def own_run_completed(get: Get, repo: str, head: str) -> bool:
    """Whether the head has a completed ``pull_request`` run of merge.yml of its own: the run
    that passed the fork approval gate, which a trusted run of it waits for."""
    query = urllib.parse.urlencode(
        {"head_sha": head, "event": "pull_request", "status": "completed", "per_page": 1}
    )
    answer = _read(get, f"/repos/{repo}/actions/workflows/{workflows.MERGE}/runs?{query}")
    count = _int(answer.get("total_count")) if isinstance(answer, Mapping) else None
    if count is None:
        raise Unread(f"{head}'s own runs give no count")
    return count > 0


def stale_branches(get: Get, repo: str, now: datetime.datetime) -> list[str]:
    """Every trusted-run branch whose commit was made more than :data:`SWEEP_AFTER` ago."""
    refs = _read(get, f"/repos/{repo}/git/matching-refs/heads/{TRUSTED_PREFIX}")
    if not isinstance(refs, list):
        raise Unread("the trusted-run branches are not a list")
    stale: list[str] = []
    for ref in refs:
        name = ref.get("ref") if isinstance(ref, Mapping) else None
        target = ref.get("object") if isinstance(ref, Mapping) else None
        sha = target.get("sha") if isinstance(target, Mapping) else None
        if not isinstance(name, str) or not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise Unread("a trusted-run branch names no commit")
        branch = name.removeprefix("refs/heads/")
        if not _SHA.fullmatch(branch.removeprefix(TRUSTED_PREFIX)):
            continue
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


def decide(get: Get, repo: str, run_id: int, controller_revision: str) -> Outcome:
    """What this run's completion does (the module's rules)."""
    if not _SHA.fullmatch(controller_revision):
        raise ValueError(f"the controller revision must be a full commit id: {controller_revision}")
    found = read_run(get, repo, run_id)
    if isinstance(found, str):
        return Outcome(why=found)
    run = found
    head = run.candidate or run.head_sha
    cleanup = run.branch if run.candidate else None
    try:
        base = main_tip(get, repo)
        _, base_tree = api_tree(get, repo, base)
        tree_oid, head_tree = api_tree(get, repo, head)
        behind = behind_by(get, repo, base, head)
        if behind and run.candidate is None:
            # Checked before the definitions: against main's tip, a head main has moved past
            # would read as changing every definition main changed since, and it must update
            # before any run of it can count.
            stale = f"the head is {behind} commits behind main: update the branch"
            return Outcome(posting=_red(run, [stale], head))
        if run.candidate is None:
            comparison = trust.compare_trees(base_tree, head_tree, base=base, head=head)
            if not comparison.trusted:
                changed = [c.path for c in comparison.changes]
                if run.event != "pull_request":
                    return Outcome(
                        why=(
                            f"{head} changes {len(changed)} CI definitions, and this is not its "
                            "own pull_request run, so no trusted run is made from it"
                        )
                    )
                return Outcome(
                    why=(
                        f"{head} changes {len(changed)} CI definitions ({_names(changed)}), so "
                        "its own run is not judged: a run of main's definitions on its tree is "
                        "started instead"
                    ),
                    dispatch=head,
                    base=base,
                )
            judged = "its own Merge run"
        else:
            _, run_tree = api_tree(get, repo, run.head_sha)
            problem, again = trusted_tree_problem(
                get, repo, run.head_sha, run_tree, base, base_tree, head_tree
            )
            if again and not own_run_completed(get, repo, head):
                return Outcome(
                    why=(
                        f"the trusted run of {head} is stale, and the head has no completed "
                        "pull_request run of its own, so none is made again"
                    ),
                    cleanup=cleanup,
                )
            if again:
                return Outcome(
                    why=f"the trusted run of {head} is stale: {problem}; it is started again",
                    dispatch=head,
                    base=base,
                )
            if problem:
                red = _red(run, [f"the trusted run: {problem}"], head)
                return Outcome(posting=red, cleanup=cleanup)
            judged = f"a run of main's definitions on its tree (`{run.head_sha}`)"
        reasons, labels = results_reasons(get, repo, run)
        reasons += dependency_review_reasons(get, repo, head)
        if behind:
            reasons.append(f"the head is {behind} commits behind main: update the branch")
        records = evidence(get, repo, run) if not reasons else {}
        if not reasons and not records:
            reasons.append(f"the run has no `{RECORD_PREFIX}*` artifact, so no evidence")
    except (Unread, trust.Refused) as exc:
        return Outcome(posting=_red(run, [f"unread: {exc}"], head), cleanup=cleanup)
    if reasons:
        return Outcome(posting=_red(run, reasons, head), cleanup=cleanup)
    receipt = {
        "controller_revision": controller_revision,
        "head_sha": head,
        "tree_oid": tree_oid,
        "external_revisions": {},
        "toolchain": {"runner": ",".join(labels) or "unnamed", "definitions": base},
        "evidence_digests": records,
    }
    summary = "\n".join(
        [
            f"Judged by {judged}: every attempt of Merge run {run.url or run.id} was green.",
            f"- the definitions that ran are main's at `{base}`",
            "- the head is up to date with main",
            "- its dependency review passed",
            f"- {len(records)} job records, their digests in the receipt below",
            "",
            ADVISORY,
        ]
    )
    output = merge_verdict.render_output(
        receipt, title="Green: every planned job passed on this head", summary=summary
    )
    posting = Posting(head, "success", output, run.url, f"{run.id}/{run.attempt}")
    return Outcome(posting=posting, cleanup=cleanup)


# ── The trusted run ───────────────────────────────────────────────────────────────────────────


def _call(api: Api, method: str, path: str, body: Mapping[str, object] | None, ok: int) -> Any:
    status, answer = api(method, path, body)
    if status != ok:
        raise RuntimeError(f"{method} {path} answered {status}: {answer!r}")
    return answer


def _kind(entry: trust.Entry) -> str:
    return "commit" if entry.mode == "160000" else "blob"


def _branch(head: str) -> str:
    if not _SHA.fullmatch(head):
        raise ValueError(f"a trusted run is made for a full commit id, not {head!r}")
    return TRUSTED_PREFIX + head


def make_trusted(get: Get, api: Api, repo: str, head: str, base: str) -> tuple[str, str]:
    """Build ``main``'s definitions on the head's tree as one commit on ``base``, and point the
    head's trusted-run branch at it; the commit and the branch."""
    branch = _branch(head)
    base_oid, base_tree = api_tree(get, repo, base)
    _, head_tree = api_tree(get, repo, head)
    definitions = trust.tree_definitions(base_tree) | trust.tree_definitions(head_tree)
    entries: list[dict[str, object]] = []
    for path, entry in sorted(head_tree.blobs.items()):
        if path not in definitions and base_tree.blobs.get(path) != entry:
            entries.append(
                {"path": path, "mode": entry.mode, "type": _kind(entry), "sha": entry.oid}
            )
    for path, entry in sorted(base_tree.blobs.items()):
        if path not in definitions and path not in head_tree.blobs:
            entries.append({"path": path, "mode": entry.mode, "type": _kind(entry), "sha": None})
    tree = base_oid
    if entries:
        made = _call(
            api, "POST", f"/repos/{repo}/git/trees", {"base_tree": base_oid, "tree": entries}, 201
        )
        made_oid = made.get("sha") if isinstance(made, Mapping) else None
        if not isinstance(made_oid, str) or not _SHA.fullmatch(made_oid):
            raise RuntimeError(f"the trusted run's tree has no id: {made!r}")
        tree = made_oid
    message = (
        f"main's definitions on {head}\n\nThe tree a trusted run of {head} tests (#3910): "
        f"main's CI definitions at {base}, and every other path as the head has it. It is "
        "never merged; the branch is deleted once its verdict is posted.\n"
    )
    made = _call(
        api,
        "POST",
        f"/repos/{repo}/git/commits",
        {"message": message, "tree": tree, "parents": [base]},
        201,
    )
    commit = made.get("sha") if isinstance(made, Mapping) else None
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        raise RuntimeError(f"the trusted run's commit has no id: {made!r}")
    status, _ = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", None)
    if status == 404:
        ref = {"ref": f"refs/heads/{branch}", "sha": commit}
        _call(api, "POST", f"/repos/{repo}/git/refs", ref, 201)
    elif status == 200:
        moved = {"sha": commit, "force": True}
        _call(api, "PATCH", f"/repos/{repo}/git/refs/heads/{branch}", moved, 200)
    else:
        raise RuntimeError(f"the branch {branch} could not be read ({status})")
    return commit, branch


def dispatch(api: Api, repo: str, branch: str) -> None:
    """Run ``main``'s merge.yml on the trusted-run branch."""
    _branch(branch.removeprefix(TRUSTED_PREFIX))
    path = f"/repos/{repo}/actions/workflows/{workflows.MERGE}/dispatches"
    _call(api, "POST", path, {"ref": branch}, 204)


def delete_branch(api: Api, repo: str, branch: str) -> None:
    """Delete a judged trusted run's branch; one already gone is fine."""
    _branch(branch.removeprefix(TRUSTED_PREFIX))
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


def main(argv: list[str] | None = None, *, get: Get | None = None, api: Api | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci merge-controller", description=(__doc__ or "").split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("decide", help="judge a Merge run and write the check run to post")
    one.add_argument("--controller-revision", required=True)
    one.add_argument("--run-id", required=True, type=int)
    one.add_argument("--out", required=True, type=Path)
    two = sub.add_parser("post", help="post the check run with the App's token (GH_TOKEN)")
    two.add_argument("--body", required=True, type=Path)
    three = sub.add_parser("trusted", help="make a head's trusted-run commit (the App's token)")
    three.add_argument("--candidate", required=True)
    three.add_argument("--base", required=True)
    four = sub.add_parser("dispatch", help="run main's merge.yml on a trusted-run branch")
    four.add_argument("--branch", required=True)
    five = sub.add_parser("cleanup", help="delete trusted-run branches (the App's token)")
    five.add_argument("--branch", required=True, action="append")
    for each in (one, two, three, four, five):
        each.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    if not _REPO.fullmatch(args.repo):
        parser.error(f"--repo must be OWNER/NAME: {args.repo}")
    token = os.environ.get("GH_TOKEN", "")
    reader = get or merge_verdict.github_get(API, token)
    writer = api or github_api(token)
    if args.command == "decide":
        try:
            outcome = judge(reader, args.repo, args.run_id, args.controller_revision)
        except Unread as exc:
            print(f"::error title=merge verdict unread::{exc}")
            return 2
        try:
            sweep = stale_branches(reader, args.repo, datetime.datetime.now(datetime.timezone.utc))
        except Unread as exc:
            print(f"::warning title=no sweep::the trusted-run branches could not be read: {exc}")
            sweep = []
        _output("dispatch", outcome.dispatch or "")
        _output("base", outcome.base or "")
        doomed = set(sweep) | ({outcome.cleanup} if outcome.cleanup else set())
        if outcome.dispatch:
            doomed.discard(TRUSTED_PREFIX + outcome.dispatch)
        _output("cleanup", " ".join(sorted(doomed)))
        if outcome.posting is None:
            print(f"no verdict to post: {outcome.why}")
            _output("post", "false")
            return 0
        found = outcome.posting
        args.out.write_text(json.dumps(found.body(), indent=2) + "\n", encoding="utf-8")
        print(f"merge-verdict on {found.head_sha}: {found.conclusion}: {found.output['title']}")
        _output("post", "true")
        return 0
    if not token and api is None:
        print(f"::error title=no token::{args.command} needs GH_TOKEN")
        return 1
    try:
        if args.command == "post":
            body = check_body(json.loads(args.body.read_text(encoding="utf-8")))
            check_id = post(args.repo, body, lambda path, b: writer("POST", path, b))
            said = f"{body['conclusion']} on {body['head_sha']} (check run {check_id})"
            print(f"posted merge-verdict {said}")
        elif args.command == "trusted":
            if not _SHA.fullmatch(args.base):
                raise ValueError(f"--base must be a full commit id: {args.base}")
            commit, branch = make_trusted(reader, writer, args.repo, args.candidate, args.base)
            print(f"main's definitions on {args.candidate}: {commit} on {branch}")
            _output("branch", branch)
        elif args.command == "dispatch":
            dispatch(writer, args.repo, args.branch)
            print(f"dispatched {workflows.MERGE} on {args.branch}")
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
