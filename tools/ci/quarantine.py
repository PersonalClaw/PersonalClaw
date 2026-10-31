"""``python -m tools.ci quarantine add``: one quarantine entry that the list's rules take.

When a person looks at a red that ran out of retries and finds a known flake (0.4.0 A5's outcome
(a)), the fix is a quarantine entry, and this writes it: the test's id from its flaky-test issue,
that issue's number, the login of whoever owns the fix, the expiry and a one-line reason.

**What it refuses before writing**, each with the rule's own words: a rail (``tests/rails/``),
an id that is not ``tests/<file>.py::<test>``, an issue that is no number from 1, an assignee that
is no GitHub login, a reason that is empty or more than one line, a test already listed, an
eleventh entry, and a first expiry more than :data:`FIRST_DAYS` days out (a renewal follows a
recorded look and edits its entry by hand, inside the file's 30 days).

**What it checks after writing.** The list is read again with ``tests/quarantine.py``'s own rules
(``problems``), in a child interpreter, since that module imports pytest and nothing in
``tools/ci`` does. A list they refuse (a security-marked test, an id whose file, class, test or
case does not exist as spelt) is put back exactly as it was, and their words are printed. What
only collection shows (a mark a hook adds, a case that is gone) is checked by the run of the pull
request that adds the entry, as every run checks it.

It writes only the list, appending one ``[[entry]]`` table and keeping every line above it.
Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

#: The list, relative to the repository root (``tests/quarantine.py``'s ``LIST``).
LIST = "tests/quarantine.toml"
#: At most this many entries (``tests/quarantine.py``'s ``CAP``).
CAP = 10
#: A first entry expires at most this many days out (A5's opening expiry); the file allows 30.
FIRST_DAYS = 14
#: The repository whose ``tests/quarantine.py`` judges the list.
REPO = Path(__file__).resolve().parents[2]
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
_NODEID = re.compile(r"tests/[^\s:]+\.py::\S.*")
#: Reads the list at argv[1] with the rules of the tests folder at argv[2], one problem a line.
_CHECK = (
    "import sys, pathlib; sys.path[:0] = [sys.argv[2]]; import quarantine; "
    "print('\\n'.join(quarantine.problems(pathlib.Path(sys.argv[1]), quarantine.today())))"
)


class Refused(Exception):
    """An entry the quarantine's rules would not take."""


def today() -> datetime.date:
    """The day the entry is written, in UTC, as the rules read it."""
    return datetime.datetime.now(datetime.UTC).date()


def entry_text(nodeid: str, issue: int, assignee: str, expires: datetime.date, reason: str) -> str:
    """One ``[[entry]]`` table; each string a TOML basic string (JSON's escapes are TOML's)."""
    return (
        "\n[[entry]]\n"
        f"nodeid = {json.dumps(nodeid)}\n"
        f"issue = {issue}\n"
        f"assignee = {json.dumps(assignee)}\n"
        f"expires = {expires.isoformat()}\n"
        f"reason = {json.dumps(reason)}\n"
    )


def refusals(
    entries: Sequence[dict[str, object]],
    *,
    nodeid: str,
    issue: int,
    assignee: str,
    days: int,
    reason: str,
) -> list[str]:
    """Why the entry may not be added, before anything is written."""
    bad: list[str] = []
    if nodeid.startswith("tests/rails/"):
        bad.append(f"{nodeid} is a rail: a rail is never quarantined")
    elif not _NODEID.fullmatch(nodeid):
        bad.append(f"{nodeid} is not a test id (tests/<file>.py::<test>)")
    if issue < 1:
        bad.append(f"issue {issue} is no issue number")
    if not _LOGIN.fullmatch(assignee):
        bad.append(f"{assignee!r} is no GitHub login")
    if not reason.strip() or reason.splitlines() != [reason]:
        bad.append("the reason must be one line: what fails, and why it is waived")
    if not 1 <= days <= FIRST_DAYS:
        bad.append(
            f"a first entry expires 1 to {FIRST_DAYS} days out, not {days}: a renewal follows a"
            " recorded look"
        )
    if any(e.get("nodeid") == nodeid for e in entries):
        bad.append(f"{nodeid} is listed already: renew its entry instead")
    if len(entries) >= CAP:
        bad.append(f"{LIST} holds {len(entries)} entries: at most {CAP} may be quarantined")
    return bad


def _judged(root: Path) -> list[str]:
    """What ``tests/quarantine.py``'s rules say of the list at *root*."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", _CHECK, str(root), str(REPO / "tests")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        raise Refused(f"the quarantine's rules could not be read ({tail})")
    return [line for line in done.stdout.splitlines() if line.strip()]


def add(
    root: Path,
    *,
    nodeid: str,
    issue: int,
    assignee: str,
    days: int = FIRST_DAYS,
    reason: str,
    day: datetime.date | None = None,
) -> str:
    """Append the entry to *root*'s list and return it, or put the list back and refuse."""
    path = root / LIST
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        data = tomllib.loads(before)
    except tomllib.TOMLDecodeError as exc:
        raise Refused(f"{LIST} does not parse: {exc}") from exc
    entries = data.get("entry", [])
    if not isinstance(entries, list):
        raise Refused(f"{LIST}: entry must be an array of tables, [[entry]]")
    bad = refusals(
        [e for e in entries if isinstance(e, dict)],
        nodeid=nodeid,
        issue=issue,
        assignee=assignee,
        days=days,
        reason=reason,
    )
    if bad:
        raise Refused("\n".join(bad))
    expires = (day or today()) + datetime.timedelta(days=days)
    text = entry_text(nodeid, issue, assignee, expires, reason)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(before + ("" if not before or before.endswith("\n") else "\n") + text, "utf-8")
    try:
        problems = _judged(root)
    except (Refused, subprocess.SubprocessError, OSError):
        path.write_text(before, encoding="utf-8")
        raise
    if problems:
        path.write_text(before, encoding="utf-8")
        raise Refused("\n".join(problems))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci quarantine",
        description=(__doc__ or "").splitlines()[0],
    )
    commands = parser.add_subparsers(dest="command", required=True)
    adding = commands.add_parser("add", help="add one entry")
    adding.add_argument("--nodeid", required=True, help="the test's id, from its flaky-test issue")
    adding.add_argument("--issue", type=int, required=True, help="the flaky-test issue's number")
    adding.add_argument("--assignee", required=True, help="the GitHub login who owns the fix")
    adding.add_argument("--reason", required=True, help="one line: what fails, and why waived")
    adding.add_argument("--days", type=int, default=FIRST_DAYS, help="days to its expiry")
    adding.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        text = add(
            args.root,
            nodeid=args.nodeid,
            issue=args.issue,
            assignee=args.assignee,
            days=args.days,
            reason=args.reason,
        )
    except (Refused, subprocess.SubprocessError, OSError) as exc:
        print(f"quarantine add: refused, {LIST} is unchanged:\n{exc}", file=sys.stderr)
        return 1
    print(f"quarantine add: {LIST} gained{text}")
    return 0
