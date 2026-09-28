"""Codex scanner — ``$CODEX_HOME`` (default ``~/.codex``), and the skills Codex reads beside it.

The same contract as the Claude Code scanner: :func:`scan` opens files, applies the floors and
returns a :class:`~..model.ScanResult`, and it never writes to anything it reads. What it maps —
the layout the Codex CLI writes, and nothing it does not:

=============================================  ===============================================
``AGENTS.override.md``, ``AGENTS.md``          ``instructions`` (Codex reads the first of the
                                               two that has text)
``memories/*.md``                              ``memories`` (what Codex's memory built)
``config.toml`` → ``[mcp_servers.<name>]``     ``mcp_servers``
``skills/<name>/``, ``~/.agents/skills/``,     ``skills``
``memories/skills/``
``agents/*.toml``                              ``agents``
``prompts/*.md``                               ``prompts``
``rules/*.rules`` → ``forbidden`` rules        ``denied_commands``
``sessions/YYYY/MM/DD/rollout-*.jsonl``, or    ``conversations``, titled from
the ``.jsonl.zst`` Codex compressed it to      ``session_index.jsonl``
=============================================  ===============================================

Counted and named, not imported: Codex's own settings (the rest of ``config.toml``, or the older
CLI's ``config.json``), prompt history, rules that ask first or allow, archived conversations, and
the files Codex builds its memories from. A session file this import cannot read (cut off,
damaged, or larger than :data:`MAX_SESSION_BYTES`) is named with the reason. ``auth.json`` (the
Codex login, when it is kept in a file) is never opened; it counts as a withheld credential file.

**A remote server's token.** Codex never keeps it in its config: it reads it from an environment
variable when it starts (``bearer_token_env_var``, ``env_http_headers``). :func:`mcp_servers` reads
that variable from the environment PersonalClaw runs in, so an import can store it; when that
environment does not set it, the server's note says so.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import tomllib
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, TextIO

import zstandard

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
    NotImported,
    ScanResult,
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
    Unreadable,
    and_list,
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
    one_line,
    prompt_history,
    recorded_label,
    scan_skills,
    settings_not_imported,
    slug_name,
    text_item,
    unreadable_conversations,
    unreadable_file,
)

NAME = "codex"
DISPLAY_NAME = "Codex"
ENV_VAR = "CODEX_HOME"
DEFAULT_ROOT = "~/.codex"

#: Codex's own instructions, in the order it reads them: the first with text is the one in use.
_INSTRUCTION_FILES = ("AGENTS.override.md", "AGENTS.md")
_TOML_CONFIG = "config.toml"
#: The older (TypeScript) Codex CLI's config. It held no MCP servers, so it is settings.
_JSON_CONFIG = "config.json"
_MCP_TABLE = "mcp_servers"
_MEMORIES_DIR = "memories"
#: What Codex's memory keeps beside the memories it builds: its inputs for the next build.
_MEMORY_WORKING_FILES = ("raw_memories.md", "phase2_workspace_diff.md")
_MEMORY_WORKING_DIRS = ("rollout_summaries",)
_SKILLS_DIR = "skills"
_AGENTS_DIR = "agents"
_PROMPTS_DIR = "prompts"
_RULES_DIR = "rules"
_SESSIONS_DIR = "sessions"
_ARCHIVED_SESSIONS_DIR = "archived_sessions"
_SESSION_INDEX = "session_index.jsonl"
_HISTORY_FILE = "history.jsonl"


def resolve_root() -> Path:
    """``$CODEX_HOME`` when it is set, ``~/.codex`` otherwise — Codex's own rule."""
    env = os.environ.get(ENV_VAR, "").strip()
    return Path(env).expanduser() if env else Path.home() / ".codex"


def _read_config(base: Path) -> tuple[dict[str, Any], str, int]:
    """``(document, file name, withheld)``: Codex's config WITH its values (``{}`` when there is
    none), the file it came from, and 1 when that file was refused unread.

    Values included, for the one reader that must see them: an MCP server's, which the MCP
    writer keeps in the credential store. The settings reader reads only the names.

    A config that is there and cannot be read raises :class:`FileUnreadable`. Repairing another
    tool's half-written file is not our job, but reading it as an empty one said "nothing to
    import" about a Codex whose servers and settings were simply unknown.
    """
    for name in (_TOML_CONFIG, _JSON_CONFIG):
        path = base / name
        if not path.is_file():
            continue
        if refuses(path):
            return {}, name, 1
        kind = "TOML" if name == _TOML_CONFIG else "JSON"
        try:
            if name == _TOML_CONFIG:
                with path.open("rb") as handle:
                    parsed: Any = tomllib.load(handle)
            else:
                parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, *UNPARSABLE) as exc:
            raise FileUnreadable(path, why_unreadable(exc, kind=kind)) from exc
        if not isinstance(parsed, dict):  # only JSON: a TOML document is always a table
            raise FileUnreadable(path, "it is not a JSON object")
        return parsed, name, 0
    return {}, "", 0


def _read_toml(path: Path) -> dict[str, Any]:
    """One TOML file as a table. Raises :class:`FileUnreadable` for one that will not open or
    does not parse, so a broken agent file is named rather than skipped without a word."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, *UNPARSABLE) as exc:
        raise FileUnreadable(path, why_unreadable(exc, kind="TOML")) from exc


def _settings_left(keys: list[str], *, owner: str, of: str = "") -> str:
    """``"<owner> x and y settings<of> are not carried over."``, agreeing in number."""
    many = len(keys) > 1
    return (
        f"{owner} {and_list(keys)} setting{'s' if many else ''}{of} "
        f"{'are' if many else 'is'} not carried over."
    )


# ── MCP servers: the one reader of Codex's MCP configuration ───────────────────

#: The keys of a Codex server entry this import carries. Any other is named in its note.
_CARRIED_KEYS = frozenset(
    {
        "command",
        "args",
        "env",
        "env_vars",
        "cwd",
        "url",
        "bearer_token_env_var",
        "http_headers",
        "env_http_headers",
        "enabled",
        "disabled_tools",
    }
)

#: PersonalClaw's own variables. The gateway's secrets live under this prefix, and no other
#: tool's server was ever given one, so a server that names one is given nothing.
_OWN_VARIABLE_PREFIX = "PERSONALCLAW_"


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if isinstance(v, (str, int, float))}


def _variable_names(value: Any) -> list[str]:
    """``env_vars``: names, or ``{name, source}`` tables."""
    if not isinstance(value, list):
        return []
    names = [v.get("name") if isinstance(v, dict) else v for v in value]
    return [n.strip() for n in names if isinstance(n, str) and n.strip()]


@dataclass
class _Variables:
    """The environment variables one server needs, sorted by what the environment answered."""

    environ: Mapping[str, str]
    unset: list[str] = field(default_factory=list)
    own: list[str] = field(default_factory=list)

    def read(self, name: str) -> str:
        """The value Codex would read for ``name`` — from the environment PersonalClaw runs in,
        and never one of PersonalClaw's own variables."""
        if name.upper().startswith(_OWN_VARIABLE_PREFIX):
            self.own.append(name)
            return ""
        value = str(self.environ.get(name) or "")
        if not value:
            self.unset.append(name)
        return value


def _mcp_server(name: str, entry: dict[str, Any], environ: Mapping[str, str]) -> McpServer:
    """One ``[mcp_servers.<name>]`` table in PersonalClaw's form, and what to know about it."""
    spec: dict[str, Any] = {}
    variables = _Variables(environ)
    #: The bearer token's variable, when the environment does not set it.
    token_unset = ""
    url = entry.get("url")
    if isinstance(url, str) and url.strip() and not entry.get("command"):
        # Codex speaks one remote transport, Streamable HTTP. A bare URL means SSE to PersonalClaw
        # (`mcp_discovery.mcp_transport`), so the transport is said, not left to that default.
        spec["type"] = "http"
        spec["url"] = url.strip()
        headers = _string_map(entry.get("http_headers"))
        for header, variable in _string_map(entry.get("env_http_headers")).items():
            value = variables.read(variable)
            if value:
                headers[header] = value
        token_variable = entry.get("bearer_token_env_var")
        if isinstance(token_variable, str) and token_variable.strip():
            token = token_variable.strip()
            if token.upper().startswith(_OWN_VARIABLE_PREFIX):
                variables.own.append(token)
            elif environ.get(token):
                headers["Authorization"] = f"Bearer {environ[token]}"
            else:
                token_unset = token
        if headers:
            spec["headers"] = headers
    else:
        command = entry.get("command")
        if isinstance(command, str):
            spec["command"] = command
        args = entry.get("args")
        if isinstance(args, list):
            spec["args"] = [str(a) for a in args]
        env = _string_map(entry.get("env"))
        if env:
            spec["env"] = env
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            spec["cwd"] = cwd
        # Codex hands these to the server from its own environment. PersonalClaw starts a
        # server in the whole of its environment (`mcp_discovery.stdio_spawn_env`), so each is
        # there when that environment sets it; the ones it does not set are said.
        for variable in _variable_names(entry.get("env_vars")):
            variables.read(variable)
    notes: list[str] = []
    if entry.get("enabled") is False:
        spec["disabled"] = True
        notes.append("It is turned off in Codex.")
    disabled_tools = entry.get("disabled_tools")
    if isinstance(disabled_tools, list):
        tools = [t for t in disabled_tools if isinstance(t, str) and t]
        if tools:
            spec["disabledTools"] = tools
    if token_unset:
        notes.append(
            f"Its token comes from ${token_unset}, which the environment PersonalClaw runs in "
            "does not set: add it as an Authorization header on the Tools page after importing."
        )
    if variables.unset:
        one = len(variables.unset) == 1
        notes.append(
            f"It needs {and_list(['$' + v for v in variables.unset])}, which the environment "
            f"PersonalClaw runs in does not set: add {'it' if one else 'them'} on the Tools page "
            "after importing."
        )
    if variables.own:
        notes.append(
            f"It names {and_list(['$' + v for v in variables.own])}, which "
            f"{'is' if len(variables.own) == 1 else 'are'} PersonalClaw's own and never handed "
            "to another tool's server."
        )
    dropped = sorted(str(k) for k in entry if str(k) not in _CARRIED_KEYS)
    if dropped:
        notes.append(_settings_left(dropped, owner="Codex's", of=" for it"))
    return McpServer(
        source=NAME,
        name=name,
        scope="user",
        project="",
        spec=spec,
        origin="",
        note=" ".join(notes),
    )


def _servers_from(config: dict[str, Any], environ: Mapping[str, str]) -> list[McpServer]:
    table = config.get(_MCP_TABLE)
    if not isinstance(table, dict):
        return []
    return [
        _mcp_server(str(name), entry, environ)
        for name, entry in sorted(table.items(), key=lambda pair: str(pair[0]))
        if isinstance(entry, dict) and str(name).strip()
    ]


def mcp_servers(
    root: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> McpListing:
    """Every MCP server Codex has configured (``config.toml`` → ``[mcp_servers.<name>]``), in
    PersonalClaw's form, and the config itself when it is there and could not be read.

    THE reader of Codex's MCP configuration: the onboarding scan and the Tools page's Import both
    call it and both write what it returns through the one MCP writer, so the two cannot disagree
    about what Codex has or where its values go. Codex's meaning is kept:

    - a server with a ``url`` speaks Streamable HTTP, Codex's one remote transport, so it is
      ``type: "http"``;
    - ``http_headers`` are its headers, and ``bearer_token_env_var`` and ``env_http_headers``
      name variables Codex reads when it starts, read here from ``environ`` (default: the
      environment PersonalClaw runs in), and never one of PersonalClaw's own;
    - ``enabled = false`` is ``disabled``, and ``disabled_tools`` is ``disabledTools``.

    Everything else a server holds is named in its note as not carried over. A config that
    cannot be read lists no server, and is named in the listing's ``unreadable``.
    """
    base = root if root is not None else resolve_root()
    try:
        config, name, _withheld = _read_config(base)
    except FileUnreadable as exc:
        return McpListing(servers=[], unreadable=[unreadable_file(exc)])
    if name != _TOML_CONFIG:
        return McpListing(servers=[], unreadable=[])
    environment = os.environ if environ is None else environ
    return McpListing(servers=_servers_from(config, environment), unreadable=[])


# ── the scan ──────────────────────────────────────────────────────────────────


def scan(root: Path | str | None = None, *, look: bool = False) -> ScanResult:
    """What Codex holds. To ``look`` is to read each session not read before only as far as its
    first typed prompt (:func:`_scan_conversations`); otherwise every one is read in full."""
    explicit = Path(root).expanduser() if root is not None else None
    base = explicit if explicit is not None else resolve_root()
    result = ScanResult(
        source=NAME, display_name=DISPLAY_NAME, root=str(base), present=base.is_dir()
    )
    if not result.present:
        return result

    with one_walk():
        try:
            config, config_name, withheld = _read_config(base)
        except FileUnreadable as exc:
            # Named; its servers and settings are unknown, so neither is listed or counted.
            note_unreadable(result, unreadable_file(exc))
            config, config_name, withheld = {}, "", 0
        _scan_instructions(base, result)
        _scan_memories(base, result)
        if config_name == _TOML_CONFIG:
            _scan_mcp(_servers_from(config, os.environ), result)
        scan_skills(NAME, _skill_roots(base, explicit), result)
        _scan_agents(base, result)
        _scan_prompts(base, result)
        _scan_rules(base, result)
        _scan_conversations(base, result, look=look)
        _scan_settings(config, config_name, withheld, result)
        prompt_history(base / _HISTORY_FILE, result)
        _count_withheld_files(base, result)
    result.note_withheld()
    return result


def _count_withheld_files(base: Path, result: ScanResult) -> None:
    """Count the credential FILES at the root that are deliberately never opened.

    ``auth.json`` is the Codex login when Codex keeps it in a file (its tokens, or an API key),
    and ``.credentials.json`` the MCP logins. Nothing maps them, so nothing would read them; the
    count is how the user learns they were there and left alone. Files a category already
    accounted for are excluded so nothing is counted twice.
    """
    visited = {*_INSTRUCTION_FILES, _TOML_CONFIG, _JSON_CONFIG, _HISTORY_FILE, _SESSION_INDEX}
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name not in visited and refuses(path):
            result.secrets_skipped += 1


def _scan_instructions(base: Path, result: ScanResult) -> None:
    """``AGENTS.override.md`` and ``AGENTS.md``. Codex reads the first of the two with text in
    it, so when both have text the other one is offered unticked, with the reason."""
    in_use = ""
    for file_name in _INSTRUCTION_FILES:
        path = base / file_name
        if not path.is_file():
            continue
        added = text_item(
            NAME,
            path,
            result,
            category=ImportCategory.INSTRUCTIONS,
            key=file_name,
            title=file_name,
            note=f"Codex reads your {in_use} instead of this file." if in_use else "",
            preselect=not in_use,
        )
        if added and not in_use:
            in_use = file_name


def _scan_memories(base: Path, result: ScanResult) -> None:
    """What Codex's memory built: the notes at the top of ``memories/``.

    Codex keeps its build inputs beside them — the raw notes it took from each conversation and
    a summary per conversation — and those are counted, not imported.
    """
    root = base / _MEMORIES_DIR
    for path in markdown_files(root, recursive=False):
        if path.name in _MEMORY_WORKING_FILES:
            continue
        text_item(
            NAME,
            path,
            result,
            category=ImportCategory.MEMORIES,
            key=f"{_MEMORIES_DIR}/{path.name}",
            title=path.name,
        )
    working = sum(1 for name in _MEMORY_WORKING_FILES if (root / name).is_file())
    for name in _MEMORY_WORKING_DIRS:
        folder = root / name
        if folder.is_dir():
            working += sum(1 for p in folder.rglob("*") if p.is_file())
    if working:
        result.not_imported.append(
            NotImported(
                what="Memory working files",
                count=working,
                why=(
                    "Codex builds its memories from them. The memories it built come over, and "
                    "so do your conversations."
                ),
            )
        )


def _scan_mcp(servers: list[McpServer], result: ScanResult) -> None:
    """Every server, definition whole: the MCP writer keeps each ``env`` and ``headers`` value in
    the credential store, as Tools › Import does."""
    for server in servers:
        result.items.append(mcp_item(NAME, server, key=server.name))


def _skill_roots(base: Path, explicit: Path | None) -> list[tuple[Path, str]]:
    """Where Codex reads your skills, in its order: ``$CODEX_HOME/skills``, then
    ``~/.agents/skills``, then the skills its memory wrote.

    ``~/.agents/skills`` is your home's, not the Codex home's, so it is read for this machine's
    Codex and not for a root named explicitly (a copied or fixture Codex home).
    """
    roots = [(base / _SKILLS_DIR, "")]
    if explicit is None:
        agents_skills = Path.home() / ".agents" / _SKILLS_DIR
        roots.append((agents_skills, display_path(agents_skills)))
    roots.append((base / _MEMORIES_DIR / _SKILLS_DIR, "Codex memories"))
    return roots


#: The keys of an agent file this import carries. Any other is named in the agent's note.
_AGENT_KEYS = frozenset({"name", "description", "developer_instructions"})


def _scan_agents(base: Path, result: ScanResult) -> None:
    """Custom agents: ``agents/*.toml``, each a ``name``, a ``description`` and the agent's
    ``developer_instructions`` — a profile on the Agents page here."""
    root = base / _AGENTS_DIR
    if not root.is_dir():
        return
    for path in sorted(p for p in root.glob("*.toml") if p.is_file()):
        if refuses(path):
            result.secrets_skipped += 1
            continue
        try:
            doc = _read_toml(path)
        except FileUnreadable as exc:
            note_unreadable(result, unreadable_file(exc))
            continue
        instructions = doc.get("developer_instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            continue
        # The name field names the agent, not the file ("the name field is the source of truth").
        declared = str(doc.get("name") or "").strip() or path.stem
        text, redactions = safe_text(instructions.strip() + "\n")
        description, more = safe_text(str(doc.get("description") or "").strip())
        dropped = sorted(str(k) for k in doc if str(k) not in _AGENT_KEYS)
        result.redactions += redactions + more
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.AGENTS,
                key=f"{_AGENTS_DIR}/{path.name}",
                title=declared,
                name=slug_name(declared, lower=True),
                text=text,
                payload={"description": description},
                note=_settings_left(dropped, owner="Its Codex") if dropped else "",
                redactions=redactions + more,
            )
        )


# ── prompts ───────────────────────────────────────────────────────────────────

#: A named placeholder, as Codex's custom prompts wrote one: ``$`` and an upper-case name, not
#: after another ``$`` (``$$NAME`` is literal).
_NAMED_PLACEHOLDER_RE = re.compile(r"(?<!\$)\$([A-Z][A-Z0-9_]*)")
#: ``NAME=<what it is>`` in an ``argument-hint``.
_HINT_RE = re.compile(r"\b([A-Z][A-Z0-9_]*)=(\S+)")
_ARGUMENTS = "ARGUMENTS"


def prompt_template(body: str, *, argument_hint: str) -> tuple[str, list[dict[str, Any]]]:
    """A Codex custom prompt as a PersonalClaw prompt: ``(content, variables)``.

    Codex's rule, kept: a prompt with any named placeholder (``$ISSUE``) takes named values, each
    a ``{{issue}}`` variable here, all required; one with none takes positional values —
    ``$1``…``$9`` become ``{{arg1}}``… and ``$ARGUMENTS`` all of them, as ``{{arguments}}``. A
    ``$$`` stays as Codex left it.
    """
    names = [m.group(1) for m in _NAMED_PLACEHOLDER_RE.finditer(body) if m.group(1) != _ARGUMENTS]
    if names:
        hints = {key: value.strip("<>") for key, value in _HINT_RE.findall(argument_hint)}

        def _named(match: re.Match[str]) -> str:
            word = match.group(1)
            return match.group(0) if word == _ARGUMENTS else f"{{{{{word.lower()}}}}}"

        variables = [
            {"name": n.lower(), "type": "text", "description": hints.get(n, ""), "required": True}
            for n in dict.fromkeys(names)
        ]
        return _NAMED_PLACEHOLDER_RE.sub(_named, body), variables
    return _positional_template(body, argument_hint)


def _positional_template(body: str, argument_hint: str) -> tuple[str, list[dict[str, Any]]]:
    """``$1``…``$9`` and ``$ARGUMENTS``, read the way Codex's expansion read them."""
    out: list[str] = []
    digits: set[str] = set()
    everything = False
    i = 0
    while (j := body.find("$", i)) >= 0:
        out.append(body[i:j])
        after = body[j + 1 : j + 2]
        if after == "$":
            out.append("$$")
            i = j + 2
        elif after and after in "123456789":
            out.append(f"{{{{arg{after}}}}}")
            digits.add(after)
            i = j + 2
        elif body.startswith(_ARGUMENTS, j + 1):
            out.append("{{arguments}}")
            everything = True
            i = j + 1 + len(_ARGUMENTS)
        else:
            out.append("$")
            i = j + 1
    out.append(body[i:])
    variables: list[dict[str, Any]] = []
    if everything:
        variables.append(
            {
                "name": "arguments",
                "type": "textarea",
                "description": argument_hint or "What the prompt was given after its name.",
            }
        )
    variables.extend({"name": f"arg{d}", "type": "text", "description": ""} for d in sorted(digits))
    return "".join(out), variables


def _scan_prompts(base: Path, result: ScanResult) -> None:
    """Custom prompts: ``prompts/*.md``, which Codex ran as ``/prompts:<name>``."""
    from personalclaw.skills.loader import SkillsLoader, parse_frontmatter

    root = base / _PROMPTS_DIR
    for path in markdown_files(root, recursive=False):
        try:
            text, redactions, skipped = read_text_safely(path)
        except FileUnreadable as exc:
            note_unreadable(result, unreadable_file(exc))
            continue
        result.secrets_skipped += skipped
        if not text.strip():
            continue
        meta = parse_frontmatter(text)
        content, variables = prompt_template(
            SkillsLoader.strip_frontmatter(text), argument_hint=meta.get("argument-hint", "")
        )
        result.redactions += redactions
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.PROMPTS,
                key=f"{_PROMPTS_DIR}/{path.name}",
                title=f"/prompts:{path.stem}",
                name=slug_name(path.stem, lower=False),
                text=content,
                payload={
                    "description": meta.get("description", "").strip(),
                    "variables": variables,
                },
                redactions=redactions,
            )
        )


# ── rules ─────────────────────────────────────────────────────────────────────

_DECISIONS = ("allow", "prompt", "forbidden")


@dataclass(frozen=True)
class CommandRule:
    """One ``prefix_rule(...)``: the command prefix it matches, what Codex does, and why."""

    #: Each position of the prefix, as the words Codex accepts there.
    pattern: tuple[tuple[str, ...], ...]
    decision: str
    justification: str


def _prefix(value: Any) -> tuple[tuple[str, ...], ...] | None:
    """A ``pattern`` argument: a non-empty list whose items are words or non-empty word lists."""
    if not isinstance(value, list) or not value:
        return None
    positions: list[tuple[str, ...]] = []
    for element in value:
        alternatives = element if isinstance(element, list) else [element]
        if not alternatives or not all(isinstance(w, str) and w for w in alternatives):
            return None
        positions.append(tuple(alternatives))
    return tuple(positions)


def parse_rules(source: str) -> tuple[list[CommandRule], int]:
    """The ``prefix_rule(...)`` calls in a Codex rules file, read as data and never run:
    ``(rules, unreadable)``.

    A rules file is Starlark. A top-level ``prefix_rule`` call whose arguments are plain lists
    and strings is read; one built any other way (from a variable, a function) is counted as
    unreadable, and so is a file that does not parse at all. ``decision`` defaults to
    ``allow``, as it does for Codex.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return [], 1
    rules: list[CommandRule] = []
    unreadable = 0
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) else None
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "prefix_rule"
        ):
            continue
        try:
            given = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
            if call.args:
                given.setdefault("pattern", ast.literal_eval(call.args[0]))
        except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
            unreadable += 1
            continue
        pattern = _prefix(given.get("pattern"))
        decision = given.get("decision", "allow")
        if pattern is None or decision not in _DECISIONS:
            unreadable += 1
            continue
        justification = given.get("justification")
        rules.append(
            CommandRule(
                pattern=pattern,
                decision=decision,
                justification=justification.strip() if isinstance(justification, str) else "",
            )
        )
    return rules, unreadable


def _scan_rules(base: Path, result: ScanResult) -> None:
    """``rules/*.rules``. A ``forbidden`` rule is a command Codex refuses to run, and becomes one
    PersonalClaw refuses: a shell-denylist pattern. PersonalClaw has no rule that asks before one
    command, and an import never lets a command run unasked, so the other two are counted."""
    root = base / _RULES_DIR
    if not root.is_dir():
        return
    asks = allows = unreadable = 0
    for path in sorted(p for p in root.glob("*.rules") if p.is_file()):
        if refuses(path):
            result.secrets_skipped += 1
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rules, unread = parse_rules(source)
        unreadable += unread
        for rule in rules:
            if rule.decision == "prompt":
                asks += 1
            elif rule.decision == "allow":
                allows += 1
            else:
                denied_command_item(
                    NAME,
                    result,
                    key=f"{_RULES_DIR}/{path.name}:{json.dumps(rule.pattern)}",
                    pattern=rule.pattern,
                    note=f"Codex's reason: {rule.justification}" if rule.justification else "",
                )
    not_imported_rows(
        result,
        [
            (asks, *RULES_THAT_ASK),
            (allows, *RULES_THAT_ALLOW),
            (
                unreadable,
                "Command rules",
                "They are not written as plain lists and strings, so this import cannot read them.",
            ),
        ],
    )


# ── conversations ─────────────────────────────────────────────────────────────

#: What Codex puts around a prompt in the model's input: its context, not something you typed.
_CONTEXT_PREFIXES = ("<environment_context>", "<user_instructions>", "# AGENTS.md instructions")
#: Call arguments that say what a call did, in the order they are preferred.
_CALL_SUMMARY_FIELDS = ("cmd", "command", "query", "path", "file_path", "url", "pattern")
_CALL_SUMMARY_CHARS = 160
_PATCH_OPS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ")
_SESSION_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$"
)
#: A session file, and the name Codex gives it when it compresses it: with its rollout compression
#: turned on, Codex compresses a session nobody has touched for a week.
_SESSION_SUFFIX = ".jsonl"
_COMPRESSED_SUFFIX = ".jsonl.zst"
#: The most of one session this import reads: a plain file's size, or what a compressed one
#: decompresses to. One rule for both, so a session Codex compresses after an import does not
#: change what the next import does with it. It is also the most one line of a session can hold
#: while it is read. A session past it is named as too large, not read.
MAX_SESSION_BYTES = 500_000_000
#: How much of a compressed file is decoded at a time. A zstd block of 128 KiB can be written in
#: 4 bytes, so one step never yields more than 32 MiB, whatever the file holds.
_COMPRESSED_STEP = 1024


_CUT_OFF = "is cut off before the end of its compressed data"
_DAMAGED = "does not decompress: its data is damaged, or is not zstd"


def _too_large(*, compressed: bool) -> str:
    size = f"{MAX_SESSION_BYTES / 1_000_000:g} MB"
    what = f"decompresses to more than {size}" if compressed else f"is larger than {size}"
    return f"{what}, the most this import reads for one conversation"


def _plain_name(name: str) -> str:
    """A session file's name as Codex names it before it compresses it."""
    return name.removesuffix(".zst") if name.endswith(_COMPRESSED_SUFFIX) else name


class _Decompressed(io.RawIOBase):
    """A ``.jsonl.zst`` session read as the JSONL it holds: each zstd frame in the file in turn,
    as Codex reads one, and never more than :data:`MAX_SESSION_BYTES` of it.

    The file is decoded :data:`_COMPRESSED_STEP` bytes at a time, so a small file that expands
    to a great deal is refused at the limit instead of filling memory first. It raises
    :class:`SessionUnreadable` for a file that ends inside a frame or holds none (cut off) and
    for one past the limit, and ``zstandard.ZstdError`` for one the decoder refuses (damaged).
    """

    def __init__(self, handle: BinaryIO) -> None:
        super().__init__()
        self._handle = handle
        self._context = zstandard.ZstdDecompressor()
        self._frame = self._next_frame()
        #: Whether the frame being decoded has been given any of the file yet.
        self._started = False
        self._frames = 0
        #: What the last step read past the end of a frame: the start of the next one.
        self._pending = b""
        self._out = memoryview(b"")
        self._decoded = 0

    def _next_frame(self) -> Any:
        # Explicitly one frame per decoder: zstandard means to change the default.
        return self._context.decompressobj(read_across_frames=False)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not self._out:
            if not self._step():
                return 0
        count = min(len(buffer), len(self._out))
        buffer[:count] = self._out[:count]
        self._out = self._out[count:]
        return count

    def _step(self) -> bool:
        """Decode the next piece of the file: False at its end."""
        piece = self._pending or self._handle.read(_COMPRESSED_STEP)
        self._pending = b""
        if not piece:
            if self._started or not self._frames:
                raise SessionUnreadable(_CUT_OFF)
            return False
        self._started = True
        out = self._frame.decompress(piece)
        if self._frame.eof:
            self._frames += 1
            self._pending = self._frame.unused_data
            self._frame = self._next_frame()
            self._started = False
        self._decoded += len(out)
        if self._decoded > MAX_SESSION_BYTES:
            raise SessionUnreadable(_too_large(compressed=True))
        self._out = memoryview(out)
        return True

    def close(self) -> None:
        self._handle.close()
        super().close()


def _open_session(path: Path) -> TextIO:
    """A session file as the lines of JSON it holds, read the same way whether or not Codex has
    compressed it: as UTF-8 with a bad byte replaced, and no more of it than
    :data:`MAX_SESSION_BYTES`."""
    if not path.name.endswith(_COMPRESSED_SUFFIX):
        if path.stat().st_size > MAX_SESSION_BYTES:
            raise SessionUnreadable(_too_large(compressed=False))
        return path.open(encoding="utf-8", errors="replace")
    raw = _Decompressed(path.open("rb"))
    return io.TextIOWrapper(io.BufferedReader(raw), encoding="utf-8", errors="replace")


def _session_titles(base: Path) -> dict[str, str]:
    """``{session id: thread name}`` from ``session_index.jsonl`` — the names Codex lists
    conversations by. A later line for an id is a rename, so it wins.

    A line that does not parse is passed over: Codex appends to this file, and the one it is
    writing now is not damage. An index that is there and will not open raises
    :class:`FileUnreadable`: the conversations still come over, under their first prompts, and
    the scan says which file their names were in."""
    path = base / _SESSION_INDEX
    titles: dict[str, str] = {}
    if not path.is_file() or refuses(path):
        return titles
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    entry = json.loads(raw)
                except UNPARSABLE:
                    continue
                if isinstance(entry, dict):
                    session, name = entry.get("id"), entry.get("thread_name")
                    if isinstance(session, str) and isinstance(name, str) and name.strip():
                        titles[session] = name
    except OSError as exc:
        raise FileUnreadable(path, why_unreadable(exc, kind="text")) from exc
    return titles


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") in ("input_text", "output_text", "text")
    ]
    return "\n\n".join(p for p in parts if p.strip())


def _command_text(value: Any) -> str:
    """A command as a person reads it: a shell script, not ``bash -lc`` around it."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return ""
    if len(value) >= 3 and value[0].rsplit("/", 1)[-1].endswith("sh") and value[1] in ("-lc", "-c"):
        return value[2]
    return " ".join(value)


def _call_line(payload: dict[str, Any]) -> str:
    """One tool call as a line: its name and what it was for — never its output."""
    kind = payload.get("type")
    name = str(payload.get("name") or "")
    summary = ""
    if kind == "function_call":
        try:
            params = json.loads(str(payload.get("arguments") or "{}"))
        except UNPARSABLE:
            params = {}
        if isinstance(params, dict):
            for field_name in _CALL_SUMMARY_FIELDS:
                summary = _command_text(params.get(field_name))
                if summary.strip():
                    break
    elif kind == "custom_tool_call":
        text = str(payload.get("input") or "")
        ops = [line[4:] for line in text.splitlines() if line.startswith(_PATCH_OPS)]
        summary = (
            "; ".join(ops) if ops else next((ln for ln in text.splitlines() if ln.strip()), "")
        )
    elif kind in ("local_shell_call", "web_search_call"):
        held = payload.get("action")
        action: dict[str, Any] = held if isinstance(held, dict) else {}
        name = "shell" if kind == "local_shell_call" else "web_search"
        summary = _command_text(action.get("command")) or str(action.get("query") or "")
    else:
        return ""
    name = name or "tool"
    return f"{name}: {one_line(summary, _CALL_SUMMARY_CHARS)}" if summary.strip() else name


def _session_lines(path: Path) -> Generator[str, None, None]:
    """Every line of a session file, compressed or not (:func:`_open_session`). Raises
    :class:`SessionUnreadable` when the file cannot be read to its end."""
    try:
        with _open_session(path) as handle:
            yield from handle
    except OSError as exc:
        raise SessionUnreadable(not_read(exc)) from None
    except zstandard.ZstdError:
        raise SessionUnreadable(_DAMAGED) from None


class _Rollout:
    """A Codex session file, line by line: the ONE reading of its lines, whether it is read in full
    (:func:`read_rollout`) or only as far as its first typed prompt (:func:`_reading`).

    The prompts are the ``user_message`` events — what the person typed. The model's input holds
    each one too, with Codex's context around it, so it is read only from a file that has no such
    events (:meth:`kept`).
    """

    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}
        self.messages: list[dict[str, Any]] = []
        self.typed = False
        self.redactions = 0
        #: The first prompt the person typed, once a ``user_message`` event has held one.
        self.typed_prompt = ""

    def feed(self, raw: str) -> None:
        try:
            line = json.loads(raw)
        except UNPARSABLE:
            return
        if not isinstance(line, dict):
            return
        kind = line.get("type")
        held = line.get("payload")
        payload: dict[str, Any] = held if isinstance(held, dict) else {}
        ts = str(line.get("timestamp") or "")
        if kind == "session_meta":
            self.meta = self.meta or payload
            return
        if kind == "event_msg":
            if payload.get("type") == "user_message":
                self.typed = True
                text = str(payload.get("message") or "")
                if text.strip():
                    cleaned, n = safe_text(text)
                    self.redactions += n
                    self.messages.append({"role": "user", "content": cleaned, "ts": ts})
                    self.typed_prompt = self.typed_prompt or cleaned
            return
        if kind != "response_item":
            return
        if payload.get("type") == "message":
            role = payload.get("role")
            text = _content_text(payload.get("content"))
            if not text.strip():
                return
            cleaned, n = safe_text(text)
            if role == "assistant":
                self.redactions += n
                last = self.messages[-1] if self.messages else None
                if last is not None and last["role"] == "assistant":
                    last["content"] = f"{last['content']}\n\n{cleaned}"
                    last["ts"] = ts or last["ts"]
                else:
                    self.messages.append({"role": "assistant", "content": cleaned, "ts": ts})
            elif role == "user" and not text.lstrip().startswith(_CONTEXT_PREFIXES):
                self.messages.append(
                    {"role": "user", "content": cleaned, "ts": ts, "input": True, "n": n}
                )
            return
        call = _call_line(payload)
        if call:
            cleaned, n = safe_text(call)
            self.redactions += n
            self.messages.append({"role": "tool", "content": cleaned, "ts": ts})

    def kept(self) -> tuple[list[dict[str, Any]], int]:
        """``(messages, redactions)`` of what comes over: a prompt read from the model's input only
        when the file holds none the person typed."""
        kept: list[dict[str, Any]] = []
        redactions = self.redactions
        for message in self.messages:
            if message.get("input"):
                if self.typed:
                    continue
                redactions += message["n"]
                message = {k: v for k, v in message.items() if k not in ("input", "n")}
            kept.append(message)
        return kept, redactions

    def session(self, path: Path) -> str:
        plain = Path(_plain_name(path.name))
        found = _SESSION_ID_RE.search(plain.name)
        return str(self.meta.get("id") or "") or (found.group(1) if found else plain.stem)

    def conversation(
        self, path: Path, titles: Mapping[str, str]
    ) -> tuple[dict[str, Any], str, int] | None:
        """The whole file's conversation, or ``None`` when it holds no prompt."""
        kept, redactions = self.kept()
        prompts = [m for m in kept if m["role"] == "user"]
        if not prompts:
            return None
        session = self.session(path)
        title, _n = safe_text(one_line(titles.get(session) or prompts[0]["content"], TITLE_CHARS))
        stamps = [m["ts"] for m in kept if m["ts"]]
        conversation = {
            "messages": kept,
            "title": title,
            "created_at": str(self.meta.get("timestamp") or "") or (stamps[0] if stamps else ""),
            "updated_at": stamps[-1] if stamps else "",
            "cwd": str(self.meta.get("cwd") or ""),
        }
        return conversation, session, redactions

    def transcript(self, path: Path, *, whole: bool) -> Transcript | None:
        """What the step lists of the file — ``None`` when what was read holds no prompt."""
        cwd = str(self.meta.get("cwd") or "")
        if whole:
            kept, redactions = self.kept()
            prompts = [m for m in kept if m["role"] == "user"]
            if not prompts:
                return None
            return Transcript(
                title=one_line(prompts[0]["content"], TITLE_CHARS),
                session=self.session(path),
                cwd=cwd,
                messages=message_count(kept),
                redactions=redactions,
            )
        typed_or_input = self.typed_prompt or next(
            (m["content"] for m in self.messages if m.get("input") and not self.typed), ""
        )
        if not typed_or_input:
            return None
        return Transcript(
            title=one_line(typed_or_input, TITLE_CHARS), session=self.session(path), cwd=cwd
        )


def _read_rollout(path: Path, *, look: bool) -> tuple[_Rollout, bool]:
    """``(lines, whole)``: ``path`` read in full — or, to ``look``, only until its first typed
    prompt, and never past :data:`LOOK_BYTES` — and whether that reached the end of the file.
    Raises :class:`SessionUnreadable` when the part it needs cannot be read."""
    rollout = _Rollout()
    with contextlib.closing(_session_lines(path)) as lines:
        read = 0
        for count, raw in enumerate(lines):
            if not count % GIVE_WAY_LINES:
                give_way()
            rollout.feed(raw)
            if look:
                if rollout.typed_prompt:
                    return rollout, False
                read += len(raw)
                if read >= LOOK_BYTES:
                    return rollout, False
    return rollout, True


def _signature(path: Path) -> tuple | None:
    """The file's :func:`file_signature`, and the size limit its reading was made under."""
    signature = file_signature(path)
    return None if signature is None else (*signature, MAX_SESSION_BYTES)


def _reading(path: Path, *, look: bool) -> Reading:
    """What ``path`` holds, as the step lists it: remembered while the file is unchanged, else
    read — in full, or to ``look``, only as far as its first typed prompt."""
    signature = _signature(path)
    if signature is None:
        return None
    found, reading = READINGS.recall(path, signature, whole=not look)
    if found:
        return reading
    try:
        rollout, whole = _read_rollout(path, look=look)
    except SessionUnreadable as exc:
        reading = Unreadable(exc.reason)
    else:
        transcript = rollout.transcript(path, whole=whole)
        reading = transcript if transcript is not None or whole else UNDECIDED
    READINGS.keep(path, signature, reading)
    return reading


def read_rollout(path: Path, titles: Mapping[str, str]) -> tuple[dict[str, Any], str, int] | None:
    """One Codex session file as a PersonalClaw conversation: ``(conversation, session id,
    redactions)``, or ``None`` for one with no prompt in it. ``path`` is the session as Codex
    wrote it (``.jsonl``) or as it compressed it (``.jsonl.zst``), and both read the same.

    ``conversation`` is ``{"messages", "title", "created_at", "updated_at", "cwd"}``: each prompt,
    each reply, and each tool call by name and what it was for. Tool OUTPUT is not carried (it is
    where a session is largest and where a printed credential sits), and neither is the model's
    reasoning. Every text passes floor 2.

    Raises :class:`SessionUnreadable` for a file this import cannot read: cut off, damaged, larger
    than :data:`MAX_SESSION_BYTES`, or refused by the system. Nothing of it is kept. Read whole,
    so what the step says of the file becomes final too.
    """
    if refuses(path):
        return None
    signature = _signature(path)
    try:
        rollout, _whole = _read_rollout(path, look=False)
    except SessionUnreadable as exc:
        if signature is not None:
            READINGS.keep(path, signature, Unreadable(exc.reason))
        raise
    if signature is not None:
        READINGS.keep(path, signature, rollout.transcript(path, whole=True))
    return rollout.conversation(path, titles)


def read_for_import(item: ImportItem) -> tuple[dict[str, Any], int] | None:
    """The conversation ``item`` names, read in full now (:func:`read_rollout`), titled as the scan
    listed it. Raises :class:`SessionUnreadable` for a file that cannot be read to its end."""
    listed = str(item.payload.get("listed_as") or "")
    read = read_rollout(Path(item.path), {item.target: listed} if listed else {})
    if read is None:
        return None
    conversation, _session, redactions = read
    return conversation, redactions


def read_in_full(path: Path) -> None:
    """Read one session file in full, so what the step says of it is final."""
    if not refuses(path):
        _reading(path, look=False)


def _session_files(root: Path) -> list[Path]:
    """Every session under ``root``, one file each, in the order of their names: its
    ``rollout-*.jsonl``, or the ``.jsonl.zst`` Codex compressed it to.

    Codex's rule for the rest: while it turns one into the other both files are there and it reads
    the plain one, so this does too. What else it leaves beside them (the ``.tmp`` files of a
    compression under way) is not a session.
    """
    if not root.is_dir():
        return []
    found: dict[Path, Path] = {}
    for path in root.rglob("rollout-*"):
        if not path.name.endswith((_SESSION_SUFFIX, _COMPRESSED_SUFFIX)) or not path.is_file():
            continue
        plain = path.with_name(_plain_name(path.name))
        if path == plain or plain not in found:
            found[plain] = path
    return [found[plain] for plain in sorted(found)]


def _session_label(path: Path, titles: Mapping[str, str]) -> str:
    """A session file as the step names it: its file name, after the title Codex lists it by
    when it has one."""
    found = _SESSION_ID_RE.search(_plain_name(path.name))
    title = titles.get(found.group(1), "") if found else ""
    if not title.strip():
        return path.name
    shown, _n = safe_text(one_line(title, TITLE_CHARS))
    return f"“{shown}” ({path.name})"


def _scan_conversations(base: Path, result: ScanResult, *, look: bool) -> None:
    """Every session, ``sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl`` — a conversation each,
    whether it is as Codex wrote it or compressed (``.jsonl.zst``). Either way it is keyed by its
    plain name, so a session Codex compresses after an import is still the one that import brought
    over.

    A session this import cannot read is named with the reason. One Codex archived (moved to
    ``archived_sessions/``) is counted, not imported. To ``look`` is to read each file not read
    before only as far as its first typed prompt: a provisional item, read in full later
    (:attr:`ScanResult.unread`) — and a file that cannot be read to its end is named then. An
    index of their names that cannot be read is named on the scan, and they come over under their
    first prompts.
    """
    try:
        titles = _session_titles(base)
    except FileUnreadable as exc:
        note_unreadable(result, unreadable_file(exc))
        titles = {}
    unreadable: list[str] = []
    for path in _session_files(base / _SESSIONS_DIR):
        if refuses(path):
            continue
        result.conversation_files += 1
        reading = _reading(path, look=look)
        if not is_final(reading):
            result.unread.append(path)
        if isinstance(reading, Unreadable):
            unreadable.append(f"{_session_label(path, titles)} {reading.reason}.")
            continue
        if not isinstance(reading, Transcript):
            continue
        listed = titles.get(reading.session, "")
        title, _n = safe_text(one_line(listed or reading.title, TITLE_CHARS))
        redactions = reading.redactions or 0
        result.redactions += redactions
        cwd = reading.cwd
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.CONVERSATIONS,
                key=path.with_name(_plain_name(path.name)).relative_to(base).as_posix(),
                title=title,
                name=reading.session,
                payload={"listed_as": listed} if listed else {},
                path=str(path),
                origin=f"Project · {recorded_label(cwd)}" if cwd else "",
                note=conversation_note(reading.messages),
                redactions=redactions,
                provisional=not reading.whole,
            )
        )
    not_imported_rows(
        result,
        [
            unreadable_conversations(unreadable),
            (
                len(_session_files(base / _ARCHIVED_SESSIONS_DIR)),
                "Archived conversations",
                "You archived them in Codex, so they stay there.",
            ),
        ],
    )


def _scan_settings(
    config: dict[str, Any], config_name: str, withheld: int, result: ScanResult
) -> None:
    """The rest of the config: Codex's own options, named and left where they are. Its MCP
    servers are items of their own."""
    result.secrets_skipped += withheld
    names = [
        str(key)
        for key, value in config.items()
        if not (config_name == _TOML_CONFIG and key == _MCP_TABLE and isinstance(value, dict))
    ]
    settings_not_imported(result, DISPLAY_NAME, names)
