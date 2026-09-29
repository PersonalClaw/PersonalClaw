"""Standing instructions — the instruction files you brought over from your other agent tools,
carried into every conversation whole.

A ``CLAUDE.md``, an ``AGENTS.md`` and their rule files are what a person has already told an agent
about how they work: the tools they use, what never to do, how to write. The other tool reads each
of them in full at the start of every session. So does PersonalClaw, here — they are not summarised
into a preference line or left for recall to find. The import writes each one, redacted, under
``workspace/memory/instructions/<tool>/`` (``onboarding_import.writers``), and
:func:`render` puts every one that applies into the session context, before anything recalled.

**Which files.** The ones the owner's import wrote there, as its ledger records them. The ledger
lives outside every folder an agent's file tools reach, so a file something else drops into the
folder is not followed: a standing instruction comes from the owner's own import, never from a
write an agent made. What the file says is read from the file, so an edit the owner makes to it is
what the next conversation follows. A project's own file (its ``CLAUDE.md``) applies where the other
tool applies it: to a conversation working in that project's folder or below it.

**The room.** Instructions are the first claim on the share of the model's window that memory may
use (``context._MEMORY_WINDOW_FRACTION``), up to :data:`MAX_CHARS` — the rest of memory shares what
they leave. Each file goes in whole or not at all: a rule cut in half reads as a different rule.
In order: the files that apply everywhere, then the ones for this folder; within each, by tool
(the import's own order), then by name. A file that does not fit is left out whole, and both the
model and the person are told: the model with its path, so it can read the file when a request
needs it, and the person with a notice naming it and the room there was.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: The folder, inside the memory directory, the import writes instruction files to.
INSTRUCTIONS_DIRNAME = "instructions"

#: The most characters of instructions one conversation carries, whatever the window. About
#: 6,000 tokens: a long global file, its rule files and a few projects' files. At the 200,000-token
#: calibration window memory's share is 100,000 characters and its sections' baseline 59,000, so up
#: to here instructions leave every other section of memory its full baseline.
MAX_CHARS = 24_000

#: How many left-out files the model's line names one by one; the rest are counted.
_NAMED_LEFT_OUT = 5

_OPEN = (
    "[Standing instructions: the user's own instruction files, brought over from the other agent "
    "tools they use. These are rules, not background. Follow every one in each reply and each "
    "action, as if the user had written it at the top of this conversation. What the user asks in "
    "this conversation wins over them. A file for a folder applies to work in that folder.]\n"
)
_CLOSE = "[End of standing instructions]\n"


def instructions_dir() -> Path:
    """Where the import writes instruction files: ``<home>/workspace/memory/instructions``."""
    from personalclaw.memory import memory_dir

    return memory_dir() / INSTRUCTIONS_DIRNAME


@dataclass(frozen=True)
class InstructionFile:
    """One instruction file that applies to a conversation."""

    path: Path
    #: The file's name in the tool it came from: ``CLAUDE.md``, ``rules/python.md``.
    name: str
    #: The tool's name, as the import step shows it: ``Claude Code``.
    tool: str
    #: The folder it applies to, or ``""`` for every conversation.
    workspace: str
    text: str
    #: Other files with exactly this text — one tool's file linked to another's — carried once.
    same_as: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """The file as a person names it: ``CLAUDE.md from Claude Code``."""
        return f"{self.name} from {self.tool}" if self.tool else self.name

    def heading(self) -> str:
        where = f", for work in {self.workspace}" if self.workspace else ""
        also = f" (also {', '.join(self.same_as)})" if self.same_as else ""
        return f"### {self.label}{where}{also}\n"

    def block(self) -> str:
        return f"{self.heading()}{self.text.strip()}\n\n"


@dataclass(frozen=True)
class Rendered:
    """What :func:`render` put into the conversation, and what it left out."""

    text: str
    budget: int
    included: tuple[InstructionFile, ...] = field(default_factory=tuple)
    left_out: tuple[InstructionFile, ...] = field(default_factory=tuple)

    def notice(self) -> str:
        """The sentence the person is shown when a file was left out, or ``""``."""
        if not self.left_out:
            return ""
        named = [f"{f.label} ({len(f.text):,} characters)" for f in self.left_out]
        if len(named) == 1:
            opening = f"Your instruction file {named[0]} was left out"
            rest = "it does not fit. It is"
        else:
            total = len(self.included) + len(self.left_out)
            listed = ", ".join(named[:-1]) + f" and {named[-1]}"
            opening = f"{len(named)} of your {total} instruction files were left out, {listed}"
            rest = "they do not fit. They are"
        return (
            f"{opening}: this conversation has room for {self.budget:,} characters of "
            f"instructions, and {rest} still in Files › Workspace › memory/instructions."
        )


def _applies(workspace: str, cwd: str | None) -> bool:
    """Whether a file for ``workspace`` applies to a conversation working in ``cwd``: that folder
    or one below it. Real paths on both sides, compared by whole path components. Both are
    absolute: the import records the project's folder as it found it on this machine, and a
    conversation's working directory is one."""
    if not workspace:
        return True
    if not cwd or not os.path.isabs(cwd) or not os.path.isabs(workspace):
        return False
    here = os.path.realpath(cwd)
    there = os.path.realpath(workspace)
    return here == there or here.startswith(there.rstrip(os.sep) + os.sep)


def _tool_order() -> dict[str, int]:
    from personalclaw.onboarding_import.registry import list_sources

    return {source.name: index for index, source in enumerate(list_sources())}


def _tool_name(source: str) -> str:
    from personalclaw.onboarding_import.registry import get_source

    try:
        return get_source(source).display_name
    except KeyError:
        return ""


def instruction_files(cwd: str | None = None) -> list[InstructionFile]:
    """Every instruction file the import wrote that applies to a conversation in ``cwd``, in the
    order they are carried. Reads the ledger and the files; writes nothing.

    A ledger entry whose file is gone, is not a plain file inside the instructions folder, or
    will not read is skipped — and said in the log, since the person's instruction is not being
    followed. Two files with exactly the same text are carried once, naming both.

    Two choices, stated because they point opposite ways. WHICH files is fail-closed: a file the
    ledger does not name, one outside the folder, or one reached through a link is never
    carried, however it got there — an instruction must not arrive from anywhere but the owner's
    import. WHETHER the turn runs is fail-open: an unreadable file, or a ledger that will not
    parse (read as empty by ``writers``), costs the turn that file, never the turn itself.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.onboarding_import.model import ImportCategory
    from personalclaw.onboarding_import.writers import imported_items

    root = os.path.realpath(instructions_dir())
    home = config_dir()
    order = _tool_order()
    found: list[tuple[tuple[int, int, str], InstructionFile]] = []
    for entry in imported_items(ImportCategory.INSTRUCTIONS):
        destination = str(entry.get("destination") or "")
        workspace = str(entry.get("workspace") or "")
        if not destination or not _applies(workspace, cwd):
            continue
        path = home / destination
        if not os.path.realpath(path).startswith(root + os.sep):
            # Recorded somewhere else — an import made before instruction files had a folder of
            # their own wrote them beside the memories. Importing again brings them here.
            logger.debug("imported instruction %s is outside %s; not carried", path, root)
            continue
        # A plain file, reached by no link: what the ledger names is what is read.
        if path.is_symlink() or not path.is_file():
            if path.is_symlink() or path.exists():
                logger.warning("standing instruction %s is not a plain file; skipped", path)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("standing instruction %s could not be read; skipped", path)
            continue
        if not text.strip():
            continue
        source = str(entry.get("source") or "")
        name = str(entry.get("title") or entry.get("key") or path.name)
        rank = (1 if workspace else 0, order.get(source, len(order)), name)
        found.append(
            (
                rank,
                InstructionFile(
                    path=path, name=name, tool=_tool_name(source), workspace=workspace, text=text
                ),
            )
        )
    found.sort(key=lambda pair: pair[0])
    files: list[InstructionFile] = []
    for _rank, file in found:
        twin = next((f for f in files if f.text.strip() == file.text.strip()), None)
        if twin is None:
            files.append(file)
            continue
        files[files.index(twin)] = InstructionFile(
            path=twin.path,
            name=twin.name,
            tool=twin.tool,
            workspace=twin.workspace,
            text=twin.text,
            same_as=(*twin.same_as, file.label),
        )
    return files


def _left_out_line(left_out: list[InstructionFile], budget: int) -> str:
    named = [f"{f.path} ({len(f.text):,} characters)" for f in left_out[:_NAMED_LEFT_OUT]]
    more = len(left_out) - len(named)
    if more:
        named.append(f"{more} more in {instructions_dir()}")
    return (
        f"[Left out for lack of room: this conversation has {budget:,} characters for the user's "
        f"instructions, and these did not fit. Read one with read_file when the request touches "
        f"it: {'; '.join(named)}]\n"
    )


def render(cwd: str | None = None, *, budget_chars: int) -> Rendered:
    """The standing-instructions block for a conversation in ``cwd``, at most ``budget_chars``.

    Files go in whole, in :func:`instruction_files` order, each one that fits the room still left;
    one that does not is left out whole. When anything is left out, a line naming it is part of
    the block and of its budget — the last file taken in is given up for that line if it must be —
    so the model knows the file exists and where to read it. ``""`` when no instruction file
    applies, or when the room would not hold even the framing and that line.
    """
    files = instruction_files(cwd)
    if not files:
        return Rendered(text="", budget=budget_chars)
    room = budget_chars - len(_OPEN) - len(_CLOSE)
    included: list[InstructionFile] = []
    left_out: list[InstructionFile] = []
    for file in files:
        size = len(file.block())
        if size <= room:
            included.append(file)
            room -= size
        else:
            left_out.append(file)
    while left_out and included and len(_left_out_line(left_out, budget_chars)) > room:
        given_up = included.pop()
        room += len(given_up.block())
        left_out.append(given_up)
    left_out.sort(key=files.index)
    tail = _left_out_line(left_out, budget_chars) if left_out else ""
    if len(tail) > room:
        return Rendered(text="", budget=budget_chars, left_out=tuple(files))
    body = "".join(f.block() for f in included)
    return Rendered(
        text=f"{_OPEN}{body}{tail}{_CLOSE}",
        budget=budget_chars,
        included=tuple(included),
        left_out=tuple(left_out),
    )


__all__ = [
    "INSTRUCTIONS_DIRNAME",
    "MAX_CHARS",
    "InstructionFile",
    "Rendered",
    "instruction_files",
    "instructions_dir",
    "render",
]
