"""``tests/shard_list.py``: ``--shard-list FILE`` collects only the files a shard's list names."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import nested_pytest

TESTS = Path(__file__).resolve().parent

CONFTEST = f"""
import sys
sys.path.insert(0, {str(TESTS)!r})
import shard_list


def pytest_addoption(parser):
    shard_list.addoption(parser)


def pytest_configure(config):
    shard_list.configure(config)


def pytest_ignore_collect(collection_path, config):
    return shard_list.ignore_collect(collection_path, config)


def pytest_collection_finish(session):
    shard_list.collection_finish(session)


def pytest_sessionfinish(session):
    shard_list.session_finish(session)


def pytest_terminal_summary(terminalreporter):
    shard_list.report(terminalreporter)
"""


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    tests = tmp_path / "tests"
    (tests / "sub").mkdir(parents=True)
    (tests / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    for rel in ("test_a.py", "test_b.py", "sub/test_c.py", "d_test.py"):
        (tests / rel).write_text("def test_it():\n    pass\n", encoding="utf-8")
    return tmp_path


def _collect(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`tests` is named, as the workflow names it: a conftest's option registers only when its
    folder is on the command line."""
    return subprocess.run(
        [
            *nested_pytest.argv(root),
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        + ["-o", "addopts=", "tests", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _ids(done: subprocess.CompletedProcess[str]) -> list[str]:
    return sorted(line for line in done.stdout.splitlines() if "::" in line)


def test_it_collects_only_the_listed_files_with_their_usual_node_ids(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "list.txt").write_text("tests/test_b.py\ntests/sub/test_c.py\n", encoding="utf-8")
    done = _collect(root, "--shard-list", "list.txt")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _ids(done) == ["tests/sub/test_c.py::test_it", "tests/test_b.py::test_it"]


def test_without_the_option_every_file_is_collected(tmp_path: Path) -> None:
    done = _collect(_tree(tmp_path))
    assert done.returncode == 0
    assert len(_ids(done)) == 4


def test_an_empty_or_missing_list_fails_rather_than_selecting_everything(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "empty.txt").write_text("\n", encoding="utf-8")
    for named in ("empty.txt", "missing.txt"):
        done = _collect(root, "--shard-list", named)
        assert done.returncode != 0, named
        assert _ids(done) == [], named


def test_a_listed_file_collection_never_reaches_fails_the_run(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "list.txt").write_text("tests/test_a.py\ntests/test_gone.py\n", encoding="utf-8")
    done = _collect(root, "--shard-list", "list.txt")
    assert done.returncode != 0
    assert "tests/test_gone.py" in done.stdout + done.stderr


def _run(
    root: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [
            *nested_pytest.argv(root),
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
        ]
        + ["tests", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


_LINE = re.compile(
    r"^(gw\d+|main): collected in (\d+\.\d+)s, peak RSS (\d+) MB, (\d+) MB over the run$", re.M
)


def test_each_worker_reports_its_collection_seconds_and_peak_memory(tmp_path: Path) -> None:
    """A shard's log names, for each xdist worker, how long it took to collect its files and the
    most memory it held, so a run shows both against #3923's bars (30 s and 600 MB)."""
    root = _tree(tmp_path)
    (root / "list.txt").write_text("tests/test_a.py\ntests/test_b.py\n", encoding="utf-8")
    summary = root / "summary.md"
    done = _run(
        root, "--shard-list", "list.txt", "-n", "2", env={"GITHUB_STEP_SUMMARY": str(summary)}
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "shard collection and memory" in done.stdout
    workers = {
        m.group(1): (float(m.group(2)), int(m.group(3))) for m in _LINE.finditer(done.stdout)
    }
    assert set(workers) == {"gw0", "gw1"}, done.stdout
    assert all(0 < mb < 100_000 for _, mb in workers.values()), workers
    text = summary.read_text(encoding="utf-8")
    assert "gw0" in text and "peak RSS" in text


def test_a_run_without_workers_reports_its_own_process(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "list.txt").write_text("tests/test_a.py\n", encoding="utf-8")
    done = _run(root, "--shard-list", "list.txt")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [m.group(1) for m in _LINE.finditer(done.stdout)] == ["main"], done.stdout


def test_without_a_shard_list_nothing_is_reported(tmp_path: Path) -> None:
    done = _run(_tree(tmp_path))
    assert done.returncode == 0
    assert "shard collection and memory" not in done.stdout


# ── --shard-ids: the ids a run collected, for breadth's union check ───────────────────────────


def _written_ids(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_shard_ids_writes_every_id_a_run_without_workers_collected(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "list.txt").write_text("tests/test_b.py\ntests/sub/test_c.py\n", encoding="utf-8")
    out = root / "ids.txt"
    done = _collect(root, "--shard-list", "list.txt", "--shard-ids", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    assert _written_ids(out) == ["tests/sub/test_c.py::test_it", "tests/test_b.py::test_it"]


def test_shard_ids_writes_the_ids_the_workers_collected_once(tmp_path: Path) -> None:
    """Every xdist worker collects the shard's whole list; the file names each id once, as the
    controller heard it, not once per worker."""
    root = _tree(tmp_path)
    (root / "list.txt").write_text("tests/test_a.py\ntests/test_b.py\n", encoding="utf-8")
    out = root / "ids.txt"
    done = _run(root, "--shard-list", "list.txt", "--shard-ids", str(out), "-n", "2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _written_ids(out) == ["tests/test_a.py::test_it", "tests/test_b.py::test_it"]


def test_shard_ids_names_a_parametrized_case_and_a_class_test_by_their_node_ids(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    (root / "tests" / "test_p.py").write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('x', [1, 2])\ndef test_p(x):\n    pass\n\n\n"
        "class TestK:\n    def test_k(self):\n        pass\n",
        encoding="utf-8",
    )
    (root / "list.txt").write_text("tests/test_p.py\n", encoding="utf-8")
    out = root / "ids.txt"
    assert _collect(root, "--shard-list", "list.txt", "--shard-ids", str(out)).returncode == 0
    assert _written_ids(out) == [
        "tests/test_p.py::TestK::test_k",
        "tests/test_p.py::test_p[1]",
        "tests/test_p.py::test_p[2]",
    ]


def test_without_shard_ids_no_file_is_written(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "list.txt").write_text("tests/test_a.py\n", encoding="utf-8")
    assert _collect(root, "--shard-list", "list.txt").returncode == 0
    assert not list(root.glob("*ids*"))


def test_shard_ids_names_what_a_red_run_collected(tmp_path: Path) -> None:
    """The file is written when collection ends, so a shard with a failing test still names
    every id it collected, the failing one too, with workers and without."""
    root = _tree(tmp_path)
    (root / "tests" / "test_red.py").write_text("def test_red():\n    assert False\n", "utf-8")
    (root / "list.txt").write_text("tests/test_a.py\ntests/test_red.py\n", encoding="utf-8")
    for workers in ((), ("-n", "2")):
        out = root / f"ids-{len(workers)}.txt"
        done = _run(root, "--shard-list", "list.txt", "--shard-ids", str(out), *workers)
        assert done.returncode != 0, done.stdout + done.stderr
        assert _written_ids(out) == ["tests/test_a.py::test_it", "tests/test_red.py::test_red"]


#: A plugin that holds the first test twice, as one that rewrites the run's items could.
_TWICE = "\n\ndef pytest_collection_modifyitems(items):\n    items.append(items[0])\n"


def test_shard_ids_names_an_id_the_run_holds_twice_twice(tmp_path: Path) -> None:
    """A run that holds one test twice runs it twice, so the file names it twice, and the union
    sees an id one shard collected twice, with workers and without."""
    root = _tree(tmp_path)
    conftest = root / "tests" / "conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + _TWICE, encoding="utf-8")
    (root / "list.txt").write_text("tests/test_a.py\ntests/test_b.py\n", encoding="utf-8")
    for workers in ((), ("-n", "2")):
        out = root / f"ids-{len(workers)}.txt"
        done = _run(root, "--shard-list", "list.txt", "--shard-ids", str(out), *workers)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "3 passed" in done.stdout, done.stdout
        assert _written_ids(out) == [
            "tests/test_a.py::test_it",
            "tests/test_a.py::test_it",
            "tests/test_b.py::test_it",
        ]


#: Each xdist worker records whether it holds the id writer, by its worker id.
_PROBE = (
    "\n\ndef pytest_collection_modifyitems(config, items):\n"
    "    import os, pathlib\n"
    "    worker = config.workerinput['workerid']\n"
    "    held = config.pluginmanager.hasplugin('shard-list-ids')\n"
    "    pathlib.Path(os.environ['SHARD_IDS_PROBE'], worker).write_text(str(held))\n"
)


def test_an_xdist_worker_writes_no_ids_its_controller_does(tmp_path: Path) -> None:
    """Only the controller holds the id writer, so one process writes the file, never a worker
    beside it."""
    root = _tree(tmp_path)
    conftest = root / "tests" / "conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + _PROBE, encoding="utf-8")
    (root / "list.txt").write_text("tests/test_a.py\ntests/test_b.py\n", encoding="utf-8")
    probe, out = root / "probe", root / "ids.txt"
    probe.mkdir()
    done = _run(
        root,
        *("--shard-list", "list.txt", "--shard-ids", str(out), "-n", "2"),
        env={"SHARD_IDS_PROBE": str(probe)},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert {p.name: p.read_text(encoding="utf-8") for p in probe.iterdir()} == {
        "gw0": "False",
        "gw1": "False",
    }
    assert _written_ids(out) == ["tests/test_a.py::test_it", "tests/test_b.py::test_it"]
