"""``python -m tools.ci image-pins``: the images the workflows pin, against their tags now (#4585).

Dependabot refreshes the Dockerfile bases and the actions. The images a workflow names itself (a
job's ``container:``, an action's ``image:`` input) it does not, so they were moved by hand. Each
such pin is ``<image>:<tag>@sha256:<platform digest>`` with a comment above it naming the
platform and the index it was taken from (``the linux/amd64 manifest ... of index sha256:...``),
which the supply-chain contract already requires. This reads every one, asks the image's registry
what the tag names now, anonymously, and reports each pin whose tag has moved, with the exact new
line and comment digest to put in its place.

It reads and writes no file. It fails on a moved tag and on any answer it cannot read (an unknown
registry, a tag that is gone, a platform the index no longer lists), never passing a pin it could
not check. With ``--issue-repo`` (the weekly run on ``main``) it opens or edits the one open issue
titled "Pinned images to refresh" that the workflow's account opened, and closes it when every pin
is current. See docs/maintainers/ci.md, "How a refresh arrives".
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ISSUE_TITLE = "Pinned images to refresh"
BOT = "github-actions[bot]"
GH_TIMEOUT_SECONDS = 60
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json"
)

#: ``get(url, headers)`` returns the HTTP status, the response headers (lower-case names) and the
#: body; status 0 is an answer the network could not give.
Get = Callable[[str, Mapping[str, str]], tuple[int, Mapping[str, str], bytes]]

_PIN = re.compile(
    r"^\s*(?:-\s+)?(?:container|image):\s*(?:docker://)?"
    r"(?P<image>[a-z0-9][a-z0-9./_-]*):(?P<tag>[\w.-]+)@(?P<digest>sha256:[0-9a-f]{64})\s*$"
)
_PLATFORM = re.compile(r"(?:the|its) (?P<platform>[a-z0-9]+/[a-z0-9]+(?:/[a-z0-9]+)?) manifest")
_INDEX = re.compile(r"index\s+(?P<index>sha256:[0-9a-f]{64})")


@dataclass(frozen=True)
class Pin:
    where: str
    image: str
    tag: str
    digest: str
    platform: str
    index: str


@dataclass(frozen=True)
class Moved:
    pin: Pin
    index: str
    digest: str

    def line(self) -> str:
        return (
            f"{self.pin.where}: {self.pin.image}:{self.pin.tag} has moved: index "
            f"{self.pin.index} -> {self.index}; pin {self.pin.image}:{self.pin.tag}@{self.digest} "
            f"(its {self.pin.platform} manifest)"
        )


def pins(files: Iterable[tuple[str, str]]) -> tuple[list[Pin], list[str]]:
    """Every image pinned by digest in *files* (name, text), and a problem for each one whose
    comment does not name its platform and index."""
    found: list[Pin] = []
    problems: list[str] = []
    for name, text in files:
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            match = _PIN.match(line)
            if not match:
                continue
            where = f"{name}:{number}"
            above: list[str] = []
            for previous in reversed(lines[: number - 1]):
                stripped = previous.strip()
                if not stripped.startswith("#"):
                    break
                above.insert(0, stripped.lstrip("#").strip())
            comment = " ".join(above)
            platform, index = _PLATFORM.search(comment), _INDEX.search(comment)
            if not platform or not index:
                problems.append(f"{where}: the comment above names no platform manifest and index")
                continue
            found.append(
                Pin(
                    where,
                    match["image"],
                    match["tag"],
                    match["digest"],
                    platform["platform"],
                    index["index"],
                )
            )
    return found, problems


def registry(image: str) -> tuple[str, str, str]:
    """(registry host, repository, token URL) of *image*, the way docker resolves a short name."""
    first, _, rest = image.partition("/")
    if not rest or ("." not in first and ":" not in first and first != "localhost"):
        host, repository = "docker.io", image if rest else f"library/{image}"
    else:
        host, repository = first, rest
    if host == "docker.io":
        token = (
            "https://auth.docker.io/token?service=registry.docker.io"
            f"&scope=repository:{repository}:pull"
        )
        return "registry-1.docker.io", repository, token
    if host == "ghcr.io":
        return host, repository, f"https://ghcr.io/token?scope=repository:{repository}:pull"
    if host == "mirror.gcr.io":
        return host, repository, ""
    raise ValueError(f"{image}: {host} is a registry this check does not know")


def resolve(get: Get, image: str, tag: str, platform: str) -> tuple[str, str]:
    """(index digest, *platform*'s manifest digest) that *image*:*tag* names now."""
    host, repository, token_url = registry(image)
    headers = {"Accept": INDEX_TYPES}
    if token_url:
        status, _, body = get(token_url, {})
        try:
            token = json.loads(body).get("token") if status == 200 else None
        except ValueError:
            token = None
        if not isinstance(token, str):
            raise ValueError(f"{image}: no pull token ({status})")
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://{host}/v2/{repository}/manifests/{urllib.parse.quote(tag)}"
    status, answer, body = get(url, headers)
    if status != 200:
        raise ValueError(f"{image}:{tag}: the registry answered {status}")
    index = answer.get("docker-content-digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", index):
        raise ValueError(f"{image}:{tag}: no content digest in the answer")
    try:
        manifests = json.loads(body).get("manifests")
    except ValueError:
        manifests = None
    if not isinstance(manifests, list):
        raise ValueError(f"{image}:{tag}: not an index of platforms")
    os_name, _, arch_variant = platform.partition("/")
    arch, _, variant = arch_variant.partition("/")
    for entry in manifests:
        where = (entry.get("platform") or {}) if isinstance(entry, dict) else {}
        if (
            where.get("os") == os_name
            and where.get("architecture") == arch
            and (where.get("variant") or "") == variant
        ):
            digest = entry.get("digest")
            if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                return index, digest
    raise ValueError(f"{image}:{tag}: the index lists no {platform} manifest")


def check(files: Iterable[tuple[str, str]], get: Get) -> tuple[list[Moved], list[str]]:
    """The pins whose tag has moved, and every pin or answer that could not be checked."""
    found, problems = pins(files)
    moved: list[Moved] = []
    answers: dict[tuple[str, str, str], tuple[str, str] | str] = {}
    for pin in found:
        key = (pin.image, pin.tag, pin.platform)
        if key not in answers:
            try:
                answers[key] = resolve(get, pin.image, pin.tag, pin.platform)
            except ValueError as error:
                answers[key] = str(error)
        answer = answers[key]
        if isinstance(answer, str):
            problems.append(f"{pin.where}: {answer}")
            continue
        index, digest = answer
        if index != pin.index or digest != pin.digest:
            moved.append(Moved(pin, index, digest))
    return moved, problems


def http_get(url: str, headers: Mapping[str, str]) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            names = {k.lower(): v for k, v in response.headers.items()}
            return response.status, names, response.read()
    except urllib.error.HTTPError as error:
        return error.code, {}, b""
    except (OSError, http.client.HTTPException) as error:
        return 0, {}, str(error).encode()


def workflow_files(root: Path) -> list[Path]:
    """Every workflow, and every local action's ``action.yml``."""
    workflows = [
        p for p in (root / ".github" / "workflows").glob("*") if p.suffix in (".yml", ".yaml")
    ]
    actions = [
        p
        for p in (root / ".github" / "actions").rglob("*")
        if p.name in ("action.yml", "action.yaml")
    ]
    return sorted(workflows + actions)


def markdown(moved: Sequence[Moved], problems: Sequence[str], run_url: str) -> str:
    if not moved and not problems:
        return f"Every pinned workflow image is its tag's current digest. From {run_url}.\n"
    lines = [
        "The weekly image check found pinned workflow images to refresh. For each, put the new "
        "digest on the pin and the new index in the comment above it, in one reviewed pull "
        'request (docs/maintainers/ci.md, "How a refresh arrives").',
        "",
    ]
    lines += [f"- {m.line()}" for m in moved]
    lines += [f"- not checked: {p}" for p in problems]
    lines += ["", f"From {run_url}."]
    return "\n".join(lines) + "\n"


def _gh(*args: str) -> str:
    done = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    return done.stdout


def publish(repo: str, body: str, stale: bool, gh: Callable[..., str] = _gh) -> int | None:
    """Open or edit the one refresh issue when *stale*; otherwise close it if one is open."""
    batch = json.loads(
        gh(
            "api",
            f"repos/{repo}/issues?state=open&creator=github-actions%5Bbot%5D&per_page=100",
        )
    )
    if not isinstance(batch, list):
        raise ValueError("the open issues are not a list")
    ours = sorted(
        int(i["number"])
        for i in batch
        if i.get("title") == ISSUE_TITLE
        and isinstance(i.get("user"), Mapping)
        and i["user"].get("login") == BOT
        and "pull_request" not in i
    )
    with tempfile.TemporaryDirectory() as scratch:
        text = Path(scratch) / "body.md"
        text.write_text(body, encoding="utf-8")
        if stale and not ours:
            url = gh(
                "issue", "create", "-R", repo, "--title", ISSUE_TITLE, "--body-file", str(text)
            )
            return int(url.strip().rsplit("/", 1)[-1])
        if stale:
            gh("issue", "edit", str(ours[0]), "-R", repo, "--body-file", str(text))
            return ours[0]
        for number in ours:
            gh("issue", "close", str(number), "-R", repo, "--comment", body.strip())
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci image-pins", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--issue-repo", help="open, edit or close the refresh issue here")
    parser.add_argument("--run-url", default="a local run")
    args = parser.parse_args(argv)
    files = [
        (str(p.relative_to(args.root)), p.read_text(encoding="utf-8"))
        for p in workflow_files(args.root)
    ]
    moved, problems = check(files, http_get)
    body = markdown(moved, problems, args.run_url)
    print(body, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("## Pinned workflow images\n\n" + body)
    if args.issue_repo:
        number = publish(args.issue_repo, body, bool(moved or problems))
        if number is not None:
            print(f"refresh issue: #{number}")
    return 1 if moved or problems else 0


if __name__ == "__main__":
    sys.exit(main())
