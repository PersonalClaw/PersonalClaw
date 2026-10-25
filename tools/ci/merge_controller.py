"""``python -m tools.ci merge-controller``: the merge verdict's poster, advisory for now (#3910).

``.github/workflows/merge-verdict.yml`` runs this when a Merge run (``merge.yml``) completes.
GitHub runs that listener from the default branch, so this is ``main``'s copy whatever the
candidate brings. It reads the API only, by the run's id: never the event's text, an artifact's
contents or anything of the candidate on disk. Both trees are read from the API, so the
candidate's files are never fetched or checked out (``trust.compare_trees``).

``decide`` judges the run and writes the check run it would post. The rules, each failing
closed; a red names every rule the head fails, so one verdict says all it must fix:

1. The run is this repository's ``.github/workflows/merge.yml``, completed, on a ``pull_request``
   or a dispatch. Any other run posts nothing: another workflow named Merge, which a candidate
   can add, a push to ``main`` or a schedule. Only a run of merge.yml itself can bring a verdict.
2. A head that changes a definition (``trust``), compared with ``main``'s tip, is judged by two
   things, both green (#4584 step 4): its own run, by every rule here, which shows the change
   works as written; and ``main``'s CI contract over its definitions, which shows it weakens
   nothing ``main``'s contract holds CI to, whatever its own copy of the contract says
   (``tools/ci/contract.py``). The contract is run only from the head's own ``pull_request``
   run, completed: that run passed the fork approval gate, so no head's code runs in the base
   repository without the approval its own run needed. Until the contract run reports, the head
   is a red that waits on it, which a required check reads as not passed. The poster dispatches
   it with the job's token, whose events start no workflow, so its completion wakes nothing: the
   schedule's sweep of waiting reds judges the head again and posts its verdict.
3. The head is not behind ``main``, so the squash commit lands the tree the run tested, and the
   contract judged the head against the ``main`` it would land on. A head that is behind is
   red whatever else holds, and its definitions are not compared: against ``main``'s tip, a head
   ``main`` has moved past would read as changing every definition ``main`` changed since. So no
   contract runs for it until it updates.

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
5. The head's dependency review passed (#4585 step 6): the newest ``pull_request_target`` run
   of ``dependency-review.yml`` on the head, main's copy, concluded ``success``. One that has
   not run, is still running or did not pass is red. When a Dependency review run completes, the
   poster judges its head again from the head's own completed Merge run (and, for a head that
   changes a definition, its contract run), so they may finish in any order.
6. CodeQL analysed the head in every language ``main``'s CodeQL analyses name, and the pull
   request brings no alert of its own: the newest run of GitHub's dynamic CodeQL workflow on the
   head finished, with an ``Analyze (<language>)`` job that concluded ``success`` for each of
   ``main``'s languages; each language's analysis of the head is processed on the pull request's
   ref (a job's success means its results were uploaded, not yet read); and no alert is open on
   that ref that is not open on ``main``. The ``CodeQL`` summary check is never read for its
   conclusion: GitHub computes it when the first language's analysis uploads, so it reads
   ``neutral`` on most pull requests. A red with a reason that waits (CodeQL still running or
   still processing, or the head's contract run not reported) is marked waiting, whatever its
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
    event, url = str(run.get("event")), str(run.get("html_url") or "")
    return Run(run_id, attempt, head, event, url, branch)


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
    head whose contract run is started, and on which commit of ``main``; and the contract
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
    output = {"title": f"Red: {reasons[0]}"[:255], "summary": summary}
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
    conclusion = newest.get("conclusion")
    return Contract(
        int(newest["id"]),
        str(newest.get("html_url") or ""),
        str(newest.get("status")),
        str(conclusion) if conclusion is not None else None,
    )


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


def codeql_reasons(get: Get, repo: str, head: str) -> list[str]:
    """Why CodeQL's analysis of the head does not pass (rule 6), or nothing."""
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
        return [f"{WAITING}CodeQL has not analysed the head yet"]
    newest = max(ours, key=lambda r: int(r["id"]))
    if newest.get("status") != "completed":
        return [f"{WAITING}CodeQL's analysis of the head is {newest.get('status')!r}, not finished"]
    pull = _PULL_HEAD.fullmatch(str(newest.get("head_branch") or ""))
    if pull is None:
        return ["CodeQL's analysis of the head is not a pull request's"]
    jobs = _pages(get, f"/repos/{repo}/actions/runs/{newest['id']}/jobs", "jobs")
    done = {str(j.get("name")): j.get("conclusion") for j in jobs}
    languages = sorted(codeql_languages(get, repo))
    reasons = [
        f"CodeQL's `Analyze ({lang})` is {done.get(f'Analyze ({lang})')!r}"
        for lang in languages
        if done.get(f"Analyze ({lang})") != "success"
    ]
    if reasons:
        return reasons
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
        return waiting
    alerts = "/repos/{repo}/code-scanning/alerts?state=open&ref={ref}"
    on_pull = {_int(a.get("number")) for a in _all_pages(get, alerts.format(repo=repo, ref=ref))}
    on_main = {
        _int(a.get("number"))
        for a in _all_pages(get, alerts.format(repo=repo, ref="refs/heads/main"))
    }
    brought = sorted(n for n in on_pull - on_main if n is not None)
    if None in on_pull:
        raise Unread("an alert on the pull request names no number")
    if brought:
        named = ", ".join(f"#{n}" for n in brought[:10])
        return [f"CodeQL finds {len(brought)} alerts this pull request brings ({named})"]
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
    """A completed Dependency review run: judge its head again (:func:`rejudge`)."""
    head = run.get("head_sha")
    if run.get("event") != "pull_request_target" or not isinstance(head, str):
        return Outcome(why=f"run {run.get('id')} is not a pull request's dependency review")
    if not _SHA.fullmatch(head):
        raise Unread(f"dependency review run {run.get('id')} names no head")
    return rejudge(get, repo, head, controller_revision)


def judge_check(get: Get, repo: str, check_id: int, controller_revision: str) -> Outcome:
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
    return rejudge(get, repo, head, controller_revision)


def rejudge(get: Get, repo: str, head: str, controller_revision: str) -> Outcome:
    """Judge a head again from its own completed pull_request Merge run, and for a head that
    changes a definition, the contract run that run's verdict waits for."""
    own = _latest_merge_run(get, repo, head_sha=head, event="pull_request")
    if own is None:
        return Outcome(why=f"{head} has no completed Merge run of its own to judge yet")
    return decide(get, repo, own, controller_revision)


def judge(get: Get, repo: str, run_id: int, controller_revision: str) -> Outcome:
    """What a completed run of a workflow the poster follows does: a Merge run is judged; a
    Dependency review, CodeQL or contract run judges its head again."""
    run = _read(get, f"/repos/{repo}/actions/runs/{run_id}")
    if not isinstance(run, Mapping):
        raise Unread(f"run {run_id} is not an object")
    repository = run.get("repository")
    owner = repository.get("full_name") if isinstance(repository, Mapping) else None
    if owner == repo and run.get("path") == DEPENDENCY_REVIEW:
        if run.get("status") != "completed":
            return Outcome(why=f"run {run_id} is {run.get('status')!r}, not completed")
        return decide_review(get, repo, run, controller_revision)
    if owner == repo and run.get("path") == CONTRACT_WORKFLOW:
        branch = str(run.get("head_branch") or "")
        named = branch.removeprefix(CONTRACT_PREFIX)
        if run.get("status") != "completed" or run.get("event") != "workflow_dispatch":
            return Outcome(why=f"run {run_id} is not a completed dispatch of the contract")
        if not branch.startswith(CONTRACT_PREFIX) or not _SHA.fullmatch(named):
            return Outcome(why=f"run {run_id} is on {branch!r}, which names no head")
        return rejudge(get, repo, named, controller_revision)
    if owner == repo and str(run.get("path", "")).startswith(CODEQL_WORKFLOW):
        head = run.get("head_sha")
        if run.get("status") != "completed" or not isinstance(head, str):
            return Outcome(why=f"run {run_id} is not a completed CodeQL analysis")
        if not _SHA.fullmatch(head):
            raise Unread(f"CodeQL run {run_id} names no head")
        return rejudge(get, repo, head, controller_revision)
    return decide(get, repo, run_id, controller_revision)


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
    schedule's own sweep reads every head."""
    waiting = waiting_heads(query, repo, app) if query is not None else None
    if limit is not None:
        if waiting is None:
            print("the one read of the open heads gave no answer: no waiting head judged here")
            return []
        waiting = [h for h in waiting if h not in skip][:limit]
    if waiting is not None and not waiting:
        print("no open pull request's merge verdict still waits: nothing to judge again")
        return []
    if query is not None and waiting is None:
        print("the one read of the open heads gave no answer: reading each head")
    pulls = _all_pages(get, f"/repos/{repo}/pulls?state=open&base=main")[:SWEEP_LIMIT]
    changed: list[Outcome] = []
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
        outcome = rejudge(get, repo, sha, controller_revision)
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


def decide(get: Get, repo: str, run_id: int, controller_revision: str) -> Outcome:
    """What this run's completion does (the module's rules)."""
    if not _SHA.fullmatch(controller_revision):
        raise ValueError(f"the controller revision must be a full commit id: {controller_revision}")
    found = read_run(get, repo, run_id)
    if isinstance(found, str):
        return Outcome(why=found)
    run = found
    head = run.head_sha
    changed: list[str] = []
    contract: Contract | None = None
    # Every reason that holds, so one verdict names all a head must fix.
    reasons: list[str] = []
    try:
        base = main_tip(get, repo)
        tree_oid, head_tree = api_tree(get, repo, head)
        behind = behind_by(get, repo, base, head)
        if behind:
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
        reasons += codeql_reasons(get, repo, head)
        if changed:
            contract = contract_run(get, repo, head, base)
            if contract is not None and contract.status == "completed":
                if contract.conclusion != "success":
                    reasons.append(
                        "main's CI contract fails on its definitions: contract run "
                        f"{contract.url or contract.id} is {contract.conclusion!r}"
                    )
        records = evidence(get, repo, run) if not reasons else {}
        if not reasons and not records:
            reasons.append(f"the run has no `{RECORD_PREFIX}*` artifact, so no evidence")
    except (Unread, trust.Refused) as exc:
        return Outcome(posting=_red(run, [*reasons, f"unread: {exc}"], head))
    # A head that changes a definition: its contract run is started once, at main's tip, and its
    # verdict waits for it. Once that run has reported, its branch goes with the posted verdict.
    # Until it reports, the head is a red that waits on it: a contract run dispatched with the
    # job's token raises no workflow_run when it completes, so the schedule's sweep, which judges
    # every waiting red again, is what posts the head's verdict once it has.
    start = head if changed and contract is None else None
    judged = changed and contract is not None and contract.status == "completed"
    cleanup = CONTRACT_PREFIX + head if judged else None
    if changed and not judged:
        running = (
            "is started"
            if contract is None
            else f"is {contract.status!r}: contract run {contract.url or contract.id}"
        )
        reasons.append(f"{WAITING_CONTRACT}{running}")
    if reasons:
        return Outcome(posting=_red(run, reasons, head), dispatch=start, base=base, cleanup=cleanup)
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
            "- the head is up to date with main",
            "- its dependency review passed",
            "- CodeQL analysed every language main has, and found no alert it brings",
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
    if args.command == "decide":
        if args.waiting and not args.controller_app_id:
            parser.error("--waiting needs --controller-app-id")
        try:
            if args.run_id is not None:
                outcomes = [judge(reader, args.repo, args.run_id, args.controller_revision)]
            elif args.check_run_id is not None:
                outcomes = [
                    judge_check(reader, args.repo, args.check_run_id, args.controller_revision)
                ]
            else:
                # The one read is the live API's; a test's reader, given alone, answers every read.
                graph = query if query is not None else None if get else github_graphql(token)
                outcomes = judge_waiting(
                    reader, args.repo, args.controller_app_id, args.controller_revision, graph
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
            graph = query if query is not None else None if get else github_graphql(token)
            try:
                outcomes += judge_waiting(
                    reader,
                    args.repo,
                    args.controller_app_id,
                    args.controller_revision,
                    graph,
                    limit=WAITING_PER_RUN,
                    skip=mine,
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
