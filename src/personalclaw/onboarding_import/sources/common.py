"""What every source reader shares, so two tools' importers cannot drift on the parts that match.

- **Paths another machine recorded.** A tool's home copied from an old laptop names that laptop's
  home (``/Users/old-name/src/app``). :func:`on_this_machine` finds the same path under THIS home,
  and :func:`display_path` says it the way a person reads it.
- **One file, one item** (:func:`text_item`), read through floors 1 and 2.
- **Skills** (:func:`scan_skills`): a directory with a ``SKILL.md``, whichever tool it came from,
  with the supply-chain scan its install will make (:class:`ImportedSkillMarketplace`).
- **Prompt history** (:func:`prompt_history`): counted and named, never imported.
- **A document that does not parse** (:data:`UNPARSABLE`): read as unreadable, never a scan fault.
- **A conversation's title and note** (:func:`one_line`, :func:`conversation_note`).
- **An MCP server** (:class:`McpServer`, :func:`mcp_item`): what a tool has configured, values
  included, for the onboarding scan and the Tools page's Import — one definition, one writer.
- **A command the tool refuses** (:func:`denied_command_item`), and the rest of its settings
  (:func:`settings_not_imported`), named and left where they are.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.onboarding_import.floors import read_text_safely, refuses, safe_text
from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    NotImported,
    ScanResult,
    SkillScan,
)
from personalclaw.skills.marketplace import (
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    read_skill_file_entry,
    scan_before_install,
)

logger = logging.getLogger(__name__)

#: A title is a line in a list, not the first paragraph of a conversation.
TITLE_CHARS = 80

#: What ``json`` and ``tomllib`` raise for a document they cannot read: ``ValueError`` when it is
#: not valid, and ``RecursionError`` when it nests deeper than the interpreter recurses. Either
#: way that one file or line is unreadable, and neither may end the scan of the rest.
UNPARSABLE = (ValueError, RecursionError)

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
    """One file read through floors 1 and 2 into an item. False when nothing was added: the file
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


def _skill_files(skill_dir: Path) -> list[dict[str, Any]]:
    """A skill directory's files as an install stages them: every file but a credential file,
    which is never staged, scanned or installed (the floor the scan counts it with)."""
    files: list[dict[str, Any]] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or refuses(path):
            continue
        try:
            files.append(read_skill_file_entry(path, path.relative_to(skill_dir).as_posix()))
        except OSError:
            logger.warning("skipping unreadable file in imported skill %s", skill_dir.name)
    return files


class ImportedSkillMarketplace(SkillsMarketplace):
    """A transient, single-directory skills source rooted at a foreign skill dir.

    Not registered in the shared registry — another tool's skills dir is not a marketplace. It
    exists so an imported skill is scanned before the import (:func:`scan_skills`) and installed
    by it (``writers._write_skill``) through the one gate a Store skill goes through
    (quarantine → scan → commit → lock), at the ``community`` trust tier (foreign, unsigned).
    """

    def __init__(self, skill_dir: Path) -> None:
        self._skill_dir = Path(skill_dir)

    @property
    def marketplace_type(self) -> str:
        return "onboarding_import"

    @property
    def trust_tier(self) -> str:
        return "community"

    def search(self, query: str, limit: int = 20) -> list[SkillEntry]:  # pragma: no cover
        return []

    def fetch(self, skill_id: str) -> SkillDetail:
        return SkillDetail(
            id=skill_id,
            name=self._skill_dir.name,
            files=_skill_files(self._skill_dir),
            audit_status="pass",
        )


#: The findings a person weighs before importing a skill: what refuses it, and what it needs
#: their acceptance for. A ``low`` finding changes neither, so it is not listed.
_SHOWN_SEVERITIES = frozenset({"warning", "dangerous"})


def _skill_scan(skill_dir: Path, name: str) -> SkillScan | None:
    """The supply-chain scan the import's install will make of this skill, made now: ``None``
    when its files cannot be staged, in which case the install says why."""
    try:
        report, consent = scan_before_install(ImportedSkillMarketplace(skill_dir), name)
    except (ValueError, OSError):
        logger.debug("could not scan imported skill %s", name, exc_info=True)
        return None
    findings = tuple(
        {
            "rule": finding.rule,
            "severity": finding.severity.value,
            "path": finding.path,
            # A snippet of the skill's own file: through the detector like any text shown.
            "evidence": safe_text(finding.evidence)[0],
        }
        for finding in report.findings
        if finding.severity.value in _SHOWN_SEVERITIES
    )
    return SkillScan(verdict=report.verdict.value, findings=findings, consent=consent)


def _warnings_note(scan: SkillScan | None) -> str:
    if scan is None or not scan.needs_acceptance:
        return ""
    count = sum(1 for finding in scan.findings if finding["severity"] == "warning")
    one = count == 1
    return (
        f"Its security scan found {count} warning{'' if one else 's'}, so it comes over only if "
        f"you accept {'it' if one else 'them'}."
    )


def scan_skills(source: str, roots: list[tuple[Path, str]], result: ScanResult) -> None:
    """Every ``<root>/<name>/SKILL.md``, one skill per name: ``roots`` is ``(directory, origin)``
    in the order the tool reads them, and the first root to hold a name is the one it keeps.

    A dot-directory is the tool's own (Codex keeps the skills it ships in ``skills/.system``),
    not one of yours. Each skill carries the supply-chain scan its install will make, so the step
    shows what refuses it, or what it needs accepting, before anything is chosen: a skill whose
    scan has warnings starts unticked.
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
            scan = _skill_scan(skill_dir, name)
            result.items.append(
                ImportItem(
                    source=source,
                    category=ImportCategory.SKILLS,
                    key=name,
                    title=name,
                    path=str(skill_dir),
                    origin=origin,
                    note=_warnings_note(scan),
                    preselect=scan is None or not scan.needs_acceptance,
                    secrets_skipped=withheld,
                    scan=scan,
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


# ── commands a tool refuses, and the rest of its settings ─────────────────────

#: The characters that mean something in a regular expression outside a character class.
_REGEX_SPECIAL_RE = re.compile(r"([.^$*+?{}\[\]\\|()])")


def denied_pattern(pattern: tuple[tuple[str, ...], ...]) -> str:
    """A command prefix as a shell-denylist pattern: its words in order, each position one of the
    words the tool accepts there, apart by whitespace, bounded so ``rm -rf`` does not also refuse
    ``rm -rfv``.

    PersonalClaw matches the pattern anywhere in a command, whatever the case, so it refuses at
    least every command the other tool refused — ``bash -lc "rm -rf build"`` too.
    """
    positions = []
    for alternatives in pattern:
        words = [_REGEX_SPECIAL_RE.sub(r"\\\1", word) for word in alternatives]
        positions.append(words[0] if len(words) == 1 else f"(?:{'|'.join(words)})")
    start = r"\b" if all(re.match(r"\w", w[0]) for w in pattern[0]) else r"(?<!\S)"
    end = r"\b" if all(re.match(r"\w", w[-1]) for w in pattern[-1]) else r"(?!\S)"
    return start + r"\s+".join(positions) + end


def denied_command_item(
    source: str,
    result: ScanResult,
    *,
    key: str,
    pattern: tuple[tuple[str, ...], ...],
    note: str = "",
) -> None:
    """A command the other tool refuses to run, as one it refuses here: a shell-denylist item.

    A command that itself holds a credential is left out and counted: its pattern would carry
    the credential into ``config.json``.
    """
    title, redacted = safe_text(" ".join("|".join(alternatives) for alternatives in pattern))
    if redacted:
        result.secrets_skipped += 1
        return
    clean_note, redactions = safe_text(note)
    result.redactions += redactions
    result.items.append(
        ImportItem(
            source=source,
            category=ImportCategory.DENIED_COMMANDS,
            key=key,
            title=title,
            payload={"pattern": denied_pattern(pattern)},
            note=clean_note,
            redactions=redactions,
        )
    )


#: Rules that ask before a command runs, and rules that let one run unasked: what PersonalClaw
#: does with each, in the words both tools' scans use.
RULES_THAT_ASK = (
    "Command rules that ask first",
    "PersonalClaw has no rule that asks before one particular command.",
)
RULES_THAT_ALLOW = (
    "Command rules that allow without asking",
    "Letting a command run without asking stays your call in PersonalClaw, so an import never "
    "makes it.",
)


def and_list(words: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def not_imported_rows(result: ScanResult, rows: list[tuple[int, str, str]]) -> None:
    """``(count, what, why)`` rows, each named under "Not brought over" when it counts anything."""
    for count, what, why in rows:
        if count:
            result.not_imported.append(NotImported(what=what, count=count, why=why))


#: How many of a tool's option names a "Not brought over" row lists before it says how many more.
_NAMES_SHOWN = 6


def settings_not_imported(result: ScanResult, tool: str, names: list[str]) -> None:
    """A tool's own options, named and left where they are.

    They are the tool's options, not PersonalClaw's: an import that guessed at a match would
    change live settings on a guess, and a copy set aside for review was a file nothing read. So
    the step names them, and no value in them becomes a setting here. (An ``env`` value reaches
    PersonalClaw only inside an MCP server that names it, and then only in the credential store.)
    """
    if not names:
        return
    shown = names[:_NAMES_SHOWN]
    more = len(names) - len(shown)
    listed = and_list([*shown, f"{more} more"] if more else shown)
    one = len(names) == 1
    result.not_imported.append(
        NotImported(
            what=f"{tool} settings",
            count=len(names),
            why=(
                f"{listed} {'is' if one else 'are'} {tool}'s own "
                f"{'option' if one else 'options'}. PersonalClaw keeps its own in Settings, so "
                f"{'it stays' if one else 'they stay'} in {tool}."
            ),
        )
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


def mcp_item(source: str, server: McpServer, *, key: str) -> ImportItem:
    """One MCP server as an item, its definition WHOLE.

    The one secret policy, the same as Tools › Import's: the MCP writer keeps every ``env`` and
    ``headers`` value in the credential store (``secret_refs.write_mcp_document``), so a
    credential is stored as a reference rather than dropped for the user to type in again.
    The item's payload never leaves the server — it is not in :meth:`ImportItem.to_dict` — and
    the note says only what a person should know first.
    """
    return ImportItem(
        source=source,
        category=ImportCategory.MCP_SERVERS,
        key=key,
        title=server.name,
        name=server.name,
        payload=dict(server.spec),
        origin=server.origin,
        note=server.note,
        preselect=server.approved,
    )
