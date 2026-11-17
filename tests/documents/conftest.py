"""The documents package's own tests (v0.4.0 B4, #5815).

They run twice. The catalogue's ``documents`` job runs them in an environment holding only the
lock's ``documents`` group and the package's source (``--confcutdir=tests/documents``), so a test
that reaches core fails there. The suite's shards run them beside everything else, which is their
run on macOS and their run with core installed.

So this conftest is loaded both alone and under the suite's root conftest. It defines no fixture
the root conftest has, and sets no option. With ``--confcutdir`` the root's home guard isn't loaded,
so this one points HOME and PERSONALCLAW_HOME at the test's own folder. Under the root conftest,
that is a home the test chose, which the root's guard leaves as it is.

In the job, which sets ``DOCUMENTS_JOB=1``, two things hold that the shards don't need:
- a skip fails the run, a test's or a whole module's at collection: the shards run every test here,
  so one the job's environment skips is a test the job never ran;
- core beyond the package isn't there. Its source is on the path (``PYTHONPATH=src`` and
  pyproject's ``pythonpath``), so an import of another unit of ``personalclaw``, static or by
  name, would otherwise work: here it fails as it would with the package alone.
"""

from __future__ import annotations

import os
import sys

import pytest

_IN_THE_JOB = os.environ.get("DOCUMENTS_JOB") == "1"

_SKIPPED: list[str] = []


def pytest_runtest_logreport(report):
    if _IN_THE_JOB and report.skipped and not hasattr(report, "wasxfail"):
        _SKIPPED.append(report.nodeid)


def pytest_collectreport(report):
    if _IN_THE_JOB and report.skipped:
        _SKIPPED.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if _SKIPPED:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        print(f"\n{len(_SKIPPED)} test(s) skipped in the documents job, which must run them all:")
        for nodeid in _SKIPPED:
            print(f"  {nodeid}")


@pytest.fixture(autouse=True)
def _documents_tests_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home / ".personalclaw"))


class _OnlyThePackage:
    """In the job, core beyond the package is not there: importing it fails as it would alone."""

    def find_spec(self, name, path=None, target=None):
        if name.startswith("personalclaw.") and not (
            name == "personalclaw.documents" or name.startswith("personalclaw.documents.")
        ):
            raise ModuleNotFoundError(
                f"{name} is core, which the documents job does not hold", name=name
            )
        return None


if _IN_THE_JOB:
    sys.meta_path.insert(0, _OnlyThePackage())
