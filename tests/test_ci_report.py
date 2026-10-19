"""``python -m tools.ci report``: the weekly CI health report from the telemetry history (#3948).

It reads the ``history-<run>-<attempt>.jsonl`` files ``tools/ci/telemetry.py`` writes, and only
those: every figure comes from one schema and one writer. Driven through ``report.main`` on
fixture histories written in that schema.
"""

from __future__ import annotations

import datetime
import io
import json
import zipfile
from pathlib import Path

import pytest

from tools.ci import report, telemetry

DAY0 = datetime.datetime(2026, 9, 1, 9, 0, tzinfo=datetime.UTC)


def _write(
    folder: Path,
    run_id: int,
    attempt: int,
    day: int,
    tests: dict[str, tuple[str, float]],
    *,
    workflow: str = "ci.yml",
    extra: list[dict] | None = None,
) -> None:
    created = (DAY0 + datetime.timedelta(days=day)).isoformat().replace("+00:00", "Z")
    head = {f: None for f in telemetry.RUN_FIELDS}
    head.update(
        kind="run",
        schema=telemetry.SCHEMA,
        version=telemetry.VERSION,
        run_id=run_id,
        attempt=attempt,
        workflow=workflow,
        event="push",
        branch="main",
        created_at=created,
        jobs=[],
    )
    lines = [head]
    for test, (result, seconds) in tests.items():
        line = {f: None for f in telemetry.TEST_FIELDS}
        line.update(
            kind="test",
            run_id=run_id,
            attempt=attempt,
            workflow=workflow,
            report="junit-test-shard-1",
            test=test,
            seconds=seconds,
            first_result=result,
        )
        lines.append(line)
    lines += extra or []
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"history-{run_id}-{attempt}.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def _report(folder: Path, monkeypatch, tmp_path: Path, *more: str) -> tuple[int, dict, str]:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "report.json"
    code = report.main(["--history", str(folder), "--json", str(out), *more])
    data = json.loads(out.read_text("utf-8")) if out.exists() else {}
    return code, data, summary.read_text("utf-8") if summary.exists() else ""


def _rates(data: dict) -> dict[str, dict]:
    return {row["test"]: row for row in data["tests"]}


# ── flake rates come from first results ───────────────────────────────────────────────────────


def test_a_test_that_passed_on_rerun_three_times_in_ten_runs_flakes_thirty_percent(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    for i in range(10):
        flaky = i in (2, 5, 8)
        _write(folder, 100 + i, 1, i, {"t::flaky": ("failed" if flaky else "passed", 1.0)})
        if flaky:
            _write(folder, 100 + i, 2, i, {"t::flaky": ("passed", 1.0)})
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    row = _rates(data)["t::flaky"]
    assert row["runs"] == 10
    assert row["first_failures"] == 3 and row["passed_on_rerun"] == 3
    assert row["flake_rate"] == pytest.approx(0.30)


def test_a_failure_with_a_passing_rerun_is_a_failure_never_a_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """The first result is the attempt-1 record; a passing attempt 2 adds a passing rerun to it
    and never replaces it, whichever file the report reads first."""
    folder = tmp_path / "h"
    for i in range(10):
        _write(folder, 200 + i, 1, i, {"t::x": ("passed", 1.0)})
    _write(folder, 300, 2, 10, {"t::x": ("passed", 1.0)})  # written first on disk order...
    _write(folder, 300, 1, 10, {"t::x": ("failed", 1.0)})  # ...but attempt 1 is the first result
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    row = _rates(data)["t::x"]
    assert row["runs"] == 11 and row["first_failures"] == 1 and row["passed_on_rerun"] == 1
    assert row["first_passes"] == 10


@pytest.mark.parametrize(("word", "passed"), [("flake", 1), ("repeated failure", 0)])
def test_a_rerun_result_in_one_record_counts_as_a_passing_rerun_only_when_a_flake(
    tmp_path: Path, monkeypatch, word: str, passed: int
) -> None:
    """run-job records the diagnostic rerun on the same line, as ``rerun_result``, in the two
    words the collector lets through: ``flake`` is a passing rerun, ``repeated failure`` is not."""
    assert word in telemetry.RERUN_RESULTS
    folder = tmp_path / "h"
    for i in range(10):
        _write(folder, 400 + i, 1, i, {"t::y": ("passed", 1.0)})
    rerun = {f: None for f in telemetry.TEST_FIELDS}
    rerun.update(
        kind="test",
        run_id=410,
        attempt=1,
        workflow="ci.yml",
        test="t::y",
        seconds=1.0,
        first_result="failed",
        rerun_result=word,
        report="r",
    )
    _write(folder, 410, 1, 10, {}, extra=[rerun])
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    row = _rates(data)["t::y"]
    assert row["first_failures"] == 1 and row["passed_on_rerun"] == passed


def test_two_records_of_one_retried_attempt_are_counted_once(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "h"
    for i in range(10):
        _write(folder, 500 + i, 1, i, {"t::z": ("passed", 1.0)})
    dup = {f: None for f in telemetry.TEST_FIELDS}
    dup.update(
        kind="test",
        run_id=509,
        attempt=1,
        workflow="ci.yml",
        test="t::z",
        seconds=1.0,
        first_result="failed",
        report="junit-test-shard-2",
    )
    _write(folder, 509, 1, 9, {"t::z": ("failed", 1.0)}, extra=[dup])
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    assert _rates(data)["t::z"]["runs"] == 10


def test_a_test_with_fewer_than_ten_runs_is_not_rated(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "h"
    for i in range(9):
        _write(folder, 600 + i, 1, i, {"t::new": ("failed", 1.0)})
    _, data, text = _report(folder, monkeypatch, tmp_path)
    row = _rates(data)["t::new"]
    assert row["runs"] == 9 and row["flake_rate"] is None
    assert "fewer than 10 runs" in text


def test_skips_and_xfails_are_not_runs_of_the_test(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "h"
    for i in range(12):
        _write(folder, 700 + i, 1, i, {"t::s": ("skipped" if i % 2 else "passed", 1.0)})
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    assert _rates(data)["t::s"]["runs"] == 6


# ── durations ─────────────────────────────────────────────────────────────────────────────────


def test_the_slowest_tests_are_listed_slowest_first_with_p50_and_p99(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    for i in range(10):
        _write(
            folder,
            800 + i,
            1,
            i,
            {"t::a": ("passed", 5.0 + i), "t::b": ("passed", 50.0), "t::c": ("passed", 0.1)},
        )
    _, data, text = _report(folder, monkeypatch, tmp_path)
    assert [row["test"] for row in data["slowest"]] == ["t::b", "t::a", "t::c"]
    a = _rates(data)["t::a"]
    assert a["p50_seconds"] == pytest.approx(9.5) and a["p99_seconds"] == pytest.approx(13.91)
    assert data["suite_size"] == 3
    assert "t::b" in text.split("Slowest")[1]


def test_the_trend_compares_this_week_with_the_week_before(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "h"
    for day in range(14):
        _write(folder, 900 + day, 1, day, {"t::slow": ("passed", 10.0 if day < 7 else 15.0)})
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    assert _rates(data)["t::slow"]["trend"] == pytest.approx(0.5)


# ── the window ────────────────────────────────────────────────────────────────────────────────


def test_with_twelve_days_of_history_it_states_twelve_days_and_keeps_the_ninety_day_checks_off(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    for day in range(13):
        _write(folder, 1000 + day, 1, day, {"t::a": ("passed", 1.0)})
    _, data, text = _report(folder, monkeypatch, tmp_path)
    assert data["window"]["days"] == 12
    assert data["window"]["first"].startswith("2026-09-01") and data["window"]["last"].startswith(
        "2026-09-13"
    )
    assert data["window"]["mature"] is False
    assert "12 days of history" in text and "90-day" in text


def test_ninety_days_turn_the_maturity_checks_on_and_older_history_is_left_out(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    for day in (0, 5, 50, 100):
        _write(folder, 1100 + day, 1, day, {"t::a": ("passed", 1.0)})
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    assert data["window"]["mature"] is True
    assert data["window"]["days"] == 90 and data["window"]["runs"] == 2, "only day 50 and day 100"


# ── what it refuses ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "first_line",
    [
        {"kind": "run", "schema": "something-else", "version": 1},
        {"kind": "run", "schema": telemetry.SCHEMA, "version": telemetry.VERSION + 1},
        {"kind": "test"},
    ],
    ids=["another schema", "a newer version", "no run line first"],
)
def test_a_history_file_not_in_the_one_schema_fails_the_report(
    first_line: dict, tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    folder.mkdir()
    (folder / "history-1-1.jsonl").write_text(json.dumps(first_line) + "\n", encoding="utf-8")
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2


def test_an_empty_history_states_that_there_is_none(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "h").mkdir()
    code, data, text = _report(tmp_path / "h", monkeypatch, tmp_path)
    assert code == 0 and data["window"]["runs"] == 0 and "no history yet" in text


# ── scheduled slots ───────────────────────────────────────────────────────────────────────────


def _slots(folder: Path, at: str, *slots: tuple[str, str, str, int | None]) -> None:
    head = {"kind": "slots", "schema": telemetry.SCHEMA, "version": telemetry.VERSION, "at": at}
    lines = [head] + [
        {
            "kind": "slot",
            "workflow": w,
            "due_at": due,
            "status": status,
            "run_id": None,
            "lag_seconds": lag,
            "overdue_seconds": None,
        }
        for w, due, status, lag in slots
    ]
    folder.mkdir(parents=True, exist_ok=True)
    name = at.replace(":", "-")[:16]
    (folder / f"slots-{name}.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in lines), "utf-8"
    )


def test_a_missed_night_is_its_own_line_not_a_late_one(tmp_path: Path, monkeypatch) -> None:
    """A scheduled run that never started is not a late run: the report names the slot, and the
    newest slots file is the one read, so a slot later answered is not reported missed."""
    folder = tmp_path / "h"
    for i in range(3):
        _write(folder, 1200 + i, 1, i, {"t::a": ("passed", 1.0)})
    _slots(folder, "2026-10-17T13:00:00Z", ("nightly.yml", "2026-10-16T05:41:00Z", "pending", None))
    _slots(
        folder,
        "2026-10-19T13:00:00Z",
        ("breadth.yml", "2026-10-18T09:37:00Z", "missed", None),
        ("breadth.yml", "2026-10-19T09:37:00Z", "pending", None),
        ("nightly.yml", "2026-10-16T05:41:00Z", "ran", 435 * 60),
        ("nightly.yml", "2026-10-17T05:41:00Z", "ran", 239 * 60),
    )
    _, data, text = _report(folder, monkeypatch, tmp_path)
    by = {row["workflow"]: row for row in data["schedules"]}
    assert by["breadth.yml"]["missed"] == ["2026-10-18T09:37:00Z"]
    assert (
        by["breadth.yml"]["pending"] == ["2026-10-19T09:37:00Z"] and by["breadth.yml"]["ran"] == 0
    )
    assert by["nightly.yml"]["missed"] == [] and by["nightly.yml"]["ran"] == 2
    assert by["nightly.yml"]["median_lag_minutes"] == 337
    assert "breadth.yml missed its 2026-10-18T09:37:00Z run" in text


def test_a_slots_file_not_in_the_one_schema_fails_the_report(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "h"
    folder.mkdir()
    (folder / "slots-x.jsonl").write_text(
        json.dumps({"kind": "slots", "schema": "other"}) + "\n", "utf-8"
    )
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2


def test_a_history_folder_that_does_not_exist_fails_the_report(tmp_path: Path, monkeypatch) -> None:
    code, data, text = _report(tmp_path / "not-downloaded", monkeypatch, tmp_path)
    assert code == 2 and data == {} and text == ""


def test_a_bad_line_in_a_history_inside_the_window_fails_the_report(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    _write(folder, 1300, 1, 0, {"t::a": ("passed", 1.0)})
    with (folder / "history-1300-1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2


def test_a_history_outside_the_window_is_read_for_its_first_line_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """The report reads each file's run line to find the window, and the test lines only of the
    runs inside it, so a run past the 90 days costs one line, and its test lines are never
    parsed."""
    folder = tmp_path / "h"
    _write(folder, 1400, 1, 0, {"t::a": ("passed", 1.0)})
    with (folder / "history-1400-1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    _write(folder, 1401, 1, 95, {"t::a": ("passed", 2.0)})
    code, data, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 0
    assert data["window"]["runs"] == 1 and _rates(data)["t::a"]["p50_seconds"] == 2.0


def test_a_history_line_past_the_limit_fails_the_report(tmp_path: Path, monkeypatch) -> None:
    """A history is read one line at a time, each line within ``MAX_LINE_BYTES``, so no line
    the report reads can hold more than that in memory."""
    folder = tmp_path / "h"
    long = {f: None for f in telemetry.TEST_FIELDS}
    long.update(kind="test", run_id=1500, attempt=1, test="t::" + "x" * report.MAX_LINE_BYTES)
    long.update(first_result="passed", seconds=1.0)
    _write(folder, 1500, 1, 0, {}, extra=[long])
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2


# ── the quarantine's status ───────────────────────────────────────────────────────────────────


def _quarantine(path: Path, *entries: dict) -> Path:
    lines = []
    for entry in entries:
        lines.append("[[entry]]")
        for key, value in entry.items():
            text = value.isoformat() if isinstance(value, datetime.date) else json.dumps(value)
            lines.append(f"{key} = {text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _entry(nodeid: str, expires: datetime.date, **more: object) -> dict:
    entry = {
        "nodeid": nodeid,
        "issue": 4000,
        "assignee": "someone",
        "expires": expires,
        "reason": "fails one run in five on a slow runner",
    }
    entry.update(more)
    return entry


TODAY = datetime.date(2026, 10, 19)


def test_the_quarantine_shows_each_entrys_issue_assignee_and_expiry_and_flags_the_expired(
    tmp_path: Path, monkeypatch
) -> None:
    """On its date an entry has expired, as ``tests/quarantine.py`` reads it."""
    monkeypatch.setattr(report, "today", lambda: TODAY)
    listed = _quarantine(
        tmp_path / "quarantine.toml",
        _entry("tests/test_b.py::test_later", datetime.date(2026, 11, 1), issue=4002),
        _entry("tests/test_a.py::test_today", TODAY, issue=4001, assignee="other-one"),
    )
    (tmp_path / "h").mkdir()
    code, data, text = _report(tmp_path / "h", monkeypatch, tmp_path, "--quarantine", str(listed))
    assert code == 0
    assert data["quarantine"] == [
        {
            "nodeid": "tests/test_a.py::test_today",
            "issue": 4001,
            "assignee": "other-one",
            "expires": "2026-10-19",
            "days_left": 0,
            "expired": True,
            "reason": "fails one run in five on a slow runner",
        },
        {
            "nodeid": "tests/test_b.py::test_later",
            "issue": 4002,
            "assignee": "someone",
            "expires": "2026-11-01",
            "days_left": 13,
            "expired": False,
            "reason": "fails one run in five on a slow runner",
        },
    ]
    section = text.split("### Quarantine")[1].split("###")[0]
    assert "| `tests/test_a.py::test_today` | #4001 | @other-one | **expired 2026-10-19** |" in (
        section
    )
    assert "| `tests/test_b.py::test_later` | #4002 | @someone | 2026-11-01, 13 days left |" in (
        section
    )


@pytest.mark.parametrize("contents", [None, "# nothing listed\n"], ids=["missing", "empty"])
def test_an_empty_quarantine_says_so(tmp_path: Path, monkeypatch, contents: str | None) -> None:
    listed = tmp_path / "quarantine.toml"
    if contents is not None:
        listed.write_text(contents, encoding="utf-8")
    (tmp_path / "h").mkdir()
    code, data, text = _report(tmp_path / "h", monkeypatch, tmp_path, "--quarantine", str(listed))
    assert code == 0 and data["quarantine"] == []
    assert "The quarantine is empty." in text


@pytest.mark.parametrize(
    "entry",
    [
        {k: v for k, v in _entry("tests/test_a.py::t", TODAY).items() if k != "assignee"},
        _entry("tests/test_a.py::t", TODAY, owner="someone"),
        _entry("tests/test_a.py::t", TODAY, issue="4000"),
        _entry("tests/test_a.py::t", TODAY, issue=True),
        _entry("tests/test_a.py::t", datetime.datetime(2026, 10, 19, 9, 0)),
        _entry("tests/test_a.py::t", "2026-10-19"),
    ],
    ids=[
        "no-assignee",
        "an-unknown-field",
        "an-issue-as-text",
        "an-issue-as-a-bool",
        "a-time",
        "a-date-as-text",
    ],
)
def test_a_quarantine_entry_not_in_its_shape_fails_the_report(
    tmp_path: Path, monkeypatch, entry: dict
) -> None:
    """The status read from an entry the report does not understand would be a guess."""
    listed = _quarantine(tmp_path / "quarantine.toml", entry)
    (tmp_path / "h").mkdir()
    code, _, _ = _report(tmp_path / "h", monkeypatch, tmp_path, "--quarantine", str(listed))
    assert code == 2


def test_the_report_reads_the_quarantine_this_repository_keeps(tmp_path: Path, monkeypatch) -> None:
    import quarantine

    assert report.QUARANTINE_FIELDS == quarantine.FIELDS
    assert report.QUARANTINE == Path(__file__).resolve().parents[1] / quarantine.LIST
    (tmp_path / "h").mkdir()
    code, data, _ = _report(tmp_path / "h", monkeypatch, tmp_path)
    assert code == 0 and len(data["quarantine"]) == len(quarantine.read(Path.cwd())[0])


# ── at 90 days' scale: the same figures, read file by file ───────────────────────────────────


def _reference(folder: Path) -> dict:
    """The figures as the report computed them when it held every record in memory, kept here
    as the oracle the streaming reader must agree with, figure for figure."""
    import math
    import statistics

    def at(value: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))

    def percentile(values: list[float], q: float) -> float:
        ordered = sorted(values)
        k = (len(ordered) - 1) * q
        low = math.floor(k)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (k - low)

    runs: dict[int, list] = {}
    records = []
    for path in sorted(folder.rglob("history-*.jsonl")):
        lines = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
        head = lines[0]
        run_id, attempt, created = int(head["run_id"]), int(head["attempt"]), at(head["created_at"])
        known = runs.get(run_id)
        if known is None or created < known[0]:
            runs[run_id] = [created, known[1] if known else set()]
        records += [(run_id, attempt, line) for line in lines[1:]]
    outcomes: dict[tuple[int, str], list] = {}
    for run_id, attempt, record in sorted(records, key=lambda r: (r[0], r[1])):
        test, result = str(record["test"]), str(record["first_result"])
        runs[run_id][1].add(test)
        seconds = float(record.get("seconds") or 0.0)
        found = outcomes.get((run_id, test))
        if found is None:
            found = outcomes[(run_id, test)] = [attempt, result, seconds, runs[run_id][0], False]
        elif found[0] == attempt:
            if result in report.FAILED and found[1] not in report.FAILED:
                found[1], found[2] = result, seconds
            continue
        elif found[1] in report.FAILED and result == "passed":
            found[4] = True
            continue
        if found[1] in report.FAILED and record.get("rerun_result") == telemetry.FLAKE:
            found[4] = True
    newest = max(r[0] for r in runs.values())
    oldest = min(r[0] for r in runs.values())
    mature = (newest - oldest) >= datetime.timedelta(days=report.WINDOW_DAYS)
    kept = {i: r for i, r in runs.items() if r[0] >= newest - datetime.timedelta(days=90)}
    first = min(r[0] for r in kept.values())
    window = {
        "runs": len(kept),
        "days": report.WINDOW_DAYS if mature else (newest - first).days,
        "first": first.isoformat(),
        "last": newest.isoformat(),
        "mature": mature,
    }
    by_test: dict[str, list] = {}
    for (run_id, test), outcome in outcomes.items():
        if run_id in kept:
            by_test.setdefault(test, []).append(outcome)
    week, before = newest - datetime.timedelta(days=7), newest - datetime.timedelta(days=14)
    rows = []
    for test, seen in sorted(by_test.items()):
        ran = [o for o in seen if o[1] in report.RAN]
        seconds = [o[2] for o in ran]
        this_week = [o[2] for o in ran if o[3] > week]
        last_week = [o[2] for o in ran if before < o[3] <= week]
        trend = None
        if this_week and last_week and statistics.median(last_week) > 0:
            trend = statistics.median(this_week) / statistics.median(last_week) - 1
        reruns = sum(o[1] in report.FAILED and o[4] for o in ran)
        rows.append(
            {
                "test": test,
                "runs": len(ran),
                "first_passes": sum(o[1] == "passed" for o in ran),
                "first_failures": sum(o[1] in report.FAILED for o in ran),
                "passed_on_rerun": reruns,
                "flake_rate": reruns / len(ran) if len(ran) >= report.MIN_RUNS else None,
                "p50_seconds": percentile(seconds, 0.5) if seconds else None,
                "p99_seconds": percentile(seconds, 0.99) if seconds else None,
                "trend": trend,
            }
        )
    timed = [r for r in rows if r["p50_seconds"] is not None]
    slowest = sorted(timed, key=lambda r: (-r["p50_seconds"], r["test"]))[: report.SLOWEST]
    latest = max(kept.items(), key=lambda item: (item[1][0], item[0]))[1]
    return {"window": window, "tests": rows, "slowest": slowest, "suite_size": len(latest[1])}


#: Seconds as junit writes them, to the millisecond, a few repeating so medians fall on ties.
_SECONDS = (0.0, 0.001, 0.002, 0.25, 0.5, 1.0, 1.5, 2.125, 7.0, 31.416)
_RESULTS = ("passed",) * 6 + ("failed", "error", "skipped", "xfailed")


def _random_history(folder: Path, seed: int) -> None:
    """Runs over 120 days (some past the window), up to three attempts each, a run's attempt
    split across two folders now and then, records repeated within an attempt, reruns that
    passed or failed again, and ``rerun_result`` words on the failures."""
    import random

    rng = random.Random(seed)
    tests = [f"tests.test_{i % 4}::test_{i}" for i in range(14)]
    for n in range(rng.randint(12, 40)):
        run_id, day = 7000 + n, rng.randint(0, 120)
        for attempt in range(1, rng.randint(1, 3) + 1):
            extra = []
            for test in rng.sample(tests, rng.randint(1, len(tests))):
                for _ in range(rng.choice((1, 1, 1, 2))):
                    result = rng.choice(_RESULTS)
                    line = {f: None for f in telemetry.TEST_FIELDS}
                    line.update(
                        kind="test",
                        run_id=run_id,
                        attempt=attempt,
                        test=test,
                        seconds=rng.choice(_SECONDS),
                        first_result=result,
                        report=rng.choice(("junit-test-shard-1", "junit-test-macos-1")),
                    )
                    if result in report.FAILED and rng.random() < 0.4:
                        line["rerun_result"] = rng.choice(sorted(telemetry.RERUN_RESULTS))
                    extra.append(line)
            where = folder / rng.choice(("a", "b")) if rng.random() < 0.3 else folder
            later = rng.choice((0, 0, 0, 1))  # an attempt's file may say its run began later
            _write(where, run_id, attempt, day + later, {}, extra=extra)


@pytest.mark.parametrize("seed", range(25))
def test_the_streaming_reader_gives_the_figures_the_in_memory_one_did(
    tmp_path: Path, seed: int
) -> None:
    folder = tmp_path / "h"
    _random_history(folder, seed)
    data = report.figures(report.read(folder), [], [])
    expected = _reference(folder)
    assert {k: data[k] for k in expected} == expected


#: A real main history's 53,999 tests by how long each took, at 1 in 100: (seconds, tests).
_SPREAD = ((0.005, 1), (0.05, 388), (0.4, 138), (2.5, 10), (20.0, 3))
#: A real main run's tests, and the histories 90 days hold (docs/maintainers/ci.md).
MAIN_RUN_TESTS = 54_000
RUNS_IN_90_DAYS = 860


def _synthetic_window(folder: Path, runs: int) -> int:
    """``runs`` runs 2.5 hours apart, as 90 days of main runs come, of tests spread as a main
    run's are, each run's seconds its test's times a factor in [0.8, 1.25], to the millisecond.
    Returns how many tests a run holds."""
    import random

    folder.mkdir(parents=True)
    bases = [seconds for seconds, count in _SPREAD for _ in range(count)]
    for n in range(runs):
        rng = random.Random(n)
        created = (DAY0 + datetime.timedelta(hours=2.5 * n)).isoformat().replace("+00:00", "Z")
        head = {f: None for f in telemetry.RUN_FIELDS}
        head.update(kind="run", schema=telemetry.SCHEMA, version=telemetry.VERSION, run_id=n)
        head.update(attempt=1, created_at=created, jobs=[])
        with (folder / f"history-{n}-1.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(head) + "\n")
            for t, base in enumerate(bases):
                line = {f: None for f in telemetry.TEST_FIELDS}
                line.update(kind="test", run_id=n, attempt=1, report="junit-test-shard-1")
                line.update(test=f"tests.test_m{t % 50}::test_{t}", first_result="passed")
                line.update(seconds=round(base * rng.uniform(0.8, 1.25), 3))
                fh.write(json.dumps(line) + "\n")
    return len(bases)


def _peak(read, folder: Path) -> int:
    import gc
    import tracemalloc

    gc.collect()
    tracemalloc.start()
    try:
        read(folder)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _per_run(read, windows: dict[int, Path]) -> tuple[list[int], list[float]]:
    """The reader's peak on each window, and what each added run cost between two windows."""
    sizes = sorted(windows)
    peaks = [_peak(read, windows[runs]) for runs in sizes]
    steps = zip(peaks, peaks[1:], sizes, sizes[1:], strict=False)
    return peaks, [(b - a) / (m - n) for a, b, n, m in steps]


def test_what_each_added_run_costs_the_reader_falls_off_and_90_days_stay_under_512_mb(
    tmp_path: Path,
) -> None:
    """A test's seconds take as many values as it has distinct milliseconds, which stop growing
    once its runs have covered its spread, so each added run costs less than the one before it.
    The projection to 90 days is an upper bound: every run after the largest window is charged
    what the last runs cost, and the whole peak (the history's run list too) grows with the tests.
    The reading that held every record pays the same for every run, so this would catch it."""
    sizes = (16, 32, 64, 128, 256)
    windows = {runs: tmp_path / str(runs) for runs in sizes}
    tests = {_synthetic_window(folder, runs) for runs, folder in windows.items()}.pop()
    peaks, per_run = _per_run(report.read, windows)
    assert per_run[-1] < per_run[0] / 2, f"bytes per added run: {per_run}"
    projected = (peaks[-1] + per_run[-1] * (RUNS_IN_90_DAYS - sizes[-1])) * MAIN_RUN_TESTS / tests
    assert projected < 512 * 1024 * 1024, f"{projected / 2**20:.0f} MiB at 90 days"
    _, held = _per_run(_reference, {runs: windows[runs] for runs in sizes[:3]})
    assert held[-1] > 0.9 * held[0], f"the oracle's bytes per added run fell off: {held}"


# ── browse-live's environment quarantine ──────────────────────────────────────────────────────
# The row that replaced the browse-live quarantine-frequency tracker (a workflow and a tool of its
# own until this row), run on that tracker's own fixtures: each run's junit-browse-live report is
# the same ZIP, read by the collector's own parser into the history's lines.


def _browse_junit(message: str) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr(
            "junit-browse-live.xml",
            f"<testsuite><testcase><failure>{message}</failure></testcase></testsuite>",
        )
    return payload.getvalue()


def _browse_run(
    folder: Path,
    run_id: int,
    sha: str,
    updated: str,
    conclusion: str = "success",
    archive: bytes | None = None,
    *,
    attempt: int = 1,
    version: int = telemetry.VERSION,
) -> None:
    head = {f: None for f in telemetry.RUN_FIELDS}
    head.update(kind="run", schema=telemetry.SCHEMA, version=version, run_id=run_id)
    head.update(attempt=attempt, sha=sha, conclusion=conclusion, created_at=updated)
    head.update(updated_at=updated, workflow="ci.yml", event="push", branch="main", jobs=[])
    lines = [head]
    if archive is not None:
        cases, _, _ = telemetry.report_cases(report.BROWSE_LIVE_REPORT, archive)
        for test, seconds, first, _, rerun, environment in cases:
            line = {f: None for f in telemetry.TEST_FIELDS}
            line.update(kind="test", run_id=run_id, attempt=attempt, sha=sha, test=test)
            line.update(report=report.BROWSE_LIVE_REPORT, seconds=seconds, first_result=first)
            line.update(rerun_result=rerun, environment_failure=environment)
            if version == 1:
                del line["environment_failure"]
            lines.append(line)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"history-{run_id}-{attempt}.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def _row(folder: Path) -> dict:
    return report.figures(report.read(folder), [], [])["browse_live"]


def test_retries_are_collapsed_to_the_newest_measured_run_per_sha(tmp_path: Path) -> None:
    """The tracker's own case. Its head on another base is gone: every history is a push to
    main, so there are no pull-request heads to keep or leave out."""
    folder = tmp_path / "h"
    a, b, d, e, f = (c * 40 for c in "abdef")
    _browse_run(folder, 12, a, "2026-09-20T10:02:00Z", "cancelled")
    _browse_run(folder, 11, a, "2026-09-20T10:01:00Z", archive=_browse_junit("passed on retry"))
    stale = _browse_junit("ENVIRONMENT, NOT POLICY — stale failed retry")
    _browse_run(folder, 10, a, "2026-09-20T10:00:00Z", "failure", stale)
    unattached = _browse_junit("ENVIRONMENT, NOT POLICY — unattached")
    _browse_run(folder, 20, b, "2026-09-20T09:00:00Z", "failure", unattached)
    _browse_run(folder, 40, d, "2026-09-20T07:00:00Z", archive=_browse_junit("passed"))
    _browse_run(folder, 50, e, "2026-09-20T06:00:00Z", "failure", unattached)
    _browse_run(folder, 60, f, "2026-09-20T05:00:00Z", "failure", unattached)
    row = _row(folder)
    assert [o["run_id"] for o in row["observations"]] == [11, 20, 40, 50, 60]
    assert [o["quarantined"] for o in row["observations"]] == [False, True, False, True, True]
    assert row["quarantined"] == 3 and row["recurring"] is True
    assert (row["window"], row["threshold"]) == (5, 3)


@pytest.mark.parametrize(
    ("first", "quarantined", "recurring"),
    [(1, 3, False), (1, 4, True), (3, 2, False)],
    ids=["a-window-short-of-five", "three-of-five", "two-of-five"],
)
def test_the_alert_needs_a_full_window_and_three_distinct_quarantined_heads(
    tmp_path: Path, first: int, quarantined: int, recurring: bool
) -> None:
    folder = tmp_path / "h"
    marked = _browse_junit("ENVIRONMENT, NOT POLICY — unattached")
    heads = [False] * first + [True] * quarantined
    if (first, quarantined) == (3, 2):
        heads = [False, False, True, True, False]
    for i, marked_head in enumerate(heads):
        archive = marked if marked_head else _browse_junit("passed")
        _browse_run(
            folder, 100 + i, f"{i:040x}", f"2026-09-20T{10 - i:02d}:00:00Z", archive=archive
        )
    row = _row(folder)
    assert row["recurring"] is recurring
    assert row["measured"] == len(heads) and row["quarantined"] == sum(heads)


def test_a_newest_run_that_measured_no_browse_live_leaves_its_head_unobserved(
    tmp_path: Path,
) -> None:
    """As the tracker did: a head's newest run that is not cancelled is the one that counts,
    and an older run of that head never stands in for it."""
    folder = tmp_path / "h"
    sha = "a" * 40
    _browse_run(folder, 2, sha, "2026-09-20T10:00:00Z", "failure")
    marked = _browse_junit("ENVIRONMENT, NOT POLICY — unattached")
    _browse_run(folder, 1, sha, "2026-09-20T09:00:00Z", "failure", marked)
    assert _row(folder)["observations"] == []


def test_a_rerun_attempt_of_one_run_is_a_retry_of_its_head(tmp_path: Path) -> None:
    folder = tmp_path / "h"
    sha = "a" * 40
    marked = _browse_junit("ENVIRONMENT, NOT POLICY — unattached")
    _browse_run(folder, 7, sha, "2026-09-20T09:00:00Z", "failure", marked)
    clear = _browse_junit("passed")
    _browse_run(folder, 7, sha, "2026-09-20T09:30:00Z", "success", clear, attempt=2)
    [observation] = _row(folder)["observations"]
    assert (observation["attempt"], observation["quarantined"]) == (2, False)


def test_a_version_one_history_is_read_but_cannot_say_whether_browse_live_was_quarantined(
    tmp_path: Path, monkeypatch
) -> None:
    """A version-1 test line has no ``environment_failure``, so its head is no observation,
    never a clear one; every other figure reads it as before."""
    folder = tmp_path / "h"
    marked = _browse_junit("ENVIRONMENT, NOT POLICY — unattached")
    _browse_run(folder, 1, "a" * 40, "2026-09-20T09:00:00Z", "failure", marked, version=1)
    code, data, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 0 and data["window"]["runs"] == 1
    assert data["browse_live"]["observations"] == [] and data["tests"][0]["first_failures"] == 1


def test_the_row_is_shown_and_a_recurring_quarantine_is_an_error_annotation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    folder = tmp_path / "h"
    marked = _browse_junit("ENVIRONMENT, NOT POLICY — unattached")
    for i in range(5):
        archive = marked if i < 3 else _browse_junit("passed")
        _browse_run(
            folder, 200 + i, f"{i + 1}" * 40, f"2026-09-20T{10 - i:02d}:00:00Z", "failure", archive
        )
    code, _, summary = _report(folder, monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert code == 0
    section = summary.split("### Browse-live")[1].split("###")[0]
    assert "**3 of the last 5** measured heads on main carried `ENVIRONMENT, NOT POLICY`" in section
    assert "- `111111111111` run 200: quarantined" in section
    assert "::error title=browse-live environment quarantine is recurring::" in out
    assert "::error" not in summary


def test_a_short_window_says_there_is_no_rate_yet(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "h"
    _browse_run(folder, 1, "a" * 40, "2026-09-20T09:00:00Z", archive=_browse_junit("passed"))
    _, data, summary = _report(folder, monkeypatch, tmp_path)
    assert data["browse_live"]["recurring"] is False
    assert "1 measured head on main of the 5 a rate needs" in summary


def test_the_reports_are_read_within_the_trackers_size_limits() -> None:
    """The tracker never read an archive past 8 MiB, nor its XML past 8 MiB unpacked. The
    collector that writes the history holds the same two limits, and a report past them fails
    the collection, so no line of the row comes from one."""
    assert telemetry.MAX_ARCHIVE_BYTES == telemetry.MAX_JUNIT_BYTES == 8 * 1024 * 1024
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(
            "junit-browse-live.xml", "<testsuite>" + " " * (8 * 1024 * 1024) + "</testsuite>"
        )
    with pytest.raises(telemetry.CollectError, match="bytes"):
        telemetry.report_cases(report.BROWSE_LIVE_REPORT, payload.getvalue())
