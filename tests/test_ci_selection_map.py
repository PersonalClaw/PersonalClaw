"""``python -m tools.ci selection-map``: which test files ran each source file, read from a coverage
database built with per-test contexts (#3946).

The map's cases build their coverage databases with coverage's own API, one context at a time,
as ``--cov-context=test`` records them, and run the command on them. The last cases run a real
suite under coverage, in a scratch project holding this repository's ``[tool.coverage.run]``
table, to show that a child interpreter's lines are recorded with it and lost without its
``patch``, and that ``make selection-map`` writes the map into the git common dir.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import coverage
import nested_pytest
import pytest
from coverage import sqldata

from tools.ci import selection_map

REPO = Path(__file__).resolve().parents[1]
COMMIT = "c" * 40


def _database(path: Path, contexts: dict[str, dict[str, list[int]]], *, arcs: bool = False) -> Path:
    """A coverage database holding, for each context, the lines it ran in each file."""
    data = coverage.CoverageData(basename=str(path))
    for context, files in contexts.items():
        data.set_context(context)
        if arcs:
            data.add_arcs(
                {f: {(-1, lines[0]), *zip(lines, lines[1:])} for f, lines in files.items()}
            )
        else:
            data.add_lines(files)
    data.write()
    return path


def _build(tmp_path: Path, data: Path, *more: str) -> tuple[int, dict]:
    out = tmp_path / "selection-map.json"
    code = selection_map.main(["--data", str(data), "--commit", COMMIT, "--out", str(out), *more])
    return code, json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}


# ── the map ───────────────────────────────────────────────────────────────────────────────────


def test_each_source_file_maps_to_the_test_files_whose_setup_or_call_ran_it(
    tmp_path: Path,
) -> None:
    data = _database(
        tmp_path / ".coverage",
        {
            "tests/test_a.py::test_one|setup": {"src/personalclaw/config/loader.py": [1, 2]},
            "tests/test_a.py::test_one|run": {"src/personalclaw/a.py": [3]},
            "tests/test_b.py::TestB::test_two[x|y]|run": {"src/personalclaw/a.py": [4]},
            "tests/test_c.py::test_three|teardown": {"src/personalclaw/c.py": [5]},
        },
    )
    code, built = _build(tmp_path, data)
    assert code == 0
    assert built["map"] == {
        "src/personalclaw/a.py": ["tests/test_a.py", "tests/test_b.py"],
        "src/personalclaw/c.py": ["tests/test_c.py"],
        "src/personalclaw/config/loader.py": ["tests/test_a.py"],
    }
    assert built["commit"] == COMMIT and built["test_files"] == 3
    assert built["schema"] == selection_map.SCHEMA and built["version"] == selection_map.VERSION


def test_a_source_file_run_only_outside_a_tests_context_is_listed_apart(tmp_path: Path) -> None:
    """The empty context is what ran before the first test (an import at collection) or in a
    child interpreter, whose lines carry no test: such a file is no test's, and is named."""
    data = _database(
        tmp_path / ".coverage",
        {
            "": {"src/personalclaw/child.py": [1], "src/personalclaw/a.py": [1]},
            "tests/test_a.py::test_one|run": {"src/personalclaw/a.py": [2]},
            "a static label": {"src/personalclaw/labelled.py": [1]},
        },
    )
    code, built = _build(tmp_path, data)
    assert code == 0
    assert built["map"] == {"src/personalclaw/a.py": ["tests/test_a.py"]}
    assert built["outside_tests"] == ["src/personalclaw/child.py", "src/personalclaw/labelled.py"]


def test_a_source_file_more_than_forty_percent_of_test_files_reach_is_a_hub(
    tmp_path: Path,
) -> None:
    contexts: dict[str, dict[str, list[int]]] = {}
    for i in range(5):
        files = {"src/personalclaw/every.py": [1]}
        if i < 3:
            files["src/personalclaw/three.py"] = [1]
        if i < 2:
            files["src/personalclaw/two.py"] = [1]
        contexts[f"tests/test_{i}.py::test_it|run"] = files
    code, built = _build(tmp_path, _database(tmp_path / ".coverage", contexts))
    assert code == 0
    assert built["hub_share"] == selection_map.HUB_SHARE == 0.4
    # Three of five is 60%, a hub; two of five is 40%, which is not more than 40%.
    assert built["hubs"] == ["src/personalclaw/every.py", "src/personalclaw/three.py"]


def test_branch_data_is_read_as_well_as_line_data(tmp_path: Path) -> None:
    data = _database(
        tmp_path / ".coverage",
        {"tests/test_a.py::test_one|run": {"src/personalclaw/a.py": [1, 2, 3]}},
        arcs=True,
    )
    code, built = _build(tmp_path, data)
    assert code == 0 and built["map"] == {"src/personalclaw/a.py": ["tests/test_a.py"]}


def test_an_absolute_path_under_the_root_is_written_relative_to_it(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    data = _database(
        tmp_path / ".coverage",
        {"tests/test_a.py::test_one|run": {str(root / "src/personalclaw/a.py"): [1]}},
    )
    code, built = _build(tmp_path, data, "--root", str(root))
    assert code == 0 and built["map"] == {"src/personalclaw/a.py": ["tests/test_a.py"]}


def test_the_map_reads_the_schema_coverage_writes() -> None:
    """The database is read with sqlite3, so tools/ci stays standard library only; a coverage
    that writes another schema fails here first."""
    assert selection_map.COVERAGE_SCHEMA == sqldata.SCHEMA_VERSION


def test_a_database_in_another_schema_cannot_be_read(tmp_path: Path) -> None:
    data = _database(tmp_path / ".coverage", {"tests/test_a.py::t|run": {"src/a.py": [1]}})
    with sqlite3.connect(data) as db:
        db.execute("update coverage_schema set version = ?", (sqldata.SCHEMA_VERSION + 1,))
    code, built = _build(tmp_path, data)
    assert code == 2 and built == {}


@pytest.mark.parametrize(
    "contents", [None, b"", b"not a database"], ids=["missing", "empty", "text"]
)
def test_a_file_that_is_not_a_coverage_database_cannot_be_read(
    tmp_path: Path, contents: bytes | None
) -> None:
    data = tmp_path / ".coverage"
    if contents is not None:
        data.write_bytes(contents)
    code, built = _build(tmp_path, data)
    assert code == 2 and built == {}
    assert contents is None or data.read_bytes() == contents, "the reader wrote to the file"


def test_a_database_with_no_test_context_cannot_make_a_map(tmp_path: Path) -> None:
    """A run without ``--cov-context=test`` records every line under the empty context, and a
    map with no test in it would select nothing."""
    data = _database(tmp_path / ".coverage", {"": {"src/personalclaw/a.py": [1]}})
    code, built = _build(tmp_path, data)
    assert code == 2 and built == {}


# ── the coverage configuration the map needs ──────────────────────────────────────────────────


def _coverage_run_table() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]


def test_the_coverage_run_table_records_lines_relative_paths_and_children() -> None:
    table = _coverage_run_table()
    assert table["run"] == {
        "branch": False,
        "parallel": True,
        "relative_files": True,
        "patch": ["subprocess"],
    }
    assert table["paths"]["source"][0] == "src/personalclaw/"


_CHILD = """\
def main():
    total = 0
    for i in range(3):
        total += i
    print(total)


if __name__ == "__main__":
    main()
"""

_TESTS = {
    "tests/test_inproc.py": (
        "import pytest\n"
        "from pkg import inproc\n\n\n"
        "@pytest.fixture\n"
        "def two():\n"
        "    return inproc.add(1, 1)\n\n\n"
        "def test_add(two):\n"
        "    assert two == 2\n"
    ),
    "tests/test_child.py": (
        "import subprocess\nimport sys\n\n\n"
        "def test_child():\n"
        '    done = subprocess.run([sys.executable, "-m", "pkg.child"], capture_output=True,'
        " text=True, check=True)\n"
        '    assert done.stdout.strip() == "3"\n'
    ),
}


def _toml_table(name: str, table: dict) -> str:
    return f"[{name}]\n" + "".join(f"{key} = {json.dumps(v)}\n" for key, v in table.items())


def _scratch_project(root: Path, run_table: dict) -> Path:
    """A package with a module a test's fixture calls and a module only a child interpreter
    runs, and this repository's coverage table, as given."""
    files = {
        "pkg/__init__.py": "",
        "pkg/inproc.py": "def add(a, b):\n    return a + b\n",
        "pkg/child.py": _CHILD,
        "pyproject.toml": _toml_table("tool.coverage.run", run_table),
        **_TESTS,
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _scratch_env(home: Path) -> dict[str, str]:
    """No COVERAGE_* of a run this suite may itself be under, and a home of the test's own."""
    return {"PATH": os.environ["PATH"], "HOME": str(home), "LC_ALL": "C"}


def _covered(root: Path, workers: str) -> dict[str, set[str]]:
    """Run the scratch suite under coverage with per-test contexts: the contexts of each file
    that recorded a line (coverage also lists a source file no one imported, with none)."""
    done = subprocess.run(
        [
            *nested_pytest.argv(root),
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            workers,
        ]
        + ["--cov=pkg", "--cov-context=test", "--cov-report=", "tests"],
        cwd=root,
        env=_scratch_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    data = coverage.CoverageData(basename=str(root / ".coverage"))
    data.read()
    ran = {f: data.contexts_by_lineno(f) for f in data.measured_files()}
    return {f: {c for cs in lines.values() for c in cs} for f, lines in ran.items() if lines}


@pytest.mark.parametrize("workers", ["0", "2"])
def test_a_child_interpreters_lines_are_recorded_with_the_tables_patch_and_lost_without_it(
    tmp_path: Path, workers: str
) -> None:
    """Before and after: the same suite, this repository's table with and without ``patch``.
    With it, the child that ``test_child`` starts records ``pkg/child.py``, under the empty
    context, since a child's lines carry no test; without it, no line of the file is."""
    table = _coverage_run_table()["run"]
    with_patch = _scratch_project(tmp_path / "with", table)
    without = _scratch_project(
        tmp_path / "without", {k: v for k, v in table.items() if k != "patch"}
    )
    assert _covered(with_patch, workers)["pkg/child.py"] == {""}
    assert "pkg/child.py" not in _covered(without, workers)
    code, built = _build(tmp_path, with_patch / ".coverage", "--root", str(with_patch))
    assert code == 0, built
    assert built["map"] == {"pkg/inproc.py": ["tests/test_inproc.py"]}
    assert built["outside_tests"] == ["pkg/__init__.py", "pkg/child.py"]


# ── make selection-map ────────────────────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> str:
    env = {**_scratch_env(root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    done = subprocess.run(
        ["git", "-c", "credential.helper=", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_make_selection_map_writes_the_map_into_the_git_common_dir(tmp_path: Path) -> None:
    """Run from a linked worktree, the target writes beside the main repository's objects, the
    one place every worktree of the clone shares, and never into the checkout."""
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not on PATH")
    main = _scratch_project(tmp_path / "main", _coverage_run_table()["run"])
    shutil.copy(REPO / "Makefile", main / "Makefile")
    _git(main, "init", "-q", "-b", "main")
    _git(main, "-c", "user.name=A", "-c", "user.email=a@example.com", "add", "-A")
    _git(main, "-c", "user.name=A", "-c", "user.email=a@example.com", "commit", "-qm", "c")
    _git(main, "worktree", "add", "-q", str(tmp_path / "linked"))
    linked = tmp_path / "linked"
    done = subprocess.run(
        [make, "-s", "selection-map", f"PYTHON={sys.executable}"]
        + ["SELECTION_MAP_ARGS=-p no:cacheprovider -n 0 tests", "SELECTION_MAP_COV=pkg"],
        cwd=linked,
        env={**_scratch_env(linked), "PYTHONPATH": str(REPO)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    common = Path(_git(linked, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert common == (main / ".git").resolve()
    built = json.loads((common / "selection-map" / "selection-map.json").read_text("utf-8"))
    assert built["commit"] == _git(linked, "rev-parse", "HEAD")
    assert built["map"] == {"pkg/inproc.py": ["tests/test_inproc.py"]}
    assert not list(linked.glob(".coverage*")), "the run left coverage data in the checkout"
