"""The JUnit report records a test with subtests at the time it took, once (#5648).

pytest's junitxml adds every report's duration to its node's testcase
(``LogXML.update_testcase_duration``), and a subtest's report has its test's node id. A test's
subtests run inside its call, so their time was counted twice: once in the call, and again per
subtest. ``test_ci_contract.py``'s row "every-timeout-mark-is-listed-with-its-reason" ran its call
in 9.3 s, and junit wrote 18.9 s. Every reader that weighs files by junit seconds read such a file
at about twice what it ran: the telemetry record, the suite's shard plan, and whoever balances the
rails legs.

So a subtest's report adds nothing to its testcase here. The test's own setup, call and teardown
still add theirs, and the call holds every subtest's time. The report's outcomes and failures are
untouched: only the duration a subtest adds goes.

It reads two of pytest's private names, ``_pytest.junitxml.xml_key`` and
``_pytest.subtests.SubtestReport``. ``tests/test_junit_records_a_subtest_test_once.py`` holds them,
so a pytest that moves them fails that test, not every report silently.
"""

from __future__ import annotations

from typing import Any

import pytest
from _pytest import junitxml
from _pytest.subtests import SubtestReport


class Plugin:
    """Wraps the run's JUnit writer once it exists, after every plugin has configured."""

    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        xml = session.config.stash.get(junitxml.xml_key, None)
        if xml is None:
            return  # no --junitxml, or an xdist worker, whose controller writes the report
        add = xml.update_testcase_duration

        def once(report: Any) -> None:
            if not isinstance(report, SubtestReport):
                add(report)

        xml.update_testcase_duration = once  # type: ignore[method-assign]


def configure(config: pytest.Config) -> None:
    config.pluginmanager.register(Plugin(), "junit-once")
