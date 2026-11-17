"""``python -m tools.ci flaky-issues`` (tools/ci/flaky_issues.py): one issue for each test that
keeps passing as a flake, kept from main's per-test histories.

Each plant is a window of histories as the fetch leaves one (history files and its status) and a
stand-in ``gh`` that serves an issue listing and records each call, with the body each write sent.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools.ci import fetch, flaky_issues, telemetry

REPO = "o/r"
TODAY = datetime.date(2026, 10, 27)
FLAKY = "tests/test_timing.py::test_it_answers_in_time"
E2E = "web/e2e/chat.spec.ts::it answers [chromium]"
BOT = {"login": "github-actions[bot]", "type": "Bot"}
LABELLED = [{"name": flaky_issues.LABEL}]


def _day(days_ago: float) -> str:
    at = datetime.datetime(2026, 10, 27, 12, tzinfo=datetime.UTC) - datetime.timedelta(
        days=days_ago
    )
    return at.isoformat().replace("+00:00", "Z")


def _history(
    window: Path,
    run_id: int,
    days_ago: float,
    *,
    flaky: tuple[str, ...] = (),
    tested: tuple[str, ...] = (),
    extra: tuple[dict, ...] = (),
    version: int = telemetry.VERSION,
    queued: bool = False,
) -> None:
    """One push's history: its own record naming *flaky* as flakes, and, when *tested* names any,
    its tested pull request run's record (run ``run_id + 1000``) naming those. *queued*: a merge
    queue run's instead, on commit ``f"{run_id:040x}"``."""
    head = {f: None for f in telemetry.RUN_FIELDS}
    head.update(
        kind="run",
        schema=telemetry.SCHEMA,
        version=version,
        run_id=run_id,
        attempt=1,
        workflow="merge.yml",
        event="push",
        branch="main",
        sha=f"{run_id:040x}",
        created_at=_day(days_ago),
        jobs=[],
    )
    if queued:
        head.update(
            event=telemetry.QUEUE_EVENT,
            branch=f"gh-readonly-queue/main/pr-{run_id}-{'c' * 40}",
            queue={"pull_request": run_id, "head_sha": "d" * 40},
        )

    def record(tests: tuple[str, ...]) -> dict:
        return {
            "key": "suite (1/2)",
            "result": "failure",
            "rerun": [{"test": t, "rerun_result": "flake"} for t in tests],
            "quarantined": [],
        }

    lines: list[dict] = [head]
    if flaky:
        lines.append(dict(record(flaky), kind="record"))
    if tested:
        lines.append(
            dict(
                record(tested),
                kind=telemetry.TESTED_RECORD,
                run_id=run_id + 1000,
                attempt=1,
            )
        )
    lines += extra
    window.mkdir(parents=True, exist_ok=True)
    (window / f"history-{run_id}-1.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8"
    )


def _status(window: Path, **counts: int) -> None:
    doc = {k: 0 for k in ("carried", "dropped", "fetched", "refused", "left", "reserve")}
    doc.update(counts, carry="fetched afresh", slots=None, refusals=[])
    window.mkdir(parents=True, exist_ok=True)
    (window / fetch.STATUS).write_text(json.dumps(doc), encoding="utf-8")


class FakeGh:
    """A stand-in ``gh``: it lists *issues* (each a listing entry), answers whether the label
    exists, and records every call, with the body each write sent."""

    def __init__(self, issues: list[dict] | None = None, *, label: bool = True) -> None:
        self.issues = issues or []
        self.label = label
        self.calls: list[tuple[str, ...]] = []
        self.bodies: list[tuple[tuple[str, ...], str]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        for i, a in enumerate(args):
            if a == "-F" and args[i + 1].startswith("body=@"):
                self.bodies.append((args, Path(args[i + 1][len("body=@") :]).read_text()))
        if len(args) == 2 and args[0] == "api":
            url = args[1]
            if "/labels/" in url:
                if not self.label:
                    raise subprocess.CalledProcessError(1, ["gh"], "not found")
                return "{}"
            if "/issues?" in url:
                page = int(re.search(r"[?&]page=(\d+)", url).group(1))
                return json.dumps(self.issues[(page - 1) * 100 : page * 100])
        return json.dumps({"number": 99})

    def writes(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if "-X" in c]

    def comparing(self, merged: frozenset[int]) -> Any:
        """This ``gh``, answering a commit's comparison with main: ``ahead`` for the commit of
        each run in *merged*, ``diverged`` for any other, recording each as a call."""

        def gh(*args: str) -> str:
            url = args[1] if len(args) > 1 else ""
            if args[:1] == ("api",) and "/compare/" in url:
                self.calls.append(args)
                sha = url.split("/compare/")[1].split("...")[0]
                return "ahead\n" if int(sha, 16) in merged else "diverged\n"
            return self(*args)

        return gh

    def compared(self) -> list[str]:
        return [c[1] for c in self.calls if len(c) > 1 and "/compare/" in c[1]]


def _issue(
    number: int,
    test: str,
    *,
    state: str = "open",
    closed_days_ago: float | None = None,
    **over: Any,
) -> dict:
    issue = {
        "number": number,
        "title": flaky_issues.title(test),
        "state": state,
        "user": BOT,
        "labels": LABELLED,
        "closed_at": None if closed_days_ago is None else _day(closed_days_ago),
    }
    issue.update(over)
    return issue


def _run(window: Path, gh: FakeGh, merged: frozenset[int] = frozenset()) -> flaky_issues.Plan:
    """The plan, with *merged* the queue runs whose commits are on main."""
    found = flaky_issues.read(window)
    on_main = flaky_issues.on_main(REPO, gh.comparing(merged))
    whole = flaky_issues.complete(window / fetch.STATUS) and flaky_issues.covers(
        flaky_issues.proved(found, on_main), TODAY
    )
    done = flaky_issues.plan(
        found.flakes,
        flaky_issues.own_issues(REPO, gh),
        repo=REPO,
        today=TODAY,
        whole=whole,
    )
    flaky_issues.apply(done, REPO, gh=gh)
    return done


# ── opening, once ─────────────────────────────────────────────────────────────────────────────


def test_two_flakes_in_a_week_open_one_issue_titled_labelled_and_linked(
    tmp_path: Path,
) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,))
    _history(window, 102, 3, flaky=(FLAKY,))
    _status(window, fetched=2)
    gh = FakeGh(label=False)
    done = _run(window, gh)
    [(name, text)] = done.open
    assert name == f"Flaky test: {FLAKY}"
    assert "2 times in the last 7 days (2 in 14)" in text
    assert f"https://github.com/{REPO}/actions/runs/101/attempts/1" in text
    assert f"https://github.com/{REPO}/actions/runs/102/attempts/1" in text
    [create] = [c for c in gh.writes() if c[3] == f"repos/{REPO}/issues"]
    assert f"title={name}" in create and f"labels[]={flaky_issues.LABEL}" in create
    assert any(c[3] == f"repos/{REPO}/labels" for c in gh.writes()), "the missing label is made"


def test_a_tested_pull_request_runs_e2e_flakes_count(tmp_path: Path) -> None:
    """The flakes the verdict passed on a pull request run, Playwright's too, reach the history
    only as the tested push's ``tested_record`` lines (version 6)."""
    window = tmp_path / "w"
    _history(window, 101, 1, tested=(E2E,))
    _history(window, 102, 2, tested=(E2E,))
    _status(window, fetched=2)
    done = _run(window, FakeGh())
    [(name, text)] = done.open
    assert name == f"Flaky test: {E2E}"
    assert "/actions/runs/1101/attempts/1" in text and "/actions/runs/1102/attempts/1" in text


def test_a_third_flake_edits_the_issue_and_opens_no_second(tmp_path: Path) -> None:
    window = tmp_path / "w"
    for n, days in enumerate((1, 2, 4)):
        _history(window, 101 + n, days, flaky=(FLAKY,))
    _status(window, fetched=3)
    gh = FakeGh([_issue(7, FLAKY)])
    done = _run(window, gh)
    assert done.open == () and [n for n, _ in done.edit] == [7]
    assert "3 times in the last 7 days" in done.edit[0][1]
    assert [c[3] for c in gh.writes()] == [f"repos/{REPO}/issues/7"]


def test_one_flake_in_a_week_opens_nothing(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,))
    _history(window, 102, 9, flaky=(FLAKY,))  # in 14 days, not in 7
    _status(window, fetched=2)
    gh = FakeGh()
    done = _run(window, gh)
    assert done == flaky_issues.Plan()
    assert gh.writes() == []


def test_a_test_counts_once_a_run_however_many_jobs_name_it(tmp_path: Path) -> None:
    window = tmp_path / "w"
    again = {
        "kind": "record",
        "key": "suite (2/2)",
        "result": "failure",
        "rerun": [{"test": FLAKY, "rerun_result": "flake"}],
        "quarantined": [],
    }
    _history(window, 101, 1, flaky=(FLAKY,), extra=(again,))
    _status(window, fetched=1)
    assert _run(window, FakeGh()).open == ()


# ── closing ───────────────────────────────────────────────────────────────────────────────────


def test_fourteen_days_with_no_flake_close_the_issue(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15, flaky=(FLAKY,))  # past the 14 days
    _history(window, 102, 2)
    _status(window, fetched=2)
    gh = FakeGh([_issue(7, FLAKY)])
    done = _run(window, gh)
    assert done.close == (7,)
    comment, close = gh.writes()
    assert comment[3] == f"repos/{REPO}/issues/7/comments"
    assert close[3] == f"repos/{REPO}/issues/7" and "state=closed" in close


@pytest.mark.parametrize("short", [{"left": 3}, {"refused": 1}, {"dropped": 1}])
def test_a_short_window_closes_nothing(tmp_path: Path, short: dict) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15)
    _history(window, 102, 2)
    _status(window, fetched=1, **short)
    gh = FakeGh([_issue(7, FLAKY)])
    assert _run(window, gh).close == ()
    assert gh.writes() == []


@pytest.mark.parametrize(
    "days",
    [(), (15, 5), (10, 1)],
    ids=["no history", "none in the last two days", "none fourteen days back"],
)
def test_a_window_the_collector_stopped_filling_closes_nothing(
    tmp_path: Path, days: tuple[int, ...]
) -> None:
    """A status that left nothing out is not enough: a collector that stopped uploading leaves a
    window with no flake in it, and its issues would read as fixed."""
    window = tmp_path / "w"
    for n, ago in enumerate(days):
        _history(window, 101 + n, ago)
    _status(window, fetched=len(days))
    gh = FakeGh([_issue(7, FLAKY)])
    assert _run(window, gh).close == ()
    assert gh.writes() == []


def test_a_flake_in_the_last_fourteen_days_keeps_its_issue_open(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15)
    _history(window, 102, 10, flaky=(FLAKY,))
    _history(window, 103, 1)
    _status(window, fetched=3)
    gh = FakeGh([_issue(7, FLAKY)])
    assert _run(window, gh) == flaky_issues.Plan()


# ── the merge queue's runs ────────────────────────────────────────────────────────────────────


def test_a_queue_run_s_flakes_open_an_issue_though_its_commit_never_reached_main(
    tmp_path: Path,
) -> None:
    """Opening only asks a person to look, so every queue run's flakes count."""
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,), queued=True)
    _history(window, 102, 2, flaky=(FLAKY,), queued=True)
    _status(window, fetched=2)
    gh = FakeGh()
    done = _run(window, gh)
    assert [name for name, _ in done.open] == [flaky_issues.title(FLAKY)]


def test_a_queue_run_s_flake_keeps_an_issue_open_though_its_commit_never_reached_main(
    tmp_path: Path,
) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15)
    _history(window, 102, 10, flaky=(FLAKY,), queued=True)
    _history(window, 103, 1)
    _status(window, fetched=3)
    assert _run(window, FakeGh([_issue(7, FLAKY)])) == flaky_issues.Plan()


def test_a_queue_run_that_never_reached_main_closes_nothing(tmp_path: Path) -> None:
    """Main's own histories stop three days back; only a queue run is fresh, and its commit is
    not on main, so the window does not hold the last two days and nothing is closed."""
    window = tmp_path / "w"
    _history(window, 101, 15)
    _history(window, 102, 3)
    _history(window, 103, 1, queued=True)
    _status(window, fetched=3)
    gh = FakeGh([_issue(7, FLAKY)])
    assert _run(window, gh).close == ()
    assert gh.compared() == [f"repos/{REPO}/compare/{103:040x}...main"]
    assert gh.writes() == []


def test_a_queue_run_on_main_counts_toward_the_window_s_days(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15)
    _history(window, 102, 3)
    _history(window, 103, 1, queued=True)
    _status(window, fetched=3)
    gh = FakeGh([_issue(7, FLAKY)])
    assert _run(window, gh, merged=frozenset({103})).close == (7,)


def test_a_queue_run_on_main_can_be_the_oldest_day_too(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15, queued=True)
    _history(window, 102, 16, queued=True)
    _history(window, 103, 1)
    _status(window, fetched=3)
    assert _run(window, FakeGh([_issue(7, FLAKY)])).close == ()
    gh = FakeGh([_issue(7, FLAKY)])
    assert _run(window, gh, merged=frozenset({101})).close == (7,)
    assert gh.compared() == [
        f"repos/{REPO}/compare/{102:040x}...main",
        f"repos/{REPO}/compare/{101:040x}...main",
    ], "the oldest first, until one is on main"


def test_a_window_main_s_own_histories_span_asks_nothing_of_a_queue_run(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15)
    _history(window, 102, 10, queued=True)
    _history(window, 103, 1)
    _status(window, fetched=3)
    gh = FakeGh([_issue(7, FLAKY)])
    assert _run(window, gh).close == (7,)
    assert gh.compared() == []


@pytest.mark.parametrize(
    "answer",
    ["behind", "diverged", "", "ahead-ish", subprocess.CalledProcessError(1, ["gh"])],
    ids=["behind", "diverged", "empty", "another-word", "unread"],
)
def test_a_commit_main_does_not_hold_is_not_on_main(answer: object) -> None:
    def gh(*args: str) -> str:
        if isinstance(answer, BaseException):
            raise answer
        return f"{answer}\n"

    assert flaky_issues.on_main(REPO, gh)("a" * 40) is False


@pytest.mark.parametrize("status", ["identical", "ahead"])
def test_a_commit_main_is_or_holds_is_on_main(status: str) -> None:
    asked: list[tuple[str, ...]] = []

    def gh(*args: str) -> str:
        asked.append(args)
        return f"{status}\n"

    assert flaky_issues.on_main(REPO, gh)("a" * 40) is True
    assert asked == [("api", f"repos/{REPO}/compare/{'a' * 40}...main", "--jq", ".status")]


@pytest.mark.parametrize("sha", ["", "main", "a" * 39, "A" * 40, "a" * 40 + "...x"])
def test_a_commit_that_is_no_full_id_is_never_asked_about(sha: str) -> None:
    def gh(*args: str) -> str:
        raise AssertionError(args)

    assert flaky_issues.on_main(REPO, gh)(sha) is False


# ── a fix that did not hold ───────────────────────────────────────────────────────────────────


def test_two_flakes_after_its_close_reopen_the_issue_and_open_no_second(
    tmp_path: Path,
) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,))
    _history(window, 102, 2, flaky=(FLAKY,))
    _status(window, fetched=2)
    gh = FakeGh([_issue(7, FLAKY, state="closed", closed_days_ago=4)])
    done = _run(window, gh)
    assert done.open == () and [n for n, _ in done.reopen] == [7]
    [reopen] = gh.writes()
    assert reopen[3] == f"repos/{REPO}/issues/7" and "state=open" in reopen


def test_fewer_flakes_since_its_close_leave_it_closed(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,))
    _history(window, 102, 3, flaky=(FLAKY,))
    _status(window, fetched=2)
    gh = FakeGh([_issue(7, FLAKY, state="closed", closed_days_ago=2)])  # one flake since
    assert _run(window, gh) == flaky_issues.Plan()
    assert gh.writes() == []


# ── only its own issues ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "over",
    [
        {"user": {"login": "someone", "type": "User"}},
        {"user": {"login": "github-actions[bot]", "type": "User"}},
        {"labels": []},
        {"labels": [{"name": "bug"}]},
        {"title": f"flaky test: {FLAKY}"},
        {"pull_request": {"url": "u"}},
    ],
    ids=[
        "another author",
        "not the bot",
        "no label",
        "another label",
        "another title",
        "a PR",
    ],
)
def test_an_issue_not_this_jobs_is_never_touched_or_taken_for_the_tests(
    tmp_path: Path, over: dict
) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,))
    _history(window, 102, 2, flaky=(FLAKY,))
    _status(window, fetched=2)
    gh = FakeGh([_issue(7, FLAKY, **over)])
    done = _run(window, gh)
    assert len(done.open) == 1 and done.edit == () and done.close == ()
    assert not any("/issues/7" in c[3] for c in gh.writes())


def test_an_issue_not_this_jobs_is_never_closed(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 15)
    _history(window, 102, 2)
    _status(window, fetched=2)
    gh = FakeGh([_issue(7, FLAKY, labels=[])])
    assert _run(window, gh).close == ()


def test_two_own_open_issues_of_one_title_edit_the_older_and_name_the_other(
    tmp_path: Path, capsys
) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,))
    _history(window, 102, 2, flaky=(FLAKY,))
    _status(window, fetched=2)
    done = _run(window, FakeGh([_issue(9, FLAKY), _issue(7, FLAKY)]))
    assert [n for n, _ in done.edit] == [7] and done.open == ()
    assert "edited #7; close #9" in capsys.readouterr().out


# ── no log text ───────────────────────────────────────────────────────────────────────────────


def test_a_body_holds_the_id_in_a_fence_its_counts_and_links_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A test id can hold markdown, backticks or an @mention: it reaches the body only inside a
    fence longer than any run of backticks in it. Nothing else a history holds is read into it:
    a test line's text, and the run line's, are never in a body."""
    test = "tests/test_x.py::test_it[`` @someone **bold** ```]"
    leaked = "Traceback: secret-token-value"
    noise = {
        **{f: None for f in telemetry.TEST_FIELDS},
        "kind": "test",
        "test": leaked,
        "first_result": "failed",
        "rerun_result": "flake",
        "run_id": 101,
        "attempt": 1,
        "report": "junit-test-shard-1",
    }
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(test,), extra=(noise,))
    _history(window, 102, 2, flaky=(test,))
    _status(window, fetched=2)
    gh = FakeGh()
    _run(window, gh)
    [(_, text)] = gh.bodies
    assert leaked not in text
    fence = "````"
    assert f"\n{fence}text\n{test}\n{fence}\n" in text
    rows = [line for line in text.splitlines() if line.startswith("| 2026-")]
    link = r"\[run \d+, attempt 1\]\(https://github\.com/o/r/actions/runs/\d+/attempts/1\)"
    assert len(rows) == 2 and all(
        re.fullmatch(rf"\| \d{{4}}-\d\d-\d\d \| {link} \|", r) for r in rows
    )


def test_a_test_id_no_issue_can_name_opens_nothing(tmp_path: Path, capsys) -> None:
    test = "tests/test_x.py::test_it[a\nb]"
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(test,))
    _history(window, 102, 2, flaky=(test,))
    _status(window, fetched=2)
    assert _run(window, FakeGh()).open == ()
    assert "a test id no issue can name" in capsys.readouterr().out


# ── long ids ──────────────────────────────────────────────────────────────────────────────────


def test_a_long_id_has_a_title_within_the_limit_that_the_next_run_finds(
    tmp_path: Path,
) -> None:
    test = "tests/test_x.py::test_it[" + "x" * 400 + "]"
    name = flaky_issues.title(test)
    assert len(name) <= flaky_issues.TITLE_LIMIT and name.startswith(flaky_issues.PREFIX)
    assert name == flaky_issues.title(test) and name != flaky_issues.title(test + "y")
    window = tmp_path / "w"
    for n, days in enumerate((1, 2, 3)):
        _history(window, 101 + n, days, flaky=(test,))
    _status(window, fetched=3)
    done = _run(window, FakeGh([_issue(7, test)]))
    assert done.open == () and [n for n, _ in done.edit] == [7]


# ── the window ────────────────────────────────────────────────────────────────────────────────


def test_a_version_5_history_counts_its_own_records(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,), version=5)
    _history(window, 102, 2, flaky=(FLAKY,), version=5)
    _status(window, fetched=2)
    assert len(_run(window, FakeGh()).open) == 1


def test_a_history_it_cannot_read_refuses_the_window(tmp_path: Path) -> None:
    window = tmp_path / "w"
    _history(window, 101, 1, flaky=(FLAKY,), extra=({"kind": "record", "key": "x"},))
    _status(window, fetched=1)
    with pytest.raises(flaky_issues.Refused):
        flaky_issues.read(window)
