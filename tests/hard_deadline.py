"""A hard deadline for each test, behind pytest-timeout's alarm: past it, it is killed and named.

pytest-timeout's signal method arms one alarm per test (``setitimer``, no interval). When it fires
in the call, ``Failed`` is raised there, and the teardown runs with the alarm spent: a teardown
that never ends is then never interrupted, and its worker holds the job to the job's own limit.
The upload tests' teardown did, for two hours. The alarm had landed where the loop had taken a
callback off its queue and not run it, so a task was never woken, not even to be cancelled, and
``asyncio.Runner.close`` waited for it.

So each test also gets a deadline, from its start, of twice its own timeout and :data:`GRACE`
seconds: its ``timeout`` mark's, else the run's (``--timeout``, the ini's, ``PYTEST_TIMEOUT``). A
watchdog thread waits for it. Past it, the watchdog writes every thread's stack and every asyncio
task not done, with where each waits, to a file, and ends the process with ``os._exit``. Under
xdist the controller reports the worker as crashed while running the test, and attaches that file
to the report (:meth:`Controller.pytest_handlecrashitem`), so the stacks reach the terminal and
the JUnit report, and xdist replaces the worker. A test the alarm already ended is never passed
or skipped here: a hang becomes a failure that names it.

The watchdog holds the GIL to read the stacks (``sys._current_frames``), never faulthandler's
dump: that writes them from its own thread without the GIL, while execnet's threads run, and it
crashed xdist workers that were not hung (#5255, reverted in 7ead7cebf). A process blocked in
native code that holds the GIL is beyond any Python thread, this one included.
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import pytest

#: Seconds past twice a test's own timeout before the watchdog ends its process.
GRACE = 30.0
#: The exit status of a process the watchdog ends.
EXIT = 70
#: The ini option a planted run lowers :data:`GRACE` by, so its deadline comes in seconds.
GRACE_INI = "hard_deadline_grace"


def _ini(config: pytest.Config, name: str) -> str | None:
    try:
        return config.getini(name) or None
    except ValueError:  # not registered: pytest-timeout or this module is not loaded
        return None


def timeout_of(item: pytest.Item) -> float | None:
    """The test's own timeout, in pytest-timeout's order: its ``timeout`` mark's, else
    ``--timeout``'s, ``PYTEST_TIMEOUT``'s, then the ini's. ``None`` where there is none or it is
    0, as pytest-timeout then arms nothing."""
    mark = item.get_closest_marker("timeout")
    value = None
    if mark is not None:
        value = mark.args[0] if mark.args else mark.kwargs.get("timeout")
    if value is None:
        value = item.config.getoption("timeout", None)
    if value is None:
        value = os.environ.get("PYTEST_TIMEOUT") or _ini(item.config, "timeout")
    if value in (None, ""):
        return None
    return float(value) or None


def grace_of(config: pytest.Config) -> float:
    return float(_ini(config, GRACE_INI) or GRACE)


def deadline_of(item: pytest.Item) -> float | None:
    """Seconds from the test's start to its hard deadline: twice its timeout, and the grace."""
    timeout = timeout_of(item)
    return None if timeout is None else timeout * 2 + grace_of(item.config)


def dump_path(config: pytest.Config, worker: str) -> Path:
    """Where *worker*'s dump goes: beside ``--junitxml``'s report, as ``hard-deadline-<leg>-<worker>
    .txt``; without one, in the temporary folder, under the run's root."""
    xml = getattr(config.option, "xmlpath", None)
    if xml:
        junit = Path(xml)
        leg = junit.stem.removeprefix("junit-") or "run"
        return junit.parent / f"hard-deadline-{leg}-{worker}.txt"
    tag = hashlib.sha256(str(config.rootpath).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"pytest-hard-deadline-{tag}-{worker}.txt"


def stacks() -> str:
    """Every thread's stack and every asyncio task not done, with what it awaits."""
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    out = []
    for ident, frame in sys._current_frames().items():
        out.append(f"Thread {names.get(ident, '?')} ({ident}):\n")
        out.extend(traceback.format_stack(frame))
    tasks = [o for o in gc.get_objects() if isinstance(o, asyncio.Task) and not o.done()]
    out.append(f"\n{len(tasks)} asyncio task(s) not done:\n")
    for task in tasks:
        out.append(f"  {task!r}\n"[:400])
        for frame in task.get_stack():
            out.append(
                f"    {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}\n"
            )
    return "".join(out)


class Watchdog:
    """One thread that waits for the test in flight's deadline, and ends the process past it."""

    def __init__(self, path: Path, *, end=os._exit) -> None:
        self.path = path
        self._end = end
        self._armed: tuple[str, float, float, float] | None = None  # nodeid, at, timeout, grace
        self._changed = threading.Condition()
        self._thread = threading.Thread(target=self._wait, name="hard-deadline", daemon=True)
        self._thread.start()

    def arm(self, nodeid: str, timeout: float, grace: float) -> None:
        with self._changed:
            self._armed = (nodeid, time.monotonic() + timeout * 2 + grace, timeout, grace)
            self._changed.notify()

    def disarm(self) -> None:
        with self._changed:
            self._armed = None
            self._changed.notify()

    def _wait(self) -> None:
        with self._changed:
            while True:
                if self._armed is None:
                    self._changed.wait()
                    continue
                nodeid, deadline, timeout, grace = self._armed
                left = deadline - time.monotonic()
                if left > 0:
                    self._changed.wait(left)
                    continue
                self._fire(nodeid, timeout, grace)

    def _fire(self, nodeid: str, timeout: float, grace: float) -> None:
        text = (
            f"hard deadline: {nodeid} was still running {timeout * 2 + grace:g}s after it started,"
            f" twice its timeout of {timeout:g}s and {grace:g}s, so its process (pid"
            f" {os.getpid()}) was ended.\n\n{stacks()}"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")
        except OSError:
            pass
        try:
            sys.__stderr__.write(text)
            sys.__stderr__.flush()
        except (OSError, ValueError, AttributeError):
            pass
        self._end(EXIT)


class Worker:
    """In the process that runs the tests: arms the watchdog for each test's whole protocol."""

    def __init__(self, watchdog: Watchdog) -> None:
        self.watchdog = watchdog

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem):
        timeout = timeout_of(item)
        if timeout is not None:
            self.watchdog.arm(item.nodeid, timeout, grace_of(item.config))
        try:
            yield
        finally:
            self.watchdog.disarm()


class Controller:
    """In xdist's controller: attaches a crashed worker's dump to the report of its test."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config

    @pytest.hookimpl
    def pytest_handlecrashitem(self, crashitem: str, report: pytest.TestReport, sched) -> None:
        gateway = getattr(getattr(report, "node", None), "gateway", None)
        worker = getattr(gateway, "id", None)
        if worker is None:
            return
        path = dump_path(self.config, str(worker))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        if crashitem in text:
            report.longrepr = f"{report.longrepr}\n\n{text}"
            path.unlink(missing_ok=True)


def addoption(parser: pytest.Parser) -> None:
    parser.addini(GRACE_INI, f"seconds past twice a test's timeout before it is killed ({GRACE})")


def configure(config: pytest.Config) -> None:
    """A watchdog in each process that runs tests, and the dump's reader in xdist's controller."""
    workers = bool(getattr(config.option, "numprocesses", None))
    if hasattr(config, "workerinput"):
        worker = config.workerinput["workerid"]
    elif workers:
        config.pluginmanager.register(Controller(config), "hard-deadline-controller")
        return
    else:
        worker = "main"
    path = dump_path(config, worker)
    path.unlink(missing_ok=True)
    config.pluginmanager.register(Worker(Watchdog(path)), "hard-deadline")
