"""``python -m tools.ci verify-landed``: each commit a push to ``main`` lands is the tree its pull
request's merge verdict verified (#3908, rule 4).

``main`` takes a pull request only by squash, on a head up to date with ``main``, once its
``merge-verdict`` is green (#3910), so the squash commit's tree is the head's tree, the one the
verdict's receipt names. Under main's merge queue (0.4.0 A1) the commit that lands is the queue
commit itself: main as it stood plus the entry, built and tested before main reached it, whose
tree is the head's only when the head was up to date. The controller judges that commit's own
``merge_group`` run and posts its verdict on it, so a queue commit is held to that verdict. This
holds every commit a push brings to one of the two, read from GitHub and local git, so a commit
that lands another tree, or that no green verdict stands behind, is red the moment it lands:

1. The commit is the merge commit of exactly one merged pull request
   (``GET /repos/{repo}/commits/{sha}/pulls``, its ``merge_commit_sha``). ``GitHub
   <noreply@github.com>`` is also the committer of a commit made in the web editor or of a
   revert from a pull request's page, and provenance's rule 2 passes all three, so this is
   what tells a squash merge from them.
2. Which commit's verdict stands behind it. The commit is a queue commit when a ``merge_group``
   run of merge.yml ran on it, in this repository (:func:`_queue_run`); every such run must be
   the pull request's own queue entry (its branch ``gh-readonly-queue/main/pr-<n>-<base>``, with
   ``<n>`` that pull request) and the newest must have passed. A branch so named is never enough
   alone: a push to it is a ``push`` run, not a ``merge_group`` one. A queue commit's verdict is
   the one on itself; any other commit's is the one on its pull request's head. Which one is
   read never depends on main's rules as they read when this runs, so a rerun judges a landing
   by what stood behind it when it landed.
3. That verdict counts, by :func:`merge_verdict.verify`'s rules: the App main's rule names, its
   newest run green, one receipt naming that commit and its tree, a controller revision on
   ``main``.
4. The commit's own tree, read with local git, is the receipt's ``tree_oid``.

Each commit that passes gets a record: the pull request, its head, the commit whose verdict was
read and its queue run if it is a queue commit, the verified tree, the ``main`` commit, the
verdict's check run and its controller revision. ``--record`` writes them
as JSON, one array, in the range's order.

Each violation is one line, ``<sha> rule 4 <what was found>``, on stdout. The exit code is 0
when every commit passes, 1 for any violation, and 2 when there is nothing that can be checked:
an empty or unknown range, or an API answer that cannot be read. A check that cannot run never
passes, and writes no record. It uses only the standard library. See
``docs/maintainers/ci.md``, "Commit provenance".
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.parse
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import merge_verdict

_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

#: The workflow whose ``merge_group`` runs a queue commit is built and judged by, and its event.
QUEUE_WORKFLOW = ".github/workflows/merge.yml"
QUEUE_EVENT = "merge_group"
#: A queue commit's branch, as GitHub names it: the pull request whose entry it is, then the
#: commit it was built on. ``main_replay.QUEUE_BRANCH``'s pattern; a test holds the two equal.
QUEUE_BRANCH = re.compile(r"gh-readonly-queue/main/pr-(?P<number>[1-9][0-9]*)-[0-9a-f]{40}")
_PER_PAGE = 100


@dataclass(frozen=True)
class Landed:
    """What a commit that passes rule 4 is recorded as."""

    pull_request: int
    head_sha: str
    #: The commit whose verdict was read: the queue commit itself, or the pull request's head.
    verified_sha: str
    #: The queue commit's newest ``merge_group`` run of merge.yml, or None for a head's landing.
    queue_run: int | None
    tree_oid: str
    main_sha: str
    check_run_id: int
    controller_revision: str


class Unusable(Exception):
    """Nothing that can be checked: exit 2."""


def _git(git_dir: Path, *args: str) -> str:
    # The repository is named explicitly, and replace refs are off, so neither an exported
    # GIT_DIR nor a replace ref can change what a commit's tree reads as.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    proc = subprocess.run(
        ["git", "-C", str(git_dir), *args], capture_output=True, text=True, env=env, check=False
    )
    if proc.returncode != 0:
        raise Unusable(f"git {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout.strip()


def commits_in(git_dir: Path, spec: str) -> list[str]:
    """The commits ``BEFORE..AFTER`` brings, oldest first; an empty or unknown range is
    refused."""
    before, sep, after = spec.partition("..")
    if not sep or not _SHA.fullmatch(before) or not _SHA.fullmatch(after):
        raise Unusable(f"--range must be BEFORE..AFTER, two full commit ids, got {spec!r}")
    for sha in (before, after):
        _git(git_dir, "cat-file", "-e", f"{sha}^{{commit}}")
    commits = _git(git_dir, "rev-list", "--reverse", f"{before}..{after}").split()
    if not commits:
        raise Unusable(f"{spec} brings no commit")
    return commits


def _pull_request(get: merge_verdict.Get, repo: str, sha: str) -> tuple[int, str] | str:
    """The merged pull request whose merge commit ``sha`` is, as (number, head), or why there is
    none."""
    try:
        pulls = get(f"/repos/{repo}/commits/{sha}/pulls?per_page=100")
    # HTTPException: a body cut short, which is not an OSError (as merge_verdict reads it).
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise Unusable(f"the pull requests of {sha} could not be read: {exc}") from exc
    if not isinstance(pulls, list):
        raise Unusable(f"the pull requests of {sha} are not a list")
    merged = []
    for pull in pulls:
        if not isinstance(pull, Mapping):
            raise Unusable(f"a pull request of {sha} is not an object")
        if pull.get("merge_commit_sha") == sha and pull.get("merged_at"):
            merged.append(pull)
    if not merged:
        return "is the merge commit of no merged pull request"
    if len(merged) > 1:
        numbers = ", ".join(f"#{p.get('number')}" for p in merged)
        return f"is the merge commit of more than one pull request ({numbers})"
    pull = merged[0]
    number, head = pull.get("number"), (pull.get("head") or {}).get("sha")
    if not isinstance(number, int) or not isinstance(head, str) or not _SHA.fullmatch(head):
        raise Unusable(f"the pull request {sha} came from names no number or no head")
    return number, head


def _field(run: Mapping[str, Any], key: str, kind: type, sha: str) -> Any:
    """``run[key]`` when it is a ``kind`` (never a bool for an int); otherwise the runs are no
    answer."""
    value = run.get(key)
    if not isinstance(value, kind) or isinstance(value, bool):
        raise Unusable(f"a merge.yml queue run of {sha} has no usable {key}: {value!r}")
    return value


def _queue_run(get: merge_verdict.Get, repo: str, sha: str, number: int) -> int | str | None:
    """The newest ``merge_group`` run of merge.yml on ``sha`` when main's merge queue landed it as
    #``number``'s entry, why such runs do not stand for that entry, or None when none ran on it.

    A run counts only as all of: the ``merge_group`` event, merge.yml, ``sha`` itself, and this
    repository as both the run's and its head's. A page this cannot read whole is no answer."""
    query = urllib.parse.urlencode({"head_sha": sha, "event": QUEUE_EVENT, "per_page": _PER_PAGE})
    try:
        page = get(f"/repos/{repo}/actions/workflows/merge.yml/runs?{query}")
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise Unusable(f"the merge.yml queue runs of {sha} could not be read: {exc}") from exc
    runs = page.get("workflow_runs") if isinstance(page, Mapping) else None
    total = page.get("total_count") if isinstance(page, Mapping) else None
    if not isinstance(runs, list) or not isinstance(total, int) or isinstance(total, bool):
        raise Unusable(f"the merge.yml queue runs of {sha} are not a page of runs")
    if total > len(runs):
        raise Unusable(f"{total} merge.yml queue runs of {sha} do not fit one page")
    ours = []
    for run in runs:
        if not isinstance(run, Mapping):
            raise Unusable(f"the merge.yml runs of {sha} hold an entry that is not a run")
        if run.get("path") != QUEUE_WORKFLOW:
            raise Unusable(f"the merge.yml runs of {sha} hold a run of {run.get('path')!r}")
        if run.get("event") == QUEUE_EVENT and run.get("head_sha") == sha:
            ours.append(run)
    if not ours:
        return None
    for run in ours:
        homes = {
            _field(run, k, Mapping, sha).get("full_name") for k in ("repository", "head_repository")
        }
        if homes != {repo}:
            return f"is a commit with a merge_group run of another repository ({run.get('id')})"
        branch = QUEUE_BRANCH.fullmatch(str(run.get("head_branch") or ""))
        if branch is None or int(branch["number"]) != number:
            return (
                f"is a queue commit whose merge.yml merge_group run {run.get('id')} is on"
                f" {run.get('head_branch')!r}, which names no queue entry of #{number}"
            )
    newest = max(ours, key=lambda r: (_field(r, "run_number", int, sha), _field(r, "id", int, sha)))
    ident = newest["id"]
    status, conclusion = newest.get("status"), newest.get("conclusion")
    if status != "completed" or conclusion != "success":
        return (
            f"is a queue commit whose newest merge.yml merge_group run {ident} concluded"
            f" {conclusion or status}"
        )
    return ident


def check(get: merge_verdict.Get, *, repo: str, commit: str, git_dir: Path) -> Landed | str:
    """The commit's record, or what is wrong with it. ``main`` for the controller revision is
    the commit itself: the revision must be on main as it stands once this commit lands."""
    tree = _git(git_dir, "rev-parse", f"{commit}^{{tree}}")
    found = _pull_request(get, repo, commit)
    if isinstance(found, str):
        return found
    number, head = found
    queued = _queue_run(get, repo, commit, number)
    if isinstance(queued, str):
        return queued
    verified = commit if queued is not None else head
    on = "itself" if queued is not None else f"its head {head}"
    result = merge_verdict.verify(get, repo=repo, sha=verified, git_dir=git_dir, main=commit)
    if isinstance(result, merge_verdict.Unreadable):
        raise Unusable(f"#{number}'s verdict on {verified} could not be read: {result.reason}")
    if isinstance(result, merge_verdict.Refused):
        as_what = " as a queue commit" if queued is not None else ""
        return f"lands #{number}{as_what}, whose verdict on {on} does not count: {result.reason}"
    receipt = result.receipt
    if receipt.tree_oid != tree:
        return (
            f"lands tree {tree}, not {receipt.tree_oid}, the tree #{number}'s verdict verified on"
            f" {on}"
        )
    return Landed(
        pull_request=number,
        head_sha=head,
        verified_sha=verified,
        queue_run=queued,
        tree_oid=tree,
        main_sha=commit,
        check_run_id=result.check_run_id,
        controller_revision=receipt.controller_revision,
    )


def main(argv: list[str] | None = None, *, get: merge_verdict.Get | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci verify-landed", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--repo", required=True, help="OWNER/NAME")
    parser.add_argument("--range", required=True, help="BEFORE..AFTER, the push's own range")
    parser.add_argument("--git-dir", default=".", type=Path, help="a full clone holding both")
    parser.add_argument("--record", type=Path, help="where to write the records, as JSON")
    args = parser.parse_args(argv)
    if args.record is not None:
        args.record.unlink(missing_ok=True)
    if get is None:
        env = os.environ
        get = merge_verdict.github_get(
            env.get("GITHUB_API_URL", "https://api.github.com"), env.get("GH_TOKEN", "")
        )
    records: list[dict[str, Any]] = []
    lines: list[str] = []
    try:
        for commit in commits_in(args.git_dir, args.range):
            found = check(get, repo=args.repo, commit=commit, git_dir=args.git_dir)
            if isinstance(found, Landed):
                records.append(asdict(found))
            else:
                lines.append(f"{commit} rule 4 {found}")
    except Unusable as exc:
        print(f"could not check: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
        # The same line as an annotation under Actions, so a red run's summary names the commit,
        # as the provenance check does. Every field in it is a commit id or this file's own words.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error::{line}", file=sys.stderr)
    if args.record is not None:
        args.record.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    verdict = f"{len(lines)} violation(s)" if lines else "no violation"
    checked = len(records) + len(lines)
    print(f"verified tree: {checked} commit(s), rule 4: {verdict}", file=sys.stderr)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
