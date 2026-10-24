"""The gateway image's layer cache: main writes it, and nothing else does (#3917).

Each build of the gateway image outside a release reads its layers from the Actions cache, scope
``gateway-runtime``, and ``image-cache.yml`` alone writes it. GitHub keys each entry to the ref
of the run that saved it, and a run reads its own ref's and the default branch's, so a cache only
main's run writes holds only what main's code built from main's tree. The rules, each failed by a
planted edit below:

1. One step of one workflow writes a layer cache: ``image-cache.yml``'s, whose workflow runs on a
   push to main or a dispatch, and whose one job runs only on ``refs/heads/main``.
2. A step reads it only where the image is built for a smoke or for the cache: the single-container
   workflow, the tier workflows' stage jobs and the writer, never a release workflow, and each
   reads exactly main's scope.
3. Each such build is the same build (the gateway Dockerfile's runtime stage, from the checkout)
   with a closed set of inputs: no secret, no build argument, no SSH, no step env, and an empty
   ``github-token``, so it holds no token. A smoke's build loads the image; the writer's goes
   nowhere.
4. A smoke runs the build's own content ID, never a tag.
5. The two workflows' path filters are one list, each with its own file, and it holds every path
   the Dockerfile copies, so a change to what ships builds and boots it.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from rails_repo import REPO

from tools.ci import jobs

_WORKFLOWS = ".github/workflows/"
_WRITER = _WORKFLOWS + "image-cache.yml"
_SINGLE = _WORKFLOWS + "docker-single-container.yml"
_TIERS = (_WORKFLOWS + "merge.yml", _WORKFLOWS + "pr.yml")
_BUILD = "docker/build-push-action"
_SCOPE = "gateway-runtime"
_CACHE_FROM = f"type=gha,scope={_SCOPE}"
_CACHE_TO = f"type=gha,mode=max,scope={_SCOPE},ignore-error=true"
_IMAGE_ID = "${{ steps.image.outputs.imageid }}"
_ON_MAIN = "${{ github.ref == 'refs/heads/main' }}"
_IF_IMAGE = "${{ matrix.image }}"
#: Every input a gateway build step is given, and the value each must have where it is fixed.
_FIXED = {
    "context": ".",
    "file": jobs.GATEWAY_DOCKERFILE,
    "target": jobs.GATEWAY_TARGET,
    "push": False,
    "provenance": False,
    "sbom": False,
    "github-token": "",
    "cache-from": _CACHE_FROM,
}
_SMOKE_BUILD = {**_FIXED, "load": True}
_WRITER_BUILD = {**_FIXED, "outputs": "type=cacheonly", "cache-to": _CACHE_TO}
_SMOKE_RUN = 'python3 tools/docker_single_container_smoke.py --image "$IMAGE"'


def _texts() -> dict[str, str]:
    return {
        f"{_WORKFLOWS}{p.name}": p.read_text(encoding="utf-8")
        for p in sorted((REPO / _WORKFLOWS).glob("*.yml"))
    }


def _tracked() -> list[str]:
    done = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [f for f in done.stdout.split("\0") if f]


def _on(data: dict[Any, Any]) -> Any:
    return data.get("on", data.get(True))


def _steps(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    for job_id, job in (data.get("jobs") or {}).items():
        if isinstance(job, dict):
            for step in job.get("steps") or []:
                if isinstance(step, dict):
                    yield str(job_id), job, step


def _uses(step: dict[str, Any]) -> str:
    return str(step.get("uses", "")).split("@", 1)[0]


def _writes(step: dict[str, Any]) -> bool:
    given = step.get("with") if isinstance(step.get("with"), dict) else {}
    return "cache-to" in given or "--cache-to" in str(step.get("run", ""))


def _reads(step: dict[str, Any]) -> bool:
    given = step.get("with") if isinstance(step.get("with"), dict) else {}
    run = str(step.get("run", ""))
    return "cache-from" in given or "--cache-from" in run or "type=gha" in run


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
        args = [w for w in words[1:] if not w.startswith("--")]
        if any(w.startswith("--from") for w in words[1:]):
            continue
        found += args[:-1]
    return found


def _filter_problems(texts: dict[str, str], dockerfile: str, tracked: list[str]) -> list[str]:
    bad: list[str] = []
    single = yaml.safe_load(texts.get(_SINGLE, "")) or {}
    writer = yaml.safe_load(texts.get(_WRITER, "")) or {}
    on_single, on_writer = _on(single) or {}, _on(writer) or {}
    pr = list((on_single.get("pull_request") or {}).get("paths") or [])
    push = list((on_single.get("push") or {}).get("paths") or [])
    mine = list((on_writer.get("push") or {}).get("paths") or [])
    if pr != push:
        bad.append("the single-container workflow's pull_request and push filters differ")
    if [p for p in pr if p != _SINGLE] != [p for p in mine if p != _WRITER]:
        bad.append("image-cache.yml's path filter is not the single-container workflow's")
    if _SINGLE not in pr or _WRITER not in mine:
        bad.append("a path filter leaves out its own workflow")
    patterns = [_pattern(p) for p in pr]
    for source in _copied(dockerfile):
        under = [f for f in tracked if f == source or f.startswith(source.rstrip("/") + "/")]
        if not under:
            bad.append(f"the Dockerfile copies {source}, which the tree does not hold")
        missed = [f for f in under if not any(p.fullmatch(f) for p in patterns)]
        if missed:
            bad.append(f"the Dockerfile copies {missed[0]}, and no path filter reaches it")
    return bad


def _writer_problems(texts: dict[str, str]) -> list[str]:
    bad: list[str] = []
    data = yaml.safe_load(texts.get(_WRITER, "")) or {}
    on = _on(data) or {}
    if sorted(on) != ["push", "workflow_dispatch"] or on.get("workflow_dispatch") not in (
        None,
        {},
    ):
        bad.append(f"image-cache.yml runs on {sorted(on)}, not a push to main and a dispatch")
    elif set(on["push"]) != {"branches", "paths"} or on["push"]["branches"] != ["main"]:
        bad.append(f"image-cache.yml's push is {on['push']!r}, not main's alone")
    if data.get("permissions") != {"contents": "read"}:
        bad.append(f"image-cache.yml holds {data.get('permissions')!r}")
    found = list((data.get("jobs") or {}).items())
    if len(found) != 1 or found[0][1].get("if") != _ON_MAIN:
        bad.append("image-cache.yml is not one job that runs on refs/heads/main alone")
    elif "permissions" in found[0][1] or "environment" in found[0][1]:
        bad.append("image-cache.yml's job holds permissions or an environment of its own")
    return bad


def _build_problems(where: str, step: dict[str, Any], want: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    if _uses(step) != _BUILD:
        return [f"{where} reads or writes the layer cache, and is no gateway build step"]
    if step.get("with") != want:
        bad.append(f"{where}'s build is given {step.get('with')!r}, not {want!r}")
    if "env" in step or "secrets." in str(step):
        bad.append(f"{where}'s build has an env or reads a secret")
    return bad


def _cache_problems(texts: dict[str, str]) -> list[str]:
    bad: list[str] = []
    writers: list[str] = []
    for rel, text in texts.items():
        data = yaml.safe_load(text) or {}
        for job_id, _job, step in _steps(data):
            where = f"{rel.removeprefix(_WORKFLOWS)}:{job_id}"
            if _writes(step):
                writers.append(where)
                if rel != _WRITER:
                    bad.append(f"{where} writes the layer cache: only image-cache.yml, on main")
                else:
                    bad += _build_problems(where, step, _WRITER_BUILD)
                continue
            if not _reads(step):
                continue
            if rel == _SINGLE and job_id == "single-container":
                bad += _build_problems(where, step, _SMOKE_BUILD)
            elif rel in _TIERS and step.get("id") == "image":
                bad += _build_problems(where, step, _SMOKE_BUILD)
                if step.get("if") != _IF_IMAGE:
                    bad.append(f"{where} builds the image for an entry that names no {{image}}")
            else:
                bad.append(f"{where} reads the layer cache, and it builds no gateway image")
    if len(writers) != 1:
        bad.append(f"the layer cache has {len(writers)} writers, not image-cache.yml's one")
    return bad


def _smoke_problems(texts: dict[str, str]) -> list[str]:
    bad: list[str] = []
    single = yaml.safe_load(texts.get(_SINGLE, "")) or {}
    steps = list(((single.get("jobs") or {}).get("single-container") or {}).get("steps") or [])
    smokes = [s for s in steps if "docker_single_container_smoke" in str(s.get("run", ""))]
    if (
        len(smokes) != 1
        or smokes[0].get("run") != _SMOKE_RUN
        or smokes[0].get("env") != {"IMAGE": _IMAGE_ID}
    ):
        bad.append("the single-container smoke does not run the build's own content ID")
    for rel in _TIERS:
        data = yaml.safe_load(texts.get(rel, "")) or {}
        for job_id, _job, step in _steps(data):
            if "run-job" in str(step.get("run", "")):
                if (step.get("env") or {}).get("IMAGE") != _IMAGE_ID:
                    bad.append(f"{rel}:{job_id}'s run-job is handed no build's content ID")
                if '${IMAGE:+--image "$IMAGE"}' not in str(step.get("run")):
                    bad.append(f"{rel}:{job_id}'s run-job passes no --image")
    return bad


def _problems(texts: dict[str, str], dockerfile: str, tracked: list[str]) -> list[str]:
    return [
        *_writer_problems(texts),
        *_cache_problems(texts),
        *_smoke_problems(texts),
        *_filter_problems(texts, dockerfile, tracked),
    ]


def _dockerfile() -> str:
    return (REPO / jobs.GATEWAY_DOCKERFILE).read_text(encoding="utf-8")


def test_main_alone_writes_the_layer_cache() -> None:
    assert _problems(_texts(), _dockerfile(), _tracked()) == []


_SINGLE_FROM = "          cache-from: type=gha,scope=gateway-runtime\n\n      - name: Assert"
_TIER_FROM = "          cache-from: type=gha,scope=gateway-runtime\n      - name: the entry's"


@pytest.mark.parametrize(
    "rel, old, new, says",
    [
        (
            _SINGLE,
            _SINGLE_FROM,
            _SINGLE_FROM.replace("\n\n", f"\n          cache-to: {_CACHE_TO}\n\n", 1),
            "single-container writes the layer cache",
        ),
        (
            _WORKFLOWS + "merge.yml",
            _TIER_FROM,
            _TIER_FROM.replace("\n      -", f"\n          cache-to: {_CACHE_TO}\n      -", 1),
            "merge.yml:build writes the layer cache",
        ),
        (
            _WORKFLOWS + "pr.yml",
            _TIER_FROM,
            _TIER_FROM.replace("\n      -", "\n          cache-to: type=gha,mode=max\n      -", 1),
            "pr.yml:test writes the layer cache",
        ),
        (_WRITER, "  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n", "runs on"),
        (_WRITER, "    branches: [main]\n", "    branches: [main, next]\n", "not main's alone"),
        (_WRITER, "    if: ${{ github.ref == 'refs/heads/main' }}\n", "", "runs on refs/heads"),
        (
            _WRITER,
            "\npermissions:\n  contents: read\n",
            "\npermissions:\n  contents: write\n",
            "holds",
        ),
        (
            _WORKFLOWS + "release-artifacts.yml",
            "          provenance: mode=max\n",
            f"          provenance: mode=max\n          cache-from: {_CACHE_FROM}\n",
            "release-artifacts.yml:images reads the layer cache",
        ),
        (_SINGLE, '          github-token: ""\n', "", "single-container's build is given"),
        (
            _WRITER,
            '          github-token: ""\n',
            '          github-token: ""\n          build-args: TOKEN=x\n',
            "image-cache.yml:write's build is given",
        ),
        (
            _SINGLE,
            '          github-token: ""\n',
            "          github-token: ${{ secrets.GITHUB_TOKEN }}\n",
            "reads a secret",
        ),
        (
            _SINGLE,
            "          cache-from: type=gha,scope=gateway-runtime\n\n",
            "          cache-from: type=gha,scope=gateway-runtime-pr\n\n",
            "single-container's build is given",
        ),
        (
            _SINGLE,
            '--image "$IMAGE"',
            "--image personalclaw-gateway:single-container-ci",
            "own content ID",
        ),
        (
            _WORKFLOWS + "merge.yml",
            "          IMAGE: ${{ steps.image.outputs.imageid }}\n",
            "          IMAGE: personalclaw-gateway:ci\n",
            "handed no build's content ID",
        ),
        (
            _WORKFLOWS + "merge.yml",
            "        if: ${{ matrix.image }}\n        uses: docker/build-push-action",
            "        uses: docker/build-push-action",
            "for an entry that names no {image}",
        ),
        (_WRITER, "      - uv.lock\n", "", "image-cache.yml's path filter"),
        (
            _SINGLE,
            "      - src/personalclaw/**\n",
            "      - src/personalclaw/dashboard/**\n",
            "no path filter reaches it",
        ),
    ],
)
def test_a_planted_edit_of_the_layer_cache_fails(rel: str, old: str, new: str, says: str) -> None:
    texts = _texts()
    assert texts[rel].count(old) >= 1, f"the plant's anchor is gone: {old!r}"
    texts[rel] = texts[rel].replace(old, new, 1)
    found = _problems(texts, _dockerfile(), _tracked())
    assert any(says in p for p in found), found


def test_a_copied_path_no_filter_reaches_fails() -> None:
    """The Dockerfile copying a path neither filter lists: the image would ship it unbooted."""
    planted = _dockerfile() + "\nCOPY docs/ /opt/docs/\n"
    found = _problems(_texts(), planted, _tracked())
    assert any("copies docs/" in p and "no path filter reaches it" in p for p in found), found
