"""A workflow of its own that a ``paths:`` filter starts reaches every path its jobs read (#3945).

``nix.yml``, ``action-pins.yml`` and ``release-image-tooling.yml`` are no catalogue jobs, so the
planner never sees them: each starts on a pull request (and ``nix.yml`` on a push to main) only
when a changed path matches its ``paths:``. A filter is a path classifier of its own, and this
rail holds each to a census of what its jobs read, so a step that starts reading a new path
fails here until the filter names it:

- the workflow's own file, and each local action a step uses;
- each repository path a ``run:`` step names, and what each Python script among them names,
  followed into each script it names (``job_reads.python_reads``);
- for a step that runs ``nix`` (``flake check``, ``build``, ``eval``), the flake and its lock, the
  ``.nix`` files a step names, and every path a ``.nix`` file names relative to itself
  (``./python.nix``, ``../uv.lock``, ``${./scripts/nix_smoke.py}``), followed into each.

A step that builds the project (``uv build``) reads all of it, which no filter names: the workflow
says why its filter leaves that out (:data:`PROJECT_BUILDS`). Every trigger's ``paths:`` is the same
list, and every workflow with a filter is held here or by the rail :data:`HELD_ELSEWHERE` names.
GitHub reads a filter as globs, in order: ``*`` matches within a folder, ``**`` across folders, and
a later ``!pattern`` takes back what an earlier one matched.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from job_reads import forget, python_reads, tracked_dirs, tracked_paths
from rails_repo import REPO

_WORKFLOWS = ".github/workflows"
HELD = ("nix.yml", "action-pins.yml", "release-image-tooling.yml")

#: Workflows whose filter another rail holds, and that rail. None since #3915's flip retired
#: docker-single-container.yml, whose build is the catalogue's `image` entry.
HELD_ELSEWHERE: dict[str, str] = {}

#: The workflows with a step that builds the project, and why their filter names only part of it.
PROJECT_BUILDS = {
    "nix.yml": (
        "the next-release step builds this checkout's wheel to judge its dependency bounds against"
        " nixpkgs; the bounds live in pyproject.toml and uv.lock, which the filter names, and the"
        " weekly run builds whatever else changed"
    ),
}

_NIX_RUN = re.compile(r"(^|\s)nix\s+(flake|build|eval)\b", re.MULTILINE)
_NIX_PATH = re.compile(r"(?<![\w.$])(\.\.?/[\w./-]*[\w-])")
_PROJECT_BUILD = re.compile(r"(^|\s)uv\s+build\b", re.MULTILINE)
_WORD = re.compile(r"[^\s\"'=;|&()<>]+")


def _triggers(doc: Mapping[Any, Any]) -> Mapping[str, Any]:
    # YAML 1.1 reads a bare `on:` key as True.
    on = doc.get("on", doc.get(True, {}))
    return on if isinstance(on, dict) else {}


def filters(doc: Mapping[Any, Any]) -> dict[str, list[str]]:
    """Each trigger's ``paths:`` list."""
    return {
        name: list(spec["paths"])
        for name, spec in _triggers(doc).items()
        if isinstance(spec, dict) and spec.get("paths")
    }


def _steps(doc: Mapping[Any, Any]) -> list[Mapping[str, Any]]:
    return [step for job in doc.get("jobs", {}).values() for step in job.get("steps", [])]


def _nix_reads(root: Path, start: list[str]) -> dict[str, str]:
    tracked, folders = tracked_paths(root), tracked_dirs(root)
    found: dict[str, str] = {}
    todo = list(start)
    while todo:
        rel = todo.pop()
        if rel in found:
            continue
        found[rel] = "the flake reads it"
        if not rel.endswith(".nix") or rel not in tracked:
            continue
        for match in _NIX_PATH.finditer((root / rel).read_text(encoding="utf-8")):
            path = posixpath.normpath(posixpath.join(posixpath.dirname(rel), match.group(1)))
            if path in tracked or path in folders:
                todo.append(path)
    return found


def reads(root: Path, name: str, doc: Mapping[Any, Any]) -> dict[str, str]:
    """Each repository path the workflow ``name``'s jobs read, with why."""
    tracked, folders = tracked_paths(root), tracked_dirs(root)
    found = {f"{_WORKFLOWS}/{name}": "the workflow itself"}
    nix: list[str] = []
    runs_nix = False
    for step in _steps(doc):
        uses = str(step.get("uses", ""))
        if uses.startswith("./"):
            found[uses[2:].rstrip("/")] = "a step uses the action"
        run = str(step.get("run", ""))
        for word in _WORD.findall(run):
            word = word.rstrip("/")
            if word != "." and (word in tracked or word in folders):
                found.setdefault(word, "a step names it")
                if word.endswith(".nix"):
                    nix.append(word)
        runs_nix = runs_nix or bool(_NIX_RUN.search(run))
    if runs_nix:
        start = [p for p in ("flake.nix", "flake.lock") if p in tracked] + nix
        for path, why in _nix_reads(root, start).items():
            found.setdefault(path, why)
    scripts = sorted(p for p in found if p.endswith(".py") and p in tracked)
    for path, why in python_reads(root, scripts).items():
        found.setdefault(path, why)
    return found


def builds_the_project(doc: Mapping[Any, Any]) -> bool:
    return any(_PROJECT_BUILD.search(str(step.get("run", ""))) for step in _steps(doc))


def _glob(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def matches(patterns: list[str], path: str) -> bool:
    """Whether GitHub's filter starts the workflow for ``path``: patterns read in order, a later
    ``!pattern`` taking back what an earlier one matched."""
    hit = False
    for pattern in patterns:
        if pattern.startswith("!"):
            hit = hit and not _glob(pattern[1:]).fullmatch(path)
        else:
            hit = hit or bool(_glob(pattern).fullmatch(path))
    return hit


def unmatched(patterns: list[str], found: Mapping[str, str], root: Path = REPO) -> list[str]:
    """Each path of ``found`` the filter does not match, with why it is read. A folder counts as
    matched only when an arbitrary file under it is."""
    folders = tracked_dirs(root)
    out = []
    for path, why in sorted(found.items()):
        if not matches(patterns, f"{path}/x" if path in folders else path):
            out.append(f"{path} ({why})")
    return out


def _doc(name: str) -> Mapping[Any, Any]:
    return yaml.safe_load((REPO / _WORKFLOWS / name).read_text(encoding="utf-8"))


def problems(root: Path, name: str, doc: Mapping[Any, Any]) -> list[str]:
    lists = filters(doc)
    out: list[str] = []
    if not lists:
        return [f"{name} has no paths: filter"]
    if len({tuple(v) for v in lists.values()}) > 1:
        out.append(f"{name}: its triggers' paths: lists differ ({', '.join(sorted(lists))})")
    out += [
        f"{name} misses {m}"
        for m in unmatched(next(iter(lists.values())), reads(root, name, doc), root)
    ]
    if builds_the_project(doc) and name not in PROJECT_BUILDS:
        out.append(
            f"{name} builds the project, which its filter cannot name: say why in PROJECT_BUILDS"
        )
    return out


@pytest.mark.parametrize("name", HELD)
def test_each_filter_reaches_every_path_its_workflow_reads(name: str) -> None:
    found = problems(REPO, name, _doc(name))
    assert not found, "\n".join(found)


def test_every_workflow_with_a_filter_is_held() -> None:
    filtered = {
        p.name
        for p in (REPO / _WORKFLOWS).glob("*.yml")
        if filters(yaml.safe_load(p.read_text(encoding="utf-8")))
    }
    assert filtered == set(HELD) | set(HELD_ELSEWHERE), sorted(filtered)
    for rail in HELD_ELSEWHERE.values():
        assert (REPO / rail).is_file(), rail


def test_each_reasoned_project_build_is_still_one() -> None:
    for name in PROJECT_BUILDS:
        assert builds_the_project(_doc(name)), f"{name} builds the project no more: drop its reason"


def test_the_census_is_not_vacuous() -> None:
    nix = reads(REPO, "nix.yml", _doc("nix.yml"))
    for path in ("flake.lock", "nix/python.nix", "uv.lock", "scripts/nix_smoke.py"):
        assert path in nix, path
    pins = reads(REPO, "action-pins.yml", _doc("action-pins.yml"))
    assert ".github/actions" in pins and ".github/workflows" in pins, pins
    image = reads(REPO, "release-image-tooling.yml", _doc("release-image-tooling.yml"))
    assert "tools/ci/oci_layout.py" in image, image


# ── the planted controls ─────────────────────────────────────────────────────────────────────


def _without(name: str, pattern: str) -> Mapping[Any, Any]:
    doc = _doc(name)
    for spec in _triggers(doc).values():
        if isinstance(spec, dict) and spec.get("paths"):
            assert pattern in spec["paths"], (name, pattern)
            spec["paths"] = [p for p in spec["paths"] if p != pattern]
    return doc


@pytest.mark.parametrize(
    "name, dropped, missed",
    [
        ("nix.yml", "nix/**", "nix/python.nix"),
        ("nix.yml", "uv.lock", "uv.lock"),
        ("nix.yml", "scripts/nix_smoke.py", "scripts/nix_smoke.py"),
        ("nix.yml", ".github/workflows/nix.yml", ".github/workflows/nix.yml"),
        ("action-pins.yml", ".github/actions/**", ".github/actions"),
        ("action-pins.yml", "tools/ci/action_pins.py", "tools/ci/action_pins.py"),
        ("release-image-tooling.yml", "tools/ci/oci_layout.py", "tools/ci/oci_layout.py"),
    ],
)
def test_a_filter_missing_a_path_its_workflow_reads_fails(
    name: str, dropped: str, missed: str
) -> None:
    found = problems(REPO, name, _without(name, dropped))
    assert any(f.startswith(f"{name} misses {missed} ") for f in found), found


def test_triggers_whose_lists_differ_fail() -> None:
    doc = _doc("nix.yml")
    _triggers(doc)["push"]["paths"] = [*_triggers(doc)["push"]["paths"], "README.md"]
    assert "nix.yml: its triggers' paths: lists differ (pull_request, push)" in problems(
        REPO, "nix.yml", doc
    )


def _tree(tmp_path: Path, files: Mapping[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    forget()
    return tmp_path


_PLANTED = """
on:
  pull_request:
    paths: ["flake.nix", "flake.lock", "nix/**", ".github/workflows/w.yml"]
jobs:
  j:
    steps:
      - uses: ./.github/actions/setup
      - run: nix flake check
      - run: python3 scripts/new.py --check
"""


def test_a_new_read_of_a_step_fails_until_the_filter_names_it(tmp_path: Path) -> None:
    """The planted workflow's steps read, besides what its filter names: a local action, a
    script (and the file it names), and a lock a .nix file names relative to itself."""
    root = _tree(
        tmp_path,
        {
            ".github/workflows/w.yml": _PLANTED,
            ".github/actions/setup/action.yml": "runs: {using: composite, steps: []}\n",
            "flake.nix": "{ outputs = _: import ./nix/pkg.nix; }\n",
            "flake.lock": "{}\n",
            "nix/pkg.nix": "{ lock = builtins.readFile ../deps.lock; }\n",
            "deps.lock": "",
            "scripts/new.py": "from pathlib import Path\nX = Path('.') / 'data' / 'x.json'\n",
            "data/x.json": "{}\n",
        },
    )
    try:
        doc = yaml.safe_load(_PLANTED)
        assert problems(root, "w.yml", doc) == [
            "w.yml misses .github/actions/setup (a step uses the action)",
            "w.yml misses data/x.json (named by scripts/new.py)",
            "w.yml misses deps.lock (the flake reads it)",
            "w.yml misses scripts/new.py (a step names it)",
        ]
    finally:
        forget()


def test_a_project_build_without_a_reason_fails(tmp_path: Path) -> None:
    doc = yaml.safe_load(
        "on:\n  pull_request:\n    paths: ['.github/workflows/w.yml']\n"
        "jobs:\n  j:\n    steps:\n      - run: uv build --wheel\n"
    )
    root = _tree(tmp_path, {".github/workflows/w.yml": "x\n"})
    try:
        assert problems(root, "w.yml", doc) == [
            "w.yml builds the project, which its filter cannot name: say why in PROJECT_BUILDS"
        ]
    finally:
        forget()


def test_a_later_negation_takes_a_path_back() -> None:
    assert matches(["deploy/**"], "deploy/docker/agent-clis/package-lock.json")
    assert not matches(["deploy/**", "!deploy/docker/**"], "deploy/docker/x")
    assert matches(["!deploy/docker/**", "deploy/**"], "deploy/docker/x")
