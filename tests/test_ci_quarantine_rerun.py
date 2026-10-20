"""``python -m tools.ci quarantine-rerun``: each quarantined test, run ten times, for the weekly CI
health report (#3948).

The unit cases hold how a JUnit case is named after a test's pytest id and what a case's result
is. The last case runs a scratch suite through the command itself: a test that fails every
other round, one pytest reports as an expected failure (as the quarantine marks a listed test),
a parametrized case whose id holds a space, and an entry naming a test that does not exist.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools.ci import quarantine_rerun, report


@pytest.mark.parametrize(
    ("nodeid", "case"),
    [
        ("tests/test_x.py::test_y", ("tests.test_x", "test_y")),
        ("tests/test_x.py::TestA::test_y", ("tests.test_x.TestA", "test_y")),
        (
            "tests/sub/test_x.py::TestA::TestB::test_y[a b]",
            ("tests.sub.test_x.TestA.TestB", "test_y[a b]"),
        ),
    ],
    ids=["a-function", "a-method", "nested-with-a-case"],
)
def test_a_tests_junit_case_is_named_as_pytest_names_it(nodeid: str, case: tuple) -> None:
    assert quarantine_rerun.junit_case(nodeid) == case


@pytest.mark.parametrize(
    ("xml", "result"),
    [
        ("<testcase/>", "passed"),
        ('<testcase><failure message="x"/></testcase>', "failed"),
        ('<testcase><error message="x"/></testcase>', "failed"),
        ('<testcase><skipped type="pytest.xfail" message="quarantined"/></testcase>', "failed"),
        ('<testcase><skipped type="pytest.skip" message="no"/></testcase>', "skipped"),
    ],
    ids=["passed", "failed", "errored", "a-quarantined-failure", "skipped"],
)
def test_a_cases_result(xml: str, result: str) -> None:
    """A quarantined test is marked an expected failure, so its failure reports as one: that is
    a failure here."""
    assert quarantine_rerun.result_of(ET.fromstring(xml)) == result


def test_an_empty_quarantine_runs_nothing(tmp_path: Path) -> None:
    out = tmp_path / "reruns.json"
    listed = tmp_path / "quarantine.toml"
    listed.write_text("# nothing listed\n", encoding="utf-8")
    code = quarantine_rerun.main(["--quarantine", str(listed), "--out", str(out)])
    assert code == 0
    assert json.loads(out.read_text("utf-8")) == {
        "schema": report.RERUN_SCHEMA,
        "rounds": quarantine_rerun.ROUNDS,
        "tests": {},
    }


_SUITE = """\
import pathlib

import pytest

COUNT = pathlib.Path(__file__).with_name("rounds")


def _round():
    n = int(COUNT.read_text()) if COUNT.exists() else 0
    COUNT.write_text(str(n + 1))
    return n


def test_every_other_round():
    assert _round() % 2 == 1


@pytest.mark.xfail(strict=False, reason="as the quarantine marks a listed test")
def test_marked():
    assert False


@pytest.mark.parametrize("case", ["a b", "c"])
def test_cases(case):
    assert case
"""


def test_the_rounds_tally_each_listed_test(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_q.py").write_text(_SUITE, encoding="utf-8")
    listed = root / "quarantine.toml"
    entries = [
        "tests/test_q.py::test_every_other_round",
        "tests/test_q.py::test_marked",
        "tests/test_q.py::test_cases[a b]",
    ]
    listed.write_text(
        "".join(
            f'[[entry]]\nnodeid = "{n}"\nissue = 4000\nassignee = "someone"\n'
            f'expires = 2026-11-01\nreason = "flaky"\n'
            for n in entries
        ),
        encoding="utf-8",
    )
    out = tmp_path / "reruns.json"
    code = quarantine_rerun.main(
        ["--quarantine", str(listed), "--rounds", "4", "--root", str(root), "--out", str(out)]
        + ["--python", sys.executable]
    )
    assert code == 0
    doc = json.loads(out.read_text("utf-8"))
    assert doc["rounds"] == 4
    assert doc["tests"] == {
        "tests/test_q.py::test_every_other_round": {
            "passed": 2,
            "failed": 2,
            "skipped": 0,
            "not_run": 0,
        },
        "tests/test_q.py::test_marked": {"passed": 0, "failed": 4, "skipped": 0, "not_run": 0},
        "tests/test_q.py::test_cases[a b]": {"passed": 4, "failed": 0, "skipped": 0, "not_run": 0},
    }
    assert report.read_reruns(out) == doc["tests"]


def test_a_round_pytest_refuses_runs_none_of_its_tests(tmp_path: Path) -> None:
    """A round that ran nothing (here an id pytest cannot find; in this repository a quarantine
    list the suite refuses, which stops every session) counts each test as not run, never as
    passed."""
    root = tmp_path / "suite"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_q.py").write_text(_SUITE, encoding="utf-8")
    listed = root / "quarantine.toml"
    listed.write_text(
        "".join(
            f'[[entry]]\nnodeid = "{n}"\nissue = 4000\nassignee = "someone"\n'
            f'expires = 2026-11-01\nreason = "flaky"\n'
            for n in ("tests/test_q.py::test_marked", "tests/test_q.py::test_gone")
        ),
        encoding="utf-8",
    )
    out = tmp_path / "reruns.json"
    code = quarantine_rerun.main(
        ["--quarantine", str(listed), "--rounds", "2", "--root", str(root), "--out", str(out)]
        + ["--python", sys.executable]
    )
    assert code == 0
    never = {"passed": 0, "failed": 0, "skipped": 0, "not_run": 2}
    assert json.loads(out.read_text("utf-8"))["tests"] == {
        "tests/test_q.py::test_marked": never,
        "tests/test_q.py::test_gone": never,
    }


def test_a_round_out_of_time_leaves_its_tests_and_the_rest_not_run(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8"
    )
    listed = root / "quarantine.toml"
    listed.write_text(
        '[[entry]]\nnodeid = "tests/test_slow.py::test_slow"\nissue = 1\nassignee = "a"\n'
        'expires = 2026-11-01\nreason = "slow"\n',
        encoding="utf-8",
    )
    out = tmp_path / "reruns.json"
    code = quarantine_rerun.main(
        ["--quarantine", str(listed), "--rounds", "3", "--root", str(root), "--out", str(out)]
        + ["--python", sys.executable, "--budget-seconds", "3"]
    )
    assert code == 0
    tally = json.loads(out.read_text("utf-8"))["tests"]["tests/test_slow.py::test_slow"]
    assert tally == {"passed": 0, "failed": 0, "skipped": 0, "not_run": 3}
