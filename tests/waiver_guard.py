"""Nothing but the quarantine waives a failure: a waived outcome its list does not name is red.

pytest waives a failure as an expected one (xfail) for an ``xfail`` mark, strict or not, a call
to ``pytest.xfail()`` in a test or a fixture, or any plugin that adds the mark. The one waiver
this suite allows is an entry of ``tests/quarantine.toml``, which ``tests/quarantine.py`` turns
into an ``xfail(strict=False)`` mark at collection (#3947). This guard fails every test whose
report pytest waived (an XFAIL, or an XPASS that ``strict=False`` let pass) unless the list names
the test, and says why in the failure.

It is the check that does not go through ``tests/quarantine.py``: it reads the list's names
itself and imports nothing from that module, so a quarantine that waives more than it lists is
red at every test it waives, in the very session that tests it. Whether an entry may waive (its
issue, assignee and expiry, a rail, a security mark) is ``tests/quarantine.py``'s to say; a list
it refuses stops the session before a test runs. A list this module cannot read names nothing,
so every waiver is red.

Like ``tests/quarantine.py`` this imports nothing from ``tools``: a test that runs pytest on a
tree of its own loads it with only ``tests/`` on its path.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

#: The list, relative to the repository root.
LIST = "tests/quarantine.toml"
#: The plugin's name in the session.
NAME = "waiver-guard"


def listed(root: Path) -> frozenset[str]:
    """The test ids the list at ``root`` names; none for a list that is missing or unreadable."""
    try:
        data = tomllib.loads((root / LIST).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return frozenset()
    entries = data.get("entry")
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(
        entry["nodeid"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("nodeid"), str)
    )


class Plugin:
    """Turns a waived report the list does not name into a failure."""

    def __init__(self, names: frozenset[str]) -> None:
        self.names = names

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call):  # noqa: ARG002 — hook signature
        # Outside pytest's own xfail handling, so this sees the report after it has waived one.
        report = yield
        if hasattr(report, "wasxfail") and report.nodeid not in self.names:
            why = (
                f"{report.nodeid} was waived as an expected failure (xfail: "
                f"{report.wasxfail or 'no reason'}), and {LIST} does not list it: a known "
                "failure is waived only by a quarantine entry, with an issue, an assignee and an "
                "expiry. Fix the test, or list it there."
            )
            first = None if report.passed else report.longrepr
            report.outcome = "failed"
            report.longrepr = why if first is None else f"{why}\n\n{first}"
            del report.wasxfail
        return report


def register(config: pytest.Config, root: Path) -> None:
    """Load the guard for the list at ``root``."""
    config.pluginmanager.register(Plugin(listed(root)), NAME)
