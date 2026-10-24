"""What built each release artifact, as it ran (#3911, the fields #4585's step 7 defines).

The release manifest (``tools/ci/release_manifest.py``) records each artifact's digest, and with
it the identity of the job that built it: not what the workflow text names, but what ran.

- ``record`` is the last step of each job that builds an artifact. It writes the job's own part:
  the runner it ran on (``RUNNER_NAME``, ``ImageOS``, ``ImageVersion``); the Python, uv, Node and
  npm versions it used (``--tool NAME=VERSION``, all four; an image's build names none, since the
  bases it built on are its toolchain); every image that ran (``--bases``, the provenance
  BuildKit attached to each platform, which ``tools/ci/oci_layout.py`` reads out of the layout,
  and ``--containers``, ``docker image ls --digests`` after the job's containers ran); and what it
  built (``--artifact``, file globs, and ``--image``).
- ``collect`` runs in the manifest job. It finds each record's own job in this run by the
  runner's name, and reads that job's set-up log from the API: the runner image GitHub reports
  there, and every action the job downloaded, nested ones included, each with the SHA it
  resolved to (:func:`parse_setup`). It writes the list the manifest's ``builds`` holds
  (:func:`collect`), and refuses a record whose job it cannot find, a record with no image of
  its own, and a log that names no runner image, no action, or an action without its SHA.

The runner's image is recorded twice, since GitHub reports it twice: the set-up log's ``Image``
and ``Version``, and the image's own ``ImageOS`` and ``ImageVersion`` as the job read them. The
two versions can differ on one runner (rehearsal run 40028736768's desktop-linux job read
20261023.334.3 where its set-up log said 20261012.334.1), so both are kept and neither is refused
for disagreeing with the other.

Both exit 2 on anything they cannot rely on, naming it. Standard library only, with no relative
imports: a job runs it as ``python3 -I tools/ci/build_identity.py``.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

API = "https://api.github.com"
RECORD_VERSION = 1
#: The tools each build names, in this order.
TOOLS = ("python", "uv", "node", "npm")

_STAMP = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ")
_COLOUR = re.compile(r"\x1b\[[0-9;]*m")
_DOWNLOAD = re.compile(
    r"Download action repository '(?P<action>[^'@]+)@(?P<ref>[^']+)' \(SHA:(?P<sha>[0-9a-f]{40})\)"
)
_DOWNLOADING = re.compile(r"Download (?:action repository|immutable action package) '([^']+)'")
_SOURCE = re.compile(r"Source commit SHA: (?P<sha>[0-9a-f]{40})")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class Refused(ValueError):
    """An identity this cannot rely on: missing, unreadable, or not borne out by the job's log."""


@dataclass(frozen=True)
class Action:
    action: str
    ref: str
    sha: str


@dataclass(frozen=True)
class SetUp:
    """What a job's set-up log says ran: its runner image, and every action it downloaded."""

    image: str
    version: str
    actions: tuple[Action, ...]


# ── the set-up log ─────────────────────────────────────────────────────────────────────────────


def _lines(text: str) -> list[str]:
    return [_STAMP.sub("", _COLOUR.sub("", line)).strip() for line in text.splitlines()]


def parse_setup(text: str) -> SetUp:
    """The runner image and every downloaded action, each with its SHA, that a job's log names.

    The runner image is the ``Runner Image`` group GitHub writes first. An action is a
    ``Download action repository 'owner/name@ref' (SHA:...)`` line, or an immutable action package
    with its ``Source commit SHA``: the set-up resolves a composite action's own actions too, so
    the nested ones are among them. A download line without its SHA is refused, never skipped.
    """
    lines = _lines(text)
    image = version = ""
    if "##[group]Runner Image" in lines:
        start = lines.index("##[group]Runner Image")
        for line in lines[start + 1 :]:
            if line.startswith("##[endgroup]"):
                break
            key, _, value = line.partition(": ")
            if key == "Image":
                image = value.strip()
            elif key == "Version":
                version = value.strip()
    if not image:
        raise Refused("the set-up log names no runner image")
    if not version:
        raise Refused("the set-up log names no runner image version")
    actions: list[Action] = []
    for number, line in enumerate(lines):
        named = _DOWNLOADING.search(line)
        if not named:
            continue
        found = _DOWNLOAD.search(line)
        if found:
            actions.append(Action(found["action"], found["ref"], found["sha"]))
            continue
        action, _, ref = named.group(1).partition("@")
        source = next(
            (m for m in (_SOURCE.search(after) for after in lines[number + 1 : number + 6]) if m),
            None,
        )
        if "immutable action package" not in line or not source or not ref:
            raise Refused(f"the set-up log downloaded {named.group(1)} with no SHA")
        actions.append(Action(action, ref, source["sha"]))
    if not actions:
        raise Refused("the set-up log downloaded no action")
    unique = sorted(set(actions), key=lambda a: (a.action, a.ref, a.sha))
    return SetUp(image, version, tuple(unique))


# ── one build ──────────────────────────────────────────────────────────────────────────────────


def _string(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"the build record names no {key}")
    return value


def collect(
    records: Sequence[Mapping[str, Any]],
    jobs: Sequence[Mapping[str, Any]],
    log: Callable[[int], str],
) -> list[dict[str, Any]]:
    """Each record, borne out by its own job's set-up log: the manifest's ``builds``.

    *jobs* is this run's jobs as the API lists them (``id``, ``name``, ``runner_name``), and
    *log* reads one job's log by its id.
    """
    if not records:
        raise Refused("no build record: every job that builds an artifact writes one")
    builds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if record.get("version") != RECORD_VERSION:
            raise Refused(f"not a version {RECORD_VERSION} build record: {record!r}")
        runner = _string(record, "runner_name")
        if runner in seen:
            raise Refused(f"{runner} is recorded twice")
        seen.add(runner)
        ran = [job for job in jobs if job.get("runner_name") == runner]
        if len(ran) != 1:
            many = f"{len(ran)} jobs of this run ran" if ran else "no job of this run ran"
            raise Refused(f"{many} on {runner}, so its build record cannot be read against a log")
        job = ran[0]
        image_os, image_version = _string(record, "image_os"), _string(record, "image_version")
        setup = parse_setup(log(int(job["id"])))
        images = [str(i) for i in record.get("images") or []]
        tools = record.get("toolchain") or {}
        builds.append(
            {
                "job": str(job.get("name")),
                "runner": {
                    "image": setup.image,
                    "version": setup.version,
                    "image_os": image_os,
                    "image_version": image_version,
                },
                "actions": [
                    {"action": a.action, "ref": a.ref, "sha": a.sha} for a in setup.actions
                ],
                "toolchain": {tool: tools.get(tool) if not images else None for tool in TOOLS},
                "bases": [dict(base) for base in record.get("bases") or []],
                "artifacts": sorted([*images, *(str(a) for a in record.get("artifacts") or [])]),
            }
        )
    return sorted(builds, key=lambda b: b["job"])


# ── the record a job writes ────────────────────────────────────────────────────────────────────


def _bases(paths: Iterable[str], containers: str | None) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for pattern in paths:
        matched = sorted(glob.glob(pattern))
        if not matched:
            raise Refused(f"--bases {pattern} matches no file")
        for path in matched:
            listed = _json(Path(path).read_text(encoding="utf-8"), path)
            if not isinstance(listed, list) or not listed:
                raise Refused(f"{path} lists no base image")
            for base in listed:
                if (
                    not isinstance(base, dict)
                    or not isinstance(base.get("image"), str)
                    or not _DIGEST.fullmatch(str(base.get("digest")))
                ):
                    raise Refused(f"{path}: {base!r} is not an image and its digest")
                found.append({"image": base["image"], "digest": base["digest"]})
    if containers:
        # `docker image ls --digests`: an image built or loaded here has no digest, and ran from
        # no registry.
        for line in Path(containers).read_text(encoding="utf-8").splitlines():
            image, _, digest = line.strip().rpartition("@")
            if image and image != "<none>" and _DIGEST.fullmatch(digest):
                found.append({"image": image, "digest": digest})
    unique = {(b["image"], b["digest"]): b for b in found}
    return list(unique.values())


def record(
    *,
    environ: Mapping[str, str],
    artifacts: Sequence[str],
    images: Sequence[str],
    tools: Sequence[str],
    bases: Sequence[str],
    containers: str | None,
) -> dict[str, Any]:
    """The job's own record, from its environment and arguments."""
    for key in ("RUNNER_NAME", "ImageOS", "ImageVersion"):
        if not environ.get(key, "").strip():
            raise Refused(f"no {key}: a build record is written on a GitHub-hosted runner")
    named: dict[str, str] = {}
    for pair in tools:
        name, _, version = pair.partition("=")
        if name not in TOOLS or name in named:
            raise Refused(f"--tool {pair!r} is not one of {', '.join(TOOLS)}, once each")
        named[name] = version.strip()
    if not images:
        for tool in TOOLS:
            if not named.get(tool):
                raise Refused(f"the build names no {tool} version")
    files: list[str] = []
    for pattern in artifacts:
        matched = sorted(p for p in glob.glob(pattern) if Path(p).is_file())
        if not matched:
            raise Refused(f"--artifact {pattern} matches no file")
        files.extend(Path(p).name for p in matched)
    if not files and not images:
        raise Refused("the build record names no artifact")
    found = _bases(bases, containers)
    if images and not found:
        raise Refused("an image's build record names no base image")
    return {
        "version": RECORD_VERSION,
        "runner_name": environ["RUNNER_NAME"],
        "image_os": environ["ImageOS"],
        "image_version": environ["ImageVersion"],
        "artifacts": sorted(set(files)),
        "images": sorted(set(images)),
        "toolchain": {tool: named[tool] for tool in TOOLS if tool in named},
        "bases": found,
    }


# ── the API ────────────────────────────────────────────────────────────────────────────────────


def _request(url: str, token: str | None) -> urllib.request.Request:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    return request


def github_get(token: str) -> Callable[[str], Any]:
    def get(path: str) -> Any:
        with urllib.request.urlopen(
            _request(API + path, token), timeout=30
        ) as response:  # noqa: S310
            return json.loads(response.read() or b"null")

    return get


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # the log's own address is read next, without the token


def github_log(token: str, repository: str) -> Callable[[int], str]:
    """One job's log. The API answers with a redirect to signed storage, which is read without
    the job's token: the token goes to the API alone."""
    opener = urllib.request.build_opener(_Stay)

    def log(job_id: int) -> str:
        return _log_at(opener, f"{API}/repos/{repository}/actions/jobs/{job_id}/logs", token)

    return log


def _log_at(opener: urllib.request.OpenerDirector, url: str, token: str) -> str:
    try:
        with opener.open(_request(url, token), timeout=30) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as answer:
        location = answer.headers.get("Location") if answer.code in (301, 302, 303, 307) else None
        if not location:
            raise
    with urllib.request.urlopen(location, timeout=60) as response:  # noqa: S310
        return response.read().decode("utf-8", "replace")


def run_jobs(get: Callable[[str], Any], repository: str, run_id: int) -> list[Mapping[str, Any]]:
    """Every job of the run, in every attempt: a job a rerun did not repeat is its own attempt's."""
    answer = get(f"/repos/{repository}/actions/runs/{run_id}/jobs?filter=all&per_page=100")
    jobs = answer.get("jobs") if isinstance(answer, dict) else None
    if not isinstance(jobs, list) or answer.get("total_count", len(jobs)) > len(jobs):
        raise Refused(f"cannot read every job of run {run_id}: {str(answer)[:200]}")
    return [job for job in jobs if isinstance(job, dict)]


# ── the command ────────────────────────────────────────────────────────────────────────────────


def _json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Refused(f"{what} is not JSON: {exc}") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Refused(message)


def _record_command(argv: list[str]) -> int:
    parser = _Parser(prog="build_identity.py record", add_help=False)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--bases", action="append", default=[])
    parser.add_argument("--containers")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.name):
        raise Refused(f"--name {args.name!r} is not a plain name")
    built = record(
        environ=os.environ,
        artifacts=args.artifact,
        images=args.image,
        tools=args.tool,
        bases=args.bases,
        containers=args.containers,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.name}.json"
    path.write_text(json.dumps(built, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"build record: {path}: {', '.join(built['images'] + built['artifacts'])}")
    return 0


def _collect_command(argv: list[str]) -> int:
    parser = _Parser(prog="build_identity.py collect", add_help=False)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get("RUNS_TOKEN")
    if not token:
        raise Refused("no RUNS_TOKEN: the job's token reads this run's jobs and their logs")
    records = [
        _json(path.read_text(encoding="utf-8"), str(path))
        for path in sorted(args.records.glob("*.json"))
    ]
    jobs = run_jobs(github_get(token), args.repository, args.run_id)
    builds = collect(records, jobs, github_log(token, args.repository))
    args.out.write_text(json.dumps(builds, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for build in builds:
        runner = build["runner"]
        print(
            f"{build['job']}: {runner['image']} {runner['version']}, "
            f"{len(build['actions'])} action(s), {len(build['bases'])} base image(s)"
        )
    return 0


VERBS = {"collect": _collect_command, "record": _record_command}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in VERBS:
        print(f"usage: build_identity.py {{{','.join(sorted(VERBS))}}} ...", file=sys.stderr)
        return 2
    try:
        return VERBS[argv[0]](argv[1:])
    except (Refused, OSError) as exc:
        print(f"build_identity.py {argv[0]}: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
