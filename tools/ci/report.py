"""``python -m tools.ci report``: the weekly CI health report, read from the telemetry history.

It reads only what ``tools/ci/telemetry.py`` writes, in its one schema: the
``history-<run>-<attempt>.jsonl`` files and the ``slots-*.jsonl`` files. A file in another
schema or a newer version fails the report (exit 2), since a figure read from a record it does
not understand would be a guess. The same table prints in a terminal and goes to the job summary
(``$GITHUB_STEP_SUMMARY``), and ``--json`` writes the figures for the weekly issue and for the
tools that read them (#3926 the p99 durations, #3946 the reds).

**The window.** The 90 days up to the newest run in the history. While less than 90 days of
history exist, the report states the exact window it read (its first and last run, and how many
days that is), and the 90-day checks (#3946's replay among them) stay off: ``window.mature`` is
false until the history spans 90 days.

**First results.** A test's result in a run is its record in that run's lowest attempt: a later
attempt, or a ``rerun_result`` #3947's ``run-job`` writes on the same line, adds a passing rerun
to a first failure and never replaces it. Two records of one test in one attempt (a report read
twice, a retried upload) count once, the first failure if either failed. Skips and expected
failures are not runs of the test.

**Flake rate.** First failures that a rerun then passed, over the test's runs, rated only over
``MIN_RUNS`` runs or more.

**Durations.** Each test's p50 and p99 seconds over the window, the 20 slowest by p50, and the
trend: the p50 of the last 7 days over the p50 of the 7 days before, less one.

**Scheduled runs.** From the newest slots file: each scheduled workflow's slots that ran, with
their median lag, and the slots no run answered, as ``missed`` (its next slot passed with no run)
or ``pending`` (not yet). A missed night is its own line, never a late one.

Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import telemetry

WINDOW_DAYS = 90
MIN_RUNS = 10
SLOWEST = 20
TREND_DAYS = 7
#: The results that are a run of the test; a skip or an expected failure is not.
RAN = frozenset({"passed", "failed", "error"})
FAILED = frozenset({"failed", "error"})


class SchemaError(Exception):
    """A file in the history is not in the one schema this report reads."""


@dataclass
class _Outcome:
    """One test in one run: its first attempt's result, and whether a rerun passed."""

    attempt: int
    first: str
    seconds: float
    created: datetime.datetime
    passed_on_rerun: bool = False


@dataclass
class _Run:
    run_id: int
    created: datetime.datetime
    tests: set[str] = field(default_factory=set)


def _at(value: object) -> datetime.datetime:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"not a time: {value!r}")
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _lines(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError) as exc:
        raise SchemaError(f"{path.name}: {exc}") from None


def _head(path: Path, lines: Sequence[Mapping[str, Any]], kind: str) -> None:
    if not lines:
        raise SchemaError(f"{path.name} is empty")
    head = lines[0]
    if head.get("kind") != kind or head.get("schema") != telemetry.SCHEMA:
        raise SchemaError(f"{path.name}: its first line is not a {telemetry.SCHEMA} {kind} line")
    if head.get("version") != telemetry.VERSION:
        raise SchemaError(
            f"{path.name}: schema version {head.get('version')}, this report reads "
            f"{telemetry.VERSION}"
        )


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    low = math.floor(k)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


def read(folder: Path) -> tuple[dict[int, _Run], dict[tuple[int, str], _Outcome]]:
    """Every run and every test's outcome per run, from the history files under ``folder``."""
    runs: dict[int, _Run] = {}
    outcomes: dict[tuple[int, str], _Outcome] = {}
    records: list[tuple[int, int, Mapping[str, Any], datetime.datetime]] = []
    for path in sorted(folder.rglob("history-*.jsonl")):
        lines = _lines(path)
        _head(path, lines, "run")
        head = lines[0]
        run_id, attempt = int(head["run_id"]), int(head["attempt"])
        created = _at(head["created_at"])
        known = runs.get(run_id)
        if known is None or created < known.created:
            runs[run_id] = _Run(run_id, created, known.tests if known else set())
        for line in lines[1:]:
            if line.get("kind") != "test":
                raise SchemaError(f"{path.name}: a line after the first is not a test line")
            records.append((run_id, attempt, line, created))
    # Lowest attempt first, so each test's first result is that attempt's, whatever the file order.
    for run_id, attempt, record, created in sorted(records, key=lambda r: (r[0], r[1])):
        test, result = str(record["test"]), str(record["first_result"])
        runs[run_id].tests.add(test)
        key = (run_id, test)
        seconds = float(record.get("seconds") or 0.0)
        found = outcomes.get(key)
        if found is None:
            outcomes[key] = _Outcome(attempt, result, seconds, runs[run_id].created)
            found = outcomes[key]
        elif found.attempt == attempt:
            if result in FAILED and found.first not in FAILED:
                found.first, found.seconds = result, seconds
            continue
        elif found.first in FAILED and result == "passed":
            found.passed_on_rerun = True
            continue
        if found.first in FAILED and record.get("rerun_result") == "passed":
            found.passed_on_rerun = True
    return runs, outcomes


def read_slots(folder: Path) -> list[dict[str, Any]]:
    """The slot lines of the newest slots file, by the time it was written."""
    newest: tuple[datetime.datetime, list[dict[str, Any]]] | None = None
    for path in sorted(folder.rglob("slots-*.jsonl")):
        lines = _lines(path)
        _head(path, lines, "slots")
        at = _at(lines[0].get("at"))
        if newest is None or at > newest[0]:
            newest = (at, [x for x in lines[1:] if x.get("kind") == "slot"])
    return newest[1] if newest else []


def figures(
    runs: Mapping[int, _Run],
    outcomes: Mapping[tuple[int, str], _Outcome],
    slots: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    if not runs:
        window: dict[str, Any] = {"runs": 0, "days": 0, "first": None, "last": None}
        window["mature"] = False
        return {"window": window, "tests": [], "slowest": [], "suite_size": 0, "schedules": []}
    newest = max(r.created for r in runs.values())
    oldest = min(r.created for r in runs.values())
    mature = (newest - oldest) >= datetime.timedelta(days=WINDOW_DAYS)
    start = newest - datetime.timedelta(days=WINDOW_DAYS)
    kept = {i: r for i, r in runs.items() if r.created >= start}
    first = min(r.created for r in kept.values())
    window = {
        "runs": len(kept),
        "days": WINDOW_DAYS if mature else (newest - first).days,
        "first": first.isoformat(),
        "last": newest.isoformat(),
        "mature": mature,
    }
    by_test: dict[str, list[_Outcome]] = {}
    for (run_id, test), outcome in outcomes.items():
        if run_id in kept:
            by_test.setdefault(test, []).append(outcome)
    week = newest - datetime.timedelta(days=TREND_DAYS)
    before = newest - datetime.timedelta(days=2 * TREND_DAYS)
    rows: list[dict[str, Any]] = []
    for test, seen in sorted(by_test.items()):
        ran = [o for o in seen if o.first in RAN]
        seconds = [o.seconds for o in ran]
        this_week = [o.seconds for o in ran if o.created > week]
        last_week = [o.seconds for o in ran if before < o.created <= week]
        trend = None
        if this_week and last_week and statistics.median(last_week) > 0:
            trend = statistics.median(this_week) / statistics.median(last_week) - 1
        failures = sum(o.first in FAILED for o in ran)
        reruns = sum(o.first in FAILED and o.passed_on_rerun for o in ran)
        rows.append(
            {
                "test": test,
                "runs": len(ran),
                "first_passes": sum(o.first == "passed" for o in ran),
                "first_failures": failures,
                "passed_on_rerun": reruns,
                "flake_rate": reruns / len(ran) if len(ran) >= MIN_RUNS else None,
                "p50_seconds": _percentile(seconds, 0.5) if seconds else None,
                "p99_seconds": _percentile(seconds, 0.99) if seconds else None,
                "trend": trend,
            }
        )
    timed = [r for r in rows if r["p50_seconds"] is not None]
    slowest = sorted(timed, key=lambda r: (-r["p50_seconds"], r["test"]))[:SLOWEST]
    latest = max(kept.values(), key=lambda r: (r.created, r.run_id))
    return {
        "window": window,
        "tests": rows,
        "slowest": slowest,
        "suite_size": len(latest.tests),
        "schedules": schedules(slots),
    }


def schedules(slots: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for slot in slots:
        name = str(slot.get("workflow"))
        row = by.setdefault(
            name,
            {"workflow": name, "ran": 0, "lags": [], "missed": [], "pending": [], "disabled": []},
        )
        status, due = slot.get("status"), slot.get("due_at")
        if status == "ran":
            row["ran"] += 1
            if slot.get("lag_seconds") is not None:
                row["lags"].append(int(slot["lag_seconds"]))
        elif status in ("missed", "pending", "disabled"):
            row[str(status)].append(due)
    out = []
    for name, row in sorted(by.items()):
        lags = row.pop("lags")
        row["median_lag_minutes"] = round(statistics.median(lags) / 60) if lags else None
        out.append(row)
    return out


def markdown(data: Mapping[str, Any]) -> str:
    window = data["window"]
    lines = ["## Weekly CI health", ""]
    if not window["runs"]:
        lines.append("There is no history yet: no collected run of main.")
    elif window["mature"]:
        lines.append(
            f"The last {WINDOW_DAYS} days of history: {window['runs']} runs, from "
            f"{window['first']} to {window['last']}."
        )
    else:
        lines.append(
            f"{window['days']} days of history: {window['runs']} runs, from {window['first']} to "
            f"{window['last']}. The {WINDOW_DAYS}-day replay and maturity checks stay off until "
            f"{WINDOW_DAYS} days exist."
        )
    if window["runs"]:
        lines += ["", f"Suite size: {data['suite_size']} tests in the newest run."]
    rated = [r for r in data["tests"] if r["flake_rate"]]
    unrated = [r for r in data["tests"] if r["flake_rate"] is None and r["runs"]]
    lines += ["", "### Flaky tests", ""]
    if rated:
        lines += [
            "| Test | Runs | First failures | Passed on rerun | Flake rate |",
            "|---|---|---|---|---|",
        ]
        for r in sorted(rated, key=lambda r: (-r["flake_rate"], r["test"])):
            lines.append(
                f"| `{r['test']}` | {r['runs']} | {r['first_failures']} | "
                f"{r['passed_on_rerun']} | {r['flake_rate']:.0%} |"
            )
    else:
        lines.append("No rated test failed first and then passed on a rerun.")
    if unrated:
        lines.append(
            f"\n{len(unrated)} test(s) have fewer than {MIN_RUNS} runs in the window and are "
            "not rated."
        )
    lines += ["", "### Slowest tests", ""]
    if data["slowest"]:
        lines += ["| Test | p50 s | p99 s | Week over week |", "|---|---|---|---|"]
        for r in data["slowest"]:
            trend = "" if r["trend"] is None else f"{r['trend']:+.0%}"
            lines.append(
                f"| `{r['test']}` | {r['p50_seconds']:.1f} | {r['p99_seconds']:.1f} | {trend} |"
            )
    lines += ["", "### Scheduled runs", ""]
    if not data["schedules"]:
        lines.append("No slots file was read.")
    for row in data["schedules"]:
        lag = (
            ""
            if row["median_lag_minutes"] is None
            else f", median {row['median_lag_minutes']} min late"
        )
        lines.append(f"- {row['workflow']}: {row['ran']} ran{lag}")
        lines += [f"  - **{row['workflow']} missed its {due} run**" for due in row["missed"]]
        lines += [
            f"  - {row['workflow']}'s {due} run has not started yet" for due in row["pending"]
        ]
        lines += [f"  - {row['workflow']} is disabled: no {due} run" for due in row["disabled"]]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci report")
    parser.add_argument("--history", type=Path, required=True, help="a folder of telemetry files")
    parser.add_argument("--json", type=Path, default=None, help="write the figures here too")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.history.is_dir():
        print(f"::error title=the report did not run::{args.history} is not a folder")
        return 2
    try:
        runs, outcomes = read(args.history)
        data = figures(runs, outcomes, read_slots(args.history))
    except (SchemaError, KeyError, TypeError, ValueError) as err:
        print(f"::error title=the report did not run::{err}")
        return 2
    text = markdown(data)
    print(text, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
