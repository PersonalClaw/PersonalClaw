"""Check every commit a pull request brings, or a push to ``main`` lands, against the project's
commit policy.

The rules:

1. Every commit carries ``Signed-off-by: <author name> <author email>`` as a line of its own,
   the author as git records it. Dependabot's own commits, by its one exact author address, are
   skipped: the bot cannot sign off. Any author can set that address, so the skip is as narrow as
   it can be, and what it lets through is still a pull request someone reviews.
2. Every committer is an identity ``MAINTAINERS`` lists, name and email both. Checked on
   ``main`` only: on a pull request a contributor is the committer of their own commits.
3. No commit a maintainer authored carries a co-author, a tool or session trailer, a "generated
   with" footer or a link to a tool's session. An author is a maintainer when their name or
   their email (either one, any case) is one ``MAINTAINERS`` lists. Commit metadata cannot tell
   a person from a tool, so a contributor's own co-author passes.
5. No commit message, and on a pull request no title, carries a marker that tells GitHub to
   start no workflow run (``[skip ci]``, ``[ci skip]``, ``[no ci]``, ``[skip actions]``,
   ``[actions skip]``, or a ``skip-checks: true`` trailer). A push to ``main`` whose head
   carries one starts no run, so this check never sees it, and the next push checks only its
   own range: those commits would land unchecked. The pull request's check is where the marker
   is stopped; on ``main`` a marker in a commit before the head is refused as well.

Rule 4, which binds a pushed commit to the tree its merge verdict verified, is not here: it needs
the record the trusted merge controller writes, and that record does not exist yet. Until it
does, nothing here can tell a squash merge from another commit GitHub writes:
``GitHub <noreply@github.com>`` is also the committer of a commit made in the web editor and of
a revert made from a pull request's page, and rule 2 passes all three.

Two modes, one per place the check runs:

``--mode pr --commits-json FILE --expected-count N --expected-head SHA --event FILE``
    Rules 1, 3 and 5, over a pull request's commits as ``gh api --paginate
    repos/OWNER/REPO/pulls/N/commits`` writes them (one JSON array per page). That endpoint
    returns at most 250 commits and pagination then stops without an error, so the count must
    equal ``N``, the pull request's own ``commits``. It answers for the pull request as it is
    when asked, so the list must also end at ``SHA``, the head the event names: a push after
    the event, or a list truncated or reordered, is refused. The title is read from the event
    payload.
    It reads no git object, so the pull request's commits never reach the disk of the job that
    holds a write token.
``--mode main --range BEFORE..AFTER``
    Rules 1, 2, 3 and 5, over the commits a push to ``main`` brings, read with git.

Each violation is one line, ``<sha> rule <n> <what was found>`` (``pull-request`` for the
title), on stdout and in ``--report`` when given. Every field a commit supplies is printed with
control characters and line breaks collapsed to spaces, so no name can start a line of its own.
The exit code is 0 for a clean range, 1 for any violation, and 2 when there is nothing that can
be checked: an unreadable ``MAINTAINERS``, an empty or unknown range, a commit whose raw object
holds a NUL, a commit list that is empty, malformed, short of its count or not ending at the
event's head. A check that cannot
run never passes, and it writes no report, removing any it finds.

It runs nothing from the repository it checks: no hook, no configured program, nothing but
``git rev-list``, ``git cat-file`` and ``git log``, with replace refs and mailmap off. It uses the
standard library only, so CI runs it on the runner image's own ``python3`` with nothing
installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: ``Name <email>  reason``: one identity per line, each with the reason it may commit.
_MAINTAINER_LINE = re.compile(r"^(?P<name>[^<>]*\S)\s+<(?P<email>[^<>\s]+)>\s+(?P<reason>\S.*)$")

#: Dependabot's author address, exactly as GitHub writes it on the bot's commits.
_DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"

#: A full commit id, which every sha the commits API returns is. Applied with ``fullmatch``
#: only: ``$`` under ``match`` also matches before a trailing newline, so ``"<40 hex>\n"`` would
#: pass as a full commit id.
_SHA = re.compile(r"[0-9a-f]{40}")

#: What rule 3 refuses on a maintainer's commit, matched case-insensitively at the start of a
#: line, so prose that quotes a trailer mid-line is not one. Each pattern says why it is here.
_TOOL_MARKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The trailer GitHub's squash merge and most tools add for another author.
    ("a co-author trailer", re.compile(r"^co-authored-by\s*:", re.I)),
    # Tools that record which tool or model wrote the change name it in a trailer of their own.
    ("a tool trailer", re.compile(r"^(generated|assisted)-by\s*:", re.I)),
    # A trailer whose key has `session` as one of its hyphen-separated words (Session-Id,
    # Agent-Session, X-Session-Url), so `Possession:` at the start of a line is not one.
    ("a session trailer", re.compile(r"^(?:[a-z0-9]+-)*session(?:-[a-z0-9]+)*\s*:", re.I)),
    # The footer line a tool appends to a message it wrote ("Generated with ...").
    ("a generated-with footer", re.compile(r"^\W*generated (with|by)\b", re.I)),
)

#: A link to a tool's session, shared conversation or chat, anywhere in a line: a path segment
#: that is one of those words followed by an id (`/sessions/…`, `/share/…`, `/chat/…`,
#: `/conversation/…`), a segment that is a session id (`/session_…`, `/session-…`), or a chat host
#: (`chat.example.com`). A page about sessions (`/docs/sessions.md`, `/guide/chat-history`) has
#: no id after the word, and is not one.
_SESSION_LINK = re.compile(
    r"https?://(?:chat\.[^\s/]+|[^\s/]+(?:/[^\s/]*)*?"
    r"/(?:(?:sessions?|shared?|chats?|conversations?)/[^\s/]|session[_-][^\s/.]))",
    re.I,
)

#: The markers that make GitHub start no workflow run for a push or a pull request.
_SKIP_MARKER = re.compile(
    r"\[(?:skip ci|ci skip|no ci|skip actions|actions skip)\]|^\s*skip-checks\s*:\s*true\s*$",
    re.I | re.M,
)


class Unusable(Exception):
    """There is nothing that can be checked, so the check refuses rather than passes."""


def _clean(text: str, limit: int = 160) -> str:
    """*text* on one line: every control, format and line-separator character a space, runs of
    whitespace one space, and at most *limit* characters. A commit's fields are its author's to
    write, and a carriage return or a newline in one would start a line the runner reads."""
    spaced = "".join(
        (
            " "
            if unicodedata.category(ch)[0] == "C" or unicodedata.category(ch) in ("Zl", "Zp")
            else ch
        )
        for ch in text
    )
    flat = " ".join(spaced.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class Maintainers:
    identities: frozenset[str]
    names: frozenset[str]
    emails: frozenset[str]

    def commits_as(self, name: str, email: str) -> bool:
        """Rule 2: the committer is a listed identity, name and email both, exactly."""
        return f"{name} <{email}>" in self.identities

    def is_one(self, name: str, email: str) -> bool:
        """Rule 3: the author shares a name or an email with a listed identity, in any case."""
        return _fold(name) in self.names or _fold(email) in self.emails


@dataclass(frozen=True)
class Commit:
    sha: str
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    message: str

    @property
    def author(self) -> str:
        return f"{self.author_name} <{self.author_email}>"

    @property
    def committer(self) -> str:
        return f"{self.committer_name} <{self.committer_email}>"


def read_maintainers(path: Path) -> Maintainers:
    """The identities ``MAINTAINERS`` lists. A line without a reason, or a file that lists
    nobody, is refused: an empty list would refuse every commit on ``main`` for a reason nobody
    could read off the output."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unusable(f"cannot read {path}: {exc}") from exc
    identities, names, emails = set(), set(), set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _MAINTAINER_LINE.match(line)
        if not match:
            raise Unusable(f"{path}:{number}: expected `Name <email>  reason`, found {line!r}")
        identities.add(f"{match['name']} <{match['email']}>")
        names.add(_fold(match["name"]))
        emails.add(_fold(match["email"]))
    if not identities:
        raise Unusable(f"{path} lists no identity")
    return Maintainers(frozenset(identities), frozenset(names), frozenset(emails))


def _git(*args: str) -> bytes:
    env = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "log.showSignature=false", *args],
        capture_output=True,
        env=env,
    )
    if done.returncode != 0:
        why = _clean(done.stderr.decode("utf-8", "replace"))
        raise Unusable(f"git {' '.join(args)} failed: {why}")
    return done.stdout


def commits_in_range(spec: str) -> list[Commit]:
    """The commits ``BEFORE..AFTER`` brings, oldest first, each read on its own. Both ends must
    name commits, and the range must hold at least one: a push that creates the branch
    (``BEFORE`` all zeros) has no range, and checking nothing is not a pass. A commit whose raw
    object holds a NUL is refused, since git's formatted fields stop at one."""
    before, sep, after = spec.partition("..")
    if not sep or not before or not after or "." in after:
        raise Unusable(f"--range must be BEFORE..AFTER, got {spec!r}")
    if set(before) == {"0"}:
        raise Unusable(f"{spec}: BEFORE is all zeros, so the push brings no range to check")
    for end in (before, after):
        _git("rev-parse", "--verify", "--quiet", f"{end}^{{commit}}")
    shas = _git("rev-list", "--reverse", f"{before}..{after}").decode("ascii").split()
    if not shas:
        raise Unusable(f"{spec} holds no commit")
    commits = []
    for sha in shas:
        if b"\0" in _git("cat-file", "commit", sha):
            raise Unusable(f"commit {sha} holds a NUL byte, so its fields cannot be read")
        record = _git(
            "log",
            "-1",
            "--no-mailmap",
            "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B",
            sha,
        ).decode("utf-8", "replace")
        fields = record.split("\x00")
        if len(fields) != 6 or fields[0] != sha:
            raise Unusable(f"commit {sha}'s record has {len(fields)} fields, not 6")
        commits.append(Commit(*fields))
    return commits


def commits_from_api(text: str, expected: int, head: str) -> list[Commit]:
    """A pull request's commits as the paginated commits API returns them: every one well
    formed, as many as the pull request has, and the last of them the head the event names. The
    API answers for the pull request as it is when asked, so a push made after the event, or a
    list truncated or reordered on the way, could hand this run another head's commits; a list
    that does not end at the event's head is refused."""
    if not _SHA.fullmatch(head):
        raise Unusable(f"--expected-head must be a full commit id, got {_clean(head)!r}")
    decoder = json.JSONDecoder()
    pages: list[Any] = []
    position = 0
    text = text.strip()
    while position < len(text):
        try:
            page, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise Unusable(f"the commit list is not JSON: {exc}") from exc
        if not isinstance(page, list):
            raise Unusable("the commit list is not a JSON array per page")
        pages.extend(page)
        while position < len(text) and text[position].isspace():
            position += 1
    commits = []
    for index, item in enumerate(pages):
        try:
            commit = item["commit"]
            fields = (
                item["sha"],
                commit["author"]["name"],
                commit["author"]["email"],
                commit["committer"]["name"],
                commit["committer"]["email"],
                commit["message"],
            )
        except (KeyError, TypeError) as exc:
            raise Unusable(f"commit {index} in the list lacks {_clean(str(exc))}") from exc
        if not all(isinstance(field, str) for field in fields) or not _SHA.fullmatch(fields[0]):
            raise Unusable(f"commit {index} in the list is not a commit the API returns")
        commits.append(Commit(*fields))
    if not commits:
        raise Unusable("the commit list is empty")
    if len(commits) != expected:
        raise Unusable(
            f"the list holds {len(commits)} commit(s) where the pull request has {expected}; the "
            "commits API returns at most 250, so a longer pull request cannot be checked here"
        )
    if commits[-1].sha != head:
        raise Unusable(
            f"the list ends at {commits[-1].sha}, not at the event's head {head}: the pull "
            "request moved after this run started, or the list is not its own, so it is not "
            "checked here"
        )
    return commits


def pull_request_title(event: Path) -> str:
    try:
        title = json.loads(event.read_text(encoding="utf-8"))["pull_request"]["title"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Unusable(f"cannot read the pull request's title from {event}: {exc}") from exc
    if not isinstance(title, str):
        raise Unusable(f"the pull request's title in {event} is not text")
    return title


def rule_1_signoff(commit: Commit) -> list[str]:
    if commit.author_email == _DEPENDABOT_EMAIL:
        return []
    expected = f"Signed-off-by: {commit.author}"
    if any(line.strip() == expected for line in commit.message.splitlines()):
        return []
    return [f"no `{_clean(expected)}` line, so the author has not signed off"]


def rule_2_committer(commit: Commit, maintainers: Maintainers) -> list[str]:
    if maintainers.commits_as(commit.committer_name, commit.committer_email):
        return []
    return [f"committer {_clean(commit.committer)} is not listed in MAINTAINERS"]


def rule_3_no_tool_marks(commit: Commit, maintainers: Maintainers) -> list[str]:
    if not maintainers.is_one(commit.author_name, commit.author_email):
        return []
    found = []
    for line in commit.message.splitlines():
        stripped = line.strip()
        for what, pattern in _TOOL_MARKS:
            if pattern.search(stripped):
                found.append(f"a maintainer's commit carries {what}: {_clean(stripped)}")
                break
        else:
            if _SESSION_LINK.search(stripped):
                found.append(f"a maintainer's commit carries a session link: {_clean(stripped)}")
    return found


def rule_5_no_skip_marker(text: str, where: str) -> list[str]:
    match = _SKIP_MARKER.search(text)
    if not match:
        return []
    return [f"{where} carries `{_clean(match.group(0))}`, which would start no workflow run"]


def check(commits: list[Commit], maintainers: Maintainers, mode: str) -> list[str]:
    """Every violation, one line each: ``<sha> rule <n> <what was found>``."""
    lines = []
    for commit in commits:
        found = [(1, what) for what in rule_1_signoff(commit)]
        if mode == "main":
            found += [(2, what) for what in rule_2_committer(commit, maintainers)]
        found += [(3, what) for what in rule_3_no_tool_marks(commit, maintainers)]
        found += [(5, what) for what in rule_5_no_skip_marker(commit.message, "the message")]
        lines += [f"{_clean(commit.sha)} rule {rule} {what}" for rule, what in found]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--mode", choices=("pr", "main"), required=True)
    parser.add_argument("--range", dest="spec", help="main mode: BEFORE..AFTER")
    parser.add_argument("--commits-json", type=Path, help="pr mode: the commits API's pages")
    parser.add_argument("--expected-count", type=int, help="pr mode: the pull request's commits")
    parser.add_argument("--expected-head", help="pr mode: the head commit the event names")
    parser.add_argument("--event", type=Path, help="pr mode: the event payload, for the title")
    # Required, with no default: the workflow that runs the check names the copy it trusts,
    # which on a push to main is the previous commit's, never the one beside this script.
    parser.add_argument("--maintainers", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="also write the violation lines here")
    args = parser.parse_args(argv)
    if args.report:
        # A report left from an earlier run must not stand in for this one.
        args.report.unlink(missing_ok=True)
    title_lines: list[str] = []
    try:
        maintainers = read_maintainers(args.maintainers)
        if args.mode == "main":
            if (
                not args.spec
                or args.commits_json
                or args.event
                or args.expected_count is not None
                or args.expected_head is not None
            ):
                raise Unusable("--mode main takes --range BEFORE..AFTER and no pr-mode inputs")
            commits = commits_in_range(args.spec)
        else:
            if args.spec or not args.commits_json or not args.event:
                raise Unusable(
                    "--mode pr takes --commits-json, --expected-count, --expected-head and --event"
                )
            if args.expected_count is None or args.expected_count < 1:
                raise Unusable("--mode pr needs --expected-count, the pull request's commits")
            if args.expected_head is None:
                raise Unusable("--mode pr needs --expected-head, the head the event names")
            try:
                text = args.commits_json.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise Unusable(f"cannot read {args.commits_json}: {exc}") from exc
            commits = commits_from_api(text, args.expected_count, args.expected_head)
            title = pull_request_title(args.event)
            title_lines = [
                f"pull-request rule 5 {what}"
                for what in rule_5_no_skip_marker(title, "the pull request's title")
            ]
    except Unusable as exc:
        print(f"commit provenance: cannot check: {_clean(str(exc), 600)}", file=sys.stderr)
        return 2
    violations = title_lines + check(commits, maintainers, args.mode)
    if args.report:
        args.report.write_text("".join(f"{line}\n" for line in violations), encoding="utf-8")
    for line in violations:
        print(line)
        # The same line as an annotation, so a red run's summary names the commit. Every field in
        # it was collapsed onto one line, so it cannot start a second workflow command.
        print(f"::error::{line}", file=sys.stderr)
    rules = "1, 2, 3 and 5" if args.mode == "main" else "1, 3 and 5"
    verdict = f"{len(violations)} violation(s)" if violations else "no violation"
    print(f"commit provenance: {len(commits)} commit(s), rules {rules}: {verdict}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
