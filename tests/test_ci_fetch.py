"""``python -m tools.ci fetch``: the weekly report's window, fetched from the record (#3948).

Each case serves ``gh api`` from a stand-in that holds the collector's runs and their artifacts,
the CI and Breadth runs the histories came from, and the window a previous weekly run carried;
it counts every call against a rate budget and runs the fetch on it. Only the collector's own
uploads are fetched, by the checks the shard planner shares (``tools/ci/telemetry.py``); every
history, fetched or carried, is checked again against the run it came from; the window is one
stored archive, built member by member and never unpacked to disk; and the report then reads it.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import re
import stat
import sys
import warnings
import zipfile
from pathlib import Path

import pytest

from tools.ci import fetch, flaky_issues, report, telemetry, workflows

REPO = "owner/project"
NOW = datetime.datetime(2026, 10, 26, 8, 13, tzinfo=datetime.UTC)


def _iso(at: datetime.datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


#: Every fixture member's time: an archive built twice is the same bytes, whatever the clock says.
_STAMP = (2026, 10, 26, 8, 13, 0)


def _info(name: str, compress: int = zipfile.ZIP_STORED) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_STAMP)
    info.compress_type = compress
    return info


def _history_lines(ci_run: int, days_ago: float, *, attempt: int = 1, **head: object) -> bytes:
    """A history as the collector writes one: its run line a push to main of merge.yml unless
    ``head`` says otherwise, and two test lines."""
    created = _iso(NOW - datetime.timedelta(days=days_ago))
    line = dict.fromkeys(telemetry.RUN_FIELDS)
    line.update(kind="run", schema=telemetry.SCHEMA, version=telemetry.VERSION, run_id=ci_run)
    line.update(attempt=attempt, created_at=created, sha=f"{ci_run:040x}", jobs=[])
    line.update(workflow=workflows.MERGE, event="push", branch="main")
    line.update(head)
    lines = [line]
    for i in range(2):
        test = dict.fromkeys(telemetry.TEST_FIELDS)
        test.update(kind="test", run_id=ci_run, attempt=attempt, test=f"t::{i}", seconds=1.0)
        test.update(first_result="passed", report="junit-test-shard-1")
        lines.append(test)
    return "".join(json.dumps(x) + "\n" for x in lines).encode()


def _history_zip(ci_run: int, days_ago: float, *, attempt: int = 1, **head: object) -> bytes:
    """A history archive as the collector uploads one: its one ``history-<run>-<attempt>.jsonl``."""
    data = _history_lines(ci_run, days_ago, attempt=attempt, **head)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(_info(f"history-{ci_run}-{attempt}.jsonl", zipfile.ZIP_DEFLATED), data)
    return buffer.getvalue()


def _slots_line(at: datetime.datetime) -> bytes:
    head = {"kind": "slots", "schema": telemetry.SCHEMA, "version": telemetry.VERSION}
    head["at"] = _iso(at)
    return (json.dumps(head) + "\n").encode()


def _slots_zip(at: datetime.datetime) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(_info(f"slots-{at:%Y-%m-%dT%H-%M}.jsonl"), _slots_line(at))
    return buffer.getvalue()


def _zip(members: dict[str, bytes], *, links: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zipped:
        for name, data in members.items():
            info = _info(name)
            if name in links:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipped.writestr(info, data)
    return buffer.getvalue()


def _page(path: str, key: str, items: list[dict], short_by: int = 0) -> str:
    page = int(re.search(r"[?&]page=(\d+)", path).group(1))  # type: ignore[union-attr]
    chunk = items[(page - 1) * 100 : page * 100]
    return json.dumps({"total_count": len(items) + short_by, key: chunk})


class Gh:
    """``gh api`` as the fetch calls it, every call counted against the rate budget."""

    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.sources: dict[str, list[dict]] = {name: [] for name in telemetry.COUNTED}
        self.artifacts: dict[int, list[dict]] = {}
        self.data: dict[int, bytes] = {}
        self.carried: list[dict] = []
        self.own_runs: dict[int, dict] = {}
        self.remaining = 5000
        #: What rate_limit answers instead of the count, when set.
        self.rate_limit: str | None = None
        self.asked: list[str] = []
        #: Every artifact the fetch asked to download, and those it was served.
        self.requested: list[int] = []
        self.downloads: list[int] = []
        self.short_by = 0

    def collector(self, run_id: int, event: str, days_ago: float, **changes: object) -> dict:
        run = {
            "id": run_id,
            "path": telemetry.COLLECTOR_PATH,
            "event": event,
            "head_branch": "main",
            "head_repository": {"full_name": REPO},
            "conclusion": "success",
            "created_at": _iso(NOW - datetime.timedelta(days=days_ago)),
        }
        run.update(changes)
        self.runs.append(run)
        self.runs.sort(key=lambda r: r["created_at"], reverse=True)
        return run

    def source(self, run_id: int, days_ago: float, workflow: str = workflows.MERGE, **changes):
        """The CI, Merge or Breadth run a history came from, as its workflow's listing shows it."""
        run = {
            "id": run_id,
            "path": f"{telemetry.WORKFLOWS}/{workflow}",
            "event": "push" if workflow in workflows.MAIN_PUSH else "schedule",
            "head_branch": "main",
            "head_repository": {"full_name": REPO},
            "run_attempt": 1,
            "created_at": _iso(NOW - datetime.timedelta(days=days_ago)),
        }
        run.update(changes)
        self.sources[workflow].append(run)

    def artifact(self, run_id: int, name: str, data: bytes, **changes: object) -> dict:
        ident = 1000 + len(self.data)
        made_by = {"id": run_id, "head_branch": "main", "repository_id": 1, "head_repository_id": 1}
        made_by.update(changes.pop("workflow_run", {}))  # type: ignore[arg-type]
        artifact = {
            "id": ident,
            "name": name,
            "size_in_bytes": len(data),
            "expired": False,
            "created_at": _iso(NOW),
            "workflow_run": made_by,
        }
        artifact.update(changes)
        self.data[ident] = data
        self.artifacts.setdefault(run_id, []).append(artifact)
        return artifact

    def history(self, run_id: int, ci_run: int, days_ago: float, **head: object) -> None:
        """A collector run, the history it uploaded, and the CI run it came from."""
        self.collector(run_id, "workflow_run", days_ago)
        self.artifact(
            run_id, f"telemetry-history-{ci_run}-1", _history_zip(ci_run, days_ago, **head)
        )
        self.source(ci_run, days_ago)

    def text(self, *args: str) -> str:
        assert args[0] == "api", args
        path = args[1]
        if path == "rate_limit":
            if self.rate_limit is not None:
                return self.rate_limit
            return json.dumps({"resources": {"core": {"remaining": self.remaining}}})
        self.remaining -= 1
        self.asked.append(path)
        if "/actions/workflows/ci-telemetry.yml/runs?" in path:
            return _page(path, "workflow_runs", self.runs, self.short_by)
        for workflow, runs in self.sources.items():
            if f"/actions/workflows/{workflow}/runs?" in path:
                return _page(path, "workflow_runs", runs)
        if m := re.search(r"/actions/runs/(\d+)/artifacts\?", path):
            listed = self.artifacts.get(int(m.group(1)), [])
            return json.dumps({"total_count": len(listed), "artifacts": listed})
        if m := re.search(r"/actions/artifacts\?name=([^&]+)&", path):
            named = [a for a in self.carried if a["name"] == m.group(1)]
            return json.dumps({"total_count": len(named), "artifacts": named})
        if m := re.search(r"/actions/runs/(\d+)$", path):
            return json.dumps(self.own_runs[int(m.group(1))])
        raise AssertionError(f"the fetch asked gh for {args}")

    def download(self, path: str, dest: Path, limit: int) -> None:
        self.remaining -= 1
        ident = int(re.search(r"/artifacts/(\d+)/zip$", path).group(1))  # type: ignore[union-attr]
        self.requested.append(ident)
        data = self.data[ident]
        if len(data) > limit:
            raise fetch.FetchError(f"artifact {ident} passes {limit} bytes")
        self.downloads.append(ident)
        dest.write_bytes(data)


def _fetch(gh: Gh, out: Path, **more: object) -> fetch.Fetched:
    options = {"days": 91, "reserve": 100, "carry": True, "now": NOW}
    options.update(more)
    run = fetch.fetch
    return run(REPO, out, text=gh.text, download=gh.download, **options)  # type: ignore[arg-type]


def _members(out: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(out / fetch.WINDOW) as window:
        return {i.filename: window.read(i) for i in window.infolist()}


def _status(out: Path) -> dict:
    return json.loads((out / fetch.STATUS).read_text("utf-8"))


def _gh_with_three_histories() -> Gh:
    gh = Gh()
    for run, ci, days in ((30, 300, 1), (20, 200, 3), (10, 100, 5)):
        gh.history(run, ci, days)
    gh.collector(25, "schedule", 2)
    gh.artifact(25, "telemetry-slots-25", _slots_zip(NOW - datetime.timedelta(days=2)))
    gh.collector(15, "schedule", 4)
    gh.artifact(15, "telemetry-slots-15", _slots_zip(NOW - datetime.timedelta(days=4)))
    return gh


HISTORIES = [
    "telemetry-history-100-1.zip",
    "telemetry-history-200-1.zip",
    "telemetry-history-300-1.zip",
]


# ── the window, fetched ───────────────────────────────────────────────────────────────────────


def _refused(out: Path, *parts: str) -> bool:
    """Whether one of the fetch's refusals says every one of ``parts``."""
    return any(all(p in why for p in parts) for why in _status(out)["refusals"])


def test_the_window_is_every_collector_history_and_the_newest_slots_in_one_archive(
    tmp_path: Path,
) -> None:
    gh = _gh_with_three_histories()
    out = tmp_path / "window"
    got = _fetch(gh, out)
    members = _members(out)
    assert sorted(members) == sorted([*HISTORIES, "telemetry-slots-25.zip", fetch.MANIFEST])
    assert members["telemetry-history-300-1.zip"] == _history_zip(300, 1)
    with zipfile.ZipFile(out / fetch.WINDOW) as window:
        assert {i.compress_type for i in window.infolist()} == {zipfile.ZIP_STORED}
    assert sorted(p.name for p in out.iterdir()) == sorted([fetch.WINDOW, fetch.STATUS])
    assert (got.fetched, got.left, got.refused) == (3, 0, 0)
    assert report.read(out).window["runs"] == 3
    assert _status(out)["carry"] == "none: no window was carried"
    # The collector's runs are listed from 91 days back: 2026-07-27, for a run on 2026-10-26.
    listing = [a for a in gh.asked if "/workflows/ci-telemetry.yml/runs?" in a]
    assert listing and all("created=%3E%3D2026-07-27" in a for a in listing), listing


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        pytest.param(
            {"event": "pull_request"},
            "pull_request, main, owner/project) is not the collector's own run on main",
            id="a-pr-run",
        ),
        pytest.param(
            {"path": telemetry.MERGE_PATH}, "is not the collector's own run", id="another-workflow"
        ),
        pytest.param(
            {"head_branch": "feature-x"},
            "workflow_run, feature-x, owner/project) is not the collector's own",
            id="another-branch",
        ),
        pytest.param(
            {"head_repository": {"full_name": "someone/fork"}},
            "workflow_run, main, someone/fork) is not the collector's own",
            id="a-forks-run",
        ),
        pytest.param({"conclusion": "failure"}, "concluded 'failure'", id="a-failed-run"),
        pytest.param(
            # A listing that does not honour its created= filter: the run is still not read.
            {"created_at": _iso(NOW - datetime.timedelta(days=92))},
            "was not made in the window",
            id="made-before-the-window",
        ),
        pytest.param(
            {"created_at": "yesterday"}, "was not made in the window", id="made-at-no-time"
        ),
    ],
)
def test_only_the_collectors_own_successful_runs_in_the_window_are_read(
    tmp_path: Path, changes: dict, why: str
) -> None:
    gh = Gh()
    changes = dict(changes)
    gh.collector(30, changes.pop("event", "workflow_run"), 1, **changes)
    gh.artifact(30, "telemetry-history-300-1", _history_zip(300, 1))
    gh.source(300, 1)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.fetched == 0 and gh.requested == []
    assert not any("/runs/30/artifacts" in a for a in gh.asked)
    assert _refused(out, "collector run 30 ", why), _status(out)


_NOT_UPLOADED = [
    pytest.param({"workflow_run": {"id": 99}}, "was uploaded by run 99", id="another-runs-upload"),
    pytest.param({"workflow_run": {"head_repository_id": 2}}, "a fork's run", id="a-forks-upload"),
    pytest.param(
        {"workflow_run": {"head_branch": "feature-x"}}, "'feature-x', not main", id="another-branch"
    ),
    pytest.param({"workflow_run": {"id": "30"}}, "names no run", id="no-run-id"),
    pytest.param({"expired": True}, "has expired", id="expired"),
    pytest.param({"name": "-x"}, "is not named as the collector names", id="another-name"),
    pytest.param(
        {"size_in_bytes": telemetry.MAX_HISTORY_ARCHIVE_BYTES + 1}, "is listed at", id="too-large"
    ),
]


@pytest.mark.parametrize(("changes", "why"), _NOT_UPLOADED)
@pytest.mark.parametrize("kind", ["history", "slots"])
def test_an_artifact_the_collector_run_did_not_upload_is_never_downloaded_and_says_why(
    tmp_path: Path, changes: dict, why: str, kind: str
) -> None:
    gh = Gh()
    changes = dict(changes)
    if kind == "history":
        run, name, data = 30, "telemetry-history-300-1", _history_zip(300, 1)
        gh.collector(30, "workflow_run", 1)
        gh.source(300, 1)
    else:
        run, name, data = 25, "telemetry-slots-25", _slots_zip(NOW - datetime.timedelta(days=2))
        gh.collector(25, "schedule", 2)
    gh.artifact(run, name + changes.pop("name", ""), data, **changes)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.fetched, got.slots, gh.requested) == (0, None, [])
    assert _refused(out, f"collector run {run}: artifact 1000 ", why), _status(out)


def test_a_collector_run_that_lists_no_artifact_says_so(tmp_path: Path) -> None:
    gh = Gh()
    gh.collector(30, "workflow_run", 1)
    gh.collector(25, "schedule", 2)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.fetched, got.refused, got.slots) == (0, 1, None)
    assert _refused(out, "collector run 30 lists no artifact")
    assert _refused(out, "collector run 25 lists no artifact")


def test_an_archive_over_the_limit_is_never_downloaded_nor_kept(tmp_path: Path) -> None:
    gh = Gh()
    gh.history(30, 300, 1)
    gh.artifacts[30][0]["size_in_bytes"] = 33
    gh.history(20, 200, 2)
    gh.artifacts[20][0]["size_in_bytes"] = 10  # it says it is small, and is not
    out = tmp_path / "window"
    got = _fetch(gh, out, archive_limit=32)
    assert got.fetched == 0 and set(_members(out)) == {fetch.MANIFEST}
    # As listed, run 30's is never asked for; as it arrives, run 20's is stopped at the limit.
    assert gh.requested == [gh.artifacts[20][0]["id"]] and gh.downloads == []
    assert _refused(out, "collector run 30: ", "is listed at 33 bytes")
    assert _refused(out, "telemetry-history-200-1.zip: ", "passes 32 bytes")


#: A merge queue commit's ref for main, as a queue run's branch.
QUEUE_REF = f"gh-readonly-queue/main/pr-5801-{'c' * 40}"


def test_a_queue_run_s_history_is_fetched_from_its_own_listing(tmp_path: Path) -> None:
    """A queue run is on the queue's ref, which no branch filter names: merge.yml's runs are
    listed by that event too, and its history is kept like a push's."""
    gh = Gh()
    gh.collector(30, "workflow_run", 1)
    head = {"event": "merge_group", "branch": QUEUE_REF}
    gh.artifact(30, "telemetry-history-300-1", _history_zip(300, 1, **head))
    gh.source(300, 1, event="merge_group", head_branch=QUEUE_REF)
    got = _fetch(gh, tmp_path / "window")
    assert (got.fetched, got.refused) == (1, 0), _status(tmp_path / "window")
    listed = [a for a in gh.asked if f"/workflows/{workflows.MERGE}/runs?" in a]
    assert any("branch=main" in a for a in listed) and any("event=merge_group" in a for a in listed)
    assert not any(
        "event=merge_group" in a for a in gh.asked if "/workflows/breadth.yml/runs?" in a
    ), "breadth is no workflow the queue runs"


@pytest.mark.parametrize(
    ("head", "source", "why"),
    [
        pytest.param({}, None, "run 300 attempt 1 is not a run", id="its-ci-run-is-not-listed"),
        pytest.param({"event": "pull_request"}, {}, "is not a counted run", id="a-pr-event"),
        pytest.param({"branch": "feature-x"}, {}, "is not a counted run", id="another-branch"),
        pytest.param({"workflow": "nix.yml"}, {}, "is not a counted run", id="another-workflow"),
        pytest.param(
            {"event": "merge_group"}, {}, "is not a counted run", id="a-queue-run-on-main"
        ),
        pytest.param(
            {"event": "merge_group", "branch": "gh-readonly-queue/main/pr-1-ccccccc"},
            {},
            "is not a counted run",
            id="a-queue-run-on-a-short-ref",
        ),
        pytest.param({"branch": QUEUE_REF}, {}, "is not a counted run", id="a-push-on-a-queue-ref"),
        pytest.param({"run_id": 301}, {}, "describes run 301", id="another-runs-history"),
        pytest.param({"schema": "another"}, {}, "not a", id="another-schema"),
        pytest.param({"version": 99}, {}, "not a", id="a-version-it-does-not-read"),
        pytest.param({"kind": "test"}, {}, "not a", id="its-first-line-is-no-run-line"),
        pytest.param(
            {"attempt": 2}, {}, "run 300 attempt 2 is not a run", id="an-attempt-not-listed"
        ),
        pytest.param(
            {},
            {"head_repository": {"full_name": "someone/fork"}},
            "is not a run its workflow's listing shows",
            id="listed-as-a-forks-run",
        ),
        pytest.param(
            {},
            {"event": "pull_request"},
            "is not a run its workflow's listing shows",
            id="listed-as-a-pr-run",
        ),
        pytest.param(
            {},
            {"head_branch": "feature-x"},
            "is not a run its workflow's listing shows",
            id="listed-on-another-branch",
        ),
        pytest.param(
            {"event": "merge_group", "branch": QUEUE_REF},
            {"event": "merge_group", "head_branch": f"gh-readonly-queue/release/pr-1-{'c' * 40}"},
            "is not a run its workflow's listing shows",
            id="a-queue-run-listed-for-another-base",
        ),
        pytest.param(
            {},
            {"path": f"{telemetry.WORKFLOWS}/other.yml"},
            "is not a run its workflow's listing shows",
            id="listed-as-another-workflows-run",
        ),
    ],
)
def test_a_history_whose_source_run_cannot_be_shown_is_refused(
    tmp_path: Path, head: dict, source: dict | None, why: str
) -> None:
    """A history is kept only when its run line names a counted run (a push to main of
    merge.yml, or a scheduled or dispatched Breadth run on main) that the workflow's own listing
    shows, in this repository, with that attempt, and the archive is named after that run and
    attempt."""
    gh = Gh()
    gh.collector(30, "workflow_run", 1)
    attempt = head.get("attempt", 1)
    gh.artifact(30, f"telemetry-history-300-{attempt}", _history_zip(300, 1, **head))
    if source is not None:
        gh.source(300, 1, **source)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.fetched, got.refused) == (0, 1)
    assert set(_members(out)) == {fetch.MANIFEST}
    assert _refused(out, why), _status(out)


@pytest.mark.parametrize(
    ("archive", "why"),
    [
        pytest.param(
            _zip(
                {
                    "history-300-1.jsonl": _history_lines(300, 1),
                    "history-999-1.jsonl": _history_lines(999, 1, event="pull_request"),
                }
            ),
            "not history-300-1.jsonl alone",
            id="a-second-member",
        ),
        pytest.param(
            _zip({"history-300-1.jsonl": _history_lines(300, 1, attempt=2)}),
            "describes run 300 attempt 2",
            id="its-line-names-another-attempt",
        ),
        pytest.param(
            _zip({"history-300-1.jsonl": _history_lines(300, 1)}, links=("history-300-1.jsonl",)),
            "'history-300-1.jsonl' is a link",
            id="its-member-is-a-link",
        ),
        pytest.param(_zip({}), "not history-300-1.jsonl alone", id="an-empty-archive"),
        pytest.param(b"PK\x03\x04broken", "cannot be read", id="not-an-archive"),
    ],
)
def test_a_history_archive_not_in_its_one_shape_is_refused(
    tmp_path: Path, archive: bytes, why: str
) -> None:
    """A history archive holds ``history-<run>-<attempt>.jsonl`` alone, a file, its run line
    naming that run and attempt: the report reads every history member of every archive in the
    window, so one that got past the fetch would be read unchecked."""
    gh = Gh()
    gh.collector(30, "workflow_run", 1)
    gh.artifact(30, "telemetry-history-300-1", archive)
    gh.source(300, 1)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.fetched, got.refused) == (0, 1)
    assert set(_members(out)) == {fetch.MANIFEST}
    assert _refused(out, "telemetry-history-300-1.zip", why), _status(out)


def test_a_history_past_its_bound_unpacked_is_refused(tmp_path: Path, monkeypatch) -> None:
    gh = Gh()
    gh.history(30, 300, 1)
    monkeypatch.setattr(report, "MAX_FILE_BYTES", 100)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.fetched, got.refused) == (0, 1)
    assert _refused(out, "telemetry-history-300-1.zip: history-300-1.jsonl passes 100 bytes")


@pytest.mark.parametrize(
    "members",
    [
        {"slots-2026-10-24T08-13.jsonl": b"{}\n", "slots-2026-10-25T08-13.jsonl": b"{}\n"},
        {"history-300-1.jsonl": b"{}\n"},
        {},
    ],
    ids=["two-slots-files", "not-a-slots-file", "an-empty-archive"],
)
def test_a_slots_archive_not_of_one_slots_file_is_refused(tmp_path: Path, members: dict) -> None:
    gh = Gh()
    gh.collector(25, "schedule", 2)
    gh.artifact(25, "telemetry-slots-25", _zip(members))
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.slots is None and set(_members(out)) == {fetch.MANIFEST}
    assert _refused(out, "telemetry-slots-25.zip does not hold one slots file"), _status(out)


def test_a_breadth_history_is_shown_by_breadths_own_listing(tmp_path: Path) -> None:
    gh = Gh()
    gh.collector(30, "workflow_run", 1)
    gh.artifact(
        30,
        "telemetry-history-300-1",
        _history_zip(300, 1, workflow="breadth.yml", event="schedule"),
    )
    gh.source(300, 1, workflow="breadth.yml")
    assert _fetch(gh, tmp_path / "window").fetched == 1


def test_a_history_of_the_deleted_ci_yml_is_refused_before_its_download(tmp_path: Path) -> None:
    """The flip deleted ci.yml (#3915): the fetch lists no run of it, so no listing shows the run
    a history of it names, and the archive is refused by its name, never downloaded."""
    gh = Gh()
    gh.collector(30, "workflow_run", 1)
    gh.artifact(30, "telemetry-history-300-1", _history_zip(300, 1, workflow="ci.yml"))
    out = tmp_path / "window"
    assert _fetch(gh, out).refused == 1
    assert _refused(out, "run 300 attempt 1 is not a run its workflow's listing shows")
    assert gh.requested == [], gh.requested
    assert not any("/actions/workflows/ci.yml/" in a for a in gh.asked), gh.asked


@pytest.mark.parametrize(
    ("archive", "source"),
    [
        pytest.param("telemetry-history-300-1", None, id="its-run-is-not-listed"),
        pytest.param("telemetry-history-300-2", {}, id="its-attempt-is-not-listed"),
    ],
)
def test_a_history_no_listing_shows_is_refused_before_its_download(
    tmp_path: Path, archive: str, source: dict | None, monkeypatch
) -> None:
    """The run and attempt a history archive's name gives are checked against the listings before
    it is downloaded: the check ``history_refusal`` makes after the download, made first, so an
    archive it would refuse costs no download. Without the early check the archive is downloaded
    and refused in the same words."""
    attempt = int(archive.rsplit("-", 1)[1])

    def one() -> Gh:
        gh = Gh()
        gh.collector(30, "workflow_run", 1)
        gh.artifact(30, archive, _history_zip(300, 1, attempt=attempt))
        if source is not None:
            gh.source(300, 1, **source)
        return gh

    words = f"run 300 attempt {attempt} is not a run its workflow's listing shows"
    gh = one()
    got = _fetch(gh, tmp_path / "early")
    assert (got.fetched, got.refused) == (0, 1) and gh.requested == []
    assert _refused(tmp_path / "early", f"{archive}.zip: {words}"), _status(tmp_path / "early")
    monkeypatch.setattr(fetch, "_unshown", lambda artifact, shown: None)
    gh = one()
    got = _fetch(gh, tmp_path / "late")
    assert (got.fetched, got.refused) == (0, 1) and len(gh.requested) == 1
    assert _refused(tmp_path / "late", f"{archive}.zip: {words}"), _status(tmp_path / "late")


def test_a_second_archive_of_one_name_is_refused(tmp_path: Path) -> None:
    """The report opens a window member by its name, so the window never holds a name twice: of
    two collector runs that uploaded one history, the newer's is kept."""
    gh = Gh()
    gh.history(30, 300, 1)
    gh.collector(29, "workflow_run", 1.5)
    gh.artifact(29, "telemetry-history-300-1", _history_zip(300, 1))
    out = tmp_path / "window"
    got = _fetch(gh, out)
    with zipfile.ZipFile(out / fetch.WINDOW) as window:
        names = [i.filename for i in window.infolist()]
    assert sorted(names) == sorted(["telemetry-history-300-1.zip", fetch.MANIFEST])
    assert (got.fetched, got.refused) == (1, 1)
    assert json.loads(_members(out)[fetch.MANIFEST])["30"]["archive"] in names
    assert _refused(out, "telemetry-history-300-1.zip: the window holds it already")


def test_a_listing_short_of_its_count_fails_the_fetch(tmp_path: Path) -> None:
    gh = _gh_with_three_histories()
    gh.short_by = 1
    with pytest.raises(fetch.FetchError, match="listed"):
        _fetch(gh, tmp_path / "window")


def test_the_fetch_stops_at_its_reserve_and_says_what_it_left(tmp_path: Path) -> None:
    """The token's calls are the repository's, shared with every other workflow, so the fetch
    keeps a reserve: newest first, and what it could not fetch it counts, for the next run."""
    gh = _gh_with_three_histories()
    gh.remaining = 100 + 9  # six calls list the runs and the slots before the first history
    out = tmp_path / "window"
    got = _fetch(gh, out, reserve=100)
    assert got.fetched < 3 and got.fetched + got.left == 3
    assert gh.remaining >= 100
    kept = sorted(n for n in _members(out) if n.startswith("telemetry-history-"))
    assert kept and kept[-1] == "telemetry-history-300-1.zip"
    assert _status(out)["left"] == got.left


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps({"message": "not what was asked"}),
        json.dumps({"resources": {"core": {"remaining": "5000"}}}),
        "<html>",
    ],
    ids=["no-count", "a-count-as-text", "not-json"],
)
def test_a_count_the_fetch_cannot_read_is_a_spent_budget(tmp_path: Path, answer: str) -> None:
    """The budget fails closed: when rate_limit does not say how many calls are left, no history
    is fetched, each is left for the next run, and the status says why."""
    gh = _gh_with_three_histories()
    gh.rate_limit = answer
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.fetched, got.left) == (0, 3)
    assert not any("/runs/30/artifacts" in a or "/runs/20/artifacts" in a for a in gh.asked)
    assert _status(out)["refusals"][0].startswith("the rate limit does not say"), _status(out)


@pytest.mark.parametrize("cap", ["members", "bytes"])
def test_the_window_being_built_stops_at_its_caps(tmp_path: Path, monkeypatch, cap: str) -> None:
    """Both caps hold as the window is built, with a place kept for the manifest: what does not
    fit is refused and named, never written."""
    gh = _gh_with_three_histories()
    slots, newest = gh.artifacts[25][0], gh.artifacts[30][0]
    if cap == "members":
        monkeypatch.setattr(telemetry, "MAX_WINDOW_MEMBERS", 3)
        why = "the window holds 3 members"
    else:
        room = slots["size_in_bytes"] + newest["size_in_bytes"] + fetch.MAX_MANIFEST_BYTES
        monkeypatch.setattr(telemetry, "MAX_WINDOW_BYTES", room)
        why = f"the window passes {room} bytes"
    out = tmp_path / "window"
    got = _fetch(gh, out)
    members = _members(out)
    assert sorted(members) == sorted(
        ["telemetry-slots-25.zip", "telemetry-history-300-1.zip", fetch.MANIFEST]
    )
    assert len(members) <= telemetry.MAX_WINDOW_MEMBERS
    assert sum(len(v) for v in members.values()) <= telemetry.MAX_WINDOW_BYTES
    assert report.read(out).window["runs"] == 1, "the report refuses the window the fetch built"
    assert (got.fetched, got.refused) == (1, 2)
    assert _refused(out, f"telemetry-history-200-1.zip: {why}"), _status(out)
    assert _refused(out, f"telemetry-history-100-1.zip: {why}"), _status(out)


# ── a download, as it arrives ─────────────────────────────────────────────────────────────────


def _stand_in_gh(tmp_path: Path, monkeypatch, body: str) -> None:
    """A ``gh`` on PATH that runs ``body``: what the real download streams from."""
    folder = tmp_path / "bin"
    folder.mkdir()
    gh = folder / "gh"
    gh.write_text(f"#!{sys.executable}\nimport sys\n{body}\n", encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


def test_a_download_is_stopped_at_its_limit_as_it_arrives(tmp_path: Path, monkeypatch) -> None:
    _stand_in_gh(
        tmp_path,
        monkeypatch,
        "try:\n"
        "    for _ in range(256):\n"
        "        sys.stdout.buffer.write(b'x' * 65536)\n"
        "        sys.stdout.buffer.flush()\n"
        "except BrokenPipeError:\n"
        "    pass",
    )
    dest = tmp_path / "download.zip"
    with pytest.raises(fetch.FetchError, match="passes 100000 bytes"):
        fetch._gh_download("repos/o/p/actions/artifacts/1/zip", dest, 100_000)
    assert dest.stat().st_size <= 100_000


def test_a_download_within_its_limit_is_written_whole(tmp_path: Path, monkeypatch) -> None:
    _stand_in_gh(tmp_path, monkeypatch, "sys.stdout.buffer.write(b'x' * 5000)")
    dest = tmp_path / "download.zip"
    fetch._gh_download("repos/o/p/actions/artifacts/1/zip", dest, 100_000)
    assert dest.read_bytes() == b"x" * 5000


def test_a_download_gh_fails_fails(tmp_path: Path, monkeypatch) -> None:
    _stand_in_gh(tmp_path, monkeypatch, "sys.stdout.buffer.write(b'x')\nsys.exit(3)")
    with pytest.raises(fetch.FetchError, match="exited 3"):
        fetch._gh_download("repos/o/p/actions/artifacts/1/zip", tmp_path / "d.zip", 100_000)


# ── the window carried from the previous weekly run ───────────────────────────────────────────


def _carry(
    gh: Gh,
    members: dict[str, bytes] | bytes,
    *,
    links: tuple[str, ...] = (),
    beside: dict[str, bytes] | None = None,
    carrier: str = "flake-report",
    **run: object,
) -> None:
    """The artifact a previous run of ``carrier``'s workflow uploaded (the weekly one unless it
    says otherwise): upload-artifact's archive, holding the one window archive the fetch built
    (``members``, or that archive as it is), and ``beside`` it whatever else the artifact holds."""
    name, path, _ = fetch.CARRIES[carrier]
    own = {
        "id": 77,
        "path": path,
        "event": "schedule",
        "head_branch": "main",
        "head_repository": {"full_name": REPO},
    }
    own.update(run)
    gh.own_runs[77] = own
    window = members if isinstance(members, bytes) else _zip(members, links=links)
    data = _zip({fetch.WINDOW: window, **(beside or {})})
    ident = 5000 + len(gh.carried)
    gh.data[ident] = data
    gh.carried.append(
        {
            "id": ident,
            "name": name,
            "size_in_bytes": len(data),
            "expired": False,
            "created_at": _iso(NOW - datetime.timedelta(days=7)),
            "workflow_run": {
                "id": 77,
                "head_branch": "main",
                "repository_id": 1,
                "head_repository_id": 1,
            },
        }
    )


def _manifest(entries: dict[int, tuple[str, float]]) -> bytes:
    return json.dumps(
        {
            str(run): {"archive": name, "created_at": _iso(NOW - datetime.timedelta(days=days))}
            for run, (name, days) in entries.items()
        }
    ).encode()


def _carried_two() -> dict[str, bytes]:
    return {
        "telemetry-history-200-1.zip": _history_zip(200, 3),
        "telemetry-history-100-1.zip": _history_zip(100, 5),
        fetch.MANIFEST: _manifest(
            {20: ("telemetry-history-200-1.zip", 3), 10: ("telemetry-history-100-1.zip", 5)}
        ),
    }


def test_the_carried_window_is_rechecked_and_only_newer_histories_are_fetched(
    tmp_path: Path,
) -> None:
    gh = _gh_with_three_histories()
    _carry(gh, _carried_two())
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.carried, got.fetched, got.dropped) == (2, 1, 0)
    assert not any("/runs/20/artifacts" in a or "/runs/10/artifacts" in a for a in gh.asked)
    members = _members(out)
    assert members["telemetry-history-200-1.zip"] == _history_zip(200, 3)
    assert set(json.loads(members[fetch.MANIFEST])) == {"30", "20", "10"}
    assert report.read(out).window["runs"] == 3
    assert _status(out)["carry"] == "kept: the window weekly run 77 carried"


@pytest.mark.parametrize(
    "run",
    [
        {"event": "pull_request"},
        {"event": "push"},
        {"path": telemetry.COLLECTOR_PATH},
        {"head_branch": "feature-x"},
        {"head_repository": {"full_name": "someone/fork"}},
    ],
    ids=["a-pr-run", "a-push-run", "another-workflow", "another-branch", "a-forks-run"],
)
def test_a_window_another_run_uploaded_is_never_carried_and_the_window_refills(
    tmp_path: Path, run: dict
) -> None:
    """The uploading run is read through the API by the id the artifact names, and only this
    workflow's own scheduled or dispatched run on main is trusted, never the artifact's name."""
    gh = _gh_with_three_histories()
    _carry(gh, {**_carried_two(), "telemetry-history-999-1.zip": _history_zip(999, 1)}, **run)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.carried == 0 and got.fetched == 3
    assert "telemetry-history-999-1.zip" not in _members(out)
    assert _status(out)["carry"].startswith("refused: ")


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_run": {"head_repository_id": 2}},
        {"workflow_run": {"head_branch": "feature-x"}},
        {"expired": True},
        {"name": f"{fetch.CARRY}-x"},
    ],
    ids=["a-forks-upload", "another-branchs-upload", "expired", "another-name"],
)
def test_a_carried_artifact_not_uploaded_on_main_under_its_name_is_never_read(
    tmp_path: Path, changes: dict
) -> None:
    gh = _gh_with_three_histories()
    _carry(gh, _carried_two())
    gh.carried[0]["workflow_run"].update(changes.pop("workflow_run", {}))
    gh.carried[0].update(changes)
    got = _fetch(gh, tmp_path / "window")
    assert got.carried == 0 and got.fetched == 3 and 5000 not in gh.requested


def test_a_carried_artifact_holding_more_than_the_window_is_refused_and_the_window_refills(
    tmp_path: Path,
) -> None:
    gh = _gh_with_three_histories()
    _carry(gh, _carried_two(), beside={fetch.STATUS: b"{}"})
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.carried == 0 and got.fetched == 3
    assert _status(out)["carry"].startswith("refused: ")
    assert f"not {fetch.WINDOW} alone" in _status(out)["carry"], _status(out)


def test_a_carried_window_holding_a_name_twice_is_refused_whole(tmp_path: Path) -> None:
    """The report opens a window member by its name, and of two of one name zipfile opens the
    last: a carried window holding one twice is not carried."""
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w") as zipped:
        warnings.simplefilter("ignore")  # zipfile warns of the name it is asked to write twice
        for name, data in [*_carried_two().items(), ("telemetry-history-200-1.zip", b"x")]:
            zipped.writestr(_info(name), data)
    gh = _gh_with_three_histories()
    _carry(gh, buffer.getvalue())
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.carried == 0 and got.fetched == 3
    assert "'telemetry-history-200-1.zip' twice" in _status(out)["carry"], _status(out)


def test_a_missed_week_refills_within_the_budget(tmp_path: Path) -> None:
    """No carried window (it expired after a missed week): every history is fetched afresh,
    newest first, and the fetch still stops at its reserve."""
    gh = _gh_with_three_histories()
    gh.remaining = 100 + 9  # six calls list the runs and the slots before the first history
    out = tmp_path / "window"
    got = _fetch(gh, out, reserve=100)
    assert got.carried == 0 and got.fetched >= 1 and got.left >= 1
    assert _status(out)["carry"] == "none: no window was carried"


@pytest.mark.parametrize(
    ("change", "why"),
    [
        pytest.param("collector", "no longer the collector's own", id="its-collector-run-is-gone"),
        pytest.param("source", "is not a run", id="its-ci-run-is-not-shown"),
        pytest.param("content", "describes run 201", id="it-names-another-run"),
        pytest.param("age", "left the window", id="it-left-the-window"),
    ],
)
def test_a_carried_history_is_checked_again_as_a_fetched_one_and_dropped(
    tmp_path: Path, change: str, why: str
) -> None:
    gh = _gh_with_three_histories()
    members = _carried_two()
    if change == "collector":
        gh.runs = [r for r in gh.runs if r["id"] != 20]
    elif change == "source":
        gh.sources[workflows.MERGE] = [r for r in gh.sources[workflows.MERGE] if r["id"] != 200]
    elif change == "content":
        members["telemetry-history-200-1.zip"] = _history_zip(200, 3, run_id=201)
    else:
        members[fetch.MANIFEST] = _manifest(
            {20: ("telemetry-history-200-1.zip", 120), 10: ("telemetry-history-100-1.zip", 5)}
        )
    _carry(gh, members)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.dropped == 1 and got.carried == 1
    assert any(why in reason for reason in _status(out)["refusals"]), _status(out)
    kept = _members(out)
    if change in ("collector", "source"):
        # Nothing shows the history's run, so it is neither kept nor fetched again.
        assert "telemetry-history-200-1.zip" not in kept and got.fetched == 1
    else:
        # Its collector run is listed, so the history is fetched again, as uploaded.
        assert kept["telemetry-history-200-1.zip"] == _history_zip(200, 3) and got.fetched == 2


@pytest.mark.parametrize(
    ("name", "inner", "links", "why"),
    [
        pytest.param("../telemetry-history-1-1.zip", None, (), "a parent path", id="dot-dot"),
        pytest.param("/telemetry-history-1-1.zip", None, (), "an absolute path", id="absolute"),
        pytest.param(
            "a/../telemetry-history-1-1.zip", None, (), "a parent path", id="inner-dot-dot"
        ),
        pytest.param("nested/telemetry-history-1-1.zip", None, (), "a folder", id="a-folder"),
        pytest.param(
            "telemetry-history-1-1.zip",
            None,
            ("telemetry-history-1-1.zip",),
            "'telemetry-history-1-1.zip' is a link",
            id="a-link",
        ),
        pytest.param(
            "telemetry-history-1-1.zip",
            "../history-1-1.jsonl",
            (),
            "not history-1-1.jsonl alone",
            id="dot-dot-inside-a-collector-archive",
        ),
    ],
)
def test_a_traversal_or_link_member_of_the_carried_window_is_refused(
    tmp_path: Path, name: str, inner: str | None, links: tuple, why: str
) -> None:
    """Each plant is a history of a run the listings show, and the manifest names it under a
    listed collector run, so only the member check stands between it and the window."""
    gh = _gh_with_three_histories()
    gh.collector(40, "workflow_run", 1)
    gh.source(1, 1)
    plant = _zip({inner or "history-1-1.jsonl": _history_lines(1, 1)})
    members = {name: plant, fetch.MANIFEST: _manifest({40: (name, 1)})}
    _carry(gh, members, links=links)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert not any("1-1" in member for member in _members(out))
    assert "40" not in json.loads(_members(out)[fetch.MANIFEST]) and got.carried == 0
    assert _refused(out, "carried: ", why), _status(out)
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == sorted(
        [fetch.WINDOW, fetch.STATUS]
    ), "a member was written to disk"


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        pytest.param({"created_at": "not a time"}, "has no time", id="not-a-time"),
        pytest.param({"created_at": None}, "has no time", id="no-time"),
        pytest.param({"created_at": "2026-10-23T08:13:00"}, "has no time", id="no-zone"),
        pytest.param(
            {"key": "x"}, "manifest entry 'x' names no archive the window holds", id="not-a-run"
        ),
        pytest.param(
            {"archive": fetch.MANIFEST}, "names no archive the window holds", id="the-manifest"
        ),
        pytest.param(
            {"archive": "telemetry-history-999-1.zip"},
            "names no archive the window holds",
            id="an-archive-it-does-not-hold",
        ),
    ],
)
def test_a_carried_manifest_entry_not_in_its_shape_is_dropped_and_fetched_again(
    tmp_path: Path, entry: dict, why: str
) -> None:
    """One bad entry drops that history alone: the rest of the window is carried, and its
    collector run, still listed, is fetched again."""
    gh = _gh_with_three_histories()
    members = _carried_two()
    doc = json.loads(members[fetch.MANIFEST])
    changes = dict(entry)
    changed = {**doc.pop("20"), **changes}
    doc[changes.pop("key", "20")] = {k: v for k, v in changed.items() if k != "key" and v}
    members[fetch.MANIFEST] = json.dumps(doc).encode()
    _carry(gh, members)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.carried, got.dropped, got.fetched) == (1, 1, 2)
    assert _status(out)["carry"] == "kept: the window weekly run 77 carried"
    assert _refused(out, "carried: ", why), _status(out)
    assert set(json.loads(_members(out)[fetch.MANIFEST])) == {"30", "20", "10"}


def test_an_oversized_member_of_the_carried_window_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "MAX_HISTORY_ARCHIVE_BYTES", 4096)
    gh = _gh_with_three_histories()
    members = _carried_two()
    members["telemetry-history-200-1.zip"] = members["telemetry-history-200-1.zip"] + b"\0" * 8192
    _carry(gh, members)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert any("passes 4096 bytes" in r for r in _status(out)["refusals"]), _status(out)
    assert got.carried == 1 and _members(out)["telemetry-history-200-1.zip"] == _history_zip(200, 3)


def test_a_carried_window_past_its_total_cap_is_refused_whole(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "MAX_WINDOW_BYTES", 1024)
    gh = _gh_with_three_histories()
    _carry(gh, _carried_two())
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.carried == 0
    assert _status(out)["carry"].startswith("refused: ") and "1024" in _status(out)["carry"]


def test_a_carried_window_is_copied_within_the_caps_the_manifest_keeps_room_in(
    tmp_path: Path, monkeypatch
) -> None:
    """A carried window within the byte cap whole still leaves the new manifest its place: the
    history that would take it is dropped, and refused again when fetched."""
    members = _carried_two()
    monkeypatch.setattr(fetch, "MAX_MANIFEST_BYTES", 2048)
    cap = len(members["telemetry-history-100-1.zip"]) + fetch.MAX_MANIFEST_BYTES
    assert sum(len(v) for v in members.values()) <= cap, "the carried window is within the cap"
    monkeypatch.setattr(telemetry, "MAX_WINDOW_BYTES", cap)
    gh = _gh_with_three_histories()
    _carry(gh, members)
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert (got.carried, got.dropped, got.fetched) == (1, 1, 0)
    assert _status(out)["carry"] == "kept: the window weekly run 77 carried"
    assert _refused(out, f"carried: telemetry-history-200-1.zip: the window passes {cap} bytes")
    assert sorted(_members(out)) == ["fetched.json", "telemetry-history-100-1.zip"]
    assert report.read(out).window["runs"] == 1, "the report refuses the window the fetch built"


def test_a_carried_window_of_too_many_members_is_refused_whole(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "MAX_WINDOW_MEMBERS", 2)
    gh = _gh_with_three_histories()
    _carry(gh, _carried_two())
    out = tmp_path / "window"
    got = _fetch(gh, out)
    assert got.carried == 0 and "2 members" in _status(out)["carry"]


# ── which workflow's window is carried ────────────────────────────────────────────────────────


def test_the_daily_job_carries_its_own_window_and_fetches_only_newer_histories(
    tmp_path: Path,
) -> None:
    """``flaky-tests.yml`` carries its window to its next day's run as the weekly report does to
    its next week's: the carried histories are checked again, and only the collector runs the
    window does not hold are listed and downloaded."""
    gh = _gh_with_three_histories()
    _carry(gh, _carried_two(), carrier="flaky-tests")
    out = tmp_path / "window"
    got = _fetch(gh, out, carrier="flaky-tests")
    assert (got.carried, got.fetched, got.dropped, got.refused) == (2, 1, 0, 0)
    assert not any("/runs/20/artifacts" in a or "/runs/10/artifacts" in a for a in gh.asked)
    assert any("/actions/artifacts?name=flaky-tests-window&" in a for a in gh.asked), gh.asked
    assert not any(f"name={fetch.CARRY}&" in a for a in gh.asked), gh.asked
    assert _status(out)["carry"] == "kept: the window daily run 77 carried"
    assert set(json.loads(_members(out)[fetch.MANIFEST])) == {"30", "20", "10"}


@pytest.mark.parametrize(
    ("carrier", "uploader"),
    [
        pytest.param("flaky-tests", "flake-report", id="the-daily-window-from-the-weekly-run"),
        pytest.param("flake-report", "flaky-tests", id="the-weekly-window-from-the-daily-run"),
    ],
)
def test_each_carry_trusts_only_its_own_workflows_run(
    tmp_path: Path, carrier: str, uploader: str
) -> None:
    """A window under the carry's own name is taken only from its own workflow's scheduled or
    dispatched run on main; the same name uploaded by the other workflow's run is refused, and
    the window refills from the record."""
    gh = _gh_with_three_histories()
    name, _, _ = fetch.CARRIES[carrier]
    _carry(gh, _carried_two(), carrier=carrier, path=fetch.CARRIES[uploader][1])
    assert gh.carried[0]["name"] == name
    out = tmp_path / "window"
    got = _fetch(gh, out, carrier=carrier)
    assert got.carried == 0 and got.fetched == 3
    assert _status(out)["carry"].startswith("refused: "), _status(out)


def test_a_carry_the_table_does_not_name_is_refused(tmp_path: Path) -> None:
    """Which artifact is carried, and whose run is trusted to have uploaded it, come from the
    fixed table alone: neither the command line nor a caller can name another."""
    with pytest.raises(ValueError, match="no carried window is named 'other'"):
        _fetch(Gh(), tmp_path / "window", carrier="other")
    with pytest.raises(SystemExit) as exited:
        fetch.main(["--repo", REPO, "--out", str(tmp_path / "w"), "--carry", "other"])
    assert exited.value.code == 2
    assert set(fetch.CARRIES) == {"flake-report", "flaky-tests"}
    assert fetch.CARRIES["flaky-tests"][:2] == (
        "flaky-tests-window",
        f"{telemetry.WORKFLOWS}/flaky-tests.yml",
    )
    assert fetch.CARRIES["flake-report"][:2] == (fetch.CARRY, fetch.FLAKE_REPORT_PATH)


def test_a_carried_history_that_aged_out_of_the_window_is_neither_dropped_nor_refused(
    tmp_path: Path,
) -> None:
    """The daily window is 15 days, so every day the oldest carried histories leave it: one whose
    collector run is no longer listed and whose day is before the window's is counted as aged,
    not dropped, so the window stays whole and the close rule can still judge it. A history of
    the window's first day, its collector run still listed, is kept, not dropped and fetched
    again."""
    gh = Gh()
    gh.history(30, 300, 1)
    gh.history(20, 200, 3)
    # Run 10's day is the window's first, a few hours before its start; run 5's is long gone.
    first_day = 15 + 2 / 24
    gh.history(10, 100, first_day)
    members = {
        "telemetry-history-200-1.zip": _history_zip(200, 3),
        "telemetry-history-100-1.zip": _history_zip(100, first_day),
        "telemetry-history-50-1.zip": _history_zip(50, 20),
        fetch.MANIFEST: _manifest(
            {
                20: ("telemetry-history-200-1.zip", 3),
                10: ("telemetry-history-100-1.zip", first_day),
                5: ("telemetry-history-50-1.zip", 20),
            }
        ),
    }
    gh.source(50, 20)
    _carry(gh, members, carrier="flaky-tests")
    out = tmp_path / "window"
    now = NOW.replace(hour=12)
    got = _fetch(gh, out, carrier="flaky-tests", days=15, now=now)
    assert (got.carried, got.aged, got.dropped, got.fetched, got.refused) == (2, 1, 0, 1, 0)
    kept = _members(out)
    assert "telemetry-history-50-1.zip" not in kept
    assert kept["telemetry-history-100-1.zip"] == _history_zip(100, first_day)
    assert _status(out)["aged"] == 1
    assert not any("telemetry-history-50-1" in why for why in _status(out)["refusals"])
    assert flaky_issues.complete(out / fetch.STATUS)
