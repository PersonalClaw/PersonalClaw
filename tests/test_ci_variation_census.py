"""``tools/ci/variation_census.py``: where the suite pays most for rows that could share a test.

The planted test modules below are collected, and run for a junit report, by a pytest of their
own, so the census reads what pytest really prints and writes. The durations the ranking is
checked on come from a junit report with chosen times. Nothing here reads the real suite.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import time
from pathlib import Path

import nested_pytest
import pytest
import table_rows

from tools.ci import __main__ as ci_main
from tools.ci import variation_census as census

#: The suite's own ``addopts`` start with ``--verbose``, which is why ``--collect-only -qq`` prints
#: one id per line here (with no ``--verbose`` it prints a count per file).
_INI = (
    "[pytest]\ntestpaths = tests\naddopts = --verbose\n"
    "markers =\n    security: proves a fail-closed boundary\n"
)

#: Planted modules: (relative path, source). Their names say how many cases each function has.
_MODULES = {
    "tests/test_wide.py": """
        import pytest

        pytestmark = pytest.mark.security

        @pytest.mark.parametrize("n", range(25))
        def test_wide(n):
            assert n >= 0
    """,
    "tests/test_edge.py": """
        import pytest

        @pytest.mark.parametrize("n", range(20))
        def test_edge(n):
            assert n >= 0
    """,
    "tests/test_narrow.py": """
        import pytest

        @pytest.mark.parametrize("n", range(19))
        def test_narrow(n):
            assert n >= 0

        def test_plain():
            assert True
    """,
    "tests/sub/test_classy.py": """
        import pytest

        HOSTS = [f"[::{n}]:80 a b" for n in range(30)]

        class TestHosts:
            @pytest.mark.parametrize("host", HOSTS)
            def test_host(self, host):
                assert "::" in host
    """,
    "tests/test_marked_one.py": """
        import pytest

        @pytest.mark.security
        @pytest.mark.parametrize("n", range(22))
        def test_marked(n):
            assert n >= 0

        @pytest.mark.parametrize("n", range(21))
        def test_unmarked(n):
            assert n >= 0
    """,
}

#: What the census must list: (function, cases, security). Every other function has fewer than
#: 20 cases: the 19-case one and the plain one.
_LISTED = [
    ("tests/sub/test_classy.py::TestHosts::test_host", 30, False),
    ("tests/test_wide.py::test_wide", 25, True),
    ("tests/test_marked_one.py::test_marked", 22, True),
    ("tests/test_marked_one.py::test_unmarked", 21, False),
    ("tests/test_edge.py::test_edge", 20, False),
]


def _pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.run(
        [
            *nested_pytest.argv(root),
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            *args,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class _Planted:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.collection = _pytest(root, "--collect-only", "-qq").stdout
        self.security = _pytest(root, "--collect-only", "-qq", "-m", "security").stdout
        ran = _pytest(root, "-q", "--junitxml=junit.xml")
        assert ran.returncode == 0, ran.stdout + ran.stderr
        self.junit = (root / "junit.xml").read_bytes()


@pytest.fixture(scope="module")
def planted(tmp_path_factory: pytest.TempPathFactory) -> _Planted:
    root = tmp_path_factory.mktemp("planted-suite")
    (root / "pytest.ini").write_text(_INI, encoding="utf-8")
    for rel, source in _MODULES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    return _Planted(root)


def test_the_census_lists_every_function_with_twenty_or_more_cases_and_no_other(
    planted: _Planted, subtests
) -> None:
    """The threshold, both sides of it: 20 cases is listed, 19 is not. Each listed function is
    counted from the collection, flagged from the ``-m security`` collection whether the marker
    is on its module or on itself, and timed on every one of its cases from the real junit."""
    rows = census.census(planted.collection, planted.security, [planted.junit])
    found = {row.function: row for row in rows}
    assert len(_LISTED) == 5
    for function, cases, security in _LISTED:
        with table_rows.row(subtests, function=function):
            listed = found.get(function)
            assert listed is not None, f"{function} ({cases} cases) is not listed: {sorted(found)}"
            assert (listed.cases, listed.security) == (cases, security), listed
            assert listed.timed == cases, f"{function}: {listed.timed} of {cases} cases timed"
    assert sorted(found) == sorted(f for f, _, _ in _LISTED), "a function under 20 cases is listed"


def test_rows_are_ranked_by_summed_seconds_then_by_name(planted: _Planted) -> None:
    """Most summed seconds first, whatever the case count; equal sums in name order."""
    seconds = {"test_edge": 0.5, "test_wide": 0.04, "test_host": 0.04, "test_marked": 0.01}
    cases = []
    for line in census.collected_ids(planted.collection):
        head, bracket, param = line.partition("[")
        path, _, rest = head.partition("::")
        classes, _, base = rest.rpartition("::")
        name = base + bracket + param
        module = path[: -len(".py")].replace("/", ".")
        classname = f"{module}.{classes}" if classes else module
        cases.append(
            f'<testcase classname="{classname}" name="{_xml(name)}" '
            f'time="{seconds.get(base, 0.0)}" />'
        )
    junit = f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>".encode()

    rows = census.census(planted.collection, planted.security, [junit])

    assert [(r.function.rsplit("::", 1)[1], round(r.seconds, 2)) for r in rows] == [
        ("test_edge", 10.0),
        ("test_host", 1.2),
        ("test_wide", 1.0),
        ("test_marked", 0.22),
        ("test_unmarked", 0.0),
    ]


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def test_a_case_no_junit_names_is_counted_but_not_timed(planted: _Planted) -> None:
    """Cases from several reports add up; a case none of them names still counts as a case, and
    the mean per case is over the timed ones only."""
    junit = (
        b'<testsuites><testsuite><testcase classname="tests.test_edge" name="test_edge[0]" '
        b'time="0.3" /></testsuite></testsuites>'
    )
    other = (
        b'<testsuites><testsuite><testcase classname="tests.test_edge" name="test_edge[1]" '
        b'time="0.1" /><testcase classname="" name="tests.test_gone" time="9" />'
        b"</testsuite></testsuites>"
    )
    row = {r.function: r for r in census.census(planted.collection, "", [junit, other])}[
        "tests/test_edge.py::test_edge"
    ]
    assert (row.cases, row.timed, round(row.seconds, 2), round(row.mean_ms)) == (20, 2, 0.4, 200)


def test_the_collection_reader_keeps_each_id_once_and_skips_pytests_own_lines() -> None:
    text = (
        "\x1b[32mtests/test_a.py::test_x[1]\x1b[0m\n"
        "tests/test_a.py::test_x[a b::c]\n"
        "tests/test_a.py::test_x[1]\n"
        "71/113 tests collected (42 deselected) in 0.99s\n"
        "real-home guard report written to /somewhere/report.txt\n"
        "  <Function test_x[2]>\n"
    )
    assert census.collected_ids(text) == [
        "tests/test_a.py::test_x[1]",
        "tests/test_a.py::test_x[a b::c]",
    ]


def test_a_junit_report_that_is_not_xml_is_refused() -> None:
    with pytest.raises(ValueError, match="not a junit report"):
        census.census("tests/test_a.py::test_x[1]\n", "", [b"<testsuites>"])


def test_the_written_census_names_the_commit_the_run_and_the_reports(
    planted: _Planted, tmp_path: Path
) -> None:
    """``python -m tools.ci variation-census`` writes the table under a header that says where its
    numbers came from: report files by name only, never by where they sat on a machine."""
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "collection.txt").write_text(planted.collection, encoding="utf-8")
    (inputs / "security.txt").write_text(planted.security, encoding="utf-8")
    (inputs / "junit-test-shard-1.xml").write_bytes(planted.junit)
    out = tmp_path / "variation-census.md"

    code = ci_main.main(
        [
            "variation-census",
            "--collection",
            str(inputs / "collection.txt"),
            "--security-collection",
            str(inputs / "security.txt"),
            "--junit",
            str(inputs / "junit-test-shard-1.xml"),
            "--commit",
            "0123abcd",
            "--run",
            "1234567",
            "--output",
            str(out),
        ]
    )

    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "`0123abcd`" in text and "1234567" in text and "`junit-test-shard-1.xml`" in text
    assert str(inputs) not in text, "the report's folder on this machine is published"
    assert "| Function | Cases | Seconds | Mean per case (ms) | Security |" in text
    assert "| `tests/test_wide.py::test_wide` | 25 |" in text and "| yes |" in text
    assert "test_narrow" not in text


_ASYNC_TABLE = """
    import asyncio
    import pytest

    ROWS = [("a", 1), ("b", 2), ("c", 3)]

    @pytest.mark.asyncio
    async def test_async_table(subtests):
        assert len(ROWS) == 3
        for name, value in ROWS:
            with subtests.test(row=name):
                await asyncio.sleep(0)
                assert value == 1, f"row {name!r} read {value}"
"""


def test_an_async_table_test_reports_every_failing_row(tmp_path: Path) -> None:
    """docs/maintainers/testing.md recommends ``subtests`` for async tests too, on this: inside an
    ``async def`` test under pytest-asyncio, each failing row is reported with its own message,
    and the rows after a failure still run."""
    (tmp_path / "pytest.ini").write_text(_INI, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_async_table.py").write_text(
        textwrap.dedent(_ASYNC_TABLE), encoding="utf-8"
    )
    ran = _pytest(tmp_path, "-q", "--color=no")
    said = ran.stdout + ran.stderr
    assert ran.returncode == 1, said
    assert "SUBFAILED(row='b')" in said and "SUBFAILED(row='c')" in said, said
    assert "row 'b' read 2" in said and "row 'c' read 3" in said, said
    assert "SUBFAILED(row='a')" not in said, said


def test_a_suite_sized_collection_and_report_are_read_in_seconds() -> None:
    """The census over a suite's collection (55,000 items) and one shard's report (14,000 cases)
    takes well under the 30 seconds it may: it reads two inputs once each."""
    ids = [
        f"tests/test_m{m}.py::test_f{f}[{c}]"
        for m in range(500)
        for f in range(10)
        for c in range(11)
    ]
    collection = "\n".join(ids) + "\n"
    junit = (
        "<testsuites><testsuite>"
        + "".join(
            f'<testcase classname="tests.test_m{m}" name="test_f{f}[{c}]" time="0.01" />'
            for m in range(130)
            for f in range(10)
            for c in range(11)
        )
        + "</testsuite></testsuites>"
    ).encode()

    started = time.monotonic()
    rows = census.census(collection, "", [junit])
    elapsed = time.monotonic() - started

    assert rows == [], "eleven cases per function is under the threshold"
    assert elapsed < 30, f"took {elapsed:.1f} s"
