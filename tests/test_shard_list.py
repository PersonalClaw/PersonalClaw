"""``tests/shard_list.py``: ``--shard-list FILE`` collects only the files a shard's list names."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
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
