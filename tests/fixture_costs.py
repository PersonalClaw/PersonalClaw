"""The fixture-cost report: how long each fixture's setup and teardown take, per call, over a run.

Enabled by ``--fixture-costs`` (``tests/conftest.py``), it writes ``reports/fixture-costs.json``
under the run's root, beside the suite's junit.

Each call is measured twice: in wall time (``time.perf_counter``) and in this thread's CPU time
(``time.thread_time``). On a loaded machine a fixture's wall time is mostly waiting for a core,
and its CPU time is what it would cost on an idle one. A fixture that waits on something (a
child process, a thread, a sleep) shows the wait in wall time only.

Setup
-----
What is measured is a fixture's OWN setup. pytest sets up a fixture's declared arguments before
it calls ``pytest_fixture_setup`` for that fixture, so they never nest inside their dependent's
hook. Setups nest only under a ``request.getfixturevalue`` call made inside a hook: from a
fixture's own code, or by pytest-asyncio's wrapper, which fetches its ``_<scope>_scoped_runner``
inside the hook of every async fixture. That call sets up the fetched fixture inside the hook,
together with any of its own dependencies not yet set up (the runner's session-scoped
``_asyncio_loop_factory``, the first time). A plain wrapper would charge that nested setup to the
fixture that asked for it, and count it twice in its test's total. Each call's time therefore
excludes the time spent setting up fixtures inside it, and each of those is charged to itself. A
fixture served from its cache never reaches the hook and costs nothing here.

Teardown
--------
pytest tears a fixture down in ``FixtureDef.finish``, which runs the fixture's finalizers last
added first: the code after a ``yield`` and each ``request.addfinalizer``, which the fixture adds
while it is set up, and before them, the ``finish`` of every fixture set up later that depends on
it. The first finalizer pytest adds, so the last to run, calls ``pytest_fixture_post_finalizer``.
So the clock starts in a finalizer this report adds as the fixture's setup returns, after the
fixture's own and before any dependent's, and stops in ``pytest_fixture_post_finalizer``: a
dependent's teardown runs before the clock starts and is charged to the dependent. A fixture torn
down while another is being set up (a fixture whose parameter changed is finished when it is next
asked for) is charged to that setup as well; no fixture in this suite is torn down that way inside
a ``request.getfixturevalue`` call, which is the only place it could happen.

Each test's totals are the sums of the fixture setups made while that test was being set up, and
of the fixture teardowns made while it was being torn down; a module-scoped fixture's teardown
lands in the teardown of the module's last test. The setup total is the number the rule for
autouse fixtures in ``tests/README.md`` is judged by.

Merging across xdist workers: each worker keeps its own raw durations, and hands them to the
controller in ``workeroutput`` when it finishes, as ``real_home_guard`` relays its findings. The
controller merges them and writes the one file. A run without workers (``-n 0``) has no
controller and no ``workeroutput``, and writes its own measurements the same way. Raw durations,
not per-worker summaries, cross over, because a median of medians is not a median.
"""

from __future__ import annotations

import inspect
import json
import statistics
import time
from pathlib import Path

import pytest

#: Where the report is written, under the run's root.
REPORT = Path("reports") / "fixture-costs.json"

#: The key a worker's measurements travel under in ``workeroutput``.
_WORKER_KEY = "fixture_costs"

#: The two phases measured, and the two clocks each is measured on.
_PHASES = ("setup", "teardown")
_CLOCKS = ("wall", "cpu")


def _start() -> tuple[float, float]:
    """Wall and this thread's CPU time, in microseconds, as a span starts: the wall clock first."""
    wall = time.perf_counter() * 1e6
    return wall, time.thread_time() * 1e6


def _stop() -> tuple[float, float]:
    """Wall and this thread's CPU time, in microseconds, as a span stops: the CPU clock first.

    Read in this order, the wall span holds the CPU span, so a span's wall time is never less than
    its CPU time: a thread spends no more CPU time than the time that passes. Read wall first at
    both ends, the CPU span started and stopped one read later than the wall span, and when the
    last read took longer than the second one, a busy span read more CPU time than wall time
    (5.018 ms against 5.017 ms in CI). Each is rounded to whole microseconds with ``round``, which
    keeps the order. A setup's own time is its span less the setups nested in it, and a difference
    keeps no such order."""
    cpu = time.thread_time() * 1e6
    return time.perf_counter() * 1e6, cpu


def _percentile(values: list[int], fraction: float) -> int:
    """The nearest-rank percentile of *values* (microseconds)."""
    ordered = sorted(values)
    rank = max(1, round(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _ms(microseconds: float) -> float:
    return round(microseconds / 1000, 3)


def _empty() -> dict[str, dict[str, list[int]]]:
    return {phase: {clock: [] for clock in _CLOCKS} for phase in _PHASES}


def _summary(measured: dict[str, list[int]]) -> dict:
    """Calls, then median, p95 and total in wall time, then median and total in CPU time."""
    wall, cpu = measured["wall"], measured["cpu"]
    if not wall:
        return {"calls": 0}
    return {
        "calls": len(wall),
        "median_ms": _ms(statistics.median(wall)),
        "p95_ms": _ms(_percentile(wall, 0.95)),
        "total_ms": _ms(sum(wall)),
        "cpu_median_ms": _ms(statistics.median(cpu)),
        "cpu_total_ms": _ms(sum(cpu)),
    }


class Plugin:
    """Times each fixture's own setup and teardown, and writes the merged report at the end."""

    def __init__(self, rootpath: Path) -> None:
        self._rootpath = rootpath
        #: ``where::name`` → its scope, whether it is autouse, and each call's microseconds, per
        #: phase and clock.
        self._fixtures: dict[str, dict] = {}
        #: Each test's fixture totals in microseconds, per phase and clock.
        self._per_test: dict[str, dict[str, list[int]]] = _empty()
        #: The (wall, cpu) spent in calls nested inside it, one entry per fixture setup in progress.
        self._nested: list[list[float]] = []
        #: When each fixture's teardown started, by ``id(fixturedef)``.
        self._tearing_down: dict[int, tuple[float, float]] = {}
        #: The running (wall, cpu) total of the test whose setup or teardown is in progress.
        self._this_test: list[float] | None = None
        self._workers = 0
        self._keys: dict[int, str] = {}

    def _key(self, fixturedef) -> str:
        key = self._keys.get(id(fixturedef))
        if key is None:
            try:
                where = Path(inspect.getsourcefile(fixturedef.func) or "?")
                where_text = str(where.relative_to(self._rootpath))
            except ValueError:
                where_text = where.name
            except TypeError:
                where_text = "?"
            key = f"{where_text}::{fixturedef.argname}"
            self._keys[id(fixturedef)] = key
            self._fixtures.setdefault(
                key,
                {
                    "scope": fixturedef.scope,
                    "autouse": bool(getattr(fixturedef, "_autouse", False)),
                    **_empty(),
                },
            )
        return key

    def _record(self, key: str, phase: str, wall: float, cpu: float) -> None:
        entry = self._fixtures[key][phase]
        entry["wall"].append(round(wall))
        entry["cpu"].append(round(cpu))
        if self._this_test is not None:
            self._this_test[0] += wall
            self._this_test[1] += cpu

    def _per_test_phase(self, phase: str):
        self._this_test = [0.0, 0.0]
        try:
            yield
        finally:
            wall, cpu = self._this_test
            self._per_test[phase]["wall"].append(round(wall))
            self._per_test[phase]["cpu"].append(round(cpu))
            self._this_test = None

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item):  # noqa: ARG002 — hook signature
        phase = self._per_test_phase("setup")
        next(phase)
        try:
            return (yield)
        finally:
            next(phase, None)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item, nextitem):  # noqa: ARG002 — hook signature
        phase = self._per_test_phase("teardown")
        next(phase)
        try:
            return (yield)
        finally:
            next(phase, None)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):  # noqa: ARG002 — hook signature
        key = self._key(fixturedef)
        self._nested.append([0.0, 0.0])
        wall0, cpu0 = _start()
        try:
            return (yield)
        finally:
            wall1, cpu1 = _stop()
            wall, cpu = wall1 - wall0, cpu1 - cpu0
            nested = self._nested.pop()
            if self._nested:
                self._nested[-1][0] += wall
                self._nested[-1][1] += cpu
            self._record(key, "setup", wall - nested[0], cpu - nested[1])
            # Added now, after the fixture's own finalizers and before any dependent's `finish`,
            # so it runs once the dependents are torn down and before the fixture's own code.
            fixturedef.addfinalizer(lambda ident=id(fixturedef): self._teardown_starts(ident))

    def _teardown_starts(self, ident: int) -> None:
        self._tearing_down[ident] = _start()

    @pytest.hookimpl(tryfirst=True)
    def pytest_fixture_post_finalizer(self, fixturedef, request):  # noqa: ARG002 — hook signature
        started = self._tearing_down.pop(id(fixturedef), None)
        if started is None:
            return
        wall1, cpu1 = _stop()
        self._record(self._key(fixturedef), "teardown", wall1 - started[0], cpu1 - started[1])

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002 — xdist hook signature
        measured = getattr(node, "workeroutput", {}).get(_WORKER_KEY)
        if measured is None:
            return
        self._workers += 1
        for phase in _PHASES:
            for clock in _CLOCKS:
                self._per_test[phase][clock].extend(measured["per_test"][phase][clock])
        for key, entry in measured["fixtures"].items():
            mine = self._fixtures.setdefault(
                key, {"scope": entry["scope"], "autouse": entry["autouse"], **_empty()}
            )
            for phase in _PHASES:
                for clock in _CLOCKS:
                    mine[phase][clock].extend(entry[phase][clock])

    def pytest_sessionfinish(self, session) -> None:
        workeroutput = getattr(session.config, "workeroutput", None)
        if workeroutput is not None:
            # An xdist worker: the controller merges and writes.
            workeroutput[_WORKER_KEY] = {"fixtures": self._fixtures, "per_test": self._per_test}
            return
        path = self._rootpath / REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report(), indent=1) + "\n", encoding="utf-8")
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(f"fixture costs written to {path}")

    def report(self) -> dict:
        """The merged report: per-test totals, then each fixture, costliest in wall time first
        (setup and teardown together)."""
        fixtures = []
        for key, entry in self._fixtures.items():
            if not entry["setup"]["wall"] and not entry["teardown"]["wall"]:
                continue
            fixtures.append(
                {
                    "fixture": key,
                    "scope": entry["scope"],
                    "autouse": entry["autouse"],
                    **{phase: _summary(entry[phase]) for phase in _PHASES},
                }
            )
        fixtures.sort(
            key=lambda row: sum(row[phase].get("total_ms", 0.0) for phase in _PHASES),
            reverse=True,
        )
        return {
            "workers": self._workers,
            "tests": len(self._per_test["setup"]["wall"]),
            "per_test": {phase: _summary(self._per_test[phase]) for phase in _PHASES},
            "fixtures": fixtures,
        }
