"""``tools/ci/rails_census.py``: which test modules are rails, from their source alone.

One table: each row is a module's source and the verdict the census must reach on it. A rail
reads the repository's tree; a test that walks a tree it made, boots a gateway or reaches the
network is not one, and a module with tests of both kinds is mixed and gets split.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import rails_census, shards

_HEAD = "from pathlib import Path\n\nREPO = Path(__file__).resolve().parents[1]\n\n\n"

ROWS: list[tuple[str, str, str]] = [
    (
        "a walk of the repository with rglob is a rail",
        _HEAD + "def test_it():\n    assert list((REPO / 'src').rglob('*.py'))\n",
        "rail",
    ),
    (
        "a walk of tmp_path is not",
        _HEAD + "def test_it(tmp_path):\n    assert not list(tmp_path.rglob('*'))\n",
        "suite",
    ),
    (
        "an ast.parse of a package file is a rail",
        _HEAD
        + "import ast\n\n\ndef test_it():\n"
        + "    ast.parse((REPO / 'src' / 'personalclaw' / 'cli.py').read_text())\n",
        "rail",
    ),
    (
        "an ast.parse of a live module's source is a rail",
        "import ast\nimport inspect\n\nimport personalclaw.cli as cli\n\n\n"
        "def test_it():\n    ast.parse(inspect.getsource(cli))\n",
        "rail",
    ),
    (
        "an ast.parse of a string the test wrote is not",
        "import ast\n\n\ndef test_it():\n    ast.parse('x = 1')\n",
        "suite",
    ),
    (
        "a read of the source index is a rail",
        "import source_index\n\n\ndef test_it():\n    assert source_index.package_files()\n",
        "rail",
    ),
    (
        "a workflow read is a rail",
        _HEAD + "def test_it():\n    (REPO / '.github' / 'workflows' / 'ci.yml').read_text()\n",
        "rail",
    ),
    (
        "a workflow named only in a docstring is not",
        'def test_it():\n    """See .github/workflows/ci.yml."""\n    assert True\n',
        "suite",
    ),
    (
        "a single file read with no parse is not",
        _HEAD + "def test_it():\n    assert (REPO / 'pyproject.toml').read_text()\n",
        "suite",
    ),
    (
        "a gateway start is not",
        "import signed_in_gateway\n\n\ndef test_it():\n    signed_in_gateway.start()\n",
        "suite",
    ),
    (
        "a rail that also calls the network is not",
        "import urllib.request\n\nimport source_index\n\n\ndef test_it():\n"
        "    source_index.package_files()\n    urllib.request.urlopen('https://example.com')\n",
        "suite",
    ),
    (
        "a rail that runs git, a local child, is still a rail",
        "import subprocess\n\nimport source_index\n\n\ndef test_it():\n"
        "    source_index.package_files()\n    subprocess.run(['git', 'status'])\n",
        "rail",
    ),
    (
        "a rail that spawns a container runtime is not",
        "import subprocess\n\nimport source_index\n\n\ndef test_it():\n"
        "    source_index.package_files()\n    subprocess.run(['docker', 'ps'])\n",
        "suite",
    ),
    (
        "a tree read through a module-level helper the test reaches is a rail",
        "import source_index\n\n\ndef _files():\n    return source_index.package_files()\n\n\n"
        "def test_it():\n    assert _files()\n",
        "rail",
    ),
    (
        "a tree read through a fixture the test asks for is a rail",
        "import pytest\nimport source_index\n\n\n@pytest.fixture\ndef files():\n"
        "    return source_index.package_files()\n\n\ndef test_it(files):\n    assert files\n",
        "rail",
    ),
    (
        "a tree read in a decorator's parameters is a rail",
        "import pytest\nimport source_index\n\n\n"
        "@pytest.mark.parametrize('path', source_index.package_files()[:2])\n"
        "def test_it(path):\n    assert path\n",
        "rail",
    ),
    (
        "a module with a tree test and a network test is mixed",
        "import urllib.request\n\nimport source_index\n\n\ndef test_tree():\n"
        "    assert source_index.package_files()\n\n\ndef test_wire():\n"
        "    urllib.request.urlopen('https://example.com')\n",
        "mixed",
    ),
    # A whole-tree check run on the real tree through an import (tools/ci/tree_checks.py): the
    # check reads the tree for the test, though the test's own source opens no file.
    (
        "a baseline generator called with no tree is a rail",
        "from scripts.generate_docs_lint_baseline import build_inventory\n\n\n"
        "def test_it():\n    assert build_inventory()\n",
        "rail",
    ),
    (
        "a baseline generator given a tree the test made is not",
        "from scripts.generate_docs_lint_baseline import build_inventory\n\n\n"
        "def test_it(tmp_path):\n    assert build_inventory(tmp_path)\n",
        "suite",
    ),
    (
        "a baseline generator whose tree source the test replaces is not",
        "from scripts import generate_docs_lint_baseline as gen\n\n\n"
        "def test_it(monkeypatch):\n    monkeypatch.setattr(gen, '_tracked_files', lambda: [])\n"
        "    assert gen.build_inventory()\n",
        "suite",
    ),
    (
        "the gate aggregate run for real is a rail",
        "from scripts.gate_report import run_all_gates\n\n\n"
        "def test_it():\n    assert run_all_gates()\n",
        "rail",
    ),
    (
        "the asset census with no tree named is a rail",
        "from scripts import check_asset_licenses as assets\n\n\n"
        "def test_it():\n    assert assets.violations() == []\n",
        "rail",
    ),
    (
        "harness validate on the shipped specs is a rail",
        "from harness import cli\n\n\ndef test_it():\n    assert cli.main(['validate']) == 0\n",
        "rail",
    ),
    (
        "harness explain, which reads one spec, is not",
        "from harness import cli\n\n\ndef test_it():\n    assert cli.main(['explain', 'x']) == 2\n",
        "suite",
    ),
    (
        "a real-tree check reached through a module-level helper is a rail",
        "from scripts.generate_docs_lint_baseline import build_inventory\n\n\n"
        "def _fresh():\n    return build_inventory()\n\n\ndef test_it():\n    assert _fresh()\n",
        "rail",
    ),
    (
        "a module with a real-tree check run and a network test is mixed",
        "import urllib.request\n\nfrom harness import cli\n\n\ndef test_tree():\n"
        "    assert cli.main(['validate']) == 0\n\n\ndef test_wire():\n"
        "    urllib.request.urlopen('https://example.com')\n",
        "mixed",
    ),
    (
        "a security-marked module stays put whatever it reads",
        "import pytest\nimport source_index\n\npytestmark = pytest.mark.security\n\n\n"
        "def test_it():\n    assert source_index.package_files()\n",
        "security",
    ),
    (
        "a module that reads no tree is not",
        "def test_it():\n    assert 1 + 1 == 2\n",
        "suite",
    ),
]


@pytest.mark.parametrize(("source", "kind"), [(s, k) for _, s, k in ROWS], ids=[r[0] for r in ROWS])
def test_the_census_classifies_each_module(source: str, kind: str) -> None:
    assert rails_census.classify("tests/test_x.py", source).kind == kind


def test_each_verdict_says_why() -> None:
    found = rails_census.classify("tests/test_x.py", ROWS[0][1])
    assert found.tree == {"test_it": "walks the repository (rglob)"}
    assert "walks the repository (rglob)" in found.line()


def test_a_real_tree_check_run_says_which_check() -> None:
    """The reason names the whole-tree check, so a reader sees why a module that opens no file
    is a rail. The census and the copy rail read that one definition (tools/ci/tree_checks.py)."""
    row = next(s for name, s, _ in ROWS if name.startswith("harness validate"))
    found = rails_census.classify("tests/test_x.py", row)
    assert found.tree == {"test_it": "runs harness on the real tree"}
    assert "runs harness on the real tree" in found.line()


def test_a_planted_real_tree_check_outside_the_rails_is_misplaced(tmp_path: Path) -> None:
    """The planted module: a suite module that runs a baseline generator on the real tree is a
    rail outside tests/rails, and `rails check` fails on it; the same module under tests/rails
    is a rail and passes."""
    row = next(s for name, s, _ in ROWS if name.startswith("a baseline generator called"))
    _plant(tmp_path, "tests/test_ratchet_copy.py", row)
    _plant(tmp_path, "tests/rails/test_ratchet_tree.py", row)
    bad = rails_census.misplaced(rails_census.census(tmp_path))
    assert bad == [
        "tests/test_ratchet_copy.py is a rail outside tests/rails/: "
        "1/1 read the tree; runs docs-lint on the real tree)"
    ], bad


def _plant(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_misplaced_names_a_rail_outside_tests_rails_and_a_suite_module_inside(
    tmp_path: Path,
) -> None:
    rail, suite = ROWS[5][1], ROWS[-1][1]
    _plant(tmp_path, "tests/test_rail_outside.py", rail)
    _plant(tmp_path, "tests/rails/test_rail_inside.py", rail)
    _plant(tmp_path, "tests/rails/test_suite_inside.py", suite)
    _plant(tmp_path, "tests/test_suite_outside.py", suite)
    _plant(tmp_path, "tests/security/test_marked.py", ROWS[-2][1])
    bad = rails_census.misplaced(rails_census.census(tmp_path))
    assert [line.split(" ", 1)[0] for line in bad] == [
        "tests/rails/test_suite_inside.py",
        "tests/test_rail_outside.py",
    ]


def test_a_checkout_under_a_dot_folder_is_read_whole(tmp_path: Path) -> None:
    """Only the parts below the root are filtered: a checkout that sits inside a folder whose
    name starts with a dot (a worktree under another checkout's `.local/`) is still read."""
    root = tmp_path / ".hidden" / "repo"
    _plant(root, "tests/test_suite.py", ROWS[-1][1])
    _plant(root, "tests/rails/test_rail.py", ROWS[5][1])
    _plant(root, "tests/.cache/test_ignored.py", ROWS[-1][1])
    found = [m.path for m in rails_census.census(root)]
    assert found == ["tests/rails/test_rail.py", "tests/test_suite.py"]


def test_the_census_reads_every_module_pytest_collects_by_its_defaults(tmp_path: Path) -> None:
    """With no ``python_files`` set, pytest collects ``test_*.py`` and ``*_test.py``: the census
    reads both, and neither is a helper."""
    _plant(tmp_path, "tests/test_suite.py", ROWS[-1][1])
    _plant(tmp_path, "tests/new_rail_test.py", ROWS[5][1])
    _plant(tmp_path, "tests/rails/c_test.py", ROWS[5][1])
    _plant(tmp_path, "tests/helper.py", "X = 1\n")
    _plant(tmp_path, "tests/conftest.py", "")
    found = {m.path: m.kind for m in rails_census.census(tmp_path)}
    assert found == {
        "tests/new_rail_test.py": "rail",
        "tests/rails/c_test.py": "rail",
        "tests/test_suite.py": "suite",
    }
    assert set(rails_census.helper_facts(tmp_path)) == {"helper"}
    bad = rails_census.misplaced(rails_census.census(tmp_path))
    assert [line.split(" ", 1)[0] for line in bad] == ["tests/new_rail_test.py"], bad


def test_the_census_reads_python_files_from_pyproject(tmp_path: Path) -> None:
    _plant(tmp_path, "pyproject.toml", '[tool.pytest.ini_options]\npython_files = ["check_*.py"]\n')
    _plant(tmp_path, "tests/rails/check_a.py", ROWS[5][1])
    _plant(tmp_path, "tests/rails/test_b.py", ROWS[5][1])
    assert [m.path for m in rails_census.census(tmp_path)] == ["tests/rails/check_a.py"]


@pytest.mark.parametrize(
    ("python_files", "rails"),
    [
        (None, {"tests/rails/test_r.py", "tests/rails/s_test.py"}),
        ('["check_*.py", "*_test.py"]', {"tests/rails/s_test.py"}),
    ],
    ids=["defaults", "named"],
)
def test_the_census_and_the_shards_read_the_same_test_modules(
    tmp_path: Path, python_files: str | None, rails: set[str]
) -> None:
    """Differential: outside tests/rails the census reads exactly the shards' universe, the files
    pytest collects there, so a module either reads as a test module is one in both."""
    config = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    if python_files:
        config += f"python_files = {python_files}\n"
    _plant(tmp_path, "pyproject.toml", config)
    for rel in (
        "tests/test_a.py",
        "tests/b_test.py",
        "tests/check_c.py",
        "tests/sub/test_d.py",
        "tests/helper.py",
        "tests/conftest.py",
        "tests/rails/test_r.py",
        "tests/rails/s_test.py",
    ):
        _plant(tmp_path, rel, ROWS[-1][1])
    found = {path.relative_to(tmp_path).as_posix() for path in rails_census.test_modules(tmp_path)}
    outside = {rel for rel in found if not rel.startswith(rails_census.RAILS + "/")}
    assert outside == set(shards.universe(tmp_path))
    assert found - outside == rails


def _halves(root: Path, first: str, second: str) -> None:
    _plant(root, rails_census.HALVES[0], first)
    _plant(root, rails_census.HALVES[1], second)


def _two_rails(root: Path) -> None:
    _plant(root, "tests/rails/test_a.py", ROWS[5][1])
    _plant(root, "tests/rails/test_b.py", ROWS[5][1])
    _plant(root, "tests/test_suite.py", ROWS[-1][1])


def test_the_halves_hold_every_rail_once(tmp_path: Path) -> None:
    _two_rails(tmp_path)
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_b.py\n")
    assert rails_census.half_violations(tmp_path) == []


@pytest.mark.parametrize(
    ("first", "second", "expect"),
    [
        pytest.param(
            "tests/rails/test_a.py\n",
            "\n",
            "tools/ci/rails-half-2.txt names no file",
            id="an empty half",
        ),
        pytest.param(
            "tests/rails/test_a.py\n",
            "tests/rails/test_a.py\n",
            "tests/rails/test_b.py is in neither half",
            id="a rail in neither half",
        ),
        pytest.param(
            "tests/rails/test_a.py\ntests/rails/test_b.py\n",
            "tests/rails/test_b.py\n",
            "tests/rails/test_b.py is listed 2 times",
            id="a rail in both halves",
        ),
        pytest.param(
            "tests/rails/test_a.py\ntests/test_suite.py\n",
            "tests/rails/test_b.py\n",
            "tests/test_suite.py is listed in tools/ci/rails-half-1.txt and is no test module",
            id="a suite module listed",
        ),
        pytest.param(
            "tests/rails/test_a.py\ntests/rails/test_gone.py\n",
            "tests/rails/test_b.py\n",
            "tests/rails/test_gone.py is listed in tools/ci/rails-half-1.txt and is no test",
            id="a listed file that is gone",
        ),
    ],
)
def test_a_planted_half_violation_fails(tmp_path: Path, first: str, second: str, expect: str):
    _two_rails(tmp_path)
    _halves(tmp_path, first, second)
    bad = rails_census.half_violations(tmp_path)
    assert any(expect in line for line in bad), bad


def test_a_rail_named_the_other_way_pytest_collects_is_in_neither_half(tmp_path: Path) -> None:
    """pytest collects ``*_test.py`` as well as ``test_*.py`` (its default ``python_files``), and
    ``--shard-list`` skips either kind when no list names it: so a rail of either name that no
    half lists would run nowhere."""
    _two_rails(tmp_path)
    _plant(tmp_path, "tests/rails/c_test.py", ROWS[5][1])
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_b.py\n")
    assert rails_census.half_violations(tmp_path) == [
        "tests/rails/c_test.py is in neither half of the rails job"
        f" ({', '.join(rails_census.HALVES)})"
    ]
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_b.py\ntests/rails/c_test.py\n")
    assert rails_census.half_violations(tmp_path) == []


def test_a_half_that_is_not_utf8_fails(tmp_path: Path) -> None:
    _two_rails(tmp_path)
    _plant(tmp_path, rails_census.HALVES[0], "tests/rails/test_a.py\n")
    (tmp_path / rails_census.HALVES[1]).write_bytes(b"tests/rails/test_b.py\n\xff\n")
    bad = rails_census.half_violations(tmp_path)
    assert bad[0] == (
        f"{rails_census.HALVES[1]} cannot be read (UnicodeDecodeError), so its half runs nothing"
    ), bad


def test_a_folder_named_like_the_rails_is_not_theirs(tmp_path: Path) -> None:
    """``tests/rails_other`` is not ``tests/rails``: its modules are in no half, and need not be."""
    _two_rails(tmp_path)
    _plant(tmp_path, "tests/rails_other/test_x.py", ROWS[-1][1])
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_b.py\n")
    assert rails_census.half_violations(tmp_path) == []


#: A conftest that wires ``--shard-list`` in, as ``tests/conftest.py`` does.
_SHARD_LIST_CONFTEST = f"""
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
import shard_list


def pytest_addoption(parser):
    shard_list.addoption(parser)


def pytest_configure(config):
    shard_list.configure(config)


def pytest_ignore_collect(collection_path, config):
    return shard_list.ignore_collect(collection_path, config)


def pytest_collection_finish(session):
    shard_list.collection_finish(session)
"""


def _collected(root: Path, *args: str) -> set[str]:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        + ["-o", "addopts=", "tests/rails", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return {line for line in done.stdout.splitlines() if "::" in line}


def test_halves_the_check_passes_collect_what_pytest_collects_in_tests_rails(
    tmp_path: Path,
) -> None:
    """Differential, against pytest itself: halves ``rails check`` passes collect, with the
    ``--shard-list`` runs CI makes, exactly what ``pytest tests/rails`` collects, each test in
    one half. A rail pytest collects that no half lists is a violation, never a pass."""
    _plant(tmp_path, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    _plant(tmp_path, "tests/conftest.py", _SHARD_LIST_CONFTEST)
    for rel in ("tests/rails/test_a.py", "tests/rails/test_b.py", "tests/rails/c_test.py"):
        _plant(tmp_path, rel, ROWS[0][1])
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_b.py\n")
    assert any(
        "c_test.py is in neither half" in line for line in rails_census.half_violations(tmp_path)
    )
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_b.py\ntests/rails/c_test.py\n")
    assert rails_census.half_violations(tmp_path) == []
    whole = _collected(tmp_path)
    first, second = (_collected(tmp_path, "--shard-list", half) for half in rails_census.HALVES)
    assert len(whole) == 3, whole
    assert not first & second and first | second == whole, (first, second)


def test_a_missing_half_fails(tmp_path: Path) -> None:
    _two_rails(tmp_path)
    _plant(tmp_path, rails_census.HALVES[0], "tests/rails/test_a.py\ntests/rails/test_b.py\n")
    bad = rails_census.half_violations(tmp_path)
    assert bad == [
        f"{rails_census.HALVES[1]} cannot be read (FileNotFoundError), so its half runs nothing"
    ]


def test_rails_check_reports_a_half_violation(tmp_path: Path, capsys) -> None:
    _two_rails(tmp_path)
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_a.py\n")
    assert rails_census.main(["check", "--root", str(tmp_path)]) == 1
    assert "tests/rails/test_b.py is in neither half" in capsys.readouterr().err
    _halves(tmp_path, "tests/rails/test_a.py\n", "tests/rails/test_b.py\n")
    assert rails_census.main(["check", "--root", str(tmp_path)]) == 0


def test_a_tree_with_no_test_modules_fails_the_check(tmp_path: Path) -> None:
    """An empty census checked nothing, so it is a violation, never a pass."""
    (tmp_path / "tests").mkdir()
    bad = rails_census.violations(tmp_path)
    assert len(bad) == 1 and "no test modules" in bad[0]
    _plant(tmp_path, "tests/test_suite.py", ROWS[-1][1])
    assert rails_census.violations(tmp_path) == []
