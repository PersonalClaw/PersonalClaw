"""``python -m tools.ci telemetry``: the one writer of CI telemetry (#3948).

The collector runs after a CI or Breadth run completes. It decides from that run's event and
branch, before it downloads anything, whether the run counts (a push to main, or a Breadth run
on main), then writes one ``history-<run>-<attempt>.jsonl`` in the one versioned schema: a run
line with the facts only the Actions API knows, then one line per test case from the attempt's
junit reports. Driven end to end through ``telemetry.main`` with a fake ``gh``.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from tools.ci import telemetry

RUN = 500


def _zip(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


def _junit(*cases: str) -> bytes:
    return (
        '<?xml version="1.0"?><testsuites><testsuite>'
        + "".join(cases)
        + "</testsuite></testsuites>"
    ).encode()


PASSED = '<testcase classname="tests.test_a" name="test_one" time="1.5"/>'
FAILED = (
    '<testcase classname="tests.test_a.TestB" name="test_two" time="2.0">'
    '<failure message="boom">trace</failure></testcase>'
)
ERRORED = (
    '<testcase classname="tests.test_c" name="test_three" time="0.5">'
    '<error message="x"/></testcase>'
)
SKIPPED = (
    '<testcase classname="tests.test_c" name="test_four" time="0.0">'
    '<skipped type="pytest.skip" message="no"/></testcase>'
)
XFAILED = (
    '<testcase classname="tests.test_c" name="test_five" time="0.1">'
    '<skipped type="pytest.xfail" message="known"/></testcase>'
)


def _run(**changes: object) -> dict:
    run = {
        "id": RUN,
        "run_attempt": 1,
        "name": "CI",
        "path": telemetry.CI_PATH,
        "event": "push",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "head_repository": {"full_name": "o/r"},
        "conclusion": "failure",
        "created_at": "2026-10-18T09:00:00Z",
        "run_started_at": "2026-10-18T09:00:00Z",
        "updated_at": "2026-10-18T09:40:00Z",
    }
    run.update(changes)
    return run


def _attempt(started: str, ended: str, conclusion: str) -> dict:
    """``actions/runs/<id>/attempts/<n>``: that attempt's own start, end and conclusion."""
    return {"run_started_at": started, "updated_at": ended, "conclusion": conclusion}


def _job(name: str, start: str, end: str, conclusion: str = "success", **more: object) -> dict:
    job = {
        "name": name,
        "run_attempt": 1,
        "created_at": "2026-10-18T09:00:00Z",
        "started_at": start,
        "completed_at": end,
        "conclusion": conclusion,
        "labels": ["ubuntu-24.04"],
        "steps": [
            {"name": "Set up job", "started_at": start, "completed_at": start[:-3] + "05Z"},
        ],
    }
    job.update(more)
    return job


class FakeGh:
    """``gh api`` as the collector calls it."""

    def __init__(self, run: dict | None = None) -> None:
        self.run = run or _run()
        self.attempts = {1: _attempt("2026-10-18T09:00:00Z", "2026-10-18T09:40:00Z", "failure")}
        self.page_size = 100
        self.short_by = 0
        self.jobs = [
            _job("test-shard (1)", "2026-10-18T09:02:00Z", "2026-10-18T09:32:00Z", "failure"),
            _job("lint", "2026-10-18T09:01:00Z", "2026-10-18T09:06:00Z"),
            _job("web", "2026-10-18T09:03:00Z", "2026-10-18T09:13:00Z", "cancelled"),
        ]
        self.artifacts: list[dict] = [
            self._artifact(
                11, "junit-test-shard-1", _junit(PASSED, FAILED, ERRORED, SKIPPED, XFAILED)
            ),
            self._artifact(12, "shard-plan", b"{}", zipped=False),
        ]
        self.asked: list[str] = []

    def _artifact(
        self,
        ident: int,
        name: str,
        xml: bytes,
        *,
        created: str = "2026-10-18T09:30:00Z",
        zipped: bool = True,
    ) -> dict:
        data = _zip(f"{name}.xml", xml) if zipped else xml
        return {
            "id": ident,
            "name": name,
            "size_in_bytes": len(data),
            "expired": False,
            "created_at": created,
            "_data": data,
        }

    def _page(self, path: str, key: str, items: list[dict]) -> str:
        assert "per_page=100" in path, path
        page = int(path.split("page=")[-1])
        size = self.page_size
        chunk = items[(page - 1) * size : page * size]
        return json.dumps({"total_count": len(items) + self.short_by, key: chunk})

    def text(self, *args: str) -> str:
        path = next(a for a in args[1:] if not a.startswith("-"))
        self.asked.append(path)
        if path.endswith(f"/actions/runs/{RUN}"):
            return json.dumps(self.run)
        if f"/actions/runs/{RUN}/attempts/" in path and "/jobs?" in path:
            return self._page(path, "jobs", self.jobs)
        if f"/actions/runs/{RUN}/attempts/" in path:
            attempt = int(path.rsplit("/", 1)[1])
            return json.dumps(dict(self.attempts[attempt], run_attempt=attempt))
        if f"/actions/runs/{RUN}/artifacts?" in path:
            listed = [{k: v for k, v in a.items() if k != "_data"} for a in self.artifacts]
            return self._page(path, "artifacts", listed)
        raise AssertionError(f"the collector asked gh for {args}")

    def binary(self, *args: str) -> bytes:
        path = next(a for a in args[1:] if not a.startswith("-"))
        self.asked.append(path)
        ident = int(path.split("/artifacts/")[1].split("/")[0])
        return next(a["_data"] for a in self.artifacts if a["id"] == ident)


def _collect(tmp_path: Path, monkeypatch, gh: FakeGh, attempt: int = 1) -> tuple[int, list[dict]]:
    monkeypatch.setattr(telemetry, "_gh_text", gh.text)
    monkeypatch.setattr(telemetry, "_gh_binary", gh.binary)
    out = tmp_path / "history"
    code = telemetry.main(
        [
            "collect",
            "--repo",
            "o/r",
            "--run-id",
            str(RUN),
            "--attempt",
            str(attempt),
            "--out",
            str(out),
        ]
    )
    files = sorted(out.glob("*.jsonl"))
    lines = [json.loads(line) for f in files for line in f.read_text("utf-8").splitlines()]
    return code, lines


# ── which runs count ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(_run(event="pull_request", head_branch="feature-x"), id="a pull request"),
        pytest.param(_run(event="push", head_branch="feature-x"), id="a branch push"),
        pytest.param(
            _run(
                name="Breadth",
                path=telemetry.BREADTH_PATH,
                head_branch="v1.0.0",
                event="workflow_dispatch",
            ),
            id="breadth on a tag",
        ),
        pytest.param(
            _run(name="Nix", path=f"{telemetry.WORKFLOWS}/nix.yml"), id="another workflow"
        ),
        pytest.param(
            _run(
                name="Breadth",
                path=telemetry.BREADTH_PATH,
                event="pull_request",
                head_repository={"full_name": "someone/fork"},
            ),
            id="a fork's run on a branch it named main",
        ),
        pytest.param(_run(head_repository={"full_name": "someone/fork"}), id="a fork's main"),
    ],
)
def test_a_run_that_does_not_count_is_never_read(run: dict, tmp_path: Path, monkeypatch) -> None:
    """The decision comes from the run itself, before any artifact is listed or downloaded, so
    a pull request's artifacts are never read at all."""
    gh = FakeGh(run)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and lines == []
    assert not any("/artifacts" in a for a in gh.asked), gh.asked


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(_run(), id="a push to main"),
        pytest.param(
            _run(name="Breadth", path=telemetry.BREADTH_PATH, event="schedule"),
            id="breadth's night",
        ),
        pytest.param(
            _run(name="Breadth", path=telemetry.BREADTH_PATH, event="workflow_dispatch"),
            id="breadth dispatched on main",
        ),
    ],
)
def test_a_run_that_counts_is_written(run: dict, tmp_path: Path, monkeypatch) -> None:
    code, lines = _collect(tmp_path, monkeypatch, FakeGh(run))
    assert code == 0 and lines and lines[0]["kind"] == "run"


# ── the one schema ────────────────────────────────────────────────────────────────────────────


def test_the_first_line_names_the_schema_and_its_version(tmp_path: Path, monkeypatch) -> None:
    _, lines = _collect(tmp_path, monkeypatch, FakeGh())
    head = lines[0]
    assert head["schema"] == telemetry.SCHEMA and head["version"] == telemetry.VERSION
    assert set(telemetry.RUN_FIELDS) <= set(head), set(telemetry.RUN_FIELDS) - set(head)
    for line in lines[1:]:
        assert set(line) == set(telemetry.TEST_FIELDS), line


def test_each_test_case_is_one_line_with_its_first_result(tmp_path: Path, monkeypatch) -> None:
    _, lines = _collect(tmp_path, monkeypatch, FakeGh())
    tests = {line["test"]: line for line in lines if line["kind"] == "test"}
    assert {t: v["first_result"] for t, v in tests.items()} == {
        "tests.test_a::test_one": "passed",
        "tests.test_a.TestB::test_two": "failed",
        "tests.test_c::test_three": "error",
        "tests.test_c::test_four": "skipped",
        "tests.test_c::test_five": "xfailed",
    }
    one = tests["tests.test_a::test_one"]
    assert one["seconds"] == 1.5 and one["report"] == "junit-test-shard-1"
    assert one["run_id"] == RUN and one["attempt"] == 1 and one["sha"] == "a" * 40
    # Rerun and quarantine facts are #3947's run-job's; until it lands they are null, never guessed.
    assert one["rerun_result"] is None and one["quarantined"] is None


def test_the_run_line_carries_the_facts_only_the_api_knows(tmp_path: Path, monkeypatch) -> None:
    _, lines = _collect(tmp_path, monkeypatch, FakeGh())
    run = lines[0]
    assert run["run_id"] == RUN and run["attempt"] == 1 and run["workflow"] == "ci.yml"
    assert run["verdict_seconds"] == 40 * 60
    assert run["queue_delay_seconds"] == 60, "the first job started a minute after the run"
    assert run["runner_minutes"] == 30 + 5 + 10
    assert run["cancelled_minutes"] == 10
    assert run["first_failure_seconds"] == 32 * 60
    assert run["artifact_bytes"] == sum(a["size_in_bytes"] for a in FakeGh().artifacts)
    assert run["schedule_lag_seconds"] is None, "a push has no schedule"
    assert run["cache_hits"] is None, "no API reports it; run-job records it (#3909)"
    jobs = {j["name"]: j for j in run["jobs"]}
    assert jobs["lint"]["setup_seconds"] == 5 and jobs["lint"]["queue_seconds"] == 60


def test_a_scheduled_run_records_how_late_it_started(tmp_path: Path, monkeypatch) -> None:
    run = _run(
        name="Breadth",
        path=telemetry.BREADTH_PATH,
        event="schedule",
        created_at="2026-10-18T10:02:00Z",
        run_started_at="2026-10-18T10:02:00Z",
    )
    gh = FakeGh(run)
    monkeypatch.setattr(telemetry, "_crons", lambda path: ["37 9 * * *"])
    _, lines = _collect(tmp_path, monkeypatch, gh)
    assert lines[0]["schedule_lag_seconds"] == 25 * 60


# ── the first result is the attempt's own ────────────────────────────────────────────────────


def test_a_rerun_attempt_writes_its_own_file_and_reads_only_its_own_reports(
    tmp_path: Path, monkeypatch
) -> None:
    """Attempt 2 re-uploads its reports under the same names. Each attempt's collector reads only
    the reports made after that attempt started, so attempt 1's file keeps the first result and
    a passing rerun never replaces it: the report sees both and reads attempt 1 as first."""
    gh = FakeGh(_run(run_attempt=2))
    gh.attempts[2] = _attempt("2026-10-18T10:00:00Z", "2026-10-18T10:40:00Z", "success")
    gh.artifacts.append(
        gh._artifact(21, "junit-test-shard-1", _junit(PASSED), created="2026-10-18T10:20:00Z")
    )
    gh.jobs = [dict(j, run_attempt=2) for j in gh.jobs]
    _, lines = _collect(tmp_path, monkeypatch, gh, attempt=2)
    assert (tmp_path / "history" / f"history-{RUN}-2.jsonl").is_file()
    tests = [line for line in lines if line["kind"] == "test"]
    assert [t["test"] for t in tests] == ["tests.test_a::test_one"]
    assert all(t["attempt"] == 2 for t in tests)
    assert not any("/artifacts/11/" in a for a in gh.asked), "attempt 1's report is not re-read"


def test_two_reports_with_one_name_in_one_attempt_are_read_once(
    tmp_path: Path, monkeypatch
) -> None:
    gh = FakeGh()
    gh.artifacts.append(gh._artifact(13, "junit-test-shard-1", _junit(PASSED, PASSED)))
    _, lines = _collect(tmp_path, monkeypatch, gh)
    assert sum(1 for line in lines if line["kind"] == "test") == 5


# ── artifacts are data, read within bounds ───────────────────────────────────────────────────


def test_an_archive_over_the_limit_is_never_downloaded(tmp_path: Path, monkeypatch) -> None:
    gh = FakeGh()
    gh.artifacts[0]["size_in_bytes"] = telemetry.MAX_ARCHIVE_BYTES + 1
    code, _ = _collect(tmp_path, monkeypatch, gh)
    assert code == 1
    assert not any("/artifacts/11/" in a for a in gh.asked)


def test_a_report_that_inflates_past_the_limit_is_refused(tmp_path: Path, monkeypatch) -> None:
    gh = FakeGh()
    big = _junit(PASSED * (telemetry.MAX_JUNIT_BYTES // len(PASSED) + 1))
    gh.artifacts[0] = gh._artifact(11, "junit-test-shard-1", big)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []


@pytest.mark.parametrize(
    "data", [b"not a zip", _zip("r.xml", b"<not junit")], ids=["not a zip", "not xml"]
)
def test_a_report_that_cannot_be_read_fails_and_writes_nothing(
    data: bytes, tmp_path: Path, monkeypatch
) -> None:
    """A half-written history would read as a run with fewer tests: nothing is better."""
    gh = FakeGh()
    gh.artifacts[0]["_data"] = data
    gh.artifacts[0]["size_in_bytes"] = len(data)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []


# ── scheduled slots: late, and missed ─────────────────────────────────────────────────────────
#
# A run that never started fires no workflow_run, so the collector also runs on its own schedule
# and writes one line per slot each scheduled workflow was due: the run that answered it, or that
# none did. On this repository scheduled runs start hours late (one workflow's last ten nights:
# 239-469 minutes), so a slot is `missed` only once the workflow's next slot has passed with no
# run for this one; until then it is `pending`, never guessed.

NOW = "2026-10-18T13:13:00Z"


class SlotGh:
    def __init__(self, workflows: dict[str, dict], runs: dict[str, list[str]]) -> None:
        self.workflows, self.runs, self.asked = workflows, runs, []

    def text(self, *args: str) -> str:
        path = next(a for a in args[1:] if not a.startswith("-"))
        self.asked.append(path)
        name = path.split("/actions/workflows/")[1].split("/")[0].split("?")[0]
        if "/runs?" in path:
            assert "event=schedule" in path, path
            return json.dumps(
                {
                    "workflow_runs": [
                        {"id": i, "created_at": c} for i, c in enumerate(self.runs.get(name, []), 1)
                    ]
                }
            )
        return json.dumps(self.workflows[name])

    def binary(self, *args: str) -> bytes:
        raise AssertionError("slots read no artifact")


def _slots(
    tmp_path: Path, monkeypatch, gh: SlotGh, crons: dict[str, list[str]], days: int = 3
) -> list[dict]:
    monkeypatch.setattr(telemetry, "_gh_text", gh.text)
    monkeypatch.setattr(telemetry, "_gh_binary", gh.binary)
    monkeypatch.setattr(telemetry, "_crons", lambda path: crons[path.rsplit("/", 1)[1]])
    monkeypatch.setattr(telemetry, "_scheduled_workflows", lambda: sorted(crons))
    out = tmp_path / "slots"
    code = telemetry.main(
        ["slots", "--repo", "o/r", "--now", NOW, "--days", str(days), "--out", str(out)]
    )
    assert code == 0
    files = sorted(out.glob("*.jsonl"))
    assert len(files) == 1 and files[0].name == "slots-2026-10-18T13-13.jsonl"
    lines = [json.loads(x) for x in files[0].read_text("utf-8").splitlines()]
    assert lines[0]["kind"] == "slots" and lines[0]["schema"] == telemetry.SCHEMA
    return lines[1:]


def _by_due(lines: list[dict], workflow: str) -> dict[str, dict]:
    return {x["due_at"]: x for x in lines if x["workflow"] == workflow}


def test_a_slot_with_no_run_is_missed_once_the_next_slot_has_passed(
    tmp_path: Path, monkeypatch
) -> None:
    """Breadth's first slot, 2026-10-18 09:37 UTC, had no run. At 13:13 the next slot (tomorrow's)
    has not come, so it is pending: a run four hours late is ordinary here."""
    gh = SlotGh({"breadth.yml": {"created_at": "2026-10-17T20:53:48Z", "state": "active"}}, {})
    lines = _by_due(
        _slots(tmp_path, monkeypatch, gh, {"breadth.yml": ["37 9 * * *"]}), "breadth.yml"
    )
    assert list(lines) == [
        "2026-10-18T09:37:00Z"
    ], "the slots before the workflow existed were not due"
    slot = lines["2026-10-18T09:37:00Z"]
    assert slot["status"] == "pending" and slot["run_id"] is None
    assert slot["overdue_seconds"] == (3 * 60 + 36) * 60


def test_a_slot_whose_next_slot_passed_with_no_run_is_missed(tmp_path: Path, monkeypatch) -> None:
    gh = SlotGh(
        {"nightly.yml": {"created_at": "2026-10-01T00:00:00Z", "state": "active"}},
        {"nightly.yml": ["2026-10-17T05:50:00Z"]},
    )
    lines = _by_due(
        _slots(tmp_path, monkeypatch, gh, {"nightly.yml": ["41 5 * * *"]}), "nightly.yml"
    )
    assert lines["2026-10-17T05:41:00Z"]["status"] == "ran"
    assert lines["2026-10-17T05:41:00Z"]["lag_seconds"] == 9 * 60
    assert lines["2026-10-16T05:41:00Z"]["status"] == "missed"
    assert lines["2026-10-18T05:41:00Z"]["status"] == "pending"


def test_a_late_run_answers_its_own_slot_not_the_next(tmp_path: Path, monkeypatch) -> None:
    gh = SlotGh(
        {"nightly.yml": {"created_at": "2026-10-01T00:00:00Z", "state": "active"}},
        {"nightly.yml": ["2026-10-16T13:30:00Z", "2026-10-17T12:56:00Z", "2026-10-18T09:52:00Z"]},
    )
    lines = _by_due(
        _slots(tmp_path, monkeypatch, gh, {"nightly.yml": ["41 5 * * *"]}), "nightly.yml"
    )
    assert [lines[d]["lag_seconds"] for d in sorted(lines)] == [469 * 60, 435 * 60, 251 * 60]
    assert all(x["status"] == "ran" for x in lines.values())


def test_a_weekly_slot_before_the_workflow_existed_is_not_due(tmp_path: Path, monkeypatch) -> None:
    gh = SlotGh({"weekly.yml": {"created_at": "2026-10-18T02:42:31Z", "state": "active"}}, {})
    assert _slots(tmp_path, monkeypatch, gh, {"weekly.yml": ["29 6 * * 3"]}, days=8) == []


def test_a_disabled_workflow_says_so_instead_of_missing_every_slot(
    tmp_path: Path, monkeypatch
) -> None:
    gh = SlotGh(
        {"nightly.yml": {"created_at": "2026-10-01T00:00:00Z", "state": "disabled_manually"}}, {}
    )
    lines = _slots(tmp_path, monkeypatch, gh, {"nightly.yml": ["41 5 * * *"]})
    assert {x["status"] for x in lines} == {"disabled"}


# ── review fixes: the attempt's own facts, pagination, the event rule, bounds, where it ran ───


def test_attempt_one_collected_after_a_rerun_keeps_its_own_first_result(
    tmp_path: Path, monkeypatch
) -> None:
    """A collector re-run after CI was re-run reads attempt 1 when the run object already
    describes attempt 2. Attempt 1's line must still say attempt 1's conclusion and end, its own
    jobs, and only the reports it uploaded, never the green rerun's."""
    gh = FakeGh(_run(run_attempt=2, conclusion="success", updated_at="2026-10-18T11:00:00Z"))
    gh.attempts[2] = _attempt("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "success")
    gh.artifacts.append(
        gh._artifact(21, "junit-test-shard-2", _junit(PASSED), created="2026-10-18T10:20:00Z")
    )
    gh.jobs.append(
        _job("test-shard (2)", "2026-10-18T10:01:00Z", "2026-10-18T10:30:00Z", run_attempt=2)
    )
    _, lines = _collect(tmp_path, monkeypatch, gh, attempt=1)
    run = lines[0]
    assert run["conclusion"] == "failure" and run["updated_at"] == "2026-10-18T09:40:00Z"
    assert run["verdict_seconds"] == 40 * 60
    assert "test-shard (2)" not in {j["name"] for j in run["jobs"]}
    assert {t["report"] for t in lines[1:]} == {"junit-test-shard-1"}
    assert not any("/artifacts/21/" in a for a in gh.asked), "attempt 2's report is not read"


def test_every_page_of_a_long_listing_is_read(tmp_path: Path, monkeypatch) -> None:
    gh = FakeGh()
    gh.page_size = 1
    _, lines = _collect(tmp_path, monkeypatch, gh)
    assert len(lines) == 1 + 5 and len(lines[0]["jobs"]) == 3


def test_a_listing_that_comes_up_short_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    """Silently missing reports would read as a run that ran fewer tests."""
    gh = FakeGh()
    gh.short_by = 1
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(_run(event="workflow_dispatch"), id="ci.yml dispatched on main"),
        pytest.param(
            _run(name="Breadth", path=telemetry.BREADTH_PATH, event="push"), id="breadth pushed"
        ),
    ],
)
def test_an_event_its_workflow_does_not_count_is_never_read(
    run: dict, tmp_path: Path, monkeypatch
) -> None:
    """Main, this repository, a collected workflow: only the event excludes these."""
    gh = FakeGh(run)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and lines == []
    assert not any("/artifacts" in a for a in gh.asked)


def test_the_inflate_bound_is_what_refuses_an_oversized_report(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Compressed, so the archive passes the download bound and only the inflate bound can
    refuse it."""
    gh = FakeGh()
    big = _junit(PASSED * (telemetry.MAX_JUNIT_BYTES // len(PASSED) + 1))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("junit-test-shard-1.xml", big)
    gh.artifacts[0] = dict(gh._artifact(11, "junit-test-shard-1", b""), _data=buffer.getvalue())
    gh.artifacts[0]["size_in_bytes"] = len(buffer.getvalue())
    assert gh.artifacts[0]["size_in_bytes"] < telemetry.MAX_ARCHIVE_BYTES
    code, _ = _collect(tmp_path, monkeypatch, gh)
    assert code == 1
    assert f"its reports pass {telemetry.MAX_JUNIT_BYTES} bytes" in capsys.readouterr().out


@pytest.mark.parametrize(
    "xml",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "y">]><testsuites/>',
        b'<?xml version="1.0"?>' + b"<!-- " + b"p" * 8192 + b" -->"
        b'<!DOCTYPE x [<!ENTITY e "y">]><testsuites/>',
    ],
    ids=["at the start", "after a long prolog"],
)
def test_a_report_that_declares_a_doctype_writes_nothing(
    xml: bytes, tmp_path: Path, monkeypatch, capsys
) -> None:
    gh = FakeGh()
    gh.artifacts[0] = gh._artifact(11, "junit-test-shard-1", xml)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []
    assert "declares a DOCTYPE" in capsys.readouterr().out


def test_a_report_that_is_not_utf8_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    gh = FakeGh()
    utf16 = '<?xml version="1.0" encoding="utf-16"?><testsuites/>'.encode("utf-16")
    gh.artifacts[0] = gh._artifact(11, "junit-test-shard-1", utf16)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []
    assert "not UTF-8" in capsys.readouterr().out


def _facts(platform: str, workers: str) -> str:
    return (
        '<testcase classname="tests.test_a" name="test_one" time="1.5"><properties>'
        f'<property name="ci_platform" value="{platform}"/>'
        f'<property name="ci_workers" value="{workers}"/>'
        "</properties></testcase>"
    )


def test_every_test_line_carries_the_platform_and_workers_its_report_recorded(
    tmp_path: Path, monkeypatch
) -> None:
    """The suite records them on each process's first test case (tests/run_facts.py); every
    line of that report carries them, so a reader keeps each platform's seconds apart."""
    gh = FakeGh()
    gh.artifacts[0] = gh._artifact(
        11, "junit-test-shard-1", _junit(_facts("linux-x86_64-3.12.15", "4"), FAILED, SKIPPED)
    )
    _, lines = _collect(tmp_path, monkeypatch, gh)
    tests = lines[1:]
    assert len(tests) == 3
    assert {(t["platform"], t["workers"]) for t in tests} == {("linux-x86_64-3.12.15", 4)}


def test_a_report_with_no_recorded_platform_says_so_with_nulls(tmp_path: Path, monkeypatch) -> None:
    _, lines = _collect(tmp_path, monkeypatch, FakeGh())
    assert {(t["platform"], t["workers"]) for t in lines[1:]} == {(None, None)}


def test_a_report_whose_processes_disagree_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    gh = FakeGh()
    gh.artifacts[0] = gh._artifact(
        11,
        "junit-test-shard-1",
        _junit(_facts("linux-x86_64-3.12.15", "4"), _facts("darwin-arm64-3.13.16", "2")),
    )
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []


@pytest.mark.parametrize(
    "at, matches",
    [
        ("2026-10-15T06:00:00Z", True),  # a Thursday, the 15th: the day of the month alone
        ("2026-10-19T06:00:00Z", True),  # a Monday, the 19th: the day of the week alone
        ("2026-10-20T06:00:00Z", False),  # a Tuesday, the 20th: neither
    ],
)
def test_a_cron_that_names_both_days_matches_either_as_cron_does(at: str, matches: bool) -> None:
    when = telemetry._at(at)
    assert when is not None
    assert telemetry._cron_matches("0 6 15 * 1", when) is matches
