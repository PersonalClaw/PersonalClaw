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
import datetime
import json
import re
import shutil
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import security_census
import yaml

from tools.ci import supply_chain

REPO = Path(__file__).resolve().parents[1]

#: An exact release, ``X.Y.Z``.
_EXACT = re.compile(r"\d+\.\d+\.\d+")

Tree = dict[str, str]
"""Repository-relative path to text: the files the toolchain rows read."""


def _read_paths(root: Path) -> list[Path]:
    return [
        *sorted(root.glob(".github/workflows/*.yml")),
        *sorted(root.glob("deploy/docker/Dockerfile*")),
        root / "package.json",
        *sorted(root.glob("*/package.json")),
        root / "pyproject.toml",
        root / ".python-version",
        root / ".nvmrc",
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


def _steps(tree: Tree) -> Iterator[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    for name, job_id, job in _jobs(tree):
        for step in job.get("steps") or []:
            yield name, job_id, job, step


def _matrix_values(job: dict[str, Any], key: str) -> list[str]:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict):
        return []
    values = [str(v) for v in matrix.get(key) or []]
    values += [str(entry[key]) for entry in matrix.get("include") or [] if key in entry]
    return values


def _action(step: dict[str, Any]) -> str:
    return str(step.get("uses", "")).split("@", 1)[0]


def _dockerfiles(tree: Tree) -> Iterator[tuple[str, str]]:
    for rel, text in tree.items():
        if rel.startswith("deploy/docker/Dockerfile"):
            yield rel, text


# ── the rows ───────────────────────────────────────────────────────────────────────────────


def python_violations(tree: Tree) -> list[str]:
    """Every setup-uv and setup-python step names an exact Python: a literal ``X.Y.Z`` or a
    matrix value that is one. One patch per minor across the workflows, and the minor of
    ``.python-version`` is that file's exact patch."""
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
            if not values:
                bad.append(f"{where}: python-version reads a matrix.python the job does not set")
            named += [(f"{where} (matrix)", v) for v in values]
        elif not value:
            bad.append(f"{where}: {_action(step)} names no python-version")
        else:
            named.append((where, value))
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
            for step in job.get("steps") or []:
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
    """Every job logs the image it ran on: a ``Runner identity`` step that reads the runner
    image's name and version, or the container's own ``/etc/os-release``."""
    bad: list[str] = []
    for name, job_id, job in _jobs(tree):
        if "steps" not in job:
            continue
        runs = [str(s.get("run", "")) for s in job["steps"] if s.get("name") == "Runner identity"]
        wants = ("/etc/os-release",) if "container" in job else ("ImageOS", "ImageVersion")
        if not any(all(word in run for word in wants) for run in runs):
            bad.append(f"{name}:{job_id}: no Runner identity step reading {' and '.join(wants)}")
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


def _toolchain_row(
    name: str, check: Callable[[Tree], list[str]], controls: tuple[Control, ...]
) -> Row:
    return Row(
        name=name,
        check=lambda root: check(_tree(root)),
        scaffold=_copy_toolchain_files,
        controls=controls,
    )


_IDENTITY_STEP = (
    "      - name: Runner identity\n"
    "        # The image this job ran on, so a runner image that moves is visible in the log.\n"
    '        run: echo "runner image ${ImageOS:?not a GitHub-hosted runner image}'
    ' ${ImageVersion:?no image version} on ${RUNNER_OS} ${RUNNER_ARCH}"\n'
)

#: The image references the digest plants edit, as the tree pins them today.
_UBUNTU = (
    "ubuntu:26.04@sha256:" + "88a381d5b5eeb2b35d3ad70925a362c37ce569daf43ede89ff818ec20e4d3794"
)
_NGINX = (
    "nginx:1.27-alpine@sha256:" + "65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
)
_PYTHON = (
    "python:3.13.16-slim@sha256:"
    + "70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f"
)
_NODE_ARM64 = (
    "#   linux/arm64 sha256:" + "38a36422dc7de80f3ced964270f23b3b17f73af816728d21a5fbbf84bf8be46f\n"
)

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
_TRUSTED_TRIGGERS = {"pull_request_target", "workflow_run"}
_PR_REF_MARKERS = ("pull_request.head", "workflow_run.head", "head_ref", "refs/pull/", "/pull/")
_PUBLISHING_SCOPES = {"contents", "packages", "id-token", "attestations", "deployments"}
_RELEASE_WORKFLOW = "release.yml"


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
            if held:
                found.append(f"{name}:{job_name} holds {held} outside {_RELEASE_WORKFLOW}")
            if job.get("environment") is not None:
                found.append(
                    f"{name}:{job_name} deploys to {job['environment']!r} outside "
                    f"{_RELEASE_WORKFLOW}"
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
    _write(root, f"{_WORKFLOWS}/full.yml", _MAIN_ONLY)


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
        Control("ref: refs/pull/<n>/merge fails", _feedback_checkout("ref", "refs/pull/7/merge")),
        Control("ref: github.head_ref fails", _feedback_checkout("ref", "${{ github.head_ref }}")),
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
            "permissions: {} passes",
            _replace("ci.yml", _LINT_PERMISSIONS, "    permissions: {}\n"),
            fails=False,
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
                "full.yml", "permissions:\n  contents: read\n", "permissions:\n  id-token: write\n"
            ),
        ),
        Control(
            "environment: release outside release.yml fails",
            _add_to_job("full.yml", "    environment: release\n"),
        ),
        Control(
            "an environment in its mapping form fails",
            _add_to_job("dco.yml", "    environment:\n      name: release-client\n"),
        ),
        Control(
            "any new environment outside release.yml fails",
            _add_to_job("full.yml", "    environment: smoke\n"),
        ),
        Control(
            "packages: write in a trusted job fails",
            _replace("dco.yml", "      pull-requests: write\n", "      packages: write\n"),
        ),
        Control(
            "permissions: write-all fails",
            _replace("full.yml", "permissions:\n  contents: read\n", "permissions: write-all\n"),
        ),
        Control("pull-requests: write in a trusted workflow passes", _nothing, fails=False),
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
    """Move release.yml's setting off the workflow and onto its uv jobs: every one of them, or
    only the first, so the other uv jobs run without it. release.yml has uv jobs and jobs
    without uv, so both halves of the rule are read."""
    rel = ".github/workflows/release.yml"
    workflow = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
    del workflow["env"]["UV_PYTHON_PREFERENCE"]
    uv_jobs = [
        job for job in workflow["jobs"].values() if any(_runs_uv(s) for s in job.get("steps", []))
    ]
    assert len(uv_jobs) > 1, "release.yml no longer has several uv jobs to plant on"
    for job in uv_jobs[:1] if only_first else uv_jobs:
        job.setdefault("env", {})["UV_PYTHON_PREFERENCE"] = "only-managed"
    _write(root, rel, yaml.safe_dump(workflow, sort_keys=False))


# ── The pull request builds the wheel, and scans its diff against its own base ───────────────


def wheel_and_scan_base_violations(tree: Tree) -> list[str]:
    """``ci.yml``'s ``web`` job builds the wheel after the SPA build and runs the wheel contract
    on it, and the ``harness`` job's scan names the pull request's base. The wheel must carry
    the SPA, so a wheel built before ``npm run build`` would verify a dashboard-less build; a
    scan with no base falls back to ``main`` and ``HEAD~1``, which can be a different change."""
    bad: list[str] = []
    workflow = dict(_workflows(tree)).get("ci.yml")
    if workflow is None:
        return ["ci.yml is missing"]
    jobs = workflow.get("jobs") or {}
    runs = [str(s.get("run", "")) for s in (jobs.get("web") or {}).get("steps") or []]

    def first(word: str) -> int | None:
        return next((i for i, run in enumerate(runs) if word in run), None)

    spa, build, verify = first("npm run build"), first("uv build"), first("verify_wheel.py")
    if spa is None:
        bad.append("ci.yml:web builds no SPA")
    if build is None or "--wheel" not in runs[build]:
        bad.append("ci.yml:web builds no wheel (`uv build --wheel`)")
    if verify is None or "--wheel" not in runs[verify]:
        bad.append("ci.yml:web runs no wheel contract (`verify_wheel.py --wheel`)")
    if None not in (spa, build, verify) and not (spa < build < verify):  # type: ignore[operator]
        bad.append("ci.yml:web must build the SPA, then the wheel, then verify that wheel")
    scans = [
        s
        for s in (jobs.get("harness") or {}).get("steps") or []
        if "harness scan" in str(s.get("run", ""))
    ]
    if not scans:
        bad.append("ci.yml:harness runs no `harness scan`")
    for step in scans:
        run = str(step.get("run", ""))
        env = {str(k): str(v) for k, v in (step.get("env") or {}).items()}
        named = [k for k, v in env.items() if "pull_request.base.sha" in v]
        if not any(f'--base "${k}"' in run for k in named):
            bad.append("ci.yml:harness scans without `--base` set to the pull request's base")
        if "${{" in run:
            bad.append("ci.yml:harness pastes an expression into its scan's `run:`")
    return bad


# ── The supply chain (docs/maintainers/ci.md, "Supply chain") ─────────────────────────────────
#
# tools/ci/supply_chain.py holds the rules and tools/ci/supply-chain.toml the policy they read.
# Each row checks the whole tree through the policy's exceptions and keeps its own rules'
# violations, so a plant that also breaks another rule cannot pass for the one under test.

_POLICY = supply_chain.POLICY_FILE
_BACKEND = "deploy/docker/Dockerfile.backend"
_CI = ".github/workflows/ci.yml"
_FULL = ".github/workflows/full.yml"
_RELEASE_YML = ".github/workflows/release.yml"
_TOOLING = ".github/workflows/release-image-tooling.yml"
_SETUP_NODE = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0"
_LOGIN = "docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4.6.0"
_QEMU_AMD64 = "465d3fdd28d0f2b871ba4b4ec98bd183292e96167f00d9fd40bd249f8632d705"
_QEMU_INDEX = "400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0"
_UBUNTU_INDEX = "f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7"
_PINNED_NODE_BASE = (
    "# node:24.21.0-alpine, as its multi-arch index. Platform manifests:\n"
    "#   linux/amd64 sha256:83f1c388c31fb2e51f7cbd4dea949b96260798c98f206e8e4696bc93bd964e3a\n"
    + _NODE_ARM64
    + "FROM mirror.gcr.io/library/node:24.21.0-alpine@sha256:"
    + "ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1\n"
)


def _days(n: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


def _copy_supply_chain_files(root: Path) -> None:
    for rel, text in supply_chain.read_tree(REPO).items():
        _write(root, rel, text)


def _supply_row(name: str, rules: tuple[str, ...], controls: tuple[Control, ...]) -> Row:
    """A row over *rules*, or over every rule and the exceptions when *rules* is empty."""

    def check(root: Path) -> list[str]:
        found = supply_chain.check(supply_chain.read_tree(root))
        return [str(v) for v in found if not rules or v.rule in rules]

    return Row(name=name, check=check, scaffold=_copy_supply_chain_files, controls=controls)


def _together(name: str, *controls: Control, fails: bool = True, expect: str = "") -> Control:
    def plant(root: Path) -> None:
        for control in controls:
            control.plant(root)

    return Control(name, plant, fails, expect)


def _line(name: str, rel: str, start: str, new: str = "", **kw: Any) -> Control:
    """Replace (or, with *new* empty, remove) the first line of *rel* that begins with *start*."""

    def plant(root: Path) -> None:
        lines = (root / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        index = next((i for i, line in enumerate(lines) if line.startswith(start)), None)
        assert index is not None, f"the plant {name!r} found no line {start!r} in {rel}"
        lines[index] = new + "\n" if new else ""
        _write(root, rel, "".join(lines))

    return Control(name, plant, **kw)


def _append(name: str, rel: str, text: str, **kw: Any) -> Control:
    def plant(root: Path) -> None:
        path = root / rel
        _write(root, rel, (path.read_text(encoding="utf-8") if path.exists() else "") + text)

    return Control(name, plant, **kw)


def _planted_workflow(
    name: str, steps: str, top: str = "permissions: {}\n", job: str = "", **kw: Any
) -> Control:
    text = (
        f"name: planted\non: workflow_dispatch\n{top}jobs:\n  it:\n    runs-on: ubuntu-24.04\n"
        f"{job}    steps:\n{steps}"
    )
    return _append(name, ".github/workflows/planted.yml", text, **kw)


def _runs(command: str, **kw: Any) -> Control:
    return _planted_workflow(f"`{command}`", f"      - run: {command}\n", **kw)


_LISTED_CHECKOUT = (
    '\n[[audited_write]]\nworkflow = "ci.yml"\njob = "changes"\n'
    'permission = "persist-credentials"\nsteps = ["actions/checkout"]\nreason = "planted"\n'
)

SUPPLY_CHAIN_ROWS: tuple[Row, ...] = (
    _supply_row(
        "action-sha",
        ("action-sha", "image-digest"),
        (
            _plant(
                "an action by tag",
                ".github/workflows/dco.yml",
                "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0",
                "actions/github-script@v9",
            ),
            _plant(
                "an action by branch",
                _RELEASE_YML,
                "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2",
                "pypa/gh-action-pypi-publish@release/v1",
            ),
            _plant("a SHA with no release comment", ".github/workflows/nix.yml", " # v31.11.2", ""),
            _plant(
                "a SHA whose comment is a bare major",
                ".github/workflows/dco.yml",
                "# v9.0.0",
                "# v9",
            ),
            _planted_workflow("a docker:// action by tag", "      - uses: docker://alpine:3.20\n"),
            _planted_workflow(
                "a docker:// action by digest that names no platform",
                f"      - uses: docker://alpine:3.20@sha256:{'a' * 64}\n",
            ),
            _planted_workflow(
                "a docker:// action pinned to its linux/amd64 manifest passes",
                f"      # alpine:3.20, the linux/amd64 manifest of index sha256:{'b' * 64}.\n"
                f"      - uses: docker://alpine:3.20@sha256:{'a' * 64}\n",
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "image-digest",
        ("image-digest",),
        (
            _plant("a workflow container by tag alone", _FULL, _UBUNTU, "ubuntu:26.04"),
            _plant(
                "the moving tag latest",
                _FULL,
                "container: ubuntu:26.04@",
                "container: ubuntu:latest@",
            ),
            _plant(
                "an image base by tag alone",
                "deploy/docker/Dockerfile.web",
                _NGINX,
                "nginx:1.27-alpine",
            ),
            _plant("a base ARG by tag alone", _BACKEND, _PYTHON, "python:3.13.16-slim"),
            _plant(
                "a two-platform base without its arm64 manifest",
                "deploy/docker/Dockerfile.web",
                _NODE_ARM64,
                "",
            ),
            _plant(
                "a workflow container without its platform",
                ".github/workflows/clean-machine-walkthroughs.yml",
                "the linux/amd64 manifest",
                "the manifest",
            ),
            _plant(
                "a workflow container pinned to the index its comment names",
                _FULL,
                _UBUNTU,
                "ubuntu:26.04@sha256:" + _UBUNTU_INDEX,
                expect="pins the index",
            ),
            _plant(
                "a workflow container whose comment names no index",
                ".github/workflows/clean-machine-walkthroughs.yml",
                "of index",
                "of",
                expect="does not name the index",
            ),
            _plant(
                "the QEMU image pinned to its index",
                _RELEASE_YML,
                _QEMU_AMD64,
                _QEMU_INDEX,
                expect="pins the index",
            ),
            _plant(
                "a base ARG with no default",
                _BACKEND,
                "ARG PYTHON_IMAGE=mirror.gcr.io/library/" + _PYTHON,
                "ARG PYTHON_IMAGE",
                expect="no default",
            ),
            _append(
                "a tagged FROM in a Dockerfile outside deploy/docker/",
                "tools/sandbox/Dockerfile",
                "FROM alpine:3.20\n",
            ),
            _append(
                "a pinned base in a Dockerfile outside deploy/docker/ passes",
                "tools/sandbox/Dockerfile",
                _PINNED_NODE_BASE,
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "run-install",
        ("run-install",),
        (
            _plant("pip-audit with no version", _FULL, "pip-audit==2.10.1", "pip-audit"),
            _plant(
                "uv run --with a package with no version",
                _RELEASE_YML,
                "--with packaging==26.2",
                "--with packaging",
            ),
            _plant(
                "the image's wheel with no version",
                _BACKEND,
                "pip install wheel==0.48.0",
                "pip install --upgrade wheel",
            ),
            _runs("npm install -g typescript"),
            _runs("npm install"),
            _runs("npx cowsay hi"),
            _runs("curl -fsSL https://example.com/x.sh | sh"),
            _runs("pip install -r requirements.txt"),
            _runs("uvx ruff check"),
            _line(
                "the agent CLIs once their exception scopes another rule",
                _POLICY,
                'scope = "run-install"',
                'scope = "node-eol"',
                expect="npm install of",
            ),
            _runs("pip install ruff==0.15.0", fails=False),
            _runs("pip install --require-hashes --no-deps -r requirements.txt", fails=False),
            _runs("npx playwright --version", fails=False),
            _runs("uv pip install --no-deps dist/*.whl", fails=False),
            _runs("npm install -g typescript@5.9.3", fails=False),
            _runs("sudo apt-get install -y tmux", fails=False),
        ),
    ),
    _supply_row(
        "setup-floating",
        ("setup-floating",),
        (
            _plant(
                "setup-node with check-latest",
                _CI,
                "node-version-file: .nvmrc\n",
                "node-version-file: .nvmrc\n          check-latest: true\n",
            ),
            _plant("setup-uv with no version", _CI, '          version: "0.13.1"\n', ""),
            _plant("setup-node with no version", _CI, "          node-version-file: .nvmrc\n", ""),
            _line(
                "setup-qemu with no image", _RELEASE_YML, "          image: docker.io/tonistiigi"
            ),
            _plant(
                "setup-qemu's image by tag",
                _RELEASE_YML,
                f"binfmt:qemu-v10.2.3-68@sha256:{_QEMU_AMD64}",
                "binfmt:latest",
            ),
            _plant("buildx's latest", _RELEASE_YML, "version: v0.38.0", "version: latest"),
            _line("buildx with no BuildKit image", _RELEASE_YML, "          driver-opts: image="),
            _plant(
                "buildx's exact version without the v passes",
                _RELEASE_YML,
                "version: v0.38.0",
                "version: 0.38.0",
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "node-eol",
        ("node-eol", "policy-file"),
        (
            _line("the pinned line past its date", _POLICY, '"24" =', f'"24" = {_days(-1)}'),
            _line("the pinned line not in the table", _POLICY, '"24" ='),
            _line(
                "a table value that is not a date",
                _POLICY,
                '"24" =',
                '"24" = "2028-04-30"',
                expect="must be a major line with a date",
            ),
            _planted_workflow(
                "setup-node on a line past its date",
                f"      - uses: {_SETUP_NODE}\n        with:\n          node-version: 20.19.0\n",
                expect="20.19.0 reached its end of life",
            ),
            _plant(
                ".nvmrc on a newer dated line passes", ".nvmrc", "24.21.0", "26.1.0", fails=False
            ),
        ),
    ),
    _supply_row(
        "least-privilege",
        ("least-privilege",),
        (
            _plant(
                "write-all at the top",
                _FULL,
                "permissions:\n  contents: read\n",
                "permissions: write-all\n",
            ),
            _planted_workflow("write-all on a job", "", job="    permissions: write-all\n"),
            _planted_workflow(
                "an unlisted contents: write on a job",
                "      - run: true\n",
                job="    permissions:\n      contents: write\n",
            ),
            _planted_workflow(
                "an unlisted write inherited from the top",
                "      - run: true\n",
                top="permissions:\n  issues: write\n",
                expect="inherited from the top",
            ),
            _plant(
                "a top-level write beside a job that grants its own",
                ".github/workflows/pr-feedback.yml",
                "  actions: read           # download the artifact from the CI run\n\njobs:",
                "  actions: read\n  pull-requests: write\n\njobs:",
                expect="top-level write",
            ),
            _plant(
                "an extra write on a listed job",
                _RELEASE_YML,
                "      contents: write\n",
                "      contents: write\n      issues: write\n",
            ),
            _planted_workflow("a job with no permissions anywhere", "      - run: true\n", top=""),
            _line(
                "a checkout that keeps its credentials", _CI, "          persist-credentials: false"
            ),
            _plant(
                "a workflow env that hands a token to every step",
                ".github/workflows/headless-run-smoke.yml",
                _PREFERENCE,
                _PREFERENCE + "  GH_TOKEN: ${{ github.token }}\n",
            ),
            _plant(
                "a job env that hands a secret to every step",
                _FULL,
                '      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n',
                '      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n      SMOKE: ${{ secrets.SMOKE }}\n',
            ),
            *(
                _line(
                    f"an audited write with no {field}", _POLICY, start, expect=f"missing {field}"
                )
                for field, start in (
                    ("workflow", 'workflow = "dco.yml"'),
                    ("job", 'job = "dco"'),
                    ("permission", 'permission = "pull-requests: write"'),
                    ("steps", 'steps = ["Explain how to fix DCO"]'),
                    ("reason", 'reason = "Posts or updates the one comment that says which'),
                )
            ),
            _line(
                "an audited write for a job that is gone",
                _POLICY,
                'job = "dco"',
                'job = "gone"',
                expect="matches no job",
            ),
            _line(
                "an audited write naming a step the job lacks",
                _POLICY,
                'steps = ["Explain how to fix DCO"]',
                'steps = ["No such step"]',
                expect="no step named",
            ),
            _line(
                "an audited write for a permission the job does not hold",
                _POLICY,
                'permission = "pull-requests: write"',
                'permission = "issues: write"',
                expect="does not hold",
            ),
            _together(
                "a checkout that keeps its credentials passes once it is an audited write",
                _line("", _CI, "          persist-credentials: false"),
                _append("", _POLICY, _LISTED_CHECKOUT),
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "dependabot",
        ("dependabot",),
        (
            _plant(
                "the docker entry watching another folder",
                ".github/dependabot.yml",
                "    directory: /deploy/docker\n",
                "    directory: /deploy/compose\n",
            ),
            _append("a Dockerfile in a folder nothing watches", "tools/sandbox/Dockerfile", "\n"),
            _plant(
                "the folder written with a trailing slash passes",
                ".github/dependabot.yml",
                "    directory: /deploy/docker\n",
                "    directory: /deploy/docker/\n",
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "release-tooling",
        ("release-tooling",),
        (
            _plant("release.yml's buildx moved alone", _RELEASE_YML, "v0.38.0", "v0.37.0"),
            _plant("the smoke's buildx moved alone", _TOOLING, "v0.38.0", "v0.37.0"),
            _plant("the smoke's QEMU image moved alone", _TOOLING, _QEMU_AMD64, "c" * 64),
            _plant(
                "the smoke not run when release.yml changes",
                _TOOLING,
                "      - .github/workflows/release.yml\n",
                "",
            ),
            _plant(
                "the smoke holding a permission",
                _TOOLING,
                "permissions: {}\n\njobs:",
                "permissions:\n  contents: read\n\njobs:",
            ),
            _plant(
                "the smoke logging in",
                _TOOLING,
                "      - name: Build a two-platform image (no push)\n",
                f"      - uses: {_LOGIN}\n      - name: Build a two-platform image (no push)\n",
            ),
            _plant("the smoke publishing", _TOOLING, "--output type=cacheonly", "--push"),
            _plant(
                "a comment changed on one side passes",
                _TOOLING,
                "# These three steps are",
                "# These steps are",
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "exceptions",
        (),
        (
            _line("an expired exception", _POLICY, "expires =", f"expires = {_days(-1)}"),
            _line(
                "an exception more than 30 days out",
                _POLICY,
                "expires =",
                f"expires = {_days(31)}",
                expect="more than 30 days",
            ),
            _line(
                "an exception that matches nothing",
                _POLICY,
                'reference = "npm install of',
                'reference = "nothing"',
                expect="matches nothing",
            ),
            *(
                _line(f"an exception with no {field}", _POLICY, start, expect=f"missing {field}")
                for field, start in (
                    ("file", 'file = "deploy/docker/Dockerfile.backend"'),
                    ("reference", 'reference = "npm install of'),
                    ("scope", 'scope = "run-install"'),
                    ("owner", 'owner = "maintainers"'),
                    ("reason", 'reason = "The gateway image'),
                    ("expires", "expires ="),
                )
            ),
            _line(
                "an exception for a rule no exception may waive",
                _POLICY,
                'scope = "run-install"',
                'scope = "least-privilege"',
                expect="not a rule an exception may waive",
            ),
            _line(
                "an exception with an unknown field",
                _POLICY,
                'owner = "maintainers"',
                'owner = "maintainers"\nticket = "x"',
                expect="unknown field ticket",
            ),
            _line(
                "an exception renewed exactly 30 days out passes",
                _POLICY,
                "expires =",
                f"expires = {_days(30)}",
                fails=False,
            ),
        ),
    ),
)


ROWS: tuple[Row, ...] = (
    SECURITY_MARKER,
    _toolchain_row(
        "python",
        python_violations,
        (
            _plant(
                "setup-uv without python-version",
                ".github/workflows/ci.yml",
                '          python-version: "3.12.15"\n',
                "",
            ),
            _plant(
                "a minor, not a patch",
                ".github/workflows/ci.yml",
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
                ".github/workflows/full.yml",
                '{ python: "3.13.16", os: macos-14',
                '{ python: "3.13.15", os: macos-14',
            ),
            _plant(".python-version a minor", ".python-version", "3.13.16", "3.13"),
        ),
    ),
    _toolchain_row(
        "uv",
        uv_violations,
        (
            _plant(
                "setup-uv installs the newest uv",
                ".github/workflows/ci.yml",
                '          version: "0.13.1"\n',
                "",
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
                ".github/workflows/ci.yml",
                _PREFERENCE,
                "",
            ),
            _plant(
                "the workflow sets managed",
                ".github/workflows/full.yml",
                _PREFERENCE,
                "  UV_PYTHON_PREFERENCE: managed\n",
            ),
            _plant(
                "the workflow sets another value",
                ".github/workflows/release.yml",
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
                ".github/workflows/ci.yml",
                "node-version-file: .nvmrc",
                'node-version: "24"',
            ),
            _plant(".nvmrc a major", ".nvmrc", "24.21.0", "24"),
            _plant(
                "an image Node stage off .nvmrc",
                "deploy/docker/Dockerfile.web",
                "node:24.21.0-alpine@",
                "node:20.19.0-alpine@",
            ),
        ),
    ),
    _toolchain_row(
        "npm",
        npm_violations,
        (
            _plant("no packageManager", "package.json", '  "packageManager": "npm@11.19.0",\n', ""),
            _plant("a floating npm", "package.json", '"npm@11.19.0"', '"npm@11"'),
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
                "a matrix leg on macos-latest",
                ".github/workflows/full.yml",
                "os: [ubuntu-24.04, macos-26]",
                "os: [ubuntu-24.04, macos-latest]",
            ),
        ),
    ),
    *SUPPLY_CHAIN_ROWS,
    _toolchain_row(
        "runner-identity",
        runner_identity_violations,
        (
            _plant(
                "a job that logs no runner image", ".github/workflows/dco.yml", _IDENTITY_STEP, ""
            ),
            _plant(
                "a container job reading the runner's variables",
                ".github/workflows/full.yml",
                "          . /etc/os-release\n",
                '          echo "$ImageOS $ImageVersion"\n',
            ),
        ),
    ),
    _toolchain_row(
        "wheel-and-scan-base",
        wheel_and_scan_base_violations,
        (
            _plant(
                "web without the wheel contract",
                ".github/workflows/ci.yml",
                "        run: uv run --no-project python scripts/verify_wheel.py"
                ' --wheel "dist/*.whl"\n',
                "        run: ls dist\n",
            ),
            _plant(
                "a wheel built without --wheel (the sdist path)",
                ".github/workflows/ci.yml",
                "run: uv build --wheel --out-dir dist",
                "run: uv build --out-dir dist",
            ),
            Control(
                "the wheel built before the SPA",
                _both(
                    _replace(
                        "ci.yml",
                        "      - name: build (tsc + vite)\n        run: npm run build\n",
                        "",
                    ),
                    _replace(
                        "ci.yml",
                        '--wheel "dist/*.whl"\n',
                        '--wheel "dist/*.whl"\n      - name: the SPA, late\n'
                        "        run: npm run build\n",
                    ),
                ),
            ),
            _plant(
                "a scan with no base",
                ".github/workflows/ci.yml",
                'uv run python -m harness scan --diff --base "$BASE_SHA"',
                "uv run python -m harness scan --diff",
            ),
            _plant(
                "a base pasted into run: as an expression",
                ".github/workflows/ci.yml",
                '--base "$BASE_SHA"',
                '--base "${{ github.event.pull_request.base.sha }}"',
            ),
        ),
    ),
    PER_TEST_TEMPORARY_FOLDERS,
    NO_TRUSTED_CHECKOUT_OF_CANDIDATE_CODE,
    NO_CANDIDATE_JOB_HOLDS_A_WRITE_OR_A_SECRET,
    ONLY_THE_RELEASE_CONTROLLER_PUBLISHES,
)


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.name)
def test_the_ci_contract_holds_and_each_check_can_fail(
    row: Row, subtests, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    with subtests.test("the scaffold satisfies the check"):
        row.scaffold(tmp_path / "scaffold")
        assert row.check(tmp_path / "scaffold") == []
    for index, control in enumerate(row.controls):
        with subtests.test(control.name):
            root = tmp_path / f"control-{index}"
            shutil.copytree(tmp_path / "scaffold", root)
            control.plant(root)
            found = row.check(root)
            if control.fails:
                assert found, f"the planted violation went unseen: {control.name}"
                assert any(control.expect in v for v in found), "\n".join(found)
            else:
                assert found == [], "\n".join(found)
    with subtests.test("the tree"):
        violations = row.check(REPO)
        assert violations == [], "\n".join(violations)
    if row.on_tree is not None:
        row.on_tree(request)
