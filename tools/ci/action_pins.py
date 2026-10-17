"""Every action a workflow pins by commit SHA is still the commit its release comment names.

A ``uses: owner/repo@<sha> # vX.Y.Z`` line says two things, and only the SHA is what runs. If a
release tag moves, or the comment is edited without the SHA, the comment stops describing the
code. This reads each distinct (repository, SHA, tag) once from the GitHub API, follows an
annotated tag through to its commit, and fails on:

- a SHA that is not the tag's commit;
- a tag that is gone;
- a pinned reference whose comment names no release;
- an answer the API could not give, reported as unresolved and never as a pass.

Standard library only, and read-only: it sends GETs, with ``GH_TOKEN`` if one is set, for the
rate limit. Run weekly by .github/workflows/action-pins.yml; see docs/maintainers/ci.md,
"Supply chain".
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

API = "https://api.github.com"
#: An annotated tag can point at another tag; more hops than this is a loop, not a release.
MAX_TAG_HOPS = 5

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*([^\s@]+)@(\S+)(.*)$")
_SHA = re.compile(r"[0-9a-f]{40}")
_TAG = re.compile(r"\s+#\s*(v\d+\.\d+\.\d+)\s*$")

#: ``fetch(path)`` returns the HTTP status and the decoded JSON body (None when there is none).
Fetch = Callable[[str], tuple[int, object]]


@dataclass(frozen=True)
class Pin:
    repository: str
    sha: str
    tag: str
    where: str


def pins(files: Iterable[tuple[str, str]]) -> tuple[list[Pin], list[str]]:
    """The SHA pins in *files* (name, text), and a problem for each one with no release tag."""
    found: list[Pin] = []
    problems: list[str] = []
    for name, text in files:
        for number, line in enumerate(text.splitlines(), 1):
            match = _USES.match(line)
            if not match or match.group(1).startswith(("./", "docker://")):
                continue
            action, ref, rest = match.groups()
            if not _SHA.fullmatch(ref):
                continue  # the CI contract refuses a pin that is not a SHA
            where = f"{name}:{number}"
            tag = _TAG.match(rest)
            if not tag:
                problems.append(f"{where}: {action}@{ref} names no `# vX.Y.Z` release")
                continue
            repository = "/".join(action.split("/")[:2])
            found.append(Pin(repository, ref, tag.group(1), where))
    return found, problems


def resolve(fetch: Fetch, repository: str, tag: str) -> tuple[str | None, str]:
    """The commit *tag* names in *repository*, or None with the reason."""
    status, body = fetch(f"/repos/{repository}/git/ref/tags/{tag}")
    if status == 404:
        return None, "the tag is gone"
    for _hop in range(MAX_TAG_HOPS):
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("object"), dict):
            return None, f"unresolved: the API answered {status}"
        target = body["object"]
        kind, sha = target.get("type"), str(target.get("sha", ""))
        if kind == "commit" and _SHA.fullmatch(sha):
            return sha, ""
        if kind != "tag":
            return None, f"unresolved: the tag points at a {kind}"
        status, body = fetch(f"/repos/{repository}/git/tags/{sha}")
    return None, f"unresolved: more than {MAX_TAG_HOPS} annotated tags deep"


def check(files: Iterable[tuple[str, str]], fetch: Fetch) -> list[str]:
    """Every pin whose SHA is not its tag's commit, or that cannot be shown to be."""
    found, problems = pins(files)
    answers: dict[tuple[str, str], tuple[str | None, str]] = {}
    for pin in found:
        key = (pin.repository, pin.tag)
        if key not in answers:
            answers[key] = resolve(fetch, pin.repository, pin.tag)
        commit, why = answers[key]
        if commit is None:
            problems.append(f"{pin.where}: {pin.repository} {pin.tag}: {why}")
        elif commit != pin.sha:
            problems.append(
                f"{pin.where}: {pin.repository}@{pin.sha} is not {pin.tag}, which is {commit}"
            )
    return problems


def github_fetch(token: str | None) -> Fetch:
    def fetch(path: str) -> tuple[int, object]:
        request = urllib.request.Request(API + path, method="GET")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, None
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            return 0, str(error)

    return fetch


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0] if argv else ".")
    files = [
        (path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted((root / ".github" / "workflows").glob("*.y*ml"))
    ]
    found, _ = pins(files)
    if not found:
        print("::error::read no SHA-pinned action, so this check proved nothing")
        return 1
    problems = check(files, github_fetch(os.environ.get("GH_TOKEN") or None))
    for problem in problems:
        print(f"::error::{problem}")
    distinct = len({(p.repository, p.tag) for p in found})
    print(f"{len(found)} pins, {distinct} distinct releases, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
