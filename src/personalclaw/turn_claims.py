"""What a finished turn's reply claims, held to the turn's own record, at no model cost.

A reply can say work happened that its turn never did: "The helper completed its survey" from a
turn that ran no helper, "`notes.md` is staged" in a repository where it is not. The reader takes
the reply at its word, and nothing told them otherwise: the check-work skill reads claims only when
asked, and its offer is a chip in the web chat alone (`chat_followups.maybe_offer_check_work`).

:func:`note_for` reads the claims the turn's record can settle, and the repository's state a reply
reports, and says each that disagrees in one short line: "Note: this answer says `notes.md` is
staged; the repository shows it is not staged." A reply whose claims match says nothing, and so
does one that claims nothing this module reads. No model is asked, and nothing runs but a read of
the repository's index (``git diff --cached``), through the git every PersonalClaw read uses
(`net.git`).

What it reads, first the claims a record settles:

* **A helper's work.** A sentence that says a helper or a sub-agent completed, finished, ran or
  reported, from a turn whose calls hold no helper's (:data:`HELPER_TOOLS`).
* **A staged file.** A sentence that says a file is staged, in a turn that ran in a repository whose
  index does not hold it (and, when the file has changes, that they are unstaged).
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The calls that run a helper: PersonalClaw's own, and an agent CLI's sub-agent tool.
HELPER_TOOLS = frozenset({"subagent_run", "task", "agent"})

#: How long a read of the repository's index may take before it is no evidence either way.
GIT_TIMEOUT_SECS = 5.0

#: The note's opening, which says it is the record's and not the answer's.
NOTE_HEAD = "Note:"

_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
_INTENT = re.compile(r"\b(will|going to|plan to|let me|should|would|could|can|next)\b", re.I)
_HELPER = re.compile(r"\b(helpers?|sub-?agents?)\b", re.I)
_HELPER_DONE = re.compile(
    r"\b(completed|finished|ran|returned|reported|found|surveyed|came back|is done|was done)\b",
    re.I,
)
_STAGED_AFTER = re.compile(
    r"`([^`\s]+)`\s+(?:is|are|was|were|has been|have been|is now|are now)\s+(?:now\s+)?staged\b",
    re.I,
)
_STAGED_BEFORE = re.compile(r"\bstaged\s+`([^`\s]+)`", re.I)


@dataclass(frozen=True)
class Mismatch:
    """One claim the turn's record or its repository does not bear out: what the answer says, and
    what the record shows."""

    says: str
    shows: str

    def line(self) -> str:
        return f"{NOTE_HEAD} this answer says {self.says}; {self.shows}."


@dataclass(frozen=True)
class TurnRecord:
    """What a turn did, as its runner kept it: the calls it made, by name, and the folder it ran in
    ("" for none)."""

    tools: tuple[str, ...] = ()
    folder: str = ""

    def ran_a_helper(self) -> bool:
        return any(_tool_name(tool) in HELPER_TOOLS for tool in self.tools)


def note_for(reply: str, record: TurnRecord) -> str:
    """The note a finished turn's *reply* gets, one line per claim its *record* does not bear out,
    or "" when every claim this module reads matches."""
    return "\n".join(found.line() for found in mismatches(reply, record))


def mismatches(reply: str, record: TurnRecord) -> list[Mismatch]:
    """Each claim in *reply* that *record* does not bear out, in the order the reply makes them."""
    found: list[Mismatch] = []
    claimed: list[str] = []
    for sentence in _claims(reply):
        if _HELPER.search(sentence) and _HELPER_DONE.search(sentence) and not record.ran_a_helper():
            said = f"“{_quoted(sentence)}”"
            found.append(Mismatch(says=said, shows="the turn's record shows no helper ran"))
        claimed += [p for p in _staged_paths(sentence) if p not in claimed]
    if claimed and record.folder:
        found += _not_staged(claimed, Path(record.folder))
    return found


def _claims(reply: str) -> Iterable[str]:
    """The sentences of *reply* that say something happened: none that say it will."""
    for raw in _SENTENCES.split(reply or ""):
        sentence = raw.strip().strip("-*# ").strip()
        if sentence and len(sentence) <= 600 and not _INTENT.search(sentence):
            yield sentence


def _staged_paths(sentence: str) -> list[str]:
    patterns = (_STAGED_AFTER, _STAGED_BEFORE)
    return [m.group(1) for pattern in patterns for m in pattern.finditer(sentence)]


def _not_staged(paths: list[str], folder: Path) -> list[Mismatch]:
    """A mismatch for each of *paths* the index of the repository at *folder* does not hold. None
    when the folder is no repository, or its index could not be read: no evidence either way."""
    staged = _git_names(folder, "diff", "--cached", "--name-only", "--relative")
    if staged is None:
        return []
    changed = _git_names(folder, "diff", "--name-only", "--relative") or set()
    out: list[Mismatch] = []
    for path in paths:
        name = path.removeprefix("./")
        if name in staged:
            continue
        how = "has changes that are not staged" if name in changed else "is not staged"
        out.append(Mismatch(says=f"`{path}` is staged", shows=f"the repository shows it {how}"))
    return out


def _git_names(folder: Path, *args: str) -> set[str] | None:
    """The paths a read-only git command prints in *folder*, or None when it could not say."""
    from personalclaw.net.git import git_argv, git_env

    if not (folder / ".git").exists():
        return None
    try:
        done = subprocess.run(  # noqa: S603 - a fixed read of the index, through net.git
            git_argv(["-C", str(folder), *args]),
            env=git_env(site="turn-claims-git"),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("turn claims: git %s in %s could not run", args[0], folder, exc_info=True)
        return None
    if done.returncode != 0:
        return None
    return {line.strip() for line in done.stdout.splitlines() if line.strip()}


def _tool_name(title: str) -> str:
    """A call's tool name as the record keeps it ("subagent_run", "Task: survey the docs")."""
    head = re.split(r"[\s:(]", (title or "").strip().removeprefix("Running: "), maxsplit=1)[0]
    return head.lower()


def _quoted(sentence: str) -> str:
    said = sentence.rstrip(".!")
    return said if len(said) <= 120 else f"{said[:117].rstrip()}…"
