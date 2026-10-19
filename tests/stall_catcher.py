"""When a test run stalls, it leaves behind which test it was in, and every thread's stack.

A CI suite job that runs to its cap is killed with every pytest process still alive. pytest writes
its JUnit report when the session ends, so a killed run writes none, and the per-test timeout does
not fire for a hang outside a test's phases or inside a call that never returns to the interpreter.
Measured: three shards stopped printing 43-45 minutes before the cap, and nothing they left named
the test.

Set ``PERSONALCLAW_TEST_STALL_DUMP_SECONDS`` to a number of seconds, as CI does under each suite
job's cap, and every pytest process of the run leaves two things in ``reports/stalls/``:

* ``threads-<process>.txt`` (``controller``, or an xdist worker's id): the stack of every thread
  in the process, written that many seconds after the process started by faulthandler's own
  thread, which reads the stacks without the interpreter lock, so a test that holds it is dumped
  too. pytest cancels faulthandler's one timer whenever a test fails (its
  ``pytest_exception_interact``), so the timer is armed again after each test and each failure,
  for what is left until the deadline. Don't combine this with pytest's ``faulthandler_timeout``,
  which uses the same timer.
* ``junit-so-far.xml``, from the run's controller: a JUnit report of every test that has started,
  rewritten every few seconds while the run changes, in which a test that has started and not
  finished carries an ``<error message="still running ...">``. It is written by a thread of the
  controller, so with xdist (as CI runs) a test that holds a worker's interpreter lock still
  shows; in a run without workers, such a test stops the writer too.

A run that ends on its own removes both, whatever its result: its own JUnit report says what
happened, and a file left behind would read as a stall. An interrupted run keeps them, and so does
one killed outright. The run's controller takes the setting out of its environment as it reads it
and hands it to its xdist workers itself, so a pytest a test starts does not inherit it.
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import shutil
import threading
import time
from pathlib import Path
from typing import IO, Any
from xml.sax.saxutils import escape, quoteattr

import pytest
from _pytest.junitxml import mangle_test_address

#: The variable that turns the catcher on, in seconds after each process starts.
SETTING = "PERSONALCLAW_TEST_STALL_DUMP_SECONDS"
#: Where it leaves what it leaves, under the run's root.
FOLDER = "reports/stalls"
#: The report the controller writes as the run goes.
REPORT = "junit-so-far.xml"
#: How often the controller's report is written again, when something changed.
EVERY = 5.0
#: What a test that has started and not finished says.
STILL_RUNNING = "still running when this report was written"


#: The key the controller hands each xdist worker the setting under.
WORKER_KEY = "stall_dump_seconds"


def configure(config: pytest.Config) -> None:
    """Register the catcher when :data:`SETTING` names a positive number of seconds.

    The run's controller takes the setting out of the environment as it reads it, and hands it
    to each xdist worker as that worker is set up (:meth:`Catcher.pytest_configure_node`), so a
    pytest a test starts of its own (several do, over a planted project) does not inherit it and
    arm a catcher that writes into the same folder."""
    if config.pluginmanager.has_plugin("stall-catcher"):
        return
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None:
        raw = str(workerinput.get(WORKER_KEY) or "")
    else:
        raw = os.environ.pop(SETTING, "").strip()
    if not raw:
        return
    seconds = float(raw)
    if seconds <= 0:
        raise pytest.UsageError(f"{SETTING}={raw!r}: it is a number of seconds above 0")
    config.pluginmanager.register(Catcher(config, seconds), "stall-catcher")


#: Under ``-p stall_catcher`` the module is the plugin, and registers the catcher itself.
pytest_configure = configure


class Catcher:
    """Arms one process's dump of its threads, and in the controller writes the report so far."""

    def __init__(self, config: pytest.Config, seconds: float) -> None:
        self._seconds = seconds
        self._deadline = time.monotonic() + seconds
        self._interrupted = False
        workerinput = getattr(config, "workerinput", None)
        self._name = workerinput["workerid"] if workerinput else "controller"
        self._folder = Path(config.rootpath) / FOLDER
        self._folder.mkdir(parents=True, exist_ok=True)
        self._dump_path = self._folder / f"threads-{self._name}.txt"
        self._dump: IO[str] | None = open(self._dump_path, "w")  # noqa: SIM115 — kept open
        self._arm()
        self._report: _Report | None = None if workerinput else _Report(self._folder / REPORT)

    def _arm(self) -> None:
        """Arm faulthandler's timer for what is left until the deadline; past it, do nothing."""
        left = self._deadline - time.monotonic()
        if self._dump is not None and left > 0:
            faulthandler.dump_traceback_later(left, file=self._dump)

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: Any) -> None:
        """Hand an xdist worker the setting, which the environment no longer holds."""
        node.workerinput[WORKER_KEY] = str(self._seconds)

    @pytest.hookimpl(trylast=True)
    def pytest_exception_interact(self) -> None:
        self._arm()  # pytest has just cancelled the timer

    def pytest_sessionfinish(self, exitstatus: int) -> None:
        self._interrupted = exitstatus == pytest.ExitCode.INTERRUPTED

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        if self._report is not None:
            self._report.started(nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if self._report is not None:
            self._report.reported(report)

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        if self._report is not None:
            self._report.finished(nodeid)
        self._arm()

    def pytest_unconfigure(self) -> None:
        """The process is done. A worker removes its dump only when the timer never wrote it: a
        written one is the record, and only the controller knows whether the run ended. The
        controller removes the whole folder when the run ended on its own, whatever its result:
        its JUnit report says what happened. An interrupted run (a cancelled job's runner
        interrupts before it kills) keeps everything, since it never reached its end."""
        faulthandler.cancel_dump_traceback_later()
        if self._dump is not None:
            self._dump.close()
            self._dump = None
        if self._report is None:
            with contextlib.suppress(OSError):
                if self._dump_path.stat().st_size == 0:
                    self._dump_path.unlink()
            return
        self._report.close(keep=self._interrupted)
        if not self._interrupted:
            shutil.rmtree(self._folder, ignore_errors=True)


class _Report:
    """Every test that has started, its outcome once it has one, written as JUnit every
    :data:`EVERY` seconds by a thread of its own while something has changed."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._tests: dict[str, dict[str, Any]] = {}
        self._changed = False
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._write_while_running, daemon=True)
        self._writer.start()

    def started(self, nodeid: str) -> None:
        with self._lock:
            self._tests.setdefault(nodeid, {"seconds": 0.0, "outcome": None, "done": False})
            self._changed = True

    def reported(self, report: pytest.TestReport) -> None:
        with self._lock:
            test = self._tests.setdefault(
                report.nodeid, {"seconds": 0.0, "outcome": None, "done": False}
            )
            test["seconds"] += report.duration
            if report.failed and test["outcome"] is None:
                test["outcome"] = ("failure" if report.when == "call" else "error", report.when)
            elif report.skipped and test["outcome"] is None:
                test["outcome"] = ("skipped", report.when)
            self._changed = True

    def finished(self, nodeid: str) -> None:
        with self._lock:
            if nodeid in self._tests:
                self._tests[nodeid]["done"] = True
                self._changed = True

    def close(self, *, keep: bool) -> None:
        """Stop writing; with *keep*, write the report once more, as it stands, and leave it."""
        self._stop.set()
        self._writer.join()
        if keep:
            with self._lock:
                self._path.write_text(_junit(self._tests), encoding="utf-8")
        else:
            self._path.unlink(missing_ok=True)

    def _write_while_running(self) -> None:
        while not self._stop.wait(EVERY):
            with self._lock:
                if not self._changed:
                    continue
                self._changed = False
                text = _junit(self._tests)
            partial = self._path.with_suffix(".partial")
            partial.write_text(text, encoding="utf-8")
            os.replace(partial, self._path)


def _junit(tests: dict[str, dict[str, Any]]) -> str:
    cases = []
    for nodeid, test in tests.items():
        names = mangle_test_address(nodeid)
        attrs = (
            f"classname={quoteattr('.'.join(names[:-1]))} name={quoteattr(names[-1])} "
            f'time="{test["seconds"]:.3f}"'
        )
        if not test["done"]:
            body = f"<error message={quoteattr(STILL_RUNNING)}>{escape(nodeid)}</error>"
        elif test["outcome"] is None:
            body = ""
        else:
            kind, when = test["outcome"]
            body = f"<{kind} message={quoteattr(f'{kind} in {when}')} />"
        cases.append(f"<testcase {attrs}>{body}</testcase>")
    running = sum(1 for t in tests.values() if not t["done"])
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="so far" tests="{len(tests)}" errors="{running}">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    )
