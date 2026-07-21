"""User tool preferences (PT3) — which tools the user has turned OFF entirely.

A harder gate than per-turn retrieval: a disabled tool is removed from the
runtime's ``_tool_defs`` AND ``_tool_index`` (so the model never sees it and can't
call it) and is reported disabled by ``GET /api/tools``. Disable is authoritative
and pre-retrieval — orthogonal to progressive disclosure (which only defers
*schemas* of enabled tools).

Persisted in ``~/.personalclaw/tool_prefs.json``:

    {"disabled": ["builtin:some_tool", "mcp/GitHub/SomeTool", ...]}

Keys are ``"<provider>:<tool>"`` (provider = the provider-instance name, e.g.
``builtin`` for native builtins, the MCP server name for MCP tools). MCP tools
ALSO honor the existing ``mcpServers.<server>.disabledTools`` in ``mcp.json`` (the
ACP layer reads that) — this module is the native-runtime + UI unification.

CORE-LOCKED tools (:data:`CORE_LOCKED`) can never be disabled — the platform's own
features call them. A disable request for a locked tool is rejected; the filter
ignores a locked entry even if one somehow lands in the file.
"""

from __future__ import annotations

import json
import logging

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_PREFS_NAME = "tool_prefs.json"

# Tools the platform's own features depend on — never user-disableable. Bare tool
# names (matched against the tool name regardless of provider). Primitives +
# discovery + the orientation tools + the SDLC/loop entry tools chat & loops drive.
CORE_LOCKED: frozenset[str] = frozenset(
    {
        # universal coding primitives (git/tests/lint run via bash, not own tools)
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "glob",
        "list_dir",
        # progressive-disclosure discovery — tools AND skills (can't recover without these)
        "tool_search",
        "tool_schema",
        "skill_search",
        "skill_invoke",
        # orientation / control
        "ask_user",
        "finish",
        "tool_result_get",
        # project-run (loop) entry points the chat/loop features invoke by name
        "project_run_create",
        "project_run_start",
        "project_run_status",
        "project_run_list",
    }
)


def _prefs_path():
    return config_dir() / _PREFS_NAME


def key_for(provider: str, name: str) -> str:
    """The disable key for a (provider, tool) pair."""
    return f"{provider or 'other'}:{name}"


def is_locked(name: str) -> bool:
    """Whether a tool name is core-locked (cannot be user-disabled)."""
    return name in CORE_LOCKED


# Native tool PROVIDERS the platform can't run without — never user-disableable
# at the provider level (the bedrock filesystem+shell bundle). Other native/app
# providers (knowledge/tasks/loops/inbox/memory/artifacts/…) and every MCP/OpenAI
# server ARE provider-disableable. (MCP servers also have their own mcp.json
# `disabled` flag; this set is the native side.)
LOCKED_PROVIDERS: frozenset[str] = frozenset({"personalclaw-filesystem"})


def is_provider_locked(provider: str) -> bool:
    return provider in LOCKED_PROVIDERS


def _load() -> dict:
    """Load the prefs doc. Never raises — missing/corrupt → empty (fail-open)."""
    try:
        data = json.loads(_prefs_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        logger.debug("tool_prefs: unreadable — treating as none disabled", exc_info=True)
        return {}


def _save(doc: dict) -> None:
    # prune empties so the file stays clean
    out = {k: sorted(v) for k, v in doc.items() if v}
    atomic_write(_prefs_path(), json.dumps(out, ensure_ascii=False, indent=2))


def load_disabled() -> set[str]:
    """The set of disabled TOOL keys (``provider:tool``)."""
    items = _load().get("disabled", [])
    return {str(k) for k in items if isinstance(k, str)}


def load_disabled_providers() -> set[str]:
    """The set of disabled PROVIDER names (whole-provider off). A locked platform
    provider is never reported disabled (a stale entry is ignored)."""
    items = _load().get("disabledProviders", [])
    return {str(k) for k in items if isinstance(k, str) and not is_provider_locked(str(k))}


def set_enabled(provider: str, name: str, enabled: bool) -> dict:
    """Enable/disable a native-provider TOOL. Refuses a core-locked tool. (MCP
    tools use ``/api/mcp/toggle-tool`` → mcp.json.)"""
    if not name:
        return {"ok": False, "error": "tool name is required"}
    if not enabled and is_locked(name):
        return {
            "ok": False,
            "error": f"{name!r} is required by platform features and can't be disabled",
            "locked": True,
        }
    doc = _load()
    disabled = {str(k) for k in doc.get("disabled", [])}
    k = key_for(provider, name)
    disabled.discard(k) if enabled else disabled.add(k)
    doc["disabled"] = disabled
    _save(doc)
    return {"ok": True, "provider": provider, "tool": name, "enabled": enabled}


def set_provider_enabled(provider: str, enabled: bool) -> dict:
    """Enable/disable a whole native tool PROVIDER. Refuses a locked platform
    provider. Disabling removes its entire toolset from the runtime + catalog."""
    if not provider:
        return {"ok": False, "error": "provider is required"}
    if not enabled and is_provider_locked(provider):
        return {
            "ok": False,
            "error": f"{provider!r} is a platform provider and can't be disabled",
            "locked": True,
        }
    doc = _load()
    dp = {str(k) for k in doc.get("disabledProviders", [])}
    dp.discard(provider) if enabled else dp.add(provider)
    doc["disabledProviders"] = dp
    _save(doc)
    return {"ok": True, "provider": provider, "enabled": enabled}


def is_disabled(
    provider: str,
    name: str,
    disabled: set[str] | None = None,
    disabled_providers: set[str] | None = None,
) -> bool:
    """Whether a tool is user-disabled — either individually OR because its whole
    provider is disabled. A locked tool is NEVER disabled (defensive)."""
    if is_locked(name):
        return False
    dp = disabled_providers if disabled_providers is not None else load_disabled_providers()
    if provider in dp:
        return True
    d = disabled if disabled is not None else load_disabled()
    return key_for(provider, name) in d


def is_provider_disabled(provider: str, disabled_providers: set[str] | None = None) -> bool:
    dp = disabled_providers if disabled_providers is not None else load_disabled_providers()
    return provider in dp
