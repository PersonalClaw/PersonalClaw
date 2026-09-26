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
``settings.json``                               ``settings`` (review-gated, never live config)
``history.jsonl``                               not imported — the scan counts it and says why
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
    read_json_safely,
    read_text_safely,
    refuses,
    safe_text,
    strip_secrets,
)
from personalclaw.onboarding_import.model import ImportCategory, ImportItem, ScanResult
from personalclaw.onboarding_import.sources.common import (
    TITLE_CHARS,
    McpServer,
    conversation_note,
    display_path,
    markdown_files,
    on_this_machine,
    one_line,
    prompt_history,
    scan_skills,
    slug_name,
    text_item,
)

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
    """A Claude Code JSON file as a dict, values INCLUDED — ``{}`` when absent, refused or not a
    JSON object. For the two readers that must see values (an MCP server's definition, the
    settings a ``.mcp.json`` expands from); everything else goes through the floors."""
    if not path.is_file() or refuses(path):
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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


def settings_env(root: Path | None = None) -> dict[str, str]:
    """The ``env`` block of Claude Code's ``settings.json`` — the variables every session it
    starts has, and so what a project's ``${VAR}`` expands to. Values included; see
    :func:`_read_json_document`."""
    base = root if root is not None else resolve_root()
    env = _read_json_document(base / _SETTINGS_FILE).get("env")
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


def mcp_servers(root: Path | None = None, *, config_path: Path | None = None) -> list[McpServer]:
    """Every MCP server Claude Code has configured, in all three of its scopes.

    THE reader of Claude Code's MCP configuration: the onboarding scan (through the floors) and
    the Tools page's Import both call it, so the two cannot disagree about what Claude Code has.
    ``config_path`` is the global config (:func:`global_config_path` of ``root`` when omitted) and
    ``root`` the config directory whose ``settings.json`` goes with it (:func:`config_dir_of` the
    file when omitted), so a caller naming one file never reads another installation's settings.

    - **User scope** — ``.claude.json`` → ``mcpServers``: every project has them.
    - **Local scope** — ``.claude.json`` → ``projects[path].mcpServers``: yours, in one project.
    - **Project scope** — ``<project>/.mcp.json``: checked into the project, run by Claude Code
      only once approved there, with ``${VAR}`` expanded from the environment — here, from the
      one environment this reader can see, Claude Code's ``settings.json`` ``env``.
    """
    config_path = config_path or global_config_path(root)
    base = root if root is not None else config_dir_of(config_path)
    config = _read_json_document(config_path)
    user_settings = _read_json_document(base / _SETTINGS_FILE)
    env = settings_env(base)
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
    for project in known:
        if project.local is None:
            continue
        mcp_file = project.local / _PROJECT_MCP_FILE
        doc = _read_json_document(mcp_file)
        if not doc:
            continue
        approved, turned_off, every = _project_approvals(project, user_settings)
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
    return out


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


def scan(root: Path | str | None = None) -> ScanResult:
    explicit = Path(root).expanduser() if root is not None else None
    base = explicit if explicit is not None else resolve_root()
    result = ScanResult(
        source=NAME, display_name=DISPLAY_NAME, root=str(base), present=base.is_dir()
    )
    if not result.present:
        return result

    config = _read_json_document(global_config_path(explicit))
    known = projects(config)
    seen_files: set[Path] = set()
    _scan_instructions(base, known, seen_files, result)
    _scan_memories(base, known, result)
    _scan_mcp(base, explicit, result)
    scan_skills(NAME, [(base / _SKILLS_DIR, "")], result)
    _scan_agents(base, result)
    _scan_commands(base, result)
    _scan_conversations(base, known, result)
    _scan_settings(base, result)
    history = prompt_history(base / _HISTORY_FILE)
    if history is not None:
        result.not_imported.append(history)
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
                seen_files=seen_files,
            )


#: One line of ``MEMORY.md``: ``- [Title](file.md) — hook``.
_INDEX_LINE_RE = re.compile(r"^\s*[-*]\s*\[(?P<title>[^\]]+)\]\((?P<file>[^)\s]+\.md)\)")


def _memory_index(index: Path) -> tuple[dict[str, str], bool]:
    """``({file name: title}, is_only_an_index)`` for a ``MEMORY.md``.

    Claude Code's auto-memory keeps one topic per file and ``MEMORY.md`` as the list of them; an
    older layout kept the notes in ``MEMORY.md`` itself. A file of nothing but index lines is the
    index, and the topic files it links are the memories. One with anything else in it is a
    memory of its own, and imported as one.
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
        titles, only_index = _memory_index(index) if index.is_file() else ({}, True)
        for path in sorted(mem_dir.glob("*.md")):
            if not path.is_file() or (path.name == _MEMORY_INDEX and only_index):
                continue
            text, redactions, skipped = read_text_safely(path)
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
    """Every server in every scope (:func:`mcp_servers`), each through floor 2 on its own, so
    each says how many of its own credentials stay behind — the one a user will re-enter."""
    for server in mcp_servers(base, config_path=global_config_path(explicit)):
        clean, withheld = strip_secrets(server.spec)
        result.secrets_skipped += withheld
        user = server.scope == SCOPE_USER
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.MCP_SERVERS,
                key=server.name if user else f"{server.scope}:{server.project}:{server.name}",
                title=server.name,
                name=server.name,
                payload=clean,
                origin=server.origin,
                note=server.note,
                preselect=server.approved,
                secrets_skipped=withheld,
            )
        )


def _scan_agents(base: Path, result: ScanResult) -> None:
    """Subagents: ``agents/*.md``, a ``name``/``description``/``tools``/``model`` frontmatter over
    the agent's instructions. An agent here is a profile on the Agents page."""
    from personalclaw.skills.loader import SkillsLoader, parse_frontmatter

    agents_root = base / _AGENTS_DIR
    for path in markdown_files(agents_root):
        text, redactions, skipped = read_text_safely(path)
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
    each declared, so the prompt asks for them where Claude Code took them from the command line.
    """
    variables: list[dict[str, Any]] = []
    content = body
    if _ARGUMENTS_RE.search(content):
        content = _ARGUMENTS_RE.sub("{{arguments}}", content)
        variables.append(
            {
                "name": "arguments",
                "type": "textarea",
                "description": argument_hint or "What the command was given after its name.",
            }
        )
    for digit in sorted({m.group(1) for m in _POSITIONAL_RE.finditer(content)}):
        content = re.sub(rf"\${digit}(?![0-9])", f"{{{{arg{digit}}}}}", content)
        variables.append({"name": f"arg{digit}", "type": "text", "description": ""})
    return content, variables


def _scan_commands(base: Path, result: ScanResult) -> None:
    """Slash commands: ``commands/**/*.md``. A command is a prompt you run by name."""
    from personalclaw.skills.loader import SkillsLoader, parse_frontmatter

    commands_root = base / _COMMANDS_DIR
    for path in markdown_files(commands_root):
        text, redactions, skipped = read_text_safely(path)
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


def read_conversation(path: Path) -> tuple[dict[str, Any], int] | None:
    """One Claude Code transcript as a PersonalClaw conversation: ``(conversation, redactions)``.

    ``conversation`` is ``{"messages", "title", "created_at", "updated_at", "cwd"}``. Messages are
    what a person reads back: each prompt, each reply, and each tool call by name and what it
    was for. Tool OUTPUT is not carried — it is where a transcript is largest and where a pasted
    or printed credential sits — and neither are a subagent's own turns, Claude Code's notices,
    or the summary it writes after compacting (the conversation it summarises is imported
    whole). Every text passes floor 3. ``None`` for a file with no prompt in it.
    """
    if refuses(path):
        return None
    messages: list[dict[str, Any]] = []
    redactions = 0
    summary = ""
    cwd = ""
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for raw in handle:
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(line, dict):
                continue
            kind = line.get("type")
            if kind == "summary" and isinstance(line.get("summary"), str):
                summary = line["summary"]
                continue
            if kind not in ("user", "assistant"):
                continue
            if line.get("isSidechain") or line.get("isMeta") or line.get("isCompactSummary"):
                continue
            cwd = cwd or str(line.get("cwd") or "")
            ts = str(line.get("timestamp") or "")
            held = line.get("message")
            message: dict[str, Any] = held if isinstance(held, dict) else {}
            content = message.get("content")
            if kind == "user":
                text = _user_text(content)
                if not text.strip():
                    continue
                cleaned, n = safe_text(text)
                redactions += n
                messages.append({"role": "user", "content": cleaned, "ts": ts})
                continue
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    cleaned, n = safe_text(str(block["text"]))
                    redactions += n
                    last = messages[-1] if messages else None
                    if last is not None and last["role"] == "assistant":
                        # One reply per stretch of text: Claude Code writes a line per block.
                        last["content"] = f"{last['content']}\n\n{cleaned}"
                        last["ts"] = ts or last["ts"]
                    else:
                        messages.append({"role": "assistant", "content": cleaned, "ts": ts})
                elif block.get("type") == "tool_use":
                    cleaned, n = safe_text(_tool_line(block))
                    redactions += n
                    messages.append({"role": "tool", "content": cleaned, "ts": ts})
    prompts = [m for m in messages if m["role"] == "user"]
    if not prompts:
        return None
    title = one_line(summary or prompts[0]["content"], TITLE_CHARS)
    stamps = [m["ts"] for m in messages if m["ts"]]
    conversation = {
        "messages": messages,
        "title": safe_text(title)[0],
        "created_at": stamps[0] if stamps else "",
        "updated_at": stamps[-1] if stamps else "",
        "cwd": cwd,
    }
    return conversation, redactions


def _scan_conversations(base: Path, known: list[Project], result: ScanResult) -> None:
    """Every session transcript, ``projects/<cwd>/<session id>.jsonl`` — a conversation each.

    A subagent's own transcript (``agent-*.jsonl``) is part of the conversation that started it,
    not a conversation of its own.
    """
    for project_dir in _project_dirs(base):
        origin = f"Project · {_project_label(project_dir.name, known)}"
        for path in sorted(project_dir.glob("*.jsonl")):
            if not path.is_file() or path.name.startswith("agent-"):
                continue
            read = read_conversation(path)
            if read is None:
                continue
            conversation, redactions = read
            result.redactions += redactions
            result.items.append(
                ImportItem(
                    source=NAME,
                    category=ImportCategory.CONVERSATIONS,
                    key=f"{_PROJECTS_DIR}/{project_dir.name}/{path.name}",
                    title=conversation["title"],
                    name=path.stem,
                    payload=conversation,
                    origin=origin,
                    note=conversation_note(conversation["messages"]),
                    redactions=redactions,
                )
            )


def _scan_settings(base: Path, result: ScanResult) -> None:
    path = base / _SETTINGS_FILE
    if not path.is_file():
        return
    data, skipped = read_json_safely(path)
    result.secrets_skipped += skipped
    if not isinstance(data, dict) or not data:
        return
    result.items.append(
        ImportItem(
            source=NAME,
            category=ImportCategory.SETTINGS,
            key=_SETTINGS_FILE,
            title=f"{DISPLAY_NAME} settings",
            payload=data,
            secrets_skipped=skipped,
        )
    )
