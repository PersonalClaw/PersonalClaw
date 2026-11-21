"""``python -m tools.ci pin-refresh``: the weekly pin-refresh pull request (#4585).

Two checks only report: ``image-pins`` (an image a workflow pins whose tag has moved) and
``action_pins.py`` (an action SHA its release comment no longer names). This makes the reviewed
edit they ask for, and opens it as one pull request that merges like any other.

``plan`` reads the tree and resolves, read-only:

- each image a workflow pins by digest (``image_pins.check``) whose tag now names another index:
  its new index and platform manifest, every copy of the old ones in the tree;
- each action pinned by commit SHA (``action_pins.pins``): the newest release on its major line
  that was published at least :data:`COOLDOWN_DAYS` ago, Dependabot's own cooldown, or, when no
  newer one is, the commit its own tag names now if that tag has moved. Every copy of the old SHA
  in the tree moves, and its ``# vX.Y.Z`` comment with it.

With ``--write`` it edits the tracked files in place, writes the pull request's body (each update's
old and new identity, and its release notes), and writes ``changed`` and ``workflows`` (whether a
workflow file changed, so the App's token asks for workflows write only then) to ``--outputs``.
A pin it cannot resolve is listed in the body and makes the run exit 1, after the edits it could
make; nothing unresolved is ever written as current.

``publish`` commits those edits signed off by the App's bot account, pushes them to a branch of
their own (``pin-refresh/<run id>``), opens the pull request with the App's token (``GH_TOKEN``),
and closes the App's older open refresh pull request as superseded. One already open with the same
updates is left as it is, and nothing is pushed. It never force-pushes, never touches a ruleset,
and pushes no other branch. See docs/maintainers/ci.md, "How a refresh arrives".
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import action_pins, image_pins

#: Dependabot's cooldown for actions (.github/dependabot.yml): a release this new waits a week.
COOLDOWN_DAYS = 7
BRANCH_PREFIX = "pin-refresh/"
TITLE = "Refresh pinned actions and images"
GH_TIMEOUT_SECONDS = 60
_KEY = re.compile(r"<!-- pin-refresh: ([0-9a-f]{64}) -->")
_VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)")

#: ``run(args, env)`` runs a command and returns its standard output.
Run = Callable[[Sequence[str], Mapping[str, str] | None], str]


@dataclass(frozen=True)
class ActionUpdate:
    repository: str
    old_sha: str
    old_tag: str
    new_sha: str
    new_tag: str
    notes: str

    def line(self) -> str:
        why = "a newer release" if self.new_tag != self.old_tag else "its tag has moved"
        return (
            f"`{self.repository}` {self.old_tag} → {self.new_tag} ({why}): "
            f"`{self.old_sha}` → `{self.new_sha}`. Release notes: {self.notes}"
        )


@dataclass(frozen=True)
class ImageUpdate:
    image: str
    tag: str
    platform: str
    old_index: str
    old_digest: str
    new_index: str
    new_digest: str

    @property
    def notes(self) -> str:
        host, repository, _ = image_pins.registry(self.image)
        if repository.startswith("library/"):
            return f"https://hub.docker.com/_/{repository[8:]}/tags?name={self.tag}"
        if host == "ghcr.io":
            return f"https://github.com/{repository.split('/')[0]}/packages"
        return f"https://hub.docker.com/r/{repository}/tags?name={self.tag}"

    def line(self) -> str:
        return (
            f"`{self.image}:{self.tag}` ({self.platform}): index `{self.old_index}` → "
            f"`{self.new_index}`, manifest `{self.old_digest}` → `{self.new_digest}`. "
            f"Release notes: {self.notes}"
        )


def _version(tag: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(tag)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def newest_release(
    fetch: action_pins.Fetch, repository: str, tag: str, now: dt.datetime
) -> tuple[str, str] | None:
    """The newest release of *repository* on *tag*'s major line that is newer than *tag* and was
    published at least :data:`COOLDOWN_DAYS` before *now*, as (tag, release notes URL); None
    when there is none, or the API gives no list of releases."""
    pinned = _version(tag)
    status, body = fetch(f"/repos/{repository}/releases?per_page=100")
    if pinned is None or status != 200 or not isinstance(body, list):
        return None
    cutoff = now - dt.timedelta(days=COOLDOWN_DAYS)
    best: tuple[tuple[int, int, int], str, str] | None = None
    for release in body:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        name = str(release.get("tag_name") or "")
        version = _version(name)
        published = str(release.get("published_at") or "")
        try:
            when = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if version is None or version[0] != pinned[0] or version <= pinned or when > cutoff:
            continue
        if best is None or version > best[0]:
            best = (version, name, str(release.get("html_url") or ""))
    return (best[1], best[2]) if best else None


def plan_actions(
    files: Iterable[tuple[str, str]], fetch: action_pins.Fetch, now: dt.datetime
) -> tuple[list[ActionUpdate], list[str]]:
    """The action updates *files* call for, and every pin that could not be resolved."""
    found, problems = action_pins.pins(files)
    updates: dict[tuple[str, str], ActionUpdate] = {}
    for pin in found:
        if (pin.repository, pin.sha) in updates:
            continue
        current, why = action_pins.resolve(fetch, pin.repository, pin.tag)
        if current is None:
            problems.append(f"{pin.where}: {pin.repository} {pin.tag}: {why}")
            continue
        newer = newest_release(fetch, pin.repository, pin.tag, now)
        notes = f"https://github.com/{pin.repository}/releases/tag/{pin.tag}"
        new_tag, new_sha = pin.tag, current
        if newer:
            commit, why = action_pins.resolve(fetch, pin.repository, newer[0])
            if commit is None:
                problems.append(f"{pin.where}: {pin.repository} {newer[0]}: {why}")
                continue
            new_tag, new_sha, notes = newer[0], commit, newer[1] or notes
        if new_sha != pin.sha:
            updates[(pin.repository, pin.sha)] = ActionUpdate(
                pin.repository, pin.sha, pin.tag, new_sha, new_tag, notes
            )
    return list(updates.values()), problems


def plan_images(
    files: Iterable[tuple[str, str]], get: image_pins.Get
) -> tuple[list[ImageUpdate], list[str]]:
    """The image updates *files* call for, and every pin that could not be resolved."""
    moved, problems = image_pins.check(files, get)
    updates: dict[tuple[str, str], ImageUpdate] = {}
    for m in moved:
        key = (m.pin.index, m.pin.digest)
        updates.setdefault(
            key,
            ImageUpdate(
                m.pin.image,
                m.pin.tag,
                m.pin.platform,
                m.pin.index,
                m.pin.digest,
                m.index,
                m.digest,
            ),
        )
    return list(updates.values()), problems


def edit(text: str, actions: Sequence[ActionUpdate], images: Sequence[ImageUpdate]) -> str:
    """*text* with every copy of each old pin moved: an action's SHA, and the release comment
    beside it; an image's index and its platform manifest."""
    for a in actions:
        text = re.sub(
            rf"{a.old_sha}(\s*#\s*){re.escape(a.old_tag)}\b", rf"{a.new_sha}\g<1>{a.new_tag}", text
        )
        text = text.replace(a.old_sha, a.new_sha)
    for i in images:
        text = text.replace(i.old_index, i.new_index).replace(i.old_digest, i.new_digest)
    return text


def apply(
    root: Path,
    tracked: Iterable[str],
    actions: Sequence[ActionUpdate],
    images: Sequence[ImageUpdate],
) -> list[str]:
    """Edit every tracked file under *root* that holds an old pin; the paths changed, in order."""
    olds = [a.old_sha for a in actions] + [x for i in images for x in (i.old_index, i.old_digest)]
    changed: list[str] = []
    for rel in tracked:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(old in text for old in olds):
            continue
        new = edit(text, actions, images)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(rel)
    return changed


def key(actions: Sequence[ActionUpdate], images: Sequence[ImageUpdate]) -> str:
    """What the updates are, so an open refresh pull request with the same ones is left alone."""
    lines = sorted([a.line() for a in actions] + [i.line() for i in images])
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def body(
    actions: Sequence[ActionUpdate],
    images: Sequence[ImageUpdate],
    problems: Sequence[str],
    changed: Sequence[str],
    run_url: str,
) -> str:
    lines = [
        "The weekly pin refresh (#4585) moves these pins to what their tags and releases name "
        "now. It merges like any other pull request: up to date, the normal verdict green.",
        "",
    ]
    if actions:
        lines += ["### Actions", "", *[f"- {a.line()}" for a in actions], ""]
    if images:
        lines += ["### Images", "", *[f"- {i.line()}" for i in images], ""]
    if changed:
        lines += ["### Files", "", *[f"- `{c}`" for c in changed], ""]
    if problems:
        lines += ["### Not resolved, so not moved", "", *[f"- {p}" for p in problems], ""]
    lines += [f"From {run_url}.", "", f"<!-- pin-refresh: {key(actions, images)} -->"]
    return "\n".join(lines) + "\n"


def run_command(args: Sequence[str], env: Mapping[str, str] | None = None) -> str:
    done = subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=GH_TIMEOUT_SECONDS,
        env={**os.environ, **env} if env else None,
    )
    return done.stdout


def tracked_files(root: Path, run: Run = run_command) -> list[str]:
    return [p for p in run(["git", "-C", str(root), "ls-files", "-z"], None).split("\0") if p]


def plan_main(args: argparse.Namespace) -> int:
    root: Path = args.root
    files = [
        (str(p.relative_to(root)), p.read_text(encoding="utf-8"))
        for p in action_pins.workflow_and_action_files(root)
    ]
    now = dt.datetime.now(dt.timezone.utc)
    fetch = action_pins.github_fetch(os.environ.get("GH_TOKEN") or None)
    actions, action_problems = plan_actions(files, fetch, now)
    images, image_problems = plan_images(files, image_pins.http_get)
    problems = action_problems + image_problems
    changed = apply(root, tracked_files(root), actions, images) if args.write else []
    text = body(actions, images, problems, changed, args.run_url)
    print(text, end="")
    if args.body:
        Path(args.body).write_text(text, encoding="utf-8")
    if args.outputs:
        with open(args.outputs, "a", encoding="utf-8") as out:
            out.write(f"changed={'true' if changed else 'false'}\n")
            workflows = any(c.startswith(".github/workflows/") for c in changed)
            out.write(f"workflows={'true' if workflows else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("## Pin refresh\n\n" + text)
    return 1 if problems else 0


def _open_refreshes(repo: str, slug: str, run: Run) -> list[dict[str, object]]:
    """The App's own open refresh pull requests: on a ``pin-refresh/`` branch, by its bot."""
    answer = json.loads(
        run(
            [
                "gh",
                "pr",
                "list",
                "-R",
                repo,
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "number,headRefName,author,body",
            ],
            None,
        )
    )
    if not isinstance(answer, list):
        raise ValueError("the open pull requests are not a list")
    ours = []
    for pr in answer:
        author = pr.get("author") if isinstance(pr, dict) else None
        login = str(author.get("login", "")) if isinstance(author, dict) else ""
        if str(pr.get("headRefName", "")).startswith(BRANCH_PREFIX) and login in (
            f"app/{slug}",
            f"{slug}[bot]",
        ):
            ours.append(pr)
    return ours


def publish(
    repo: str, slug: str, text: str, run_id: str, token: str, run: Run = run_command
) -> int | None:
    """Commit the edits, push them to their own branch and open the pull request; close the App's
    older open refresh pull requests as superseded. None when one with the same updates is open."""
    found = _KEY.search(text)
    if not found:
        raise ValueError("the body names no updates key")
    older = _open_refreshes(repo, slug, run)
    if any(_KEY.search(str(pr.get("body", ""))) and found[1] in str(pr["body"]) for pr in older):
        return None
    bot = f"{slug}[bot]"
    user_id = run(["gh", "api", f"users/{bot}", "--jq", ".id"], None).strip()
    email = f"{user_id}+{bot}@users.noreply.github.com"
    branch = f"{BRANCH_PREFIX}{run_id}"
    run(["git", "switch", "-c", branch], None)
    run(["git", "add", "--update"], None)
    message = f"ci: {TITLE.lower()}\n\n{text.split(chr(10))[0]}\n\nSigned-off-by: {bot} <{email}>\n"
    identity = {
        "GIT_AUTHOR_NAME": bot,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": bot,
        "GIT_COMMITTER_EMAIL": email,
    }
    run(["git", "commit", "-q", "-m", message], identity)
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # The token reaches git through the environment for this one push, never the command line
    # or the checkout's config (the checkout keeps no credentials).
    header = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }
    run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], header)
    url = run(
        [
            "gh",
            "pr",
            "create",
            "-R",
            repo,
            "--head",
            branch,
            "--base",
            "main",
            "--title",
            TITLE,
            "--body",
            text,
        ],
        None,
    ).strip()
    number = int(url.rsplit("/", 1)[-1])
    for pr in older:
        run(
            [
                "gh",
                "pr",
                "close",
                str(pr["number"]),
                "-R",
                repo,
                "--comment",
                f"Superseded by #{number}, this week's refresh.",
            ],
            None,
        )
    return number


def publish_main(args: argparse.Namespace) -> int:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("::error::publish needs the App's token in GH_TOKEN")
        return 1
    text = Path(args.body).read_text(encoding="utf-8")
    number = publish(args.repo, args.app_slug, text, args.run_id, token)
    print(f"refresh pull request: #{number}" if number else "the same refresh is already open")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci pin-refresh", description=(__doc__ or "").split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="resolve the pins, and with --write edit them")
    plan.add_argument("--root", type=Path, default=Path("."))
    plan.add_argument("--write", action="store_true", help="edit the tracked files in place")
    plan.add_argument("--body", help="write the pull request's body here")
    plan.add_argument("--outputs", help="append changed= and workflows= here ($GITHUB_OUTPUT)")
    plan.add_argument("--run-url", default="a local run")
    pub = sub.add_parser("publish", help="commit, push and open the pull request (App token)")
    pub.add_argument("--repo", required=True)
    pub.add_argument("--app-slug", required=True)
    pub.add_argument("--body", required=True)
    pub.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    return plan_main(args) if args.command == "plan" else publish_main(args)


if __name__ == "__main__":
    sys.exit(main())
