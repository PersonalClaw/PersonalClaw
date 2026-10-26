"""``python -m tools.ci bisect-pollution <nodeid> --orders DIR``: the test that left another failing
(#3949).

A test that fails only after others ran in its process was left failing by one of them. The
random-order leg keeps each worker's sequence as an order file
(``reports/random-order/order-<seed>-<worker>.jsonl``, ``tests/random_order.py``); this reads the
failing test's, and replays its tests in one process (``pytest -n 0 --replay-order``):

1. the failing test alone: if it fails there, nothing before it is to blame;
2. every test its worker ran before it, then it: if it passes, the failure is not this sequence's;
3. half of the tests before it, then it, keeping whichever half still fails it, until one is left;
4. that one, then the failing test, when no run has yet shown the pair failing.

So *n* tests before it take ``2 + ceil(log2 n)`` runs, and one more when the last is unproven:
the 600 or so tests a worker runs before a late test take 12 or 13. A failure that needs two of
the earlier tests together shows as the last pair passing, and is said so.

Each run has a ``--basetemp`` of its own and a JUnit report, read for the failing test's result
alone: another test failing in a replay does not move the search. Standard library only, like
every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .quarantine_rerun import junit_case, result_of

#: How long one replay may take before it is killed and counts as not run.
RUN_SECONDS = 20 * 60
#: A JUnit report larger than this is not read.
MAX_JUNIT_BYTES = 8 * 1024 * 1024


class BisectError(Exception):
    """The order cannot be read or bisected; the message says why."""


@dataclass
class Run:
    #: How many tests ran before the failing one.
    before: int
    #: The failing test's result: ``passed``, ``failed``, ``skipped`` or ``not_run``.
    result: str
    seconds: float


@dataclass
class Finding:
    nodeid: str
    order_file: str
    #: ``polluter`` (one test found), ``fails_alone``, ``not_reproduced``, ``not_one_test``
    #: (no single earlier test leaves it failing) or ``inconclusive`` (a run did not finish).
    outcome: str
    polluter: str | None = None
    #: Whether a run showed the polluter, then the failing test, failing it.
    confirmed: bool = False
    runs: list[Run] = field(default_factory=list)

    def sentence(self) -> str:
        n = len(self.runs)
        if self.outcome == "polluter":
            proof = "shown" if self.confirmed else "inferred"
            return f"{self.polluter} leaves {self.nodeid} failing ({proof}, {n} runs)"
        return {
            "fails_alone": f"{self.nodeid} fails alone: nothing before it is to blame",
            "not_reproduced": f"{self.nodeid} passes after the tests its worker ran before it",
            "not_one_test": f"no single test before {self.nodeid} leaves it failing ({n} runs)",
            "inconclusive": f"a replay of {self.nodeid} did not finish ({n} runs)",
        }[self.outcome]


def order_file(orders: Path, nodeid: str, seed: int | None, worker: str | None) -> Path:
    """The one order file under *orders* in which *nodeid* ran, narrowed by *seed* and
    *worker*."""
    found: list[Path] = []
    for path in sorted(orders.rglob("order-*-*.jsonl")):
        stem = path.name.removesuffix(".jsonl").split("-", 2)
        if len(stem) != 3:
            continue
        if seed is not None and stem[1] != str(seed):
            continue
        if worker is not None and stem[2] != worker:
            continue
        if any(row["nodeid"] == nodeid for row in _rows(path)):
            found.append(path)
    if not found:
        raise BisectError(f"no order file under {orders} ran {nodeid}")
    if len(found) > 1:
        names = ", ".join(str(p.relative_to(orders)) for p in found)
        raise BisectError(
            f"{nodeid} ran in {len(found)} order files ({names}): name --seed/--worker"
        )
    return found[0]


def _rows(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            rows.append({"nodeid": str(row["nodeid"]), "position": int(row["position"])})
        except (ValueError, KeyError, TypeError) as exc:
            raise BisectError(f"{path}:{number}: not an order line ({exc})") from None
    return sorted(rows, key=lambda row: row["position"])


def preceding(path: Path, nodeid: str) -> list[str]:
    """The tests *path*'s worker ran before *nodeid*, in its order."""
    ids = [row["nodeid"] for row in _rows(path)]
    return ids[: ids.index(nodeid)]


#: Runs one replay: the tests, the failing test last; its result.
Replay = Callable[[Sequence[str]], str]


def search(nodeid: str, before: Sequence[str], replay: Replay, *, path: str = "") -> Finding:
    """Bisect *before* for the one test that leaves *nodeid* failing, through *replay*."""
    finding = Finding(nodeid=nodeid, order_file=path, outcome="polluter")

    def run(tests: Sequence[str]) -> str:
        started = time.monotonic()
        result = replay([*tests, nodeid])
        finding.runs.append(Run(len(tests), result, round(time.monotonic() - started, 2)))
        return result

    alone = run([])
    if alone == "failed":
        finding.outcome = "fails_alone"
        return finding
    if alone == "not_run":
        finding.outcome = "inconclusive"
        return finding
    if not before:
        finding.outcome = "not_reproduced"
        return finding
    whole = run(before)
    if whole != "failed":
        finding.outcome = "inconclusive" if whole == "not_run" else "not_reproduced"
        return finding
    left, shown = list(before), len(before) == 1
    while len(left) > 1:
        half = left[: len(left) // 2]
        result = run(half)
        if result == "not_run":
            finding.outcome = "inconclusive"
            return finding
        failed = result == "failed"
        left = half if failed else left[len(half) :]
        shown = failed and len(left) == 1
    finding.polluter = left[0]
    if not shown:
        pair = run(left)
        if pair == "not_run":
            finding.outcome = "inconclusive"
            return finding
        if pair != "failed":
            finding.outcome, finding.polluter = "not_one_test", None
            return finding
    finding.confirmed = True
    return finding


def runs_at_most(before: int) -> int:
    """The most runs :func:`search` takes over *before* earlier tests."""
    return 2 + (math.ceil(math.log2(before)) if before > 1 else 0) + (1 if before > 1 else 0)


def pytest_replay(
    *,
    python: str,
    root: Path,
    scratch: Path,
    pytest_args: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    seconds: float = RUN_SECONDS,
) -> Replay:
    """A :data:`Replay` that runs ``pytest -n 0 --replay-order`` in *root*, each run under
    *scratch* with a basetemp and a JUnit report of its own."""
    count = 0

    def replay(tests: Sequence[str]) -> str:
        nonlocal count
        count += 1
        here = scratch / f"run-{count}"
        here.mkdir(parents=True)
        listed = here / "order.txt"
        listed.write_text("".join(f"{t}\n" for t in tests), encoding="utf-8")
        xml = here / "junit.xml"
        files = list(dict.fromkeys(t.split("::", 1)[0] for t in tests))
        command = [
            python,
            "-m",
            "pytest",
            *pytest_args,
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            f"--basetemp={here / 'basetemp'}",
            f"--junitxml={xml}",
            "--replay-order",
            str(listed),
            "-q",
            *files,
        ]
        log = (here / "pytest.log").open("wb")
        process = subprocess.Popen(
            command,
            cwd=root,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return "not_run"
        finally:
            log.close()
        return _result(xml, tests[-1])

    return replay


def _result(report: Path, nodeid: str) -> str:
    try:
        data = report.read_bytes()
    except OSError:
        return "not_run"
    if len(data) > MAX_JUNIT_BYTES or b"<!DOCTYPE" in data.upper():
        return "not_run"
    try:
        tree = ET.fromstring(data)
    except ET.ParseError:
        return "not_run"
    want = junit_case(nodeid)
    for case in tree.iter("testcase"):
        if (case.get("classname") or "", case.get("name") or "") == want:
            return result_of(case)
    return "not_run"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci bisect-pollution")
    parser.add_argument("nodeid", help="the test that failed only after others ran")
    parser.add_argument(
        "--orders", type=Path, required=True, help="a random-order artifact's folder"
    )
    parser.add_argument("--seed", type=int, default=None, help="the order the test failed in")
    parser.add_argument("--worker", default=None, help="the worker it failed on, as gw3")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="where pytest runs")
    parser.add_argument("--python", default=sys.executable, help="the interpreter pytest runs on")
    parser.add_argument("--out", type=Path, default=None, help="write the finding here as JSON")
    args = parser.parse_args(argv)
    try:
        path = order_file(args.orders, args.nodeid, args.seed, args.worker)
        before = preceding(path, args.nodeid)
    except BisectError as exc:
        print(f"bisect-pollution: {exc}", file=sys.stderr)
        return 2
    print(
        f"bisect-pollution: {len(before)} tests ran before it in {path.name}; at most "
        f"{runs_at_most(len(before))} runs"
    )
    with tempfile.TemporaryDirectory(prefix="bisect-pollution-") as scratch:
        replay = pytest_replay(python=args.python, root=args.root, scratch=Path(scratch))
        finding = search(args.nodeid, before, replay, path=str(path))
    print(f"bisect-pollution: {finding.sentence()}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(asdict(finding), indent=2) + "\n", encoding="utf-8")
    return 0 if finding.outcome == "polluter" else 1
