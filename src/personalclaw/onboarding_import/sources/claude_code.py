"""Claude Code scanner — ``$CLAUDE_CONFIG_DIR`` (default ``~/.claude``).

A pure function of a directory: :func:`scan` opens files, applies the floors, and
returns a :class:`~..model.ScanResult`. It holds no store, no session and no config
handle, so it is fixture-testable against a throwaway root — and it never writes to
the root it reads (importing from another tool must not modify that tool).

What it maps:

===================  ==========================================================
``CLAUDE.md``        ``instructions``
``memories/*.md``    ``memories``
``.mcp.json``        ``mcp_servers`` (one item per server)
``skills/<n>/``      ``skills`` (a dir with a ``SKILL.md``)
``settings.json``    ``settings`` (one item; review-gated, never live config)
===================  ==========================================================

Anything else in the root is ignored rather than guessed at.
"""

from __future__ import annotations

import os
from pathlib import Path

from personalclaw.onboarding_import.floors import read_json_safely, read_text_safely, refuses
from personalclaw.onboarding_import.model import ImportCategory, ImportItem, ScanResult

NAME = "claude_code"
DISPLAY_NAME = "Claude Code"
ENV_VAR = "CLAUDE_CONFIG_DIR"
DEFAULT_ROOT = "~/.claude"

_INSTRUCTION_FILES = ("CLAUDE.md",)
_MEMORIES_DIR = "memories"
_SKILLS_DIR = "skills"
_MCP_FILE = ".mcp.json"
_SETTINGS_FILE = "settings.json"


def resolve_root() -> Path:
    """Env var first, documented default second (no other search paths)."""
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return Path(env).expanduser()
    return Path(DEFAULT_ROOT).expanduser()


def scan(root: Path | str | None = None) -> ScanResult:
    base = Path(root).expanduser() if root is not None else resolve_root()
    result = ScanResult(
        source=NAME, display_name=DISPLAY_NAME, root=str(base), present=base.is_dir()
    )
    if not result.present:
        return result

    _scan_instructions(base, result)
    _scan_memories(base, result)
    _scan_mcp(base, result)
    _scan_skills(base, result)
    _scan_settings(base, result)
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
    visited = {*_INSTRUCTION_FILES, _MCP_FILE, _SETTINGS_FILE}
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name not in visited and refuses(path):
            result.secrets_skipped += 1


def _scan_instructions(base: Path, result: ScanResult) -> None:
    for name in _INSTRUCTION_FILES:
        path = base / name
        if not path.is_file():
            continue
        text, redactions, skipped = read_text_safely(path)
        result.secrets_skipped += skipped
        if not text.strip():
            continue
        result.redactions += redactions
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.INSTRUCTIONS,
                key=name,
                title=name,
                text=text,
                redactions=redactions,
            )
        )


def _scan_memories(base: Path, result: ScanResult) -> None:
    mem_dir = base / _MEMORIES_DIR
    if not mem_dir.is_dir():
        return
    for path in sorted(mem_dir.glob("*.md")):
        if not path.is_file():
            continue
        text, redactions, skipped = read_text_safely(path)
        result.secrets_skipped += skipped
        if not text.strip():
            continue
        result.redactions += redactions
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.MEMORIES,
                key=f"{_MEMORIES_DIR}/{path.name}",
                title=path.stem,
                text=text,
                redactions=redactions,
            )
        )


def _scan_mcp(base: Path, result: ScanResult) -> None:
    path = base / _MCP_FILE
    if not path.is_file():
        return
    data, skipped = read_json_safely(path)
    result.secrets_skipped += skipped
    if not isinstance(data, dict):
        return
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return
    for name, spec in sorted(servers.items()):
        if not isinstance(spec, dict) or not str(name).strip():
            continue
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.MCP_SERVERS,
                key=str(name),
                title=str(name),
                payload=spec,
            )
        )


def _scan_skills(base: Path, result: ScanResult) -> None:
    skills_root = base / _SKILLS_DIR
    if not skills_root.is_dir():
        return
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        # Count (and later exclude) any credential file sitting inside the skill.
        # The writer's fetch applies the same predicate, so a counted file is also
        # an uninstalled file — the count and the behaviour cannot drift.
        withheld = sum(1 for f in skill_dir.rglob("*") if f.is_file() and refuses(f))
        result.secrets_skipped += withheld
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.SKILLS,
                key=skill_dir.name,
                title=skill_dir.name,
                path=str(skill_dir),
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
        )
    )
