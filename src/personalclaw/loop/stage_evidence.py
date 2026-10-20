"""What a Code loop's stage gate is shown of the stage's work, and where each part comes from.

A stage's gate asks a judge to rule each exit criterion, and its evidence comes in kinds the judge
must tell apart: what the SUPERVISOR observed itself, what the loop's OWNER told it, and what the
WORKERS reported. A worker's report is never proof on its own (no agent certifies its own work), so
a criterion can pass only on what was observed or on the owner's word, and that is only possible
when the observation covers what the stage did. It used to cover one declared deliverable file and
a bare "the command passed": a criterion about a second file the stage changed, or about one named
test passing, could never pass, and a loop whose work was done and merged blocked.

So the gate observes the stage's work the way its owner would review it:

* in a workspace git tracks, every file that differs from where the loop's run started
  (``worktree.start_point``): the list, git's diff, and each changed file as it is now, within
  budgets that say what they cut;
* in a folder git does not track, the files the stage's findings name, read from disk: the names
  are the workers', the content is what is on disk;
* every file the stage's deliverable names;
* the checks the gate ran, with what they printed: the end of it, and every line that names a file
  the stage changed, the test runner asked to name each test (``gates.run_verify_command``).

Text from the workspace, a check's output and the workers' words reach the judge through the
door every text from outside takes into a prompt (``gates.for_a_judge``): masked, read by the
injection screen and fenced as data, never instructions to it; what the screen refuses is said
withheld, never shown. A file is read only when it is inside the work folder
(``loop.files.file_inside``): one a name or a link leads out of is never read.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from personalclaw.loop import worktree
from personalclaw.loop.files import file_inside
from personalclaw.security import redact_for_display

#: How much of the stage's findings its judge is shown (characters), and of any one finding's
#: recorded evidence (test output, say). Over either, the oldest findings and the middle of a long
#: evidence block are cut, and the judge is told so.
FINDINGS_BUDGET = 12000
FINDING_EVIDENCE = 2400
#: How much of the workspace's diff is shown, of the changed files as they are now (and of any
#: one of them), and of the stage's deliverable.
DIFF_BUDGET = 14000
FILES_BUDGET = 8000
FILE_CHARS = 4000
DELIVERABLE_CHARS = 6000
#: How many of the owner's steers are shown, newest last, and how much of each.
OWNER_STEERS = 10
OWNER_STEER_CHARS = 400
#: The most files a stage's findings name that are read in a folder git does not track, the most
#: changed files a check watches its output for, and the most changed files listed by name.
NAMED_FILES = 40
WATCHED_FILES = 40
LISTED_FILES = 80
#: A file larger than this is read from its start only.
_WHOLE_FILE_BYTES = 1_000_000

#: A file name in a deliverable label: a path ending in an extension ("src/engine.ts", "PLAN.md").
_FILENAME_RE = re.compile(r"[\w./-]+\.[A-Za-z0-9]+")
#: Folders a search for a deliverable by its bare name never enters, and how many it looks in.
_HEAVY_DIRS = frozenset({"node_modules", ".git", "dist", "build", ".venv", "__pycache__", ".next"})
_SEARCH_DIRS = 5000

#: Where a project keeps its tests, by the conventions of the common runners: a folder named so,
#: or a file named so (``test_*.py``, ``*_test.go``, ``*.test.ts``, ``*.spec.js``, ``*_spec.rb``,
#: ``FooTest.java``, ``FooTests.cs``).
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing"})
_TEST_FILE_RE = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|_spec\.|Tests?\.[A-Za-z0-9]+$)")


def is_test_file(path: str) -> bool:
    """Whether *path* (relative to its workspace) is where its project keeps tests."""
    parts = path.replace("\\", "/").split("/")
    return any(p in _TEST_DIRS for p in parts[:-1]) or bool(_TEST_FILE_RE.search(parts[-1]))


def fenced(text: str, *, what: str, source: str, source_type: str = "", source_id: str = "") -> str:
    """*text* as the judge may be handed it (``gates.for_a_judge``): fenced as data, or the door's
    sentence naming *what* it was when the injection screen refuses it."""
    from personalclaw.loop.gates import for_a_judge

    return for_a_judge(
        text, what=what, source=source, source_type=source_type, source_id=source_id
    )[0]


def cut_middle(text: str, limit: int) -> tuple[str, bool]:
    """*text* within *limit* characters, its head and tail kept (a test run's summary and its
    failures sit at the end), and whether it was cut."""
    if len(text) <= limit:
        return text, False
    head = limit // 4
    tail = limit - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n… [{cut} characters cut] …\n{text[-tail:]}", True


def _named(paths: list[str], most: int = 8) -> str:
    shown = ", ".join(paths[:most])
    return shown + (f" and {len(paths) - most} more" if len(paths) > most else "")


@dataclass
class Observed:
    """What the supervisor observed of a stage's work.

    ``blocks`` is the text the judge reads; ``checks`` the checks it ran, said in their own
    section; ``seen`` one line per thing it looked at, for the gate's record and the words a pause
    says; ``cut`` what it left out, for the judge's note; ``changed`` the files the stage changed,
    relative to the work folder; ``shown`` those whose content is already in ``blocks``."""

    blocks: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)
    cut: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    shown: set[str] = field(default_factory=set)

    def tests_changed(self) -> bool:
        """Whether the stage changed a file where its project keeps tests."""
        return any(is_test_file(p) for p in self.changed)

    def watch(self) -> tuple[str, ...]:
        """The names a check's output is kept for, line by line: the test files the stage changed
        (whose tests a runner names), else every file it changed."""
        tests = [p for p in self.changed if is_test_file(p)]
        return tuple((tests or self.changed)[:WATCHED_FILES])


def _read_text(path: str, limit: int) -> tuple[str | None, bool]:
    """The text of the file at *path* within *limit* characters (its middle cut), and whether it
    was cut; ``None`` for a file that is not text or cannot be read."""
    try:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="strict") as fh:
            text = fh.read(limit if size > _WHOLE_FILE_BYTES else -1)
    except (OSError, UnicodeDecodeError):
        return None, False
    if size > _WHOLE_FILE_BYTES:
        return f"{text}\n… [the rest of this {size}-byte file is not shown] …", True
    return cut_middle(text, limit)


def _file_block(observed: Observed, rel: str, path: str, *, limit: int) -> int:
    """Show the file *rel* (at *path*) as it is now; returns how many characters it took."""
    text, cut = _read_text(path, limit)
    if text is None:
        observed.blocks.append(f"{rel} is not text, so its content is not shown.")
        return 0
    if not text.strip():
        observed.blocks.append(f"{rel} is empty.")
        observed.shown.add(rel)
        return 0
    how = " (cut in the middle, for length)" if cut else ""
    observed.blocks.append(
        f"{rel} as it is now{how}:\n"
        + fenced(
            text,
            what=f"The content of {rel}",
            source="workspace",
            source_type="file",
            source_id=rel,
        )
    )
    observed.shown.add(rel)
    return len(text)


def observe_changes(observed: Observed, folder: str, base: str) -> None:
    """The workspace's changes since the loop's run started at *base*, read with git."""
    changes = worktree.changes_since(folder, base, diff_chars=DIFF_BUDGET)
    if changes.error:
        observed.blocks.append(
            f"The workspace's changes since this loop started could not be read: {changes.error}."
        )
        observed.seen.append(f"no changes in the workspace it could read ({changes.error})")
        return
    if not changes.files:
        observed.blocks.append("Nothing in the workspace differs from where this loop started.")
        observed.seen.append("the workspace, which has not changed since the loop started")
        return
    paths = [c.path for c in changes.files]
    observed.changed = paths
    listing = "\n".join(
        f"- {c.path}: {c.state}" + (f", {c.lines} lines" if c.lines else "")
        for c in changes.files[:LISTED_FILES]
    )
    if len(paths) > LISTED_FILES:
        listing += f"\n- and {len(paths) - LISTED_FILES} more"
    files = f"{len(paths)} file" + ("s" if len(paths) != 1 else "")
    observed.blocks.append(
        f"The workspace's changes since this loop started (from {base[:12]}), read with git, "
        f"{files}:\n{listing}"
    )
    if changes.diff:
        observed.blocks.append(
            "Their diff:\n"
            + fenced(
                changes.diff,
                what="The diff of the workspace's changes",
                source="workspace",
                source_type="diff",
            )
        )
    unshown = [
        c.path
        for c in changes.files
        if c.state not in (worktree.CHANGE_UNTRACKED, worktree.CHANGE_DELETED)
        and c.path not in changes.shown
    ]
    if unshown:
        observed.cut.append(f"The diff of {_named(unshown)} is not shown, for length.")
    left = FILES_BUDGET
    unread: list[str] = []
    for change in changes.files:
        if change.state not in (worktree.CHANGE_MODIFIED, worktree.CHANGE_UNTRACKED):
            continue
        path = file_inside(folder, change.path)
        if path is None:
            continue
        if left <= 0:
            unread.append(change.path)
            continue
        left -= _file_block(observed, change.path, str(path), limit=min(FILE_CHARS, left))
    if unread:
        observed.cut.append(f"The content of {_named(unread)} is not shown, for length.")
    observed.seen.append(f"the changes to {files} in the workspace: {_named(paths)}")


def observe_named_files(observed: Observed, folder: str, names: list[str]) -> None:
    """In a folder git does not track: the files the stage's findings name, read from disk."""
    wanted = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))[:NAMED_FILES]
    if not wanted:
        return
    root = os.path.realpath(folder)
    found: dict[str, str] = {}
    missing: list[str] = []
    outside: list[str] = []
    for name in wanted:
        hit = file_inside(root, name)
        if hit is None:
            gone = not os.path.lexists(os.path.join(root, name))
            (missing if gone else outside).append(name)
        else:
            found.setdefault(os.path.relpath(str(hit), root), str(hit))
    read = list(found)
    observed.changed = read
    if read:
        observed.blocks.append(
            "The work folder is not one git tracks, so the supervisor read the files the stage's "
            "findings name from disk (the names are the workers'; the content is what is on disk "
            "now)."
        )
    left = FILES_BUDGET
    unread: list[str] = []
    for rel, path in found.items():
        if left <= 0:
            unread.append(rel)
            continue
        left -= _file_block(observed, rel, path, limit=min(FILE_CHARS, left))
    if unread:
        observed.cut.append(f"The content of {_named(unread)} is not shown, for length.")
    if missing:
        observed.blocks.append(f"Named by a finding but not on disk: {', '.join(missing)}.")
    if outside:
        observed.blocks.append(
            f"Named by a finding but not a file in the work folder, so not read: "
            f"{', '.join(outside)}."
        )
    said = f"the files its findings name, read from disk: {_named(read)}" if read else ""
    if missing:
        said += ("; " if said else "") + f"{_named(missing)} not on disk"
    if said:
        observed.seen.append(said)


def deliverable_files(folder: str, label: str) -> tuple[list[tuple[str, str]], list[str]]:
    """The files a stage's deliverable label names, found in *folder*, and the names not found.

    Each name is looked for as given (relative to the folder: ``src/engine.ts``), then by its bare
    name at the folder's top, then by its bare name anywhere below it (heavy and version-control
    folders skipped, the search bounded), since a worker may put it in another valid place. Only a
    file inside the folder counts (``loop.files.file_inside``): a name or a link that leads out of
    it is not the stage's deliverable. Found files are ``(path relative to the folder, real
    path)``."""
    root = os.path.realpath(folder)
    found: list[tuple[str, str]] = []
    missing: list[str] = []
    for name in dict.fromkeys(_FILENAME_RE.findall(label or "")):
        base = os.path.basename(name)
        path = (
            file_inside(root, name.lstrip("./"))
            or file_inside(root, base)
            or _found_below(root, base)
        )
        if path is None:
            missing.append(name)
            continue
        rel = os.path.relpath(str(path), root)
        if rel not in [r for r, _ in found]:
            found.append((rel, str(path)))
    return found, missing


def _found_below(root: str, base: str):
    """The first file named *base* below *root* that is inside it, or None (a bounded walk)."""
    for looked, (where, dirs, files) in enumerate(os.walk(root)):
        if looked >= _SEARCH_DIRS:
            return None
        dirs[:] = sorted(d for d in dirs if d not in _HEAVY_DIRS)
        if base in files:
            hit = file_inside(root, os.path.relpath(os.path.join(where, base), root))
            if hit is not None:
                return hit
    return None


def observe_deliverable(
    observed: Observed, folder: str, label: str
) -> list[tuple[str, int]] | None:
    """Show every file the stage's deliverable names: each found one ``(path relative to the
    folder, its size in bytes)``. ``None`` when the label names no file (nothing to look for);
    ``[]`` when it names some and none is in the folder: the gate then holds the stage without its
    judge."""
    if not _FILENAME_RE.search(label or "") or not os.path.isdir(folder):
        return None  # nothing named, or no folder to look in: the judge still gates
    found, missing = deliverable_files(folder, label)
    if not found:
        return []
    for rel, path in found:
        if rel in observed.shown:
            observed.blocks.append(f"The deliverable {rel} is on disk; its content is shown above.")
        else:
            observed.blocks.append(f"The deliverable {rel} is on disk.")
            _file_block(observed, rel, path, limit=DELIVERABLE_CHARS)
    if missing:
        observed.blocks.append(
            f"Not in the work folder, though the deliverable names it: {', '.join(missing)}."
        )
    said = f"the deliverable {_named([r for r, _ in found])}"
    observed.seen.append(said + (f" ({_named(missing)} not found)" if missing else ""))
    return [(rel, _size(path)) for rel, path in found]


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def observe_check(observed: Observed, label: str, command: str, ok: bool | None, report) -> None:
    """One check the gate ran (``gates.CheckReport`` *report*): its outcome and what it printed."""
    shown = redact_for_display(command)
    if ok is True:
        state = "passed (exit 0)"
    elif ok is False:
        state = f"failed (exit {report.exit_code})" if report.exit_code is not None else "failed"
    else:
        state = f"did not run: {report.not_run}" if report.not_run else "could not run"
    lines = [f"- `{shown}` ({label}): {state}."]
    named = report.named
    if named and all(line in report.output for line in named.splitlines()):
        named = ""  # every such line is in the end of its output, shown next
    if named:
        lines += [
            "  Each line it printed that names a file the stage changed:",
            fenced(named, what=f"What `{shown}` printed", source="check", source_type="output"),
        ]
    if report.output:
        lines += [
            "  The end of what it printed:",
            fenced(
                report.output, what=f"What `{shown}` printed", source="check", source_type="output"
            ),
        ]
    observed.checks.append("\n".join(lines))
    observed.seen.append(f"`{shown}` ({label}), which {state}")


def owner_block(nudges: list[dict]) -> str:
    """What the loop's owner told it (``loop.files.get_nudges``), newest last, or ``""``."""
    said = [
        " ".join(str(n.get("text") or "").split())[:OWNER_STEER_CHARS]
        for n in (nudges or [])[-OWNER_STEERS:]
        if isinstance(n, dict)
    ]
    said = [s for s in said if s]
    if not said:
        return ""
    return "What the loop's owner told it, newest last (their own words):\n" + "\n".join(
        f"- {s}" for s in said
    )


def _evidence_text(raw) -> str:
    """A finding's recorded evidence as text: a string as it is, a list or a mapping (a worker
    may record checks as ``{"pytest": "13 passed"}``) one item per line."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return "\n".join(f"{k}: {v}" for k, v in raw.items()).strip()
    if isinstance(raw, list):
        return "\n".join(_evidence_text(v) for v in raw).strip()
    return str(raw).strip()


def _finding_block(finding: dict, task_titles: dict[str, str]) -> tuple[str, bool]:
    """One finding as its stage's judge reads it, fenced, and whether its evidence was cut."""
    tid = str(finding.get("task_id") or "")
    who = f", task “{task_titles.get(tid) or tid}”" if tid else ""
    lines = [" ".join(str(finding.get("summary") or "").split())[:600]]
    insight = " ".join(str(finding.get("key_insight") or "").split())[:400]
    if insight:
        lines.append(f"key insight: {insight}")
    files = finding.get("files_touched")
    if isinstance(files, list) and files:
        lines.append("files touched: " + ", ".join(str(f) for f in files[:20]))
    evidence, cut = cut_middle(_evidence_text(finding.get("evidence")), FINDING_EVIDENCE)
    if evidence:
        lines.append("evidence it recorded:")
        lines.extend(f"  {ln}" for ln in evidence.splitlines())
    body = fenced("\n".join(lines), what="This finding", source="worker", source_type="finding")
    indented = "\n".join(f"    {ln}" for ln in body.splitlines())
    return f"- cycle {finding.get('cycle', '?')}{who}:\n{indented}", cut


def findings_block(findings: list[dict], task_titles: dict[str, str]) -> tuple[str, list[str]]:
    """The stage's findings, the workers' own account, oldest first: the newest kept within
    :data:`FINDINGS_BUDGET`, and notes on what was cut."""
    blocks = [_finding_block(f, task_titles) for f in findings]
    kept: list[str] = []
    used = 0
    cut_evidence = False
    for block, cut in reversed(blocks):
        if kept and used + len(block) > FINDINGS_BUDGET:
            break
        kept.append(block)
        used += len(block)
        cut_evidence = cut_evidence or cut
    notes: list[str] = []
    left_out = len(blocks) - len(kept)
    if left_out:
        notes.append(
            f"The {left_out} earliest finding{'s' if left_out != 1 else ''} of this stage "
            "are not shown, for length."
        )
    if cut_evidence:
        notes.append("Some findings' recorded evidence is cut in the middle, for length.")
    head = "What the workers reported, oldest first (their own account, never proof on its own):"
    return "\n".join([head, *reversed(kept)]) if kept else "", notes
