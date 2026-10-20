"""``.env.example`` names only variables something actually reads or sets.

It advertised ``OLLAMA_HOST`` (nothing reads it: the Ollama address is the provider's Endpoint
setting) and listed ``PERSONALCLAW_CLI``, ``PERSONALCLAW_READY``, ``PERSONALCLAW_BIN`` and
``PERSONALCLAW_MCP_JSON`` among the variables the gateway sets for its subprocesses. Nothing sets
or reads the first, second-to-last or last, and ``PERSONALCLAW_READY`` is the prefix of a line on
stdout, not a variable. A copied ``.env`` that sets a name nobody reads changes nothing, silently.

Every ``PERSONALCLAW_*`` name the file gives is held to the code that reads or sets it. The other
names belong to programs this repository does not contain, so each is pinned with its reader.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_EXAMPLE = _REPO / ".env.example"

#: ``# NAME=value`` settings, and the ``#   NAME   — what it is`` reference list.
_SETTING = re.compile(r"^#\s?([A-Z][A-Z0-9_]+)=", re.M)
_REFERENCE = re.compile(r"^#\s+([A-Z][A-Z0-9_]+)\s+—", re.M)

#: Names read by something outside core's own code, each with its reader.
_READ_OUTSIDE_CORE = {
    "SLACK_APP_TOKEN": "the Slack channel app, through the credential store's key name",
    "SLACK_BOT_TOKEN": "the Slack channel app, through the credential store's key name",
    "ANTHROPIC_API_KEY": "a provider's credential descriptor (`value_env`), env first",
    "OPENAI_API_KEY": "a provider's credential descriptor (`value_env`), env first",
    "OPENAI_BASE_URL": "the openai SDK, when the provider names no base URL",
    "SKILLS_SH_API_KEY": "the skills.sh app",
}


def _named() -> set[str]:
    text = _EXAMPLE.read_text(encoding="utf-8")
    return set(_SETTING.findall(text)) | set(_REFERENCE.findall(text))


def _code_text() -> str:
    """Core's code with comment-only lines dropped: a name in a comment is read by nobody."""
    roots = [_REPO / "src" / "personalclaw", _REPO / "desktop", _REPO / "deploy"]
    parts: list[str] = []
    for root in roots:
        for p in root.rglob("*"):
            if not p.is_file() or "node_modules" in p.parts or "test" in p.parts:
                continue
            if p.suffix not in {".py", ".js", ".mjs", ".json", ".yml", ".yaml", ".sh", ".conf"}:
                continue
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            parts.extend(ln for ln in lines if not ln.lstrip().startswith(("#", "//", "*")))
    parts.append((_REPO / "Makefile").read_text(encoding="utf-8"))
    return "\n".join(parts)


def _read_or_set(name: str, code: str) -> bool:
    if re.search(rf"""["'`]{name}["'`]|\$\{{{name}\b|\b{name}=""", code):
        return True
    # A family read under one prefix, like `f"PERSONALCLAW_UPLOAD_LIMIT_{category.upper()}"`.
    head = name.rsplit("_", 1)[0]
    return bool(re.search(rf"""f["']{head}_\{{""", code))


def test_every_personalclaw_variable_it_names_is_read_or_set_by_core() -> None:
    names = sorted(n for n in _named() if n.startswith("PERSONALCLAW_"))
    # Positive control: the parse sees both forms the file uses.
    assert "PERSONALCLAW_HOME" in names and "PERSONALCLAW_TERMINAL" in names, names
    code = _code_text()
    assert _read_or_set("PERSONALCLAW_HOME", code)
    unread = [n for n in names if not _read_or_set(n, code)]
    assert unread == []


def test_every_other_variable_it_names_has_a_known_reader() -> None:
    others = {n for n in _named() if not n.startswith("PERSONALCLAW_")}
    assert others, "the parse found no provider credential names at all"
    assert others == set(_READ_OUTSIDE_CORE)
