"""``python -m tools.ci flaky-issues``: one issue for each test that keeps passing as a flake.

The merge verdict passes a pull request run whose only failures are flakes, at most two, each
rerun alone (#3948's flake rule), so a test can flake on every run and never be seen again. Once a
day, ``.github/workflows/flaky-tests.yml`` fetches the last 14 days of main's per-test histories
(``python -m tools.ci fetch --days 15 --no-carry``) and this keeps one issue for each test that
passed as a flake :data:`OPEN_AT` or more times in the last :data:`OPEN_DAYS` days.

**What counts.** Each job record's failed test that its rerun classified ``flake``: a ``record``
line, the push's or Breadth's own attempt, and a ``tested_record`` line (version 6), the pull
request run a tested push took its suite from, whose flakes are the ones the verdict passed. Each
test counts once per run and attempt. A record is where every runner's classification lands,
Playwright's included, under one id; a junit test line names the same test another way, so it is
never counted beside it.

**Only its own issues.** An issue is this job's when the workflow's account opened it (its
creator and its author are ``github-actions[bot]``), it carries :data:`LABEL`, its title is one
:func:`title` gives, and it is no pull request. They are listed through the issues API, page by
page, never search, which lags behind a new issue. Every other issue, of any title or label, is
never edited, reopened, closed or counted. A test's title is its id after :data:`PREFIX`, cut to
GitHub's limit with the id's digest after it, so the next run finds it by the same title.

**One issue a test.** A test with :data:`OPEN_AT` or more flakes in :data:`OPEN_DAYS` days has its
open issue edited. Or, when its issue was closed (a fix that did not hold), that issue is reopened
and edited once :data:`OPEN_AT` flakes came after it closed. Or one is opened when it has none. Two
own issues of one title (a race) edit the older and name the other.

**Closing.** An open issue whose test passed as no flake in :data:`CLOSE_DAYS` days is closed, with
a comment that says so, but only when the window holds all of those days: the fetch left none of
its histories unread, the oldest was made :data:`CLOSE_DAYS` or more days ago and the newest
within :data:`FRESH_DAYS`. A short window, or one the collector stopped filling, closes nothing. A
pull request that says ``Fixes #N`` closes it through GitHub's own keywords.

**Queue runs** (``telemetry.QUEUE_EVENT``). A merge queue run tests what is enqueued for main, and
its commit reaches main only when its group is not torn down. Its flakes count as any run's, so
they open, edit and reopen issues and keep one open: each only asks a person to look. But a close
stops people watching, so a queue run's history counts toward the window's span only once its
commit is on main (:func:`proved`, asked of the API only for a history that would widen the span
main's own histories give). A commit that cannot be read is not on main.

**No log text.** A body holds the test's id, in a fence longer than any run of backticks in it,
the counts, and each flake's run link and day. Nothing else of a history is read into it: a
history holds no failure message, and a record's ``why`` is never read.

Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fetch, report, telemetry

#: The label each of this job's issues carries.
LABEL = "flaky-test"
LABEL_COLOR = "d93f0b"
LABEL_DESCRIPTION = "A test that keeps passing only when rerun alone"
#: Each issue's title: this, then the test's id.
PREFIX = "Flaky test: "
#: GitHub refuses a longer title.
TITLE_LIMIT = 256
#: Flakes in :data:`OPEN_DAYS` days that open, edit or reopen a test's issue.
OPEN_AT = 2
OPEN_DAYS = 7
#: Days with no flake after which a test's open issue is closed.
CLOSE_DAYS = 14
#: The newest history a window that closes issues holds is at most this many days old: the
#: daily run comes after Breadth's slot, whose history may not be collected yet.
FRESH_DAYS = 2
#: The flakes a body lists at most, newest first; its counts hold every one.
LINKS_SHOWN = 20
#: Pages of 100 issues read at most when listing this job's own.
ISSUE_PAGES = 10
#: How long one ``gh`` call may take.
GH_TIMEOUT_SECONDS = 120
#: Characters no test id carries: a control character could end a title or a fence.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Refused(Exception):
    """A window, a status or an issue listing this job cannot judge by."""


@dataclass(frozen=True, slots=True)
class Flake:
    """One run attempt in which a test passed as a flake."""

    test: str
    run_id: int
    attempt: int
    at: datetime.datetime


# ── the window ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Window:
    """The window's flakes, when its oldest and newest histories' runs were made (a queue run's
    only once :func:`proved` finds its commit on main), and each queue run's history: when its run
    was made and the commit it ran on."""

    flakes: tuple[Flake, ...]
    first: datetime.datetime | None
    last: datetime.datetime | None
    queued: tuple[tuple[datetime.datetime, str], ...] = ()


def read(window: Path) -> Window:
    """Every flake the window's histories hold, once per test, run and attempt, each dated by the
    history's run (a tested run's by the push that took its suite). A history that cannot be read
    refuses the whole window, as the weekly report does."""
    seen: dict[tuple[str, int, int], Flake] = {}
    made: list[datetime.datetime] = []
    queued: list[tuple[datetime.datetime, str]] = []
    try:
        for path in report.sources(window, report.HISTORY_NAME):
            head, own, tested = report.run_records(path)
            at = _when(head.get("created_at"), path.name)
            if head.get("event") == telemetry.QUEUE_EVENT:
                queued.append((at, str(head.get("sha") or "")))
            else:
                made.append(at)
            run_id, attempt = int(head["run_id"]), int(head["attempt"])
            for line in own:
                for test in _flaky(line):
                    seen.setdefault((test, run_id, attempt), Flake(test, run_id, attempt, at))
            for line in tested:
                theirs = int(line["run_id"])
                for test in _flaky(line):
                    seen.setdefault((test, theirs, 1), Flake(test, theirs, 1, at))
    except report.SchemaError as exc:
        raise Refused(str(exc)) from None
    found = sorted(seen.values(), key=lambda f: (f.test, f.at, f.run_id, f.attempt))
    return Window(
        tuple(found), min(made, default=None), max(made, default=None), tuple(sorted(queued))
    )


def proved(found: Window, on_main: Callable[[str], bool]) -> Window:
    """The window with the queue runs whose commits are on main counted toward its span: the
    oldest such history older than main's own oldest, and the newest newer than its newest.
    Only those are asked about, in that order, so a window main's own histories span asks
    nothing; each other queue run counts for flakes alone."""
    first, last = found.first, found.last
    for at, sha in found.queued:
        if first is not None and at >= first:
            break
        if on_main(sha):
            first = at
            break
    for at, sha in reversed(found.queued):
        if last is not None and at <= last:
            break
        if on_main(sha):
            last = at
            break
    return dataclasses.replace(found, first=first, last=last)


#: A full commit id, as a queue run's history names the commit it ran on.
_COMMIT = re.compile(r"[0-9a-f]{40}")


def on_main(repo: str, gh: Callable[..., str]) -> Callable[[str], bool]:
    """Whether a commit is on main: main is it, or holds it (the API's comparison of the commit
    with main says ``identical`` or ``ahead``). A commit that cannot be read, or that is no full
    id, is not."""

    def ask(sha: str) -> bool:
        if not _COMMIT.fullmatch(sha):
            return False
        try:
            status = gh("api", f"repos/{repo}/compare/{sha}...main", "--jq", ".status").strip()
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"::warning title=a queue run's commit not read::{sha}: {exc}")
            return False
        return status in ("identical", "ahead")

    return ask


def _when(value: object, where: str) -> datetime.datetime:
    """A GitHub time (``…Z``), as an aware datetime."""
    try:
        at = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        at = None
    if not isinstance(value, str) or at is None or at.tzinfo is None:
        raise Refused(f"{where}: not a time: {value!r}")
    return at


def _flaky(line: Mapping[str, Any]) -> list[str]:
    return [t["test"] for t in line["rerun"] if t["rerun_result"] == telemetry.FLAKE]


def complete(status: Path) -> bool:
    """Whether the fetch read every history of its window: none left for a later run, none
    refused, none dropped."""
    try:
        counts = report.read_fetch_status(status)
    except (OSError, ValueError, report.SchemaError) as exc:
        raise Refused(f"the fetch's status cannot be read: {exc}") from None
    return counts["left"] == 0 and counts["refused"] == 0 and counts["dropped"] == 0


def covers(found: Window, today: datetime.date) -> bool:
    """Whether the window's histories span the last :data:`CLOSE_DAYS` days: the oldest made that
    many days or more before *today*, the newest at most :data:`FRESH_DAYS`."""
    if found.first is None or found.last is None:
        return False
    back = (today - found.first.date()).days
    fresh = (today - found.last.date()).days
    return back >= CLOSE_DAYS and fresh <= FRESH_DAYS


# ── the issues ───────────────────────────────────────────────────────────────────────────────


def title(test: str) -> str:
    """A test's issue title: :data:`PREFIX` and its id, or, past :data:`TITLE_LIMIT`, as much of
    the id as fits, then ``…`` and the id's digest, so the same id always gives the same title.
    """
    whole = PREFIX + test
    if len(whole) <= TITLE_LIMIT:
        return whole
    tail = f"… ({hashlib.sha256(test.encode('utf-8')).hexdigest()[:16]})"
    return PREFIX + test[: TITLE_LIMIT - len(PREFIX) - len(tail)] + tail


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
        and issue["title"].startswith(PREFIX)
        and any(
            isinstance(lab, Mapping) and lab.get("name") == LABEL
            for lab in issue.get("labels") or []
        )
    )


def own_issues(repo: str, gh: Callable[..., str]) -> dict[str, list[dict[str, Any]]]:
    """This job's issues, open and closed, by title, oldest first."""
    by: dict[str, list[dict[str, Any]]] = {}
    for page in range(1, ISSUE_PAGES + 1):
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
        raise Refused(f"more than {ISSUE_PAGES * 100} of this job's issues: some may be unseen")
    for issues in by.values():
        issues.sort(key=lambda i: int(i["number"]))
    return by


def _fence(text: str) -> str:
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def body(test: str, found: Sequence[Flake], *, repo: str, today: datetime.date) -> str:
    """A test's issue body: its id, its counts and its flakes' run links. Nothing else."""
    week = [f for f in found if f.at.date() > today - datetime.timedelta(days=OPEN_DAYS)]
    fence = _fence(test)
    lines = [
        f"This test passed as a flake {len(week)} times in the last {OPEN_DAYS} days"
        f" ({len(found)} in {CLOSE_DAYS}): it failed in its run, and passed when it was rerun"
        " alone.",
        "",
        f"{fence}text",
        test,
        fence,
        "",
        "| Day (UTC) | Run |",
        "|---|---|",
    ]
    for flake in sorted(found, key=lambda f: f.at, reverse=True)[:LINKS_SHOWN]:
        url = f"https://github.com/{repo}/actions/runs/{flake.run_id}/attempts/{flake.attempt}"
        run = f"[run {flake.run_id}, attempt {flake.attempt}]({url})"
        lines.append(f"| {flake.at.date().isoformat()} | {run} |")
    if len(found) > LINKS_SHOWN:
        lines.append(f"| … | {len(found) - LINKS_SHOWN} more |")
    lines += [
        "",
        f"It closes itself after {CLOSE_DAYS} days with no flake, or when a pull request that"
        " says `Fixes` and this issue's number merges.",
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class Plan:
    """What one run does to the issues: each opened, edited, reopened or closed, by title."""

    open: tuple[tuple[str, str], ...] = ()  # (title, body)
    edit: tuple[tuple[int, str], ...] = ()  # (number, body)
    reopen: tuple[tuple[int, str], ...] = ()  # (number, body)
    close: tuple[int, ...] = ()
    #: Two own issues of one title: the one edited, and the others.
    twins: tuple[tuple[int, tuple[int, ...]], ...] = ()


def plan(
    found: Iterable[Flake],
    issues: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    repo: str,
    today: datetime.date,
    whole: bool,
) -> Plan:
    """What to do, from the window's flakes and this job's issues, on *today*; *whole* says
    whether the window holds every history of the last :data:`CLOSE_DAYS` days."""
    since = today - datetime.timedelta(days=CLOSE_DAYS)
    week = today - datetime.timedelta(days=OPEN_DAYS)
    by_test: dict[str, list[Flake]] = {}
    for flake in found:
        if flake.at.date() > since:
            by_test.setdefault(flake.test, []).append(flake)
    opened: list[tuple[str, str]] = []
    edited: list[tuple[int, str]] = []
    reopened: list[tuple[int, str]] = []
    twins: list[tuple[int, tuple[int, ...]]] = []
    flaky_titles: set[str] = set()
    for test, its in sorted(by_test.items()):
        if _CONTROL.search(test) or not test.strip():
            print(f"::warning title=a test id no issue can name::{test!r}")
            continue
        name = title(test)
        flaky_titles.add(name)
        if sum(f.at.date() > week for f in its) < OPEN_AT:
            continue
        text = body(test, its, repo=repo, today=today)
        mine = list(issues.get(name, ()))
        live = [i for i in mine if i.get("state") == "open"]
        if live:
            edited.append((int(live[0]["number"]), text))
            if len(live) > 1:
                twins.append((int(live[0]["number"]), tuple(int(i["number"]) for i in live[1:])))
        elif mine:
            newest = mine[-1]
            closed = _when(newest.get("closed_at"), f"issue #{newest['number']}")
            if sum(f.at > closed for f in its) >= OPEN_AT:
                reopened.append((int(newest["number"]), text))
        else:
            opened.append((name, text))
    closing: list[int] = []
    if whole:
        for name, its_issues in sorted(issues.items()):
            if name in flaky_titles:
                continue
            closing += [int(i["number"]) for i in its_issues if i.get("state") == "open"]
    return Plan(
        tuple(opened),
        tuple(edited),
        tuple(reopened),
        tuple(sorted(closing)),
        tuple(twins),
    )


# ── writing ──────────────────────────────────────────────────────────────────────────────────


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


def apply(done: Plan, repo: str, *, gh: Callable[..., str] = _gh) -> None:
    """Make *done* so, each change one call (two for a close: its comment, then the close)."""
    if done.open:
        _ensure_label(repo, gh)
    with tempfile.TemporaryDirectory() as scratch:

        def file(text: str) -> str:
            path = Path(scratch) / "body.md"
            path.write_text(text, encoding="utf-8")
            return f"body=@{path}"

        for name, text in done.open:
            gh(
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues",
                "-f",
                f"title={name}",
                "-F",
                file(text),
                "-f",
                f"labels[]={LABEL}",
            )
        for number, text in done.edit:
            gh("api", "-X", "PATCH", f"repos/{repo}/issues/{number}", "-F", file(text))
        for number, text in done.reopen:
            gh(
                "api",
                "-X",
                "PATCH",
                f"repos/{repo}/issues/{number}",
                "-f",
                "state=open",
                "-F",
                file(text),
            )
        for number in done.close:
            gh(
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues/{number}/comments",
                "-f",
                f"body=No flake in the last {CLOSE_DAYS} days: closed.",
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
            f"::warning title=more than one issue for one flaky test::edited #{kept};"
            f" close {', '.join(f'#{n}' for n in others)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci flaky-issues",
        description=(__doc__ or "").splitlines()[0],
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--window", type=Path, required=True, help="the fetch's --out folder")
    args = parser.parse_args(argv)
    today = report.today()
    try:
        found = read(args.window)
        whole = complete(args.window / fetch.STATUS) and covers(
            proved(found, on_main(args.repo, _gh)), today
        )
        done = plan(
            found.flakes,
            own_issues(args.repo, _gh),
            repo=args.repo,
            today=today,
            whole=whole,
        )
    except (Refused, subprocess.SubprocessError, ValueError) as exc:
        print(f"flaky-issues: {exc}", file=sys.stderr)
        return 2
    try:
        apply(done, args.repo)
    except subprocess.SubprocessError as exc:
        print(f"flaky-issues: a change to the issues failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"flaky-issues: {len(found.flakes)} flake(s) in the window; opened {len(done.open)},"
        f" edited {len(done.edit)}, reopened {len(done.reopen)}, closed {len(done.close)}"
        + ("" if whole else f" (the window does not hold all {CLOSE_DAYS} days: none closed)")
    )
    return 0
