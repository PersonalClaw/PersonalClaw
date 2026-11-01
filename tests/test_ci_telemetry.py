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
import stat
import subprocess
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
        "name": "Merge",
        "path": telemetry.MERGE_PATH,
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


PUSHED = "a" * 40
PARENT = "b" * 40
TREE = "e" * 40


class FakeGh:
    """``gh api`` as the collector calls it. A push's commit, its parent and its pull requests
    answer as a push no pull request was merged as, unless a test plants one (``tested``)."""

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
        self.commits: dict[str, dict] = {PUSHED: _commit(TREE, PARENT)}
        self.pulls: list[dict] = []
        self.behind = 0
        self.head_runs: list[dict] = []
        self.events: list[dict] = []
        #: Main's rules, which the tree check reads for a merge queue (0.4.0 A1): none yet. An
        #: exception planted here is the read failing.
        self.rules: object = [{"type": "pull_request"}]
        #: Other runs, by id: the run object, its first attempt, and its artifacts.
        self.others: dict[int, dict] = {}

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
        found = self._tested(path)
        if found is not None:
            return found
        if f"/actions/runs/{RUN}/attempts/" in path and "/jobs?" in path:
            return self._page(path, "jobs", self.jobs)
        if f"/actions/runs/{RUN}/attempts/" in path:
            attempt = int(path.rsplit("/", 1)[1])
            return json.dumps(dict(self.attempts[attempt], run_attempt=attempt))
        if f"/actions/runs/{RUN}/artifacts?" in path:
            listed = [{k: v for k, v in a.items() if k != "_data"} for a in self.artifacts]
            return self._page(path, "artifacts", listed)
        raise AssertionError(f"the collector asked gh for {args}")

    def _tested(self, path: str) -> str | None:
        """The reads that find the pull request run that tested a push's tree, or None."""
        if path.startswith("repos/o/r/commits/") and path.endswith("/pulls"):
            return json.dumps(self.pulls)
        if path.startswith("repos/o/r/commits/"):
            sha = path.rsplit("/", 1)[1]
            if sha not in self.commits:
                raise subprocess.CalledProcessError(1, ["gh", "api", path], "", "Not Found")
            return json.dumps(self.commits[sha])
        if path.startswith("repos/o/r/compare/"):
            # Only the pushed commit's parent is a base the planted head holds.
            base = path.split("/compare/")[1].split("...")[0]
            return json.dumps({"behind_by": self.behind if base == PARENT else 9, "ahead_by": 1})
        if path.startswith("repos/o/r/actions/workflows/merge.yml/runs?"):
            return json.dumps({"total_count": len(self.head_runs), "workflow_runs": self.head_runs})
        if path.startswith("repos/o/r/issues/"):
            return json.dumps(self.events)
        if path.startswith("repos/o/r/rules/branches/main"):
            if isinstance(self.rules, Exception):
                raise self.rules
            return json.dumps(self.rules)
        for ident, other in self.others.items():
            base = f"repos/o/r/actions/runs/{ident}"
            if path == base:
                return json.dumps(other["run"])
            if path == f"{base}/attempts/1":
                return json.dumps(other["attempt"])
            if path.startswith(f"{base}/artifacts?"):
                listed = [{k: v for k, v in a.items() if k != "_data"} for a in other["artifacts"]]
                return self._page(path, "artifacts", listed)
        return None

    def binary(self, *args: str) -> bytes:
        path = next(a for a in args[1:] if not a.startswith("-"))
        self.asked.append(path)
        ident = int(path.split("/artifacts/")[1].split("/")[0])
        every = [*self.artifacts, *(a for o in self.others.values() for a in o["artifacts"])]
        return next(a["_data"] for a in every if a["id"] == ident)


def _commit(tree: str, parent: str | None = None) -> dict:
    return {"commit": {"tree": {"sha": tree}}, "parents": [{"sha": parent}] if parent else []}


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
        pytest.param(
            _run(name="Merge", path=telemetry.MERGE_PATH, event="workflow_dispatch"),
            id="merge.yml dispatched on main",
        ),
        pytest.param(
            _run(name="Merge", path=telemetry.MERGE_PATH, event="pull_request"),
            id="merge.yml on a pull request",
        ),
        # The workflow the flip deleted (#3915): a run of it, were one to come, counts for nothing.
        pytest.param(
            _run(name="CI", path=f"{telemetry.WORKFLOWS}/ci.yml"), id="the deleted ci.yml's push"
        ),
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
        pytest.param(_run(), id="merge.yml's push to main"),
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
    # A case run-job did not rerun carries no rerun_result; quarantine facts are not written yet.
    # Both are null, never guessed.
    assert one["rerun_result"] is None and one["quarantined"] is None


def test_the_run_line_carries_the_facts_only_the_api_knows(tmp_path: Path, monkeypatch) -> None:
    _, lines = _collect(tmp_path, monkeypatch, FakeGh())
    run = lines[0]
    assert run["run_id"] == RUN and run["attempt"] == 1 and run["workflow"] == "merge.yml"
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
        pytest.param(_run(event="workflow_dispatch"), id="merge.yml dispatched on main"),
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


def test_a_test_line_carries_the_rerun_result_run_job_wrote_on_its_case(
    tmp_path: Path, monkeypatch
) -> None:
    """#3947's run-job writes the diagnostic rerun's word on the failed case; the line keeps the
    first result and carries the word beside it."""
    flaked = (
        '<testcase classname="tests.test_a.TestB" name="test_two" time="2.0">'
        '<properties><property name="rerun_result" value="flake"/></properties>'
        '<failure message="boom">trace</failure></testcase>'
    )
    gh = FakeGh()
    gh.artifacts[0] = gh._artifact(11, "junit-test-shard-1", _junit(PASSED, flaked))
    _, lines = _collect(tmp_path, monkeypatch, gh)
    tests = {line["test"]: line for line in lines if line["kind"] == "test"}
    two = tests["tests.test_a.TestB::test_two"]
    assert two["first_result"] == "failed" and two["rerun_result"] == "flake"
    assert tests["tests.test_a::test_one"]["rerun_result"] is None


# ── the environment marker (version 2) ────────────────────────────────────────────────────────


def test_the_schema_is_version_two_and_every_reader_accepts_version_one() -> None:
    """Version 2 adds ``environment_failure`` to the test line and changes nothing else, so a
    reader takes a version-1 history as one whose lines carry no such field."""
    assert telemetry.VERSION >= 2, "version 3 pins the exact version and readers below"
    assert frozenset({1, 2}) <= telemetry.READS
    assert "environment_failure" in telemetry.TEST_FIELDS


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            '<testcase classname="tests.test_b" name="test_live" time="9.0">'
            '<failure message="AssertionError: proof: ENVIRONMENT, NOT POLICY — detached">'
            "trace</failure></testcase>",
            True,
            id="in-the-failure-message",
        ),
        pytest.param(
            '<testcase classname="tests.test_b" name="test_live" time="9.0">'
            '<failure message="AssertionError">E   ENVIRONMENT, NOT POLICY — detached</failure>'
            "</testcase>",
            True,
            id="in-the-failure-text",
        ),
        pytest.param(
            '<testcase classname="tests.test_b" name="test_live" time="9.0">'
            '<error message="ENVIRONMENT, NOT POLICY in setup"/></testcase>',
            True,
            id="in-an-error",
        ),
        pytest.param(
            '<testcase classname="tests.test_b" name="test_live" time="9.0">'
            '<failure message="AssertionError: the redirect was allowed">trace</failure>'
            "</testcase>",
            False,
            id="a-failure-without-it",
        ),
        pytest.param(
            '<testcase classname="tests.test_b" name="test_live" time="9.0">'
            '<failure message="environment, not policy">trace</failure></testcase>',
            False,
            id="not-the-exact-marker",
        ),
        pytest.param(
            '<testcase classname="tests.test_b" name="test_live" time="9.0">'
            '<skipped type="pytest.skip" message="ENVIRONMENT, NOT POLICY"/></testcase>',
            False,
            id="a-skip-that-names-it",
        ),
        pytest.param(
            '<testcase classname="tests.test_b" name="test_live" time="9.0">'
            "<system-out>ENVIRONMENT, NOT POLICY</system-out></testcase>",
            False,
            id="a-pass-whose-output-names-it",
        ),
    ],
)
def test_a_test_line_says_whether_its_failure_carried_the_environment_marker(
    tmp_path: Path, monkeypatch, case: str, expected: bool
) -> None:
    """The suite names a red the environment caused, and not the code under test, with the
    exact words ``ENVIRONMENT, NOT POLICY`` in the failure (the browse proof's detached page).
    Only a failure or an error carries it; any other case is false, never null."""
    gh = FakeGh()
    gh.artifacts[0] = gh._artifact(11, "junit-browse-live", _junit(PASSED, case))
    _, lines = _collect(tmp_path, monkeypatch, gh)
    tests = {line["test"]: line for line in lines if line["kind"] == "test"}
    assert tests["tests.test_b::test_live"]["environment_failure"] is expected
    assert tests["tests.test_a::test_one"]["environment_failure"] is False


# ── the CPU model each report ran on (schema version 3) ────────────────────────────────────────


def _hosted(platform: str, workers: str, cpu: str | None) -> str:
    model = f'<property name="ci_cpu" value="{cpu}"/>' if cpu is not None else ""
    return (
        '<testcase classname="tests.test_a" name="test_one" time="1.5"><properties>'
        f'<property name="ci_platform" value="{platform}"/>'
        f'<property name="ci_workers" value="{workers}"/>{model}'
        "</properties></testcase>"
    )


def test_the_run_line_maps_each_report_to_the_cpu_model_it_ran_on(
    tmp_path: Path, monkeypatch
) -> None:
    """A shard's every file runs slower together on a slower host model, so the planner reads
    the model per report from the run line: one entry per report, null when none was recorded."""
    gh = FakeGh()
    gh.artifacts[0] = gh._artifact(
        11, "junit-test-shard-1", _junit(_hosted("linux-x86_64-3.12.15", "4", "EPYC 7763"), FAILED)
    )
    gh.artifacts.append(gh._artifact(13, "junit-test-shard-2", _junit(PASSED)))
    _, lines = _collect(tmp_path, monkeypatch, gh)
    head = lines[0]
    assert head["version"] >= 3 and telemetry.VERSION == 6
    assert head["cpus"] == {"junit-test-shard-1": "EPYC 7763", "junit-test-shard-2": None}
    assert "cpus" in telemetry.RUN_FIELDS
    for line in lines[1:]:
        assert set(line) == set(telemetry.TEST_FIELDS), "the test lines are unchanged"


def test_a_report_whose_processes_disagree_on_the_cpu_model_writes_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    gh = FakeGh()
    gh.artifacts[0] = gh._artifact(
        11,
        "junit-test-shard-1",
        _junit(
            _hosted("linux-x86_64-3.12.15", "4", "EPYC 7763"),
            _hosted("linux-x86_64-3.12.15", "4", "EPYC 9V45"),
        ),
    )
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []


def test_the_schema_is_version_six_and_every_reader_accepts_versions_one_to_five() -> None:
    """Version 3 only added the run line's ``cpus``, version 4 only the record lines, version 5
    only the run line's ``tested_by``, and version 6 only the tested run's record lines, so a
    history kept from before any of them stays readable for as long as it is kept, and nothing
    newer is guessed at."""
    assert telemetry.VERSION == 6
    assert telemetry.READS == frozenset({1, 2, 3, 4, 5, 6})
    assert telemetry.TESTED_RECORDS_SINCE == telemetry.VERSION
    assert "cpus" in telemetry.RUN_FIELDS and "records" not in telemetry.RUN_FIELDS
    assert telemetry.RUN_FIELDS[-1] == "tested_by"
    assert telemetry.RECORD_FIELDS == ("kind", "key", "result", "rerun", "quarantined")


# ── each job's record (schema version 4) ──────────────────────────────────────────────────────
#
# Each test-stage entry uploads its job's record (``run-job``'s) as ``ci-record-<key slug>-<run>-
# <attempt>``. Each record the attempt uploaded is one line after the run line: the job's result,
# each failed test with the diagnostic rerun's word for it, and each quarantined failure, the one
# place all three runners' classifications land, Playwright's included, which no junit report
# carries. A line of its own holds each record within a line's bound however many jobs failed.

FLAKY = "tests/test_a.py::test_flaky"
TWICE = "tests/test_a.py::test_twice"


def _job_record(
    key: str,
    *,
    result: str = "failure",
    tests: tuple[tuple[str, str | None], ...] = ((FLAKY, "flake"), (TWICE, "repeated failure")),
    quarantined: tuple[str, ...] = (),
    run_id: int | None = RUN,
    attempt: int = 1,
) -> str:
    from tools.ci import verdict

    failed = result == "failure"
    rerun = None
    if failed and tests:
        rerun = {
            "argv": ["pytest", "-n", "0"],
            "exit": 1,
            "tests": [{"test": t, "rerun_result": w, "why": "w"} for t, w in tests],
        }
    return verdict.job_record(
        key=key,
        result=result,
        identity=verdict.Identity("a" * 40),
        steps=[(["pytest"], 1 if failed else 0, 1.0)],
        attempt=attempt,
        run_id=run_id,
        rerun=rerun,
        quarantined=quarantined,
    )


def _zip_members(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _named(key: str, attempt: int) -> str:
    from tools.ci import verdict

    return verdict.record_artifact(key, run_id=RUN, attempt=attempt)


def _record_artifact(
    gh: FakeGh,
    ident: int,
    key: str,
    text: str,
    *,
    name: str | None = None,
    members: dict[str, bytes] | None = None,
    created: str = "2026-10-18T09:30:00Z",
) -> None:
    from tools.ci import verdict

    data = _zip_members(members if members is not None else {"record.json": text.encode()})
    gh.artifacts.append(
        {
            "id": ident,
            "name": name or verdict.record_artifact(key, run_id=RUN, attempt=1),
            "size_in_bytes": len(data),
            "expired": False,
            "created_at": created,
            "_data": data,
        }
    )


def _record_lines(lines: list[dict]) -> list[dict]:
    return [line for line in lines if line["kind"] == "record"]


def test_each_job_record_is_one_line_after_the_run_line(tmp_path: Path, monkeypatch) -> None:
    gh = FakeGh()
    _record_artifact(gh, 21, "suite (1/2)", _job_record("suite (1/2)"))
    _record_artifact(
        gh, 22, "e2e", _job_record("e2e", tests=(("e2e/chat.spec.ts > sends", "flake"),))
    )
    _record_artifact(
        gh, 23, "static", _job_record("static", result="success", quarantined=(FLAKY,))
    )
    _record_artifact(gh, 24, "lint", _job_record("lint", result="success"))
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0
    head = lines[0]
    assert head["kind"] == "run" and head["version"] >= 4 and "records" not in head
    records = _record_lines(lines)
    assert lines[1 : 1 + len(records)] == records
    assert records == [
        {
            "kind": "record",
            "key": "e2e",
            "result": "failure",
            "rerun": [{"test": "e2e/chat.spec.ts > sends", "rerun_result": "flake"}],
            "quarantined": [],
        },
        {"kind": "record", "key": "lint", "result": "success", "rerun": [], "quarantined": []},
        {
            "kind": "record",
            "key": "static",
            "result": "success",
            "rerun": [],
            "quarantined": [FLAKY],
        },
        {
            "kind": "record",
            "key": "suite (1/2)",
            "result": "failure",
            "rerun": [
                {"test": FLAKY, "rerun_result": "flake"},
                {"test": TWICE, "rerun_result": "repeated failure"},
            ],
            "quarantined": [],
        },
    ]
    assert all(set(line) == set(telemetry.RECORD_FIELDS) for line in records)
    # A record adds no test line: the test lines are the junit reports' alone.
    tests = [line for line in lines if line["kind"] == "test"]
    assert {line["report"] for line in tests} == {"junit-test-shard-1"}
    assert len(lines) == 1 + len(records) + len(tests)


def test_the_records_are_in_their_keys_order_whatever_their_artifacts_names_sort_as(
    tmp_path: Path, monkeypatch
) -> None:
    """An artifact's name is its key's slug, lower case, so the two can sort apart."""
    gh = FakeGh()
    _record_artifact(gh, 21, "alpha", _job_record("alpha", result="success"))
    _record_artifact(gh, 22, "Zed", _job_record("Zed", result="success"))
    _, lines = _collect(tmp_path, monkeypatch, gh)
    assert [entry["key"] for entry in _record_lines(lines)] == ["Zed", "alpha"]


def test_a_failed_test_the_rerun_did_not_classify_is_carried_as_null(
    tmp_path: Path, monkeypatch
) -> None:
    gh = FakeGh()
    rail = "tests/rails/test_r.py::test_rail"
    _record_artifact(gh, 21, "rails", _job_record("rails", tests=((rail, None),)))
    _, lines = _collect(tmp_path, monkeypatch, gh)
    [entry] = _record_lines(lines)
    assert entry["rerun"] == [{"test": rail, "rerun_result": None}]


def test_an_attempt_that_uploaded_no_record_carries_none(tmp_path: Path, monkeypatch) -> None:
    code, lines = _collect(tmp_path, monkeypatch, FakeGh())
    assert code == 0 and _record_lines(lines) == []


def test_a_record_another_attempt_uploaded_is_not_this_attempts(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.ci import verdict

    gh = FakeGh()
    later = verdict.record_artifact("suite", run_id=RUN, attempt=2)
    _record_artifact(
        gh, 21, "suite", _job_record("suite", attempt=2), name=later, created="2026-10-18T10:30:00Z"
    )
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and _record_lines(lines) == []


def test_two_uploads_of_one_name_are_read_once_the_lower_id(tmp_path: Path, monkeypatch) -> None:
    gh = FakeGh()
    _record_artifact(gh, 31, "suite", _job_record("suite", tests=((TWICE, "repeated failure"),)))
    _record_artifact(gh, 30, "suite", _job_record("suite", tests=((FLAKY, "flake"),)))
    _, lines = _collect(tmp_path, monkeypatch, gh)
    [entry] = _record_lines(lines)
    assert entry["rerun"] == [{"test": FLAKY, "rerun_result": "flake"}]


def _not_a_zip(gh: FakeGh) -> None:
    _record_artifact(gh, 21, "suite", _job_record("suite"))
    gh.artifacts[-1]["_data"] = b"not a zip"
    gh.artifacts[-1]["size_in_bytes"] = len(b"not a zip")


@pytest.mark.parametrize(
    ("plant", "why"),
    [
        pytest.param(_not_a_zip, "not a record archive", id="not-a-zip"),
        pytest.param(
            lambda gh: _record_artifact(gh, 21, "suite", "", members={}),
            "holds no record",
            id="no-member",
        ),
        pytest.param(
            lambda gh: _record_artifact(
                gh,
                21,
                "suite",
                "",
                members={"a.json": _job_record("suite").encode(), "b.json": b"{}"},
            ),
            "holds 2 files",
            id="two-members",
        ),
        pytest.param(
            lambda gh: _record_artifact(
                gh, 21, "suite", "", members={"x/record.json": _job_record("suite").encode()}
            ),
            "under a folder",
            id="a-member-under-a-folder",
        ),
        pytest.param(
            lambda gh: _record_artifact(
                gh, 21, "suite", "", members={"../record.json": _job_record("suite").encode()}
            ),
            "holds a parent path",
            id="a-member-that-climbs-out",
        ),
        pytest.param(
            lambda gh: _record_artifact(
                gh, 21, "suite", "", members={"/record.json": _job_record("suite").encode()}
            ),
            "is an absolute path",
            id="a-member-at-an-absolute-path",
        ),
        pytest.param(
            lambda gh: _record_artifact(
                gh, 21, "suite", "", members={"record.txt": _job_record("suite").encode()}
            ),
            "not a record file",
            id="not-json",
        ),
        pytest.param(
            lambda gh: _record_artifact(gh, 21, "suite", '{"key": "suite"}'),
            "must hold exactly",
            id="a-record-the-reader-refuses",
        ),
        pytest.param(
            lambda gh: _record_artifact(gh, 21, "suite", "", members={"r.json": b"\xff{"}),
            "not UTF-8",
            id="not-utf-8",
        ),
        pytest.param(
            lambda gh: _record_artifact(gh, 21, "suite", _job_record("other")),
            "holds the record of 'other'",
            id="renamed",
        ),
        pytest.param(
            lambda gh: _record_artifact(gh, 21, "suite", _job_record("suite", run_id=499)),
            "names run 499",
            id="another-runs-record",
        ),
        pytest.param(
            lambda gh: _record_artifact(gh, 21, "suite", _job_record("suite", run_id=None)),
            "names run None",
            id="a-local-record",
        ),
        pytest.param(
            lambda gh: _record_artifact(gh, 21, "suite", _job_record("suite", attempt=2)),
            "holds the record of 'suite' on attempt 2",
            id="another-attempts-record",
        ),
        pytest.param(
            lambda gh: _record_artifact(
                gh, 21, "suite", _job_record("suite", attempt=2), name=_named("suite", 2)
            ),
            "written by attempt 2, not attempt 1",
            id="a-later-attempts-upload-in-this-attempts-time",
        ),
    ],
)
def test_a_record_it_cannot_read_writes_nothing(
    plant, why: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    """A history with fewer records would read as a run whose reruns found less: a record that
    cannot be read fails the collection, as a junit report that cannot be read does."""
    gh = FakeGh()
    plant(gh)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []
    assert why in capsys.readouterr().out


def test_a_record_archive_past_its_bound_is_never_downloaded(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    gh = FakeGh()
    _record_artifact(gh, 21, "suite", _job_record("suite"))
    gh.artifacts[-1]["size_in_bytes"] = telemetry.MAX_RECORD_ARCHIVE_BYTES + 1
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []
    assert not any(a.endswith("/21/zip") for a in gh.asked), gh.asked


@pytest.mark.parametrize("listed", [True, False], ids=["as-listed", "as-downloaded"])
def test_a_record_archive_over_two_mib_is_refused(
    listed: bool, tmp_path: Path, monkeypatch, capsys
) -> None:
    """Over 2 MiB, whether the listing says so or only the bytes downloaded do: the record it
    holds is within 1 MB, so nothing larger is one."""
    gh = FakeGh()
    _record_artifact(gh, 21, "suite", _job_record("suite"))
    over = 2 * 1024 * 1024 + 1
    if listed:
        gh.artifacts[-1]["size_in_bytes"] = over
    else:
        gh.artifacts[-1]["_data"] += b"\0" * (over - len(gh.artifacts[-1]["_data"]))
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []
    assert f"the archive passes {2 * 1024 * 1024} bytes" in capsys.readouterr().out


def test_a_record_past_a_records_bound_is_refused(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools.ci import verdict

    gh = FakeGh()
    padded = _job_record("suite")[:-2] + " " * (verdict.RECORD_BYTES + 1) + "}\n"
    _record_artifact(gh, 21, "suite", padded)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []
    assert "larger than any job record" in capsys.readouterr().out


def test_a_record_of_exactly_a_records_bound_is_read(tmp_path: Path, monkeypatch) -> None:
    """The collector reads a record within the bound ``verdict --records`` reads one within, to
    the byte."""
    from tools.ci import verdict

    gh = FakeGh()
    text = _job_record("suite")
    padded = text[:-2] + " " * (verdict.RECORD_BYTES - len(text)) + "}\n"
    assert len(padded.encode()) == verdict.RECORD_BYTES
    _record_artifact(gh, 21, "suite", padded)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and [entry["key"] for entry in _record_lines(lines)] == ["suite"]


@pytest.mark.parametrize("listed", [True, False], ids=["as-listed", "as-downloaded"])
def test_a_record_archive_of_exactly_two_mib_is_read(
    listed: bool, tmp_path: Path, monkeypatch
) -> None:
    gh = FakeGh()
    _record_artifact(gh, 21, "suite", _job_record("suite"))
    bound = 2 * 1024 * 1024
    if listed:
        gh.artifacts[-1]["size_in_bytes"] = bound
    else:
        # Bytes before a zip's first member are no part of it: the archive still reads.
        data = gh.artifacts[-1]["_data"]
        gh.artifacts[-1]["_data"] = b"\0" * (bound - len(data)) + data
        assert len(gh.artifacts[-1]["_data"]) == bound
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and [entry["key"] for entry in _record_lines(lines)] == ["suite"]


def _many_failed(key: str, shard: int, count: int) -> str:
    """A record of ``count`` repeated failures, each id about as long as the suite's longest."""
    long = "x" * 40
    tests = tuple(
        (f"tests/test_mass_{shard}.py::TestEach::test_{n:05}[{long}]", "repeated failure")
        for n in range(count)
    )
    return _job_record(key, tests=tests)


def test_records_that_pass_a_line_together_keep_the_run_in_every_readers_history(
    tmp_path: Path, monkeypatch
) -> None:
    """Each record is within its own bound, and together they pass the bound every reader holds a
    history line to: on one line they would lose the whole run, its test lines included, to the
    weekly report, its fetch and the shard planner. A line per record keeps every line within
    the bound, and the run in all three."""
    from tools.ci import fetch, report, shards, verdict

    gh = FakeGh()
    keys = ("suite (1/3)", "suite (2/3)", "suite (3/3)")
    for shard, key in enumerate(keys, 1):
        text = _many_failed(key, shard, 3000)
        assert len(text.encode()) <= verdict.RECORD_BYTES
        _record_artifact(gh, 20 + shard, key, text)
    code, _ = _collect(tmp_path, monkeypatch, gh)
    assert code == 0
    history = tmp_path / "history" / f"history-{RUN}-1.jsonl"
    found = report.read(history.parent)
    assert len(found.diagnosed) == 9000 and found.runs_with_records == 1
    assert found.tests["tests.test_a::test_one"].runs == 1
    archive = _zip(history.name, history.read_bytes())
    leg = shards.Leg("suite", "ci.yml", "junit-test-shard-", 1)
    planned = shards.history_seconds(archive, leg, ["tests/test_a.py", "tests/test_c.py"], run=RUN)
    assert planned.seconds
    shown = {RUN: 1}
    assert (
        fetch.history_refusal(io.BytesIO(archive), f"telemetry-history-{RUN}-1.zip", shown) is None
    )
    raw = history.read_bytes().splitlines(keepends=True)
    records = [line for line in raw if b'"kind": "record"' in line]
    assert len(records) == 3 and sum(map(len, records)) > report.MAX_LINE_BYTES
    assert max(map(len, raw)) <= report.MAX_LINE_BYTES


def _raw_utf8(text: str) -> str:
    """A record as JSON may spell it: its text as UTF-8, not as escapes."""
    return json.dumps(json.loads(text), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def _a_test_line_past_the_bound(gh: FakeGh) -> None:
    from tools.ci import report

    name = "test_" + "x" * report.MAX_LINE_BYTES
    case = f'<testcase classname="tests.test_a" name="{name}" time="1.0"/>'
    gh.artifacts[0] = gh._artifact(11, "junit-test-shard-1", _junit(case))


def _a_record_line_past_the_bound(gh: FakeGh) -> None:
    # Within a record's bound as UTF-8; each character a six-byte escape as the line writes it.
    test = "tests/test_a.py::test_" + "\u00e9" * 200_000
    text = _raw_utf8(_job_record("suite", tests=((test, "flake"),)))
    _record_artifact(gh, 21, "suite", text)


@pytest.mark.parametrize(
    "plant",
    [
        pytest.param(_a_test_line_past_the_bound, id="a-test-line"),
        pytest.param(_a_record_line_past_the_bound, id="a-record-line"),
    ],
)
def test_a_line_no_reader_would_take_fails_the_collection_and_writes_nothing(
    plant, tmp_path: Path, monkeypatch, capsys
) -> None:
    """Every reader refuses a history with a line past its bound (``telemetry.MAX_LINE_BYTES``),
    its line break included, whole: a history no reader takes is no history, so the collection
    fails by name, as a report it cannot read does."""
    from tools.ci import report, verdict

    gh = FakeGh()
    plant(gh)
    for artifact in gh.artifacts[2:]:
        member = zipfile.ZipFile(io.BytesIO(artifact["_data"])).infolist()[0]
        assert member.file_size <= verdict.RECORD_BYTES
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 1 and lines == []
    assert f"passes {report.MAX_LINE_BYTES} bytes, which no reader takes" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize(("over", "written"), [(0, True), (1, False)], ids=["at", "one-past"])
def test_a_line_of_the_bound_with_its_line_break_is_written_and_one_byte_more_is_not(
    over: int, written: bool, tmp_path: Path, monkeypatch
) -> None:
    """The collector's bound is the readers', to the byte, the line break counted as they count
    it: a line it writes, a reader takes."""
    from tools.ci import report

    def collect(length: int) -> tuple[int, Path]:
        gh = FakeGh()
        case = f'<testcase classname="tests.test_a" name="test_{"x" * length}" time="1.0"/>'
        gh.artifacts[0] = gh._artifact(11, "junit-test-shard-1", _junit(case))
        code, _ = _collect(tmp_path / str(length), monkeypatch, gh)
        return code, tmp_path / str(length) / "history"

    _, probe = collect(1)
    [_, line] = (probe / f"history-{RUN}-1.jsonl").read_bytes().splitlines(keepends=True)
    length = 1 + report.MAX_LINE_BYTES + over - len(line)
    code, folder = collect(length)
    assert code == (0 if written else 1)
    if written:
        lines = (folder / f"history-{RUN}-1.jsonl").read_bytes().splitlines(keepends=True)
        assert len(lines[1]) == report.MAX_LINE_BYTES
        assert report.read(folder).suite_size == 1


def test_every_reader_holds_a_history_line_to_the_collectors_one_bound() -> None:
    from tools.ci import parity, report, shards

    assert telemetry.MAX_LINE_BYTES == 1024 * 1024
    assert report.MAX_LINE_BYTES == telemetry.MAX_LINE_BYTES
    assert parity.MAX_LINE_BYTES == telemetry.MAX_LINE_BYTES
    assert shards.MAX_HISTORY_LINE_BYTES == telemetry.MAX_LINE_BYTES


# ── whose upload is the record: the checks the shard planner and the weekly fetch share ───────

REPO = "owner/project"


def _collector(**changes: object) -> dict:
    run = {
        "id": 7,
        "path": telemetry.COLLECTOR_PATH,
        "event": "workflow_run",
        "head_branch": "main",
        "head_repository": {"full_name": REPO},
    }
    run.update(changes)
    return run


@pytest.mark.parametrize(
    ("changes", "events", "expected"),
    [
        pytest.param({}, telemetry.HISTORY_EVENTS, True, id="the-collectors-history-run"),
        pytest.param({"event": "schedule"}, telemetry.SLOTS_EVENTS, True, id="its-slots-run"),
        pytest.param(
            {"event": "workflow_dispatch"}, telemetry.SLOTS_EVENTS, True, id="a-dispatched-slots"
        ),
        pytest.param({"event": "schedule"}, telemetry.HISTORY_EVENTS, False, id="slots-as-history"),
        pytest.param({"event": "pull_request"}, telemetry.HISTORY_EVENTS, False, id="a-pr-run"),
        pytest.param(
            {"path": telemetry.MERGE_PATH}, telemetry.HISTORY_EVENTS, False, id="merge-yml"
        ),
        pytest.param({"head_branch": "feature-x"}, telemetry.HISTORY_EVENTS, False, id="a-branch"),
        pytest.param(
            {"head_repository": {"full_name": "someone/fork"}},
            telemetry.HISTORY_EVENTS,
            False,
            id="a-forks-run",
        ),
        pytest.param({"head_repository": None}, telemetry.HISTORY_EVENTS, False, id="no-repo"),
    ],
)
def test_only_the_collectors_own_run_on_main_is_the_record(
    changes: dict, events: frozenset, expected: bool
) -> None:
    """Any run can upload an artifact of any name, a fork's pull request included: only a
    ``ci-telemetry.yml`` run of its own events, on main, in this repository, made the record."""
    assert telemetry.collector_run(_collector(**changes), REPO, events) is expected


@pytest.mark.parametrize(
    ("made_by", "expired", "expected"),
    [
        pytest.param({}, False, None, id="unexpired-on-main-here"),
        pytest.param({}, True, "has expired", id="expired"),
        pytest.param(
            {"head_branch": "feature-x"}, False, "uploaded on 'feature-x', not main", id="a-branch"
        ),
        pytest.param({"head_repository_id": 2}, False, "by a fork's run", id="a-forks-run"),
        pytest.param({"id": "7"}, False, "names no run that uploaded it", id="no-run-id"),
    ],
)
def test_an_artifact_names_the_run_that_made_it_on_main_of_this_repository(
    made_by: dict, expired: bool, expected: str | None
) -> None:
    run = {"id": 7, "head_branch": "main", "repository_id": 1, "head_repository_id": 1}
    run.update(made_by)
    artifact = {"id": 70, "expired": expired, "workflow_run": run}
    why = telemetry.upload_refusal(artifact)
    assert why == expected if expected is None else expected in str(why)


@pytest.mark.parametrize(
    "artifact", [None, [], {"id": 70, "expired": False}], ids=["none", "a-list", "no-run"]
)
def test_what_is_not_an_artifact_naming_its_run_is_refused(artifact: object) -> None:
    assert telemetry.upload_refusal(artifact)


@pytest.mark.parametrize(
    ("name", "why"),
    [
        pytest.param("history-1-1.jsonl", None, id="a-file"),
        pytest.param("/history-1-1.jsonl", "an absolute path", id="absolute"),
        pytest.param("\\history-1-1.jsonl", "an absolute path", id="absolute-backslash"),
        pytest.param("C:history-1-1.jsonl", "an absolute path", id="a-drive-letter"),
        pytest.param("..", "a parent path", id="dot-dot"),
        pytest.param("a\\..\\history-1-1.jsonl", "a parent path", id="dot-dot-backslash"),
        pytest.param("a/history-1-1.jsonl", "under a folder", id="a-folder"),
        pytest.param("a/", "under a folder", id="is-a-folder"),
    ],
)
def test_a_member_is_refused_by_its_name_alone(name: str, why: str | None) -> None:
    got = telemetry.member_refusal(zipfile.ZipInfo(name))
    assert got == why if why is None else why in str(got)


def test_a_link_member_is_refused() -> None:
    info = zipfile.ZipInfo("history-1-1.jsonl")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert "is a link" in str(telemetry.member_refusal(info))


def test_the_planner_reads_the_shared_checks_and_limit() -> None:
    from tools.ci import shards

    assert shards.MAX_HISTORY_ARCHIVE_BYTES == telemetry.MAX_HISTORY_ARCHIVE_BYTES
    assert telemetry.MAX_HISTORY_ARCHIVE_BYTES == 32 * 1024 * 1024


# ── merge.yml's reports archives ──────────────────────────────────────────────────────────────
#
# merge.yml uploads each entry's reports folder as ``reports-<slug>-<attempt>``. Each
# ``junit-*.xml`` in its ``reports/`` (or at its root) is one report, named as the file.

MACOS = '<testcase classname="tests.test_m" name="test_mac" time="4.0"/>'


def _reports_archive(gh: FakeGh, ident: int, name: str, members: dict[str, bytes]) -> dict:
    artifact = gh._artifact(ident, name, b"")
    data = _zip_members(members)
    artifact.update(_data=data, size_in_bytes=len(data))
    return artifact


def _suite_archive(gh: FakeGh, ident: int, shard: int = 1) -> dict:
    return _reports_archive(
        gh,
        ident,
        f"reports-python-suite-{shard}-3-1",
        {
            f"reports/junit-test-shard-{shard}.xml": _junit(PASSED),
            f"reports/in-flight-test-shard-{shard}.txt": b"",
            "reports/state-leaks.json": b"{}",
            "reports/coverage.xml": _junit(FAILED),
            "reports/deeper/junit-not-this.xml": _junit(FAILED),
        },
    )


def test_a_merge_yml_runs_test_cases_reach_the_history(tmp_path: Path, monkeypatch) -> None:
    """The shard planner and the weekly report read them by report name, as before the flip."""
    gh = FakeGh()
    gh.artifacts = [
        _suite_archive(gh, 21),
        _reports_archive(gh, 22, "reports-web-unit-1", {"junit-web-unit.xml": _junit(MACOS)}),
    ]
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0
    tests = [line for line in lines if line["kind"] == "test"]
    assert sorted((t["report"], t["test"]) for t in tests) == [
        ("junit-test-shard-1", "tests.test_a::test_one"),
        ("junit-web-unit", "tests.test_m::test_mac"),
    ]
    assert lines[0]["cpus"] == {"junit-test-shard-1": None, "junit-web-unit": None}


def test_the_planner_reads_a_merge_yml_runs_seconds(tmp_path: Path, monkeypatch) -> None:
    from tools.ci import shards

    gh = FakeGh()
    gh.artifacts = [_suite_archive(gh, 21)]
    _collect(tmp_path, monkeypatch, gh)
    [written] = (tmp_path / "history").glob("*.jsonl")
    leg = shards.Leg.parse("test-shard:main-push:junit-test-shard-:3")
    history = shards.history_seconds(
        _zip(written.name, written.read_bytes()), leg, ["tests/test_a.py"], run=RUN
    )
    assert history.seconds == {(None, None): {"junit-test-shard-1": {"tests/test_a.py": 1.5}}}


def test_a_reports_archive_uploaded_twice_under_one_name_is_read_once(
    tmp_path: Path, monkeypatch
) -> None:
    gh = FakeGh()
    gh.artifacts = [_suite_archive(gh, 21), _suite_archive(gh, 23)]
    _, lines = _collect(tmp_path, monkeypatch, gh)
    assert [t["report"] for t in lines if t["kind"] == "test"] == ["junit-test-shard-1"]


def test_a_report_two_entries_write_is_read_from_each(tmp_path: Path, monkeypatch) -> None:
    """ui-quality's vitest wrote junit-web-unit.xml too (until #5529), in its own archive, which
    had the lower id: read once, its 10 cases hid web-unit's 15,640. Each is read, named by its
    archive."""
    gh = FakeGh()
    gh.artifacts = [
        _reports_archive(
            gh, 21, "reports-ui-quality-1", {"reports/junit-web-unit.xml": _junit(MACOS)}
        ),
        _reports_archive(
            gh, 22, "reports-web-unit-1", {"reports/junit-web-unit.xml": _junit(PASSED, FAILED)}
        ),
        _suite_archive(gh, 23),
    ]
    _, lines = _collect(tmp_path, monkeypatch, gh)
    reports = sorted({t["report"] for t in lines if t["kind"] == "test"})
    assert reports == [
        "junit-test-shard-1",
        "junit-web-unit@reports-ui-quality-1",
        "junit-web-unit@reports-web-unit-1",
    ]
    tests = [t for t in lines if t["kind"] == "test"]
    assert sum(1 for t in tests if t["report"] == "junit-web-unit@reports-web-unit-1") == 2
    assert set(lines[0]["cpus"]) == set(reports)


@pytest.mark.parametrize(
    ("size", "read"),
    [
        (telemetry.MAX_ARCHIVE_BYTES + 1, True),
        (telemetry.MAX_REPORTS_ARCHIVE_BYTES + 1, False),
    ],
    ids=["past-a-reports-own-bound", "past-a-reports-archives-bound"],
)
def test_a_reports_archive_has_a_bound_of_its_own(
    size: int, read: bool, tmp_path: Path, monkeypatch
) -> None:
    """It holds the entry's other output too, Playwright's traces among them."""
    gh = FakeGh()
    gh.artifacts = [_suite_archive(gh, 21)]
    gh.artifacts[0]["size_in_bytes"] = size
    code, _ = _collect(tmp_path, monkeypatch, gh)
    assert (code == 0) is read
    assert any("/artifacts/21/" in a for a in gh.asked) is read


# ── a tested push's suite (version 5) ─────────────────────────────────────────────────────────
#
# A push whose tree a green pull request run tested runs only the always-run jobs. Its history
# also takes that run's first attempt: the run tree-check names, by tree-check's own rules, from
# this repository and on the pull request's head.

HEAD = "c" * 40
OLD_HEAD = "d" * 40
OTHER = 600


def _tested(gh: FakeGh) -> FakeGh:
    """Pull request #7 merged as the pushed commit, its head with the pushed tree and holding the
    parent, and its one merge.yml run, 600, green on its first attempt."""
    gh.commits[HEAD] = _commit(TREE, PARENT)
    gh.pulls = [
        {
            "number": 7,
            "merge_commit_sha": PUSHED,
            "merged_at": "2026-10-18T08:50:00Z",
            "created_at": "2026-10-18T07:00:00Z",
            "base": {"ref": "main"},
            "head": {"sha": HEAD},
        }
    ]
    gh.head_runs = [
        {
            "id": OTHER,
            "run_number": 9,
            "head_sha": HEAD,
            "event": "pull_request",
            "created_at": "2026-10-18T08:00:00Z",
        }
    ]
    first = _attempt("2026-10-18T08:00:00Z", "2026-10-18T08:40:00Z", "success")
    suite = _reports_archive(
        gh,
        61,
        "reports-python-suite-macos-1-2-1",
        {"reports/junit-test-macos-1.xml": _junit(_hosted("macOS-26-arm64", "2", "M1"), MACOS)},
    )
    suite["created_at"] = "2026-10-18T08:30:00Z"
    gh.others[OTHER] = {
        "run": {
            "id": OTHER,
            "path": telemetry.MERGE_PATH,
            "event": "pull_request",
            "head_sha": HEAD,
            "head_repository": {"full_name": "o/r"},
        },
        "attempt": dict(first, status="completed"),
        "artifacts": [suite],
    }
    return gh


def test_a_tested_push_s_history_holds_the_pull_request_run_s_suite(
    tmp_path: Path, monkeypatch
) -> None:
    gh = _tested(FakeGh())
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0
    head = lines[0]
    assert head["tested_by"] == {
        "run_id": OTHER,
        "attempt": 1,
        "pull_request": 7,
        "head_sha": HEAD,
        "tree": TREE,
    }
    mine = [t for t in lines if t["kind"] == "test" and t["run_id"] == RUN]
    theirs = [t for t in lines if t["kind"] == "test" and t["run_id"] == OTHER]
    assert {t["report"] for t in mine} == {"junit-test-shard-1"}
    assert [(t["report"], t["test"], t["attempt"], t["sha"]) for t in theirs] == [
        ("junit-test-macos-1", "tests.test_a::test_one", 1, HEAD),
        ("junit-test-macos-1", "tests.test_m::test_mac", 1, HEAD),
    ]
    assert theirs[0]["platform"] == "macOS-26-arm64" and theirs[0]["workers"] == 2
    assert head["cpus"] == {"junit-test-shard-1": None, "junit-test-macos-1": "M1"}


def test_the_planner_reads_a_tested_push_s_suite(tmp_path: Path, monkeypatch) -> None:
    """The macOS leg's seconds, from the pull request run, under the push's own history."""
    from tools.ci import shards

    _collect(tmp_path, monkeypatch, _tested(FakeGh()))
    [written] = (tmp_path / "history").glob("*.jsonl")
    leg = shards.Leg.parse("test-macos:main-push:junit-test-macos-:2")
    history = shards.history_seconds(
        _zip(written.name, written.read_bytes()), leg, ["tests/test_m.py"], run=RUN
    )
    assert history.seconds == {
        ("macOS-26-arm64", 2): {"junit-test-macos-1": {"tests/test_m.py": 4.0}}
    }


def _their_record(gh: FakeGh, ident: int, key: str, text: str, **members: bytes) -> None:
    """A job record the tested run's first attempt uploaded, as ``run-job`` names it."""
    from tools.ci import verdict

    data = _zip_members(members or {"record.json": text.encode()})
    gh.others[OTHER]["artifacts"].append(
        {
            "id": ident,
            "name": verdict.record_artifact(key, run_id=OTHER, attempt=1),
            "size_in_bytes": len(data),
            "expired": False,
            "created_at": "2026-10-18T08:35:00Z",
            "_data": data,
        }
    )


E2E_FLAKE = "web/e2e/chat.spec.ts::it answers [chromium]"


def test_a_tested_push_s_history_holds_the_pull_request_run_s_records(
    tmp_path: Path, monkeypatch
) -> None:
    """Version 6: the flakes the merge verdict passed on the pull request run, Playwright's too,
    which no junit report carries, are in main's history beside the push's own records."""
    gh = _tested(FakeGh())
    _record_artifact(gh, 21, "lint", _job_record("lint", result="success"))
    _their_record(
        gh,
        62,
        "e2e-shard (1/2)",
        _job_record("e2e-shard (1/2)", tests=((E2E_FLAKE, "flake"),), run_id=OTHER),
    )
    _their_record(gh, 63, "suite (1/2)", _job_record("suite (1/2)", run_id=OTHER))
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0
    assert lines[0]["version"] == 6 and lines[0]["tested_by"]["run_id"] == OTHER
    assert [(r["key"], r["result"]) for r in _record_lines(lines)] == [("lint", "success")]
    tested = [line for line in lines if line["kind"] == telemetry.TESTED_RECORD]
    assert [set(t) for t in tested] == [set(telemetry.TESTED_RECORD_FIELDS)] * 2
    assert [(t["run_id"], t["attempt"], t["key"]) for t in tested] == [
        (OTHER, 1, "e2e-shard (1/2)"),
        (OTHER, 1, "suite (1/2)"),
    ]
    assert tested[0]["rerun"] == [{"test": E2E_FLAKE, "rerun_result": "flake"}]
    order = ["run", "record", telemetry.TESTED_RECORD, "test"]
    kinds = [line["kind"] for line in lines]
    assert kinds == sorted(kinds, key=order.index), "the run, its records, the tested run's, tests"


def test_a_tested_run_whose_record_cannot_be_read_keeps_neither_its_reports_nor_its_records(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    gh = _tested(FakeGh())
    _their_record(gh, 62, "e2e-shard (1/2)", "", **{"a.json": b"{}", "b.json": b"{}"})
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0
    assert lines[0]["tested_by"] is None
    assert not [line for line in lines if line["kind"] == telemetry.TESTED_RECORD]
    assert not [line for line in lines if line["kind"] == "test" and line["run_id"] == OTHER]
    assert "the tested pull request run's reports are not read" in capsys.readouterr().out


def test_the_planner_reads_a_version_6_history_with_tested_records(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.ci import shards

    gh = _tested(FakeGh())
    _their_record(gh, 62, "suite (1/2)", _job_record("suite (1/2)", run_id=OTHER))
    _collect(tmp_path, monkeypatch, gh)
    [written] = (tmp_path / "history").glob("*.jsonl")
    assert '"kind": "tested_record"' in written.read_text()
    leg = shards.Leg.parse("test-macos:main-push:junit-test-macos-:2")
    history = shards.history_seconds(
        _zip(written.name, written.read_bytes()), leg, ["tests/test_m.py"], run=RUN
    )
    assert history.seconds == {
        ("macOS-26-arm64", 2): {"junit-test-macos-1": {"tests/test_m.py": 4.0}}
    }


def _moved(gh: FakeGh) -> None:
    """The run tested an older head; the pull request's head now has the pushed tree."""
    gh.head_runs[0]["head_sha"] = OLD_HEAD
    gh.others[OTHER]["run"]["head_sha"] = OLD_HEAD


def _other_tree(gh: FakeGh) -> None:
    gh.commits[HEAD] = _commit("f" * 40, PARENT)


def _older_base(gh: FakeGh) -> None:
    gh.behind = 1


def _fork(gh: FakeGh) -> None:
    gh.others[OTHER]["run"]["head_repository"] = {"full_name": "someone/fork"}


def _unmerged(gh: FakeGh) -> None:
    gh.pulls[0]["merged_at"] = None


def _merged_as_another(gh: FakeGh) -> None:
    gh.pulls[0]["merge_commit_sha"] = "9" * 40


def _red_first(gh: FakeGh) -> None:
    gh.others[OTHER]["attempt"]["conclusion"] = "failure"


def _off_the_head(gh: FakeGh) -> None:
    """The listing names the run on the head; the run itself says otherwise."""
    gh.others[OTHER]["run"]["head_sha"] = OLD_HEAD


def _another_workflow(gh: FakeGh) -> None:
    gh.others[OTHER]["run"]["path"] = f"{telemetry.WORKFLOWS}/pr.yml"


@pytest.mark.parametrize(
    ("plant", "reason"),
    [
        (_moved, "no merge.yml pull_request run of #7's head"),
        (_other_tree, "the pushed tree is not #7's head tree"),
        (_older_base, "commit(s) behind the previous main"),
        (_fork, f"run {OTHER}'s head is another repository's"),
        (_unmerged, "no pull request into main was merged as this commit"),
        (_merged_as_another, "no pull request into main was merged as this commit"),
        (_red_first, "concluded failure"),
        (_off_the_head, f"run {OTHER} is not on #7's head"),
        (_another_workflow, f"run {OTHER} is not a merge.yml pull_request run"),
    ],
    ids=[
        "a-moved-head",
        "another-tree",
        "a-head-behind-main",
        "a-fork-s-run",
        "an-unmerged-pull-request",
        "merged-as-another-commit",
        "a-red-first-attempt",
        "a-run-off-the-head",
        "another-workflow-s-run",
    ],
)
def test_a_pull_request_run_that_did_not_test_the_pushed_tree_is_never_read(
    plant, reason: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    gh = _tested(FakeGh())
    plant(gh)
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and lines[0]["tested_by"] is None
    assert all(t["run_id"] == RUN for t in lines if t["kind"] == "test")
    assert not any(f"/runs/{OTHER}/artifacts" in a for a in gh.asked)
    assert reason in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rules", "reason"),
    [
        pytest.param(
            subprocess.CalledProcessError(1, ["gh", "api"], "", "HTTP 502"),
            "could not find out: GET /repos/o/r/rules/branches/main?per_page=100",
            id="the-read-fails",
        ),
        pytest.param(
            {"message": "Not Found"},
            "could not find out: main's rules are not a list",
            id="an-answer-that-is-not-a-list",
        ),
        pytest.param(
            [{"type": "merge_queue"}],
            "main's merges are queued, and no merge.yml merge_group run ran on the pushed commit",
            id="a-live-queue-and-no-queue-run",
        ),
    ],
)
def test_a_push_the_tree_check_cannot_vouch_for_by_main_s_rules_still_writes_its_history(
    rules: object, reason: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    """The tree check reads main's rules to learn whether its merges are queued (0.4.0 A1). A
    read that fails, or a queue the push bypassed, is its no: the push's history is still
    written, with its own reports only and ``tested_by`` null, and the log says why."""
    gh = _tested(FakeGh())
    gh.rules = rules
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and lines[0]["kind"] == "run" and lines[0]["tested_by"] is None
    assert {t["run_id"] for t in lines if t["kind"] == "test"} == {RUN}
    assert any(a.startswith("repos/o/r/rules/branches/main") for a in gh.asked)
    assert not any(f"/runs/{OTHER}" in a for a in gh.asked)
    assert f"no pull request run's reports are read for this push: {reason}" in (
        capsys.readouterr().out
    )


def test_every_report_name_the_collector_writes_is_ascii(tmp_path: Path, monkeypatch) -> None:
    """A report name goes into shells, URLs and greps: the separator a collision adds is the
    ASCII ``@``, and nothing else in a name is anything but ASCII."""
    gh = _tested(FakeGh())
    gh.artifacts = [
        gh._artifact(20, "junit-test-shard-2", _junit(PASSED)),
        _reports_archive(
            gh, 21, "reports-ui-quality-1", {"reports/junit-web-unit.xml": _junit(MACOS)}
        ),
        _reports_archive(
            gh, 22, "reports-web-unit-1", {"reports/junit-web-unit.xml": _junit(PASSED)}
        ),
    ]
    _, lines = _collect(tmp_path, monkeypatch, gh)
    names = {t["report"] for t in lines if t["kind"] == "test"} | set(lines[0]["cpus"])
    assert "junit-web-unit@reports-web-unit-1" in names and "junit-test-macos-1" in names
    assert all(name.isascii() for name in names), sorted(n for n in names if not n.isascii())


def test_a_report_the_push_holds_twice_still_wins_over_the_pull_request_run_s(
    tmp_path: Path, monkeypatch
) -> None:
    gh = _tested(FakeGh())
    gh.artifacts = [
        _reports_archive(
            gh, 21, "reports-ui-quality-1", {"reports/junit-web-unit.xml": _junit(MACOS)}
        ),
        _reports_archive(
            gh, 22, "reports-web-unit-1", {"reports/junit-web-unit.xml": _junit(PASSED)}
        ),
    ]
    theirs = _reports_archive(
        gh, 64, "reports-web-unit-1", {"reports/junit-web-unit.xml": _junit(PASSED, FAILED)}
    )
    theirs["created_at"] = "2026-10-18T08:30:00Z"
    gh.others[OTHER]["artifacts"].append(theirs)
    _, lines = _collect(tmp_path, monkeypatch, gh)
    assert not any(
        t["report"].startswith("junit-web-unit") and t["run_id"] == OTHER
        for t in lines
        if t["kind"] == "test"
    )


def test_the_push_s_own_report_wins_over_the_pull_request_run_s(
    tmp_path: Path, monkeypatch
) -> None:
    gh = _tested(FakeGh())
    shard = _suite_archive(gh, 62)
    shard["created_at"] = "2026-10-18T08:30:00Z"
    gh.others[OTHER]["artifacts"].append(shard)
    _, lines = _collect(tmp_path, monkeypatch, gh)
    by_report = {(t["report"], t["run_id"]) for t in lines if t["kind"] == "test"}
    assert by_report == {("junit-test-shard-1", RUN), ("junit-test-macos-1", OTHER)}


def test_only_the_pull_request_run_s_first_attempt_is_read(tmp_path: Path, monkeypatch) -> None:
    gh = _tested(FakeGh())
    later = _reports_archive(
        gh,
        63,
        "reports-python-suite-macos-2-2-1",
        {"reports/junit-test-macos-2.xml": _junit(MACOS)},
    )
    later["created_at"] = "2026-10-18T09:10:00Z"
    gh.others[OTHER]["artifacts"].append(later)
    _, lines = _collect(tmp_path, monkeypatch, gh)
    assert "junit-test-macos-2" not in {t["report"] for t in lines if t["kind"] == "test"}


def test_a_pull_request_run_whose_reports_cannot_be_read_leaves_the_push_s_own(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    gh = _tested(FakeGh())
    gh.others[OTHER]["artifacts"][0]["_data"] = b"not a zip"
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and lines[0]["tested_by"] is None
    assert {t["run_id"] for t in lines if t["kind"] == "test"} == {RUN}
    assert "the tested pull request run's reports are not read" in capsys.readouterr().out


def test_a_breadth_run_looks_for_no_pull_request(tmp_path: Path, monkeypatch) -> None:
    gh = FakeGh(_run(name="Breadth", path=telemetry.BREADTH_PATH, event="schedule"))
    code, lines = _collect(tmp_path, monkeypatch, gh)
    assert code == 0 and lines[0]["tested_by"] is None
    assert not any("/commits/" in a or "/pulls" in a for a in gh.asked)
