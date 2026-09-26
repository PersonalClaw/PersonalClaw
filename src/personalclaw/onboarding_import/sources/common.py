"""What every source reader shares, so two tools' importers cannot drift on the parts that match.

- **Paths another machine recorded.** A tool's home copied from an old laptop names that laptop's
  home (``/Users/old-name/src/app``). :func:`on_this_machine` finds the same path under THIS home,
  and :func:`display_path` says it the way a person reads it.
- **One file, one item** (:func:`text_item`), read through floors 1 and 3.
- **Skills** (:func:`scan_skills`): a directory with a ``SKILL.md``, whichever tool it came from.
- **Prompt history** (:func:`prompt_history`): counted and named, never imported.
- **A conversation's title and note** (:func:`one_line`, :func:`conversation_note`).
- **An MCP server** (:class:`McpServer`): what a tool has configured, values included, for the
  onboarding scan (through floor 2) and for the Tools page's Import (whole, server-side).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.onboarding_import.floors import read_text_safely, refuses
from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    NotImported,
    ScanResult,
)

#: A title is a line in a list, not the first paragraph of a conversation.
TITLE_CHARS = 80

# ── paths another machine recorded ────────────────────────────────────────────

#: ``/Users/<name>`` or ``/home/<name>``: the part of a recorded path that names the machine's
#: home directory rather than anything inside it.
_HOME_PREFIX_RE = re.compile(r"^(?:/Users|/home)/[^/]+")


def on_this_machine(recorded: str) -> Path | None:
    """Where the directory a tool recorded as ``recorded`` is on THIS machine, or ``None``.

    The recorded path itself when it exists. Otherwise, when it is under a home directory
    (``/Users/<name>/…``), the same path under this machine's home: a tool's home copied from an
    old laptop names the old laptop's home, and ``~/src/app`` is still ``~/src/app``.
    """
    if not recorded:
        return None
    path = Path(recorded)
    if path.is_dir():
        return path
    match = _HOME_PREFIX_RE.match(recorded)
    if match is None:
        return None
    moved = Path.home() / recorded[match.end() :].lstrip("/")
    return moved if moved.is_dir() else None


def display_path(path: Path) -> str:
    """A path as a person reads it: ``~/src/app`` under the home, the full path elsewhere."""
    home = Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def recorded_label(recorded: str) -> str:
    """A directory a tool recorded, as a person reads it: where it is on this machine when it is
    here, and the path as recorded when it is not."""
    local = on_this_machine(recorded)
    return display_path(local) if local is not None else recorded


# ── files ─────────────────────────────────────────────────────────────────────


def markdown_files(directory: Path, *, recursive: bool = True) -> list[Path]:
    if not directory.is_dir():
        return []
    found = directory.rglob("*.md") if recursive else directory.glob("*.md")
    return sorted(p for p in found if p.is_file())


def slug_name(raw: str, *, lower: bool) -> str:
    """``raw`` as a destination name: runs of other characters become ``-``."""
    text = raw.strip().lower() if lower else raw.strip()
    pattern = r"[^a-z0-9-]+" if lower else r"[^A-Za-z0-9_-]+"
    return re.sub(pattern, "-", text).strip("-")[:63]


def text_item(
    source: str,
    path: Path,
    result: ScanResult,
    *,
    category: ImportCategory,
    key: str,
    title: str,
    origin: str = "",
    note: str = "",
    preselect: bool = True,
    seen_files: set[Path] | None = None,
) -> bool:
    """One file read through floors 1 and 3 into an item. False when nothing was added: the file
    is empty, refused, or (``seen_files``) already an item."""
    if seen_files is not None:
        # One file, one item: a tool can reach one file by two names.
        resolved = path.resolve()
        if resolved in seen_files:
            return False
        seen_files.add(resolved)
    text, redactions, skipped = read_text_safely(path)
    result.secrets_skipped += skipped
    if not text.strip():
        return False
    result.redactions += redactions
    result.items.append(
        ImportItem(
            source=source,
            category=category,
            key=key,
            title=title,
            text=text,
            origin=origin,
            note=note,
            preselect=preselect,
            redactions=redactions,
            secrets_skipped=skipped,
        )
    )
    return True


def scan_skills(source: str, roots: list[tuple[Path, str]], result: ScanResult) -> None:
    """Every ``<root>/<name>/SKILL.md``, one skill per name: ``roots`` is ``(directory, origin)``
    in the order the tool reads them, and the first root to hold a name is the one it keeps.

    A dot-directory is the tool's own (Codex keeps the skills it ships in ``skills/.system``),
    not one of yours.
    """
    seen: set[str] = set()
    for root, origin in roots:
        if not root.is_dir():
            continue
        for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            name = skill_dir.name
            if name.startswith(".") or name in seen or not (skill_dir / "SKILL.md").is_file():
                continue
            seen.add(name)
            # Count (and later exclude) any credential file sitting inside the skill. The
            # writer's fetch applies the same predicate, so a counted file is also an
            # uninstalled file — the count and the behaviour cannot drift.
            withheld = sum(1 for f in skill_dir.rglob("*") if f.is_file() and refuses(f))
            result.secrets_skipped += withheld
            result.items.append(
                ImportItem(
                    source=source,
                    category=ImportCategory.SKILLS,
                    key=name,
                    title=name,
                    path=str(skill_dir),
                    origin=origin,
                    secrets_skipped=withheld,
                )
            )


def count_lines(path: Path) -> int:
    """How many non-blank lines a JSONL file holds (0 when it is refused or unreadable)."""
    if not path.is_file() or refuses(path):
        return 0
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def prompt_history(path: Path) -> NotImported | None:
    """A tool's list of past prompts (``history.jsonl``), counted and never imported."""
    count = count_lines(path)
    if not count:
        return None
    return NotImported(
        what="Prompt history",
        count=count,
        why=(
            "PersonalClaw keeps no separate list of past prompts. The prompts in your "
            "conversations come over with them."
        ),
    )


# ── conversations ─────────────────────────────────────────────────────────────


def one_line(text: str, limit: int) -> str:
    """``text`` on one line, cut at ``limit`` characters with an ellipsis."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def conversation_note(messages: list[dict[str, Any]]) -> str:
    """What a person should know about an imported conversation before they pick it."""
    count = sum(1 for m in messages if m["role"] != "tool")
    return (
        f"{count} message{'' if count == 1 else 's'}. Tool calls come over by name; their "
        "output does not."
    )


# ── MCP servers ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class McpServer:
    """One MCP server another tool has configured, with where it has it.

    ``spec`` is PersonalClaw's form of the definition (``type``, ``command``/``url``, ``env``,
    ``headers``, ``disabled``) WITH its values: this is what an import copies (into the credential
    store, through ``write_mcp_document``) and what a listing masks. It never reaches a browser, a
    note or a log.
    """

    #: The importer that read it (``claude_code``, ``codex``).
    source: str
    name: str
    #: Where the tool keeps it: ``user`` (yours everywhere), ``local`` (yours, in one project) or
    #: ``project`` (checked into the project).
    scope: str
    #: The project it belongs to, as recorded (``""`` for a user-scope server).
    project: str
    spec: dict[str, Any]
    #: Where it was found, in words.
    origin: str
    #: Whether the tool lets it run as it stands. False starts the item unticked.
    approved: bool = True
    #: What a person importing it should know first, in words — or ``""``. Value-free, and true
    #: on both surfaces that show it: the onboarding step and the Tools page's Import list.
    note: str = ""

    @property
    def id(self) -> str:
        """A stable id for this server in this scope — what a pick names instead of a path."""
        raw = "\0".join((self.source, self.scope, self.project, self.name)).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]
