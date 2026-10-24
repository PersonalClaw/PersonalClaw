"""The tests a pytest run has started and not finished, written where CI keeps them (#3905).

A worker blocked in native code that never comes back to the interpreter is out of every signal's
reach: pytest-timeout's SIGALRM never fires, and under ``--dist worksteal`` the blocked worker
cannot hand its queue over, so the run stalls with every worker alive and a log of dots that names
nothing. ci.yml's test-shard (3) in run 40056079320 stopped at 79% for 36 minutes that way.

The xdist controller hears each test start (``pytest_runtest_logstart``) and finish
(``pytest_runtest_logfinish``), and each report names the worker it came from. This plugin keeps
the tests started and not finished, each with its worker and the time it started, and a daemon
thread rewrites them, oldest first, every :data:`INTERVAL` seconds to ``in-flight-<leg>.txt``
beside the run's JUnit report (``junit-<leg>.xml``): a temporary file renamed over it, so a reader
never sees half of one. After a hang the oldest entry is the test. A run that ends leaves it
empty.

It runs on the controller alone. Where ``workerinput`` exists it does nothing, and it arms nothing
in a worker: no signal, no faulthandler (#5255's dump, armed in every process, crashed workers that
were not hung). Without xdist the one process is the controller, and its tests run on ``main``.
It is armed only by ``--junitxml``, as CI runs every suite, so a run without a report writes
nothing.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

#: How often the file is rewritten, in seconds.
INTERVAL = 10.0


def report_path(config: pytest.Config) -> Path | None:
    """``in-flight-<leg>.txt`` beside ``--junitxml``'s ``junit-<leg>.xml``, or ``None`` when the
    run writes no JUnit report."""
    xml = getattr(config.option, "xmlpath", None)
    if not xml:
        return None
    junit = Path(xml)
    leg = junit.stem.removeprefix("junit-") or "run"
    return junit.parent / f"in-flight-{leg}.txt"


class Plugin:
    def __init__(self, path: Path, *, workers: bool, interval: float = INTERVAL) -> None:
        self.path = path
        self.interval = interval
        #: Whether the run has xdist workers: until a test's first report names its worker, it
        #: reads "?" there, and "main" where there are none.
        self._unknown = "?" if workers else "main"
        self._started: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._keep, name="in-flight", daemon=True)

    def lines(self) -> list[str]:
        """``<started, UTC>\\t<worker>\\t<nodeid>`` for each test in flight, oldest first."""
        with self._lock:
            entries = sorted(self._started.items(), key=lambda item: item[1][0])
        return [
            f"{datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"\t{worker}\t{nodeid}"
            for nodeid, (at, worker) in entries
        ]

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(f"{line}\n" for line in self.lines()), encoding="utf-8")
        os.replace(tmp, self.path)

    def _keep(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.write()
            except OSError:
                pass  # the next turn writes it, and a run is never failed for this file

    @pytest.hookimpl
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.write()
        self._thread.start()

    @pytest.hookimpl
    def pytest_runtest_logstart(self, nodeid: str, location: Any) -> None:
        with self._lock:
            self._started[nodeid] = (time.time(), self._unknown)

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        gateway = getattr(getattr(report, "node", None), "gateway", None)
        worker = getattr(gateway, "id", None)
        if worker is None:
            return
        with self._lock:
            if report.nodeid in self._started:
                self._started[report.nodeid] = (self._started[report.nodeid][0], str(worker))

    @pytest.hookimpl
    def pytest_runtest_logfinish(self, nodeid: str, location: Any) -> None:
        with self._lock:
            self._started.pop(nodeid, None)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self._stop.set()
        self.write()


def configure(config: pytest.Config) -> None:
    """Register the plugin on the controller of a run that writes a JUnit report."""
    if hasattr(config, "workerinput"):
        return
    path = report_path(config)
    if path is None:
        return
    workers = bool(getattr(config.option, "numprocesses", None))
    config.pluginmanager.register(Plugin(path, workers=workers), "in-flight")
