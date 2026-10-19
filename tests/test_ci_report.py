"""``python -m tools.ci report``: the weekly CI health report from the telemetry history (#3948).

It reads the ``history-<run>-<attempt>.jsonl`` files ``tools/ci/telemetry.py`` writes, and only
those: every figure comes from one schema and one writer. Driven through ``report.main`` on
fixture histories written in that schema.
"""

from __future__ import annotations

import datetime
import json
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
