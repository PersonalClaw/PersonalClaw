"""Codex scanner — ``$CODEX_HOME`` (default ``~/.codex``).

Same contract as the Claude Code scanner: a pure, read-only function of a root.
Codex keeps less in its home, so the map is shorter:

====================  =========================================================
``AGENTS.md``         ``instructions``
``config.toml``       ``mcp_servers`` (``[mcp_servers.<name>]``) + ``settings``
``config.json``       same, for a JSON-configured install
====================  =========================================================

TOML is parsed with the stdlib ``tomllib``; a root whose config can't be parsed
simply yields no config items (repairing another tool's half-written file is not
our job).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from personalclaw.onboarding_import.floors import (
    SplitDocument,
    read_json_tables_safely,
    read_text_safely,
    refuses,
    split_tables,
)
from personalclaw.onboarding_import.model import ImportCategory, ImportItem, ScanResult

NAME = "codex"
DISPLAY_NAME = "Codex"
ENV_VAR = "CODEX_HOME"
DEFAULT_ROOT = "~/.codex"

_INSTRUCTION_FILES = ("AGENTS.md",)
_TOML_CONFIG = "config.toml"
_JSON_CONFIG = "config.json"
#: The config table that holds MCP servers (the JSON install uses ``mcpServers``).
_MCP_KEYS = ("mcp_servers", "mcpServers")


def resolve_root() -> Path:
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

    config, config_name = _read_config(base)
    # The whole-document count, as before; each item below also carries its own share.
    result.secrets_skipped += config.total
    _scan_config(config, config_name, result)

    result.note_withheld()
    return result


def _read_config(base: Path) -> tuple[SplitDocument, str]:
    toml_path = base / _TOML_CONFIG
    if toml_path.is_file():
        if refuses(toml_path):
            return SplitDocument(unattributed=1), _TOML_CONFIG
        try:
            with toml_path.open("rb") as handle:
                parsed = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return SplitDocument(), _TOML_CONFIG
        return split_tables(parsed, _MCP_KEYS), _TOML_CONFIG
    json_path = base / _JSON_CONFIG
    if json_path.is_file():
        return read_json_tables_safely(json_path, _MCP_KEYS), _JSON_CONFIG
    return SplitDocument(), ""


def _scan_config(config: SplitDocument, config_name: str, result: ScanResult) -> None:
    for name, spec, withheld in config.entries:
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.MCP_SERVERS,
                key=name,
                title=name,
                payload=spec,
                secrets_skipped=withheld,
            )
        )
    if isinstance(config.remainder, dict) and config.remainder:
        result.items.append(
            ImportItem(
                source=NAME,
                category=ImportCategory.SETTINGS,
                key=config_name,
                title=f"{DISPLAY_NAME} settings",
                payload=config.remainder,
                secrets_skipped=config.remainder_withheld,
            )
        )
