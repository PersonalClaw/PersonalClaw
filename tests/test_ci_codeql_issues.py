"""``python -m tools.ci codeql-issues``: one issue per new CodeQL alert open on main (0.4.0 A1).

Each planted listing (an alert new, still open, reopened, fixed, dismissed, or made before the
queue) gives the issue action it should, and an issue this job did not open is never touched.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools.ci import codeql_issues

REPO = "o/r"
BOT = {"login": "github-actions[bot]", "type": "Bot"}
LABELLED = [{"name": codeql_issues.LABEL}]
COMMIT = "a" * 40
NEW = "2026-11-03T10:00:00Z"
OLD = "2026-10-27T10:18:34Z"


def _alert(number: int, *, created: str = NEW, rule: str = "py/path-injection", **over: Any):
    alert: dict[str, Any] = {
        "number": number,
        "created_at": created,
        "html_url": f"https://github.com/{REPO}/security/code-scanning/{number}",
        "state": "open",
        "rule": {
            "id": rule,
            "severity": "error",
            "security_severity_level": "high",
            "description": "Uncontrolled data used in path expression",
        },
        "most_recent_instance": {
            "ref": "refs/heads/main",
            "commit_sha": COMMIT,
            "message": {"text": "This path depends on a user-provided value."},
            "location": {"path": "src/app/files.py", "start_line": 42},
        },
    }
    alert.update(over)
    return alert


def _issue(number: int, alert: int, *, state: str = "open", rule: str = "py/path-injection",
           **over: Any) -> dict:  # fmt: skip
    issue = {
        "number": number,
        "title": f"CodeQL alert #{alert} on main: {rule}",
        "state": state,
        "user": BOT,
        "labels": LABELLED,
    }
    issue.update(over)
    return issue


class FakeGh:
    """A stand-in ``gh``: it lists *alerts* and *issues* page by page, answers whether the label
    exists, which pull requests merged a commit and each alert's state once closed, and records
    every call, with the body each write sent."""

    def __init__(
        self,
        alerts: list[dict] | None = None,
        issues: list[dict] | None = None,
        *,
        label: bool = True,
        pulls: object = None,
        closed: dict[int, dict] | None = None,
    ) -> None:
        self.alerts = alerts or []
        self.issues = issues or []
        self.label = label
        self.pulls = [{"number": 5900}] if pulls is None else pulls
        self.closed = closed or {}
        self.calls: list[tuple[str, ...]] = []
        self.bodies: list[str] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        for i, a in enumerate(args):
            if a == "-F" and args[i + 1].startswith("body=@"):
                self.bodies.append(Path(args[i + 1][len("body=@") :]).read_text())
        if len(args) == 2 and args[0] == "api":
            url = args[1]
            page = re.search(r"[?&]page=(\d+)", url)
            start = (int(page.group(1)) - 1) * 100 if page else 0
            if "/labels/" in url:
                if not self.label:
                    raise subprocess.CalledProcessError(1, ["gh"], "not found")
                return "{}"
            if "/code-scanning/alerts?" in url:
                return json.dumps(self.alerts[start : start + 100])
            if "/issues?" in url:
                return json.dumps(self.issues[start : start + 100])
            if url.endswith("/pulls"):
                if isinstance(self.pulls, Exception):
                    raise self.pulls
                return json.dumps(self.pulls)
            found = re.search(r"/code-scanning/alerts/(\d+)$", url)
            if found:
                return json.dumps(self.closed.get(int(found.group(1)), {"state": "open"}))
        return json.dumps({"number": 99})

    def writes(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if "-X" in c]


def _run(gh: FakeGh) -> codeql_issues.Plan:
    alerts, whole = codeql_issues.open_alerts(REPO, gh)
    done = codeql_issues.plan(alerts, codeql_issues.own_issues(REPO, gh), whole=whole)
    codeql_issues.apply(done, REPO, gh=gh)
    return done


def _field(call: tuple[str, ...], name: str) -> str | None:
    for i, a in enumerate(call):
        if a in ("-f", "-F") and call[i + 1].startswith(f"{name}="):
            return call[i + 1][len(name) + 1 :]
    return None


# ── a new alert ───────────────────────────────────────────────────────────────────────────────


def test_a_new_alert_opens_one_issue_titled_labelled_and_naming_its_merge() -> None:
    gh = FakeGh([_alert(501)])
    done = _run(gh)
    assert [a.number for a in done.open] == [501] and not done.reopen and not done.close
    (made,) = gh.writes()
    assert made[:4] == ("api", "-X", "POST", f"repos/{REPO}/issues")
    assert _field(made, "title") == "CodeQL alert #501 on main: py/path-injection"
    assert _field(made, "labels[]") == "codeql-alert"
    (text,) = gh.bodies
    assert f"[#501](https://github.com/{REPO}/security/code-scanning/501)" in text
    assert "`py/path-injection`, severity high" in text
    assert "`src/app/files.py`, line 42" in text
    assert f"{COMMIT}, merged by #5900" in text
    assert ("api", f"repos/{REPO}/commits/{COMMIT}/pulls") in gh.calls


def test_the_label_is_made_once_when_it_is_missing() -> None:
    gh = FakeGh([_alert(501)], label=False)
    _run(gh)
    label, issue = gh.writes()
    assert label[3] == f"repos/{REPO}/labels" and _field(label, "name") == "codeql-alert"
    assert issue[3] == f"repos/{REPO}/issues"


def test_an_alert_made_before_the_queue_opens_nothing() -> None:
    gh = FakeGh([_alert(444, created=OLD), _alert(445, created="2026-10-31T23:59:59Z")])
    done = _run(gh)
    assert done == codeql_issues.Plan() and gh.writes() == []


def test_several_new_alerts_open_one_issue_each_in_number_order() -> None:
    gh = FakeGh([_alert(503), _alert(501, rule="js/xss"), _alert(502)])
    done = _run(gh)
    assert [a.number for a in done.open] == [501, 502, 503]
    assert [_field(c, "title") for c in gh.writes()] == [
        "CodeQL alert #501 on main: js/xss",
        "CodeQL alert #502 on main: py/path-injection",
        "CodeQL alert #503 on main: py/path-injection",
    ]


# ── an alert still open, and one open again ───────────────────────────────────────────────────


def test_an_alert_still_open_with_its_issue_open_writes_nothing() -> None:
    gh = FakeGh([_alert(501)], [_issue(7001, 501)])
    assert _run(gh) == codeql_issues.Plan() and gh.writes() == []


def test_an_alert_open_again_reopens_its_newest_issue_and_opens_no_second() -> None:
    gh = FakeGh(
        [_alert(501)], [_issue(7001, 501, state="closed"), _issue(7009, 501, state="closed")]
    )
    done = _run(gh)
    assert not done.open and [(n, a.number) for n, a in done.reopen] == [(7009, 501)]
    (made,) = gh.writes()
    assert made[:4] == ("api", "-X", "PATCH", f"repos/{REPO}/issues/7009")
    assert _field(made, "state") == "open"
    assert "py/path-injection" in gh.bodies[0]


def test_two_open_issues_of_one_alert_keep_the_older_and_name_the_other(capsys) -> None:
    gh = FakeGh([_alert(501)], [_issue(7009, 501), _issue(7001, 501)])
    done = _run(gh)
    assert done.twins == ((7001, (7009,)),) and gh.writes() == []
    assert "kept #7001; close #7009" in capsys.readouterr().out


# ── an alert fixed or dismissed ───────────────────────────────────────────────────────────────


def test_a_fixed_alert_closes_its_issue_with_a_comment_that_says_so() -> None:
    gh = FakeGh([], [_issue(7001, 501)], closed={501: {"state": "fixed"}})
    done = _run(gh)
    assert done.close == ((7001, 501),)
    comment, close = gh.writes()
    assert comment[3] == f"repos/{REPO}/issues/7001/comments"
    assert _field(comment, "body") == "Alert #501 is fixed on main. Closed."
    assert close[3] == f"repos/{REPO}/issues/7001" and _field(close, "state") == "closed"


def test_a_dismissed_alert_closes_its_issue_naming_the_reason() -> None:
    dismissed = {"state": "dismissed", "dismissed_reason": "false positive"}
    gh = FakeGh([], [_issue(7001, 501)], closed={501: dismissed})
    _run(gh)
    comment, _close = gh.writes()
    assert _field(comment, "body") == "Alert #501 was dismissed (false positive). Closed."


def test_an_alert_it_cannot_read_still_closes_its_issue() -> None:
    gh = FakeGh([], [_issue(7001, 501)], closed={501: []})  # type: ignore[dict-item]
    _run(gh)
    comment, _close = gh.writes()
    assert _field(comment, "body") == "Alert #501 is no longer open on main. Closed."


def test_a_closed_issue_of_a_closed_alert_is_left_alone() -> None:
    gh = FakeGh([], [_issue(7001, 501, state="closed")])
    assert _run(gh) == codeql_issues.Plan() and gh.writes() == []


def test_a_listing_cut_short_closes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(codeql_issues, "PAGES", 1)
    gh = FakeGh([_alert(n, created=OLD) for n in range(1, 101)], [_issue(7001, 501)])
    alerts, whole = codeql_issues.open_alerts(REPO, gh)
    assert len(alerts) == 100 and not whole
    done = codeql_issues.plan(alerts, codeql_issues.own_issues(REPO, gh), whole=whole)
    assert done == codeql_issues.Plan()


# ── only its own issues ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "other",
    [
        pytest.param({"user": {"login": "someone", "type": "User"}}, id="a-person-opened-it"),
        pytest.param({"user": {"login": "github-actions[bot]", "type": "User"}}, id="not-a-bot"),
        pytest.param({"labels": []}, id="unlabelled"),
        pytest.param({"title": "CodeQL alert 501 on main: py/path-injection"}, id="another-title"),
        pytest.param({"pull_request": {"url": "x"}}, id="a-pull-request"),
    ],
)
def test_an_issue_not_this_jobs_is_never_touched_or_taken_for_the_alerts(other: dict) -> None:
    gh = FakeGh([_alert(501)], [_issue(7001, 501, **other), _issue(7002, 502, **other)])
    done = _run(gh)
    assert [a.number for a in done.open] == [501] and not done.reopen and not done.close
    assert all("/issues/700" not in " ".join(c) for c in gh.writes())


# ── what a body holds ─────────────────────────────────────────────────────────────────────────


def test_a_body_holds_no_message_or_description_and_no_field_starts_a_line() -> None:
    planted = _alert(
        501,
        most_recent_instance={
            "commit_sha": COMMIT,
            "message": {"text": "MESSAGE TEXT"},
            "location": {"path": "src/a.py\n\n## planted heading", "start_line": 3},
        },
    )
    gh = FakeGh([planted])
    _run(gh)
    (text,) = gh.bodies
    assert "MESSAGE TEXT" not in text and "Uncontrolled data" not in text
    assert "`src/a.py ## planted heading`, line 3" in text
    assert not any(line.startswith("## planted") for line in text.splitlines())


def test_a_commit_no_pull_request_names_says_so() -> None:
    answers: list[object] = [[], subprocess.CalledProcessError(1, ["gh"]), {"message": "x"}]
    for pulls in answers:
        gh = FakeGh([_alert(501)], pulls=pulls)
        _run(gh)
        assert f"{COMMIT}, merged by no pull request listed" in gh.bodies[0]


def test_an_instance_with_no_commit_reads_nothing_more() -> None:
    gh = FakeGh([_alert(501, most_recent_instance={"commit_sha": "HEAD; rm -rf /"})])
    _run(gh)
    assert "that instance's commit: not named, merged by no pull request listed" in gh.bodies[0]
    assert not any(c[-1].endswith("/pulls") for c in gh.calls)


# ── the command ───────────────────────────────────────────────────────────────────────────────


def test_an_alert_with_no_number_refuses_the_run(monkeypatch, capsys) -> None:
    gh = FakeGh([_alert(501), {"number": None, "created_at": NEW}])
    monkeypatch.setattr(codeql_issues, "_gh", gh)
    assert codeql_issues.main(["--repo", REPO]) == 2
    assert "an alert names no number" in capsys.readouterr().err
    assert gh.writes() == []


def test_the_command_says_what_it_did(monkeypatch, capsys) -> None:
    gh = FakeGh([_alert(501), _alert(444, created=OLD)], [_issue(7001, 400)])
    monkeypatch.setattr(codeql_issues, "_gh", gh)
    assert codeql_issues.main(["--repo", REPO]) == 0
    assert capsys.readouterr().out.strip() == (
        "codeql-issues: 2 alert(s) open on main; opened 1, reopened 0, closed 1"
    )


def test_a_failed_write_exits_one(monkeypatch, capsys) -> None:
    gh = FakeGh([_alert(501)])

    def failing(*args: str) -> str:
        if "-X" in args:
            raise subprocess.CalledProcessError(1, ["gh"])
        return gh(*args)

    monkeypatch.setattr(codeql_issues, "_gh", failing)
    assert codeql_issues.main(["--repo", REPO]) == 1
    assert "a change to the issues failed" in capsys.readouterr().err
