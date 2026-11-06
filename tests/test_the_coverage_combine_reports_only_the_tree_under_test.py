"""Breadth's coverage combine reports only the tree under test (``tools/ci/coverage_combine.sh``).

A child that a test starts while the suite is measured records every ``personalclaw`` it imports,
including one the test planted in its own temporary folder. That folder is gone by the time the
combine job runs. Each test here runs the script as the job does, over six shards' data, in a
checkout of its own: this repository's coverage settings and a small tree, with ``uv run`` standing
for the job's locked environment.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "ci" / "coverage_combine.sh"

#: The tree's files and their source: two statements in the first, three in the second.
_TREE = {
    "src/personalclaw/__init__.py": "a = 1\nb = 2\n",
    "src/personalclaw/kept.py": "def f():\n    return 1\n\n\nc = f()\n",
}


def _checkout(where: Path) -> None:
    where.mkdir(parents=True, exist_ok=True)
    (where / "pyproject.toml").write_text(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for name, source in _TREE.items():
        (where / name).parent.mkdir(parents=True, exist_ok=True)
        (where / name).write_text(source, encoding="utf-8")


def _shards(where: Path, fifth: dict[str, list[int]]) -> None:
    """Six shards' data: the first runs both of ``__init__.py``'s lines, the second two of
    ``kept.py``'s three, the rest one line, and the fifth also *fifth*."""
    data_dir = where / "coverage-data"
    data_dir.mkdir()
    lines = {
        1: {"src/personalclaw/__init__.py": [1, 2]},
        2: {"src/personalclaw/kept.py": [1, 5]},
        5: {"src/personalclaw/__init__.py": [1], **fifth},
    }
    for shard in range(1, 7):
        data = CoverageData(basename=str(data_dir / f".coverage.shard{shard}"))
        data.add_lines(lines.get(shard, {"src/personalclaw/__init__.py": [1]}))
        data.write()


def _combine(where: Path) -> subprocess.CompletedProcess[str]:
    """The script, in *where*, as breadth's ``coverage`` job runs it. Its ``uv run`` is this
    interpreter, and nothing of this run's own measurement reaches it."""
    bin_dir = where / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f'#!/bin/sh\n[ "$1" = run ] && shift\nexec {shlex.quote(sys.executable)} -m "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COVERAGE_", "COV_CORE_"))}
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=where,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_planted_personalclaw_whose_folder_is_gone_leaves_the_total_as_the_tree_reads(
    tmp_path: Path,
) -> None:
    """🔴 Red before: the fifth shard measured a ``personalclaw/__init__.py`` that a test planted,
    and the combine failed the whole report on it, "No source for code" (run 42170607590). Here
    the fifth shard measures one outside the checkout, in a folder that no longer exists. The
    total is the tree's own: four of its five statements ran, and the XML names the tree's two
    files and no other."""
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    gone = tmp_path / "gone" / "other-clone" / "personalclaw" / "__init__.py"
    _shards(checkout, {str(gone): [1]})
    done = _combine(checkout)
    said = done.stdout + done.stderr
    assert done.returncode == 0, said
    assert "coverage of the whole suite: 80%" in said, said
    xml = (checkout / "coverage.xml").read_text(encoding="utf-8")
    assert 'lines-valid="5" lines-covered="4"' in xml, xml
    assert xml.count("<class ") == len(_TREE), xml
    assert "other-clone" not in xml, xml


def test_a_file_of_the_tree_with_no_source_still_fails_the_report(tmp_path: Path) -> None:
    """Only files outside the tree are passed over: one of the tree's own with no source still
    fails the report, as before."""
    _checkout(tmp_path)
    _shards(tmp_path, {"src/personalclaw/missing.py": [1]})
    done = _combine(tmp_path)
    said = done.stdout + done.stderr
    assert done.returncode != 0, said
    assert "No source for code" in said and "src/personalclaw/missing.py" in said, said
