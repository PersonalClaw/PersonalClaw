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
import json
import re
import shutil
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ci_masking
import private_walkers
import pytest
import security_census
import yaml
from rails_repo import REPO

from tools.ci import breadth_evidence, rails_census

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


def _legs(job: dict[str, Any], value: str) -> list[tuple[str, str]]:
    """Each leg's runner and *value* (a step's input), with ``${{ matrix.<key> }}`` resolved per
    leg: the cross product of the matrix's lists, and each ``include`` entry as a leg of its own."""
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict):
        matrix = {}
    lists = {
        k: v for k, v in matrix.items() if k not in ("include", "exclude") and isinstance(v, list)
    }
    legs: list[dict[str, Any]] = [{}]
    for key, values in lists.items():
        legs = [{**leg, key: item} for leg in legs for item in values]
    legs += [entry for entry in matrix.get("include") or [] if isinstance(entry, dict)]

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


def _pinned(rel: str, pattern: str) -> str:
    """The first match of *pattern* in *rel* as the tree has it today, so a plant follows a
    refresh (a Dependabot base move, a hand-moved container) instead of going stale. A pattern
    that matches nothing fails here, loudly, rather than leaving a plant with nothing to edit."""
    match = re.search(pattern, (REPO / rel).read_text(encoding="utf-8"), flags=re.M)
    assert match, f"no {pattern!r} in {rel}: the plants that edit it need a new anchor"
    return match.group(1)


_DIGEST_REF = r"(?:[\w.-]+/)*({name}:[\w.-]+@sha256:[0-9a-f]{{64}})"

#: The image references the digest plants edit, as the tree pins them today.
_UBUNTU = _pinned(
    ".github/workflows/breadth.yml", "container: " + _DIGEST_REF.format(name="ubuntu")
)
_NGINX = _pinned("deploy/docker/Dockerfile.web", "^FROM " + _DIGEST_REF.format(name="nginx"))
_PYTHON = _pinned("deploy/docker/Dockerfile.backend", "^FROM " + _DIGEST_REF.format(name="python"))
_NODE_BASE = _pinned("deploy/docker/Dockerfile.web", "^FROM " + _DIGEST_REF.format(name="node"))
#: The comment line naming the node base's arm64 manifest, newline included.
_NODE_ARM64 = _pinned(
    "deploy/docker/Dockerfile.web",
    r"(#\s+linux/arm64 sha256:[0-9a-f]{64}\n)(?=FROM \S*node:)",
)
_NODE_AMD64 = _pinned(
    "deploy/docker/Dockerfile.web",
    r"#\s+linux/amd64 sha256:([0-9a-f]{64})\n#\s+linux/arm64 sha256:[0-9a-f]{64}\nFROM \S*node:",
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


# ── The tested tree on main, and the nightly breadth ────────────────────────────────────────
#
# A push to main runs ``ci.yml``'s jobs again only when no green pull-request run tested its
# tree, and platform breadth moves to ``breadth.yml``, a nightly whose legs reuse an earlier
# result only on identical inputs. These rows hold the shape that makes both claims true.

_GATE = "needs.tree-check.outputs.tested != 'true'"
#: ``ci.yml`` jobs that run whatever ``tree-check`` answers: the check itself, the change
#: classifier, the security corpus (never skipped on main) and the SDK contract.
_ALWAYS_RUN = frozenset({"tree-check", "changes", "security-corpus", "apps-contract"})
_ON_MAIN = "github.ref == 'refs/heads/main'"


def _workflow(tree: Tree, name: str) -> dict[str, Any] | None:
    return dict(_workflows(tree)).get(name)


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else [str(n) for n in needs]


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps") or [])


def _pytest_run(job: dict[str, Any]) -> str:
    return next(
        (str(s["run"]) for s in job.get("steps") or [] if "pytest" in str(s.get("run", ""))), ""
    )


def _waits_on_tree_check(where: str, job: dict[str, Any]) -> list[str]:
    """A gated job needs ``tree-check``, survives a failed dependency, and runs unless tested."""
    bad: list[str] = []
    condition = str(job.get("if", ""))
    if "tree-check" not in _needs(job):
        bad.append(f"{where} does not wait on tree-check")
    if "!cancelled()" not in condition:
        bad.append(f"{where} is not gated on `!cancelled()`, so a failed check skips it")
    if _GATE not in condition:
        bad.append(f"{where} does not run on every answer but `tested == 'true'`")
    return bad


def _main_never_cancels(where: str, workflow: dict[str, Any]) -> list[str]:
    concurrency = workflow.get("concurrency") or {}
    group = str(concurrency.get("group", ""))
    cancel = concurrency.get("cancel-in-progress", False)
    bad: list[str] = []
    if f"{_ON_MAIN} && github.sha" not in group:
        bad.append(f"{where}: a run on main is not grouped by its own commit")
    off_main = "github.ref != 'refs/heads/main'"
    if cancel is True or (isinstance(cancel, str) and off_main not in cancel):
        bad.append(f"{where}: a run on main can be cancelled")
    # Groups are shared across the repository, and a new pending entry replaces the old one,
    # so a job's own group could cancel or replace a main run's job whatever the workflow's is.
    for job_id, job in (workflow.get("jobs") or {}).items():
        if isinstance(job, dict) and "concurrency" in job:
            bad.append(f"{where}:{job_id} has its own concurrency group, which can cancel it")
    return bad


def main_push_runs_violations(tree: Tree) -> list[str]:
    """``ci.yml`` runs on every push to main, and no main run is ever cancelled or replaced."""
    workflow = _workflow(tree, "ci.yml")
    if workflow is None:
        return ["ci.yml is missing"]
    push = _triggers(workflow).get("push", "absent")
    bad: list[str] = []
    if push == "absent":
        bad.append("ci.yml does not run on a push")
    elif not isinstance(push, dict):
        bad.append("ci.yml runs on a bare `push:`, which a tag push also starts")
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
            bad.append(f"ci.yml's push trigger leaves main, or some pushes to it, out: {push}")
        if "tags" in push:
            bad.append("ci.yml runs on a tag push, which is release.yml's")
    return bad + _main_never_cancels("ci.yml", workflow)


def _suite_words(command: str) -> list[str]:
    """A pytest command's words, less the two a macOS shard may differ in: its worker count and
    its report's path. Everything else (paths, selections, the split) must be the same."""
    return re.sub(r"\s-n\s+\S+|\s--junitxml=\S+", "", " " + command).split()


def macos_leg_violations(tree: Tree) -> list[str]:
    """``test-macos`` runs ``test-shard``'s split suite on macOS, on pull requests into main and
    on a main push no green pull-request run tested."""
    jobs = (_workflow(tree, "ci.yml") or {}).get("jobs") or {}
    macos, ubuntu = jobs.get("test-macos"), jobs.get("test-shard")
    if macos is None or ubuntu is None:
        return ["ci.yml has no test-macos or no test-shard"]
    bad = _waits_on_tree_check("ci.yml:test-macos", macos)
    condition = str(macos.get("if", ""))
    on_pr = "github.event_name == 'pull_request' && github.base_ref == 'main'"
    if on_pr not in condition:
        bad.append("ci.yml:test-macos does not run on pull requests into main")
    if f"github.event_name == 'push' && {_ON_MAIN}" not in condition:
        bad.append("ci.yml:test-macos does not run on a push to main no pull-request run tested")
    if not str(macos.get("runs-on", "")).startswith("macos-"):
        bad.append(f"ci.yml:test-macos runs on {macos.get('runs-on')!r}, not macOS")
    shards, theirs = _matrix_values(macos, "shard"), _matrix_values(ubuntu, "shard")
    if shards != theirs:
        bad.append(f"ci.yml:test-macos splits into {len(shards)}, test-shard into {len(theirs)}")
    command = _pytest_run(macos)
    listing = f"--leg test-macos --shard ${{{{ matrix.shard }}}}/{len(shards)} "
    if listing not in " ".join(_runs(macos).split()) + " ":
        bad.append("ci.yml:test-macos does not list its own shard of its own leg's plan")
    if _suite_words(command) != _suite_words(_pytest_run(ubuntu)):
        bad.append("ci.yml:test-macos's pytest selects other than test-shard's")
    if (macos.get("env") or {}) != (ubuntu.get("env") or {}) or any(
        "PYTEST_ADDOPTS" in (s.get("env") or {}) or "PYTEST_ADDOPTS" in str(s.get("run", ""))
        for s in macos.get("steps") or []
    ):
        bad.append("ci.yml:test-macos's environment differs from test-shard's")
    if " ".join(condition.split()) != _MACOS_IF:
        bad.append(f"ci.yml:test-macos's condition is not {_MACOS_IF!r}")
    mine, theirs_step = _pytest_step(macos), _pytest_step(ubuntu)
    for key in ("if", "working-directory", "env", "shell"):
        if mine.get(key) != theirs_step.get(key):
            bad.append(f"ci.yml:test-macos's pytest step's {key} differs from test-shard's")
    if (macos.get("defaults") or {}) != (ubuntu.get("defaults") or {}):
        bad.append("ci.yml:test-macos's defaults differ from test-shard's")
    matrices = [(j.get("strategy") or {}).get("matrix") or {} for j in (macos, ubuntu)]
    if [m.get("exclude") for m in matrices] != [matrices[1].get("exclude")] * 2:
        bad.append("ci.yml:test-macos excludes shards test-shard runs")
    return bad


#: test-macos's whole condition: a clause kept but made dead (`&& github.event_name != 'push'`)
#: would pass a check that only looks for each clause's text.
_MACOS_IF = (
    "${{ !cancelled() && needs.tree-check.outputs.tested != 'true' && "
    "((github.event_name == 'pull_request' && github.base_ref == 'main') || "
    "(github.event_name == 'push' && github.ref == 'refs/heads/main')) }}"
)


def _pytest_step(job: dict[str, Any]) -> dict[str, Any]:
    return next((s for s in job.get("steps") or [] if "pytest" in str(s.get("run", ""))), {})


def tree_check_gating_violations(tree: Tree) -> list[str]:
    """Every ``ci.yml`` job outside the always-run set waits on ``tree-check``'s answer."""
    jobs = (_workflow(tree, "ci.yml") or {}).get("jobs") or {}
    if "tree-check" not in jobs:
        return ["ci.yml has no tree-check job"]
    bad: list[str] = []
    for job_id, job in jobs.items():
        if job_id not in _ALWAYS_RUN:
            bad += _waits_on_tree_check(f"ci.yml:{job_id}", job)
    return bad


_FETCH_BEFORE = "contents/tools/ci/tree_tested.py?ref=${BEFORE}"
_CHECKED_OUT_COPY = re.compile(r"python3?\s+(?:\./)?tools/ci/tree_tested\.py")


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
    if f'python3 "$check" --workflow {asks}' not in " ".join(run.split()):
        bad.append(f"{where} does not ask the fetched check about {asks}")
    return bad


def tree_check_reads_before_violations(tree: Tree) -> list[str]:
    """``tree-check`` runs the PREVIOUS main commit's copy of the script: read-only, nothing
    checked out, nothing installed."""
    bad: list[str] = []
    for name in ("ci.yml", "docker-single-container.yml"):
        jobs = (_workflow(tree, name) or {}).get("jobs") or {}
        bad += _tree_check_violations(f"{name}:tree-check", jobs.get("tree-check"), name)
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


def push_breadth_violations(tree: Tree) -> list[str]:
    """No ``macos-14`` leg anywhere (any size, any case, in the forms :func:`_runner_labels`
    reads), and coverage only in a workflow no push or PR starts."""
    bad: list[str] = []
    for name, workflow in _workflows(tree):
        on_change = {"push", "pull_request"} & set(_triggers(workflow))
        for job_id, job in (workflow.get("jobs") or {}).items():
            if any(
                re.fullmatch(r"macos-14(?:-.+)?", label, flags=re.IGNORECASE)
                for label in _runner_labels(job)
            ):
                bad.append(f"{name}:{job_id} runs on macos-14")
            steps = job.get("steps") or []
            if on_change and any(_measures_coverage(str(s.get("run", ""))) for s in steps):
                bad.append(f"{name}:{job_id} measures coverage on a push or pull request")
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

    def reaches(job_id: str, seen: frozenset[str] = frozenset()) -> bool:
        needs = _needs(jobs.get(job_id) or {})
        return "freshness" in needs or any(
            reaches(n, seen | {job_id}) for n in needs if n not in seen
        )

    for job_id in jobs:
        if job_id != "freshness" and not reaches(job_id):
            bad.append(f"breadth.yml:{job_id} does not wait on freshness")
    missing = set(jobs) - {"report"} - set(_needs(jobs["report"]))
    if missing:
        bad.append(f"breadth.yml:report does not wait on {sorted(missing)}")
    condition = str(jobs["report"].get("if", ""))
    if "!cancelled()" not in condition or "success()" in condition:
        bad.append("breadth.yml:report does not run whatever the legs concluded")
    return bad


def _security_leg(job: dict[str, Any]) -> bool:
    runs = _runs(job)
    return "tests/security" in runs or re.search(r"-m\s+['\"]?security", runs) is not None


def non_reusable_violations(tree: Tree) -> list[str]:
    """The legs the record never reuses are exactly ``NON_REUSABLE``, and none of them asks to
    carry an earlier result."""
    jobs = _breadth_jobs(tree)
    bad = [
        f"NON_REUSABLE names {job_id}, which breadth.yml does not have"
        for job_id in sorted(breadth_evidence.NON_REUSABLE - set(jobs))
    ]
    for job_id, job in jobs.items():
        decides = "breadth_evidence.py decide" in _runs(job)
        if job_id in breadth_evidence.NON_REUSABLE and decides:
            bad.append(f"breadth.yml:{job_id} is never reused, yet it carries an earlier result")
        if _security_leg(job) and job_id not in breadth_evidence.NON_REUSABLE:
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


def breadth_shards_violations(tree: Tree) -> list[str]:
    """Every breadth shard records what it ran, so the record holds what the run did:

    - every job but freshness, coverage and report is a leg, and writes entries under exactly
      one key, which no other job writes and its ``decide --key`` names; the keys and their
      matrices' shard counts (every dimension) are exactly ``SHARDS``;
    - a managed Python is installed before the first ``uv python find`` (setup-uv only names
      one, and ``uv python find`` never downloads);
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
        runs = [str(s.get("run", "")) for s in steps]
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
            if downloads_map and job_id in breadth_evidence.NON_REUSABLE:
                bad.append(f"breadth.yml:{job_id} downloads freshness's map, which it never reads")
            elif downloads_map and step.get("if") != "${{ github.ref == 'refs/heads/main' }}":
                bad.append(f"breadth.yml:{job_id} downloads the map off main, where none is read")
    return bad


def docker_tree_check_violations(tree: Tree) -> list[str]:
    """``docker-single-container.yml`` builds on a main push only when the tree is untested, and
    never cancels a main run."""
    workflow = _workflow(tree, "docker-single-container.yml")
    if workflow is None:
        return ["docker-single-container.yml is missing"]
    jobs = workflow.get("jobs") or {}
    if "tree-check" not in jobs:
        return ["docker-single-container.yml has no tree-check job"]
    bad: list[str] = []
    for job_id, job in jobs.items():
        if job_id != "tree-check":
            bad += _waits_on_tree_check(f"docker-single-container.yml:{job_id}", job)
    return bad + _main_never_cancels("docker-single-container.yml", workflow)


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
    return bad


_SHARD_LIST = '--shard-list "$RUNNER_TEMP/shard.txt"'
_PLAN_FILE = '--plan "$RUNNER_TEMP/shard-plan/plan.json"'
#: The job that plans each workflow's suite shards once for the run.
_PLANNERS = {"ci.yml": "shard-plan", "breadth.yml": "freshness"}
_PLAN_LEG = re.compile(r"--leg\s+([\w-]+):([\w.-]+):([\w.-]+):(\d+)")
_BREADTH_LISTING = (
    '--leg "${{ github.job }}" --shard "${{ matrix.shard }}/${{ strategy.job-total }}"'
)


def suite_shard_violations(tree: Tree) -> list[str]:
    """Every suite shard runs its share of its run's one plan (#3923):

    - one job of each workflow plans every leg (``shards plan``), and checks the plan
      (``shards check``) after making it;
    - each matrix job whose pytest runs the suite waits on that job, downloads the plan, lists
      its own leg's shard ``${{ matrix.shard }}`` of N before pytest, and runs pytest with that
      ``--shard-list``. N is the job's matrix size, and the plan names the leg with that N and
      the prefix of the job's junit artifact;
    - no suite step passes pytest-split's ``--splits`` or ``--group``.
    """
    bad: list[str] = []
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
            own = (f"--leg {job_id} --shard ${{{{ matrix.shard }}}}/{n} ", _BREADTH_LISTING + " ")
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
    """No workflow command passes ``--no-cov``.

    Coverage is off unless a command asks for it with ``--cov``, so ``--no-cov`` undoes nothing.
    One left in a command reads as though a default still measured coverage.
    """
    return [
        f"{name}:{job_id} passes --no-cov: {' '.join(words)}"
        for name, job_id, _job, step in _steps(tree)
        for words in _commands(str(step.get("run", "")))
        if "--no-cov" in words
    ]


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


_CI = ".github/workflows/ci.yml"
_BREADTH = ".github/workflows/breadth.yml"
_DOCKER = ".github/workflows/docker-single-container.yml"
_TESTED_IF = "if: ${{ !cancelled() && needs.tree-check.outputs.tested != 'true' }}"
_MACOS_RUN = "uv run pytest -n 2 --dist worksteal -q"


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
            "an exemption that names no walk any more fails",
            lambda root: _write(
                root,
                next(iter(private_walkers.NOT_THROUGH_THE_INDEX)).split("::")[0],
                "def test_it():\n    pass\n",
            ),
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
                ".github/workflows/breadth.yml",
                'python: ["3.13.16"]',
                'python: ["3.13.15"]',
            ),
            _plant(".python-version a minor", ".python-version", "3.13.16", "3.13"),
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
                ".github/workflows/breadth.yml",
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
                        "      - name: build (vite)\n        run: npm run build\n",
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
    _toolchain_row(
        "main-push-runs-and-is-never-cancelled",
        main_push_runs_violations,
        (
            _plant(
                "branches-ignore: [main]",
                _CI,
                '    branches: ["**"]\n',
                "    branches-ignore: [main]\n",
            ),
            _plant(
                "cancel-in-progress: true on main",
                _CI,
                "  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}\n",
                "  cancel-in-progress: true\n",
            ),
            _plant(
                "a main run grouped by branch, not by commit",
                _CI,
                "github.ref == 'refs/heads/main' && github.sha || ",
                "",
            ),
            _plant(
                "a bare push: (a tag push would start it)",
                _CI,
                "  push:\n    # Every branch, main included. A tag is not a branch, so a tag push"
                " starts no run here:\n    # tags are release.yml's.\n"
                '    branches: ["**"]\n',
                "  push:\n",
            ),
            _plant(
                'a "!main" after "**"',
                _CI,
                '    branches: ["**"]\n',
                '    branches: ["**", "!main"]\n',
            ),
            _plant(
                "a paths-ignore on the main trigger",
                _CI,
                '    branches: ["**"]\n',
                '    branches: ["**"]\n    paths-ignore: ["docs/**"]\n',
            ),
            _plant(
                "a job-level concurrency group",
                _CI,
                "  test-macos:\n",
                "  test-macos:\n    concurrency:\n      group: macos-${{ matrix.shard }}\n"
                "      cancel-in-progress: true\n",
            ),
            Control("the per-commit group on main passes", _nothing, fails=False),
        ),
    ),
    _toolchain_row(
        "test-macos-before-the-merge",
        macos_leg_violations,
        (
            _plant(
                "test-macos without the tree-check condition",
                _CI,
                "if: ${{ !cancelled() && needs.tree-check.outputs.tested != 'true'"
                " && ((github.event_name",
                "if: ${{ !cancelled() && ((github.event_name",
            ),
            _plant(
                "test-macos on a non-macOS runner",
                _CI,
                "    runs-on: macos-26\n",
                "    runs-on: ubuntu-24.04\n",
            ),
            _plant(
                "a shard count other than test-shard's",
                _CI,
                "    runs-on: macos-26\n    timeout-minutes: 50\n    strategy:\n"
                "      fail-fast: false\n      matrix:\n        shard: [1, 2, 3, 4]\n",
                "    runs-on: macos-26\n    timeout-minutes: 50\n    strategy:\n"
                "      fail-fast: false\n      matrix:\n        shard: [1, 2, 3]\n",
            ),
            _plant(
                "a -k only test-macos has",
                _CI,
                _MACOS_RUN,
                '_MACOS -k "not vector"'.replace("_MACOS", _MACOS_RUN),
            ),
            _plant(
                "an --ignore only test-macos has",
                _CI,
                _MACOS_RUN,
                f"{_MACOS_RUN} --ignore=tests/test_vector_index.py",
            ),
            Control(
                "a selection both shard sets share passes",
                _both(
                    _replace("ci.yml", _MACOS_RUN, f'{_MACOS_RUN} -m "not slow"'),
                    _replace(
                        "ci.yml",
                        "uv run pytest -n auto --dist worksteal -q tests --shard-list",
                        'uv run pytest -n auto --dist worksteal -q -m "not slow"'
                        " tests --shard-list",
                    ),
                ),
                fails=False,
            ),
            _plant(
                "test-macos only on pull requests (an untested main merge skips it)",
                _CI,
                " || (github.event_name == 'push' && github.ref == 'refs/heads/main')",
                "",
            ),
            _plant(
                "every macOS shard lists shard 1",
                _CI,
                "--leg test-macos --shard ${{ matrix.shard }}/4",
                "--leg test-macos --shard 1/4",
            ),
            _plant(
                "test-macos listing test-shard's plan",
                _CI,
                "--leg test-macos --shard ${{ matrix.shard }}/4",
                "--leg test-shard --shard ${{ matrix.shard }}/4",
            ),
            _plant(
                "a positional path only test-macos has",
                _CI,
                _MACOS_RUN,
                f"{_MACOS_RUN} tests/security",
            ),
            _plant(
                "PYTEST_ADDOPTS narrowing test-macos",
                _CI,
                "        run: " + _MACOS_RUN,
                "        env:\n          PYTEST_ADDOPTS: -k vector\n        run: " + _MACOS_RUN,
            ),
            _plant(
                "PYTEST_ADDOPTS in test-macos's job env",
                _CI,
                "      # has one.\n      PERSONALCLAW_SKIP_APP_BACKENDS",
                "      # has one.\n      PYTEST_ADDOPTS: \"-k 'not vector'\"\n"
                "      PERSONALCLAW_SKIP_APP_BACKENDS",
            ),
            _plant(
                "PYTEST_ADDOPTS written to $GITHUB_ENV by an earlier step",
                _CI,
                "          brew install tmux\n          tmux -V\n",
                "          brew install tmux\n"
                '          echo "PYTEST_ADDOPTS=-k vector" >> "$GITHUB_ENV"\n'
                "          tmux -V\n",
            ),
            _plant(
                "the push clause kept but made dead",
                _CI,
                "(github.event_name == 'push' && github.ref == 'refs/heads/main')) }}",
                "(github.event_name == 'push' && github.ref == 'refs/heads/main'))"
                " && github.event_name != 'push' }}",
            ),
            _plant(
                "a step-level if: on test-macos's pytest",
                _CI,
                "      - name: pytest (macOS shard ${{ matrix.shard }} of 4"
                " — two workers, no coverage)\n",
                "      - name: pytest (macOS shard ${{ matrix.shard }} of 4"
                " — two workers, no coverage)\n"
                "        if: github.event_name == 'pull_request'\n",
            ),
            _plant(
                "test-macos's pytest narrowed by working-directory",
                _CI,
                "      - name: pytest (macOS shard ${{ matrix.shard }} of 4"
                " — two workers, no coverage)\n",
                "      - name: pytest (macOS shard ${{ matrix.shard }} of 4"
                " — two workers, no coverage)\n"
                "        working-directory: tests/security\n",
            ),
            _plant(
                "test-macos excludes a shard",
                _CI,
                "        shard: [1, 2, 3, 4]\n    env:\n      # As `test-shard`",
                "        shard: [1, 2, 3, 4]\n        exclude:\n          - shard: 4\n"
                "    env:\n      # As `test-shard`",
            ),
        ),
    ),
    _toolchain_row(
        "every-gated-job-waits-on-tree-check",
        tree_check_gating_violations,
        (
            _plant(
                "a new job without needs",
                _CI,
                "jobs:\n  tree-check:\n",
                "jobs:\n  planted:\n    runs-on: ubuntu-24.04\n    steps:\n"
                "      - run: true\n  tree-check:\n",
            ),
            _plant(
                "a job that drops its needs", _CI, "  web:\n    needs: [tree-check]\n", "  web:\n"
            ),
            _plant(
                "an if: without !cancelled()",
                _CI,
                _TESTED_IF,
                "if: ${{ needs.tree-check.outputs.tested != 'true' }}",
            ),
            _plant(
                "tested == 'false' (a failed check would skip the job)",
                _CI,
                _TESTED_IF,
                "if: ${{ !cancelled() && needs.tree-check.outputs.tested == 'false' }}",
            ),
            Control(
                "an always-run job with no needs passes (changes, security-corpus)",
                _nothing,
                fails=False,
            ),
        ),
    ),
    _toolchain_row(
        "tree-check-runs-the-previous-commits-copy",
        tree_check_reads_before_violations,
        (
            _plant(
                "the checked-out copy",
                _CI,
                'python3 "$check" --workflow ci.yml',
                "python3 tools/ci/tree_tested.py --workflow ci.yml",
            ),
            _plant(
                "docker-single-container.yml runs the checked-out copy",
                _DOCKER,
                'python3 "$check" --workflow docker-single-container.yml',
                "python3 tools/ci/tree_tested.py --workflow docker-single-container.yml",
            ),
            _plant(
                "the copy read at the pushed commit",
                _CI,
                "BEFORE: ${{ github.event.before }}",
                "BEFORE: ${{ github.sha }}",
            ),
            _plant(
                "uv sync before the check",
                _CI,
                "          set -u\n",
                "          set -u\n          uv sync --locked\n",
            ),
            _plant(
                "the pushed tree checked out",
                _CI,
                "      - name: did a green pull-request run already test this tree\n",
                "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
                "      - name: did a green pull-request run already test this tree\n",
            ),
            _plant(
                "a write grant",
                _CI,
                "      actions: read         # the pull request's runs and their first attempts\n",
                "      actions: write\n",
            ),
        ),
    ),
    _toolchain_row(
        "no-macos-14-and-no-coverage-on-a-push",
        push_breadth_violations,
        (
            _plant(
                "a macos-14 leg in ci.yml",
                _CI,
                "    runs-on: macos-26\n",
                "    runs-on: macos-14\n",
            ),
            _plant(
                "a macos-14 leg in breadth.yml", _BREADTH, "runs-on: macos-26", "runs-on: macos-14"
            ),
            _plant(
                "--cov in ci.yml",
                _CI,
                _MACOS_RUN,
                "uv run pytest --cov=personalclaw -n 2 -q",
            ),
            _plant(
                "--cov on a gate shard",
                _CI,
                "uv run pytest -n auto --dist worksteal -q tests --shard-list",
                "uv run pytest -n auto --dist worksteal --cov=personalclaw -q tests --shard-list",
            ),
            _plant(
                "--cov personalclaw, the spaced form",
                _CI,
                "uv run pytest -n auto --dist worksteal -q tests --shard-list",
                "uv run pytest -n auto --dist worksteal --cov personalclaw -q tests --shard-list",
            ),
            _plant(
                "runs-on: [macos-14]",
                _CI,
                "    runs-on: macos-26\n",
                "    runs-on: [macos-14]\n",
            ),
            _plant(
                "runs-on: macos-14-xlarge",
                _CI,
                "    runs-on: macos-26\n",
                "    runs-on: macos-14-xlarge\n",
            ),
            _plant("runs-on: macOS-14", _CI, "    runs-on: macos-26\n", "    runs-on: macOS-14\n"),
            _plant(
                "runs-on as a {group, labels} mapping",
                _CI,
                "    runs-on: macos-26\n",
                "    runs-on:\n      group: g\n      labels: [macos-14]\n",
            ),
            _plant(
                "macos-14 as an expression's literal",
                _CI,
                "    runs-on: macos-26\n",
                "    runs-on: ${{ github.event_name == 'push' && 'macos-14' || 'macos-26' }}\n",
            ),
            _plant(
                "macos-14 through a matrix key",
                _CI,
                "    runs-on: macos-26\n    timeout-minutes: 50\n    strategy:\n"
                "      fail-fast: false\n      matrix:\n",
                "    runs-on: ${{ matrix.runner }}\n    timeout-minutes: 50\n    strategy:\n"
                "      fail-fast: false\n      matrix:\n        runner: [macos-14]\n",
            ),
            _plant(
                "macos-14 nested in a matrix object",
                _CI,
                "    runs-on: macos-26\n    timeout-minutes: 50\n    strategy:\n"
                "      fail-fast: false\n      matrix:\n",
                "    runs-on: ${{ matrix.platform.os }}\n    timeout-minutes: 50\n    strategy:\n"
                "      fail-fast: false\n      matrix:\n        platform: [{os: macos-14}]\n",
            ),
            _plant(
                "the root venv's python -m pytest with --cov",
                _CI,
                "uv run pytest tests/test_npm_advisory_floors.py ",
                ".venv/bin/python -m pytest tests/test_npm_advisory_floors.py --cov ",
            ),
            _plant(
                "a second pytest in a step, with --cov",
                _CI,
                "          uv run pytest tests/test_website_installer.py"
                " --junitxml=reports/junit-lint-installer.xml\n",
                "          uv run pytest tests/test_website_installer.py"
                " --junitxml=reports/junit-lint-installer.xml\n"
                "          uv run pytest tests/test_website_installer.py --cov=personalclaw\n",
            ),
            Control(
                "a pytest that names no --cov passes: the addopts measure nothing",
                _nothing,
                fails=False,
            ),
            Control("coverage in breadth.yml passes", _nothing, fails=False),
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
                "needs: [freshness, ubuntu, macos, rails-macos, arm,",
                "needs: [freshness, ubuntu, rails-macos, arm,",
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
        "breadth-non-reusable-legs-match-the-jobs",
        non_reusable_violations,
        (
            _plant(
                "a security leg that carries an earlier result",
                _BREADTH,
                "      - name: pytest (adversarial scanner corpus)\n",
                "      - name: carry\n"
                "        run: python3 tools/ci/breadth_evidence.py decide --key security-corpus\n"
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
            _plant("a never-reused leg renamed away", _BREADTH, "  audit:\n", "  audit-deps:\n"),
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
        "docker-single-container-checks-the-tree",
        docker_tree_check_violations,
        (
            _plant(
                "a build job without needs: tree-check",
                _DOCKER,
                "    needs: [tree-check]\n" f"    {_TESTED_IF}\n",
                "",
            ),
            _plant(
                "a main run that can be cancelled",
                _DOCKER,
                "  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}\n",
                "  cancel-in-progress: true\n",
            ),
        ),
    ),
    _toolchain_row(
        "breadth-shards-record-what-they-ran",
        breadth_shards_violations,
        (
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
                'decide --key "ubuntu-24.04-3.13/${{ matrix.shard }}"',
                'decide --key "macos-26-3.12/${{ matrix.shard }}"',
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
    PER_TEST_TEMPORARY_FOLDERS,
    NO_TRUSTED_CHECKOUT_OF_CANDIDATE_CODE,
    NO_CANDIDATE_JOB_HOLDS_A_WRITE_OR_A_SECRET,
    ONLY_THE_RELEASE_CONTROLLER_PUBLISHES,
    _toolchain_row(
        "every job that runs steps has its own timeout-minutes",
        job_timeout_violations,
        (
            _plant(
                "a job in a cancelling group without one",
                _CI,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n",
            ),
            _plant(
                "a job in a workflow with no concurrency group without one",
                ".github/workflows/release.yml",
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
                _CI,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 0\n",
            ),
            _plant(
                "a job whose cap is negative",
                _CI,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: -5\n",
            ),
            _plant(
                "a job whose cap is a fraction",
                _CI,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5.5\n",
            ),
            _plant(
                "a job whose cap is a string",
                _CI,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: '5'\n",
            ),
            _plant(
                "a job whose cap is a boolean",
                _CI,
                "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
                "    runs-on: ubuntu-24.04\n    timeout-minutes: true\n",
            ),
            Control(
                "a job that calls a reusable workflow, without one, passes",
                _plant(
                    "a reusable call",
                    _CI,
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
                "a suite shard with no shard list",
                _CI,
                '-q tests --shard-list "$RUNNER_TEMP/shard.txt"'
                " --junitxml=reports/junit-test-shard-",
                "-q --junitxml=reports/junit-test-shard-",
            ),
            _plant(
                "a shard listing another N",
                _CI,
                "--leg test-shard --shard ${{ matrix.shard }}/4",
                "--leg test-shard --shard ${{ matrix.shard }}/3",
            ),
            _plant(
                "the plan naming a leg in another N",
                _CI,
                "--leg test-shard:ci.yml:junit-test-shard-:4",
                "--leg test-shard:ci.yml:junit-test-shard-:3",
            ),
            _plant(
                "a plan nobody checks",
                _CI,
                "      - name: every collectable file is in exactly one shard of each leg\n"
                "        run: python3 -m tools.ci shards check"
                ' --plan "$RUNNER_TEMP/shard-plan/plan.json"\n',
                "",
            ),
            _plant(
                "a shard that does not wait on the plan",
                _CI,
                "    needs: [tree-check, shard-plan]\n",
                "    needs: [tree-check]\n",
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
            _plant(
                "a suite shard with no -n",
                _CI,
                "uv run pytest -n auto --dist worksteal -q tests --shard-list",
                "uv run pytest -q tests --shard-list",
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
                "a pytest that names no path, so collects testpaths",
                _CI,
                "uv run pytest tests/test_npm_advisory_floors.py ",
                "uv run pytest ",
            ),
            _plant(
                "a pytest that names tests/ itself",
                _CI,
                "uv run pytest tests/test_npm_advisory_floors.py ",
                "uv run pytest tests/ ",
            ),
            _plant(
                "the root venv's python -m pytest of the suite",
                _CI,
                "uv run pytest tests/test_npm_advisory_floors.py ",
                ".venv/bin/python -m pytest ",
            ),
            Control(
                "a suite shard with -n4 passes",
                _replace(
                    "ci.yml",
                    "uv run pytest -n auto --dist worksteal -q tests --shard-list",
                    "uv run pytest -n4 --dist worksteal -q tests --shard-list",
                ),
                fails=False,
            ),
            Control(
                "a suite shard with --numprocesses=4 passes",
                _replace(
                    "ci.yml",
                    "uv run pytest -n auto --dist worksteal -q tests --shard-list",
                    "uv run pytest --numprocesses=4 --dist worksteal -q tests --shard-list",
                ),
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
                "--no-cov on a single-file step",
                _CI,
                "tests/test_npm_advisory_floors.py --junitxml",
                "tests/test_npm_advisory_floors.py --no-cov --junitxml",
            ),
            _plant(
                "--no-cov on a suite shard",
                _CI,
                "uv run pytest -n auto --dist worksteal -q tests --shard-list",
                "uv run pytest -n auto --dist worksteal --no-cov -q tests --shard-list",
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


def run_row(row: Row, subtests, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """One row: its scaffold holds, each control fails or passes as it says, and the tree
    holds. Every contract table's test is this body."""
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


def _pr_context(*, head_repo: str, action: str, merged: bool = False) -> dict[str, Any]:
    """A `pull_request` event's context: a pull request's run (`synchronize`), or its close."""
    merged_close = action == "closed" and merged
    head = {"ref": _CLOSE_HEAD, "sha": "head2222", "repo": {"full_name": head_repo}}
    return {
        "github": {
            "repository": _CLOSE_REPO,
            "event_name": "pull_request",
            # GitHub's "Events that trigger workflows", `pull_request`: when a pull request closes
            # because it merged, the ref is the branch it merged into and the sha its merge commit.
            "ref": "refs/heads/main" if merged_close else "refs/pull/42/merge",
            "ref_name": "main" if merged_close else "42/merge",
            "sha": _MERGE_SHA if merged_close else "testmerge1111",
            "head_ref": _CLOSE_HEAD,
            "base_ref": "main",
            "event": {
                "action": action,
                "number": 42,
                "pull_request": {"number": 42, "merged": merged, "head": head},
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
    """One term: a property, ``true``/``false``, or a property compared with a quoted literal.
    Anything else raises, so the row reports an expression it cannot read instead of guessing."""
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


def _evaluate(template: str, context: dict[str, Any]) -> str:
    """A group name with every ``${{ }}`` evaluated, lower-cased: GitHub compares group names
    without case."""

    def one(match: re.Match[str]) -> str:
        value = _expression(context, match.group(1))
        return ("true" if value else "false") if isinstance(value, bool) else str(value)

    return _EXPRESSION.sub(one, template).lower()


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
    came from this repository or a fork. No job of it joins a push to main's group."""
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


_CANCEL_CI_GROUP = (
    "      group: ci-${{ github.event.pull_request.head.repo.full_name || github.repository }}"
    "-${{ github.head_ref }}\n"
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
            "ci.yml's own key copied, which a merged close evaluates to main's commit",
            _CANCELLER_PATH,
            _CANCEL_CI_GROUP,
            "      group: ci-${{ github.ref == 'refs/heads/main' && github.sha"
            " || github.event.pull_request.head.repo.full_name || github.repository }}"
            "-${{ github.head_ref || github.ref_name }}\n",
            expect="once it is merged",
        ),
        _plant(
            "ci.yml's own cancel-in-progress copied, which is false on a merged close",
            _CANCELLER_PATH,
            "      cancel-in-progress: true\n",
            "      cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}\n",
            expect="ci.yml: no",
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
            _CANCEL_CI_GROUP,
            "      group: ci-${{ github.repository }}-${{ github.head_ref }}\n",
            expect="from a fork",
        ),
        _plant(
            "a group that joins the merge's push to main",
            _CANCELLER_PATH,
            _CANCEL_PINS_GROUP,
            "      group: ci-${{ github.sha }}-${{ github.ref_name }}\n",
            expect="cancel main's check",
        ),
        _plant(
            "an expression the row cannot evaluate",
            _CANCELLER_PATH,
            _CANCEL_CI_GROUP,
            "      group: ci-${{ format('{0}', github.head_ref) }}\n",
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


# ── Every rail lives in tests/rails ─────────────────────────────────────────────────────────
#
# A test that checks the source tree runs once per CI run, in the rails job, and never in a
# suite shard (tools/ci/shards.py leaves tests/rails out of every plan). tools/ci/rails_census.py
# decides which modules are rails from their source. A security-marked module stays where it is,
# and a rail that still holds behaviour tests may wait outside while the pending-split list
# (tools/ci/rails-pending-split.txt) names it: a list that only shrinks.

_A_RAIL = "import source_index\n\n\ndef test_it():\n    assert source_index.package_files()\n"
_NOT_A_RAIL = "def test_it():\n    assert 1 + 1 == 2\n"
_MIXED = (
    "import urllib.request\n\nimport source_index\n\n\ndef test_tree():\n"
    "    assert source_index.package_files()\n\n\ndef test_wire():\n"
    "    urllib.request.urlopen('https://example.com')\n"
)


def _rails_scaffold(root: Path) -> None:
    """A tree where the rule holds: a rail in tests/rails, a suite module beside it, a
    security-marked rail left in place, and a mixed module that the pending list names."""
    _write(root, "tests/rails/test_a_rail.py", _A_RAIL)
    _write(root, "tests/test_behaviour.py", _NOT_A_RAIL)
    _write(
        root,
        "tests/security/test_guarded.py",
        "import pytest\n\npytestmark = pytest.mark.security\n\n" + _A_RAIL,
    )
    _write(root, "tests/test_waits.py", _MIXED)
    _write(root, rails_census.PENDING_SPLIT, "# pending\ntests/test_waits.py\n")


EVERY_RAIL_LIVES_IN_TESTS_RAILS = Row(
    name="every rail lives in tests/rails, or waits on the pending-split list",
    check=rails_census.violations,
    scaffold=_rails_scaffold,
    controls=(
        Control(
            "a rail outside tests/rails that the list does not name fails",
            lambda root: _write(root, "tests/test_new_rail.py", _A_RAIL),
        ),
        Control(
            "a mixed module the list does not name fails",
            lambda root: _write(root, "tests/test_new_mixed.py", _MIXED),
        ),
        Control(
            "a suite module under tests/rails fails",
            lambda root: _write(root, "tests/rails/test_behaviour_here.py", _NOT_A_RAIL),
        ),
        Control(
            "a listed module that is no longer a rail fails: the list only shrinks",
            lambda root: _write(root, "tests/test_waits.py", _NOT_A_RAIL),
        ),
        Control(
            "a listed module that is gone fails",
            lambda root: (root / "tests/test_waits.py").unlink(),
        ),
        Control(
            "a list longer than its ceiling fails",
            lambda root: _write(
                root,
                rails_census.PENDING_SPLIT,
                "\n".join(f"tests/test_w{i}.py" for i in range(rails_census.PENDING_CEILING + 1))
                + "\n",
            ),
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
            "the main-push check back in ci.yml",
            ".github/workflows/ci.yml",
            "  rails:\n",
            "  provenance-again:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: python3 "
            "scripts/check_commit_provenance.py --mode main --range x..y\n\n  rails:\n",
            expect="ci.yml runs the main-push check",
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


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.name)
def test_the_ci_contract_holds_and_each_check_can_fail(
    row: Row, subtests, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    run_row(row, subtests, tmp_path, request)
