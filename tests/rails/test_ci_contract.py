"""The CI contract: every CI and suite-structure property is one row, with a planted violation.

Each row names a property, a checker that returns the violations a tree has (an empty list when it
holds), a scaffold that builds the smallest tree the property holds on, and controls: plants that
must each make the checker fail on that scaffold, and plants that must leave it passing. A checker
that cannot fail on its own planted violation proves nothing about the real tree, so both run in
one test, the controls as subtests. Add a CI property as a row here, not as a new rail file.

The toolchain rows: a job runs on a named runner image and logs which one, on an exact Python,
uv, Node and npm, with every action by commit SHA and every container image by digest and
architecture. A runner label or image tag that moves would otherwise change what CI tests on a
day nothing in the repository changed. Their scaffold is a copy of the files they read, and each
control is one edit of one of them.
"""

from __future__ import annotations

import ast
import contextlib
import fnmatch
import functools
import itertools
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ci_masking
import count_ratchet_census
import getsource_census
import laid_tree
import local_actions
import private_walkers
import pytest
import quarantine
import security_census
import site_baseline
import source_pin_census
import timeout_marks
import yaml
from rails_repo import REPO

from tools.ci import breadth_evidence, catalogue
from tools.ci import jobs as run_jobs
from tools.ci import merge_decision
from tools.ci import plan as ci_plan
from tools.ci import rails_census, shards, shell_installs, supply_chain, verdict

#: An exact release, ``X.Y.Z``.
_EXACT = re.compile(r"\d+\.\d+\.\d+")

Tree = dict[str, str]
"""Repository-relative path to text: the files the toolchain rows read."""


def _read_paths(root: Path) -> list[Path]:
    return [
        *sorted(root.glob(".github/workflows/*.yml")),
        *local_actions.action_paths(root),
        *sorted(root.glob("deploy/docker/Dockerfile*")),
        # The shell scripts a workflow step runs (``sh <path>``), which a row reads as the step.
        *sorted(root.glob("tools/ci/*.sh")),
        *sorted(root.glob("scripts/*.sh")),
        *sorted(root.glob("deploy/*/*.sh")),
        root / "package.json",
        *sorted(root.glob("*/package.json")),
        root / "pyproject.toml",
        root / ".python-version",
        root / ".nvmrc",
        # What a tier workflow's planned matrix gives each entry (its Python, its cap).
        root / catalogue.CATALOGUE,
    ]


def _tree(root: Path) -> Tree:
    """The files under *root* the toolchain rows read (a missing version file reads as empty)."""
    return {
        path.relative_to(root).as_posix(): (
            path.read_text(encoding="utf-8") if path.is_file() else ""
        )
        for path in _read_paths(root)
    }


# ── readers over a tree ────────────────────────────────────────────────────────────────────


def _workflows(tree: Tree) -> Iterator[tuple[str, dict[str, Any]]]:
    for rel, text in tree.items():
        if rel.startswith(".github/workflows/"):
            yield rel.rsplit("/", 1)[1], yaml.safe_load(text)


def _jobs(tree: Tree) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for name, workflow in _workflows(tree):
        for job_id, job in (workflow.get("jobs") or {}).items():
            yield name, job_id, job


def _job_steps(tree: Tree, job: dict[str, Any]) -> list[dict[str, Any]]:
    """A job's steps as it runs them: each call of a local composite action is followed by the
    action's steps that call runs, its ``inputs`` filled in (``tests/rails/local_actions.py``).
    A call of a local action the tree does not hold raises, so no row passes without its steps."""
    actions = local_actions.action_files(tree)
    return [step for _index, step in local_actions.expand(job.get("steps") or [], actions)]


def _steps(tree: Tree) -> Iterator[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    for name, job_id, job in _jobs(tree):
        for step in _job_steps(tree, job):
            yield name, job_id, job, step


def _tier_entries(tree: Tree) -> tuple[list[catalogue.Job], list[str]]:
    """The catalogue's change- and merge-tier entries, whose steps run-job runs in the tier
    workflows, and why the catalogue cannot be read (each problem once)."""
    try:
        cat = catalogue.load(tree.get(catalogue.CATALOGUE, ""), catalogue.CATALOGUE)
    except catalogue.CatalogueError as exc:
        return [], [f"the catalogue does not load: {problem}" for problem in exc.problems]
    return [e for e in cat.jobs if {"change", "merge"} & set(e.tiers)], []


def _matrix_values(job: dict[str, Any], key: str) -> list[str]:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict):
        return []
    values = [str(v) for v in matrix.get(key) or []]
    values += [str(entry[key]) for entry in matrix.get("include") or [] if key in entry]
    return values


#: Each tier workflow (#3915) and the tier it runs.
_TIER_WORKFLOWS: dict[str, str] = {"pr.yml": "change", "merge.yml": "merge"}
#: A tier workflow's stage job's matrix: its own part of the plan's matrix (pr.yml, merge.yml).
_PLANNED_MATRIX = re.compile(r"\$\{\{ fromJSON\(needs\.changes\.outputs\.[\w-]+\) \}\}")


def _planned_tier(name: str, job: dict[str, Any]) -> str | None:
    """The tier whose plan a tier workflow's stage job runs a part of, or None for any other
    job: its matrix is the planner's, so each entry's values are the catalogue's for that tier."""
    matrix = (job.get("strategy") or {}).get("matrix")
    if isinstance(matrix, str) and _PLANNED_MATRIX.fullmatch(matrix):
        return _TIER_WORKFLOWS.get(name)
    return None


def _planned_pythons(tree: Tree, tier: str) -> list[str]:
    """Each Python the tier's catalogue jobs name, the values a planned ``matrix.python`` takes
    (a job with none sets up no uv)."""
    cat = catalogue.load(tree.get(catalogue.CATALOGUE, ""), catalogue.CATALOGUE)
    return sorted({j.python for j in cat.tier(tier) if j.python and j.python != "image"})


@functools.lru_cache(maxsize=8)
def _planned_entries(text: str, tier: str) -> tuple[dict[str, Any], ...]:
    """Every entry the tier's plan can give a stage job: the whole tier's matrix, from the
    catalogue's ``text``. A plan for a diff holds some of these, each in one of the stage jobs.
    Read once per catalogue and tier; no caller changes an entry."""
    cat = catalogue.load(text, catalogue.CATALOGUE)
    return tuple(ci_plan.matrix(cat, ci_plan.plan(cat, tier, None, overrides=()))["include"])


def _literal_matrix(tree: Tree, name: str, job: dict[str, Any]) -> dict[str, Any]:
    """``job`` with a tier workflow's planned matrix read as the literal entries its tier's plan
    can give it, so ``${{ matrix.<key> }}`` resolves entry by entry; any other job as it is. A
    catalogue that cannot be read leaves the matrix an expression, which a row refuses."""
    tier = _planned_tier(name, job)
    if tier is None:
        return job
    try:
        entries = _planned_entries(tree.get(catalogue.CATALOGUE, ""), tier)
    except (catalogue.CatalogueError, ValueError):
        return job
    return {**job, "strategy": {**job["strategy"], "matrix": {"include": list(entries)}}}


def _action(step: dict[str, Any]) -> str:
    return str(step.get("uses", "")).split("@", 1)[0]


def _matrix_legs(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Each leg of a job's matrix: the cross product of its lists, and each ``include`` entry as
    a leg of its own."""
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict):
        matrix = {}
    lists = {
        k: v for k, v in matrix.items() if k not in ("include", "exclude") and isinstance(v, list)
    }
    legs: list[dict[str, Any]] = [{}]
    for key, values in lists.items():
        legs = [{**leg, key: item} for leg in legs for item in values]
    return legs + [entry for entry in matrix.get("include") or [] if isinstance(entry, dict)]


def _legs(job: dict[str, Any], value: str) -> list[tuple[str, str]]:
    """Each leg's runner and *value* (a step's input), with ``${{ matrix.<key> }}`` resolved per
    leg (:func:`_matrix_legs`)."""
    legs = _matrix_legs(job)

    def resolve(text: str, leg: dict[str, Any]) -> str:
        return re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", lambda m: str(leg.get(m[1], "")), text)

    runs_on = str(job.get("runs-on", ""))
    return sorted({(resolve(runs_on, leg), resolve(value, leg)) for leg in legs})


def _dockerfiles(tree: Tree) -> Iterator[tuple[str, str]]:
    for rel, text in tree.items():
        if rel.startswith("deploy/docker/Dockerfile"):
            yield rel, text


# ── the rows ───────────────────────────────────────────────────────────────────────────────


#: CPython publishes macOS and Windows installers only while a minor takes bugfix releases: its
#: security releases are source only, and actions/setup-python's builds follow the installers
#: (its manifest lists 3.12.15 for Linux alone). The last patch with installers, per minor that
#: has gone security-only, so a setup-python leg on macOS or Windows never names a later one.
_LAST_INSTALLER_PATCH = {"3.12": 10}


def python_violations(tree: Tree) -> list[str]:
    """Every setup-uv and setup-python step names an exact Python: a literal ``X.Y.Z`` or a
    matrix value that is one. One patch per minor across the workflows, and the minor of
    ``.python-version`` is that file's exact patch. A setup-python leg on a macOS or Windows
    runner names a patch setup-python builds there (:data:`_LAST_INSTALLER_PATCH`)."""
    bad: list[str] = []
    pinned = tree[".python-version"].strip()
    if not _EXACT.fullmatch(pinned):
        bad.append(f".python-version is {pinned!r}, not an exact X.Y.Z")
    named: list[tuple[str, str]] = []
    for name, job_id, job, step in _steps(tree):
        if _action(step) not in ("astral-sh/setup-uv", "actions/setup-python"):
            continue
        where = f"{name}:{job_id}"
        value = str((step.get("with") or {}).get("python-version", ""))
        if value == "${{ matrix.python }}":
            values = _matrix_values(job, "python")
            tier = _planned_tier(name, job)
            if not values and tier is not None:
                values = _planned_pythons(tree, tier)
            if not values:
                bad.append(f"{where}: python-version reads a matrix.python the job does not set")
            named += [(f"{where} (matrix)", v) for v in values]
        elif not value:
            bad.append(f"{where}: {_action(step)} names no python-version")
        else:
            named.append((where, value))
        if _action(step) != "actions/setup-python":
            continue
        for runner, python in _legs(job, value):
            minor, _, patch = python.rpartition(".")
            last = _LAST_INSTALLER_PATCH.get(minor)
            if (
                last is not None
                and patch.isdigit()
                and int(patch) > last
                and re.search(r"macos|windows", runner, re.I)
            ):
                bad.append(
                    f"{where}: setup-python has no {runner} build of Python {python} "
                    f"(the last {minor} with installers is {minor}.{last}); "
                    "run that leg on .python-version's Python, or set it up with setup-uv"
                )
    by_minor: dict[str, set[str]] = {}
    for where, value in named:
        if not _EXACT.fullmatch(value):
            bad.append(f"{where}: Python {value!r} is not an exact X.Y.Z")
            continue
        by_minor.setdefault(value.rsplit(".", 1)[0], set()).add(value)
    for minor, patches in sorted(by_minor.items()):
        if len(patches) > 1:
            bad.append(f"Python {minor} is named as {sorted(patches)}: one patch per minor")
    minor = pinned.rsplit(".", 1)[0]
    if minor in by_minor and by_minor[minor] != {pinned}:
        bad.append(f"the workflows run {sorted(by_minor[minor])}, .python-version {pinned}")
    if not named:
        bad.append("no setup-uv or setup-python step names a Python, so this row read nothing")
    return bad


def uv_violations(tree: Tree) -> list[str]:
    """One uv release: every setup-uv step installs it exactly, the gateway image installs it,
    and ``[tool.uv] required-version`` is that release as a floor. No step installs an
    interpreter apart from setup-uv's ``python-version``."""
    bad: list[str] = []
    required = tomllib.loads(tree["pyproject.toml"]).get("tool", {}).get("uv", {})
    floor = re.fullmatch(r">=(\d+\.\d+\.\d+)", str(required.get("required-version", "")))
    if not floor:
        bad.append("pyproject.toml's [tool.uv] required-version is not a `>=X.Y.Z` floor")
    release = floor.group(1) if floor else ""
    seen: list[tuple[str, str]] = []
    for name, job_id, _job, step in _steps(tree):
        where = f"{name}:{job_id}"
        if _action(step) == "astral-sh/setup-uv":
            seen.append((where, str((step.get("with") or {}).get("version", ""))))
        if "uv python install" in str(step.get("run", "")):
            bad.append(f"{where}: `uv python install` (setup-uv's python-version names it)")
    for rel, text in _dockerfiles(tree):
        for match in re.finditer(r"^ARG UV_VERSION=(\S*)$", text, flags=re.MULTILINE):
            seen.append((rel, match.group(1)))
    for where, version in seen:
        if not _EXACT.fullmatch(version):
            bad.append(f"{where}: uv {version or '(newest)'} is not an exact X.Y.Z")
        elif version != release:
            bad.append(f"{where}: uv {version}, where required-version names {release}")
    if not seen:
        bad.append("no setup-uv step or image uv found, so this row read nothing")
    return bad


#: A shell line that invokes ``uv`` or ``uvx`` as a command, not one that only names it in prose.
_UV_COMMAND = re.compile(r"(?:^|[\s;&|(])uvx?\s+\S", flags=re.MULTILINE)


def _runs_uv(step: dict[str, Any]) -> bool:
    if _action(step) == "astral-sh/setup-uv":
        return True
    code = "\n".join(
        line for line in str(step.get("run", "")).splitlines() if not line.lstrip().startswith("#")
    )
    return bool(_UV_COMMAND.search(code))


def uv_python_preference_violations(tree: Tree) -> list[str]:
    """Every step that runs uv sees ``UV_PYTHON_PREFERENCE=only-managed``, from the workflow's
    ``env:`` or its job's, and no job or step sets it to anything else. uv's default prefers an
    installed Python that matches the requested version exactly over a download, so a runner
    image that ships that exact patch would run the job on the image's interpreter."""
    bad: list[str] = []
    found = 0
    key = "UV_PYTHON_PREFERENCE"
    for name, workflow in _workflows(tree):
        top = workflow.get("env") or {}
        for job_id, job in (workflow.get("jobs") or {}).items():
            job_env = job.get("env") or {}
            for step in _job_steps(tree, job):
                if not _runs_uv(step):
                    continue
                found += 1
                step_env = step.get("env") or {}
                value = next((env[key] for env in (step_env, job_env, top) if key in env), None)
                if value != "only-managed":
                    where = f"{name}:{job_id}:{step.get('name') or _action(step) or 'a uv step'}"
                    bad.append(f"{where}: {key} is {value!r}, not 'only-managed'")
    if not found:
        bad.append("found no step that runs uv, so this row read nothing")
    return bad


def node_violations(tree: Tree) -> list[str]:
    """``.nvmrc`` is an exact Node, every setup-node step reads it, and the image's Node
    stages run the same release."""
    bad: list[str] = []
    nvmrc = tree[".nvmrc"].strip()
    if not _EXACT.fullmatch(nvmrc):
        bad.append(f".nvmrc is {nvmrc!r}, not an exact X.Y.Z")
    steps = 0
    for name, job_id, _job, step in _steps(tree):
        if _action(step) != "actions/setup-node":
            continue
        steps += 1
        inputs = step.get("with") or {}
        if inputs.get("node-version-file") != ".nvmrc" or "node-version" in inputs:
            bad.append(f"{name}:{job_id}: setup-node does not read .nvmrc alone ({inputs})")
    images = 0
    for rel, text in _dockerfiles(tree):
        for tag in re.findall(r"\bnode:([^\s@]+)", text):
            images += 1
            if tag != nvmrc and not tag.startswith(f"{nvmrc}-"):
                bad.append(f"{rel}: node:{tag}, where .nvmrc names {nvmrc}")
    if not steps or not images:
        bad.append("found no setup-node step or no image Node stage, so this row read nothing")
    return bad


def npm_violations(tree: Tree) -> list[str]:
    """Every ``package.json`` a CI job runs ``npm ci`` from names an exact npm in
    ``packageManager``."""
    bad: list[str] = []
    dirs: set[str] = set()
    for _name, _job_id, _job, step in _steps(tree):
        if re.search(r"\bnpm ci\b", str(step.get("run", ""))):
            dirs.add(str(step.get("working-directory") or "."))
    if not dirs:
        return ["no job runs `npm ci`, so this row read nothing"]
    for folder in sorted(dirs):
        rel = "package.json" if folder == "." else f"{folder}/package.json"
        manager = str(json.loads(tree.get(rel, "{}")).get("packageManager", ""))
        if not re.fullmatch(r"npm@\d+\.\d+\.\d+", manager):
            bad.append(f"{rel}: packageManager is {manager!r}, not an exact npm@X.Y.Z")
    return bad


def runner_label_violations(tree: Tree) -> list[str]:
    """No job runs on a ``-latest`` label, named directly or through its matrix."""
    bad: list[str] = []
    for name, job_id, job in _jobs(tree):
        runs_on = job.get("runs-on", [])
        labels = [str(x) for x in (runs_on if isinstance(runs_on, list) else [runs_on])]
        labels += _matrix_values(job, "os")
        bad += [f"{name}:{job_id}: {label}" for label in labels if label.endswith("-latest")]
    return bad


def runner_identity_violations(tree: Tree) -> list[str]:
    """Every job logs the image it ran on: a ``Runner identity`` step of its own that reads the
    runner image's name and version, or the container's own ``/etc/os-release``. The step is
    never a local action's: an action loads only from the checkout, so one there logs nothing
    when the checkout fails."""
    bad: list[str] = []
    for name, job_id, job in _jobs(tree):
        if "steps" not in job:
            continue
        own = [s for s in job["steps"] if isinstance(s, dict)]
        runs = [str(s.get("run", "")) for s in own if s.get("name") == "Runner identity"]
        wants = ("/etc/os-release",) if "container" in job else ("ImageOS", "ImageVersion")
        if not any(all(word in run for word in wants) for run in runs):
            bad.append(f"{name}:{job_id}: no Runner identity step reading {' and '.join(wants)}")
        inside = [
            s
            for s in _job_steps(tree, job)
            if s.get("name") == "Runner identity" and not any(s is o for o in own)
        ]
        if inside:
            bad.append(
                f"{name}:{job_id}: a Runner identity step inside a local action it calls, "
                "which runs only after the checkout: the job's own step logs the image"
            )
    return bad


# ── the table ──────────────────────────────────────────────────────────────────────────────


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class Control:
    """One plant on the row's scaffold, and whether the checker must then fail."""

    name: str
    plant: Callable[[Path], None]
    fails: bool = True
    #: Text one violation must carry, for a plant that would fail the check for a second reason
    #: too: then only the named violation proves the clause under test bites.
    expect: str = ""


@dataclass(frozen=True)
class Row:
    name: str
    check: Callable[[Path], list[str]]
    scaffold: Callable[[Path], None]
    controls: tuple[Control, ...]
    #: What a run that checked the real tree records for its summary, if anything.
    on_tree: Callable[[pytest.FixtureRequest], None] | None = None


# ── The security marker ──────────────────────────────────────────────────────────────────────

_MARKED = "import pytest\n\npytestmark = pytest.mark.security\n\n\ndef test_it():\n    pass\n"


def _security_scaffold(root: Path) -> None:
    """A tree where the rule holds: every registry has a source, every named guard exists and is
    marked, and one marked module sits under tests/security/."""
    for registry in security_census.REGISTRIES:
        _write(root, "src/" + registry.replace(".", "/") + ".py", "")
    for rel in security_census.NAMED_MODULES:
        _write(root, rel, _MARKED)
    _write(root, "tests/security/test_marked.py", _MARKED)
    _write(root, "tests/test_unrelated.py", "def test_it():\n    pass\n")


def _security_check(root: Path) -> list[str]:
    return security_census.take(root).violations()


def _record_security_census(request: pytest.FixtureRequest) -> None:
    security_census.record(request.config, security_census.take(REPO))


def _remove(rel: str) -> Callable[[Path], None]:
    return lambda root: (root / rel).unlink()


_FIRST_GUARD = next(iter(security_census.NAMED_MODULES))

SECURITY_MARKER = Row(
    name="every fail-closed test module carries the security marker",
    check=_security_check,
    scaffold=_security_scaffold,
    controls=(
        Control(
            "clause 1: an unmarked module under tests/security fails",
            lambda root: _write(root, "tests/security/test_new.py", "def test_it():\n    pass\n"),
        ),
        Control(
            "clause 2: an unmarked module importing safety_flags inside a function fails",
            lambda root: _write(
                root,
                "tests/test_reads_a_flag.py",
                "def test_it():\n    from personalclaw.safety_flags import yes_or_no\n",
            ),
        ),
        Control(
            "clause 2: `from personalclaw.apps import permissions` is an import of the registry",
            lambda root: _write(
                root, "tests/test_routes.py", "from personalclaw.apps import permissions\n"
            ),
        ),
        Control(
            "clause 3: a name-pattern module with only another mark fails",
            lambda root: _write(
                root,
                "tests/test_apps_cannot_do_this.py",
                "import pytest\n\npytestmark = pytest.mark.asyncio\n",
            ),
        ),
        Control(
            "a misspelt marker on a module a clause names fails",
            lambda root: _write(
                root,
                "tests/test_guarded.py",
                "import pytest\n\nimport personalclaw.security\n\n"
                "pytestmark = [pytest.mark.securty]\n",
            ),
        ),
        Control(
            "a named guard that is gone fails, so a rename cannot shrink the set",
            _remove(_FIRST_GUARD),
        ),
        Control(
            "a registry whose source is gone fails",
            _remove("src/personalclaw/safety_flags.py"),
        ),
        Control(
            "the marker in a list beside another mark passes",
            lambda root: _write(
                root,
                "tests/test_apps_cannot_do_that.py",
                "import pytest\n\npytestmark = [pytest.mark.anyio, pytest.mark.security]\n",
            ),
            fails=False,
        ),
        Control(
            "a marked module no clause names passes: the set may grow by hand",
            lambda root: _write(root, "tests/test_opted_in.py", _MARKED),
            fails=False,
        ),
    ),
    on_tree=_record_security_census,
)


# ── The toolchain pins ──────────────────────────────────────────────────────────────────────


def _copy_toolchain_files(root: Path) -> None:
    """The toolchain rows' scaffold: a copy of the files they read, as the tree pins them."""
    for rel, text in _tree(REPO).items():
        _write(root, rel, text)


def _plant(
    name: str, rel: str, old: str, new: str, fails: bool = True, expect: str = ""
) -> Control:
    """A violation planted by one edit of one file; a plant whose text is gone fails loudly."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        assert old in text, f"the plant {name!r} found no {old!r} in {rel}"
        _write(root, rel, text.replace(old, new, 1))

    return Control(name, plant, fails, expect)


def _plants(
    name: str, rel: str, edits: tuple[tuple[str, str], ...], expect: str = "", fails: bool = True
) -> Control:
    """A violation planted by several edits of one file, each of which must find its text."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        for old, new in edits:
            assert old in text, f"the plant {name!r} found no {old!r} in {rel}"
            text = text.replace(old, new, 1)
        _write(root, rel, text)

    return Control(name, plant, fails, expect)


def _plant_in_job(
    name: str, rel: str, job_id: str, old: str, new: str, expect: str = "", fails: bool = True
) -> Control:
    """A violation planted by one edit inside one job of a workflow: the first *old* after the
    job's own ``  <job_id>:`` line, so text that every job of a family shares names one."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        start = text.find(f"\n  {job_id}:\n")
        at = text.find(old, start)
        assert start >= 0 and at >= 0, f"the plant {name!r} found no {old!r} in {rel}:{job_id}"
        _write(root, rel, text[:at] + new + text[at + len(old) :])

    return Control(name, plant, fails, expect)


def _plant_in_entry(
    name: str, rel: str, entry_id: str, old: str, new: str, expect: str = "", fails: bool = True
) -> Control:
    """A violation planted by one edit inside one ``[[job]]`` entry of the catalogue: the first
    *old* between the entry's ``id = "<entry_id>"`` line and the next table, whatever the entry's
    other lines say, so a head that changes those lines still takes the plant."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        _write(root, rel, _edit_in_entry(text, entry_id, old, new, name, rel))

    return Control(name, plant, fails, expect)


def _edit_in_entry(text: str, entry_id: str, old: str, new: str, name: str, rel: str) -> str:
    start = text.find(f'\nid = "{entry_id}"\n')
    end = text.find("\n[[", start + 1) if start >= 0 else -1
    end = len(text) if end < 0 else end
    at = text.find(old, start, end) if start >= 0 else -1
    assert at >= 0, f"the plant {name!r} found no {old!r} in {rel}'s {entry_id!r} entry"
    return text[:at] + new + text[at + len(old) :]


def test_a_plant_in_an_entry_finds_it_by_its_id_whatever_its_other_lines_say() -> None:
    """The entry plant edits its own entry only, with the entry's other lines as they are or
    changed, and fails loudly where the entry, or the text in it, is gone."""

    def catalogue_text(inputs: str, security: str = 'security = "security"\n') -> str:
        return (
            '[[job]]\nid = "audit"\nsecurity = "security"\n\n'
            f'[[job]]\nid = "browse-live"\nexternal_inputs = {inputs}\n{security}\n'
            '[[job]]\nid = "x"\nsecurity = "security"\n'
        )

    def mark(text: str) -> str:
        return _edit_in_entry(
            text, "browse-live", 'security = "security"', 'security = "ordinary"', "p", "c"
        )

    for inputs in ('["browser-download"]', '["browser-download", "apps-partner"]'):
        text = catalogue_text(inputs)
        assert mark(text) == catalogue_text(inputs, 'security = "ordinary"\n')
    text = catalogue_text('["browser-download"]')
    for gone in (text.replace('id = "browse-live"', 'id = "browse"'), catalogue_text("[]", "")):
        with pytest.raises(AssertionError, match="found no"):
            mark(gone)


def _plant_job_copy(name: str, rel: str, job_id: str, copy_id: str, expect: str = "") -> Control:
    """A violation planted by a copy of one job of a workflow under a new id, as a new leg made
    from an existing one would be: the job's own lines, up to the next job's."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        start = text.find(f"\n  {job_id}:\n")
        assert start >= 0, f"the plant {name!r} found no job {job_id} in {rel}"
        following = re.search(r"\n  [\w-]+:\n", text[start + 1 :])
        end = start + 1 + following.start() if following else len(text)
        copy = text[start:end].replace(f"\n  {job_id}:\n", f"\n  {copy_id}:\n", 1)
        _write(root, rel, text[:end] + copy + text[end:])

    return Control(name, plant, True, expect)


def _toolchain_row(
    name: str,
    check: Callable[[Tree], list[str]],
    controls: tuple[Control, ...],
    scaffold: Callable[[Path], None] = _copy_toolchain_files,
) -> Row:
    return Row(
        name=name,
        check=lambda root: check(_tree(root)),
        scaffold=scaffold,
        controls=controls,
    )


_IDENTITY_STEP = (
    "      - name: Runner identity\n"
    "        # The image this job ran on, so a runner image that moves is visible in the log.\n"
    '        run: echo "runner image ${ImageOS:?not a GitHub-hosted runner image}'
    ' ${ImageVersion:?no image version} on ${RUNNER_OS} ${RUNNER_ARCH}"\n'
)


def _pinned(rel: str, pattern: str) -> str:
    """The first match of *pattern* in *rel* as the tree has it today, so a plant follows a
    refresh (a Dependabot base move, a hand-moved container) instead of going stale. A pattern
    that matches nothing fails here, loudly, rather than leaving a plant with nothing to edit."""
    match = re.search(pattern, (REPO / rel).read_text(encoding="utf-8"), flags=re.M)
    assert match, f"no {pattern!r} in {rel}: the plants that edit it need a new anchor"
    return match.group(1)


_DIGEST_REF = r"(?:[\w.-]+/)*({name}:[\w.-]+@sha256:[0-9a-f]{{64}})"
#: A FROM line up to its image: the SPA stages build on the build platform, the one flag a FROM
#: here carries. The image after it is still matched whole, digest included.
_FROM = r"^FROM (?:--platform=\$BUILDPLATFORM )?"

#: The image references the digest plants edit, as the tree pins them today.
_UBUNTU = _pinned(
    ".github/workflows/breadth.yml", "container: " + _DIGEST_REF.format(name="ubuntu")
)
_NGINX = _pinned("deploy/docker/Dockerfile.web", _FROM + _DIGEST_REF.format(name="nginx"))
_PYTHON = _pinned("deploy/docker/Dockerfile.backend", _FROM + _DIGEST_REF.format(name="python"))
_NODE_BASE = _pinned("deploy/docker/Dockerfile.web", _FROM + _DIGEST_REF.format(name="node"))
#: The comment line naming the node base's arm64 manifest, newline included.
_NODE_ARM64 = _pinned(
    "deploy/docker/Dockerfile.web",
    r"(#\s+linux/arm64 sha256:[0-9a-f]{64}\n)(?=FROM (?:--platform=\$BUILDPLATFORM )?\S*node:)",
)
_NODE_AMD64 = _pinned(
    "deploy/docker/Dockerfile.web",
    r"#\s+linux/amd64 sha256:([0-9a-f]{64})\n#\s+linux/arm64 sha256:[0-9a-f]{64}\n"
    r"FROM (?:--platform=\$BUILDPLATFORM )?\S*node:",
)
#: The Node `.nvmrc` names, which the images' node tag must equal.
_NVMRC = (REPO / ".nvmrc").read_text(encoding="utf-8").strip()

# ── Per-test temporary folders are constant-time ─────────────────────────────────────────────
#
# `TempPathFactory.mktemp()` numbers its folder by scanning and sorting the whole temporary folder,
# so a call made for every test grows slower with every test its worker has run. A fixture that
# runs once per module or per session pays that once and may keep it; one that runs for every test
# (a function-scoped autouse fixture, and `tmp_path` itself) names its folder from a counter.

_CONFTEST = "tests/conftest.py"


def _fixture_call(decorator: ast.expr) -> ast.Call | None:
    """The `pytest.fixture(...)` call a decorator is, a bare `@pytest.fixture` as a call of none."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    named = (isinstance(target, ast.Attribute) and target.attr == "fixture") or (
        isinstance(target, ast.Name) and target.id == "fixture"
    )
    if not named:
        return None
    return decorator if isinstance(decorator, ast.Call) else ast.Call(target, [], [])


def _runs_for_every_test(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        call = _fixture_call(decorator)
        if call is None:
            continue
        options = {kw.arg: kw.value for kw in call.keywords}
        scope = options.get("scope")
        if isinstance(scope, ast.Constant) and scope.value != "function":
            return False
        if node.name == "tmp_path":
            return True
        autouse = options.get("autouse")
        return isinstance(autouse, ast.Constant) and autouse.value is True
    return False


def _per_test_mktemp_check(root: Path) -> list[str]:
    tree = ast.parse((root / _CONFTEST).read_text(encoding="utf-8"), filename=_CONFTEST)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _runs_for_every_test(node):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "mktemp":
                found.append(
                    f"{_CONFTEST}:{inner.lineno}: `{node.name}` runs for every test and calls "
                    "mktemp(), which scans the whole temporary folder: name the folder from a "
                    "counter under getbasetemp() instead"
                )
    return found


def _per_test_mktemp_scaffold(root: Path) -> None:
    _write(
        root,
        _CONFTEST,
        "import itertools\n\nimport pytest\n\n_N = itertools.count()\n\n\n"
        "@pytest.fixture(autouse=True)\n"
        "def _isolated(tmp_path_factory):\n"
        '    folder = tmp_path_factory.getbasetemp() / f"isolated-{next(_N)}"\n'
        "    folder.mkdir(mode=0o700)\n\n\n"
        "@pytest.fixture\n"
        "def tmp_path(tmp_path_factory):\n"
        '    path = tmp_path_factory.getbasetemp() / f"t{next(_N)}"\n'
        "    path.mkdir(mode=0o700)\n"
        "    return path\n",
    )


def _append_to_conftest(text: str) -> Callable[[Path], None]:
    def plant(root: Path) -> None:
        path = root / _CONFTEST
        path.write_text(path.read_text(encoding="utf-8") + "\n\n" + text, encoding="utf-8")

    return plant


PER_TEST_TEMPORARY_FOLDERS = Row(
    name="no fixture that runs for every test calls mktemp()",
    check=_per_test_mktemp_check,
    scaffold=_per_test_mktemp_scaffold,
    controls=(
        Control(
            "an autouse fixture that calls mktemp() fails",
            _append_to_conftest(
                "@pytest.fixture(autouse=True)\n"
                "def _planted(tmp_path_factory):\n"
                '    return tmp_path_factory.mktemp("planted")\n'
            ),
        ),
        Control(
            "a tmp_path fixture that calls mktemp() fails",
            _append_to_conftest(
                "@pytest.fixture\n"
                "def tmp_path(tmp_path_factory):\n"
                '    return tmp_path_factory.mktemp("t")\n'
            ),
        ),
        Control(
            "a module-scoped autouse fixture that calls mktemp() passes: it runs once",
            _append_to_conftest(
                '@pytest.fixture(autouse=True, scope="module")\n'
                "def _once(tmp_path_factory):\n"
                '    return tmp_path_factory.mktemp("once")\n'
            ),
            fails=False,
        ),
        Control(
            "a fixture a test asks for by name that calls mktemp() passes",
            _append_to_conftest(
                "@pytest.fixture\n"
                "def asked_for(tmp_path_factory):\n"
                '    return tmp_path_factory.mktemp("asked")\n'
            ),
            fails=False,
        ),
    ),
)


# ── The trust boundary (docs/maintainers/ci.md, "Trust boundary") ─────────────────────────────
#
# A trusted workflow is one GitHub runs from the default branch: `pull_request_target`,
# `workflow_run`, `schedule`, and pushes or dispatches on `main`. Candidate code is any job of a
# workflow a pull request or a feature-branch push runs, since GitHub takes that workflow from
# the candidate, and any workflow such a job calls.

_WORKFLOWS = ".github/workflows"
_PR_YML, _MERGE_YML = f"{_WORKFLOWS}/pr.yml", f"{_WORKFLOWS}/merge.yml"
_TRUSTED_TRIGGERS = {"pull_request_target", "workflow_run"}
#: Where a trusted job could name the candidate's code: its head, its ref, the run's head, and the
#: test-merge commit GitHub builds from the candidate (`pull_request.merge_commit_sha`), and
#: `event.after`, which on a `pull_request_target` synchronize is the candidate's new head.
_PR_REF_MARKERS = (
    "pull_request.head",
    "workflow_run.head",
    "head_ref",
    "refs/pull/",
    "/pull/",
    "merge_commit_sha",
    "event.after",
)
_PUBLISHING_SCOPES = {"contents", "packages", "id-token", "attestations", "deployments"}
_RELEASE_WORKFLOW = "release.yml"
#: The environments a workflow other than release.yml may name, by workflow and job. Each deploys
#: only from main (the settings report reads that policy back every week), and its workflow runs
#: only on a schedule or a dispatch, so no candidate's run can name it. `settings-report` holds
#: the report's read-only credential; `ci-controller` the release controller's App key, which
#: release-tag.yml uses to create a `v*` tag and merge-verdict.yml to post the merge verdict.
#: None of these jobs may hold a publishing permission, as no job outside release.yml may.
_MAIN_ONLY_ENVIRONMENTS = {
    ("settings-report.yml", "report"): "settings-report",
    ("release-tag.yml", "tag"): "ci-controller",
    ("merge-verdict.yml", "post"): "ci-controller",
    ("merge-verdict.yml", "contract"): "ci-controller",
}
_SCHEDULED_ONLY = {"schedule", "workflow_dispatch"}
#: The main-only jobs that run on another trigger: the merge verdict's poster and its trusted
#: run's maker, in a listener of runs (`workflow_run`) and checks (`check_run`), with a
#: schedule, and a dispatch (a contract run's wake), that runs on nothing else. GitHub runs the
#: first three from the default branch; a dispatch on another branch runs that branch's file, but
#: its jobs' condition runs them on main alone and the environment deploys only from main, as for
#: the scheduled jobs above. So no candidate's run can name the environment through them either.
#: What a listener job's condition must hold a dispatch to: main alone. A dispatch on another
#: branch runs that branch's copy of the file, and the environment deploys only from main.
_ON_MAIN = "github.ref == 'refs/heads/main'"
_LISTENER_ONLY = {
    ("merge-verdict.yml", "post"): {"workflow_run", "check_run", "schedule", "workflow_dispatch"},
    ("merge-verdict.yml", "contract"): {
        "workflow_run",
        "check_run",
        "schedule",
        "workflow_dispatch",
    },
}
#: The one publishing permission a workflow other than release.yml may hold, by workflow and job:
#: release-push-proof.yml runs release.yml's own image push to a scratch package no release names,
#: and deletes it (#3911; tests/rails/test_release_push_proof_tree.py holds that). It may hold
#: `packages: write` alone, on a dispatch alone, in a job that runs on main alone.
_SCRATCH_PUBLISHERS = {("release-push-proof.yml", "push"): ["packages"]}


def _trust_workflows(root: Path) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for path in sorted((root / _WORKFLOWS).glob("*.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        found.append((path.name, data if isinstance(data, dict) else {}))
    return found


def _triggers(workflow: dict) -> dict[str, object]:
    # PyYAML reads the bare key `on` as the boolean True.
    raw = workflow.get("on", workflow.get(True))
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {str(name): None for name in raw}
    return {str(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}


def _trust_jobs(workflow: dict) -> dict[str, dict]:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {str(name): job for name, job in jobs.items() if isinstance(job, dict)}


def _main_or_tags_only(filters: object) -> bool:
    """A push filter that admits only `main` and tags, so the run is not a candidate's."""
    if not isinstance(filters, dict):
        return False
    branches = filters.get("branches")
    if branches is None:
        return "tags" in filters and "branches-ignore" not in filters
    return isinstance(branches, list) and set(branches) <= {"main"}


def _runs_candidate_code(workflows: list[tuple[str, dict]]) -> set[str]:
    """The workflows whose jobs run a candidate's code."""
    candidates = set()
    for name, wf in workflows:
        triggers = _triggers(wf)
        if "pull_request" in triggers or (
            "push" in triggers and not _main_or_tags_only(triggers["push"])
        ):
            candidates.add(name)
    changed = True
    while changed:
        changed = False
        for name, wf in workflows:
            if name not in candidates:
                continue
            for job in _trust_jobs(wf).values():
                callee = str(job.get("uses", ""))
                if callee.startswith(f"./{_WORKFLOWS}/"):
                    called = callee.split("/")[-1].split("@")[0]
                    if called not in candidates:
                        candidates.add(called)
                        changed = True
    return candidates


def _trust_steps(job: dict) -> list[dict]:
    steps = job.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _checks_out_candidate(step: dict) -> str | None:
    """Why this step puts a candidate's code on disk, or None."""
    with_ = step.get("with")
    if str(step.get("uses", "")).startswith("actions/checkout@") and isinstance(with_, dict):
        for key in ("ref", "repository"):
            value = str(with_.get(key, ""))
            if any(marker in value for marker in _PR_REF_MARKERS):
                return f"checks out {key}: {value}"
    run = str(step.get("run", ""))
    if "gh pr checkout" in run:
        return "runs gh pr checkout"
    for line in run.splitlines():
        if re.search(r"\bgit\b.*\b(fetch|checkout|switch|pull|worktree)\b", line) and any(
            marker in line for marker in _PR_REF_MARKERS
        ):
            return f"runs {line.strip()}"
    return None


def _no_trusted_workflow_checks_out_candidate_code(root: Path) -> list[str]:
    found: list[str] = []
    for name, wf in _trust_workflows(root):
        if not _TRUSTED_TRIGGERS & set(_triggers(wf)):
            continue
        for job_name, job in _trust_jobs(wf).items():
            for step in _trust_steps(job):
                why = _checks_out_candidate(step)
                if why:
                    found.append(f"{name}:{job_name} runs from the default branch and {why}")
    return found


def _grants_write(permissions: object) -> bool:
    if permissions is None:
        return True  # the repository's default, which a workflow file cannot show
    if isinstance(permissions, str):
        return permissions != "read-all"
    return not isinstance(permissions, dict) or "write" in permissions.values()


def _no_candidate_job_holds_a_write_or_a_secret(root: Path) -> list[str]:
    workflows = _trust_workflows(root)
    candidates = _runs_candidate_code(workflows)
    found: list[str] = []
    for name, wf in workflows:
        if name not in candidates:
            continue
        default = wf.get("permissions")
        for job_name, job in _trust_jobs(wf).items():
            held = job.get("permissions", default)
            if _grants_write(held):
                found.append(f"{name}:{job_name} runs candidate code holding {held!r}")
            if job.get("secrets") is not None:
                found.append(f"{name}:{job_name} passes secrets on ({job['secrets']!r})")
            # Comments are gone once the YAML is parsed, so a match here is a real reference.
            if "secrets." in json.dumps(job):
                found.append(f"{name}:{job_name} runs candidate code and references a secret")
    return found


def _publishing(permissions: object) -> list[str]:
    if permissions == "write-all":
        return ["write-all"]
    if not isinstance(permissions, dict):
        return []
    return sorted(k for k, v in permissions.items() if k in _PUBLISHING_SCOPES and v == "write")


def _only_the_release_controller_publishes(root: Path) -> list[str]:
    found: list[str] = []
    for name, wf in _trust_workflows(root):
        if name == _RELEASE_WORKFLOW:
            continue
        held = _publishing(wf.get("permissions"))
        if held:
            found.append(f"{name} grants every job {held}")
        for job_name, job in _trust_jobs(wf).items():
            held = _publishing(job.get("permissions"))
            scratch = _SCRATCH_PUBLISHERS.get((name, job_name))
            if held and scratch is not None and held == scratch:
                if set(_triggers(wf)) != {"workflow_dispatch"}:
                    found.append(
                        f"{name}:{job_name} holds {held} in a workflow not dispatched alone"
                    )
                if "github.ref == 'refs/heads/main'" not in str(job.get("if", "")):
                    found.append(f"{name}:{job_name} holds {held} in a job not run on main alone")
            elif held:
                found.append(f"{name}:{job_name} holds {held} outside {_RELEASE_WORKFLOW}")
            environment = job.get("environment")
            if environment is None:
                continue
            named = environment.get("name") if isinstance(environment, dict) else environment
            allowed = _MAIN_ONLY_ENVIRONMENTS.get((name, job_name))
            if allowed is None or named != allowed:
                found.append(
                    f"{name}:{job_name} deploys to {environment!r} outside {_RELEASE_WORKFLOW}"
                )
            elif not set(_triggers(wf)) <= _LISTENER_ONLY.get((name, job_name), _SCHEDULED_ONLY):
                runs_on = _LISTENER_ONLY.get((name, job_name), _SCHEDULED_ONLY)
                found.append(
                    f"{name}:{job_name} names the main-only environment {named!r} in a workflow "
                    f"that runs on {sorted(set(_triggers(wf)) - runs_on)}"
                )
            elif (
                (name, job_name) in _LISTENER_ONLY
                and "workflow_dispatch" in _triggers(wf)
                and _ON_MAIN not in str(job.get("if", ""))
            ):
                found.append(
                    f"{name}:{job_name} names the main-only environment {named!r} on a dispatch"
                    " its condition does not hold to main"
                )
    return found


_CANDIDATE_WORKFLOW = """\
name: CI
on:
  pull_request:
  push:
    branches-ignore: [main]
permissions:
  contents: read
jobs:
  lint:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      pull-requests: read
    steps:
      - uses: actions/checkout@v7
      - run: make lint
        env:
          GH_TOKEN: ${{ github.token }}
"""

_FEEDBACK_WORKFLOW = """\
name: PR feedback
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
permissions:
  contents: read
  pull-requests: write
jobs:
  relay:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
        with:
          sparse-checkout: scripts/relay.mjs
"""

_DCO_WORKFLOW = """\
name: DCO
on:
  pull_request_target:
    types: [opened]
jobs:
  dco:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      pull-requests: write
    steps:
      - run: gh api "repos/$REPO/pulls/$PR/commits"
        env:
          PR: ${{ github.event.pull_request.number }}
"""

_RELEASE = """\
name: Release
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
jobs:
  pypi:
    runs-on: ubuntu-24.04
    environment: release
    permissions:
      id-token: write
    steps:
      - run: echo publish
        env:
          TOKEN: ${{ secrets.WEBSITE_DISPATCH_TOKEN }}
"""

_SETTINGS_REPORT = """\
name: Settings report
on:
  schedule:
    - cron: "41 7 * * 1"
  workflow_dispatch:
permissions:
  contents: read
jobs:
  report:
    runs-on: ubuntu-24.04
    environment: settings-report
    permissions:
      contents: read
      issues: write
    steps:
      - run: python3 -m tools.ci settings-report
        env:
          SETTINGS_READ_TOKEN: ${{ secrets.SETTINGS_READ_TOKEN }}
"""


def _settings_report(old: str = "", new: str = "") -> Callable[[Path], None]:
    """Add the settings report's workflow, with *old* replaced by *new* in it."""

    def plant(root: Path) -> None:
        text = _SETTINGS_REPORT
        if old:
            assert old in text, f"the plant's anchor is not in the settings report: {old!r}"
            text = text.replace(old, new, 1)
        _write(root, f"{_WORKFLOWS}/settings-report.yml", text)

    return plant


_RELEASE_TAG = """\
name: Release tag
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  tag:
    runs-on: ubuntu-24.04
    environment: ci-controller
    permissions:
      contents: read
    steps:
      - uses: actions/create-github-app-token@v3
        with:
          private-key: ${{ secrets.CONTROLLER_APP_KEY }}
"""


def _release_tag(old: str = "", new: str = "") -> Callable[[Path], None]:
    """Add the tagging workflow, with *old* replaced by *new* in it."""

    def plant(root: Path) -> None:
        text = _RELEASE_TAG.replace(old, new, 1) if old else _RELEASE_TAG
        assert not old or old in _RELEASE_TAG, f"the plant's anchor is not there: {old!r}"
        _write(root, f"{_WORKFLOWS}/release-tag.yml", text)

    return plant


_MERGE_VERDICT = """\
name: Merge verdict
on:
  workflow_run:
    workflows: ["Merge"]
    types: [completed]
permissions:
  contents: read
jobs:
  post:
    runs-on: ubuntu-24.04
    environment: ci-controller
    permissions:
      contents: read
      actions: read
    steps:
      - uses: actions/create-github-app-token@v3
        with:
          private-key: ${{ secrets.CONTROLLER_APP_KEY }}
"""


def _merge_verdict(old: str = "", new: str = "") -> Callable[[Path], None]:
    """Add the merge verdict's poster, with *old* replaced by *new* in it."""

    def plant(root: Path) -> None:
        text = _MERGE_VERDICT.replace(old, new, 1) if old else _MERGE_VERDICT
        assert not old or old in _MERGE_VERDICT, f"the plant's anchor is not there: {old!r}"
        _write(root, f"{_WORKFLOWS}/merge-verdict.yml", text)

    return plant


_PUSH_PROOF = """\
name: Release push proof
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  push:
    if: ${{ github.ref == 'refs/heads/main' && github.repository == 'PersonalClaw/PersonalClaw' }}
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      actions: read
      packages: write
    steps:
      - run: ./push.sh
"""


def _push_proof(old: str = "", new: str = "") -> Callable[[Path], None]:
    """Add the GHCR push proof, with *old* replaced by *new* in it."""

    def plant(root: Path) -> None:
        assert not old or old in _PUSH_PROOF, f"the plant's anchor is not there: {old!r}"
        text = _PUSH_PROOF.replace(old, new, 1) if old else _PUSH_PROOF
        _write(root, f"{_WORKFLOWS}/release-push-proof.yml", text)

    return plant


_MAIN_ONLY = """\
name: Full
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 7 * * *"
permissions:
  contents: read
jobs:
  smoke:
    runs-on: ubuntu-24.04
    steps:
      - run: ./smoke.sh
        env:
          CREDENTIAL: ${{ secrets.SMOKE_CREDENTIAL }}
"""


def _trust_scaffold(root: Path) -> None:
    """Today's shapes: a candidate CI, two trusted relays, a release and a main-only run."""
    _write(root, f"{_WORKFLOWS}/ci.yml", _CANDIDATE_WORKFLOW)
    _write(root, f"{_WORKFLOWS}/pr-feedback.yml", _FEEDBACK_WORKFLOW)
    _write(root, f"{_WORKFLOWS}/dco.yml", _DCO_WORKFLOW)
    _write(root, f"{_WORKFLOWS}/release.yml", _RELEASE)
    _write(root, f"{_WORKFLOWS}/main-only.yml", _MAIN_ONLY)


def _replace(workflow: str, old: str, new: str) -> Callable[[Path], None]:
    def plant(root: Path) -> None:
        path = root / _WORKFLOWS / workflow
        text = path.read_text(encoding="utf-8")
        assert old in text, f"the plant's anchor is not in {workflow}: {old!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return plant


def _both(*plants: Callable[[Path], None]) -> Callable[[Path], None]:
    def plant(root: Path) -> None:
        for each in plants:
            each(root)

    return plant


def _nothing(root: Path) -> None:
    """The scaffold as it is: a positive control for a shape it already holds."""


_SPARSE = "          sparse-checkout: scripts/relay.mjs\n"


def _feedback_checkout(key: str, value: str) -> Callable[[Path], None]:
    return _replace("pr-feedback.yml", _SPARSE, f"          {key}: {value}\n")


def _feedback_run(script: str) -> Callable[[Path], None]:
    return _replace("pr-feedback.yml", _SPARSE, f"{_SPARSE}      - run: {script}\n")


def _ci_checkout(key: str, value: str) -> Callable[[Path], None]:
    checkout = "      - uses: actions/checkout@v7\n"
    return _replace("ci.yml", checkout, f"{checkout}        with:\n          {key}: {value}\n")


def _ci_job(text: str) -> Callable[[Path], None]:
    return _replace("ci.yml", "jobs:\n", f"jobs:\n{text}")


def _add_to_job(workflow: str, text: str) -> Callable[[Path], None]:
    runs_on = "    runs-on: ubuntu-24.04\n"
    return _replace(workflow, runs_on, f"{runs_on}{text}")


_LINT_PERMISSIONS = "    permissions:\n      contents: read\n      pull-requests: read\n"
_ON_EVERY_PULL_REQUEST = "on:\n  pull_request: {}\n"


def _flipped(rel: str) -> Callable[[Path], None]:
    """The tree's pr.yml or merge.yml, which run on every pull request since #3915's flip."""

    def plant(root: Path) -> None:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert (
            text.count(_ON_EVERY_PULL_REQUEST) == 1
        ), f"{rel} no longer runs on every pull request"
        _write(root, rel, text)

    return plant


_CALLS_REUSABLE = "  called:\n    uses: ./.github/workflows/reusable.yml\n"

NO_TRUSTED_CHECKOUT_OF_CANDIDATE_CODE = Row(
    name="no pull_request_target or workflow_run workflow checks out candidate code",
    check=_no_trusted_workflow_checks_out_candidate_code,
    scaffold=_trust_scaffold,
    controls=(
        Control(
            "ref: the pull request's head sha fails",
            _feedback_checkout("ref", "${{ github.event.pull_request.head.sha }}"),
        ),
        Control(
            "ref: the workflow run's head sha fails",
            _feedback_checkout("ref", "${{ github.event.workflow_run.head_sha }}"),
        ),
        Control(
            "ref: the workflow run's head branch, a PR run's as a CI run's, fails",
            _feedback_checkout("ref", "${{ github.event.workflow_run.head_branch }}"),
        ),
        Control("ref: refs/pull/<n>/merge fails", _feedback_checkout("ref", "refs/pull/7/merge")),
        Control("ref: github.head_ref fails", _feedback_checkout("ref", "${{ github.head_ref }}")),
        Control(
            "ref: the pull request's test-merge commit under pull_request_target fails",
            _replace(
                "dco.yml",
                "    steps:\n",
                "    steps:\n      - uses: actions/checkout@v7\n        with:\n"
                "          ref: ${{ github.event.pull_request.merge_commit_sha }}\n",
            ),
        ),
        Control(
            "ref: the synchronize event's new head (event.after) fails",
            _replace(
                "dco.yml",
                "    steps:\n",
                "    steps:\n      - uses: actions/checkout@v7\n        with:\n"
                "          ref: ${{ github.event.after }}\n",
            ),
        ),
        Control(
            "a git fetch of the test-merge commit under pull_request_target fails",
            _replace(
                "dco.yml",
                "    steps:\n",
                "    steps:\n      - run: git fetch origin "
                '"${{ github.event.pull_request.merge_commit_sha }}"\n',
            ),
        ),
        Control(
            "repository: the run's head repository fails",
            _feedback_checkout(
                "repository", "${{ github.event.workflow_run.head_repository.full_name }}"
            ),
        ),
        Control("gh pr checkout fails", _feedback_run("gh pr checkout 7")),
        Control(
            "a git fetch of the pull request's ref fails",
            _feedback_run("git fetch origin refs/pull/7/head && git checkout FETCH_HEAD"),
        ),
        Control(
            "the head checkout under pull_request_target fails",
            _replace(
                "dco.yml",
                "    steps:\n",
                "    steps:\n      - uses: actions/checkout@v7\n        with:\n"
                "          ref: ${{ github.event.pull_request.head.sha }}\n",
            ),
        ),
        Control(
            "a candidate workflow checking out its own head passes",
            _ci_checkout("ref", "${{ github.event.pull_request.head.sha }}"),
            fails=False,
        ),
        Control(
            "reading the run's id from the event passes",
            _feedback_run('echo "${{ github.event.workflow_run.id }}"'),
            fails=False,
        ),
    ),
)

NO_CANDIDATE_JOB_HOLDS_A_WRITE_OR_A_SECRET = Row(
    name="no job that runs candidate code holds a write permission or a secret",
    check=_no_candidate_job_holds_a_write_or_a_secret,
    scaffold=_trust_scaffold,
    controls=(
        Control(
            "a job-level write grant fails",
            _replace("ci.yml", "      pull-requests: read\n", "      pull-requests: write\n"),
        ),
        Control(
            "permissions: write-all fails",
            _replace("ci.yml", _LINT_PERMISSIONS, "    permissions: write-all\n"),
        ),
        Control(
            "a job with no permissions anywhere fails: the default is not known here",
            _both(
                _replace("ci.yml", _LINT_PERMISSIONS, ""),
                _replace("ci.yml", "permissions:\n  contents: read\njobs:", "jobs:"),
            ),
        ),
        Control(
            "a secrets. reference in env: fails",
            _replace("ci.yml", "${{ github.token }}", "${{ secrets.SMOKE_CREDENTIAL }}"),
        ),
        Control(
            "a secrets. reference in with: fails",
            _ci_checkout("token", "${{ secrets.WEBSITE_DISPATCH_TOKEN }}"),
        ),
        Control("secrets: inherit fails", _ci_job(f"{_CALLS_REUSABLE}    secrets: inherit\n")),
        Control(
            "a write in a workflow a candidate calls fails",
            _both(
                _ci_job(_CALLS_REUSABLE),
                lambda root: _write(
                    root,
                    f"{_WORKFLOWS}/reusable.yml",
                    "on: workflow_call\njobs:\n  it:\n    runs-on: ubuntu-24.04\n"
                    "    permissions:\n      contents: write\n    steps:\n      - run: true\n",
                ),
            ),
        ),
        Control(
            "a secret in a feature-branch push workflow fails",
            lambda root: _write(
                root,
                f"{_WORKFLOWS}/branch.yml",
                "on:\n  push:\n    branches-ignore: [main]\njobs:\n  it:\n"
                "    runs-on: ubuntu-24.04\n    permissions:\n      contents: read\n"
                "    steps:\n      - run: echo ${{ secrets.ANY }}\n",
            ),
        ),
        Control("a secret in a workflow that runs only on main passes", _nothing, fails=False),
        Control(
            "pr.yml on every pull request, as the flip runs it, passes",
            _flipped(_PR_YML),
            fails=False,
        ),
        Control(
            "merge.yml on every pull request, as the flip runs it, passes",
            _flipped(_MERGE_YML),
            fails=False,
        ),
        Control(
            "pr.yml on every pull request with a secret in a step fails",
            _both(
                _flipped(_PR_YML),
                _replace(
                    "pr.yml",
                    "GH_TOKEN: ${{ github.token }}",
                    "GH_TOKEN: ${{ secrets.CONTROLLER_APP_KEY }}",
                ),
            ),
            expect="pr.yml",
        ),
        Control(
            "merge.yml on every pull request with a write token fails",
            _both(
                _flipped(_MERGE_YML),
                _replace(
                    "merge.yml",
                    "      pull-requests: read   # `commits/{sha}/pulls`",
                    "      pull-requests: write   # `commits/{sha}/pulls`",
                ),
            ),
            expect="merge.yml",
        ),
        Control(
            "permissions: {} passes",
            _replace("ci.yml", _LINT_PERMISSIONS, "    permissions: {}\n"),
            fails=False,
        ),
    ),
)

#: The workflow that runs main's CI contract over a head's definitions (#4584 step 4). It runs the
#: head's code, so it holds nothing worth taking and writes nothing another run reads.
_CONTRACT_YML = "contract.yml"
#: The one job of contract.yml that may hold a permission of its own: ``wake``, which, once the
#: contract has run, dispatches the poster on main for the head. It holds ``actions: write``
#: alone and runs nothing of the head: no action (so no checkout), no git, uv or python.
_CONTRACT_WAKE, _CONTRACT_WAKE_HOLDS = "wake", {"actions": "write"}
_RUNS_THE_HEAD = re.compile(r"\b(?:git|uv|uvx|python3?|pip|make|npm|npx|node)\b")
#: Actions that save to the Actions cache when their step ends.
_CACHE_SAVERS = ("actions/cache@", "actions/cache/save@")


def _contract_run_holds_nothing(root: Path) -> list[str]:
    """contract.yml holds ``contents: read`` and nothing else, reads no secret, deploys to no
    environment and saves nothing to the Actions cache (a restore is fine). Its one exception is
    ``wake`` (:data:`_CONTRACT_WAKE`), which holds ``actions: write`` alone and runs nothing of
    the head."""
    path = root / _WORKFLOWS / _CONTRACT_YML
    if not path.is_file():
        return [f"{_CONTRACT_YML} is missing, so nothing pins what main's contract run holds"]
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    bad: list[str] = []
    if data.get("permissions") != {"contents": "read"}:
        bad.append(f"{_CONTRACT_YML} holds {data.get('permissions')!r}, not contents: read alone")
    if "secrets." in text or "secrets: inherit" in text:
        bad.append(f"{_CONTRACT_YML} reads a secret")
    for job_id, job in (data.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        if "permissions" in job and (job_id, job["permissions"]) != (
            _CONTRACT_WAKE,
            _CONTRACT_WAKE_HOLDS,
        ):
            bad.append(f"{_CONTRACT_YML} job {job_id} sets its own permissions")
        elif "permissions" in job:
            steps = [st for st in job.get("steps") or [] if isinstance(st, dict)]
            if any("uses" in st for st in steps) or "uses" in job:
                bad.append(f"{_CONTRACT_YML} job {job_id} holds actions: write and runs an action")
            if any(_RUNS_THE_HEAD.search(str(st.get("run", ""))) for st in steps):
                bad.append(f"{_CONTRACT_YML} job {job_id} holds actions: write and runs a tool")
        if "environment" in job:
            bad.append(f"{_CONTRACT_YML} job {job_id} deploys to an environment")
        for step in job.get("steps") or []:
            uses = str(step.get("uses", "")) if isinstance(step, dict) else ""
            given = (step.get("with") or {}) if isinstance(step, dict) else {}
            if uses.startswith(_CACHE_SAVERS):
                bad.append(f"{_CONTRACT_YML} job {job_id} saves to the Actions cache ({uses})")
            elif uses.startswith("astral-sh/setup-uv@") and given.get("enable-cache") is not False:
                bad.append(f"{_CONTRACT_YML} job {job_id}'s setup-uv saves its cache")
            elif uses.startswith("actions/setup-node@") and given.get("cache"):
                bad.append(f"{_CONTRACT_YML} job {job_id}'s setup-node saves its cache")
            elif uses.startswith("./.github/actions/"):
                bad.append(f"{_CONTRACT_YML} job {job_id} runs a local action, which may cache")
    return bad


_CONTRACT_SCAFFOLD = (
    "name: Contract\non:\n  workflow_dispatch:\n    inputs:\n      head:\n"
    "        required: true\npermissions:\n  contents: read\njobs:\n  contract:\n"
    "    runs-on: ubuntu-24.04\n    steps:\n"
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    "        with:\n          persist-credentials: false\n"
    "      - uses: astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0\n"
    "        with:\n          enable-cache: false\n"
    "      - run: uv run --no-sync python -m tools.ci contract --main . --head ../head\n"
)


def _contract_scaffold(root: Path) -> None:
    _write(root, f"{_WORKFLOWS}/{_CONTRACT_YML}", _CONTRACT_SCAFFOLD)


_CONTRACT_STEPS = "    steps:\n"
_UV_NO_CACHE = "        with:\n          enable-cache: false\n"
_CONTRACT_WAKE_JOB = (
    "    needs: contract\n    runs-on: ubuntu-24.04\n    permissions:\n      actions: write\n"
    "    steps:\n"
    '      - run: gh workflow run merge-verdict.yml --ref main -f head="$HEAD_SHA"\n'
)


def _add_contract_job(job_id: str, text: str) -> Callable[[Path], None]:
    def plant(root: Path) -> None:
        path = root / _WORKFLOWS / _CONTRACT_YML
        path.write_text(path.read_text(encoding="utf-8") + f"  {job_id}:\n{text}", encoding="utf-8")

    return plant


CONTRACT_RUN_HOLDS_NOTHING = Row(
    name="main's contract run holds nothing worth taking and saves no cache",
    check=_contract_run_holds_nothing,
    scaffold=_contract_scaffold,
    controls=(
        Control(
            "a secret reference fails",
            _replace(_CONTRACT_YML, "--head ../head\n", "--head ../head ${{ secrets.ANY }}\n"),
        ),
        Control(
            "a write permission fails",
            _replace(_CONTRACT_YML, "  contents: read\n", "  contents: read\n  checks: write\n"),
        ),
        Control(
            "a job's own permissions fail",
            _replace(
                _CONTRACT_YML, _CONTRACT_STEPS, "    permissions: write-all\n" + _CONTRACT_STEPS
            ),
        ),
        Control(
            "an environment fails",
            _replace(
                _CONTRACT_YML, _CONTRACT_STEPS, "    environment: ci-controller\n" + _CONTRACT_STEPS
            ),
        ),
        Control(
            "a cache save fails",
            _replace(
                _CONTRACT_YML,
                _CONTRACT_STEPS,
                _CONTRACT_STEPS
                + "      - uses: actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809 # v4.2.4\n"
                "        with:\n          path: ~/.cache/uv\n          key: uv\n",
            ),
        ),
        Control(
            "setup-uv with its cache on fails",
            _replace(_CONTRACT_YML, _UV_NO_CACHE, "        with:\n          enable-cache: true\n"),
        ),
        Control(
            "the setup action, which caches, fails",
            _replace(
                _CONTRACT_YML,
                _CONTRACT_STEPS,
                _CONTRACT_STEPS + "      - uses: ./.github/actions/setup\n",
            ),
        ),
        Control(
            "a restore alone passes",
            _replace(
                _CONTRACT_YML,
                _CONTRACT_STEPS,
                _CONTRACT_STEPS
                + "      - uses: actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809"
                " # v4.2.4\n        with:\n          path: ~/.cache/uv\n          key: uv\n",
            ),
            fails=False,
        ),
        Control(
            "the workflow gone fails", lambda root: (root / _WORKFLOWS / _CONTRACT_YML).unlink()
        ),
        Control(
            "a wake job holding actions: write alone, running only its dispatch, passes",
            _add_contract_job(_CONTRACT_WAKE, _CONTRACT_WAKE_JOB),
            fails=False,
        ),
        Control(
            "a wake job holding contents: write too fails",
            _add_contract_job(
                _CONTRACT_WAKE,
                _CONTRACT_WAKE_JOB.replace(
                    "      actions: write\n", "      actions: write\n      contents: write\n"
                ),
            ),
        ),
        Control(
            "a wake job that checks out fails",
            _add_contract_job(
                _CONTRACT_WAKE,
                _CONTRACT_WAKE_JOB.replace(
                    "    steps:\n",
                    "    steps:\n"
                    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n",
                ),
            ),
        ),
        Control(
            "a wake job that runs python fails",
            _add_contract_job(
                _CONTRACT_WAKE,
                _CONTRACT_WAKE_JOB.replace(
                    "      - run: gh", "      - run: python -m tools.ci x; gh"
                ),
            ),
        ),
        Control(
            "another job holding actions: write fails",
            _add_contract_job("rerun", _CONTRACT_WAKE_JOB),
        ),
    ),
)


ONLY_THE_RELEASE_CONTROLLER_PUBLISHES = Row(
    name="only the release controller holds a publishing permission or a deployment environment",
    check=_only_the_release_controller_publishes,
    scaffold=_trust_scaffold,
    controls=(
        Control(
            "contents: write in a ci.yml job fails",
            _replace("ci.yml", "      contents: read\n", "      contents: write\n"),
        ),
        Control(
            "a top-level id-token: write fails",
            _replace(
                "main-only.yml",
                "permissions:\n  contents: read\n",
                "permissions:\n  id-token: write\n",
            ),
        ),
        Control(
            "environment: release outside release.yml fails",
            _add_to_job("main-only.yml", "    environment: release\n"),
        ),
        Control(
            "an environment in its mapping form fails",
            _add_to_job("dco.yml", "    environment:\n      name: release-client\n"),
        ),
        Control(
            "any new environment outside release.yml fails",
            _add_to_job("main-only.yml", "    environment: smoke\n"),
        ),
        Control(
            "packages: write in a trusted job fails",
            _replace("dco.yml", "      pull-requests: write\n", "      packages: write\n"),
        ),
        Control(
            "permissions: write-all fails",
            _replace(
                "main-only.yml", "permissions:\n  contents: read\n", "permissions: write-all\n"
            ),
        ),
        Control("pull-requests: write in a trusted workflow passes", _nothing, fails=False),
        Control(
            "the settings report's read-only environment, on its scheduled job, passes",
            _settings_report(),
            fails=False,
        ),
        Control(
            "the read-only environment on another job fails",
            _settings_report("  report:\n", "  other:\n"),
        ),
        Control(
            "another environment on the settings report's job fails",
            _settings_report("environment: settings-report", "environment: release"),
        ),
        Control(
            "the read-only environment in a workflow a pull request runs fails",
            _settings_report("  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n"),
        ),
        Control(
            "the read-only environment on a push-triggered workflow fails",
            _settings_report("  workflow_dispatch:\n", "  push:\n    branches: [main]\n"),
        ),
        Control(
            "the controller's environment on the dispatched tagging job passes",
            _release_tag(),
            fails=False,
        ),
        Control(
            "the controller's environment on a tagging workflow that runs on a push fails",
            _release_tag("  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n"),
        ),
        Control(
            "the controller's environment on the merge verdict's workflow_run job passes",
            _merge_verdict(),
            fails=False,
        ),
        Control(
            "the controller's environment on a poster that also runs on pull_request fails",
            _merge_verdict("    types: [completed]\n", "    types: [completed]\n  pull_request:\n"),
        ),
        Control(
            "the controller's environment on a poster that also runs on a pull_request fails",
            _merge_verdict("    types: [completed]\n", "    types: [completed]\n  pull_request:\n"),
        ),
        Control(
            "the controller's environment on a poster's dispatch its job holds to main passes",
            _merge_verdict(
                "    types: [completed]\npermissions:\n  contents: read\njobs:\n  post:\n",
                "    types: [completed]\n  workflow_dispatch:\npermissions:\n  contents: read\n"
                "jobs:\n  post:\n    if: github.ref == 'refs/heads/main'\n",
            ),
            fails=False,
        ),
        Control(
            "the controller's environment on a poster's dispatch its job does not hold to main"
            " fails",
            _merge_verdict(
                "    types: [completed]\n", "    types: [completed]\n  workflow_dispatch:\n"
            ),
            expect="on a dispatch its condition does not hold to main",
        ),
        Control(
            "the controller's environment on another job of the poster fails",
            _merge_verdict("  post:\n", "  other:\n"),
        ),
        Control(
            "the tagging job on a workflow_run trigger fails",
            _release_tag("  workflow_dispatch:\n", "  workflow_run:\n    workflows: [Merge]\n"),
        ),
        Control(
            "the controller's environment on the settings report's job fails",
            _settings_report("environment: settings-report", "environment: ci-controller"),
        ),
        Control(
            "the read-only environment named in its mapping form passes",
            _settings_report(
                "    environment: settings-report\n",
                "    environment:\n      name: settings-report\n",
            ),
            fails=False,
        ),
        Control("the push proof's packages: write passes", _push_proof(), fails=False),
        Control(
            "the push proof holding contents: write too fails",
            _push_proof(
                "      packages: write\n", "      packages: write\n      contents: write\n"
            ),
        ),
        Control(
            "the push proof on a push trigger fails",
            _push_proof("  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n"),
        ),
        Control(
            "the push proof's job off main fails",
            _push_proof("github.ref == 'refs/heads/main' && ", ""),
        ),
        Control(
            "packages: write in another job of the push proof fails",
            _push_proof("  push:\n", "  other:\n"),
        ),
        Control(
            "release.yml's own publishing jobs pass",
            _replace(
                "release.yml",
                "      id-token: write\n",
                "      id-token: write\n      attestations: write\n",
            ),
            fails=False,
        ),
    ),
)


#: The workflow-level line the preference plants remove or change.
_PREFERENCE = "  UV_PYTHON_PREFERENCE: only-managed\n"

_NO_UV_WORKFLOW = (
    "name: no-uv\non: workflow_dispatch\npermissions: {}\njobs:\n  only:\n"
    "    runs-on: ubuntu-24.04\n    steps:\n      - name: Runner identity\n"
    '        run: echo "${ImageOS} ${ImageVersion}"\n'
    "      - run: python3 tools/something.py  # names no uv\n"
)


def _job_level_preference(root: Path, only_first: bool = False) -> None:
    """Move release-artifacts.yml's setting off the workflow and onto its uv jobs: every one of
    them, or only the first, so the other uv jobs run without it. It has uv jobs and jobs
    without uv, so both halves of the rule are read."""
    rel = ".github/workflows/release-artifacts.yml"
    workflow = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
    del workflow["env"]["UV_PYTHON_PREFERENCE"]
    uv_jobs = [
        job for job in workflow["jobs"].values() if any(_runs_uv(s) for s in job.get("steps", []))
    ]
    assert len(uv_jobs) > 1, "release-artifacts.yml no longer has several uv jobs to plant on"
    for job in uv_jobs[:1] if only_first else uv_jobs:
        job.setdefault("env", {})["UV_PYTHON_PREFERENCE"] = "only-managed"
    _write(root, rel, yaml.safe_dump(workflow, sort_keys=False))


# ── The pull request builds the wheel after the SPA, and verifies that wheel ────────────────


def _entry_argvs(tree: Tree, job_id: str) -> tuple[catalogue.Job | None, list[str]]:
    """The catalogue's ``job_id`` entry, or None and why."""
    try:
        cat = catalogue.load(tree.get(catalogue.CATALOGUE, ""), catalogue.CATALOGUE)
    except catalogue.CatalogueError as exc:
        return None, [f"the catalogue does not load: {problem}" for problem in exc.problems]
    try:
        return cat.job(job_id), []
    except KeyError:
        return None, [f"the catalogue has no {job_id} entry"]


def _first_step(job: catalogue.Job, *words: str) -> int | None:
    """The index of the job's first step whose argv holds every one of ``words``."""
    return next((i for i, s in enumerate(job.command) if set(words) <= set(s.argv)), None)


def wheel_violations(tree: Tree) -> list[str]:
    """The catalogue's ``package`` builds the wheel after ``web-build``'s SPA build and runs the
    wheel contract on it. The wheel must carry the SPA, so a wheel built before ``npm run build``
    would verify a dashboard-less build: package runs after web-build (``after``), whose dist
    its stage job downloads first, and builds the wheel, then verifies that wheel."""
    package, bad = _entry_argvs(tree, "package")
    web, more = _entry_argvs(tree, "web-build")
    bad += more
    if package is None or web is None:
        return bad
    if _first_step(web, "npm", "run", "build") is None:
        bad.append("web-build builds no SPA (`npm run build`)")
    if "web-build" not in package.after:
        bad.append("package does not run after web-build, so its wheel may carry no SPA")
    build, verify = _first_step(package, "uv", "build", "--wheel"), _first_step(
        package, "scripts/verify_wheel.py", "--wheel"
    )
    if build is None:
        bad.append("package builds no wheel (`uv build --wheel`)")
    if verify is None:
        bad.append("package runs no wheel contract (`verify_wheel.py --wheel`)")
    if None not in (build, verify) and not build < verify:  # type: ignore[operator]
        bad.append("package must build the wheel, then verify that wheel")
    return bad


# ── A browser install never waits on apt unless the browser cannot start ─────────────────────
#
# run-job installs Chromium as `npx playwright install chromium` (tools/ci/jobs.py INSTALL_ARGV),
# never with --with-deps, so no tier job runs apt. A workflow job that installs it itself is held
# to the shape below: no apt unless Chromium does not start, and then bounded.

_PROBE_ID = "chromium-starts"


#: A workflow job that installs Chromium in the shape the row holds, in a workflow of its own.
_BROWSER_WORKFLOW = """name: Browser
on:
  pull_request:
permissions:
  contents: read
jobs:
  browser:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - name: install chromium
        timeout-minutes: 5
        run: npx playwright install chromium
      - name: Check that Chromium starts
        id: chromium-starts
        continue-on-error: true
        timeout-minutes: 2
        run: node -e "require('playwright').chromium.launch().then((browser) => browser.close())"
      - name: Install Chromium's system libraries
        if: ${{ steps.chromium-starts.outcome == 'failure' }}
        timeout-minutes: 6
        run: |
          if ! timeout 300 npx playwright install-deps chromium; then
            exit 1
          fi
"""


def _browser_workflow(*edits: tuple[str, str]) -> Callable[[Path], None]:
    """:data:`_BROWSER_WORKFLOW` written as a workflow of the tree, each edit made in turn."""

    def plant(root: Path) -> None:
        text = _BROWSER_WORKFLOW
        for old, new in edits:
            assert old in text, f"no {old!r} in the planted browser workflow"
            text = text.replace(old, new, 1)
        _write(root, f"{_WORKFLOWS}/browser.yml", text)

    return plant


def browser_install_violations(tree: Tree) -> list[str]:
    """Each job that installs Playwright's Chromium does it without apt, within 5 minutes, then
    checks that it starts (``id: chromium-starts``, ``continue-on-error``, bounded), and installs
    the system libraries from apt only when that check failed, under ``timeout``. ``--with-deps``
    ran apt on every run, and a hung Ubuntu mirror held a job there for its whole limit."""
    bad: list[str] = []
    for where, job_id, job in _jobs(tree):
        steps = _job_steps(tree, job)
        runs = [str(step.get("run", "")) for step in steps]
        if not any("playwright install" in run for run in runs):
            continue
        at = f"{where}:{job_id}"
        probe = next((s for s in steps if s.get("id") == _PROBE_ID), None)
        for step, run in zip(steps, runs):
            name = step.get("name", "a step")
            if re.search(r"playwright install\b(?!-deps).*--with-deps", run):
                bad.append(f"{at} {name!r} installs Chromium with --with-deps, which runs apt")
            elif re.search(r"playwright install\b(?!-deps)", run):
                if not 0 < int(step.get("timeout-minutes") or 0) <= 5:
                    bad.append(
                        f"{at} {name!r} installs Chromium with no timeout-minutes of 5 or less"
                    )
            if "playwright install-deps" in run:
                cond = " ".join(str(step.get("if", "")).split())
                if f"steps.{_PROBE_ID}.outcome == 'failure'" not in cond:
                    bad.append(f"{at} {name!r} runs apt without the start check having failed")
                if not re.search(r"\btimeout \d+ npx playwright install-deps", run):
                    bad.append(f"{at} {name!r} runs apt with no `timeout` around it")
        if probe is None:
            bad.append(f"{at} installs Chromium but never checks that it starts (id: {_PROBE_ID})")
        elif probe.get("continue-on-error") is not True or not probe.get("timeout-minutes"):
            bad.append(f"{at} start check must be continue-on-error, with a timeout-minutes")
        if not any("playwright install-deps" in run for run in runs):
            bad.append(f"{at} has no apt install for a Chromium that does not start")
    return bad


# ── The e2e legs' web-dist is a finished build ───────────────────────────────────────────────

#: What marks a web-build step that reads the vite build's output: its licence check, its stamp
#: and the desktop built-page legs.
_READS_THE_BUILD = (
    "check_asset_licenses.py",
    "spa_dist_freshness.py stamp",
    "test:desktop:built",
)


#: A step condition that runs it on a red job too: any status function but the implied success.
_STATUS_FUNCTION = re.compile(r"\b(?:always|cancelled|failure|success)\(\)")


def web_dist_violations(tree: Tree) -> list[str]:
    """The ``web-dist`` the e2e legs and package read is a finished build (#3905): the catalogue's
    web-build builds the SPA in its first step, before every step that reads the build, and
    run-job stops at the first step that fails, so a failed build runs none of them; and each
    tier workflow's step that uploads ``web-dist`` runs only on the job's success (no status
    function in its ``if:``), after run-job, so a failed build uploads no half-built dist."""
    web, bad = _entry_argvs(tree, "web-build")
    if web is not None:
        build = _first_step(web, "npm", "run", "build")
        if build is None:
            bad.append("web-build builds no SPA (`npm run build`)")
        else:
            for n, step in enumerate(web.command[:build], 1):
                if any(r in shlex.join(step.argv) for r in _READS_THE_BUILD):
                    bad.append(f"web-build: command step {n} reads the build before it is built")
    for name in _TIER_WORKFLOWS:
        workflow = _workflow(tree, name) or {}
        for job_id, job in (workflow.get("jobs") or {}).items():
            steps = (job or {}).get("steps") or [] if isinstance(job, dict) else []
            ran = [i for i, s in enumerate(steps) if "tools.ci run-job" in str(s.get("run", ""))]
            for i, step in enumerate(steps):
                given = (step.get("with") or {}) if isinstance(step, dict) else {}
                if _action(step) != "actions/upload-artifact" or given.get("name") != "web-dist":
                    continue
                where = f"{name}:{job_id} step {step.get('name')!r}"
                if _STATUS_FUNCTION.search(str(step.get("if", ""))):
                    bad.append(
                        f"{where} uploads web-dist under `if: {step.get('if')}`, on a red build too"
                    )
                if not ran or i < ran[0]:
                    bad.append(f"{where} uploads web-dist before run-job builds it")
    return bad


# ── The tested tree on main, and the nightly breadth ────────────────────────────────────────
#
# A push to main runs ``merge.yml``'s whole tier again only when no green pull-request run tested
# its tree, and platform breadth is ``breadth.yml``'s, a nightly whose legs reuse an earlier
# result only on identical inputs. These rows hold the shape that makes both claims true.

_GATE = "needs.tree-check.outputs.tested != 'true'"
_ON_MAIN = "github.ref == 'refs/heads/main'"


#: libyaml's loader where it is built, as tools/ci/supply_chain.py reads the workflows: the same
#: constructor as yaml.safe_load's, and several times faster on a workflow the size of ci.yml.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _workflow(tree: Tree, name: str) -> dict[str, Any] | None:
    """The workflow *name* in *tree*, parsed alone: a row that reads one workflow parses no
    other, on the tree and on every control it plants."""
    texts = [
        text
        for rel, text in tree.items()
        if rel.startswith(".github/workflows/") and rel.rsplit("/", 1)[1] == name
    ]
    return yaml.load(texts[-1], Loader=_LOADER) if texts else None


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else [str(n) for n in needs]


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps") or [])


#: The login GitHub gives Dependabot as a pull request's author, and two of its branches.
_DEPENDABOT_LOGIN = "dependabot[bot]"
_DEPENDABOT_A = "dependabot/uv/ruff-0.15.0"
_DEPENDABOT_B = "dependabot/npm_and_yarn/web/vite-8.1.0"
#: A tier run for a Dependabot head: its pull request's author is Dependabot, which no fork's
#: branch name can borrow.
_DEPENDABOT_RUN = f"github.event.pull_request.user.login == '{_DEPENDABOT_LOGIN}'"


def _dependabot_tier_concurrency(prefix: str) -> dict[str, str]:
    """The tier concurrency that puts every Dependabot head's runs, ``pr.yml``'s and
    ``merge.yml``'s alike, in one group, ``dependabot-heads``: one at a time, waiting their turn,
    none cancelled. Any other run keys, and cancels, as it does without it."""
    when = _DEPENDABOT_RUN
    return {
        "group": f"${{{{ {when} && 'dependabot' || '{prefix}' }}}}-${{{{ {when} && 'heads'"
        " || github.event.pull_request.number || github.sha }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' &&"
        " github.event.pull_request.user.login != 'dependabot[bot]' }}",
        "queue": f"${{{{ {when} && 'max' || 'single' }}}}",
    }


def _tier_concurrency_line(rel: str, key: str, value: str) -> Callable[[Path], None]:
    """*rel*'s workflow-level ``concurrency`` *key* set to *value*, wherever the tree spells it
    (a key it lacks is added, an empty *value* removes it), so a plant reads a tree in either
    tier shape."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        block = re.search(r"^concurrency:\n((?:  .*\n)+)", text, re.M)
        assert block, f"no workflow-level concurrency in {rel}"
        lines = block.group(1)
        line = re.search(rf"^  {re.escape(key)}:.*\n", lines, re.M)
        set_to = f"  {key}: {value}\n" if value else ""
        lines = lines.replace(line.group(0), set_to, 1) if line else lines + set_to
        _write(root, rel, text[: block.start(1)] + lines + text[block.end(1) :])

    return plant


def _tier_concurrency(*rels: str, dependabot: bool) -> Callable[[Path], None]:
    """Each tier workflow of *rels* in the shape that queues every Dependabot head's runs in one
    group, or in the shape without it."""

    def plant(root: Path) -> None:
        for rel in rels:
            prefix = Path(rel).stem
            shape = (
                _dependabot_tier_concurrency(prefix)
                if dependabot
                else {"group": f"{prefix}-{_TIER_RUN_KEY}", "cancel-in-progress": _PR_RUN_CANCELS}
            )
            for key in ("group", "cancel-in-progress", "queue"):
                _tier_concurrency_line(rel, key, shape.get(key, ""))(root)

    return plant


def _plant_tier(name: str, rel: str, key: str, value: str, expect: str = "") -> Control:
    """A violation planted by setting one workflow-level ``concurrency`` key, in either shape."""
    return Control(name, _tier_concurrency_line(rel, key, value), True, expect)


#: The catalogue entries whose shards share one group per shard on main's first attempt
#: (decision 9): a newer push's waiting shard replaces an older one that has not started, so a
#: burst of merges runs the suite on its newest commit, and the older run reads that shard red. A
#: re-run (a later ``run_attempt``) keys on its own commit, so it can neither replace nor be
#: replaced, and every other entry keys on its commit, so no push's security corpus or
#: apps-contract is ever replaced.
_SUITE_ENTRIES = frozenset({"python-suite", "python-suite-macos"})


def _main_push_at(sha: str, attempt: str = "1") -> dict[str, Any]:
    context = _main_push_context()
    context["github"]["sha"] = sha
    context["github"]["run_attempt"] = attempt
    return context


def _stage_group_violations(tree: Tree, job_id: str, job: dict[str, Any]) -> list[str]:
    """A merge.yml stage job's own group, entry by entry of the tier's plan: shared
    by every main push for a suite shard, one per shard, the commit's own for every other entry
    and for a re-run, and never cancelling."""
    where = f"merge.yml:{job_id}"
    concurrency = job.get("concurrency")
    if not isinstance(concurrency, dict) or "group" not in concurrency:
        return [
            f"{where} has no concurrency group of its own, so every push to main queues its whole"
            " suite (#3915)"
        ]
    group = str(concurrency["group"])
    bad: list[str] = []
    if concurrency.get("cancel-in-progress") is not False:
        bad.append(f"{where}'s group can cancel a running entry: set it to false")
    legs = _matrix_legs(_literal_matrix(tree, "merge.yml", job))
    if not legs:
        return [*bad, f"{where}: no planned entry to read its group for"]
    shared: dict[str, set[str]] = {}
    for leg in legs:
        key, suite = str(leg.get("key", "")), str(leg.get("id", "")) in _SUITE_ENTRIES
        resolved = _leg_group(group, leg)
        try:
            one, two, rerun_one, rerun_two = (
                _evaluate(resolved, _main_push_at(sha, attempt))
                for sha, attempt in (
                    ("1" * 40, "1"),
                    ("2" * 40, "1"),
                    ("1" * 40, "2"),
                    ("2" * 40, "2"),
                )
            )
        except ValueError as exc:
            return [*bad, f"{where}: {exc}"]
        if suite and one != two:
            bad.append(
                f"{where}: {key}'s group on main is per commit ({one!r} and {two!r}), so a newer"
                " push never replaces a waiting shard"
            )
        elif not suite and one == two:
            bad.append(
                f"{where}: {key}'s group on main ({one!r}) is shared by every push, so a newer"
                " push replaces its waiting run"
            )
        if suite and one in shared:
            bad.append(f"{where}: {key} and {shared[one]} share the group {one!r} on main")
        shared.setdefault(one, key)
        # A re-run keeps its run id and raises run_attempt. In the shared group an older commit's
        # re-run would replace the tip's waiting shard, so a re-run on main keys on its own commit.
        if rerun_one in (one, two) and suite or rerun_one == rerun_two:
            bad.append(
                f"{where}: {key}'s group for a re-run on main ({rerun_one!r}) is the shared one or"
                " another commit's, so a re-run of an older commit can replace the tip's"
            )
    return bad


def _merge_never_cancels(tree: Tree, workflow: dict[str, Any]) -> list[str]:
    """No merge.yml run on main is cancelled: its group is its commit's, it cancels in progress
    only a pull request's run, and the stage jobs alone have a group of their own."""
    concurrency = workflow.get("concurrency") or {}
    group = str(concurrency.get("group", ""))
    bad: list[str] = []
    try:
        one, two = (_evaluate(group, _main_push_at(sha)) for sha in ("1" * 40, "2" * 40))
        cancels = _cancels(concurrency.get("cancel-in-progress", False), _main_push_at("1" * 40))
    except ValueError as exc:
        return [f"merge.yml: {exc}"]
    if one == two or "1" * 40 not in one:
        bad.append(f"merge.yml: a run on main is not grouped by its own commit ({one!r})")
    if cancels:
        bad.append("merge.yml: a run on main can be cancelled")
    for job_id, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        if _planned_tier("merge.yml", job) is not None:
            bad.extend(_stage_group_violations(tree, job_id, job))
        elif "concurrency" in job:
            bad.append(f"merge.yml:{job_id} has its own concurrency group, which can cancel it")
    return bad


def main_push_runs_violations(tree: Tree) -> list[str]:
    """``merge.yml`` runs on every push to main and on no other push, and no main run is ever
    cancelled. The suite's shards are the one exception: a newer push's waiting shard replaces
    an older one's in their shared group, and that run's verdict reads the shard red.

    A pull request's run is its candidate's check. A push run on the same feature branch only
    duplicates it (#3915)."""
    workflow = _workflow(tree, "merge.yml")
    if workflow is None:
        return ["merge.yml is missing"]
    push = _triggers(workflow).get("push", "absent")
    bad: list[str] = []
    if push == "absent":
        bad.append("merge.yml does not run on a push")
    elif not isinstance(push, dict):
        bad.append("merge.yml runs on a bare `push:`, which every branch and tag push also starts")
    else:
        branches = [str(b) for b in push.get("branches") or []]
        # GitHub reads branch patterns in order, so a later `!` pattern drops what an earlier
        # one matched; and a path filter starts no run for a push whose paths it excludes.
        if (
            "branches-ignore" in push
            or not ({"**", "main"} & set(branches))
            or any(b.startswith("!") for b in branches)
            or {"paths", "paths-ignore"} & set(push)
        ):
            bad.append(f"merge.yml's push trigger leaves main, or some pushes to it, out: {push}")
        others = [b for b in branches if b != "main" and not b.startswith("!")]
        if "branches-ignore" in push or others:
            bad.append(
                f"merge.yml runs on a push to a branch other than main ({push}), which only "
                "duplicates that branch's pull_request run"
            )
        # Beside a branch filter, a tags-ignore starts a run on every tag it does not match.
        if {"tags", "tags-ignore"} & set(push):
            bad.append("merge.yml runs on a tag push, which is release.yml's")
    return bad + _merge_never_cancels(tree, workflow)


def _suite_words(argv: Sequence[str]) -> list[str]:
    """A pytest step's words, less the two a macOS shard may differ in: its worker count and its
    report's path. Everything else (paths, selections, the split) must be the same."""
    words = list(argv)
    if "-n" in words:
        at = words.index("-n")
        del words[at : at + 2]
    return [w for w in words if not w.startswith("--junitxml=")]


#: The suite's two merge-tier entries, and the leg of the shard plan each lists its share from.
_SUITE, _SUITE_MACOS = "python-suite", "python-suite-macos"
_SUITE_LEGS = {_SUITE: "test-shard", _SUITE_MACOS: "test-macos"}


def _suite_step(job: catalogue.Job, word: str) -> catalogue.Step | None:
    return next((s for s in job.command if word in s.argv), None)


def macos_leg_violations(tree: Tree) -> list[str]:
    """``python-suite-macos`` runs ``python-suite``'s split suite, in the same tiers but the entry
    tier, and whenever it runs: on the merge queue's run and a pull request's merge run, and on a
    push to main no green run tested (the merge tier's push stage plans it beside every other
    merge job). It leaves the entry tier, a pull request's run once the queue gates main (0.4.0
    A4: the queue's run and breadth's night hold it). That it runs on macOS, and in the merge
    tier, the catalogue's loader holds: it is security evidence."""
    try:
        cat = catalogue.load(tree.get(catalogue.CATALOGUE, ""), catalogue.CATALOGUE)
    except catalogue.CatalogueError as exc:
        return [f"the catalogue does not load: {problem}" for problem in exc.problems]
    try:
        macos, ubuntu = cat.job(_SUITE_MACOS), cat.job(_SUITE)
    except KeyError:
        return [f"the catalogue has no {_SUITE_MACOS} beside {_SUITE}"]
    bad: list[str] = []
    if macos.tiers != tuple(t for t in ubuntu.tiers if t != catalogue.ENTRY_TIER):
        bad.append(
            f"{_SUITE_MACOS} runs in {macos.tiers}, {_SUITE} in {ubuntu.tiers}: the same tiers"
            f" but {catalogue.ENTRY_TIER}"
        )
    pushed = [j in (cat.every_push or ()) for j in (_SUITE_MACOS, _SUITE)]
    if pushed[0] != pushed[1]:
        bad.append(f"{_SUITE_MACOS} and {_SUITE} differ in whether every push to main runs them")
    for job in (macos, ubuntu):
        listing = _suite_step(job, "shards")
        want = ["--leg", _SUITE_LEGS[job.id], "--shard", f"{{shard}}/{job.shards}"]
        if listing is None or not _contains(list(listing.argv), want):
            bad.append(f"{job.id} does not list its own shard of its own leg's plan")
    mine, theirs = _suite_step(macos, "pytest"), _suite_step(ubuntu, "pytest")
    if mine is None or theirs is None:
        return [*bad, f"{_SUITE_MACOS} or {_SUITE} runs no pytest"]
    if _suite_words(mine.argv) != _suite_words(theirs.argv):
        bad.append(f"{_SUITE_MACOS}'s pytest selects other than {_SUITE}'s")
    if dict(macos.env) != dict(ubuntu.env) or "PYTEST_ADDOPTS" in macos.env:
        bad.append(f"{_SUITE_MACOS}'s environment differs from {_SUITE}'s")
    for key in ("workdir", "env"):
        if getattr(mine, key) != getattr(theirs, key) or "PYTEST_ADDOPTS" in mine.env:
            bad.append(f"{_SUITE_MACOS}'s pytest step's {key} differs from {_SUITE}'s")
    return bad


def _contains(words: list[str], run: list[str]) -> bool:
    """Whether ``run`` is a contiguous run of ``words``."""
    return any(words[i : i + len(run)] == run for i in range(len(words) - len(run) + 1))


_FETCH_BEFORE = "contents/tools/ci/tree_tested.py?ref=${BEFORE}"
_CHECKED_OUT_COPY = re.compile(r"python3?(?:\s+-\w+)*\s+(?:\./)?tools/ci/tree_tested\.py")


def _tree_check_violations(where: str, job: dict[str, Any] | None, asks: str) -> list[str]:
    if job is None:
        return [f"{where} is missing"]
    bad: list[str] = []
    permissions = job.get("permissions")
    if not isinstance(permissions, dict) or "write" in permissions.values():
        bad.append(f"{where} holds more than read permissions: {permissions!r}")
    steps = job.get("steps") or []
    if any(_action(step) == "actions/checkout" for step in steps):
        bad.append(f"{where} checks out the pushed tree")
    answer = next((s for s in steps if _FETCH_BEFORE in str(s.get("run", ""))), None)
    if answer is None:
        return bad + [f"{where} does not read the previous main commit's copy of the check"]
    run = str(answer["run"])
    if str((answer.get("env") or {}).get("BEFORE", "")) != "${{ github.event.before }}":
        bad.append(f"{where} reads the check at a ref other than `github.event.before`")
    if _CHECKED_OUT_COPY.search(run):
        bad.append(f"{where} runs the checked-out copy of the check")
    if _UV_COMMAND.search(run):
        bad.append(f"{where} installs or runs through uv")
    if f'python3 -I "$check" --workflow {asks}' not in " ".join(run.split()):
        bad.append(f"{where} does not ask the fetched check about {asks}, under python3 -I")
    return bad


def tree_check_reads_before_violations(tree: Tree) -> list[str]:
    """``tree-check`` runs the PREVIOUS main commit's copy of the script: read-only, nothing
    checked out, nothing installed."""
    bad: list[str] = []
    jobs = (_workflow(tree, "merge.yml") or {}).get("jobs") or {}
    bad += _tree_check_violations("merge.yml:tree-check", jobs.get("tree-check"), "merge.yml")
    return bad


#: The one condition merge.yml's superseded proof runs under: a push to main alone.
_ON_A_PUSH_TO_MAIN = (
    "${{ !cancelled() && github.event_name == 'push' && github.ref == 'refs/heads/main' }}"
)
_FETCH_SUPERSEDED = "contents/tools/ci/superseded.py?ref=${BEFORE}"


def superseded_proof_violations(tree: Tree) -> list[str]:
    """On a push to main alone, merge.yml's ``results`` proves which cancelled suite shard a newer
    push's waiting shard replaced (decision 9), before the verdict reads the proof: one step,
    with the run's token, runs the PREVIOUS main commit's copy of ``superseded.py`` under
    ``python3 -I``, for exactly the suite jobs whose shards share one group per shard on main,
    in that group alone, and writes the file the verdict step passes as ``--superseded``.
    The proof's commit is held to a full object id by the verdict (``decide_main_push``)."""
    where = "merge.yml:results"
    jobs = (_workflow(tree, "merge.yml") or {}).get("jobs") or {}
    results = jobs.get("results")
    if not isinstance(results, dict):
        return [f"{where} is missing"]
    bad: list[str] = []
    permissions = results.get("permissions") or {}
    for scope in ("actions", "checks"):
        if permissions.get(scope) != "read":
            bad.append(
                f"{where} grants {scope}: {permissions.get(scope)!r}, not read, and superseded.py"
                " reads the run's jobs and a cancelled shard's annotations"
            )
    steps = [s for s in results.get("steps") or [] if isinstance(s, dict)]
    proving = [n for n, s in enumerate(steps) if "superseded.py" in str(s.get("run", ""))]
    judging = [n for n, s in enumerate(steps) if "--records" in str(s.get("run", ""))]
    if len(proving) != 1:
        return [*bad, f"{where} proves superseded shards in {len(proving)} steps, not one"]
    step = steps[proving[0]]
    run = " ".join(str(step.get("run", "")).split())
    if any(n < proving[0] for n in judging):
        bad.append(f"{where} proves superseded shards after the verdict that reads the proof")
    if step.get("if") != _ON_A_PUSH_TO_MAIN:
        bad.append(
            f"{where} proves superseded shards under {step.get('if')!r}, not on a push to main"
            " alone"
        )
    env = step.get("env") or {}
    if env.get("GH_TOKEN") != "${{ github.token }}":
        bad.append(f"{where}'s proof is given no token of the run's")
    if env.get("BEFORE") != "${{ github.event.before }}" or _FETCH_SUPERSEDED not in run:
        bad.append(f"{where} does not fetch the previous main commit's copy of superseded.py")
    if re.search(r"python3?(?:\s+-\w+)*\s+(?:\./)?tools/ci/superseded\.py", run):
        bad.append(f"{where} runs the checked-out copy of superseded.py")
    calls = re.findall(r"python3?((?:\s+-\w+)*)\s+\"\$check\"", run)
    if not calls or any("-I" not in flags.split() for flags in calls):
        bad.append(f"{where} does not run the fetched superseded.py under python3 -I")
    if "--workflow merge.yml" not in run:
        bad.append(f"{where} does not ask superseded.py about merge.yml's runs")
    # The suite jobs and the group, as the stage jobs' own concurrency spells them.
    groups = {
        str((job.get("concurrency") or {}).get("group", ""))
        for job in jobs.values()
        if isinstance(job, dict) and isinstance(job.get("concurrency"), dict)
    }
    shared = sorted(
        {m for g in groups for m in re.findall(r"matrix\.id == '([^']+)' && 'suite'", g)}
    )
    loop = re.search(r"for job in ([^;]+); do", run)
    proved = sorted(loop.group(1).split()) if loop else []
    if proved != shared:
        bad.append(
            f"{where} proves {proved}, not the suite jobs whose shards share a group on main"
            f" ({shared})"
        )
    prefixes = {m for g in groups for m in re.findall(r"group: (\S+?)\$\{\{", f"group: {g}")}
    want = {f"{p}suite-" for p in prefixes}
    named = set(re.findall(r"--group-prefix (\S+)", run))
    if not named or named != want:
        bad.append(f"{where} reads the groups {sorted(named)}, not the shared group {sorted(want)}")
    out = re.search(r'--out "([^"]+)"', run)
    verdict_run = " ".join(str(steps[judging[0]].get("run", "")).split()) if judging else ""
    if out is None or f'if [ -f "{out.group(1)}" ]' not in verdict_run:
        bad.append(f"{where}'s verdict does not read the file the proof writes")
    if '${proof:+--superseded "$proof"}' not in verdict_run:
        bad.append(f"{where}'s verdict is not given the proof (--superseded)")
    return bad


#: The root project's venv, whose pytest reads pyproject's pytest config. A package's own venv
#: (the client's ``.venv-client``) reads its own.
_ROOT_VENV_PYTHON = re.compile(r"(?:^|/)\.venv/bin/python3?$")


def _commands(run: str) -> list[list[str]]:
    """A ``run:`` block's commands, each as its words: continued lines joined, a ``#`` comment
    dropped, split at newlines, ``&&``, ``||``, ``;`` and pipes."""
    commands = []
    for part in re.split(r"\n|&&|\|\||;|\|", run.replace("\\\n", " ")):
        words = []
        for word in part.split():
            if word.startswith("#"):
                break
            words.append(word)
        commands.append(words)
    return commands


def _root_pytest(words: list[str]) -> bool:
    """A pytest of the root project, in any ``uv run`` spelling (``uv run --frozen pytest``,
    ``uv run python3 -m pytest``, ``uv run -m pytest``) or through the root venv's Python."""
    for i, word in enumerate(words):
        if word == "uv" and words[i + 1 : i + 2] == ["run"] and "pytest" in words[i + 2 :]:
            return True
        if _ROOT_VENV_PYTHON.search(word) and words[i + 1 : i + 3] == ["-m", "pytest"]:
            return True
    return False


def _measures_coverage(run: str) -> bool:
    """Per command, never per step: a command naming ``--cov`` or ``--cov=…``. pyproject's
    addopts measure nothing, so a pytest that does not ask for coverage measures none."""
    return any(w == "--cov" or w.startswith("--cov=") for words in _commands(run) for w in words)


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [str(value)]


def _runner_labels(job: dict[str, Any]) -> list[str]:
    """The labels a job can run on: ``runs-on`` as a string, a list or a ``{group, labels}``
    mapping; the quoted literals of an expression in it; and, when it names the matrix, every
    value the matrix holds, nested or in ``include``; and the matrix ``os``."""
    labels = _strings(job.get("runs-on", []))
    for label in list(labels):
        labels += re.findall(r"'([^']*)'", label)
    if any("matrix." in label for label in labels):
        labels += _strings((job.get("strategy") or {}).get("matrix") or {})
    return labels + _matrix_values(job, "os")


def _starts_on_a_change(workflow: dict[str, Any]) -> bool:
    """Whether a pull request or a branch push starts the workflow. A push filtered to tags alone
    (breadth's ``rehearsal-v*``, a release's ``v*``) starts on no branch's push."""
    triggers = _triggers(workflow)
    if "pull_request" in triggers:
        return True
    if "push" not in triggers:
        return False
    push = triggers["push"]
    return not (isinstance(push, dict) and push and set(push) <= {"tags", "tags-ignore"})


def push_breadth_violations(tree: Tree) -> list[str]:
    """No ``macos-14`` leg anywhere (any size, any case, in the forms :func:`_runner_labels`
    reads, or as a catalogue entry's platform), and coverage only in a workflow no pull request
    or branch push starts and in no catalogue entry of the change or merge tier, which the tier
    workflows run on every pull request and push."""
    bad: list[str] = []
    for name, workflow in _workflows(tree):
        on_change = _starts_on_a_change(workflow)
        for job_id, job in (workflow.get("jobs") or {}).items():
            if any(
                re.fullmatch(r"macos-14(?:-.+)?", label, flags=re.IGNORECASE)
                for label in _runner_labels(job)
            ):
                bad.append(f"{name}:{job_id} runs on macos-14")
            steps = job.get("steps") or []
            if on_change and any(_measures_coverage(str(s.get("run", ""))) for s in steps):
                bad.append(f"{name}:{job_id} measures coverage on a push or pull request")
    try:
        cat = catalogue.load(tree.get(catalogue.CATALOGUE, ""), catalogue.CATALOGUE)
    except catalogue.CatalogueError as exc:
        return [*bad, *(f"the catalogue does not load: {problem}" for problem in exc.problems)]
    for entry in cat.jobs:
        if re.fullmatch(r"macos-14(?:-.+)?", entry.platform, flags=re.IGNORECASE):
            bad.append(f"the catalogue's {entry.id} runs on macos-14")
        tiers = sorted({"change", "merge"} & set(entry.tiers))
        words = [w for step in entry.command for w in step.argv]
        if tiers and any(w == "--cov" or w.startswith("--cov=") for w in words):
            bad.append(
                f"the catalogue's {entry.id} measures coverage in the {' and '.join(tiers)} tier,"
                " which every pull request or push runs"
            )
    return bad


def _breadth_jobs(tree: Tree) -> dict[str, Any]:
    return dict((_workflow(tree, "breadth.yml") or {}).get("jobs") or {})


def breadth_order_violations(tree: Tree) -> list[str]:
    """``freshness`` runs first and every leg waits on it; ``report`` runs last, on every leg,
    whatever the legs concluded."""
    jobs = _breadth_jobs(tree)
    if "freshness" not in jobs or "report" not in jobs:
        return ["breadth.yml has no freshness or no report job"]
    bad: list[str] = []
    if _needs(jobs["freshness"]):
        bad.append("breadth.yml:freshness waits on another job")
    for job_id in jobs:
        if job_id != "freshness" and not _waits_on(jobs, job_id, "freshness"):
            bad.append(f"breadth.yml:{job_id} does not wait on freshness")
    missing = set(jobs) - {"report"} - set(_needs(jobs["report"]))
    if missing:
        bad.append(f"breadth.yml:report does not wait on {sorted(missing)}")
    if not _runs_whatever_its_needs_concluded(jobs["report"]):
        bad.append("breadth.yml:report does not run whatever the legs concluded")
    return bad


def _waits_on(
    jobs: dict[str, Any], job_id: str, target: str, seen: frozenset[str] = frozenset()
) -> bool:
    """Whether *job_id* waits on *target*, itself or through a job it waits on."""
    needs = _needs(jobs.get(job_id) or {})
    return target in needs or any(
        _waits_on(jobs, n, target, seen | {job_id}) for n in needs if n not in seen
    )


def _runs_whatever_its_needs_concluded(job: dict[str, Any]) -> bool:
    condition = str(job.get("if", ""))
    return "!cancelled()" in condition and "success()" not in condition


#: The macOS legs that wait for the macOS suite instead of running beside its shards, which hold
#: one macOS runner at a time (``max-parallel: 1``, :func:`macos_budget_violations`).
_AFTER_THE_MACOS_SUITE = ("security-macos", "planned-ids-macos")


def macos_suite_first_violations(tree: Tree) -> list[str]:
    """Each leg in ``_AFTER_THE_MACOS_SUITE`` waits on ``macos``, and still runs whatever the
    suite concluded, so a red suite shard never skips it."""
    jobs = _breadth_jobs(tree)
    if "macos" not in jobs:
        return ["breadth.yml has no macos job"]
    bad: list[str] = []
    for job_id in _AFTER_THE_MACOS_SUITE:
        if job_id not in jobs:
            bad.append(f"breadth.yml has no {job_id} job")
            continue
        if not _waits_on(jobs, job_id, "macos"):
            bad.append(f"breadth.yml:{job_id} does not wait on macos, so it runs beside its shards")
        if not _runs_whatever_its_needs_concluded(jobs[job_id]):
            bad.append(f"breadth.yml:{job_id} does not run whatever macos concluded")
    return bad


#: The macOS runners the account runs at once (the free plan's 5): a pull request's macOS shards
#: and breadth's macOS jobs share them.
_MACOS_RUNNERS = 5
#: The macOS runners one merge-tier run may hold of them: every one. Pull requests run one at a
#: time and a tested push holds none, so the merge tier's macOS suite runs in as many shards as
#: the account has runners, and breadth's macOS jobs queue behind a run that holds them (their
#: timeouts count only the minutes they run). This was three until the suite's shards ran 20 to 25
#: minutes, the longest leg of every merge (#3915, #3923).
_MERGE_MACOS = _MACOS_RUNNERS
#: The macOS runners breadth's jobs may hold at once, so a pull request's run beside a night's
#: breadth still starts two of its shards at once: the `macos` leg (one shard at a time,
#: ``max-parallel: 1``) or the legs that wait for it, whichever holds more, and the legs beside it.
_BREADTH_MACOS = 3
#: The workers one merge-tier macOS job may run: a macOS runner's memory holds it there (#3923).
#: A count of its own, never ``auto``, which reads the host's CPUs and not its memory.
_MACOS_WORKERS = 2


def _macos_at_once(job: dict[str, Any]) -> int:
    """The macOS runners one job holds at once: its matrix's legs (each list's values crossed,
    and each ``include`` entry), capped by ``max-parallel``; none for a job off macOS."""
    if not any(re.fullmatch(r"macos-.+", label, re.IGNORECASE) for label in _runner_labels(job)):
        return 0
    strategy = job.get("strategy") or {}
    matrix = strategy.get("matrix") or {}
    legs = 1
    if isinstance(matrix, dict):
        for key, values in matrix.items():
            if key not in ("include", "exclude") and isinstance(values, list):
                legs *= len(values)
        legs += len(matrix.get("include") or []) - len(matrix.get("exclude") or [])
    cap = strategy.get("max-parallel")
    return min(legs, cap) if isinstance(cap, int) and cap > 0 else legs


def _macos_workers(name: str, workers: str, *, expect: str = "", fails: bool = True) -> Control:
    """The merge tier's macOS suite planted at ``workers``: its count and its pytest's ``-n``,
    which the catalogue's loader holds equal."""
    return _plants(
        name,
        catalogue.CATALOGUE,
        (
            (
                f"timeout_min = 50\nworkers = 2\nshards = {_MACOS_SHARDS}\n",
                f"timeout_min = 50\nworkers = {workers}\nshards = {_MACOS_SHARDS}\n",
            ),
            (
                '"pytest", "-n", "2", "--dist", "worksteal", "-q", "tests", "--shard-list"',
                f'"pytest", "-n", "{workers.strip(chr(34))}", "--dist", "worksteal", "-q", "tests",'
                ' "--shard-list"',
            ),
        ),
        expect=expect,
        fails=fails,
    )


def breadth_macos_at_once(tree: Tree) -> tuple[int, str]:
    """The macOS runners breadth's jobs hold at once, and how: the legs beside the ``macos``
    leg, and the larger of that leg (capped by ``max-parallel``) and the legs that wait for it,
    which run after it but may run beside a leg still going (``rails-macos``, say, on a night
    whose suite shards were carried and took a minute each)."""
    jobs = _breadth_jobs(tree)
    suite = _macos_at_once(jobs.get("macos") or {})
    after = beside = 0
    for job_id, job in jobs.items():
        if job_id == "macos" or not isinstance(job, dict):
            continue
        if _waits_on(jobs, job_id, "macos"):
            after += _macos_at_once(job)
        else:
            beside += _macos_at_once(job)
    how = f"{beside} beside the macos leg, and the larger of its {suite} and the {after} after it"
    return beside + max(suite, after), how


def macos_budget_violations(tree: Tree) -> list[str]:
    """A merge-tier run holds at most :data:`_MERGE_MACOS` macOS runners, every one the account
    runs, each running at most :data:`_MACOS_WORKERS` workers: the merge tier's macOS shards,
    which merge.yml runs, as the catalogue gives them. Breadth's macOS jobs hold at most
    :data:`_BREADTH_MACOS` at once (:func:`breadth_macos_at_once`), so a night's breadth always
    leaves a pull request's run some of its shards, and queues behind a run that holds the rest."""
    cat = catalogue.load(tree.get(catalogue.CATALOGUE, ""), catalogue.CATALOGUE)
    merge = sum(j.shards for j in cat.tier("merge") if j.platform.startswith("macos"))
    bad: list[str] = []
    for job in cat.tier("merge"):
        if not job.platform.startswith("macos"):
            continue
        workers = job.workers
        if not isinstance(workers, int) or isinstance(workers, bool) or workers > _MACOS_WORKERS:
            bad.append(
                f"{job.id} runs {workers!r} workers on a macOS runner, not a count of at most the"
                f" {_MACOS_WORKERS} its memory holds"
            )
    if merge > _MERGE_MACOS:
        bad.append(
            f"the merge tier's run holds {merge} macOS runners, more than the {_MERGE_MACOS} the"
            " account runs"
        )
    held, how = breadth_macos_at_once(tree)
    if held > _BREADTH_MACOS:
        bad.append(
            f"breadth's macOS jobs hold {held} runners at once ({how}), more than the"
            f" {_BREADTH_MACOS} breadth may hold"
        )
    return bad


#: Each union job of breadth.yml and the suite legs (job ids) it holds to their planned suites.
#: The canary's red is the next image's news, never the run's (its job is continue-on-error), so
#: its union is a job of its own, advisory as the canary is, and the gating union names it
#: nowhere and waits on none of its jobs.
_UNIONS: dict[str, tuple[str, ...]] = {
    "shard-union": ("ubuntu", "macos", "arm", "coverage-shard", "random-order"),
    "shard-union-canary": ("canary",),
}
#: The jobs whose red is not a verdict on the run: the canary, its planned collection and its
#: union, each continue-on-error.
_ADVISORY_UNION_JOBS = frozenset({"canary", "planned-ids-canary", "shard-union-canary"})
#: A suite leg's env a collection need not share: the Python is ``PY``, held on its own, and the
#: coverage data file changes no test id.
_COLLECTION_NEUTRAL_ENV = frozenset({"PY", "COVERAGE_FILE"})
_SHARD_LIST = '--shard-list "$RUNNER_TEMP/shard.txt"'


def _words_after(job: dict[str, Any], flag: str) -> list[str]:
    """Every value *flag* takes in the job's ``run:`` text, a double-quoted one whole (it may
    hold ``${{ matrix.shard }}``), quotes stripped."""
    pattern = rf'(?:^|\s){re.escape(flag)}[\s=]+(?:"([^"]*)"|(\S+))'
    return [m.group(1) or m.group(2) for m in re.finditer(pattern, _runs(job))]


def _union_legs(job: dict[str, Any]) -> list[str]:
    """The ``--leg`` values of the job's ``shards union`` commands."""
    legs: list[str] = []
    for words in _commands(_runs(job)):
        pairs = list(zip(words, words[1:]))
        if ("shards", "union") in pairs:
            legs += [b for a, b in pairs if a == "--leg"]
    return legs


def _uploads(job: dict[str, Any]) -> list[str]:
    return [
        str((step.get("with") or {}).get("name", ""))
        for step in job.get("steps") or []
        if _action(step) == "actions/upload-artifact"
    ]


def _leg_python(job: dict[str, Any]) -> str:
    pythons = _matrix_values(job, "python")
    return pythons[0] if len(pythons) == 1 else str((job.get("env") or {}).get("PY", ""))


def _setup_uv_pythons(job: dict[str, Any]) -> list[str]:
    """Each setup-uv step's ``python-version``, as written: the interpreter ``uv sync`` builds
    the venv on, so the one ``uv run pytest`` collects on (``PY`` only names the one
    ``uv python find`` returns)."""
    return [
        str((step.get("with") or {}).get("python-version", ""))
        for step in job.get("steps") or []
        if _action(step) == "astral-sh/setup-uv"
    ]


def _continues_on_error(job: dict[str, Any]) -> bool:
    return str(job.get("continue-on-error", "")).strip().lower() == "true"


def _union_leg_violations(jobs: dict[str, Any], leg: str) -> list[str]:
    """A suite leg's shards write and upload ids named for their own leg and shard, and its
    planned collection runs on the leg's own runner, Python (its ``PY`` and setup-uv's, which the
    venv its pytest collects in is built on) and env, and writes and uploads its own file."""
    job, planned_id = jobs[leg], f"planned-ids-{leg}"
    planned = jobs.get(planned_id)
    shard = "${{ matrix.shard }}"
    own = f"$RUNNER_TEMP/ids/{shards.SHARD_IDS.format(leg=leg, shard=shard)}"
    bad = [
        f"breadth.yml:{leg} writes its ids to {path}, not {own}"
        for path in _words_after(job, "--shard-ids")
        if path != own
    ]
    if not _words_after(job, "--shard-ids"):
        bad.append(f"breadth.yml:{leg} passes no --shard-ids")
    if f"shard-ids-{leg}-{shard}" not in _uploads(job):
        bad.append(f"breadth.yml:{leg} uploads no shard-ids-{leg}-{shard}")
    if planned is None:
        return [*bad, f"breadth.yml has no {planned_id} job"]
    if planned.get("runs-on") != job.get("runs-on"):
        bad.append(
            f"breadth.yml:{planned_id} runs on {planned.get('runs-on')}, "
            f"not {leg}'s {job.get('runs-on')}"
        )
    want_py, got_py = _leg_python(job), str((planned.get("env") or {}).get("PY", ""))
    if got_py != want_py:
        bad.append(f"breadth.yml:{planned_id} collects on Python {got_py}, not {leg}'s {want_py}")
    if _setup_uv_pythons(planned) != [want_py]:
        bad.append(
            f"breadth.yml:{planned_id}'s setup-uv sets up Python {_setup_uv_pythons(planned)}, "
            f"not {leg}'s {want_py}, so its pytest collects on another"
        )
    env = {k: v for k, v in (job.get("env") or {}).items() if k not in _COLLECTION_NEUTRAL_ENV}
    planned_env = {
        k: v for k, v in (planned.get("env") or {}).items() if k not in _COLLECTION_NEUTRAL_ENV
    }
    if planned_env != env:
        bad.append(f"breadth.yml:{planned_id}'s env is {planned_env}, not {leg}'s {env}")
    file = f"$RUNNER_TEMP/ids/{shards.PLANNED_IDS.format(leg=leg)}"
    if _words_after(planned, "--shard-ids") != [file]:
        bad.append(f"breadth.yml:{planned_id} does not write its ids to {file} alone")
    if planned_id not in _uploads(planned):
        bad.append(f"breadth.yml:{planned_id} uploads no {planned_id}")
    universe = [
        words[words.index("--out") + 1].strip('"')
        for words in _commands(_runs(planned))
        if ("shards", "universe") in zip(words, words[1:]) and "--out" in words[:-1]
    ]
    collected = [
        words
        for words in _commands(_runs(planned))
        if "pytest" in words and "--collect-only" in words
    ]
    if len(universe) != 1 or len(collected) != 1:
        bad.append(f"breadth.yml:{planned_id} does not collect `shards universe`'s files")
    elif [b.strip('"') for a, b in zip(collected[0], collected[0][1:]) if a == "--shard-list"] != (
        universe
    ):
        bad.append(f"breadth.yml:{planned_id} collects a list other than `shards universe`'s")
    return bad


def shard_union_violations(tree: Tree) -> list[str]:
    """Each suite leg's shards are held, test id by test id, to a collection of the planned
    suite made on that leg's own runner and Python, by the one union that names the leg.

    Every suite shard (a pytest over this run's shard list) is in exactly one union; each
    union names exactly its legs, each with the leg's own shard count, waits on each and on its
    collection, and runs whatever they concluded. The gating union is never continue-on-error and
    waits on no advisory job; the canary's collection and union are continue-on-error, as the
    canary is."""
    jobs = _breadth_jobs(tree)
    bad: list[str] = []
    suites = {job_id for job_id, job in jobs.items() if _SHARD_LIST in _runs(job)}
    held = [leg for legs in _UNIONS.values() for leg in legs]
    bad += [
        f"breadth.yml:{leg} runs suite shards that no union holds to its planned suite"
        for leg in sorted(suites - set(held))
    ]
    for union, legs in _UNIONS.items():
        job = jobs.get(union)
        if job is None:
            bad.append(f"breadth.yml has no {union} job")
            continue
        want = []
        for leg in legs:
            if leg not in jobs:
                bad.append(f"breadth.yml has no {leg} job")
                continue
            want.append(f"{leg}:{len(_matrix_values(jobs[leg], 'shard'))}")
            bad += _union_leg_violations(jobs, leg)
            for needed in (leg, f"planned-ids-{leg}"):
                if needed not in _needs(job):
                    bad.append(f"breadth.yml:{union} does not wait on {needed}")
        if sorted(_union_legs(job)) != sorted(want):
            bad.append(f"breadth.yml:{union} holds {_union_legs(job)}, not {want}")
        if not _runs_whatever_its_needs_concluded(job):
            bad.append(f"breadth.yml:{union} does not run whatever its legs concluded")
        advisory = union in _ADVISORY_UNION_JOBS
        if _continues_on_error(job) != advisory:
            bad.append(
                f"breadth.yml:{union} is "
                + ("not " if advisory else "")
                + "continue-on-error, so "
                + ("the canary's red is the run's" if advisory else "its red is swallowed")
            )
        if not advisory:
            bad += [
                f"breadth.yml:{union} waits on {other}, whose red is not the run's"
                for other in sorted(set(_needs(job)) & _ADVISORY_UNION_JOBS)
            ]
    for job_id in sorted(_ADVISORY_UNION_JOBS - {"canary"}):
        if job_id in jobs and not _continues_on_error(jobs[job_id]):
            bad.append(f"breadth.yml:{job_id} is not continue-on-error, so the canary's red is")
    return bad


#: The randomized-order leg's seeds (#3949): the run's number with 1 and with 2, as written.
_RANDOM_ORDER_SEEDS = ("${{ github.run_number }}1", "${{ github.run_number }}2")
#: A flag's value in a ``run:`` line: quoted, an expression with what follows it, or a word.
_FLAG_VALUE = r'[\s=]+("[^"]*"|\$\{\{[^}]*\}\}\S*|\S+)'
_ALWAYS = frozenset({"always()", "${{ always() }}"})
_WHATEVER_IT_CONCLUDED = _ALWAYS | {"!cancelled()", "${{ !cancelled() }}"}


def _flag_values(step: dict[str, Any], flag: str) -> list[str]:
    """Every value *flag* takes in the step's ``run:`` text, quotes stripped and each
    expression's spacing as ``${{ x }}``."""
    found = re.findall(re.escape(flag) + _FLAG_VALUE, str(step.get("run", "")))
    return [re.sub(r"\$\{\{\s*(.*?)\s*\}\}", r"${{ \1 }}", value.strip('"')) for value in found]


def random_order_violations(tree: Tree) -> list[str]:
    """The randomized-order leg runs its shard in two orders, each its own step, seeded the
    run's number with 1 and with 2 (so a rerun repeats them, and no two runs share one), the
    second whatever the first concluded, each in a basetemp of its own under ``/var/tmp``, outside
    the runner's home. In pytest's shared one, a nested pytest the second order's tests start
    deletes the first order's whole finished tree at its end (pytest keeps 3 sessions), one unlink
    at a time through the real-home guard, and that ran a test's child past the suite's timeout
    (run 40199713134). Its entry keeps exactly those seeds, in that order, beside its inputs and
    never among them. It uploads its order files, leak reports and file list, whatever it
    concluded, after every step that writes them."""
    job = _breadth_jobs(tree).get("random-order")
    if job is None:
        return ["breadth.yml has no random-order job"]
    where = "breadth.yml:random-order"
    steps = list(job.get("steps") or [])
    bad: list[str] = []
    orders = [
        (i, s)
        for i, s in enumerate(steps)
        if any(_root_pytest(w) for w in _commands(str(s.get("run", ""))))
        and _SHARD_LIST in str(s.get("run", ""))
    ]
    seeds = [_flag_values(s, "--random-order-seed") for _, s in orders]
    if seeds != [[seed] for seed in _RANDOM_ORDER_SEEDS]:
        bad.append(
            f"{where} seeds its orders {seeds}, not one step each seeded"
            f" {', '.join(_RANDOM_ORDER_SEEDS)} in turn"
        )
    temps = [_flag_values(s, "--basetemp") for _, s in orders]
    if any(len(t) != 1 or not t[0].startswith("/var/tmp/") for t in temps) or len(
        {t[0] for t in temps if t}
    ) != len(temps):
        bad.append(
            f"{where} runs its orders in basetemps {temps}, not each in one of its own under"
            " /var/tmp, so a nested pytest in the second deletes the first's finished tree"
        )
    for i, step in orders:
        condition = str(step.get("if", "")).strip()
        if condition and condition not in _WHATEVER_IT_CONCLUDED:
            bad.append(
                f"{where} runs its order {step.get('id') or i + 1} under `if: {condition}`,"
                " not whatever the steps before it concluded"
            )
    recorded = [
        _flag_values(s, "--seeds")
        for s in steps
        if "breadth_evidence.py ran" in str(s.get("run", ""))
    ]
    if recorded != [[",".join(_RANDOM_ORDER_SEEDS)]]:
        bad.append(
            f"{where}'s entry keeps the seeds {recorded}, not its orders'"
            f" {','.join(_RANDOM_ORDER_SEEDS)}"
        )
    for s in steps:
        if "breadth_evidence.py inputs" in str(s.get("run", "")) and "seeds=" in str(s["run"]):
            bad.append(
                f"{where} records its seeds among its inputs, so a green on the next night's"
                " seeds reads as changed inputs and closes a red order's issue"
            )
    uploads = [
        (i, s)
        for i, s in enumerate(steps)
        if _action(s) == "actions/upload-artifact"
        and str((s.get("with") or {}).get("name", "")) == "random-order-${{ matrix.shard }}"
    ]
    if len(uploads) != 1:
        return [*bad, f"{where} uploads no one random-order-${{{{ matrix.shard }}}}"]
    at, upload = uploads[0]
    if str(upload.get("if", "")).strip() not in _ALWAYS:
        bad.append(f"{where} uploads its orders under `if: {upload.get('if')}`, not always()")
    paths = [p.strip() for p in str((upload.get("with") or {}).get("path", "")).splitlines()]
    if not {"reports/random-order/", "reports/random-order"} & set(paths):
        bad.append(f"{where} uploads {paths}, not the whole of reports/random-order/")
    writers = [i for i, s in enumerate(steps) if "reports/random-order" in str(s.get("run", ""))]
    late = [steps[i].get("name") or i + 1 for i in [*writers, *(i for i, _ in orders)] if i > at]
    if late:
        bad.append(f"{where} uploads its orders before {late} wrote to them")
    return bad


def _security_leg(job: dict[str, Any]) -> bool:
    """A leg that runs security-marked tests, by any one command of its steps: ``tests/security``,
    ``-m security``, or the suite itself (a pytest over ``tests`` alone, as every suite shard
    runs), which holds them. A pytest that only collects (``catalogue.collect_only``) runs no
    test, so it is none of these; every other command is read as it was."""
    for step in job.get("steps") or []:
        for words in _commands(str(step.get("run", ""))):
            if catalogue.collect_only(words):
                continue
            text = " ".join(words)
            if "tests/security" in text or re.search(r"-m\s+['\"]?security", text):
                return True
            if _runs_the_suite(words) and "tests" in _pytest_args(words):
                return True
    return False


def _record_legs(job_id: str, job: dict[str, Any]) -> frozenset[str]:
    """The record keys' legs a breadth job writes (``entry/<leg>__``), or its id when it writes
    none."""
    keys = {m for s in job.get("steps") or [] for m in _ENTRY.findall(str(s.get("run", "")))}
    return frozenset(keys or {job_id})


def non_reusable_violations(tree: Tree) -> list[str]:
    """The legs the record never reuses are exactly ``NON_REUSABLE``, none of them asks to carry
    an earlier result, and every leg that runs security-marked tests is one of them (#3909: a
    security result is never carried, so the suite runs every night)."""
    jobs = _breadth_jobs(tree)
    written = {leg for job_id, job in jobs.items() for leg in _record_legs(job_id, job)}
    bad = [
        f"NON_REUSABLE names {leg}, which no breadth.yml job writes"
        for leg in sorted(breadth_evidence.NON_REUSABLE - written)
    ]
    for job_id, job in jobs.items():
        legs = _record_legs(job_id, job)
        never = legs & breadth_evidence.NON_REUSABLE
        decides = "breadth_evidence.py decide" in _runs(job)
        if never and decides:
            bad.append(f"breadth.yml:{job_id} is never reused, yet it carries an earlier result")
        if _security_leg(job) and legs - breadth_evidence.NON_REUSABLE:
            bad.append(f"breadth.yml:{job_id} runs security tests but is not in NON_REUSABLE")
    return bad


def evidence_artifact_violations(tree: Tree) -> list[str]:
    """The run's record is uploaded once, under its digest, and kept 90 days."""
    uploads = [
        step
        for step in (_breadth_jobs(tree).get("report") or {}).get("steps") or []
        if _action(step) == "actions/upload-artifact"
        and str((step.get("with") or {}).get("name", "")).startswith(breadth_evidence.PREFIX)
    ]
    if len(uploads) != 1:
        return [f"breadth.yml:report uploads {len(uploads)} evidence records, not one"]
    given = uploads[0].get("with") or {}
    bad: list[str] = []
    if given["name"] != breadth_evidence.PREFIX + "${{ steps.report.outputs.digest }}":
        bad.append(f"breadth.yml's evidence artifact is not named by its digest: {given['name']}")
    if given.get("retention-days") != 90:
        bad.append(f"breadth.yml's evidence is kept {given.get('retention-days')} days, not 90")
    return bad


def report_permissions_violations(tree: Tree) -> list[str]:
    """``report`` holds ``issues: write`` and no other write, and writes issues only on main."""
    report = _breadth_jobs(tree).get("report") or {}
    permissions = report.get("permissions")
    bad: list[str] = []
    if not isinstance(permissions, dict):
        bad.append(f"breadth.yml:report's permissions are not listed: {permissions!r}")
    else:
        writes = sorted(k for k, v in permissions.items() if v == "write")
        if writes != ["issues"]:
            bad.append(f"breadth.yml:report writes {writes}, not only issues")
    for step in report.get("steps") or []:
        run = str(step.get("run", ""))
        writes_issues = "breadth_evidence.py issues" in run or "gh issue" in run
        if writes_issues and _ON_MAIN not in str(step.get("if", "")):
            bad.append(f"breadth.yml:report's {step.get('name')!r} writes issues off main")
    return bad


#: A step that leaves the uv-managed interpreter installed, so a later `uv python find` finds it.
_INSTALLS_PYTHON = re.compile(r"\buv (?:venv|sync|tool install|python install)\b")
_ENTRY = re.compile(r"entry/([\w.-]+)__")


#: breadth.yml's jobs that run no shard: the earlier record, the coverage combine, the report.
_NOT_A_LEG = frozenset({"freshness", "coverage", "report"})
#: The legs whose outside state moves daily, so the run's night is one of their inputs.
_NIGHTLY_LEGS = frozenset({"install-smoke", "audit"})
_NIGHT_INPUT = '--external "night=${NIGHT:?freshness named no night}"'
_FRESHNESS_NIGHT = "${{ needs.freshness.outputs.night }}"
_FRESHNESS_TREE = "${{ needs.freshness.outputs.tree }}"
#: The only trees a breadth step may name: its own checkout's, or freshness's, with no fallback.
_TREE_ARGS = frozenset(
    {"--tree \"$(git rev-parse 'HEAD^{tree}')\"", '--tree "${TREE:?freshness named no tree}"'}
)
#: After the inputs are written, an unreadable installer exits nonzero.
_UNREADABLE_IS_RED = re.compile(r"\bunreadable \]; then\n(?:[^\n]*\n)*?[ \t]*exit [1-9]")
_DECIDE_KEY = re.compile(r'decide --key "([\w.-]+)/')


def _shard_count(job: dict[str, Any]) -> int:
    """How many shards a job's matrix runs: the product of every dimension. An ``exclude`` is
    refused on its own; an ``include`` that only adds keys to a combination adds none."""
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict):
        return 1
    count = 1
    for key, values in matrix.items():
        if key not in ("include", "exclude") and isinstance(values, list):
            count *= len(values)
    return count


def _night_violations(jobs: dict[str, Any], cron: list[str]) -> list[str]:
    bad: list[str] = []
    hour, minute = breadth_evidence.NIGHT_STARTS_UTC
    if cron != [f"{minute} {hour} * * *"]:
        bad.append(f"breadth.yml's schedule {cron} is not NIGHT_STARTS_UTC's slot")
    freshness = jobs.get("freshness") or {}
    if (freshness.get("outputs") or {}).get("night") != "${{ steps.night.outputs.night }}":
        bad.append("breadth.yml:freshness publishes no night")
    step = next((s for s in freshness.get("steps") or [] if s.get("id") == "night"), {})
    run = " ".join(str(step.get("run", "")).split())
    if (
        "actions/runs/$GITHUB_RUN_ID" not in run
        or "--jq .created_at" not in run
        or "breadth_evidence.py night --created-at" not in run
    ):
        bad.append("breadth.yml:freshness's night is not its run's created_at, as `night` reads it")
    return bad


def _as_run(tree: Tree, job_id: str, step: dict[str, Any], bad: list[str]) -> str:
    """The shell a step runs: its ``run:``, then the shell lines of each repository script it
    runs as ``sh <path>`` and of each script those run, as tools/ci/supply_chain.py reads them.
    A script the tree does not hold is a violation, so a rename cannot leave a row reading
    nothing."""
    run = str(step.get("run", ""))
    texts = [run]
    for path, text in supply_chain.scripts_run(tree.get, run):
        if text is None:
            bad.append(f"breadth.yml:{job_id} runs {path}, which is not a file")
        else:  # its shell, not its prose: a script's comments name what it does not do
            texts += [line for _, line in shell_installs.read_script(text).lines]
    return "\n".join(texts)


def breadth_shards_violations(tree: Tree) -> list[str]:
    """Every breadth shard records what it ran, so the record holds what the run did:

    - every job but freshness, coverage and report is a leg, and writes entries under exactly
      one key, which no other job writes and its ``decide --key`` names; the keys and their
      matrices' shard counts (every dimension) are exactly ``SHARDS``;
    - a managed Python is installed before the first ``uv python find`` (setup-uv only names
      one, and ``uv python find`` never downloads), in a step's ``run:`` or a script it runs;
    - a decision that crashes leaves the shard to run (``continue-on-error``);
    - every ``--tree`` is the checkout's or freshness's, with no fallback, and a ``TREE`` is
      freshness's as it is; the served installer is never hashed from a ``curl … |
      sha256sum`` pipe, continued lines included, and an unreadable one exits red after its
      inputs are written;
    - the installer smoke and the audit record freshness's night, which is the run's
      ``created_at`` at the cron's slot (``NIGHT_STARTS_UTC``);
    - the coverage combine reads a failed data download as a failure, never as "carried".
    """
    workflow = _workflow(tree, "breadth.yml") or {}
    jobs = _breadth_jobs(tree)
    on = _triggers(workflow)
    schedule = on.get("schedule") if isinstance(on, dict) else None
    cron = [str(s.get("cron")) for s in schedule or [] if isinstance(s, dict)]
    bad: list[str] = _night_violations(jobs, cron)
    found: dict[str, int] = {}
    for job_id, job in jobs.items():
        steps = job.get("steps") or []
        runs = [_as_run(tree, job_id, s, bad) for s in steps]
        keys = {m for run in runs for m in _ENTRY.findall(run)}
        if len(keys) > 1 or (not keys and job_id not in _NOT_A_LEG):
            bad.append(f"breadth.yml:{job_id} writes entries under {sorted(keys)}, not one key")
        for key in keys:
            if key in found:
                bad.append(f"breadth.yml:{job_id} writes {key}'s entries, as another job does")
            found[key] = _shard_count(job)
        decided = {m for run in runs for m in _DECIDE_KEY.findall(run)}
        if decided and decided != keys:
            bad.append(f"breadth.yml:{job_id} decides as {sorted(decided)}, writes {sorted(keys)}")
        if (job.get("strategy") or {}).get("matrix", {}).get("exclude"):
            bad.append(f"breadth.yml:{job_id} excludes some of its shards")
        if keys & _NIGHTLY_LEGS and not any(
            _NIGHT_INPUT in run
            and (s.get("env") or {}).get("NIGHT") == _FRESHNESS_NIGHT
            and "breadth_evidence.py inputs" in run
            for s, run in zip(steps, runs, strict=True)
        ):
            bad.append(f"breadth.yml:{job_id}: {sorted(keys)} does not record freshness's night")
        first_find = next((i for i, run in enumerate(runs) if "uv python find" in run), None)
        if first_find is not None and not any(
            _INSTALLS_PYTHON.search(r) for r in runs[:first_find]
        ):
            bad.append(f"breadth.yml:{job_id} runs `uv python find` before any managed Python")
        for step in steps:
            raw = str(step.get("run", ""))
            run = raw.replace("\\\n", " ")  # a continued line is one line
            if "breadth_evidence.py decide" in run and step.get("continue-on-error") is not True:
                bad.append(f"breadth.yml:{job_id}: a crashed decision skips the shard")
            for arg in re.findall(r"--tree\s+(?:\"[^\"]*\"|\S+)", run):
                if " ".join(arg.split()) not in _TREE_ARGS:
                    bad.append(f"breadth.yml:{job_id}: --tree is not the checkout's: {arg}")
            if "${TREE" in run and (step.get("env") or {}).get("TREE") != _FRESHNESS_TREE:
                bad.append(f"breadth.yml:{job_id}: TREE is not freshness's tree as it is")
            if re.search(r"curl[^\n|]*\|\s*sha256sum", run):
                bad.append(f"breadth.yml:{job_id} hashes a piped fetch")
            at = raw.find("breadth_evidence.py inputs")
            if (
                "personalclaw.dev/install" in raw
                and "sha256sum" in raw
                and (at < 0 or not _UNREADABLE_IS_RED.search(raw[at:]))
            ):
                bad.append(f"breadth.yml:{job_id}: an unreadable installer is not red")
            if (
                _action(step) == "actions/download-artifact"
                and "coverage-data" in str((step.get("with") or {}).get("pattern", ""))
                and step.get("continue-on-error")
            ):
                bad.append(f"breadth.yml:{job_id}: a failed coverage download is not a failure")
    if found != dict(breadth_evidence.SHARDS):
        bad.append(f"breadth.yml's shards {found} are not SHARDS {dict(breadth_evidence.SHARDS)}")
    return bad


#: The full ref: `$GITHUB_REF_NAME` is `main` for a tag named `main`, which is off main.
_REF = '--ref "$GITHUB_REF"'
_ATTEMPT = '--attempt "$GITHUB_RUN_ATTEMPT"'


def freshness_ref_violations(tree: Tree) -> list[str]:
    """Off main nothing is reused, and a rerun keeps every red, which rests on the full ref
    and the run attempt reaching each command:

    - ``freshness`` and every ``decide`` pass the full ref (``$GITHUB_REF``, never the short
      name a tag called ``main`` shares), so off main neither reads nor carries a result;
    - ``report`` passes the ref and the run attempt, and every ``decide`` and ``ran`` the
      attempt, so each entry and record names the attempt that wrote it;
    - every leg's job is named ``breadth-leg <key>``, the name ``report`` holds each key to in
      the run's latest jobs;
    - the map is downloaded only on main: no never-reused leg downloads it, and a tree-keyed
      shard's download is gated to ``refs/heads/main``."""
    jobs = _breadth_jobs(tree)
    bad: list[str] = []
    steps = (jobs.get("freshness") or {}).get("steps") or []
    calls = [str(s["run"]) for s in steps if "breadth_evidence.py previous" in str(s.get("run"))]
    if len(calls) != 1:
        bad.append(f"breadth.yml:freshness reads the earlier record {len(calls)} times, not once")
    elif _REF not in calls[0]:
        bad.append("breadth.yml:freshness reads main's runs whatever ref it runs on")
    steps = (jobs.get("report") or {}).get("steps") or []
    reports = [str(s["run"]) for s in steps if "breadth_evidence.py report" in str(s.get("run"))]
    if len(reports) != 1 or _REF not in reports[0] or (_ATTEMPT not in reports[0]):
        bad.append("breadth.yml:report does not name the ref and the run attempt")
    for job_id, job in jobs.items():
        steps = job.get("steps") or []
        keys = {m for s in steps for m in _ENTRY.findall(str(s.get("run", "")))}
        if keys:
            shard = "${{ matrix.shard }}" if _matrix_values(job, "shard") else "1"
            named = {breadth_evidence.job_name(f"{key}/{shard}") for key in keys}
            if job.get("name") not in named:
                bad.append(f"breadth.yml:{job_id} is named {job.get('name')!r}, not {named}")
        for step in steps:
            run = str(step.get("run", ""))
            if "breadth_evidence.py decide" in run and (_REF not in run or _ATTEMPT not in run):
                bad.append(f"breadth.yml:{job_id}'s decision names no full ref or no attempt")
            if "breadth_evidence.py ran" in run and _ATTEMPT not in run:
                bad.append(f"breadth.yml:{job_id}'s entry names no attempt")
            downloads_map = (
                _action(step) == "actions/download-artifact"
                and (step.get("with") or {}).get("name") == "breadth-previous"
            )
            if downloads_map and _record_legs(job_id, job) <= breadth_evidence.NON_REUSABLE:
                bad.append(f"breadth.yml:{job_id} downloads freshness's map, which it never reads")
            elif downloads_map and step.get("if") != "${{ github.ref == 'refs/heads/main' }}":
                bad.append(f"breadth.yml:{job_id} downloads the map off main, where none is read")
    return bad


#: The ``collection-parses`` leg's one pytest command, word for word. Any other word can narrow
#: what it collects, however it is spelled (a narrower path, ``--ignore=…``, ``--ignore-glob=…``,
#: ``--deselect``, ``-k``, ``-m``, another ``-c`` or ``--rootdir``), or unload the guard.
_COLLECTION_PARSES_COMMAND = (
    "uv run pytest --collect-only -qq -n0 -p collection_parse_guard tests"
    " --junitxml=reports/junit-collection-parses.xml"
).split()


def collection_parses_violations(tree: Tree) -> list[str]:
    """breadth.yml's ``collection-parses`` leg collects the whole suite with the parse guard
    loaded (``tests/collection_parse_guard.py``): its one pytest command is
    ``_COLLECTION_PARSES_COMMAND`` word for word, and no ``PYTEST_`` variable reaches it from the
    workflow's, the job's or a step's ``env``, or from a step's ``run`` (``$GITHUB_ENV``).
    Without the guard, or over part of the suite, the leg is green on a parse it never saw."""
    job = _breadth_jobs(tree).get("collection-parses")
    if job is None:
        return ["breadth.yml has no collection-parses leg"]
    commands = [words for words in _commands(_runs(job)) if "pytest" in words]
    if len(commands) != 1:
        return [f"breadth.yml:collection-parses runs {len(commands)} pytest commands, not one"]
    bad: list[str] = []
    if commands[0] != _COLLECTION_PARSES_COMMAND:
        bad.append(
            f"breadth.yml:collection-parses runs {' '.join(commands[0])!r}, not "
            f"{' '.join(_COLLECTION_PARSES_COMMAND)!r}"
        )
    workflow = _workflow(tree, "breadth.yml") or {}
    envs = [workflow.get("env"), job.get("env"), *(s.get("env") for s in job.get("steps") or [])]
    named = sorted({str(k) for env in envs for k in env or {} if str(k).startswith("PYTEST_")})
    if named or "PYTEST_" in _runs(job):
        bad.append(
            f"breadth.yml:collection-parses sets {named or 'a PYTEST_ variable in a run'}, "
            "which can narrow its collection or unload the guard"
        )
    return bad


def job_timeout_violations(tree: Tree) -> list[str]:
    """Every job carries its own ``timeout-minutes``, in every workflow.

    Without one a hung job holds its runner for GitHub's 360-minute default. Cancelling
    concurrency is no bound: it evicts a hung job only when a later run in the same group
    arrives, and a main push, a tag or a nightly may have none behind it. A non-cancelling
    group is worse: the hung job holds the lane for every run queued behind it. A job that
    calls a reusable workflow is exempt, because the callee's jobs carry the caps.
    """
    bad: list[str] = []
    for name, job_id, job in _jobs(tree):
        if job.get("uses"):
            continue
        cap = job.get("timeout-minutes")
        if cap is None:
            bad.append(f"{name}:{job_id} has no timeout-minutes")
        elif cap == "${{ matrix.timeout }}" and _planned_tier(name, job) is not None:
            # Each entry's cap is its catalogue job's timeout_min, which the loader holds to a
            # whole number of at least MIN_TIMEOUT_MIN.
            continue
        elif isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
            bad.append(f"{name}:{job_id} has timeout-minutes {cap!r}, not a positive integer")
    return bad


#: A word that names what pytest collects: a file, a directory under the repository, a node id.
_TEST_PATH = re.compile(r"^(?:tests|packages|src|harness|scripts|tools)(?:/|$)|\.py(?:::|$)")


def _pytest_args(words: list[str]) -> list[str]:
    """The words after ``pytest`` in a root pytest command."""
    return words[words.index("pytest") + 1 :] if "pytest" in words else []


def _runs_the_suite(words: list[str]) -> bool:
    """A root pytest that runs the suite, or a shard of it, rather than files it names.

    It is a suite run when nothing it collects is narrower than the whole suite: it names no
    path at all (pytest falls back to ``testpaths``), or names only ``tests`` itself, as every
    shard does with its ``--shard-list``. A step that names files or a
    narrower directory, such as ``lint``'s installer test or the scanner corpus, picks its own.
    """
    if not _root_pytest(words):
        return False
    args = _pytest_args(words)
    paths = [w for w in args if not w.startswith("-") and _TEST_PATH.search(w)]
    return all(p.rstrip("/") == "tests" for p in paths)


def _sets_workers(args: list[str]) -> bool:
    return any(
        w in ("-n", "--numprocesses")
        or w.startswith("--numprocesses=")
        or re.fullmatch(r"-n\S+", w)
        for w in args
    )


def suite_workers_violations(tree: Tree) -> list[str]:
    """Every pytest step that runs the suite names its own worker count with ``-n``.

    pyproject's ``addopts`` set no worker count, so a suite step that names none runs the whole
    suite in one process. A step that names its own files (see :func:`_runs_the_suite`) runs in
    one process by design and is not held to this.
    """
    bad: list[str] = []
    for name, job_id, _job, step in _steps(tree):
        for words in _commands(str(step.get("run", ""))):
            if _runs_the_suite(words) and not _sets_workers(_pytest_args(words)):
                bad.append(f"{name}:{job_id} runs the suite with no -n: {' '.join(words)}")
    entries, bad_catalogue = _tier_entries(tree)
    for entry in entries:
        for step in entry.command:
            words = list(step.argv)
            if _runs_the_suite(words) and not _sets_workers(_pytest_args(words)):
                bad.append(
                    f"the catalogue's {entry.id} runs the suite with no -n: {shlex.join(words)}"
                )
    return bad + bad_catalogue


_SHARD_LIST = '--shard-list "$RUNNER_TEMP/shard.txt"'
_PLAN_FILE = '--plan "$RUNNER_TEMP/shard-plan/plan.json"'
#: The job that plans each workflow's suite shards once for the run.
_PLANNERS = {"merge.yml": "shard-plan", "breadth.yml": "freshness"}
_PLAN_LEG = re.compile(r"--leg\s+([\w-]+):([\w.-]+):([\w.-]+):(\d+)")


def suite_shard_violations(tree: Tree) -> list[str]:
    """Every suite shard runs its share of its run's one plan (#3923):

    - one job of each workflow plans every leg (``shards plan``), and checks the plan
      (``shards check``) after making it;
    - each matrix job whose pytest runs the suite waits on that job, downloads the plan, lists
      its own leg's shard ``${{ matrix.shard }}`` of N before pytest, and runs pytest with that
      ``--shard-list``. N is the job's matrix size, and the plan names the leg with that N and
      the prefix of the job's junit artifact;
    - no suite step passes pytest-split's ``--splits`` or ``--group``.

    merge.yml's stage jobs run the catalogue's entries, so there the entry is the shard: an entry
    of the change or merge tier whose step runs the suite reads the plan (``shard-history``),
    lists its own leg's shard ``{shard}`` of its shard count before pytest, and runs pytest with
    that list; and merge.yml's plan names its leg with that count and a prefix its reports carry.
    """
    bad: list[str] = []
    leg_plans: dict[str, dict[str, tuple[str, int]]] = {}
    for name, planner in _PLANNERS.items():
        jobs = (_workflow(tree, name) or {}).get("jobs") or {}
        steps = (jobs.get(planner) or {}).get("steps") or []
        runs = [" ".join(str(s.get("run", "")).split()) for s in steps]
        plans = [i for i, r in enumerate(runs) if "tools.ci shards plan" in r]
        if len(plans) != 1:
            bad.append(f"{name}:{planner} does not plan the shards once")
            continue
        if not any(f"tools.ci shards check {_PLAN_FILE}" in r for r in runs[plans[0] + 1 :]):
            bad.append(f"{name}:{planner} uploads a plan it never checked")
        legs = {m[0]: (m[2], int(m[3])) for m in _PLAN_LEG.findall(runs[plans[0]])}
        leg_plans[name] = legs
        for job_id, job in jobs.items():
            steps = job.get("steps") or []
            texts = [" ".join(str(s.get("run", "")).split()) for s in steps]
            suite = [
                i
                for i, s in enumerate(steps)
                if any(_runs_the_suite(w) for w in _commands(str(s.get("run", ""))))
            ]
            shards = _matrix_values(job, "shard")
            if not suite or not shards:
                continue
            where, n, first = f"{name}:{job_id}", len(shards), suite[0]
            if any(_SHARD_LIST not in texts[i] for i in suite):
                bad.append(f"{where} runs the suite without its shard's list")
            if any(w.startswith(("--splits", "--group")) for i in suite for w in texts[i].split()):
                bad.append(f"{where} still splits with pytest-split")
            if planner not in _needs(job):
                bad.append(f"{where} does not wait on {planner}, which plans its shards")
            if not any(
                _action(s) == "actions/download-artifact"
                and (s.get("with") or {}).get("name") == "shard-plan"
                for s in steps[:first]
            ):
                bad.append(f"{where} does not download the run's shard plan before pytest")
            own = (f"--leg {job_id} --shard ${{{{ matrix.shard }}}}/{n} ",)
            if not any(
                f"tools.ci shards list {_PLAN_FILE}" in t
                and '--out "$RUNNER_TEMP/shard.txt"' in t
                and any(o in t + " " for o in own)
                for t in texts[:first]
            ):
                bad.append(f"{where} does not list its own shard of {n} before pytest")
            planned = legs.get(job_id)
            reports = [
                str((s.get("with") or {}).get("name", ""))
                for s in steps
                if _action(s) == "actions/upload-artifact"
            ]
            if planned is None or planned[1] != n:
                bad.append(f"{where}: the plan names it {planned}, not in {n} shards")
            elif not any(r.startswith(planned[0]) for r in reports):
                bad.append(f"{where}: no junit report is named {planned[0]}*, as the plan reads")
    entries, problems = _tier_entries(tree)
    return bad + problems + _suite_entry_violations(entries, leg_plans.get("merge.yml", {}))


#: How a suite entry lists its shard of the run's plan, less its leg and its share.
_ENTRY_LISTS = ("shards", "list", "--plan", "{runner_temp}/shard-plan/plan.json")
_ENTRY_LIST_OUT = ("--out", "{runner_temp}/shard.txt")


def _suite_entry_violations(
    entries: list[catalogue.Job], legs: dict[str, tuple[str, int]]
) -> list[str]:
    """Each change- or merge-tier entry whose step runs the suite runs its share of merge.yml's
    one plan, which ``legs`` (leg -> report prefix and shard count) gives."""
    bad: list[str] = []
    for entry in entries:
        steps = [list(step.argv) for step in entry.command]
        suite = [i for i, words in enumerate(steps) if _runs_the_suite(words)]
        if not suite:
            continue
        where, first = f"the catalogue's {entry.id}", suite[0]
        if any(not _contains(steps[i], ["--shard-list", "{runner_temp}/shard.txt"]) for i in suite):
            bad.append(f"{where} runs the suite without its shard's list")
        if any(w.startswith(("--splits", "--group")) for i in suite for w in steps[i]):
            bad.append(f"{where} still splits with pytest-split")
        if "shard-history" not in entry.external_inputs:
            bad.append(f"{where} does not read the run's shard plan (shard-history)")
        listed = [
            words
            for words in steps[:first]
            if _contains(words, list(_ENTRY_LISTS)) and _contains(words, list(_ENTRY_LIST_OUT))
        ]
        leg = next((w[w.index("--leg") + 1] for w in listed if "--leg" in w[:-1]), None)
        share = f"{{shard}}/{entry.shards}"
        if leg is None or not any(_contains(w, ["--leg", leg, "--shard", share]) for w in listed):
            bad.append(f"{where} does not list its own shard of {entry.shards} before pytest")
            continue
        named = legs.get(leg)
        if named is None or named[1] != entry.shards:
            bad.append(
                f"{where}: merge.yml's plan names {leg} {named}, not in {entry.shards} shards"
            )
        elif not entry.junit or not all(
            report.rsplit("/", 1)[-1].startswith(named[0]) for report in entry.junit
        ):
            bad.append(f"{where}: its reports are not named {named[0]}*, as the plan reads")
    return bad


_KEEP_GATE = (
    'if [ "$GITHUB_REF" = "refs/heads/main" ]; then keep="--keep-from-main breadth.yml"; fi'
)


def breadth_keeps_its_plan_violations(tree: Tree) -> list[str]:
    """On main, breadth keeps the newest earlier main run's shard plan (#3923, #3913).

    A breadth shard's list digest is one of its inputs, so a plan made afresh each night would
    hand every shard other files and no tree-keyed shard would ever carry. So ``freshness``
    passes ``--keep-from-main breadth.yml``, only on ``refs/heads/main`` (off main nothing is
    reused), names its own run so it never keeps its own plan, and uploads the plan for at least
    two days, so the next night can read it.
    """
    steps = (_breadth_jobs(tree).get("freshness") or {}).get("steps") or []
    runs = [str(s.get("run", "")) for s in steps]
    planning = [r for r in runs if "tools.ci shards plan" in r]
    if len(planning) != 1:
        return ["breadth.yml:freshness does not plan the shards once"]
    bad: list[str] = []
    text = planning[0]
    if _KEEP_GATE not in text or "--keep-from-main" in text.replace(_KEEP_GATE, ""):
        bad.append("breadth.yml:freshness does not keep main's earlier plan, and only on main")
    if '--run-id "$GITHUB_RUN_ID"' not in text:
        bad.append("breadth.yml:freshness does not name its own run, so it could keep its plan")
    uploads = [
        s
        for s in steps
        if _action(s) == "actions/upload-artifact"
        and (s.get("with") or {}).get("name") == "shard-plan"
    ]
    days = [int((s.get("with") or {}).get("retention-days") or 0) for s in uploads]
    if len(days) != 1 or days[0] < 2:
        bad.append(f"breadth.yml:freshness keeps its plan {days} days, too short for tomorrow")
    return bad


def no_cov_violations(tree: Tree) -> list[str]:
    """No workflow command, and no step of a change- or merge-tier catalogue entry, passes
    ``--no-cov``.

    Coverage is off unless a command asks for it with ``--cov``, so ``--no-cov`` undoes nothing.
    One left in a command reads as though a default still measured coverage.
    """
    entries, bad = _tier_entries(tree)
    return (
        [
            f"{name}:{job_id} passes --no-cov: {' '.join(words)}"
            for name, job_id, _job, step in _steps(tree)
            for words in _commands(str(step.get("run", "")))
            if "--no-cov" in words
        ]
        + [
            f"the catalogue's {entry.id} passes --no-cov: {shlex.join(step.argv)}"
            for entry in entries
            for step in entry.command
            if "--no-cov" in step.argv
        ]
        + bad
    )


# ── No fixed waits in tests ─────────────────────────────────────────────────────────────────

#: The shortest constant sleep a test may not take unlisted. Below it a sleep is a yield or a
#: short poll step; at it and above it is wall-clock time the suite pays on every leg.
_SLEEP_FLOOR_SECS = 0.5

#: Every constant sleep of the floor or more a test still takes, by ``(file, test)``, with why it
#: has to be a sleep. Each one proves something does NOT happen within a window, or waits out a
#: real clock the code under test reads; neither has an event a test could await instead.
SLEEPS_THAT_STAY: dict[tuple[str, str], str] = {
    (
        "tests/test_a_bounded_turn_is_stopped_only_when_it_goes_quiet.py",
        "test_the_owners_queued_message_is_not_bounded_even_when_it_goes_quiet",
    ): "the silence the turn's bound measures has to pass, for the turn not to be stopped in it",
    (
        "tests/test_a_process_a_run_started_ends_with_the_run.py",
        "test_a_daemon_a_loop_tasks_command_started_ends_with_the_loops_stop",
    ): "proves the process is still alive after its cycle's turn ended: an absence",
    (
        "tests/test_a_stop_ends_the_turn_and_nothing_runs_for_it_after.py",
        "test_an_agent_that_ignores_the_stop_runs_none_of_personalclaws_tools_after_it",
    ): "proves nothing more is spawned after the Stop: an absence",
    (
        "tests/test_agent_clis_start_only_when_asked.py",
        "test_a_gateway_start_starts_no_agent_cli",
    ): "proves no agent CLI starts after the gateway's boot: an absence",
    (
        "tests/test_an_mcp_start_is_never_cut_off_part_way.py",
        "test_a_start_meanwhile_waits_for_the_one_still_installing",
    ): "proves the second start is still waiting while the install runs: an absence",
    (
        "tests/test_channel_receivers_follow_the_channel.py",
        "test_a_start_that_outlives_its_bound_says_so",
    ): "waits past the real start bound the health check reads",
    (
        "tests/test_memory_recall_answers_within_its_budget.py",
        "test_a_recall_the_route_stopped_waiting_for_goes_no_further",
    ): "proves the abandoned recall goes no further once its model answers: an absence",
    (
        "tests/test_one_store_connection_serves_many_threads.py",
        "test_four_threads_on_one_store_read_whole_rows",
    ): "the window four threads contend for one store connection in",
    (
        "tests/test_session_search.py",
        "test_it_waits_while_a_foreground_request_holds_it",
    ): "proves the indexer indexes nothing while a request holds it: an absence",
    (
        "tests/test_signed_in_devices.py",
        "test_an_expired_sign_in_is_told_how_long_it_lasted",
    ): "a one-second sign-in, stamped in whole seconds, has to run out on the real clock",
    (
        "tests/test_signed_in_devices.py",
        "test_the_desktop_apps_own_window_whose_sign_in_ran_out_is_told_the_same",
    ): "a one-second sign-in, stamped in whole seconds, has to run out on the real clock",
    (
        "tests/test_signed_in_devices.py",
        "test_a_link_cannot_sign_a_browser_in_once_its_session_has_ended",
    ): "a one-second sign-in, stamped in whole seconds, has to run out on the real clock",
    (
        "tests/test_snapshot.py",
        "test_concurrent_snapshots_unique",
    ): "snapshot names are stamped to the second, so the second snapshot needs the next second",
}


def _sleep_aliases(tree: ast.Module) -> set[str]:
    """The names a module binds ``time`` and ``asyncio`` to, anywhere in it."""
    names = {"time", "asyncio"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.asname for a in node.names if a.asname and a.name in ("time", "asyncio")}
    return names


def _fixed_wait(node: ast.stmt, aliases: set[str]) -> float | None:
    """The seconds of a statement that is a constant sleep of the floor or more, or None."""
    if not isinstance(node, ast.Expr):
        return None
    call = node.value.value if isinstance(node.value, ast.Await) else node.value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "sleep"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in aliases
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and type(call.args[0].value) in (int, float)
    ):
        return None
    secs = float(call.args[0].value)
    return secs if secs >= _SLEEP_FLOOR_SECS else None


def _test_waits(fn: ast.FunctionDef | ast.AsyncFunctionDef, aliases: set[str]) -> list[float]:
    """The fixed waits a test's own body takes, in any block of it.

    A sleep inside a function the test defines (a stub that plays a hang) or handed to another
    call as a coroutine (``ensure_future(asyncio.sleep(999))``, a task the test cancels) is a
    stand-in, not a wait the test pays, so it is not read.
    """
    waits: list[float] = []
    todo: list[ast.stmt] = list(fn.body)
    while todo:
        stmt = todo.pop()
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        secs = _fixed_wait(stmt, aliases)
        if secs is not None:
            waits.append(secs)
        for field in ("body", "orelse", "finalbody"):
            todo += getattr(stmt, field, None) or []
        for handler in getattr(stmt, "handlers", None) or []:
            todo += handler.body
        for case in getattr(stmt, "cases", None) or []:
            todo += case.body
    return waits


def fixed_wait_violations(
    root: Path, listed: dict[tuple[str, str], str] | None = None
) -> list[str]:
    """No test takes a constant ``time.sleep`` / ``asyncio.sleep`` of half a second or more
    unless it is on :data:`SLEEPS_THAT_STAY` with its reason, and every listed site still sleeps.

    A fixed wait is wall-clock time every leg pays, and under load it is a wait too short. A test
    waits for the thing it is about instead: a run's status, a node's state, an event.
    """
    listed = SLEEPS_THAT_STAY if listed is None else listed
    bad: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _sleep_aliases(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("test_"):
                continue
            waits = _test_waits(fn, aliases)
            if not waits:
                continue
            site = (rel, fn.name)
            seen.add(site)
            if site not in listed:
                bad.append(
                    f"{rel}::{fn.name} sleeps {max(waits):g}s: wait for what it is about, "
                    "or list it in SLEEPS_THAT_STAY with why it has to be a sleep"
                )
    for site in sorted(set(listed) - seen):
        bad.append(f"{site[0]}::{site[1]} is listed in SLEEPS_THAT_STAY but no longer sleeps")
    for site, reason in sorted(listed.items()):
        if not reason.strip():
            bad.append(f"{site[0]}::{site[1]} is listed in SLEEPS_THAT_STAY with no reason")
    return bad


_SCAFFOLD_SLEEP = ("tests/test_waits.py", "test_it_must_wait")


def _sleep_scaffold(root: Path) -> None:
    """One listed test that sleeps, and tests whose stand-in sleeps are not waits."""
    _write(
        root,
        _SCAFFOLD_SLEEP[0],
        "import asyncio\nimport time\n\n\n"
        "def test_it_must_wait():\n    time.sleep(1)\n\n\n"
        "async def test_a_hang_is_played_by_a_stub():\n"
        "    async def hang():\n        await asyncio.sleep(60)\n\n"
        "    task = asyncio.ensure_future(asyncio.sleep(999))\n    task.cancel()\n\n\n"
        "async def test_a_yield_is_not_a_wait():\n    await asyncio.sleep(0.05)\n",
    )


def _sleep_check(root: Path) -> list[str]:
    if root == REPO:
        return fixed_wait_violations(root)
    return fixed_wait_violations(root, {_SCAFFOLD_SLEEP: "the scaffold's listed wait"})


def _append(rel: str, text: str) -> Callable[[Path], None]:
    def plant(root: Path) -> None:
        path = root / rel
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        _write(root, rel, existing + text)

    return plant


FIXED_WAITS = Row(
    name="no test takes an unlisted fixed wait of half a second or more",
    check=_sleep_check,
    scaffold=_sleep_scaffold,
    controls=(
        Control(
            "a planted time.sleep(1) in a test module",
            _append("tests/test_planted.py", "import time\n\n\ndef test_x():\n    time.sleep(1)\n"),
            expect="tests/test_planted.py::test_x sleeps 1s",
        ),
        Control(
            "a planted await asyncio.sleep(5) inside a with block",
            _append(
                "tests/test_planted.py",
                "import asyncio\nimport contextlib\n\n\nasync def test_y():\n"
                "    with contextlib.suppress(Exception):\n        await asyncio.sleep(5)\n",
            ),
            expect="test_y sleeps 5s",
        ),
        Control(
            "a sleep through an alias",
            _append(
                "tests/test_planted.py", "import time as _t\n\n\ndef test_z():\n    _t.sleep(0.5)\n"
            ),
            expect="test_z sleeps 0.5s",
        ),
        _plant(
            "a listed test that no longer sleeps",
            _SCAFFOLD_SLEEP[0],
            "    time.sleep(1)\n",
            "    pass\n",
            expect="no longer sleeps",
        ),
        Control(
            "a short poll step passes",
            _append(
                "tests/test_planted.py",
                "import asyncio\n\n\nasync def test_w():\n    await asyncio.sleep(0.4)\n",
            ),
            fails=False,
        ),
    ),
)


_BREADTH = ".github/workflows/breadth.yml"
#: The composite action the tier workflows' stage jobs set up their toolchain with.
_SETUP_ACTION = ".github/actions/setup/action.yml"


#: How many shards the merge tier's macOS suite runs in, as the tree's catalogue gives it. A
#: change of what a row names lands in two merges (#4584 step 4), these rows first, so they read
#: the count from the tree, and the macOS runners' row holds it to at most :data:`_MERGE_MACOS`.
_MACOS_SHARDS = next(j.shards for j in catalogue.read(REPO).jobs if j.id == "python-suite-macos")

#: python-suite-macos's two steps as the catalogue spells them.
_SUITE_MACOS_LEG = f'"--leg", "test-macos", "--shard", "{{shard}}/{_MACOS_SHARDS}"'
_SUITE_MACOS_PYTEST = (
    '"-n", "2", "--dist", "worksteal", "-q", "tests", "--shard-list", "{runner_temp}/shard.txt",'
    ' "--junitxml=reports/junit-test-macos-{shard}.xml"'
)


#: python-suite's pytest step, and breadth.yml's macOS suite leg's head, as the tree spells them.
_SUITE_PYTEST = (
    '"-n", "auto", "--dist", "worksteal", "-q", "tests", "--shard-list",'
    ' "{runner_temp}/shard.txt", "--junitxml=reports/junit-test-shard-{shard}.xml"'
)
_BREADTH_MACOS_HEAD = (
    "    runs-on: macos-26\n    timeout-minutes: 45\n    strategy:\n      fail-fast: false\n"
    "      max-parallel: 1\n      matrix:\n"
)


def _catalogue_alone(root: Path) -> None:
    """The scaffold of a toolchain row whose check reads the catalogue and nothing else."""
    _write(root, catalogue.CATALOGUE, (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8"))


def _merge_alone(root: Path) -> None:
    """The scaffold of a toolchain row whose check reads merge.yml and the catalogue its stage
    jobs' matrices come from, and nothing else."""
    for rel in (_MERGE_YML, catalogue.CATALOGUE):
        _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))


# ── No test walks a source root itself ───────────────────────────────────────────────────────
#
# A tree rail reads the package, web/src, docs and the workflows through tests/source_index.py,
# which walks each root once per worker. A test that walks one itself pays the walk (and usually a
# parse of everything it finds) again, inside its own time limit. The rule and its one recorded
# exemption live in tests/private_walkers.py.

_WALKER = "tests/test_walks.py"


def _walker_scaffold(root: Path) -> None:
    """A tree where the rule holds: a test reads the package through the index, one walks a
    folder of its own, and one lists a single folder of the package."""
    _write(
        root,
        "tests/test_reads_the_index.py",
        "import source_index\n\n\ndef test_it():\n    assert source_index.package_files()\n",
    )
    _write(
        root,
        "tests/test_walks_its_own.py",
        "def test_it(tmp_path):\n    assert not list(tmp_path.rglob('*'))\n",
    )
    _write(
        root,
        "tests/test_lists_one_folder.py",
        "from pathlib import Path\n\nSRC = Path(__file__).resolve().parents[1] / 'src' / "
        "'personalclaw'\n\n\ndef test_it():\n    assert list((SRC / 'sdk').glob('*.py'))\n",
    )


def _walker(text: str) -> Callable[[Path], None]:
    return lambda root: _write(root, _WALKER, "from pathlib import Path\n\n" + text)


NO_TEST_WALKS_A_SOURCE_ROOT_ITSELF = Row(
    name="no test walks a source root itself",
    check=private_walkers.private_walks,
    scaffold=_walker_scaffold,
    controls=(
        Control(
            "an rglob of the package, spelled from the test file, fails",
            _walker(
                "SRC = Path(__file__).resolve().parents[1] / 'src' / 'personalclaw'\n\n\n"
                "def test_it():\n    assert list(SRC.rglob('*.py'))\n"
            ),
        ),
        Control(
            "an rglob from a package module's __file__ fails",
            _walker(
                "import personalclaw\n\n\ndef test_it():\n"
                "    root = Path(personalclaw.__file__).parent\n"
                "    assert list(root.rglob('*.py'))\n"
            ),
        ),
        Control(
            "an os.walk of web/src fails",
            _walker("import os\n\n\ndef test_it():\n    assert list(os.walk('web/src'))\n"),
        ),
        Control(
            "a recursive glob.glob of docs fails",
            _walker(
                "import glob\n\n\ndef test_it():\n"
                "    assert glob.glob('docs/**/*.md', recursive=True)\n"
            ),
        ),
        Control(
            "a '**' glob of the workflows, through a name bound in the function, fails",
            _walker(
                "def test_it():\n    wf = Path('.github') / 'workflows'\n"
                "    assert list(wf.glob('**/*.yml'))\n"
            ),
        ),
        Control(
            "a recursive glob under the package, joined to a part read at run time, fails",
            _walker(
                "import glob\n\nROOT = Path(__file__).resolve().parents[1]\n\n\n"
                "def test_it():\n    for pattern in ('**/*.json',):\n"
                "        assert glob.glob(str(ROOT / 'src' / 'personalclaw' / pattern), "
                "recursive=True)\n"
            ),
        ),
        Control(
            "an rglob of the package from rails_repo's REPO fails",
            lambda root: _write(
                root,
                "tests/rails/test_walks_from_repo.py",
                "from rails_repo import REPO\n\n\ndef test_it():\n"
                "    assert list((REPO / 'src' / 'personalclaw').rglob('*.py'))\n",
            ),
        ),
        Control(
            "an rglob of a package path a helper module binds fails",
            lambda root: (
                _write(
                    root,
                    "tests/walk_roots_shared.py",
                    "from pathlib import Path\n\n"
                    "SRC = Path(__file__).resolve().parents[1] / 'src' / 'personalclaw'\n",
                ),
                _write(
                    root,
                    _WALKER,
                    "from walk_roots_shared import SRC\n\n\ndef test_it():\n"
                    "    assert list(SRC.rglob('*.py'))\n",
                ),
            ),
        ),
        Control(
            "an exemption that names no walk any more fails",
            lambda root: _write(
                root,
                next(iter(private_walkers.NOT_THROUGH_THE_INDEX)).split("::")[0],
                "def test_it():\n    pass\n",
            ),
        ),
        Control(
            "a walk of a tests/ path a helper module binds passes",
            lambda root: (
                _write(
                    root,
                    "tests/walk_roots_shared.py",
                    "from pathlib import Path\n\nTESTS = Path(__file__).resolve().parent\n",
                ),
                _write(
                    root,
                    _WALKER,
                    "from walk_roots_shared import TESTS\n\n\ndef test_it():\n"
                    "    assert list(TESTS.rglob('*.py'))\n",
                ),
            ),
            fails=False,
        ),
        Control(
            "a walk of tests/ is not a source root, and passes",
            _walker(
                "TESTS = Path(__file__).resolve().parent\n\n\n"
                "def test_it():\n    assert list(TESTS.rglob('*.py'))\n"
            ),
            fails=False,
        ),
        Control(
            "a walk of a path the function was handed passes",
            _walker("def walk(root):\n    return list(root.rglob('*'))\n"),
            fails=False,
        ),
    ),
)


def _every(rel: str, old: str, new: str) -> Callable[[Path], None]:
    """Every *old* in *rel* replaced, so no copy keeps the shape the plant moves the file off."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        assert old in text, f"the plant's anchor is not in {rel}: {old!r}"
        _write(root, rel, text.replace(old, new))

    return plant


def _setup_caller(node: str) -> Callable[[Path], None]:
    """A planted workflow whose one job, ``web``, calls the setup action for Node alone, its
    ``node`` input given as *node*, and names no Python: a call no tier job makes literally."""
    text = (
        "name: Setup call\non:\n  workflow_dispatch:\npermissions:\n  contents: read\njobs:\n"
        "  web:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n    steps:\n"
        "      - uses: ./.github/actions/setup\n        with:\n"
        f"          node: {node}\n"
    )
    return lambda root: _write(root, f"{_WORKFLOWS}/setup-call.yml", text)


def _node_on_a_literal(root: Path) -> None:
    """The setup action's setup-node on a literal version, not ``.nvmrc``."""
    _plant("", _SETUP_ACTION, "node-version-file: .nvmrc", 'node-version: "24"').plant(root)


def _uv_step_env(line: str) -> Callable[[Path], None]:
    """The setup action's uv step given an ``env:`` of one *line*."""
    return _plant(
        "",
        _SETUP_ACTION,
        "        prune-cache: true\n",
        f"        prune-cache: true\n      env:\n        {line}\n",
    ).plant


def _first_call_env(line: str) -> Callable[[Path], None]:
    """pr.yml's call of the setup action, its stage job's, given an ``env:`` of one *line*."""
    return _plant(
        "",
        _PR_YML,
        "        uses: ./.github/actions/setup\n",
        f"        uses: ./.github/actions/setup\n        env:\n          {line}\n",
    ).plant


def _setup_action_as_yaml(root: Path) -> None:
    """The setup action in ``action.yaml``, which GitHub loads as it loads ``action.yml``, with
    no uv version."""
    old = '        version: "0.13.1"\n'
    text = (root / _SETUP_ACTION).read_text(encoding="utf-8")
    assert old in text, f"the plant's anchor is not in {_SETUP_ACTION}: {old!r}"
    (root / _SETUP_ACTION).unlink()
    _write(root, _SETUP_ACTION.replace("action.yml", "action.yaml"), text.replace(old, ""))


ROWS: tuple[Row, ...] = (
    SECURITY_MARKER,
    _toolchain_row(
        "python",
        python_violations,
        (
            # The tier jobs set up uv through the setup action, so the plant is inside it.
            _plant(
                "setup-uv without python-version",
                _SETUP_ACTION,
                "        python-version: ${{ inputs.python-version }}\n",
                "",
                expect="names no python-version",
            ),
            # Text outside one ${{ }}, here a block scalar's newline, makes the condition a
            # string GitHub fills in, which is always true: web's Node call then runs setup-uv
            # with no Python.
            Control(
                "the setup action's uv `if:` as a block scalar, which GitHub always runs",
                _both(
                    _plant(
                        "",
                        _SETUP_ACTION,
                        "      if: inputs.python-version != ''\n",
                        "      if: |\n        ${{ inputs.python-version != '' }}\n",
                    ).plant,
                    _setup_caller('"true"'),
                ),
                expect="setup-call.yml:web: astral-sh/setup-uv names no python-version",
            ),
            Control(
                "a call of the setup action for Node alone passes",
                _setup_caller('"true"'),
                fails=False,
            ),
            _plant(
                "a minor, not a patch",
                ".github/workflows/flake-report.yml",
                'python-version: "3.12.15"',
                'python-version: "3.12"',
            ),
            _plant(
                "two patches of one minor",
                ".github/workflows/nix.yml",
                'python-version: "3.12.15"',
                'python-version: "3.12.14"',
            ),
            _plant(
                "a matrix leg off .python-version",
                ".github/workflows/breadth.yml",
                'python: ["3.13.16"]',
                'python: ["3.13.15"]',
            ),
            _plant(".python-version a minor", ".python-version", "3.13.16", "3.13"),
            # A tier workflow's stage job sets up the Python its entry's catalogue job names.
            _plant(
                "a planned stage job whose tier names a minor",
                catalogue.CATALOGUE,
                '".python-version"]\nexternal_inputs = ["package-index"]\ntoolchain = ["uv"]\n'
                'cache = ["uv"]\nsecurity = "ordinary"\npython = "3.12.15"\n',
                '".python-version"]\nexternal_inputs = ["package-index"]\ntoolchain = ["uv"]\n'
                'cache = ["uv"]\nsecurity = "ordinary"\npython = "3.12"\n',
                expect="pr.yml:test (matrix): Python '3.12' is not an exact X.Y.Z",
            ),
            _plant(
                "setup-python on a Mac names a 3.12 security release",
                ".github/workflows/clean-machine-walkthroughs.yml",
                'python-version: "3.13.16"',
                'python-version: "3.12.15"',
                expect="setup-python has no macos-26 build of Python 3.12.15",
            ),
        ),
    ),
    _toolchain_row(
        "uv",
        uv_violations,
        (
            _plant(
                "setup-uv installs the newest uv",
                _SETUP_ACTION,
                '        version: "0.13.1"\n',
                "",
                expect="flake-report.yml:rerun: uv (newest) is not an exact X.Y.Z",
            ),
            Control(
                "the setup action read from action.yaml installs the newest uv",
                _setup_action_as_yaml,
                expect="flake-report.yml:rerun: uv (newest) is not an exact X.Y.Z",
            ),
            _plant(
                "an interpreter installed apart",
                ".github/workflows/headless-run-smoke.yml",
                "        run: uv sync --locked --extra dev\n",
                "        run: uv python install 3.12 && uv sync --locked --extra dev\n",
            ),
            _plant(
                "the image on another uv",
                "deploy/docker/Dockerfile.backend",
                "ARG UV_VERSION=0.13.1",
                "ARG UV_VERSION=0.12.19",
            ),
            _plant(
                "required-version not a floor",
                "pyproject.toml",
                'required-version = ">=0.13.1"',
                'required-version = "==0.13.1"',
            ),
        ),
    ),
    _toolchain_row(
        "uv-python-preference",
        uv_python_preference_violations,
        (
            _plant(
                "a workflow that runs uv without it",
                _PR_YML,
                _PREFERENCE,
                "",
            ),
            _plant(
                "the workflow sets managed",
                ".github/workflows/breadth.yml",
                _PREFERENCE,
                "  UV_PYTHON_PREFERENCE: managed\n",
            ),
            _plant(
                "the workflow sets another value",
                ".github/workflows/release-artifacts.yml",
                _PREFERENCE,
                "  UV_PYTHON_PREFERENCE: system\n",
            ),
            _plant(
                "a uv step overrides it",
                ".github/workflows/headless-run-smoke.yml",
                "        run: uv sync --locked --extra dev\n",
                "        env:\n          UV_PYTHON_PREFERENCE: managed\n"
                "        run: uv sync --locked --extra dev\n",
            ),
            _plant(
                "the setup action's uv step overrides it",
                _SETUP_ACTION,
                "        prune-cache: true\n",
                "        prune-cache: true\n      env:\n        UV_PYTHON_PREFERENCE: system\n",
                expect="pr.yml:test:Install uv: UV_PYTHON_PREFERENCE is 'system'",
            ),
            _plant(
                "a call of the setup action overrides it",
                _PR_YML,
                "        uses: ./.github/actions/setup\n",
                "        uses: ./.github/actions/setup\n"
                "        env:\n          UV_PYTHON_PREFERENCE: system\n",
                expect="pr.yml:test:Install uv: UV_PYTHON_PREFERENCE is 'system'",
            ),
            # The call's env sits beneath the action step's own, in both directions.
            Control(
                "the setup action's uv step overrides a call that sets it",
                _both(
                    _uv_step_env("UV_PYTHON_PREFERENCE: system"),
                    _first_call_env("UV_PYTHON_PREFERENCE: only-managed"),
                ),
                expect="pr.yml:test:Install uv: UV_PYTHON_PREFERENCE is 'system'",
            ),
            Control(
                "a call overrides it over a uv step with an env of its own",
                _both(_uv_step_env("OTHER: x"), _first_call_env("UV_PYTHON_PREFERENCE: system")),
                expect="pr.yml:test:Install uv: UV_PYTHON_PREFERENCE is 'system'",
            ),
            Control(
                "set on one uv job of several, not the workflow, fails",
                lambda root: _job_level_preference(root, only_first=True),
            ),
            Control(
                "set on every uv job instead of the workflow passes",
                _job_level_preference,
                fails=False,
            ),
            Control(
                "a workflow with no uv step passes without it",
                lambda root: _write(root, ".github/workflows/no-uv.yml", _NO_UV_WORKFLOW),
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "node",
        node_violations,
        (
            _plant(
                "setup-node with a literal version",
                _SETUP_ACTION,
                "node-version-file: .nvmrc",
                'node-version: "24"',
                expect="merge.yml:build: setup-node does not read .nvmrc alone",
            ),
            # GitHub compares strings ignoring case, and only the runner evaluates an
            # expression, so either call still runs the action's setup-node.
            Control(
                "a call that asks for Node in capitals",
                _both(_setup_caller('"TRUE"'), _node_on_a_literal),
                expect="setup-call.yml:web: setup-node does not read .nvmrc alone",
            ),
            Control(
                "a call whose node input is an expression",
                _both(_setup_caller("${{ github.event_name != '' }}"), _node_on_a_literal),
                expect="setup-call.yml:web: setup-node does not read .nvmrc alone",
            ),
            _plant(".nvmrc a major", ".nvmrc", _NVMRC, _NVMRC.split(".")[0]),
            _plant(
                "an image Node stage off .nvmrc",
                "deploy/docker/Dockerfile.web",
                _NODE_BASE,
                _NODE_BASE.replace(f"node:{_NVMRC}", "node:20.19.0"),
            ),
        ),
    ),
    _toolchain_row(
        "npm",
        npm_violations,
        (
            _plant("no packageManager", "package.json", '  "packageManager": "npm@11.19.0",\n', ""),
            _plant("a floating npm", "package.json", '"npm@11.19.0"', '"npm@11"'),
            _plant(
                "npm ci inside the setup action, from a folder with no exact npm",
                _SETUP_ACTION,
                "        cache: ${{ inputs.node-cache }}\n",
                "        cache: ${{ inputs.node-cache }}\n"
                "    - name: npm ci\n      if: inputs.node == 'true'\n      shell: bash\n"
                "      working-directory: elsewhere\n      run: npm ci\n",
                expect="elsewhere/package.json: packageManager is ''",
            ),
        ),
    ),
    _toolchain_row(
        "runner-label",
        runner_label_violations,
        (
            _plant(
                "a job on ubuntu-latest",
                ".github/workflows/dco.yml",
                "runs-on: ubuntu-24.04",
                "runs-on: ubuntu-latest",
            ),
            _plant(
                "a leg on macos-latest",
                ".github/workflows/breadth.yml",
                "runs-on: macos-26",
                "runs-on: macos-latest",
            ),
        ),
    ),
    _toolchain_row(
        "runner-identity",
        runner_identity_violations,
        (
            _plant(
                "a job that logs no runner image", ".github/workflows/dco.yml", _IDENTITY_STEP, ""
            ),
            _plant(
                "a container job reading the runner's variables",
                ".github/workflows/breadth.yml",
                "          . /etc/os-release\n",
                '          echo "$ImageOS $ImageVersion"\n',
            ),
            _plant(
                "a Runner identity step inside the setup action",
                _SETUP_ACTION,
                "  steps:\n",
                "  steps:\n    - name: Runner identity\n      shell: bash\n"
                '      run: echo "runner image ${ImageOS:?no image} ${ImageVersion:?no version}"\n',
                expect="flake-report.yml:rerun: a Runner identity step inside a local action",
            ),
        ),
    ),
    _toolchain_row(
        "wheel-built-after-the-spa-and-verified",
        wheel_violations,
        (
            _plant(
                "package without the wheel contract",
                catalogue.CATALOGUE,
                '  { argv = ["uv", "run", "--no-project", "python", "scripts/verify_wheel.py",'
                ' "--wheel", "dist/*.whl"] },\n',
                '  { argv = ["ls", "dist"] },\n',
                expect="package runs no wheel contract",
            ),
            _plant(
                "a wheel built without --wheel (the sdist path)",
                catalogue.CATALOGUE,
                '"uv", "build", "--wheel", "--out-dir", "dist"',
                '"uv", "build", "--out-dir", "dist"',
                expect="package builds no wheel",
            ),
            _plant(
                "package not after web-build, so its wheel may carry no SPA",
                catalogue.CATALOGUE,
                'timeout_min = 45\nafter = ["web-build"]\ncommand = [\n'
                '  { argv = ["uv", "build", "--wheel"',
                'timeout_min = 45\ncommand = [\n  { argv = ["uv", "build", "--wheel"',
                expect="package does not run after web-build",
            ),
            _plants(
                "the wheel verified before it is built",
                catalogue.CATALOGUE,
                (
                    ('  { argv = ["uv", "build", "--wheel", "--out-dir", "dist"] },\n', ""),
                    (
                        '"--wheel", "dist/*.whl"] },\n',
                        '"--wheel", "dist/*.whl"] },\n'
                        '  { argv = ["uv", "build", "--wheel", "--out-dir", "dist"] },\n',
                    ),
                ),
                expect="package must build the wheel, then verify that wheel",
            ),
            _plant(
                "web-build without its SPA build",
                catalogue.CATALOGUE,
                '  { argv = ["npm", "run", "build"] },\n',
                "",
                expect="web-build builds no SPA",
            ),
        ),
    ),
    _toolchain_row(
        "browser-installs-wait-on-apt-only-when-chromium-cannot-start",
        browser_install_violations,
        (
            Control(
                "a workflow job that installs Chromium as run-job's install does, start-checked,"
                " passes",
                _browser_workflow(),
                fails=False,
            ),
            Control(
                "a browser install with --with-deps",
                _browser_workflow(
                    (
                        "run: npx playwright install chromium\n",
                        "run: npx playwright install --with-deps chromium\n",
                    )
                ),
                expect="with --with-deps, which runs apt",
            ),
            Control(
                "apt run whatever the start check said",
                _browser_workflow(
                    ("        if: ${{ steps.chromium-starts.outcome == 'failure' }}\n", "")
                ),
                expect="without the start check having failed",
            ),
            Control(
                "an apt install with no timeout around it",
                _browser_workflow(
                    (
                        "          if ! timeout 300 npx playwright install-deps chromium; then\n",
                        "          if ! npx playwright install-deps chromium; then\n",
                    )
                ),
                expect="no `timeout` around it",
            ),
            Control(
                "a browser install never checked to start",
                _browser_workflow(("        id: chromium-starts\n", "")),
                expect="never checks that it starts",
            ),
        ),
    ),
    _toolchain_row(
        "web-dist-only-from-a-finished-build",
        web_dist_violations,
        (
            _plant_in_job(
                "an upload that runs on a red build too",
                _MERGE_YML,
                "build",
                "        if: ${{ matrix.id == 'web-build' }}\n",
                "        if: ${{ always() && matrix.id == 'web-build' }}\n",
                expect="'Upload the build (web-dist)' uploads web-dist under `if:",
            ),
            _plant_in_job(
                "an upload that runs whatever the build answered",
                _MERGE_YML,
                "build",
                "        if: ${{ matrix.id == 'web-build' }}\n",
                "        if: ${{ !cancelled() && matrix.id == 'web-build' }}\n",
                expect="on a red build too",
            ),
            _plants(
                "a check that reads the build before it is built",
                catalogue.CATALOGUE,
                (
                    ('  { argv = ["npm", "run", "build"] },\n', ""),
                    (
                        '"--built-web"] },\n',
                        '"--built-web"] },\n  { argv = ["npm", "run", "build"] },\n',
                    ),
                ),
                expect="web-build: command step 1 reads the build before it is built",
            ),
            _plant(
                "web-build without its SPA build",
                catalogue.CATALOGUE,
                '  { argv = ["npm", "run", "build"] },\n',
                "",
                expect="web-build builds no SPA",
            ),
        ),
    ),
    _toolchain_row(
        "main-push-runs-and-is-never-cancelled",
        main_push_runs_violations,
        (
            _plant(
                "branches-ignore: [main]",
                _MERGE_YML,
                "    branches: [main]\n",
                "    branches-ignore: [main]\n",
            ),
            _plant(
                "branches-ignore of one feature branch",
                _MERGE_YML,
                "    branches: [main]\n",
                "    branches-ignore: [feature]\n",
                expect="a branch other than main",
            ),
            _plant(
                "main and a release branch, named without a glob",
                _MERGE_YML,
                "    branches: [main]\n",
                "    branches: [main, release]\n",
                expect="a branch other than main",
            ),
            _plant(
                "a tag push beside main",
                _MERGE_YML,
                "    branches: [main]\n",
                '    branches: [main]\n    tags: ["v*"]\n',
                expect="tag push",
            ),
            _plant(
                "a tags-ignore beside main",
                _MERGE_YML,
                "    branches: [main]\n",
                '    branches: [main]\n    tags-ignore: ["v*"]\n',
                expect="tag push",
            ),
            _plant(
                'every branch again (branches: ["**"])',
                _MERGE_YML,
                "    branches: [main]\n",
                '    branches: ["**"]\n',
                expect="a branch other than main",
            ),
            _plant(
                "main and one feature pattern",
                _MERGE_YML,
                "    branches: [main]\n",
                '    branches: [main, "feature-*"]\n',
                expect="a branch other than main",
            ),
            _plant(
                "no push trigger",
                _MERGE_YML,
                "  push:\n",
                "  push-off:\n",
                expect="does not run on a push",
            ),
            _plant(
                "a bare push: (every branch and tag push would start it)",
                _MERGE_YML,
                "    branches: [main]\n",
                "",
                expect="bare `push:`",
            ),
            _plant(
                'a "!main" after "main"',
                _MERGE_YML,
                "    branches: [main]\n",
                '    branches: [main, "!main"]\n',
                expect="leaves main",
            ),
            _plant(
                "a paths-ignore on the main trigger",
                _MERGE_YML,
                "    branches: [main]\n",
                '    branches: [main]\n    paths-ignore: ["docs/**"]\n',
                expect="leaves main",
            ),
            _plant(
                "no main at all",
                _MERGE_YML,
                "    branches: [main]\n",
                "    branches: [release]\n",
                expect="leaves main",
            ),
            _plant_tier(
                "cancel-in-progress: true on main",
                _MERGE_YML,
                "cancel-in-progress",
                "true",
                expect="a run on main can be cancelled",
            ),
            _plant_tier(
                "a main run grouped by branch, not by commit",
                _MERGE_YML,
                "group",
                "merge-${{ github.event.pull_request.number || github.ref }}",
                expect="a run on main is not grouped by its own commit",
            ),
            Control(
                "every Dependabot head's runs queued in one group, which a push to main is not in",
                _tier_concurrency(_MERGE_YML, dependabot=True),
                fails=False,
            ),
            _plant(
                "a job-level concurrency group outside the stage jobs",
                _MERGE_YML,
                "\n  shard-plan:\n",
                "\n  shard-plan:\n    concurrency:\n      group: plan-${{ github.sha }}\n"
                "      cancel-in-progress: false\n",
                expect="merge.yml:shard-plan has its own concurrency group",
            ),
            _plant_in_job(
                "a stage job with no group of its own",
                _MERGE_YML,
                "test-2",
                "    concurrency:\n",
                "    concurrency-off:\n",
                expect="merge.yml:test-2 has no concurrency group of its own",
            ),
            _plant_in_job(
                "a stage group that cancels a running entry",
                _MERGE_YML,
                "test",
                "      cancel-in-progress: false\n",
                "      cancel-in-progress: true\n",
                expect="merge.yml:test's group can cancel a running entry",
            ),
            _plant_in_job(
                "a stage group with no cancel-in-progress (an expression is not false)",
                _MERGE_YML,
                "test",
                "      cancel-in-progress: false\n",
                "      cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}\n",
                expect="merge.yml:test's group can cancel a running entry",
            ),
            _plant_in_job(
                "a suite group per commit on main",
                _MERGE_YML,
                "test",
                "matrix.id == 'python-suite' && 'suite' ||",
                "matrix.id == 'python-suite' && github.sha ||",
                expect="python-suite (1/3)'s group on main is per commit",
            ),
            _plant_in_job(
                "the macOS suite left out of the shared group",
                _MERGE_YML,
                "test",
                " || github.ref == 'refs/heads/main' && github.run_attempt == '1' &&"
                " matrix.id == 'python-suite-macos' && 'suite'",
                "",
                expect=f"python-suite-macos (1/{_MACOS_SHARDS})'s group on main is per commit",
            ),
            _plant_in_job(
                "a suite group that does not name its shard",
                _MERGE_YML,
                "test",
                "}}-${{ matrix.key }}\n",
                "}}-${{ matrix.id }}\n",
                expect="python-suite (2/3) and python-suite (1/3) share the group",
            ),
            _plant_in_job(
                "the two suites in one group",
                _MERGE_YML,
                "test",
                "}}-${{ matrix.key }}\n",
                "}}-suite\n",
                expect="and python-suite (1/3) share the group 'merge-suite-suite' on main",
            ),
            _plant_in_job(
                "every entry in the shared group on main",
                _MERGE_YML,
                "test",
                "matrix.id == 'python-suite' && 'suite' ||",
                "'suite' ||",
                expect="security's group on main",
            ),
            _plant_in_job(
                "a re-run on main joins the shared group",
                _MERGE_YML,
                "test",
                "github.ref == 'refs/heads/main' && github.run_attempt == '1' &&"
                " matrix.id == 'python-suite' &&",
                "github.ref == 'refs/heads/main' && matrix.id == 'python-suite' &&",
                expect="python-suite (1/3)'s group for a re-run on main",
            ),
            _plant_in_job(
                "every commit's re-run on main shares one group",
                _MERGE_YML,
                "test",
                "|| github.event.pull_request.number || github.sha }}",
                "|| github.event.pull_request.number || github.repository }}",
                expect="'s group for a re-run on main",
            ),
            Control("the per-commit group on main passes", _nothing, fails=False),
        ),
        scaffold=_merge_alone,
    ),
    _toolchain_row(
        "test-macos-before-the-merge",
        macos_leg_violations,
        (
            _plant(
                "the macOS suite on a non-macOS runner",
                catalogue.CATALOGUE,
                'platform = "macos-26"\npython = "3.13.16"\ntimeout_min = 50\n',
                'platform = "ubuntu-24.04"\npython = "3.13.16"\ntimeout_min = 50\n',
                expect="python-suite-macos: platform is ubuntu-24.04, but the merge tier's",
            ),
            # Each leg lists its own shard of its own plan, so the two may split differently:
            # the shard plan holds every file in exactly one shard of each.
            _plants(
                "the macOS suite in fewer shards than the suite passes",
                catalogue.CATALOGUE,
                (
                    (
                        f"timeout_min = 50\nworkers = 2\nshards = {_MACOS_SHARDS}\n",
                        f"timeout_min = 50\nworkers = 2\nshards = {_MACOS_SHARDS - 1}\n",
                    ),
                    (
                        _SUITE_MACOS_LEG,
                        _SUITE_MACOS_LEG.replace(f"/{_MACOS_SHARDS}", f"/{_MACOS_SHARDS - 1}"),
                    ),
                ),
                fails=False,
            ),
            _plant(
                "the macOS suite listing a shard count other than its own",
                catalogue.CATALOGUE,
                _SUITE_MACOS_LEG,
                _SUITE_MACOS_LEG.replace(f"/{_MACOS_SHARDS}", f"/{_MACOS_SHARDS + 1}"),
                expect="python-suite-macos does not list its own shard",
            ),
            _plant(
                "a -k only the macOS suite has",
                catalogue.CATALOGUE,
                _SUITE_MACOS_PYTEST,
                _SUITE_MACOS_PYTEST.replace('"-q",', '"-q", "-k", "not vector",'),
                expect="python-suite-macos's pytest selects other",
            ),
            _plant(
                "an --ignore only the macOS suite has",
                catalogue.CATALOGUE,
                _SUITE_MACOS_PYTEST,
                _SUITE_MACOS_PYTEST.replace(
                    '"-q",', '"-q", "--ignore=tests/test_vector_index.py",'
                ),
                expect="python-suite-macos's pytest selects other",
            ),
            _plant(
                "a positional path only the macOS suite has",
                catalogue.CATALOGUE,
                _SUITE_MACOS_PYTEST,
                _SUITE_MACOS_PYTEST.replace('"tests",', '"tests", "tests/security",'),
                expect="python-suite-macos's pytest selects other",
            ),
            _plants(
                "a selection both suites share passes",
                catalogue.CATALOGUE,
                (
                    (
                        _SUITE_MACOS_PYTEST,
                        _SUITE_MACOS_PYTEST.replace('"-q",', '"-q", "-m", "not slow",'),
                    ),
                    (
                        '"-n", "auto", "--dist", "worksteal", "-q", "tests", "--shard-list",'
                        ' "{runner_temp}/shard.txt",'
                        ' "--junitxml=reports/junit-test-shard-{shard}.xml"',
                        '"-n", "auto", "--dist", "worksteal", "-q", "-m", "not slow", "tests",'
                        ' "--shard-list", "{runner_temp}/shard.txt",'
                        ' "--junitxml=reports/junit-test-shard-{shard}.xml"',
                    ),
                ),
                fails=False,
            ),
            _plant(
                "the macOS suite in a tier the suite is not",
                catalogue.CATALOGUE,
                'id = "python-suite-macos"\ntiers = ["merge"]',
                'id = "python-suite-macos"\ntiers = ["merge", "breadth"]',
                expect="python-suite-macos runs in ('merge', 'breadth'), python-suite in",
            ),
            _plants(
                "the macOS suite back in a pull request's run, the entry tier",
                catalogue.CATALOGUE,
                (
                    (
                        'id = "python-suite-macos"\ntiers = ["merge"]',
                        'id = "python-suite-macos"\ntiers = ["entry", "merge"]',
                    ),
                    (
                        "[tier.entry]\njobs = [\n",
                        '[tier.entry]\njobs = [\n  "python-suite-macos",\n',
                    ),
                ),
                expect="python-suite-macos runs in ('entry', 'merge'), python-suite in",
            ),
            _plants(
                "the macOS suite out of the merge tier",
                catalogue.CATALOGUE,
                (
                    (
                        'id = "python-suite-macos"\ntiers = ["merge"]',
                        'id = "python-suite-macos"\ntiers = ["breadth"]',
                    ),
                    ('  "python-suite-macos",\n', ""),
                ),
                expect="the merge tier lacks python-suite-macos",
            ),
            _plant(
                "the macOS suite on every push, the suite only on an untested one",
                catalogue.CATALOGUE,
                'every_push = ["browse-live", "security", "apps-contract"]',
                'every_push = ["browse-live", "security", "apps-contract", "python-suite-macos"]',
                expect="differ in whether every push to main runs them",
            ),
            _plant(
                "every macOS shard lists shard 1",
                catalogue.CATALOGUE,
                _SUITE_MACOS_LEG,
                _SUITE_MACOS_LEG.replace("{shard}/", "1/"),
                expect="python-suite-macos does not list its own shard",
            ),
            _plant(
                "the macOS suite listing python-suite's leg",
                catalogue.CATALOGUE,
                _SUITE_MACOS_LEG,
                _SUITE_MACOS_LEG.replace("test-macos", "test-shard"),
                expect="python-suite-macos does not list its own shard",
            ),
            _plant(
                "PYTEST_ADDOPTS narrowing the macOS suite's pytest step",
                catalogue.CATALOGUE,
                _SUITE_MACOS_PYTEST + "] },",
                _SUITE_MACOS_PYTEST + '], env = { PYTEST_ADDOPTS = "-k vector" } },',
                expect="python-suite-macos's pytest step's env differs",
            ),
            _plant(
                "PYTEST_ADDOPTS in the macOS suite's job env",
                catalogue.CATALOGUE,
                f'shards = {_MACOS_SHARDS}\nenv = {{ PERSONALCLAW_SKIP_APP_BACKENDS = "1",'
                ' PERSONALCLAW_TEST_CONTAINER_RUNTIME = "1" }\njunit = ["reports/junit-test-macos',
                f'shards = {_MACOS_SHARDS}\nenv = {{ PERSONALCLAW_SKIP_APP_BACKENDS = "1",'
                ' PERSONALCLAW_TEST_CONTAINER_RUNTIME = "1", PYTEST_ADDOPTS = "-k vector" }\n'
                'junit = ["reports/junit-test-macos',
                expect="python-suite-macos's environment differs",
            ),
            _plant(
                "the macOS suite's pytest narrowed by its working directory",
                catalogue.CATALOGUE,
                _SUITE_MACOS_PYTEST + "] },",
                _SUITE_MACOS_PYTEST + '], workdir = "tests/security" },',
                expect="python-suite-macos's pytest step's workdir differs",
            ),
        ),
        scaffold=_catalogue_alone,
    ),
    _toolchain_row(
        "tree-check-runs-the-previous-commits-copy",
        tree_check_reads_before_violations,
        (
            _plant(
                "the checked-out copy",
                _MERGE_YML,
                'python3 -I "$check" --workflow merge.yml',
                "python3 -I tools/ci/tree_tested.py --workflow merge.yml",
            ),
            _plant(
                "the fetched check run without -I",
                _MERGE_YML,
                'python3 -I "$check" --workflow merge.yml',
                'python3 "$check" --workflow merge.yml',
                expect="does not ask the fetched check about merge.yml, under python3 -I",
            ),
            _plant(
                "the copy read at the pushed commit",
                _MERGE_YML,
                "BEFORE: ${{ github.event.before }}",
                "BEFORE: ${{ github.sha }}",
            ),
            _plant(
                "the check asked about another workflow's runs",
                _MERGE_YML,
                'python3 -I "$check" --workflow merge.yml',
                'python3 -I "$check" --workflow pr.yml',
                expect="does not ask the fetched check about merge.yml",
            ),
            _plant(
                "uv sync before the check",
                _MERGE_YML,
                '          set -u\n          answer=""\n',
                '          set -u\n          uv sync --locked\n          answer=""\n',
            ),
            _plant(
                "the pushed tree checked out",
                _MERGE_YML,
                "      - name: did a green pull-request run already test this tree\n",
                "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
                "      - name: did a green pull-request run already test this tree\n",
            ),
            _plant(
                "a write grant",
                _MERGE_YML,
                "      actions: read         # the pull request's runs and their first attempts\n",
                "      actions: write\n",
            ),
        ),
    ),
    # The superseded proof on a push to main: the old ci.yml `test`/`test-macos-gate` gates'
    # plants, each on the step that replaced them.
    _toolchain_row(
        "a-push-to-mains-superseded-proof-is-the-previous-commits",
        superseded_proof_violations,
        (
            _plant(
                "the proof read at the pushed commit",
                _MERGE_YML,
                "contents/tools/ci/superseded.py?ref=${BEFORE}",
                "contents/tools/ci/superseded.py?ref=${GITHUB_SHA}",
                expect="does not fetch the previous main commit's copy of superseded.py",
            ),
            _plant(
                "the proof read at main's tip, which on a push is the pushed commit",
                _MERGE_YML,
                "contents/tools/ci/superseded.py?ref=${BEFORE}",
                "contents/tools/ci/superseded.py?ref=main",
                expect="does not fetch the previous main commit's copy of superseded.py",
            ),
            _plant_in_job(
                "the proof binds BEFORE to the pushed commit",
                _MERGE_YML,
                "results",
                "          BEFORE: ${{ github.event.before }}\n",
                "          BEFORE: ${{ github.sha }}\n",
                expect="does not fetch the previous main commit's copy of superseded.py",
            ),
            _plant(
                "the checked-out superseded.py",
                _MERGE_YML,
                'python3 -I "$check" --workflow merge.yml --job',
                "python3 -I tools/ci/superseded.py --workflow merge.yml --job",
                expect="runs the checked-out copy of superseded.py",
            ),
            _plant(
                "the fetched superseded.py run without -I",
                _MERGE_YML,
                'python3 -I "$check" --workflow merge.yml --job',
                'python3 "$check" --workflow merge.yml --job',
                expect="does not run the fetched superseded.py under python3 -I",
            ),
            _plant(
                "the proof made on any event",
                _MERGE_YML,
                "        if: " + _ON_A_PUSH_TO_MAIN + "\n",
                "        if: ${{ !cancelled() }}\n",
                expect="not on a push to main alone",
            ),
            _plant(
                "the proof made on a pull request too",
                _MERGE_YML,
                "        if: " + _ON_A_PUSH_TO_MAIN + "\n",
                "        if: ${{ !cancelled() && github.ref == 'refs/heads/main' }}\n",
                expect="not on a push to main alone",
            ),
            _plant(
                "no proof over the macOS suite",
                _MERGE_YML,
                "for job in python-suite python-suite-macos; do",
                "for job in python-suite; do",
                expect="proves ['python-suite'], not the suite jobs",
            ),
            _plant(
                "a proof over a job outside the shared group",
                _MERGE_YML,
                "for job in python-suite python-suite-macos; do",
                "for job in python-suite python-suite-macos e2e; do",
                expect="proves ['e2e', 'python-suite', 'python-suite-macos'], not",
            ),
            _plant(
                "the proof reads another group",
                _MERGE_YML,
                "--group-prefix merge-suite- --out",
                "--group-prefix merge- --out",
                expect="reads the groups ['merge-'], not the shared group ['merge-suite-']",
            ),
            _plant(
                "the proof asks about another workflow's runs",
                _MERGE_YML,
                'python3 -I "$check" --workflow merge.yml --job',
                'python3 -I "$check" --workflow pr.yml --job',
                expect="does not ask superseded.py about merge.yml's runs",
            ),
            _plant_in_job(
                "the proof given no token",
                _MERGE_YML,
                "results",
                "          GH_TOKEN: ${{ github.token }}\n          BEFORE:",
                "          BEFORE:",
                expect="proof is given no token of the run's",
            ),
            _plant(
                "the proof written where the verdict does not read it",
                _MERGE_YML,
                '--out "$RUNNER_TEMP/superseded.json"',
                '--out "$RUNNER_TEMP/proof.json"',
                expect="verdict does not read the file the proof writes",
            ),
            _plant(
                "the verdict not given the proof",
                _MERGE_YML,
                '            ${proof:+--superseded "$proof"} \\\n',
                "",
                expect="verdict is not given the proof (--superseded)",
            ),
            _plant_in_job(
                "a second step that proves superseded shards",
                _MERGE_YML,
                "results",
                "      - name: the verdict over the plan's keys and the run's records\n",
                "      - name: a second proof\n"
                '        run: python3 -I "$RUNNER_TEMP/superseded.py" --workflow merge.yml\n'
                "      - name: the verdict over the plan's keys and the run's records\n",
                expect="proves superseded shards in 2 steps, not one",
            ),
            _plant(
                "results without the annotations' read",
                _MERGE_YML,
                "      checks: read     # a cancelled shard's annotations (superseded.py)\n",
                "",
                expect="grants checks: None, not read",
            ),
            _plant(
                "results without the runs' read",
                _MERGE_YML,
                "      actions: read    # this run's jobs and main's later push runs"
                " (superseded.py)\n",
                "",
                expect="grants actions: None, not read",
            ),
        ),
        scaffold=_merge_alone,
    ),
    _toolchain_row(
        "no-macos-14-and-no-coverage-on-a-push",
        push_breadth_violations,
        (
            _plant(
                "a macos-14 leg in breadth.yml", _BREADTH, "runs-on: macos-26", "runs-on: macos-14"
            ),
            _plant(
                "a macos-14 entry in the catalogue",
                catalogue.CATALOGUE,
                'platform = "macos-26"\npython = "3.13.16"\ntimeout_min = 50\n',
                'platform = "macos-14"\npython = "3.13.16"\ntimeout_min = 50\n',
                expect="the catalogue's python-suite-macos runs on macos-14",
            ),
            _plant(
                "breadth's coverage on a branch push",
                _BREADTH,
                '    tags: ["rehearsal-v*"]\n',
                '    tags: ["rehearsal-v*"]\n    branches: ["**"]\n',
            ),
            _plant(
                "--cov in a merge-tier entry",
                catalogue.CATALOGUE,
                _SUITE_PYTEST,
                _SUITE_PYTEST.replace('"-q",', '"--cov=personalclaw", "-q",'),
                expect="python-suite measures coverage in the merge tier",
            ),
            _plant(
                "--cov personalclaw, the spaced form",
                catalogue.CATALOGUE,
                _SUITE_PYTEST,
                _SUITE_PYTEST.replace('"-q",', '"--cov", "personalclaw", "-q",'),
                expect="python-suite measures coverage in the merge tier",
            ),
            _plant(
                "a second pytest step in a change-tier entry, with --cov",
                catalogue.CATALOGUE,
                '"--junitxml=reports/junit-lint-installer.xml"],',
                '"--junitxml=reports/junit-lint-installer.xml", "--cov=personalclaw"],',
                expect="static measures coverage in the change and merge tier",
            ),
            _plant(
                "an entry's root venv python -m pytest with --cov",
                catalogue.CATALOGUE,
                '  { argv = ["uv", "run", "pytest", "tests/test_npm_advisory_floors.py",',
                '  { argv = [".venv/bin/python", "-m", "pytest", "--cov",'
                ' "tests/test_npm_advisory_floors.py",',
                expect="rails-checks measures coverage in the change and merge tier",
            ),
            Control(
                "a pull request workflow that measures coverage",
                lambda root: _write(
                    root,
                    f"{_WORKFLOWS}/covered.yml",
                    "name: Covered\non:\n  pull_request:\npermissions: {}\njobs:\n  cov:\n"
                    "    runs-on: ubuntu-24.04\n    steps:\n"
                    "      - run: uv run pytest --cov=personalclaw -n 2 -q\n",
                ),
                expect="covered.yml:cov measures coverage on a push or pull request",
            ),
            _plant(
                "runs-on: [macos-14]",
                _BREADTH,
                "    runs-on: macos-26\n",
                "    runs-on: [macos-14]\n",
            ),
            _plant(
                "runs-on: macos-14-xlarge",
                _BREADTH,
                "    runs-on: macos-26\n",
                "    runs-on: macos-14-xlarge\n",
            ),
            _plant(
                "runs-on: macOS-14", _BREADTH, "    runs-on: macos-26\n", "    runs-on: macOS-14\n"
            ),
            _plant(
                "runs-on as a {group, labels} mapping",
                _BREADTH,
                "    runs-on: macos-26\n",
                "    runs-on:\n      group: g\n      labels: [macos-14]\n",
            ),
            _plant(
                "macos-14 as an expression's literal",
                _BREADTH,
                "    runs-on: macos-26\n",
                "    runs-on: ${{ github.event_name == 'push' && 'macos-14' || 'macos-26' }}\n",
            ),
            _plant(
                "macos-14 through a matrix key",
                _BREADTH,
                _BREADTH_MACOS_HEAD,
                _BREADTH_MACOS_HEAD.replace("runs-on: macos-26", "runs-on: ${{ matrix.runner }}")
                + "        runner: [macos-14]\n",
            ),
            _plant(
                "macos-14 nested in a matrix object",
                _BREADTH,
                _BREADTH_MACOS_HEAD,
                _BREADTH_MACOS_HEAD.replace(
                    "runs-on: macos-26", "runs-on: ${{ matrix.platform.os }}"
                )
                + "        platform: [{os: macos-14}]\n",
            ),
            Control(
                "a pytest that names no --cov passes: the addopts measure nothing",
                _nothing,
                fails=False,
            ),
            Control(
                "coverage in breadth.yml, and in the breadth tier's entries, passes",
                _nothing,
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "breadth-freshness-first-and-report-last",
        breadth_order_violations,
        (
            _plant(
                "a leg without needs: freshness",
                _BREADTH,
                "    name: breadth-leg ubuntu-24.04-3.13/${{ matrix.shard }}\n"
                "    needs: [freshness]\n",
                "    name: breadth-leg ubuntu-24.04-3.13/${{ matrix.shard }}\n",
            ),
            _plant(
                "report missing a leg",
                _BREADTH,
                "needs: [freshness, ubuntu, macos, rails-macos, collection-parses, arm,",
                "needs: [freshness, ubuntu, rails-macos, collection-parses, arm,",
            ),
            _plant(
                "report gated on success()",
                _BREADTH,
                "install-smoke, audit]\n    if: ${{ !cancelled() }}\n",
                "install-smoke, audit]\n    if: ${{ success() }}\n",
            ),
            Control(
                "coverage, which reaches freshness through coverage-shard, passes",
                _nothing,
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "a-merge-run-may-take-every-macos-runner-and-breadth-leaves-it-some",
        macos_budget_violations,
        (
            # The macOS shards are the tree's (_MACOS_SHARDS), so each plant reads both trees.
            _plant(
                "the merge tier's macOS suite in as many shards as the account's macOS runners"
                " fits",
                catalogue.CATALOGUE,
                f"timeout_min = 50\nworkers = 2\nshards = {_MACOS_SHARDS}\n",
                f"timeout_min = 50\nworkers = 2\nshards = {_MACOS_RUNNERS}\n",
                fails=False,
            ),
            _plant(
                "the merge tier's macOS suite in one shard more than the account's macOS runners",
                catalogue.CATALOGUE,
                f"timeout_min = 50\nworkers = 2\nshards = {_MACOS_SHARDS}\n",
                f"timeout_min = 50\nworkers = 2\nshards = {_MACOS_RUNNERS + 1}\n",
                expect=f"the merge tier's run holds {_MACOS_RUNNERS + 1} macOS runners, more than"
                f" the {_MACOS_RUNNERS} the account runs",
            ),
            _plants(
                "two more macOS entries in the merge tier, the browser legs' two shards and the"
                " rails' three",
                catalogue.CATALOGUE,
                (
                    (
                        'id = "e2e-shard"\ntiers = ["entry", "merge"]\n',
                        'id = "e2e-shard"\ntiers = ["entry", "merge"]\nplatform = "macos-26"\n',
                    ),
                    (
                        'id = "rails"\ntiers = ["change", "entry", "merge"]\n',
                        'id = "rails"\ntiers = ["change", "entry", "merge"]\n'
                        'platform = "macos-26"\n',
                    ),
                ),
                expect=f"the merge tier's run holds {_MACOS_SHARDS + 5} macOS runners, more than"
                f" the {_MACOS_RUNNERS} the account runs",
            ),
            _macos_workers("the merge tier's macOS suite at one worker fits", "1", fails=False),
            _macos_workers(
                "the merge tier's macOS suite at three workers",
                "3",
                expect="python-suite-macos runs 3 workers on a macOS runner, not a count of",
            ),
            _macos_workers(
                "the merge tier's macOS suite at as many workers as the host has CPUs",
                '"auto"',
                expect="python-suite-macos runs 'auto' workers on a macOS runner, not a count",
            ),
            # Breadth today: rails-macos beside the macos leg, which runs one shard at a time,
            # and security-macos and planned-ids-macos after it: 1 + max(1, 2) = 3.
            _plant(
                "breadth's macos leg two shards at once, as many as the legs after it, fits",
                _BREADTH,
                '      max-parallel: 1\n      matrix:\n        python: ["3.12.15"]\n',
                '      max-parallel: 2\n      matrix:\n        python: ["3.12.15"]\n',
                fails=False,
            ),
            _plant(
                "breadth's macos leg three shards at once",
                _BREADTH,
                '      max-parallel: 1\n      matrix:\n        python: ["3.12.15"]\n',
                '      max-parallel: 3\n      matrix:\n        python: ["3.12.15"]\n',
                expect="breadth's macOS jobs hold 4 runners at once (1 beside the macos leg, and"
                " the larger of its 3 and the 2 after it), more than the 3 breadth may hold",
            ),
            _plant(
                "breadth's macos leg uncapped: six shards at once",
                _BREADTH,
                '      max-parallel: 1\n      matrix:\n        python: ["3.12.15"]\n',
                '      matrix:\n        python: ["3.12.15"]\n',
                expect="breadth's macOS jobs hold 7 runners at once (1 beside the macos leg, and"
                " the larger of its 6",
            ),
            _plant_in_job(
                "breadth's collection-parses moved onto macOS, a second leg beside the suite",
                _BREADTH,
                "collection-parses",
                "    runs-on: ubuntu-24.04\n",
                "    runs-on: macos-26\n",
                expect="breadth's macOS jobs hold 4 runners at once (2 beside the macos leg",
            ),
            _plant_in_job(
                "rails-macos waiting for the macos leg, a third leg after it, fits",
                _BREADTH,
                "rails-macos",
                "    needs: [freshness]\n",
                "    needs: [freshness, macos]\n",
                fails=False,
            ),
            _plant_in_job(
                "rails-macos in two legs, both waiting for the macos leg",
                _BREADTH,
                "rails-macos",
                "    needs: [freshness]\n",
                "    needs: [freshness, macos]\n    strategy:\n      matrix:\n        n: [1, 2]\n",
                expect="breadth's macOS jobs hold 4 runners at once (0 beside the macos leg, and"
                " the larger of its 1 and the 4 after it)",
            ),
        ),
    ),
    _toolchain_row(
        "breadth-security-macos-waits-for-the-macos-suite",
        macos_suite_first_violations,
        (
            _plant(
                "security-macos waiting on freshness alone",
                _BREADTH,
                "    needs: [freshness, macos]\n",
                "    needs: [freshness]\n",
                expect="breadth.yml:security-macos does not wait on macos",
            ),
            _plant(
                "security-macos gated on success()",
                _BREADTH,
                "    needs: [freshness, macos]\n    if: ${{ !cancelled() }}\n",
                "    needs: [freshness, macos]\n    if: ${{ success() }}\n",
                expect="breadth.yml:security-macos does not run whatever macos concluded",
            ),
            _plant(
                "security-macos with no if:, which GitHub reads as success()",
                _BREADTH,
                "    needs: [freshness, macos]\n    if: ${{ !cancelled() }}\n",
                "    needs: [freshness, macos]\n",
                expect="breadth.yml:security-macos does not run whatever macos concluded",
            ),
            _plant(
                "no security-macos job",
                _BREADTH,
                "  security-macos:\n",
                "  security-macos-renamed:\n",
                expect="breadth.yml has no security-macos job",
            ),
            _plant(
                "no macos job",
                _BREADTH,
                "  macos:\n",
                "  macos-renamed:\n",
                expect="breadth.yml has no macos job",
            ),
            _plant(
                "planned-ids-macos waiting on freshness alone",
                _BREADTH,
                "    # beside the suite's two; it runs whatever the suite concluded.\n"
                "    needs: [freshness, macos]\n",
                "    # beside the suite's two; it runs whatever the suite concluded.\n"
                "    needs: [freshness]\n",
                expect="breadth.yml:planned-ids-macos does not wait on macos",
            ),
            Control(
                "rails-macos and the suite's shards, which wait on freshness alone, pass",
                _nothing,
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "breadth-each-suite-leg-is-held-to-its-own-planned-ids",
        shard_union_violations,
        (
            _plant(
                "the canary in the gating union",
                _BREADTH,
                "--leg ubuntu:4 --leg macos:6 --leg arm:4 --leg coverage-shard:6\n",
                "--leg ubuntu:4 --leg macos:6 --leg arm:4 --leg coverage-shard:6 --leg canary:4\n",
                expect="breadth.yml:shard-union holds",
            ),
            _plant(
                "the gating union waiting on the canary",
                _BREADTH,
                "    needs: [freshness, ubuntu, macos, arm, coverage-shard, planned-ids-ubuntu,",
                "    needs: [freshness, ubuntu, macos, arm, coverage-shard, canary,"
                " planned-ids-ubuntu,",
                expect="breadth.yml:shard-union waits on canary, whose red is not the run's",
            ),
            _plant(
                "the gating union not waiting on a leg's planned collection",
                _BREADTH,
                " planned-ids-arm, planned-ids-coverage-shard,",
                " planned-ids-coverage-shard,",
                expect="breadth.yml:shard-union does not wait on planned-ids-arm",
            ),
            _plant(
                "the gating union not waiting on a leg's shards",
                _BREADTH,
                "    needs: [freshness, ubuntu, macos, arm, coverage-shard, planned-ids-ubuntu,",
                "    needs: [freshness, ubuntu, macos, coverage-shard, planned-ids-ubuntu,",
                expect="breadth.yml:shard-union does not wait on arm",
            ),
            _plant(
                "no job for a leg the gating union names",
                _BREADTH,
                "\n  arm:\n",
                "\n  arm-renamed:\n",
                expect="breadth.yml has no arm job",
            ),
            _plant(
                "the gating union leaving a leg out",
                _BREADTH,
                "--leg ubuntu:4 --leg macos:6 --leg arm:4 --leg coverage-shard:6\n",
                "--leg ubuntu:4 --leg macos:6 --leg coverage-shard:6\n",
                expect="breadth.yml:shard-union holds",
            ),
            _plant(
                "the gating union short of a leg's shards",
                _BREADTH,
                "--leg ubuntu:4 --leg macos:6 --leg arm:4 --leg coverage-shard:6\n",
                "--leg ubuntu:4 --leg macos:5 --leg arm:4 --leg coverage-shard:6\n",
                expect="breadth.yml:shard-union holds",
            ),
            _plant(
                "a leg's matrix grown past the union's count",
                _BREADTH,
                "        shard: [1, 2, 3, 4, 5, 6]\n",
                "        shard: [1, 2, 3, 4, 5, 6, 7]\n",
                expect="breadth.yml:shard-union holds",
            ),
            _plant_in_job(
                "the gating union continue-on-error",
                _BREADTH,
                "shard-union",
                "    timeout-minutes: 10\n",
                "    timeout-minutes: 10\n    continue-on-error: true\n",
                expect="breadth.yml:shard-union is continue-on-error",
            ),
            _plant_in_job(
                "the gating union gated on success()",
                _BREADTH,
                "shard-union",
                "    if: ${{ !cancelled() }}\n",
                "    if: ${{ success() }}\n",
                expect="breadth.yml:shard-union does not run whatever its legs concluded",
            ),
            _plant_in_job(
                "the canary's union not continue-on-error",
                _BREADTH,
                "shard-union-canary",
                "    continue-on-error: true\n",
                "",
                expect="breadth.yml:shard-union-canary is not continue-on-error",
            ),
            _plant_in_job(
                "the canary's planned collection not continue-on-error",
                _BREADTH,
                "planned-ids-canary",
                "    continue-on-error: true\n",
                "",
                expect="breadth.yml:planned-ids-canary is not continue-on-error",
            ),
            _plant(
                "no canary union",
                _BREADTH,
                "  shard-union-canary:\n",
                "  shard-union-canary-renamed:\n",
                expect="breadth.yml has no shard-union-canary job",
            ),
            _plant(
                "a macos shard writing the ubuntu shard's id file",
                _BREADTH,
                '--shard-ids "$RUNNER_TEMP/ids/shard-ids-macos-${{ matrix.shard }}.txt"',
                '--shard-ids "$RUNNER_TEMP/ids/shard-ids-ubuntu-${{ matrix.shard }}.txt"',
                expect="breadth.yml:macos writes its ids to",
            ),
            _plant(
                "a shard that collects with no --shard-ids",
                _BREADTH,
                ' --shard-ids "$RUNNER_TEMP/ids/shard-ids-canary-${{ matrix.shard }}.txt"',
                "",
                expect="breadth.yml:canary passes no --shard-ids",
            ),
            _plant(
                "an arm shard uploading its ids under the ubuntu shard's name",
                _BREADTH,
                "          name: shard-ids-arm-${{ matrix.shard }}\n",
                "          name: shard-ids-ubuntu-${{ matrix.shard }}\n",
                expect="breadth.yml:arm uploads no shard-ids-arm-",
            ),
            _plant_in_job(
                "the arm leg's planned suite collected on an x86 runner",
                _BREADTH,
                "planned-ids-arm",
                "    runs-on: ubuntu-24.04-arm\n",
                "    runs-on: ubuntu-24.04\n",
                expect="breadth.yml:planned-ids-arm runs on ubuntu-24.04, not arm's",
            ),
            _plant_in_job(
                "the coverage leg's planned suite collected on another Python",
                _BREADTH,
                "planned-ids-coverage-shard",
                '      PY: "3.12.15"\n',
                '      PY: "3.13.16"\n',
                expect="breadth.yml:planned-ids-coverage-shard collects on Python 3.13.16",
            ),
            _plant_in_job(
                "the ubuntu leg's planned suite collected without the leg's env",
                _BREADTH,
                "planned-ids-ubuntu",
                '      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n',
                "",
                expect="breadth.yml:planned-ids-ubuntu's env is",
            ),
            _plant(
                "the ubuntu leg's planned collection writing the arm leg's file",
                _BREADTH,
                '--shard-ids "$RUNNER_TEMP/ids/planned-ids-ubuntu.txt"',
                '--shard-ids "$RUNNER_TEMP/ids/planned-ids-arm.txt"',
                expect="breadth.yml:planned-ids-ubuntu does not write its ids to",
            ),
            _plant(
                "a planned collection over a list other than the universe",
                _BREADTH,
                '--shard-list "$RUNNER_TEMP/universe.txt" --junitxml=reports/junit-planned-arm.xml',
                '--shard-list "$RUNNER_TEMP/shard.txt" --junitxml=reports/junit-planned-arm.xml',
                expect="breadth.yml:planned-ids-arm collects a list other than",
            ),
            _plant(
                "no planned collection for a leg",
                _BREADTH,
                "  planned-ids-macos:\n",
                "  planned-ids-macos-renamed:\n",
                expect="breadth.yml has no planned-ids-macos job",
            ),
            _plant_job_copy(
                "a new suite leg, copied from the arm leg, that no union names",
                _BREADTH,
                "arm",
                "arm-extra",
                expect="breadth.yml:arm-extra runs suite shards that no union holds",
            ),
            _plant_in_job(
                "the macos leg's planned collection uploading its ids under another name",
                _BREADTH,
                "planned-ids-macos",
                "          name: planned-ids-macos\n",
                "          name: planned-ids-macos-renamed\n",
                expect="breadth.yml:planned-ids-macos uploads no planned-ids-macos",
            ),
            _plant_in_job(
                "a planned collection whose list is a shard's, with no `shards universe`",
                _BREADTH,
                "planned-ids-arm",
                'shards universe --out "$RUNNER_TEMP/universe.txt"',
                'shards list --plan "$RUNNER_TEMP/plan.json" --leg arm --shard 1/4'
                ' --out "$RUNNER_TEMP/universe.txt"',
                expect="breadth.yml:planned-ids-arm does not collect `shards universe`'s files",
            ),
            _plant_in_job(
                "a planned collection that runs a second collection",
                _BREADTH,
                "planned-ids-ubuntu",
                "      - name: pytest (collect the planned suite, writing its ids)\n",
                "      - name: an earlier collection\n"
                "        run: uv run pytest --collect-only -qq -n0 tests/test_cli.py\n"
                "      - name: pytest (collect the planned suite, writing its ids)\n",
                expect="breadth.yml:planned-ids-ubuntu does not collect `shards universe`'s files",
            ),
            _plant_in_job(
                "the coverage leg's planned suite collected in a venv on another Python",
                _BREADTH,
                "planned-ids-coverage-shard",
                '          python-version: "3.12.15"\n',
                '          python-version: "3.13.16"\n',
                expect="breadth.yml:planned-ids-coverage-shard's setup-uv sets up Python",
            ),
            Control(
                "the gating union downloading the canary's files too passes: it reads its legs'",
                _nothing,
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "breadth-non-reusable-legs-match-the-jobs",
        non_reusable_violations,
        (
            _plant(
                "a security leg that carries an earlier result",
                _BREADTH,
                "      - name: pytest (adversarial scanner corpus)\n",
                "      - name: carry\n"
                "        run: python3 tools/ci/breadth_evidence.py decide --key security\n"
                "      - name: pytest (adversarial scanner corpus)\n",
            ),
            _plant(
                "a new job running tests/security outside the set",
                _BREADTH,
                "jobs:\n  freshness:\n",
                "jobs:\n  security-extra:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: uv run pytest tests/security\n  freshness:\n",
            ),
            _plant(
                "a new job running -m security outside the set",
                _BREADTH,
                "jobs:\n  freshness:\n",
                "jobs:\n  marked:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: uv run pytest -m security\n  freshness:\n",
            ),
            _plants(
                "a never-reused leg's record key renamed away",
                _BREADTH,
                (
                    ("entry/audit__1.json", "entry/audit-deps__1.json"),
                    ("entry/audit__1.json", "entry/audit-deps__1.json"),
                ),
                expect="NON_REUSABLE names audit, which no breadth.yml job writes",
            ),
            _plant(
                "a suite leg, a security entry, that carries an earlier result",
                _BREADTH,
                "      - name: Install (locked)\n        run: uv sync --locked --extra dev\n"
                "      - name: Install tmux (durable sessions — real-daemon test leg)\n",
                "      - name: carry\n"
                "        run: python3 tools/ci/breadth_evidence.py decide"
                ' --key "ubuntu-24.04-3.13/1"\n'
                "      - name: Install (locked)\n        run: uv sync --locked --extra dev\n"
                "      - name: Install tmux (durable sessions — real-daemon test leg)\n",
                expect="breadth.yml:ubuntu is never reused, yet it carries an earlier result",
            ),
            _plant(
                "a new suite leg that may carry",
                _BREADTH,
                "jobs:\n  freshness:\n",
                "jobs:\n  suite-extra:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: uv run pytest -n auto tests\n  freshness:\n",
                expect="breadth.yml:suite-extra runs security tests but is not in NON_REUSABLE",
            ),
            _plant(
                "a collect-only leg outside the set, which runs no test, passes",
                _BREADTH,
                "jobs:\n  freshness:\n",
                "jobs:\n  collect-extra:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: uv run pytest --collect-only -qq tests\n  freshness:\n",
                fails=False,
            ),
            _plant(
                "the same leg without --collect-only",
                _BREADTH,
                "jobs:\n  freshness:\n",
                "jobs:\n  collect-extra:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: uv run pytest -qq tests\n  freshness:\n",
                expect="breadth.yml:collect-extra runs security tests but is not in NON_REUSABLE",
            ),
            _plant(
                "the same leg with its --co hidden in an echo",
                _BREADTH,
                "jobs:\n  freshness:\n",
                "jobs:\n  collect-extra:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: echo pytest --co && uv run pytest -qq tests\n"
                "  freshness:\n",
                expect="breadth.yml:collect-extra runs security tests but is not in NON_REUSABLE",
            ),
            Control(
                "rails-macos, ordinary and tree-reused, still carries",
                _nothing,
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "breadth-evidence-named-by-digest-kept-90-days",
        evidence_artifact_violations,
        (
            _plant(
                "a fixed name",
                _BREADTH,
                "name: breadth-evidence-${{ steps.report.outputs.digest }}",
                "name: breadth-evidence-latest",
            ),
            _plant(
                "retention-days: 7",
                _BREADTH,
                "          retention-days: 90\n",
                "          retention-days: 7\n",
            ),
            _plant(
                "no record uploaded",
                _BREADTH,
                "name: breadth-evidence-${{ steps.report.outputs.digest }}",
                "name: evidence",
            ),
        ),
    ),
    _toolchain_row(
        "breadth-report-writes-only-issues-only-on-main",
        report_permissions_violations,
        (
            _plant(
                "contents: write",
                _BREADTH,
                "      contents: read\n      actions: read\n      issues: write",
                "      contents: write\n      actions: read\n      issues: write",
            ),
            _plant(
                "an issue step with no ref gate",
                _BREADTH,
                "        if: ${{ github.ref == 'refs/heads/main' }}\n        env:\n",
                "        env:\n",
            ),
            Control("issues: write alone, on main, passes", _nothing, fails=False),
        ),
    ),
    _toolchain_row(
        "breadth-shards-record-what-they-ran",
        breadth_shards_violations,
        (
            Control(
                "no script install-smoke runs installs a managed Python before `uv python find`",
                lambda root: [
                    plant.plant(root)
                    for plant in (
                        _plant(
                            "",
                            "tools/ci/install_smoke_current.sh",
                            "uv tool install --python 3.13 .\n",
                            "uv pip install --python 3.13 .\n",
                        ),
                        _plant(
                            "",
                            "deploy/website/install.sh",
                            '    uv tool install --upgrade --python "$PC_PYTHON"',
                            '    uv pip install --upgrade --python "$PC_PYTHON"',
                        ),
                    )
                ],
                expect="breadth.yml:install-smoke runs `uv python find` before any managed Python",
            ),
            _plant(
                "a step running a script the tree does not hold",
                _BREADTH,
                "        run: sh tools/ci/install_smoke_current.sh\n",
                "        run: sh tools/ci/install_smoke_gone.sh\n",
                expect="breadth.yml:install-smoke runs tools/ci/install_smoke_gone.sh, which is"
                " not a file",
            ),
            _plant(
                "no managed Python before `uv python find`",
                _BREADTH,
                "      - name: the uv-managed Python this shard runs on\n"
                '        run: uv venv "$RUNNER_TEMP/shard-python"\n',
                "",
            ),
            _plant(
                "a decision that crashes skips the shard",
                _BREADTH,
                "        id: decide\n        continue-on-error: true\n",
                "        id: decide\n",
            ),
            _plant(
                "install-smoke's tree falls back to a constant",
                _BREADTH,
                '--tree "${TREE:?freshness named no tree}"',
                "--tree \"$(git rev-parse 'HEAD^{tree}' 2>/dev/null || echo unknown)\"",
            ),
            _plant(
                "the served installer hashed from a pipe",
                _BREADTH,
                'if curl -fsSL --retry 3 --max-time 60 -o "$RUNNER_TEMP/served-install.sh"'
                " https://personalclaw.dev/install; then",
                "installer=$(curl -fsSL https://personalclaw.dev/install | sha256sum)\n"
                "          if true; then",
            ),
            _plant(
                "a failed coverage download read as carried",
                _BREADTH,
                "      - name: Download the coverage data of every shard\n",
                "      - name: Download the coverage data of every shard\n"
                "        continue-on-error: true\n",
            ),
            _plant(
                "a shard count other than SHARDS'",
                _BREADTH,
                'python: ["3.12.15"]\n        shard: [1, 2, 3, 4, 5, 6]\n',
                'python: ["3.12.15"]\n        shard: [1, 2, 3, 4, 5]\n',
            ),
            _plant(
                "a second matrix dimension doubling a leg's shards",
                _BREADTH,
                'python: ["3.13.16"]\n        shard: [1, 2, 3, 4]\n',
                'python: ["3.13.16", "3.13.15"]\n        shard: [1, 2, 3, 4]\n',
            ),
            _plant(
                "a leg that excludes a shard",
                _BREADTH,
                'python: ["3.12.15"]\n        shard: [1, 2, 3, 4, 5, 6]\n',
                'python: ["3.12.15"]\n        shard: [1, 2, 3, 4, 5, 6]\n'
                "        exclude:\n          - shard: 6\n",
            ),
            _plant(
                "a leg that writes no entry",
                _BREADTH,
                "  report:\n",
                "  extra:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: uv run pytest -n auto -q\n  report:\n",
            ),
            _plant(
                "two legs under one key",
                _BREADTH,
                "  report:\n",
                "  ubuntu-again:\n    needs: [freshness]\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n        shard: [1, 2, 3, 4]\n    steps:\n"
                "      - run: python3 tools/ci/breadth_evidence.py ran"
                ' --out "$RUNNER_TEMP/entry/ubuntu-24.04-3.13__${{ matrix.shard }}.json"\n'
                "  report:\n",
            ),
            _plant(
                "a decision under another leg's key",
                _BREADTH,
                'decide --key "rails-macos-26-3.12/1"',
                'decide --key "macos-26-3.12/1"',
            ),
            _plant(
                "an unreadable installer only warns",
                _BREADTH,
                "            exit 1\n          fi\n      - name: record what this shard ran\n",
                "          fi\n      - name: record what this shard ran\n",
            ),
            _plant(
                "install-smoke without the night",
                _BREADTH,
                ' --external "night=${NIGHT:?freshness named no night}"\n'
                '          if [ "$installer" = unreadable ]',
                '\n          if [ "$installer" = unreadable ]',
            ),
            _plant(
                "audit without the night",
                _BREADTH,
                '--node "$(node --version 2>/dev/null || echo none)" \\\n'
                '            --external "night=${NIGHT:?freshness named no night}"\n',
                '--node "$(node --version 2>/dev/null || echo none)"\n',
            ),
            _plant(
                "audit's night from the attempt's clock",
                _BREADTH,
                '--node "$(node --version 2>/dev/null || echo none)" \\\n'
                '            --external "night=${NIGHT:?freshness named no night}"\n',
                '--node "$(node --version 2>/dev/null || echo none)" \\\n'
                '            --external "night=$(date -u +%F)"\n',
            ),
            _plant(
                "the night read from the attempt's start",
                _BREADTH,
                "--jq .created_at)",
                "--jq .run_started_at)",
            ),
            _plant("a cron slot other than the night's", _BREADTH, '"37 9 * * *"', '"0 3 * * *"'),
            _plant(
                "install-smoke's TREE falls back in its expression",
                _BREADTH,
                "TREE: ${{ needs.freshness.outputs.tree }}\n",
                "TREE: ${{ needs.freshness.outputs.tree || 'unknown' }}\n",
            ),
            _plant(
                "install-smoke's tree falls back in the shell",
                _BREADTH,
                '--tree "${TREE:?freshness named no tree}"',
                '--tree "${TREE:-unknown}"',
            ),
            _plant(
                "the served installer hashed from a pipe over a continued line",
                _BREADTH,
                "            installer=unreadable\n",
                "            installer=$(curl -fsSL --retry 3 \\\n"
                "                 https://personalclaw.dev/install | sha256sum | cut -d' ' -f1)\n",
            ),
        ),
    ),
    _toolchain_row(
        "breadth-freshness-reads-its-own-ref",
        freshness_ref_violations,
        (
            _plant("freshness without --ref", _BREADTH, ' --ref "$GITHUB_REF"', ""),
            _plant(
                "freshness on the short ref name, which a tag called main shares",
                _BREADTH,
                ' --ref "$GITHUB_REF"',
                ' --ref "$GITHUB_REF_NAME"',
            ),
            _plant(
                "a decision without the ref",
                _BREADTH,
                '--previous "$RUNNER_TEMP/previous/previous.json" --ref "$GITHUB_REF"',
                '--previous "$RUNNER_TEMP/previous/previous.json"',
            ),
            _plant(
                "an entry without its attempt",
                _BREADTH,
                ' --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT"',
                ' --outcome "$OUTCOME"',
            ),
            _plant(
                "a leg's job renamed away from its key",
                _BREADTH,
                "    name: breadth-leg audit/1\n",
                "    name: audit\n",
            ),
            _plant(
                "a tree-keyed shard downloading the map off main",
                _BREADTH,
                "        if: ${{ github.ref == 'refs/heads/main' }}\n"
                "        continue-on-error: true\n",
                "        continue-on-error: true\n",
            ),
            _plant(
                "report without the run attempt",
                _BREADTH,
                ' --attempt "$GITHUB_RUN_ATTEMPT"',
                "",
            ),
            _plant(
                "a never-reused leg downloading the map",
                _BREADTH,
                "      - name: pytest (adversarial scanner corpus)\n",
                "      - name: the earlier record's results\n"
                "        uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"
                " # v8.0.2\n"
                "        with:\n"
                "          name: breadth-previous\n"
                "          path: ${{ runner.temp }}/previous\n"
                "      - name: pytest (adversarial scanner corpus)\n",
            ),
            Control("the ref named, as the tree has it, passes", _nothing, fails=False),
        ),
    ),
    _toolchain_row(
        "breadth-collection-parses-loads-the-guard-over-the-whole-suite",
        collection_parses_violations,
        (
            _plant(
                "the leg without the guard",
                _BREADTH,
                " -n0 -p collection_parse_guard tests --junitxml",
                " -n0 tests --junitxml",
            ),
            _plant(
                "the leg collecting the rails alone",
                _BREADTH,
                " -p collection_parse_guard tests --junitxml",
                " -p collection_parse_guard tests/rails --junitxml",
            ),
            _plant(
                "the leg loading another plugin of a longer name",
                _BREADTH,
                " -p collection_parse_guard tests --junitxml",
                " -p collection_parse_guard_off tests --junitxml",
            ),
            # Each narrowing below keeps the text the plants above edit, so it is the check, not
            # a stale anchor, that fails it.
            _plant(
                "the leg ignoring the rails",
                _BREADTH,
                "pytest --collect-only -qq -n0 -p collection_parse_guard",
                "pytest --collect-only -qq --ignore=tests/rails -n0 -p collection_parse_guard",
            ),
            _plant(
                "the leg ignoring the rails by glob",
                _BREADTH,
                "pytest --collect-only -qq -n0 -p collection_parse_guard",
                "pytest --collect-only -qq --ignore-glob=tests/rails/* -n0 -p "
                "collection_parse_guard",
            ),
            _plant(
                "the leg deselecting by keyword",
                _BREADTH,
                "pytest --collect-only -qq -n0 -p collection_parse_guard",
                "pytest --collect-only -qq -k not_rails -n0 -p collection_parse_guard",
            ),
            _plant(
                "PYTEST_ADDOPTS in the leg's pytest step",
                _BREADTH,
                "          PYTHONPATH: tests\n        run: uv run pytest --collect-only",
                "          PYTHONPATH: tests\n          PYTEST_ADDOPTS: --ignore=tests/rails\n"
                "        run: uv run pytest --collect-only",
            ),
            _plant(
                "PYTEST_ADDOPTS in the leg's job env",
                _BREADTH,
                '    timeout-minutes: 20\n    env:\n      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n',
                '    timeout-minutes: 20\n    env:\n      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n'
                "      PYTEST_ADDOPTS: --ignore=tests/rails\n",
            ),
            _plant(
                "PYTEST_ADDOPTS written to $GITHUB_ENV by an earlier step",
                _BREADTH,
                "        if: ${{ steps.decide.outputs.run != 'false' }}\n"
                "        run: uv sync --locked --extra dev\n"
                "      - name: pytest (collect the whole suite",
                "        if: ${{ steps.decide.outputs.run != 'false' }}\n"
                "        run: |\n          uv sync --locked --extra dev\n"
                '          echo "PYTEST_ADDOPTS=--ignore=tests/rails" >> "$GITHUB_ENV"\n'
                "      - name: pytest (collect the whole suite",
            ),
        ),
    ),
    PER_TEST_TEMPORARY_FOLDERS,
    NO_TRUSTED_CHECKOUT_OF_CANDIDATE_CODE,
    NO_CANDIDATE_JOB_HOLDS_A_WRITE_OR_A_SECRET,
    CONTRACT_RUN_HOLDS_NOTHING,
    ONLY_THE_RELEASE_CONTROLLER_PUBLISHES,
    _toolchain_row(
        "every job that runs steps has its own timeout-minutes",
        job_timeout_violations,
        (
            _plant(
                "a planned stage job's cap read from another key",
                _PR_YML,
                "    timeout-minutes: ${{ matrix.timeout }}\n",
                "    timeout-minutes: ${{ matrix.cap }}\n",
                expect="pr.yml:test has timeout-minutes '${{ matrix.cap }}'",
            ),
            _plant(
                "a matrix.timeout cap in a job whose matrix is not the plan's",
                _MERGE_YML,
                "      matrix: ${{ fromJSON(needs.changes.outputs.test-3) }}\n",
                "      matrix: ${{ fromJSON(needs.other.outputs.test-3) }}\n",
                expect="merge.yml:test-3 has timeout-minutes '${{ matrix.timeout }}'",
            ),
            _plant(
                "a job in a cancelling group without one",
                _MERGE_YML,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n",
            ),
            _plant(
                "a job in a workflow with no concurrency group without one",
                ".github/workflows/release-artifacts.yml",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 10\n",
                "    runs-on: ubuntu-24.04\n",
            ),
            _plant(
                "a job in a non-cancelling group without one",
                _BREADTH,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 10\n",
                "    runs-on: ubuntu-24.04\n",
            ),
            _plant(
                "a job whose cap is zero",
                _MERGE_YML,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 0\n",
            ),
            _plant(
                "a job whose cap is negative",
                _MERGE_YML,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: -5\n",
            ),
            _plant(
                "a job whose cap is a fraction",
                _MERGE_YML,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5.5\n",
            ),
            _plant(
                "a job whose cap is a string",
                _MERGE_YML,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: '5'\n",
            ),
            _plant(
                "a job whose cap is a boolean",
                _MERGE_YML,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: true\n",
            ),
            Control(
                "a job that calls a reusable workflow, without one, passes",
                _plant(
                    "a reusable call",
                    _MERGE_YML,
                    "jobs:\n",
                    "jobs:\n  called:\n    uses: ./.github/workflows/reusable.yml\n",
                ).plant,
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "breadth keeps main's earlier shard plan",
        breadth_keeps_its_plan_violations,
        (
            _plant(
                "a plan made afresh every night",
                _BREADTH,
                _KEEP_GATE + "\n",
                "",
            ),
            _plant(
                "the earlier plan kept off main too",
                _BREADTH,
                _KEEP_GATE,
                'keep="--keep-from-main breadth.yml"',
            ),
            _plant(
                "a plan that may keep its own run's",
                _BREADTH,
                ' --run-id "$GITHUB_RUN_ID" $keep',
                " $keep",
            ),
            _plant(
                "a plan gone before the next night",
                _BREADTH,
                "          retention-days: 7\n\n  ubuntu:\n",
                "          retention-days: 1\n\n  ubuntu:\n",
            ),
            Control("the kept plan, as the tree has it, passes", _nothing, fails=False),
        ),
    ),
    _toolchain_row(
        "every suite shard runs its share of its run's one plan",
        suite_shard_violations,
        (
            _plant(
                "a suite entry with no shard list",
                catalogue.CATALOGUE,
                _SUITE_PYTEST,
                _SUITE_PYTEST.replace(' "--shard-list", "{runner_temp}/shard.txt",', ""),
                expect="the catalogue's python-suite runs the suite without its shard's list",
            ),
            _plants(
                "a suite entry listing another N",
                catalogue.CATALOGUE,
                (
                    (
                        '"--leg", "test-shard", "--shard", "{shard}/3"',
                        '"--leg", "test-shard", "--shard", "{shard}/2"',
                    ),
                ),
                expect="python-suite does not list its own shard of 3 before pytest",
            ),
            _plant(
                "the plan naming a leg in another N",
                _MERGE_YML,
                "--leg test-shard:main-push:junit-test-shard-:3",
                "--leg test-shard:main-push:junit-test-shard-:2",
                expect="merge.yml's plan names test-shard ('junit-test-shard-', 2), not in 3",
            ),
            _plant(
                "the plan naming the macOS leg at one shard more than the catalogue",
                _MERGE_YML,
                f"--leg test-macos:main-push:junit-test-macos-:{_MACOS_SHARDS}\n",
                f"--leg test-macos:main-push:junit-test-macos-:{_MACOS_SHARDS + 1}\n",
                expect=f"merge.yml's plan names test-macos ('junit-test-macos-',"
                f" {_MACOS_SHARDS + 1}), not in {_MACOS_SHARDS}",
            ),
            _plant(
                "the plan leaving a suite entry's leg out",
                _MERGE_YML,
                f"          --leg test-macos:main-push:junit-test-macos-:{_MACOS_SHARDS}\n",
                "",
                expect="merge.yml's plan names test-macos None",
            ),
            _plant(
                "a report prefix the entry's reports do not carry",
                _MERGE_YML,
                "--leg test-shard:main-push:junit-test-shard-:3",
                "--leg test-shard:main-push:junit-test-shards-:3",
                expect="its reports are not named junit-test-shards-*",
            ),
            _plant(
                "a suite entry that does not read the plan",
                catalogue.CATALOGUE,
                'external_inputs = ["shard-history"]\ntoolchain = ["uv-sync", "tmux"]\n'
                'cache = ["uv"]\nsecurity = "security"\nplatform = "macos-26"',
                'external_inputs = []\ntoolchain = ["uv-sync", "tmux"]\ncache = ["uv"]\n'
                'security = "security"\nplatform = "macos-26"',
                expect="python-suite-macos does not read the run's shard plan",
            ),
            _plant(
                "a suite entry back on pytest-split",
                catalogue.CATALOGUE,
                _SUITE_PYTEST,
                _SUITE_PYTEST.replace('"-q",', '"-q", "--splits", "3",'),
                expect="python-suite still splits with pytest-split",
            ),
            _plant(
                "a plan nobody checks",
                _MERGE_YML,
                "      - name: every collectable file is in exactly one shard of each leg\n"
                "        run: python3 -m tools.ci shards check"
                ' --plan "$RUNNER_TEMP/shard-plan/plan.json"\n',
                "",
                expect="merge.yml:shard-plan uploads a plan it never checked",
            ),
            _plant(
                "a breadth leg the plan leaves out",
                _BREADTH,
                "            --leg arm:breadth.yml:junit-arm-:4 \\\n",
                "",
            ),
            _plant(
                "a junit prefix the leg's reports do not carry",
                _BREADTH,
                "--leg canary:breadth.yml:junit-canary-:4",
                "--leg canary:breadth.yml:junit-canaries-:4",
            ),
            _plant(
                "a shard back on pytest-split",
                _BREADTH,
                'tests --shard-list "$RUNNER_TEMP/shard.txt" --junitxml=reports/junit-arm.xml',
                'tests --shard-list "$RUNNER_TEMP/shard.txt" --splits 4'
                " --junitxml=reports/junit-arm.xml",
            ),
            Control("every shard, as the tree has it, passes", _nothing, fails=False),
        ),
    ),
    _toolchain_row(
        "every pytest step that runs the suite sets its worker count",
        suite_workers_violations,
        (
            # The loader refuses an entry whose workers no step passes, so the plant drops both.
            _plants(
                "a suite entry with no -n",
                catalogue.CATALOGUE,
                (
                    (_SUITE_PYTEST, _SUITE_PYTEST.replace('"-n", "auto", ', "")),
                    (
                        'timeout_min = 55\nworkers = "auto"\nshards = 3\n',
                        "timeout_min = 55\nshards = 3\n",
                    ),
                ),
                expect="the catalogue's python-suite runs the suite with no -n",
            ),
            _plant(
                "a breadth shard with no -n",
                _BREADTH,
                "uv run pytest -n auto --dist worksteal -q tests"
                ' --shard-list "$RUNNER_TEMP/shard.txt"'
                " --junitxml=reports/junit-ubuntu.xml",
                'uv run pytest -q tests --shard-list "$RUNNER_TEMP/shard.txt"'
                " --junitxml=reports/junit-ubuntu.xml",
            ),
            _plant(
                "an entry's pytest that names no path, so collects testpaths",
                catalogue.CATALOGUE,
                '"pytest", "tests/test_npm_advisory_floors.py",',
                '"pytest",',
                expect="runs the suite with no -n",
            ),
            _plant(
                "an entry's pytest that names tests/ itself",
                catalogue.CATALOGUE,
                '"pytest", "tests/test_npm_advisory_floors.py",',
                '"pytest", "tests/",',
                expect="runs the suite with no -n",
            ),
            _plant(
                "an entry's root venv python -m pytest of the suite",
                catalogue.CATALOGUE,
                '"uv", "run", "pytest", "tests/test_npm_advisory_floors.py",',
                '".venv/bin/python", "-m", "pytest",',
                expect="runs the suite with no -n",
            ),
            _plants(
                "a suite entry with -n4 passes",
                catalogue.CATALOGUE,
                (
                    (_SUITE_PYTEST, _SUITE_PYTEST.replace('"-n", "auto",', '"-n4",')),
                    (
                        'timeout_min = 55\nworkers = "auto"\nshards = 3\n',
                        "timeout_min = 55\nworkers = 4\nshards = 3\n",
                    ),
                ),
                fails=False,
            ),
            _plant(
                "a breadth shard with --numprocesses=4 passes",
                _BREADTH,
                "uv run pytest -n auto --dist worksteal -q tests"
                ' --shard-list "$RUNNER_TEMP/shard.txt"'
                " --junitxml=reports/junit-ubuntu.xml",
                "uv run pytest --numprocesses=4 --dist worksteal -q tests"
                ' --shard-list "$RUNNER_TEMP/shard.txt"'
                " --junitxml=reports/junit-ubuntu.xml",
                fails=False,
            ),
            Control("steps that name their own files, with no -n, pass", _nothing, fails=False),
        ),
    ),
    _toolchain_row(
        "no workflow command passes --no-cov",
        no_cov_violations,
        (
            _plant(
                "--no-cov on an entry's single-file step",
                catalogue.CATALOGUE,
                '"tests/test_npm_advisory_floors.py",',
                '"tests/test_npm_advisory_floors.py", "--no-cov",',
                expect="the catalogue's rails-checks passes --no-cov",
            ),
            _plant(
                "--no-cov on a suite entry",
                catalogue.CATALOGUE,
                _SUITE_PYTEST,
                _SUITE_PYTEST.replace('"-q",', '"--no-cov", "-q",'),
                expect="the catalogue's python-suite passes --no-cov",
            ),
            _plant(
                "--no-cov inside a folded multi-line run",
                _BREADTH,
                "          uv run pytest\n          --junitxml=reports/junit-security-corpus.xml\n",
                "          uv run pytest\n          --no-cov\n"
                "          --junitxml=reports/junit-security-corpus.xml\n",
            ),
            Control("the coverage shard's --cov passes", _nothing, fails=False),
        ),
    ),
    FIXED_WAITS,
    NO_TEST_WALKS_A_SOURCE_ROOT_ITSELF,
)


def run_controls(row: Row, subtests, tmp_path: Path) -> None:
    """A row's scaffold holds, and each control fails or passes as it says. The scaffold is laid
    out once (:class:`laid_tree.Laid`): each control runs on it put back as it was, not on a copy
    of its own (a copy per control was 34,090 files over the contract's 795 controls, and a
    loaded disk took one past its cap). It runs under a folder named for the control, as a copy
    did: the suite keeps bytecode in one folder per run by each source's path
    (``tests/pycache_guard.py``), so a module a plant edited is never read for the next one's."""
    root, laid = tmp_path / "scaffold", None
    with subtests.test("the scaffold satisfies the check"):
        row.scaffold(root)
        laid = laid_tree.Laid.take(root)  # as it was built, before the check reads it
        assert row.check(root) == []
    for index, control in enumerate(row.controls):
        with subtests.test(control.name):
            assert laid is not None, "the scaffold was never laid out"
            root = root.rename(tmp_path / f"control-{index}")
            laid.reset(root)
            control.plant(root)
            found = row.check(root)
            if control.fails:
                assert found, f"the planted violation went unseen: {control.name}"
                assert any(control.expect in v for v in found), "\n".join(found)
            else:
                assert found == [], "\n".join(found)


def run_row(row: Row, subtests, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """One row: its scaffold holds, each control fails or passes as it says
    (:func:`run_controls`), and the tree holds. Every contract table's test is this body."""
    run_controls(row, subtests, tmp_path)
    with subtests.test("the tree"):
        violations = row.check(REPO)
        assert violations == [], "\n".join(violations)
    if row.on_tree is not None:
        row.on_tree(request)


# ── Each control runs on the scaffold as it was laid out ────────────────────────────────────

_LAID_MODULE = "VALUE = 1\n"
_LAID_ENTRIES = ["a.txt", "laid_module.py", "link", "run.sh", "sub", "sub/b.txt"]
_IMPORTS = (
    "import sys; sys.path.insert(0, sys.argv[1]); import laid_module; print(laid_module.VALUE)"
)


def _laid_scaffold(root: Path) -> None:
    """One of each kind of thing a plant changes: a file, a file in a folder, an executable, a
    link, and a module the check imports."""
    _write(root, "a.txt", "a\n")
    _write(root, "sub/b.txt", "b\n")
    _write(root, "run.sh", "#!/bin/sh\n")
    (root / "run.sh").chmod(0o755)
    (root / "link").symlink_to("a.txt")
    _write(root, "laid_module.py", _LAID_MODULE)


def _imported(root: Path) -> str:
    """The module's value as a child that imports it reads, its bytecode kept beside its source as
    Python keeps it by default: not in the suite's own per-run folder (``tests/pycache_guard.py``,
    which a control's own folder name keeps apart anyway), and written."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"}
    }
    argv = [sys.executable, "-c", _IMPORTS, str(root)]
    return subprocess.run(argv, capture_output=True, text=True, check=True, env=env).stdout.strip()


def _as_laid(root: Path) -> list[str]:
    """How *root* differs from :func:`_laid_scaffold`: what it holds, then what a child that
    imports its module reads."""
    found = []
    entries = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    if entries != _LAID_ENTRIES:
        found.append(f"the tree holds {entries}")
    if (root / "a.txt").read_text(encoding="utf-8") != "a\n":
        found.append("a.txt was changed")
    if stat.S_IMODE((root / "run.sh").stat().st_mode) != 0o755:
        found.append("run.sh is no longer executable")
    if os.readlink(root / "link") != "a.txt":
        found.append("link points elsewhere")
    if (value := _imported(root)) != "1":
        found.append(f"laid_module.py runs as VALUE = {value}")
    return found


def _changes_one_of_each(root: Path) -> None:
    """Changes one of each in the shared scaffold, and imports a same-size edit of its module
    stamped with the laid file's own second, as an edit made and run within the second the
    scaffold was laid in is: its bytecode, kept beside it, matches the laid file put back by time
    and size, so a reset that kept it would run the edit again."""
    module = root / "laid_module.py"
    laid = module.stat().st_mtime_ns
    _write(root, "a.txt", "changed\n")
    (root / "sub" / "b.txt").unlink()
    _write(root, "new/added.txt", "added\n")
    (root / "run.sh").chmod(0o644)
    (root / "link").unlink()
    (root / "link").symlink_to("sub")
    _write(root, "laid_module.py", _LAID_MODULE.replace("1", "2"))
    os.utime(module, ns=(laid, laid))
    assert _imported(root) == "2", "premise: the edit runs"
    assert (root / "__pycache__").is_dir(), "premise: its bytecode is kept beside it"


_LAID_ROW = Row(
    name="each control runs on the scaffold as it was laid out",
    check=_as_laid,
    scaffold=_laid_scaffold,
    controls=(
        Control("a plant that changes one of each and runs a same-size edit", _changes_one_of_each),
        Control(
            "the control after it, planting nothing, finds the scaffold as laid", _nothing, False
        ),
    ),
)


class _Recorded:
    """A ``subtests`` stand-in that records each failing subtest's message by its name."""

    def __init__(self) -> None:
        self.failed: dict[str, str] = {}

    @contextlib.contextmanager
    def test(self, msg: str = "", **_kwargs: Any) -> Iterator[None]:
        try:
            yield
        except AssertionError as exc:
            self.failed[msg] = str(exc)


def test_a_control_runs_on_the_scaffold_as_it_was_laid_out(subtests, tmp_path: Path) -> None:
    """The controls share one laid-out scaffold (:func:`run_controls`): a plant that changes one
    of each kind of thing in it, a compiled same-size edit of its module included, reaches none
    of the control after it."""
    run_controls(_LAID_ROW, subtests, tmp_path)


@pytest.mark.parametrize(
    ("kept", "control", "said"),
    [("nothing", 1, "the tree holds"), ("__pycache__", 0, "premise: the edit runs")],
    ids=["no reset", "a reset that keeps what an import wrote"],
)
def test_without_a_whole_reset_a_control_runs_on_what_another_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept: str, control: int, said: str
) -> None:
    """The mutants. With no reset, the control after the plant finds what it left. With one that
    keeps a ``__pycache__``, the bytecode the scaffold's check wrote for the laid module, in the
    second it was laid, is kept, and the plant's same-size edit runs that instead of itself: a
    module other than the one on disk runs, the hazard a reset that kept bytecode would leave."""
    real = laid_tree.Laid.reset

    def reset(laid: laid_tree.Laid, root: Path) -> list[str]:
        if kept == "nothing":
            return []
        aside = root.parent / "kept"
        if (root / kept).exists():
            (root / kept).rename(aside)
        undone = real(laid, root)
        if aside.exists():
            aside.rename(root / kept)
        return undone

    monkeypatch.setattr(laid_tree.Laid, "reset", reset)
    recorded = _Recorded()
    run_controls(_LAID_ROW, recorded, tmp_path)
    name = _LAID_ROW.controls[control].name
    assert said in recorded.failed.get(name, ""), recorded.failed


def _links_outside(outside: Path) -> Callable[[Path], None]:
    def plant(root: Path) -> None:
        (root / "a.txt").unlink()
        os.link(outside, root / "a.txt")

    return plant


@pytest.mark.parametrize("through", [False, True], ids=["the reset", "an in-place write"])
def test_a_reset_never_writes_through_a_link_a_plant_left_to_a_file_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through: bool
) -> None:
    """A plant that puts a hard link to a file outside the scaffold at a laid path: the reset
    after it puts the laid bytes back as a file of the tree's own, and the outside file is as it
    was. The mutant writes the laid bytes in place, through the link, into the outside file."""
    outside = tmp_path / "outside.txt"
    outside.write_text("not the scaffold's\n", encoding="utf-8")
    if through:

        def in_place(path: Path, laid: Any) -> None:
            path.write_bytes(laid.data)

        monkeypatch.setattr(laid_tree.Laid, "_write", staticmethod(in_place))
    row = Row(
        name="a reset never writes through a link a plant left",
        check=_as_laid,
        scaffold=_laid_scaffold,
        controls=(
            Control("a plant that links an outside file at a laid path", _links_outside(outside)),
            Control("the control after it finds the laid bytes", _nothing, False),
        ),
    )
    recorded = _Recorded()
    run_controls(row, recorded, tmp_path / "rows")
    if through:
        assert outside.read_text(encoding="utf-8") == "a\n", "premise: the mutant writes through"
    else:
        assert recorded.failed == {}, recorded.failed
        assert outside.read_text(encoding="utf-8") == "not the scaffold's\n"
        assert (tmp_path / "rows" / "control-1" / "a.txt").stat().st_nlink == 1


# ── A closed pull request's runs are cancelled ──────────────────────────────────────────────
#
# A concurrency group cancels a run only when a newer run joins it, and closing a pull request
# starts no run in the workflows that test it. `cancel-closed-pr-runs.yml` runs on `closed`, and
# each of its jobs joins one such workflow's group with cancel-in-progress. The row evaluates the
# group expressions against event contexts, as GitHub would, instead of comparing their text: on a
# merged pull request's close `github.ref` is the base branch, so a key copied from the workflow
# can evaluate to another group.

_CANCELLER = "cancel-closed-pr-runs.yml"
_CANCELLER_PATH = f"{_WORKFLOWS}/{_CANCELLER}"
#: The types a `pull_request:` trigger with no `types` runs on.
_OPENING_TYPES = frozenset({"opened", "synchronize", "reopened"})
_CLOSE_REPO = "PersonalClaw/PersonalClaw"
_CLOSE_FORK = "someone/PersonalClaw"
_CLOSE_HEAD = "bugfix-some-branch"
#: The merge commit: what `github.sha` is on the merge's push to main, and on a merged close.
_MERGE_SHA = "merge0000"
_EXPRESSION = re.compile(r"\$\{\{(.+?)\}\}", re.S)
_OPERAND = re.compile(r"\s*(!)?\s*([A-Za-z_][\w.-]*)\s*(?:(==|!=)\s*'([^']*)')?\s*")
_LITERAL = re.compile(r"\s*'([^']*)'\s*")
#: Two quoted literals compared, as a matrix property read from its leg leaves the comparison.
_LITERALS_COMPARED = re.compile(r"\s*'([^']*)'\s*(==|!=)\s*'([^']*)'\s*")


def _pr_context(
    *,
    head_repo: str,
    action: str,
    merged: bool = False,
    author: str = "a-contributor",
    number: int = 42,
    branch: str = _CLOSE_HEAD,
) -> dict[str, Any]:
    """A `pull_request` event's context: a pull request's run (`synchronize`), or its close."""
    merged_close = action == "closed" and merged
    head = {"ref": branch, "sha": "head2222", "repo": {"full_name": head_repo}}
    return {
        "github": {
            "repository": _CLOSE_REPO,
            "event_name": "pull_request",
            # GitHub's "Events that trigger workflows", `pull_request`: when a pull request closes
            # because it merged, the ref is the branch it merged into and the sha its merge commit.
            "ref": "refs/heads/main" if merged_close else f"refs/pull/{number}/merge",
            "ref_name": "main" if merged_close else f"{number}/merge",
            "sha": _MERGE_SHA if merged_close else "testmerge1111",
            "head_ref": branch,
            "base_ref": "main",
            "event": {
                "action": action,
                "number": number,
                "pull_request": {
                    "number": number,
                    "merged": merged,
                    "user": {"login": author},
                    "head": head,
                },
            },
        }
    }


def _main_push_context() -> dict[str, Any]:
    """The merge's own push to main."""
    return {
        "github": {
            "repository": _CLOSE_REPO,
            "event_name": "push",
            "ref": "refs/heads/main",
            "ref_name": "main",
            "sha": _MERGE_SHA,
            "head_ref": "",
            "base_ref": "",
            "event": {},
        }
    }


def _context_value(context: dict[str, Any], dotted: str) -> Any:
    """``a.b.c`` in *context*, or ``""`` for a missing link, as GitHub reads a missing property."""
    value: Any = context
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return ""
        value = value[part]
    return value


def _operand(context: dict[str, Any], text: str) -> Any:
    """One term: a quoted literal, a property, ``true``/``false``, or a property compared with a
    quoted literal. Anything else raises, so the row reports an expression it cannot read
    instead of guessing."""
    literal = _LITERAL.fullmatch(text)
    if literal is not None:
        return literal.group(1)
    compared = _LITERALS_COMPARED.fullmatch(text)
    if compared is not None:
        left, op, right = compared.groups()
        return (left.lower() == right.lower()) is (op == "==")
    match = _OPERAND.fullmatch(text)
    if match is None:
        raise ValueError(f"an expression this row cannot evaluate: {text.strip()!r}")
    negate, path, op, literal = match.groups()
    value = path == "true" if path in ("true", "false") else _context_value(context, path)
    if op is not None:
        same = str(value).lower() == literal.lower()
        value = same if op == "==" else not same
    return (not value) if negate else value


def _expression(context: dict[str, Any], text: str) -> Any:
    """``a || b`` and ``a && b`` as GitHub evaluates them: the first truthy alternative or the
    last one, and within an alternative the first falsy term or the last one."""
    value: Any = ""
    for alternative in text.split("||"):
        for term in alternative.split("&&"):
            value = _operand(context, term)
            if not value:
                break
        if value:
            return value
    return value


def _rendered(template: str, context: dict[str, Any]) -> str:
    """*template* with every ``${{ }}`` evaluated, as GitHub renders a value it sets."""

    def one(match: re.Match[str]) -> str:
        value = _expression(context, match.group(1))
        return ("true" if value else "false") if isinstance(value, bool) else str(value)

    return _EXPRESSION.sub(one, template)


def _evaluate(template: str, context: dict[str, Any]) -> str:
    """A group name with every ``${{ }}`` evaluated, lower-cased: GitHub compares group names
    without case."""
    return _rendered(template, context).lower()


def _cancels(value: object, context: dict[str, Any]) -> bool:
    if isinstance(value, bool):
        return value
    match = _EXPRESSION.fullmatch(str(value).strip())
    if match:
        return bool(_expression(context, match.group(1)))
    return str(value).strip() == "true"


def _pull_request_types(trigger: object) -> set[str]:
    types = trigger.get("types") if isinstance(trigger, dict) else None
    if types is None:
        return set(_OPENING_TYPES)
    return {str(t) for t in (types if isinstance(types, list) else [types])}


def _runs_on_pull_requests(tree: Tree) -> list[tuple[str, dict[str, Any]]]:
    """Every workflow but the canceller that runs on a `pull_request` event other than `closed`."""
    found = []
    for name, workflow in _workflows(tree):
        trigger = _triggers(workflow).get("pull_request", "absent")
        if name != _CANCELLER and trigger != "absent":
            if _pull_request_types(trigger) - {"closed"}:
                found.append((name, workflow))
    return found


def _canceller_shape(workflow: dict[str, Any]) -> list[str]:
    """It runs on `pull_request` `closed` and nothing else, holds no permission, and runs nothing
    but each job's Runner identity step: no checkout, no action, no secret."""
    bad: list[str] = []
    triggers = _triggers(workflow)
    if set(triggers) != {"pull_request"} or _pull_request_types(triggers["pull_request"]) != {
        "closed"
    }:
        bad.append(f"{_CANCELLER} runs on {triggers!r}, not on `pull_request` `closed` alone")
    if workflow.get("permissions") != {}:
        bad.append(f"{_CANCELLER} holds {workflow.get('permissions')!r}, not `permissions: {{}}`")
    for job_id, job in (workflow.get("jobs") or {}).items():
        where = f"{_CANCELLER}:{job_id}"
        if not isinstance(job, dict):
            bad.append(f"{where} is not a job")
            continue
        if job.get("permissions") != {}:
            bad.append(f"{where} holds {job.get('permissions')!r}, not `permissions: {{}}`")
        if "uses" in job:
            bad.append(f"{where} calls the workflow {job['uses']}")
        if "secrets." in json.dumps(job):
            bad.append(f"{where} references a secret")
        for step in job.get("steps") or []:
            if step.get("uses"):
                bad.append(f"{where} runs {step['uses']}: the closing run runs nothing")
            elif step.get("name") != "Runner identity":
                what = step.get("name") or step.get("run")
                bad.append(f"{where} runs {what!r}: the closing run runs nothing but its identity")
    return bad


def closed_pr_runs_violations(tree: Tree) -> list[str]:
    """Every workflow that runs on `pull_request` has its run cancelled when the pull request
    closes: a job of the canceller evaluates, on the close, to the group that workflow's run
    evaluated to, with cancel-in-progress, whether the pull request merged or not and whether it
    came from this repository or a fork. No job of it joins a push to main's group, nor, on a
    Dependabot pull request's close, the group every Dependabot head's runs wait in: those go on
    in their turn."""
    canceller = dict(_workflows(tree)).get(_CANCELLER)
    if canceller is None:
        return [f"{_CANCELLER} is missing, so a closed pull request's runs go on to their end"]
    bad = _canceller_shape(canceller)
    joins: list[tuple[str, str, object]] = []
    for job_id, job in (canceller.get("jobs") or {}).items():
        concurrency = job.get("concurrency") if isinstance(job, dict) else None
        if isinstance(concurrency, dict) and "group" in concurrency:
            cancel = concurrency.get("cancel-in-progress", False)
            joins.append((job_id, str(concurrency["group"]), cancel))
        else:
            bad.append(f"{_CANCELLER}:{job_id} joins no concurrency group")

    def closing_groups(context: dict[str, Any], *, cancelling: bool) -> list[tuple[str, str]]:
        groups = []
        for job_id, group, cancel in joins:
            try:
                if not cancelling or _cancels(cancel, context):
                    groups.append((job_id, _evaluate(group, context)))
            except ValueError as exc:
                bad.append(f"{_CANCELLER}:{job_id}: {exc}")
        return groups

    for name, workflow in _runs_on_pull_requests(tree):
        concurrency = workflow.get("concurrency")
        if not isinstance(concurrency, dict) or "group" not in concurrency:
            bad.append(
                f"{name} runs on pull_request with no workflow-level concurrency group, so no "
                "run can cancel its runs"
            )
            continue
        for head_repo, origin in ((_CLOSE_REPO, "this repository"), (_CLOSE_FORK, "a fork")):
            run = _pr_context(head_repo=head_repo, action="synchronize")
            try:
                key = _evaluate(str(concurrency["group"]), run)
            except ValueError as exc:
                bad.append(f"{name}: {exc}")
                break
            for merged in (False, True):
                close = _pr_context(head_repo=head_repo, action="closed", merged=merged)
                if key not in {group for _job, group in closing_groups(close, cancelling=True)}:
                    how = "merged" if merged else "closed unmerged"
                    bad.append(
                        f"{name}: no {_CANCELLER} job cancels the run of a pull request from "
                        f"{origin} once it is {how} (the run's group is {key!r})"
                    )

    # Every Dependabot head's runs wait in one group, so a closed Dependabot pull request's runs
    # there go on, in their turn: a job that joined it on the close would cancel another
    # Dependabot head's run in flight, or replace the one waiting.
    shared: dict[str, str] = {}
    for name, workflow in _runs_on_pull_requests(tree):
        group = _concurrency_group(workflow.get("concurrency"))
        if group is None:
            continue
        try:
            keys = {
                _evaluate(group, _pr_context(**_DEPENDABOT_CLOSE, action="synchronize", **pr))
                for pr in _TWO_DEPENDABOT_PRS
            }
        except ValueError:
            continue  # reported above
        if len(keys) == 1:
            shared.setdefault(keys.pop(), set()).add(name)
    for merged in (False, True):
        close = _pr_context(**_DEPENDABOT_CLOSE, action="closed", merged=merged, number=45)
        for job_id, group in closing_groups(close, cancelling=False):
            if group in shared:
                bad.append(
                    f"{_CANCELLER}:{job_id} joins {group!r}, where every Dependabot head's runs"
                    f" wait ({', '.join(sorted(shared[group]))}), on a Dependabot pull request's"
                    " close, so it cancels another Dependabot head's run in flight or replaces the"
                    " one waiting"
                )

    # A job that only joins a group still replaces its pending run, so every join counts here.
    merged_close = _pr_context(head_repo=_CLOSE_REPO, action="closed", merged=True)
    joined = closing_groups(merged_close, cancelling=False)
    for name, workflow in _workflows(tree):
        concurrency = workflow.get("concurrency")
        if name == _CANCELLER or "push" not in _triggers(workflow):
            continue
        if not isinstance(concurrency, dict) or "group" not in concurrency:
            continue
        try:
            main_group = _evaluate(str(concurrency["group"]), _main_push_context())
        except ValueError as exc:
            bad.append(f"{name}: {exc}")
            continue
        for job_id, group in joined:
            if group == main_group:
                bad.append(
                    f"{_CANCELLER}:{job_id} joins {name}'s group on the merge's push to main "
                    f"({group!r}), so a merge would cancel main's check"
                )
    return list(dict.fromkeys(bad))


#: A Dependabot pull request's context, and two of them, by number and branch.
_DEPENDABOT_CLOSE: dict[str, Any] = {"head_repo": _CLOSE_REPO, "author": _DEPENDABOT_LOGIN}
_TWO_DEPENDABOT_PRS = (
    {"number": 45, "branch": _DEPENDABOT_A},
    {"number": 46, "branch": _DEPENDABOT_B},
)
_CANCEL_MERGE_GROUP = "      group: merge-${{ github.event.pull_request.number }}\n"
_CANCEL_NIX_GROUP = (
    "      group: nix-${{ github.event.pull_request.head.repo.full_name"
    " || github.repository }}-${{ github.head_ref }}\n"
)
_CANCEL_PINS_GROUP = (
    "      group: action-pins-refs/pull/${{ github.event.pull_request.number }}/merge\n"
)
_NEW_PR_WORKFLOW = (
    "name: A new check\non:\n  pull_request:\n"
    "concurrency:\n  group: new-check-${{ github.head_ref }}\n  cancel-in-progress: true\n"
    "permissions: {}\njobs: {}\n"
)


def _new_workflow(rel: str, text: str) -> Callable[[Path], None]:
    return lambda root: _write(root, rel, text)


CLOSED_PR_RUNS_ARE_CANCELLED = _toolchain_row(
    "every workflow that runs on pull_request is cancelled when the pull request closes",
    closed_pr_runs_violations,
    (
        Control(
            "the canceller removed",
            lambda root: (root / _CANCELLER_PATH).unlink(),
            expect="is missing",
        ),
        _plant(
            "a pull_request workflow no job cancels",
            _CANCELLER_PATH,
            "group: nix-",
            "group: nix-closed-",
            expect="nix.yml: no",
        ),
        Control(
            "a new pull_request workflow no job cancels",
            _new_workflow(f"{_WORKFLOWS}/new-check.yml", _NEW_PR_WORKFLOW),
            expect="new-check.yml: no",
        ),
        Control(
            "a new pull_request workflow with no concurrency group",
            _new_workflow(
                f"{_WORKFLOWS}/new-check.yml",
                "name: A new check\non:\n  pull_request:\npermissions: {}\njobs: {}\n",
            ),
            expect="no workflow-level concurrency group",
        ),
        _plant(
            "a key read from the ref, which a merged close evaluates to main's",
            _CANCELLER_PATH,
            _CANCEL_MERGE_GROUP,
            "      group: merge-${{ github.ref == 'refs/heads/main' && github.sha"
            " || github.event.pull_request.number }}\n",
            expect="once it is merged",
        ),
        _plant(
            "a cancel-in-progress read from the ref, which is false on a merged close",
            _CANCELLER_PATH,
            "      cancel-in-progress: true\n",
            "      cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}\n",
            expect="pr.yml: no",
        ),
        # A closed event is a pull_request event with its number, so the tier's own key and
        # cancel-in-progress read on it as on the pull request's runs.
        _plant(
            "the key merge.yml gives any but a Dependabot head's run, copied, passes",
            _CANCELLER_PATH,
            _CANCEL_MERGE_GROUP,
            "      group: merge-${{ github.event.pull_request.number || github.sha }}\n",
            fails=False,
        ),
        _plant(
            "merge.yml's whole key copied, which joins Dependabot's group on its close",
            _CANCELLER_PATH,
            _CANCEL_MERGE_GROUP,
            f"      group: {_dependabot_tier_concurrency('merge')['group']}\n",
            expect="joins 'dependabot-heads', where every Dependabot head's runs wait (merge.yml,"
            " pr.yml)",
        ),
        _plant(
            "a job that does not cancel in progress",
            _CANCELLER_PATH,
            "      cancel-in-progress: true\n",
            "      cancel-in-progress: false\n",
            expect="closed unmerged",
        ),
        _plant(
            "a group keyed on github.ref, which a merge moves to the base branch",
            _CANCELLER_PATH,
            _CANCEL_PINS_GROUP,
            "      group: action-pins-${{ github.ref }}\n",
            expect="action-pins.yml: no",
        ),
        _plant(
            "a group without the head repository, so a fork's run is missed",
            _CANCELLER_PATH,
            _CANCEL_NIX_GROUP,
            "      group: nix-${{ github.repository }}-${{ github.head_ref }}\n",
            expect="from a fork",
        ),
        _plant(
            "a group that joins the merge's push to main",
            _CANCELLER_PATH,
            _CANCEL_PINS_GROUP,
            "      group: merge-${{ github.sha }}\n",
            expect="cancel main's check",
        ),
        _plant(
            "an expression the row cannot evaluate",
            _CANCELLER_PATH,
            _CANCEL_MERGE_GROUP,
            "      group: merge-${{ format('{0}', github.head_ref) }}\n",
            expect="cannot evaluate",
        ),
        _plant(
            "pull_request_target instead",
            _CANCELLER_PATH,
            "  pull_request:\n    types: [closed]\n",
            "  pull_request_target:\n    types: [closed]\n",
            expect="not on `pull_request` `closed` alone",
        ),
        _plant(
            "a run on an opening event too",
            _CANCELLER_PATH,
            "    types: [closed]\n",
            "    types: [closed, opened]\n",
            expect="not on `pull_request` `closed` alone",
        ),
        _plant(
            "a write at the top",
            _CANCELLER_PATH,
            "\npermissions: {}\n\njobs:\n",
            "\npermissions:\n  actions: write\n\njobs:\n",
            expect="not `permissions: {}`",
        ),
        _plant(
            "a write on a job",
            _CANCELLER_PATH,
            "    permissions: {}\n",
            "    permissions:\n      actions: write\n",
            expect="not `permissions: {}`",
        ),
        _plant(
            "a checkout of the pull request's head",
            _CANCELLER_PATH,
            "    steps:\n",
            "    steps:\n"
            "      - uses: actions/checkout@0000000000000000000000000000000000000000 # v6.0.0\n"
            "        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n",
            expect="runs actions/checkout",
        ),
        _plant(
            "a step that runs a command",
            _CANCELLER_PATH,
            "    steps:\n",
            "    steps:\n      - name: Cancel by hand\n        run: gh run list\n",
            expect="runs nothing but its identity",
        ),
        Control(
            "a workflow that runs on pull_request only when closed is not one to cancel",
            _new_workflow(
                f"{_WORKFLOWS}/on-close.yml",
                "name: On close\non:\n  pull_request:\n    types: [closed]\n"
                "permissions: {}\njobs: {}\n",
            ),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, CLOSED_PR_RUNS_ARE_CANCELLED)


# ── Nothing throws an exit status away unseen (#3918) ─────────────────────────────────────────
# The checker and its reasoned list live in tests/ci_masking.py: every `|| true`, `|| :` and
# `continue-on-error` in a workflow, a Dockerfile or a script under scripts/ is named there with
# its reason, and an entry that matches nothing fails, so the list cannot rot.

NOTHING_IS_MASKED_UNSEEN = Row(
    name="nothing-throws-an-exit-status-away-unseen",
    check=ci_masking.violations,
    scaffold=ci_masking.scaffold,
    controls=(
        *(_plant(name, rel, old, new) for name, rel, old, new in ci_masking.PLANTS),
        Control("every masked site in the tree is named, with its reason", _nothing, fails=False),
    ),
)

ROWS = (*ROWS, NOTHING_IS_MASKED_UNSEEN)


# ── The randomized-order leg runs two seeded orders and keeps them (#3949) ─────────────────────
# Each order is its own step, seeded from the run's number so a rerun repeats it; the second runs
# whatever the first concluded; the entry keeps the seeds beside its inputs; and the shard uploads
# its orders, leak reports and file list whatever it concluded. How the two outcomes make the
# shard's is the masking row's (tests/ci_masking.py).

_RANDOM_ORDER_UPLOAD = (
    "      - name: Upload the orders and snapshots (random-order · shard ${{ matrix.shard }})\n"
    "        if: always()\n"
    "        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2\n"
    "        with:\n"
    "          name: random-order-${{ matrix.shard }}\n"
    "          path: reports/random-order/\n"
    "          if-no-files-found: ignore\n"
    "          retention-days: 14\n"
)
_SECOND_ORDER = "      - name: pytest (random-order shard ${{ matrix.shard }}, second order)\n"
_RANDOM_ORDER_SEEDED = "breadth.yml:random-order seeds its orders"
_RANDOM_ORDER_BASETEMP = "breadth.yml:random-order runs its orders in basetemps"

RANDOM_ORDER_RUNS_TWO_SEEDED_ORDERS = _toolchain_row(
    "breadth-random-order-runs-two-seeded-orders-and-keeps-them",
    random_order_violations,
    (
        _plant(
            "the first order seeded from a constant",
            _BREADTH,
            "--random-order-seed ${{ github.run_number }}1",
            "--random-order-seed 7771",
            expect=_RANDOM_ORDER_SEEDED,
        ),
        _plant(
            "the first order seeded from the run's attempt",
            _BREADTH,
            "--random-order-seed ${{ github.run_number }}1",
            "--random-order-seed ${{ github.run_attempt }}1",
            expect=_RANDOM_ORDER_SEEDED,
        ),
        _plant(
            "the first order unseeded, in collection order",
            _BREADTH,
            " --random-order-seed ${{ github.run_number }}1",
            "",
            expect=_RANDOM_ORDER_SEEDED,
        ),
        _plant(
            "both orders on one seed",
            _BREADTH,
            "--random-order-seed ${{ github.run_number }}2",
            "--random-order-seed ${{ github.run_number }}1",
            expect=_RANDOM_ORDER_SEEDED,
        ),
        _plant(
            "an order seeded twice",
            _BREADTH,
            "--random-order-seed ${{ github.run_number }}2",
            "--random-order-seed ${{ github.run_number }}2 --random-order-seed 5",
            expect=_RANDOM_ORDER_SEEDED,
        ),
        _plant(
            "the first order in pytest's shared basetemp",
            _BREADTH,
            ' --basetemp "/var/tmp/pytest-random-order-${{ github.run_number }}1"',
            "",
            expect=_RANDOM_ORDER_BASETEMP,
        ),
        _plant(
            "both orders in one basetemp",
            _BREADTH,
            '--basetemp "/var/tmp/pytest-random-order-${{ github.run_number }}2"',
            '--basetemp "/var/tmp/pytest-random-order-${{ github.run_number }}1"',
            expect=_RANDOM_ORDER_BASETEMP,
        ),
        _plant(
            "the entry keeping other seeds than the orders'",
            _BREADTH,
            '--seeds "${{ github.run_number }}1,${{ github.run_number }}2"',
            '--seeds "${{ github.run_number }}2,${{ github.run_number }}1"',
            expect="breadth.yml:random-order's entry keeps the seeds",
        ),
        _plant(
            "the entry keeping no seeds",
            _BREADTH,
            ' --seeds "${{ github.run_number }}1,${{ github.run_number }}2"',
            "",
            expect="breadth.yml:random-order's entry keeps the seeds",
        ),
        _plant_in_job(
            "the seeds back among the inputs",
            _BREADTH,
            "random-order",
            '            --external "shard_files=${SHARD_FILES:?no shard list}"\n',
            '            --external "shard_files=${SHARD_FILES:?no shard list}" \\\n'
            '            --external "seeds=${{ github.run_number }}1,${{ github.run_number }}2"\n',
            expect="breadth.yml:random-order records its seeds among its inputs",
        ),
        _plant(
            "the second order run only after a green first",
            _BREADTH,
            _SECOND_ORDER,
            _SECOND_ORDER + "        if: steps.pytest-1.outcome == 'success'\n",
            expect="breadth.yml:random-order runs its order pytest-2 under `if:",
        ),
        _plant(
            "no upload of the orders",
            _BREADTH,
            _RANDOM_ORDER_UPLOAD,
            "",
            expect="breadth.yml:random-order uploads no one random-order-",
        ),
        _plant(
            "the upload narrowed to the order files",
            _BREADTH,
            "          path: reports/random-order/\n",
            "          path: reports/random-order/order-*.jsonl\n",
            expect="breadth.yml:random-order uploads ['reports/random-order/order-*.jsonl']",
        ),
        _plant(
            "the upload only when the shard is green",
            _BREADTH,
            _RANDOM_ORDER_UPLOAD,
            _RANDOM_ORDER_UPLOAD.replace("        if: always()\n", ""),
            expect="breadth.yml:random-order uploads its orders under `if: None`",
        ),
        _plants(
            "the upload before the second order ran",
            _BREADTH,
            ((_RANDOM_ORDER_UPLOAD, ""), (_SECOND_ORDER, _RANDOM_ORDER_UPLOAD + _SECOND_ORDER)),
            expect="breadth.yml:random-order uploads its orders before",
        ),
        _plant(
            "no random-order job",
            _BREADTH,
            "\n  random-order:\n",
            "\n  random-order-renamed:\n",
            expect="breadth.yml has no random-order job",
        ),
        Control("the leg as the tree has it passes", _nothing, fails=False),
    ),
)

ROWS = (*ROWS, RANDOM_ORDER_RUNS_TWO_SEEDED_ORDERS)


# ── Every rail lives in tests/rails ─────────────────────────────────────────────────────────
#
# A test that checks the source tree runs once per CI run, in the rails job, and never in a
# suite shard (tools/ci/shards.py leaves tests/rails out of every plan). tools/ci/rails_census.py
# decides which modules are rails from their source. A security-marked module stays where it is;
# a module with tree tests and behaviour tests is split, its tree half under tests/rails.

_A_RAIL = "import source_index\n\n\ndef test_it():\n    assert source_index.package_files()\n"
_NOT_A_RAIL = "def test_it():\n    assert 1 + 1 == 2\n"
_MIXED = (
    "import urllib.request\n\nimport source_index\n\n\ndef test_tree():\n"
    "    assert source_index.package_files()\n\n\ndef test_wire():\n"
    "    urllib.request.urlopen('https://example.com')\n"
)


def _rails_scaffold(root: Path) -> None:
    """A tree where the rule holds: a rail in tests/rails, a suite module beside it, and a
    security-marked rail left in place."""
    _write(root, "tests/rails/test_a_rail.py", _A_RAIL)
    _write(root, "tests/test_behaviour.py", _NOT_A_RAIL)
    _write(
        root,
        "tests/security/test_guarded.py",
        "import pytest\n\npytestmark = pytest.mark.security\n\n" + _A_RAIL,
    )


EVERY_RAIL_LIVES_IN_TESTS_RAILS = Row(
    name="every rail lives in tests/rails",
    check=rails_census.violations,
    scaffold=_rails_scaffold,
    controls=(
        Control(
            "a rail outside tests/rails fails",
            lambda root: _write(root, "tests/test_new_rail.py", _A_RAIL),
        ),
        Control(
            "a mixed module fails: it is split",
            lambda root: _write(root, "tests/test_new_mixed.py", _MIXED),
        ),
        Control(
            "a suite module under tests/rails fails",
            lambda root: _write(root, "tests/rails/test_behaviour_here.py", _NOT_A_RAIL),
        ),
        Control(
            "a rail named the other way pytest collects (*_test.py) outside tests/rails fails",
            lambda root: _write(root, "tests/new_rail_test.py", _A_RAIL),
        ),
        Control(
            "a security-marked rail outside tests/rails passes",
            lambda root: _write(
                root,
                "tests/test_also_guarded.py",
                "import pytest\n\npytestmark = pytest.mark.security\n\n" + _A_RAIL,
            ),
            fails=False,
        ),
        Control(
            "a second rail in tests/rails passes",
            lambda root: _write(root, "tests/rails/test_another.py", _A_RAIL),
            fails=False,
        ),
        Control(
            "a module that walks only tmp_path passes outside tests/rails",
            lambda root: _write(
                root,
                "tests/test_tmp_walk.py",
                "def test_it(tmp_path):\n    assert not list(tmp_path.rglob('*'))\n",
            ),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, EVERY_RAIL_LIVES_IN_TESTS_RAILS)


# ── Each leg's list of the rails runs once, on its own leg ──────────────────────────────────
#
# ci.yml's rails job is a matrix with one leg per list in rails_census.LEGS: leg i runs pytest
# over tests/rails with `--shard-list` naming LEGS[i-1]. `rails check` holds the lists to every
# rail, each once; this row holds the legs to the lists, so no list runs on two legs and none on
# no leg. The legs are the catalogue's rails entry's shards: the tier workflows run each shard the
# plan gives as a matrix line of its own, fail-fast off, so a red leg never cancels the others.


def _shard_lists(args: list[str]) -> list[str]:
    """Each ``--shard-list`` a pytest command's arguments pass, in either spelling."""
    found: list[str] = []
    for n, word in enumerate(args):
        if word == "--shard-list":
            found.append(args[n + 1] if n + 1 < len(args) else "")
        elif word.startswith("--shard-list="):
            found.append(word.partition("=")[2])
    return found


def _runs_rails(words: list[str]) -> bool:
    """A root pytest that collects from tests/rails: the folder, or a file or folder in it."""
    rails = rails_census.RAILS
    return _root_pytest(words) and any(
        w.rstrip("/") == rails or w.startswith(rails + "/") for w in _pytest_args(words)
    )


def rails_legs_violations(root: Path) -> list[str]:
    """Each list runs once, on its own leg: the catalogue's rails entry has one shard per list,
    and shard i runs pytest over tests/rails once, over list i. The tier workflows run each shard
    of the entry the plan gives as a leg of its own (one matrix line per shard, fail-fast off,
    which the tier rows hold), so the entry is the legs."""
    named = rails_census.LEGS
    try:
        entry = catalogue.read(root).job(catalogue.MERGE_NEEDS_JOB)
    except (OSError, KeyError, catalogue.CatalogueError) as exc:
        return [f"{catalogue.CATALOGUE}'s rails entry cannot be read: {exc}"]
    bad: list[str] = []
    if entry.shards != len(named):
        bad.append(
            f"rails: shards is {entry.shards}, and rails_census.LEGS names {len(named)} lists"
        )
    for shard in range(1, entry.shards + 1):
        runs = [step for step in entry.command if _runs_rails(list(step.argv))]
        if len(runs) != 1:
            bad.append(f"rails: shard {shard} runs pytest over tests/rails {len(runs)} times")
            continue
        lists = [
            listed.replace("{shard}", str(shard))
            for listed in _shard_lists(_pytest_args(list(runs[0].argv)))
        ]
        if shard <= len(named) and lists != [named[shard - 1]]:
            bad.append(
                f"rails: shard {shard} of the catalogue's entry runs {lists or 'every rail'},"
                f" and its list is {named[shard - 1]}"
            )
    return bad


def _rails_legs_scaffold(root: Path) -> None:
    _write(root, catalogue.CATALOGUE, (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8"))


_RAILS_ENTRY_LIST = '"--shard-list", "tools/ci/rails-leg-{shard}.txt"'
_RAILS_ENTRY_HEAD = (
    '  { argv = ["uv", "run", "pytest", "-n", "4", "--dist", "worksteal", "-q", "tests/rails",'
)

EACH_RAILS_LIST_RUNS_ONCE_ON_ITS_OWN_LEG = Row(
    name="each list of the rails runs once, on its own leg",
    check=rails_legs_violations,
    scaffold=_rails_legs_scaffold,
    controls=(
        _plant(
            "the catalogue's shards reading list 1 fail",
            catalogue.CATALOGUE,
            _RAILS_ENTRY_LIST,
            '"--shard-list", "tools/ci/rails-leg-1.txt"',
            expect="rails: shard 2 of the catalogue's entry runs ['tools/ci/rails-leg-1.txt']",
        ),
        _plant(
            "a shard that reads the old half's list fails",
            catalogue.CATALOGUE,
            _RAILS_ENTRY_LIST,
            '"--shard-list", "tools/ci/rails-half-{shard}.txt"',
            expect="rails: shard 2 of the catalogue's entry runs ['tools/ci/rails-half-2.txt']",
        ),
        _plant(
            "a shard that runs every rail fails",
            catalogue.CATALOGUE,
            _RAILS_ENTRY_LIST + ", ",
            "",
            expect="rails: shard 1 of the catalogue's entry runs every rail",
        ),
        _plant(
            "a second run of tests/rails on each shard fails",
            catalogue.CATALOGUE,
            _RAILS_ENTRY_HEAD,
            '  { argv = ["uv", "run", "pytest", "-q", "tests/rails/test_ci_contract.py"] },\n'
            + _RAILS_ENTRY_HEAD,
            expect="rails: shard 1 runs pytest over tests/rails 2 times",
        ),
        _plant(
            "the catalogue's rails in two shards fail",
            catalogue.CATALOGUE,
            "workers = 4\nshards = 3\n",
            "workers = 4\nshards = 2\n",
            expect="rails: shards is 2, and rails_census.LEGS names 3 lists",
        ),
        _plant(
            "a list passed as --shard-list=FILE passes",
            catalogue.CATALOGUE,
            _RAILS_ENTRY_LIST,
            '"--shard-list=tools/ci/rails-leg-{shard}.txt"',
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, EACH_RAILS_LIST_RUNS_ONCE_ON_ITS_OWN_LEG)

# ── Every rail is in exactly one leg ────────────────────────────────────────────────────────
#
# The legs row holds each leg to its list; this row holds the lists to the rails. Together the
# lists name every module pytest collects under tests/rails once, so moving files between the
# legs to rebalance them can neither drop a rail nor run one twice. `rails check` runs the same
# function in `rails-checks`; the row adds the planted proof that each clause bites.

_LEG_1, _LEG_2, _LEG_3 = rails_census.LEGS
_SCAFFOLD_RAILS = tuple(
    f"{rails_census.RAILS}/{name}" for name in ("test_a.py", "test_b.py", "c_test.py", "test_d.py")
)
#: A helper module beside the rails, which pytest does not collect, so it is in no leg.
_SCAFFOLD_HELPER = f"{rails_census.RAILS}/rails_helper.py"


def _rails_lists_scaffold(root: Path) -> None:
    for rel in _SCAFFOLD_RAILS:
        _write(root, rel, "def test_it():\n    pass\n")
    _write(root, _SCAFFOLD_HELPER, "VALUE = 1\n")
    _write(root, "tests/test_suite.py", "def test_it():\n    pass\n")
    # Leg 2 holds two rails, so one can move out of it and no leg is left empty.
    _write(root, _LEG_1, f"{_SCAFFOLD_RAILS[0]}\n")
    _write(root, _LEG_2, f"{_SCAFFOLD_RAILS[1]}\n{_SCAFFOLD_RAILS[3]}\n")
    _write(root, _LEG_3, f"{_SCAFFOLD_RAILS[2]}\n")


EVERY_RAIL_IS_IN_EXACTLY_ONE_LEG = Row(
    name="every rail is in exactly one leg of the rails job",
    check=rails_census.leg_violations,
    scaffold=_rails_lists_scaffold,
    controls=(
        Control(
            "a new rail in no leg fails",
            lambda root: _write(root, f"{rails_census.RAILS}/test_new.py", "def test_it(): ...\n"),
            expect="tests/rails/test_new.py is in no leg",
        ),
        _plant(
            "a rail dropped from its leg fails",
            _LEG_2,
            f"{_SCAFFOLD_RAILS[3]}\n",
            "",
            expect="tests/rails/test_d.py is in no leg",
        ),
        Control(
            "a rail listed in two legs fails",
            _append(_LEG_3, f"{_SCAFFOLD_RAILS[0]}\n"),
            expect=f"tests/rails/test_a.py is listed 2 times ({_LEG_1}, {_LEG_3})",
        ),
        Control(
            "a rail listed twice in one leg fails",
            _append(_LEG_2, f"{_SCAFFOLD_RAILS[1]}\n"),
            expect=f"tests/rails/test_b.py is listed 2 times ({_LEG_2}, {_LEG_2})",
        ),
        Control(
            "a listed rail that is gone fails",
            _remove(_SCAFFOLD_RAILS[2]),
            expect=f"tests/rails/c_test.py is listed in {_LEG_3} and is no test module",
        ),
        Control(
            "a suite module listed in a leg fails",
            _append(_LEG_2, "tests/test_suite.py\n"),
            expect=f"tests/test_suite.py is listed in {_LEG_2} and is no test module",
        ),
        Control(
            "a helper module beside the rails listed in a leg fails",
            _append(_LEG_1, f"{_SCAFFOLD_HELPER}\n"),
            expect=f"{_SCAFFOLD_HELPER} is listed in {_LEG_1} and is no test module",
        ),
        Control(
            "a leg emptied into another fails",
            lambda root: (
                _write(root, _LEG_3, ""),
                _append(_LEG_2, f"{_SCAFFOLD_RAILS[2]}\n")(root),
            ),
            expect=f"{_LEG_3} names no file",
        ),
        Control(
            "a missing leg fails",
            _remove(_LEG_1),
            expect=f"{_LEG_1} cannot be read",
        ),
        Control(
            "the two halves' lists in place of the legs fail: the census reads only the legs",
            lambda root: (
                [(root / leg).unlink() for leg in rails_census.LEGS],
                _write(root, "tools/ci/rails-half-1.txt", f"{_SCAFFOLD_RAILS[0]}\n"),
                _write(
                    root,
                    "tools/ci/rails-half-2.txt",
                    "".join(f"{rel}\n" for rel in _SCAFFOLD_RAILS[1:]),
                ),
            ),
            expect="tests/rails/test_a.py is in no leg",
        ),
        Control(
            "a rail moved from one leg to another passes",
            lambda root: (
                _write(root, _LEG_2, f"{_SCAFFOLD_RAILS[1]}\n"),
                _append(_LEG_3, f"{_SCAFFOLD_RAILS[3]}\n")(root),
            ),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, EVERY_RAIL_IS_IN_EXACTLY_ONE_LEG)


# ── Each rails leg compares the site baselines with the gate's base (#3930) ─────────────────
# A site baseline (tests/site_baseline.py) read against the gate's base commit admits a line the
# base lacks only with `added` and a reason, and writes each such line to the job summary. With no
# base it compares the file with the working tree alone, which passes: a rails job that lost its
# base would let a branch grow its own list unseen. So the catalogue's rails entry gives the step
# that runs tests/rails `BASE_SHA = "{base}"`, which run-job renders as the gate's base: a pull
# request's base, or on a push to main the commit it replaced (tools/ci/jobs.py, held by
# tests/test_ci_jobs.py), the base apps-contract's `--base-ref {base}` reads too.


def rails_base_violations(root: Path) -> list[str]:
    """The catalogue's rails entry sets ``BASE_SHA`` to ``{base}`` on each step that runs
    tests/rails, and has one."""
    variable = site_baseline.BASE_VARIABLE
    try:
        entry = catalogue.read(root).job(catalogue.MERGE_NEEDS_JOB)
    except (OSError, KeyError, catalogue.CatalogueError) as exc:
        return [f"{catalogue.CATALOGUE}'s rails entry cannot be read: {exc}"]
    bad: list[str] = []
    rails = [(n, s) for n, s in enumerate(entry.command, 1) if _runs_rails(list(s.argv))]
    if not rails:
        bad.append("rails: no command step runs tests/rails")
    for n, step in rails:
        if step.env.get(variable) != "{base}":
            bad.append(
                f"rails: command step {n} runs tests/rails with {variable}="
                f"{step.env.get(variable)!r}, not {{base}}"
            )
    return bad


def _edits(*edits: tuple[str, str, str]) -> Callable[[Path], None]:
    """One edit in each of several files, ``(rel, old, new)``, each of which must find its
    text."""

    def plant(root: Path) -> None:
        for rel, old, new in edits:
            text = (root / rel).read_text(encoding="utf-8")
            assert old in text, f"the plant found no {old!r} in {rel}"
            _write(root, rel, text.replace(old, new, 1))

    return plant


_RAILS_JUNIT_TOML = '"--junitxml=reports/junit-rails-{shard}.xml"]'
_RAILS_BASE_TOML = _RAILS_JUNIT_TOML + ', env = { BASE_SHA = "{base}" } },'


EACH_RAILS_LEG_COMPARES_WITH_THE_GATES_BASE = Row(
    name="each rails leg compares the site baselines with the gate's base",
    check=rails_base_violations,
    scaffold=_rails_legs_scaffold,
    controls=(
        _plant(
            "a catalogue rails step whose BASE_SHA is not {base} fails",
            catalogue.CATALOGUE,
            _RAILS_BASE_TOML,
            _RAILS_BASE_TOML.replace('"{base}"', '"main"'),
            expect="runs tests/rails with BASE_SHA='main', not {base}",
        ),
        _plant(
            "a catalogue rails step with no BASE_SHA fails",
            catalogue.CATALOGUE,
            _RAILS_BASE_TOML,
            _RAILS_JUNIT_TOML + " },",
            expect="rails: command step 1 runs tests/rails with BASE_SHA=None, not {base}",
        ),
        _plant(
            "a rails entry with no step that runs tests/rails fails",
            catalogue.CATALOGUE,
            '"-q", "tests/rails", "--shard-list"',
            '"-q", "tests/railz", "--shard-list"',
            expect="rails: no command step runs tests/rails",
        ),
    ),
)

ROWS = (*ROWS, EACH_RAILS_LEG_COMPARES_WITH_THE_GATES_BASE)


# ── Every push to main checks the commits it lands, with main's previous check (#3908) ───────
# Each push checks only `before..sha`, so a run that never finishes leaves its commits unchecked
# for good. The check therefore runs in a workflow of its own that no later run can cancel or
# replace, and the copy that runs is the previous main commit's, so a push cannot judge itself.

_PROVENANCE = ".github/workflows/provenance.yml"
#: The one command the job runs: the fetched check, isolated, over the push's own range.
_PROVENANCE_RUN = (
    'python3 -I "$check" --maintainers "$maintainers" '
    '--mode main --range "${BEFORE}..${GITHUB_SHA}"'
)


def provenance_violations(tree: Tree) -> list[str]:
    """``provenance.yml`` runs on every push to main and nothing else, one group per commit and
    never cancelled, read-only, and runs the previous main commit's check and MAINTAINERS,
    isolated. No other workflow runs the check over a main push."""
    workflow = _workflow(tree, "provenance.yml")
    if workflow is None:
        return [f"{_PROVENANCE} is missing"]
    bad: list[str] = []
    if _triggers(workflow) != {"push": {"branches": ["main"]}}:
        bad.append(f"{_PROVENANCE} runs on {_triggers(workflow)}, not on every push to main alone")
    concurrency = workflow.get("concurrency") or {}
    if concurrency.get("group") != "provenance-${{ github.sha }}":
        bad.append(f"{_PROVENANCE}'s group is not its commit's own: {concurrency.get('group')!r}")
    if concurrency.get("cancel-in-progress") is not False:
        bad.append(f"{_PROVENANCE} can cancel a run")
    if workflow.get("permissions") != {"contents": "read"}:
        bad.append(f"{_PROVENANCE} grants {workflow.get('permissions')!r}, not contents: read")
    for job_id, job in (workflow.get("jobs") or {}).items():
        where = f"provenance.yml:{job_id}"
        if job.get("permissions") != {"contents": "read"}:
            bad.append(f"{where} holds {job.get('permissions')!r}, not contents: read")
        if "concurrency" in job or "if" in job or "needs" in job:
            bad.append(f"{where} can be skipped, replaced or cancelled on its own")
        run = _runs(job)
        if _PROVENANCE_RUN not in run:
            bad.append(f"{where} does not run the fetched check over `before..sha`")
        for fetched in ("scripts/check_commit_provenance.py", "MAINTAINERS"):
            if f'git show "$BEFORE:{fetched}"' not in run:
                bad.append(f"{where} does not read {fetched} from the previous main commit")
        if re.search(r"python3?\s+(?:-\S+\s+)*(?:\./)?scripts/check_commit_provenance\.py", run):
            bad.append(f"{where} runs the pushed copy of the check by path")
    for name, other in _workflows(tree):
        if (
            name != "provenance.yml"
            and "--mode main" in json.dumps(other)
            and ("check_commit_provenance" in json.dumps(other))
        ):
            bad.append(f"{name} runs the main-push check, which only provenance.yml may")
    return bad


PROVENANCE_RUNS_ON_EVERY_MAIN_PUSH = _toolchain_row(
    "provenance-checks-every-main-push-with-mains-previous-check",
    provenance_violations,
    (
        _plant(
            "a group shared across commits",
            _PROVENANCE,
            "group: provenance-${{ github.sha }}",
            "group: provenance-${{ github.ref }}",
        ),
        _plant(
            "a run that can be cancelled",
            _PROVENANCE,
            "cancel-in-progress: false",
            "cancel-in-progress: true",
        ),
        _plant(
            "a branch filter that admits more than main",
            _PROVENANCE,
            "    branches: [main]\n",
            '    branches: ["**"]\n',
        ),
        _plant(
            "the pushed copy of the check run by path",
            _PROVENANCE,
            '          python3 -I "$check" --maintainers "$maintainers" --mode main',
            "          python3 scripts/check_commit_provenance.py --maintainers MAINTAINERS"
            " --mode main",
        ),
        _plant(
            "the pushed MAINTAINERS read",
            _PROVENANCE,
            'git show "$BEFORE:MAINTAINERS" > "$maintainers"',
            'git show "$GITHUB_SHA:MAINTAINERS" > "$maintainers"',
        ),
        _plant(
            "a write permission",
            _PROVENANCE,
            "permissions:\n  contents: read\n",
            "permissions:\n  contents: write\n",
        ),
        _plant(
            "the main-push check in merge.yml too",
            _MERGE_YML,
            "\n  results:\n",
            "\n  provenance-again:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: python3 "
            "scripts/check_commit_provenance.py --mode main --range x..y\n\n  results:\n",
            expect="merge.yml runs the main-push check",
        ),
        Control("the tree's provenance.yml passes", _nothing, fails=False),
    ),
)

ROWS = (*ROWS, PROVENANCE_RUNS_ON_EVERY_MAIN_PUSH)


# ── dco.yml checks a pull request with the base branch's check, and nothing of the candidate ──
# It runs on `pull_request_target` and holds `pull-requests: write`, so each property here is a
# trust property: what runs is the base branch's, the token reaches only the step that calls the
# API, nothing a contributor wrote is pasted into code or carried between steps, and the check is
# handed what it needs to refuse a short or stale list.

_DCO = ".github/workflows/dco.yml"
_DCO_TYPES = ["opened", "edited", "synchronize", "reopened"]
_DCO_SPARSE = {"scripts/check_commit_provenance.py", "MAINTAINERS"}
_DCO_FETCH = "Fetch the pull request's commits"
_DCO_CHECK = "Verify DCO sign-off on every commit"
_DCO_COMMENT = "Explain how to fix DCO"
_DCO_FENCE = "const bar = '`'.repeat(Math.max(2, ...runs.map((r) => r.length)) + 1);"
#: The Runner identity step's comment line, the anchor its token plant edits.
_IDENTITY_COMMENT = (
    "        # The image this job ran on, so a runner image that moves is visible in the log."
)


def dco_violations(tree: Tree) -> list[str]:
    """``dco.yml``'s trust shape, one finding per property."""
    from tools.ci.supply_chain import hands_token

    workflow = _workflow(tree, "dco.yml")
    if workflow is None:
        return [f"{_DCO} is missing"]
    bad: list[str] = []
    triggers = _triggers(workflow)
    if set(triggers) != {"pull_request_target"}:
        bad.append(f"{_DCO} runs on {sorted(triggers)}, not pull_request_target alone")
    types = (triggers.get("pull_request_target") or {}).get("types")  # type: ignore[union-attr]
    if types != _DCO_TYPES:
        bad.append(f"{_DCO} runs on {types}, not {_DCO_TYPES} (an edited title is rule 5's)")
    steps = ((workflow.get("jobs") or {}).get("dco") or {}).get("steps") or []
    by_name = {str(s.get("name", "")): s for s in steps}
    checkouts = [s for s in steps if _action(s) == "actions/checkout"]
    if len(checkouts) != 1:
        bad.append(f"{_DCO} has {len(checkouts)} checkouts, not the base branch's one")
    for checkout in checkouts:
        inputs = checkout.get("with") or {}
        if {"ref", "repository"} & set(inputs):
            bad.append(f"{_DCO}'s checkout names a ref or repository, not the base branch")
        if inputs.get("persist-credentials") is not False:
            bad.append(f"{_DCO}'s checkout keeps its credentials")
        if set(str(inputs.get("sparse-checkout", "")).split()) != _DCO_SPARSE:
            bad.append(f"{_DCO}'s checkout is not the check and MAINTAINERS alone")
    for step in steps:
        where = f"{_DCO}:{step.get('name') or _action(step)}"
        # Through `env:` or `with:` (a checkout's `token:`, a script's `github-token:`) alike.
        handed = hands_token(step.get("env")) or hands_token(step.get("with"))
        if handed and step.get("name") != _DCO_FETCH:
            bad.append(f"{where} hands the job token to a step that does not fetch")
        code = [str(step.get("run", "")), str((step.get("with") or {}).get("script", ""))]
        if any("${{" in text for text in code):
            bad.append(f"{where} pastes an expression into code")
        if any("contents/" in text for text in code):
            bad.append(f"{where} reads a file by path through the contents API")
        if "GITHUB_OUTPUT" in json.dumps(step):
            bad.append(f"{where} writes a step output")
    fetch, check, comment = (by_name.get(n) for n in (_DCO_FETCH, _DCO_CHECK, _DCO_COMMENT))
    if fetch is None or not hands_token(fetch.get("env")):
        bad.append(f"{_DCO} has no fetch step holding the token")
    elif [w for words in _commands(str(fetch.get("run", ""))) for w in words[:1]] != [
        "set",
        "gh",
    ]:
        bad.append(f"{_DCO}'s fetch step runs more than `gh api`")
    if check is None:
        bad.append(f"{_DCO} has no check step")
    else:
        run = str(check.get("run", ""))
        if not run.lstrip().startswith("set -euo pipefail"):
            bad.append(f"{_DCO}'s check step does not start with `set -euo pipefail`")
        if "python3 -I scripts/check_commit_provenance.py --mode pr" not in run:
            bad.append(f"{_DCO}'s check step does not run the base copy, isolated, in pr mode")
        if (
            '--expected-count "$COMMITS"' not in run
            or (check.get("env") or {}).get("COMMITS") != "${{ github.event.pull_request.commits }}"
        ):
            bad.append(f"{_DCO}'s check is not handed the pull request's own commit count")
        if (
            '--expected-head "$HEAD_SHA"' not in run
            or (check.get("env") or {}).get("HEAD_SHA")
            != "${{ github.event.pull_request.head.sha }}"
        ):
            bad.append(f"{_DCO}'s check is not handed the head the event names")
        if '--event "$GITHUB_EVENT_PATH"' not in run:
            bad.append(f"{_DCO}'s check does not read the title from the event file")
    if comment is None:
        bad.append(f"{_DCO} has no comment step")
    else:
        script = str((comment.get("with") or {}).get("script", ""))
        if "fs.readFileSync(process.env.REPORT, 'utf8')" not in script:
            bad.append(f"{_DCO}'s comment does not read the report from its file")
        if _DCO_FENCE not in script:
            bad.append(f"{_DCO}'s comment fence does not grow with the report")
    return bad


DCO_CHECKS_WITH_THE_BASE_BRANCHS_CHECK = _toolchain_row(
    "dco-checks-a-pull-request-with-the-base-branchs-check",
    dco_violations,
    (
        _plant(
            "a title edit that starts no run",
            _DCO,
            "types: [opened, edited, synchronize, reopened]",
            "types: [opened, synchronize, reopened]",
        ),
        _plant(
            "the checkout of the pull request's head",
            _DCO,
            "          sparse-checkout-cone-mode: false\n",
            "          sparse-checkout-cone-mode: false\n"
            "          ref: ${{ github.event.pull_request.head.sha }}\n",
        ),
        _plant(
            "a checkout that keeps its credentials",
            _DCO,
            "          persist-credentials: false\n",
            "          persist-credentials: true\n",
        ),
        _plant(
            "a checkout of more than the check and MAINTAINERS",
            _DCO,
            "            MAINTAINERS\n",
            "            MAINTAINERS\n            scripts/\n",
        ),
        _plant(
            "the token on the check step",
            _DCO,
            "          COMMITS: ${{ github.event.pull_request.commits }}\n",
            "          COMMITS: ${{ github.event.pull_request.commits }}\n"
            "          GH_TOKEN: ${{ github.token }}\n",
        ),
        _plant(
            "the token on the Runner identity step",
            _DCO,
            _IDENTITY_COMMENT + "\n        run: echo",
            "        env:\n          GH_TOKEN: ${{ github.token }}\n        run: echo",
        ),
        _plant(
            "the token on the comment step",
            _DCO,
            "          REPORT: ${{ runner.temp }}/provenance.txt\n",
            "          REPORT: ${{ runner.temp }}/provenance.txt\n"
            "          GH_TOKEN: ${{ github.token }}\n",
        ),
        _plant(
            "the check run in the fetch step",
            _DCO,
            'gh api --paginate "repos/$REPO/pulls/$PR/commits" > "$RUNNER_TEMP/commits.json"\n',
            'gh api --paginate "repos/$REPO/pulls/$PR/commits" > "$RUNNER_TEMP/commits.json"\n'
            "          python3 -I scripts/check_commit_provenance.py --help\n",
        ),
        _plant(
            "an expression pasted into the check's run",
            _DCO,
            '--expected-count "$COMMITS"',
            "--expected-count ${{ github.event.pull_request.commits }}",
        ),
        _plant(
            "a step output carrying text",
            _DCO,
            '            --report "$RUNNER_TEMP/provenance.txt"\n',
            '            --report "$RUNNER_TEMP/provenance.txt"\n'
            '          echo "report=x" >> "$GITHUB_OUTPUT"\n',
        ),
        _plant(
            "a check without pipefail",
            _DCO,
            "          set -euo pipefail\n          python3 -I",
            "          python3 -I",
        ),
        _plant(
            "the pushed copy run without isolation",
            _DCO,
            "python3 -I scripts/check_commit_provenance.py --mode pr",
            "python3 scripts/check_commit_provenance.py --mode pr",
        ),
        _plant(
            "the commit count not handed over",
            _DCO,
            '--expected-count "$COMMITS"',
            "--expected-count 250",
        ),
        _plant(
            "the event's head not handed over",
            _DCO,
            '            --expected-head "$HEAD_SHA" \\\n',
            "",
            expect="not handed the head the event names",
        ),
        _plant(
            "the head read from somewhere other than the event",
            _DCO,
            "          HEAD_SHA: ${{ github.event.pull_request.head.sha }}\n",
            "          HEAD_SHA: ${{ github.sha }}\n",
            expect="not handed the head the event names",
        ),
        _plant(
            "the title not read from the event file",
            _DCO,
            '--event "$GITHUB_EVENT_PATH"',
            '--event "$RUNNER_TEMP/event.json"',
        ),
        _plant(
            "the report read from somewhere else",
            _DCO,
            "fs.readFileSync(process.env.REPORT, 'utf8')",
            "(process.env.REPORT_TEXT || '')",
        ),
        _plant("a fence of fixed length", _DCO, _DCO_FENCE, "const bar = '```';"),
        _plant(
            "the token handed to the checkout",
            _DCO,
            "          persist-credentials: false\n",
            "          persist-credentials: false\n          token: ${{ github.token }}\n",
            expect="hands the job token to a step that does not fetch",
        ),
        Control("the tree's dco.yml passes", _nothing, fails=False),
    ),
)

ROWS = (*ROWS, DCO_CHECKS_WITH_THE_BASE_BRANCHS_CHECK)


# ── Every timeout mark is listed with its reason ────────────────────────────────────────────
#
# A test that needs a cap other than the suite's default says so with `pytest.mark.timeout(N)`,
# and each such site is listed in tests/timeout-marks.toml with its cap and why (#3926). The
# walker and the list's reader live in tests/timeout_marks.py.

_MARKED_TEST = "tests/test_slow.py"
_SCAFFOLD_MARKS = (
    "[[mark]]\n"
    'site = "tests/test_slow.py::test_it_is_slow"\n'
    "cap = 300\n"
    'reason = "the scaffold\'s listed mark"\n'
    "\n[[mark]]\n"
    'site = "tests/test_slow.py::_SHARED"\n'
    "cap = 240\n"
    'reason = "a mark a decorator reuses, by a name bound to a constant"\n'
    "\n[[mark]]\n"
    'site = "tests/test_slow.py::ROW_TIMEOUTS[a slow row]"\n'
    "cap = 90\n"
    'reason = "a table row\'s own mark, keyed by its name"\n'
)


def _plant_and_list(rel: str, text: str, site: str, cap: int) -> Callable[[Path], None]:
    """*text* appended to *rel*, and its mark listed with a reason, as a reviewed entry would be."""

    def plant(root: Path) -> None:
        _append(rel, text)(root)
        _append(
            timeout_marks.LIST,
            f'\n[[mark]]\nsite = "{site}"\ncap = {cap}\nreason = "listed, with a reason"\n',
        )(root)

    return plant


def _timeout_scaffold(root: Path) -> None:
    """A tree where the rule holds: one listed decorator, one listed mark held in a name whose cap
    is a module constant, a listed mark keyed by a table row's name, and a test with no mark."""
    _write(
        root,
        _MARKED_TEST,
        "import pytest\n\n_CAP = 200 + 40\n_SHARED = pytest.mark.timeout(_CAP)\n\n\n"
        "@pytest.mark.timeout(300)\ndef test_it_is_slow():\n    pass\n\n\n"
        "@_SHARED\ndef test_it_shares_a_cap():\n    pass\n\n\n"
        "def test_it_is_quick():\n    pass\n\n\n"
        'ROW_TIMEOUTS = {"a slow row": pytest.mark.timeout(90)}\n',
    )
    _write(root, timeout_marks.LIST, _SCAFFOLD_MARKS)


EVERY_TIMEOUT_MARK_IS_LISTED = Row(
    name="every-timeout-mark-is-listed-with-its-reason",
    check=timeout_marks.violations,
    scaffold=_timeout_scaffold,
    controls=(
        Control(
            "an unlisted decorator",
            _append(
                "tests/test_planted.py",
                "import pytest\n\n\n@pytest.mark.timeout(90)\ndef test_new():\n    pass\n",
            ),
            expect="tests/test_planted.py::test_new sets a 90 s timeout",
        ),
        Control(
            "a conftest hook that adds a mark",
            _append(
                "tests/conftest.py",
                "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                "    for item in items:\n"
                "        item.add_marker(pytest.mark.timeout(60))\n",
            ),
            expect="tests/conftest.py::pytest_collection_modifyitems caps every test it reaches",
        ),
        Control(
            "a module's pytestmark",
            _append(
                "tests/test_planted.py",
                "import pytest\n\npytestmark = [pytest.mark.anyio, pytest.mark.timeout(500)]\n",
            ),
            expect="tests/test_planted.py::pytestmark caps every test it reaches",
        ),
        Control(
            "a listed module's pytestmark, still a blanket",
            _plant_and_list(
                "tests/test_planted.py",
                "import pytest\n\npytestmark = pytest.mark.timeout(500)\n",
                "tests/test_planted.py::pytestmark",
                500,
            ),
            expect="tests/test_planted.py::pytestmark caps every test it reaches",
        ),
        Control(
            "a listed mark in a conftest, still a blanket",
            _plant_and_list(
                "tests/sub/conftest.py",
                "import pytest\n\n\n@pytest.fixture\ndef slow(request):\n"
                "    request.node.add_marker(pytest.mark.timeout(60))\n",
                "tests/sub/conftest.py::slow",
                60,
            ),
            expect="tests/sub/conftest.py::slow caps every test it reaches",
        ),
        _plant(
            "an unlisted row in a table of marks",
            _MARKED_TEST,
            '{"a slow row": pytest.mark.timeout(90)}',
            '{"a slow row": pytest.mark.timeout(90), "another": pytest.mark.timeout(70)}',
            expect="tests/test_slow.py::ROW_TIMEOUTS[another] sets a 70 s timeout",
        ),
        _plant(
            "a table row's cap changed without its entry",
            _MARKED_TEST,
            '{"a slow row": pytest.mark.timeout(90)}',
            '{"a slow row": pytest.mark.timeout(95)}',
            expect="ROW_TIMEOUTS[a slow row] sets 95 s where tests/timeout-marks.toml lists 90 s",
        ),
        Control(
            "a mark through `from pytest import mark as m`",
            _append(
                "tests/test_planted.py",
                "from pytest import mark as m\n\n\n@m.timeout(70)\ndef test_aliased():\n    pass\n",
            ),
            expect="test_aliased sets a 70 s timeout",
        ),
        Control(
            "a mark on a method of a test class",
            _append(
                "tests/test_planted.py",
                "import pytest\n\n\nclass TestThing:\n    @pytest.mark.timeout(80)\n"
                "    def test_m(self):\n        pass\n",
            ),
            expect="tests/test_planted.py::TestThing.test_m sets a 80 s timeout",
        ),
        _plant(
            "a cap changed without its entry",
            _MARKED_TEST,
            "@pytest.mark.timeout(300)",
            "@pytest.mark.timeout(600)",
            expect="sets 600 s where tests/timeout-marks.toml lists 300 s",
        ),
        _plant(
            "a module constant moved under a mark that reuses it",
            _MARKED_TEST,
            "_CAP = 200 + 40",
            "_CAP = 200 + 400",
            expect="::_SHARED sets 600 s where",
        ),
        _plant(
            "an entry left behind after its mark is deleted",
            _MARKED_TEST,
            "@pytest.mark.timeout(300)\n",
            "",
            expect="test_it_is_slow is listed in tests/timeout-marks.toml but sets no timeout",
        ),
        _plant(
            "an entry with no reason",
            timeout_marks.LIST,
            'reason = "the scaffold\'s listed mark"',
            'reason = "  "',
            expect="with no reason",
        ),
        Control(
            "a site listed twice",
            _append(
                timeout_marks.LIST,
                '\n[[mark]]\nsite = "tests/test_slow.py::test_it_is_slow"\ncap = 300\n'
                'reason = "again"\n',
            ),
            expect="is listed twice",
        ),
        Control(
            "a cap the row cannot read",
            _append(
                "tests/test_planted.py",
                "import os\n\nimport pytest\n\n\n"
                "@pytest.mark.timeout(int(os.environ.get('T', '9')))\n"
                "def test_env():\n    pass\n",
            ),
            expect="sets a cap the row cannot read",
        ),
        Control("the list removed", _remove(timeout_marks.LIST), expect="is missing"),
        Control(
            "a test with another mark, and a test that sets a subprocess timeout, pass",
            _append(
                "tests/test_planted.py",
                "import subprocess\n\nimport pytest\n\n\n@pytest.mark.slow\ndef test_a():\n"
                "    subprocess.run(['true'], timeout=5)\n",
            ),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, EVERY_TIMEOUT_MARK_IS_LISTED)


# ── The suite's default cap is set once ─────────────────────────────────────────────────────
#
# Every test runs under timeout_marks.DEFAULT seconds, the one `--timeout` in pyproject.toml's
# addopts (#3926). A step, a package's pytest config, an ini key or PYTEST_TIMEOUT that passes
# another would give a whole job a second default, which no mark and no reason names.

_DEFAULT_CAP = f"--timeout={timeout_marks.DEFAULT}"
_CAP_CATALOGUE = "tools/ci/catalogue.toml"
_CAP_STEP = 'argv = ["uv", "run", "pytest", "-n", "auto", "tests"]'


def _default_scaffold(root: Path) -> None:
    """A tree where the cap is set once: pyproject's addopts, and a catalogue step, a package, a
    Makefile target, a workflow and a script that run pytest without one."""
    _write(
        root,
        "pyproject.toml",
        f'[tool.pytest.ini_options]\naddopts = ["--verbose", "{_DEFAULT_CAP}"]\n'
        'timeout_method = "signal"\n',
    )
    _write(
        root,
        "packages/client/pyproject.toml",
        '[tool.pytest.ini_options]\naddopts = ["-q"]\ntestpaths = ["tests"]\n',
    )
    _write(
        root,
        _CAP_CATALOGUE,
        '[[job]]\nid = "suite"\n\n[job.env]\nPYTHONHASHSEED = "0"\n\n'
        f"[[job.command]]\n{_CAP_STEP}\n\n"
        '[[job]]\nid = "e2e"\n\n[[job.command]]\n'
        'argv = ["npx", "playwright", "test", "--timeout", "60000"]\n',
    )
    _write(root, "Makefile", "test:\n\t$(PYTHON) -m pytest -n auto --dist worksteal tests\n")
    _write(
        root,
        ".github/workflows/ci.yml",
        "jobs:\n  e2e:\n    steps:\n      - run: npx playwright test --timeout 60000\n",
    )
    _write(root, "scripts/run_suite.sh", '#!/bin/sh\nexec python -m pytest tests "$@"\n')
    # A test's own child pytest, over a scratch tree it built, is under the test's own cap.
    _write(
        root,
        "tests/test_child.py",
        'import subprocess\n\n\ndef test_it():\n    subprocess.run(["pytest", "--timeout=1"])\n',
    )


THE_DEFAULT_CAP_IS_SET_ONCE = Row(
    name="the-suite-default-cap-is-set-once-in-pyproject",
    check=timeout_marks.default_violations,
    scaffold=_default_scaffold,
    controls=(
        _plant(
            "the default raised",
            "pyproject.toml",
            _DEFAULT_CAP,
            "--timeout=120",
            expect=f"must pass the default cap once, as {_DEFAULT_CAP}",
        ),
        _plant(
            "the default removed",
            "pyproject.toml",
            f', "{_DEFAULT_CAP}"',
            "",
            expect="it passes none",
        ),
        _plant(
            "a second cap in addopts",
            "pyproject.toml",
            f'"{_DEFAULT_CAP}"',
            f'"{_DEFAULT_CAP}", "--timeout=300"',
            expect="must pass the default cap once",
        ),
        _plant(
            "the cap as an ini key",
            "pyproject.toml",
            'timeout_method = "signal"',
            "timeout = 300",
            expect="pyproject.toml sets pytest-timeout's timeout ini key",
        ),
        _plant(
            "the cap made to leave fixtures out",
            "pyproject.toml",
            'timeout_method = "signal"',
            "timeout_func_only = true",
            expect="sets pytest-timeout's timeout_func_only ini key",
        ),
        _plant(
            "a package's own cap",
            "packages/client/pyproject.toml",
            'addopts = ["-q"]',
            'addopts = ["-q", "--timeout=600"]',
            expect="packages/client/pyproject.toml's addopts passes a cap of its own",
        ),
        _plant(
            "a catalogue step's own cap",
            _CAP_CATALOGUE,
            _CAP_STEP,
            'argv = ["uv", "run", "pytest", "-n", "auto", "--timeout=90", "tests"]',
            expect="the catalogue's suite passes pytest a cap",
        ),
        _plant(
            "a catalogue step's ini override",
            _CAP_CATALOGUE,
            _CAP_STEP,
            'argv = ["uv", "run", "pytest", "-o", "timeout=90", "tests"]',
            expect="the catalogue's suite passes pytest a cap",
        ),
        _plant(
            "a catalogue job's PYTEST_TIMEOUT",
            _CAP_CATALOGUE,
            'PYTHONHASHSEED = "0"',
            'PYTEST_TIMEOUT = "90"',
            expect="the catalogue's suite sets PYTEST_TIMEOUT in its env",
        ),
        _plant(
            "a catalogue step's PYTEST_TIMEOUT",
            _CAP_CATALOGUE,
            _CAP_STEP,
            _CAP_STEP + '\nenv = { PYTEST_TIMEOUT = "90" }',
            expect="the catalogue's suite sets PYTEST_TIMEOUT on a step",
        ),
        _plant(
            "a Makefile target's own cap",
            "Makefile",
            "--dist worksteal tests",
            "--dist worksteal --timeout=0 tests",
            expect="Makefile:2 passes pytest a cap of its own",
        ),
        _plant(
            "a workflow's PYTEST_TIMEOUT",
            ".github/workflows/ci.yml",
            "jobs:\n",
            "env:\n  PYTEST_TIMEOUT: 90\njobs:\n",
            expect=".github/workflows/ci.yml:2 passes pytest a cap of its own",
        ),
        _plant(
            "a script's own cap",
            "scripts/run_suite.sh",
            "pytest tests",
            "pytest --timeout=600 tests",
            expect="scripts/run_suite.sh:2 passes pytest a cap of its own",
        ),
    ),
)

ROWS = (*ROWS, THE_DEFAULT_CAP_IS_SET_ONCE)


# ── The suite's temporary folders stay outside the runner's home ─────────────────────────────
#
# On a hosted Linux runner `RUNNER_TEMP` and the workspace sit under `/home/runner`, which is
# `$HOME`. The suite's real-home guard refuses a folder under the home unless it sits under the
# system temp folder, and path renderers shorten home paths to `~/...`, so a suite run whose
# `tmp_path` folders live under the home reds on the folder, not on the code. Breadth's first
# canary night did exactly that, with `--basetemp="$RUNNER_TEMP/pytest"`.

#: A `--basetemp` value, in its `=` or its space form, quoted or not, or an expression.
_BASETEMP = re.compile(r"--basetemp(?:=|\s+)(\"[^\"]*\"|'[^']*'|\$\{\{[^}]*\}\}\S*|\S+)")
#: A folder under the runner's home: the runner's temp folder or workspace, `$HOME` or `~`, or
#: a hosted runner's home written out.
_UNDER_HOME = re.compile(
    r"\$\{?RUNNER_TEMP\b|\$\{\{\s*runner\.temp\b|\$\{?GITHUB_WORKSPACE\b"
    r"|\$\{\{\s*github\.workspace\b|\$\{?HOME\b|\$\{\{\s*env\.HOME\b|^~|/home/|/Users/"
)


def _under_home(value: str) -> bool:
    """A folder under the runner's home. A relative one resolves in the workspace, so it is."""
    bare = value.strip().strip("'\"")
    return bool(_UNDER_HOME.search(bare)) or not bare.startswith(("/", "$"))


def suite_basetemp_violations(tree: Tree) -> list[str]:
    """No step that runs the suite keeps pytest's temporary folders under the runner's home:
    no ``--basetemp`` there, in either form, and no ``TMPDIR`` there in the workflow's, the
    job's or the step's env."""
    bad: list[str] = []
    for name, workflow in _workflows(tree):
        top = workflow.get("env") or {}
        for job_id, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                run = str(step.get("run", ""))
                suite = [w for w in _commands(run) if _runs_the_suite(w)]
                if not suite:
                    continue
                for words in suite:
                    for value in _BASETEMP.findall(" ".join(words)):
                        if _under_home(value):
                            bad.append(
                                f"{name}:{job_id} keeps pytest's folders under the runner's "
                                f"home: --basetemp {value}"
                            )
                for where, env in (
                    ("workflow", top),
                    ("job", job.get("env") or {}),
                    ("step", step.get("env") or {}),
                ):
                    value = str(env.get("TMPDIR", ""))
                    if value and _under_home(value):
                        bad.append(
                            f"{name}:{job_id} runs the suite with its {where}'s TMPDIR under "
                            f"the runner's home: {value}"
                        )
    return bad


_CANARY_RUN = (
    'uv run pytest -n auto --dist worksteal -q tests --shard-list "$RUNNER_TEMP/shard.txt"'
    " --junitxml=reports/junit-canary.xml"
    ' --shard-ids "$RUNNER_TEMP/ids/shard-ids-canary-${{ matrix.shard }}.txt"'
)


def _canary_basetemp(name: str, value: str, fails: bool = True) -> Control:
    """The canary's suite step with ``--basetemp`` *value* (the ``=`` or the space form)."""
    return _plant(
        name,
        _BREADTH,
        _CANARY_RUN,
        _CANARY_RUN.replace(" --junitxml=", f" {value} --junitxml="),
        fails,
        "" if not fails else "--basetemp",
    )


SUITE_TEMP_STAYS_OUT_OF_THE_HOME = _toolchain_row(
    "no suite step keeps pytest's temporary folders under the runner's home",
    suite_basetemp_violations,
    (
        _canary_basetemp("the first canary night's form", '--basetemp="$RUNNER_TEMP/pytest"'),
        _canary_basetemp("runner.temp, space form", "--basetemp ${{ runner.temp }}/p"),
        _canary_basetemp("the workspace", "--basetemp=$GITHUB_WORKSPACE/p"),
        _canary_basetemp("$HOME", "--basetemp=$HOME/p"),
        _canary_basetemp("~", "--basetemp=~/p"),
        _canary_basetemp("a Linux runner's home written out", "--basetemp=/home/runner/p"),
        _canary_basetemp("a macOS runner's home written out", "--basetemp=/Users/runner/p"),
        _canary_basetemp("a relative folder, so in the workspace", "--basetemp=reports/pytest"),
        _plant(
            "TMPDIR under runner.temp in a suite job's env",
            _BREADTH,
            '      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n',
            '      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n      TMPDIR: ${{ runner.temp }}\n',
            expect="TMPDIR",
        ),
        _plant(
            "TMPDIR under runner.temp in a suite step's env",
            _BREADTH,
            f"        run: {_CANARY_RUN}\n",
            f"        env:\n          TMPDIR: ${{{{ runner.temp }}}}\n        run: {_CANARY_RUN}\n",
            expect="TMPDIR",
        ),
        _canary_basetemp(
            "a basetemp outside the home passes", "--basetemp=/var/tmp/pytest-canary", fails=False
        ),
        Control("a suite step with no --basetemp passes", _nothing, fails=False),
    ),
)
ROWS = (*ROWS, SUITE_TEMP_STAYS_OUT_OF_THE_HOME)


# ── Every catalogue command is the workflow's ────────────────────────────────────────────────
#
# tools/ci/catalogue.toml lists CI's validation jobs (#3909). The tier workflows run each change
# and merge entry through run-job, so its steps, cap, runner, Python and env are the catalogue's
# own (the tier rows below hold each stage job to its lines). breadth.yml still writes its jobs'
# steps, so each breadth entry must say what breadth.yml runs, and breadth.yml must run nothing an
# entry does not say. Each breadth entry maps to the breadth.yml jobs below. Each of its steps is
# one of those jobs' run: lines, token for token, from the same working directory with the same
# step env, once the runner's spellings of a value are read as the catalogue's placeholders; its
# steps come in breadth.yml's order, none twice. The other way, every run: line of a mapped job is
# one entry's step, except the closed set of lines below that validate nothing. Its cap, runner,
# Python, environment and shard count are the jobs'. An install is toolchain, never a step: each
# install a breadth entry's toolchain names is one its breadth.yml jobs run. Every breadth.yml job
# is an entry's or orchestration. Of every entry, breadth's or not: its reports are the ones its
# steps write, each script it runs stops at its first failing command, one whose steps run a
# security-marked module is security posture, and its posture and tiers are the ones this table
# gives it. The table is the one a reader of the catalogue goes by.


@dataclass(frozen=True)
class _Mapped:
    """One entry as this row holds it: the breadth.yml jobs it maps to (none for an entry that runs
    in the change and merge tiers alone), its tiers and its posture."""

    jobs: tuple[str, ...]
    tiers: tuple[str, ...]
    security: str = "ordinary"
    live: bool = False
    reuse: str = "never"


#: A breadth.yml job is named by its id in this table.
#: Each workflow this row maps entries to, by the prefix its jobs carry in this table.
_MAPPED_PREFIXES = ("",)

#: The browser run against the pinned image. #3943 renames it from e2e-stateful to e2e-pinned,
#: and a rename of what a row names lands in two merges (#4584 step 4): these rows first, then
#: the catalogue. So the rows take either name, and the catalogue holds exactly one of them.
_PINNED_BROWSER = ("e2e-stateful", "e2e-pinned")

_CATALOGUE_JOBS: dict[str, _Mapped] = {
    "static": _Mapped((), ("change", "entry", "merge")),
    "rails": _Mapped((), ("change", "entry", "merge"), reuse="tree"),
    "rails-checks": _Mapped((), ("change", "entry", "merge"), reuse="tree"),
    "python-suite": _Mapped((), ("entry", "merge"), security="security"),
    "python-suite-macos": _Mapped((), ("merge",), security="security"),
    "security": _Mapped(
        ("security",), ("change", "entry", "merge", "breadth"), security="security"
    ),
    "security-macos": _Mapped(("security-macos",), ("breadth",), security="security"),
    "suite-ubuntu": _Mapped(("ubuntu",), ("breadth",), security="security"),
    "suite-macos": _Mapped(("macos",), ("breadth",), security="security"),
    "suite-arm": _Mapped(("arm",), ("breadth",), security="security"),
    "suite-canary": _Mapped(("canary",), ("breadth",), security="security"),
    # The suite in seeded orders: it runs the security-marked modules, and its seeds change every
    # run, so it is never reused.
    "random-order": _Mapped(("random-order",), ("breadth",), security="security"),
    "coverage-shard": _Mapped(("coverage-shard",), ("breadth",), security="security"),
    "coverage": _Mapped(("coverage",), ("breadth",)),
    "rails-macos": _Mapped(("rails-macos",), ("breadth",), reuse="tree"),
    "audit-nightly": _Mapped(("audit",), ("breadth",), live=True),
    # Collects the whole suite and runs no test (catalogue.collect_only), so it is no security
    # evidence: ordinary, and it may carry an earlier result.
    "collection-parses": _Mapped(("collection-parses",), ("breadth",), reuse="tree"),
    # It reads the installer the website serves and PyPI as they are the night it runs: live,
    # like the nightly audit, so its evidence is never reused, by TTL or by tree.
    "install-smoke": _Mapped(("install-smoke",), ("breadth",), live=True),
    "planned-ids-ubuntu": _Mapped(("planned-ids-ubuntu",), ("breadth",)),
    "planned-ids-macos": _Mapped(("planned-ids-macos",), ("breadth",)),
    "planned-ids-arm": _Mapped(("planned-ids-arm",), ("breadth",)),
    "planned-ids-coverage-shard": _Mapped(("planned-ids-coverage-shard",), ("breadth",)),
    "planned-ids-canary": _Mapped(("planned-ids-canary",), ("breadth",)),
    "planned-ids-random-order": _Mapped(("planned-ids-random-order",), ("breadth",)),
    "shard-union": _Mapped(("shard-union",), ("breadth",)),
    "shard-union-canary": _Mapped(("shard-union-canary",), ("breadth",)),
    "client": _Mapped((), ("change", "entry", "merge")),
    "web-unit": _Mapped((), ("change", "entry", "merge"), reuse="tree"),
    "web-build": _Mapped((), ("entry", "merge"), reuse="tree"),
    "package": _Mapped((), ("entry", "merge")),
    "e2e": _Mapped((), ("entry", "merge")),
    "e2e-shard": _Mapped((), ("entry", "merge")),
    **{name: _Mapped((), ("entry", "merge")) for name in _PINNED_BROWSER},
    "ui-quality": _Mapped((), ("entry", "merge")),
    "desktop-smoke": _Mapped((), ("entry", "merge")),
    "browse-live": _Mapped((), ("entry", "merge"), security="security", live=True),
    "apps-contract": _Mapped((), ("entry", "merge")),
    # The sandbox's own namespace tests, on a runner whose user-namespace restriction its `userns`
    # install lifts (#3918). Security posture: it runs fence tests, so it is never reused or rerun.
    # The row is on main before the entry, which a second change adds, as a toolchain word is.
    "namespace-sandbox": _Mapped((), ("entry", "merge"), security="security"),
    "audit": _Mapped((), ("entry", "merge"), live=True),
    # The gateway image's build and smoke (#3917), which merge.yml runs through run-job. Its
    # Dockerfile's apt installs are pinned by no lock, so it is never reused.
    "image": _Mapped((), ("entry", "merge")),
}

#: Each mapped job is at most one entry's, so a run: line of it is one entry's step or none.
_MAPPED_JOBS = [b for entry in _CATALOGUE_JOBS.values() for b in entry.jobs]
assert len(_MAPPED_JOBS) == len(set(_MAPPED_JOBS)), "a workflow job mapped to two entries"

#: The mapped workflow's jobs that decide what runs or gather what ran, and validate nothing
#: themselves: breadth.yml's earlier record and shard plan, and its run's report.
_ORCHESTRATION = frozenset({"freshness", "report"})


def _prefix_of(key: str) -> str:
    """The prefix of the workflow a job key of this table is in: empty for breadth.yml's."""
    return next((p for p in _MAPPED_PREFIXES if p and key.startswith(p)), "")


def _workflow_name(key: str) -> str:
    return _prefix_of(key)[:-1] or "breadth.yml"


def _where(key: str) -> str:
    """A job key of this table as a reader names it: ``breadth.yml job X``."""
    return f"{_workflow_name(key)} job {key[len(_prefix_of(key)):]}"


def _owners(keys: Sequence[str]) -> str:
    """The workflows the keys are in, as a possessive: ``breadth.yml's``."""
    found = sorted({_workflow_name(k) for k in keys})
    return " and ".join(f"{w}'s" for w in found) if len(found) > 1 else f"{found[0]}'s"


def _names(keys: Sequence[str]) -> str:
    """The workflows the keys are in: ``breadth.yml``."""
    return " and ".join(sorted({_workflow_name(k) for k in keys}))


def _norm(value: str, *, expressions_only: bool = False) -> str:
    """A workflow value with each runner spelling of a placeholder read as the placeholder.

    With ``expressions_only``, only the ``${{ }}`` spellings: a step's ``env:`` is not a shell,
    so GitHub expands an expression there and leaves ``$RUNNER_TEMP`` or ``$(...)`` as the
    literal text, which is not what the entry's placeholder becomes when it runs."""
    for name, spellings in catalogue.PLACEHOLDERS.items():
        for spelling in spellings:
            if not expressions_only or spelling.startswith("${{"):
                value = value.replace(spelling, f"{{{name}}}")
    return value


#: The run: lines of a mapped breadth.yml job that validate nothing, by what they do. Each is read
#: the way a run: line is (placeholders, folded lines, words), so it matches only itself: a
#: closed set of lines, not a pattern. Every other line of a mapped job is one entry's step.
_NOT_COMMANDS: dict[str, str] = {
    "the runner's identity": r"""
        echo "runner image ${ImageOS:?not a GitHub-hosted runner image} ${ImageVersion:?no image version} on ${RUNNER_OS} ${RUNNER_ARCH}"
    """,  # noqa: E501
    "an install the job's toolchain names": r"""
        uv sync --locked --extra dev
    """,
    "breadth's: the uv-managed interpreter {python} finds, made present before it is found": r"""
uv venv "$RUNNER_TEMP/shard-python"
""",  # noqa: E501
    "breadth's: the tmux install, by runner": r"""
if command -v tmux >/dev/null 2>&1; then tmux -V; exit 0; fi
case "$RUNNER_OS" in
  Linux) sudo apt-get update -qq &&
         sudo apt-get install -y --no-install-recommends tmux ;;
  macOS) brew install tmux ;;
  *) echo "no tmux installer known for RUNNER_OS=$RUNNER_OS" >&2; exit 1 ;;
esac
tmux -V

""",  # noqa: E501
    "breadth's: a leg's inputs, as its runner reports them": r"""
"$(uv python find "$PY")" tools/ci/breadth_evidence.py inputs --out "$RUNNER_TEMP/inputs.json" \
  --tree "$(git rev-parse 'HEAD^{tree}')" \
  --image "$ImageOS" --image-version "$ImageVersion" --arch "$RUNNER_ARCH" \
  --python "$("$(uv python find "$PY")" -c 'import platform; print(platform.python_version())')" \
  --uv "$(uv --version | awk '{print $2}')" \
  --node "$(node --version 2>/dev/null || echo none)" \
  --external "shard_files=${SHARD_FILES:?no shard list}"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py inputs --out "$RUNNER_TEMP/inputs.json" \
  --tree "$(git rev-parse 'HEAD^{tree}')" \
  --image "$ImageOS" --image-version "$ImageVersion" --arch "$RUNNER_ARCH" \
  --python "$("$(uv python find "$PY")" -c 'import platform; print(platform.python_version())')" \
  --uv "$(uv --version | awk '{print $2}')" \
  --node "$(node --version 2>/dev/null || echo none)"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py inputs --out "$RUNNER_TEMP/inputs.json" \
  --tree "$(git rev-parse 'HEAD^{tree}')" \
  --image "$ImageOS" --image-version "$ImageVersion" --arch "$RUNNER_ARCH" \
  --python "$("$(uv python find "$PY")" -c 'import platform; print(platform.python_version())')" \
  --uv "$(uv --version | awk '{print $2}')" \
  --node "$(node --version 2>/dev/null || echo none)" \
  --external "night=${NIGHT:?freshness named no night}"

""",  # noqa: E501
    "breadth's: the legs that may carry an earlier result decide whether they run": r"""
"$(uv python find "$PY")" tools/ci/breadth_evidence.py decide --key "rails-macos-26-3.12/1" --inputs "$RUNNER_TEMP/inputs.json" --previous "$RUNNER_TEMP/previous/previous.json" --ref "$GITHUB_REF" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/rails-macos-26-3.12__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py decide --key "collection-parses/1" --inputs "$RUNNER_TEMP/inputs.json" --previous "$RUNNER_TEMP/previous/previous.json" --ref "$GITHUB_REF" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/collection-parses__1.json"
""",  # noqa: E501
    "breadth's: what a leg ran, written to the run's record": r"""
"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/ubuntu-24.04-3.13__${{ matrix.shard }}.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/macos-26-3.12__${{ matrix.shard }}.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/ubuntu-24.04-arm-3.13__${{ matrix.shard }}.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/canary__${{ matrix.shard }}.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --seeds "${{ github.run_number }}1,${{ github.run_number }}2" --out "$RUNNER_TEMP/entry/random-order__${{ matrix.shard }}.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/coverage__${{ matrix.shard }}.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/rails-macos-26-3.12__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/security__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/security-macos__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/audit__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/collection-parses__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/planned-ids-ubuntu__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/planned-ids-macos__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/planned-ids-arm__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/planned-ids-coverage-shard__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/planned-ids-canary__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/planned-ids-random-order__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/shard-union__1.json"

"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/shard-union-canary__1.json"

""",  # noqa: E501
    "breadth's: a leg's result, read back from the entry it wrote": r"""
"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/ubuntu-24.04-3.13__${{ matrix.shard }}.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/macos-26-3.12__${{ matrix.shard }}.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/ubuntu-24.04-arm-3.13__${{ matrix.shard }}.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/canary__${{ matrix.shard }}.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/random-order__${{ matrix.shard }}.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/coverage__${{ matrix.shard }}.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/rails-macos-26-3.12__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/security__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/security-macos__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/audit__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/collection-parses__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/planned-ids-ubuntu__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/planned-ids-macos__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/planned-ids-arm__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/planned-ids-coverage-shard__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/planned-ids-canary__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/planned-ids-random-order__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/shard-union__1.json"

"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/shard-union-canary__1.json"

""",  # noqa: E501
    "breadth's: the coverage shards' result, gated before the combine": r"""
result='${{ needs.coverage-shard.result }}'
# Reachable only when a shard was cancelled while the run lived on: part of the
# suite was measured by nothing, so it is red (matrix's doctrine).
if [ "$result" = "cancelled" ]; then
  echo "::error title=coverage shards unproven::a coverage shard was cancelled while" \
       "this run was still live, so part of the suite was measured by nothing. Re-run."
  exit 1
fi
if [ "$result" != "success" ]; then
  echo "::error title=coverage shards failed::the coverage shards' result was" \
       "'$result' (expected 'success'). Open the red 'coverage-shard (...)' leg(s) above."
  exit 1
fi
echo "all coverage shards passed"

""",  # noqa: E501
    "random-order's: each order's state-leak report, and the shard's file list, kept beside its"
    " order files": r"""
mkdir -p reports/random-order
if [ -f reports/state-leaks.json ]; then
  mv reports/state-leaks.json "reports/random-order/state-leaks-${{ github.run_number }}1.json"
  mv reports/state-leaks.json "reports/random-order/state-leaks-${{ github.run_number }}2.json"
fi
cp "$RUNNER_TEMP/shard.txt" reports/random-order/shard.txt
""",  # noqa: E501
    "install-smoke's: the bare container's own system, for the log": r"""
. /etc/os-release
echo "container ${PRETTY_NAME} on ${RUNNER_OS} ${RUNNER_ARCH}"
""",  # noqa: E501
    "install-smoke's: the bare container's curl and CA roots, before any installer runs": r"""
set -eu
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl
""",  # noqa: E501
    "install-smoke's: its inputs, the served installer's digest among them": r"""
if curl -fsSL --retry 3 --max-time 60 -o "$RUNNER_TEMP/served-install.sh" https://personalclaw.dev/install; then
  installer=$(sha256sum "$RUNNER_TEMP/served-install.sh" | cut -d' ' -f1)
else
  installer=unreadable
fi
"$(uv python find "$PY")" tools/ci/breadth_evidence.py inputs --out "$RUNNER_TEMP/inputs.json" \
  --tree "${TREE:?freshness named no tree}" \
  --image "ubuntu:26.04" \
  --image-version "sha256:42527649dbc4f9197a6073f426f488cbff0e9cc9f2afce32d91c383e4875634a" \
  --arch "$RUNNER_ARCH" \
  --python "$("$(uv python find "$PY")" -c 'import platform; print(platform.python_version())')" \
  --uv "$(uv --version | awk '{print $2}')" --node none \
  --external "installer_sha256=$installer" --external "night=${NIGHT:?freshness named no night}"
if [ "$installer" = unreadable ]; then
  echo "::error title=installer unreadable::the served installer could not be read, so this leg's inputs are not known: red"
  exit 1
fi
""",  # noqa: E501
    "install-smoke's: what it ran, written to the run's record": r"""
"$(uv python find "$PY")" tools/ci/breadth_evidence.py ran --inputs "$RUNNER_TEMP/inputs.json" --outcome "$OUTCOME" --attempt "$GITHUB_RUN_ATTEMPT" --out "$RUNNER_TEMP/entry/install-smoke__1.json"
""",  # noqa: E501
    "install-smoke's: its result, read back from the entry it wrote": r"""
"$(uv python find "$PY")" -c "import json,sys; e=json.load(open(sys.argv[1])); print(e[\"result\"], e.get(\"reused_from\") or \"ran\"); sys.exit(e[\"result\"] != \"success\")" "$RUNNER_TEMP/entry/install-smoke__1.json"
""",  # noqa: E501
}


def _run_lines(run: str) -> Iterator[tuple[str, list[str] | None]]:
    """Each logical line of a run: block, a placeholder for each runner spelling, with its words
    (``None`` for a line no argv could equal, such as an echo with an apostrophe)."""
    for name, spellings in catalogue.PLACEHOLDERS.items():
        for spelling in spellings:
            run = run.replace(spelling, f"{{{name}}}")
    for line in run.replace("\\\n", " ").splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            yield line.strip(), None
            continue
        if words:
            yield line.strip(), words


_NOT_COMMAND_LINES = frozenset(
    tuple(words) for text in _NOT_COMMANDS.values() for _, words in _run_lines(text) if words
)


@dataclass(frozen=True)
class _Line:
    """One validating run: line of a mapped workflow job, the argv it runs, where and with what
    step env."""

    job: str
    step: int
    name: str
    argv: tuple[str, ...]
    workdir: str
    env: tuple[tuple[str, str], ...]

    def text(self) -> str:
        return f"{self.workdir + '$ ' if self.workdir else ''}{shlex.join(self.argv)}"


#: Each variable a shell word expands, by its whole name: ``$NAME`` or ``${NAME...}``.
_SHELL_VARIABLE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")

#: The variables a workflow step sets only to carry a placeholder's value (the base a diff is
#: taken against): the placeholder stands for them, so a step's env is compared without them.
_PLACEHOLDER_VARIABLES = frozenset(
    spelling[1:]
    for spellings in catalogue.PLACEHOLDERS.values()
    for spelling in spellings
    if re.fullmatch(r"\$[A-Z_][A-Z0-9_]*", spelling)
)


def _step_env(step: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """A workflow step's own env, less each variable a placeholder stands for that a command of
    its run: block reads, each value with its ``${{ }}`` placeholders read as the placeholder (an
    entry's env value may name one, as ``blob-report/shard-{shard}`` does). A shell spelling stays
    as it is: GitHub leaves it. A variable a command reads (``$BASE_SHA`` or ``${BASE_SHA}``) is
    how the placeholder reaches the argv, which holds it as the placeholder; one no command reads,
    a comment's included, reaches the process, as ``BASE_SHA`` reaches the rails' site baselines,
    so it is the step's env like any other."""
    words = [word for command in _commands(str(step.get("run", ""))) for word in command]
    read = _PLACEHOLDER_VARIABLES & {
        name for word in words for name in _SHELL_VARIABLE.findall(word)
    }
    return tuple(
        sorted(
            (str(k), _norm(str(v), expressions_only=True))
            for k, v in (step.get("env") or {}).items()
            if str(k) not in read
        )
    )


def _job_lines(key: str, job: dict[str, Any], bad: list[str]) -> list[_Line]:
    """A mapped workflow job's validating lines, in order. A line repeated in one step is one
    line."""
    lines: list[_Line] = []
    for index, step in enumerate(job.get("steps") or []):
        if "run" not in step:
            continue
        name = str(step.get("name", f"step {index + 1}"))
        seen: set[tuple[str, ...]] = set()
        for text, words in _run_lines(str(step["run"])):
            if words is None:
                bad.append(f"{_where(key)} runs {text!r} ({name!r}), which no argv can be")
                continue
            if tuple(words) in _NOT_COMMAND_LINES:
                continue
            argv = tuple(words)
            if argv in seen:
                continue
            seen.add(argv)
            workdir = str(step.get("working-directory", ""))
            lines.append(_Line(key, index, name, argv, workdir, _step_env(step)))
    return lines


def _junit_paths(argv: tuple[str, ...]) -> Iterator[str]:
    # The Vitest step names no report: its config writes one under CI, which the Vitest
    # runner's row holds to this path.
    if tuple(argv) == run_jobs.VITEST_STEP:
        yield run_jobs.VITEST_JUNIT
    for n, arg in enumerate(argv):
        if arg.startswith("--junitxml="):
            yield arg.split("=", 1)[1]
        elif arg == "--junitxml" and n + 1 < len(argv):
            yield argv[n + 1]


@functools.cache
def _tree_marked() -> frozenset[str]:
    return security_census.take(REPO).marked


def _marked(root: Path) -> frozenset[str]:
    """The test modules that carry the security marker under ``root``."""
    return _tree_marked() if root == REPO else security_census.take(root).marked


def _runs_marked(job: catalogue.Job, marked: frozenset[str]) -> list[str]:
    """The security-marked modules a job's pytest steps name, one by one or by a folder."""
    named = {
        arg.rstrip("/")
        for step in job.command
        if "pytest" in step.argv and not catalogue.collect_only(step.argv)
        for arg in step.argv[step.argv.index("pytest") + 1 :]
    }
    return sorted(m for m in marked if any(m == p or m.startswith(p + "/") for p in named))


def _claim(
    job: catalogue.Job, lines: list[_Line], claims: dict[_Line, list[str]], bad: list[str]
) -> None:
    """Match each of the entry's steps to the next of its jobs' lines that runs it, in order.
    A script entry's steps are its scripts, each run as ``sh <script>``: the one way
    tools/ci/supply_chain.py reads a script a step runs."""
    last, last_step = -1, 0
    steps = job.command or tuple(catalogue.Step(("sh", s)) for s in job.script or ())
    for n, step in enumerate(steps, 1):
        hits = [
            i
            for i, line in enumerate(lines)
            if line.argv == step.argv and line.workdir == step.workdir
        ]
        jobs = ", ".join(map(_where, _CATALOGUE_JOBS[job.id].jobs))
        if not hits:
            bad.append(f"{job.id}: command step {n} ({step.render()}) is no run: line of {jobs}")
            continue
        later = [i for i in hits if i > last]
        if not later:
            where = _workflow_name(lines[hits[0]].job)
            if last in hits:
                bad.append(
                    f"{job.id}: command step {n} repeats command step {last_step}"
                    f" ({step.render()}): {where} runs it once"
                )
            else:
                bad.append(
                    f"{job.id}: command step {n} is out of {where}'s order"
                    f" ({step.render()}): {where} runs it before command step {last_step}"
                )
            continue
        last, last_step = later[0], n
        line = lines[last]
        claims[line].append(job.id)
        if tuple(sorted(step.env.items())) != line.env:
            bad.append(
                f"{job.id}: command step {n} ({step.render()}) sets env {dict(step.env)},"
                f" and its {_workflow_name(line.job)} step sets {dict(line.env)}"
            )


def _installs(job: catalogue.Job, mapped: list[Any], bad: list[str], where: str) -> None:
    """Each install the entry's toolchain names is one its mapped jobs, in *where*, run."""
    ran = {
        tool
        for workflow_job in mapped
        for step in workflow_job.get("steps") or []
        if "run" in step
        for _, words in _run_lines(str(step["run"]))
        if words
        for prefix, tool in catalogue.INSTALLS
        if tuple(words[: len(prefix)]) == prefix
    }
    for tool in sorted({t for _, t in catalogue.INSTALLS} & set(job.toolchain) - ran):
        bad.append(f"{job.id}: toolchain names {tool}, and none of its {where} jobs installs it")


def _same_job(
    job: catalogue.Job,
    jobs: dict[str, Any],
    mine: list[_Line],
    bad: list[str],
    actions: dict[str, dict[str, Any]] | None = None,
) -> None:
    """The entry's cap, runner, Python, env and shards are its mapped jobs'. A setup step
    inside a local action counts at the index of the step that calls the action."""
    mapped = _CATALOGUE_JOBS[job.id].jobs
    cap = max(int(jobs[c].get("timeout-minutes") or 0) for c in mapped)
    if job.timeout_min != cap:
        bad.append(
            f"{job.id}: timeout_min is {job.timeout_min}, and {_owners(mapped)} cap is {cap}"
        )
    runners = sorted({str(jobs[c].get("runs-on")) for c in mapped})
    if runners != [job.platform]:
        bad.append(
            f"{job.id}: platform is {job.platform}, and {_names(mapped)} runs it on"
            f" {', '.join(runners)}"
        )
    # The Python is the one setup-uv set before the entry's first step on each of its jobs.
    pythons: set[str] = set()
    for c in mapped:
        first = min((line.step for line in mine if line.job == c), default=None)
        for index, s in local_actions.expand(jobs[c].get("steps") or [], actions or {}):
            if _action(s) != "astral-sh/setup-uv" or (first is not None and index >= first):
                continue
            value = str((s.get("with") or {}).get("python-version"))
            if value == "${{ matrix.python }}":
                pythons |= set(_matrix_values(jobs[c], "python"))
            else:
                pythons.add(value)
    if pythons and job.python not in pythons:
        bad.append(
            f"{job.id}: python is {job.python or 'unset'}, and {_owners(mapped)} setup-uv sets"
            f" {', '.join(sorted(pythons))} before its steps"
        )
    elif not pythons and job.python not in (None, "image"):
        bad.append(
            f"{job.id}: python is {job.python}, and {_names(mapped)} sets none before its" " steps"
        )
    env: dict[str, str] = {}
    for c in mapped:
        env.update(
            {
                str(k): _norm(str(v))
                for k, v in (jobs[c].get("env") or {}).items()
                if str(k) not in catalogue.PLACEHOLDER_ENV
            }
        )
    if dict(job.env) != env:
        bad.append(f"{job.id}: env is {dict(job.env)}, and {_owners(mapped)} is {env}")
    shards = max(len(_matrix_values(jobs[c], "shard")) or 1 for c in mapped)
    if job.shards != shards:
        bad.append(f"{job.id}: shards is {job.shards}, and {_names(mapped)} runs {shards}")


def _reports(job: catalogue.Job, bad: list[str]) -> None:
    """The reports an entry lists are the ones its steps write, in their order."""
    written = [path for step in job.command for path in _junit_paths(step.argv)]
    if list(job.junit) != written:
        bad.append(
            f"{job.id}: junit lists {', '.join(job.junit) or 'nothing'}, and its steps write"
            f" {', '.join(written) or 'none'}"
        )


def _posture(job: catalogue.Job, marked: frozenset[str], bad: list[str]) -> None:
    """The entry's posture and tiers are this table's, and one that runs a security-marked
    module is security posture."""
    want = _CATALOGUE_JOBS[job.id]
    runs = _runs_marked(job, marked)
    if runs and job.security != "security":
        more = f" and {len(runs) - 3} more" if len(runs) > 3 else ""
        bad.append(
            f"{job.id}: security is {job.security}, but its steps run security-marked modules:"
            f" {', '.join(runs[:3])}{more}"
        )
    if job.tiers != want.tiers:
        bad.append(
            f"{job.id}: tiers are {', '.join(job.tiers)}, and this row says {', '.join(want.tiers)}"
        )
    if job.security != want.security:
        bad.append(f"{job.id}: security is {job.security}, and this row says {want.security}")
    if job.live != want.live:
        bad.append(
            f"{job.id}: live is {str(job.live).lower()}, and this row says {str(want.live).lower()}"
        )
    if str(job.reuse) != want.reuse:
        bad.append(f"{job.id}: reuse is {job.reuse}, and this row says {want.reuse}")


def _catalogue_check(root: Path) -> list[str]:
    try:
        cat = catalogue.read(root)
    except FileNotFoundError:
        return [f"{catalogue.CATALOGUE} is missing"]
    except catalogue.CatalogueError as exc:
        return exc.problems
    jobs: dict[str, Any] = {}
    for rel, prefix in ((_BREADTH, ""),):
        workflow = yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}
        jobs.update({prefix + str(k): v for k, v in (workflow.get("jobs") or {}).items()})
    actions = local_actions.actions_at(root)
    bad: list[str] = []
    pinned = [job.id for job in cat.jobs if job.id in _PINNED_BROWSER]
    if len(pinned) != 1:
        bad.append(
            f"the pinned browser run is {', '.join(pinned) or 'missing'}: the catalogue holds one"
            f" of {', '.join(_PINNED_BROWSER)}, once"
        )
    mapped = {c for entry in _CATALOGUE_JOBS.values() for c in entry.jobs}
    for key in sorted(set(jobs) - mapped - _ORCHESTRATION):
        bad.append(f"{_where(key)} is no catalogue entry's, and not orchestration")
    lines = {c: _job_lines(c, jobs[c], bad) for c in sorted(mapped & set(jobs))}
    ran = {
        tuple(words)
        for c in lines
        for step in jobs[c].get("steps") or []
        if "run" in step
        for _, words in _run_lines(str(step["run"]))
        if words
    }
    for words in sorted(_NOT_COMMAND_LINES - ran):
        bad.append(
            f"this row lists {shlex.join(words)} as a line that validates nothing, and no mapped"
            " workflow job runs it"
        )
    claims: dict[_Line, list[str]] = {line: [] for c in lines for line in lines[c]}
    marked = _marked(root)
    for job in cat.jobs:
        entry = _CATALOGUE_JOBS.get(job.id)
        if entry is None:
            bad.append(f"{job.id}: this row has no line for it")
            continue
        _reports(job, bad)
        _stops_at_a_failure(job, root, bad)
        _posture(job, marked, bad)
        missing = [c for c in entry.jobs if c not in jobs]
        for key in missing:
            bad.append(
                f"{job.id}: maps to {_where(key)}, which {_workflow_name(key)} does not have"
            )
        if missing or not entry.jobs:
            continue
        # An entry that runs in both workflows holds each one's run: lines on its own.
        for prefix in _MAPPED_PREFIXES:
            own = [c for c in entry.jobs if _prefix_of(c) == prefix]
            if own:
                _claim(job, [line for c in own for line in lines[c]], claims, bad)
        _installs(job, [jobs[c] for c in entry.jobs], bad, _names(entry.jobs))
        mine = [line for line, holders in claims.items() if job.id in holders]
        _same_job(job, jobs, mine, bad, actions)
    for line, holders in claims.items():
        if not holders:
            bad.append(
                f"{_where(line.job)} runs {line.text()} ({line.name!r}),"
                " and no entry's command holds it"
            )
    return bad


def _stops_at_a_failure(job: catalogue.Job, root: Path, bad: list[str]) -> None:
    """Each script a script entry runs is one the tree holds, and its first command is
    ``set -eu``, so a command that fails fails the job instead of the next line running."""
    for rel in job.script or ():
        path = root / rel
        if not path.is_file():
            bad.append(f"{job.id}: runs {rel}, which the tree does not hold")
            continue
        lines = shell_installs.read_script(path.read_text(encoding="utf-8")).lines
        if not lines or lines[0][1] != "set -eu":
            first = lines[0][1] if lines else "nothing"
            bad.append(f"{job.id}: {rel} opens with `{first}`, not `set -eu`")


def _catalogue_scaffold(root: Path) -> None:
    """The tree's own breadth.yml and catalogue, which agree, the
    scripts its script entries run, and a marked stand-in for each module the tree marks security,
    so the census finds what the entries' steps run."""
    for rel in (_BREADTH, catalogue.CATALOGUE, _SETUP_ACTION):
        _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))
    for job in catalogue.read(REPO).jobs:
        for rel in job.script or ():
            _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))
    for rel in _tree_marked():
        _write(root, rel, _MARKED)


def _without_merge_list(root: Path) -> None:
    """The tree's catalogue with its [tier.merge] table, the header, its list and its every_push
    line, removed, and the [tier.entry] table, a list of merge-tier jobs, with it."""
    path = root / catalogue.CATALOGUE
    text = path.read_text(encoding="utf-8")
    for table in ("[tier.merge]\n", "[tier.entry]\n"):
        start = text.index(table)
        end = text.index("]\n", text.index("jobs = [", start)) + 2
        if text.startswith("every_push = ", end):
            end = text.index("\n", end) + 1
        text = text[:start] + text[end:]
    _write(root, catalogue.CATALOGUE, text)


def _pinned_named(name: str, new: str, fails: bool = True, expect: str = "") -> Control:
    """The pinned browser run renamed to *new* (empty: the other of its two names) everywhere the
    catalogue names it, from whichever of :data:`_PINNED_BROWSER` the tree's catalogue holds, so
    the plant reads either tree."""

    def plant(root: Path) -> None:
        text = (root / catalogue.CATALOGUE).read_text(encoding="utf-8")
        (old,) = [n for n in _PINNED_BROWSER if f'id = "{n}"' in text]
        (other,) = [n for n in _PINNED_BROWSER if n != old]
        _write(root, catalogue.CATALOGUE, text.replace(f'"{old}"', f'"{new or other}"'))

    return Control(name, plant, fails, expect)


def _plant_files(
    name: str, edits: tuple[tuple[str, str, str], ...], fails: bool = True, expect: str = ""
) -> Control:
    """A violation planted by one edit in each of several files, ``(rel, old, new)``, each of
    which must find its text: for a change the catalogue and its workflow make together."""
    return Control(name, _edits(*edits), fails, expect)


#: collection-parses's one pytest step, as breadth.yml and the catalogue spell it.
_COLLECT_STEP = (
    (_BREADTH, "run: uv run pytest --collect-only -qq"),
    (catalogue.CATALOGUE, '"pytest", "--collect-only", "-qq",'),
)


def _collect_step(yml: str, toml: str) -> tuple[tuple[str, str, str], ...]:
    """collection-parses's pytest step with its ``--collect-only`` replaced in both files."""
    (b, b_old), (c, c_old) = _COLLECT_STEP
    return (
        (b, b_old, b_old.replace(" --collect-only", yml)),
        (c, c_old, c_old.replace(' "--collect-only",', toml)),
    )


_RUNS_MARKED = "collection-parses: security is ordinary, but its steps run security-marked modules"

_RAILS_MACOS_STEP = (
    "        run: uv run pytest -n 2 --dist worksteal -q tests/rails"
    " --junitxml=reports/junit-rails-macos.xml\n"
)
#: collection-parses's one step env, as breadth.yml and the catalogue spell it.
_COLLECT_ENV = (
    (_BREADTH, "          PYTHONPATH: tests\n"),
    (catalogue.CATALOGUE, 'env = { PYTHONPATH = "tests" } },'),
)


def _ubuntu_suite_step_env(root: Path) -> None:
    """suite-ubuntu's pytest step given ``BLOB`` in both files: breadth.yml's as ``${{ matrix.shard
    }}``, an expression GitHub expands in env:, the catalogue's as ``{shard}``."""
    _plant_in_job(
        "",
        _BREADTH,
        "ubuntu",
        "        id: pytest\n",
        "        id: pytest\n        env:\n          BLOB: blob-${{ matrix.shard }}\n",
    ).plant(root)
    step = '"--shard-ids", "{runner_temp}/ids/shard-ids-ubuntu-{shard}.txt"] },'
    _plant(
        "", catalogue.CATALOGUE, step, step.replace("] },", '], env = { BLOB = "blob-{shard}" } },')
    ).plant(root)


def _collect_env(yml: str, toml: str) -> tuple[tuple[str, str, str], ...]:
    """collection-parses's step env with one more variable in each file."""
    (b, b_old), (c, c_old) = _COLLECT_ENV
    return (
        (b, b_old, b_old + f"          {yml}\n"),
        (c, c_old, c_old.replace('"tests" }', f'"tests", {toml} }}')),
    )


EVERY_CATALOGUE_COMMAND_IS_THE_WORKFLOWS = Row(
    name="every catalogue command is the workflow's",
    check=_catalogue_check,
    scaffold=_catalogue_scaffold,
    controls=(
        _pinned_named(
            "the pinned browser run under the other of its two names passes", "", fails=False
        ),
        _pinned_named(
            "the pinned browser run under a third name fails",
            "e2e-other",
            expect="the pinned browser run is missing",
        ),
        _plant_files(
            "collection-parses spelling it --co, which runs no test, passes",
            _collect_step(" --co", ' "--co",'),
            fails=False,
        ),
        _plant_files(
            "collection-parses without --collect-only",
            _collect_step("", ""),
            expect=_RUNS_MARKED,
        ),
        _plant_files(
            "collection-parses with its --co hidden in an echo",
            (
                (
                    _BREADTH,
                    "run: uv run pytest --collect-only -qq",
                    "run: echo --co && uv run pytest -qq",
                ),
                (catalogue.CATALOGUE, ' "--collect-only", "-qq",', ' "-qq",'),
            ),
            expect=_RUNS_MARKED,
        ),
        _plant_files(
            "collection-parses with its --co only the value of -k",
            _collect_step(" -k --co", ' "-k", "--co",'),
            expect=_RUNS_MARKED,
        ),
        _plant(
            "an entry without timeout_min",
            catalogue.CATALOGUE,
            'reuse = "tree"\ntimeout_min = 10\n',
            'reuse = "tree"\n',
            expect="rails: missing timeout_min",
        ),
        _plant(
            "a security entry with reuse = tree",
            catalogue.CATALOGUE,
            'security = "security"\npython = "3.12.15"\ntimeout_min = 55\n',
            'security = "security"\npython = "3.12.15"\nreuse = "tree"\ntimeout_min = 55\n',
            expect="python-suite: reuse is tree, but a security job's evidence is never reused",
        ),
        _plant(
            "a live entry with a TTL",
            catalogue.CATALOGUE,
            'live = true\npython = "3.12.15"\ntimeout_min = 15\n',
            'live = true\nreuse = "ttl:24"\npython = "3.12.15"\ntimeout_min = 15\n',
            expect="audit: reuse is ttl:24, but a live job's evidence is never reused",
        ),
        _plant(
            "a command one token off the workflow's",
            catalogue.CATALOGUE,
            '"worksteal", "-q", "tests/rails", "--junitxml=reports/junit-rails-macos.xml"',
            '"worksteal", "-qq", "tests/rails", "--junitxml=reports/junit-rails-macos.xml"',
            expect="rails-macos: command step 1",
        ),
        _plant(
            "the first random-order seed drawn from another number than the run's",
            _BREADTH,
            "--random-order-seed ${{ github.run_number }}1",
            "--random-order-seed ${{ github.run_attempt }}1",
            expect="random-order: command step 2",
        ),
        _plant(
            "the second random-order seed drawn from another number than the run's",
            _BREADTH,
            "--random-order-seed ${{ github.run_number }}2",
            "--random-order-seed ${{ github.run_attempt }}2",
            expect="random-order: command step 3",
        ),
        _plant(
            "an entry naming a breadth.yml job that does not exist",
            _BREADTH,
            "\n  rails-macos:\n",
            "\n  rails-macos-26:\n",
            expect="rails-macos: maps to breadth.yml job rails-macos, which breadth.yml does not",
        ),
        Control(
            "an entry this row has no line for",
            _append(
                catalogue.CATALOGUE,
                '\n[[job]]\nid = "extra"\ntiers = ["weekly"]\ninputs = ["docs/"]\n'
                'external_inputs = []\ntoolchain = []\ncache = []\nsecurity = "ordinary"\n'
                'timeout_min = 5\ncommand = [{ argv = ["true"] }]\n',
            ),
            expect="extra: this row has no line for it",
        ),
        _plant(
            "a cap that differs from the job's",
            catalogue.CATALOGUE,
            'python = "3.12.15"\ntimeout_min = 25\n',
            'python = "3.12.15"\ntimeout_min = 30\n',
            expect="security: timeout_min is 30, and breadth.yml's cap is 25",
        ),
        _plant(
            "the suite on one platform marked ordinary",
            catalogue.CATALOGUE,
            'security = "security"\npython = "3.12.15"\ntimeout_min = 55\n',
            'security = "ordinary"\npython = "3.12.15"\ntimeout_min = 55\n',
            expect="python-suite: security is ordinary",
        ),
        _plant(
            "a step without its workflow step's environment",
            catalogue.CATALOGUE,
            ', env = { PYTHONPATH = "tests" } },',
            " },",
            expect="collection-parses: command step 1",
        ),
        _plant(
            "the workflow step's environment changed without its entry",
            _BREADTH,
            "          PYTHONPATH: tests\n",
            "          PYTHONPATH: tests:harness\n",
            expect="collection-parses: command step 1",
        ),
        # GitHub expands only `${{ }}` in a step's env:, so `$RUNNER_TEMP` there is the literal
        # text, and the entry's `{runner_temp}` is the real folder when it runs locally.
        _plant_files(
            "a step env that spells a placeholder the shell's way",
            _collect_env("PW_SCRATCH: $RUNNER_TEMP/pw", 'PW_SCRATCH = "{runner_temp}/pw"'),
            expect="collection-parses: command step 1",
        ),
        Control(
            "a step env that spells a placeholder as an expression passes",
            _ubuntu_suite_step_env,
            fails=False,
        ),
        _plant(
            "a merge tier without the rails job",
            catalogue.CATALOGUE,
            'id = "rails"\ntiers = ["change", "entry", "merge"]',
            'id = "rails"\ntiers = ["change"]',
            expect="the merge tier lacks the rails job",
        ),
        Control("the catalogue removed", _remove(catalogue.CATALOGUE), expect="is missing"),
        # Both directions, in order: every validating run: line is one entry's step.
        _plants(
            "an entry missing a step breadth.yml runs: the suite without its pytest",
            catalogue.CATALOGUE,
            (
                (
                    '  { argv = ["uv", "run", "pytest", "-n", "auto", "--dist", "worksteal", "-q",'
                    ' "tests", "--shard-list", "{runner_temp}/shard.txt",'
                    ' "--junitxml=reports/junit-ubuntu.xml", "--shard-ids",'
                    ' "{runner_temp}/ids/shard-ids-ubuntu-{shard}.txt"] },\n',
                    "",
                ),
                (
                    'workers = "auto"\nshards = 4\nenv = { PERSONALCLAW_SKIP_APP_BACKENDS = "1",'
                    ' PERSONALCLAW_TEST_CONTAINER_RUNTIME = "1" }\n'
                    'junit = ["reports/junit-ubuntu.xml"]',
                    'shards = 4\nenv = { PERSONALCLAW_SKIP_APP_BACKENDS = "1",'
                    ' PERSONALCLAW_TEST_CONTAINER_RUNTIME = "1" }\njunit = []',
                ),
            ),
            expect="breadth.yml job ubuntu runs uv run pytest -n auto --dist worksteal -q tests",
        ),
        _plant(
            "a step listed twice",
            catalogue.CATALOGUE,
            '"tests/rails", "--junitxml=reports/junit-rails-macos.xml"] },\n',
            '"tests/rails", "--junitxml=reports/junit-rails-macos.xml"] },\n'
            '  { argv = ["uv", "run", "pytest", "-n", "2", "--dist", "worksteal", "-q",'
            ' "tests/rails", "--junitxml=reports/junit-rails-macos.xml"] },\n',
            expect="rails-macos: command step 2 repeats command step 1",
        ),
        _plant(
            "a gate that a printf pipes into",
            _BREADTH,
            _RAILS_MACOS_STEP,
            _RAILS_MACOS_STEP + "      - name: a new gate\n"
            "        run: printf '%s\\n' \"$PWD\" | uv run python scripts/new_gate.py\n",
            expect="breadth.yml job rails-macos runs printf",
        ),
        _plant(
            "a gate added to an install line",
            _BREADTH,
            "        run: uv sync --locked --extra dev\n",
            "        run: uv sync --locked --extra dev && uv run pytest tests/test_more.py\n",
            expect="runs uv sync --locked --extra dev '&&' uv run pytest tests/test_more.py",
        ),
        _plant(
            "a line no argv can be",
            _BREADTH,
            "        run: uv run python -m harness scan\n",
            "        run: |\n          uv run python -m harness scan\n"
            "          echo it's scanned\n",
            expect='breadth.yml job rails-macos runs "echo it\'s scanned"',
        ),
        # The census: an entry whose steps run a security-marked module is security posture.
        # By the entry's id, not its whole text, so a change to browse-live's inputs or toolchain
        # still takes the plant (#3918: browse-live gains the apps partner).
        _plant_in_entry(
            "the browse proof marked ordinary",
            catalogue.CATALOGUE,
            "browse-live",
            'security = "security"',
            'security = "ordinary"',
            expect="browse-live: security is ordinary, but its steps run security-marked modules",
        ),
        _plant(
            "the security corpus marked ordinary and reused by tree",
            catalogue.CATALOGUE,
            'security = "security"\npython = "3.12.15"\ntimeout_min = 25\n',
            'security = "ordinary"\nreuse = "tree"\npython = "3.12.15"\ntimeout_min = 25\n',
            # The loader refuses it first: security is one of the merge tier's security jobs.
            expect="security: security is ordinary, but its run is the merge tier's security",
        ),
        # The posture and tiers this row holds each entry to.
        _plants(
            "an entry out of the merge tier, and out of its list",
            catalogue.CATALOGUE,
            (
                ('  "audit",\n]', "]"),
                ('  "audit",\n]', "]"),
                ('id = "audit"\ntiers = ["entry", "merge"]', 'id = "audit"\ntiers = ["breadth"]'),
            ),
            expect="audit: tiers are breadth, and this row says entry, merge",
        ),
        _plant(
            "a live entry no longer live",
            catalogue.CATALOGUE,
            'security = "security"\nlive = true\n',
            'security = "security"\n',
            expect="browse-live: live is false, and this row says true",
        ),
        _plant(
            "an ordinary entry turned security",
            catalogue.CATALOGUE,
            'external_inputs = ["package-index"]\ntoolchain = ["uv"]\ncache = ["uv"]\n'
            'security = "ordinary"',
            'external_inputs = ["package-index"]\ntoolchain = ["uv"]\ncache = ["uv"]\n'
            'security = "security"',
            expect="client: security is security, and this row says ordinary",
        ),
        _plant(
            "an entry reused that this row says is never",
            catalogue.CATALOGUE,
            'timeout_min = 45\nafter = ["web-build"]\n',
            'reuse = "tree"\ntimeout_min = 45\nafter = ["web-build"]\n',
            expect="package: reuse is tree, and this row says never",
        ),
        # The loader's rules on what the tree does not pin, the merge tier's list, the worker
        # count and installs, each failing on the real catalogue.
        _plant(
            "a job that reads an unpinned input reused by tree",
            catalogue.CATALOGUE,
            'security = "ordinary"\npython = "3.12.15"\ntimeout_min = 5\n',
            'security = "ordinary"\nreuse = "tree"\npython = "3.12.15"\ntimeout_min = 5\n',
            expect="client: reuse is tree, but it reads package-index, which the tree does not pin",
        ),
        _plant(
            "a merge job the merge tier's list leaves out",
            catalogue.CATALOGUE,
            '  "package",\n',
            "",
            expect="package: its tiers claim merge, and [tier.merge] does not list it",
        ),
        _plant(
            "the merge tier's list naming a job that does not claim merge",
            catalogue.CATALOGUE,
            'id = "audit"\ntiers = ["entry", "merge"]',
            'id = "audit"\ntiers = ["breadth"]',
            expect="audit: [tier.merge] lists it, and it is no job whose tiers claim merge",
        ),
        Control(
            "no merge tier list",
            _without_merge_list,
            expect="the catalogue has no [tier.merge] jobs list",
        ),
        _plant(
            "a pytest -n that is not the job's workers",
            catalogue.CATALOGUE,
            '"-n", "4", "--dist"',
            '"-n", "8", "--dist"',
            expect="rails: a step passes pytest -n 8, and workers is 4",
        ),
        _plant(
            "a pytest -n with no workers declared",
            catalogue.CATALOGUE,
            "timeout_min = 10\nworkers = 4\n",
            "timeout_min = 10\n",
            expect="rails: a step passes pytest -n, so workers is required",
        ),
        _plant(
            "an install as a step",
            catalogue.CATALOGUE,
            '  { argv = ["npm", "run", "typecheck:web"] },\n',
            '  { argv = ["npm", "ci"] },\n  { argv = ["npm", "run", "typecheck:web"] },\n',
            expect="web-unit: command step 1 is an install (npm ci)",
        ),
        _plant(
            "the user-namespace lift as a step, which would run wherever the job runs",
            catalogue.CATALOGUE,
            '  { argv = ["npm", "run", "typecheck:web"] },\n',
            '  { argv = ["sh", "tools/ci/allow_userns.sh"] },\n'
            '  { argv = ["npm", "run", "typecheck:web"] },\n',
            expect="web-unit: command step 1 is an install (sh tools/ci/allow_userns.sh)",
        ),
        _plant(
            "a toolchain install no breadth.yml job of the entry runs",
            catalogue.CATALOGUE,
            'id = "coverage"\ntiers = ["breadth"]\ninputs = [":(glob)**"]\nexternal_inputs = []\n'
            'toolchain = ["uv-sync"]',
            'id = "coverage"\ntiers = ["breadth"]\ninputs = [":(glob)**"]\nexternal_inputs = []\n'
            'toolchain = ["uv-sync", "electron"]',
            expect="coverage: toolchain names electron, and none of its breadth.yml jobs installs",
        ),
        _plant(
            "the audit not live, and reused by tree",
            catalogue.CATALOGUE,
            'live = true\npython = "3.12.15"\ntimeout_min = 15\n',
            'reuse = "tree"\npython = "3.12.15"\ntimeout_min = 15\n',
            expect="audit: reads advisory-feed",
        ),
        # The runner, the Python, the reports, the env and the shards are the jobs'.
        _plant(
            "the macOS suite on a Linux runner",
            catalogue.CATALOGUE,
            'platform = "macos-26"',
            'platform = "ubuntu-24.04"',
            expect="python-suite-macos: platform is ubuntu-24.04, but",
        ),
        _plant(
            "a runner that differs from the job's",
            catalogue.CATALOGUE,
            'id = "coverage"\ntiers = ["breadth"]',
            'id = "coverage"\nplatform = "ubuntu-22.04"\ntiers = ["breadth"]',
            expect="coverage: platform is ubuntu-22.04, and breadth.yml runs it on ubuntu-24.04",
        ),
        _plant(
            "reports that are not the ones its steps write",
            catalogue.CATALOGUE,
            'junit = ["reports/junit-rails-{shard}.xml"]',
            "junit = []",
            expect="rails: junit lists nothing, and its steps write reports/junit-rails-{shard}",
        ),
        _plant(
            "a Python that differs from the job's",
            catalogue.CATALOGUE,
            'platform = "macos-26"\npython = "3.12.15"\nreuse = "tree"\ntimeout_min = 30\n',
            'platform = "macos-26"\npython = "3.12.14"\nreuse = "tree"\ntimeout_min = 30\n',
            expect="rails-macos: python is 3.12.14, and breadth.yml's setup-uv sets 3.12.15 before",
        ),
        _plant(
            "no Python where the job sets one",
            catalogue.CATALOGUE,
            'platform = "macos-26"\npython = "3.12.15"\nreuse = "tree"\ntimeout_min = 30\n',
            'platform = "macos-26"\nreuse = "tree"\ntimeout_min = 30\n',
            expect="rails-macos: python is unset, and breadth.yml's setup-uv sets 3.12.15 before",
        ),
        _plant(
            "a job env that differs from the job's",
            catalogue.CATALOGUE,
            'PERSONALCLAW_TEST_CONTAINER_RUNTIME = "1" }\n'
            'junit = ["reports/junit-rails-macos.xml"',
            'PERSONALCLAW_TEST_CONTAINER_RUNTIME = "0" }\n'
            'junit = ["reports/junit-rails-macos.xml"',
            expect="rails-macos: env is",
        ),
        _plant(
            "a run: line folded over two lines with a backslash still matches",
            _BREADTH,
            "run: uv run pytest -n 2 --dist worksteal -q tests/rails --junitxml",
            "run: |\n          uv run pytest -n 2 --dist worksteal -q tests/rails \\\n"
            "            --junitxml",
            fails=False,
        ),
        # breadth.yml's half (#3909): every breadth job is an entry's, or orchestration.
        _plant(
            "a breadth job no entry holds",
            _BREADTH,
            "jobs:\n  freshness:\n",
            "jobs:\n  nightly-extra:\n    runs-on: ubuntu-24.04\n    steps:\n"
            "      - run: uv run python scripts/nightly_extra.py\n  freshness:\n",
            expect="breadth.yml job nightly-extra is no catalogue entry's, and not orchestration",
        ),
        _plant(
            "a breadth command one token off its entry's",
            _BREADTH,
            '-q tests --shard-list "$RUNNER_TEMP/shard.txt" --junitxml=reports/junit-ubuntu.xml',
            '-qq tests --shard-list "$RUNNER_TEMP/shard.txt" --junitxml=reports/junit-ubuntu.xml',
            expect="suite-ubuntu: command step 2",
        ),
        _plant(
            "a breadth step no entry holds",
            _BREADTH,
            "        run: uv run pytest -n 2 --dist worksteal -q tests/rails"
            " --junitxml=reports/junit-rails-macos.xml\n",
            "        run: uv run pytest -n 2 --dist worksteal -q tests/rails"
            " --junitxml=reports/junit-rails-macos.xml\n"
            "      - name: a new check\n        run: uv run python scripts/new_check.py\n",
            expect="breadth.yml job rails-macos runs uv run python scripts/new_check.py",
        ),
        _plant(
            "a breadth entry's cap that is not its job's",
            catalogue.CATALOGUE,
            'reuse = "tree"\ntimeout_min = 30\n',
            'reuse = "tree"\ntimeout_min = 25\n',
            expect="rails-macos: timeout_min is 25, and breadth.yml's cap is 30",
        ),
        _plant(
            "a breadth entry whose shard count is not its matrix's",
            catalogue.CATALOGUE,
            "workers = 2\nshards = 6\n",
            "workers = 2\nshards = 4\n",
            expect="suite-macos: shards is 4, and breadth.yml runs 6",
        ),
        _plant(
            "a script entry whose step runs another script",
            _BREADTH,
            "        run: sh tools/ci/install_smoke_staged.sh\n",
            "        run: sh tools/ci/install_smoke_other.sh\n",
            expect="install-smoke: command step 2 (.$ sh tools/ci/install_smoke_staged.sh)",
        ),
        _plant(
            "a script entry's script that does not stop at its first failing command",
            "tools/ci/install_smoke_staged.sh",
            "\nset -eu\n",
            "\nset -u\n",
            expect="install-smoke: tools/ci/install_smoke_staged.sh opens with `set -u`, not"
            " `set -eu`",
        ),
        _plant(
            "a script entry whose step runs its script with bash",
            _BREADTH,
            "        run: sh tools/ci/install_smoke_current.sh\n",
            "        run: bash tools/ci/install_smoke_current.sh\n",
            expect="install-smoke: command step 1 (.$ sh tools/ci/install_smoke_current.sh) is no"
            " run: line",
        ),
        _plant(
            "a script entry whose scripts are out of the job's order",
            catalogue.CATALOGUE,
            '  "tools/ci/install_smoke_current.sh",\n  "tools/ci/install_smoke_staged.sh",\n',
            '  "tools/ci/install_smoke_staged.sh",\n  "tools/ci/install_smoke_current.sh",\n',
            expect="install-smoke: command step 2 is out of breadth.yml's order",
        ),
        Control(
            "a closed breadth line no mapped job runs any more",
            _every(
                _BREADTH,
                '        run: uv venv "$RUNNER_TEMP/shard-python"\n',
                '        run: uv venv "$RUNNER_TEMP/a-python"\n',
            ),
            expect="this row lists uv venv '{runner_temp}/shard-python' as a line that validates",
        ),
    ),
)

ROWS = (*ROWS, EVERY_CATALOGUE_COMMAND_IS_THE_WORKFLOWS)


# ── The quarantine is capped, assigned and expiring ─────────────────────────────────────────
#
# The one way a known failure is waived is an entry of tests/quarantine.toml (#3947): at most 10,
# each with an issue, an assignee, a one-line reason and an expiry at most 30 days out, and never
# a rail or a security-marked test. The rules and the session hook live in tests/quarantine.py;
# the suite's session refuses a list that breaks one, and this row holds each rule the source
# shows with a planted entry. What only collection shows (a mark a hook adds, a case that is
# gone) and the guard that reds every other waiver are held by tests/test_quarantine.py.

#: The day the scaffold's list is read on, so its dates do not move with the calendar.
_QUARANTINE_DAY = __import__("datetime").date(2026, 10, 18)
_QUARANTINED = "tests/test_known.py"
_QUARANTINE_MIXED = "tests/test_mixed.py"


def _quarantine_entry(
    nodeid: str | None,
    *,
    issue: str = "4000",
    assignee: str = '"someone"',
    expires: str = "2026-10-28",
    reason: str = '"fails one run in five on a slow runner"',
    extra: str = "",
) -> str:
    named = f'nodeid = "{nodeid}"\n' if nodeid is not None else ""
    return (
        f"[[entry]]\n{named}issue = {issue}\nassignee = {assignee}\n"
        f"expires = {expires}\nreason = {reason}\n{extra}\n"
    )


def _quarantine_listing(text: str) -> Callable[[Path], None]:
    """Replace the whole list, for a plant that is not one more entry."""
    return lambda root: _write(root, quarantine.LIST, text)


def _quarantine_scaffold(root: Path) -> None:
    """A tree where the rule holds: one listed test, a rail, a security-marked module, twelve
    plain tests to plant entries for, and a module with a test of each other kind."""
    body = "".join(f"def test_{n}():\n    pass\n\n\n" for n in range(12))
    _write(root, _QUARANTINED, body)
    _write(root, "tests/rails/test_rail.py", "def test_it():\n    pass\n")
    _write(root, "tests/test_guard.py", _MARKED)
    _write(
        root,
        _QUARANTINE_MIXED,
        "import pytest\n\n\n"
        "@pytest.mark.security\ndef test_guarded():\n    pass\n\n\n"
        "@pytest.mark.security\nclass TestGuarded:\n    def test_it(self):\n        pass\n\n\n"
        "class TestMarked:\n    pytestmark = [pytest.mark.security]\n\n"
        "    def test_it(self):\n        pass\n\n\n"
        "class TestAnnotated:\n    pytestmark: list = [pytest.mark.security]\n\n"
        "    def test_it(self):\n        pass\n\n\n"
        '@pytest.mark.parametrize("n", [1, 2])\ndef test_cases(n):\n    pass\n\n\n'
        "def helper():\n    pass\n",
    )
    _write(root, quarantine.LIST, _quarantine_entry(f"{_QUARANTINED}::test_0"))


def _quarantine_check(root: Path) -> list[str]:
    day = quarantine.today() if root == REPO else _QUARANTINE_DAY
    return quarantine.problems(root, day)


def _plant_entry(name: str, nodeid: str | None, expect: str, **fields: str) -> Control:
    return Control(
        name, _append(quarantine.LIST, _quarantine_entry(nodeid, **fields)), expect=expect
    )


_ONE = f"{_QUARANTINED}::test_1"

QUARANTINE_IS_CAPPED_ASSIGNED_AND_EXPIRING = Row(
    name="the-quarantine-is-capped-assigned-and-expiring",
    check=_quarantine_check,
    scaffold=_quarantine_scaffold,
    controls=(
        Control(
            "an eleventh entry",
            _append(
                quarantine.LIST,
                "".join(_quarantine_entry(f"{_QUARANTINED}::test_{n}") for n in range(1, 11)),
            ),
            expect="holds 11 entries",
        ),
        _plant_entry("an expired entry", _ONE, "expired on 2026-10-17", expires="2026-10-17"),
        _plant_entry(
            "an entry on its expiry date", _ONE, "expired on 2026-10-18", expires="2026-10-18"
        ),
        _plant_entry(
            "an entry more than 30 days out", _ONE, "more than 30 days out", expires="2026-11-18"
        ),
        _plant_entry(
            "an expiry written as text", _ONE, "expires must be a date", expires='"2026-10-28"'
        ),
        _plant_entry(
            "an expiry that is a date and a time",
            _ONE,
            "expires must be a date",
            expires="2026-10-28T00:00:00Z",
        ),
        _plant_entry("an entry without an issue", _ONE, "no issue", issue="0"),
        _plant_entry("an issue that is true", _ONE, "no issue", issue="true"),
        _plant_entry("an issue below one", _ONE, "no issue", issue="-5"),
        _plant_entry("an entry without an assignee", _ONE, "no assignee", assignee='""'),
        _plant_entry("an assignee that is not a login", _ONE, "no assignee", assignee='"-someone"'),
        _plant_entry("an assignee and a newline", _ONE, "no assignee", assignee='"someone\\n"'),
        _plant_entry("an entry without a reason", _ONE, "no reason", reason='" "'),
        _plant_entry("a reason of two lines", _ONE, "one line", reason='"slow\\nand flaky"'),
        _plant_entry(
            "an entry with an unknown field", _ONE, "unknown field owner", extra='owner = "x"\n'
        ),
        _plant_entry("an entry without a nodeid", None, "no nodeid"),
        Control(
            "a test listed twice",
            _append(quarantine.LIST, _quarantine_entry(f"{_QUARANTINED}::test_0")),
            expect="listed twice",
        ),
        Control(
            "a key the list does not know",
            _quarantine_listing('owner = "x"\n' + _quarantine_entry(f"{_QUARANTINED}::test_0")),
            expect="unknown key owner",
        ),
        Control(
            "an entry that is not an array of tables",
            _quarantine_listing(_quarantine_entry(_ONE).replace("[[entry]]", "[entry]")),
            expect="array of tables",
        ),
        Control(
            "a list that does not parse",
            _append(quarantine.LIST, "[[entry]\n"),
            expect="does not parse",
        ),
        _plant_entry("an entry for a rail", "tests/rails/test_rail.py::test_it", "is a rail"),
        _plant_entry(
            "an entry for a test of a security-marked module",
            "tests/test_guard.py::test_it",
            "security-marked (its module)",
        ),
        _plant_entry(
            "an entry for a security-marked test",
            f"{_QUARANTINE_MIXED}::test_guarded",
            "is security-marked: a security test",
        ),
        _plant_entry(
            "an entry for a test of a security-decorated class",
            f"{_QUARANTINE_MIXED}::TestGuarded::test_it",
            "security-marked (its class)",
        ),
        _plant_entry(
            "an entry for a test of a class whose pytestmark is security",
            f"{_QUARANTINE_MIXED}::TestMarked::test_it",
            "security-marked (its class)",
        ),
        _plant_entry(
            "an entry for a test of a class whose annotated pytestmark is security",
            f"{_QUARANTINE_MIXED}::TestAnnotated::test_it",
            "security-marked (its class)",
        ),
        _plant_entry(
            "an entry for a test that is gone", f"{_QUARANTINED}::test_renamed", "no such test"
        ),
        _plant_entry(
            "an entry whose file is spelt in another case",
            "tests/test_Known.py::test_1",
            "does not exist",
        ),
        _plant_entry(
            "an entry for a function that is not a test",
            f"{_QUARANTINE_MIXED}::helper",
            "not a test pytest collects",
        ),
        _plant_entry(
            "an entry for a parametrized test without its case",
            f"{_QUARANTINE_MIXED}::test_cases",
            "without its case",
        ),
        Control(
            "ten valid entries pass: the cap is ten",
            _append(
                quarantine.LIST,
                "".join(_quarantine_entry(f"{_QUARANTINED}::test_{n}") for n in range(1, 10)),
            ),
            fails=False,
        ),
        Control(
            "an entry expiring in exactly 30 days passes",
            _append(quarantine.LIST, _quarantine_entry(_ONE, expires="2026-11-17")),
            fails=False,
        ),
        Control(
            "an entry for one case of a parametrized test passes",
            _append(quarantine.LIST, _quarantine_entry(f"{_QUARANTINE_MIXED}::test_cases[1]")),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, QUARANTINE_IS_CAPPED_ASSIGNED_AND_EXPIRING)


# ── Each runner retries once, in a fresh process, on the same budget ───────────────────────
#
# One contract for pytest, Vitest and Playwright (#3947), one row per runner: a failed test is run
# once more, in a new process, under the first attempt's per-test time budget, only to classify
# it; a test that passes only on that retry still reds the job. pytest's and Vitest's retry is
# run-job's diagnostic rerun (tools/ci/jobs.py), which tests/test_ci_jobs_rerun.py and
# tests/test_ci_jobs_web_rerun.py hold; Playwright's is its own `retries: 1`, a new worker under
# the same test timeout, with `failOnFlakyTests`. Each row holds what the tree must not add for
# its runner, wherever that runner reads it: a second retry, a same-process retry or a changed
# budget, and the report the runner's classification is read from. Each rule has a planted break,
# each listed option, plugin and key its own.

_PYPROJECT = "pyproject.toml"
_UV_LOCK = "uv.lock"
#: The packages with a pytest and dependencies of their own (the client's: no lock, its own venv).
_PACKAGE_PYPROJECTS = "packages/*/pyproject.toml"
_VITEST_CONFIG = "web/vitest.config.ts"
_PLAYWRIGHT_CONFIG = "web/playwright.config.ts"

#: pytest options that retry or repeat a test in the same process, or skip to the last failures.
_PYTEST_RETRIES = (
    "--reruns",
    "--reruns-delay",
    "--count",
    "--lf",
    "--last-failed",
    "--sw",
    "--stepwise",
)
#: The names ``-p`` loads a retry or repeat plugin by.
_PYTEST_RETRY_PLUGINS = ("rerunfailures", "flaky", "pytest_repeat", "repeat")
#: The distributions of retry and repeat plugins. Installed, each loads itself with no ``-p``, and
#: pytest-rerunfailures then reruns a test that carries its own ``flaky(reruns=N)`` mark.
_PYTEST_RETRY_DISTS = (
    "pytest-rerunfailures",
    "flaky",
    "pytest-repeat",
    "pytest-retry",
    "pytest-flakefinder",
)
#: Vitest options that retry, repeat or retime a test.
_VITEST_RETRIES = ("--retry", "--repeats", "--testTimeout", "--hookTimeout")
#: Vitest config keys that retry or repeat a test in the same worker, at its top or in a project.
_VITEST_CONFIG_RETRIES = ("retry", "repeats")
#: The npm scripts the Vitest step runs (``npm run test:web``, then the web workspace's ``test``),
#: by file, and their text: an option there is the step's and never its rerun's.
_VITEST_SCRIPTS = (
    ("package.json", "test:web", "npm run test --workspace=web"),
    ("web/package.json", "test", "vitest run"),
)
#: How Vitest's config adds the JUnit report under CI alone: to Vitest's own reporters, at
#: :data:`run_jobs.VITEST_JUNIT` from the repository root.
_VITEST_REPORTERS = "reporters: [...configDefaults.reporters, 'junit'],"
_VITEST_OUTPUT = (
    "outputFile: { junit: join(WEB, "
    + ", ".join(f"'{part}'" for part in ("..", *run_jobs.VITEST_JUNIT.split("/")))
    + ") },"
)
_VITEST_UNDER_CI = re.compile(
    r"\.\.\.\(process\.env\.CI\s*\?\s*\{\s*"
    + re.escape(_VITEST_REPORTERS)
    + r"\s*"
    + re.escape(_VITEST_OUTPUT)
    + r"\s*\}\s*:\s*\{\}\s*\),"
)
#: Playwright options that change its one retry or its test timeout.
_PLAYWRIGHT_RETRIES = ("--retries", "--repeat-each", "--timeout", "--fail-on-flaky-tests")
#: The config's one retry, and the flag that reds a retried pass, each a top-level line.
_PLAYWRIGHT_RETRY = "  retries: process.env.CI ? 1 : 0,"
_PLAYWRIGHT_FLAKY = "  failOnFlakyTests: !!process.env.CI,"


def _option_names(argv: tuple[str, ...]) -> set[str]:
    return {a.split("=", 1)[0] for a in argv if a.startswith("-")}


def _runner_steps(root: Path) -> list[tuple[str, tuple[str, ...], dict[str, str]]]:
    data = tomllib.loads((root / catalogue.CATALOGUE).read_text(encoding="utf-8"))
    return [
        (str(job.get("id")), tuple(step.get("argv") or ()), dict(step.get("env") or {}))
        for job in data.get("job") or []
        for step in job.get("command") or []
    ]


def _dist(requirement: str) -> str:
    """A requirement's distribution name, normalised as an index normalises it."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""


def _pytest_runner_check(root: Path) -> list[str]:
    bad: list[str] = []
    runs: list[tuple[str, tuple[str, ...]]] = []
    held: list[tuple[str, str]] = []
    for path in [root / _PYPROJECT, *sorted(root.glob(_PACKAGE_PYPROJECTS))]:
        rel = path.relative_to(root).as_posix()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        addopts = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", [])
        opts = tuple(addopts if isinstance(addopts, list) else str(addopts).split())
        runs.append((f"{rel}'s addopts", ("pytest", *opts)))
        project = data.get("project", {})
        held += [(rel, r) for r in project.get("dependencies", [])]
        for extra, reqs in project.get("optional-dependencies", {}).items():
            held += [(f"{rel}'s {extra} extra", r) for r in reqs]
        for group, reqs in data.get("dependency-groups", {}).items():
            held += [(f"{rel}'s {group} group", r) for r in reqs if isinstance(r, str)]
    for where, argv in [*runs, *((job_id, argv) for job_id, argv, _ in _runner_steps(root))]:
        if "pytest" not in argv:
            continue
        for name in sorted(_option_names(argv) & set(_PYTEST_RETRIES)):
            bad.append(f"pytest: {where} passes {name}, a second retry in the same process")
        plugins = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-p"]
        for plugin in plugins:
            if plugin in _PYTEST_RETRY_PLUGINS:
                bad.append(f"pytest: {where} loads {plugin}, a second retry")
    lock = root / _UV_LOCK
    if not lock.is_file():
        bad.append(f"pytest: there is no {_UV_LOCK} to read the installed plugins from")
    else:
        packages = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
        held += [(_UV_LOCK, str(p.get("name", ""))) for p in packages]
    retry_dists = {_dist(d) for d in _PYTEST_RETRY_DISTS}
    for where, requirement in held:
        if _dist(requirement) in retry_dists:
            bad.append(
                f"pytest: {where} holds {_dist(requirement)}, a retry plugin that loads itself"
            )
    return bad


def _vitest_runner_check(root: Path) -> list[str]:
    bad: list[str] = []
    steps = _runner_steps(root)
    for job_id, argv, _ in steps:
        if "vitest" in argv[:3]:
            for name in sorted(_option_names(argv) & set(_VITEST_RETRIES)):
                bad.append(f"vitest: {job_id} passes {name}, which retries or retimes a test")
    if run_jobs.VITEST_STEP not in [argv for job_id, argv, _ in steps if job_id == "web-unit"]:
        bad.append(f"vitest: web-unit no longer runs {shlex.join(run_jobs.VITEST_STEP)}")
    for rel, script, want in _VITEST_SCRIPTS:
        path = root / rel
        if not path.is_file():
            bad.append(f"vitest: there is no {rel} to read the step's {script} script from")
            continue
        scripts = json.loads(path.read_text(encoding="utf-8")).get("scripts", {})
        if scripts.get(script) != want:
            bad.append(
                f"vitest: {rel}'s {script} script is {scripts.get(script)!r}, not {want!r}: the"
                " step runs it, and an option there is the step's and never its rerun's"
            )
    vitest = (root / _VITEST_CONFIG).read_text(encoding="utf-8")
    for key in _VITEST_CONFIG_RETRIES:
        if re.search(rf"\b{key}\s*:", vitest):
            bad.append(f"vitest: its config sets {key}, a retry in the same worker")
    if "'junit'" not in vitest:
        bad.append("vitest: its config writes no JUnit report, so no rerun can read one")
    elif _VITEST_REPORTERS not in vitest:
        bad.append(
            "vitest: its config's reporters are not Vitest's own and junit, so one of Vitest's"
            " own (the github-actions reporter's failure annotations) is dropped"
        )
    elif _VITEST_OUTPUT not in vitest:
        bad.append(f"vitest: its config's JUnit report is not {run_jobs.VITEST_JUNIT}")
    elif not _VITEST_UNDER_CI.search(vitest):
        bad.append("vitest: its config sets its reporters other than under CI alone")
    return bad


def _playwright_runner_check(root: Path) -> list[str]:
    bad: list[str] = []
    for job_id, argv, env in _runner_steps(root):
        if tuple(argv[:3]) != run_jobs.PLAYWRIGHT_STEP:
            continue
        for name in sorted(_option_names(argv) & set(_PLAYWRIGHT_RETRIES)):
            bad.append(f"playwright: {job_id} passes {name}, over the config's one retry")
        if run_jobs.PLAYWRIGHT_REPORT_ENV not in env:
            bad.append(
                f"playwright: {job_id} sets no {run_jobs.PLAYWRIGHT_REPORT_ENV}, so its"
                " classification has no report to be read from"
            )
    playwright = (root / _PLAYWRIGHT_CONFIG).read_text(encoding="utf-8")
    lines = playwright.splitlines()
    if _PLAYWRIGHT_RETRY not in lines:
        bad.append("playwright: retries is not one under CI and none locally")
    if len(re.findall(r"\bretries\s*:", playwright)) > 1:
        bad.append("playwright: its config sets retries more than once, a project's own included")
    if _PLAYWRIGHT_FLAKY not in lines:
        bad.append("playwright: failOnFlakyTests is not set under CI, so a retried pass is green")
    if re.search(r"\brepeatEach\s*:", playwright):
        bad.append("playwright: its config sets repeatEach, which runs each test again")
    if re.search(r"^\s*timeout\s*:", playwright.split("projects:", 1)[0], re.M):
        bad.append("playwright: its config sets a test timeout of its own")
    return bad


_RUNNER_PYPROJECT = (
    '[project]\nname = "planted"\ndependencies = ["aiohttp>=3"]\n\n'
    "[project.optional-dependencies]\n"
    'dev = [\n    "pytest>=7",\n    "pytest-xdist>=3",\n]\n\n'
    '[tool.pytest.ini_options]\naddopts = ["--timeout=30"]\n'
)
_RUNNER_PACKAGE = "packages/client/pyproject.toml"
_RUNNER_PACKAGE_TOML = (
    '[project]\nname = "client"\n\n[project.optional-dependencies]\ndev = ["pytest>=8.0"]\n\n'
    '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
)
_RUNNER_LOCK = (
    'version = 1\n\n[[package]]\nname = "pytest"\nversion = "8.0.0"\n\n'
    '[[package]]\nname = "pytest-xdist"\nversion = "3.0.0"\n'
)
_RUNNER_CATALOGUE = (
    '[[job]]\nid = "suite"\ncommand = [\n'
    '  { argv = ["uv", "run", "pytest", "-n", "auto", "tests", "--junitxml=reports/j.xml"] },\n'
    "]\n\n"
    '[[job]]\nid = "web-unit"\ncommand = [\n  { argv = ["npm", "run", "test:web"] },\n]\n\n'
    '[[job]]\nid = "e2e"\ncommand = [\n'
    '  { argv = ["npx", "playwright", "test", "--grep-invert", "@visual"], workdir = "web",'
    ' env = { PLAYWRIGHT_JSON_OUTPUT_NAME = "e2e-report.json" } },\n]\n'
)
_RUNNER_VITEST = (
    "export default defineConfig({\n  test: {\n    testTimeout: 20_000,\n"
    "    ...(process.env.CI\n      ? {\n"
    f"          {_VITEST_REPORTERS}\n          {_VITEST_OUTPUT}\n"
    "        }\n      : {}),\n"
    "    projects: [\n      {\n        extends: true,\n        test: { name: 'dom' },\n      },\n"
    "    ],\n  },\n})\n"
)
_RUNNER_PLAYWRIGHT = (
    f"export default defineConfig({{\n{_PLAYWRIGHT_RETRY}\n{_PLAYWRIGHT_FLAKY}\n"
    "  projects: [\n    {\n      name: 'chromium',\n    },\n  ],\n"
    "  webServer: [{ command: 'serve', timeout: 60_000 }],\n})\n"
)


def _runners_scaffold(root: Path) -> None:
    _write(root, _PYPROJECT, _RUNNER_PYPROJECT)
    _write(root, _UV_LOCK, _RUNNER_LOCK)
    _write(root, _RUNNER_PACKAGE, _RUNNER_PACKAGE_TOML)
    _write(root, catalogue.CATALOGUE, _RUNNER_CATALOGUE)
    for rel, script, text in _VITEST_SCRIPTS:
        _write(root, rel, json.dumps({"scripts": {script: text}}, indent=2) + "\n")
    _write(root, _VITEST_CONFIG, _RUNNER_VITEST)
    _write(root, _PLAYWRIGHT_CONFIG, _RUNNER_PLAYWRIGHT)


def _edit(rel: str, old: str, new: str) -> Callable[[Path], None]:
    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        assert old in text, f"the plant's anchor is not in {rel}: {old!r}"
        _write(root, rel, text.replace(old, new, 1))

    return plant


def _another_job(argv: Sequence[str]) -> Callable[[Path], None]:
    return _append(
        catalogue.CATALOGUE,
        f'\n[[job]]\nid = "other"\ncommand = [\n  {{ argv = {json.dumps(list(argv))} }},\n]\n',
    )


#: The members of the lists above, each written out again for its own planted break: read from
#: the lists themselves, a member dropped from one would drop its plant with it, unseen.
_PLANTED_PYTEST_RETRIES = (
    "--reruns",
    "--reruns-delay",
    "--count",
    "--lf",
    "--last-failed",
    "--sw",
    "--stepwise",
)
_PLANTED_PYTEST_RETRY_PLUGINS = ("rerunfailures", "flaky", "pytest_repeat", "repeat")
_PLANTED_PYTEST_RETRY_DISTS = (
    "pytest-rerunfailures",
    "flaky",
    "pytest-repeat",
    "pytest-retry",
    "pytest-flakefinder",
)
_PLANTED_VITEST_RETRIES = ("--retry", "--repeats", "--testTimeout", "--hookTimeout")
_PLANTED_VITEST_CONFIG_RETRIES = ("retry", "repeats")
_PLANTED_PLAYWRIGHT_RETRIES = ("--retries", "--repeat-each", "--timeout", "--fail-on-flaky-tests")

_PYTEST_STEP = '"tests", "--junitxml=reports/j.xml"'
_DEV_EXTRA = '    "pytest-xdist>=3",\n'
_PW_STEP = '"--grep-invert", "@visual"]'
_VITEST_PROJECT = "test: { name: 'dom' }"

PYTEST_RETRIES_ONCE_FRESH_ON_ITS_BUDGET = Row(
    name="pytest-retries-once-in-a-fresh-process-on-the-same-budget",
    check=_pytest_runner_check,
    scaffold=_runners_scaffold,
    controls=(
        Control(
            "a second retry in addopts",
            _edit(_PYPROJECT, '"--timeout=30"', '"--timeout=30", "--reruns=2"'),
            expect="pyproject.toml's addopts passes --reruns,",
        ),
        Control(
            "a second retry in a package's addopts",
            _edit(
                _RUNNER_PACKAGE,
                'testpaths = ["tests"]\n',
                'testpaths = ["tests"]\naddopts = "-q --reruns 2"\n',
            ),
            expect=f"{_RUNNER_PACKAGE}'s addopts passes --reruns,",
        ),
        Control(
            "a retry plugin in a package's dev extra",
            _edit(_RUNNER_PACKAGE, 'dev = ["pytest>=8.0"]', 'dev = ["pytest>=8.0", "flaky>=3"]'),
            expect=f"{_RUNNER_PACKAGE}'s dev extra holds flaky,",
        ),
        *(
            Control(
                f"a step that passes {name}",
                _edit(catalogue.CATALOGUE, _PYTEST_STEP, f'"{name}", "1", {_PYTEST_STEP}'),
                expect=f"suite passes {name},",
            )
            for name in _PLANTED_PYTEST_RETRIES
        ),
        *(
            Control(
                f"a step that loads {plugin}",
                _edit(catalogue.CATALOGUE, _PYTEST_STEP, f'"-p", "{plugin}", {_PYTEST_STEP}'),
                expect=f"suite loads {plugin},",
            )
            for plugin in _PLANTED_PYTEST_RETRY_PLUGINS
        ),
        Control(
            "a step that blocks a retry plugin passes",
            _edit(catalogue.CATALOGUE, _PYTEST_STEP, f'"-p", "no:rerunfailures", {_PYTEST_STEP}'),
            fails=False,
        ),
        *(
            Control(
                f"{dist} in the dev extra",
                _edit(_PYPROJECT, _DEV_EXTRA, f'{_DEV_EXTRA}    "{dist}>=1",\n'),
                expect=f"dev extra holds {dist},",
            )
            for dist in _PLANTED_PYTEST_RETRY_DISTS
        ),
        Control(
            "a retry plugin in a dependency group, spelt otherwise",
            _append(_PYPROJECT, '\n[dependency-groups]\nci = ["Pytest_RerunFailures==14"]\n'),
            expect="ci group holds pytest-rerunfailures,",
        ),
        *(
            Control(
                f"{dist} in the lock",
                _append(_UV_LOCK, f'\n[[package]]\nname = "{dist}"\nversion = "1.0"\n'),
                expect=f"{_UV_LOCK} holds {dist},",
            )
            for dist in _PLANTED_PYTEST_RETRY_DISTS
        ),
        Control(
            "a plugin that only times a test passes",
            _edit(_PYPROJECT, _DEV_EXTRA, f'{_DEV_EXTRA}    "pytest-timeout>=2",\n'),
            fails=False,
        ),
        Control(
            "no lock to read",
            lambda root: (root / _UV_LOCK).unlink(),
            expect=f"no {_UV_LOCK}",
        ),
    ),
)

VITEST_RETRIES_ONCE_FRESH_ON_ITS_BUDGET = Row(
    name="vitest-retries-once-in-a-fresh-process-on-the-same-budget",
    check=_vitest_runner_check,
    scaffold=_runners_scaffold,
    controls=(
        *(
            Control(
                f"a step that passes {name}",
                _another_job(["npx", "vitest", "run", f"{name}=2"]),
                expect=f"other passes {name},",
            )
            for name in _PLANTED_VITEST_RETRIES
        ),
        Control(
            "a step that runs vitest through npm exec",
            _another_job(["npm", "exec", "vitest", "--", "run", "--retry=2"]),
            expect="other passes --retry,",
        ),
        *(
            Control(
                f"the config sets {key}",
                _edit(_VITEST_CONFIG, "    testTimeout:", f"    {key}: 1,\n    testTimeout:"),
                expect=f"its config sets {key},",
            )
            for key in _PLANTED_VITEST_CONFIG_RETRIES
        ),
        *(
            Control(
                f"a project sets {key}",
                _edit(_VITEST_CONFIG, _VITEST_PROJECT, f"test: {{ name: 'dom', {key}: 2 }}"),
                expect=f"its config sets {key},",
            )
            for key in _PLANTED_VITEST_CONFIG_RETRIES
        ),
        Control(
            "the root script passes a retry to the workspace",
            _edit(
                "package.json",
                '"npm run test --workspace=web"',
                '"npm run test --workspace=web -- --retry=2"',
            ),
            expect="package.json's test:web script",
        ),
        Control(
            "the web script retries",
            _edit("web/package.json", '"vitest run"', '"vitest run --retry=2"'),
            expect="web/package.json's test script",
        ),
        Control(
            "no web workspace manifest to read the script from",
            lambda root: (root / "web/package.json").unlink(),
            expect="there is no web/package.json",
        ),
        Control(
            "the web script retimes",
            _edit("web/package.json", '"vitest run"', '"vitest run --testTimeout=120000"'),
            expect="web/package.json's test script",
        ),
        Control(
            "no JUnit report under CI",
            _edit(_VITEST_CONFIG, ", 'junit']", "]"),
            expect="writes no JUnit report",
        ),
        Control(
            "Vitest's own reporters dropped",
            _edit(_VITEST_CONFIG, "[...configDefaults.reporters, 'junit']", "['default', 'junit']"),
            expect="are not Vitest's own and junit",
        ),
        Control(
            "the report under another name",
            _edit(_VITEST_CONFIG, "'junit-web-unit.xml'", "'junit-web.xml'"),
            expect=f"is not {run_jobs.VITEST_JUNIT}",
        ),
        Control(
            "the report under web/reports/",
            _edit(_VITEST_CONFIG, "join(WEB, '..', 'reports',", "join(WEB, 'reports',"),
            expect=f"is not {run_jobs.VITEST_JUNIT}",
        ),
        Control(
            "the reporters set locally too",
            _edit(_VITEST_CONFIG, "...(process.env.CI", "...(true"),
            expect="other than under CI alone",
        ),
        Control(
            "web-unit runs it another way",
            _edit(catalogue.CATALOGUE, '"npm", "run", "test:web"', '"npm", "run", "test"'),
            expect="no longer runs",
        ),
    ),
)

PLAYWRIGHT_RETRIES_ONCE_FRESH_ON_ITS_BUDGET = Row(
    name="playwright-retries-once-in-a-fresh-process-on-the-same-budget",
    check=_playwright_runner_check,
    scaffold=_runners_scaffold,
    controls=(
        *(
            Control(
                f"a step that passes {name}",
                _edit(catalogue.CATALOGUE, _PW_STEP, f'"{name}", "3", {_PW_STEP}'),
                expect=f"e2e passes {name},",
            )
            for name in _PLANTED_PLAYWRIGHT_RETRIES
        ),
        Control(
            "a step with no JSON report",
            _edit(
                catalogue.CATALOGUE,
                ', env = { PLAYWRIGHT_JSON_OUTPUT_NAME = "e2e-report.json" }',
                "",
            ),
            expect="sets no PLAYWRIGHT_JSON_OUTPUT_NAME",
        ),
        Control(
            "two retries",
            _edit(_PLAYWRIGHT_CONFIG, "CI ? 1 : 0", "CI ? 2 : 0"),
            expect="retries is not one",
        ),
        Control(
            "a project's own retries",
            _edit(
                _PLAYWRIGHT_CONFIG,
                "      name: 'chromium',\n",
                "      name: 'chromium',\n      retries: 3,\n",
            ),
            expect="sets retries more than once",
        ),
        Control(
            "failOnFlakyTests off",
            _edit(_PLAYWRIGHT_CONFIG, f"{_PLAYWRIGHT_FLAKY}\n", ""),
            expect="failOnFlakyTests is not set",
        ),
        Control(
            "a retried pass green under CI",
            _edit(
                _PLAYWRIGHT_CONFIG, "failOnFlakyTests: !!process.env.CI", "failOnFlakyTests: false"
            ),
            expect="failOnFlakyTests is not set",
        ),
        Control(
            "repeatEach in the config",
            _edit(_PLAYWRIGHT_CONFIG, "  projects:", "  repeatEach: 2,\n  projects:"),
            expect="sets repeatEach",
        ),
        Control(
            "repeatEach in a project",
            _edit(
                _PLAYWRIGHT_CONFIG,
                "      name: 'chromium',\n",
                "      name: 'chromium',\n      repeatEach: 2,\n",
            ),
            expect="sets repeatEach",
        ),
        Control(
            "a longer test timeout in the config",
            _edit(_PLAYWRIGHT_CONFIG, "  projects:", "  timeout: 120_000,\n  projects:"),
            expect="a test timeout of its own",
        ),
    ),
)

ROWS = (
    *ROWS,
    PYTEST_RETRIES_ONCE_FRESH_ON_ITS_BUDGET,
    VITEST_RETRIES_ONCE_FRESH_ON_ITS_BUDGET,
    PLAYWRIGHT_RETRIES_ONCE_FRESH_ON_ITS_BUDGET,
)


# ── No candidate job posts the merge verdict (#3915) ──────────────────────────────────────────
#
# `merge-verdict` is the one check a pull request is to be required to pass, and only the default
# branch's controller posts it (#3910). A workflow that runs a candidate's code
# (`_runs_candidate_code`: a pull request's run, a push to a branch other than `main`, and every
# workflow either calls) could post its own green under that name: as a job whose id or name it
# is, or from a step that creates a check run or a commit status by that name. The row reads each
# such workflow's keys and values for the name as a whole word, in any case, and resolves a job
# name built from its matrix leg by leg. A name it cannot resolve (a matrix that is an expression,
# a matrix value that is one, or any other expression) is refused: it could be `merge-verdict`.
# A trusted workflow, and a push to `main`, may post it. That only the controller does is #3910's
# to hold, once the controller's workflow exists.

MERGE_VERDICT = "merge-verdict"
_MERGE_VERDICT_WORD = re.compile(r"(?<![\w-])merge-verdict(?![\w-])", re.I)
_MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.(\w+)\s*\}\}")


def _texts(value: object) -> Iterator[str]:
    """Every key and value in a workflow's data, as text. A key PyYAML reads as a date or a
    number is still a key, so it is read as text, never dumped."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)
    elif value is not None:
        yield str(value)


def _names_merge_verdict(value: object) -> bool:
    return any(_MERGE_VERDICT_WORD.search(text) for text in _texts(value))


def _unresolved_name(job: dict[str, Any]) -> str:
    """Why a job's ``name`` cannot be read leg by leg, or ``""`` when every leg resolves."""
    name = str(job.get("name", ""))
    if "${{" not in name:
        return ""
    if "${{" in _MATRIX_REF.sub("", name):
        return "an expression other than a matrix value"
    return _matrix_gap(job, name)


def _matrix_gap(job: dict[str, Any], text: str, keys: Iterable[str] | None = None) -> str:
    """Why ``text``'s ``${{ matrix.<key> }}`` (or the ``keys`` given) cannot be read from a
    literal matrix, or ``""``."""
    matrix = (job.get("strategy") or {}).get("matrix")
    if not isinstance(matrix, dict):
        return "a matrix that is an expression"
    include = matrix.get("include", [])
    if not isinstance(include, list):
        return "an include that is an expression"
    for key in _MATRIX_REF.findall(text) if keys is None else keys:
        values = matrix.get(key)
        given = list(values) if isinstance(values, list) else []
        given += [e[key] for e in include if isinstance(e, dict) and key in e]
        if (values is not None and not isinstance(values, list)) or not given:
            return f"matrix.{key}, which the matrix gives no literal value"
        if any("${{" in str(v) for v in given):
            return f"matrix.{key}, which the matrix gives as an expression"
    return ""


def merge_verdict_violations(tree: Tree) -> list[str]:
    """No job of a workflow that runs candidate code is, or names, the merge verdict."""
    workflows = [(name, wf if isinstance(wf, dict) else {}) for name, wf in _workflows(tree)]
    candidates = _runs_candidate_code(workflows)
    bad: list[str] = []
    for name, workflow in workflows:
        if name not in candidates:
            continue
        outside = {k: v for k, v in workflow.items() if k != "jobs"}
        if _names_merge_verdict(outside):
            bad.append(f"{name} runs candidate code and names {MERGE_VERDICT} outside its jobs")
        for job_id, job in _trust_jobs(workflow).items():
            where = f"{name}:{job_id}"
            job = _literal_matrix(tree, name, job)
            unresolved = _unresolved_name(job)
            if unresolved:
                bad.append(
                    f"{where} runs candidate code under a name the row cannot read "
                    f"({unresolved}), which could be {MERGE_VERDICT}"
                )
            names = [str(job_id)] + [
                label for _runner, label in _legs(job, str(job.get("name", "")))
            ]
            if any(_MERGE_VERDICT_WORD.fullmatch(n.strip()) for n in names):
                bad.append(f"{where} runs candidate code under the check name {MERGE_VERDICT}")
            elif _names_merge_verdict({k: v for k, v in job.items() if k != "name"}):
                bad.append(f"{where} runs candidate code and names {MERGE_VERDICT}")
    return bad


_JOBS = "\njobs:\n"
_SHA40 = "0" * 40


def _adds_job(text: str) -> str:
    return _JOBS + text


_POSTED_CHECK_RUN = (
    "  planted:\n    runs-on: ubuntu-24.04\n    steps:\n"
    "      - run: gh api repos/${{ github.repository }}/check-runs -f name=merge-verdict"
    " -f head_sha=${{ github.event.pull_request.head.sha }} -f conclusion=success\n"
)
_REUSABLE = (
    "name: Reusable\non:\n  workflow_call:\npermissions:\n  contents: read\n"
    "jobs:\n  merge-verdict:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo green\n"
)
_VERDICT_JOB = "  merge-verdict:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo green\n"

#: pr.yml as the flip will run it: on pull_request, one group per pull request.
#: A catalogue job of the change tier renamed merge-verdict, which a planned stage job would run
#: under: the key the plan gives its entry is the job's id.
#: merge.yml's stage jobs' one group (decision 9), as the workflow writes it.
_MERGE_STAGE_GROUP = (
    "      group: merge-${{ github.ref == 'refs/heads/main' && github.run_attempt == '1' &&"
    " matrix.id == 'python-suite' && 'suite' || github.ref == 'refs/heads/main' &&"
    " github.run_attempt == '1' && matrix.id == 'python-suite-macos' && 'suite' ||"
    " github.event.pull_request.number || github.sha }}-${{ matrix.key }}\n"
)
_CATALOGUE_JOB_NAMED_MERGE_VERDICT = _both(
    _plant("", catalogue.CATALOGUE, 'id = "client"\n', 'id = "merge-verdict"\n').plant,
    _both(
        _plant("", catalogue.CATALOGUE, '  "client",\n', '  "merge-verdict",\n').plant,
        _plant("", catalogue.CATALOGUE, '  "client",\n', '  "merge-verdict",\n').plant,
    ),
)

NO_CANDIDATE_POSTS_THE_MERGE_VERDICT = _toolchain_row(
    "no workflow that runs candidate code posts the merge-verdict check",
    merge_verdict_violations,
    (
        _plant(
            "a candidate job whose id is merge-verdict",
            _PR_YML,
            _JOBS,
            _adds_job(_VERDICT_JOB),
            expect="pr.yml:merge-verdict runs candidate code under the check name",
        ),
        _plant(
            "a candidate job named merge-verdict",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-verdict\n    runs-on: ubuntu-24.04\n    steps: []\n"
            ),
            expect="pr.yml:planted runs candidate code under the check name",
        ),
        _plant(
            "the name in another case",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: Merge-Verdict\n    runs-on: ubuntu-24.04\n    steps: []\n"
            ),
            expect="under the check name",
        ),
        _plant(
            "a job named from its matrix, whose one leg is merge-verdict",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.part }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n        part: [verdict]\n    steps: []\n"
            ),
            expect="pr.yml:planted runs candidate code under the check name",
        ),
        _plant(
            "a step that posts a check run by that name",
            _PR_YML,
            _JOBS,
            _adds_job(_POSTED_CHECK_RUN),
            expect="pr.yml:planted runs candidate code and names merge-verdict",
        ),
        _plant(
            "an action handed the name",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    runs-on: ubuntu-24.04\n    steps:\n"
                f"      - uses: some/checks-action@{_SHA40}\n"
                "        with:\n          name: merge-verdict\n          conclusion: success\n"
            ),
            expect="pr.yml:planted runs candidate code and names merge-verdict",
        ),
        _plant(
            "the name in the candidate workflow's own env",
            _PR_YML,
            "\nenv:\n",
            "\nenv:\n  CHECK_NAME: merge-verdict\n",
            expect="names merge-verdict outside its jobs",
        ),
        Control(
            "a workflow a candidate calls, with a merge-verdict job",
            _both(
                _new_workflow(f"{_WORKFLOWS}/reusable.yml", _REUSABLE),
                _plant(
                    "",
                    _PR_YML,
                    _JOBS,
                    _adds_job("  called:\n    uses: ./.github/workflows/reusable.yml\n"),
                ).plant,
            ),
            expect="reusable.yml:merge-verdict",
        ),
        Control(
            "a workflow a push to a feature branch runs, with a merge-verdict job",
            _new_workflow(
                f"{_WORKFLOWS}/branch-check.yml",
                "name: Branch check\non:\n  push:\n    branches-ignore: [main]\n"
                "permissions:\n  contents: read\njobs:\n" + _VERDICT_JOB,
            ),
            expect="branch-check.yml:merge-verdict",
        ),
        _plant(
            "a candidate check whose name only starts with merge-verdict",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  preview:\n    name: merge-verdict-preview\n    runs-on: ubuntu-24.04\n"
                "    steps: []\n"
            ),
            fails=False,
        ),
        _plant(
            "a job name quoted with a trailing space",
            _PR_YML,
            _JOBS,
            _adds_job(
                '  planted:\n    name: "merge-verdict "\n    runs-on: ubuntu-24.04\n'
                "    steps: []\n"
            ),
            expect="pr.yml:planted runs candidate code under the check name",
        ),
        _plant(
            "a job named from a matrix that is an expression",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.part }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix: ${{ fromJSON(needs.plan.outputs.parts) }}\n"
                "    steps: []\n"
            ),
            expect="a matrix that is an expression",
        ),
        _plant(
            "a job named from a matrix value that is an expression",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.part }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n"
                "        part: ${{ fromJSON(needs.plan.outputs.parts) }}\n    steps: []\n"
            ),
            expect="matrix.part, which the matrix gives no literal value",
        ),
        _plant(
            "a job named from a matrix key the matrix does not have",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.other }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n        part: [verdict]\n    steps: []\n"
            ),
            expect="matrix.other, which the matrix gives no literal value",
        ),
        _plant(
            "a job named from a matrix whose include is an expression",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.part }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n        part: [preview]\n"
                "        include: ${{ fromJSON(needs.plan.outputs.inc) }}\n    steps: []\n"
            ),
            expect="an include that is an expression",
        ),
        _plant(
            "a job named from an include entry that is an expression",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.part }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n"
                "        include: [{part: '${{ needs.plan.outputs.p }}'}]\n    steps: []\n"
            ),
            expect="matrix.part, which the matrix gives as an expression",
        ),
        _plant(
            "a job named from a matrix value that is an expression beside an include",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.part }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n"
                "        part: ${{ fromJSON(needs.plan.outputs.parts) }}\n"
                "        include: [{part: preview}]\n    steps: []\n"
            ),
            expect="matrix.part, which the matrix gives no literal value",
        ),
        _plant(
            "a job named from an expression other than the matrix",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: ${{ needs.plan.outputs.check }}\n"
                "    runs-on: ubuntu-24.04\n    steps: []\n"
            ),
            expect="an expression other than a matrix value",
        ),
        _plant(
            "a key PyYAML reads as a date",
            _PR_YML,
            "\nenv:\n",
            "\nenv:\n  2026-01-01: merge-verdict\n",
            expect="names merge-verdict outside its jobs",
        ),
        _plant(
            "the name as a key, with a value that is not it",
            _PR_YML,
            "\nenv:\n",
            "\nenv:\n  merge-verdict: on\n",
            expect="names merge-verdict outside its jobs",
        ),
        _plant(
            "a job named from a literal matrix with no such leg",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  planted:\n    name: merge-${{ matrix.part }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix:\n        part: [preview]\n    steps: []\n"
            ),
            fails=False,
        ),
        _plant(
            "a step that names merge-verdict-preview, a longer word",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  preview:\n    runs-on: ubuntu-24.04\n    steps:\n"
                "      - run: echo merge-verdict-preview\n"
            ),
            fails=False,
        ),
        _plant(
            "a step that names pre-merge-verdict, a longer word",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  preview:\n    runs-on: ubuntu-24.04\n    steps:\n"
                "      - run: echo pre-merge-verdict\n"
            ),
            fails=False,
        ),
        _plant(
            "a job named merge-verdict preview, not the name itself",
            _PR_YML,
            _JOBS,
            _adds_job(
                "  preview:\n    name: merge-verdict preview\n    runs-on: ubuntu-24.04\n"
                "    steps: []\n"
            ),
            fails=False,
        ),
        _plant(
            "a trusted workflow_run job named merge-verdict",
            f"{_WORKFLOWS}/pr-feedback.yml",
            _JOBS,
            _JOBS + _VERDICT_JOB,
            fails=False,
        ),
        Control(
            "a push to main that records the merge verdict",
            _new_workflow(
                f"{_WORKFLOWS}/main-record.yml",
                "name: Main record\non:\n  push:\n    branches: [main]\n"
                "permissions:\n  contents: read\njobs:\n" + _VERDICT_JOB,
            ),
            fails=False,
        ),
        # A tier workflow's stage job is named from its planned matrix: each entry's key, which
        # the tree's catalogue gives, so the row reads every key the tier's plan can give it.
        Control(
            "pr.yml, its stage job named by the planned matrix, passes",
            _nothing,
            fails=False,
        ),
        Control(
            "pr.yml beside a catalogue job named merge-verdict",
            _CATALOGUE_JOB_NAMED_MERGE_VERDICT,
            expect="pr.yml:test runs candidate code under the check name merge-verdict",
        ),
        Control(
            "a planned matrix's expression in a workflow that is not a tier workflow",
            _new_workflow(
                f"{_WORKFLOWS}/new-check.yml",
                "name: New check\non:\n  pull_request:\npermissions: {}\njobs:\n"
                "  planted:\n    name: ${{ matrix.key }}\n    runs-on: ubuntu-24.04\n"
                "    strategy:\n      matrix: ${{ fromJSON(needs.changes.outputs.test) }}\n"
                "    steps: []\n",
            ),
            expect="new-check.yml:planted runs candidate code under a name the row cannot read",
        ),
    ),
)


# ── Concurrency keys on the pull request or the candidate's head (#3915) ──────────────────────
#
# Concurrency groups are shared across the repository: a run that joins a group cancels the run
# in progress there (with `cancel-in-progress`), and a pending run replaces the one waiting. So a
# group that two unrelated pull requests can both evaluate to lets one pull request's push cancel
# or drop the other's check. The row evaluates every concurrency group a candidate's run joins,
# the workflow's and each job's, against the event contexts of several unrelated candidates, the
# way GitHub would, and holds two relations:
#
# 1. no two runs for different candidates (two pull requests from this repository, two forks with
#    the same branch name, pushes to two feature branches) evaluate to one group, in one workflow
#    or across two;
# 2. every run for one candidate at one head evaluates to one group: a second run of the
#    workflow (a new run id) and a GitHub re-run (the same run id and commit, a later
#    `run_attempt`). So a group keys on the pull request or on its head, and on nothing that
#    differs per run or per attempt.
#
# Each event is modelled as GitHub fills it in. Under `pull_request`, `github.ref` is the pull
# request's `refs/pull/N/merge` and `github.sha` its test-merge commit. Under
# `pull_request_target` the run is the base branch's: `github.ref` is `refs/heads/main` and
# `github.sha` main's commit for EVERY pull request, so a group keyed on either is one group for
# them all. A `workflow_run` listener of a candidate workflow (a trusted job that reacts to each
# pull request's run) is held to relation 1 against each candidate run's `workflow_run` event.
# Relation 2 does not apply to it: a listener may key on the run it listens to, attempt included
# (ci-telemetry.yml collects each attempt).
#
# Any context the row does not model (`vars`, `inputs`, `needs`, `matrix`, ...) is refused as an
# expression it cannot evaluate, never read as empty. That holds for context roots only: a
# `github.*` property the model leaves out (`github.workflow`, `github.actor`, ...) reads as
# "", so `a || b` falls through to `b`. A group that keys on such a property is not held.
#
# A push to a feature branch is the same candidate as the pull request whose head it is. A push
# to main and a scheduled run are not a candidate's, so main's own grouping (the suite's shared
# group on main, #3915) is not this row's.


@dataclass(frozen=True)
class _CandidateRun:
    label: str
    #: The candidate the run is for. Two runs for one candidate may share a group.
    candidate: str
    #: The event the run is: `pull_request`, `pull_request_target`, `push` or `workflow_run`.
    kind: str
    head: str
    context: dict[str, Any]
    #: A Dependabot head's: its pull request's author is Dependabot.
    dependabot: bool = False


_REPO_SLUG = "PersonalClaw/PersonalClaw"
_FORK = "someone/PersonalClaw"
_FORK_2 = "another/PersonalClaw"
#: Main's commit when the runs below start: what every `pull_request_target` and `workflow_run`
#: run checks out, whichever pull request it is for.
_MAIN_SHA = "base0000"


def _candidate_pr(
    label: str,
    candidate: str,
    number: int,
    head_repo: str,
    branch: str,
    head: str,
    run: int,
    attempt: int = 1,
    kind: str = "pull_request",
    author: str = "a-contributor",
) -> _CandidateRun:
    target = kind == "pull_request_target"
    pull_request = {
        "number": number,
        "user": {"login": author},
        "head": {"ref": branch, "sha": head, "repo": {"full_name": head_repo}},
        "base": {"ref": "main", "sha": _MAIN_SHA},
    }
    github = {
        "repository": _REPO_SLUG,
        "event_name": kind,
        # `pull_request` runs the test merge; `pull_request_target` runs the base branch.
        "ref": "refs/heads/main" if target else f"refs/pull/{number}/merge",
        "ref_name": "main" if target else f"{number}/merge",
        # The test-merge commit differs for every run, so a group keyed on it keys on no head.
        "sha": _MAIN_SHA if target else f"merge{number}{run}",
        "head_ref": branch,
        "base_ref": "main",
        "run_id": str(9000 + run),
        "run_number": str(run),
        "run_attempt": str(attempt),
        "event": {"action": "synchronize", "number": number, "pull_request": pull_request},
    }
    dependabot = author == _DEPENDABOT_LOGIN
    return _CandidateRun(label, candidate, kind, head, {"github": github}, dependabot)


def _candidate_push(
    label: str, candidate: str, branch: str, head: str, run: int, attempt: int = 1
) -> _CandidateRun:
    github = {
        "repository": _REPO_SLUG,
        "event_name": "push",
        "ref": f"refs/heads/{branch}",
        "ref_name": branch,
        "sha": head,
        "head_ref": "",
        "base_ref": "",
        "run_id": str(9500 + run),
        "run_number": str(run),
        "run_attempt": str(attempt),
        "event": {"ref": f"refs/heads/{branch}", "after": head},
    }
    return _CandidateRun(label, candidate, "push", head, {"github": github})


def _candidate_queue(
    label: str, candidate: str, number: int, built_on: str, head: str, run: int, attempt: int = 1
) -> _CandidateRun:
    """A queue commit's run (``merge_group``, 0.4.0 A1): GitHub's squash of pull request
    ``number`` on ``built_on`` (main's tip, or the queue commit of the entry ahead), on the queue's
    branch, which names the pull request and that commit, never the pull request's head. The
    queue commit is its own candidate: its tree is the one main may reach."""
    branch = f"gh-readonly-queue/main/pr-{number}-{built_on}"
    github = {
        "repository": _REPO_SLUG,
        "event_name": "merge_group",
        "ref": f"refs/heads/{branch}",
        "ref_name": branch,
        "sha": head,
        "head_ref": "",
        "base_ref": "",
        "run_id": str(9700 + run),
        "run_number": str(run),
        "run_attempt": str(attempt),
        "event": {
            "action": "checks_requested",
            "merge_group": {
                "head_sha": head,
                "head_ref": f"refs/heads/{branch}",
                "base_sha": built_on,
                "base_ref": "refs/heads/main",
            },
        },
    }
    return _CandidateRun(label, candidate, "merge_group", head, {"github": github})


def _listener_run(run: _CandidateRun) -> _CandidateRun:
    """The ``workflow_run`` event a candidate run's completion starts in a listener."""
    github = run.context["github"]
    pull = github["event"].get("pull_request") or {}
    head_repo = (pull.get("head") or {}).get("repo", {}).get("full_name", _REPO_SLUG)
    listened = {
        "id": int(github["run_id"]),
        "run_attempt": int(github["run_attempt"]),
        "event": github["event_name"],
        "head_sha": run.head,
        "head_branch": github["head_ref"] or github["ref_name"],
        "head_repository": {"full_name": head_repo},
        # GitHub lists no pull request for a fork's run.
        "pull_requests": [{"number": pull["number"]}] if pull and head_repo == _REPO_SLUG else [],
    }
    context = {
        "github": {
            "repository": _REPO_SLUG,
            "event_name": "workflow_run",
            "ref": "refs/heads/main",
            "ref_name": "main",
            "sha": _MAIN_SHA,
            "head_ref": "",
            "base_ref": "",
            "run_id": str(int(github["run_id"]) + 50000),
            "run_number": github["run_number"],
            "run_attempt": "1",
            "event": {"action": "completed", "workflow_run": listened},
        }
    }
    label = f"the listener of {run.label}"
    kind = f"workflow_run:{run.kind}"
    return _CandidateRun(label, run.candidate, kind, run.head, context, run.dependabot)


_PR41 = "pull request #41 (feature-a)"
_PR42 = "pull request #42 (feature-b)"
_PR43 = "pull request #43 from a fork (feature-a)"
_PR44 = "pull request #44 from another fork (feature-a)"
_PR45 = f"Dependabot's pull request #45 ({_DEPENDABOT_A})"
_PR46 = f"Dependabot's pull request #46 ({_DEPENDABOT_B})"
_PR47 = f"pull request #47 from a fork ({_DEPENDABOT_A})"


def _pr_runs(kind: str) -> tuple[_CandidateRun, ...]:
    at = " under pull_request_target" if kind == "pull_request_target" else ""
    a, b = "feature-a", "feature-b"
    return (
        _candidate_pr(f"{_PR41}{at}", "pr41", 41, _REPO_SLUG, a, "a1", 1, kind=kind),
        _candidate_pr(f"{_PR41}{at}, run again", "pr41", 41, _REPO_SLUG, a, "a1", 2, kind=kind),
        # Between two runs of one head, so relation 2 cannot lean on the table's order.
        _candidate_pr(f"{_PR41}{at}, a newer head", "pr41", 41, _REPO_SLUG, a, "a2", 3, kind=kind),
        _candidate_pr(
            f"{_PR41}{at}, re-run as attempt 2", "pr41", 41, _REPO_SLUG, a, "a1", 1, 2, kind
        ),
        _candidate_pr(f"{_PR42}{at}", "pr42", 42, _REPO_SLUG, b, "b1", 4, kind=kind),
        _candidate_pr(f"{_PR43}{at}", "pr43", 43, _FORK, a, "c1", 5, kind=kind),
        _candidate_pr(f"{_PR44}{at}", "pr44", 44, _FORK_2, a, "d1", 6, kind=kind),
        _candidate_pr(f"{_PR45}{at}", "pr45", 45, _REPO_SLUG, _DEPENDABOT_A, "e1", 10, kind=kind,
                      author=_DEPENDABOT_LOGIN),
        _candidate_pr(f"{_PR46}{at}", "pr46", 46, _REPO_SLUG, _DEPENDABOT_B, "f1", 11, kind=kind,
                      author=_DEPENDABOT_LOGIN),
        # A fork's branch named as Dependabot names its own, by another author.
        _candidate_pr(f"{_PR47}{at}", "pr47", 47, _FORK, _DEPENDABOT_A, "g1", 12, kind=kind),
    )  # fmt: skip


_PUSH_RUNS: tuple[_CandidateRun, ...] = (
    _candidate_push("a push to feature-a", "pr41", "feature-a", "a1", 7),
    _candidate_push("a push to feature-a, run again", "pr41", "feature-a", "a1", 8),
    _candidate_push("a push to feature-a, re-run as attempt 2", "pr41", "feature-a", "a1", 7, 2),
    _candidate_push("a push to feature-b", "pr42", "feature-b", "b1", 9),
)

#: Queue commits: #41's on main's tip, run again and re-run; #42's on #41's; and #41's again
#: after the queue rebuilt it (a new commit), each its own candidate.
_QUEUE_RUNS: tuple[_CandidateRun, ...] = (
    _candidate_queue("#41's queue commit", "q41", 41, _MAIN_SHA, "q41a", 21),
    _candidate_queue("#41's queue commit, run again", "q41", 41, _MAIN_SHA, "q41a", 22),
    _candidate_queue(
        "#41's queue commit, re-run as attempt 2", "q41", 41, _MAIN_SHA, "q41a", 21, 2
    ),
    _candidate_queue("#42's queue commit, on #41's", "q42", 42, "q41a", "q42a", 23),
    _candidate_queue("#41's queue commit, rebuilt", "q41b", 41, _MAIN_SHA, "q41b", 24),
)

_CANDIDATE_RUNS: tuple[_CandidateRun, ...] = (
    *_pr_runs("pull_request"),
    *_pr_runs("pull_request_target"),
    *_PUSH_RUNS,
    *_QUEUE_RUNS,
    *(
        _listener_run(r)
        for r in (
            *_pr_runs("pull_request"),
            *_pr_runs("pull_request_target"),
            *_PUSH_RUNS,
            *_QUEUE_RUNS,
        )
    ),
)

#: The roots of a context this row models. Anything else is refused, never read as empty.
_MODELLED_ROOTS = frozenset({"github", "true", "false"})
#: A named value an expression starts from: a word not after a dot, and not a function's name.
_ROOT = re.compile(r"(?<![\w.'-])([A-Za-z_][\w-]*)\b(?!\s*\()")


def _unmodelled(group: str) -> str:
    """The first context root in a group's expressions the row does not model, or ``""``."""
    for match in _EXPRESSION.finditer(group):
        body = re.sub(r"'[^']*'", "''", match.group(1))
        for root in _ROOT.findall(body):
            if root not in _MODELLED_ROOTS:
                return root
    return ""


#: A matrix property inside an expression: ``matrix.<key>``, not after a word or a dot.
_MATRIX_PROPERTY = re.compile(r"(?<![\w.])matrix\.([\w-]+)")


def _group_legs(job: dict[str, Any], group: str) -> list[str]:
    """A job's group once per matrix leg, each ``matrix.<key>`` in its expressions read from a
    literal matrix as a quoted literal. A matrix the row cannot read raises, as an expression it
    cannot evaluate does."""
    keys = {k for body in _EXPRESSION.findall(group) for k in _MATRIX_PROPERTY.findall(body)}
    if not keys:
        return [group]
    gap = _matrix_gap(job, group, sorted(keys))
    if gap:
        raise ValueError(f"an expression this row cannot evaluate: {gap}")

    return sorted({_leg_group(group, leg) for leg in _matrix_legs(job)})


def _leg_group(group: str, leg: dict[str, Any]) -> str:
    """``group`` with each ``matrix.<key>`` in its expressions read from ``leg`` as a quoted
    literal."""

    def one(match: re.Match[str]) -> str:
        body = _MATRIX_PROPERTY.sub(lambda m: f"'{leg.get(m[1], '')}'", match.group(1))
        return "${{" + body + "}}"

    return _EXPRESSION.sub(one, group)


def _concurrency_group(value: object) -> str | None:
    """A ``concurrency:`` value's group: the string itself, or its ``group``."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "group" in value:
        return str(value["group"])
    return None


def _listens_to(trigger: object, names: set[str]) -> set[str]:
    """The workflow names a ``workflow_run`` trigger listens to, of ``names``, when it listens
    off ``main``: a ``branches`` filter of ``main`` alone listens to no candidate's run."""
    if not isinstance(trigger, dict):
        return set()
    branches = trigger.get("branches")
    if isinstance(branches, list) and branches and set(map(str, branches)) <= {"main"}:
        return set()
    listened = trigger.get("workflows") or []
    patterns = [str(w) for w in (listened if isinstance(listened, list) else [listened])]
    # GitHub reads each entry as a glob; a `!` entry only narrows, so reading it as nothing
    # holds the listener to more workflows, never fewer.
    return {n for n in names if any(fnmatch.fnmatchcase(n, p) for p in patterns)}


def _candidate_kinds(workflows: list[tuple[str, dict]]) -> dict[str, set[str]]:
    """Per workflow, the candidate runs it takes part in: ``pull_request`` and
    ``pull_request_target`` (an opening event of a pull request), ``push`` (a push to a branch
    other than main), ``merge_group`` (a queue commit of main's merge queue),
    ``workflow_run:<kind>`` (a listener of a workflow that takes part in ``<kind>``), and for a
    called workflow, whatever its callers take part in."""
    kinds: dict[str, set[str]] = {}
    for name, wf in workflows:
        triggers = _triggers(wf)
        own: set[str] = set()
        for event in ("pull_request", "pull_request_target"):
            if event in triggers and _pull_request_types(triggers[event]) - {"closed"}:
                own.add(event)
        if "push" in triggers and not _main_or_tags_only(triggers["push"]):
            own.add("push")
        if "merge_group" in triggers:
            own.add("merge_group")
        kinds[name] = own
    by_name = {str(wf.get("name", "")): file for file, wf in workflows if wf.get("name")}
    # Read from each workflow's own kinds, so a glob that matches the listener itself reads
    # the same whatever order the workflows come in.
    own_kinds = {name: set(k) for name, k in kinds.items()}
    for name, wf in workflows:
        trigger = _triggers(wf).get("workflow_run")
        for listened in _listens_to(trigger, set(by_name)):
            for kind in own_kinds.get(by_name[listened], set()):
                kinds[name].add(f"workflow_run:{kind}")
    changed = True
    while changed:
        changed = False
        for name, wf in workflows:
            for job in _trust_jobs(wf).values():
                callee = str(job.get("uses", ""))
                if callee.startswith(f"./{_WORKFLOWS}/"):
                    called = callee.split("/")[-1].split("@")[0]
                    if called in kinds and not kinds[name] <= kinds[called]:
                        kinds[called] |= kinds[name]
                        changed = True
    return kinds


def concurrency_keying_violations(tree: Tree) -> list[str]:
    """Every concurrency group a candidate's run joins keys on its pull request or its head."""
    workflows = [(name, wf if isinstance(wf, dict) else {}) for name, wf in _workflows(tree)]
    kinds = _candidate_kinds(workflows)
    bad: list[str] = []
    #: group key -> the (where, run, whether the run waits its turn there) that evaluate to it
    joined: dict[str, list[tuple[str, _CandidateRun, bool]]] = {}
    for name, workflow in workflows:
        if not kinds.get(name):
            continue
        values = [(name, workflow.get("concurrency"))]
        values += [
            (f"{name}:{job_id}", job.get("concurrency"))
            for job_id, job in _trust_jobs(workflow).items()
        ]
        groups = [(where, _concurrency_group(value), value) for where, value in values]
        jobs = {
            "": {},
            **{
                job_id: _literal_matrix(tree, name, job)
                for job_id, job in _trust_jobs(workflow).items()
            },
        }
        for where, group, value in groups:
            if group is None:
                continue
            job = jobs.get(where.partition(":")[2], {})
            keys: dict[str, set[str]] = {}
            try:
                # The matrix legs first: `${{ matrix.<key> }}` is read from a literal matrix, and
                # only what is left must be a context the row models.
                legs = _group_legs(job, group)
                for leg in legs:
                    root = _unmodelled(leg)
                    if root:
                        raise ValueError(
                            f"an expression this row cannot evaluate: it reads `{root}`, which "
                            "the row does not model, so its value for each candidate is unknown"
                        )
                for run in _CANDIDATE_RUNS:
                    if run.kind in kinds[name]:
                        keys[run.label] = {_evaluate(leg, run.context) for leg in legs}
                        waits = _waits_its_turn(value, run.context)
                        for key in keys[run.label]:
                            joined.setdefault(key, []).append((where, run, waits))
            except ValueError as exc:
                bad.append(f"{where}: {exc}")
                continue
            runs = [r for r in _CANDIDATE_RUNS if r.label in keys]
            # Relation 2, over every pair for one candidate at one head, whatever their order.
            # A listener may key on the run it listens to, so it is held to relation 1 only.
            for i, a in enumerate(runs):
                for b in runs[i + 1 :]:
                    if a.kind.startswith("workflow_run:"):
                        continue
                    same = (a.candidate, a.head, a.kind) == (b.candidate, b.head, b.kind)
                    if same and keys[a.label] != keys[b.label]:
                        bad.append(
                            f"{where}: {a.label} and {b.label} evaluate to "
                            f"{sorted(keys[a.label])!r} and {sorted(keys[b.label])!r}, so its "
                            "group keys on neither the pull request nor the head, and a run "
                            "never cancels or replaces its own duplicate"
                        )
    for key, members in joined.items():
        first = members[0][1]
        others = [run for _where, run, _waits in members if run.candidate != first.candidate]
        if not others:
            continue
        # Name a run that is not a Dependabot head's where there is one: it is the one out of place.
        other = next((run for run in others if not run.dependabot), others[0])
        wheres = ", ".join(sorted({where for where, _run, _waits in members}))
        # The one exception: every Dependabot head's runs wait in one group, one at a time, and
        # none cancels or replaces another, so Dependabot holds few of the account's runners.
        if all(run.dependabot for _where, run, _waits in members):
            hurried = sorted({where for where, _run, waits in members if not waits})
            if hurried:
                bad.append(
                    f"{wheres}: every Dependabot head's runs share the group {key!r}, and"
                    f" {', '.join(hurried)} cancels a run in flight there or replaces the one"
                    " waiting (cancel-in-progress, or a queue other than `max`)"
                )
            continue
        bad.append(
            f"{wheres}: the group {key!r} is shared by {first.label} and {other.label}, so "
            "one candidate's run cancels or replaces another's"
        )
    return list(dict.fromkeys(bad))


def _waits_its_turn(value: object, context: dict[str, Any]) -> bool:
    """Whether a run that joins a ``concurrency:`` *value* waits in its group whatever joins
    after it: never cancelled in flight (no cancel-in-progress) and never replaced while it waits
    (``queue: max``). A value the row cannot read waits for no one."""
    if not isinstance(value, dict):
        return False
    cancel, queue = value.get("cancel-in-progress", False), value.get("queue", "single")
    if any(_unmodelled(str(v)) for v in (cancel, queue)):
        return False
    try:
        return not _cancels(cancel, context) and _rendered(str(queue), context) == "max"
    except ValueError:
        return False


_DCO_GROUP = "group: dco-${{ github.event.pull_request.number }}"
_NIX = f"{_WORKFLOWS}/nix.yml"
_NIX_GROUP = (
    "group: nix-${{ github.event.pull_request.head.repo.full_name || github.repository }}"
    "-${{ github.head_ref || github.ref_name }}"
)


def _new_pr_workflow(rel: str, concurrency: str) -> Callable[[Path], None]:
    return _new_workflow(
        f"{_WORKFLOWS}/{rel}",
        f"name: New check\non:\n  pull_request:\n{concurrency}permissions: {{}}\njobs: {{}}\n",
    )


CONCURRENCY_KEYS_ON_THE_CANDIDATE = _toolchain_row(
    "every concurrency group a candidate joins keys on its pull request or its head",
    concurrency_keying_violations,
    (
        _plant("one group for every pull request", _NIX, _NIX_GROUP, "group: nix-lane",
               expect="is shared by"),
        _plant("a group keyed on the base branch", _NIX, _NIX_GROUP,
               "group: nix-${{ github.base_ref }}", expect="pull request #42 (feature-b)"),
        _plant("a group keyed on the branch alone, which two forks share", _NIX, _NIX_GROUP,
               "group: nix-${{ github.head_ref }}", expect="#43 from a fork"),
        _plant("a group keyed on the run", _NIX, _NIX_GROUP, "group: nix-${{ github.run_id }}",
               expect="keys on neither"),
        _plant("a group keyed on the test-merge commit", _NIX, _NIX_GROUP,
               "group: nix-${{ github.sha }}", expect="keys on neither"),
        _plant("an expression the row cannot evaluate", _NIX, _NIX_GROUP,
               "group: nix-${{ format('{0}', github.head_ref) }}", expect="cannot evaluate"),
        Control("a group written as a bare string",
                _new_pr_workflow("new-check.yml", "concurrency: one-lane\n"),
                expect="new-check.yml"),
        _plant("a job's own group that every pull request shares", _PR_YML, _JOBS, _adds_job(
            "  lane:\n    runs-on: ubuntu-24.04\n    concurrency:\n      group: lane\n"
            "      cancel-in-progress: true\n    steps: []\n"), expect="pr.yml:lane"),
        Control("a group that lands on another workflow's group for another pull request",
                _new_pr_workflow("new-check.yml",
                                 "concurrency:\n  group: nix-${{ github.repository }}-feature-b\n"),
                expect="new-check.yml, nix.yml"),
        Control("a feature-branch push workflow with one group for every branch",
                _new_workflow(f"{_WORKFLOWS}/branch-check.yml",
                              "name: Branch check\non:\n  push:\n    branches-ignore: [main]\n"
                              "concurrency:\n  group: branch-lane\npermissions: {}\njobs: {}\n"),
                expect="a push to feature-b"),
        Control("a group keyed on the pull request's number",
                _new_pr_workflow("new-check.yml",
                                 "concurrency:\n"
                                 "  group: new-${{ github.event.pull_request.number }}\n"
                                 "  cancel-in-progress: true\n"), fails=False),
        Control("a group keyed on the candidate's head",
                _new_pr_workflow("new-check.yml",
                                 "concurrency:\n  group: new-${{ github.event.pull_request.head.sha"
                                 " || github.sha }}\n  cancel-in-progress: false\n"), fails=False),
        Control("main's suite in one shared group, which is not a candidate's",
                _new_workflow(f"{_WORKFLOWS}/main-suite.yml",
                              "name: Main suite\non:\n  push:\n    branches: [main]\n"
                              "concurrency:\n  group: main-suite\n  cancel-in-progress: false\n"
                              "permissions: {}\njobs: {}\n"), fails=False),
        Control("a scheduled run in one group",
                _new_workflow(f"{_WORKFLOWS}/nightly.yml",
                              "name: Nightly\non:\n  schedule:\n    - cron: '0 3 * * *'\n"
                              "concurrency:\n  group: nightly\npermissions: {}\njobs: {}\n"),
                fails=False),
        # pull_request_target runs in the base branch's context, the same for every pull request.
        _plant("a pull_request_target group every pull request shares", _DCO,
               _DCO_GROUP, "group: dco-lane", expect="dco.yml: the group 'dco-lane' is shared by"),
        _plant("a pull_request_target group keyed on github.ref", _DCO,
               _DCO_GROUP, "group: dco-${{ github.ref }}", expect="under pull_request_target"),
        _plant("a pull_request_target group keyed on github.ref_name", _DCO,
               _DCO_GROUP, "group: dco-${{ github.ref_name }}", expect="under pull_request_target"),
        _plant("a pull_request_target group keyed on github.sha", _DCO,
               _DCO_GROUP, "group: dco-${{ github.sha }}", expect="is shared by"),
        Control("dco.yml's own group, keyed on the pull request's number", _nothing, fails=False),
        # A called workflow takes part in whatever its callers take part in.
        Control("a reusable workflow a candidate calls, with one shared group",
                _both(_new_workflow(f"{_WORKFLOWS}/reusable.yml",
                                    "name: Reusable\non:\n  workflow_call:\n"
                                    "concurrency:\n  group: reusable-lane\n"
                                    "permissions: {}\njobs: {}\n"),
                      _plant("", _PR_YML, _JOBS, _adds_job(
                          "  called:\n    uses: ./.github/workflows/reusable.yml\n")).plant),
                expect="reusable.yml: the group 'reusable-lane' is shared by"),
        # A queue commit's run (0.4.0 A1) keys on its commit: the queue's base is shared by
        # every entry built on it, and its branch names the commit under it, not its own.
        Control("a queue run in one group keyed on the queue's base",
                _new_workflow(f"{_WORKFLOWS}/queue-check.yml",
                              "name: Queue check\non:\n  merge_group:\n"
                              "concurrency:\n  group: q-${{ github.event.merge_group.base_ref }}\n"
                              "permissions: {}\njobs: {}\n"),
                expect="#41's queue commit"),
        Control("a queue run keyed on the commit it was built on",
                _new_workflow(f"{_WORKFLOWS}/queue-check.yml",
                              "name: Queue check\non:\n  merge_group:\n"
                              "concurrency:\n  group: q-${{ github.event.merge_group.base_sha }}\n"
                              "permissions: {}\njobs: {}\n"),
                expect="rebuilt"),
        Control("a queue run keyed on its queue commit",
                _new_workflow(f"{_WORKFLOWS}/queue-check.yml",
                              "name: Queue check\non:\n  merge_group:\n"
                              "concurrency:\n  group: q-${{ github.sha }}\n"
                              "  cancel-in-progress: false\npermissions: {}\njobs: {}\n"),
                fails=False),
        # A GitHub re-run keeps its run id and commit and raises run_attempt.
        _plant("a group keyed on the run attempt", _NIX, _NIX_GROUP,
               _NIX_GROUP.replace("group: nix-", "group: nix-${{ github.run_attempt }}-"),
               expect="re-run as attempt 2"),
        Control("a feature-branch push group keyed on the run",
                _new_workflow(f"{_WORKFLOWS}/branch-check.yml",
                              "name: Branch check\non:\n  push:\n    branches-ignore: [main]\n"
                              "concurrency:\n  group: b-${{ github.ref }}-${{ github.run_id }}\n"
                              "permissions: {}\njobs: {}\n"),
                expect="a push to feature-a and a push to feature-a, run again"),
        _plant("a group that tells this repository from every fork as one", _NIX, _NIX_GROUP,
               "group: nix-${{ github.event.pull_request.head.repo.full_name"
               " == 'PersonalClaw/PersonalClaw' }}-${{ github.head_ref }}",
               expect=f"{_PR43} and {_PR44}"),
        # A context the row does not model is refused, never read as empty.
        _plant("a group that reads vars", _NIX, _NIX_GROUP,
               "group: nix-${{ vars.LANE || github.event.pull_request.number }}",
               expect="it reads `vars`"),
        # The one exception: every Dependabot head's runs, both tiers', wait in one group.
        Control("every Dependabot head's runs in one group, waiting their turn", _nothing,
                fails=False),
        Control("a Dependabot head's run cancelled in flight in the group they share",
                _tier_concurrency_line(_PR_YML, "cancel-in-progress", "true"),
                expect="and pr.yml cancels a run in flight there"),
        Control("a Dependabot head's waiting run replaced in the group they share",
                _tier_concurrency_line(_MERGE_YML, "queue", ""),
                expect="and merge.yml cancels a run in flight there or replaces the one waiting"),
        Control("a fork's branch named as Dependabot's, in the group Dependabot's heads share",
                _tier_concurrency_line(
                    _PR_YML, "group",
                    f"${{{{ github.head_ref == '{_DEPENDABOT_A}' && 'dependabot' || 'pr' }}}}-"
                    f"${{{{ github.head_ref == '{_DEPENDABOT_A}' && 'heads'"
                    " || github.event.pull_request.number || github.sha }}"),
                expect=f"the group 'dependabot-heads' is shared by {_PR45} and {_PR47}"),
        Control("a called workflow keyed on an input its caller fixes",
                _both(_new_workflow(f"{_WORKFLOWS}/reusable.yml",
                                    "name: Reusable\non:\n  workflow_call:\n"
                                    "    inputs:\n      lane:\n        type: string\n"
                                    "concurrency:\n  group: r-${{ inputs.lane || github.ref }}\n"
                                    "permissions: {}\njobs: {}\n"),
                      _plant("", _PR_YML, _JOBS, _adds_job(
                          "  called:\n    uses: ./.github/workflows/reusable.yml\n"
                          "    with:\n      lane: one\n")).plant),
                expect="it reads `inputs`"),
        _plant("a job's group keyed on a matrix the row cannot read", _PR_YML, _JOBS,
               _adds_job("  lane:\n    runs-on: ubuntu-24.04\n"
                         "    strategy:\n      matrix: ${{ fromJSON(needs.plan.outputs.m) }}\n"
                         "    concurrency:\n      group: lane-${{ matrix.part }}\n"
                         "    steps: []\n"),
               expect="a matrix that is an expression"),
        _plant("a job's group per literal matrix leg, shared by every pull request", _PR_YML, _JOBS,
               _adds_job("  lane:\n    runs-on: ubuntu-24.04\n"
                         "    strategy:\n      matrix:\n        part: [1, 2]\n"
                         "    concurrency:\n      group: lane-${{ matrix.part }}\n"
                         "    steps: []\n"),
               expect="pr.yml:lane: the group 'lane-1' is shared by"),
        # A workflow_run listener of a candidate workflow is held to relation 1.
        Control("a listener of PR with one group for every pull request",
                _new_workflow(f"{_WORKFLOWS}/listener.yml",
                              "name: Listener\non:\n  workflow_run:\n    workflows: [\"PR\"]\n"
                              "    types: [completed]\n"
                              "concurrency:\n  group: listener\n  cancel-in-progress: true\n"
                              "permissions: {}\njobs: {}\n"),
                expect="listener.yml: the group 'listener' is shared by the listener of"),
        # GitHub reads a `workflows:` entry as a glob.
        Control("a listener of every workflow with one group for every pull request",
                _new_workflow(f"{_WORKFLOWS}/listener.yml",
                              "name: Listener\non:\n  workflow_run:\n    workflows: [\"*\"]\n"
                              "    types: [completed]\n"
                              "concurrency:\n  group: listener\n  cancel-in-progress: true\n"
                              "permissions: {}\njobs: {}\n"),
                expect="listener.yml: the group 'listener' is shared by the listener of"),
        # DCO runs on pull_request_target, so its listener is a candidate's too.
        Control("a listener of DCO with one group for every pull request",
                _new_workflow(f"{_WORKFLOWS}/listener.yml",
                              "name: Listener\non:\n  workflow_run:\n    workflows: [\"DCO\"]\n"
                              "    types: [completed]\n"
                              "concurrency:\n  group: listener\n  cancel-in-progress: true\n"
                              "permissions: {}\njobs: {}\n"),
                expect="listener.yml: the group 'listener' is shared by the listener of"),
        Control("a listener of PR keyed on the run it listens to",
                _new_workflow(f"{_WORKFLOWS}/listener.yml",
                              "name: Listener\non:\n  workflow_run:\n    workflows: [\"PR\"]\n"
                              "    types: [completed]\n"
                              "concurrency:\n  group: l-${{ github.event.workflow_run.id }}"
                              "-${{ github.event.workflow_run.run_attempt }}\n"
                              "permissions: {}\njobs: {}\n"),
                fails=False),
        Control("a listener that listens on main alone",
                _new_workflow(f"{_WORKFLOWS}/listener.yml",
                              "name: Listener\non:\n  workflow_run:\n    workflows: [\"PR\"]\n"
                              "    branches: [main]\n"
                              "concurrency:\n  group: listener\npermissions: {}\njobs: {}\n"),
                fails=False),
        Control("a workflow that runs on pull_request only when closed",
                _new_workflow(f"{_WORKFLOWS}/on-close.yml",
                              "name: On close\non:\n  pull_request:\n    types: [closed]\n"
                              "concurrency:\n  group: close-lane\npermissions: {}\njobs: {}\n"),
                fails=False),
        # A tier workflow's stage job reads its planned matrix: each entry the tier's plan can
        # give it, from the tree's catalogue.
        Control("pr.yml, a stage job's group keyed on the pull request and entry",
                _plant_in_job("", _PR_YML, "test", "    needs: [changes]\n",
                              "    needs: [changes]\n    concurrency:\n"
                              "      group: pr-${{ github.event.pull_request.number }}"
                              "-${{ matrix.key }}\n").plant,
                fails=False),
        Control("pr.yml, a stage job's group keyed on the entry alone",
                _plant_in_job("", _PR_YML, "test", "    needs: [changes]\n",
                              "    needs: [changes]\n    concurrency:\n"
                              "      group: entry-${{ matrix.key }}\n").plant,
                expect="pr.yml:test: the group 'entry-"),
        Control("merge.yml, a stage job's group keyed on the entry alone",
                _plant_in_job("", _MERGE_YML, "test", _MERGE_STAGE_GROUP,
                              "      group: entry-${{ matrix.key }}\n").plant,
                expect="merge.yml:test: the group 'entry-"),
    ),
)  # fmt: skip

ROWS = (*ROWS, NO_CANDIDATE_POSTS_THE_MERGE_VERDICT, CONCURRENCY_KEYS_ON_THE_CANDIDATE)


# ── No new getsource call outside tests/rails ───────────────────────────────────────────────
#
# A test that reads the product's source and asserts a literal in it pins the text, not what the
# code does. The calls still outside tests/rails are listed in a site baseline that only shrinks
# (tests/getsource_census.py holds the rule): a call the list does not hold fails, and so does a
# line whose call is gone. Against the gate's base commit, a line the base lacks needs `added` and
# a reason (tests/site_baseline.py).

_LISTED = "tests/test_listed.py::test_it::inspect.getsource(json)"
_PIN_REASON = "not converted yet"
_A_PIN = "import inspect\nimport json\n\n\ndef test_it():\n    assert inspect.getsource(json)\n"


def _getsource_scaffold(root: Path) -> None:
    """A tree where the rule holds: one listed call, a call inside tests/rails, a test with no
    call, and the baseline registered."""
    _write(root, "tests/test_listed.py", _A_PIN)
    _write(root, "tests/rails/test_a_rail.py", _A_PIN)
    _write(root, "tests/test_clean.py", "def test_it():\n    assert 1 + 1 == 2\n")
    _write(
        root,
        getsource_census.BASELINE,
        json.dumps({"site": _LISTED, "reason": _PIN_REASON}) + "\n",
    )
    registered = {
        "site": getsource_census.BASELINE,
        "rail": "tests/rails/test_a_rail.py",
        "replaces": "no limit",
        "ceiling": 1,
        "reason": "the scaffold's list",
    }
    _write(root, site_baseline.REGISTRY, json.dumps(registered) + "\n")


def _getsource_check(root: Path) -> list[str]:
    """The baseline's failures for *root*: against the gate's base on the real tree, and with no
    base on a scaffold, which is not a repository."""
    base = site_baseline.FROM_THE_GATE if root == REPO else None
    try:
        site_baseline.check(
            getsource_census.sites(root), getsource_census.BASELINE, base, root=root, prune=False
        )
    except AssertionError as exc:
        return str(exc).splitlines()[1:] or [str(exc)]
    return []


def _new_test(text: str, rel: str = "tests/test_new_pin.py") -> Callable[[Path], None]:
    return lambda root: _write(root, rel, "import inspect\nimport json\n\n\n" + text)


def _drop_listed_call(root: Path) -> None:
    _write(root, "tests/test_listed.py", "def test_it():\n    assert True\n")


def _baseline_of(*lines: dict[str, str]) -> Callable[[Path], None]:
    text = "".join(json.dumps(line) + "\n" for line in lines)
    return lambda root: (root / getsource_census.BASELINE).write_text(text, encoding="utf-8")


NO_NEW_GETSOURCE_OUTSIDE_THE_RAILS = Row(
    name="no new inspect.getsource call lands outside tests/rails",
    check=_getsource_check,
    scaffold=_getsource_scaffold,
    controls=(
        Control(
            "a planted call outside the list fails",
            _new_test("def test_it():\n    assert inspect.getsource(json)\n"),
            expect="tests/test_new_pin.py::test_it::inspect.getsource(json) is not listed",
        ),
        Control(
            "a second call in a listed test fails",
            lambda root: _write(
                root, "tests/test_listed.py", _A_PIN + "    assert inspect.getsource(json)\n"
            ),
            expect=f"{_LISTED} #2 is not listed",
        ),
        Control(
            "a bare getsource imported from inspect fails",
            lambda root: _write(
                root,
                "tests/test_bare.py",
                "import json\nfrom inspect import getsource\n\n\n"
                "def test_it():\n    assert getsource(json)\n",
            ),
            expect="tests/test_bare.py::test_it::getsource(json) is not listed",
        ),
        Control(
            "getsource taken under another name fails where it is taken",
            _new_test("READ = inspect.getsource\n\n\ndef test_it():\n    assert READ(json)\n"),
            expect="tests/test_new_pin.py::<module>::inspect.getsource is not listed",
        ),
        Control(
            "getsource imported under an alias fails at the import",
            lambda root: _write(
                root,
                "tests/test_alias.py",
                "import json\nfrom inspect import getsource as read\n\n\n"
                "def test_it():\n    assert read(json)\n",
            ),
            expect="from inspect import getsource as read is not listed",
        ),
        Control(
            "a call in a helper module under tests fails too",
            _new_test(
                "def source_of(module):\n    return inspect.getsource(module)\n",
                rel="tests/pin_helpers.py",
            ),
            expect="tests/pin_helpers.py::source_of::inspect.getsource(module) is not listed",
        ),
        Control(
            "a converted call whose line stays fails: the list only shrinks",
            _drop_listed_call,
            expect=f"Site {_LISTED} is listed",
        ),
        Control(
            "a line for a call that does not exist fails",
            _baseline_of(
                {"site": _LISTED, "reason": _PIN_REASON},
                {"site": "tests/test_x.py::t::inspect.getsource(x)", "reason": "r"},
            ),
            expect="Site tests/test_x.py::t::inspect.getsource(x) is listed",
        ),
        Control(
            "a converted call with its line deleted passes",
            lambda root: (_drop_listed_call(root), _baseline_of()(root)),
            fails=False,
        ),
        Control(
            "a new call inside tests/rails passes",
            _new_test(
                "def test_it():\n    assert inspect.getsource(json)\n",
                rel="tests/rails/test_another_rail.py",
            ),
            fails=False,
        ),
        Control(
            "the word in a comment or a string passes",
            _new_test(
                "# inspect.getsource(json) would pin the text\n\n\n"
                "def test_it():\n    assert 'getsource' != inspect.getdoc(json)\n"
            ),
            fails=False,
        ),
        Control(
            "inspect.getsourcefile passes: it names a file and reads no text",
            _new_test("def test_it():\n    assert inspect.getsourcefile(json)\n"),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, NO_NEW_GETSOURCE_OUTSIDE_THE_RAILS)


# ── No rail holds a count of the repository's sites to a fixed number ───────────────────────
#
# A count held to a fixed number goes red on whichever of two pull requests merges second, though
# neither broke it. A rail lists its sites instead (tests/site_baseline.py). The comparisons still
# in tests/rails are listed in a site baseline that only shrinks (tests/count_ratchet_census.py
# holds the rule): a comparison the list does not hold fails, and so does a line whose comparison
# is gone. What stays listed for good is an absolute cap, which counts no sites.

_RATCHET = "tests/rails/test_listed.py"
_RATCHET_SITE = f"{_RATCHET}::test_it::len(found) <= CEILING"
_A_RATCHET = (
    "from rails_repo import REPO\n\nCEILING = 3\n\n\n"
    "def test_it():\n    found = sorted(REPO.glob('*.py'))\n    assert len(found) <= CEILING\n"
)


def _ratchet_scaffold(root: Path) -> None:
    """A tree where the rule holds: one listed ratchet, a rail of comparisons that are no
    ratchets, the same ratchet outside tests/rails, and the list registered."""
    _write(root, _RATCHET, _A_RATCHET)
    _write(
        root,
        "tests/rails/test_no_ratchet.py",
        "import ast\n\nimport source_index\n\nPLANTED = 'x = 1\\ny = 2\\n'\n\n\n"
        "def test_it():\n"
        "    files = source_index.package_files()\n"
        "    assert len(files) >= 1300\n"
        "    assert len(files) > 1300\n"
        "    assert len([f for f in files if f.name == 'x']) == 1\n"
        "    assert len([f for f in files if f.name == 'y']) == 0\n"
        "    assert len([f for f in files if f.name == 'z']) < 2\n"
        "    assert len(files) != 7\n"
        "    assert len(ast.parse(PLANTED).body) == 2\n"
        "    thin = [f for f in files if len(f.name) < 30]\n"
        "    assert thin\n",
    )
    _write(root, "tests/test_outside.py", _A_RATCHET)
    _write(
        root,
        count_ratchet_census.BASELINE,
        json.dumps({"site": _RATCHET_SITE, "reason": "not converted yet"}) + "\n",
    )
    registered = {
        "site": count_ratchet_census.BASELINE,
        "rail": _RATCHET,
        "replaces": "no limit",
        "ceiling": 1,
        "reason": "the scaffold's list",
    }
    _write(root, site_baseline.REGISTRY, json.dumps(registered) + "\n")


def _ratchet_check(root: Path) -> list[str]:
    """The list's failures for *root*: against the gate's base on the real tree, and with no base
    on a scaffold, which is not a repository."""
    base = site_baseline.FROM_THE_GATE if root == REPO else None
    try:
        site_baseline.check(
            count_ratchet_census.sites(root),
            count_ratchet_census.BASELINE,
            base,
            root=root,
            prune=False,
        )
    except AssertionError as exc:
        return str(exc).splitlines()[1:] or [str(exc)]
    return []


def _new_rail(body: str, head: str = "", rel: str = "tests/rails/test_new.py"):
    return lambda root: _write(
        root,
        rel,
        f"import source_index\nfrom rails_repo import REPO\n{head}\n\ndef test_it():\n{body}",
    )


def _drop_listed_ratchet(root: Path) -> None:
    _write(root, _RATCHET, "def test_it():\n    assert True\n")


def _ratchets_of(*lines: dict[str, str]) -> Callable[[Path], None]:
    text = "".join(json.dumps(line) + "\n" for line in lines)
    return lambda root: (root / count_ratchet_census.BASELINE).write_text(text, encoding="utf-8")


_FILES = "    files = source_index.package_files()\n"

NO_RAIL_HOLDS_A_COUNT_TO_A_FIXED_NUMBER = Row(
    name="no rail holds a count of the repository's sites to a fixed number",
    check=_ratchet_check,
    scaffold=_ratchet_scaffold,
    controls=(
        Control(
            "a count held to a module's named number fails",
            _new_rail(_FILES + "    assert len(files) <= BUDGET\n", head="BUDGET = 1500\n"),
            expect="tests/rails/test_new.py::test_it::len(files) <= BUDGET is not listed",
        ),
        Control(
            "a count of the repository held to a literal fails",
            _new_rail(_FILES + "    assert len(files) <= 1500\n"),
            expect="tests/rails/test_new.py::test_it::len(files) <= 1500 is not listed",
        ),
        Control(
            "an exact count of the repository fails",
            _new_rail("    assert len(sorted(REPO.rglob('*.md'))) == 112\n"),
            expect="test_it::len(sorted(REPO.rglob('*.md'))) == 112 is not listed",
        ),
        Control(
            "a count bound to a name first fails",
            _new_rail(_FILES + "    n = len(files)\n    assert n < 41\n"),
            expect="tests/rails/test_new.py::test_it::n < 41 is not listed",
        ),
        Control(
            "a count written on the right fails",
            _new_rail(_FILES + "    assert 40 >= sum(1 for f in files if f.suffix)\n"),
            expect="test_it::40 >= sum((1 for f in files if f.suffix)) is not listed",
        ),
        Control(
            "a count of a name a loop over the tree binds fails",
            _new_rail(
                "    for path in source_index.package_files():\n"
                "        lines = path.read_text().splitlines()\n"
                "        assert len(lines) <= 400\n"
            ),
            expect="tests/rails/test_new.py::test_it::len(lines) <= 400 is not listed",
        ),
        Control(
            "a count that reaches the tree through a helper module under tests fails",
            lambda root: (
                _write(
                    root,
                    "tests/walk_shared.py",
                    "from rails_repo import REPO\n\n\n"
                    "def walk():\n    return sorted(REPO.rglob('*.py'))\n",
                ),
                _write(
                    root,
                    "tests/rails/test_new.py",
                    "from walk_shared import walk\n\n\n"
                    "def test_it():\n    assert len(walk()) == 12\n",
                ),
            ),
            expect="tests/rails/test_new.py::test_it::len(walk()) == 12 is not listed",
        ),
        Control(
            "a count that reaches the tree through a module's file fails",
            lambda root: _write(
                root,
                "tests/rails/test_new.py",
                "from pathlib import Path\n\nimport personalclaw\n\n"
                "SRC = Path(personalclaw.__file__).parent\n\n\n"
                "def _modules():\n    return sorted(SRC.rglob('*.py'))\n\n\n"
                "def test_it():\n    assert len(_modules()) == 9\n",
            ),
            expect="tests/rails/test_new.py::test_it::len(_modules()) == 9 is not listed",
        ),
        Control(
            "a count in a chain of comparisons fails",
            _new_rail(_FILES + "    assert 0 < len(files) <= 40\n"),
            expect="tests/rails/test_new.py::test_it::0 < len(files) <= 40 is not listed",
        ),
        Control(
            "an exact count written on the right fails",
            _new_rail(_FILES + "    assert 12 == len(files)\n"),
            expect="tests/rails/test_new.py::test_it::12 == len(files) is not listed",
        ),
        Control(
            "a count of the test's own table held to a named number fails",
            _new_rail("    rows = [1, 2]\n    assert len(rows) <= CEILING\n", head="CEILING = 3\n"),
            expect="tests/rails/test_new.py::test_it::len(rows) <= CEILING is not listed",
        ),
        Control(
            "a count held to a number the function names fails",
            _new_rail(_FILES + "    limit = 40\n    assert len(files) <= limit\n"),
            expect="tests/rails/test_new.py::test_it::len(files) <= limit is not listed",
        ),
        Control(
            "a count held to a number imported from a helper module fails",
            lambda root: (
                _write(root, "tests/limits_shared.py", "CEILING = 40\n"),
                _new_rail(
                    "    rows = [1]\n    assert len(rows) <= CEILING\n",
                    head="from limits_shared import CEILING\n",
                )(root),
            ),
            expect="tests/rails/test_new.py::test_it::len(rows) <= CEILING is not listed",
        ),
        Control(
            "a count held to a number read off a helper module fails",
            lambda root: (
                _write(root, "tests/rails/limits_shared.py", "CEILING = 40\n"),
                _new_rail(
                    "    rows = [1]\n    assert len(rows) <= limits_shared.CEILING\n",
                    head="import limits_shared\n",
                )(root),
            ),
            expect="test_it::len(rows) <= limits_shared.CEILING is not listed",
        ),
        Control(
            "a count of what a helper module's function walks fails",
            lambda root: (
                _write(
                    root,
                    "tests/rails/walk_shared.py",
                    "import source_index\n\n\n"
                    "def walk():\n    return source_index.package_files()\n",
                ),
                _new_rail(
                    "    assert len(walk_shared.walk()) == 12\n", head="import walk_shared\n"
                )(root),
            ),
            expect="tests/rails/test_new.py::test_it::len(walk_shared.walk()) == 12 is not listed",
        ),
        Control(
            "a count of what a with block reads fails",
            _new_rail(
                "    with (REPO / 'notes.txt').open() as notes:\n"
                "        lines = notes.readlines()\n"
                "    assert len(lines) <= 300\n"
            ),
            expect="tests/rails/test_new.py::test_it::len(lines) <= 300 is not listed",
        ),
        Control(
            "a count of a name bound by := fails",
            _new_rail(
                "    if (found := sorted(REPO.glob('*'))):\n        assert len(found) <= 30\n"
            ),
            expect="tests/rails/test_new.py::test_it::len(found) <= 30 is not listed",
        ),
        Control(
            "a count a comprehension keeps from the tree fails",
            _new_rail(
                "    rows = [1, 2]\n"
                "    names = [name for name in rows if (REPO / str(name)).exists()]\n"
                "    assert len(names) <= 30\n"
            ),
            expect="tests/rails/test_new.py::test_it::len(names) <= 30 is not listed",
        ),
        Control(
            "a count the module binds fails where it is held",
            _new_rail("    assert FOUND <= 30\n", head="FOUND = len(sorted(REPO.glob('*')))\n"),
            expect="tests/rails/test_new.py::test_it::FOUND <= 30 is not listed",
        ),
        Control(
            "a count of a list a loop over the tree fills fails",
            _new_rail(
                "    found = []\n"
                "    for path in source_index.package_files():\n"
                "        found.append(path)\n"
                "    assert len(found) <= 40\n"
            ),
            expect="tests/rails/test_new.py::test_it::len(found) <= 40 is not listed",
        ),
        Control(
            "a count of a dict filled from the test's own table where the tree says fails",
            _new_rail(
                "    by_name = {}\n"
                "    for name in ('a', 'b', 'c'):\n"
                "        if (REPO / name).exists():\n"
                "            by_name[name] = 1\n"
                "    assert len(by_name) == 3\n"
            ),
            expect="tests/rails/test_new.py::test_it::len(by_name) == 3 is not listed",
        ),
        Control(
            "a count of a list the module fills from the tree fails",
            _new_rail(
                "    assert len(FOUND) <= 40\n",
                head=(
                    "FOUND = []\n"
                    "for _path in source_index.package_files():\n    FOUND.append(_path)\n"
                ),
            ),
            expect="tests/rails/test_new.py::test_it::len(FOUND) <= 40 is not listed",
        ),
        Control(
            "a count of a list extended by what the tree holds fails",
            _new_rail(
                "    found = []\n    found.extend(source_index.package_files())\n"
                "    assert len(found) <= 40\n"
            ),
            expect="tests/rails/test_new.py::test_it::len(found) <= 40 is not listed",
        ),
        Control(
            "a named number held to at most one fails too",
            _new_rail(_FILES + "    assert len(files) <= ONE\n", head="ONE = 1\n"),
            expect="tests/rails/test_new.py::test_it::len(files) <= ONE is not listed",
        ),
        Control(
            "a second ratchet in a listed rail fails",
            lambda root: _write(root, _RATCHET, _A_RATCHET + "    assert len(found) <= CEILING\n"),
            expect=f"{_RATCHET_SITE} #2 is not listed",
        ),
        Control(
            "a converted ratchet whose line stays fails: the list only shrinks",
            _drop_listed_ratchet,
            expect=f"Site {_RATCHET_SITE} is listed",
        ),
        Control(
            "a converted ratchet with its line deleted passes",
            lambda root: (_drop_listed_ratchet(root), _ratchets_of()(root)),
            fails=False,
        ),
        Control(
            "a ratchet outside tests/rails passes",
            lambda root: _write(root, "tests/test_outside_two.py", _A_RATCHET),
            fails=False,
        ),
        Control(
            "a list the test fills from its own table, and a number the test names, pass",
            _new_rail(
                "    planted = [REPO / 'a']\n"
                "    planted += [REPO / 'b', source_index.package_files()]\n"
                "    planted.append(REPO / 'c')\n"
                "    expected = 4\n"
                "    assert len(planted) == expected\n"
            ),
            fails=False,
        ),
        Control(
            "the test's own tables held to literals pass, though their entries read the tree",
            _new_rail(
                "    rows = [source_index.package_files(), REPO / 'a', REPO / 'b']\n"
                "    assert len(rows) == 3\n"
                "    paths = {name: REPO / name for name in SURFACES}\n"
                "    assert len(paths) == 3\n"
                "    assert len(sorted(TABLE)) == 3\n",
                head="SURFACES = ('a', 'b', 'c')\nTABLE = (REPO / 'a', REPO / 'b', REPO / 'c')\n",
            ),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, NO_RAIL_HOLDS_A_COUNT_TO_A_FIXED_NUMBER)


# ── #3937's line is the source-pin census's own ──────────────────────────────────────────────
#
# #3937 is done when the source-text pins outside tests/rails (the getsource sites above, and the
# literal pins on source text) fall to half their count at its starting commit. Both counts are
# taken by tests/source_pin_census.py and recorded in one row, which this row holds to the counter:
# taken under the counter's current rule, each count the sum of its kinds at a full commit sha, and
# the target half the baseline. A row taken under another rule measured something else.

_SHA_THEN = "1" * 40
_SHA_NOW = "2" * 40


def _measure_row(**changes: Any) -> dict[str, Any]:
    recorded: dict[str, Any] = {
        "counter": source_pin_census.COUNTER,
        "rule": source_pin_census.RULE,
        "baseline": {
            "sha": _SHA_THEN,
            "sites": 9,
            "getsource": 3,
            "in": 4,
            "not_in": 1,
            "count": 1,
        },
        "target": 4,
        "measured": {"sha": _SHA_NOW, "sites": 6, "getsource": 2, "in": 3, "not_in": 1, "count": 0},
    }
    for key, value in changes.items():
        side, _, field = key.partition("__")
        if field:
            recorded[side] = {**recorded[side], field: value}
        else:
            recorded[side] = value
    return recorded


def _write_measure(**changes: Any) -> Callable[[Path], None]:
    text = json.dumps(_measure_row(**changes), indent=2) + "\n"
    return lambda root: _write(root, source_pin_census.MEASURE, text)


def _measure_check(root: Path) -> list[str]:
    return source_pin_census.row_problems(root)


SOURCE_PIN_LINE_IS_THE_COUNTERS_OWN = Row(
    name="#3937's source-pin line is recorded by its own counter",
    check=_measure_check,
    scaffold=_write_measure(),
    controls=(
        Control(
            "a target that is not half the baseline fails",
            _write_measure(target=5),
            expect="the target 5 is not half the baseline's 9 sites (4)",
        ),
        Control(
            "a row taken under another rule fails",
            _write_measure(rule=source_pin_census.RULE + 1),
            expect=f"taken under rule {source_pin_census.RULE + 1}",
        ),
        Control(
            "a count that is not the sum of its kinds fails",
            _write_measure(measured__sites=7),
            expect="the measured's 7 sites are not the sum of its kinds",
        ),
        Control(
            "a kind left out fails",
            _write_measure(
                baseline={"sha": _SHA_THEN, "sites": 9, "getsource": 3, "in": 5, "count": 1}
            ),
            expect="the baseline lacks a count",
        ),
        Control(
            "a short sha fails",
            _write_measure(baseline__sha=_SHA_THEN[:8]),
            expect="the baseline names no full commit sha",
        ),
        Control(
            "a row naming another counter fails",
            _write_measure(counter="tests/getsource_census.py"),
            expect="not this counter",
        ),
        Control(
            "a missing row fails",
            _remove(source_pin_census.MEASURE),
            expect="is missing",
        ),
        Control(
            "a row that is not JSON fails",
            lambda root: _write(root, source_pin_census.MEASURE, "{baseline: 9}\n"),
            expect="is not JSON",
        ),
        Control(
            "a later measure passes, at the target or above it",
            _write_measure(
                measured={
                    "sha": "3" * 40,
                    "sites": 8,
                    "getsource": 3,
                    "in": 4,
                    "not_in": 1,
                    "count": 0,
                }
            ),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, SOURCE_PIN_LINE_IS_THE_COUNTERS_OWN)


# ── No workflow job outside the catalogue runs a test (#3909) ────────────────────────────────
# A test command belongs to a catalogue entry, whose command the row "every catalogue command is
# the workflow's" holds to its workflow job, token for token. That row reads only the jobs mapped
# to an entry, so a test run added to any other job, in any workflow file, would run with no
# entry, no junit and no tier. This row refuses one by its command word: each test runner below,
# behind the wrappers a step uses (``NAME=value``, ``env``, ``cd`` before ``&&``, ``uv run``,
# ``npx`` and ``npm exec``), in every workflow file and the local actions its jobs call.

#: The test runners, by their command word or its path's last part.
_TEST_RUNNERS = frozenset({"pytest", "vitest", "playwright", "jest", "mocha", "tox", "nox"})
#: The runners a Python runs as ``-m``.
_PYTHON_M_RUNNERS = frozenset({"pytest", "unittest", "tox", "nox"})
#: The make targets that run tests, and the npm scripts.
_MAKE_TEST_TARGET = re.compile(r"test|check|gate|e2e\S*|test-\S+")
_NPM_TEST_SCRIPT = re.compile(r"test\S*")
#: The options of each wrapper whose next word is their value.
_UV_RUN_VALUED = frozenset(
    {
        "--with",
        "--with-requirements",
        "--python",
        "-p",
        "--project",
        "--directory",
        "--extra",
        "--group",
        "--only-group",
        "--package",
        "--env-file",
    }
)
_NPX_VALUED = frozenset({"-p", "--package", "-c", "--call"})
_NPM_VALUED = frozenset({"--prefix", "-w", "--workspace"})
_ENV_VALUED = frozenset({"-u", "--unset", "-C", "--chdir"})
_MAKE_VALUED = frozenset({"-C", "--directory", "-f", "--file", "--makefile", "-I"})


def _skip_options(words: list[str], valued: frozenset[str]) -> list[str]:
    """``words`` after its leading options, each valued one with its value; ``--`` ends them."""
    while words and words[0].startswith("-"):
        if words[0] == "--":
            return words[1:]
        words = words[1 + (words[0] in valued) :]
    return words


def _test_runner(words: list[str]) -> str | None:
    """The test runner one command runs, read by its command word once the wrappers are peeled;
    None for any other command, so ``echo pytest`` or ``pip install pytest`` runs none."""
    words = [w.strip("()") for w in words if w.strip("()")]
    while True:
        while words and re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0]):
            words = words[1:]
        if not words:
            return None
        head = words[0].rsplit("/", 1)[-1]
        if head == "env":
            words = _skip_options(words[1:], _ENV_VALUED)
        elif head == "npx":
            words = _skip_options(words[1:], _NPX_VALUED)
        elif head == "npm" and _skip_options(words[1:], _NPM_VALUED)[:1] in (["exec"], ["x"]):
            words = _skip_options(_skip_options(words[1:], _NPM_VALUED)[1:], _NPX_VALUED)
        elif head == "uv" and words[1:2] == ["run"]:
            words = _skip_options(words[2:], _UV_RUN_VALUED)
        else:
            break
    head, args = words[0].rsplit("/", 1)[-1], words[1:]
    if head in _TEST_RUNNERS:
        return head
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", head):
        for i, arg in enumerate(args):
            if arg == "-m":
                module = args[i + 1] if i + 1 < len(args) else ""
                return f"python -m {module}" if module in _PYTHON_M_RUNNERS else None
            if not arg.startswith("-"):
                return None
        return None
    if head == "npm":
        args = _skip_options(args, _NPM_VALUED)
        if args[:1] in (["test"], ["t"], ["tst"]):
            return "npm test"
        if args[:1] in (["run"], ["run-script"]):
            script = _skip_options(args[1:], _NPM_VALUED)
            if script and _NPM_TEST_SCRIPT.fullmatch(script[0]):
                return f"npm run {script[0]}"
        return None
    if head == "make":
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in _MAKE_VALUED:
                i += 2
                continue
            if not arg.startswith("-") and "=" not in arg and _MAKE_TEST_TARGET.fullmatch(arg):
                return f"make {arg}"
            i += 1
    return None


def raw_test_command_violations(tree: Tree) -> list[str]:
    """No job of any workflow file runs a test runner unless the job is a catalogue entry's."""
    mapped = {c for entry in _CATALOGUE_JOBS.values() for c in entry.jobs}
    bad: list[str] = []
    for name, job_id, job in _jobs(tree):
        if (str(job_id) if name == "breadth.yml" else f"{name}:{job_id}") in mapped:
            continue
        for n, step in enumerate(_job_steps(tree, job), 1):
            for words in _commands(str(step.get("run", ""))):
                runner = _test_runner(words)
                if runner:
                    bad.append(
                        f"{name} job {job_id} runs {' '.join(words)} ({runner}, in"
                        f" {step.get('name') or f'step {n}'!r}), and no catalogue entry holds"
                        " the job: a test runs as an entry's command"
                    )
    return bad


def _raw_test(name: str, rel: str, job: str, run: str, expect: str = "") -> Control:
    """A step running ``run`` added first in ``job`` of ``rel``; ``run`` is the step's text
    after ``run:``, a multi-line block included. Red unless ``expect`` is empty and the
    plant is a pass."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        start = text.index(f"\n  {job}:\n")
        at = text.index("    steps:\n", start) + len("    steps:\n")
        _write(root, rel, text[:at] + f"      - name: planted\n        run: {run}\n" + text[at:])

    return Control(name, plant, fails=bool(expect), expect=expect)


_IN_MERGE_ORCHESTRATION = (".github/workflows/merge.yml", "changes")
_IN_BREADTH_ORCHESTRATION = (".github/workflows/breadth.yml", "freshness")
_IN_RELEASE = (".github/workflows/release-artifacts.yml", "build")
_IN_SMOKE = (".github/workflows/headless-run-smoke.yml", "smoke")


def _refused(name: str, where: tuple[str, str], run: str, command: str = "") -> Control:
    rel, job = where
    expect = f"{rel.rsplit('/', 1)[1]} job {job} runs {command or run}"
    return _raw_test(name, rel, job, run, expect=expect)


def _passes(name: str, where: tuple[str, str], run: str) -> Control:
    return _raw_test(name, *where, run)


NO_WORKFLOW_JOB_OUTSIDE_THE_CATALOGUE_RUNS_A_TEST = _toolchain_row(
    "no-workflow-job-outside-the-catalogue-runs-a-test",
    raw_test_command_violations,
    (
        _refused("pytest", _IN_MERGE_ORCHESTRATION, "pytest tests/test_more.py"),
        _refused("pytest by its path", _IN_MERGE_ORCHESTRATION, ".venv/bin/pytest tests"),
        _refused("python -m pytest", _IN_MERGE_ORCHESTRATION, "python -m pytest tests"),
        _refused("python3.13 -I -m pytest", _IN_RELEASE, "python3.13 -I -m pytest tests"),
        _refused("uv run pytest", _IN_BREADTH_ORCHESTRATION, "uv run pytest tests"),
        _refused(
            "uv run with options, python -m pytest",
            _IN_BREADTH_ORCHESTRATION,
            "uv run --frozen --extra dev python -m pytest tests",
        ),
        _refused("uv run -m pytest", _IN_SMOKE, "uv run -m pytest tests"),
        _refused("vitest", _IN_MERGE_ORCHESTRATION, "vitest run"),
        _refused("npx vitest", _IN_MERGE_ORCHESTRATION, "npx vitest run"),
        _refused("npx --yes vitest", _IN_RELEASE, "npx --yes vitest run"),
        _refused("npm exec vitest", _IN_RELEASE, "npm exec -- vitest run"),
        _refused("npm test", _IN_MERGE_ORCHESTRATION, "npm test"),
        _refused("npm test in a workspace", _IN_SMOKE, "npm --prefix web test"),
        _refused("npm run test", _IN_MERGE_ORCHESTRATION, "npm run test"),
        _refused("npm run test:web", _IN_RELEASE, "npm run test:web"),
        _refused("npm run test:e2e -w web", _IN_SMOKE, "npm run -w web test:e2e"),
        _refused("playwright", _IN_MERGE_ORCHESTRATION, "playwright test"),
        _refused("npx playwright", _IN_SMOKE, "npx playwright test"),
        _refused("jest", _IN_RELEASE, "jest"),
        _refused("mocha", _IN_RELEASE, "mocha test/"),
        _refused("tox", _IN_BREADTH_ORCHESTRATION, "tox -e py313"),
        _refused("nox", _IN_BREADTH_ORCHESTRATION, "nox -s tests"),
        _refused("python -m unittest", _IN_SMOKE, "python -m unittest discover"),
        _refused("make test", _IN_MERGE_ORCHESTRATION, "make test"),
        _refused("make check", _IN_MERGE_ORCHESTRATION, "make check"),
        _refused("make gate", _IN_RELEASE, "make gate"),
        _refused("make e2e", _IN_SMOKE, "make e2e"),
        _refused("make e2e-live", _IN_SMOKE, "make e2e-live"),
        _refused("make test-web", _IN_BREADTH_ORCHESTRATION, "make test-web"),
        _refused("make -C web test", _IN_RELEASE, "make -C web test"),
        _refused("make with a variable, then check", _IN_RELEASE, "make PYTHON=python3 check"),
        _refused(
            "after cd",
            _IN_MERGE_ORCHESTRATION,
            "cd web && npx vitest run",
            command="npx vitest run",
        ),
        _refused(
            "in a subshell after cd", _IN_RELEASE, "(cd web && npm test)", command="npm test)"
        ),
        _refused(
            "behind env", _IN_SMOKE, "env CI=true pytest tests", command="env CI=true pytest tests"
        ),
        _refused("behind env -u", _IN_SMOKE, "env -u HOME uv run pytest tests"),
        _refused("behind an assignment", _IN_RELEASE, "PYTHONPATH=tests pytest tests"),
        _refused("after a ;", _IN_RELEASE, "true; pytest tests", command="pytest tests"),
        _refused(
            "inside a multi-line run: |",
            _IN_BREADTH_ORCHESTRATION,
            "|\n          set -eu\n          echo starting\n          uv run pytest tests\n",
            command="uv run pytest tests",
        ),
        _passes("an echo that names pytest passes", _IN_MERGE_ORCHESTRATION, "echo pytest --co"),
        _passes("installing pytest passes", _IN_RELEASE, "uv pip install pytest"),
        _passes("python -c that imports pytest passes", _IN_RELEASE, "python -c 'import pytest'"),
        _passes("a build script passes", _IN_RELEASE, "npm run build"),
        _passes("make lint passes", _IN_RELEASE, "make lint"),
        _passes("make testdata passes: no such target name", _IN_RELEASE, "make testdata"),
        _passes("grep for pytest passes", _IN_SMOKE, "grep -rn pytest docs"),
        _passes("a comment naming pytest passes", _IN_SMOKE, "true  # pytest runs elsewhere"),
        _passes(
            "a test runner in an entry's own job passes",
            (".github/workflows/breadth.yml", "rails-macos"),
            "uv run pytest tests/test_more.py",
        ),
    ),
)

ROWS = (*ROWS, NO_WORKFLOW_JOB_OUTSIDE_THE_CATALOGUE_RUNS_A_TEST)


# ── A tier workflow runs each test through run-job (#3915, #5600) ────────────────────────────
# The tier workflows run a catalogue entry as one command, `python -m tools.ci run-job <id>`, so a
# test runs under the entry's own steps, cap, runner, Python and env: one definition. `pr.yml` and
# `merge.yml` hold that. `breadth.yml` still runs its legs' steps itself, each held to its entry
# token for token by "every catalogue command is the workflow's". This row refuses a step of a
# tier workflow that runs a test runner itself (`_test_runner`), outside the steps that still do.
# That list never grows: the Contract run judges a head with main's copy of this row, so a head
# can't add an entry. A listed step that goes is refused too, unless its job now runs run-job,
# which is how #5600 moves each leg in one merge.

#: The tier workflows, whose every test runs through run-job.
_RUN_JOB_TIERS = ("pr.yml", "merge.yml", "breadth.yml")
#: The steps that still run a test themselves, by workflow, job and step name: breadth.yml's legs,
#: until #5600 moves each one to run-job.
_STEPS_THAT_RUN_THEIR_OWN_TEST: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("breadth.yml", "ubuntu", "pytest (shard ${{ matrix.shard }})"),
        ("breadth.yml", "macos", "pytest (shard ${{ matrix.shard }})"),
        ("breadth.yml", "arm", "pytest (shard ${{ matrix.shard }})"),
        ("breadth.yml", "coverage-shard", "pytest (shard ${{ matrix.shard }})"),
        ("breadth.yml", "canary", "pytest (canary shard ${{ matrix.shard }})"),
        (
            "breadth.yml",
            "random-order",
            "pytest (random-order shard ${{ matrix.shard }}, first order)",
        ),
        (
            "breadth.yml",
            "random-order",
            "pytest (random-order shard ${{ matrix.shard }}, second order)",
        ),
        ("breadth.yml", "rails-macos", "pytest (every rail, tests/rails)"),
        (
            "breadth.yml",
            "collection-parses",
            "pytest (collect the whole suite, counting parses of the source tree)",
        ),
        ("breadth.yml", "security", "pytest (adversarial scanner corpus)"),
        ("breadth.yml", "security-macos", "pytest (adversarial scanner corpus)"),
        *(
            (
                "breadth.yml",
                f"planned-ids-{leg}",
                "pytest (collect the planned suite, writing its ids)",
            )
            for leg in ("ubuntu", "macos", "arm", "coverage-shard", "canary", "random-order")
        ),
    }
)


def _runs_run_job(words: list[str]) -> bool:
    """Whether one command is ``python -m tools.ci run-job``, behind ``uv run`` or not."""
    words = [w for w in words if not re.fullmatch(r"[A-Za-z_]\w*=\S*", w)]
    if words[:2] == ["uv", "run"]:
        words = _skip_options(words[2:], _UV_RUN_VALUED)
    if not words or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", words[0].rsplit("/", 1)[-1]):
        return False
    args = [w for w in words[1:] if w != "-I"]
    return args[:3] == ["-m", "tools.ci", "run-job"]


def own_test_step_violations(tree: Tree) -> list[str]:
    """No step of a tier workflow runs a test runner itself, but the listed ones; and no listed
    step is gone unless its job runs run-job now."""
    bad: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    runs_run_job: set[tuple[str, str]] = set()
    for name, job_id, job in _jobs(tree):
        if name not in _RUN_JOB_TIERS:
            continue
        for n, step in enumerate(_job_steps(tree, job), 1):
            label = str(step.get("name") or f"step {n}")
            for words in _commands(str(step.get("run", ""))):
                if _runs_run_job(words):
                    runs_run_job.add((name, str(job_id)))
                runner = _test_runner(words)
                if not runner:
                    continue
                key = (name, str(job_id), label)
                if key in _STEPS_THAT_RUN_THEIR_OWN_TEST:
                    seen.add(key)
                    continue
                bad.append(
                    f"{name} job {job_id} step {label!r} runs {' '.join(words)} ({runner})"
                    " itself: a tier workflow runs each test through"
                    " `python -m tools.ci run-job <id>`"
                )
    for name, job_id, label in sorted(_STEPS_THAT_RUN_THEIR_OWN_TEST - seen):
        if (name, job_id) not in runs_run_job:
            bad.append(
                f"{name} job {job_id} step {label!r} runs no test any more, and its job runs no"
                " run-job: a leg that stops running its tests is a check gone"
            )
    return bad


#: Where the plants put a step: an orchestration job of each tier, and a breadth leg.
_IN_PR_STAGE = (".github/workflows/pr.yml", "test")
_IN_BREADTH_LEG = (".github/workflows/breadth.yml", "ubuntu")


def _own_test(name: str, where: tuple[str, str], run: str, expect: str = "") -> Control:
    rel, job = where
    return _raw_test(name, rel, job, run, expect=expect)


A_TIER_WORKFLOW_RUNS_EACH_TEST_THROUGH_RUN_JOB = _toolchain_row(
    "a-tier-workflow-runs-each-test-through-run-job",
    own_test_step_violations,
    (
        _own_test(
            "a pytest step in merge.yml",
            _IN_MERGE_ORCHESTRATION,
            "pytest tests/test_more.py",
            expect="merge.yml job changes step 'planted' runs pytest tests/test_more.py (pytest)",
        ),
        _own_test(
            "a vitest step in pr.yml's stage job",
            _IN_PR_STAGE,
            "npx vitest run",
            expect="pr.yml job test step 'planted' runs npx vitest run (vitest)",
        ),
        _own_test(
            "a new pytest step in a breadth leg, beside its listed one",
            _IN_BREADTH_LEG,
            "uv run pytest tests/test_more.py",
            expect="breadth.yml job ubuntu step 'planted' runs uv run pytest tests/test_more.py",
        ),
        Control(
            "a listed step renamed",
            _edit(
                _BREADTH,
                "      - name: pytest (every rail, tests/rails)\n",
                "      - name: pytest (the rails)\n",
            ),
            expect="breadth.yml job rails-macos step 'pytest (the rails)' runs uv run pytest",
        ),
        Control(
            "a listed step gone, and its job running no run-job",
            _edit(_BREADTH, _RAILS_MACOS_STEP, "        run: echo no rails tonight\n"),
            expect=(
                "breadth.yml job rails-macos step 'pytest (every rail, tests/rails)' runs no"
                " test any more, and its job runs no run-job"
            ),
        ),
        Control(
            "a listed step moved to run-job passes",
            _edit(
                _BREADTH,
                _RAILS_MACOS_STEP,
                "        run: python3 -m tools.ci run-job rails-macos\n",
            ),
            fails=False,
        ),
        _own_test(
            "run-job in a tier workflow passes",
            _IN_MERGE_ORCHESTRATION,
            "python3 -m tools.ci run-job static",
        ),
        _own_test(
            "an echo that names pytest passes",
            _IN_PR_STAGE,
            "echo pytest runs through run-job",
        ),
    ),
)

ROWS = (*ROWS, A_TIER_WORKFLOW_RUNS_EACH_TEST_THROUGH_RUN_JOB)


# ── The harness's replay and exemplar gates run in the merge tier (#3909) ─────────────────────
# `replay` and `exemplars` are harness profiles, not catalogue entries: the merge tier runs what
# they gate already. python-suite collects the three test files that gate every replay scenario
# against its baseline and run every exemplar's smoke script, and web-unit's vitest run includes
# the replay fold's test. This row holds each link of that chain, so a gate that leaves the suite
# or the web-unit set goes red here rather than running nowhere.

#: The pytest files python-suite must collect, each with the test that is the gate.
_HARNESS_GATES = {
    "tests/test_harness_replay.py": "test_shipped_scenarios_pass_baseline",
    "tests/test_harness_replay_scenarios.py": "test_workflow_scenarios_present_and_green",
    "tests/test_harness_exemplars.py": (
        "test_each_exemplar_smoke_script_runs_and_proves_its_mechanism"
    ),
}
_REPLAY_FOLD = "web/src/harness/replayFold.test.ts"
_VITEST_CONFIG = "web/vitest.config.ts"
#: The vitest config's four lines that decide its file set, as this row reads them: the dom
#: project's include, its exclude of the node files and the forks files, the node project's
#: include of the node files, and the dom-forks project's include of the forks files. Any other
#: shape is refused, so a narrowed set cannot pass unread.
_VITEST_SET_LINES = (
    "include: ['src/**/*.{test,spec}.{ts,tsx}'],",
    "exclude: [...NODE_FILES, ...FORKS_FILES],",
    "include: NODE_FILES,",
    "include: FORKS_FILES,",
)
_SLICES = re.compile(r"^_EXPECTED_SLICES = \{([^}]*)\}$", re.M)


def harness_gates_violations(root: Path) -> list[str]:
    """python-suite collects the replay and exemplar gates, the exemplars' pinned set is every
    slice, and web-unit's vitest run includes the replay fold's test."""
    bad: list[str] = []
    collected = set(shards.universe(root))
    for rel, gate in _HARNESS_GATES.items():
        if rel not in collected:
            bad.append(f"{rel} is not in the suite's universe, so python-suite runs no {gate}")
            continue
        text = (root / rel).read_text(encoding="utf-8")
        if not re.search(rf"^def {gate}\(", text, re.M):
            bad.append(f"{rel} has no {gate}: the gate the merge tier runs is gone")
    exemplars = root / "tests/test_harness_exemplars.py"
    if exemplars.is_file():
        pinned = _SLICES.search(exemplars.read_text(encoding="utf-8"))
        named = set(re.findall(r'"(slice_\d+)"', pinned.group(1))) if pinned else set()
        present = {p.name for p in (root / "harness/exemplars").glob("slice_*") if p.is_dir()}
        if not pinned:
            bad.append("tests/test_harness_exemplars.py pins no _EXPECTED_SLICES set")
        for name in sorted(present - named):
            bad.append(f"harness/exemplars/{name} is not in _EXPECTED_SLICES, so it can vanish")
    cat = tomllib.loads((root / catalogue.CATALOGUE).read_text(encoding="utf-8"))
    entries = {str(j.get("id")): j for j in cat.get("job") or []}
    web = entries.get("web-unit") or {}
    at_root = [
        list(step.get("argv") or []) for step in web.get("command") or [] if not step.get("workdir")
    ]
    if "merge" not in (web.get("tiers") or []):
        bad.append("web-unit is not in the merge tier")
    if ["npm", "run", "test:web"] not in at_root:
        bad.append("web-unit's command runs no `npm run test:web` from the repository root")
    root_scripts = json.loads((root / "package.json").read_text(encoding="utf-8"))["scripts"]
    web_scripts = json.loads((root / "web/package.json").read_text(encoding="utf-8"))["scripts"]
    if root_scripts.get("test:web") != "npm run test --workspace=web":
        bad.append(f"package.json test:web is {root_scripts.get('test:web')!r}, not the web test")
    if web_scripts.get("test") != "vitest run":
        bad.append(f"web/package.json test is {web_scripts.get('test')!r}, not `vitest run`")
    config = [line.strip() for line in (root / _VITEST_CONFIG).read_text().splitlines()]
    shape = [line for line in config if re.match(r"(include|exclude):", line)]
    if shape != list(_VITEST_SET_LINES):
        bad.append(
            f"{_VITEST_CONFIG}'s file set is {shape}, which this row does not read: it reads"
            f" {list(_VITEST_SET_LINES)}"
        )
    # Under web/src and named *.test.ts, the fold's test is in the dom include's glob; the
    # node project takes it from there when the census says it needs no DOM. Either project
    # is web-unit's run.
    if not (root / _REPLAY_FOLD).is_file():
        bad.append(f"{_REPLAY_FOLD} is gone, so web-unit runs no replay fold test")
    return bad


def _harness_gates_scaffold(root: Path) -> None:
    """The files the row reads, as the tree has them, and an empty folder per exemplar slice."""
    for rel in (
        *_HARNESS_GATES,
        catalogue.CATALOGUE,
        "pyproject.toml",
        "package.json",
        "web/package.json",
        _VITEST_CONFIG,
        _REPLAY_FOLD,
    ):
        _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))
    for slice_dir in sorted((REPO / "harness/exemplars").glob("slice_*")):
        if slice_dir.is_dir():
            (root / "harness/exemplars" / slice_dir.name).mkdir(parents=True, exist_ok=True)


def _moved(name: str, old: str, new: str, expect: str) -> Control:
    def plant(root: Path) -> None:
        (root / new).parent.mkdir(parents=True, exist_ok=True)
        (root / old).rename(root / new)

    return Control(name, plant, True, expect)


def _new_slice(root: Path) -> None:
    (root / "harness/exemplars/slice_9").mkdir(parents=True)


THE_HARNESS_GATES_RUN_IN_THE_MERGE_TIER = Row(
    name="the-harness-replay-and-exemplar-gates-run-in-the-merge-tier",
    check=harness_gates_violations,
    scaffold=_harness_gates_scaffold,
    controls=(
        *(
            _moved(
                f"{rel} renamed out of pytest's python_files",
                rel,
                rel.replace("tests/test_", "tests/check_"),
                f"{rel} is not in the suite's universe",
            )
            for rel in _HARNESS_GATES
        ),
        _moved(
            "the replay gate moved under tests/rails, which the suite prunes",
            "tests/test_harness_replay.py",
            "tests/rails/test_harness_replay.py",
            "tests/test_harness_replay.py is not in the suite's universe",
        ),
        *(
            _plant(
                f"{gate} renamed",
                rel,
                f"def {gate}(",
                f"def _{gate}(",
                expect=f"{rel} has no {gate}",
            )
            for rel, gate in _HARNESS_GATES.items()
        ),
        _plant(
            "a slice dropped from _EXPECTED_SLICES",
            "tests/test_harness_exemplars.py",
            ', "slice_5"}',
            "}",
            expect="harness/exemplars/slice_5 is not in _EXPECTED_SLICES",
        ),
        Control(
            "a new slice the pinned set does not name",
            _new_slice,
            True,
            "harness/exemplars/slice_9 is not in _EXPECTED_SLICES",
        ),
        _plant(
            "web-unit without npm run test:web",
            catalogue.CATALOGUE,
            '  { argv = ["npm", "run", "test:web"] },\n',
            "",
            expect="web-unit's command runs no `npm run test:web`",
        ),
        _plant(
            "the root test:web script pointed elsewhere",
            "package.json",
            '"test:web": "npm run test --workspace=web"',
            '"test:web": "npm run test --workspace=desktop"',
            expect="package.json test:web is",
        ),
        _plant(
            "the web test script narrowed to one folder",
            "web/package.json",
            '"test": "vitest run"',
            '"test": "vitest run src/app"',
            expect="web/package.json test is",
        ),
        _plant(
            "the vitest include narrowed",
            _VITEST_CONFIG,
            "include: ['src/**/*.{test,spec}.{ts,tsx}'],",
            "include: ['src/{app,pages}/**/*.{test,spec}.{ts,tsx}'],",
            expect="which this row does not read",
        ),
        _plant(
            "the harness folder excluded",
            _VITEST_CONFIG,
            "exclude: [...NODE_FILES, ...FORKS_FILES],",
            "exclude: [...NODE_FILES, ...FORKS_FILES, 'src/harness/**'],",
            expect="which this row does not read",
        ),
        _plant(
            "the forks project's files dropped",
            _VITEST_CONFIG,
            "include: FORKS_FILES,",
            "include: [],",
            expect="which this row does not read",
        ),
        _moved(
            "replayFold.test.ts renamed out of the test glob",
            _REPLAY_FOLD,
            "web/src/harness/replayFold.check.ts",
            f"{_REPLAY_FOLD} is gone",
        ),
    ),
)

ROWS = (*ROWS, THE_HARNESS_GATES_RUN_IN_THE_MERGE_TIER)


# ── Each whole-tree check is in every tier that needs it, as a step that fails its job ───────
#
# A whole-tree check runs in the job that owns it (#3929): the rails in rails (and in breadth's
# rails-macos), the rails census and the gate aggregate in rails-checks, harness validate and
# the whole-tree harness scan in static, the security corpus in security (and in breadth's
# security-macos). Breadth runs no static, so its whole-tree scan is a step of rails-macos. This
# row holds the catalogue to it: each check runs in a job of every tier listed for it, and a check
# marked once runs in exactly one job of each such tier, so it is neither skipped nor run twice.
# Static's validate runs with --strict, so a warning fails it as an error does: no other run checks
# that the shipped specs carry none. The scan is the whole tree's: `scan --diff` judges only a
# change's lines, so it is not the check. The tier workflows run each change and merge entry's
# steps through run-job, which fails the job on the first step that fails, so a check of those
# tiers alone is held by the catalogue half. It holds breadth.yml, which writes its own steps, to
# the other half: each check of the breadth tier is a step of it, and a step that runs harness
# validate, harness scan, the gate aggregate or the rails census fails its job, with no
# continue-on-error on the step or its job, no `|| true` after the command, and no `if:` that
# can skip it while the job goes on green. A breadth leg that records an entry may gate the step
# on its carry decision alone, since a step that decision skips is the earlier entry carried. The
# outcome that leg records is evaluated as GitHub would, with the step's outcome set to each one
# but success and every other step's to success or failure: none of them may record success, so
# a later night that carries the entry carries the step's red. The breadth legs that run the rails
# and the security corpus record the step's outcome and fail the job on it in their last step, so
# they are held by the tiers half.

#: ``(what, the words its argv holds, the tiers that need it, whether exactly one job runs it,
#: the text a workflow step that runs it carries)``.
OWNED_CHECKS: tuple[tuple[str, frozenset[str], tuple[str, ...], bool, str], ...] = (
    (
        "harness validate",
        frozenset({"-m", "harness", "validate"}),
        ("change", "merge"),
        True,
        "python -m harness validate",
    ),
    (
        "harness validate --strict",
        frozenset({"-m", "harness", "validate", "--strict"}),
        ("change", "merge"),
        False,
        "python -m harness validate --strict",
    ),
    (
        "the whole-tree harness scan",
        frozenset({"-m", "harness", "scan"}),
        ("change", "merge", "breadth"),
        True,
        "python -m harness scan",
    ),
    (
        "the gate aggregate",
        frozenset({"scripts/gate_report.py"}),
        ("change", "merge"),
        True,
        "scripts/gate_report.py",
    ),
    (
        "the rails census",
        frozenset({"-m", "tools.ci", "rails", "check"}),
        ("change", "merge"),
        True,
        "python -m tools.ci rails check",
    ),
    (
        "the rails",
        frozenset({"pytest", "tests/rails"}),
        ("change", "merge", "breadth"),
        True,
        "tests/rails",
    ),
    (
        "the security corpus",
        frozenset({"pytest", "tests/security"}),
        ("change", "merge", "breadth"),
        False,
        "tests/security",
    ),
)

#: The words that make a step some other run than the check's: a scan of the diff judges only
#: the lines a change touched, not the tree.
_NOT_THE_CHECK: dict[str, frozenset[str]] = {"the whole-tree harness scan": frozenset({"--diff"})}

_OWNED_WORKFLOWS = (".github/workflows/breadth.yml",)
#: The checks whose step must itself fail its job.
_FAILS_ITS_JOB = frozenset(
    {"harness validate", "the whole-tree harness scan", "the gate aggregate", "the rails census"}
)
_SWALLOWED = re.compile(r"\|\|\s*(?:true|:)(?:\s|$|;)")


#: The one `if:` a step that runs an owned check may carry, in a breadth leg that records an
#: entry: its carry decision's. Anywhere else an `if:` can skip the check and leave its job green.
_CARRY_GATE = "steps.decide.outputs.run != 'false'"
#: The outcomes of a step that did not pass: ``""`` is one the context does not name.
_NOT_PASSED = ("failure", "cancelled", "skipped", "")


def _entry_outcome(job: dict[str, Any]) -> str | None:
    """The outcome a breadth leg records in its entry (``breadth_evidence.py ran``), which a
    later night on the same inputs may carry, or None for a job that records none."""
    for step in job.get("steps") or []:
        if isinstance(step, dict) and "breadth_evidence.py ran" in str(step.get("run", "")):
            return str((step.get("env") or {}).get("OUTCOME", ""))
    return None


def _condition(value: object) -> str:
    """An `if:` as GitHub reads it, with or without its ``${{ }}``, its spaces collapsed."""
    text = str(value).strip()
    match = _EXPRESSION.fullmatch(text)
    return " ".join((match.group(1) if match else text).split())


def _recorded(outcome: str, job: dict[str, Any], outcomes: dict[str, str]) -> str:
    """*outcome* as the record step renders it when each of the job's steps ended as
    *outcomes* says (success where it names none). A continue-on-error step concludes success
    whatever its outcome."""
    steps: dict[str, dict[str, str]] = {}
    for step in job.get("steps") or []:
        if isinstance(step, dict) and step.get("id"):
            sid = str(step["id"])
            ended = outcomes.get(sid, "success")
            concluded = "success" if step.get("continue-on-error") else ended
            steps[sid] = {"outcome": ended, "conclusion": concluded}
    return _rendered(outcome, {"steps": steps})


def _carried_red_violations(
    where: str, step: dict[str, Any], job: dict[str, Any], outcome: str
) -> list[str]:
    """The outcome a leg records is success whenever every step passed, and never when *step*
    did not pass, whatever its other steps did."""
    sid = str(step.get("id") or "")
    if not sid:
        return [f"{where} has no id, so the outcome its leg's entry records cannot read it"]
    others = [
        str(s["id"])
        for s in job.get("steps") or []
        if isinstance(s, dict) and s.get("id") and str(s["id"]) != sid
    ]
    try:
        green = _recorded(outcome, job, {})
        if green != "success":
            return [f"{where}: its leg's entry records {green!r} when every step passes"]
        for ended in _NOT_PASSED:
            for rest in itertools.product(("success", "failure"), repeat=len(others)):
                if _recorded(outcome, job, {**dict(zip(others, rest)), sid: ended}) == "success":
                    return [
                        f"{where} is {ended!r}, and its leg's entry records 'success', so a later"
                        " night that carries the entry carries the red as green"
                    ]
    except ValueError as exc:
        return [f"{where}: the outcome its leg's entry records is {exc}"]
    return []


def owning_job_violations(root: Path) -> list[str]:
    """Each owned check is in a job of every tier that needs it (exactly one where marked), each
    of the breadth tier is a breadth.yml step, and every breadth.yml step that runs one fails its
    job, every time the job runs, and its red in any entry its leg records."""
    try:
        cat = catalogue.read(root)
    except FileNotFoundError:
        return [f"{catalogue.CATALOGUE} is missing"]
    except catalogue.CatalogueError as exc:
        return exc.problems
    bad: list[str] = []
    for what, words, tiers, once, _text in OWNED_CHECKS:
        other = _NOT_THE_CHECK.get(what, frozenset())
        for tier in tiers:
            runs = sorted(
                job.id
                for job in cat.tier(tier)
                if any(
                    words <= set(step.argv) and not other & set(step.argv) for step in job.command
                )
            )
            if not runs:
                bad.append(f"{what} runs in no job of the {tier} tier")
            elif once and len(runs) > 1:
                bad.append(f"{what} runs in {len(runs)} jobs of the {tier} tier: {runs}")
    seen: set[str] = set()
    for rel in _OWNED_WORKFLOWS:
        workflow = yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}
        for job_id, job in (workflow.get("jobs") or {}).items():
            outcome = _entry_outcome(job)
            for step in job.get("steps") or []:
                run = step.get("run") if isinstance(step, dict) else None
                if not isinstance(run, str):
                    continue
                for what, _words, _tiers, _once, text in OWNED_CHECKS:
                    if text not in run:
                        continue
                    seen.add(what)
                    if what not in _FAILS_ITS_JOB:
                        continue
                    where = f"{rel} {job_id}: the step that runs {what}"
                    if step.get("continue-on-error") or job.get("continue-on-error"):
                        bad.append(f"{where} has continue-on-error, so its red is swallowed")
                    for line in run.splitlines():
                        if text in line and _SWALLOWED.search(line):
                            bad.append(f"{where} ends in `|| true`, so its red is swallowed")
                    gate = step.get("if")
                    if gate is not None and (outcome is None or _condition(gate) != _CARRY_GATE):
                        bad.append(
                            f"{where} runs under `if: {gate}`, so a run that skips it passes its"
                            " job unchecked"
                        )
                    if outcome is not None:
                        bad.extend(_carried_red_violations(where, step, job, outcome))
    for what, _words, tiers, _once, _text in OWNED_CHECKS:
        if "breadth" in tiers and what not in seen:
            bad.append(f"no breadth.yml step runs {what}")
    return bad


_STATIC = 'id = "static"\ntiers = ["change", "entry", "merge"]'
#: static's entry down to its lint_feedback flag, which only a change-tier job may set.
_STATIC_ENTRY = (
    f"{_STATIC}\n"
    'inputs = [":(glob)**"]\nexternal_inputs = []\ntoolchain = ["uv-sync", "shellcheck", "dash"]\n'
    'cache = ["uv"]\nsecurity = "ordinary"\nlint_feedback = true\n'
)
_RAILS_CHECKS = 'id = "rails-checks"\ntiers = ["change", "entry", "merge"]'
_VALIDATE_STEP = '  { argv = ["uv", "run", "python", "-m", "harness", "validate", "--strict"] },\n'
_SCAN_STEP = '  { argv = ["uv", "run", "python", "-m", "harness", "scan"] },\n'
_RAILS_MACOS_PYTEST = (
    '  { argv = ["uv", "run", "pytest", "-n", "2", "--dist", "worksteal", "-q", "tests/rails",'
    ' "--junitxml=reports/junit-rails-macos.xml"] },\n'
)
_GATES_STEP = '  { argv = ["uv", "run", "python", "scripts/gate_report.py"] },\n'
#: The start of a breadth-only job's command (security-macos's), where a planted step is in no
#: other tier.
_BREADTH_ONLY_COMMAND = 'junit = ["reports/junit-security-corpus-macos.xml"]\ncommand = [\n'
_BREADTH_SCAN_GATE = "        id: scan\n        if: ${{ steps.decide.outputs.run != 'false' }}\n"


def _entry_records(name: str, expression: str, fails: bool = True, expect: str = "") -> Control:
    """rails-macos's record step records *expression*, whatever it recorded before: the plant
    finds the step the row reads, so a rewritten OUTCOME cannot leave it without its text."""

    def plant(root: Path) -> None:
        path = root / _OWNED_WORKFLOWS[0]
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        records = [
            step
            for step in workflow["jobs"]["rails-macos"]["steps"]
            if "breadth_evidence.py ran" in str(step.get("run", ""))
        ]
        assert len(records) == 1, f"the plant {name!r} found {len(records)} record steps"
        records[0].setdefault("env", {})["OUTCOME"] = expression
        _write(root, _OWNED_WORKFLOWS[0], yaml.safe_dump(workflow, sort_keys=False))

    return Control(name, plant, fails, expect)


_SCAN_RED_CARRIED = (
    "breadth.yml rails-macos: the step that runs the whole-tree harness scan is {!r}, and its"
    " leg's entry records 'success'"
)

EACH_OWNING_JOB_IS_IN_EVERY_TIER_THAT_NEEDS_IT = Row(
    name="each-whole-tree-check-runs-in-every-tier-that-needs-it-as-a-step-that-fails-its-job",
    check=owning_job_violations,
    scaffold=_catalogue_scaffold,
    controls=(
        _plant(
            "harness validate dropped from static",
            catalogue.CATALOGUE,
            _VALIDATE_STEP,
            "",
            expect="harness validate runs in no job of the change tier",
        ),
        _plant(
            "static's validate without --strict, which passes a warning",
            catalogue.CATALOGUE,
            _VALIDATE_STEP,
            _VALIDATE_STEP.replace(', "--strict"', ""),
            expect="harness validate --strict runs in no job of the change tier",
        ),
        _plant(
            # Its lint remediation goes with it: the loader holds that job to the change tier.
            "static held to the merge tier only",
            catalogue.CATALOGUE,
            _STATIC_ENTRY,
            _STATIC_ENTRY.replace('"change", ', "").replace("lint_feedback = true\n", ""),
            expect="the whole-tree harness scan runs in no job of the change tier",
        ),
        # ci.yml's scan step under a pull-request `if:` left the merge tier with no scan; the
        # merge tier's scan is static's, so its plant takes static out of that tier.
        _plants(
            "static held to the change tier only, so merge scans no tree",
            catalogue.CATALOGUE,
            (
                (_STATIC_ENTRY, _STATIC_ENTRY.replace('"change", "entry", "merge"', '"change"')),
                ('[tier.merge]\njobs = [\n  "static",\n', "[tier.merge]\njobs = [\n"),
                ('[tier.entry]\njobs = [\n  "static",\n', "[tier.entry]\njobs = [\n"),
            ),
            expect="the whole-tree harness scan runs in no job of the merge tier",
        ),
        _plant(
            "`|| true` after the gate aggregate, through a shell",
            catalogue.CATALOGUE,
            '  { argv = ["uv", "run", "python", "scripts/gate_report.py"] },\n',
            '  { argv = ["sh", "-c", "uv run python scripts/gate_report.py || true"] },\n',
            expect="the gate aggregate runs in no job of the change tier",
        ),
        _plant(
            "static's scan back to the diff, which judges only a change's lines",
            catalogue.CATALOGUE,
            _VALIDATE_STEP + _SCAN_STEP,
            _VALIDATE_STEP + _SCAN_STEP.replace('"scan"', '"scan", "--diff", "--base", "{base}"'),
            expect="the whole-tree harness scan runs in no job of the change tier",
        ),
        _plant(
            "the scan dropped from rails-macos, so breadth scans no tree",
            catalogue.CATALOGUE,
            _RAILS_MACOS_PYTEST + _SCAN_STEP,
            _RAILS_MACOS_PYTEST,
            expect="the whole-tree harness scan runs in no job of the breadth tier",
        ),
        _plant(
            "a second whole-tree scan in breadth, in security-macos",
            catalogue.CATALOGUE,
            _BREADTH_ONLY_COMMAND,
            _BREADTH_ONLY_COMMAND + _SCAN_STEP,
            expect="the whole-tree harness scan runs in 2 jobs of the breadth tier",
        ),
        _entry_records(
            "rails-macos's entry recording the rails' outcome alone",
            "${{ steps.pytest.outcome }}",
            expect=_SCAN_RED_CARRIED.format("failure"),
        ),
        _entry_records(
            "rails-macos's entry naming the scan only as the rails' fallback",
            "${{ steps.pytest.outcome || steps.scan.outcome }}",
            expect=_SCAN_RED_CARRIED.format("failure"),
        ),
        _entry_records(
            "rails-macos's entry red on a failed scan, and green on a cancelled one",
            "${{ steps.scan.outcome != 'failure' && steps.pytest.outcome || 'failure' }}",
            expect=_SCAN_RED_CARRIED.format("cancelled"),
        ),
        _entry_records(
            "rails-macos's entry recording 'failure' whatever ran, so no outcome is read",
            "${{ 'failure' }}",
            expect="its leg's entry records 'failure' when every step passes",
        ),
        _entry_records(
            "rails-macos's entry read through a function the row cannot evaluate",
            "${{ format('{0}', steps.scan.outcome) }}",
            expect="cannot evaluate",
        ),
        _entry_records(
            "rails-macos's entry green only when the rails and the scan both are, written"
            " another way, passes",
            "${{ steps.pytest.outcome == 'success' && steps.scan.outcome == 'success'"
            " && 'success' || 'failure' }}",
            fails=False,
        ),
        _plant(
            "breadth's scan step with no id, so its leg's entry cannot read it",
            _OWNED_WORKFLOWS[0],
            "        id: scan\n",
            "",
            expect="breadth.yml rails-macos: the step that runs the whole-tree harness scan has"
            " no id",
        ),
        _plant(
            "continue-on-error on breadth's scan step",
            _OWNED_WORKFLOWS[0],
            "        id: scan\n",
            "        id: scan\n        continue-on-error: true\n",
            expect="breadth.yml rails-macos: the step that runs the whole-tree harness scan has"
            " continue-on-error",
        ),
        _plant(
            "breadth's scan step gone from breadth.yml, its entry kept",
            _OWNED_WORKFLOWS[0],
            "        run: uv run python -m harness scan\n",
            "        run: echo scanned\n",
            expect="no breadth.yml step runs the whole-tree harness scan",
        ),
        _plant(
            "breadth's scan step gated on more than the carry decision",
            _OWNED_WORKFLOWS[0],
            _BREADTH_SCAN_GATE,
            _BREADTH_SCAN_GATE.replace(
                "!= 'false' }}", "!= 'false' && github.event_name == 'schedule' }}"
            ),
            expect="breadth.yml rails-macos: the step that runs the whole-tree harness scan runs"
            " under `if:",
        ),
        _plant(
            "breadth's scan step under the carry decision written without ${{ }}, passes",
            _OWNED_WORKFLOWS[0],
            _BREADTH_SCAN_GATE,
            "        id: scan\n        if: steps.decide.outputs.run != 'false'\n",
            fails=False,
        ),
        _plant(
            "rails-checks held to the merge tier only",
            catalogue.CATALOGUE,
            _RAILS_CHECKS,
            'id = "rails-checks"\ntiers = ["entry", "merge"]',
            expect="the gate aggregate runs in no job of the change tier",
        ),
        _plant(
            "the gate aggregate run a second time, in static",
            catalogue.CATALOGUE,
            _VALIDATE_STEP,
            _VALIDATE_STEP + _GATES_STEP,
            expect="the gate aggregate runs in 2 jobs of the merge tier",
        ),
        _plants(
            "the security corpus moved out of breadth, both legs",
            catalogue.CATALOGUE,
            (
                (
                    'id = "security"\ntiers = ["change", "entry", "merge", "breadth"]',
                    'id = "security"\ntiers = ["change", "entry", "merge"]',
                ),
                (
                    'id = "security-macos"\ntiers = ["breadth"]',
                    'id = "security-macos"\ntiers = ["weekly"]',
                ),
            ),
            expect="the security corpus runs in no job of the breadth tier",
        ),
        _plant(
            "the security corpus held out of the change tier",
            catalogue.CATALOGUE,
            'id = "security"\ntiers = ["change", "entry", "merge", "breadth"]',
            'id = "security"\ntiers = ["entry", "merge", "breadth"]',
            expect="the security corpus runs in no job of the change tier",
        ),
        _plant(
            "`|| true` after breadth's scan",
            _OWNED_WORKFLOWS[0],
            "        run: uv run python -m harness scan\n",
            "        run: uv run python -m harness scan || true\n",
            expect="the step that runs the whole-tree harness scan ends in `|| true`",
        ),
        _plant(
            "continue-on-error on the whole rails-macos job",
            _OWNED_WORKFLOWS[0],
            "\n  rails-macos:\n",
            "\n  rails-macos:\n    continue-on-error: true\n",
            expect="rails-macos: the step that runs the whole-tree harness scan has"
            " continue-on-error",
        ),
        _plant(
            "the gate aggregate also in a breadth job, a tier that does not list it, passes",
            catalogue.CATALOGUE,
            _BREADTH_ONLY_COMMAND,
            _BREADTH_ONLY_COMMAND + _GATES_STEP,
            fails=False,
        ),
        _plant(
            "a second rails job in breadth fails: the rails run once per tier",
            catalogue.CATALOGUE,
            _BREADTH_ONLY_COMMAND,
            _BREADTH_ONLY_COMMAND + '  { argv = ["uv", "run", "pytest", "-q", "tests/rails"] },\n',
            expect="the rails runs in 2 jobs of the breadth tier",
        ),
    ),
)

ROWS = (*ROWS, EACH_OWNING_JOB_IS_IN_EVERY_TIER_THAT_NEEDS_IT)


# ── A make target that runs a static step runs it as static does ─────────────────────────────
#
# Static's validate runs with --strict (#3929), so a warning fails CI. A make target that ran a
# laxer argv would pass here what CI fails. This row holds each such target to its step's argv,
# word for word after the interpreter: the recipe runs `$(PYTHON)`, the step `uv run python`.

#: ``(the make target, the catalogue job, the words that find the target's step in the job)``.
_MAKE_MIRRORS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("harness-validate", "static", frozenset({"-m", "harness", "validate"})),
)
_STEP_PYTHON = ["uv", "run", "python"]


def _recipe(makefile: str, target: str) -> list[str] | None:
    """The target's recipe lines, without their tab, or None when the Makefile has no such
    target."""
    lines = makefile.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{target}:"):
            recipe: list[str] = []
            for following in lines[index + 1 :]:
                if not following.startswith("\t"):
                    break
                recipe.append(following[1:])
            return recipe
    return None


def make_mirror_violations(root: Path) -> list[str]:
    """Each mirrored make target runs one command, its job's step's argv after the
    interpreter."""
    bad: list[str] = []
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    cat = tomllib.loads((root / catalogue.CATALOGUE).read_text(encoding="utf-8"))
    entries = {str(j.get("id")): j for j in cat.get("job") or []}
    for target, job_id, words in _MAKE_MIRRORS:
        recipe = _recipe(makefile, target)
        argvs = [
            list(step.get("argv") or [])
            for step in (entries.get(job_id) or {}).get("command") or []
            if words <= set(step.get("argv") or [])
        ]
        if recipe is None:
            bad.append(f"the Makefile has no {target} target")
        elif len(argvs) != 1:
            bad.append(f"{job_id} has {len(argvs)} steps that run {sorted(words)}, not one")
        elif len(recipe) != 1:
            bad.append(f"make {target} runs {len(recipe)} commands, not {job_id}'s one step")
        elif argvs[0][:3] != _STEP_PYTHON:
            bad.append(f"{job_id}'s step {argvs[0]} does not start with {_STEP_PYTHON}")
        elif shlex.split(recipe[0]) != ["$(PYTHON)", *argvs[0][3:]]:
            bad.append(
                f"make {target} runs {recipe[0]!r} where {job_id} runs"
                f" {shlex.join(argvs[0])!r}, so a run here is not the run CI makes"
            )
    return bad


def _make_mirror_scaffold(root: Path) -> None:
    """The tree's Makefile and catalogue, which agree."""
    for rel in ("Makefile", catalogue.CATALOGUE):
        _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))


_MAKE_VALIDATE = "harness-validate:\n\t$(PYTHON) -m harness validate --strict\n"

MAKE_TARGETS_RUN_THEIR_STATIC_STEP = Row(
    name="a-make-target-that-runs-a-static-step-runs-its-argv",
    check=make_mirror_violations,
    scaffold=_make_mirror_scaffold,
    controls=(
        _plant(
            "make harness-validate without --strict, which passes a warning static fails",
            "Makefile",
            _MAKE_VALIDATE,
            _MAKE_VALIDATE.replace(" --strict", ""),
            expect="make harness-validate runs '$(PYTHON) -m harness validate' where static runs",
        ),
        _plant(
            "static's validate with a flag the make target does not pass",
            catalogue.CATALOGUE,
            _VALIDATE_STEP,
            _VALIDATE_STEP.replace('"--strict"', '"--strict", "--fast"'),
            expect="so a run here is not the run CI makes",
        ),
        _plant(
            "the recipe's failure ignored by a leading `-`",
            "Makefile",
            _MAKE_VALIDATE,
            _MAKE_VALIDATE.replace("\t$(PYTHON)", "\t-$(PYTHON)"),
            expect="make harness-validate runs '-$(PYTHON)",
        ),
        _plant(
            "a second command in the recipe",
            "Makefile",
            _MAKE_VALIDATE,
            _MAKE_VALIDATE + "\t$(PYTHON) -m harness scan\n",
            expect="make harness-validate runs 2 commands",
        ),
        _plant(
            "the harness-validate target gone",
            "Makefile",
            _MAKE_VALIDATE,
            "",
            expect="the Makefile has no harness-validate target",
        ),
        _plant(
            "static without its validate step",
            catalogue.CATALOGUE,
            _VALIDATE_STEP,
            "",
            expect="static has 0 steps that run",
        ),
        _plant(
            "static's validate run by a bare python, whose words the row does not read",
            catalogue.CATALOGUE,
            _VALIDATE_STEP,
            _VALIDATE_STEP.replace('"uv", "run", "python"', '"python"'),
            expect="does not start with ['uv', 'run', 'python']",
        ),
        _plant(
            "a target named harness-validate-fast above it, which runs --fast, passes",
            "Makefile",
            _MAKE_VALIDATE,
            "harness-validate-fast:\n\t$(PYTHON) -m harness validate --fast\n\n" + _MAKE_VALIDATE,
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, MAKE_TARGETS_RUN_THEIR_STATIC_STEP)


# ── The tier workflows: pr.yml and merge.yml (#3915) ─────────────────────────────────────────
#
# pr.yml runs the change tier and merge.yml the merge tier, each in one shape: `changes` plans the
# tier with main's planner as a job matrix (tools/ci/plan.py), a stage job per part of the matrix
# runs each entry as one run-job line after the setup action, and `results` judges the plan's
# keys against the records the entries uploaded, with main's verdict.py. Both run on dispatch
# only, beside ci.yml, until the change that retires ci.yml gives them its triggers. The rows:
#
# 1. Each job is a stage job or orchestration (`changes`, `shard-plan`, `results`). An
#    orchestration job runs a closed list of lines, each spelled exactly, its indentation too (a
#    blank line or a comment aside): a line its list does not hold fails, and so does a listed
#    line no job of either workflow runs any more. A stage job runs the runner's identity and the
#    one run-job line, and nothing else, and every stage job of both workflows has the same steps.
# 2. Each runs its tier's matrix: the plan step plans the workflow's tier, there is a stage job
#    for each part the tier's whole plan splits into (plan.stage_jobs) and no other, each reading
#    its own part after the stage before, and each entry's record is uploaded under the name
#    verdict.record_artifact gives its key, run and attempt, kept 30 days; an entry that reads the
#    shard plan waits for the run's `shard-plan`. `results` runs always, after every other job,
#    over the records and `needs`, and its last step, the verdict, runs unless the run is
#    cancelled, whatever `changes` answered. A run in flight is never cancelled: one group per
#    workflow and commit, and no job's group of its own. No job is `merge-verdict`, by its id or
#    by any key its matrix can give it, and no expensive job is in both tiers' plans.
# 3. Main's catalogue check stands in front of merge.yml's verdict: it is the last step of the
#    job the merge decision requires (merge_decision.CATALOGUE_CHECK), unconditional, and
#    `results` reads that job.
# 4. pr.yml carries no push stage: no push trigger, no job or step that runs only on a push or on
#    main, no workflow, job or step env that reads one, no tree check. What a push to main runs
#    whatever its tree is merge.yml's to hold.
# 5. Neither takes a trusted trigger (`pull_request_target`, `workflow_run`), holds a write or
#    reads a secret: the verdict needs none. Each runs on `workflow_dispatch` alone for now, so
#    nothing about today's checks moves.
#
# Their actions are pinned by the supply-chain contract's action row, which reads every workflow.

#: How long a record artifact is kept: the controller and the push replay read the records at
#: merge, after the run. No default was given, so 30 days.
_RECORD_RETENTION_DAYS = 30
#: Where run-job leaves an entry's record, and so what the record upload uploads.
_RECORDS_PATH = "${{ runner.temp }}/run-job/records/"
_PLAN_JOB, _SHARD_PLAN_JOB, _RESULTS_JOB = merge_decision.CATALOGUE_CHECK, "shard-plan", "results"
#: merge.yml's push stage's first job: did a green pull-request run of merge.yml test this tree.
_TREE_CHECK_JOB = "tree-check"
#: The external input of an entry that reads the run's one shard plan, which `shard-plan` makes.
_SHARD_HISTORY = "shard-history"
#: The expensive tier, which runs once per candidate (#3915): the suite's shards, the browser
#: run, the package and the gateway image's build and smoke.
_EXPENSIVE = frozenset(
    {"python-suite", "python-suite-macos", "e2e", "e2e-shard", *_PINNED_BROWSER, "package", "image"}
)

_RUNNER_IDENTITY_LINE = (
    'echo "runner image ${ImageOS:?not a GitHub-hosted runner image}'
    ' ${ImageVersion:?no image version} on ${RUNNER_OS} ${RUNNER_ARCH}"'
)
#: The one line a stage job runs besides its runner's identity.
_RUN_JOB_LINE = (
    'python3 -m tools.ci run-job "$JOB" ${SHARD:+--shard "$SHARD"} --out "$RUNNER_TEMP/run-job"'
)

#: Each orchestration job's run: lines, by job and by what they do, as both workflows' jobs of
#: that id may run them.
_TIER_LINES: dict[str, dict[str, str]] = {
    _PLAN_JOB: {
        "the runner's identity": _RUNNER_IDENTITY_LINE,
        "the apps repository's head, read once for the run, or no partner": r"""
set -uo pipefail
apps=$(git ls-remote https://github.com/PersonalClaw/PersonalClawApps refs/heads/main | cut -f1) || apps=""
if ! [[ "$apps" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error title=no partner::the apps repository's main could not be read, so no entry can check it out and the verdict is red. Re-run the job."
  exit 1
fi
echo "apps-partner=$apps" | tee -a "$GITHUB_OUTPUT"
""",  # noqa: E501
        "a pull request's or a queue commit's changed paths, for the flake rule": r"""
set -uo pipefail
out="$RUNNER_TEMP/changed-paths/changed.z"
mkdir -p "$RUNNER_TEMP/changed-paths"
parents=$(git cat-file -p HEAD | sed -n 's/^parent //p' | tr '\n' ' ')
read -r main head extra <<< "$parents"
if [ "${GITHUB_EVENT_NAME:-}" = merge_group ]; then
  if [ -n "${head:-}" ] || [ "$main" != "$QUEUE_BASE" ]; then
    echo "::warning title=no changed list::the checkout is not this entry's queue commit, so the flake rule passes no flake"
    exit 0
  fi
elif [ -z "${head:-}" ] || [ -n "${extra:-}" ] || [ "$head" != "$PR_HEAD" ]; then
  echo "::warning title=no changed list::the checkout is not this pull request's test merge, so the flake rule passes no flake"
  exit 0
fi
if git fetch --no-tags --depth=1 origin "$main" \
   && git diff --name-only -z --no-renames "$main" HEAD > "$out" && [ -s "$out" ]; then
  echo "ok=true" >> "$GITHUB_OUTPUT"
else
  echo "::warning title=no changed list::the test merge's changed paths could not be read, so the flake rule passes no flake"
fi
""",  # noqa: E501
        "the changed paths, against what the run compares with": r"""
set -uo pipefail
listed="$RUNNER_TEMP/changed.json"
changed="$RUNNER_TEMP/changed.txt"
gh api --paginate "repos/${GITHUB_REPOSITORY}/compare/${COMPARED}...${HEAD_SHA}" \
  --jq '.files[].filename | @json' > "$listed" || : > "$listed"
python3 -c 'import json, sys; names = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]; assert all(isinstance(n, str) and "\0" not in n for n in names); sys.stdout.write("".join(n + "\0" for n in names))' "$listed" > "$changed" || : > "$changed"
echo "enumerated $(tr -cd '\000' < "$changed" | wc -c) changed path(s)"
""",  # noqa: E501
        "main's tools/ci, archived beside the checkout, or no plan": r"""
judge="$RUNNER_TEMP/judge"
if ! { rm -rf "$judge" && mkdir -p "$judge" \
       && git fetch --no-tags --depth=1 origin "${JUDGE:-refs/heads/main}" \
       && git archive FETCH_HEAD tools/ci | tar -x -C "$judge"; }; then
  echo "::error title=no plan::main's tools/ci could not be fetched, so nothing is planned and the verdict is red. Re-run the job."
  exit 1
fi
""",  # noqa: E501
        "the tier main's plan-tier names for merge.yml's run, or no plan": r"""
tier="$TIER"
if [ "$TIER" = merge ]; then
  tier=$(cd "$judge" && python3 -m tools.ci plan-tier --event "${GITHUB_EVENT_NAME:-}" \
           --queue-var "${QUEUE_LIVE:-}" --repo "$GITHUB_REPOSITORY") || tier=""
  case "$tier" in
    entry|merge) echo "this run plans the $tier tier" ;;
    *)
      echo "::error title=no plan::main's planner named no tier for this run, so nothing is planned and the verdict is red."
      exit 1
      ;;
  esac
fi
""",  # noqa: E501
        "a push's plan, by the tree check's answer, or no plan": r"""
case "$PUSH_PLAN" in
  "") mode="" ;;
  tested) mode="--tested" ;;
  full) mode="--full" ;;
  *)
    echo "::error title=no plan::PUSH_PLAN is '$PUSH_PLAN', which is no push plan, so nothing is planned and the verdict is red."
    exit 1
    ;;
esac
""",  # noqa: E501
        "main's planner, over this checkout's catalogue, as a matrix": r"""
: > "$RUNNER_TEMP/plan.err"
answer=$(cd "$judge" && python3 -m tools.ci plan "$tier" \
           --catalogue "$GITHUB_WORKSPACE/tools/ci/catalogue.toml" \
           --trusted-areas "$judge/tools/ci/areas.toml" \
           --root "$GITHUB_WORKSPACE" --paths "$changed" -z \
           --repo "$GITHUB_REPOSITORY" --outputs --matrix ${mode:+"$mode"} \
           2> "$RUNNER_TEMP/plan.err") || answer=""
cat "$RUNNER_TEMP/plan.err" >> "$GITHUB_STEP_SUMMARY"
sed -n '/^::warning[ :]/p' "$RUNNER_TEMP/plan.err"
printf '%s\n' "$answer" > "$RUNNER_TEMP/answer.txt"
""",
        "the keys and the stage jobs' matrices, read back before they are published": r"""
if ! python3 -I - "$RUNNER_TEMP/answer.txt" "$STAGE_JOBS" > "$RUNNER_TEMP/published.txt" <<'EOF'
import json, re, sys
lines = {}
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    name, sep, value = line.partition("=")
    if not sep or name in lines:
        sys.exit(f"not one line per output: {line[:80]!r}")
    lines[name] = value
keys = json.loads(lines.pop("keys", "null"))
if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
    sys.exit("no plan: the keys are not a list of job keys")
partners = json.loads(lines.pop("key-partners", "null"))
if not isinstance(partners, dict) or list(partners) != keys or not all(isinstance(v, list) and all(isinstance(n, str) and n for n in v) for v in partners.values()):
    sys.exit("no plan: the key partners are not each key's list of partner names")
runs = sys.argv[2].split()
extra = sorted(set(lines) - {"full", "jobs", "matrix"} - {f"matrix-{job}" for job in runs})
if extra:
    sys.exit(f"the plan has stage jobs this workflow does not run: {extra}")
ran = []
for job in runs:
    if f"matrix-{job}" not in lines:
        continue
    grid = json.loads(lines[f"matrix-{job}"])
    if not isinstance(grid, dict) or list(grid) != ["include"] or not grid["include"]:
        sys.exit(f"{job}: not a matrix of entries")
    for entry in grid["include"]:
        if entry["container"] or not re.fullmatch("[a-z0-9-]+", entry["slug"]):
            sys.exit(f"{job}: an entry this workflow cannot run: {entry['key']!r}")
        ran.append(entry["key"])
    print(f"{job}={json.dumps(grid, separators=(',', ':'), sort_keys=True)}")
if sorted(ran) != sorted(keys):
    sys.exit("the stage jobs run other entries than the plan's keys")
print(f"keys={json.dumps(keys, separators=(',', ':'))}")
print(f"key-partners={json.dumps(partners, separators=(',', ':'))}")
EOF
then
  echo "::error title=no plan::the planner answered no plan this workflow can run, so nothing runs and the verdict is red. The lines above, and the summary, say why."
  exit 1
fi
tee -a "$GITHUB_OUTPUT" < "$RUNNER_TEMP/published.txt"
""",  # noqa: E501
        "the push's replay, in shadow, beside the tree check (merge.yml's)": r"""
set -uo pipefail
judge="$RUNNER_TEMP/judge"
history="$RUNNER_TEMP/main-history.git"
rc=0
{ git init -q --bare "$history" \
  && git -C "$history" fetch -q --no-tags --filter=tree:0 "https://github.com/${GITHUB_REPOSITORY}" "+refs/heads/main:refs/remotes/origin/main" \
  && (cd "$judge" && python3 -m tools.ci replay --tier merge --root "$judge" --git-dir "$history" \
        --image "ubuntu-24.04=${ImageOS}/${ImageVersion}" --controller-revision "$BEFORE" \
        --receipt "$RUNNER_TEMP/replay-receipt.json" --beside-tested "$TESTED"); } || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "::warning title=replay shadow::the push's replay did not answer (exit $rc), so this push has no shadow decisions. Nothing else changes."
fi
""",  # noqa: E501
        "main's check of this run's catalogue (merge.yml's)": r"""
set -uo pipefail
judge="$RUNNER_TEMP/judge"
if [ "${GITHUB_REF:-}" = "refs/heads/main" ]; then
  echo "a run of main: the catalogue under test is main's own"
  exit 0
fi
rc=0
(cd "$judge" && python3 -m tools.ci catalogue-check \
   --catalogue "$GITHUB_WORKSPACE/tools/ci/catalogue.toml" \
   --against "$judge/tools/ci/catalogue.toml") || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "::error title=catalogue refused::main's tools/ci refuses this run's catalogue (exit $rc); the lines above say why."
  exit 1
fi
""",  # noqa: E501
    },
    _TREE_CHECK_JOB: {
        "the runner's identity": _RUNNER_IDENTITY_LINE,
        "the previous main commit's tree check, over merge.yml's pull_request runs": r"""
set -u
answer=""
if [ "$GITHUB_EVENT_NAME" = "push" ] && [ "$GITHUB_REF" = "refs/heads/main" ]; then
  check="$RUNNER_TEMP/tree_tested.py"
  if gh api -H "Accept: application/vnd.github.raw" \
       "repos/${GITHUB_REPOSITORY}/contents/tools/ci/tree_tested.py?ref=${BEFORE}" \
       > "$check"; then
    GITHUB_OUTPUT="$RUNNER_TEMP/answer.txt" python3 -I "$check" --workflow merge.yml || true
    answer=$(cat "$RUNNER_TEMP/answer.txt" 2>/dev/null || true)
  else
    echo "the previous main commit ${BEFORE} has no tree check, so every job runs" \
      | tee -a "$GITHUB_STEP_SUMMARY"
  fi
fi
case "$answer" in
  tested=true|tested=false) ;;
  *) answer="tested=false" ;;
esac
echo "$answer"
printf '%s\n' "$answer" >> "$GITHUB_OUTPUT"
""",
    },
    _SHARD_PLAN_JOB: {
        "the runner's identity": _RUNNER_IDENTITY_LINE,
        "one shard plan for the run, from main's push runs in either shape": r"""
python3 -m tools.ci shards plan --repo "$GITHUB_REPOSITORY" --run-id "$GITHUB_RUN_ID" --leg test-shard:main-push:junit-test-shard-:3 --leg test-macos:main-push:junit-test-macos-:"""  # noqa: E501
        + str(_MACOS_SHARDS)
        + r""" --out "$RUNNER_TEMP/shard-plan/plan.json"
""",
        "every file in exactly one shard of each leg": r"""
python3 -m tools.ci shards check --plan "$RUNNER_TEMP/shard-plan/plan.json"
""",
    },
    _RESULTS_JOB: {
        "the runner's identity": _RUNNER_IDENTITY_LINE,
        # The flake rule's lists (tools/ci/verdict.py), on a pull request's run: the verdict step
        # reads its inputs, then the two lists, then calls main's verdict with them. Listed before
        # the workflows run it (#4584 step 4): until they do, the step above stands.
        "the verdict's inputs, read": r"""
set -u
judge="$RUNNER_TEMP/verdict.py"
if ! gh api -H "Accept: application/vnd.github.raw" \
     "repos/${GITHUB_REPOSITORY}/contents/tools/ci/verdict.py?ref=${JUDGE:-main}" > "$judge"; then
  echo "::error title=no verdict::main's tools/ci/verdict.py could not be read, so nothing judged this run. Re-run the job."
  exit 1
fi
printf '%s' "$NEEDS" > "$RUNNER_TEMP/needs.json"
printf '%s' "${KEYS:-[]}" > "$RUNNER_TEMP/keys.json"
printf '%s' "${KEY_PARTNERS:-"{}"}" > "$RUNNER_TEMP/key-partners.json"
mkdir -p "$RUNNER_TEMP/records"
proof=""
if [ -f "$RUNNER_TEMP/superseded.json" ]; then
  proof="$RUNNER_TEMP/superseded.json"
fi
""",  # noqa: E501
        "the flake rule's two lists, on a pull request or a queue commit only": r"""
flakes=()
case "${GITHUB_EVENT_NAME:-}" in pull_request|merge_group) judged=yes ;; *) judged="" ;; esac
if [ -n "$judged" ] && [ -f "$RUNNER_TEMP/changes/changed.z" ]; then
  if gh api --paginate --slurp \
       "repos/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}/attempts/${GITHUB_RUN_ATTEMPT}/jobs?per_page=100" \
       > "$RUNNER_TEMP/jobs.json"; then
    flakes=(--changed "$RUNNER_TEMP/changes/changed.z" --jobs "$RUNNER_TEMP/jobs.json")
  else
    echo "::warning title=no jobs list::this run's jobs could not be listed, so the flake rule passes no flake"
  fi
fi
""",  # noqa: E501
        "main's verdict.py, given the flake rule's lists": r"""
python3 -I "$judge" --plan "$RUNNER_TEMP/keys.json" --needs "$RUNNER_TEMP/needs.json" \
  --head "$HEAD_SHA" --partner "apps-partner=$APPS_PARTNER" \
  --run-head "$HEAD_SHA" --records "$RUNNER_TEMP/records" \
  --key-partners "$RUNNER_TEMP/key-partners.json" \
  ${proof:+--superseded "$proof"} \
  ${flakes[@]+"${flakes[@]}"} \
  --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"
""",  # noqa: E501
        "main's superseded.py, over the suite shards a newer push to main replaced": r"""
set -u
check="$RUNNER_TEMP/superseded.py"
if gh api -H "Accept: application/vnd.github.raw" \
     "repos/${GITHUB_REPOSITORY}/contents/tools/ci/superseded.py?ref=${BEFORE}" > "$check"; then
  for job in python-suite python-suite-macos; do
    GITHUB_OUTPUT=/dev/null python3 -I "$check" --workflow merge.yml --job "$job" \
      --group-prefix merge-suite- --out "$RUNNER_TEMP/superseded.json" || true
  done
fi
""",
    },
}

#: Each orchestration job's steps, as the blocks of :data:`_TIER_LINES` each one runs, in
#: order: a step runs exactly its blocks' lines, in that order, so a reorder that keeps every
#: line (publishing a plan before the check that refuses it) fails as an extra line does.
#: The verdict step that gives main's verdict the flake rule's two lists, on a pull request's run
#: and (0.4.0 A1) a queue commit's: the queue takes the pull request's flake rule.
_QUEUE_FLAKE_VERDICT = (
    "the verdict's inputs, read",
    "the flake rule's two lists, on a pull request or a queue commit only",
    "main's verdict.py, given the flake rule's lists",
)
#: The plan step, its changed paths compared with what the run compares with (0.4.0 A1): main, or
#: a queue commit's queue base; and its tier, merge.yml's as main's plan-tier names it (A4a).
_PLAN_STEP_TAIL = (
    "main's tools/ci, archived beside the checkout, or no plan",
    "the tier main's plan-tier names for merge.yml's run, or no plan",
    "a push's plan, by the tree check's answer, or no plan",
    "main's planner, over this checkout's catalogue, as a matrix",
    "the keys and the stage jobs' matrices, read back before they are published",
)
_PLAN_COMPARED = ("the changed paths, against what the run compares with", *_PLAN_STEP_TAIL)
_QUEUE_CHANGED = ("a pull request's or a queue commit's changed paths, for the flake rule",)
_TIER_STEPS: dict[str, tuple[tuple[str, ...], ...]] = {
    _PLAN_JOB: (
        ("the runner's identity",),
        ("the apps repository's head, read once for the run, or no partner",),
        _QUEUE_CHANGED,
        _PLAN_COMPARED,
        ("the push's replay, in shadow, beside the tree check (merge.yml's)",),
        ("main's check of this run's catalogue (merge.yml's)",),
    ),
    _TREE_CHECK_JOB: (
        ("the runner's identity",),
        ("the previous main commit's tree check, over merge.yml's pull_request runs",),
    ),
    _SHARD_PLAN_JOB: (
        ("the runner's identity",),
        ("one shard plan for the run, from main's push runs in either shape",),
        ("every file in exactly one shard of each leg",),
    ),
    _RESULTS_JOB: (
        ("the runner's identity",),
        ("main's superseded.py, over the suite shards a newer push to main replaced",),
        _QUEUE_FLAKE_VERDICT,
    ),
}


def _spelled(run: str) -> list[str]:
    """A run: block's lines as they are spelled, indentation included (a heredoc's Python reads
    it), less trailing blanks and blank and comment lines."""
    out = []
    for line in run.splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            out.append(line.rstrip())
    return out


_TIER_ALLOWED: dict[str, frozenset[str]] = {
    job: frozenset(line for block in blocks.values() for line in _spelled(block))
    for job, blocks in _TIER_LINES.items()
}
#: Each orchestration job's steps as their spelled lines, by the blocks each is made of.
_TIER_STEP_LINES: dict[str, dict[tuple[str, ...], tuple[str, ...]]] = {
    job: {
        blocks: tuple(line for block in blocks for line in _spelled(_TIER_LINES[job][block]))
        for blocks in steps
    }
    for job, steps in _TIER_STEPS.items()
}
#: The listed steps a tier workflow may run or leave out, by job: each is held to its listed lines
#: when it runs. A step a row names lands in two merges (#4584 step 4): these rows first, with
#: the step here, then the workflow, which takes it out of here. None waits today.
_TIER_OPTIONAL_STEPS: dict[str, frozenset[tuple[str, ...]]] = {}
#: Listed steps of which each tier workflow's job runs exactly one: its plan step, and its results
#: job's verdict.
_TIER_ONE_OF: dict[str, frozenset[tuple[str, ...]]] = {
    _PLAN_JOB: frozenset({_PLAN_COMPARED}),
    _RESULTS_JOB: frozenset({_QUEUE_FLAKE_VERDICT}),
}
_STAGE_ALLOWED = (_RUNNER_IDENTITY_LINE, _RUN_JOB_LINE)
#: The actions a stage job's steps use besides its two lines: the checkouts, the setup action,
#: and the artifacts the entry reads and writes.
_STAGE_ACTIONS = frozenset(
    {
        "actions/checkout",
        "./.github/actions/setup",
        "actions/download-artifact",
        "actions/upload-artifact",
    }
)


def _tier_workflows(root: Path) -> dict[str, dict[str, Any]]:
    """Each tier workflow as YAML reads it, or an empty mapping when the tree lacks it."""
    out: dict[str, dict[str, Any]] = {}
    for name in _TIER_WORKFLOWS:
        path = root / _WORKFLOWS / name
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
        out[name] = data if isinstance(data, dict) else {}
    return out


def _stage_matrix(job_id: str) -> str:
    return f"${{{{ fromJSON(needs.{_PLAN_JOB}.outputs.{job_id}) }}}}"


def _is_stage_job(job_id: str, job: dict[str, Any]) -> bool:
    """A job whose matrix is its own part of the plan's matrix."""
    return (job.get("strategy") or {}).get("matrix") == _stage_matrix(job_id)


def _run_steps(job: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(s.get("name") or f"step {n}"), str(s["run"]))
        for n, s in enumerate(_trust_steps(job), 1)
        if "run" in s
    ]


def tier_lines_violations(root: Path) -> list[str]:
    """Each tier workflow's jobs are stage jobs or orchestration, each runs only its closed list,
    every listed line is run, and every stage job runs the same steps."""
    workflows = _tier_workflows(root)
    bad: list[str] = []
    ran: dict[str, set[tuple[str, ...]]] = {job: set() for job in _TIER_LINES}
    ran_in: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    stage_steps: list[tuple[str, list[Any]]] = []
    for name, workflow in workflows.items():
        if not workflow:
            bad.append(f"{_WORKFLOWS}/{name} is missing")
            continue
        for job_id, job in _trust_jobs(workflow).items():
            where = f"{name} job {job_id}"
            if _is_stage_job(job_id, job):
                stage_steps.append((where, job.get("steps")))
                lines = [line for _step, run in _run_steps(job) for line in _spelled(run)]
                if sorted(lines) != sorted(_STAGE_ALLOWED):
                    bad.append(
                        f"{where} is a stage job and runs {lines}: a stage job runs its runner's"
                        " identity and the one run-job line, and nothing else"
                    )
                for step in _trust_steps(job):
                    if "uses" in step and _action(step) not in _STAGE_ACTIONS:
                        bad.append(
                            f"{where} is a stage job and uses {step['uses']!r}: its other steps"
                            " are the checkouts, the setup action and the entry's artifacts"
                        )
                continue
            allowed = _TIER_ALLOWED.get(job_id)
            if allowed is None:
                bad.append(f"{where} is neither a stage job nor orchestration this row lists")
                continue
            listed = _TIER_STEP_LINES[job_id]
            for step, run in _run_steps(job):
                lines = tuple(_spelled(run))
                unknown = [line for line in lines if line not in allowed]
                for line in unknown:
                    bad.append(
                        f"{where} runs {line!r} ({step!r}), which its closed list of lines"
                        " does not hold"
                    )
                matched = [blocks for blocks, want in listed.items() if want == lines]
                ran_in.setdefault((name, job_id), []).extend(matched)
                if matched:
                    ran[job_id].update(matched)
                elif not unknown:
                    bad.append(
                        f"{where} step {step!r} runs its listed lines out of their listed order"
                        " or grouping: each step runs one listed step's lines, in their order"
                    )
    for job_id, listed in _TIER_STEP_LINES.items():
        for blocks in listed:
            if blocks not in ran[job_id] and blocks not in _TIER_OPTIONAL_STEPS.get(job_id, ()):
                bad.append(
                    f"this row lists the step {' + '.join(blocks)!r} for the {job_id} job, and no"
                    f" tier workflow's {job_id} runs it"
                )
    for (name, job_id), steps in ran_in.items():
        one_of = _TIER_ONE_OF.get(job_id, frozenset())
        count = sum(1 for blocks in steps if blocks in one_of)
        if one_of and count != 1:
            bad.append(
                f"{name} job {job_id} runs {count} of its verdict steps: it runs exactly one"
            )
    if stage_steps:
        first_where, first = stage_steps[0]
        for where, steps in stage_steps[1:]:
            if steps != first:
                bad.append(
                    f"{where} runs other steps than {first_where}: every stage job's are one"
                )
    return bad


def _tier_scaffold(root: Path) -> None:
    """The tree's own tier workflows and the setup action they call, the catalogue and its
    overrides the plan reads."""
    for rel in (_PR_YML, _MERGE_YML, _SETUP_ACTION, catalogue.CATALOGUE, catalogue.AREAS):
        _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))


def _cut(rel: str, start: str, end: str) -> Callable[[Path], None]:
    """A plant that removes ``rel``'s text from ``start`` up to, not including, ``end``."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        at = text.index(start)
        _write(root, rel, text[:at] + text[text.index(end, at) :])

    return plant


#: The plan step's text one plant extends, in both workflows.
_PLAN_TAIL = '          tee -a "$GITHUB_OUTPUT" < "$RUNNER_TEMP/published.txt"\n'
_ANSWER_LINE = """          printf '%s\\n' "$answer" > "$RUNNER_TEMP/answer.txt"\n"""
_NEEDS_LINE = """          printf '%s' "$NEEDS" > "$RUNNER_TEMP/needs.json"\n"""
_KEYS_LINE = """          printf '%s' "${KEYS:-[]}" > "$RUNNER_TEMP/keys.json"\n"""
_PARTNER_PUBLISH = '          echo "apps-partner=$apps" | tee -a "$GITHUB_OUTPUT"\n'
_RUN_JOB_STEP = f"        run: {_RUN_JOB_LINE}\n"

#: merge.yml's push replay in shadow (#3919 (b1)), as its step reads in the changes job.
_REPLAY_SHADOW_STEP = """      - name: the push's replay, in shadow, beside the tree check
        if: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
        env:
          GH_TOKEN: ${{ github.token }}
          BEFORE: ${{ github.event.before }}
          TESTED: ${{ needs.tree-check.outputs.tested }}
        run: |
          set -uo pipefail
          judge="$RUNNER_TEMP/judge"
          history="$RUNNER_TEMP/main-history.git"
          rc=0
          { git init -q --bare "$history" \\
            && git -C "$history" fetch -q --no-tags --filter=tree:0 "https://github.com/${GITHUB_REPOSITORY}" "+refs/heads/main:refs/remotes/origin/main" \\
            && (cd "$judge" && python3 -m tools.ci replay --tier merge --root "$judge" --git-dir "$history" \\
                  --image "ubuntu-24.04=${ImageOS}/${ImageVersion}" --controller-revision "$BEFORE" \\
                  --receipt "$RUNNER_TEMP/replay-receipt.json" --beside-tested "$TESTED"); } || rc=$?
          if [ "$rc" -ne 0 ]; then
            echo "::warning title=replay shadow::the push's replay did not answer (exit $rc), so this push has no shadow decisions. Nothing else changes."
          fi
"""  # noqa: E501


def _tier_run(block: str) -> str:
    """A listed block as a tier workflow's ``run: |`` spells it, ten spaces in."""
    return "".join(
        f"          {line}" if line.strip() else line
        for line in block.lstrip("\n").splitlines(True)
    )


#: The flake rule's lists and the verdict's call, as the workflows spell them (0.4.0 A1).
_QUEUE_FLAKE_CALL = _tier_run(
    _TIER_LINES[_RESULTS_JOB][
        "the flake rule's two lists, on a pull request or a queue commit only"
    ]
) + _tier_run(_TIER_LINES[_RESULTS_JOB]["main's verdict.py, given the flake rule's lists"])


def _wired(text: str) -> str:
    """merge.yml's text, which runs the flake rule's wiring as listed."""
    assert _QUEUE_FLAKE_CALL in text, "merge.yml does not run the listed verdict step"
    return text


def _with_a_line_of_its_own(text: str) -> str:
    text = _wired(text)
    assert "          flakes=()\n" in text
    return text.replace("          flakes=()\n", "          flakes=()\n          rm -rf ~\n", 1)


def _no_verdict_call(text: str) -> str:
    """The workflow's verdict step without its call."""
    assert _QUEUE_FLAKE_CALL in text, "the workflow does not run the listed verdict step"
    return text.replace(_QUEUE_FLAKE_CALL, "", 1)


def _edited(rel: str, edit: Callable[[str], str]) -> Callable[[Path], None]:
    """A plant that rewrites one file's text with ``edit``."""

    def plant(root: Path) -> None:
        _write(root, rel, edit((root / rel).read_text(encoding="utf-8")))

    return plant


THE_TIER_WORKFLOWS_RUN_ONLY_THEIR_CLOSED_LISTS = Row(
    name="the-tier-workflows-run-only-their-closed-lists-of-lines",
    check=tier_lines_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant("an extra line in the plan step", _PR_YML, _PLAN_TAIL,
               _PLAN_TAIL + "          echo planned\n",
               expect="pr.yml job changes runs 'echo planned'"),
        _plant("a test run in the verdict step", _MERGE_YML,
               '            --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n',
               '            --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n'
               "          uv run pytest tests\n",
               expect="merge.yml job results runs 'uv run pytest tests'"),
        _plant_in_job("a second line in a stage job's run-job step", _MERGE_YML, "test-2",
                      _RUN_JOB_STEP,
                      f"        run: |\n          {_RUN_JOB_LINE}\n          npm test\n",
                      expect="merge.yml job test-2 is a stage job and runs"),
        _plant_in_job("a stage job with a run step of its own", _PR_YML, "test",
                      "      - name: Upload the entry's record\n",
                      "      - run: uv sync --locked --extra dev\n"
                      "      - name: Upload the entry's record\n",
                      expect="pr.yml job test is a stage job and runs"),
        _plant("the push's replay in shadow left out of merge.yml's changes",
               _MERGE_YML, _REPLAY_SHADOW_STEP, "",
               expect="beside the tree check (merge.yml's)\" for the changes job, and no tier"
               " workflow's changes runs it"),
        _plant("the push's replay in shadow with a line of its own",
               _MERGE_YML, _REPLAY_SHADOW_STEP,
               _REPLAY_SHADOW_STEP.replace("rc=0\n", "rc=0\n          rm -rf ~\n", 1),
               expect="merge.yml job changes runs 'rm -rf ~'"),
        Control("a listed line no job runs any more: the catalogue check removed",
                _cut(_MERGE_YML, "      - name: main's tools/ci checks this run's catalogue\n",
                     "\n  shard-plan:\n"),
                expect="for the changes job, and no tier workflow's changes runs it"),
        _plant("a job that is neither", _PR_YML, "\n  results:\n",
               "\n  extra:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n    steps:\n"
               "      - run: echo hi\n\n  results:\n",
               expect="pr.yml job extra is neither a stage job nor orchestration"),
        _plant_in_job("a stage job using an action of its own", _PR_YML, "test",
                      "      - name: the entry's catalogue job, through run-job\n",
                      f"      - uses: actions/cache@{'a' * 40} # v4.3.0\n"
                      "        with:\n          path: ~/.cache\n          key: k\n"
                      "      - name: the entry's catalogue job, through run-job\n",
                      expect="pr.yml job test is a stage job and uses 'actions/cache@"),
        _plant_in_job("a stage job whose steps differ from the others'", _MERGE_YML, "build",
                      "          retention-days: 30\n", "          retention-days: 31\n",
                      expect="merge.yml job build runs other steps than"),
        Control("the workflows removed", _both(_remove(_PR_YML), _remove(_MERGE_YML)),
                expect="pr.yml is missing"),
        _plant("a refusal nested under a copy of another listed line", _PR_YML,
               "                  sys.exit(f\"{job}: not a matrix of entries\")\n",
               "                  if extra:\n"
               "                      sys.exit(f\"{job}: not a matrix of entries\")\n",
               expect="pr.yml job changes runs '        if extra:'"),
        _plant("a comment line in the plan step passes", _PR_YML, _PLAN_TAIL,
               _PLAN_TAIL + "          # the end\n", fails=False),
        # The shard plan's macOS leg is the catalogue's count, which the tree gives: one more is
        # a line the closed list does not hold.
        _plant("the shard plan's macOS leg at one shard more than the catalogue", _MERGE_YML,
               f"junit-test-macos-:{_MACOS_SHARDS}\n",
               f"junit-test-macos-:{_MACOS_SHARDS + 1}\n",
               expect="merge.yml job shard-plan runs"),
        # Each step runs its listed lines in their listed order, so a reorder that keeps every
        # line fails as an extra line does.
        _plants("the plan step publishing before its read-back", _PR_YML, (
                    ("          fi\n" + _PLAN_TAIL, "          fi\n"),
                    (_ANSWER_LINE, _ANSWER_LINE + _PLAN_TAIL),
                ), expect="pr.yml job changes step \"plan the tier with main's planner"),
        _plants("two lines of the verdict step swapped", _MERGE_YML, (
                    (_NEEDS_LINE + _KEYS_LINE, _KEYS_LINE + _NEEDS_LINE),
                ), expect="merge.yml job results step \"the verdict over the plan's keys"),
        _plants("the partner step's check after its publish", _MERGE_YML, (
                    (_PARTNER_PUBLISH, ""),
                    ("      - name: the partner heads the run tests against\n        id: partners\n"
                     "        run: |\n          set -uo pipefail\n",
                     "      - name: the partner heads the run tests against\n        id: partners\n"
                     "        run: |\n          set -uo pipefail\n" + _PARTNER_PUBLISH),
                ), expect="merge.yml job changes step 'the partner heads the run tests against'"),
        # The flake rule's wiring (tools/ci/verdict.py): its changes step and its verdict step
        # are listed before merge.yml runs them, and each results job runs one verdict step.
        # Each holds whether the tree's merge.yml runs the wiring yet or not.
        Control("merge.yml running the flake rule's wiring, as listed, passes",
                _edited(_MERGE_YML, _wired), fails=False),
        Control("the flake rule's lists with a line of their own",
                _edited(_MERGE_YML, _with_a_line_of_its_own),
                expect="merge.yml job results runs 'rm -rf ~'"),
        Control("a results job running no verdict step", _edited(_PR_YML, _no_verdict_call),
                expect="pr.yml job results runs 0 of its verdict steps"),
        _plants("a results job running both verdict steps", _MERGE_YML, (
                    ("      - name: the verdict over the plan's keys and the run's records\n",
                     "      - name: the flake rule's verdict, beside the plain one\n"
                     "        run: |\n"
                     + _tier_run(_TIER_LINES[_RESULTS_JOB]["the verdict's inputs, read"])
                     + _QUEUE_FLAKE_CALL
                     + "      - name: the verdict over the plan's keys and the run's records\n"),
                ), expect="merge.yml job results runs 2 of its verdict steps"),
    ),
)  # fmt: skip


def _tier_plan(root: Path, tier: str) -> dict[str, Any]:
    """The tier's whole plan as a matrix, from the tree's catalogue."""
    cat = catalogue.read(root)
    return ci_plan.matrix(cat, ci_plan.plan(cat, tier, None, overrides=()))


#: A stage job's record upload, tried again when it fails: GitHub's artifact service failed 2 of
#: about 1,400 in 50 merge runs (an ENOTFOUND and a busy server), and a job with no record reads
#: as missing, which no flake explains. The first upload is the one step of a stage job that may
#: carry continue-on-error: the retry, last of the job's uploads so the others stand between
#: them, runs only when it failed, and fails the job if it fails too, so a job neither wrote a
#: record for is red and `results` reads it missing. One upload, as before, holds too.
_RECORD_STEP_ID = "record"
_RECORD_RETRY_IF = f"${{{{ always() && steps.{_RECORD_STEP_ID}.outcome == 'failure' }}}}"
#: What the retry uploads the same as the first.
_RECORD_SAME = ("name", "path", "if-no-files-found", "retention-days")


#: A stage job's record upload as the tier workflows write it, and the same as the first try of a
#: retry pair, with the retry itself: what a plant puts in a job to give it the pair.
_RECORD_UPLOAD = "      - name: Upload the entry's record\n        if: ${{ always() }}\n"
_RECORD_FIRST = (
    "      - name: Upload the entry's record\n        id: record\n        if: ${{ always() }}\n"
    "        continue-on-error: true\n"
)
_RECORD_RETRY = (
    "      - name: Upload the entry's record, again\n"
    f"        if: {_RECORD_RETRY_IF}\n"
    "        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2\n"
    "        with:\n"
    "          name: ci-record-${{ matrix.slug }}-${{ github.run_id }}-${{ github.run_attempt }}\n"
    f"          path: {_RECORDS_PATH}\n"
    "          if-no-files-found: ignore\n"
    f"          retention-days: {_RECORD_RETENTION_DAYS}\n"
    "          overwrite: true\n"
)


def _record_retry_plant(
    name: str,
    rel: str,
    job_id: str,
    edits: tuple[tuple[str, str], ...] = (),
    expect: str = "",
    fails: bool = True,
    retry: str = _RECORD_RETRY,
    first: str = _RECORD_FIRST,
) -> Control:
    """*job_id*'s record upload made a first try and a retry, the retry last in the job, then
    each of *edits* made once inside the job."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        start = text.find(f"\n  {job_id}:\n")
        assert start >= 0, f"the plant {name!r} found no job {job_id} in {rel}"
        following = re.search(r"\n  [\w-]+:\n", text[start + 1 :])
        end = start + 1 + following.start() if following else len(text)
        # A job that has the pair already is put back to one upload first.
        job = (
            text[start:end].replace(_RECORD_RETRY, "", 1).replace(_RECORD_FIRST, _RECORD_UPLOAD, 1)
        )
        assert job.count(_RECORD_UPLOAD) == 1, f"the plant {name!r} found no record upload"
        job = job.replace(_RECORD_UPLOAD, first, 1).rstrip("\n") + "\n" + retry
        for old, new in edits:
            assert old in job, f"the plant {name!r} found no {old!r} in {rel}:{job_id}"
            job = job.replace(old, new, 1)
        _write(root, rel, text[:start] + job + text[end:])

    return Control(name, plant, fails, expect)


def _record_if_plant(name: str, rel: str, job_id: str, new: str, expect: str = "") -> Control:
    """*job_id*'s first record upload with its ``if: ${{ always() }}`` line made *new* (empty:
    left out), in either shape the row takes: once, or the first try of a retry pair."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        start = text.find(f"\n  {job_id}:\n")
        step = text.find("      - name: Upload the entry's record\n", start)
        line = "        if: ${{ always() }}\n"
        at = text.find(line, step)
        following = text.find("      - ", step + 1)
        assert (
            start >= 0 and step >= 0 and 0 <= at < following
        ), f"the plant {name!r} found no record upload's if: in {rel}:{job_id}"
        _write(root, rel, text[:at] + new + text[at + len(line) :])

    return Control(name, plant, True, expect)


def _record_name(template: str, entry: dict[str, Any], run_id: int, attempt: int) -> str:
    values = {"matrix.slug": entry["slug"], "github.run_id": run_id, "github.run_attempt": attempt}
    return re.sub(r"\$\{\{\s*([\w.]+)\s*\}\}", lambda m: str(values.get(m[1], m[0])), template)


def _record_retry_problems(where: str, job: dict[str, Any], uploads: list[dict]) -> list[str]:
    """Why a stage job's two record uploads are not the first try and its retry, or none."""
    first, retry = uploads
    bad: list[str] = []
    if first.get("id") != _RECORD_STEP_ID or first.get("continue-on-error") is not True:
        bad.append(
            f"{where} uploads its record twice, and the first is not id {_RECORD_STEP_ID!r} with"
            " continue-on-error: its failure fails the job before the retry can settle it"
        )
    if retry.get("if") != _RECORD_RETRY_IF:
        bad.append(
            f"{where} retries its record's upload on {retry.get('if')!r}, not only when the"
            f" first failed ({_RECORD_RETRY_IF})"
        )
    if "continue-on-error" in retry:
        bad.append(
            f"{where}'s record retry carries continue-on-error, so a job neither upload wrote a"
            " record for is green"
        )
    one, two = first.get("with") or {}, retry.get("with") or {}
    for key in _RECORD_SAME:
        if one.get(key) != two.get(key):
            bad.append(
                f"{where}'s record retry uploads {key} {two.get(key)!r}, not the first's"
                f" {one.get(key)!r}"
            )
    if two.get("overwrite") is not True:
        bad.append(f"{where}'s record retry does not overwrite what a failed first upload left")
    steps = _trust_steps(job)
    after = [s for s in steps[steps.index(first) + 1 :] if _action(s) == "actions/upload-artifact"]
    if after[-1:] != [retry]:
        bad.append(
            f"{where} retries its record's upload before its other uploads, so nothing stands"
            " between the two"
        )
    return bad


def _step_using(job: dict[str, Any], action: str, **with_: str) -> list[dict[str, Any]]:
    return [
        s
        for s in _trust_steps(job)
        if _action(s) == action
        and all(str((s.get("with") or {}).get(k)) == v for k, v in with_.items())
    ]


def tier_shape_violations(root: Path) -> list[str]:
    """Each tier workflow plans its tier, runs each part of the matrix in a stage job of its
    own after the stage before, uploads each record under record_artifact's name and judges
    them all in `results`, and no job is merge-verdict."""
    bad: list[str] = []
    for name, workflow in _tier_workflows(root).items():
        if not workflow:
            bad.append(f"{_WORKFLOWS}/{name} is missing")
            continue
        tier = _TIER_WORKFLOWS[name]
        jobs = _trust_jobs(workflow)
        bad += _tier_concurrency_violations(name, workflow, jobs)
        grid = _tier_plan(root, tier)
        parts = ci_plan.stage_jobs(grid)
        stages = {job_id: job for job_id, job in jobs.items() if _is_stage_job(job_id, job)}
        if list(stages) != list(parts):
            bad.append(
                f"{name} runs the stage jobs {list(stages)}, and the {tier} tier's whole plan"
                f" splits into {list(parts)}: each part is a stage job's, and no stage job is idle"
            )
        plan_job = jobs.get(_PLAN_JOB, {})
        plan_steps = [s for s in _trust_steps(plan_job) if s.get("id") == "plan"]
        env = (plan_steps[0].get("env") or {}) if plan_steps else {}
        if env.get("TIER") != tier or env.get("STAGE_JOBS") != " ".join(stages):
            bad.append(
                f"{name}'s plan step plans TIER={env.get('TIER')!r} for STAGE_JOBS="
                f"{env.get('STAGE_JOBS')!r}, not the {tier} tier for {' '.join(stages)!r}"
            )
        outputs = plan_job.get("outputs") or {}
        for job_id in ("keys", *stages):
            if outputs.get(job_id) != f"${{{{ steps.plan.outputs.{job_id} }}}}":
                bad.append(f"{name}'s {_PLAN_JOB} job does not publish the plan step's {job_id}")
        before: str | None = None
        for job_id, job in stages.items():
            where = f"{name} job {job_id}"
            needs = job.get("needs") or []
            needs = [needs] if isinstance(needs, str) else list(needs)
            wanted = [_PLAN_JOB] + ([before] if before and job_id != ci_plan.TEST_JOB else [])
            if not set(wanted) <= set(needs):
                bad.append(f"{where} needs {needs}, not the plan and the stage before it")
            sharded = [e["key"] for e in parts.get(job_id, []) if _SHARD_HISTORY in e["external"]]
            if sharded and _SHARD_PLAN_JOB not in jobs:
                bad.append(
                    f"{where} runs {sharded[0]!r}, which reads the run's shard plan, and {name}"
                    f" has no {_SHARD_PLAN_JOB} job"
                )
            elif sharded and _SHARD_PLAN_JOB not in needs:
                bad.append(
                    f"{where} runs {sharded[0]!r}, which reads the run's shard plan, and does not"
                    f" need {_SHARD_PLAN_JOB}"
                )
            if job_id != ci_plan.TEST_JOB:
                before = job_id
            want = {
                "if": f"${{{{ !cancelled() && needs.{_PLAN_JOB}.outputs.{job_id} != '' }}}}",
                "runs-on": "${{ matrix.runs-on }}",
                "timeout-minutes": "${{ matrix.timeout }}",
                "name": "${{ matrix.key }}",
            }
            for key, value in want.items():
                if job.get(key) != value:
                    bad.append(f"{where}: {key} is {job.get(key)!r}, not {value!r}")
            # A stage job's red is its entry's: nothing in it may conclude green on a failure.
            # The one exception is a record upload its retry settles (_record_retry_problems).
            if "continue-on-error" in job:
                bad.append(f"{where} carries continue-on-error, so a red entry's job is green")
            uploads = _step_using(job, "actions/upload-artifact", path=_RECORDS_PATH)
            first = uploads[0] if len(uploads) == 2 else None
            for step in _trust_steps(job):
                if "continue-on-error" in step and step is not first:
                    bad.append(
                        f"{where}'s step {step.get('name') or step.get('run')!r} carries"
                        " continue-on-error, so its red is green"
                    )
            if (job.get("strategy") or {}).get("fail-fast") is not False:
                bad.append(
                    f"{where} does not set fail-fast: false, so one red entry cancels the rest"
                )
            if len(uploads) != 2:
                bad.append(
                    f"{where} uploads its record {len(uploads)} times, not once and a retry if"
                    " that fails"
                )
                continue
            bad += _record_retry_problems(where, job, uploads)
            upload = uploads[0]
            if upload.get("if") != "${{ always() }}":
                bad.append(f"{where} uploads its record only {upload.get('if')!r}, not always()")
            given = upload.get("with") or {}
            if given.get("retention-days") != _RECORD_RETENTION_DAYS:
                bad.append(
                    f"{where} keeps its record {given.get('retention-days')!r} days, not"
                    f" {_RECORD_RETENTION_DAYS}"
                )
            template = str(given.get("name", ""))
            for entry in parts.get(job_id, []):
                for run_id, attempt in ((1, 1), (40123456789, 3)):
                    got = _record_name(template, entry, run_id, attempt)
                    want_name = verdict.record_artifact(
                        entry["key"], run_id=run_id, attempt=attempt
                    )
                    if got != want_name:
                        bad.append(
                            f"{where} uploads {entry['key']!r}'s record as {got!r}, and the verdict"
                            f" reads it only as {want_name!r}"
                        )
                        break
        results = jobs.get(_RESULTS_JOB)
        if results is None:
            bad.append(f"{name} has no {_RESULTS_JOB} job")
        else:
            where = f"{name} job {_RESULTS_JOB}"
            # A cancelled run publishes no verdict (#2946); a failed or skipped job is still read.
            if results.get("if") != "${{ !cancelled() }}":
                bad.append(f"{where} runs {results.get('if')!r}, not !cancelled()")
            needs = results.get("needs") or []
            needs = [needs] if isinstance(needs, str) else list(needs)
            if set(needs) != set(jobs) - {_RESULTS_JOB}:
                bad.append(f"{where} needs {sorted(needs)}, not every other job of the run")
            bad += _tier_verdict_violations(where, results)
        bad += _tier_partner_violations(name, jobs, stages)
        for job_id, job in jobs.items():
            labels = [job_id, str(job.get("name", ""))]
            if job_id in stages:
                labels += [e["key"] for e in parts.get(job_id, [])]
            elif "${{" in str(job.get("name", "")):
                bad.append(f"{name} job {job_id} has a name the row cannot read")
            if any(_MERGE_VERDICT_WORD.fullmatch(label.strip()) for label in labels):
                bad.append(f"{name} job {job_id} runs under the check name {MERGE_VERDICT}")
    # The expensive tier runs once per candidate: none of its jobs is planned in both.
    both = {e["id"] for e in _tier_plan(root, "change")["include"]} & {
        e["id"] for e in _tier_plan(root, "merge")["include"]
    }
    for job_id in sorted(both & _EXPENSIVE):
        bad.append(
            f"{job_id} is planned in both pr.yml's tier and merge.yml's: an expensive job runs"
            " once per candidate"
        )
    return bad


#: The plan job's output of the partner head, each partner checkout's ref, and the verdict's
#: partner line.
_PARTNER_OUTPUT = "      apps-partner: ${{ steps.partners.outputs.apps-partner }}\n"
_PARTNER_REF = "          ref: ${{ needs.changes.outputs.apps-partner }}\n"
_VERDICT_PARTNER = '            --head "$HEAD_SHA" --partner "apps-partner=$APPS_PARTNER" \\\n'

#: The plan job's output of each key's partners, and the verdict's line that holds each record
#: to its key's.
_KEY_PARTNERS_OUTPUT = "      key-partners: ${{ steps.plan.outputs.key-partners }}\n"
_VERDICT_KEY_PARTNERS = '            --key-partners "$RUNNER_TEMP/key-partners.json" \\\n'

#: The verdict step's head, which a plant conditions, and its last line, which one extends.
_VERDICT_HEAD = (
    "      - name: the verdict over the plan's keys and the run's records\n"
    "        if: ${{ !cancelled() }}\n"
)
_VERDICT_TAIL = '            --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n'


def _tier_concurrency_violations(
    name: str, workflow: dict[str, Any], jobs: dict[str, dict[str, Any]]
) -> list[str]:
    """One group per workflow and pull request, or commit for any other run; only a pull
    request's superseded run cancelled in flight, never a dispatch's or a push's; and no job's
    group of its own but merge.yml's stage jobs' (decision 9, whose shape the main-push row
    holds). A later dispatch of the commit waits, and never waits on the other tier's run.

    Every Dependabot head's runs, both tiers', wait in one group instead, one at a time and none
    cancelled (:func:`_dependabot_tier_concurrency`)."""
    bad: list[str] = []
    concurrency = workflow.get("concurrency")
    concurrency = concurrency if isinstance(concurrency, dict) else {"group": concurrency}
    shape = _dependabot_tier_concurrency(name.removesuffix(".yml"))
    if concurrency.get("group") != shape["group"]:
        bad.append(
            f"{name} groups its runs as {concurrency.get('group')!r}, not {shape['group']!r}: one"
            " group per workflow and pull request, or commit, and every Dependabot head's runs in"
            " one"
        )
    if concurrency.get("cancel-in-progress") != shape["cancel-in-progress"]:
        bad.append(
            f"{name} sets cancel-in-progress {concurrency.get('cancel-in-progress')!r}, not"
            f" {shape['cancel-in-progress']!r}: only a pull request's superseded run is cancelled"
            " in flight, and never a Dependabot head's run, which waits in a group it shares"
        )
    if concurrency.get("queue") != shape.get("queue"):
        bad.append(
            f"{name} sets queue {concurrency.get('queue')!r}, not {shape.get('queue')!r}: GitHub"
            " refuses `max` beside cancel-in-progress, and at `single` a Dependabot head's waiting"
            " run is replaced by the next one, so cancelled"
        )
    for job_id, job in jobs.items():
        staged = name == "merge.yml" and _planned_tier(name, job) is not None
        if "concurrency" in job and not staged:
            bad.append(f"{name} job {job_id} has a concurrency group of its own")
    return bad


#: A tier workflow's run key, and when it cancels a run in flight.
_TIER_RUN_KEY = "${{ github.event.pull_request.number || github.sha }}"
_PR_RUN_CANCELS = "${{ github.event_name == 'pull_request' }}"


def _tier_partner_violations(
    name: str, jobs: dict[str, dict[str, Any]], stages: dict[str, dict[str, Any]]
) -> list[str]:
    """Each partner head is read once, in the plan job: it publishes the head, every stage job's
    checkout of that partner checks the head out, and the verdict names it as the candidate's
    partner. So every record naming the partner names the head the verdict judges it against."""
    bad: list[str] = []
    plan_job = jobs.get(_PLAN_JOB, {})
    results = jobs.get(_RESULTS_JOB, {})
    verdict_steps = [s for s in _trust_steps(results) if "--records" in str(s.get("run", ""))]
    for partner, path in run_jobs.PARTNERS.items():
        published = f"${{{{ needs.{_PLAN_JOB}.outputs.{partner} }}}}"
        output = (plan_job.get("outputs") or {}).get(partner)
        if output != f"${{{{ steps.partners.outputs.{partner} }}}}":
            bad.append(
                f"{name}'s {_PLAN_JOB} job does not publish the partner head {partner} from its"
                f" partners step (it publishes {output!r})"
            )
        for job_id, job in stages.items():
            for step in _step_using(job, "actions/checkout"):
                given = step.get("with") or {}
                if given.get("path") == path and given.get("ref") != published:
                    bad.append(
                        f"{name} job {job_id} checks out {partner} at {given.get('ref')!r}, not"
                        f" the head {_PLAN_JOB} published ({published})"
                    )
        for step in verdict_steps:
            named = re.findall(
                rf'--partner "{re.escape(partner)}=\$(\w+)"', str(step.get("run", ""))
            )
            if len(named) != 1:
                bad.append(
                    f"{name} job {_RESULTS_JOB} judges no partner {partner} (or names it"
                    f" {len(named)} times): the verdict names the head every entry checked out"
                )
                continue
            value = (step.get("env") or {}).get(named[0])
            if value != published:
                bad.append(
                    f"{name} job {_RESULTS_JOB} judges {partner} against {value!r}, not the head"
                    f" {_PLAN_JOB} published ({published})"
                )
    # Each record is held to the partners its own key's job reads, as the plan names them: a
    # verdict given none would hold every record to the candidate's whole set.
    by_key = f"${{{{ needs.{_PLAN_JOB}.outputs.key-partners }}}}"
    output = (plan_job.get("outputs") or {}).get("key-partners")
    if output != "${{ steps.plan.outputs.key-partners }}":
        bad.append(
            f"{name}'s {_PLAN_JOB} job does not publish the partners by key from its plan step"
            f" (it publishes {output!r})"
        )
    for step in verdict_steps:
        if str(step.get("run", "")).count('--key-partners "$RUNNER_TEMP/key-partners.json"') != 1:
            bad.append(
                f"{name} job {_RESULTS_JOB} holds no record to its key's partners: the verdict is"
                " given --key-partners once"
            )
        value = (step.get("env") or {}).get("KEY_PARTNERS")
        if value != by_key:
            bad.append(
                f"{name} job {_RESULTS_JOB} reads the partners by key from {value!r}, not what"
                f" {_PLAN_JOB} published ({by_key})"
            )
    return bad


def _tier_verdict_violations(where: str, results: dict[str, Any]) -> list[str]:
    """`results` downloads the run's records unconditionally, then judges them in its last step,
    which runs unless the run is cancelled: a condition on it would leave `results` green with
    nothing judged when `changes` published no plan."""
    bad: list[str] = []
    steps = _trust_steps(results)
    pattern = f"{verdict.RECORD_ARTIFACT_PREFIX}*"
    downloads = _step_using(results, "actions/download-artifact", pattern=pattern)
    if not downloads:
        bad.append(f"{where} does not download the run's records ({pattern})")
    for step in downloads:
        for key in ("if", "continue-on-error"):
            if key in step:
                bad.append(f"{where} holds back the records' download by {key}: {step[key]!r}")
    judged = [n for n, s in enumerate(steps) if "--records" in str(s.get("run", ""))]
    if len(judged) != 1:
        return [*bad, f"{where} runs {len(judged)} verdict steps over the records, not one"]
    step = steps[judged[0]]
    env = step.get("env") or {}
    if env.get("NEEDS") != "${{ toJSON(needs) }}" or env.get("KEYS") != (
        f"${{{{ needs.{_PLAN_JOB}.outputs.keys }}}}"
    ):
        bad.append(f"{where} judges no toJSON(needs) against the plan's keys")
    if step.get("if") != "${{ !cancelled() }}":
        bad.append(
            f"{where} runs its verdict {step.get('if')!r}, not !cancelled(): a run whose"
            f" {_PLAN_JOB} published no plan must read red, never skip the verdict"
        )
    if "continue-on-error" in step or "continue-on-error" in results:
        bad.append(f"{where} lets its verdict fail green (continue-on-error)")
    if judged[0] != len(steps) - 1:
        bad.append(f"{where}: the verdict is not its last step")
    if any(steps.index(d) > judged[0] for d in downloads):
        bad.append(f"{where} downloads the run's records after the verdict reads them")
    return bad


THE_TIER_WORKFLOWS_RUN_THEIR_TIERS_MATRIX = Row(
    name="the-tier-workflows-run-their-tiers-matrix-and-judge-its-records",
    check=tier_shape_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant("pr.yml planning the merge tier", _PR_YML, "          TIER: change\n",
               "          TIER: merge\n", expect="pr.yml's plan step plans TIER='merge'"),
        _plant("merge.yml without its last stage job", _MERGE_YML,
               "      matrix: ${{ fromJSON(needs.changes.outputs.test-3) }}\n",
               "      matrix: ${{ fromJSON(needs.changes.outputs.test-2) }}\n",
               expect="splits into ['build', 'test', 'test-2', 'test-3']"),
        _plant_in_job("a stage job that does not wait for the stage before it", _MERGE_YML,
                      "test-3", "    needs: [changes, build, test-2]\n",
                      "    needs: [changes, build]\n",
                      expect="merge.yml job test-3 needs ['changes', 'build']"),
        _plant_in_job("a stage job reading another job's part", _MERGE_YML, "test-2",
                      "    if: ${{ !cancelled() && needs.changes.outputs.test-2 != '' }}\n",
                      "    if: ${{ !cancelled() && needs.changes.outputs.build != '' }}\n",
                      expect="merge.yml job test-2: if is"),
        _plant_in_job("a stage job's timeout of its own", _PR_YML, "test",
                      "    timeout-minutes: ${{ matrix.timeout }}\n",
                      "    timeout-minutes: 60\n", expect="pr.yml job test: timeout-minutes"),
        _plant_in_job("one red entry cancelling the rest", _MERGE_YML, "build",
                      "      fail-fast: false\n", "      fail-fast: true\n",
                      expect="does not set fail-fast: false"),
        _plant_in_job("continue-on-error on a stage job's run-job step", _MERGE_YML, "test",
                      _RUN_JOB_STEP, _RUN_JOB_STEP + "        continue-on-error: true\n",
                      expect="merge.yml job test's step"),
        _plant_in_job("continue-on-error on a whole stage job", _PR_YML, "test",
                      "    timeout-minutes: ${{ matrix.timeout }}\n",
                      "    timeout-minutes: ${{ matrix.timeout }}\n    continue-on-error: true\n",
                      expect="pr.yml job test carries continue-on-error"),
        _plant_in_job("a stage job's if: without !cancelled()", _MERGE_YML, "test-2",
                      "    if: ${{ !cancelled() && needs.changes.outputs.test-2 != '' }}\n",
                      "    if: ${{ needs.changes.outputs.test-2 != '' }}\n",
                      expect="merge.yml job test-2: if is"),
        _plant_in_job("the record named without the attempt", _PR_YML, "test",
                      "-${{ github.run_id }}-${{ github.run_attempt }}\n",
                      "-${{ github.run_id }}\n", expect="and the verdict reads it only as"),
        _plant_in_job("the record named by key, not slug", _PR_YML, "test",
                      "name: ci-record-${{ matrix.slug }}-", "name: ci-record-${{ matrix.key }}-",
                      expect="and the verdict reads it only as"),
        _plant_in_job("the record kept a week", _MERGE_YML, "test",
                      "          retention-days: 30\n", "          retention-days: 7\n",
                      expect="keeps its record 7 days, not 30"),
        _record_if_plant("the record uploaded only on a green", _MERGE_YML, "test-2", "",
                         expect="uploads its record only None"),
        _record_retry_plant("a record upload tried again, last, when it failed", _MERGE_YML,
                            "test", fails=False),
        _record_retry_plant("pr.yml's record upload tried again", _PR_YML, "test", fails=False),
        _record_retry_plant("a record uploaded once, with no retry", _MERGE_YML, "test-2",
                            retry="", first=_RECORD_UPLOAD,
                            expect="uploads its record 1 times, not once and a retry"),
        _record_retry_plant("a record retry that runs after a green upload too", _MERGE_YML,
                            "test-2", ((f"        if: {_RECORD_RETRY_IF}\n",
                                        "        if: ${{ always() }}\n"),),
                            expect="retries its record's upload on '${{ always() }}'"),
        _record_retry_plant("a record retry that is green when it fails", _MERGE_YML, "build",
                            ((f"        if: {_RECORD_RETRY_IF}\n",
                              f"        if: {_RECORD_RETRY_IF}\n"
                              "        continue-on-error: true\n"),),
                            expect="record retry carries continue-on-error"),
        _record_retry_plant("a record's first upload that fails the job before its retry",
                            _PR_YML, "test", first=_RECORD_UPLOAD.replace(
                                "        if:", "        id: record\n        if:"),
                            expect="the first is not id 'record' with continue-on-error"),
        _record_retry_plant("a record retry uploading another name", _MERGE_YML, "test-3",
                            retry=_RECORD_RETRY.replace("name: ci-record-", "name: ci-recorded-"),
                            expect="record retry uploads name"),
        _record_retry_plant("a record retry kept a week", _MERGE_YML, "test",
                            retry=_RECORD_RETRY.replace(
                                f"retention-days: {_RECORD_RETENTION_DAYS}\n", "retention-days: 7\n"
                            ),
                            expect="record retry uploads retention-days 7"),
        _record_retry_plant("a record retry that leaves a failed upload's artifact", _PR_YML,
                            "test", retry=_RECORD_RETRY.replace("          overwrite: true\n", ""),
                            expect="does not overwrite"),
        _record_retry_plant("a record retry right after the first, with nothing between",
                            _MERGE_YML, "test-2",
                            retry="",
                            edits=(("      - name: Upload the entry's reports\n",
                                    _RECORD_RETRY + "      - name: Upload the entry's reports\n"),),
                            expect="before its other uploads"),
        _record_retry_plant("continue-on-error on another upload beside the pair", _MERGE_YML,
                            "build", (("      - name: Upload the entry's reports\n"
                                       "        if: ${{ always() }}\n",
                                       "      - name: Upload the entry's reports\n"
                                       "        if: ${{ always() }}\n"
                                       "        continue-on-error: true\n"),),
                            expect="merge.yml job build's step"),
        _record_retry_plant("a third record upload", _MERGE_YML, "test-3",
                            retry=_RECORD_RETRY + _RECORD_RETRY,
                            expect="uploads its record 3 times"),
        _plant("results only when everything passed", _PR_YML,
               "    needs: [changes, test]\n    if: ${{ !cancelled() }}\n",
               "    needs: [changes, test]\n", expect="pr.yml job results runs None"),
        _plant("results on a cancelled run", _MERGE_YML,
               "    needs: [tree-check, changes, shard-plan, build, test, test-2, test-3]\n"
               "    if: ${{ !cancelled() }}\n",
               "    needs: [tree-check, changes, shard-plan, build, test, test-2, test-3]\n"
               "    if: ${{ always() }}\n",
               expect="merge.yml job results runs '${{ always() }}', not !cancelled()"),
        _plant("results not reading the last stage", _MERGE_YML,
               "    needs: [tree-check, changes, shard-plan, build, test, test-2, test-3]\n",
               "    needs: [tree-check, changes, shard-plan, build, test, test-2]\n",
               expect="not every other job of the run"),
        _plant("results not reading the run's records", _MERGE_YML,
               "          pattern: ci-record-*\n", "          pattern: ci-records-*\n",
               expect="does not download the run's records"),
        _plant("the verdict without needs", _PR_YML, "          NEEDS: ${{ toJSON(needs) }}\n",
               "          NEEDS: '{}'\n", expect="judges no toJSON(needs)"),
        _plant("the verdict held back until a plan is published", _PR_YML, _VERDICT_HEAD,
               _VERDICT_HEAD.replace("!cancelled() }}",
                                     "!cancelled() && needs.changes.outputs.keys != '' }}"),
               expect="pr.yml job results runs its verdict"),
        _plant("the verdict skipped when the plan is red", _MERGE_YML, _VERDICT_HEAD,
               _VERDICT_HEAD.replace("!cancelled()", "needs.changes.result == 'success'"),
               expect="merge.yml job results runs its verdict"),
        _plant("a step after the verdict", _MERGE_YML, _VERDICT_TAIL,
               _VERDICT_TAIL + "      - run: echo judged\n",
               expect="merge.yml job results: the verdict is not its last step"),
        _plant("the records downloaded only on a green plan", _PR_YML,
               "      - name: every entry's record\n",
               "      - name: every entry's record\n"
               "        if: ${{ needs.changes.result == 'success' }}\n",
               expect="pr.yml job results holds back the records' download by if"),
        _plant_tier("a run in flight cancelled by a later dispatch", _MERGE_YML,
                    "cancel-in-progress", "true", expect="merge.yml sets cancel-in-progress True"),
        _plant_tier("a pull request's superseded run kept", _PR_YML, "cancel-in-progress",
                    "false", expect="pr.yml sets cancel-in-progress False"),
        _plant_tier("one group for every run", _PR_YML, "group", "pr",
                    expect="pr.yml groups its runs as 'pr'"),
        _plant_tier("a group per commit, which a pull request's newer push does not share",
                    _PR_YML, "group", "pr-${{ github.sha }}",
                    expect="pr.yml groups its runs as 'pr-${{ github.sha }}'"),
        _plant_tier("pr.yml in merge.yml's group", _PR_YML, "group", f"merge-{_TIER_RUN_KEY}",
                    expect="pr.yml groups its runs as 'merge-"),
        Control("every Dependabot head's runs in one queued group",
                _tier_concurrency(_PR_YML, _MERGE_YML, dependabot=True), fails=False),
        Control("each tier keying its own runs, Dependabot's too",
                _tier_concurrency(_PR_YML, dependabot=False),
                expect=f"pr.yml groups its runs as 'pr-{_TIER_RUN_KEY}'"),
        Control("a Dependabot head's waiting run replaced by the next one",
                _both(_tier_concurrency(_PR_YML, dependabot=True),
                      _tier_concurrency_line(_PR_YML, "queue", "")),
                expect="pr.yml sets queue None"),
        Control("a Dependabot head's run cancelled in flight",
                _both(_tier_concurrency(_MERGE_YML, dependabot=True),
                      _tier_concurrency_line(_MERGE_YML, "cancel-in-progress", _PR_RUN_CANCELS)),
                expect="merge.yml sets cancel-in-progress"),
        Control("queue max beside cancel-in-progress, which GitHub refuses",
                _both(_tier_concurrency(_MERGE_YML, dependabot=False),
                      _tier_concurrency_line(_MERGE_YML, "queue", "max")),
                expect="merge.yml sets queue 'max'"),
        Control("one tier in Dependabot's group keyed for every pull request",
                _both(_tier_concurrency(_PR_YML, dependabot=True),
                      _tier_concurrency_line(_PR_YML, "group", "dependabot-heads")),
                expect="pr.yml groups its runs as 'dependabot-heads'"),
        _plant_in_job("a merge.yml job other than a stage job with a group of its own", _MERGE_YML,
                      "results", "    if: ${{ !cancelled() }}\n",
                      "    if: ${{ !cancelled() }}\n    concurrency:\n      group: verdict\n",
                      expect="merge.yml job results has a concurrency group of its own"),
        _plant_in_job("a job with a group of its own that cancels", _PR_YML, "results",
                      "    if: ${{ !cancelled() }}\n",
                      "    if: ${{ !cancelled() }}\n    concurrency:\n      group: verdict\n"
                      "      cancel-in-progress: true\n",
                      expect="pr.yml job results has a concurrency group of its own"),
        _plant_in_job("the suite's shards not waiting for the shard plan", _MERGE_YML, "test",
                      "    needs: [changes, shard-plan]\n", "    needs: [changes]\n",
                      expect="and does not need shard-plan"),
        Control("no shard plan for the suite's shards",
                _cut(_MERGE_YML, "\n  shard-plan:\n", "\n  build:\n"),
                expect="and merge.yml has no shard-plan job"),
        _plant("a stage job named merge-verdict", _PR_YML, "    name: ${{ matrix.key }}\n",
               "    name: merge-verdict\n", expect="pr.yml job test: name is 'merge-verdict'"),
        _plant("results renamed merge-verdict", _MERGE_YML, "\n  results:\n",
               "\n  merge-verdict:\n", expect="runs under the check name merge-verdict"),
        _plant("the suite's shards in the change tier too", catalogue.CATALOGUE,
               'id = "python-suite"\ntiers = ["entry", "merge"]\n',
               'id = "python-suite"\ntiers = ["change", "entry", "merge"]\n',
               expect="python-suite is planned in both pr.yml's tier and merge.yml's"),
        Control("a catalogue job named merge-verdict, which a stage job's name would read",
                _CATALOGUE_JOB_NAMED_MERGE_VERDICT,
                expect="pr.yml job test runs under the check name merge-verdict"),
        # The partner head is read once, in the plan job: every entry checks that commit out,
        # and the verdict judges each record's partners against it.
        _plant_in_job("a partner checkout of the apps repository's moving main", _MERGE_YML,
                      "build", _PARTNER_REF, "",
                      expect="merge.yml job build checks out apps-partner at None"),
        _plant("the partner head left unpublished", _PR_YML, _PARTNER_OUTPUT, "",
               expect="pr.yml's changes job does not publish the partner head apps-partner"),
        _plant("the verdict judging no partner", _PR_YML, _VERDICT_PARTNER,
               '            --head "$HEAD_SHA" \\\n',
               expect="pr.yml job results judges no partner apps-partner"),
        _plant_in_job("the verdict's partner read from another value", _MERGE_YML, "results",
                      "          APPS_PARTNER: ${{ needs.changes.outputs.apps-partner }}\n",
                      "          APPS_PARTNER: ${{ github.sha }}\n",
                      expect="merge.yml job results judges apps-partner against"),
        # Each record is held to its own key's partners, which the plan names.
        _plant("the partners by key left unpublished", _PR_YML, _KEY_PARTNERS_OUTPUT, "",
               expect="pr.yml's changes job does not publish the partners by key"),
        _plant("the verdict given no partners by key", _MERGE_YML, _VERDICT_KEY_PARTNERS, "",
               expect="merge.yml job results holds no record to its key's partners"),
        _plant_in_job("the partners by key read from another value", _MERGE_YML, "results",
                      "          KEY_PARTNERS: ${{ needs.changes.outputs.key-partners }}\n",
                      "          KEY_PARTNERS: ${{ needs.changes.outputs.keys }}\n",
                      expect="merge.yml job results reads the partners by key from"),
    ),
)  # fmt: skip


def tier_catalogue_check_violations(root: Path) -> list[str]:
    """merge.yml's verdict reads the job whose last step is main's catalogue check."""
    workflow = _tier_workflows(root)["merge.yml"]
    if not workflow:
        return [f"{_MERGE_YML} is missing"]
    jobs = _trust_jobs(workflow)
    job = jobs.get(_PLAN_JOB)
    if job is None:
        return [f"merge.yml has no {_PLAN_JOB} job, the one the merge decision requires"]
    bad: list[str] = []
    steps = _trust_steps(job)
    ids = [s.get("id") for s in steps]
    if "catalogue-check" not in ids:
        bad.append(f"merge.yml's {_PLAN_JOB} job runs no catalogue-check step")
    else:
        check = steps[ids.index("catalogue-check")]
        if ids[-1] != "catalogue-check" or ids.index("plan") > ids.index("catalogue-check"):
            bad.append(f"merge.yml's catalogue check is not the last step after the plan: {ids}")
        for key in ("if", "continue-on-error"):
            if key in check:
                bad.append(f"merge.yml's catalogue check is held back by {key}: {check[key]!r}")
    if "continue-on-error" in job:
        bad.append(f"merge.yml's {_PLAN_JOB} job carries continue-on-error, so a refusal is green")
    results = jobs.get(_RESULTS_JOB) or {}
    needs = results.get("needs") or []
    if _PLAN_JOB not in ([needs] if isinstance(needs, str) else needs):
        bad.append(f"merge.yml's {_RESULTS_JOB} does not read the {_PLAN_JOB} job")
    return bad


_CHECK_STEP_HEAD = (
    "      - name: main's tools/ci checks this run's catalogue\n        id: catalogue-check\n"
)

THE_CATALOGUE_CHECK_STANDS_IN_FRONT_OF_MERGE_YMLS_VERDICT = Row(
    name="main's catalogue check stands in front of merge.yml's verdict",
    check=tier_catalogue_check_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant("the check's step id dropped", _MERGE_YML, _CHECK_STEP_HEAD,
               "      - name: main's tools/ci checks this run's catalogue\n",
               expect="runs no catalogue-check step"),
        _plant("the check held back by a condition", _MERGE_YML, _CHECK_STEP_HEAD,
               _CHECK_STEP_HEAD + "        if: ${{ github.event_name == 'pull_request' }}\n",
               expect="held back by if"),
        _plant("the check allowed to fail", _MERGE_YML, _CHECK_STEP_HEAD,
               _CHECK_STEP_HEAD + "        continue-on-error: true\n",
               expect="held back by continue-on-error"),
        _plant("the job allowed to fail", _MERGE_YML,
               "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n    permissions:\n"
               "      contents: read\n      actions: read",
               "    continue-on-error: true\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"
               "    permissions:\n      contents: read\n      actions: read",
               expect="carries continue-on-error"),
        _plant("a step after the check", _MERGE_YML, "\n  shard-plan:\n",
               "      - run: echo after\n\n  shard-plan:\n",
               expect="is not the last step after the plan"),
        _plant("results not reading the job", _MERGE_YML,
               "    needs: [tree-check, changes, shard-plan, build, test, test-2, test-3]\n",
               "    needs: [tree-check, shard-plan, build, test, test-2, test-3]\n",
               expect="does not read the changes job"),
    ),
)  # fmt: skip


#: A stage job's checkout of the candidate, whose inputs one plant extends.
_STAGE_CHECKOUT_DEPTH = "          fetch-depth: ${{ matrix.history && '0' || '1' }}\n"

#: A condition, job or step, that runs only on a push, on main, or against the commit a push
#: replaced.
_PUSH_ONLY = re.compile(
    r"\bpush\b|refs/heads/main|event\.before|\bBEFORE\b"
    # The push event's own fields, which no pull request's or dispatch's event holds.
    r"|event\.(?:commits|head_commit|pusher|compare|forced)\b"
)
#: The keys whose values the row reads as a whole (a condition, an env value, an input), so an
#: expression elsewhere is read once, on its own.
_READ_WHOLE = frozenset({"if", "env", "with"})


def _env_values(env: object) -> list[object]:
    """An env block's values, or the block itself when it is one expression."""
    if env is None:
        return []
    return list(env.values()) if isinstance(env, dict) else [env]


def _with_values(inputs: object) -> list[tuple[str, object]]:
    """A ``with:`` block's inputs, or the block itself when it is one expression."""
    if inputs is None:
        return []
    return list(inputs.items()) if isinstance(inputs, dict) else [("with", inputs)]


def _other_expressions(value: object, where: str = "") -> Iterator[tuple[str, str]]:
    """Each ``${{ }}`` expression under ``value``, by where it sits, outside the keys the row
    reads whole (:data:`_READ_WHOLE`) and outside the steps, which it reads one by one."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) not in _READ_WHOLE and str(key) != "steps":
                yield from _other_expressions(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, list):
        for n, item in enumerate(value):
            yield from _other_expressions(item, f"{where}[{n}]")
    elif value is not None:
        for expression in _EXPRESSION.findall(str(value)):
            yield where, expression


def pr_push_stage_violations(root: Path) -> list[str]:
    """pr.yml has no push trigger, no job or step that runs only on a push or on main, no
    workflow, job or step env or input that reads one, no other expression that reads one, and
    no tree check."""
    workflow = _tier_workflows(root)["pr.yml"]
    if not workflow:
        return [f"{_PR_YML} is missing"]
    bad: list[str] = []
    if "push" in _triggers(workflow):
        bad.append("pr.yml runs on push: the feedback tier has no push stage")
    for value in _env_values(workflow.get("env")):
        if _PUSH_ONLY.search(str(value)):
            bad.append(f"pr.yml's env reads {value!r}, a push stage's condition, in every job")
    for job_id, job in _trust_jobs(workflow).items():
        if job_id == "tree-check" or "tree_tested" in json.dumps(job):
            bad.append(f"pr.yml job {job_id} is a tree check, which is merge.yml's push stage")
        conditions = [("the job", job.get("if"))]
        conditions += [("the job's env", v) for v in _env_values(job.get("env"))]
        conditions += [(repr(s.get("name") or "a step"), s.get("if")) for s in _trust_steps(job)]
        conditions += [
            (repr(s.get("name") or "a step"), v)
            for s in _trust_steps(job)
            for v in _env_values(s.get("env"))
        ]
        conditions += [(f"the job's input {k}", v) for k, v in _with_values(job.get("with"))]
        conditions += [
            (f"the input {k} of {s.get('name') or 'a step'!r}", v)
            for s in _trust_steps(job)
            for k, v in _with_values(s.get("with"))
        ]
        conditions += [
            (where, f"${{{{{expression}}}}}")
            for where, expression in (
                *_other_expressions(job),
                *(
                    (f"{s.get('name') or 'a step'!r} {where}", expression)
                    for s in _trust_steps(job)
                    for where, expression in _other_expressions(s)
                ),
            )
        ]
        for what, condition in conditions:
            if condition is not None and _PUSH_ONLY.search(str(condition)):
                bad.append(
                    f"pr.yml job {job_id}: {what} reads {condition!r}, a push stage's condition"
                )
    for where, expression in _other_expressions({k: v for k, v in workflow.items() if k != "jobs"}):
        if _PUSH_ONLY.search(expression):
            bad.append(f"pr.yml's {where} reads ${{{{{expression}}}}}, a push stage's condition")
    return bad


#: How merge.yml's plan step reads the tree check: a push plans by its answer, every other run
#: plans what it changed. The one place the answer is read.
_PUSH_PLAN = (
    "${{ github.event_name == 'push' && (needs.tree-check.outputs.tested == 'true'"
    " && 'tested' || 'full') || '' }}"
)


def merge_push_stage_violations(root: Path) -> list[str]:
    """Every push to main runs the security corpus and apps-contract, whatever the tree check
    answers (the owner's rule, 2026-10-19): merge.yml's tree check answers `changes` alone, whose
    plan step reads it only as :data:`_PUSH_PLAN`, so a tested push plans `[tier.merge]
    every_push`, an untested one the whole tier, and no other job reads the answer; and the
    catalogue's every_push names both jobs."""
    workflow = _tier_workflows(root)["merge.yml"]
    if not workflow:
        return [f"{_MERGE_YML} is missing"]
    jobs = _trust_jobs(workflow)
    bad: list[str] = []
    answer = f"needs.{_TREE_CHECK_JOB}.outputs"
    if _TREE_CHECK_JOB not in jobs:
        bad.append(
            f"merge.yml has no {_TREE_CHECK_JOB} job: a push cannot ask whether its tree was tested"
        )
    plan_job = jobs.get(_PLAN_JOB) or {}
    if _TREE_CHECK_JOB not in _needs(plan_job):
        bad.append(
            f"merge.yml's {_PLAN_JOB} job does not need {_TREE_CHECK_JOB}, whose answer it plans by"
        )
    steps = [s for s in _trust_steps(plan_job) if s.get("id") == "plan"]
    given = (steps[0].get("env") or {}).get("PUSH_PLAN") if steps else None
    if given != _PUSH_PLAN:
        bad.append(
            f"merge.yml's plan step reads PUSH_PLAN as {given!r}, not {_PUSH_PLAN!r}: a push plans"
            " by the tree check's answer, and every other run by what it changed"
        )
    for job_id, job in jobs.items():
        text = yaml.safe_dump({k: v for k, v in job.items()}, sort_keys=True)
        if job_id != _PLAN_JOB and answer in text:
            bad.append(
                f"merge.yml job {job_id} reads the tree check's answer: only {_PLAN_JOB}'s plan"
                " reads it, so no job a push must run is gated on it"
            )
    try:
        cat = catalogue.load((root / catalogue.CATALOGUE).read_text(encoding="utf-8"))
    except catalogue.CatalogueError as exc:
        return [*bad, *exc.problems]
    missing = [j for j in catalogue.EVERY_PUSH_JOBS if j not in (cat.every_push or ())]
    if missing:
        bad.append(
            f"[tier.merge] every_push does not name {', '.join(missing)}: every push to main runs"
            " them whatever the tree check answers"
        )
    return bad


MERGE_YML_PUSH_STAGE_RUNS_THE_SECURITY_CORPUS_AND_APPS_CONTRACT = Row(
    name="every-push-runs-the-security-corpus-and-apps-contract-whatever-the-tree-check-says",
    check=merge_push_stage_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant_in_job("a stage job gated on the tree check", _MERGE_YML, "test",
                      "    if: ${{ !cancelled() && needs.changes.outputs.test != '' }}\n",
                      "    if: ${{ !cancelled() && needs.changes.outputs.test != ''"
                      " && needs.tree-check.outputs.tested != 'true' }}\n",
                      expect="merge.yml job test reads the tree check's answer"),
        _plant("the verdict reading the tree check's answer", _MERGE_YML,
               "          KEYS: ${{ needs.changes.outputs.keys }}\n",
               "          KEYS: ${{ needs.changes.outputs.keys }}\n"
               "          TESTED: ${{ needs.tree-check.outputs.tested }}\n",
               expect="merge.yml job results reads the tree check's answer"),
        _plant("every push planned as tested", _MERGE_YML, _PUSH_PLAN,
               "${{ github.event_name == 'push' && 'tested' || '' }}",
               expect="merge.yml's plan step reads PUSH_PLAN as"),
        _plant("the answer read off a push too", _MERGE_YML, _PUSH_PLAN,
               "${{ needs.tree-check.outputs.tested == 'true' && 'tested' || '' }}",
               expect="merge.yml's plan step reads PUSH_PLAN as"),
        # A failed or cancelled check answers nothing: only `tested == 'true'` plans a push as
        # tested, so a check that reads `!= 'false'` would skip the tier on no answer.
        _plant("a push planned as tested unless the answer is false", _MERGE_YML, _PUSH_PLAN,
               _PUSH_PLAN.replace("tested == 'true'", "tested != 'false'"),
               expect="merge.yml's plan step reads PUSH_PLAN as"),
        _plant("changes planning without the answer", _MERGE_YML,
               "    needs: [tree-check]\n    runs-on: ubuntu-24.04\n",
               "    runs-on: ubuntu-24.04\n",
               expect="does not need tree-check"),
        _plant("no tree check", _MERGE_YML, "\n  tree-check:\n", "\n  tree-check-renamed:\n",
               expect="merge.yml has no tree-check job"),
        _plant("every_push without apps-contract", catalogue.CATALOGUE,
               'every_push = ["browse-live", "security", "apps-contract"]\n',
               'every_push = ["browse-live", "security"]\n',
               expect="every_push leaves out apps-contract"),
        _plant("no every_push list", catalogue.CATALOGUE,
               'every_push = ["browse-live", "security", "apps-contract"]\n', "",
               expect="[tier.merge] every_push does not name security, apps-contract"),
    ),
)  # fmt: skip


PR_YML_CARRIES_NO_PUSH_STAGE = Row(
    name="pr.yml carries no push stage",
    check=pr_push_stage_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant("a push trigger", _PR_YML, "  workflow_dispatch: {}\n",
               "  workflow_dispatch: {}\n  push:\n    branches: [main]\n",
               expect="pr.yml runs on push"),
        _plant("a job that runs only on a push", _PR_YML,
               "    if: ${{ !cancelled() && needs.changes.outputs.test != '' }}\n",
               "    if: ${{ github.event_name == 'push' && needs.changes.outputs.test != '' }}\n",
               expect="pr.yml job test: the job reads"),
        _record_if_plant("a step that runs only on main", _PR_YML, "test",
                         "        if: ${{ github.ref == 'refs/heads/main' }}\n",
                         expect="reads \"${{ github.ref == 'refs/heads/main' }}\""),
        _plant("a step reading the commit a push replaced", _PR_YML,
               "          HEAD_SHA: ${{ github.sha }}\n",
               "          HEAD_SHA: ${{ github.sha }}\n"
               "          BEFORE: ${{ github.event.before }}\n",
               expect="a push stage's condition"),
        _plant_in_job("a job env reading the commit a push replaced", _PR_YML, "test",
                      "    needs: [changes]\n",
                      "    needs: [changes]\n    env:\n      BEFORE: ${{ github.event.before }}\n",
                      expect="pr.yml job test: the job's env reads"),
        _plant("a workflow env reading main's ref", _PR_YML,
               "  UV_PYTHON_PREFERENCE: only-managed\n",
               "  UV_PYTHON_PREFERENCE: only-managed\n  BASE: refs/heads/main\n",
               expect="pr.yml's env reads 'refs/heads/main'"),
        _plant("a tree check", _PR_YML, "\n  results:\n",
               "\n  tree-check:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"
               "    steps:\n      - run: python3 tree_tested.py\n\n  results:\n",
               expect="pr.yml job tree-check is a tree check"),
        Control("merge.yml's own push trigger is not pr.yml's", _nothing, fails=False),
        # A step's inputs and every other expression are read too: a push stage need not sit in
        # a condition or an env.
        _plant_in_job("a checkout of the commit a push replaced", _PR_YML, "test",
                      _STAGE_CHECKOUT_DEPTH,
                      _STAGE_CHECKOUT_DEPTH + "          ref: ${{ github.event.before }}\n",
                      expect="pr.yml job test: the input ref of 'a step' reads"),
        _plant_in_job("a checkout of main's ref", _PR_YML, "test", _STAGE_CHECKOUT_DEPTH,
                      _STAGE_CHECKOUT_DEPTH + "          ref: refs/heads/main\n",
                      expect="pr.yml job test: the input ref of 'a step' reads 'refs/heads/main'"),
        _plant_in_job("an action handed the pushed commits", _PR_YML, "results",
                      "          pattern: ci-record-*\n",
                      "          pattern: ci-record-*\n"
                      "          commits: ${{ toJSON(github.event.commits) }}\n",
                      expect="pr.yml job results: the input commits of \"every entry's record\""),
        _plant_in_job("a runner picked by the event", _PR_YML, "results",
                      "    runs-on: ubuntu-24.04\n",
                      "    runs-on: ${{ github.event_name == 'push' && 'ubuntu-24.04'"
                      " || 'none' }}\n",
                      expect="pr.yml job results: runs-on reads"),
        _plant_in_job("an input reading the pull request's head passes", _PR_YML, "test",
                      _STAGE_CHECKOUT_DEPTH,
                      _STAGE_CHECKOUT_DEPTH
                      + "          ref: ${{ github.event.pull_request.head.sha }}\n",
                      fails=False),
    ),
)  # fmt: skip


_TRUSTED_OR_CALLED = {"pull_request_target", "workflow_run", "workflow_call"}


#: merge.yml's queue trigger (0.4.0 A1), as the workflow spells it after its own merge.
_QUEUE_TRIGGER = "  merge_group:\n    types: [checks_requested]\n"


def _with_the_queue(text: str) -> str:
    """merge.yml's text with the queue commit's trigger, whether the tree already has it or not."""
    if _QUEUE_TRIGGER in text:
        return text
    assert text.count("  workflow_dispatch: {}\n") == 1, "merge.yml's dispatch is not once"
    return text.replace(
        "  workflow_dispatch: {}\n", "  workflow_dispatch: {}\n" + _QUEUE_TRIGGER, 1
    )


def _permission_writes(where: str, value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [] if value in ("{}",) else [f"{where} grants {value!r}"]
    if not isinstance(value, dict):
        return [f"{where} grants {value!r}, which the row cannot read"]
    return [
        f"{where} grants {scope}: {grant}"
        for scope, grant in value.items()
        if str(grant) not in ("read", "none")
    ]


#: Each tier workflow's triggers: a pull request's run and a dispatch, and merge.yml's push stage
#: and (0.4.0 A1) its queue commit's run, ``merge_group``, which main's merge queue needs.
_TIER_TRIGGERS: dict[str, tuple[frozenset[str], ...]] = {
    "pr.yml": (frozenset({"pull_request", "workflow_dispatch"}),),
    "merge.yml": (frozenset({"pull_request", "push", "workflow_dispatch", "merge_group"}),),
}
#: How a tier workflow names the queue's one event, its types spelt out: a queue commit whose
#: checks GitHub asks for. A bare ``merge_group:`` (or ``{}``) takes every type, so one GitHub
#: adds later would start a run unread.
_QUEUE_TRIGGERS: tuple[object, ...] = ({"types": ["checks_requested"]},)


def tier_trust_violations(root: Path) -> list[str]:
    """Neither tier workflow takes a trusted trigger, holds a write or reads a secret, and each
    runs on its own triggers alone: a pull request and a dispatch, and merge.yml a push to main
    too (#3915)."""
    bad: list[str] = []
    for name, workflow in _tier_workflows(root).items():
        if not workflow:
            bad.append(f"{_WORKFLOWS}/{name} is missing")
            continue
        triggers = set(_triggers(workflow))
        for trigger in sorted(triggers & _TRUSTED_OR_CALLED):
            bad.append(f"{name} runs on {trigger}, a trusted or called run of candidate code")
        want = _TIER_TRIGGERS[name]
        if triggers not in want:
            sets = " or ".join(str(sorted(w)) for w in want)
            bad.append(f"{name} runs on {sorted(triggers)}, not {sets}")
        if "merge_group" in triggers and _triggers(workflow)["merge_group"] not in _QUEUE_TRIGGERS:
            bad.append(f"{name} takes a merge_group event other than checks_requested")
        if "permissions" not in workflow:
            bad.append(f"{name} declares no permissions, so its token is the repository's default")
        bad += _permission_writes(name, workflow.get("permissions"))
        for job_id, job in _trust_jobs(workflow).items():
            bad += _permission_writes(f"{name} job {job_id}", job.get("permissions"))
            # A queue commit's run is the base repository's, as a branch's pull request's is: a
            # job with an environment would hold that environment's secrets wherever its branch
            # policy let the queue's branch in.
            if "environment" in job:
                bad.append(f"{name} job {job_id} deploys to an environment: the verdict needs none")
        # The secrets context in any spelling GitHub reads (`secrets.X`, `secrets['X']`,
        # `toJSON(secrets)`, any case) or a call's `secrets:`; comments are not read.
        if re.search(r"\bsecrets\b", json.dumps(workflow, default=str), flags=re.I):
            bad.append(f"{name} reads a secret: the verdict needs none")
    return bad


THE_TIER_WORKFLOWS_TAKE_NO_TRUST = Row(
    name="the tier workflows take no trusted trigger and hold no write",
    check=tier_trust_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant("pull_request_target", _MERGE_YML, "  workflow_dispatch: {}\n",
               "  workflow_dispatch: {}\n  pull_request_target:\n",
               expect="merge.yml runs on pull_request_target"),
        _plant("workflow_run", _PR_YML, "  workflow_dispatch: {}\n",
               '  workflow_dispatch: {}\n  workflow_run:\n    workflows: ["Merge"]\n',
               expect="pr.yml runs on workflow_run"),
        _plant("a push trigger on pr.yml", _PR_YML,
               "  workflow_dispatch: {}\n",
               "  workflow_dispatch: {}\n  push:\n    branches: [main]\n",
               expect="pr.yml runs on ['pull_request', 'push', 'workflow_dispatch'], not"),
        _plant("merge.yml without its pull request run", _MERGE_YML,
               "  pull_request: {}\n", "",
               expect="'push', 'workflow_dispatch'], not"),
        _plant("a schedule on merge.yml", _MERGE_YML, "  workflow_dispatch: {}\n",
               "  workflow_dispatch: {}\n  schedule:\n    - cron: '0 3 * * *'\n",
               expect="'pull_request', 'push', 'schedule', 'workflow_dispatch'], not"),
        Control("merge.yml with the queue commit's run, whichever the tree holds",
                _edited(_MERGE_YML, _with_the_queue), fails=False),
        _plant("a queue commit's run on pr.yml", _PR_YML, "  workflow_dispatch: {}\n",
               "  workflow_dispatch: {}\n  merge_group:\n    types: [checks_requested]\n",
               expect="pr.yml runs on ['merge_group', 'pull_request', 'workflow_dispatch'], not"),
        Control("merge.yml's queue run taking another type",
                _edited(_MERGE_YML, lambda t: _with_the_queue(t).replace(
                    "types: [checks_requested]", "types: [checks_requested, destroyed]", 1)),
                expect="merge.yml takes a merge_group event other than checks_requested"),
        _plant("merge.yml without its queue commit's run", _MERGE_YML, _QUEUE_TRIGGER, "",
               expect="merge.yml runs on ['pull_request', 'push', 'workflow_dispatch'], not"),
        _plant("merge.yml's queue run with every type", _MERGE_YML, _QUEUE_TRIGGER,
               "  merge_group:\n",
               expect="merge.yml takes a merge_group event other than checks_requested"),
        _plant("merge.yml's queue run with no types named", _MERGE_YML, _QUEUE_TRIGGER,
               "  merge_group: {}\n",
               expect="merge.yml takes a merge_group event other than checks_requested"),
        _plant_in_job("an environment on a tier job", _MERGE_YML, "results",
                      "    runs-on: ubuntu-24.04\n",
                      "    runs-on: ubuntu-24.04\n    environment: ci-controller\n",
                      expect="merge.yml job results deploys to an environment"),
        _plant("a write for the verdict", _MERGE_YML,
               "      checks: read     # a cancelled shard's annotations",
               "      checks: write    # a cancelled shard's annotations",
               expect="merge.yml job results grants checks: write"),
        _plant("write-all on the workflow", _PR_YML, "permissions:\n  contents: read\n",
               "permissions: write-all\n", expect="pr.yml grants 'write-all'"),
        _plant("no permissions declared", _PR_YML, "permissions:\n  contents: read\n", "",
               expect="pr.yml declares no permissions"),
        _plant("a secret in a step", _MERGE_YML, "          GH_TOKEN: ${{ github.token }}\n",
               "          GH_TOKEN: ${{ secrets.ADMIN_TOKEN }}\n",
               expect="merge.yml reads a secret"),
        *(
            _plant_in_job(f"a secret in a stage job's step as {spelling}", _MERGE_YML, "test-2",
                          "          SHARD: ${{ matrix.shard }}\n",
                          "          SHARD: ${{ matrix.shard }}\n"
                          f"          TOKEN: ${{{{ {spelling} }}}}\n",
                          expect="merge.yml reads a secret")
            for spelling in ("secrets['ADMIN_TOKEN']", "toJSON(secrets)", "SECRETS.ADMIN_TOKEN")
        ),
        _plant("a comment naming secrets passes", _PR_YML, "permissions:\n  contents: read\n",
               "permissions:\n  contents: read   # no secrets\n", fails=False),
        _plant("contents: none passes", _PR_YML, "permissions:\n  contents: read\n",
               "permissions:\n  contents: read\n  issues: none\n", fails=False),
    ),
)  # fmt: skip

#: merge.yml's env line its plan and verdict steps take their judge's ref from: on a push to
#: main, the commit the push replaced; on any other run nothing, so main's tip. pr.yml, which
#: has no push stage, leaves it empty, as it does PUSH_PLAN.
_JUDGE_REF = (
    "${{ github.event_name == 'push' && github.ref == 'refs/heads/main'"
    " && github.event.before || '' }}"
)
_JUDGE_LINE = f"          JUDGE: {_JUDGE_REF}\n"
_PR_JUDGE_LINE = '          JUDGE: ""\n'
#: Each tier's JUDGE: merge.yml's push stage sets it, pr.yml's is empty.
_JUDGE_OF = {"merge.yml": _JUDGE_REF, "pr.yml": ""}
_JUDGE_AT_THE_PUSH = "          JUDGE: ${{ github.event_name == 'push' && github.sha || '' }}\n"
#: What each step reads at JUDGE: the planner's archive, by the branch's full name off a push,
#: and main's verdict.py.
_JUDGED = {
    "plan": 'git fetch --no-tags --depth=1 origin "${JUDGE:-refs/heads/main}"',
    "verdict": "contents/tools/ci/verdict.py?ref=${JUDGE:-main}",
}


def judge_ref_violations(root: Path) -> list[str]:
    """A commit never judges its own push. Each tier workflow's plan step archives the planner,
    and its verdict step reads verdict.py, at JUDGE: in merge.yml, on a push to main the commit
    the push replaced (main's tip is the pushed commit) and on any other run main itself; in
    pr.yml always main, never ``github.event.before`` (a pull request's is its previous head)."""
    bad: list[str] = []
    for name, workflow in _tier_workflows(root).items():
        if not workflow:
            bad.append(f"{_WORKFLOWS}/{name} is missing")
            continue
        jobs = _trust_jobs(workflow)
        steps = {
            "plan": next(
                (s for s in _trust_steps(jobs.get(_PLAN_JOB) or {}) if s.get("id") == "plan"), None
            ),
            "verdict": next(
                (
                    s
                    for s in _trust_steps(jobs.get(_RESULTS_JOB) or {})
                    if "--records" in str(s.get("run", ""))
                ),
                None,
            ),
        }
        for what, step in steps.items():
            where = f"{name}'s {what} step"
            if step is None:
                bad.append(f"{where} is missing")
                continue
            judge = (step.get("env") or {}).get("JUDGE")
            if judge != _JUDGE_OF.get(name):
                bad.append(
                    f"{where} takes its judge's ref from {judge!r}, not the commit a push to main"
                    " replaced and main on any other run"
                )
            if _JUDGED[what] not in " ".join(str(step.get("run", "")).split()):
                bad.append(f"{where} does not read its judge at JUDGE ({_JUDGED[what]})")
    return bad


A_COMMIT_NEVER_JUDGES_ITS_OWN_PUSH = Row(
    name="a-commit-never-judges-its-own-push",
    check=judge_ref_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant_in_job("the verdict read at the pushed commit", _MERGE_YML, "results",
                      _JUDGE_LINE, _JUDGE_AT_THE_PUSH,
                      expect="merge.yml's verdict step takes its judge's ref from"),
        _plant_in_job("the planner read at the pushed commit", _MERGE_YML, "changes",
                      _JUDGE_LINE, _JUDGE_AT_THE_PUSH,
                      expect="merge.yml's plan step takes its judge's ref from"),
        _plant_in_job("the verdict read at main's tip on a push", _MERGE_YML, "results",
                      _JUDGE_LINE, "          JUDGE: ${{ '' }}\n",
                      expect="merge.yml's verdict step takes its judge's ref from"),
        _plant_in_job("the planner read at main's tip on a push", _MERGE_YML, "changes",
                      _JUDGE_LINE, "          JUDGE: ${{ '' }}\n",
                      expect="merge.yml's plan step takes its judge's ref from"),
        _plant_in_job("the verdict read at before on a pull request", _PR_YML, "results",
                      _PR_JUDGE_LINE, "          JUDGE: ${{ github.event.before }}\n",
                      expect="pr.yml's verdict step takes its judge's ref from"),
        _plant_in_job("the planner read at before on a pull request", _PR_YML, "changes",
                      _PR_JUDGE_LINE, "          JUDGE: ${{ github.event.before }}\n",
                      expect="pr.yml's plan step takes its judge's ref from"),
        _plant("the verdict's copy read at main whatever JUDGE says", _MERGE_YML,
               "verdict.py?ref=${JUDGE:-main}", "verdict.py?ref=main",
               expect="merge.yml's verdict step does not read its judge at JUDGE"),
        _plant("the planner fetched at main whatever JUDGE says", _MERGE_YML,
               'origin "${JUDGE:-refs/heads/main}"', "origin refs/heads/main",
               expect="merge.yml's plan step does not read its judge at JUDGE"),
        _plant("the planner fetched off a push by a name a tag could take", _PR_YML,
               'origin "${JUDGE:-refs/heads/main}"', 'origin "${JUDGE:-main}"',
               expect="pr.yml's plan step does not read its judge at JUDGE"),
    ),
)  # fmt: skip

#: The most jobs one merge-tier run holds at once (#3915), so concurrent pull requests share the
#: account's runners rather than one taking them.
_MERGE_AT_ONCE = 14


def _cap(job: dict[str, Any]) -> int | None:
    cap = (job.get("strategy") or {}).get("max-parallel")
    return cap if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else None


def merge_at_once_violations(root: Path) -> list[str]:
    """merge.yml holds at most :data:`_MERGE_AT_ONCE` jobs at once. Each stage job caps its
    entries with an integer ``max-parallel``; jobs no ``needs`` orders may run together, a stage
    job counting its cap and any other job one; and the most that may run together is at most
    the bound. The plan lists every suite shard (an entry that reads the run's shard plan) within
    its stage job's cap, so the cap never holds back the run's longest entries."""
    workflow = _tier_workflows(root)["merge.yml"]
    if not workflow:
        return [f"{_MERGE_YML} is missing"]
    jobs = _trust_jobs(workflow)
    bad: list[str] = []
    weight: dict[str, int] = {}
    for job_id, job in jobs.items():
        if not _is_stage_job(job_id, job):
            weight[job_id] = 1
            continue
        cap = _cap(job)
        if cap is None:
            bad.append(
                f"merge.yml job {job_id} sets no max-parallel"
                f" ({(job.get('strategy') or {}).get('max-parallel')!r}), so its whole matrix"
                " starts at once"
            )
        else:
            weight[job_id] = cap
    if bad:
        return bad
    needs = {
        job_id: set(
            [job.get("needs")] if isinstance(job.get("needs"), str) else job.get("needs") or []
        )
        for job_id, job in jobs.items()
    }
    after: dict[str, set[str]] = {}

    def upstream(job_id: str) -> set[str]:
        if job_id not in after:
            after[job_id] = set()
            for need in needs.get(job_id, ()):
                after[job_id] |= {need, *upstream(need)}
        return after[job_id]

    ids = sorted(jobs)
    most, together = 0, ()
    for mask in range(1, 1 << len(ids)):
        chosen = [ids[n] for n in range(len(ids)) if mask >> n & 1]
        if any(a in upstream(b) or b in upstream(a) for a in chosen for b in chosen if a < b):
            continue
        held = sum(weight[j] for j in chosen)
        if held > most:
            most, together = held, tuple(chosen)
    if most > _MERGE_AT_ONCE:
        bad.append(
            f"merge.yml holds {most} jobs at once"
            f" ({' + '.join(f'{j} {weight[j]}' for j in together)}), more than {_MERGE_AT_ONCE}"
        )
    for stage_job, entries in ci_plan.stage_jobs(_tier_plan(root, "merge")).items():
        cap = weight.get(stage_job)
        for n, entry in enumerate(entries):
            if cap is not None and _SHARD_HISTORY in entry["external"] and n >= cap:
                bad.append(
                    f"merge.yml job {stage_job} starts {cap} entries at a time, and the plan lists"
                    f" {entry['key']} as entry {n + 1}, so it waits for one to finish"
                )
    return bad


_TEST_CAP = "      max-parallel: 10\n"

A_MERGE_TIER_RUN_HOLDS_AT_MOST_14_JOBS = Row(
    name="a-merge-tier-run-holds-at-most-14-jobs-at-once",
    check=merge_at_once_violations,
    scaffold=_tier_scaffold,
    controls=(
        _plant_in_job("a stage job with no cap", _MERGE_YML, "test", _TEST_CAP, "",
                      expect="merge.yml job test sets no max-parallel (None)"),
        _plant_in_job("a cap written as an expression", _MERGE_YML, "test", _TEST_CAP,
                      "      max-parallel: ${{ 10 }}\n",
                      expect="merge.yml job test sets no max-parallel"),
        _plant_in_job("the test stage at 11 beside build's 4", _MERGE_YML, "test", _TEST_CAP,
                      "      max-parallel: 11\n", expect="merge.yml holds 15 jobs at once"),
        _plant_in_job("test-2 at 5 beside the test stage's 10", _MERGE_YML, "test-2",
                      "      max-parallel: 4\n", "      max-parallel: 5\n",
                      expect="merge.yml holds 15 jobs at once (test 10 + test-2 5)"),
        _plant_in_job("test-3 not waiting for test-2, so the two run together", _MERGE_YML,
                      "test-3", "    needs: [changes, build, test-2]\n",
                      "    needs: [changes, build]\n",
                      expect="merge.yml holds 18 jobs at once"),
        # 5 holds a macOS shard back in either order of the plan: the suite first (the last macOS
        # shard is entry 6) or the static jobs first (entry 10).
        _plant_in_job("the test stage at 5, so a macOS shard waits for a free one", _MERGE_YML,
                      "test", _TEST_CAP, "      max-parallel: 5\n",
                      expect="lists python-suite-macos ("),
        _plant_in_job("build at 1 passes: fewer at once is no fault", _MERGE_YML, "build",
                      "      max-parallel: 4\n", "      max-parallel: 1\n", fails=False),
    ),
)  # fmt: skip

ROWS = (
    *ROWS,
    THE_TIER_WORKFLOWS_RUN_ONLY_THEIR_CLOSED_LISTS,
    THE_TIER_WORKFLOWS_RUN_THEIR_TIERS_MATRIX,
    THE_CATALOGUE_CHECK_STANDS_IN_FRONT_OF_MERGE_YMLS_VERDICT,
    PR_YML_CARRIES_NO_PUSH_STAGE,
    MERGE_YML_PUSH_STAGE_RUNS_THE_SECURITY_CORPUS_AND_APPS_CONTRACT,
    THE_TIER_WORKFLOWS_TAKE_NO_TRUST,
    A_COMMIT_NEVER_JUDGES_ITS_OWN_PUSH,
    A_MERGE_TIER_RUN_HOLDS_AT_MOST_14_JOBS,
)


# ── A pull request's run never replays (#3919) ───────────────────────────────────────────────

#: How a step runs the push's replay: the command, or its module by name, anywhere in the step.
_REPLAY_CALL = re.compile(r"\btools\.ci\s+replay\b|\bmain_replay\b")
#: The events a pull request's own run is.
_PULL_REQUEST_EVENTS = ("pull_request", "pull_request_target")
#: The status functions an `if:` may call, each of which may be true or false on any run.
_STATUS_FUNCTIONS = ("always()", "success()", "failure()", "cancelled()")


def _could_run(condition: object, event: str) -> bool:
    """Whether an `if:` may be true on a run of *event*, whatever the status functions say.
    Text outside one `${{ }}` makes the condition a string, which is always true. A condition
    this row cannot evaluate raises ValueError, never reads as false."""
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    text = str(condition).strip()
    whole = _EXPRESSION.fullmatch(text)
    if whole is not None:
        text = whole.group(1)
    elif "${{" in text:
        return True
    context = {"github": {"event_name": event, "ref": "refs/pull/42/merge", "event": {}}}
    for values in itertools.product(("true", "false"), repeat=len(_STATUS_FUNCTIONS)):
        filled = text
        for function, value in zip(_STATUS_FUNCTIONS, values):
            filled = filled.replace(function, value)
        if _expression(context, filled):
            return True
    return False


def replay_violations(tree: Tree) -> list[str]:
    """A pull request's run never replays: every step that runs the push's replay
    (``python -m tools.ci replay``, ``tools/ci/main_replay.py``), a local action's steps
    included, is guarded by its own `if:` or its job's so that it cannot run on a
    ``pull_request`` or ``pull_request_target`` event, whatever the workflow's triggers are now.
    The replay refuses such an event itself (``main_replay.decide``); this holds the call."""
    from tools.ci import __main__ as commands

    bad: list[str] = []
    if "replay" not in commands.COMMANDS:
        bad.append("tools.ci has no replay command, so this row's detector names nothing")
    for name, job_id, job, step in _steps(tree):
        if not _REPLAY_CALL.search(str(step)):
            continue
        for event in _PULL_REQUEST_EVENTS:
            try:
                runs = _could_run(job.get("if"), event) and _could_run(step.get("if"), event)
            except ValueError as exc:
                bad.append(
                    f"{name}:{job_id} runs the push's replay behind an if: this row cannot read"
                    f" ({exc})"
                )
                break
            if runs:
                bad.append(
                    f"{name}:{job_id} runs the push's replay on a {event} run: a pull request's"
                    " run never replays (#3919)"
                )
                break
    return bad


def _replay_job(job_if: str = "", step_if: str = "", run: str = "") -> str:
    """A job that runs the push's replay, guarded as given."""
    job_guard = f"    if: {job_if}\n" if job_if else ""
    step_guard = f"        if: {step_if}\n" if step_if else ""
    command = run or "python3 -m tools.ci replay --plan plan.json"
    return (
        f"  replay:\n{job_guard}    runs-on: ubuntu-24.04\n    steps:\n"
        f"      - name: the push's replay\n{step_guard}        run: {command}\n"
    )


def _adds(rel: str, name: str, job: str, *, fails: bool = True, expect: str = "") -> Control:
    return _plant(name, rel, "\njobs:\n", "\njobs:\n" + job, fails=fails, expect=expect)


def _replay_in_a_local_action(root: Path) -> None:
    _write(
        root,
        ".github/actions/replay/action.yml",
        "name: replay\ndescription: the push's replay\nruns:\n  using: composite\n  steps:\n"
        "    - shell: bash\n      run: python3 -m tools.ci replay --plan plan.json\n",
    )
    rel = ".github/workflows/merge.yml"
    text = (root / rel).read_text(encoding="utf-8")
    job = (
        "  replay:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: ./.github/actions/replay\n"
    )
    _write(root, rel, text.replace("\njobs:\n", "\njobs:\n" + job, 1))


_PUSH = "github.event_name == 'push'"
A_PULL_REQUEST_RUN_NEVER_REPLAYS = _toolchain_row(
    "a pull request's run never replays",
    replay_violations,
    (
        _adds(_MERGE_YML, "an unguarded replay in merge.yml", _replay_job(),
              expect="merge.yml:replay runs the push's replay on a pull_request run"),
        _adds(_PR_YML, "an unguarded replay in pr.yml", _replay_job(),
              expect="pr.yml:replay runs the push's replay on a pull_request run"),
        _adds(_MERGE_YML, "a replay behind always()", _replay_job(job_if="always()"),
              expect="on a pull_request run"),
        _adds(_MERGE_YML, "a replay behind !cancelled()",
              _replay_job(step_if="${{ !cancelled() }}"), expect="on a pull_request run"),
        _adds(_MERGE_YML, "a guard that lets pull_request_target through",
              _replay_job(step_if="github.event_name != 'pull_request'"),
              expect="on a pull_request_target run"),
        _adds(_MERGE_YML, "a guard with text outside its expression",
              _replay_job(step_if=f"${{{{ {_PUSH} }}}} and nothing else"),
              expect="on a pull_request run"),
        _adds(_MERGE_YML, "a guard the row cannot read",
              _replay_job(step_if="contains(github.event_name, 'push')"),
              expect="behind an if: this row cannot read"),
        _adds(_MERGE_YML, "the module run by its file",
              _replay_job(run='python3 "$RUNNER_TEMP/main_replay.py" --plan plan.json'),
              expect="merge.yml:replay runs the push's replay"),
        Control("a replay in a local action", _replay_in_a_local_action,
                expect="merge.yml:replay runs the push's replay on a pull_request run"),
        _adds(_MERGE_YML, "a replay guarded on its step to a push passes",
              _replay_job(step_if=_PUSH), fails=False),
        _adds(_MERGE_YML, "a replay guarded on its job to a push to main passes",
              _replay_job(job_if=f"${{{{ {_PUSH} && github.ref == 'refs/heads/main' }}}}"),
              fails=False),
        _adds(_MERGE_YML, "a status function beside the push guard passes",
              _replay_job(step_if=f"${{{{ !cancelled() && {_PUSH} }}}}"), fails=False),
    ),
)  # fmt: skip

ROWS = (*ROWS, A_PULL_REQUEST_RUN_NEVER_REPLAYS)


# ── Every pytest step writes JUnit, and every install off the lock is named (#3909) ───────────
# A catalogue entry's pytest step writes a JUnit report (`--junitxml`), unless it only collects
# (`--collect-only`), so what a job's tests did is read from a report and never from its exit
# alone. A step that installs or fetches packages the lock does not hold is named in its entry's
# `install_exemptions`, word for word, with the reason it may: `uv pip install`, `uv tool
# install`, `uv add`, `pip install` however its interpreter is spelled (also behind `uv run` and
# its options, read with their values), `uv run --with`/`--with-editable`/`--with-requirements`,
# `uvx` and `uv tool run`, and `npm install`. An `npx` step runs a bin that the nearest
# `package-lock.json` above its workdir installs (a `bin` of one of its `packages`), so it fetches
# nothing. An exemption names an install its entry's steps run, so the list cannot outlive what
# it excused. The locked installs are the toolchain's (`uv-sync`, `npm-ci`), which the loader
# refuses as steps.

_ENV_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_PYTHON_WORD = re.compile(r"(?:\S*/)?python(?:\d+(?:\.\d+)*)?")
_PIP_WORD = re.compile(r"(?:\S*/)?pip(?:\d+(?:\.\d+)*)?")


#: `uv run`'s options that take a value, so the word after one is its value, not the command.
_UV_RUN_VALUED = frozenset(
    {
        "--directory",
        "--project",
        "--python",
        "-p",
        "--extra",
        "--group",
        "--only-group",
        "--no-group",
        "--package",
        "--env-file",
        "--index",
        "--default-index",
        "--index-url",
        "-i",
        "--extra-index-url",
        "--find-links",
        "-f",
        "--config-file",
        "--cache-dir",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--color",
        "--python-preference",
        "--resolution",
        "--prerelease",
        "--exclude-newer",
        "--link-mode",
        "--refresh-package",
        "--upgrade-package",
        "-P",
        "--reinstall-package",
    }
)
#: `uv run`'s options that add packages to the run beside the lock's.
_UV_RUN_ADDS = ("--with", "--with-editable", "--with-requirements")


def _fetches_off_the_lock(words: list[str]) -> bool:
    """Whether *words*, from their first, install packages other than from a lock."""
    if words[:3] in (["uv", "pip", "install"], ["uv", "tool", "install"], ["uv", "tool", "run"]):
        return True
    if words[:2] == ["uv", "add"] or words[:1] == ["uvx"]:
        return True
    if words and _PYTHON_WORD.fullmatch(words[0]) and words[1:4] == ["-m", "pip", "install"]:
        return True
    if words and _PIP_WORD.fullmatch(words[0]) and words[1:2] == ["install"]:
        return True
    return words[:2] in (["npm", "install"], ["npm", "i"])


def _off_the_lock(argv: tuple[str, ...]) -> bool:
    """Whether a step's words install or fetch packages the lock does not hold."""
    words = list(argv)
    while words and _ENV_WORD.fullmatch(words[0]):
        words = words[1:]
    if words[:2] != ["uv", "run"]:
        return _fetches_off_the_lock(words)
    rest = words[2:]
    while rest and rest[0].startswith("-"):
        flag, valued = rest[0].split("=", 1)[0], "=" not in rest[0]
        if flag in _UV_RUN_ADDS:
            return True
        rest = rest[2:] if valued and flag in _UV_RUN_VALUED else rest[1:]
    # An install anywhere after `uv run` is one, however its options were spelled.
    return any(_fetches_off_the_lock(rest[i:]) for i in range(len(rest)))


def _lock_bins(root: Path, workdir: str) -> set[str] | None:
    """The bins the nearest ``package-lock.json`` above ``root/workdir`` installs; None when
    there is none or it cannot be read."""
    where = (root / workdir).resolve() if workdir else root.resolve()
    for folder in (where, *where.parents):
        lock = folder / "package-lock.json"
        if lock.is_file():
            try:
                packages = json.loads(lock.read_text(encoding="utf-8")).get("packages") or {}
            except (OSError, ValueError, AttributeError):
                return None
            bins: set[str] = set()
            for key, entry in packages.items():
                found = entry.get("bin") if isinstance(entry, dict) else None
                if isinstance(found, dict):
                    bins |= {str(name) for name in found}
                elif isinstance(found, str):
                    bins.add(key.rsplit("/", 1)[-1])
            return bins
        if folder == root.resolve():
            break
    return None


def _npx_problem(root: Path, step: catalogue.Step) -> str | None:
    """Why an ``npx`` step may fetch a package the lock does not hold; None when it runs a bin
    the lock installs, or is no ``npx`` step."""
    words = list(step.argv)
    while words and _ENV_WORD.fullmatch(words[0]):
        words = words[1:]
    if words[:1] != ["npx"]:
        return None
    rest = words[1:]
    while rest and rest[0].startswith("-"):
        if rest[0].split("=", 1)[0] in ("--package", "-p"):
            return "names a package to fetch (--package)"
        rest = rest[1:]
    if not rest:
        return "runs no bin"
    name = rest[0]
    bins = _lock_bins(root, step.workdir)
    if bins is None:
        return f"runs `{name}` with no package-lock.json above {step.workdir or 'the root'}"
    if name not in bins:
        return f"runs `{name}`, which no package of its lock installs as a bin"
    return None


def _junit_and_lock_check(root: Path) -> list[str]:
    try:
        cat = catalogue.read(root)
    except catalogue.CatalogueError as exc:
        return [f"the catalogue cannot be read: {exc}"]
    bad: list[str] = []
    for job in cat.jobs:
        for step in job.command:
            args = catalogue.pytest_args(step.argv)
            if (
                args is not None
                and not {"--collect-only", "--co"} & set(args)
                and not any(w.startswith(("--junitxml", "--junit-xml")) for w in args)
            ):
                bad.append(f"{job.id}: `{shlex.join(step.argv)}` runs pytest and writes no JUnit")
        for step in job.command:
            why = _npx_problem(root, step)
            if why:
                bad.append(f"{job.id}: `{shlex.join(step.argv)}` {why}, so it may fetch it")
        installs = [" ".join(step.argv) for step in job.command if _off_the_lock(step.argv)]
        named = {exemption.install for exemption in job.install_exemptions}
        for install in installs:
            if install not in named:
                bad.append(
                    f"{job.id}: `{install}` installs off the lock, and no install_exemptions "
                    "entry names it with its reason"
                )
        for install in sorted(named - set(installs)):
            bad.append(f"{job.id}: the exemption for `{install}` names no install its steps run")
    return bad


def _catalogue_only_scaffold(root: Path) -> None:
    """The tree's catalogue, and the lock its ``npx`` steps resolve their bins from."""
    for rel in (catalogue.CATALOGUE, "package-lock.json"):
        _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))


_CLIENT_TEST = '"packages/personalclaw-client-py/tests", "--junitxml=reports/junit-client.xml"]'
_CLIENT_EXEMPTION = (
    '  { install = "uv pip install --python .venv-client packages/personalclaw-client-py[dev]", '
    'reason = "the client is not part of core\'s uv workspace and has no lock of its own" },\n'
)
_CLIENT_LAST_STEP = '"--junitxml=reports/junit-client.xml"] },\n]\n'


def _client_step(argv: str, workdir: str = "") -> tuple[str, str]:
    """The client entry's command with one more step, *argv* given as TOML list items."""
    where = f', workdir = "{workdir}"' if workdir else ""
    return _CLIENT_LAST_STEP, _CLIENT_LAST_STEP[:-2] + f"  {{ argv = [{argv}]{where} }},\n]\n"


EVERY_PYTEST_STEP_WRITES_JUNIT_AND_EVERY_UNLOCKED_INSTALL_IS_NAMED = Row(
    name="every-pytest-step-writes-junit-and-every-install-off-the-lock-is-named",
    check=_junit_and_lock_check,
    scaffold=_catalogue_only_scaffold,
    controls=(
        _plant(
            "a pytest step that writes no JUnit",
            catalogue.CATALOGUE,
            _CLIENT_TEST,
            '"packages/personalclaw-client-py/tests"]',
            expect="runs pytest and writes no JUnit",
        ),
        _plant(
            "a pytest step that only collects needs no JUnit",
            catalogue.CATALOGUE,
            *_client_step('"uv", "run", "pytest", "--collect-only", "-q", "tests"'),
            fails=False,
        ),
        _plant(
            "an unlocked install with its exemption taken away",
            catalogue.CATALOGUE,
            _CLIENT_EXEMPTION,
            "",
            expect="installs off the lock",
        ),
        _plant(
            "an exemption that names no step",
            catalogue.CATALOGUE,
            _CLIENT_EXEMPTION,
            _CLIENT_EXEMPTION + '  { install = "uv pip install gone", reason = "it was once" },\n',
            expect="names no install its steps run",
        ),
        *(
            _plant(
                f"`{argv}` as a step with no exemption",
                catalogue.CATALOGUE,
                *_client_step(argv),
                expect="installs off the lock",
            )
            for argv in (
                '"npm", "install"',
                '"python3", "-m", "pip", "install", "requests"',
                '".venv/bin/pip", "install", "requests"',
                '"FOO=1", "uv", "tool", "install", "ruff"',
                '"uv", "run", "pip", "install", "requests"',
                '"uv", "run", "--directory", "web", "pip", "install", "x"',
                '"uv", "run", "--project=web", "python", "-m", "pip", "install", "x"',
                '"uv", "run", "--with", "requests", "python", "x.py"',
                '"uv", "run", "--with-requirements", "r.txt", "python", "x.py"',
                '"uvx", "ruff", "check"',
                '"uv", "tool", "run", "ruff", "check"',
            )
        ),
        _plant(
            "`npx playwright` in web runs a bin the lock installs",
            catalogue.CATALOGUE,
            *_client_step('"npx", "playwright", "--version"', workdir="web"),
            fails=False,
        ),
        _plant(
            "`npx` of a package the lock does not hold",
            catalogue.CATALOGUE,
            *_client_step('"npx", "cowsay", "moo"', workdir="web"),
            expect="which no package of its lock installs as a bin",
        ),
        _plant(
            "`npx --package` names a package to fetch",
            catalogue.CATALOGUE,
            *_client_step('"npx", "--package", "cowsay", "cowsay", "moo"', workdir="web"),
            expect="names a package to fetch",
        ),
        _plant(
            "a step that builds, not installs, is no install",
            catalogue.CATALOGUE,
            *_client_step('"uv", "run", "python", "-m", "pip", "--version"'),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, EVERY_PYTEST_STEP_WRITES_JUNIT_AND_EVERY_UNLOCKED_INSTALL_IS_NAMED)


# ── The pre-push hook runs the change tier, and runs nothing of its own (#3920) ──────────────

_PREPUSH = "scripts/run_prepush.sh"

#: The programs a check command starts: a hook line naming one runs a check of its own.
_CHECK_PROGRAMS = frozenset(
    {
        "black",
        "isort",
        "flake8",
        "mypy",
        "ruff",
        "pylint",
        "pytest",
        "vitest",
        "jest",
        "playwright",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "node",
        "tsc",
        "eslint",
        "prettier",
        "shellcheck",
        "dash",
        "uv",
        "uvx",
        "make",
    }
)

#: How the hook runs the change tier, which is all it may run.
_HOOK_RUNS_THE_TIER = re.compile(r'"\$python" -m tools\.ci run change --pre-push\b')


def _hook_command_lines(text: str) -> list[str]:
    """The hook's lines that may start a program: no comment, no ``echo`` or ``printf`` (their
    words are prose), and no comment trailing a command."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"(echo|printf)\b", line):
            continue
        lines.append(re.sub(r"\s+#\s.*$", "", line))
    return lines


def _hook_check(root: Path) -> list[str]:
    text = (root / _PREPUSH).read_text(encoding="utf-8")
    bad: list[str] = []
    if not _HOOK_RUNS_THE_TIER.search(text):
        bad.append(f"{_PREPUSH} does not run `python -m tools.ci run change --pre-push`")
    for line in _hook_command_lines(text):
        words = set(re.findall(r"[\w./-]+", line))
        named = {w.rsplit("/", 1)[-1] for w in words} & _CHECK_PROGRAMS
        named |= {w for w in words if w.endswith(".py") or w.startswith("scripts/")}
        if named:
            bad.append(
                f"{_PREPUSH} runs a check command of its own: `{line}` ({', '.join(sorted(named))})"
            )
    return bad


def _hook_scaffold(root: Path) -> None:
    _write(root, _PREPUSH, (REPO / _PREPUSH).read_text(encoding="utf-8"))


_HOOK_EXIT = 'echo "pre-push: the run took $(($(date +%s) - started))s."\n'

THE_PRE_PUSH_HOOK_RUNS_NO_CHECK_COMMAND = Row(
    name="the-pre-push-hook-runs-the-change-tier-and-no-check-command-of-its-own",
    check=_hook_check,
    scaffold=_hook_scaffold,
    controls=(
        *(
            _plant(
                f"a `{line}` line in the hook",
                _PREPUSH,
                _HOOK_EXIT,
                _HOOK_EXIT + line + "\n",
                expect="runs a check command of its own",
            )
            for line in (
                ".venv/bin/black --check src/personalclaw tests",
                "npm ci",
                '"$python" scripts/check_asset_licenses.py',
                "uv run pytest tests/test_x.py || status=1",
            )
        ),
        _plant(
            "a hook that no longer runs the tier",
            _PREPUSH,
            '"$python" -m tools.ci run change --pre-push',
            '"$python" -m tools.ci run-job static',
            expect="does not run `python -m tools.ci run change --pre-push`",
        ),
        _plant(
            "a check named only in the hook's prose",
            _PREPUSH,
            _HOOK_EXIT,
            _HOOK_EXIT + 'echo "run black and mypy before you push"  # prose\n',
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, THE_PRE_PUSH_HOOK_RUNS_NO_CHECK_COMMAND)

#: The pytest options that take the next word as their value, so that word is no path.
_PYTEST_VALUE_OPTIONS = frozenset(
    {
        "-n",
        "--numprocesses",
        "--dist",
        "-k",
        "-m",
        "-p",
        "-c",
        "-o",
        "-W",
        "--shard-list",
        "--maxfail",
        "--tb",
        "--rootdir",
        "--basetemp",
        "--junitxml",
        "--junit-xml",
        "--durations",
        "--timeout",
        "--deselect",
        "--ignore",
        "--confcutdir",
    }
)


def _runs_a_test_suite(argv: Sequence[str]) -> str:
    """Why a step runs a test suite, too slow for a push; empty for a step that does not. pytest
    over named files does not; over a folder, or over no path at all, it does."""
    args = catalogue.pytest_args(argv)
    if args is not None:
        if catalogue.collect_only(argv):
            return ""
        paths: list[str] = []
        skip = False
        for word in args:
            if skip:
                skip = False
            elif word.startswith("-"):
                skip = word in _PYTEST_VALUE_OPTIONS
            else:
                paths.append(word)
        if not paths:
            return "pytest over no path, so the whole suite"
        folders = [w for w in paths if not w.split("::", 1)[0].endswith(".py")]
        return f"pytest over {', '.join(folders)}" if folders else ""
    words = list(argv)
    names = [w.rsplit("/", 1)[-1] for w in words]
    if "vitest" in names:
        return "vitest"
    if "playwright" in names and "test" in words[names.index("playwright") + 1 :]:
        return "playwright test"
    if names and names[0] in ("npm", "pnpm", "yarn"):
        rest = words[1:]
        script = rest[1] if rest[:1] == ["run"] and len(rest) > 1 else rest[0] if rest else ""
        if script.startswith("test"):
            return f"`{shlex.join(words)}`"
    if names and names[0] == "make" and any(w.startswith("test") for w in words[1:]):
        return f"`{shlex.join(words)}`"
    return ""


def _prepush_check(root: Path) -> list[str]:
    try:
        cat = catalogue.read(root)
    except catalogue.CatalogueError as exc:
        return [f"the catalogue cannot be read: {exc}"]
    bad = []
    for job in cat.tier("change"):
        if not job.prepush:
            continue
        for step in job.command:
            why = _runs_a_test_suite(step.argv)
            if why:
                bad.append(
                    f"{job.id}: `{shlex.join(step.argv)}` runs {why} in the change tier, which "
                    "the pre-push hook runs; its entry must say prepush = false and leave it to CI"
                )
    return bad


_STATIC_LAST_STEP = '  { argv = ["uv", "run", "python", "-m", "harness", "scan"] },\n]\n'


def _static_step(argv: str) -> tuple[str, str]:
    """static's command with one more step, *argv* given as TOML list items."""
    return _STATIC_LAST_STEP, _STATIC_LAST_STEP[:-2] + f"  {{ argv = [{argv}] }},\n]\n"


A_CHANGE_TIER_TEST_SUITE_IS_LEFT_TO_CI = Row(
    name="a-change-tier-job-that-runs-a-test-suite-is-left-to-ci-not-the-pre-push-hook",
    check=_prepush_check,
    scaffold=_catalogue_only_scaffold,
    controls=(
        *(
            _plant(
                f"`{argv}` in a job the hook runs",
                catalogue.CATALOGUE,
                *_static_step(argv),
                expect="its entry must say prepush = false",
            )
            for argv in (
                '"uv", "run", "pytest", "-q", "tests", "--junitxml=reports/x.xml"',
                '"uv", "run", "pytest", "--junitxml=reports/x.xml"',
                '"uv", "run", "pytest", "-k", "tests", "--junitxml=r.xml"',
                '"npm", "run", "test:web"',
                '"npm", "test"',
                '"make", "test"',
                '"make", "test-visual"',
                '"npx", "vitest", "run"',
                '"npx", "playwright", "test", "--project=smoke"',
            )
        ),
        _plant(
            "the rails job's prepush = false taken away",
            catalogue.CATALOGUE,
            "prepush = false  # all of tests/rails",
            "# all of tests/rails",
            expect="rails: ",
        ),
        _plant(
            "pytest over named files is no suite",
            catalogue.CATALOGUE,
            *_static_step('"uv", "run", "pytest", "tests/test_x.py::test_it", "--junitxml=r.xml"'),
            fails=False,
        ),
        _plant(
            "pytest that only collects is no suite",
            catalogue.CATALOGUE,
            *_static_step('"uv", "run", "pytest", "--collect-only", "-q", "tests"'),
            fails=False,
        ),
    ),
)

ROWS = (*ROWS, A_CHANGE_TIER_TEST_SUITE_IS_LEFT_TO_CI)

#: The workflows a release runs; one that runs the render smoke is a copy of it.
_RELEASE_WORKFLOWS = ("release.yml", "release-artifacts.yml", "release-tag.yml")
_SMOKE_PROJECT = re.compile(r"\bplaywright\s+test\b[^\n]*--project[= ]smoke\b")


def _render_smoke_check(root: Path) -> list[str]:
    try:
        cat = catalogue.read(root)
    except catalogue.CatalogueError as exc:
        return [f"the catalogue cannot be read: {exc}"]
    merge = [
        j.id
        for j in cat.tier("merge")
        if any(_SMOKE_PROJECT.search(" ".join(s.argv)) for s in j.command)
    ]
    release = [
        name
        for name in _RELEASE_WORKFLOWS
        if _SMOKE_PROJECT.search(
            (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
        )
    ]
    if merge or release:
        return []
    return [
        "no merge-tier job and no release workflow runs the render smoke (Playwright's `smoke` "
        "project), and the pre-push hook no longer runs it, so no copy is left"
    ]


def _render_smoke_scaffold(root: Path) -> None:
    _catalogue_only_scaffold(root)
    for name in _RELEASE_WORKFLOWS:
        rel = f".github/workflows/{name}"
        _write(root, rel, (REPO / rel).read_text(encoding="utf-8"))


_E2E_SMOKE = '"npx", "playwright", "test", "--project=smoke", "--project=chromium"'
_E2E_NO_SMOKE = '"npx", "playwright", "test", "--project=chromium"'


def _release_runs_the_smoke(root: Path) -> None:
    rel = ".github/workflows/release-artifacts.yml"
    text = (root / rel).read_text(encoding="utf-8")
    _write(root, rel, text + "        run: npx playwright test --project=smoke\n")


THE_RENDER_SMOKE_HAS_A_COPY_IN_CI = Row(
    name="the-render-smoke-runs-in-the-merge-tier-or-the-release",
    check=_render_smoke_check,
    scaffold=_render_smoke_scaffold,
    controls=(
        _plant(
            "the merge tier's smoke project taken away, with none in the release",
            catalogue.CATALOGUE,
            _E2E_SMOKE,
            _E2E_NO_SMOKE,
            expect="no copy is left",
        ),
        Control(
            "the merge tier's taken away, with a release workflow's kept",
            lambda root: (
                _plant("", catalogue.CATALOGUE, _E2E_SMOKE, _E2E_NO_SMOKE).plant(root),
                _release_runs_the_smoke(root),
            ),
            False,
        ),
    ),
)

ROWS = (*ROWS, THE_RENDER_SMOKE_HAS_A_COPY_IN_CI)


#: The rows that need more than the suite's default cap, each with its own, keyed by the row's
#: name. tests/timeout-marks.toml lists each one, as ``ROW_TIMEOUTS[<row>]``, with why.
ROW_TIMEOUTS = {
    "every rail lives in tests/rails": pytest.mark.timeout(190),
    "every catalogue command is the workflow's": pytest.mark.timeout(120),
    "every workflow that runs on pull_request is cancelled when the pull request closes": (
        pytest.mark.timeout(80)
    ),
    "every-timeout-mark-is-listed-with-its-reason": pytest.mark.timeout(80),
    "no-workflow-job-outside-the-catalogue-runs-a-test": pytest.mark.timeout(90),
    "every fail-closed test module carries the security marker": pytest.mark.timeout(70),
    "each-whole-tree-check-runs-in-every-tier-that-needs-it-as-a-step-that-fails-its-job": (
        pytest.mark.timeout(70)
    ),
    "no test takes an unlisted fixed wait of half a second or more": pytest.mark.timeout(60),
    "no test walks a source root itself": pytest.mark.timeout(50),
    "no workflow that runs candidate code posts the merge-verdict check": pytest.mark.timeout(60),
    "every concurrency group a candidate joins keys on its pull request or its head": (
        pytest.mark.timeout(50)
    ),
    "nothing-throws-an-exit-status-away-unseen": pytest.mark.timeout(40),
}


def table_params(rows: Sequence[Row], timeouts: dict[str, Any]) -> list[Any]:
    """Each row as a test parameter named by the row, carrying its own mark when it has one."""
    unknown = sorted(set(timeouts) - {row.name for row in rows})
    assert not unknown, f"a timeout is keyed by no row of the table: {unknown}"
    return [pytest.param(row, marks=timeouts.get(row.name, ()), id=row.name) for row in rows]


@pytest.mark.parametrize("row", table_params(ROWS, ROW_TIMEOUTS))
def test_the_ci_contract_holds_and_each_check_can_fail(
    row: Row, subtests, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    run_row(row, subtests, tmp_path, request)
