"""``python -m tools.ci codeql-issues``: one issue for each new CodeQL alert open on ``main``.

Under the merge queue, a queue commit is judged with its pull request's head's CodeQL analysis,
and that analysis sees neither what merged after the head's verdict nor what was queued ahead of
it. So a dataflow that only two pull requests bring together (one a source, the other a sink) can
reach ``main`` with no analysis having seen it. That gap is accepted only with this behind it:
when GitHub's CodeQL analysis of ``main`` completes, and hourly besides,
``.github/workflows/codeql-alerts.yml`` runs this, and it keeps one issue for each alert open on
``main`` that was made on or after :data:`SINCE`.

**What counts.** Each open code-scanning alert on ``refs/heads/main`` made on or after
:data:`SINCE`, the day before main's merges could first be queued. The alerts open before that
are main's known ones, which no queue brought.

**Only its own issues.** An issue is this job's when the workflow's account opened it, it carries
:data:`LABEL`, its title starts with :data:`PREFIX`, and it is no pull request. No other issue,
of any title or label, is ever edited, reopened, closed or counted.

**One issue an alert.** An alert with no issue of its own gets one. An alert whose issue was
closed (it was fixed or dismissed, then opened again) has the newest of its issues reopened. An
open issue whose alert is no longer open on ``main`` is closed, with a comment that says whether
the alert was fixed or dismissed; that happens only when the alerts were read whole, so a listing
cut short closes nothing. Two open issues of one title keep the older and name the other.

**What a body holds.** The alert's number and link, its rule's id and severity, the file and line
of its most recent instance on ``main``, and that instance's commit with the pull request that
merged it. Nothing of the alert's message or the code it points at is copied in.

Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import report

#: The label each of this job's issues carries.
LABEL = "codeql-alert"
LABEL_COLOR = "b60205"
LABEL_DESCRIPTION = "A CodeQL alert on main that no pull request's analysis saw"
#: Each of this job's titles starts so, then names the alert's number and rule.
PREFIX = "CodeQL alert #"
#: GitHub refuses a longer title.
TITLE_LIMIT = 256
#: The first day counted: the day before main's merges could first be queued (0.4.0 A1). An
#: alert made earlier is main's known one.
SINCE = "2026-11-01T00:00:00Z"
#: Pages of 100 read at most, of the alerts and of this job's issues.
PAGES = 10
#: How long one ``gh`` call may take.
GH_TIMEOUT_SECONDS = 120
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SHA = re.compile(r"[0-9a-f]{40}")
_NUMBER = re.compile(re.escape(PREFIX) + r"([1-9][0-9]*) on main: ")


class Refused(Exception):
    """A listing this job cannot judge by."""


@dataclass(frozen=True, slots=True)
class Alert:
    """One open alert on main, as much of it as a body names."""

    number: int
    rule: str
    severity: str
    path: str
    line: int
    commit: str
    url: str
    created: str


def _plain(value: object) -> str:
    """A field on one line: each run of control characters is one space."""
    return _CONTROL.sub(" ", value if isinstance(value, str) else "").strip()


def _field(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


def read_alert(raw: Mapping[str, Any]) -> Alert:
    """One alert of the listing, or :class:`Refused` for one that names no number."""
    number = raw.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise Refused(f"an alert names no number: {number!r}")
    rule = _field(raw, "rule")
    instance = _field(raw, "most_recent_instance")
    where = _field(instance, "location")
    line = where.get("start_line")
    return Alert(
        number=number,
        rule=_plain(rule.get("id")) or "an unnamed rule",
        severity=_plain(rule.get("security_severity_level") or rule.get("severity")) or "unrated",
        path=_plain(where.get("path")),
        line=line if isinstance(line, int) and not isinstance(line, bool) else 0,
        commit=_plain(instance.get("commit_sha")),
        url=_plain(raw.get("html_url")),
        created=_plain(raw.get("created_at")),
    )


def title(alert: Alert) -> str:
    return f"{PREFIX}{alert.number} on main: {alert.rule}"[:TITLE_LIMIT]


def _alert_of(name: str) -> int | None:
    """The alert number one of this job's titles names."""
    found = _NUMBER.match(name)
    return int(found.group(1)) if found else None


def _by_bot(user: object) -> bool:
    return (
        isinstance(user, Mapping) and user.get("login") == report.BOT and user.get("type") == "Bot"
    )


def _ours(issue: object) -> bool:
    """Whether *issue* is this job's: opened by the workflow's account, labelled, titled so."""
    return (
        isinstance(issue, Mapping)
        and "pull_request" not in issue
        and _by_bot(issue.get("user"))
        and isinstance(issue.get("title"), str)
        and _alert_of(issue["title"]) is not None
        and any(
            isinstance(lab, Mapping) and lab.get("name") == LABEL
            for lab in issue.get("labels") or []
        )
    )


def open_alerts(repo: str, gh: Callable[..., str]) -> tuple[list[Alert], bool]:
    """The alerts open on main, and whether the listing was read whole."""
    found: list[Alert] = []
    for page in range(1, PAGES + 1):
        batch = json.loads(
            gh(
                "api",
                f"repos/{repo}/code-scanning/alerts?ref=refs/heads/main&state=open"
                f"&per_page=100&page={page}",
            )
        )
        if not isinstance(batch, list):
            raise Refused("the alerts listing is not a list")
        found += [read_alert(a) for a in batch if isinstance(a, Mapping)]
        if len(batch) < 100:
            return found, True
    return found, False


def own_issues(repo: str, gh: Callable[..., str]) -> dict[str, list[dict[str, Any]]]:
    """This job's issues, open and closed, by title, oldest first."""
    by: dict[str, list[dict[str, Any]]] = {}
    for page in range(1, PAGES + 1):
        batch = json.loads(
            gh(
                "api",
                f"repos/{repo}/issues?state=all&creator=github-actions%5Bbot%5D&labels={LABEL}"
                f"&per_page=100&page={page}",
            )
        )
        if not isinstance(batch, list):
            raise Refused("the issues listing is not a list")
        for issue in batch:
            if _ours(issue):
                by.setdefault(issue["title"], []).append(dict(issue))
        if len(batch) < 100:
            break
    else:
        raise Refused(f"more than {PAGES * 100} of this job's issues: some may be unseen")
    for issues in by.values():
        issues.sort(key=lambda i: int(i["number"]))
    return by


def body(alert: Alert, merged_by: Sequence[int]) -> str:
    where = f"`{alert.path}`, line {alert.line}" if alert.path else "no file named"
    commit = alert.commit if _SHA.fullmatch(alert.commit) else "not named"
    merged = ", ".join(f"#{n}" for n in merged_by) or "no pull request listed"
    return "\n".join(
        [
            f"CodeQL's analysis of main reports alert [#{alert.number}]({alert.url}), made"
            f" {alert.created or 'at an unnamed time'}.",
            "",
            f"- rule: `{alert.rule}`, severity {alert.severity}",
            f"- its most recent instance: {where}",
            f"- that instance's commit: {commit}, merged by {merged}",
            "",
            "A queue commit is judged with its pull request's head's analysis, so an alert that"
            " only two pull requests bring together reaches main unseen until main's own"
            " analysis. When a queue merged several at once, the alert may have come with an"
            " earlier one of them. This issue closes once the alert is fixed or dismissed.",
            "",
        ]
    )


@dataclass(frozen=True, slots=True)
class Plan:
    """What one run does to the issues."""

    open: tuple[Alert, ...] = ()
    reopen: tuple[tuple[int, Alert], ...] = ()  # (issue number, its alert)
    close: tuple[tuple[int, int], ...] = ()  # (issue number, its alert's number)
    twins: tuple[tuple[int, tuple[int, ...]], ...] = ()  # (the issue kept, its open twins)


def plan(
    alerts: Iterable[Alert], issues: Mapping[str, Sequence[Mapping[str, Any]]], *, whole: bool
) -> Plan:
    """What to do, from the alerts open on main and this job's issues by title; *whole* says
    whether every alert was read."""
    counted = {title(a): a for a in sorted(alerts, key=lambda a: a.number) if a.created >= SINCE}
    opened: list[Alert] = []
    reopened: list[tuple[int, Alert]] = []
    twins: list[tuple[int, tuple[int, ...]]] = []
    for name, alert in counted.items():
        mine = issues.get(name, ())
        live = [int(i["number"]) for i in mine if i.get("state") == "open"]
        if len(live) > 1:
            twins.append((live[0], tuple(live[1:])))
        if live:
            continue
        if mine:
            reopened.append((int(mine[-1]["number"]), alert))
        else:
            opened.append(alert)
    closing: list[tuple[int, int]] = []
    if whole:
        for name, mine in issues.items():
            number = _alert_of(name)
            if name in counted or number is None:
                continue
            closing += [(int(i["number"]), number) for i in mine if i.get("state") == "open"]
    return Plan(tuple(opened), tuple(reopened), tuple(sorted(closing)), tuple(twins))


def _gh(*args: str) -> str:
    done = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    return done.stdout


def _ensure_label(repo: str, gh: Callable[..., str]) -> None:
    try:
        gh("api", f"repos/{repo}/labels/{LABEL}")
    except subprocess.CalledProcessError:
        gh(
            "api",
            "-X",
            "POST",
            f"repos/{repo}/labels",
            "-f",
            f"name={LABEL}",
            "-f",
            f"color={LABEL_COLOR}",
            "-f",
            f"description={LABEL_DESCRIPTION}",
        )


def merged_by(repo: str, commit: str, gh: Callable[..., str]) -> list[int]:
    """The pull requests that merged *commit*, or none when it names no commit or the read
    fails: a body without them still names the alert."""
    if not _SHA.fullmatch(commit):
        return []
    try:
        pulls = json.loads(gh("api", f"repos/{repo}/commits/{commit}/pulls"))
    except (subprocess.SubprocessError, ValueError):
        return []
    if not isinstance(pulls, list):
        return []
    return sorted(
        p["number"]
        for p in pulls
        if isinstance(p, Mapping) and isinstance(p.get("number"), int) and p["number"] > 0
    )


def gone(repo: str, number: int, gh: Callable[..., str]) -> str:
    """Why alert *number* is no longer open on main, for the closing comment."""
    try:
        alert = json.loads(gh("api", f"repos/{repo}/code-scanning/alerts/{number}"))
    except (subprocess.SubprocessError, ValueError):
        alert = None
    state = alert.get("state") if isinstance(alert, Mapping) else None
    if state == "dismissed":
        reason = _plain(alert.get("dismissed_reason")) if isinstance(alert, Mapping) else ""
        return f"Alert #{number} was dismissed" + (f" ({reason})." if reason else ".")
    if state == "fixed":
        return f"Alert #{number} is fixed on main."
    return f"Alert #{number} is no longer open on main."


def apply(done: Plan, repo: str, *, gh: Callable[..., str] = _gh) -> None:
    """Make *done* so: one call an open or reopen (with one read for its body's pull requests),
    and for a close one read, its comment, then the close."""
    if done.open or done.reopen:
        _ensure_label(repo, gh)
    with tempfile.TemporaryDirectory() as scratch:

        def file(alert: Alert) -> str:
            path = Path(scratch) / "body.md"
            path.write_text(body(alert, merged_by(repo, alert.commit, gh)), encoding="utf-8")
            return f"body=@{path}"

        for alert in done.open:
            gh(
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues",
                "-f",
                f"title={title(alert)}",
                "-F",
                file(alert),
                "-f",
                f"labels[]={LABEL}",
            )
        for number, alert in done.reopen:
            gh(
                "api",
                "-X",
                "PATCH",
                f"repos/{repo}/issues/{number}",
                "-f",
                "state=open",
                "-F",
                file(alert),
            )
    for number, alert_number in done.close:
        gh(
            "api",
            "-X",
            "POST",
            f"repos/{repo}/issues/{number}/comments",
            "-f",
            f"body={gone(repo, alert_number, gh)} Closed.",
        )
        gh(
            "api",
            "-X",
            "PATCH",
            f"repos/{repo}/issues/{number}",
            "-f",
            "state=closed",
            "-f",
            "state_reason=completed",
        )
    for kept, others in done.twins:
        print(
            f"::warning title=more than one issue for one CodeQL alert::kept #{kept};"
            f" close {', '.join(f'#{n}' for n in others)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci codeql-issues",
        description=(__doc__ or "").splitlines()[0],
    )
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    try:
        alerts, whole = open_alerts(args.repo, _gh)
        done = plan(alerts, own_issues(args.repo, _gh), whole=whole)
    except (Refused, subprocess.SubprocessError, ValueError) as exc:
        print(f"codeql-issues: {exc}", file=sys.stderr)
        return 2
    try:
        apply(done, args.repo, gh=_gh)
    except subprocess.SubprocessError as exc:
        print(f"codeql-issues: a change to the issues failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"codeql-issues: {len(alerts)} alert(s) open on main; opened {len(done.open)},"
        f" reopened {len(done.reopen)}, closed {len(done.close)}"
        + ("" if whole else f" (more than {PAGES * 100} alerts: none closed)")
    )
    return 0
