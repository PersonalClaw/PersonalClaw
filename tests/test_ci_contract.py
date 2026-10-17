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

import pytest
import security_census
import yaml

REPO = Path(__file__).resolve().parents[1]

#: An exact release, ``X.Y.Z``.
_EXACT = re.compile(r"\d+\.\d+\.\d+")
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[^\s@]+:[^\s@:]+@sha256:[0-9a-f]{64}")

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


def _comment_above(lines: list[str], index: int) -> str:
    """The run of ``#`` comment lines directly above ``lines[index]``."""
    block: list[str] = []
    i = index - 1
    while i >= 0 and lines[i].lstrip().startswith("#"):
        block.insert(0, lines[i])
        i -= 1
    return "\n".join(block)


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


def action_sha_violations(tree: Tree) -> list[str]:
    """Every ``uses:`` names a 40-character commit SHA, with its release as a comment."""
    bad: list[str] = []
    found = 0
    for rel, text in tree.items():
        if not rel.startswith(".github/workflows/"):
            continue
        for line in text.splitlines():
            match = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)(.*)$", line)
            if not match or match.group(1).startswith("./"):
                continue
            found += 1
            ref, rest = match.groups()
            _, _, pin = ref.partition("@")
            if not _SHA.fullmatch(pin):
                bad.append(f"{rel}: {ref} is not pinned by commit SHA")
            elif not re.fullmatch(r"\s+#\s*v\d+(?:\.\d+)*\s*", rest):
                bad.append(f"{rel}: {ref} has no `# vX.Y.Z` release comment")
    if not found:
        bad.append("found no `uses:` line, so this row read nothing")
    return bad


def image_digest_violations(tree: Tree) -> list[str]:
    """Every container image is a tag with its digest, never ``latest``, and says its
    architecture: a workflow container names the platform manifest it pins, and an image
    base built for both platforms lists each platform's manifest digest."""
    bad: list[str] = []
    found = 0

    def judge(where: str, ref: str, comment: str, platforms: tuple[str, ...]) -> None:
        if not _DIGEST.fullmatch(ref):
            bad.append(f"{where}: {ref} is not a tag pinned by digest")
        elif ref.split("@", 1)[0].rsplit(":", 1)[1] == "latest":
            bad.append(f"{where}: {ref} names the moving tag `latest`")
        for platform in platforms:
            if not re.search(rf"{platform}\b.*sha256:[0-9a-f]{{64}}", comment, flags=re.S):
                bad.append(f"{where}: {ref} does not say its {platform} manifest digest")

    for rel, text in tree.items():
        if not rel.startswith(".github/workflows/"):
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"\s+(?:container|image):\s*(\S+)\s*$", line)
            if match:
                found += 1
                judge(rel, match.group(1), _comment_above(lines, index), ("linux/amd64",))
    for rel, text in _dockerfiles(tree):
        lines = text.splitlines()
        args: dict[str, tuple[str, int]] = {}
        stages: set[str] = set()
        for index, line in enumerate(lines):
            arg = re.match(r"ARG (\w+)=(\S+)$", line)
            if arg:
                args[arg.group(1)] = (arg.group(2), index)
            words = [w for w in line.split() if not w.startswith("--")]
            if not words or words[0] != "FROM":
                continue
            ref, at = words[1], index
            variable = re.fullmatch(r"\$\{?(\w+)\}?", ref)
            if variable and variable.group(1) in args:
                ref, at = args[variable.group(1)]
            if len(words) > 3 and words[2].upper() == "AS":
                stages.add(words[3])
            if ref in stages and ref != (words[3] if len(words) > 3 else ""):
                continue
            found += 1
            judge(rel, ref, _comment_above(lines, at), ("linux/amd64", "linux/arm64"))
    if not found:
        bad.append("found no container image, so this row read nothing")
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


def _plant(name: str, rel: str, old: str, new: str) -> Control:
    """A violation planted by one edit of one file; a plant whose text is gone fails loudly."""

    def plant(root: Path) -> None:
        text = (root / rel).read_text(encoding="utf-8")
        assert old in text, f"the plant {name!r} found no {old!r} in {rel}"
        _write(root, rel, text.replace(old, new, 1))

    return Control(name, plant)


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
    _toolchain_row(
        "action-sha",
        action_sha_violations,
        (
            _plant(
                "an action by tag",
                ".github/workflows/dco.yml",
                "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0",
                "actions/github-script@v9",
            ),
            _plant(
                "an action by branch",
                ".github/workflows/release.yml",
                "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2",
                "pypa/gh-action-pypi-publish@release/v1",
            ),
            _plant("a SHA with no release comment", ".github/workflows/nix.yml", " # v31.11.2", ""),
        ),
    ),
    _toolchain_row(
        "image-digest",
        image_digest_violations,
        (
            _plant(
                "a workflow container by tag alone",
                ".github/workflows/full.yml",
                _UBUNTU,
                "ubuntu:26.04",
            ),
            _plant(
                "the moving tag latest",
                ".github/workflows/full.yml",
                "container: ubuntu:26.04@",
                "container: ubuntu:latest@",
            ),
            _plant(
                "an image base by tag alone",
                "deploy/docker/Dockerfile.web",
                _NGINX,
                "nginx:1.27-alpine",
            ),
            _plant(
                "a base ARG by tag alone",
                "deploy/docker/Dockerfile.backend",
                _PYTHON,
                "python:3.13.16-slim",
            ),
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
                ".github/workflows/full.yml",
                "          . /etc/os-release\n",
                '          echo "$ImageOS $ImageVersion"\n',
            ),
        ),
    ),
    PER_TEST_TEMPORARY_FOLDERS,
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
            else:
                assert found == [], "\n".join(found)
    with subtests.test("the tree"):
        violations = row.check(REPO)
        assert violations == [], "\n".join(violations)
    if row.on_tree is not None:
        row.on_tree(request)
