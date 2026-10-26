"""The suite in an order drawn from a seed, and the order each process ran (#3949).

``--random-order-seed N`` shuffles what was collected: the modules first, then the tests within
each module, with ``random.Random(N)`` and nothing else, so the order is a pure function of the
collected ids and the seed. Every xdist worker collects the same ids and draws the same order, as
xdist requires; a different seed gives a different one. Without the option nothing moves.

A test's module stays whole, so a module-scoped fixture is still set up once. Each process
appends the tests it actually runs, in the order it runs them, to
``reports/random-order/order-<seed>-<worker>.jsonl`` (``main`` without xdist): its real worker
and its position, which under ``--dist worksteal`` is not the collection order. With the
state-leak report (``tests/state_leak_guard.py``) that is what a failing order leaves behind: the
order, the allocation of tests to workers, and the state each test left before any fence
restored it.

A failing worker's own sequence is its order file. Under ``--dist worksteal`` a worker runs a
share of the seed's order and then tests it steals from the tail of another's, so
``pytest -n 0 tests --shard-list <list> --random-order-seed <seed>``, which reruns the seed's
whole order in one process, runs no one worker's sequence: it puts tests that ran on different
workers into one process. ``--replay-order FILE`` runs one: exactly the tests the file lists, in
its order, in one process. The file is a worker's order file, or one node id a line, and a listed
test that was not collected stops the run, since a replay that drops a test is not that order.
``tools/ci/bisect_pollution.py`` replays a failing worker's tests to find the one that left it
failing.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

#: Where each process writes the order it ran, under the run's root directory.
FOLDER = "reports/random-order"


def _seed(text: str) -> int:
    try:
        value = int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"a seed is 0 or more, not {value}")
    return value


def addoption(parser) -> None:
    parser.addoption(
        "--random-order-seed",
        type=_seed,
        default=None,
        help="run the modules, and the tests within each, in an order drawn from this seed "
        "(tests/random_order.py)",
    )
    parser.addoption(
        "--replay-order",
        type=Path,
        default=None,
        help="run exactly the tests this file lists, in its order, in one process: a worker's "
        "order file, or one node id a line (tests/random_order.py)",
    )


def replayed(text: str) -> list[str]:
    """The node ids *text* lists, in order: an order file's lines (JSON with ``nodeid`` and
    ``position``, read by position) or one node id a line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(line.startswith("{") for line in lines):
        rows = sorted((json.loads(line) for line in lines), key=lambda row: int(row["position"]))
        return [str(row["nodeid"]) for row in rows]
    return lines


def _module(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def shuffled(nodeids: Iterable[str], seed: int) -> list[str]:
    """*nodeids* in the order *seed* draws: modules shuffled, then each module's tests."""
    by_module: dict[str, list[str]] = {}
    for nodeid in nodeids:
        by_module.setdefault(_module(nodeid), []).append(nodeid)
    draw = random.Random(seed)
    modules = list(by_module)
    draw.shuffle(modules)
    out: list[str] = []
    for module in modules:
        tests = list(by_module[module])
        draw.shuffle(tests)
        out += tests
    return out


class Plugin:
    """Reorders the collected items, and writes the order this process runs."""

    def __init__(self, config, seed: int) -> None:
        self.config = config
        self.seed = seed
        self.worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        self.position = 0
        self._out = Path(config.rootpath) / FOLDER / f"order-{seed}-{self.worker}.jsonl"
        self._started = False

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        by_id: dict[str, list[pytest.Item]] = {}
        for item in items:
            by_id.setdefault(item.nodeid, []).append(item)
        order = shuffled(list(dict.fromkeys(i.nodeid for i in items)), self.seed)
        items[:] = [item for nodeid in order for item in by_id[nodeid]]

    def pytest_runtest_logstart(
        self, nodeid: str, location: Sequence[object]
    ) -> None:  # noqa: ARG002
        if getattr(self.config, "workerinput", None) is None and _is_controller_of_workers(
            self.config
        ):
            return  # xdist's controller only relays: each worker writes what it ran
        self.position += 1
        self._out.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if self._started else "w"
        self._started = True
        with self._out.open(mode, encoding="utf-8") as handle:
            line = {
                "nodeid": nodeid,
                "position": self.position,
                "worker": self.worker,
                "seed": self.seed,
            }
            handle.write(json.dumps(line, sort_keys=True) + "\n")

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if getattr(self.config, "workerinput", None) is not None:
            return
        terminalreporter.write_line(
            f"random order: seed {self.seed}; each process's order is in {FOLDER}/"
        )


def _is_controller_of_workers(config) -> bool:
    numprocesses = getattr(config.option, "numprocesses", None)
    dist = getattr(config.option, "dist", "no")
    return bool(numprocesses) and dist != "no"


class Replay:
    """Runs exactly the listed tests, in the listed order."""

    def __init__(self, nodeids: list[str]) -> None:
        self.nodeids = nodeids

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items: list[pytest.Item]) -> None:
        by_id: dict[str, list[pytest.Item]] = {}
        for item in items:
            by_id.setdefault(item.nodeid, []).append(item)
        missing = [nodeid for nodeid in self.nodeids if nodeid not in by_id]
        if missing:
            raise pytest.UsageError(
                f"--replay-order lists {len(missing)} test(s) this run did not collect, first "
                f"{missing[0]!r}: collect the files they are in"
            )
        listed = set(self.nodeids)
        dropped = [item for item in items if item.nodeid not in listed]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
        items[:] = [item for nodeid in dict.fromkeys(self.nodeids) for item in by_id[nodeid]]


def configure(config) -> None:
    seed = config.getoption("--random-order-seed")
    replay = config.getoption("--replay-order")
    if replay is not None:
        if seed is not None:
            raise pytest.UsageError("--replay-order runs the order it is given: drop the seed")
        if _is_controller_of_workers(config) or getattr(config, "workerinput", None) is not None:
            raise pytest.UsageError("--replay-order runs in one process: pass -n 0")
        try:
            nodeids = replayed(replay.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError) as exc:
            raise pytest.UsageError(f"--replay-order could not read {replay}: {exc}") from None
        config.pluginmanager.register(Replay(nodeids), "random-order-replay")
    if seed is not None:
        config.pluginmanager.register(Plugin(config, seed), "random-order")
