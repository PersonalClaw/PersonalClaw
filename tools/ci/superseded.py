"""Was a cancelled suite shard on ``main`` replaced by a newer push's waiting shard?

On a push to ``main``, at the run's first attempt, merge.yml's suite shards (``python-suite``
and ``python-suite-macos``) share one concurrency group per shard across commits,
``merge-suite-<key>``, with ``cancel-in-progress: false``. So during a burst of merges, a newer
commit's waiting shard replaces an older commit's shard that has not started yet, and GitHub
reports the replaced job as cancelled. That commit is not verified on its own (#3915).

The ``results`` job's verdict stays red either way. This check only decides which red it names:
``superseded by <newer commit>``, or ``cancelled``. It never turns anything green.

A cancelled shard is **superseded** only when every one of these holds, read from the API:

1. the run is a push to ``main`` (the full ref, so a tag named ``main`` is not it);
2. the job is a shard of the job named, by its matrix key (``<job> (<n>/<N>)``), and was
   cancelled before any step ran;
3. its check run carries exactly GitHub's replacement annotation, ``Canceling since a higher
   priority waiting request for <group> exists``, naming that shard's own group
   (``<group prefix><key>``). GitHub has written the group both in single quotes and bare (bare
   on main's run 40113017916), so either is read, and nothing else around it. merge.yml's groups
   hold a space and parentheses (``merge-suite-python-suite (2/3)``), so a bare group may too;
4. a later push run of the same workflow on ``main``, for a commit that descends from this one,
   has a job of the same name that was not skipped. The earliest such run is the one named.

The group replaces by order of joining, not by commit: an older push's slower run can queue its
shard after the newest commit's and evict it. That shard has no later descendant, so it reads
unproven, which fails closed.

Anything else is **unproven**, with its reason: a cancellation by hand, a run that was
cancelled as a whole, an annotation for another group or another wording, no later descendant,
an answer this check cannot read. ``--out`` writes each superseded shard's key and the newer
commit as JSON, which the verdict reads (``verdict --superseded``); an unproven shard is not in
it, so the verdict reads it cancelled. ``superseded=<sha>`` names the one commit when every
cancelled shard is superseded by it. Off a push to ``main`` the word never exists, and the API
is not read.

It reads only the first page of each listing (100 jobs, 100 runs): a burst past that is unproven,
never superseded. Standard library only, with no relative imports, so the workflow runs main's
copy as ``python3 -I superseded.py`` with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

MAIN = "refs/heads/main"
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
#: The annotation GitHub writes on a job its concurrency group replaced, matched whole: the group
#: in single quotes, or bare.
_REPLACED = re.compile(
    r"Canceling since a higher priority waiting request for "
    r"(?:'(?P<quoted>[^']+)'|(?P<bare>[^'\n]+)) exists"
)
_PER_PAGE = 100

Get = Callable[[str], Any]
"""One API read: a path under the API root (``/repos/...``) to its decoded JSON."""


class Unanswered(Exception):
    """The API gave no answer this check can read."""


@dataclass(frozen=True)
class Answer:
    #: Each superseded shard's name, and the newer commit whose run replaced it.
    superseded: Mapping[str, str] = field(default_factory=dict)
    #: Each cancelled shard that is not proven superseded, and why.
    unproven: Mapping[str, str] = field(default_factory=dict)
    #: What was read, for the summary: why the API was not read, or what was judged.
    note: str = ""


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Unanswered(f"{what} is not an object")
    return value


def _list(value: object, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise Unanswered(f"{what} is not a list")
    return value


def _jobs(get: Get, path: str, what: str) -> list[Mapping[str, Any]]:
    listing = _mapping(get(f"{path}?per_page={_PER_PAGE}"), what)
    return [_mapping(j, f"a job of {what}") for j in _list(listing.get("jobs"), f"{what}'s jobs")]


def _descends(get: Get, repo: str, older: str, newer: str) -> bool:
    compare = _mapping(get(f"/repos/{repo}/compare/{older}...{newer}"), "a comparison")
    return compare.get("status") == "ahead" and compare.get("behind_by") == 0


def _replacer(
    get: Get, *, repo: str, sha: str, run_id: int, workflow: str, name: str
) -> tuple[int, str] | None:
    """The earliest later push run on main, for a descendant of ``sha``, with a job ``name``."""
    listing = _mapping(
        get(
            f"/repos/{repo}/actions/workflows/{workflow}/runs"
            f"?branch=main&event=push&per_page={_PER_PAGE}"
        ),
        f"{workflow}'s runs",
    )
    runs = [_mapping(r, "a run") for r in _list(listing.get("workflow_runs"), "the runs")]
    later = []
    for run in runs:
        rid, head = run.get("id"), run.get("head_sha")
        if isinstance(rid, bool) or not isinstance(rid, int) or rid <= run_id:
            continue
        if run.get("event") != "push" or run.get("head_branch") != "main":
            continue
        if not isinstance(head, str) or not _SHA.fullmatch(head) or head == sha:
            continue
        later.append((rid, head))
    for rid, head in sorted(later):
        if not _descends(get, repo, sha, head):
            continue
        # The newer run's job of that name must be one that runs: queued, in progress, or
        # completed with anything but skipped. A skipped one never joined the group.
        for j in _jobs(get, f"/repos/{repo}/actions/runs/{rid}/jobs", "a run"):
            if j.get("name") != name:
                continue
            if j.get("status") == "completed" and j.get("conclusion") == "skipped":
                continue
            return rid, head
    return None


def _judge(
    get: Get,
    job: Mapping[str, Any],
    *,
    repo: str,
    sha: str,
    run_id: int,
    workflow: str,
    shard_job: str,
    group_prefix: str,
) -> tuple[str | None, str]:
    """``(newer commit, why)`` for one cancelled job: the commit is None when it is unproven."""
    name = str(job.get("name", ""))
    if not re.fullmatch(re.escape(shard_job) + r" \(\d+/\d+\)", name):
        return None, f"{name!r} is not a shard of {shard_job}"
    if job.get("steps"):
        return None, "it started before it was cancelled, so no waiting job replaced it"
    job_id = job.get("id")
    if isinstance(job_id, bool) or not isinstance(job_id, int):
        raise Unanswered(f"{name} has no integer id")
    notes = _list(get(f"/repos/{repo}/check-runs/{job_id}/annotations"), f"{name}'s annotations")
    groups = []
    for note in notes:
        found = _REPLACED.fullmatch(str(_mapping(note, "an annotation").get("message", "")).strip())
        if found:
            groups.append(found.group("quoted") or found.group("bare"))
    if not groups:
        return None, "no replacement annotation: it was cancelled some other way"
    own = group_prefix + name
    if own not in groups:
        return None, f"its replacement annotation names the group {groups[0]!r}, not {own!r}"
    found_run = _replacer(get, repo=repo, sha=sha, run_id=run_id, workflow=workflow, name=name)
    if found_run is None:
        return None, (
            f"no later push run of {workflow} on main, for a commit that descends from this "
            f"one, has a job named {name!r}"
        )
    return found_run[1], f"run {found_run[0]} for {found_run[1]} replaced it"


def decide(
    get: Get,
    *,
    event: str,
    ref: str,
    repo: str,
    sha: str,
    run_id: int,
    attempt: int,
    workflow: str,
    job: str,
    group_prefix: str,
) -> Answer:
    """The answer for one run. Never raises: an error is unproven, with its reason."""
    if event != "push" or ref != MAIN:
        return Answer(note=f"a {event or 'unnamed'} run on {ref or 'no ref'} is not a push to main")
    for bad, why in (
        (not isinstance(sha, str) or not _SHA.fullmatch(sha), f"the pushed commit {sha!r}"),
        (isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0, "the run id"),
        (
            isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0,
            "the run attempt",
        ),
        (not isinstance(group_prefix, str) or not group_prefix, "the group prefix"),
        (not isinstance(job, str) or not job, "the job"),
        (not isinstance(workflow, str) or not workflow, "the workflow"),
    ):
        if bad:
            return Answer(note=f"{why} cannot be read, so nothing is superseded")
    try:
        jobs = _jobs(
            get, f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs", "this run"
        )
    except (Unanswered, OSError, ValueError) as exc:
        return Answer(unproven={job: f"could not find out: {exc}"}, note="this run's jobs")
    superseded: dict[str, str] = {}
    unproven: dict[str, str] = {}
    for each in jobs:
        name = str(each.get("name", ""))
        if each.get("conclusion") != "cancelled" or not name.startswith(f"{job} ("):
            continue
        try:
            newer, why = _judge(
                get,
                each,
                repo=repo,
                sha=sha,
                run_id=run_id,
                workflow=workflow,
                shard_job=job,
                group_prefix=group_prefix,
            )
        except (Unanswered, OSError, ValueError) as exc:
            newer, why = None, f"could not find out: {exc}"
        if newer is None:
            unproven[name] = why
        else:
            superseded[name] = newer
    return Answer(
        superseded, unproven, note=f"{len(superseded) + len(unproven)} cancelled shard(s)"
    )


def output_line(answer: Answer) -> str:
    """``superseded=<sha>`` when every cancelled shard is superseded by that one commit, else
    ``superseded=``: shards replaced by different commits name none, as
    ``verdict.decide_main_push`` names none for the same proof."""
    newer = set(answer.superseded.values())
    if len(newer) != 1 or answer.unproven:
        return "superseded="
    return f"superseded={newer.pop()}"


def summary(answer: Answer, job: str) -> str:
    """The job summary: what each cancelled shard was read as. No gate reads it."""
    lines = [f"### {job}: cancelled shards", "", answer.note + "."]
    for name, newer in answer.superseded.items():
        lines.append(f"- `{name}`: superseded by `{newer}`")
    for name, why in answer.unproven.items():
        lines.append(f"- `{name or job}`: unproven ({why})")
    return "\n".join(lines) + "\n"


def github_get(api_url: str, token: str) -> Get:
    """Reads from the GitHub API with the job's token."""

    def get(path: str) -> Any:
        request = urllib.request.Request(
            api_url.rstrip("/") + path,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {token}",
                "User-Agent": "superseded-check",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - the API root
            return json.loads(response.read().decode("utf-8"))

    return get


def _int(text: str) -> int:
    return int(text) if text.isdigit() else -1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", required=True, help="the workflow file whose runs count")
    parser.add_argument("--job", required=True, help="the matrix job whose shards are judged")
    parser.add_argument("--group-prefix", required=True, help="the shards' group, less its key")
    parser.add_argument(
        "--out", help="where to add each superseded shard's key and newer commit, as JSON"
    )
    args = parser.parse_args(argv)
    env = os.environ
    answer = decide(
        github_get(env.get("GITHUB_API_URL", "https://api.github.com"), env.get("GH_TOKEN", "")),
        event=env.get("GITHUB_EVENT_NAME", ""),
        ref=env.get("GITHUB_REF", ""),
        repo=env.get("GITHUB_REPOSITORY", ""),
        sha=env.get("GITHUB_SHA", ""),
        run_id=_int(env.get("GITHUB_RUN_ID", "")),
        attempt=_int(env.get("GITHUB_RUN_ATTEMPT", "")),
        workflow=args.workflow,
        job=args.job,
        group_prefix=args.group_prefix,
    )
    line = output_line(answer)
    text = summary(answer, args.job)
    print(text)
    print(line)
    for name, content in (("GITHUB_OUTPUT", line + "\n"), ("GITHUB_STEP_SUMMARY", text)):
        target = env.get(name)
        if target:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(content)
    if args.out:
        write_out(answer, args.out)
    return 0


def write_out(answer: Answer, path: str) -> None:
    """Add the answer's superseded shards to the JSON object at *path*, made when it is absent:
    one file gathers each suite job's answer, and a key already there is never replaced."""
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
    except FileNotFoundError:
        found = {}
    if not isinstance(found, dict):
        raise SystemExit(f"{path} is not a JSON object")
    for key, newer in answer.superseded.items():
        found.setdefault(key, newer)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(found, handle, indent=1, sort_keys=True)


if __name__ == "__main__":
    sys.exit(main())
