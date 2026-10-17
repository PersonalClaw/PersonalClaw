"""The fixture-cost report: how long each fixture's setup takes, per call, over a run.

Enabled by ``--fixture-costs`` (``tests/conftest.py``), it writes ``reports/fixture-costs.json``
under the run's root, beside the suite's junit.

What is measured is a fixture's OWN setup. pytest resolves a fixture's dependencies inside the
``pytest_fixture_setup`` call it makes for that fixture (``request.getfixturevalue`` for each
argument), so a plain wrapper would charge ``tmp_path_factory`` to every fixture that is first to
ask for it. Each call's time therefore excludes the time spent setting up the fixtures it asked
for, and each of those is charged to itself. A fixture served from its cache never reaches the
hook and costs nothing here. Teardown is not measured.

Each test's setup total is the sum of the fixture setups made while that test was being set up,
which is the number the rule for autouse fixtures in ``tests/README.md`` is judged by.

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


def _percentile(values: list[int], fraction: float) -> int:
    """The nearest-rank percentile of *values* (microseconds)."""
    ordered = sorted(values)
    rank = max(1, round(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _ms(microseconds: float) -> float:
    return round(microseconds / 1000, 3)


class Plugin:
    """Times each fixture's own setup and writes the merged report at the session's end."""

    def __init__(self, rootpath: Path) -> None:
        self._rootpath = rootpath
        #: ``where::name`` → its scope, whether it is autouse, and each setup's microseconds.
        self._fixtures: dict[str, dict] = {}
        #: Each test's fixture-setup total, in microseconds.
        self._per_test: list[int] = []
        #: The time spent in dependencies, one entry per fixture setup in progress.
        self._nested: list[float] = []
        self._this_test: float | None = None
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
                    "us": [],
                },
            )
        return key

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item):  # noqa: ARG002 — hook signature
        self._this_test = 0.0
        try:
            return (yield)
        finally:
            self._per_test.append(round(self._this_test))
            self._this_test = None

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):  # noqa: ARG002 — hook signature
        key = self._key(fixturedef)
        self._nested.append(0.0)
        started = time.perf_counter()
        try:
            return (yield)
        finally:
            elapsed = (time.perf_counter() - started) * 1e6
            own = elapsed - self._nested.pop()
            if self._nested:
                self._nested[-1] += elapsed
            self._fixtures[key]["us"].append(round(own))
            if self._this_test is not None:
                self._this_test += own

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002 — xdist hook signature
        measured = getattr(node, "workeroutput", {}).get(_WORKER_KEY)
        if measured is None:
            return
        self._workers += 1
        self._per_test.extend(measured["per_test"])
        for key, entry in measured["fixtures"].items():
            mine = self._fixtures.setdefault(
                key, {"scope": entry["scope"], "autouse": entry["autouse"], "us": []}
            )
            mine["us"].extend(entry["us"])

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
        """The merged report: per-test totals, then each fixture, costliest in total first."""
        fixtures = []
        for key, entry in self._fixtures.items():
            values = entry["us"]
            if not values:
                continue
            fixtures.append(
                {
                    "fixture": key,
                    "scope": entry["scope"],
                    "autouse": entry["autouse"],
                    "calls": len(values),
                    "median_ms": _ms(statistics.median(values)),
                    "p95_ms": _ms(_percentile(values, 0.95)),
                    "total_ms": _ms(sum(values)),
                }
            )
        fixtures.sort(key=lambda row: row["total_ms"], reverse=True)
        per_test = self._per_test
        return {
            "workers": self._workers,
            "tests": len(per_test),
            "per_test_setup": {
                "median_ms": _ms(statistics.median(per_test)) if per_test else 0.0,
                "p95_ms": _ms(_percentile(per_test, 0.95)) if per_test else 0.0,
            },
            "fixtures": fixtures,
        }
