"""``python -m tools.ci parity``: test-shard and test-macos ran the same tests (#3913).

Each history is built the way the collector builds one: planted junit reports, read by
``tools/ci/telemetry.py``'s own ``report_cases``, written as lines of the one schema. So a test id
here is the one a real history carries, ``classname::name`` as the junit report gives it.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from tools.ci import parity, telemetry

RUN = 500


def _junit(*tests: str, skipped: tuple[str, ...] = ()) -> bytes:
    """A junit report of *tests* (``classname::name``) passed and *skipped* skipped."""
    cases = []
    for test in (*tests, *skipped):
        classname, _, name = test.partition("::")
        body = '<skipped type="pytest.skip" message="not here"/>' if test in skipped else ""
        cases.append(
            f'<testcase classname="{classname}" name="{name}" time="0.5">{body}</testcase>'
        )
    xml = '<?xml version="1.0"?><testsuites><testsuite hostname="runner">'
    return (xml + "".join(cases) + "</testsuite></testsuites>").encode()


def _zip(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}.xml", data)
    return buffer.getvalue()


def _head(**changes: object) -> dict:
    head = {
        "kind": "run",
        "schema": telemetry.SCHEMA,
        "version": telemetry.VERSION,
        "run_id": RUN,
        "attempt": 1,
        "workflow": "ci.yml",
    }
    head.update(changes)
    return head


def _history(tmp_path: Path, reports: dict[str, bytes], head: dict | None = None) -> Path:
    """A history of *reports* (artifact name -> junit), as the collector writes one."""
    lines: list[dict] = [head or _head()]
    for name, junit in sorted(reports.items()):
        cases, platform, workers = telemetry.report_cases(name, _zip(name, junit))
        for test, seconds, result, host, rerun, environment in cases:
            line = dict.fromkeys(telemetry.TEST_FIELDS)
            line["rerun_result"] = rerun
            line["environment_failure"] = environment
            line.update(
                kind="test",
                run_id=RUN,
                attempt=1,
                workflow="ci.yml",
                report=name,
                runner=host,
                test=test,
                seconds=seconds,
                first_result=result,
                platform=platform,
                workers=workers,
            )
            lines.append(line)
    path = tmp_path / f"history-{RUN}-1.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


def _parity(path: Path, capsys) -> tuple[int, str]:
    code = parity.main(["--history", str(path)])
    return code, capsys.readouterr().out


A, B, C, D = (f"tests.test_{x}::test_{x}" for x in "abcd")


def test_equal_sets_pass(tmp_path, capsys):
    path = _history(
        tmp_path,
        {
            "junit-test-shard-1": _junit(A, B),
            "junit-test-shard-2": _junit(C),
            "junit-test-macos-1": _junit(C, A),
            "junit-test-macos-2": _junit(B),
        },
    )
    code, out = _parity(path, capsys)
    assert code == 0, out
    assert "test-shard and test-macos ran the same 3 tests" in out


def test_a_version_6_history_with_record_lines_reads_only_its_test_lines(tmp_path, capsys):
    """Version 6 adds the tested run's record lines: a check of test lines reads past them, as
    it reads past the push's own record lines."""
    path = _history(
        tmp_path, {"junit-test-shard-1": _junit(A, B), "junit-test-macos-1": _junit(B, A)}
    )
    assert json.loads(path.read_text().splitlines()[0])["version"] == 6
    record = {"key": "e2e", "result": "failure", "rerun": [], "quarantined": []}
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(dict(record, kind="record")) + "\n")
        out.write(
            json.dumps(dict(record, kind=telemetry.TESTED_RECORD, run_id=600, attempt=1)) + "\n"
        )
    code, out = _parity(path, capsys)
    assert code == 0, out
    assert "test-shard and test-macos ran the same 2 tests" in out


def test_a_skipped_case_is_still_in_its_sides_set(tmp_path, capsys):
    """A test skipped on macOS was collected there: it is the same test, not a missing one."""
    path = _history(
        tmp_path,
        {"junit-test-shard-1": _junit(A, B), "junit-test-macos-1": _junit(A, skipped=(B,))},
    )
    code, out = _parity(path, capsys)
    assert code == 0, out


def test_an_id_on_one_side_only_fails_naming_each_and_its_side(tmp_path, capsys):
    path = _history(
        tmp_path,
        {"junit-test-shard-1": _junit(A, B, C), "junit-test-macos-1": _junit(A, D)},
    )
    code, out = _parity(path, capsys)
    assert code == 1, out
    assert "3 tests ran on one side only" in out
    assert f"only test-shard: {B}" in out
    assert f"only test-shard: {C}" in out
    assert f"only test-macos: {D}" in out
    assert f"only test-macos: {A}" not in out and f"only test-shard: {A}" not in out


def test_a_duplicate_id_counts_once_and_is_named(tmp_path, capsys):
    """One test in two of a side's reports is one test of that side's set, so the sets still
    compare equal; that it ran twice is said, since a shard plan should not let it."""
    path = _history(
        tmp_path,
        {
            "junit-test-shard-1": _junit(A, B),
            "junit-test-shard-2": _junit(B),
            "junit-test-macos-1": _junit(A, B),
        },
    )
    code, out = _parity(path, capsys)
    assert code == 0, out
    assert f"test-shard ran {B} 2 times" in out


def test_other_legs_reports_are_not_either_side(tmp_path, capsys):
    path = _history(
        tmp_path,
        {
            "junit-test-shard-1": _junit(A),
            "junit-test-macos-1": _junit(A),
            "junit-rails": _junit(D),
            "junit-browse-live": _junit(C),
        },
    )
    code, out = _parity(path, capsys)
    assert code == 0, out


@pytest.mark.parametrize(
    "head",
    [
        _head(schema="another-telemetry"),
        _head(version=telemetry.VERSION + 1),
        _head(kind="slots"),
    ],
    ids=["another schema", "a newer version", "not a run line"],
)
def test_a_history_in_another_schema_cannot_be_read(tmp_path, capsys, head):
    path = _history(
        tmp_path, {"junit-test-shard-1": _junit(A), "junit-test-macos-1": _junit(B)}, head
    )
    code, out = _parity(path, capsys)
    assert code == 2, out
    assert "cannot be read" in out


def test_a_missing_file_cannot_be_read(tmp_path, capsys):
    code, out = _parity(tmp_path / "history-500-1.jsonl", capsys)
    assert code == 2, out
    assert "cannot be read" in out


@pytest.mark.parametrize(
    "text",
    ["", "not json\n", json.dumps(_head()) + "\n" + '{"kind": "test", "report": "junit-x"}\n'],
    ids=["empty", "not json", "a test line with no id"],
)
def test_a_file_that_is_not_a_history_cannot_be_read(tmp_path, capsys, text):
    path = tmp_path / "history-500-1.jsonl"
    path.write_text(text, encoding="utf-8")
    code, out = _parity(path, capsys)
    assert code == 2, out


def test_a_line_over_the_limit_cannot_be_read(tmp_path, capsys, monkeypatch):
    path = _history(tmp_path, {"junit-test-shard-1": _junit(A), "junit-test-macos-1": _junit(A)})
    monkeypatch.setattr(parity, "MAX_LINE_BYTES", 64)
    code, out = _parity(path, capsys)
    assert code == 2, out
    # The limit's own refusal, not the JSON error a line cut at the limit would also give.
    assert "line 1 passes 64 bytes" in out, out


@pytest.mark.parametrize("missing", ["junit-test-shard-1", "junit-test-macos-1"])
def test_a_history_with_no_reports_of_one_side_cannot_be_compared(tmp_path, capsys, missing):
    """A side with no report did not run here (macOS is skipped on a push whose tree was tested),
    which is no evidence of parity either way."""
    reports = {"junit-test-shard-1": _junit(A), "junit-test-macos-1": _junit(A)}
    del reports[missing]
    code, out = _parity(_history(tmp_path, reports), capsys)
    assert code == 2, out
    assert "no " in out and " reports" in out


def test_the_command_is_reachable_from_the_controller(tmp_path, capsys):
    from tools.ci.__main__ import main

    path = _history(tmp_path, {"junit-test-shard-1": _junit(A), "junit-test-macos-1": _junit(B)})
    assert main(["parity", "--history", str(path)]) == 1


def test_a_version_one_history_is_read_as_one_without_the_environment_marker(tmp_path, capsys):
    """Version 2 only added ``environment_failure``, so a version-1 history, whose lines lack
    it, is compared as before."""
    path = _history(
        tmp_path,
        {"junit-test-shard-1": _junit(A), "junit-test-macos-1": _junit(A)},
        _head(version=1),
    )
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    for line in lines[1:]:
        del line["environment_failure"]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    code, out = _parity(path, capsys)
    assert code == 0, out
