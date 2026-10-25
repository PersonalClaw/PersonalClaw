"""The rails' read record (tests/rails_reads.py), recorded from a planted repository's rails.

A child pytest runs the planted rails, with and without xdist, under a conftest that arms the
recorder as ``tests/conftest.py`` does. Each rail reads one way a rail can read: a file at
collection, a stat of a path not there, a folder listed, a source index walk, a helper's cached
answer, a child started, a module not found. One fails. The record must hold each read against
its rail and none against the others, and ``rails select`` must then pick each rail by its read.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import rails_reads

from tools.ci import rails_select

_HERE = Path(__file__).resolve().parent

_FILES = {
    "pyproject.toml": """
        [tool.pytest.ini_options]
        python_files = ["test_*.py"]
        """,
    "conftest.py": """
        import os
        import sys

        sys.path.insert(0, os.environ["RAILS_READS_HOME"])

        import planted_index
        import pytest
        import rails_reads

        from pkg import fenced


        def pytest_configure(config):
            rails_reads.configure(config, planted_index)


        def pytest_ignore_collect(collection_path, config):
            # As shard_list's does: a stat of every file the collection walk passes.
            collection_path.is_file()


        @pytest.fixture(autouse=True)
        def _fence():
            # As conftest's registry fences do: a package module put back around every test.
            yield
            fenced.reset()
        """,
    "pkg/__init__.py": "",
    "pkg/fenced.py": """
        from pathlib import Path

        STATE = {}


        def reset():
            STATE.clear()
            STATE["default"] = (Path(__file__).parent / "fenced.txt").read_text()
        """,
    "pkg/fenced.txt": "default\n",
    "planted_index.py": """
        # The source index's contract with the recorder: its roots, their suffixes, and an index
        # whose observers hear each walk.
        from pathlib import Path

        ROOTS = {"package": "pkg", "docs": "docs"}
        SUFFIXES = {"package": ".py"}


        class _Index:
            def __init__(self):
                self.observers = []

            def files(self, name):
                for observer in self.observers:
                    observer("walk", name)
                return sorted((Path(__file__).parent / ROOTS[name]).rglob("*"))


        INDEX = _Index()
        """,
    "helper.py": """
        import functools
        from pathlib import Path

        ROOT = Path(__file__).parent


        @functools.cache
        def shared():
            return (ROOT / "data" / "shared.txt").read_text()
        """,
    "uv.lock": "",
    "data/shared.txt": "shared\n",
    "data/a.txt": "a\n",
    "data/listed/one.txt": "one\n",
    "docs/one.md": "one\n",
    "tests/rails/test_a.py": """
        from pathlib import Path

        import helper
        import planted_index

        ROOT = Path(__file__).parents[2]
        AT_COLLECTION = (ROOT / "data" / "a.txt").read_text()


        def test_reads_at_collection_stats_lists_and_walks():
            assert AT_COLLECTION == "a\\n"
            assert not (ROOT / "data" / "missing.txt").exists()
            assert [p.name for p in (ROOT / "data" / "listed").iterdir()] == ["one.txt"]
            assert planted_index.INDEX.files("docs")
            assert helper.shared() == "shared\\n"
        """,
    "tests/rails/test_b.py": """
        import helper


        def test_reads_through_the_helpers_cache():
            assert helper.shared() == "shared\\n"
        """,
    "tests/rails/test_c.py": """
        import subprocess
        import sys


        def test_starts_a_child():
            subprocess.run([sys.executable, "-c", "pass"], check=True)
        """,
    "tests/rails/test_d.py": """
        def test_fails():
            assert False
        """,
    "tests/test_beside.py": """
        def test_not_a_rail():
            pass
        """,
    "tests/rails/test_f.py": """
        from pkg import fenced


        def test_reads_the_fenced_module():
            fenced.reset()
            assert fenced.STATE["default"] == "default\\n"
        """,
    "tests/rails/test_e.py": """
        def test_looks_for_a_module_that_is_not_there():
            try:
                import planted_optional  # noqa: F401
            except ModuleNotFoundError:
                pass
        """,
}

A, B, C, D, E, F = (f"tests/rails/test_{x}.py" for x in "abcdef")


def _plant(root: Path) -> None:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "p"],
        check=True,
    )


def _run_planted(root: Path, workers: str, **given: str) -> subprocess.CompletedProcess[str]:
    """The planted rails, as a rails leg runs them, in an environment of *given* GitHub
    variables (and no recorder setting of this run's own)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "GITHUB_", rails_reads.FORCE))
    }
    env.update(RAILS_READS_HOME=str(_HERE), **given)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            workers,
            "tests/rails",
            "--junitxml=reports/junit-rails-p.xml",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


#: A push to main, the one run whose record the selector reads.
_MAIN_PUSH = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"}


@pytest.fixture(scope="module", params=["0", "2"], ids=["one-process", "xdist"])
def recorded(request, tmp_path_factory) -> tuple[Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("planted")
    _plant(root)
    done = _run_planted(root, request.param, **_MAIN_PUSH)
    assert done.returncode == 1, done.stdout + done.stderr  # test_d fails, the rest pass
    record = json.loads((root / "reports" / "rails-reads-p.json").read_text(encoding="utf-8"))
    return root, record


def test_a_pull_request_s_run_records(tmp_path) -> None:
    """Since the flip a tested push runs no rails, so a merged pull request's run is the record
    of its main commit: a pull request's leg records as a push to main's does."""
    _plant(tmp_path)
    done = _run_planted(
        tmp_path, "0", GITHUB_EVENT_NAME="pull_request", GITHUB_REF="refs/pull/12/merge"
    )
    assert done.returncode == 1, done.stdout + done.stderr
    record = json.loads((tmp_path / "reports" / "rails-reads-p.json").read_text(encoding="utf-8"))
    assert record["legs"] == ["p"] and record["rails"], record


@pytest.mark.parametrize(
    "given",
    [
        {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/1/head"},
        {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/heads/main"},
        {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/a-branch"},
        {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main"},
        {},
    ],
    ids=[
        "pull-request-head",
        "pull-request-on-a-branch-ref",
        "push-to-a-branch",
        "dispatch-on-main",
        "off-github",
    ],
)
def test_only_a_push_to_main_or_a_pull_request_s_merge_records(tmp_path, given) -> None:
    """A leg of any other run writes no record and pays none of the recorder's time: only main's
    push runs' and pull requests' merge runs' records are read. :data:`rails_reads.FORCE` arms
    one made by hand."""
    _plant(tmp_path)
    done = _run_planted(tmp_path, "0", **given)
    assert done.returncode == 1, done.stdout + done.stderr
    assert (tmp_path / "reports" / "junit-rails-p.xml").is_file()
    assert not (tmp_path / "reports" / "rails-reads-p.json").exists()
    assert rails_reads.armed({**given, rails_reads.FORCE: "1"})
    assert not rails_reads.armed(given)


def test_each_rail_s_reads_are_its_own(recorded) -> None:
    _root, record = recorded
    assert "error" not in record, record.get("error")
    rails = record["rails"]
    assert set(rails) == {A, B, C, E, F}
    a = rails[A]
    assert {"data/a.txt", "data/missing.txt", "data/shared.txt", "helper.py", A} <= set(a["files"])
    assert a["folders"] == ["data/listed"]
    assert a["walks"] == [["docs", ""]]
    assert a["spawns"] == []
    # helper.shared() answered test_b from its cache, and the file still counts as test_b's read.
    assert "data/shared.txt" in rails[B]["files"]
    assert "data/a.txt" not in rails[B]["files"] and rails[B]["folders"] == []
    assert rails[C]["spawns"] == [Path(sys.executable).name]
    assert {"planted_optional.py", "tests/rails/planted_optional.py"} & set(rails[E]["files"])


def test_a_failed_rail_is_unrecorded_and_the_setup_is_the_harness(recorded) -> None:
    _root, record = recorded
    assert record["unrecorded"] == {D: f"{D}::test_fails failed in its call"}
    harness = record["harness"]
    assert {"conftest.py", "planted_index.py", "pyproject.toml", "uv.lock"} <= set(harness["files"])
    assert "data/a.txt" not in harness["files"]
    # The package module the harness's fence imports, and what its code reads, are not the
    # harness's own: they are the rails' that read them.
    assert not [f for f in harness["files"] if f.startswith("pkg/")], harness["files"]
    assert {"pkg/fenced.py", "pkg/fenced.txt"} <= set(record["rails"][F]["files"])
    # pytest's own collection walk is no rail's input, nor the harness's stats along it.
    assert harness["folders"] == []
    assert "tests/test_beside.py" not in harness["files"], harness["files"]


def test_rails_select_picks_each_rail_by_its_read(recorded) -> None:
    root, data = recorded
    record = rails_select.load(data)
    tree = rails_select._tree(root, record.commit)
    assert tree is not None and "data/a.txt" in tree
    rails = [A, B, C, D, E, F]
    now = record.recorded_at + datetime.timedelta(minutes=1)

    def chosen(*paths: str) -> set[str]:
        found = rails_select.select(
            paths, rails, record, now=now, tree=tree, exists=lambda p: (root / p).exists()
        )
        return set(found)

    always = {C, D}  # one starts a child, one failed
    assert chosen("data/a.txt") == always | {A}
    assert chosen("data/shared.txt") == always | {A, B}
    assert chosen("data/listed/two.txt") == always | {A}
    assert chosen("docs/two.md") == always | {A}
    assert chosen("data/missing.txt") == always | {A}
    assert chosen("tests/rails/planted_optional.py", "planted_optional.py") == always | {E}
    assert chosen("data/unread.txt") == always
    assert chosen("conftest.py") == set(rails)
    # Every test runs the fence, yet the module it imports selects only the rail that reads it.
    assert chosen("pkg/fenced.py") == always | {F}
    assert chosen("pkg/fenced.txt") == always | {F}
