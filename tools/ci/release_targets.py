"""What a release's publish target already holds, as object name to digest (#3911).

``python3 -I tools/ci/release_targets.py <verb> ... --out <file>`` reads one target and writes a
JSON object of object name to digest. Each publish job runs it before it writes, and holds what it
wrote against the approved manifest (``release-manifest compare``): an object the target lacks is
created, an identical one is resumed, and a different one fails the job before anything is
written. So a release run again, or resumed after a failure, publishes only what it built.

- ``pypi --project P --version V``: the files PyPI holds for that version (its JSON API), by
  sha256. Its 404 is a version PyPI has not published.
- ``github-release --repository R --tag T``: the assets of the tag's GitHub Release, by the digest
  GitHub records for each, read with ``GH_TOKEN``. Its 404 is a tag with no Release. An asset not
  wholly uploaded, or with no sha256 digest, refuses.
- ``image --image NAME --tag T``: the index the registry holds under ``NAME:T``, by the sha256 of
  its bytes (``skopeo inspect --raw``), the digest the manifest records for an image. Its
  "manifest unknown" is a tag the registry does not hold.

Anything else refuses (exit 2), naming why, and writes nothing: a target it cannot read never
reads as empty, which would let a write go ahead over an object it never compared. Standard
library only, so a trusted job runs the default branch's copy with ``python3 -I``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

#: Asks one URL with these headers, and answers its status and body.
Fetch = Callable[[str, Mapping[str, str]], "tuple[int, bytes]"]
#: Runs ``skopeo inspect --raw`` on one reference, and answers its exit, its output and its error.
Inspect = Callable[[str], "tuple[int, bytes, str]"]

_HEX = re.compile(r"[0-9a-f]{64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PROJECT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+!-]*")
_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_IMAGE = re.compile(r"[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9._-]+)+")
_TIMEOUT = 60


class Refused(Exception):
    """A target this cannot read, or input it cannot rely on: nothing is written."""


def _named(value: object, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Refused(f"{what} {value!r} is not one")
    return value


def _ask(fetch: Fetch, url: str, headers: Mapping[str, str], what: str) -> object | None:
    """The JSON *url* answers, or None for its 404; anything else refuses."""
    try:
        status, body = fetch(url, headers)
    except (OSError, ValueError) as error:
        raise Refused(f"cannot read {what}: {error}") from None
    if status == 404:
        return None
    if status != 200:
        raise Refused(f"cannot read {what}: it answered {status}")
    try:
        return json.loads(body)
    except ValueError:
        raise Refused(f"cannot read {what}: its answer is not JSON") from None


def _add(held: dict[str, str], name: object, digest: str, what: str) -> None:
    if not isinstance(name, str) or not name:
        raise Refused(f"cannot read {what}: it lists an object with no name")
    if name in held:
        raise Refused(f"cannot read {what}: it lists {name} twice")
    held[name] = digest


def pypi(project: str, version: str, fetch: Fetch) -> dict[str, str]:
    """The files PyPI holds for *project* at *version*, by file name, each ``sha256:<hex>``."""
    _named(project, _PROJECT, "the project")
    _named(version, _VERSION, "the version")
    what = f"PyPI's {project} {version}"
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    answer = _ask(fetch, url, {"Accept": "application/json"}, what)
    if answer is None:
        return {}
    files = answer.get("urls") if isinstance(answer, dict) else None
    if not isinstance(files, list):
        raise Refused(f"cannot read {what}: its answer holds no list of files")
    held: dict[str, str] = {}
    for entry in files:
        file = entry if isinstance(entry, dict) else {}
        digests = file.get("digests")
        sha = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(sha, str) or not _HEX.fullmatch(sha):
            raise Refused(f"cannot read {what}: {file.get('filename')!r} has no sha256")
        _add(held, file.get("filename"), f"sha256:{sha}", what)
    return held


def github_release(repository: str, tag: str, fetch: Fetch, *, token: str) -> dict[str, str]:
    """The assets of *tag*'s GitHub Release in *repository*, by name, each by the digest GitHub
    records for it."""
    if not token:
        raise Refused("no token: a Release's assets are read with GH_TOKEN")
    _named(repository, _REPOSITORY, "the repository")
    _named(tag, _TAG, "the tag")
    what = f"{repository}'s Release {tag}"
    url = f"https://api.github.com/repos/{repository}/releases/tags/{urllib.parse.quote(tag)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    answer = _ask(fetch, url, headers, what)
    if answer is None:
        return {}
    assets = answer.get("assets") if isinstance(answer, dict) else None
    if not isinstance(assets, list):
        raise Refused(f"cannot read {what}: its answer holds no list of assets")
    held: dict[str, str] = {}
    for entry in assets:
        asset = entry if isinstance(entry, dict) else {}
        name = asset.get("name")
        if asset.get("state") != "uploaded":
            raise Refused(f"cannot read {what}: {name!r} is not wholly uploaded")
        digest = asset.get("digest")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise Refused(f"cannot read {what}: {name!r} has no digest")
        _add(held, name, digest, what)
    return held


def image(name: str, tag: str, inspect: Inspect) -> dict[str, str]:
    """The index the registry holds under *name*:*tag*, as ``{name: sha256:<hex of its bytes>}``,
    or nothing when the registry does not hold the tag."""
    _named(name, _IMAGE, "the image")
    _named(tag, _TAG, "the tag")
    try:
        status, raw, said = inspect(f"docker://{name}:{tag}")
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused(f"cannot read {name}:{tag}: {error}") from None
    if status == 0:
        return {name: "sha256:" + hashlib.sha256(raw).hexdigest()}
    if "manifest unknown" in said:
        return {}
    raise Refused(f"cannot read {name}:{tag}: {said.strip() or f'skopeo exited {status}'}")


def fetch_url(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as answer:  # noqa: S310 - https only
            return answer.status, answer.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise OSError(str(error.reason)) from None


def inspect_image(ref: str) -> tuple[int, bytes, str]:
    run = subprocess.run(
        ["skopeo", "inspect", "--raw", ref], capture_output=True, timeout=_TIMEOUT * 2
    )
    return run.returncode, run.stdout, run.stderr.decode("utf-8", "replace")


VERBS = ("pypi", "github-release", "image")


def _parser(verb: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"release-targets {verb}")
    if verb == "pypi":
        parser.add_argument("--project", required=True)
        parser.add_argument("--version", required=True)
    elif verb == "github-release":
        parser.add_argument("--repository", required=True)
        parser.add_argument("--tag", required=True)
    else:
        parser.add_argument("--image", required=True)
        parser.add_argument("--tag", required=True)
    parser.add_argument("--out", required=True, help="the JSON file of name to digest written")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in VERBS:
        print(f"usage: release-targets {{{','.join(VERBS)}}} ... --out <file>", file=sys.stderr)
        return 2
    verb = argv[0]
    args = _parser(verb).parse_args(argv[1:])
    try:
        if verb == "pypi":
            held = pypi(args.project, args.version, fetch_url)
        elif verb == "github-release":
            token = os.environ.get("GH_TOKEN", "")
            held = github_release(args.repository, args.tag, fetch_url, token=token)
        else:
            held = image(args.image, args.tag, inspect_image)
    except Refused as refused:
        print(f"release-targets {verb}: refused: {refused}", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(held, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    listed = "".join(f"\n  {name} {digest}" for name, digest in sorted(held.items()))
    print(f"release-targets {verb}: the target holds {len(held)}{listed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
