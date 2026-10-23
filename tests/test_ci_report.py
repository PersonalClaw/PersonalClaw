"""``python -m tools.ci report``: the weekly CI health report from the telemetry history (#3948).

It reads the ``history-<run>-<attempt>.jsonl`` files ``tools/ci/telemetry.py`` writes, and only
those: every figure comes from one schema and one writer. Driven through ``report.main`` on
fixture histories written in that schema.
"""

from __future__ import annotations

import datetime
import io
import json
import warnings
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
    records: list[object] | None = None,
    version: int = telemetry.VERSION,
) -> None:
    created = (DAY0 + datetime.timedelta(days=day)).isoformat().replace("+00:00", "Z")
    head = {f: None for f in telemetry.RUN_FIELDS}
    head.update(
        kind="run",
        schema=telemetry.SCHEMA,
        version=version,
        run_id=run_id,
        attempt=attempt,
        workflow=workflow,
        event="push",
        branch="main",
        created_at=created,
        jobs=[],
    )
    lines: list[object] = [head]
    # Each job record is a line of its own after the run line, as the collector writes it.
    lines += [
        dict(entry, kind="record") if isinstance(entry, dict) else entry for entry in records or []
    ]
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


# ── what the diagnostic reruns found, from each job's record (schema version 4) ──────────────

FLAKY = "tests/test_a.py::test_flaky"
TWICE = "tests/test_a.py::test_twice"
WEB = "e2e/chat.spec.ts > chat > sends"


def _job(key: str, *rerun: tuple[str, str | None], quarantined: tuple[str, ...] = ()) -> dict:
    """One job record as the collector writes it on its own line, ``kind`` aside."""
    return {
        "key": key,
        "result": "failure" if rerun else "success",
        "rerun": [{"test": t, "rerun_result": w} for t, w in rerun],
        "quarantined": list(quarantined),
    }


def _diagnosed(data: dict) -> dict[str, tuple[int, int, list[str]]]:
    return {r["test"]: (r["flakes"], r["repeated_failures"], r["jobs"]) for r in data["diagnosed"]}


def test_each_test_the_reruns_classified_is_counted_from_the_job_records(
    tmp_path: Path, monkeypatch
) -> None:
    """Every runner's classification is in the records, Playwright's too, which no junit report
    carries: each job record that found a test a flake or a repeated failure counts once."""
    folder = tmp_path / "h"
    suite = ("suite (1/2)", (FLAKY, "flake"), (TWICE, "repeated failure"))
    _write(folder, 1, 1, 0, {}, records=[_job(*suite), _job("e2e", (WEB, "flake"))])
    _write(folder, 2, 1, 1, {}, records=[_job("suite (2/2)", (FLAKY, "flake"), (TWICE, None))])
    _write(folder, 3, 1, 2, {}, records=[_job("macos (1/2)", (FLAKY, "repeated failure"))])
    _write(folder, 4, 1, 3, {}, records=[_job("static", quarantined=(FLAKY,))])
    code, data, summary = _report(folder, monkeypatch, tmp_path)
    assert code == 0
    assert _diagnosed(data) == {
        FLAKY: (2, 1, ["macos (1/2)", "suite (1/2)", "suite (2/2)"]),
        WEB: (1, 0, ["e2e"]),
        TWICE: (0, 1, ["suite (1/2)"]),
    }
    # Most flakes first.
    assert [r["test"] for r in data["diagnosed"]] == [FLAKY, WEB, TWICE]
    assert data["record"]["runs_with_records"] == 4
    assert "### What the reruns found" in summary
    assert f"| `{FLAKY}` | 2 | 1 | `macos (1/2)`, `suite (1/2)`, `suite (2/2)` |" in summary


def test_of_two_tests_as_often_a_flake_the_one_more_often_a_repeated_failure_comes_first(
    tmp_path: Path, monkeypatch
) -> None:
    """By name the first would come first; by repeated failures, the tie-break, it comes last."""
    folder = tmp_path / "h"
    fewer, more = "tests/test_a.py::test_a_few", "tests/test_a.py::test_b_more"
    _write(folder, 1, 1, 0, {}, records=[_job("suite", (fewer, "flake"), (more, "flake"))])
    _write(folder, 2, 1, 1, {}, records=[_job("suite", (more, "repeated failure"))])
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    assert _diagnosed(data) == {fewer: (1, 0, ["suite"]), more: (1, 1, ["suite"])}
    assert [r["test"] for r in data["diagnosed"]] == [more, fewer]


def test_only_each_runs_first_attempt_counts_as_the_flake_rates_first_results_do(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    _write(folder, 1, 2, 0, {}, records=[_job("suite", (FLAKY, "flake"))])
    _write(folder, 1, 1, 0, {}, records=[_job("suite", (TWICE, "repeated failure"))])
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    assert _diagnosed(data) == {TWICE: (0, 1, ["suite"])}


def test_a_history_that_carries_no_records_says_the_reruns_found_nothing_in_it(
    tmp_path: Path, monkeypatch
) -> None:
    """A version-3 history predates the records, and a version-4 one with no record line is a
    run that uploaded none: the issue says how many runs carried any."""
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)}, version=3)
    _write(folder, 2, 1, 1, {"t::a": ("passed", 1.0)})
    _write(folder, 3, 1, 2, {"t::a": ("passed", 1.0)}, records=[_job("static")])
    code, data, summary = _report(folder, monkeypatch, tmp_path)
    assert code == 0 and data["diagnosed"] == []
    assert data["record"]["runs_with_records"] == 1
    assert "1 of the window's 3 runs carried its jobs' records" in summary
    assert "No job record in the window classified a failed test." in summary


def test_a_record_outside_the_window_is_not_counted(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {}, records=[_job("suite", (FLAKY, "flake"))])
    _write(folder, 2, 1, report.WINDOW_DAYS + 5, {}, records=[])
    _, data, _ = _report(folder, monkeypatch, tmp_path)
    assert data["diagnosed"] == []


def _rerun(*tests: object) -> dict:
    return dict(_job("suite"), result="failure", rerun=list(tests))


@pytest.mark.parametrize(
    ("records", "why"),
    [
        pytest.param(["suite"], "a line is not a JSON object", id="a-line-not-an-object"),
        pytest.param([dict(_job("suite"), extra=1)], "does not hold", id="an-unknown-field"),
        pytest.param(
            [{"key": "suite", "result": "failure", "rerun": []}],
            "does not hold",
            id="a-missing-field",
        ),
        pytest.param([dict(_job("suite"), key="")], "names no key", id="no-key"),
        pytest.param([dict(_job("suite"), key=["suite"])], "names no key", id="a-key-not-text"),
        pytest.param([dict(_job("suite"), result="passed")], "is no job's", id="no-jobs-result"),
        pytest.param(
            [dict(_job("suite"), result=["failure"])], "is no job's", id="a-result-that-is-a-list"
        ),
        pytest.param(
            [dict(_job("suite"), result={"failure": 1})],
            "is no job's",
            id="a-result-that-is-an-object",
        ),
        pytest.param([dict(_job("suite"), rerun="flake")], "not in their shape", id="rerun-text"),
        pytest.param(
            [_job("suite", (FLAKY, "passed"))], "not in their shape", id="a-word-no-rerun-gives"
        ),
        pytest.param(
            [_rerun({"test": FLAKY, "rerun_result": ["flake"]})],
            "not in their shape",
            id="a-rerun-result-that-is-a-list",
        ),
        pytest.param(
            [_rerun({"test": FLAKY, "rerun_result": {"flake": 1}})],
            "not in their shape",
            id="a-rerun-result-that-is-an-object",
        ),
        pytest.param([_job("suite", ("", "flake"))], "not in their shape", id="no-test"),
        pytest.param(
            [_rerun({"test": FLAKY, "rerun_result": "flake", "x": 1})],
            "not in their shape",
            id="a-test-of-another-shape",
        ),
        pytest.param(
            [_job("suite", (FLAKY, "flake"), (FLAKY, "repeated failure"))],
            f"names {FLAKY} twice",
            id="one-test-twice",
        ),
        pytest.param(
            [dict(_job("suite"), quarantined=[1])], "not test ids", id="quarantined-not-test-ids"
        ),
        pytest.param(
            [_job("suite", (FLAKY, "flake")), _job("suite")], "a key twice", id="one-key-twice"
        ),
    ],
)
def test_records_not_in_their_shape_fail_the_report(
    records: list[object], why: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    """Each by name, in the file it is in: the collector writes none of these, and a figure read
    from one would be a guess."""
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {}, records=records)
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2
    said = capsys.readouterr().out
    assert "history-1-1.jsonl" in said and why in said, said


def test_a_record_line_in_a_history_of_an_older_version_fails_the_report(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Version 4 is the first to write a record line: in an older history one is no line the
    collector wrote."""
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {}, records=[_job("suite", (FLAKY, "flake"))], version=3)
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2
    assert "history-1-1.jsonl: a line after the first is not a test line" in capsys.readouterr().out


def test_a_later_attempts_records_not_in_their_shape_fail_the_report_too(
    tmp_path: Path, monkeypatch
) -> None:
    """Only the first attempt's records count, and every attempt's are read: a figure beside one
    that cannot be read would be a guess."""
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {}, records=[_job("suite", (FLAKY, "flake"))])
    _write(folder, 1, 2, 0, {}, records=[dict(_job("suite"), result="passed")])
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2


def test_the_issue_shows_the_first_twenty_tests_and_report_json_holds_every_one(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    found = [(f"tests/test_a.py::test_{n:02}", "flake") for n in range(report.DIAGNOSED_SHOWN + 1)]
    _write(folder, 1, 1, 0, {}, records=[_job("suite", *found)])
    code, data, summary = _report(folder, monkeypatch, tmp_path)
    assert code == 0 and len(data["diagnosed"]) == 21 == report.DIAGNOSED_SHOWN + 1
    rows = [line for line in summary.splitlines() if line.startswith("| `tests/test_a.py::")]
    assert len(rows) == 20
    assert "1 more in report.json." in summary


def test_the_report_and_the_collector_read_one_record_shape() -> None:
    from tools.ci import verdict

    text = verdict.job_record(
        key="suite",
        result=verdict.FAILURE,
        identity=verdict.Identity("a" * 40),
        steps=[(["pytest"], 1, 1.0)],
        run_id=7,
        rerun={
            "argv": ["pytest"],
            "exit": 1,
            "tests": [{"test": FLAKY, "rerun_result": "flake", "why": "w"}],
        },
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("suite.json", text)
    name = verdict.record_artifact("suite", run_id=7, attempt=1)
    line = telemetry.record_line(name, buffer.getvalue(), 7, 1)
    assert line["kind"] == "record" and set(line) == set(report.RECORD_FIELDS)
    assert report.job_records([line], "h") == [line]


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
            "reruns": None,
        },
        {
            "nodeid": "tests/test_b.py::test_later",
            "issue": 4002,
            "assignee": "someone",
            "expires": "2026-11-01",
            "days_left": 13,
            "expired": False,
            "reason": "fails one run in five on a slow runner",
            "reruns": None,
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


#: A real main history's 53,999 tests by how long each took, at 1 in 200, a bucket's share rounded
#: up to a whole test: (seconds, tests). The projection scales a run's whole cost by
#: ``MAIN_RUN_TESTS / tests``, so the smaller sample charges a run's fixed cost more, never less
#: (276 MiB at 1 in 100, 361 at 1 in 200). At 1 in 100 the reads under tracemalloc took 144 s on a
#: hosted Mac, past the per-test timeout (#3905).
_SPREAD = ((0.005, 1), (0.05, 194), (0.4, 69), (2.5, 5), (20.0, 2))
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


# ── the fetched archives, read in place ───────────────────────────────────────────────────────


def _archive(folder: Path, name: str, members: dict[str, bytes]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        for member, data in members.items():
            zipped.writestr(member, data)
    return path


def test_histories_and_slots_are_read_inside_the_archives_the_fetch_kept(
    tmp_path: Path, monkeypatch
) -> None:
    """The fetch keeps each collector archive as it was downloaded; the report reads the
    histories and the slots inside them line by line, and never unpacks one to disk."""
    plain = tmp_path / "plain"
    for i in range(10):
        _write(plain, 100 + i, 1, i, {"t::a": ("failed" if i == 3 else "passed", 1.0 + i)})
    _slots(plain, "2026-10-19T13:00:00Z", ("breadth.yml", "2026-10-18T09:37:00Z", "missed", None))
    zipped = tmp_path / "zipped"
    for path in sorted(plain.iterdir()):
        stem = path.name.removesuffix(".jsonl")
        _archive(zipped, f"telemetry-{stem}", {path.name: path.read_bytes()})
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, expected, _ = _report(plain, monkeypatch, tmp_path / "a")
    code, data, _ = _report(zipped, monkeypatch, tmp_path / "b")
    assert code == 0 and data == expected
    assert data["window"]["runs"] == 10 and data["schedules"][0]["missed"]
    assert sorted(p.name for p in zipped.iterdir()) == sorted(
        f"telemetry-{p.name.removesuffix('.jsonl')}.zip" for p in plain.iterdir()
    ), "the report wrote into the folder"


@pytest.mark.parametrize(
    "members",
    [
        {"../history-1-1.jsonl": b"x"},
        {"nested/history-1-1.jsonl": b"x"},
        {"history-1-1.jsonl.exe": b"x"},
        {"README": b"x"},
    ],
    ids=["a-parent-path", "a-folder", "another-suffix", "another-file"],
)
def test_an_archive_member_not_named_as_the_collector_names_its_files_is_not_read(
    tmp_path: Path, monkeypatch, members: dict
) -> None:
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)})
    _archive(folder, "telemetry-history-2-1", members)
    code, data, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 0 and data["window"]["runs"] == 1


@pytest.mark.parametrize(
    "data", [b"not a zip", b"PK\x03\x04broken"], ids=["not-a-zip", "a-broken-zip"]
)
def test_an_archive_that_cannot_be_read_fails_the_report(
    tmp_path: Path, monkeypatch, data: bytes
) -> None:
    folder = tmp_path / "h"
    folder.mkdir()
    (folder / "telemetry-history-1-1.zip").write_bytes(data)
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2


def test_an_archived_history_past_its_bound_fails_the_report(tmp_path: Path, monkeypatch) -> None:
    """A history is bounded as it is unpacked in memory, so a small archive that inflates past
    ``MAX_FILE_BYTES`` fails the report instead of filling the runner."""
    monkeypatch.setattr(report, "MAX_FILE_BYTES", 4096)
    plain = tmp_path / "plain"
    _write(plain, 1, 1, 0, {f"t::{i}": ("passed", 1.0) for i in range(100)})
    folder = tmp_path / "h"
    _archive(
        folder,
        "telemetry-history-1-1",
        {"history-1-1.jsonl": (plain / "history-1-1.jsonl").read_bytes()},
    )
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2


# ── the quarantined tests' reruns ─────────────────────────────────────────────────────────────


def _reruns(path: Path, entries: dict[str, dict[str, int]], rounds: int = 10) -> Path:
    doc = {"schema": report.RERUN_SCHEMA, "rounds": rounds, "tests": entries}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_each_quarantined_tests_reruns_are_shown_beside_its_entry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(report, "today", lambda: TODAY)
    listed = _quarantine(
        tmp_path / "quarantine.toml",
        _entry("tests/test_a.py::test_one", datetime.date(2026, 11, 1)),
        _entry("tests/test_b.py::test_two", datetime.date(2026, 11, 2)),
    )
    reruns = _reruns(
        tmp_path / "reruns.json",
        {
            "tests/test_a.py::test_one": {"passed": 7, "failed": 3, "skipped": 0, "not_run": 0},
            "tests/test_b.py::test_two": {"passed": 0, "failed": 0, "skipped": 0, "not_run": 10},
        },
    )
    (tmp_path / "h").mkdir()
    code, data, text = _report(
        tmp_path / "h", monkeypatch, tmp_path, "--quarantine", str(listed), "--reruns", str(reruns)
    )
    assert code == 0
    by = {q["nodeid"]: q for q in data["quarantine"]}
    assert by["tests/test_a.py::test_one"]["reruns"] == {
        "passed": 7,
        "failed": 3,
        "skipped": 0,
        "not_run": 0,
    }
    section = text.split("### Quarantine")[1].split("###")[0]
    assert (
        "| `tests/test_a.py::test_one` | #4000 | @someone | 2026-11-01, 13 days left "
        "| failed 3 of 10 |" in section
    )
    assert (
        "| `tests/test_b.py::test_two` | #4000 | @someone | 2026-11-02, 14 days left | not run |"
        in section
    )


def test_without_the_reruns_the_quarantine_says_they_did_not_run(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(report, "today", lambda: TODAY)
    listed = _quarantine(
        tmp_path / "quarantine.toml", _entry("tests/test_a.py::t", datetime.date(2026, 11, 1))
    )
    (tmp_path / "h").mkdir()
    _, data, text = _report(tmp_path / "h", monkeypatch, tmp_path, "--quarantine", str(listed))
    assert data["quarantine"][0]["reruns"] is None
    assert "| no reruns |" in text


def _counts(**changes: int) -> dict[str, int]:
    return {"passed": 10, "failed": 0, "skipped": 0, "not_run": 0, **changes}


@pytest.mark.parametrize(
    "doc",
    [
        pytest.param({"schema": "another", "rounds": 10, "tests": {}}, id="another-schema"),
        pytest.param({"rounds": 10, "tests": {}}, id="no-schema"),
        pytest.param(
            {"schema": report.RERUN_SCHEMA, "rounds": "10", "tests": {}}, id="rounds-text"
        ),
        pytest.param({"schema": report.RERUN_SCHEMA, "rounds": 0, "tests": {}}, id="no-rounds"),
        pytest.param(
            {"schema": report.RERUN_SCHEMA, "rounds": 10, "tests": {"t": {"passed": 10}}},
            id="a-count-missing",
        ),
        pytest.param(
            # They add up to the rounds: only the count's sign is wrong.
            {
                "schema": report.RERUN_SCHEMA,
                "rounds": 10,
                "tests": {"t": _counts(passed=11, failed=-1)},
            },
            id="a-negative-count",
        ),
        pytest.param(
            {"schema": report.RERUN_SCHEMA, "rounds": 10, "tests": {"t": _counts(failed=1)}},
            id="counts-past-the-rounds",
        ),
        pytest.param(
            {"schema": report.RERUN_SCHEMA, "rounds": 10, "tests": {"t": _counts(passed=9)}},
            id="counts-short-of-the-rounds",
        ),
        pytest.param({"schema": report.RERUN_SCHEMA, "rounds": 10, "tests": []}, id="tests-list"),
        pytest.param([], id="a-list"),
        pytest.param("reruns", id="a-string"),
    ],
)
def test_reruns_not_in_their_shape_fail_the_report(
    tmp_path: Path, monkeypatch, capsys, doc: object
) -> None:
    path = tmp_path / "reruns.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "h").mkdir()
    code, _, _ = _report(tmp_path / "h", monkeypatch, tmp_path, "--reruns", str(path))
    assert code == 2
    assert "::error title=the report did not run::reruns.json" in capsys.readouterr().out


def test_reruns_that_add_up_to_their_rounds_are_read(tmp_path: Path) -> None:
    counts = {"passed": 6, "failed": 2, "skipped": 1, "not_run": 1}
    path = _reruns(tmp_path / "reruns.json", {"t": counts})
    assert report.read_reruns(path) == {"t": counts}


# ── the one weekly issue ──────────────────────────────────────────────────────────────────────


class _Issues:
    """``gh`` as the report calls it for the weekly issue, with the issues it holds."""

    def __init__(self, issues: list[dict] | None = None) -> None:
        self.issues = list(issues or [])
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:1] == ("api",):
            path = args[-1]
            assert "/issues?" in path and "state=open" in path, path
            page = int(path.split("page=")[-1])
            return json.dumps(self.issues[(page - 1) * 100 : page * 100])
        if args[:2] == ("issue", "create"):
            title = args[args.index("--title") + 1]
            body = Path(args[args.index("--body-file") + 1]).read_text("utf-8")
            number = 100 + len(self.issues)
            self.issues.append(_issue(number, title, body=body))
            return f"https://github.com/o/r/issues/{number}\n"
        if args[:2] == ("issue", "edit"):
            number = int(args[2])
            body = Path(args[args.index("--body-file") + 1]).read_text("utf-8")
            next(i for i in self.issues if i["number"] == number)["body"] = body
            return ""
        raise AssertionError(f"the report asked gh for {args}")


def _issue(
    number: int,
    title: str,
    *,
    login: str = report.BOT,
    body: str = "",
    pr: bool = False,
    kind: str | None = None,
):
    issue = {
        "number": number,
        "title": title,
        "body": body,
        "user": {"login": login, "type": kind or ("Bot" if login == report.BOT else "User")},
    }
    if pr:
        issue["pull_request"] = {"url": "x"}
    return issue


def _publish(gh: _Issues, body: str = "## Weekly CI health\n") -> int:
    return report.publish("o/r", body, run_url="https://example.invalid/run/1", gh=gh)


def test_two_reports_edit_the_one_issue_and_never_create_a_second() -> None:
    gh = _Issues()
    first = _publish(gh, "first\n")
    second = _publish(gh, "second\n")
    assert first == second
    assert [c[:2] for c in gh.calls if c[0] == "issue"] == [("issue", "create"), ("issue", "edit")]
    [only] = [i for i in gh.issues if i["title"] == report.ISSUE_TITLE]
    assert only["body"].startswith("second\n") and "https://example.invalid/run/1" in only["body"]


@pytest.mark.parametrize(
    "other",
    [
        _issue(7, report.ISSUE_TITLE, login="someone"),
        _issue(8, report.ISSUE_TITLE, pr=True),
        _issue(9, "Weekly CI health (old)"),
        _issue(10, report.ISSUE_TITLE, login="another-app[bot]", kind="Bot"),
        _issue(11, report.ISSUE_TITLE, login=report.BOT, kind="User"),
    ],
    ids=["a-persons-issue", "a-pull-request", "another-title", "another-bots", "not-a-bot"],
)
def test_only_the_bots_open_issue_of_that_exact_title_is_edited(other: dict) -> None:
    """On a public repository anyone may open an issue of that title; only the workflow's own
    is the report's."""
    gh = _Issues([other])
    number = _publish(gh)
    assert number != other["number"] and ("issue", "create") in [c[:2] for c in gh.calls]


def test_two_bot_issues_of_that_title_edit_the_older_and_name_the_other(capsys) -> None:
    gh = _Issues([_issue(12, report.ISSUE_TITLE), _issue(5, report.ISSUE_TITLE)])
    assert _publish(gh) == 5
    assert "::warning title=more than one weekly CI health issue::" in capsys.readouterr().out


def test_past_the_open_issues_it_reads_the_report_writes_no_issue() -> None:
    """Past 1,000 open issues the weekly one may be among those unread: a second would open."""
    gh = _Issues([_issue(n, f"another {n}") for n in range(1, report.ISSUE_PAGES * 100 + 1)])
    with pytest.raises(ValueError, match="more than 1000 open issues"):
        _publish(gh)
    assert [c for c in gh.calls if c[0] == "issue"] == []


def test_a_body_past_githubs_limit_is_cut_and_says_where_the_rest_is() -> None:
    gh = _Issues()
    _publish(gh, "x" * (report.ISSUE_BODY_LIMIT * 2))
    [only] = gh.issues
    assert len(only["body"]) <= report.ISSUE_BODY_LIMIT
    assert "cut here" in only["body"] and "https://example.invalid/run/1" in only["body"]


# ── what the window holds ─────────────────────────────────────────────────────────────────────


def _status(path: Path, **changes: object) -> Path:
    status = {
        "carry": "kept: the window weekly run 77 carried",
        "carried": 9,
        "dropped": 0,
        "fetched": 3,
        "refused": 0,
        "left": 0,
        "reserve": 200,
        "slots": "telemetry-slots-25.zip",
        "refusals": [],
    }
    status.update(changes)
    path.write_text(json.dumps(status), encoding="utf-8")
    return path


def test_the_report_says_how_much_of_the_90_days_the_window_holds(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    for day in range(13):
        _write(folder, 2000 + day, 1, day, {"t::a": ("passed", 1.0)})
    status = _status(tmp_path / "status.json")
    code, data, text = _report(folder, monkeypatch, tmp_path, "--fetch-status", str(status))
    assert code == 0
    assert data["record"]["days"] == 12 and data["record"]["of"] == 90
    section = text.split("### The record")[1].split("###")[0]
    assert "The window holds 12 of the 90 days, 13 runs." in section
    assert "9 histories carried from the last weekly run, 3 fetched" in section


def test_a_refused_carry_and_a_spent_budget_are_said_plainly(tmp_path: Path, monkeypatch) -> None:
    """A missed week, or a carried window this workflow did not upload, refills from the record
    within the budget; the issue says so, and what is left for the next run."""
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)})
    status = _status(
        tmp_path / "status.json",
        carry="refused: artifact 5000 was uploaded by run 9, which is not this workflow's own",
        carried=0,
        dropped=2,
        fetched=40,
        left=12,
        refusals=["carried: telemetry-history-1-1.zip left the window"],
    )
    _, data, text = _report(folder, monkeypatch, tmp_path, "--fetch-status", str(status))
    section = text.split("### The record")[1].split("###")[0]
    assert "**The carried window was refused**: artifact 5000 was uploaded by run 9" in section
    assert "2 carried histories were dropped when checked again" in section
    assert (
        "**12 histories were left for the next run**, to keep 200 of the token's calls for the "
        "other workflows." in section
    )
    assert data["record"]["left"] == 12


def test_each_refusal_is_one_code_span_whatever_it_quotes(tmp_path: Path, monkeypatch) -> None:
    """A refusal can quote an archive's names; a backtick in one never ends its code span."""
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)})
    why = "carried: 'a`b **c** ``d' is not a collector archive"
    status = _status(tmp_path / "status.json", refusals=[why])
    _, _, text = _report(folder, monkeypatch, tmp_path, "--fetch-status", str(status))
    [line] = [x for x in text.split("### The record")[1].splitlines() if "collector archive" in x]
    assert line.startswith("- `") and line.endswith("`") and line.count("`") == 2, line


def test_without_the_fetchs_status_the_record_is_the_window_alone(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)})
    _, data, text = _report(folder, monkeypatch, tmp_path)
    assert data["record"]["fetch"] is None
    assert "The window holds 0 of the 90 days, 1 run." in text.split("### The record")[1]


@pytest.mark.parametrize(
    "status",
    [{"carry": 7}, {"left": -1}, {"refusals": "x"}, "not a table"],
    ids=["carry-not-text", "a-negative-count", "refusals-not-a-list", "not-a-table"],
)
def test_a_fetch_status_not_in_its_shape_fails_the_report(
    tmp_path: Path, monkeypatch, status: object
) -> None:
    path = tmp_path / "status.json"
    if isinstance(status, dict):
        _status(path, **status)
    else:
        path.write_text(json.dumps(status), encoding="utf-8")
    (tmp_path / "h").mkdir()
    code, _, _ = _report(tmp_path / "h", monkeypatch, tmp_path, "--fetch-status", str(path))
    assert code == 2


def _window(folder: Path, members: dict[str, bytes], *, links: tuple[str, ...] = ()) -> None:
    import stat

    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / "window.zip", "w", zipfile.ZIP_STORED) as zipped:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            if name in links:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipped.writestr(info, data)


def _collector_archive(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("members", "links"),
    [
        ({"../telemetry-history-2-1.zip": "history-2-1.jsonl"}, ()),
        ({"/telemetry-history-2-1.zip": "history-2-1.jsonl"}, ()),
        ({"telemetry-history-2-1.zip": "../history-2-1.jsonl"}, ()),
        ({"telemetry-history-2-1.zip": "history-2-1.jsonl"}, ("telemetry-history-2-1.zip",)),
    ],
    ids=["a-parent-path", "an-absolute-path", "a-parent-path-inside", "a-link"],
)
def test_a_traversal_or_link_member_of_the_window_is_refused(
    tmp_path: Path, monkeypatch, capsys, members: dict, links: tuple
) -> None:
    plain = tmp_path / "plain"
    _write(plain, 2, 1, 1, {"t::a": ("passed", 1.0)})
    data = (plain / "history-2-1.jsonl").read_bytes()
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)})
    _window(
        folder,
        {outer: _collector_archive(inner, data) for outer, inner in members.items()},
        links=links,
    )
    code, data_out, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 0 and data_out["window"]["runs"] == 1
    assert "::warning title=archive member refused::" in capsys.readouterr().out


def test_a_name_the_window_holds_twice_is_not_read(tmp_path: Path, monkeypatch, capsys) -> None:
    """A member is opened by its name, and of two of one name zipfile opens the last, which is
    not the one checked: neither is read."""
    plain = tmp_path / "plain"
    _write(plain, 2, 1, 1, {"t::a": ("passed", 1.0)})
    data = (plain / "history-2-1.jsonl").read_bytes()
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)})
    archive = _collector_archive("history-2-1.jsonl", data)
    with warnings.catch_warnings(), zipfile.ZipFile(folder / "window.zip", "w") as zipped:
        warnings.simplefilter("ignore")  # zipfile warns of the name it is asked to write twice
        zipped.writestr("telemetry-history-2-1.zip", archive)
        zipped.writestr("telemetry-history-2-1.zip", archive)
    code, out, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 0 and out["window"]["runs"] == 1
    assert "'telemetry-history-2-1.zip' more than once" in capsys.readouterr().out


def test_an_oversized_archive_in_the_window_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "MAX_HISTORY_ARCHIVE_BYTES", 64)
    plain = tmp_path / "plain"
    _write(plain, 2, 1, 1, {"t::a": ("passed", 1.0)})
    folder = tmp_path / "h"
    _write(folder, 1, 1, 0, {"t::a": ("passed", 1.0)})
    archive = _collector_archive("history-2-1.jsonl", (plain / "history-2-1.jsonl").read_bytes())
    _window(folder, {"telemetry-history-2-1.zip": archive})
    code, data, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 0 and data["window"]["runs"] == 1


@pytest.mark.parametrize(
    ("cap", "value"),
    [("MAX_WINDOW_MEMBERS", 1), ("MAX_WINDOW_BYTES", 10)],
    ids=["members", "bytes"],
)
def test_a_window_past_its_caps_fails_the_report(
    tmp_path: Path, monkeypatch, cap: str, value: int
) -> None:
    monkeypatch.setattr(telemetry, cap, value)
    plain = tmp_path / "plain"
    _write(plain, 2, 1, 1, {"t::a": ("passed", 1.0)})
    folder = tmp_path / "h"
    archive = _collector_archive("history-2-1.jsonl", (plain / "history-2-1.jsonl").read_bytes())
    _window(folder, {"telemetry-history-2-1.zip": archive, "fetched.json": b"{}"})
    code, _, _ = _report(folder, monkeypatch, tmp_path)
    assert code == 2
