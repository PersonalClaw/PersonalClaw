"""``python -m tools.ci release-tag``: the one way a ``v*`` release tag is created (#4584).

``.github/workflows/release-tag.yml`` runs this from main's copy when the maintainer dispatches
it with a tag and a commit. It refuses, naming why, unless:

- the tag is a release tag, by the release's own parser (``scripts/release_tags.py``);
- the commit is a full commit id on ``main`` (``trust.require_on_main``), so no tag can carry a
  ``release.yml`` from a branch;
- the commit's definitions (its workflows, actions, ``tools/ci`` and the entry points they run)
  equal ``main``'s tip's (``trust.compare``), so the tag's release run is the one ``main`` holds;
- no tag of that name exists, here or on the remote;
- the commit's own push run on ``main`` is green (#3911, :func:`main_push_green`): read in each
  workflow a push to ``main`` runs in, the newer shape first (``workflows.MAIN_PUSH``,
  ``merge.yml`` once #3915's flip lands, ``ci.yml`` until then), so the gate outlives the flip.
- no ``release.yml`` run is still going (#3911, :func:`no_release_running`): every release
  shares one concurrency group, where GitHub keeps one waiting run, so a tag made behind a
  running release could have its run replaced by the next and never be released.

With ``--rehearsal`` the tag it checks for, and creates, is ``rehearsal-<tag>``: every check and
the App are the same, and no ``v*`` rule or release trigger sees it, so the whole path can be
proved without a release.

It reads the runs with ``RUNS_TOKEN``, the job's own token with ``actions: read``. With
``--create`` it then creates the annotated tag and its ref through the API with
``GH_TOKEN``, the release controller's App token: the one actor the ``release-tag-creation``
ruleset lets create a ``v*`` tag. The tag's push starts ``release.yml``. Nothing else is written,
and a tag is never moved or deleted (``release-tags`` refuses both for everyone).

#3911's gate grows with the trusted ``merge-verdict`` (#3910) and the breadth evidence (#3913)
once they exist. See docs/maintainers/ci.md, "The release tags".
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import trust, workflows

API = "https://api.github.com"
MAIN = "origin/main"
#: A rehearsal creates ``rehearsal-<tag>`` instead: the same checks and the same App, under a name
#: no ``v*`` rule or ``release.yml`` trigger matches, so the path is proved without a release.
REHEARSAL_PREFIX = "rehearsal-"


def tag_name(tag: str, *, rehearsal: bool = False) -> str:
    """The ref name the tag is created as: *tag* itself, or its rehearsal name."""
    return f"{REHEARSAL_PREFIX}{tag}" if rehearsal else tag


#: ``post(path, body)`` returns the HTTP status and the decoded JSON answer.
Post = Callable[[str, dict[str, object]], tuple[int, object]]
#: ``get(path)`` returns one API read's decoded JSON answer.
Get = Callable[[str], Any]
#: Where a run's workflow file sits, as the API's ``path`` names it.
WORKFLOW_DIR = ".github/workflows/"


def _parse_tag(repo: Path, tag: str) -> None:
    """The release's own tag parser, read from the checkout, refuses what it refuses."""
    path = repo / "scripts" / "release_tags.py"
    spec = importlib.util.spec_from_file_location("_release_tags", path)
    if spec is None or spec.loader is None:
        raise trust.Refused(f"cannot read the release's tag parser at {path}")
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up in sys.modules while the class is built, so the module is
    # registered for as long as it runs, and taken out again after.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        module.parse_release_ref(tag)
    except ValueError as error:
        raise trust.Refused(str(error)) from None
    finally:
        sys.modules.pop(spec.name, None)
    if tag != tag.strip():
        raise trust.Refused(f"{tag!r} has spaces around it")


def _tag_exists(repo: Path, tag: str) -> bool:
    for args in (
        ["rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        ["ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}"],
    ):
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if done.returncode == 0:
            return True
        if args[0] == "ls-remote" and done.returncode != 2:
            raise trust.Refused(f"cannot tell whether {tag} exists on the remote: {done.stderr}")
    return False


def check(repo: Path, tag: str, commit: str, *, main: str = MAIN, rehearsal: bool = False) -> None:
    """Refuse (``trust.Refused``) unless *commit* on *main* may be tagged *tag* (or, with
    *rehearsal*, its rehearsal name: the tag itself is still held to the release's parser)."""
    _parse_tag(repo, tag)
    trust.require_on_main(repo, commit, main=main)
    tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{main}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if tip.returncode != 0:
        raise trust.Refused(f"cannot read {main}: {tip.stderr.strip()}")
    changed = trust.compare(repo, base=tip.stdout.strip(), head=commit).changes
    if changed:
        names = ", ".join(f"{c.path} ({c.kind})" for c in changed)
        raise trust.Refused(
            f"{commit}'s definitions differ from {main}'s: {names}. Tag a commit whose "
            "workflows, actions and tools/ci are main's, or land the change first"
        )
    name = tag_name(tag, rehearsal=rehearsal)
    if _tag_exists(repo, name):
        raise trust.Refused(f"{name} exists already; a release tag is never moved")


def _runs(repository: str, workflow: str, commit: str, get: Get) -> list[dict[str, Any]]:
    """*commit*'s push runs on ``main`` of *repository* in *workflow*, as the API lists them."""
    path = (
        f"/repos/{repository}/actions/workflows/{workflow}/runs"
        f"?event=push&branch=main&head_sha={commit}&per_page=100"
    )
    try:
        answer = get(path)
    except (OSError, ValueError) as error:
        raise trust.Refused(f"cannot read {workflow}'s runs of {commit}: {error}") from None
    listed = answer.get("workflow_runs") if isinstance(answer, dict) else None
    if not isinstance(listed, list):
        raise trust.Refused(f"cannot read {workflow}'s runs of {commit}: {answer!r}")
    # The API's filters are read again here: only this commit's push to this repository's main.
    return [
        run
        for run in listed
        if isinstance(run, dict)
        and run.get("head_sha") == commit
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and (run.get("head_repository") or {}).get("full_name") == repository
        and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW_DIR + workflow
    ]


def main_push_green(repository: str, commit: str, get: Get) -> str:
    """The URL of *commit*'s own push run on ``main``, which must have finished green; else
    ``trust.Refused``, naming the run and what it concluded.

    The first workflow in ``workflows.MAIN_PUSH`` with a push run for the commit holds its run:
    ``merge.yml`` once #3915's flip lands, ``ci.yml`` until then. A red there is never passed
    over for the older shape's green. Of its runs the newest is read. A run still going, failed,
    cancelled or timed out is not green, and neither is one whose gate read a shard as superseded
    by a newer push or unproven: that gate is red, so the run concluded failure. A run re-run to
    green is green: its conclusion is its latest attempt's (the runbook's Tagging says so).
    """
    for workflow in workflows.MAIN_PUSH:
        runs = _runs(repository, workflow, commit, get)
        if not runs:
            continue
        run = max(runs, key=lambda r: (int(r.get("run_number") or 0), int(r.get("id") or 0)))
        url = run.get("html_url") or f"run {run.get('id')}"
        if run.get("status") != "completed":
            raise trust.Refused(
                f"{commit}'s push run on main ({workflow}, {url}) is {run.get('status')}: tag "
                "it once that run is green"
            )
        if run.get("conclusion") != "success":
            raise trust.Refused(
                f"{commit}'s push run on main ({workflow}, {url}) concluded "
                f"{run.get('conclusion')}, not success: tag a commit whose own run is green"
            )
        return str(url)
    raise trust.Refused(
        f"{commit} has no push run on main in {' or '.join(workflows.MAIN_PUSH)}, so nothing "
        "proved it: tag a commit main's push ran on"
    )


def github_get(token: str) -> Get:
    def get(path: str) -> Any:
        request = urllib.request.Request(API + path)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read() or b"null")

    return get


def github_post(token: str) -> Post:
    def post(path: str, body: dict[str, object]) -> tuple[int, object]:
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
        except OSError as error:
            return 0, f"{type(error).__name__}: {error}"

    return post


def create(repository: str, tag: str, commit: str, post: Post) -> str:
    """Create the annotated tag object and its ref; the tag object's id."""
    status, answer = post(
        f"/repos/{repository}/git/tags",
        {"tag": tag, "message": f"PersonalClaw {tag}", "object": commit, "type": "commit"},
        # *tag* is the name to create: the release tag, or its rehearsal name.
    )
    sha = answer.get("sha") if isinstance(answer, dict) else None
    if status != 201 or not isinstance(sha, str):
        raise trust.Refused(f"the tag object was not created ({status}): {answer!r}")
    status, answer = post(f"/repos/{repository}/git/refs", {"ref": f"refs/tags/{tag}", "sha": sha})
    if status != 201:
        raise trust.Refused(f"refs/tags/{tag} was not created ({status}): {answer!r}")
    return sha


RELEASE_WORKFLOW = "release.yml"
#: Every state a run is in before it finishes, as the API's ``status`` filter names them.
UNFINISHED = ("queued", "in_progress", "waiting", "requested", "pending", "action_required")


def no_release_running(repository: str, get: Get) -> None:
    """Refuse (``trust.Refused``) while any ``release.yml`` run of *repository* has not finished,
    naming it.

    Every release shares one concurrency group (``release.yml``'s ``concurrency``), which never
    cancels the run that is publishing but keeps only one run waiting: a tag made while a release
    runs waits behind it, and a third tag's run would replace it, leaving that tag with no
    release. A tag made only once the last release finished starts the one run, so through this
    path none is ever replaced. An answer it cannot read refuses, never reads as no release."""
    for status in UNFINISHED:
        path = (
            f"/repos/{repository}/actions/workflows/{RELEASE_WORKFLOW}/runs"
            f"?status={status}&per_page=100"
        )
        try:
            answer = get(path)
        except (OSError, ValueError) as error:
            raise trust.Refused(
                f"cannot read {RELEASE_WORKFLOW}'s {status} runs: {error}"
            ) from None
        listed = answer.get("workflow_runs") if isinstance(answer, dict) else None
        if not isinstance(listed, list):
            raise trust.Refused(f"cannot read {RELEASE_WORKFLOW}'s {status} runs: {answer!r}")
        for run in listed:
            if (
                isinstance(run, dict)
                and run.get("status") != "completed"
                and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW_DIR + RELEASE_WORKFLOW
            ):
                url = run.get("html_url") or f"run {run.get('id')}"
                raise trust.Refused(
                    f"{RELEASE_WORKFLOW}'s run for {run.get('head_branch')} ({url}) is "
                    f"{run.get('status')}: a tag made now would wait behind it, where the next "
                    "could replace it. Tag once that release has finished"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci release-tag", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default=".", type=Path, help="the checkout to read")
    parser.add_argument(
        "--repository",
        required=True,
        metavar="OWNER/NAME",
        help="the repository whose main push runs are read, and where the tag is created",
    )
    parser.add_argument("--create", action="store_true", help="create the tag")
    parser.add_argument(
        "--rehearsal", action="store_true", help=f"create {REHEARSAL_PREFIX}<tag> instead"
    )
    args = parser.parse_args(argv)
    name = tag_name(args.tag, rehearsal=args.rehearsal)
    try:
        check(args.repo, args.tag, args.commit, rehearsal=args.rehearsal)
        runs_token = os.environ.get("RUNS_TOKEN")
        if not runs_token:
            raise trust.Refused("no RUNS_TOKEN: the job's token reads the commit's push run")
        get = github_get(runs_token)
        run = main_push_green(args.repository, args.commit, get)
        no_release_running(args.repository, get)
        print(
            f"may tag: {args.commit} on main as {name}; its push run on main is green: {run}; "
            "no release is running"
        )
        if args.create:
            token = os.environ.get("GH_TOKEN")
            if not token:
                raise trust.Refused("no GH_TOKEN: the controller's App token creates the tag")
            sha = create(args.repository, name, args.commit, github_post(token))
            print(f"created: {name} -> {args.commit} (tag object {sha})")
    except trust.Refused as refused:
        print(f"::error title=release tag refused::{refused}")
        return 1
    return 0
