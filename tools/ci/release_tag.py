"""``python -m tools.ci release-tag``: the one way a ``v*`` release tag is created (#4584).

``.github/workflows/release-tag.yml`` runs this from main's copy when the maintainer dispatches
it with a tag and a commit. It refuses, naming why, unless:

- the tag is a release tag, by the release's own parser (``scripts/release_tags.py``);
- the commit is a full commit id on ``main`` (``trust.require_on_main``), so no tag can carry a
  ``release.yml`` from a branch;
- the commit's definitions (its workflows, actions, ``tools/ci`` and the entry points they run)
  equal ``main``'s tip's (``trust.compare``), so the tag's release run is the one ``main`` holds;
- no tag of that name exists, here or on the remote.

With ``--create`` it then creates the annotated tag and its ref through the API with
``GH_TOKEN``, the release controller's App token: the one actor the ``release-tag-creation``
ruleset lets create a ``v*`` tag. The tag's push starts ``release.yml``. Nothing else is written,
and a tag is never moved or deleted (``release-tags`` refuses both for everyone).

#3911 adds its release gate (a green ``merge-verdict`` for the commit's tree, and the breadth
evidence) to the checks before ``--create``. See docs/maintainers/ci.md, "The release tags".
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import trust

API = "https://api.github.com"
MAIN = "origin/main"

#: ``post(path, body)`` returns the HTTP status and the decoded JSON answer.
Post = Callable[[str, dict[str, object]], tuple[int, object]]


def _parse_tag(repo: Path, tag: str) -> None:
    """The release's own tag parser, read from the checkout, refuses what it refuses."""
    path = repo / "scripts" / "release_tags.py"
    spec = importlib.util.spec_from_file_location("_release_tags", path)
    if spec is None or spec.loader is None:
        raise trust.Refused(f"cannot read the release's tag parser at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.parse_release_ref(tag)
    except ValueError as error:
        raise trust.Refused(str(error)) from None
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


def check(repo: Path, tag: str, commit: str, *, main: str = MAIN) -> None:
    """Refuse (``trust.Refused``) unless *commit* on *main* may be tagged *tag*."""
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
    if _tag_exists(repo, tag):
        raise trust.Refused(f"{tag} exists already; a release tag is never moved")


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
    )
    sha = answer.get("sha") if isinstance(answer, dict) else None
    if status != 201 or not isinstance(sha, str):
        raise trust.Refused(f"the tag object was not created ({status}): {answer!r}")
    status, answer = post(f"/repos/{repository}/git/refs", {"ref": f"refs/tags/{tag}", "sha": sha})
    if status != 201:
        raise trust.Refused(f"refs/tags/{tag} was not created ({status}): {answer!r}")
    return sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci release-tag", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default=".", type=Path, help="the checkout to read")
    parser.add_argument("--create", metavar="OWNER/NAME", help="create the tag in this repository")
    args = parser.parse_args(argv)
    try:
        check(args.repo, args.tag, args.commit)
        print(f"may tag: {args.commit} on main as {args.tag}")
        if args.create:
            token = os.environ.get("GH_TOKEN")
            if not token:
                raise trust.Refused("no GH_TOKEN: the controller's App token creates the tag")
            sha = create(args.create, args.tag, args.commit, github_post(token))
            print(f"created: {args.tag} -> {args.commit} (tag object {sha})")
    except trust.Refused as refused:
        print(f"::error title=release tag refused::{refused}")
        return 1
    return 0
