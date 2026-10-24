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
   not judged: its run used its own workflows and ``tools/ci``, so its ``results`` say nothing
   about what ``main``'s would. Until a run of ``main``'s definitions on such a head exists
   (#4584 step 4), its verdict is red, named as not judged.
3. The head is not behind ``main``, so the squash commit lands the tree the run tested.
4. The ``results`` job, which judges the run with ``main``'s ``verdict.py``, concluded
   ``success`` on every attempt, by the API's job list. Missing, cancelled, skipped or red on any
   attempt is red: a rerun never turns a red into a green.
5. A green carries the receipt ``verify-check-run`` reads (``merge_verdict.render_output``):
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
    return Run(run_id, attempt, head, str(run.get("event")), str(run.get("html_url") or ""))


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


def _red(run: Run, reasons: list[str]) -> Posting:
    summary = "\n".join(
        ["This head's merge verdict is red:", "", *[f"- {r}" for r in reasons], ""]
        + [f"From Merge run {run.url or run.id}, attempt {run.attempt}.", "", ADVISORY]
    )
    return Posting(
        run.head_sha,
        "failure",
        {"title": f"Red: {reasons[0]}"[:255], "summary": summary},
        run.url,
        f"{run.id}/{run.attempt}",
    )


def decide(get: Get, repo: str, run_id: int, controller_revision: str) -> Posting | str:
    """The check run this run's verdict posts, or why it posts none (rule 1)."""
    if not _SHA.fullmatch(controller_revision):
        raise ValueError(f"the controller revision must be a full commit id: {controller_revision}")
    found = read_run(get, repo, run_id)
    if isinstance(found, str):
        return found
    run = found
    try:
        reasons, labels = results_reasons(get, repo, run)
        base = main_tip(get, repo)
        _, base_tree = api_tree(get, repo, base)
        tree_oid, head_tree = api_tree(get, repo, run.head_sha)
        comparison = trust.compare_trees(base_tree, head_tree, base=base, head=run.head_sha)
        if not comparison.trusted:
            names = ", ".join(f"`{c.path}`" for c in comparison.changes[:10])
            more = len(comparison.changes) - 10
            reasons.insert(
                0,
                f"not judged: the head changes {len(comparison.changes)} CI definitions "
                f"({names}{f' and {more} more' if more > 0 else ''}), so its own run is advisory, "
                "and no run of main's definitions on its tree exists yet (#4584)",
            )
        behind = behind_by(get, repo, base, run.head_sha)
        if behind:
            reasons.append(f"the head is {behind} commits behind main: update the branch")
        records = evidence(get, repo, run) if not reasons else {}
        if not reasons and not records:
            reasons.append(f"the run has no `{RECORD_PREFIX}*` artifact, so no evidence")
    except (Unread, trust.Refused) as exc:
        return _red(run, [f"unread: {exc}"])
    if reasons:
        return _red(run, reasons)
    receipt = {
        "controller_revision": controller_revision,
        "head_sha": run.head_sha,
        "tree_oid": tree_oid,
        "external_revisions": {},
        "toolchain": {"runner": ",".join(labels) or "unnamed", "definitions": base},
        "evidence_digests": records,
    }
    summary = "\n".join(
        [
            f"Every attempt of Merge run {run.url or run.id} judged this head green.",
            f"- the head changes no CI definition against main's `{base}`",
            "- it is up to date with main",
            f"- {len(records)} job records, their digests in the receipt below",
            "",
            ADVISORY,
        ]
    )
    output = merge_verdict.render_output(
        receipt, title="Green: every planned job passed on this head", summary=summary
    )
    return Posting(run.head_sha, "success", output, run.url, f"{run.id}/{run.attempt}")


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


def github_post(token: str) -> Post:
    def post(path: str, body: Mapping[str, object]) -> tuple[int, object]:
        request = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST")
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

    return post


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


def main(argv: list[str] | None = None, *, get: Get | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci merge-controller", description=(__doc__ or "").split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("decide", help="judge a Merge run and write the check run to post")
    one.add_argument("--repo", required=True)
    one.add_argument("--run-id", required=True, type=int)
    one.add_argument("--controller-revision", required=True)
    one.add_argument("--out", required=True, type=Path)
    two = sub.add_parser("post", help="post the check run with the App's token (GH_TOKEN)")
    two.add_argument("--repo", required=True)
    two.add_argument("--body", required=True, type=Path)
    args = parser.parse_args(argv)
    if not _REPO.fullmatch(args.repo):
        parser.error(f"--repo must be OWNER/NAME: {args.repo}")
    token = os.environ.get("GH_TOKEN", "")
    if args.command == "decide":
        reader = get or merge_verdict.github_get(API, token)
        try:
            found = decide(reader, args.repo, args.run_id, args.controller_revision)
        except Unread as exc:
            print(f"::error title=merge verdict unread::{exc}")
            return 2
        if isinstance(found, str):
            print(f"no verdict to post: {found}")
            _output("post", "false")
            return 0
        args.out.write_text(json.dumps(found.body(), indent=2) + "\n", encoding="utf-8")
        print(f"merge-verdict on {found.head_sha}: {found.conclusion}: {found.output['title']}")
        _output("post", "true")
        return 0
    try:
        body = check_body(json.loads(args.body.read_text(encoding="utf-8")))
        if not token:
            raise ValueError("no GH_TOKEN: the controller App's token posts the verdict")
        check_id = post(args.repo, body, github_post(token))
    except (OSError, ValueError, RuntimeError, trust.Refused) as exc:
        print(f"::error title=merge verdict not posted::{exc}")
        return 1
    print(f"posted merge-verdict {body['conclusion']} on {body['head_sha']} (check run {check_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
