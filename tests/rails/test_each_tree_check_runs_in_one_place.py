"""Each whole-tree check runs on the real tree in one place: the job that owns it (#3929).

A whole-tree check walks the repository and judges it: the six gates ``scripts/gate_report.py``
aggregates, ``harness validate`` and ``harness scan``, the asset-licence census. CI runs each in
the job that owns it. A test in the suite that runs the same check on the real tree again is a
**copy**: every suite leg pays for it, and its red shows up in a shard rather than in the job
that owns the check. The suite keeps the tests that prove a check works, on stubs or a small
tree of the test's own.

Two shapes of copy are found here, both read from source and never imported:

- **A call.** A test that calls one of a check's real-tree entry points (:data:`CHECKS`), from its
  own body, a module-level helper or a fixture of its module, without replacing every source the
  check reads the tree through. A test that ``monkeypatch.setattr``\\ s the source (to a stub, or
  to a tree it made) runs the check on that, not on the repository. Helpers in another module are
  not followed.
- **A file.** A test file that a workflow job runs by name while the suite's shards also collect
  it (``tools/ci/shards.py``), so it runs twice in one run. A file the shards leave to its job
  (``shards.JOB_OWNED``) is no copy, and no reader of ``docs/`` in the suite either; a row holds
  that its job's catalogue command still runs it by name.

Each owner, of a check or of a file the shards leave out, is a job of ``tools/ci/catalogue.toml``,
the one definition the tier workflows run, so an owner names a job, never a workflow's step.

:data:`KNOWN_COPIES` and :data:`KNOWN_FILE_COPIES` list the copies that are still there, each
with the job that owns the check. They only shrink: a copy not on them fails, and so does an
entry whose copy is gone, so each removal takes its entry with it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

from tools.ci import catalogue, shards
from tools.ci.tree_checks import CHECKS, module_copies

#: The calls that still run a check on the real tree in the suite. None is left: the gate
#: aggregate's tests run every gate on stubs, the render tests render a small tree of their
#: own, and each real-tree check is its owning job's (``scripts/gate_report.py`` in
#: rails-checks, ``scripts/check_asset_licenses.py`` in static, ``harness validate`` and the
#: whole-tree ``harness scan`` in harness) or a rail's, for what that job does not check.
#: ``(check, "path::test")``; a new copy fails.
KNOWN_COPIES: frozenset[tuple[str, str]] = frozenset()

#: The suite's modules and helpers that read ``docs/``: prose checked against the tree. Each is a
#: whole-tree read the suite still holds, so the suite's inputs are the docs too (#3945). These
#: two are security-marked, so the rails census keeps them where they are, and they stay. The
#: third, ``tests/security/test_scanner_adversarial.py``, is the ``security`` job's
#: (``shards.JOB_OWNED``), so it is no longer the suite's.
KNOWN_DOCS_READERS: frozenset[str] = frozenset(
    {
        "tests/test_architecture_docs_match_the_tree.py",
        "tests/test_browse_cdp_preflight.py",
    }
)

#: The test files a job runs by name while the shards collect them too, until the shards leave
#: each to its job (``shards.JOB_OWNED``). ``(job, file)``. None is left: the security corpus,
#: the last, runs in the ``security`` job alone.
KNOWN_FILE_COPIES: frozenset[tuple[str, str]] = frozenset()

_NO_BROWSER = (
    "browse-live runs it on Linux with a browser; the shards' run is its only run on macOS and "
    "its only run without one, where the live cases skip and the string and structure tests run"
)

#: Files a job runs by name that the shards keep collecting on purpose, each with why.
#: ``(job, file): reason``, the job a catalogue id or ``<workflow> <job>``. An exemption whose
#: file is no longer a copy fails, so the list shrinks with the tree.
EXEMPT_FILE_COPIES: Mapping[tuple[str, str], str] = {
    ("browse-live", "tests/test_browse_behavioural_proof_is_reachable.py"): _NO_BROWSER,
    ("browse-live", "tests/test_browse_cdp_live.py"): (
        f"{_NO_BROWSER}; it is security-marked, so no leg selects it out"
    ),
    ("browse-live", "tests/test_browse_safety_script.py"): (
        f"{_NO_BROWSER}; it is security-marked, so no leg selects it out"
    ),
}


# ── reading a module ──────────────────────────────────────────────────────────────────────────


def _suite_modules(root: Path) -> Iterator[tuple[str, str]]:
    tests = root / "tests"
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{shards.RAILS}/") or "/fixtures/" in rel:
            continue
        if not path.name.startswith("test"):
            continue
        yield rel, path.read_text(encoding="utf-8")


def copies(root: Path) -> set[tuple[str, str]]:
    """Every call copy in the suite under *root*. The rails are not the suite: they run once."""
    found: set[tuple[str, str]] = set()
    for rel, source in _suite_modules(root):
        found |= module_copies(rel, source)
    return found


# ── the suite's readers of docs/ ─────────────────────────────────────────────────────────────

_DOCS = re.compile(r"^docs(/|$)")


def _rootish(node: ast.AST) -> bool:
    """A path that names the repository: a root name, a root helper's call, ``parents[n]``."""
    if isinstance(node, ast.Name):
        return "ROOT" in node.id.upper() or "REPO" in node.id.upper()
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        return "root" in name.lower() or "repo" in name.lower()
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Attribute) and node.value.attr == "parents"
    return False


def reads_docs(source: str) -> bool:
    """Whether a module joins ``docs/`` onto the repository: ``<root> / "docs/..."``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                if _DOCS.match(right.value) and _rootish(node.left):
                    return True
    return False


def docs_readers(root: Path, owned: Mapping[str, str] | None = None) -> set[str]:
    """Every suite module or helper under *root*'s ``tests/`` that reads ``docs/``. Prose checked
    against the tree is a whole-tree read: while the suite holds one, the suite's inputs are the
    docs too, and a docs-only change runs it (#3945). A file the shards leave to the job that
    runs it by name (``shards.JOB_OWNED``, or *owned*) is that job's, not the suite's."""
    skipped = shards.JOB_OWNED if owned is None else owned
    found: set[str] = set()
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{shards.RAILS}/") or "/fixtures/" in rel or "/corpus/" in rel:
            continue
        if rel in skipped:
            continue
        if reads_docs(path.read_text(encoding="utf-8")):
            found.add(rel)
    return found


# ── reading the workflows ─────────────────────────────────────────────────────────────────────

_PYTEST = re.compile(r"\bpytest\b")


def _named_paths(run: str) -> Iterable[str]:
    for line in run.splitlines():
        if line.lstrip().startswith("#") or not _PYTEST.search(line):
            continue
        for word in re.split(r"[\s\\]+", line):
            word = word.strip("'\"")
            if word.startswith("tests/") and not word.startswith("tests/rails"):
                yield word.rstrip("/")


def file_copies(root: Path, owned: Mapping[str, str] | None = None) -> set[tuple[str, str]]:
    """``("workflow job", file)`` for each test file a job runs by name that a shard collects.
    A file the shards leave to its job (``shards.JOB_OWNED``, or *owned*) is no copy."""
    universe = set(shards.universe(root, owned))
    return {(job, rel) for job, rel in named_runs(root) if rel in universe}


def named_runs(root: Path) -> set[tuple[str, str]]:
    """``(job, file)`` for each test file a job's pytest step names, outside the shards' own
    steps: a workflow job's as ``<workflow> <job>``, and a catalogue job's, which the tier
    workflows run through run-job, as its id. A named folder counts as each ``test_*.py`` under
    it."""
    found: set[tuple[str, str]] = set()
    if (root / catalogue.CATALOGUE).is_file():
        for job_id, named in catalogue_runs(catalogue.read(root)):
            if named.startswith("tests/rails"):
                continue  # the rails, which the rails job runs and no shard collects
            target = root / named
            files = (
                [p.relative_to(root).as_posix() for p in sorted(target.rglob("test_*.py"))]
                if target.is_dir()
                else [named]
            )
            found |= {(job_id, rel) for rel in files}
    for workflow in sorted((root / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
        for job, spec in (data.get("jobs") or {}).items():
            # A job that runs the suite's own shards is the suite, not a second place.
            for step in (spec or {}).get("steps") or []:
                run = str((step or {}).get("run") or "")
                if "--shard-list" in run:
                    continue
                # A pytest step may name its paths over several lines of a folded block.
                joined = run if _PYTEST.search(run) else ""
                for named in _named_paths(joined.replace("\n", " ")):
                    target = root / named
                    files = (
                        [p.relative_to(root).as_posix() for p in sorted(target.rglob("test_*.py"))]
                        if target.is_dir()
                        else [named]
                    )
                    for rel in files:
                        found.add((f"{workflow.name} {job}", rel))
    return found


# ── the rails ─────────────────────────────────────────────────────────────────────────────────


def _diff(found: set[tuple[str, str]], known: frozenset[tuple[str, str]], what: str) -> list[str]:
    lines = [f"a new {what}: {a} {b}" for a, b in sorted(found - known)]
    lines += [f"listed, but no longer a {what}: {a} {b}" for a, b in sorted(known - found)]
    return lines


@pytest.mark.timeout(80)
def test_no_whole_tree_check_gains_a_copy_in_the_suite() -> None:
    problems = _diff(copies(REPO), KNOWN_COPIES, "real-tree run in the suite")
    assert not problems, (
        "a whole-tree check runs on the real tree in the job that owns it, and the suite tests it "
        "on stubs or a tree of the test's own:\n  " + "\n  ".join(problems)
    )


def exemption_problems(
    found: set[tuple[str, str]], exempt: Mapping[tuple[str, str], str]
) -> list[str]:
    """Each exemption with no reason, or whose file is no longer run in its job and the shards."""
    problems = [
        f"exempt with no reason: {a} {b}"
        for (a, b), why in sorted(exempt.items())
        if not why.strip()
    ]
    problems += [
        f"exempt, but no longer a file copy: {a} {b}" for a, b in sorted(set(exempt) - found)
    ]
    return problems


def test_no_test_file_runs_in_a_job_and_in_the_shards() -> None:
    found = file_copies(REPO)
    problems = _diff(
        found - set(EXEMPT_FILE_COPIES), KNOWN_FILE_COPIES, "file run in a job and the shards"
    )
    problems += exemption_problems(found, EXEMPT_FILE_COPIES)
    assert not problems, "\n  ".join(problems)


def test_an_exemption_naming_security_marked_says_so_only_where_it_is() -> None:
    """A reason that calls its file security-marked is checked against the file's marker."""
    for (_job, rel), why in EXEMPT_FILE_COPIES.items():
        marked = "pytest.mark.security" in (REPO / rel).read_text(encoding="utf-8")
        assert ("security-marked" in why) is marked, (rel, why)


def catalogue_runs(cat: catalogue.Catalogue) -> set[tuple[str, str]]:
    """``(catalogue job, path)`` for each path a job's pytest step names: a file, or a folder
    (each file under it counts, by :func:`_under`)."""
    found: set[tuple[str, str]] = set()
    for job in cat.jobs:
        for step in job.command:
            args = catalogue.pytest_args(step.argv) or []
            found |= {(job.id, word.rstrip("/")) for word in args if word.startswith("tests/")}
    return found


def _under(rel: str, named: str) -> bool:
    return rel == named or rel.startswith(named + "/")


def job_owned_problems(
    root: Path, owned: Mapping[str, str], cat: catalogue.Catalogue | None = None
) -> list[str]:
    """Each file the shards leave to a job that is no catalogue job, whose command no longer
    runs it by name, or that is not a test file pytest would collect: any of them, and it would
    run nowhere."""
    cat = catalogue.read(root) if cat is None else cat
    ids = {job.id for job in cat.jobs}
    runs = catalogue_runs(cat)
    collectable = set(shards.universe(root, {}))
    problems = [
        f"{rel} is left to {job}, which is no job of the catalogue"
        for rel, job in sorted(owned.items())
        if job not in ids
    ]
    problems += [
        f"{rel} is left to {job}, which no longer runs it by name"
        for rel, job in sorted(owned.items())
        if job in ids and not any(j == job and _under(rel, named) for j, named in runs)
    ]
    problems += [
        f"{rel} is left to {job}, but pytest would not collect it"
        for rel, job in sorted(owned.items())
        if rel not in collectable
    ]
    return problems


def test_every_file_the_shards_leave_to_a_job_still_runs_there() -> None:
    """The other half of leaving a file out of the shards: its owner runs it, by name, once."""
    assert shards.JOB_OWNED, "nothing is left to a job, so this row checks nothing"
    problems = job_owned_problems(REPO, shards.JOB_OWNED)
    assert not problems, "\n  ".join(problems)


def test_the_maintainer_docs_name_each_job_the_shards_leave_a_file_to() -> None:
    """``docs/maintainers/ci.md`` says what the shard plan leaves out: each job of
    ``shards.JOB_OWNED``, beside the files it runs."""
    docs = (REPO / "docs" / "maintainers" / "ci.md").read_text(encoding="utf-8")
    [said] = [p for p in docs.split("\n\n") if p.startswith("The plan leaves out `tests/rails`")]
    jobs = sorted(set(shards.JOB_OWNED.values()))
    assert jobs, "nothing is left to a job, so this row checks nothing"
    missing = [job for job in jobs if f"in `{job}`" not in said]
    assert not missing, f"ci.md's paragraph on shards.JOB_OWNED names no file in {missing}"


@pytest.mark.timeout(70)
def test_no_suite_module_starts_reading_docs() -> None:
    problems = [
        f"a new reader of docs/ in the suite: {m}"
        for m in sorted(docs_readers(REPO) - KNOWN_DOCS_READERS)
    ]
    problems += [
        f"listed, but no longer reads docs/: {m}"
        for m in sorted(KNOWN_DOCS_READERS - docs_readers(REPO))
    ]
    assert (
        not problems
    ), "a check of prose against the tree is a rail (tests/rails):\n  " + "\n  ".join(problems)


def test_every_known_copy_names_a_check_and_its_owner() -> None:
    for check, _test in KNOWN_COPIES:
        assert check in CHECKS, check
        assert CHECKS[check].owner


def owner_problems(checks: Mapping[str, object], cat: catalogue.Catalogue) -> list[str]:
    """Each check whose owner is not ``"<catalogue job>: <what it runs>"``, or whose job's
    command runs no path the owner names (a ``.py`` path, run itself or under a folder a pytest
    step names)."""
    ids = {job.id: job for job in cat.jobs}
    problems: list[str] = []
    for name, check in sorted(checks.items()):
        owner = str(getattr(check, "owner", ""))
        job_id, colon, what = owner.partition(": ")
        if not colon or job_id not in ids:
            problems.append(f"{name}'s owner {owner!r} names no job of the catalogue")
            continue
        words = {word for step in ids[job_id].command for word in step.argv}
        folders = {named for j, named in catalogue_runs(cat) if j == job_id}
        for path in (w.strip(",()") for w in what.split()):
            if path.endswith(".py") and not (
                path in words or any(_under(path, folder) for folder in folders)
            ):
                problems.append(f"{name}'s owner {job_id} runs no {path}")
    return problems


def test_every_check_is_owned_by_a_catalogue_job_that_runs_it() -> None:
    problems = owner_problems(CHECKS, catalogue.read(REPO))
    assert not problems, "\n  ".join(problems)


@pytest.mark.parametrize(
    ("owner", "fault"),
    [
        ("ci.yml rails-checks: python scripts/gate_report.py", "names no job of the catalogue"),
        ("rails-checks python scripts/gate_report.py", "names no job of the catalogue"),
        ("lint: python scripts/gate_report.py", "names no job of the catalogue"),
        ("static: python scripts/gate_report.py", "static runs no scripts/gate_report.py"),
        ("rails-checks: python scripts/gate_report.py", None),
        ("rails: tests/rails/test_licence_governance_tree.py", None),
        ("static: python -m harness scan, over the whole tree", None),
    ],
)
def test_a_planted_owner_of_one_shape_or_of_a_job_that_runs_nothing_fails(
    owner: str, fault: str | None
) -> None:
    """A label of ``ci.yml``'s shape (``ci.yml <job>``, or a ci.yml job name the catalogue does
    not have) fails, and so does a catalogue job that does not run the path; the real owners'
    forms pass."""
    problems = owner_problems({"planted": type("C", (), {"owner": owner})()}, catalogue.read(REPO))
    if fault is None:
        assert not problems, problems
    else:
        assert len(problems) == 1 and fault in problems[0], problems


@pytest.mark.parametrize(
    ("owned", "fault"),
    [
        ({"tests/test_npm_advisory_floors.py": "ci.yml rails-checks"}, "no job of the catalogue"),
        ({"tests/test_npm_advisory_floors.py": "static"}, "no longer runs it by name"),
        ({"tests/security/test_app_signature.py": "security"}, None),
    ],
)
def test_a_planted_file_owner_of_one_shape_or_of_a_job_that_does_not_run_it_fails(
    owned: dict[str, str], fault: str | None
) -> None:
    """The same for a file the shards leave out: ``ci.yml``'s label fails, and so does a
    catalogue job that does not run it; a folder its job runs (``tests/security``) holds it."""
    problems = job_owned_problems(REPO, owned)
    if fault is None:
        assert not problems, problems
    else:
        assert len(problems) == 1 and fault in problems[0], problems


def test_the_census_reads_the_real_tree() -> None:
    """Vacuity: the scan reads the suite, and the gate aggregate's own tests are in it."""
    modules = dict(_suite_modules(REPO))
    assert len(modules) > 1000, len(modules)
    assert "tests/test_gate_report.py" in modules
    assert shards.universe(REPO), "the shard universe is empty"


# ── the planted copies ────────────────────────────────────────────────────────────────────────

_REAL = """\
from scripts.gate_report import run_all_gates


def test_planted():
    assert run_all_gates()
"""

_HELPER = """\
from scripts import gate_report


def _all():
    return gate_report.main()


def test_planted():
    assert _all() == 0
"""

_FIXTURE = """\
import pytest
from scripts import generate_inert_surface_baseline as inert


@pytest.fixture
def inventory():
    return inert.build_inventory()


def test_planted(inventory):
    assert inventory
"""

_LICENCE_SWEEP = """\
from licence_governance_shared import _ROOT, declaration_sites


def _sites():
    return declaration_sites(_ROOT)


def test_planted():
    assert _sites()
"""

_SCANNER = """\
from harness import scanner


def _repo_root():
    return None


def test_planted():
    root = _repo_root()
    assert not scanner.scan(_tracked_files(root), root)
"""

_HARNESS_SCAN = """\
from harness import cli


def test_planted():
    assert cli.main(["scan"]) == 0
"""

_RAILS_CENSUS = """\
from pathlib import Path

from tools.ci import rails_census

REPO = Path(__file__).resolve().parents[1]


def test_planted():
    assert rails_census.leg_violations(REPO) == []
"""


@pytest.mark.parametrize(
    "source, check",
    [
        (_REAL, "gate-report"),
        (_HELPER, "gate-report"),
        (_FIXTURE, "inert-surface"),
        (_HARNESS_SCAN, "harness"),
        (_LICENCE_SWEEP, "licence-sweep"),
        (_SCANNER, "harness-scanner"),
        (_RAILS_CENSUS, "rails-census"),
    ],
    ids=[
        "a direct call",
        "through a helper",
        "through a fixture",
        "harness scan",
        "the licence sweep on the root",
        "the scanner on the tracked files",
        "the rails census's legs on the root",
    ],
)
def test_a_planted_real_tree_run_is_a_copy(source, check) -> None:
    assert module_copies("tests/test_planted.py", source) == {
        (check, "tests/test_planted.py::test_planted")
    }


_STUBBED_GATES = """\
from scripts import gate_report
from scripts import generate_config_baseline as config_gen
from scripts import generate_docs_lint_baseline as docs_gen
from scripts import generate_inert_surface_baseline as inert_gen
from scripts import generate_structural_baseline as structural_gen


def _stub(monkeypatch):
    monkeypatch.setattr(config_gen, "build_baseline", lambda: "[]")
    monkeypatch.setattr(inert_gen, "build_inventory", dict)
    monkeypatch.setattr(docs_gen, "build_inventory", dict)
    monkeypatch.setattr(structural_gen, "build_inventory", dict)
    monkeypatch.setattr(structural_gen, "vacuity_failures", lambda r=None: [])


def test_planted(monkeypatch):
    _stub(monkeypatch)
    assert gate_report.run_all_gates()
"""


def test_a_run_with_every_source_stubbed_is_not_a_copy() -> None:
    assert module_copies("tests/test_planted.py", _STUBBED_GATES) == set()


def test_one_source_left_real_is_still_a_copy() -> None:
    source = _STUBBED_GATES.replace(
        '    monkeypatch.setattr(structural_gen, "vacuity_failures", lambda r=None: [])\n', ""
    )
    assert module_copies("tests/test_planted.py", source) == {
        ("gate-report", "tests/test_planted.py::test_planted")
    }


@pytest.mark.parametrize(
    "source",
    [
        "from harness import cli\n\ndef test_planted():\n"
        '    assert cli.main(["scan", "--diff"]) == 0\n',
        "from scripts import generate_inert_surface_baseline as g\n\ndef test_planted(tmp_path):\n"
        "    assert g.build_inventory(tmp_path)\n",
        "from licence_governance_shared import declaration_sites\n\ndef test_planted(tmp_path):\n"
        "    assert declaration_sites(tmp_path)\n",
        "from harness import scanner\n\ndef test_planted(tmp_path):\n"
        "    assert scanner.scan([tmp_path / 'a.py'], tmp_path) == []\n",
        "from tools.ci import rails_census\n\ndef test_planted(tmp_path):\n"
        "    assert rails_census.leg_violations(tmp_path) == []\n",
    ],
    ids=[
        "scan --diff",
        "a tree the test names",
        "a planted licence tree",
        "a scanner fixture",
        "a planted rails tree",
    ],
)
def test_a_run_on_another_tree_is_not_a_copy(source) -> None:
    assert module_copies("tests/test_planted.py", source) == set()


def _planted_repo(tmp_path: Path, workflow: str) -> Path:
    root = tmp_path / "repo"
    (root / "tests" / "security").mkdir(parents=True)
    (root / "tests" / "rails").mkdir()
    for rel in ("tests/test_a.py", "tests/security/test_b.py", "tests/rails/test_c.py"):
        (root / rel).write_text("def test_x():\n    pass\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    return root


_WORKFLOW = """\
jobs:
  test-shard:
    steps:
      - run: uv run pytest -n auto tests --shard-list "$RUNNER_TEMP/shard.txt"
  rails:
    steps:
      - run: uv run pytest -n 4 tests/rails
"""


def test_a_job_that_runs_only_the_shards_or_the_rails_is_no_file_copy(tmp_path) -> None:
    assert file_copies(_planted_repo(tmp_path, _WORKFLOW)) == set()


def test_a_planted_job_that_runs_a_shard_file_is_a_file_copy(tmp_path) -> None:
    workflow = _WORKFLOW + (
        "  security:\n    steps:\n      - run: >\n          uv run pytest\n"
        "          tests/security\n          tests/test_a.py\n"
    )
    assert file_copies(_planted_repo(tmp_path, workflow)) == {
        ("ci.yml security", "tests/security/test_b.py"),
        ("ci.yml security", "tests/test_a.py"),
    }


_NAMED = _WORKFLOW + "  lint:\n    steps:\n      - run: uv run pytest tests/test_a.py\n"


def test_a_named_file_stops_being_a_copy_only_once_the_shards_leave_it_to_its_job(
    tmp_path,
) -> None:
    """The entry drops once the file is excluded, and not before: named by a job alone, it is
    still a copy; left to that job, it is none."""
    root = _planted_repo(tmp_path, _NAMED)
    assert file_copies(root, {}) == {("ci.yml lint", "tests/test_a.py")}
    assert file_copies(root, {"tests/test_a.py": "ci.yml lint"}) == set()


def _catalogue(**commands: tuple[str, ...]) -> catalogue.Catalogue:
    """A catalogue of one job per keyword, each running its one argv."""
    return catalogue.Catalogue(
        jobs=tuple(
            catalogue.Job(
                id=job_id,
                tiers=("change",),
                inputs=(":(glob)**",),
                external_inputs=(),
                toolchain=(),
                cache=(),
                security="ordinary",
                timeout_min=10,
                command=(catalogue.Step(argv),),
            )
            for job_id, argv in commands.items()
        )
    )


def test_a_file_left_to_a_job_that_no_longer_runs_it_fails(tmp_path) -> None:
    """Its owner is a catalogue job whose command runs it by name; a job that does not, a label
    the catalogue does not know (``ci.yml``'s shape) and a file pytest would not collect fail."""
    root = _planted_repo(tmp_path, _NAMED)
    cat = _catalogue(static=("uv", "run", "pytest", "tests/test_a.py"), e2e=("npx", "playwright"))
    assert job_owned_problems(root, {"tests/test_a.py": "static"}, cat) == []
    assert job_owned_problems(root, {"tests/test_a.py": "e2e"}, cat) == [
        "tests/test_a.py is left to e2e, which no longer runs it by name"
    ]
    assert job_owned_problems(root, {"tests/test_a.py": "ci.yml lint"}, cat) == [
        "tests/test_a.py is left to ci.yml lint, which is no job of the catalogue"
    ]
    gone = {"tests/test_gone.py": "static"}
    assert "tests/test_gone.py is left to static, but pytest would not collect it" in (
        job_owned_problems(root, gone, cat)
    )


def test_an_exemption_that_is_no_longer_a_copy_or_has_no_reason_fails(tmp_path) -> None:
    found = file_copies(_planted_repo(tmp_path, _NAMED), {})
    assert exemption_problems(found, {("ci.yml lint", "tests/test_a.py"): "a reason"}) == []
    assert exemption_problems(found, {("ci.yml lint", "tests/test_a.py"): " "}) == [
        "exempt with no reason: ci.yml lint tests/test_a.py"
    ]
    assert exemption_problems(found, {("ci.yml e2e", "tests/test_a.py"): "a reason"}) == [
        "exempt, but no longer a file copy: ci.yml e2e tests/test_a.py"
    ]


def test_a_docs_reader_the_shards_leave_to_its_job_is_not_the_suites(tmp_path) -> None:
    root = _planted_repo(tmp_path, _NAMED)
    (root / "tests" / "test_a.py").write_text(
        '_ROOT = None\nDOC = _ROOT / "docs" / "guide.md"\n', encoding="utf-8"
    )
    assert docs_readers(root, {}) == {"tests/test_a.py"}
    assert docs_readers(root, {"tests/test_a.py": "ci.yml lint"}) == set()


@pytest.mark.parametrize(
    "source, reads",
    [
        ('_ROOT = None\nDOC = _ROOT / "docs" / "guide.md"\n', True),
        ('def f():\n    return _repo_root() / "docs/api.md"\n', True),
        ('from pathlib import Path\nX = Path(__file__).parents[1] / "docs"\n', True),
        ('def f(tmp_path):\n    return tmp_path / "docs" / "a.md"\n', False),
        ('ROOT = None\nX = ROOT / "src" / "docs.py"\n', False),
    ],
    ids=["a root name", "a root helper", "parents[n]", "a tree the test made", "not docs/"],
)
def test_a_planted_docs_reader_is_found(source, reads) -> None:
    assert reads_docs(source) is reads


def test_a_new_copy_and_a_stale_entry_both_fail() -> None:
    known = frozenset({("gate-report", "tests/test_gone.py::test_gone")})
    found = {("gate-report", "tests/test_new.py::test_new")}
    assert _diff(found, known, "copy") == [
        "a new copy: gate-report tests/test_new.py::test_new",
        "listed, but no longer a copy: gate-report tests/test_gone.py::test_gone",
    ]
