"""``tests/table_rows.py``: a table row is reported as a subtest only when it does not pass.

The planted table below is run by a pytest of its own, so what is checked is what pytest prints.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

TESTS = Path(__file__).resolve().parent

_TABLE = """
    import pytest

    from table_rows import row

    ROWS = ["a", "b", "c", "d"]
    RAN = []


    def test_the_table(subtests):
        for name in ROWS:
            with row(subtests, name=name):
                RAN.append(name)
                if name == "b":
                    pytest.skip("b has nothing to check")
                assert name != "c", f"{name!r} is the planted failure"


    def test_every_row_ran_after_the_failure():
        assert RAN == ["a", "b", "c", "d"]
"""


def _run(tmp_path: Path) -> str:
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --verbose\n", encoding="utf-8")
    (tmp_path / "test_planted_table.py").write_text(textwrap.dedent(_TABLE), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(TESTS), env.get("PYTHONPATH", "")) if p)
    ran = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly"]
        + ["-rA", "--color=no", "-n", "0", "test_planted_table.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return ran.stdout + ran.stderr


def test_a_failing_row_is_its_own_subtest_and_the_rows_after_it_still_run(tmp_path: Path) -> None:
    said = _run(tmp_path)
    assert "SUBFAILED(name='c')" in said, said
    assert "'c' is the planted failure" in said, said
    assert "test_every_row_ran_after_the_failure PASSED" in said, said


def test_a_skipped_row_is_reported_as_that_row(tmp_path: Path) -> None:
    said = _run(tmp_path)
    assert "SUBSKIPPED(name='b')" in said, said
    assert "b has nothing to check" in said, said


def test_a_passing_row_reports_nothing(tmp_path: Path) -> None:
    said = _run(tmp_path)
    assert "SUBPASSED" not in said, said
    assert "name='a'" not in said and "name='d'" not in said, said
