"""``tests/quarantine.py``: a known failure is waived only by a capped, assigned, expiring entry.

``tests/quarantine.toml`` holds at most 10 entries of ``{nodeid, issue, assignee, expires,
reason}``, each at most 30 days out (#3947). A quarantined test still runs: a failure is recorded
as a quarantined failure instead of a red, and a pass is a pass, even under
``xfail_strict = true``. A list that breaks a rule fails the session before any test runs, and a
rail or a ``security``-marked test is never quarantined. ``tests/waiver_guard.py`` reads the list
on its own and fails every other expected-failure waiver, so nothing else waives a failure.

The list's rules are checked against a tree each case builds; the session's behaviour by running
pytest on such a tree, with the suite's own modules on the path as the suite's conftest has it.

This module carries the ``security`` marker: it proves the list's rules, so no entry may waive
one of its tests, and a quarantine that waived every test would be refused at the first of them.
"""

from __future__ import annotations

import ast
import datetime
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import quarantine
import security_census
import waiver_guard

pytestmark = pytest.mark.security

TESTS = Path(__file__).resolve().parent
TODAY = datetime.date(2026, 10, 18)
SOON = (TODAY + datetime.timedelta(days=10)).isoformat()

_MODULE = """\
import pytest


def test_flaky():
    pass


@pytest.mark.parametrize("n", [1, 2])
def test_cases(n):
    pass


class TestGroup:
    def test_method(self):
        pass


@pytest.mark.security
def test_guarded():
    pass


@pytest.mark.security
class TestGuardedGroup:
    def test_method(self):
        pass


class TestMarkedGroup:
    pytestmark = [pytest.mark.security]

    def test_method(self):
        pass


class TestAnnotatedGroup:
    pytestmark: list = [pytest.mark.security]

    def test_method(self):
        pass


@pytest.mark.parametrize("n", [1, 2])
class TestCasesGroup:
    def test_method(self, n):
        pass


class Helpers:
    def test_method(self):
        pass


def helper():
    pass
"""


class _Raw(str):
    """A value written into the list as it is, TOML already: ``true``, a datetime, an escape."""


def _entry(nodeid: object = "tests/test_x.py::test_flaky", **fields: object) -> str:
    values: dict[str, object] = {
        "nodeid": nodeid,
        "issue": 4000,
        "assignee": "someone",
        "expires": SOON,
        "reason": "fails one run in five on a slow runner",
        **fields,
    }
    lines = ["[[entry]]"]
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, _Raw) or (
            isinstance(value, str) and (value[:1].isdigit() or value[:1] == '"')
        ):
            # A date, or a value already written as TOML (a quoted date, a quoted number).
            lines.append(f"{key} = {value}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n\n"


def _tree(root: Path, listing: str) -> Path:
    (root / "tests" / "rails").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text(_MODULE, encoding="utf-8")
    (root / "tests" / "test_marked.py").write_text(
        "import pytest\n\npytestmark = [pytest.mark.anyio, pytest.mark.security]\n\n\n"
        "def test_it():\n    pass\n",
        encoding="utf-8",
    )
    (root / "tests" / "rails" / "test_rail.py").write_text(
        "def test_it():\n    pass\n", encoding="utf-8"
    )
    # A module pytest does not collect: its name is not test_*.py or *_test.py.
    (root / "tests" / "helpers.py").write_text("def test_it():\n    pass\n", encoding="utf-8")
    (root / quarantine.LIST).write_text(listing, encoding="utf-8")
    return root


# ── The list's rules ─────────────────────────────────────────────────────────────────────────

_RULES = [
    ("an-empty-list", "", ""),
    ("only-a-comment", "# no entries yet\n", ""),
    ("one-valid-entry", _entry(), ""),
    ("one-parametrized-case", _entry("tests/test_x.py::test_cases[1]"), ""),
    ("a-method", _entry("tests/test_x.py::TestGroup::test_method"), ""),
    (
        "one-case-of-a-parametrized-class",
        _entry("tests/test_x.py::TestCasesGroup::test_method[1]"),
        "",
    ),
    ("expires-tomorrow", _entry(expires=(TODAY + datetime.timedelta(days=1)).isoformat()), ""),
    ("expires-30-days-out", _entry(expires=(TODAY + datetime.timedelta(days=30)).isoformat()), ""),
    (
        "two-cases-of-one-test",
        "".join(_entry(f"tests/test_x.py::test_cases[{n}]") for n in (1, 2)),
        "",
    ),
    ("an-assignee-of-39-characters", _entry(assignee="a" * 39), ""),
    ("expires-today", _entry(expires=TODAY.isoformat()), f"expired on {TODAY.isoformat()}"),
    ("expired", _entry(expires=(TODAY - datetime.timedelta(days=1)).isoformat()), "expired on"),
    (
        "31-days-out",
        _entry(expires=(TODAY + datetime.timedelta(days=31)).isoformat()),
        "more than 30 days",
    ),
    ("expires-as-text", _entry(expires='"2026-11-01"'), "expires must be a date"),
    (
        "expires-a-date-and-time",
        _entry(expires=_Raw("2026-10-28T00:00:00Z")),
        "expires must be a date",
    ),
    ("an-eleventh-entry", "".join(_entry(f"tests/test_x.py::t{n}") for n in range(11)), "holds 11"),
    ("no-issue", _entry(issue=None), "no issue"),
    ("issue-zero", _entry(issue=0), "no issue"),
    ("issue-negative", _entry(issue=-5), "no issue"),
    ("issue-true", _entry(issue=_Raw("true")), "no issue"),
    ("issue-a-float", _entry(issue=4000.0), "no issue"),
    ("issue-not-a-number", _entry(issue='"soon"'), "no issue"),
    ("no-assignee", _entry(assignee=None), "no assignee"),
    ("an-empty-assignee", _entry(assignee=""), "no assignee"),
    ("an-assignee-that-is-not-a-login", _entry(assignee="two people"), "no assignee"),
    ("an-assignee-with-a-leading-hyphen", _entry(assignee="-someone"), "no assignee"),
    ("an-assignee-with-two-hyphens", _entry(assignee="some--one"), "no assignee"),
    ("an-assignee-of-40-characters", _entry(assignee="a" * 40), "no assignee"),
    ("an-assignee-and-a-newline", _entry(assignee=_Raw('"someone\\n"')), "no assignee"),
    ("no-reason", _entry(reason="  "), "no reason"),
    (
        "a-reason-of-two-lines",
        _entry(reason=_Raw('"fails on a slow runner\\nand a fast one"')),
        "one line",
    ),
    ("a-misspelt-field", _entry(owner="someone"), "unknown field owner"),
    ("no-nodeid", _entry(None), "no nodeid"),
    ("a-nodeid-that-is-a-number", _entry(5), "no nodeid"),
    ("a-test-listed-twice", _entry() + _entry(), "listed twice"),
    ("a-rail", _entry("tests/rails/test_rail.py::test_it"), "is a rail"),
    ("a-security-marked-module", _entry("tests/test_marked.py::test_it"), "security"),
    ("a-security-marked-test", _entry("tests/test_x.py::test_guarded"), "security"),
    (
        "a-security-decorated-class",
        _entry("tests/test_x.py::TestGuardedGroup::test_method"),
        "security-marked (its class)",
    ),
    (
        "a-class-whose-pytestmark-is-security",
        _entry("tests/test_x.py::TestMarkedGroup::test_method"),
        "security-marked (its class)",
    ),
    (
        "a-class-whose-annotated-pytestmark-is-security",
        _entry("tests/test_x.py::TestAnnotatedGroup::test_method"),
        "security-marked (its class)",
    ),
    ("a-file-that-is-gone", _entry("tests/test_gone.py::test_it"), "no such test"),
    ("a-file-spelt-in-another-case", _entry("tests/test_X.py::test_flaky"), "does not exist"),
    (
        "one-test-listed-twice-in-two-cases",
        _entry() + _entry("tests/test_X.py::test_flaky"),
        "does not exist",
    ),
    ("a-module-pytest-does-not-collect", _entry("tests/helpers.py::test_it"), "not a test module"),
    ("a-test-that-is-gone", _entry("tests/test_x.py::test_renamed"), "no such test"),
    ("a-helper-not-a-test", _entry("tests/test_x.py::helper"), "not a test pytest collects"),
    (
        "a-class-pytest-does-not-collect",
        _entry("tests/test_x.py::Helpers::test_method"),
        "not a class pytest collects",
    ),
    (
        "a-test-of-a-parametrized-class-without-its-case",
        _entry("tests/test_x.py::TestCasesGroup::test_method"),
        "without its case",
    ),
    (
        "a-parametrized-test-without-its-case",
        _entry("tests/test_x.py::test_cases"),
        "without its case",
    ),
    ("a-name-past-a-function", _entry("tests/test_x.py::test_flaky::deeper"), "no such test"),
    ("a-class-not-a-test", _entry("tests/test_x.py::TestGroup"), "names a class"),
    ("a-file-not-a-test", _entry("tests/test_x.py"), "not a test id"),
    ("a-file-outside-tests", _entry("src/x.py::f"), "not a test id"),
    ("a-file-without-py", _entry("tests/test_x::test_flaky"), "not a test id"),
    ("not-toml", "[[entry]\n", "does not parse"),
    ("a-stray-key", 'owner = "x"\n', "unknown key owner"),
    ("one-entry-table", "[entry]\n" + _entry()[len("[[entry]]\n") :], "array of tables"),
    ("entry-a-string", 'entry = "x"\n', "array of tables"),
    ("entry-an-array-of-numbers", "entry = [1, 2]\n", "array of tables"),
]


@pytest.mark.parametrize(
    ("listing", "expected"),
    [(listing, expected) for _, listing, expected in _RULES],
    ids=[name for name, _, _ in _RULES],
)
def test_the_list_holds_its_rules(tmp_path: Path, listing: str, expected: str) -> None:
    found = quarantine.problems(_tree(tmp_path, listing), TODAY)
    if expected:
        assert any(expected in line for line in found), found
    else:
        assert found == []


def test_a_missing_list_is_an_empty_one(tmp_path: Path) -> None:
    root = _tree(tmp_path, "")
    (root / quarantine.LIST).unlink()
    assert quarantine.problems(root, TODAY) == []


def test_a_list_that_is_not_utf_8_does_not_parse(tmp_path: Path) -> None:
    root = _tree(tmp_path, "")
    (root / quarantine.LIST).write_bytes(b"\xff\xfe[[entry]]\n")
    found = quarantine.problems(root, TODAY)
    assert any("does not parse" in line for line in found), found


def test_the_quarantines_own_tests_cannot_be_quarantined(tmp_path: Path) -> None:
    """This module proves the list's rules, so it is security-marked: an entry for one of its
    tests is refused, and a quarantine that waived everything is refused at the first of them."""
    root = _tree(tmp_path, "")
    (root / "tests" / "test_quarantine.py").write_text(
        Path(__file__).read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / quarantine.LIST).write_text(
        _entry("tests/test_quarantine.py::test_a_failure_the_list_does_not_name_stays_red"),
        encoding="utf-8",
    )
    found = quarantine.problems(root, TODAY)
    assert any("security-marked (its module)" in line for line in found), found
    # The census names this module, so the marker cannot be dropped without a deliberate edit.
    assert "tests/test_quarantine.py" in security_census.NAMED_MODULES


def test_the_day_a_list_is_read_is_utcs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Late in the evening west of Greenwich the local date is a day behind UTC's; the list is
    read on UTC's, as CI's runners read it."""
    instant = datetime.datetime(2026, 10, 19, 3, 0, tzinfo=datetime.UTC)
    behind = datetime.timezone(datetime.timedelta(hours=-7))

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return instant.astimezone(behind).replace(tzinfo=None)
            return instant.astimezone(tz)

    class Calendar(datetime.date):
        @classmethod
        def today(cls):  # type: ignore[override]
            return instant.astimezone(behind).date()

    clock = types.SimpleNamespace(datetime=Clock, date=Calendar, UTC=datetime.UTC)
    monkeypatch.setattr(quarantine, "datetime", clock)
    assert Calendar.today() == datetime.date(2026, 10, 18)
    assert quarantine.today() == datetime.date(2026, 10, 19)


# ── The session ──────────────────────────────────────────────────────────────────────────────


def _conftest(*, quarantined: bool = True, guarded: bool = True, extra: str = "") -> str:
    """The session's conftest: the suite's quarantine and its guard, as tests/conftest.py loads
    them, and a hook that marks one test ``security`` where the source does not show it."""
    configure = []
    if guarded:
        configure.append("    waiver_guard.register(config, ROOT)")
    if quarantined:
        configure.append("    quarantine.configure(config, ROOT)")
    return f"""
import sys
from pathlib import Path

sys.path.insert(0, {str(TESTS)!r})  # tests/ alone, as a test's own pytest run has it
import pytest
import quarantine
import waiver_guard

ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config):
{chr(10).join(configure) or "    pass"}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.name == "test_marked_by_a_hook":
            item.add_marker(pytest.mark.security)
""" + extra


_SUITE = """\
import os

import pytest


def test_quarantined_and_failing():
    assert False, "the known failure"


def test_quarantined_and_passing():
    pass


def test_not_quarantined_and_failing():
    assert os.environ.get("FAIL_THE_UNLISTED") != "1"


def test_marked_by_a_hook():
    pass


@pytest.mark.parametrize("n", [1, 2])
def test_cases(n):
    pass
"""


#: The suite's outcomes an empty list must leave as they are: a pass, a failure, a skip, and an
#: expected failure that fails and one that passes, which ``xfail_strict = true`` makes a red.
_OUTCOMES = """\
import pytest


def test_passes():
    pass


def test_fails():
    assert False


@pytest.mark.skip(reason="not here")
def test_skipped():
    pass


@pytest.mark.xfail(reason="known")
def test_expected_to_fail_and_fails():
    assert False


@pytest.mark.xfail(reason="known")
def test_expected_to_fail_and_passes():
    pass
"""


def _build(root: Path, listing: str, *, conftest: str, suite: str) -> None:
    tests = root / "tests"
    tests.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\nxfail_strict = true\n',
        encoding="utf-8",
    )
    (tests / "conftest.py").write_text(conftest, encoding="utf-8")
    (tests / "test_suite.py").write_text(suite, encoding="utf-8")
    (root / quarantine.LIST).write_text(listing, encoding="utf-8")


def _run(
    root: Path,
    *args: str,
    fail_unlisted: bool = False,
    workers: int = 0,
    cache: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "FAIL_THE_UNLISTED": "1" if fail_unlisted else "0"}
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-rA",
        "-o",
        "addopts=",
        "--junitxml=junit.xml",
    ]
    if not cache:
        command += ["-p", "no:cacheprovider"]
    if workers:
        command += ["-n", str(workers), "--dist", "worksteal"]
    return subprocess.run(
        command + list(args or ("tests",)),
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _session(
    tmp_path: Path,
    listing: str,
    *args: str,
    fail_unlisted: bool = False,
    conftest: str | None = None,
    suite: str = _SUITE,
    workers: int = 0,
) -> subprocess.CompletedProcess[str]:
    _build(tmp_path, listing, conftest=_conftest() if conftest is None else conftest, suite=suite)
    return _run(tmp_path, *args, fail_unlisted=fail_unlisted, workers=workers)


def _listed(*names: str) -> str:
    """Entries for the session's own tests, expiring ten days from the day the test runs."""
    expires = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    return "".join(_entry(f"tests/test_suite.py::{name}", expires=expires) for name in names)


def _junit_outcomes(path: Path) -> dict[str, tuple[str, dict[str, str | None]]]:
    """Each case of a junit report: how it ended, and its properties."""
    outcomes = {}
    for case in ET.parse(path).iter("testcase"):
        ended = next((c.tag for c in case if c.tag in ("failure", "error", "skipped")), "passed")
        props = {p.get("name") or "": p.get("value") for p in case.iter("property")}
        outcomes[case.get("name") or ""] = (ended, props)
    return outcomes


_WORKERS = pytest.mark.parametrize("workers", [0, 2], ids=["one-process", "two-xdist-workers"])


@_WORKERS
def test_a_quarantined_failure_is_recorded_and_does_not_red_the_session(
    tmp_path: Path, workers: int
) -> None:
    """Both quarantined tests still run. The failure is recorded, in the summary and as a junit
    property, and the pass stays a pass although the tree sets ``xfail_strict = true``."""
    done = _session(
        tmp_path,
        _listed("test_quarantined_and_failing", "test_quarantined_and_passing"),
        workers=workers,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "quarantined test(s)" in done.stdout, done.stdout
    assert "test_quarantined_and_failing failed under quarantine (#4000" in done.stdout
    assert "test_quarantined_and_passing passed under quarantine (#4000" in done.stdout
    cases = {c.get("name"): c for c in ET.parse(tmp_path / "junit.xml").iter("testcase")}
    for name in ("test_quarantined_and_failing", "test_quarantined_and_passing"):
        props = {p.get("name"): p.get("value") for p in cases[name].iter("property")}
        assert props.get(quarantine.PROPERTY) == "4000", props
    assert not list(cases["test_not_quarantined_and_failing"].iter("property"))


def test_an_empty_list_changes_no_test_outcome(tmp_path: Path) -> None:
    """With nothing listed, every test ends as it does in a session with no quarantine at all:
    the same exit, the same outcome for each case, and no case carries the property."""
    runs = {}
    arms = (
        ("empty", _conftest(guarded=False)),
        ("without", _conftest(quarantined=False, guarded=False)),
    )
    for name, conftest in arms:
        root = tmp_path / name
        done = _session(root, "# no entries yet\n", conftest=conftest, suite=_OUTCOMES)
        runs[name] = (done.returncode, _junit_outcomes(root / "junit.xml"))
        assert "quarantined test(s)" not in done.stdout, done.stdout
    assert arms[0][1] != arms[1][1]
    assert runs["empty"] == runs["without"]
    code, outcomes = runs["empty"]
    assert code == 1
    assert {name: ended for name, (ended, _) in outcomes.items()} == {
        "test_passes": "passed",
        "test_fails": "failure",
        "test_skipped": "skipped",
        "test_expected_to_fail_and_fails": "skipped",
        "test_expected_to_fail_and_passes": "failure",
    }
    assert all(not props for _, props in outcomes.values()), outcomes


def test_a_failure_the_list_does_not_name_stays_red(tmp_path: Path) -> None:
    done = _session(tmp_path, _listed("test_quarantined_and_failing"), fail_unlisted=True)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "FAILED tests/test_suite.py::test_not_quarantined_and_failing" in done.stdout


@_WORKERS
def test_a_list_that_breaks_a_rule_fails_the_session_before_any_test_runs(
    tmp_path: Path, workers: int
) -> None:
    expired = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    done = _session(
        tmp_path,
        _entry("tests/test_suite.py::test_quarantined_and_failing", expires=expired),
        workers=workers,
    )
    assert done.returncode == pytest.ExitCode.USAGE_ERROR, done.stdout + done.stderr
    assert "expired on" in done.stderr, done.stderr
    assert "passed" not in done.stdout


def _no_test_ran(root: Path) -> bool:
    return not list(ET.parse(root / "junit.xml").iter("testcase"))


@_WORKERS
def test_a_test_marked_security_at_collection_is_never_quarantined(
    tmp_path: Path, workers: int
) -> None:
    """A mark the source does not show (a hook's) is read at collection: the session refuses the
    list with the reason, under xdist as in one process, and no test runs."""
    done = _session(
        tmp_path,
        _listed("test_quarantined_and_failing", "test_marked_by_a_hook"),
        workers=workers,
    )
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.USAGE_ERROR, out
    assert out.count("test_marked_by_a_hook is security-marked") == 1, out
    assert "INTERNALERROR" not in out, out
    assert _no_test_ran(tmp_path), out


@_WORKERS
@pytest.mark.parametrize(
    "name",
    ["test_cases[3]", "test_quarantined_and_failing[1]"],
    ids=["a-case-that-is-gone", "a-case-of-a-test-with-none"],
)
def test_an_entry_its_module_did_not_collect_stops_the_session(
    tmp_path: Path, workers: int, name: str
) -> None:
    """The source cannot tell which cases a test has; collection can. An entry for a case its
    module did not collect waives nothing, so the session refuses it rather than let it hold a
    slot until it expires."""
    done = _session(tmp_path, _listed(name), workers=workers)
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.USAGE_ERROR, out
    assert out.count(f"tests/test_suite.py::{name}: no such test") == 1, out
    assert "INTERNALERROR" not in out, out
    assert _no_test_ran(tmp_path), out


@pytest.mark.parametrize(
    "selected",
    ["tests/test_suite.py::test_quarantined_and_passing", "tests/test_suite.py::test_cases[1]"],
    ids=["one-test-of-the-file", "one-case-of-a-test"],
)
def test_a_run_of_part_of_a_file_judges_no_entry_it_did_not_collect(
    tmp_path: Path, selected: str
) -> None:
    """Asked for one test, pytest collects only it; the file's other entries are not judged by
    what that run left out."""
    done = _session(tmp_path, _listed("test_quarantined_and_failing", "test_cases[2]"), selected)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "breaks its rules" not in done.stdout + done.stderr


def test_a_module_skipped_in_this_run_judges_none_of_its_entries(tmp_path: Path) -> None:
    """A module that skips itself here (another platform's tests) collects nothing; its entries
    are not gone, so the session runs."""
    skipped = (
        "import pytest\n\npytest.skip('not on this machine', allow_module_level=True)\n\n\n"
        "def test_quarantined_and_failing():\n    assert False\n"
    )
    done = _session(tmp_path, _listed("test_quarantined_and_failing"), suite=skipped)
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, out
    assert "breaks its rules" not in out, out
    assert "1 skipped" in out, out


def test_a_last_failed_run_judges_no_entry_it_did_not_collect(tmp_path: Path) -> None:
    """``--lf`` collects a file's last failures only, so an entry for its other tests is not
    judged by that run."""
    _build(tmp_path, _listed("test_quarantined_and_failing"), conftest=_conftest(), suite=_SUITE)
    first = _run(tmp_path, fail_unlisted=True, cache=True)
    assert first.returncode == 1, first.stdout + first.stderr
    again = _run(tmp_path, "--lf", "tests", fail_unlisted=True, cache=True)
    out = again.stdout + again.stderr
    assert again.returncode == 1, out
    assert "breaks its rules" not in out, out
    assert set(_junit_outcomes(tmp_path / "junit.xml")) == {"test_not_quarantined_and_failing"}


# ── Nothing else waives a failure ────────────────────────────────────────────────────────────

#: Every way pytest waives an outcome as an expected failure, beside one listed failure.
_WAIVERS = """\
import pytest


def test_passes():
    pass


def test_fails():
    assert False


@pytest.mark.skip(reason="not here")
def test_skipped():
    pass


@pytest.mark.xfail(strict=True, reason="a known gap")
def test_a_strict_xfail_that_fails():
    assert False


@pytest.mark.xfail(strict=False, reason="flaky")
def test_a_lenient_xfail_that_fails():
    assert False, "the waived failure itself"


@pytest.mark.xfail(strict=False, reason="flaky")
def test_a_lenient_xfail_that_passes():
    pass


@pytest.mark.xfail(run=False, reason="never run")
def test_an_xfail_never_run():
    pass


def test_an_imperative_xfail():
    pytest.xfail("not today")


@pytest.fixture
def waived():
    pytest.xfail("in a fixture")


def test_an_xfail_in_a_fixture(waived):
    pass


def test_listed_and_failing():
    assert False
"""

#: How each ends with the guard: every waiver the list does not name is red.
_GUARDED = {
    "test_passes": "passed",
    "test_fails": "failure",
    "test_skipped": "skipped",
    "test_a_strict_xfail_that_fails": "failure",
    "test_a_lenient_xfail_that_fails": "failure",
    "test_a_lenient_xfail_that_passes": "failure",
    "test_an_xfail_never_run": "error",
    "test_an_imperative_xfail": "failure",
    "test_an_xfail_in_a_fixture": "error",
    "test_listed_and_failing": "skipped",
}


@_WORKERS
def test_a_waiver_the_list_does_not_name_is_red(tmp_path: Path, workers: int) -> None:
    """A source ``xfail`` mark, strict or not, run or not, and ``pytest.xfail()`` in a test or a
    fixture each waive a failure; with the guard each is red and says why, and the listed failure
    alone stays waived."""
    done = _session(tmp_path, _listed("test_listed_and_failing"), suite=_WAIVERS, workers=workers)
    out = done.stdout + done.stderr
    assert done.returncode == 1, out
    outcomes = {name: ended for name, (ended, _) in _junit_outcomes(tmp_path / "junit.xml").items()}
    assert outcomes == _GUARDED, out
    for name, ended in _GUARDED.items():
        if ended in ("failure", "error") and name != "test_fails":
            assert f"tests/test_suite.py::{name} was waived as an expected failure" in out, name
    # The failure it waived is still shown, under the reason.
    assert "the waived failure itself" in out, out


def test_without_the_guard_each_of_those_is_waived(tmp_path: Path) -> None:
    """The control: the same suite and list with no guard waive every one of them, so the
    outcomes above are the guard's."""
    done = _session(
        tmp_path,
        _listed("test_listed_and_failing"),
        suite=_WAIVERS,
        conftest=_conftest(guarded=False),
    )
    assert done.returncode == 1, done.stdout + done.stderr  # test_fails, alone
    outcomes = {name: ended for name, (ended, _) in _junit_outcomes(tmp_path / "junit.xml").items()}
    assert outcomes == {
        **_GUARDED,
        "test_a_strict_xfail_that_fails": "skipped",
        "test_a_lenient_xfail_that_fails": "skipped",
        "test_a_lenient_xfail_that_passes": "passed",
        "test_an_xfail_never_run": "skipped",
        "test_an_imperative_xfail": "skipped",
        "test_an_xfail_in_a_fixture": "skipped",
    }


#: A stand-in for a quarantine that waives what its list does not name.
_WAIVES_EVERYTHING = """

class _WaivesEverything:
    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items):
        for item in items:
            item.add_marker(pytest.mark.xfail(strict=False, reason="waived by mistake"))


def pytest_sessionstart(session):
    session.config.pluginmanager.register(_WaivesEverything(), "waives-everything")
"""


@_WORKERS
def test_the_guard_reds_a_quarantine_that_waives_more_than_it_lists(
    tmp_path: Path, workers: int
) -> None:
    """The guard reads the list itself and does not go through tests/quarantine.py, so a
    quarantine that waived every test with an empty list would be red at every test."""
    done = _session(
        tmp_path,
        "# no entries yet\n",
        conftest=_conftest(extra=_WAIVES_EVERYTHING),
        suite=_OUTCOMES,
        workers=workers,
    )
    out = done.stdout + done.stderr
    assert done.returncode == 1, out
    outcomes = {name: ended for name, (ended, _) in _junit_outcomes(tmp_path / "junit.xml").items()}
    assert outcomes == {
        "test_passes": "failure",
        "test_fails": "failure",
        "test_skipped": "skipped",
        "test_expected_to_fail_and_fails": "failure",
        "test_expected_to_fail_and_passes": "failure",
    }, out
    assert "waived by mistake" in out, out


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("", frozenset()),
        (_entry("tests/test_x.py::test_flaky"), frozenset({"tests/test_x.py::test_flaky"})),
        (
            _entry(
                "tests/test_x.py::test_flaky",
                expires=(TODAY - datetime.timedelta(days=9)).isoformat(),
            ),
            frozenset({"tests/test_x.py::test_flaky"}),
        ),
        ("[[entry]\n", frozenset()),
        ('entry = "x"\n', frozenset()),
        ("entry = 5\n", frozenset()),
        (_entry(5), frozenset()),
    ],
    ids=[
        "empty",
        "one-entry",
        "an-expired-entry",
        "not-toml",
        "not-tables",
        "entry-a-number",
        "a-number",
    ],
)
def test_the_guard_reads_the_names_the_list_holds(
    tmp_path: Path, listing: str, expected: frozenset[str]
) -> None:
    """Only the names: whether an entry may waive is tests/quarantine.py's to say, and a list it
    refuses stops the session first. A list the guard cannot read names nothing."""
    assert waiver_guard.listed(_tree(tmp_path, listing)) == expected


def test_a_missing_list_names_nothing(tmp_path: Path) -> None:
    assert waiver_guard.listed(tmp_path) == frozenset()


def test_the_guard_imports_nothing_from_the_quarantine() -> None:
    """A guard that read the list through tests/quarantine.py would share that module's bugs."""
    tree = ast.parse((TESTS / "waiver_guard.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "quarantine" not in imported, imported


def test_the_suite_loads_the_quarantine_and_its_guard(request: pytest.FixtureRequest) -> None:
    """The suite's own conftest reads the list and loads the guard in every session, so their
    rules hold on every run."""
    assert request.config.pluginmanager.has_plugin("quarantine")
    assert request.config.pluginmanager.has_plugin(waiver_guard.NAME)
