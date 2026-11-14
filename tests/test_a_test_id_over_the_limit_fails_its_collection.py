"""A test whose node id is over 1,000 characters fails its module's collection, by name.

🔴 The defect. Nothing bounded how long a test's node id could be. A parametrize call that gives
no ids gets each case's id from its values, whole: three upload cases once carried a whole file in
theirs (359,560 to 894,546 characters), every log line that named them carried it, and
``gh run view --log`` stopped at the first such line, so coverage runs that finished read as hung.

The contract. ``tests/conftest.py`` bounds a node id at ``NODE_ID_LIMIT``, 1,000 characters. A
module or class holding a test past it fails its collection ("ERROR collecting <file>"), naming
each such test by its file, its line and the first 80 characters of its id, never the whole id,
and the test is never collected. A failed collect report, not an exception: one raised from an
xdist worker's collection ends the run as an INTERNALERROR that names some other test, and not
why. A case named with ``ids=`` is collected under its name, and an id of exactly 1,000 characters
is collected whole.

This file runs a session of its own, with the suite's conftest, over three planted modules. Their
sizes are spelled out here rather than read from the conftest, so that a tree without the bound
fails on what its run does.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import nested_pytest

_TESTS_DIR = Path(__file__).resolve().parent

# The decorator is on line 4, where each test's location points.
_MODULE = (
    "import pytest\n"
    "\n"
    "\n"
    '@pytest.mark.parametrize("blob", [{values}]{ids})\n'
    "def test_it(blob):\n"
    "    pass\n"
)


def _plant(folder: Path, name: str, values: str, ids: str = "") -> None:
    (folder / name).write_text(_MODULE.format(values=values, ids=ids))


def test_a_case_whose_id_is_over_the_limit_fails_its_modules_collection_by_name(tmp_path):
    # 978 plus len("test_long.py::test_it[]"), 23, is 1,001: one past the bound.
    _plant(tmp_path, "test_long.py", '"y" * 978, b"x" * 5000')
    _plant(tmp_path, "test_named.py", 'b"x" * 5000', ', ids=["blob"]')
    # 977 plus len("test_edge.py::test_it[]"), 23, is 1,000: the bound itself.
    _plant(tmp_path, "test_edge.py", '"z" * 977')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    proc = subprocess.run(
        [*nested_pytest.argv(tmp_path), "--rootdir", str(tmp_path), "-o", "addopts="]
        + ["-p", "no:cacheprovider", "-p", "conftest", "--collect-only", "-q", "--color=no"]
        + ["test_long.py", "test_named.py", "test_edge.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (proc.returncode, out[-3000:])
    assert "ERROR collecting test_long.py" in out, out[-3000:]
    assert "test_long.py:4: test_long.py::test_it[" + "y" * 58 + "… (1,001 characters)" in out
    assert "test_long.py:4: test_long.py::test_it[" + "x" * 58 + "… (5,023 characters)" in out
    assert "y" * 978 not in out
    assert "x" * 1000 not in out
    assert "test_named.py::test_it[blob]" in out, out[-3000:]
    assert "test_edge.py::test_it[" + "z" * 977 + "]" in out, out[-3000:]
