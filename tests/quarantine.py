"""The quarantine: the only waiver of a known failure, capped, assigned and expiring (#3947).

``tests/quarantine.toml`` holds at most :data:`CAP` entries, each one table of the array
``[[entry]]`` with exactly these fields:

- ``nodeid``: the test's pytest id, as ``tests/test_x.py::test_y``,
  ``tests/test_x.py::TestClass::test_y`` or ``tests/test_x.py::test_y[case]``;
- ``issue``: the number of the issue that tracks the fix;
- ``assignee``: the GitHub login of the person who owns the fix;
- ``expires``: a TOML date, at most :data:`MAX_DAYS` days after the day the list is read; on that
  date the entry has expired;
- ``reason``: one line, what fails and why it is waived rather than fixed today.

The file's shape is the contract: the weekly CI health report (#3948) reads this list for the
quarantine's status, so a field is renamed or added here and there together.

A quarantined test still runs. It is marked ``xfail(strict=False)`` at collection, so a failure
is recorded as a quarantined failure, in the terminal summary and as the junit property
:data:`PROPERTY`, instead of a red, and a pass stays a pass, although ``pyproject.toml`` sets
``xfail_strict = true``. Nothing else is waived: ``tests/waiver_guard.py`` reads the list's names
itself, imports nothing from here, and fails every test pytest waived as an expected failure that
the list does not name, whether a source ``xfail`` mark, ``pytest.xfail()`` or a bug here waived
it. An open issue alone waives nothing. On a pull request's run the verdict holds the waiver to
the known failure: a quarantined failure whose test's run reads a file the pull request changes
reads red (``tools/ci/verdict.py``, ``_unwaived``).

A list that breaks a rule stops the session, and no test runs. Read from the source, before
collection, with exit 4 (a usage error): more than :data:`CAP` entries, an entry expired or more
than :data:`MAX_DAYS` days out, an entry without an issue, an assignee or a one-line reason, a
field or key the list does not know, a test listed twice, an id whose file, class or test does
not exist as spelt (a module, class or function pytest does not collect, a parametrized test
named without its case), and an entry for a rail (``tests/rails/``) or a ``security``-marked test
(its module, its class or itself), which are never quarantined. Read again at collection, which
sees what the source cannot: a listed test that collection shows to be ``security``-marked (a
mark a hook adds), and an entry its module or class did not collect (a case that is gone, a case
of a test with none). Then every test is deselected, the reason is printed in the summary, and
the session ends with exit 4, under xdist too: each worker hands its refusal to the controller.
A run that collects only part of a module (``file::test`` or ``--lf``) judges none of its
entries by what it left out.

This module imports nothing from ``tools``: ``tests/conftest.py`` imports it, and a test that
runs pytest on a tree of its own loads that conftest with only ``tests/`` on its path.
"""

from __future__ import annotations

import ast
import datetime
import fnmatch
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import security_census

#: The list, relative to the repository root.
LIST = "tests/quarantine.toml"
#: At most this many entries.
CAP = 10
#: An entry expires at most this many days after the day the list is read.
MAX_DAYS = 30
#: The fields of an entry, all required.
FIELDS = ("nodeid", "issue", "assignee", "expires", "reason")
#: The junit property a quarantined test's case carries: the entry's issue number.
PROPERTY = "quarantine_issue"
#: A GitHub login, matched whole: letters, digits and single hyphens, at most 39 characters.
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
_RAILS = "tests/rails/"
#: The modules, classes and functions pytest collects, by its default names.
_TEST_MODULES = ("test_*.py", "*_test.py")
_TEST_CLASS = "Test"
_TEST_FUNCTION = "test"
#: Where an xdist worker hands its collection refusals to the controller.
WORKER_KEY = "quarantine_refused"


def today() -> datetime.date:
    """The day a list is read, in UTC, as CI's runners read it."""
    return datetime.datetime.now(datetime.UTC).date()


def read(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The list's entries, and what keeps it from being read. A missing list is an empty one."""
    path = root / LIST
    if not path.is_file():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return [], [f"{LIST} does not parse: {exc}"]
    bad = [
        f"{LIST}: unknown key {key} (entries are [[entry]] tables)"
        for key in data
        if key != "entry"
    ]
    entries = data.get("entry", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return [], [*bad, f"{LIST}: entry must be an array of tables, [[entry]]"]
    return entries, bad


def _decorated_security(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    return any(security_census._is_the_marker(d) for d in node.decorator_list)


def _class_security(node: ast.ClassDef) -> bool:
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        else:
            continue
        value = stmt.value
        if value is None or not any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets
        ):
            continue
        elts = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        if any(security_census._is_the_marker(e) for e in elts):
            return True
    return _decorated_security(node)


def _parametrized(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    """Whether a ``parametrize`` mark decorates it, so each of its tests has a ``[case]``."""
    for decorator in node.decorator_list:
        func = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "parametrize"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "mark"
        ):
            return True
    return False


def _spelt_exactly(root: Path, path: str) -> bool:
    """Whether ``path`` is a file under ``root`` with each part spelt as on disk, so a disk that
    ignores case does not let ``tests/TEST_X.py`` stand for ``tests/test_x.py``."""
    here = root
    for part in path.split("/"):
        try:
            if part not in os.listdir(here):
                return False
        except OSError:
            return False
        here = here / part
    return here.is_file()


def _parent(nodeid: str) -> str:
    """The id of the module or class whose collection holds this test."""
    return nodeid.split("[", 1)[0].rsplit("::", 1)[0]


def _test_problem(root: Path, nodeid: str) -> str | None:
    """Why an id names no test that may be quarantined, from the source; or None."""
    path, _, rest = nodeid.partition("::")
    if not rest or not path.startswith("tests/") or not path.endswith(".py"):
        return f"{nodeid} is not a test id (tests/<file>.py::<test>)"
    if path.startswith(_RAILS):
        return f"{nodeid} is a rail: a rail is never quarantined"
    if not _spelt_exactly(root, path):
        return f"{nodeid}: no such test ({path} does not exist)"
    if not any(fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], p) for p in _TEST_MODULES):
        return f"{nodeid}: no such test ({path} is not a test module pytest collects)"
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    if security_census.carries_marker(tree):
        return f"{nodeid} is security-marked (its module): a security test is never quarantined"
    base, case, _ = rest.partition("[")
    names = base.split("::")
    scope: list[ast.stmt] = tree.body
    cases = False
    for depth, name in enumerate(names):
        found = next(
            (
                n
                for n in scope
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.name == name
            ),
            None,
        )
        if found is None:
            return f"{nodeid}: no such test in {path}"
        cases = cases or _parametrized(found)
        if isinstance(found, ast.ClassDef):
            if _class_security(found):
                return f"{nodeid} is security-marked (its class): never quarantined"
            if depth == len(names) - 1:
                return f"{nodeid} is not a test id: it names a class, not a test"
            if not name.startswith(_TEST_CLASS):
                return f"{nodeid}: no such test ({name} is not a class pytest collects)"
            scope = found.body
        else:
            if _decorated_security(found):
                return f"{nodeid} is security-marked: a security test is never quarantined"
            if depth != len(names) - 1:
                return f"{nodeid}: no such test in {path}"
            if not name.startswith(_TEST_FUNCTION):
                return f"{nodeid}: no such test ({name} is not a test pytest collects)"
            if cases and not case:
                return (
                    f"{nodeid} names a parametrized test without its case: list each case "
                    f"to quarantine, as {nodeid}[...]"
                )
    return None


def problems(root: Path, day: datetime.date) -> list[str]:
    """Every rule the list at ``root`` breaks on ``day``, one line each."""
    entries, bad = read(root)
    if len(entries) > CAP:
        bad.append(f"{LIST} holds {len(entries)} entries: at most {CAP} may be quarantined")
    seen: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        nodeid = entry.get("nodeid")
        where = (
            f"{LIST} entry {number} ({nodeid})"
            if isinstance(nodeid, str)
            else f"{LIST} entry {number}"
        )
        bad += [f"{where}: unknown field {key}" for key in entry if key not in FIELDS]
        if not isinstance(nodeid, str) or not nodeid:
            bad.append(f"{where}: no nodeid")
        elif nodeid in seen:
            bad.append(f"{nodeid} is listed twice in {LIST}")
        else:
            seen.add(nodeid)
            problem = _test_problem(root, nodeid)
            if problem:
                bad.append(problem)
        issue = entry.get("issue")
        if type(issue) is not int or issue < 1:
            bad.append(f"{where}: no issue (the number of the issue that tracks the fix)")
        assignee = entry.get("assignee")
        if not isinstance(assignee, str) or not _LOGIN.fullmatch(assignee):
            bad.append(f"{where}: no assignee (the GitHub login of who owns the fix)")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            bad.append(f"{where}: no reason")
        elif reason.splitlines() != [reason]:
            bad.append(f"{where}: the reason must be one line")
        expires = entry.get("expires")
        if type(expires) is not datetime.date:
            bad.append(f"{where}: expires must be a date, such as {day.isoformat()}")
        elif expires <= day:
            bad.append(f"{where}: expired on {expires.isoformat()}: fix the test, or renew it")
        elif (expires - day).days > MAX_DAYS:
            bad.append(f"{where}: expires {expires.isoformat()}, more than {MAX_DAYS} days out")
    return bad


class Plugin:
    """Refuses at collection what the source could not show, marks each quarantined test, and
    says in the summary how each one ended."""

    def __init__(self, entries: dict[str, dict[str, Any]]) -> None:
        self.entries = entries
        self.outcomes: dict[str, str] = {}
        #: The collection refusals, this process's or, on the xdist controller, its workers'.
        self.refused: list[str] = []
        self._parents = {_parent(nodeid) for nodeid in entries}
        #: What pytest collected in each module or class an entry's test belongs to, for each
        #: one this session collected whole.
        self._collected: dict[str, set[str]] = {}

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        # pytest reports a module or class it collected whole, never one a `file::test`
        # argument narrowed it to; `--lf` filters the report itself, so its run is not judged.
        if report.passed and report.nodeid in self._parents:
            self._collected[report.nodeid] = {node.nodeid for node in report.result}

    def _gone(self, config: pytest.Config) -> list[str]:
        """Each entry its module or class, collected whole, did not hold."""
        if config.getoption("lf", False):
            return []
        gone = []
        for nodeid in self.entries:
            collected = self._collected.get(_parent(nodeid))
            if collected is not None and nodeid not in collected:
                gone.append(
                    f"{nodeid}: no such test (pytest collected {_parent(nodeid)}, and no such "
                    "test in it)"
                )
        return gone

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]):
        refused = self._gone(config)
        for item in items:
            entry = self.entries.get(item.nodeid)
            if entry is None:
                continue
            # Read on the item it would mark, so nothing this marks is ever a security test.
            if item.get_closest_marker(security_census.MARKER) is not None:
                refused.append(
                    f"{item.nodeid} is security-marked: a security test is never quarantined"
                )
                continue
            item.add_marker(
                pytest.mark.xfail(
                    strict=False,
                    reason=f"quarantined: #{entry['issue']}, {entry['reason']}",
                )
            )
            item.user_properties.append((PROPERTY, str(entry["issue"])))
        if refused:
            # No test runs. An xdist worker cannot stop the session itself, so it collects
            # nothing and hands the reason to the controller, which ends the session.
            self.refused = refused
            config.hook.pytest_deselected(items=list(items))
            items[:] = []
            workeroutput = getattr(config, "workeroutput", None)
            if workeroutput is not None:
                workeroutput[WORKER_KEY] = refused

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any) -> None:  # noqa: ARG002 — xdist hook
        for line in getattr(node, "workeroutput", {}).get(WORKER_KEY, []):
            if line not in self.refused:
                self.refused.append(line)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.refused:
            session.exitstatus = pytest.ExitCode.USAGE_ERROR

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.nodeid not in self.entries:
            return
        if hasattr(report, "wasxfail") and report.skipped:
            self.outcomes[report.nodeid] = "failed"
        elif report.when == "call" and report.passed:
            self.outcomes.setdefault(report.nodeid, "passed")

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.refused:
            terminalreporter.write_sep("=", f"{LIST} breaks its rules: no test ran", red=True)
            for line in self.refused:
                terminalreporter.write_line(f"  {line}")
        if not self.outcomes:
            return
        terminalreporter.write_sep("-", f"{len(self.outcomes)} quarantined test(s) ran")
        for nodeid, outcome in sorted(self.outcomes.items()):
            entry = self.entries[nodeid]
            terminalreporter.write_line(
                f"{nodeid} {outcome} under quarantine (#{entry['issue']}, "
                f"{entry['assignee']}, expires {entry['expires'].isoformat()})"
            )


def configure(config: pytest.Config, root: Path) -> None:
    """Refuse a list that breaks a rule, then quarantine what it names."""
    bad = problems(root, today())
    if bad:
        raise pytest.UsageError(f"{LIST} breaks its rules:\n" + "\n".join(f"  {b}" for b in bad))
    entries, _ = read(root)
    config.pluginmanager.register(Plugin({e["nodeid"]: e for e in entries}), "quarantine")
