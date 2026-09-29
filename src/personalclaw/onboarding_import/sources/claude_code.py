"""Claude Code scanner — ``$CLAUDE_CONFIG_DIR`` (default ``~/.claude``) and the files beside it.

A pure function of what Claude Code writes: :func:`scan` opens files, applies the floors, and
returns a :class:`~..model.ScanResult`. It holds no store, no session and no config handle, so
it is fixture-testable against a throwaway root — and it never writes to anything it reads
(importing from another tool must not modify that tool).

What it maps — the layout Claude Code 2.x writes, and nothing it does not:

==============================================  =============================================
``CLAUDE.md``, ``rules/**/*.md``                ``instructions`` (yours, every project)
``<project>/CLAUDE.md``, ``.claude/CLAUDE.md``,  ``instructions`` (one per project Claude Code
``CLAUDE.local.md``                             has been used in)
``projects/<cwd>/memory/*.md``                  ``memories`` (``MEMORY.md`` is their index)
``.claude.json`` → ``mcpServers``               ``mcp_servers``, user scope
``.claude.json`` → ``projects[p].mcpServers``   ``mcp_servers``, local scope
``<project>/.mcp.json`` → ``mcpServers``        ``mcp_servers``, project scope
``skills/<name>/SKILL.md``                      ``skills``
``agents/**/*.md``                              ``agents``
``commands/**/*.md``                            ``prompts``
``projects/<cwd>/<session>.jsonl``              ``conversations``
``settings.json`` → ``permissions.deny``        ``denied_commands`` (each ``Bash(…)`` rule)
``settings.json`` → the rest, ``history.jsonl``  not imported — the scan counts each and says why
==============================================  =============================================

**Where the projects are.** Claude Code records every directory it has been used in as a key of
``.claude.json`` → ``projects``, and names each one's transcript and memory directory by that
path with every character that is not a letter or digit turned into ``-``. A ``~/.claude`` copied
from another machine still names that machine's paths (``/Users/old-name/src/app``), so a
recorded path that does not exist here is looked for under THIS home before it is given up
(:func:`~.common.on_this_machine`) — a project's own ``.mcp.json`` and ``CLAUDE.md`` live in the
project, not in ``~/.claude``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.onboarding_import.floors import (
    FileUnreadable,
    one_walk,
    read_text_safely,
    refuses,
    safe_text,
    why_unreadable,
)
from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ScanResult,
    UnreadableFile,
)
from personalclaw.onboarding_import.sources.common import (
    GIVE_WAY_LINES,
    LOOK_BYTES,
    READINGS,
    RULES_THAT_ALLOW,
    RULES_THAT_ASK,
    TITLE_CHARS,
    UNDECIDED,
    UNPARSABLE,
    McpListing,
    McpServer,
    Reading,
    SessionUnreadable,
    Transcript,
    Undecided,
    Unreadable,
    conversation_note,
    denied_command_item,
    display_path,
    file_signature,
    give_way,
    is_final,
    markdown_files,
    mcp_item,
    message_count,
    not_imported_rows,
    not_read,
    note_unreadable,
    on_this_machine,
    one_line,
    prompt_history,
    scan_skills,
    settings_not_imported,
    slug_name,
    text_item,
    unreadable_conversations,
    unreadable_file,
)
from personalclaw.prompt_providers.base import ARGUMENTS_VARIABLE, POSITIONAL_VARIABLES

NAME = "claude_code"
DISPLAY_NAME = "Claude Code"
ENV_VAR = "CLAUDE_CONFIG_DIR"
DEFAULT_ROOT = "~/.claude"

_INSTRUCTION_FILE = "CLAUDE.md"
_RULES_DIR = "rules"
#: A project's own instruction files, as Claude Code reads them from the project directory.
_PROJECT_INSTRUCTION_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
_PROJECTS_DIR = "projects"
_MEMORY_DIR = "memory"
#: The auto-memory index Claude Code keeps beside the topic files it links.
_MEMORY_INDEX = "MEMORY.md"
_SKILLS_DIR = "skills"
_AGENTS_DIR = "agents"
_COMMANDS_DIR = "commands"
_GLOBAL_CONFIG = ".claude.json"
_LEGACY_GLOBAL_CONFIG = ".config.json"
#: A project's own MCP servers, checked into the project.
_PROJECT_MCP_FILE = ".mcp.json"
#: The one table of an MCP config that holds servers; everything else in it is not imported.
_MCP_TABLE = "mcpServers"
_SETTINGS_FILE = "settings.json"
_HISTORY_FILE = "history.jsonl"


# ── roots ─────────────────────────────────────────────────────────────────────


def _configured_root() -> Path | None:
    """``$CLAUDE_CONFIG_DIR``, when it is set: the one place this module reads it."""
    env = os.environ.get(ENV_VAR, "").strip()
    return Path(env).expanduser() if env else None


def resolve_root() -> Path:
    """Env var first, documented default second (no other search paths).

    The default is ``Path.home() / ".claude"`` — the same place as ``DEFAULT_ROOT`` expanded,
    but resolved through ``Path.home()``, the one home reader every other path here uses.
    """
    return _configured_root() or Path.home() / ".claude"


def global_config_path(root: Path | None = None) -> Path:
    """Claude Code's own global config — its user- and local-scope MCP servers and its projects.

    Claude Code's rule, read from its source: a legacy ``.config.json`` in the config root wins
    when present; otherwise ``.claude.json`` sits IN ``$CLAUDE_CONFIG_DIR`` when that is set, and
    in the home directory — beside ``~/.claude``, not inside it — when it is not. An explicit
    ``root`` is a config directory named the way ``$CLAUDE_CONFIG_DIR`` names one, so its file is
    inside it. Resolved per call, from the same variable :func:`resolve_root` reads, so the MCP
    importer and this scanner cannot read two different Claude Codes.
    """
    base = root if root is not None else resolve_root()
    legacy = base / _LEGACY_GLOBAL_CONFIG
    if legacy.is_file():
        return legacy
    if root is not None or _configured_root() is not None:
        return base / _GLOBAL_CONFIG
    return Path.home() / _GLOBAL_CONFIG


def config_dir_of(config_path: Path) -> Path:
    """The config directory that goes with a global config file: ``~/.claude`` for the
    ``~/.claude.json`` beside it, the file's own directory for one inside a config directory."""
    home = Path.home()
    if config_path.parent == home and config_path.name == _GLOBAL_CONFIG:
        return home / ".claude"
    return config_path.parent


def _read_json_document(path: Path) -> dict[str, Any]:
    """A Claude Code JSON file as a dict, values INCLUDED — ``{}`` when it is not there or is
    refused. For the readers that must see values (an MCP server's definition, the settings a
    ``.mcp.json`` expands from); everything else goes through the floors.

    A file that IS there and cannot be read, or is not a JSON object, raises
    :class:`FileUnreadable`. "Not there" means Claude Code keeps nothing of that kind;
    "unreadable" means nobody knows what it keeps, and answering both with ``{}`` showed a broken
    config as "nothing to import"."""
    if not path.is_file() or refuses(path):
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, *UNPARSABLE) as exc:
        raise FileUnreadable(path, why_unreadable(exc, kind="JSON")) from exc
    if not isinstance(data, dict):
        raise FileUnreadable(path, "it is not a JSON object")
    return data


# ── projects ──────────────────────────────────────────────────────────────────


def encoded_project_dir(recorded: str) -> str:
    """The name Claude Code gives a project's directory under ``projects/``: every character that
    is not a letter or a digit becomes ``-`` (``/Users/you/src/app`` → ``-Users-you-src-app``)."""
    return re.sub(r"[^A-Za-z0-9]", "-", recorded)


@dataclass(frozen=True)
class Project:
    """A directory Claude Code has been used in, as its global config records it."""

    #: The path as Claude Code recorded it (possibly another machine's).
    recorded: str
    #: The directory on this machine (:func:`~.common.on_this_machine`), or ``None``.
    local: Path | None
    #: The project's entry in ``.claude.json`` (its local-scope servers, its approvals).
    entry: dict[str, Any]

    @property
    def label(self) -> str:
        return display_path(self.local) if self.local is not None else self.recorded

    @property
    def distrusted(self) -> bool:
        """True when the folder's trust prompt was answered and NOT accepted. Claude Code reads a
        project's own files only once that prompt is accepted; an entry without the key (an older
        Claude Code) says nothing either way, so only an explicit ``false`` counts."""
        return self.entry.get("hasTrustDialogAccepted") is False


def projects(config: dict[str, Any]) -> list[Project]:
    """Every project a parsed ``.claude.json`` records, in path order."""
    table = config.get("projects")
    if not isinstance(table, dict):
        return []
    return [
        Project(recorded=str(path), local=on_this_machine(str(path)), entry=entry)
        for path, entry in sorted(table.items(), key=lambda pair: str(pair[0]))
        if isinstance(entry, dict)
    ]


# ── MCP servers: the one reader of Claude Code's three scopes ──────────────────

#: Claude Code's MCP scopes, in the order it resolves a name: a local-scope server shadows a
#: project one, which shadows a user one. Listed user-first because that is the one a person has
#: everywhere; the step shows each server's scope, so the order is only presentation.
SCOPE_USER = "user"
SCOPE_LOCAL = "local"
SCOPE_PROJECT = "project"

#: Why a project's own file starts unticked when its folder's trust prompt was not accepted.
_UNTRUSTED_FOLDER = "Claude Code's trust prompt for this folder was never accepted."

#: ``${VAR}`` and ``${VAR:-default}`` — the expansion Claude Code applies to a ``.mcp.json``.
_EXPANSION_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _settings_env(settings: dict[str, Any]) -> dict[str, str]:
    """The ``env`` block of Claude Code's ``settings.json`` — the variables every session it
    starts has, and so what a project's ``${VAR}`` expands to. Values included; see
    :func:`_read_json_document`."""
    env = settings.get("env")
    if not isinstance(env, dict):
        return {}
    return {str(k): str(v) for k, v in env.items() if isinstance(v, (str, int, float))}


def _expand(value: Any, env: dict[str, str], missing: set[str]) -> Any:
    """``value`` with ``${VAR}`` / ``${VAR:-default}`` expanded from ``env``, recursively.

    A name ``env`` does not set, with no default, is left as written and recorded in
    ``missing``: PersonalClaw does not expand variables in a server's definition, so an
    unexpanded reference has to be SAID, not passed on as though it were the value.
    """
    if isinstance(value, str):

        def _one(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            missing.add(name)
            return match.group(0)

        return _EXPANSION_RE.sub(_one, value)
    if isinstance(value, dict):
        return {k: _expand(v, env, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, env, missing) for v in value]
    return value


def _servers_of(table: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(table, dict):
        return []
    return sorted(
        ((str(name), spec) for name, spec in table.items() if isinstance(spec, dict)),
        key=lambda pair: pair[0],
    )


def _listed(value: Any) -> set[str]:
    return {str(v) for v in value} if isinstance(value, list) else set()


def _project_approvals(project: Project, user_settings: dict[str, Any]) -> tuple[set, set, bool]:
    """``(approved, turned_off, all_approved)`` for a project's own ``.mcp.json`` servers.

    Claude Code asks before it runs a server a repository brought with it, and keeps the answer
    in the project's ``.claude.json`` entry and in its ``.claude/settings*.json`` — or approves
    every one with ``enableAllProjectMcpServers``.
    """
    approved = _listed(project.entry.get("enabledMcpjsonServers"))
    turned_off = _listed(project.entry.get("disabledMcpjsonServers"))
    every = bool(user_settings.get("enableAllProjectMcpServers"))
    if project.local is not None:
        for name in ("settings.json", "settings.local.json"):
            doc = _read_json_document(project.local / ".claude" / name)
            approved |= _listed(doc.get("enabledMcpjsonServers"))
            turned_off |= _listed(doc.get("disabledMcpjsonServers"))
            every = every or bool(doc.get("enableAllProjectMcpServers"))
    return approved, turned_off, every


def mcp_servers(root: Path | None = None, *, config_path: Path | None = None) -> McpListing:
    """Every MCP server Claude Code has configured, in all three of its scopes, and each file of
    its configuration that is there and could not be read.

    THE reader of Claude Code's MCP configuration: the onboarding scan and the Tools page's Import
    both call it and both write what it returns through the one MCP writer, so the two cannot
    disagree about what Claude Code has or where its values go.
    ``config_path`` is the global config (:func:`global_config_path` of ``root`` when omitted) and
    ``root`` the config directory whose ``settings.json`` goes with it (:func:`config_dir_of` the
    file when omitted), so a caller naming one file never reads another installation's settings.

    - **User scope** — ``.claude.json`` → ``mcpServers``: every project has them.
    - **Local scope** — ``.claude.json`` → ``projects[path].mcpServers``: yours, in one project.
    - **Project scope** — ``<project>/.mcp.json``: checked into the project, run by Claude Code
      only once approved there, with ``${VAR}`` expanded from the environment — here, from the
      one environment this reader can see, Claude Code's ``settings.json`` ``env``.

    A file that cannot be read leaves out what depends on it, and is named in the listing's
    ``unreadable``. Without the global config nothing is listed: it holds the user- and
    local-scope servers and the list of projects. Without ``settings.json`` no project server is
    listed, because whether each was approved, and what its ``${VAR}`` expands to, are unknown.
    Without a project's ``.mcp.json`` or ``.claude/settings*.json``, that project's own servers
    are not listed.
    """
    config_path = config_path or global_config_path(root)
    base = root if root is not None else config_dir_of(config_path)
    try:
        config = _read_json_document(config_path)
    except FileUnreadable as exc:
        return McpListing(servers=[], unreadable=[unreadable_file(exc)])
    out: list[McpServer] = [
        McpServer(
            source=NAME, name=name, scope=SCOPE_USER, project="", spec=spec, origin="User scope"
        )
        for name, spec in _servers_of(config.get(_MCP_TABLE))
    ]
    known = projects(config)
    for project in known:
        for name, spec in _servers_of(project.entry.get(_MCP_TABLE)):
            out.append(
                McpServer(
                    source=NAME,
                    name=name,
                    scope=SCOPE_LOCAL,
                    project=project.recorded,
                    spec=spec,
                    origin=f"Local scope · {project.recorded}",
                )
            )
    try:
        user_settings = _read_json_document(base / _SETTINGS_FILE)
    except FileUnreadable as exc:
        return McpListing(servers=out, unreadable=[unreadable_file(exc)])
    env = _settings_env(user_settings)
    unreadable: list[UnreadableFile] = []
    for project in known:
        if project.local is None:
            continue
        mcp_file = project.local / _PROJECT_MCP_FILE
        try:
            doc = _read_json_document(mcp_file)
            if not doc:
                continue
            approved, turned_off, every = _project_approvals(project, user_settings)
        except FileUnreadable as exc:
            unreadable.append(unreadable_file(exc))
            continue
        for name, spec in _servers_of(doc.get(_MCP_TABLE)):
            missing: set[str] = set()
            expanded = _expand(spec, env, missing)
            if project.distrusted:
                reason = _UNTRUSTED_FOLDER
            elif name in turned_off:
                reason = "It is turned off for this project in Claude Code."
            elif not (every or name in approved):
                reason = "It came with the project, and it was never approved in Claude Code."
            else:
                reason = ""
            out.append(
                McpServer(
                    source=NAME,
                    name=name,
                    scope=SCOPE_PROJECT,
                    project=project.recorded,
                    spec=expanded,
                    origin=f"Project · {display_path(mcp_file)}",
                    approved=not reason,
                    note=_project_server_note(reason, sorted(missing)),
                )
            )
    return McpListing(servers=out, unreadable=unreadable)


def _project_server_note(reason: str, unresolved: list[str]) -> str:
    """What a person importing a project's server should know first: why Claude Code does not
    run it, and the ``${VAR}`` names nothing sets."""
    parts = [reason] if reason else []
    if unresolved:
        names = ", ".join(f"${{{n}}}" for n in unresolved)
        parts.append(
            f"It uses {names}, which Claude Code's settings do not set: give "
            f"{'it a value' if len(unresolved) == 1 else 'each a value'} "
            "on the Tools page after importing."
        )
    return " ".join(parts)


# ── the scan ──────────────────────────────────────────────────────────────────


def scan(root: Path | str | None = None, *, look: bool = False) -> ScanResult:
    """What Claude Code holds. To ``look`` is to read each conversation not read before only as far
    as its first prompt (:func:`_scan_conversations`); otherwise every one is read in full."""
    explicit = Path(root).expanduser() if root is not None else None
    base = explicit if explicit is not None else resolve_root()
    result = ScanResult(
        source=NAME, display_name=DISPLAY_NAME, root=str(base), present=base.is_dir()
    )
    if not result.present:
        return result

    with one_walk():
        try:
            config = _read_json_document(global_config_path(explicit))
        except FileUnreadable as exc:
            # Named, and read as holding no projects: each project's own files are then not
            # found, which the name says, rather than a Claude Code that was never used.
            note_unreadable(result, unreadable_file(exc))
            config = {}
        known = projects(config)
        seen_files: set[Path] = set()
        _scan_instructions(base, known, seen_files, result)
        _scan_memories(base, known, result)
        _scan_mcp(base, explicit, result)
        scan_skills(NAME, [(base / _SKILLS_DIR, "")], result)
        _scan_agents(base, result)
        _scan_commands(base, result)
        _scan_conversations(base, known, result, look=look)
        _scan_settings(base, result)
        prompt_history(base / _HISTORY_FILE, result)
        _count_withheld_files(base, result)
    result.note_withheld()
    return result


def _count_withheld_files(base: Path, result: ScanResult) -> None:
    """Count the credential FILES at the root that we deliberately never opened.

    ``.credentials.json`` sits next to ``settings.json`` in a real root. It is not in
    any category's map, so nothing would ever read it — but saying so is the point:
    the user learns a credential file was present and left alone, and the floor is
    visible instead of implicit. Files the category scanners already accounted for
    are excluded so nothing is counted twice.
    """
    visited = {_INSTRUCTION_FILE, _SETTINGS_FILE, _GLOBAL_CONFIG, _HISTORY_FILE}
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name not in visited and refuses(path):
            result.secrets_skipped += 1


def _scan_instructions(
    base: Path, known: list[Project], seen_files: set[Path], result: ScanResult
) -> None:
    """Your ``CLAUDE.md`` and ``rules/``, then each project's own instruction files.

    ``seen_files`` makes one file one item: ``~/.claude/CLAUDE.md`` is also the
    ``.claude/CLAUDE.md`` of the project Claude Code recorded as the home directory itself.

    A project's own file applies where Claude Code applies it, to work in that project: its item
    carries the project's folder on this machine (``payload["workspace"]``), which is where the
    import says it applies.
    """
    top = base / _INSTRUCTION_FILE
    if top.is_file():
        text_item(
            NAME,
            top,
            result,
            category=ImportCategory.INSTRUCTIONS,
            key=_INSTRUCTION_FILE,
            title=_INSTRUCTION_FILE,
            seen_files=seen_files,
        )
    rules = base / _RULES_DIR
    for path in markdown_files(rules):
        rel = f"{_RULES_DIR}/{path.relative_to(rules).as_posix()}"
        text_item(
            NAME,
            path,
            result,
            category=ImportCategory.INSTRUCTIONS,
            key=rel,
            title=rel,
            seen_files=seen_files,
        )
    for project in known:
        if project.local is None:
            continue
        for rel in _PROJECT_INSTRUCTION_FILES:
            path = project.local / rel
            if not path.is_file():
                continue
            text_item(
                NAME,
                path,
                result,
                category=ImportCategory.INSTRUCTIONS,
                key=f"project:{project.recorded}/{rel}",
                title=rel,
                origin=f"Project · {project.label}",
                note=_UNTRUSTED_FOLDER if project.distrusted else "",
                preselect=not project.distrusted,
                payload={"workspace": str(project.local)},
                seen_files=seen_files,
            )


#: One line of ``MEMORY.md``: ``- [Title](file.md) — hook``.
_INDEX_LINE_RE = re.compile(r"^\s*[-*]\s*\[(?P<title>[^\]]+)\]\((?P<file>[^)\s]+\.md)\)")


def _memory_index(index: Path) -> tuple[dict[str, str], bool]:
    """``({file name: title}, is_only_an_index)`` for a ``MEMORY.md``.

    Claude Code's auto-memory keeps one topic per file and ``MEMORY.md`` as the list of them; an
    older layout kept the notes in ``MEMORY.md`` itself. A file of nothing but index lines is the
    index, and the topic files it links are the memories. One with anything else in it is a
    memory of its own, and imported as one. Raises :class:`FileUnreadable` for an index that is
    there and will not open.
    """
    text, _redactions, _skipped = read_text_safely(index)
    titles: dict[str, str] = {}
    only_index = True
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _INDEX_LINE_RE.match(line)
        if match is None:
            only_index = False
            continue
        titles[Path(match.group("file")).name] = match.group("title").strip()
    return titles, only_index


def _project_for_dir(encoded: str, known: list[Project]) -> Project | None:
    for project in known:
        if encoded_project_dir(project.recorded) == encoded:
            return project
    return None


def _project_dirs(base: Path) -> list[Path]:
    root = base / _PROJECTS_DIR
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _project_label(encoded: str, known: list[Project]) -> str:
    project = _project_for_dir(encoded, known)
    return project.label if project is not None else encoded


def _scan_memories(base: Path, known: list[Project], result: ScanResult) -> None:
    """Claude Code's auto-memory: ``projects/<cwd>/memory/*.md``, one topic per file."""
    from personalclaw.skills.loader import parse_frontmatter

    for project_dir in _project_dirs(base):
        mem_dir = project_dir / _MEMORY_DIR
        if not mem_dir.is_dir():
            continue
        origin = f"Project · {_project_label(project_dir.name, known)}"
        index = mem_dir / _MEMORY_INDEX
        try:
            titles, only_index = _memory_index(index) if index.is_file() else ({}, True)
        except FileUnreadable as exc:
            # Named once; the topic files still come over, under their own names.
            note_unreadable(result, unreadable_file(exc))
            titles, only_index = {}, True
        for path in sorted(mem_dir.glob("*.md")):
            if not path.is_file() or (path.name == _MEMORY_INDEX and only_index):
                continue
            try:
                text, redactions, skipped = read_text_safely(path)
            except FileUnreadable as exc:
                note_unreadable(result, unreadable_file(exc))
                continue
            result.secrets_skipped += skipped
            if not text.strip():
                continue
            result.redactions += redactions
            declared = parse_frontmatter(text).get("name", "").strip()
            result.items.append(
                ImportItem(
                    source=NAME,
                    category=ImportCategory.MEMORIES,
                    key=f"{_PROJECTS_DIR}/{project_dir.name}/{_MEMORY_DIR}/{path.name}",
                    title=declared or titles.get(path.name) or path.stem,
                    text=text,
                    origin=origin,
                    redactions=redactions,
                    secrets_skipped=skipped,
                )
            )


def _scan_mcp(base: Path, explicit: Path | None, result: ScanResult) -> None:
    """Every server in every scope (:func:`mcp_servers`), definition whole: the MCP writer keeps
    each ``env`` and ``headers`` value in the credential store, as Tools › Import does. A file of
    the configuration that could not be read is named on the scan."""
    listing = mcp_servers(base, config_path=global_config_path(explicit))
    for entry in listing.unreadable:
        note_unreadable(result, entry)
    for server in listing.servers:
        user = server.scope == SCOPE_USER
        result.items.append(
            mcp_item(
                NAME,
                server,
                key=server.name if user else f"{server.scope}:{server.project}:{server.name}",
            )
        )


def _scan_agents(base: Path, result: ScanResult) -> None:
    """Subagents: ``agents/*.md``, a ``name``/``description``/``tools``/``model`` frontmatter over
    the agent's instructions. An agent here is a profile on the Agents page."""
    from personalclaw.skills.loader import SkillsLoader, parse_frontmatter

    agents_root = base / _AGENTS_DIR
    for path in markdown_files(agents_root):
        try:
            text, redactions, skipped = read_text_safely(path)
        except FileUnreadable as exc:
            note_unreadable(result, unreadable_file(exc))
            continue
        result.secrets_skipped += skipped
        if not text.strip():
            continue
        meta = parse_frontmatter(text)
        body = SkillsLoader.strip_frontmatter(text)
        declared = meta.get("name", "").strip() or path.stem
        dropped = []
        if meta.get("tools", "").strip():
            dropped.append(f"tools list ({meta['tools'].strip()})")
        model = meta.get("model", "").strip()
        if model and model != "inherit":
            dropped.append(f"model ({model})")
        note = ""
        if dropped:
            note = (
                f"Its Claude Code {' and '.join(dropped)} "
                f"{'is' if len(dropped) == 1 else 'are'} not carried over."
            )
        result.redactions += redactions
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.AGENTS,
                key=f"{_AGENTS_DIR}/{path.relative_to(agents_root).as_posix()}",
                title=declared,
                name=slug_name(declared, lower=True),
                text=body,
                payload={"description": meta.get("description", "").strip()},
                note=note,
                redactions=redactions,
                secrets_skipped=skipped,
            )
        )


#: ``$ARGUMENTS`` and ``$1``…``$9`` — what a Claude Code command substitutes.
_ARGUMENTS_RE = re.compile(r"\$ARGUMENTS\b")
_POSITIONAL_RE = re.compile(r"\$([1-9])(?![0-9])")


def command_prompt(body: str, *, argument_hint: str) -> tuple[str, list[dict[str, Any]]]:
    """A command's body as a PersonalClaw prompt: ``(content, variables)``.

    ``$ARGUMENTS`` becomes the ``{{arguments}}`` variable and ``$1``…``$9`` become ``{{arg1}}``…,
    each declared. Those are the names a prompt run by name fills from the text typed after it
    (``prompt_providers.base.ARGUMENTS_VARIABLE``), so ``@name <text>`` gives the prompt its
    arguments where Claude Code took them from the command line.
    """
    variables: list[dict[str, Any]] = []
    content = body
    if _ARGUMENTS_RE.search(content):
        content = _ARGUMENTS_RE.sub(f"{{{{{ARGUMENTS_VARIABLE}}}}}", content)
        variables.append(
            {
                "name": ARGUMENTS_VARIABLE,
                "type": "textarea",
                "description": argument_hint or "What the command was given after its name.",
            }
        )
    for digit in sorted({m.group(1) for m in _POSITIONAL_RE.finditer(content)}):
        name = POSITIONAL_VARIABLES[int(digit) - 1]
        content = re.sub(rf"\${digit}(?![0-9])", f"{{{{{name}}}}}", content)
        variables.append({"name": name, "type": "text", "description": ""})
    return content, variables


def _scan_commands(base: Path, result: ScanResult) -> None:
    """Slash commands: ``commands/**/*.md``. A command is a prompt you run by name."""
    from personalclaw.skills.loader import SkillsLoader, parse_frontmatter

    commands_root = base / _COMMANDS_DIR
    for path in markdown_files(commands_root):
        try:
            text, redactions, skipped = read_text_safely(path)
        except FileUnreadable as exc:
            note_unreadable(result, unreadable_file(exc))
            continue
        result.secrets_skipped += skipped
        if not text.strip():
            continue
        meta = parse_frontmatter(text)
        rel = path.relative_to(commands_root)
        name = slug_name("-".join((*rel.parent.parts, rel.stem)), lower=False)
        content, variables = command_prompt(
            SkillsLoader.strip_frontmatter(text), argument_hint=meta.get("argument-hint", "")
        )
        dropped = [
            label
            for key, label in (("allowed-tools", "allowed tools"), ("model", "model"))
            if meta.get(key, "").strip()
        ]
        result.redactions += redactions
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.PROMPTS,
                key=f"{_COMMANDS_DIR}/{rel.as_posix()}",
                title=f"/{name}",
                name=name,
                text=content,
                payload={
                    "description": meta.get("description", "").strip(),
                    "variables": variables,
                },
                note=(
                    f"Its Claude Code {' and '.join(dropped)} "
                    f"{'is' if dropped == ['model'] else 'are'} not carried over."
                    if dropped
                    else ""
                ),
                redactions=redactions,
                secrets_skipped=skipped,
            )
        )


# ── conversations ─────────────────────────────────────────────────────────────

#: Tool-input fields that say what a call did, in the order they are preferred.
_TOOL_SUMMARY_FIELDS = ("description", "command", "file_path", "path", "pattern", "url", "query")
_TOOL_SUMMARY_CHARS = 160


def _user_text(content: Any) -> str:
    """What the person typed, from a ``user`` line — ``""`` for a line of tool results."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n\n".join(p for p in parts if p.strip())


def _tool_line(block: dict[str, Any]) -> str:
    name = str(block.get("name") or "tool")
    raw = block.get("input")
    params: dict[str, Any] = raw if isinstance(raw, dict) else {}
    for field_name in _TOOL_SUMMARY_FIELDS:
        value = params.get(field_name)
        if isinstance(value, str) and value.strip():
            return f"{name}: {one_line(value, _TOOL_SUMMARY_CHARS)}"
    return name


class _Lines:
    """A Claude Code transcript, line by line: the ONE reading of its lines, whether it is read in
    full (:func:`read_conversation`) or only as far as its first prompt (:func:`_reading`)."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.redactions = 0
        self.summary = ""
        self.cwd = ""
        #: The first prompt, once a line has held one.
        self.prompt = ""

    def feed(self, raw: str) -> None:
        try:
            line = json.loads(raw)
        except UNPARSABLE:
            return
        if not isinstance(line, dict):
            return
        kind = line.get("type")
        if kind == "summary" and isinstance(line.get("summary"), str):
            self.summary = line["summary"]
            return
        if kind not in ("user", "assistant"):
            return
        if line.get("isSidechain") or line.get("isMeta") or line.get("isCompactSummary"):
            return
        self.cwd = self.cwd or str(line.get("cwd") or "")
        ts = str(line.get("timestamp") or "")
        held = line.get("message")
        message: dict[str, Any] = held if isinstance(held, dict) else {}
        content = message.get("content")
        if kind == "user":
            text = _user_text(content)
            if not text.strip():
                return
            cleaned, n = safe_text(text)
            self.redactions += n
            self.messages.append({"role": "user", "content": cleaned, "ts": ts})
            self.prompt = self.prompt or cleaned
            return
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                cleaned, n = safe_text(str(block["text"]))
                self.redactions += n
                last = self.messages[-1] if self.messages else None
                if last is not None and last["role"] == "assistant":
                    # One reply per stretch of text: Claude Code writes a line per block.
                    last["content"] = f"{last['content']}\n\n{cleaned}"
                    last["ts"] = ts or last["ts"]
                else:
                    self.messages.append({"role": "assistant", "content": cleaned, "ts": ts})
            elif block.get("type") == "tool_use":
                cleaned, n = safe_text(_tool_line(block))
                self.redactions += n
                self.messages.append({"role": "tool", "content": cleaned, "ts": ts})

    def title(self) -> str:
        return safe_text(one_line(self.summary or self.prompt, TITLE_CHARS))[0]

    def conversation(self) -> dict[str, Any] | None:
        """The whole file's conversation, or ``None`` when no line held a prompt."""
        if not self.prompt:
            return None
        stamps = [m["ts"] for m in self.messages if m["ts"]]
        return {
            "messages": self.messages,
            "title": self.title(),
            "created_at": stamps[0] if stamps else "",
            "updated_at": stamps[-1] if stamps else "",
            "cwd": self.cwd,
        }

    def transcript(self, path: Path, *, whole: bool) -> Transcript:
        return Transcript(
            title=self.title(),
            session=path.stem,
            cwd=self.cwd,
            messages=message_count(self.messages) if whole else None,
            redactions=self.redactions if whole else None,
        )


def _read_lines(path: Path, *, look: bool) -> _Lines | Undecided:
    """``path``'s lines, read in full — or, to ``look``, only until the first prompt, and never
    past :data:`LOOK_BYTES`: :data:`UNDECIDED` when none was reached by then.

    Raises :class:`SessionUnreadable` when the file cannot be opened or read, as Codex's reader
    does. It used to answer ``None``, the answer for a file with no prompt in it, so a transcript
    that would not open was left out of the step without a word, and an import of it said it
    held no conversation."""
    lines = _Lines()
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            read = 0
            for count, raw in enumerate(handle):
                if not count % GIVE_WAY_LINES:
                    give_way()
                lines.feed(raw)
                if look:
                    if lines.prompt:
                        return lines
                    read += len(raw)
                    if read >= LOOK_BYTES:
                        return UNDECIDED
    except OSError as exc:
        raise SessionUnreadable(not_read(exc)) from None
    return lines


def _reading(path: Path, *, look: bool) -> Reading:
    """What ``path`` holds, as the step lists it: remembered while the file is unchanged, else
    read — in full, or to ``look``, only as far as its first prompt. A file that cannot be read is
    remembered as :class:`Unreadable`, with why."""
    signature = file_signature(path)
    if signature is None:
        return None
    found, reading = READINGS.recall(path, signature, whole=not look)
    if found:
        return reading
    try:
        lines = _read_lines(path, look=look)
    except SessionUnreadable as exc:
        reading = Unreadable(exc.reason)
    else:
        if lines is UNDECIDED:
            reading = lines
        elif lines.prompt:
            # A look that reached the end of the file before its first prompt read the whole file.
            reading = lines.transcript(path, whole=not look)
        else:
            reading = None
    READINGS.keep(path, signature, reading)
    return reading


def read_conversation(path: Path) -> tuple[dict[str, Any], int] | None:
    """One Claude Code transcript as a PersonalClaw conversation: ``(conversation, redactions)``.

    ``conversation`` is ``{"messages", "title", "created_at", "updated_at", "cwd"}``. Messages are
    what a person reads back: each prompt, each reply, and each tool call by name and what it
    was for. Tool OUTPUT is not carried — it is where a transcript is largest and where a pasted
    or printed credential sits — and neither are a subagent's own turns, Claude Code's notices,
    or the summary it writes after compacting (the conversation it summarises is imported
    whole). Every text passes floor 2. ``None`` for a file with no prompt in it.

    Raises :class:`SessionUnreadable` for a file that cannot be read, which the import refuses with
    the reason. Read whole, so what the step says of the file becomes final too.
    """
    if refuses(path):
        return None
    signature = file_signature(path)
    try:
        lines = _read_lines(path, look=False)
    except SessionUnreadable as exc:
        if signature is not None:
            READINGS.keep(path, signature, Unreadable(exc.reason))
        raise
    if not isinstance(lines, _Lines):
        return None
    conversation = lines.conversation()
    if signature is not None:
        READINGS.keep(path, signature, lines.transcript(path, whole=True) if conversation else None)
    return (conversation, lines.redactions) if conversation is not None else None


def read_for_import(item: ImportItem) -> tuple[dict[str, Any], int] | None:
    """The conversation ``item`` names, read in full now: :func:`read_conversation` of its file.
    Raises :class:`SessionUnreadable` for a file that cannot be read."""
    return read_conversation(Path(item.path))


def read_in_full(path: Path) -> None:
    """Read one conversation file in full, so what the step says of it is final."""
    if not refuses(path):
        _reading(path, look=False)


def _scan_conversations(
    base: Path, known: list[Project], result: ScanResult, *, look: bool
) -> None:
    """Every session transcript, ``projects/<cwd>/<session id>.jsonl`` — a conversation each.

    A subagent's own transcript (``agent-*.jsonl``) is part of the conversation that started it,
    not a conversation of its own. To ``look`` is to read each file not read before only as far as
    its first prompt: a provisional item, read in full later (:attr:`ScanResult.unread`). A
    transcript that cannot be read is named with why under "Not brought over", the row Codex's
    importer names its own in.
    """
    unreadable: list[str] = []
    for project_dir in _project_dirs(base):
        project = _project_label(project_dir.name, known)
        origin = f"Project · {project}"
        for path in sorted(project_dir.glob("*.jsonl")):
            if not path.is_file() or path.name.startswith("agent-") or refuses(path):
                continue
            result.conversation_files += 1
            reading = _reading(path, look=look)
            if not is_final(reading):
                result.unread.append(path)
            if isinstance(reading, Unreadable):
                unreadable.append(f"{path.name} in {project} {reading.reason}.")
                continue
            if not isinstance(reading, Transcript):
                continue
            redactions = reading.redactions or 0
            result.redactions += redactions
            result.items.append(
                ImportItem(
                    source=NAME,
                    category=ImportCategory.CONVERSATIONS,
                    key=f"{_PROJECTS_DIR}/{project_dir.name}/{path.name}",
                    title=reading.title,
                    name=reading.session,
                    path=str(path),
                    origin=origin,
                    note=conversation_note(reading.messages),
                    redactions=redactions,
                    provisional=not reading.whole,
                )
            )
    not_imported_rows(result, [unreadable_conversations(unreadable)])


#: A Bash permission rule, ``Bash(<command>)`` — or ``Bash`` alone, which is every command.
_BASH_RULE_RE = re.compile(r"^Bash(?:\((?P<command>.*)\))?$", re.DOTALL)
#: How a rule's command says "and anything after it": ``Bash(npm run test:*)``, ``Bash(ls *)``.
_ANY_AFTER = (":*", " *")
#: The permission lists a rule can be in, and ``settings.json``'s keys that are not options.
_RULE_LISTS = ("deny", "ask", "allow")
_NOT_OPTIONS = frozenset({"$schema", "permissions"})


def _refused_command_words(command: str) -> tuple[str, ...] | None:
    """The words a ``Bash(<command>)`` deny rule refuses every command starting with, or
    ``None`` when a wildcard anywhere but the end leaves no such words (``Bash(git * main)``,
    ``Bash``)."""
    for suffix in _ANY_AFTER:
        if command.endswith(suffix):
            command = command[: -len(suffix)]
            break
    words = tuple(command.split())
    if not words or any("*" in word for word in words):
        return None
    return words


def _scan_settings(base: Path, result: ScanResult) -> None:
    """``settings.json``. A command Claude Code refuses (``permissions.deny``, ``Bash(…)``)
    becomes one PersonalClaw refuses: a shell-denylist item. Its other rules and its own options
    are counted and named, and no value in them becomes a setting here."""
    path = base / _SETTINGS_FILE
    if not path.is_file():
        return
    if refuses(path):
        result.secrets_skipped += 1
        return
    try:
        settings = _read_json_document(path)
    except FileUnreadable as exc:
        note_unreadable(result, unreadable_file(exc))
        return
    permissions = settings.get("permissions")
    permissions = permissions if isinstance(permissions, dict) else {}
    counted = {"ask": 0, "allow": 0, "wildcard": 0, "other": 0}
    for decision in _RULE_LISTS:
        rules = permissions.get(decision)
        for rule in rules if isinstance(rules, list) else []:
            match = _BASH_RULE_RE.match(rule.strip()) if isinstance(rule, str) else None
            if match is None:
                counted["other"] += 1
                continue
            if decision != "deny":
                counted[decision] += 1
                continue
            words = _refused_command_words(match["command"] or "")
            if words is None:
                counted["wildcard"] += 1
                continue
            denied_command_item(
                NAME,
                result,
                key=f"{_SETTINGS_FILE}:permissions.deny:{rule.strip()}",
                pattern=tuple((word,) for word in words),
            )
    not_imported_rows(
        result,
        [
            (counted["ask"], *RULES_THAT_ASK),
            (counted["allow"], *RULES_THAT_ALLOW),
            (
                counted["wildcard"],
                "Refused commands with wildcards",
                "A refused command comes over as the words it starts with. These rules use a "
                "wildcard instead, so they stay in Claude Code.",
            ),
            (
                counted["other"],
                "Other permission rules",
                "They are about files, websites and tools. This import brings over only the "
                "commands Claude Code refuses.",
            ),
        ],
    )
    names = [str(key) for key in settings if key not in _NOT_OPTIONS]
    names += [f"permissions.{key}" for key in permissions if key not in _RULE_LISTS]
    settings_not_imported(result, DISPLAY_NAME, names)
