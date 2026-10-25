"""The gateway image outside a release: built plainly, booted by its content ID, on every change
that reaches it (#3917).

#3917 measured the Actions cache as the gateway image's layer cache and kept none: reading the
cached layers back took longer than building every layer from the runner's empty daemon, and
writing them took ten minutes a push. So the image is built with a plain ``docker build``. Its one
build outside a release is the catalogue's ``image`` entry, which merge.yml runs through run-job on
each merge candidate and on an untested push to main (the single-container workflow went with
#3915's flip). The rules, each failed by a planted edit below, are:

1. The ``image`` entry builds the image with one plain ``docker build`` that keeps its content ID
   (``--iidfile``), and its smoke runs that ID (``--image-id-file``), never a tag.
2. No workflow and no catalogue step gives an image build a layer cache: no ``cache-from`` or
   ``cache-to`` input, and no ``--cache-from``, ``--cache-to`` or ``type=gha``.
3. The entry's inputs reach every path the Dockerfile copies, so a change to what ships builds and
   boots it.
4. The catalogue's one entry that builds the gateway image is ``image``, and its inputs hold
   ``src/personalclaw/``, ``uv.lock`` and ``deploy/docker/``: a backend-only change boots the image
   it ships.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from rails_repo import REPO

from tools.ci import catalogue

_WORKFLOWS = ".github/workflows/"
_DOCKERFILE = "deploy/docker/Dockerfile.backend"
_CATALOGUE = catalogue.CATALOGUE
_ID_FILE = "{runner_temp}/gateway-image.id"
_BUILD = f"docker build --file {_DOCKERFILE} --target runtime --iidfile {_ID_FILE} ."
_SMOKE = f"python3 tools/docker_single_container_smoke.py --image-id-file {_ID_FILE}"
#: The inputs #3917 names: the backend, the lock it installs from, and the image's own folder.
_REQUIRED_INPUTS = ("src/personalclaw/", "uv.lock", "deploy/docker/")
_CACHE_RUN = re.compile(r"--cache-from\b|--cache-to\b|type=gha\b")


def _texts() -> dict[str, str]:
    """Every workflow by its path, and the catalogue."""
    texts = {
        f"{_WORKFLOWS}{p.name}": p.read_text(encoding="utf-8")
        for p in sorted((REPO / _WORKFLOWS).glob("*.yml"))
    }
    texts[_CATALOGUE] = (REPO / _CATALOGUE).read_text(encoding="utf-8")
    return texts


def _tracked() -> list[str]:
    done = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [f for f in done.stdout.split("\0") if f]


def _steps(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for job_id, job in (data.get("jobs") or {}).items():
        if isinstance(job, dict):
            for step in job.get("steps") or []:
                if isinstance(step, dict):
                    yield str(job_id), step


def _pattern(path: str) -> re.Pattern[str]:
    """A workflow path filter as a regular expression: ``**`` any path, ``*`` within a folder."""
    out = ""
    for part in re.split(r"(\*\*|\*)", path):
        out += {"**": ".*", "*": "[^/]*"}.get(part, re.escape(part))
    return re.compile(out)


def _copied(dockerfile: str) -> list[str]:
    """Each path the Dockerfile copies from the build context: every COPY with no ``--from``."""
    found: list[str] = []
    for line in dockerfile.replace("\\\n", " ").splitlines():
        words = line.split()
        if not words or words[0].upper() != "COPY":
            continue
        if any(w.startswith("--from") for w in words[1:]):
            continue
        found += [w for w in words[1:] if not w.startswith("--")][:-1]
    return found


def _image_job(texts: dict[str, str]) -> catalogue.Job | None:
    cat = catalogue.load(texts[_CATALOGUE], _CATALOGUE)
    return next((j for j in cat.jobs if j.id == "image"), None)


def _step_problems(texts: dict[str, str]) -> list[str]:
    job = _image_job(texts)
    if job is None:
        return ["the catalogue has no image entry"]
    lines = [" ".join(step.argv) for step in job.command]
    bad: list[str] = []
    builds = [line for line in lines if "docker" in line and "build" in line]
    if builds != [_BUILD]:
        bad.append(f"the image entry builds with {builds}, not one plain {_BUILD!r}")
    smokes = [line for line in lines if "docker_single_container_smoke" in line]
    if smokes != [_SMOKE]:
        bad.append("the image entry's smoke does not run the build's own content ID")
    return bad


def _cache_problems(texts: dict[str, str]) -> list[str]:
    bad: list[str] = []
    for rel, text in texts.items():
        if not rel.startswith(_WORKFLOWS):
            continue
        for job_id, step in _steps(yaml.safe_load(text) or {}):
            given = step.get("with") if isinstance(step.get("with"), dict) else {}
            if {"cache-from", "cache-to"} & set(given) or _CACHE_RUN.search(str(step.get("run"))):
                bad.append(f"{rel.removeprefix(_WORKFLOWS)}:{job_id} gives a build a layer cache")
    for job in catalogue.load(texts[_CATALOGUE], _CATALOGUE).jobs:
        if any(_CACHE_RUN.search(" ".join(step.argv)) for step in job.command):
            bad.append(f"the catalogue's {job.id} gives a build a layer cache")
    return bad


def _filter_problems(texts: dict[str, str], dockerfile: str, tracked: list[str]) -> list[str]:
    job = _image_job(texts)
    patterns = [_pattern(_as_filter(spec)) for spec in (job.inputs if job else ())]
    bad: list[str] = []
    for source in _copied(dockerfile):
        under = [f for f in tracked if f == source or f.startswith(source.rstrip("/") + "/")]
        if not under:
            bad.append(f"the Dockerfile copies {source}, which the tree does not hold")
        missed = [f for f in under if not any(p.fullmatch(f) for p in patterns)]
        if missed:
            bad.append(
                f"the Dockerfile copies {missed[0]}, and no input of the image entry reaches it"
            )
    return bad


def _as_filter(spec: str) -> str:
    """A catalogue input as a workflow path filter spells it: a folder's every path."""
    return f"{spec}**" if spec.endswith("/") else spec


def _entry_problems(texts: dict[str, str]) -> list[str]:
    cat = catalogue.load(texts[_CATALOGUE], _CATALOGUE)
    found = [
        j
        for j in cat.jobs
        if any(s.argv[:2] == ("docker", "build") and _DOCKERFILE in s.argv for s in j.command)
    ]
    if [j.id for j in found] != ["image"]:
        return [f"the catalogue's entries that build the gateway image are {[j.id for j in found]}"]
    (job,) = found
    return [
        f"the image entry's inputs leave out {s}" for s in _REQUIRED_INPUTS if s not in job.inputs
    ]


def _problems(texts: dict[str, str], dockerfile: str, tracked: list[str]) -> list[str]:
    return [
        *_step_problems(texts),
        *_cache_problems(texts),
        *_filter_problems(texts, dockerfile, tracked),
        *_entry_problems(texts),
    ]


def _dockerfile() -> str:
    return (REPO / _DOCKERFILE).read_text(encoding="utf-8")


def test_the_gateway_image_is_built_plainly_and_booted_by_its_content_id() -> None:
    assert _problems(_texts(), _dockerfile(), _tracked()) == []


_BUILD_ARGV = '"--target", "runtime", "--iidfile", "{runner_temp}/gateway-image.id", "."'


@pytest.mark.parametrize(
    "rel, old, new, says",
    [
        (
            _CATALOGUE,
            _BUILD_ARGV,
            _BUILD_ARGV.replace('"--iidfile"', '"--tag", "personalclaw-gateway:ci", "--iidfile"'),
            "builds with",
        ),
        (
            _CATALOGUE,
            _BUILD_ARGV,
            _BUILD_ARGV.replace('"."', '"--cache-from", "type=gha,scope=gateway-runtime", "."'),
            "the catalogue's image gives a build a layer cache",
        ),
        (
            _CATALOGUE,
            '"--image-id-file", "{runner_temp}/gateway-image.id"',
            '"--image", "personalclaw-gateway:ci"',
            "own content ID",
        ),
        (
            _WORKFLOWS + "release-artifacts.yml",
            "          no-cache: true\n",
            "          no-cache: true\n          cache-from: type=gha,scope=gateway-runtime\n",
            "release-artifacts.yml:images gives a build a layer cache",
        ),
        (
            _CATALOGUE,
            '  "src/personalclaw/",\n',
            '  "src/personalclaw/dashboard/",\n',
            "no input of the image entry reaches it",
        ),
        (_CATALOGUE, '  "src/personalclaw/",\n', "", "leave out src/personalclaw/"),
        (_CATALOGUE, '  "uv.lock",\n', "", "leave out uv.lock"),
        (_CATALOGUE, '  "deploy/docker/",\n', "", "leave out deploy/docker/"),
        (
            _CATALOGUE,
            '{ argv = ["docker", "build", "--file", "deploy/docker/Dockerfile.backend"',
            '{ argv = ["docker", "image", "--file", "deploy/docker/Dockerfile.backend"',
            "entries that build the gateway image are []",
        ),
    ],
)
def test_a_planted_edit_of_the_image_build_fails(rel: str, old: str, new: str, says: str) -> None:
    texts = _texts()
    start, end = 0, len(texts[rel])
    if rel == _CATALOGUE:
        # Within the image entry: other entries name some of the same paths.
        start = texts[rel].index('\nid = "image"\n')
        end = texts[rel].find("\n[[job]]", start)
        end = len(texts[rel]) if end < 0 else end
    part = texts[rel][start:end]
    assert part.count(old) >= 1, f"the plant's anchor is gone: {old!r}"
    texts[rel] = texts[rel][:start] + part.replace(old, new, 1) + texts[rel][end:]
    found = _problems(texts, _dockerfile(), _tracked())
    assert any(says in p for p in found), found


def test_a_copied_path_no_filter_reaches_fails() -> None:
    """The Dockerfile copying a path no input of the image entry names: the image would ship it
    unbooted."""
    planted = _dockerfile() + "\nCOPY docs/ /opt/docs/\n"
    found = _problems(_texts(), planted, _tracked())
    assert any(
        "copies docs/" in p and "no input of the image entry reaches it" in p for p in found
    ), found
