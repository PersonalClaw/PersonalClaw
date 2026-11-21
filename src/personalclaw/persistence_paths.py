"""The files your own tools run later: a write to one asks you every time.

Some files in a project make your own tools start programs later, outside any approval and any
sandbox: when you open the folder in your editor, enter it in your shell, commit, install, or start
an agent. An agent that writes one plants code that runs as you, later. So a write to one is put to
you every time, in every mode: Trust, YOLO, Trust reads, an agent's "Always allow", your Approval
mode "Auto" and the operator's patterns answer none of it, and unattended work, with nobody to ask,
is refused it (``run_bounds.Reach.plants``). The card says which file and what runs it.

:data:`ROWS` is the one table of them, shared by every check. Its first row:

* **Editor settings** (``.vscode/settings.json``, a ``.code-workspace`` file and
  ``.zed/settings.json``): your editor starts the programs they name, such as an interpreter, a
  formatter or a terminal, when you open the folder.

The table's other kinds (git hook managers, ``.envrc``, package-manager files, editor tasks, agent
folders) join it as rows of their own. What stays refused outright, whoever asks, is
``owner_only``'s: PersonalClaw's own home, git's settings and hooks, and your shell's startup files.

**Where a row counts.** An editor reads the copy at the folder it opens, so an editor row counts at
the working folder the write is made from and at the root of a repository (a folder holding
``.git``), on the path as the call names it and as it resolves (a link followed). Names are matched
whatever their case, as the file systems your editors run on match them.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Row:
    """One kind of file your tools run later: its *name*, the *paths* that are one (each a
    folder-relative path whose last part may be a ``*`` pattern), and *what* runs it, as the card
    says it (``"which {what}"``)."""

    name: str
    paths: tuple[str, ...]
    what: str


EDITOR_SETTINGS = Row(
    name="editor settings",
    paths=(".vscode/settings.json", "*.code-workspace", ".zed/settings.json"),
    what=(
        "your editor reads when you open the folder, and starts the programs it names (an "
        "interpreter, a formatter, a terminal)"
    ),
)

#: Every kind of file your tools run later. Closed: a new kind is a new row here.
ROWS: tuple[Row, ...] = (EDITOR_SETTINGS,)


@dataclass(frozen=True)
class Planted:
    """A write a call makes to a file your tools run later: the *path* as the call names it, and
    its :class:`Row`."""

    path: str
    row: Row

    def sentence(self) -> str:
        """``This writes {path}, which {what}; it asks every time.``"""
        return f"This writes {self.path}, which {self.row.what}; it asks every time."


def _matches(parts: tuple[str, ...], pattern: str) -> int:
    """How many of *parts*' last parts *pattern* names (0 when it names none), case-insensitive."""
    wanted = tuple(p.lower() for p in pattern.split("/"))
    if len(parts) < len(wanted):
        return 0
    tail = tuple(p.lower() for p in parts[-len(wanted) :])
    if all(fnmatch.fnmatchcase(got, want) for got, want in zip(tail, wanted)):
        return len(wanted)
    return 0


def _anchored(folder: str, cwd: str) -> bool:
    """Whether *folder* is one an editor opens: the working folder, or a repository's root."""
    try:
        if cwd and os.path.realpath(folder) == os.path.realpath(cwd):
            return True
        return os.path.exists(os.path.join(folder, ".git"))
    except OSError:
        return False


def _row_of(absolute: str, cwd: str) -> Row | None:
    parts = tuple(p for p in absolute.split(os.sep) if p)
    for row in ROWS:
        for pattern in row.paths:
            named = _matches(parts, pattern)
            if named and _anchored(os.sep + os.sep.join(parts[:-named]), cwd):
                return row
    return None


def row_for(path: str, *, cwd: str = "") -> Row | None:
    """The :class:`Row` a write to *path* plants (a relative one read from *cwd*), or ``None``:
    matched on the path as named and as it resolves, so a link to one is one too."""
    if not path or not path.strip():
        return None
    expanded = os.path.expanduser(path)
    absolute = os.path.normpath(
        expanded if os.path.isabs(expanded) else os.path.join(cwd or os.getcwd(), expanded)
    )
    try:
        resolved = os.path.realpath(absolute)
    except OSError:
        resolved = absolute
    for candidate in dict.fromkeys((absolute, resolved)):
        row = _row_of(candidate, cwd)
        if row is not None:
            return row
    return None


def planted(paths: Iterable[str], *, cwd: str = "") -> tuple[Planted, ...]:
    """What a call that writes *paths* (as it names them, a relative one read from *cwd*) plants:
    one :class:`Planted` for each path a row covers, once each, in order. A path that cannot be
    read plants nothing here."""
    found: list[Planted] = []
    for path in dict.fromkeys(p for p in paths if isinstance(p, str)):
        try:
            row = row_for(path, cwd=cwd)
        except (OSError, ValueError):
            row = None
        if row is not None:
            found.append(Planted(path, row))
    return tuple(found)
