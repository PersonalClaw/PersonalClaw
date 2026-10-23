"""Did a green pull-request run already test the tree this push to ``main`` lands?

A pull request's run is its tree's one expensive execution. When the push that merges it lands
the very tree that run tested, the push repeats none of it; when it lands any other tree, the
push runs every job on it, as that tree's one execution. This check answers which, for one
workflow (``--workflow merge.yml``), and writes
``tested=true|false`` to ``$GITHUB_OUTPUT`` with a summary naming why.

**Yes** needs every one of these, read from the API, never from a candidate's artifact:

1. the push is to ``main``, and names the commit it replaced (``github.event.before``);
2. exactly one pull request into ``main`` was merged as the pushed commit (its
   ``merge_commit_sha``);
3. the pushed tree's object id equals that pull request's head tree;
4. the head contains the commit the push replaced. A ``pull_request`` run tests
   ``refs/pull/N/merge``, the head merged into the base as it stood when the run started, and
   that merge's tree equals the head's only when the head already contains that base. The base
   then was ``before`` or an older ``main`` commit, so a head that contains ``before``
   contains it too. Without this, a head whose tree happens to equal the pushed tree, but
   which was tested merged onto an older base, would count as tested;
5. the first attempt of every ``pull_request`` run of the workflow on that head concluded
   ``success``. A later run or attempt never turns a red into a pass: reruns only diagnose;
6. those runs were this pull request's into ``main``: the first was made after it was opened,
   and its base was never changed. A run's object names neither its pull request nor its base,
   and a base change starts no run, so a run made for another pull request on the same head,
   or before a retarget to ``main``, may have tested another base and skipped the jobs that run
   only for ``main``.

**No** is every other case, and any error while finding out: no pull request, a different tree
(a merge that was not up to date), a head behind ``main``, a run that was not green on its first
attempt, a run of another pull request, a changed base, an answer this check cannot read. A no
costs a full run, never a skipped job.

The previous ``main`` commit's copy of this file runs (the workflow fetches
``github.event.before`` and runs that blob), so a pull request that edits the check is judged by
the copy it replaces. It uses only the standard library, so it runs on the runner image's own
Python with nothing of the candidate installed. The summary is a pointer for people: no gate
reads it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: What GitHub sends as ``before`` on a branch's first push.
ZERO = "0" * 40
MAIN = "refs/heads/main"
_SHA = re.compile(r"[0-9a-f]{40}")
_PER_PAGE = 100
#: The issue events that say a pull request's base was changed.
_BASE_CHANGED = frozenset(
    {"base_ref_changed", "automatic_base_change_succeeded", "automatic_base_change_failed"}
)

Get = Callable[[str], Any]
"""One API read: a path under the API root (``/repos/...``) to its decoded JSON."""


class Unanswered(Exception):
    """The API gave no answer this check can read."""


@dataclass(frozen=True)
class Verdict:
    tested: bool
    reason: str
    pull_request: int | None = None
    head: str = ""
    tree: str = ""
    run: int | None = None
    run_url: str = ""


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Unanswered(f"{what} is not an object")
    return value


def _string(value: Mapping[str, Any], key: str, what: str) -> str:
    found = value.get(key)
    if not isinstance(found, str) or not found:
        raise Unanswered(f"{what} has no {key}")
    return found


def _integer(value: Mapping[str, Any], key: str, what: str) -> int:
    found = value.get(key)
    if isinstance(found, bool) or not isinstance(found, int):
        raise Unanswered(f"{what} has no integer {key}")
    return found


def _tree_of(get: Get, repo: str, sha: str) -> str:
    what = f"commit {sha[:12]}"
    commit = _mapping(get(f"/repos/{repo}/commits/{sha}"), what)
    tree = _mapping(_mapping(commit.get("commit"), what).get("tree"), f"{what}'s tree")
    return _string(tree, "sha", f"{what}'s tree")


def decide(
    get: Get, *, event: str, ref: str, repo: str, sha: str, before: str, workflow: str
) -> Verdict:
    """The answer for one push. Never raises: an error is a no, with its reason."""
    if event != "push" or ref != MAIN:
        return Verdict(
            False, f"a {event or 'unnamed'} run on {ref or 'no ref'} is not a push to main"
        )
    if not _SHA.fullmatch(sha):
        return Verdict(False, f"the pushed commit {sha!r} is not a full commit id")
    if before == ZERO or not _SHA.fullmatch(before):
        return Verdict(False, "the push names no previous commit of main to compare with")
    try:
        return _decide(get, repo=repo, sha=sha, before=before, workflow=workflow)
    except (Unanswered, OSError, ValueError) as exc:
        return Verdict(False, f"could not find out: {exc}")


def _decide(get: Get, *, repo: str, sha: str, before: str, workflow: str) -> Verdict:
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
        return Verdict(False, f"{found} into main was merged as this commit", tree=tree)
    pr = merged[0]
    number = _integer(pr, "number", "the pull request")
    head = _string(_mapping(pr.get("head"), f"#{number}'s head"), "sha", f"#{number}'s head")
    if not _SHA.fullmatch(head):
        raise Unanswered(f"#{number}'s head {head!r} is not a full commit id")
    if _tree_of(get, repo, head) != tree:
        return Verdict(
            False,
            f"the pushed tree is not #{number}'s head tree: the merge was not up to date with main",
            number,
            head,
            tree,
        )
    compare = _mapping(get(f"/repos/{repo}/compare/{before}...{head}"), "the comparison")
    behind = _integer(compare, "behind_by", "the comparison")
    if behind != 0:
        return Verdict(
            False,
            f"#{number}'s head is {behind} commit(s) behind the previous main, so its run "
            "tested it merged onto an older base",
            number,
            head,
            tree,
        )
    query = urllib.parse.urlencode(
        {"head_sha": head, "event": "pull_request", "per_page": _PER_PAGE}
    )
    page = _mapping(get(f"/repos/{repo}/actions/workflows/{workflow}/runs?{query}"), "the runs")
    runs = page.get("workflow_runs")
    if not isinstance(runs, list):
        raise Unanswered("the runs are not a list")
    total = page.get("total_count")
    if isinstance(total, int) and total > len(runs):
        raise Unanswered(f"{total} runs do not fit one page, so the first is not known")
    ours = [
        run
        for run in runs
        if isinstance(run, Mapping)
        and run.get("head_sha") == head
        and run.get("event") == "pull_request"
    ]
    if not ours:
        return Verdict(
            False, f"no {workflow} pull_request run of #{number}'s head", number, head, tree
        )
    ours.sort(key=lambda run: (_integer(run, "run_number", "a run"), _integer(run, "id", "a run")))
    first = ours[0]
    # A run's object names neither its pull request nor its base, and changing a base starts
    # no run. A first run made before this pull request was opened was another one's, perhaps
    # into another branch; a pull request whose base changed may have been run on the old one.
    opened = _string(pr, "created_at", f"#{number}")
    if _string(first, "created_at", "the first run") < opened:
        return Verdict(
            False,
            f"the first {workflow} run on #{number}'s head was made before #{number} was "
            "opened, for another pull request, which may have had another base",
            number,
            head,
            tree,
        )
    events = get(f"/repos/{repo}/issues/{number}/events?per_page={_PER_PAGE}")
    if not isinstance(events, list):
        raise Unanswered(f"#{number}'s events are not a list")
    if len(events) >= _PER_PAGE:
        raise Unanswered(f"#{number}'s events do not fit one page, so a base change may be unseen")
    if any(isinstance(e, Mapping) and e.get("event") in _BASE_CHANGED for e in events):
        return Verdict(
            False,
            f"#{number}'s base was changed, so a run on its head may have tested another base",
            number,
            head,
            tree,
        )
    # Every pull_request run on the head is green on its first attempt, not only the first:
    # another pull request on the same head may have run too, and its red stays red.
    run_id, url = _integer(first, "id", "the first run"), ""
    ids: list[str] = []
    for run in ours:
        this = _integer(run, "id", "a run")
        ids.append(str(this))
        attempt = _mapping(get(f"/repos/{repo}/actions/runs/{this}/attempts/1"), "a first attempt")
        status, conclusion = attempt.get("status"), attempt.get("conclusion")
        link = str(attempt.get("html_url") or run.get("html_url") or "")
        if this == run_id:
            url = link
        if status != "completed" or conclusion != "success":
            return Verdict(
                False,
                f"the first attempt of {workflow} run {this} on #{number}'s head "
                f"concluded {conclusion or status}: a later attempt never turns it green",
                number,
                head,
                tree,
                this,
                link,
            )
    return Verdict(
        True,
        f"#{number}'s head has this tree and contains the previous main; #{number} was opened "
        f"before the head's first {workflow} pull_request run and its base never changed; and "
        f"every pull_request run on the head ({', '.join(ids)}) passed on its first attempt",
        number,
        head,
        tree,
        run_id,
        url,
    )


def summary(verdict: Verdict, workflow: str) -> str:
    """The job summary: a pointer for people, which no gate reads."""
    named = []
    if verdict.pull_request is not None:
        named.append(f"- pull request: #{verdict.pull_request}")
    if verdict.head:
        named.append(f"- its head: `{verdict.head}`")
    if verdict.tree:
        named.append(f"- the tree: `{verdict.tree}`")
    if verdict.run is not None:
        named.append(
            f"- the run: [{verdict.run}]({verdict.run_url})"
            if verdict.run_url
            else f"- the run: {verdict.run}"
        )
    if verdict.tested:
        head = f"### {workflow}: a green pull-request run tested this tree"
        tail = "The jobs that run did on this tree are skipped; the always-run jobs run."
    else:
        head = f"### {workflow}: no green pull-request run tested this tree"
        tail = "Every job runs on the pushed tree."
    return "\n".join([head, "", verdict.reason + ".", "", *named, "", tail, ""])


def github_get(api_url: str, token: str) -> Get:
    """Reads from the GitHub API with the job's token."""

    def get(path: str) -> Any:
        request = urllib.request.Request(
            api_url.rstrip("/") + path,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {token}",
                "User-Agent": "tree-check",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - the API root
            return json.loads(response.read().decode("utf-8"))

    return get


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", required=True, help="the workflow file whose runs count")
    args = parser.parse_args(argv)
    env = os.environ
    verdict = decide(
        github_get(env.get("GITHUB_API_URL", "https://api.github.com"), env.get("GH_TOKEN", "")),
        event=env.get("GITHUB_EVENT_NAME", ""),
        ref=env.get("GITHUB_REF", ""),
        repo=env.get("GITHUB_REPOSITORY", ""),
        sha=env.get("GITHUB_SHA", ""),
        before=env.get("BEFORE", ""),
        workflow=args.workflow,
    )
    line = f"tested={'true' if verdict.tested else 'false'}"
    text = summary(verdict, args.workflow)
    print(text)
    print(line)
    for name, content in (("GITHUB_OUTPUT", line + "\n"), ("GITHUB_STEP_SUMMARY", text)):
        target = env.get(name)
        if target:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
