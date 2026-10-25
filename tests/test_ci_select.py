"""``select`` (tools/ci/select.py) on planted trees: what each edge reaches, where the walk starts,
when the answer is the whole suite, and what ``--run`` runs.

Each edge kind is shown with a control: the import that selects a test, beside a module it does
not import that leaves the test out. Trees are planted as sources and read with the module's own
parser, so no test imports what it reads; ``main`` runs on a planted clone.
"""

from __future__ import annotations

import posixpath
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.ci import select

PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'


def _graph(files: dict[str, str]) -> select.Graph:
    paths = sorted(p for p in files if p.endswith(".py"))
    has_init = {posixpath.dirname(p): True for p in paths if p.endswith("/__init__.py")}
    facts = {
        p: select.parse(files[p], select.names_of(p, has_init)[0], p.endswith("/__init__.py"))
        for p in paths
    }
    tops = frozenset(p.split("/", 1)[0] for p in files)
    return select.build(Path("."), paths, facts, tops)


def _universe(files: dict[str, str]) -> list[str]:
    return sorted(
        p
        for p in files
        if p.startswith("tests/")
        and not p.startswith("tests/rails/")
        and posixpath.basename(p).startswith("test_")
    )


def _pick(
    files: dict[str, str], changed: list[str], listed: list[select.Listed] | None = None
) -> select.Selection:
    return select.select(changed, _graph(files), listed or [], _universe(files))


def _chosen(files: dict[str, str], changed: list[str], **kw: Any) -> list[str]:
    return [p for _, p, _ in _pick(files, changed, **kw).files]


BASE = {
    "src/personalclaw/__init__.py": "",
    "src/personalclaw/a.py": "",
    "src/personalclaw/other.py": "",
    "tests/test_other.py": "import personalclaw.other\n",
}


# ── each edge ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import personalclaw.a\n",
        "from personalclaw import a\n",
        "from personalclaw.a import thing\n",
        "def test_it():\n    import personalclaw.a\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import personalclaw.a\n",
        "import importlib\nimportlib.import_module('personalclaw.a')\n",
        "import importlib\nNAME = 'personalclaw.a'\nimportlib.import_module(NAME)\n",
        "import importlib\nBASE = 'personalclaw'\nimportlib.import_module(f'{BASE}.a')\n",
        "__import__('personalclaw.a')\n",
    ],
)
def test_each_way_of_importing_a_module_selects_the_test_and_no_other(source: str) -> None:
    files = {**BASE, "tests/test_x.py": source}
    assert _chosen(files, ["src/personalclaw/a.py"]) == ["tests/test_x.py"]
    assert _chosen(files, ["src/personalclaw/other.py"]) == ["tests/test_other.py"]


def test_a_relative_import_resolves_against_its_package() -> None:
    files = {
        **BASE,
        "src/personalclaw/sub/__init__.py": "",
        "src/personalclaw/sub/b.py": "from ..a import thing\n",
        "tests/test_x.py": "import personalclaw.sub.b\n",
    }
    chosen = _pick(files, ["src/personalclaw/a.py"]).files
    assert [(d, p) for d, p, _ in chosen] == [(2, "tests/test_x.py")]


def test_an_init_runs_for_every_import_of_a_module_under_it() -> None:
    files = {
        **BASE,
        "src/personalclaw/pkg/__init__.py": "import personalclaw.a\n",
        "src/personalclaw/pkg/leaf.py": "",
        "tests/test_x.py": "import personalclaw.pkg.leaf\n",
    }
    # personalclaw.pkg.leaf's import runs pkg/__init__.py, which imports a.
    assert _chosen(files, ["src/personalclaw/a.py"]) == ["tests/test_x.py"]
    assert _chosen(files, ["src/personalclaw/pkg/__init__.py"]) == ["tests/test_x.py"]


def test_a_test_helper_is_reached_by_its_bare_name() -> None:
    files = {
        **BASE,
        "tests/helper.py": "import personalclaw.a\n",
        "tests/test_x.py": "import helper\n",
    }
    chosen = _pick(files, ["src/personalclaw/a.py"]).files
    assert [(d, p) for d, p, _ in chosen] == [(2, "tests/test_x.py")]


def test_a_deleted_module_selects_what_still_imports_it() -> None:
    files = {**BASE, "tests/test_x.py": "import personalclaw.gone\n"}
    assert _chosen(files, ["src/personalclaw/gone.py"]) == ["tests/test_x.py"]


def test_a_changed_test_file_is_selected_at_distance_zero() -> None:
    chosen = _pick({**BASE, "tests/test_x.py": ""}, ["tests/test_x.py"]).files
    assert chosen == [(0, "tests/test_x.py", "changed")]


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────

CONFTEST = """
import pytest
from personalclaw import other

@pytest.fixture
def gw():
    import personalclaw.a
    return personalclaw.a

@pytest.fixture
def uses_other(gw):
    return other

@pytest.fixture(autouse=True)
def reset():
    from personalclaw import other as o
"""


def test_a_fixture_reaches_what_its_body_imports_for_the_tests_that_ask_for_it() -> None:
    files = {
        **BASE,
        "tests/conftest.py": CONFTEST,
        "tests/test_asks.py": "def test_it(gw):\n    pass\n",
        "tests/test_does_not.py": "def test_it(tmp_path):\n    pass\n",
    }
    assert _chosen(files, ["src/personalclaw/a.py"]) == ["tests/test_asks.py"]


def test_a_fixture_reaches_what_the_fixtures_it_asks_for_reach_and_its_top_level_names() -> None:
    files = {
        **BASE,
        "tests/conftest.py": CONFTEST,
        "tests/test_chain.py": "def test_it(uses_other):\n    pass\n",
    }
    assert "tests/test_chain.py" in _chosen(files, ["src/personalclaw/a.py"])
    assert "tests/test_chain.py" in _chosen(files, ["src/personalclaw/other.py"])


def test_an_autouse_fixture_adds_no_edge() -> None:
    files = {**BASE, "tests/conftest.py": CONFTEST, "tests/test_plain.py": ""}
    assert _chosen(files, ["src/personalclaw/other.py"]) == ["tests/test_other.py"]


# ── literals ──────────────────────────────────────────────────────────────────────────────────


def test_a_test_that_names_a_path_or_its_folder_is_selected_and_one_naming_neither_is_not() -> None:
    files = {
        **BASE,
        "docs/guide/one.md": "",
        "tests/test_file.py": "REPO = object()\nX = REPO / 'docs' / 'guide' / 'one.md'\n",
        "tests/test_folder.py": "D = 'docs/guide/'\n",
        "tests/test_glob.py": "G = 'docs/**/*.md'\n",
        "tests/test_elsewhere.py": "D = 'web/src'\n",
        "src/personalclaw/reader.py": "P = 'docs/guide/one.md'\n",
        "tests/test_reader.py": "import personalclaw.reader\n",
    }
    # A package module's literal is its own: only the tests that name the path start the walk.
    assert _chosen(files, ["docs/guide/one.md"]) == [
        "tests/test_file.py",
        "tests/test_folder.py",
        "tests/test_glob.py",
    ]


def test_a_module_that_loads_by_name_reaches_what_its_literals_name() -> None:
    table = (
        "import importlib\nMODS = ['personalclaw.a']\n"
        "def load(m):\n    importlib.import_module(m)\n"
    )
    files = {**BASE, "tests/test_table.py": table}
    call = _site(files, "tests/test_table.py")
    listed = [select.Listed("tests/test_table.py", call, "", True, False, "imports from MODS")]
    assert _chosen(files, ["src/personalclaw/a.py"], listed=listed) == ["tests/test_table.py"]
    # The same literal in a module that loads nothing by name is data, and adds no edge.
    files = {**BASE, "tests/test_table.py": "MODS = ['personalclaw.a']\n"}
    assert _chosen(files, ["src/personalclaw/a.py"]) == []


def test_a_script_loaded_by_its_path_reaches_what_the_script_imports() -> None:
    loader = (
        "import importlib.util\nfrom pathlib import Path\nREPO = Path('.')\n"
        "spec = importlib.util.spec_from_file_location('x', REPO / 'scripts' / 'tool.py')\n"
    )
    files = {
        **BASE,
        "scripts/__init__.py": "",
        "scripts/tool.py": "import personalclaw.a\n",
        "tests/test_tool.py": loader,
    }
    listed = [
        select.Listed(
            "tests/test_tool.py",
            _site(files, "tests/test_tool.py"),
            "",
            True,
            False,
            "loads it by path",
        )
    ]
    assert _chosen(files, ["src/personalclaw/a.py"], listed=listed) == ["tests/test_tool.py"]


def test_package_data_starts_from_the_modules_beside_it_and_the_package_modules_naming_it() -> None:
    files = {
        **BASE,
        "src/personalclaw/config/__init__.py": "",
        "src/personalclaw/config/loader.py": "",
        "src/personalclaw/config/defaults.json": "",
        "src/personalclaw/named.py": "D = 'config/defaults.json'\n",
        "tests/test_loader.py": "import personalclaw.config.loader\n",
        "tests/test_named.py": "import personalclaw.named\n",
        "tests/test_neither.py": "import personalclaw.a\n",
    }
    assert _chosen(files, ["src/personalclaw/config/defaults.json"]) == [
        "tests/test_loader.py",
        "tests/test_named.py",
    ]


# ── dynamic imports ───────────────────────────────────────────────────────────────────────────

DYNAMIC = "import importlib\ndef load(name):\n    return importlib.import_module(name)\n"


def _site(files: dict[str, str], path: str) -> str:
    return next(call for p, call, _ in _graph(files).dynamic if p == path)


def test_a_listed_prefix_selects_its_module_on_any_change_under_the_prefix_and_no_other() -> None:
    files = {
        **BASE,
        "src/personalclaw/plugins/__init__.py": "",
        "src/personalclaw/plugins/one.py": "",
        "src/personalclaw/loader.py": DYNAMIC,
        "tests/test_loader.py": "import personalclaw.loader\n",
    }
    call = _site(files, "src/personalclaw/loader.py")
    listed = [
        select.Listed(
            "src/personalclaw/loader.py", call, "personalclaw.plugins", False, False, "plugins"
        )
    ]
    assert _chosen(files, ["src/personalclaw/plugins/one.py"], listed=listed) == [
        "tests/test_loader.py"
    ]
    assert "tests/test_loader.py" not in _chosen(files, ["src/personalclaw/a.py"], listed=listed)


def test_an_unlisted_dynamic_import_selects_the_whole_suite_and_says_where() -> None:
    files = {**BASE, "src/personalclaw/loader.py": DYNAMIC}
    chosen = _pick(files, ["src/personalclaw/a.py"])
    assert chosen.whole.startswith("src/personalclaw/loader.py: importlib.import_module(name)")
    assert [p for _, p, _ in chosen.files] == _universe(files)


def test_a_listed_entry_that_says_no_kind_or_two_covers_nothing() -> None:
    files = {**BASE, "src/personalclaw/loader.py": DYNAMIC}
    call = _site(files, "src/personalclaw/loader.py")
    for bad in (
        select.Listed("src/personalclaw/loader.py", call, "", False, False, "no kind"),
        select.Listed("src/personalclaw/loader.py", call, "personalclaw", True, False, "two"),
        select.Listed("src/personalclaw/loader.py", call, "personalclaw", False, False, ""),
    ):
        assert bad.problem()
        assert _pick(files, ["src/personalclaw/a.py"], listed=[bad]).whole


def test_a_rails_own_dynamic_import_needs_no_entry() -> None:
    files = {**BASE, "tests/rails/test_census_tree.py": DYNAMIC}
    assert not _pick(files, ["src/personalclaw/a.py"]).whole


# ── the whole suite ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "changed, says",
    [
        ("tests/conftest.py", "is a conftest"),
        ("tests/sub/conftest.py", "is a conftest"),
        ("pyproject.toml", "configures every test"),
        ("uv.lock", "configures every test"),
        ("tools/ci/catalogue.toml", "configures every test"),
        ("tests/harness_plugin.py", "part of the harness"),
        ("../outside.py", "not a plain repository path"),
        ("/abs/path.py", "not a plain repository path"),
        ("docs/./x.md", "not a plain repository path"),
    ],
)
def test_what_reaches_every_test_selects_the_whole_suite_with_its_reason(
    changed: str, says: str
) -> None:
    files = {**BASE, "tests/conftest.py": "import harness_plugin\n", "tests/harness_plugin.py": ""}
    chosen = _pick(files, [changed])
    assert says in chosen.whole
    assert [p for _, p, _ in chosen.files] == _universe(files)


def test_a_module_that_cannot_be_parsed_selects_the_whole_suite() -> None:
    chosen = _pick({**BASE, "src/personalclaw/broken.py": "def (:\n"}, ["docs/x.md"])
    assert "src/personalclaw/broken.py cannot be parsed" in chosen.whole


# ── always and never ──────────────────────────────────────────────────────────────────────────


def test_the_whole_tree_readers_and_the_security_modules_are_always_added() -> None:
    reader = select.WHOLE_TREE_READERS[0]
    files = {
        **BASE,
        reader: "",
        "tests/test_guard.py": (
            "import pytest\npytestmark = [pytest.mark.slow, pytest.mark.security]\n"
        ),
        "tests/test_plain.py": "",
    }
    chosen = _pick(files, ["src/personalclaw/other.py"]).files
    assert [(d, p) for d, p, _ in chosen] == [
        (0, "tests/test_guard.py"),
        (0, reader),
        (1, "tests/test_other.py"),
    ]


def test_a_rail_is_never_selected() -> None:
    files = {**BASE, "tests/rails/test_a_tree.py": "import personalclaw.a\n"}
    assert _chosen(files, ["src/personalclaw/a.py"]) == []


def test_more_than_eight_hundred_reachable_files_all_come_back_nearest_first() -> None:
    files = {
        **BASE,
        "src/personalclaw/b.py": "import personalclaw.a\n",
        "src/personalclaw/c.py": "import personalclaw.b\n",
    }
    for i in range(400):
        files[f"tests/test_one_{i:03}.py"] = "import personalclaw.a\n"
        files[f"tests/test_two_{i:03}.py"] = "import personalclaw.b\n"
        files[f"tests/test_three_{i:03}.py"] = "import personalclaw.c\n"
    chosen = _pick(files, ["src/personalclaw/a.py"]).files
    assert len(chosen) == 1200
    assert [(d, p) for d, p, _ in chosen] == sorted((d, p) for d, p, _ in chosen)
    assert {d for d, _, _ in chosen[:400]} == {1} and {d for d, _, _ in chosen[800:]} == {3}


# ── recall ────────────────────────────────────────────────────────────────────────────────────


def test_recall_names_each_test_the_map_ran_that_the_selection_misses() -> None:
    files = {
        **BASE,
        "tests/test_x.py": "import personalclaw.a\n",
        "tests/test_hidden.py": "",
    }
    coverage = {"src/personalclaw/a.py": ["tests/test_x.py", "tests/test_hidden.py"]}
    misses = select.recall_misses(_graph(files), [], _universe(files), coverage)
    assert misses == [("src/personalclaw/a.py", "tests/test_hidden.py")]
    coverage = {"src/personalclaw/a.py": ["tests/test_x.py"]}
    assert select.recall_misses(_graph(files), [], _universe(files), coverage) == []


# ── the command ───────────────────────────────────────────────────────────────────────────────


def _clone(tmp_path: Path, files: dict[str, str]) -> Path:
    for path, text in {"pyproject.toml": PYPROJECT, **files}.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")

    def run(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "plant")
    return tmp_path


def test_main_writes_the_list_and_exits_three_when_nothing_reaches_the_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(tmp_path, {**BASE, "docs/x.md": ""})
    out = root / "list.txt"
    assert (
        select.main(
            ["--root", str(root), "--paths", "src/personalclaw/other.py", "--out", str(out)]
        )
        == 0
    )
    assert out.read_text(encoding="utf-8") == "tests/test_other.py\n"
    assert (
        select.main(["--root", str(root), "--paths", "docs/x.md", "--out", str(out)])
        == select.NOTHING
    )
    assert not out.exists()
    assert "nothing to run" in capsys.readouterr().err


def test_main_reads_the_working_tree_untracked_files_among_it(tmp_path: Path) -> None:
    root = _clone(tmp_path, dict(BASE))
    (root / "tests/test_new.py").write_text("import personalclaw.a\n", encoding="utf-8")
    (root / "src/personalclaw/a.py").write_text("X = 1\n", encoding="utf-8")
    assert select.changed_paths(root, "HEAD", True) == [
        "src/personalclaw/a.py",
        "tests/test_new.py",
    ]
    out = root / "list.txt"
    assert (
        select.main(["--root", str(root), "--base", "HEAD", "--working-tree", "--out", str(out)])
        == 0
    )
    assert out.read_text(encoding="utf-8") == "tests/test_new.py\n"


def test_the_cache_is_used_when_a_file_is_unchanged_and_reread_when_it_changes(
    tmp_path: Path,
) -> None:
    root = _clone(tmp_path, {**BASE, "tests/test_x.py": "import personalclaw.other\n"})
    out = root / "list.txt"
    args = ["--root", str(root), "--paths", "src/personalclaw/a.py", "--out", str(out)]
    assert select.main(args) == select.NOTHING
    assert (root / select.CACHE).is_file()
    (root / "tests/test_x.py").write_text("import personalclaw.a  # now\n", encoding="utf-8")
    assert select.main(args) == 0
    assert out.read_text(encoding="utf-8") == "tests/test_x.py\n"
    (root / select.CACHE).write_text("{not json", encoding="utf-8")
    assert select.main(args) == 0


class _Memory:
    def __init__(self, free: int | None, total: int = 64_000) -> None:
        self._free, self._total, self.ledger, self.said_unreadable = free, total, None, False

    def free(self) -> int | None:
        return self._free

    def total(self) -> int:
        return self._total

    def clock(self) -> float:
        return 0.0

    def sleep(self, _s: float) -> None:
        raise AssertionError("admission waited")


def _runner(memory: _Memory, ran: list[list[str]], workers: Any = "auto") -> select.Runner:
    def run(argv: list[str], **_kw: Any) -> int:
        ran.append(argv)
        return 0

    job = SimpleNamespace(id="python-suite", workers=workers, mem_gb=None, env={"X": "1"})
    ticks = iter(range(0, 10_000, 90))  # each run takes 90 s on this clock
    return select.Runner(
        run=run,
        memory=memory,
        cpus=8,
        on_github=False,
        job=lambda _r: job,
        peaks=None,
        clock=lambda: float(next(ticks)),
    )


@pytest.mark.parametrize(
    "workers, on_github, given, count",
    [
        ("auto", False, [], "2"),
        ("auto", True, [], "8"),
        (3, False, [], "3"),
        ("auto", False, ["--workers", "6"], "6"),
    ],
)
def test_run_admits_the_workers_every_local_run_reads_and_runs_pytest_on_the_list(
    tmp_path: Path, workers: Any, on_github: bool, given: list[str], count: str
) -> None:
    root = _clone(tmp_path, dict(BASE))
    ran: list[list[str]] = []
    runner = _runner(_Memory(free=32_000), ran, workers=workers)
    runner.on_github = on_github
    code = select.main(
        ["--root", str(root), "--paths", "src/personalclaw/other.py", "--run", *given],
        runner=runner,
    )
    assert code == 0
    argv = ran[0]
    assert argv[argv.index("-n") + 1] == count
    assert argv[-2:] == ["--shard-list", str(root / ".local/select/tests.txt")]


def test_run_takes_two_workers_when_memory_cannot_be_read_and_gives_up_when_it_never_fits(
    tmp_path: Path,
) -> None:
    root = _clone(tmp_path, dict(BASE))
    ran: list[list[str]] = []
    args = ["--root", str(root), "--paths", "src/personalclaw/other.py", "--run"]
    assert select.main(args, runner=_runner(_Memory(free=None), ran)) == 0
    assert ran[0][ran[0].index("-n") + 1] == str(select.BARE_WORKERS)
    assert select.main(args, runner=_runner(_Memory(free=100, total=1_000), ran)) == 2
    assert len(ran) == 1


def test_a_whole_suite_run_says_why_and_how_long_to_expect_before_it_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(tmp_path, dict(BASE))
    ran: list[list[str]] = []
    args = ["--root", str(root), "--paths", "pyproject.toml", "--run"]
    assert select.main(args, runner=_runner(_Memory(free=32_000), ran)) == 0
    said = capsys.readouterr().err
    first = next(line for line in said.splitlines() if "the whole suite runs" in line)
    assert "pyproject.toml changed" in first and "from CI's shard time" in first
    assert said.index("the whole suite runs") < said.index("-m pytest")
    # The run was timed, so the next estimate reads this machine's own.
    assert select.main(args, runner=_runner(_Memory(free=32_000), ran)) == 0
    assert "from the last 1 timed run(s) here" in capsys.readouterr().err


def test_the_estimate_scales_this_machines_rate_to_the_files_and_workers(tmp_path: Path) -> None:
    times = tmp_path / select.TIMES
    times.parent.mkdir(parents=True)
    times.write_text('[{"files": 100, "workers": 2, "seconds": 600}]', encoding="utf-8")
    # 12 worker-seconds a file: 1,000 files at 4 workers is 50 minutes.
    assert select.expected(tmp_path, 1000, 4, 2500).startswith("about 50 minutes at 4 workers")
    times.write_text("not json", encoding="utf-8")
    assert "from CI's shard time" in select.expected(tmp_path, 2500, 2, 2500)


def test_run_past_eight_hundred_files_runs_the_whole_suite(tmp_path: Path) -> None:
    files = dict(BASE)
    for i in range(select.OVER + 1):
        files[f"tests/test_{i:04}.py"] = "import personalclaw.a\n"
    files["tests/test_unrelated.py"] = ""
    root = _clone(tmp_path, files)
    ran: list[list[str]] = []
    code = select.main(
        ["--root", str(root), "--paths", "src/personalclaw/a.py", "--run"],
        runner=_runner(_Memory(free=32_000), ran),
    )
    assert code == 0
    listed = (root / ".local/select/tests.txt").read_text(encoding="utf-8").split()
    # The reachable files nearest first, then the rest of the suite.
    assert len(listed) == select.OVER + 3
    assert listed[0] == "tests/test_0000.py" and listed[-1] == "tests/test_unrelated.py"


def test_run_with_nothing_to_run_runs_nothing_and_passes(tmp_path: Path) -> None:
    root = _clone(tmp_path, {**BASE, "docs/x.md": ""})
    ran: list[list[str]] = []
    code = select.main(
        ["--root", str(root), "--paths", "docs/x.md", "--run"], runner=_runner(_Memory(1), ran)
    )
    assert code == 0 and ran == []
