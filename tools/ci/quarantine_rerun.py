"""``python -m tools.ci quarantine-rerun``: every quarantined test run ``ROUNDS`` times, for the
weekly CI health report (#3948).

The report shows, beside each entry of ``tests/quarantine.toml``, how often the test still
fails. This runs the listed tests ``ROUNDS`` times, each round one pytest process over every
listed test with a JUnit report of its own, and writes, per entry, how many rounds it passed,
failed, skipped or did not run in. A quarantined test is marked an expected failure at
collection, so its failure reports as one, and counts here as a failure. A round that ran
nothing (pytest refused its arguments, or the suite refused the list, which stops every
session) counts each test as not run, never as passed; so does every round past the time the
job allows. An empty quarantine runs nothing.

It runs the repository's own tests, so the job that runs it holds no write token; the report's
job reads only the JSON this writes. Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from . import report

#: How many times each quarantined test is run.
ROUNDS = 10
#: The time every round together may take, so the job ends inside its own limit.
BUDGET_SECONDS = 25 * 60
#: A round's JUnit report larger than this is not read.
MAX_JUNIT_BYTES = 8 * 1024 * 1024


def junit_case(nodeid: str) -> tuple[str, str]:
    """The (classname, name) pytest's JUnit report gives a test: its module's path dotted, with
    its classes, and its name with its case."""
    path, _, rest = nodeid.partition("::")
    parts = rest.split("::")
    module = path.removesuffix(".py").replace("/", ".")
    return ".".join([module, *parts[:-1]]), parts[-1]


def result_of(case: ET.Element) -> str:
    """``passed``, ``failed`` or ``skipped``: an expected failure, as a quarantined failure is
    reported, is a failure."""
    if case.find("failure") is not None or case.find("error") is not None:
        return "failed"
    skipped = case.find("skipped")
    if skipped is not None:
        return "failed" if skipped.get("type") == "pytest.xfail" else "skipped"
    return "passed"


def _results(xml: Path) -> dict[tuple[str, str], str] | None:
    """Each case's result in one round's report, or None when there is no report to read."""
    try:
        data = xml.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_JUNIT_BYTES or b"<!DOCTYPE" in data.upper():
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    return {
        (case.get("classname") or "", case.get("name") or ""): result_of(case)
        for case in root.iter("testcase")
    }


def _round(python: str, root: Path, nodeids: Sequence[str], xml: Path, seconds: float) -> bool:
    """Run one round; whether it ran to its end inside ``seconds``."""
    process = subprocess.Popen(
        [python, "-m", "pytest", "-p", "no:cacheprovider", "--junitxml", str(xml), *nodeids],
        cwd=root,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        process.wait(timeout=max(seconds, 0.1))
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return False
    return True


def rerun(
    nodeids: Sequence[str], *, rounds: int, python: str, root: Path, budget: float
) -> dict[str, dict[str, int]]:
    tally = {n: dict.fromkeys(report.RERUN_COUNTS, 0) for n in nodeids}
    if not nodeids:
        return tally
    deadline = time.monotonic() + budget
    with tempfile.TemporaryDirectory() as scratch:
        for number in range(1, rounds + 1):
            xml = Path(scratch) / f"round-{number}.xml"
            left = deadline - time.monotonic()
            finished = left > 0 and _round(python, root, nodeids, xml, left)
            results = _results(xml) if finished else None
            for nodeid in nodeids:
                result = (results or {}).get(junit_case(nodeid), "not_run")
                tally[nodeid][result] += 1
    return tally


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci quarantine-rerun")
    parser.add_argument("--quarantine", type=Path, default=report.QUARANTINE)
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="where pytest runs")
    parser.add_argument("--python", default=sys.executable, help="the interpreter pytest runs on")
    parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    try:
        entries = report.read_quarantine(args.quarantine)
    except report.SchemaError as err:
        print(f"::error title=the quarantine was not rerun::{err}")
        return 2
    nodeids = [str(e["nodeid"]) for e in entries]
    tally = rerun(
        nodeids,
        rounds=args.rounds,
        python=args.python,
        root=args.root,
        budget=args.budget_seconds,
    )
    doc = {"schema": report.RERUN_SCHEMA, "rounds": args.rounds, "tests": tally}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for nodeid, counts in tally.items():
        print(f"{nodeid}: failed {counts['failed']} of {args.rounds}, {counts['not_run']} not run")
    if not nodeids:
        print("the quarantine is empty: nothing to rerun")
    return 0


if __name__ == "__main__":
    sys.exit(main())
