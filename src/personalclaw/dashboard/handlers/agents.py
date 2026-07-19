"""Agent configuration, themes, marketplace integration, and agent CRUD handlers."""

import asyncio
import dataclasses
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.config.loader import (
    AgentProfile,
    AppConfig,
    config_dir,
    resolve_agent_config_path,
)
from personalclaw.config.schema import SCHEMA_REGISTRY, config_entry_to_dict
from personalclaw.dashboard.chat_utils import _SLASH_COMMAND_HINTS
from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


# ── Custom Themes ──

_THEMES_DIR_NAME = "themes"
_THEME_NAME_MAX_LEN = 60
_THEME_SLUG_MAX_LEN = 40
# Holds either an emoji char (≤4) or an `icon:<LucideName>` token (icon library
# is offered first in the UI; emoji is the fallback), so allow room for the token.
_THEME_EMOJI_MAX_LEN = 48
_THEME_DEFAULT_EMOJI = "🎨"
# A theme is a named color identity; --color-primary is its defining anchor (the
# brand accent the whole UI re-tints from), so it is the one required var.
_THEME_REQUIRED_VARS = ("--color-primary",)

# CSS variables that constitute a complete theme definition. This is the color
# vocabulary of the current `web` frontend (design/tokenRegistry.ts — the Brand,
# Surfaces, Content, Semantic, and Glow/gradient color tokens). A theme carries a
# {dark, light} value for each; anything absent falls back to the token default.
# Kept in exact sync with the ColorToken varNames in tokenRegistry.ts.
_THEME_CSS_VARS = (
    # Brand
    "--color-primary",
    "--color-primary-emphasis",
    "--color-on-primary",
    "--color-primary-container",
    "--color-secondary",
    # Surfaces
    "--color-canvas",
    "--color-surface",
    "--color-surface-low",
    "--color-surface-container",
    "--color-surface-high",
    "--color-surface-highest",
    "--color-rail",
    # Content
    "--color-on-surface",
    "--color-on-surface-low",
    "--color-on-surface-var",
    "--color-outline",
    "--color-outline-variant",
    # Semantic
    "--color-ok",
    "--color-warn",
    "--color-danger",
    "--color-info",
    # Glow & gradient (the wave surface + spark + ring)
    "--grad-1",
    "--grad-2",
    "--grad-3",
    "--grad-4",
    "--glow-a",
    "--glow-b",
    "--ring-stop-2",
)


def _themes_dir() -> Path:
    """Return the custom themes directory under config_dir()."""
    return config_dir() / _THEMES_DIR_NAME


# Positive allowlist: only characters that appear in legitimate CSS color,
# shadow, and length values.  This blocks semicolons, braces, backslashes,
# angle brackets, quotes, at-signs, colons, and everything else that could
# escape the CSS declaration context.
_CSS_VALUE_ALLOWED_RE = re.compile(r"^[a-zA-Z0-9#(),.\- %/]+$")

# Function denylist for dangerous CSS functions whose individual characters
# pass the allowlist above (e.g. url(), expression(), image(), image-set()).
_CSS_DANGEROUS_FUNC_RE = re.compile(
    r"url\s*\(|expression\s*\(|image\s*\(|image-set\s*\(",
    re.IGNORECASE,
)

# Set of allowed CSS variable names (mirrors frontend ALLOWED_CSS_VARS).
_THEME_CSS_VARS_SET: frozenset[str] = frozenset(_THEME_CSS_VARS)


def _sanitize_css_value(value: str) -> str | None:
    """Validate a single CSS value using a positive character allowlist.

    Returns the trimmed value if safe, or None if rejected.
    """
    if not isinstance(value, str):
        return None
    if len(value) > 200:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if not _CSS_VALUE_ALLOWED_RE.match(trimmed):
        return None
    if _CSS_DANGEROUS_FUNC_RE.search(trimmed):
        return None
    return trimmed


def _validate_theme_data(data: dict) -> str | None:
    """Validate a theme JSON object. Returns error string or None.

    Validates keys against ``_THEME_CSS_VARS_SET`` allowlist.
    Unknown keys are rejected.
    """
    if not isinstance(data, dict):
        return "theme must be a JSON object"
    name = data.get("name", "")
    if not isinstance(name, str):
        return "name must be a string"
    name = name.strip()
    if not name:
        return "name is required"
    if len(name) > _THEME_NAME_MAX_LEN:
        return f"name too long (max {_THEME_NAME_MAX_LEN} chars)"
    # Reject path-traversal tokens in the theme display name; the slug derived
    # from it is sanitized but the name field itself is stored as-is, so
    # rejecting traversal-shaped names defends against any code that
    # interpolates name into a path.
    if "/" in name or "\\" in name or ".." in name:
        return "name cannot contain path-separator or traversal characters"
    emoji = data.get("emoji", "")
    if not isinstance(emoji, str):
        return "emoji must be a string"
    for mode in ("dark", "light"):
        mode_data = data.get(mode, {})
        if not isinstance(mode_data, dict):
            return f"'{mode}' must be a JSON object"
        for required_var in _THEME_REQUIRED_VARS:
            if required_var not in mode_data:
                return f"'{mode}' is missing required" f" variable '{required_var}'"
        for key, val in mode_data.items():
            if key not in _THEME_CSS_VARS_SET:
                return f"'{mode}' key '{key}' is not a recognized theme variable"
            if _sanitize_css_value(val) is None:
                return f"'{mode}' variable '{key}' has an invalid value"
    return None


def _strip_to_allowed_vars(mode_data: dict[str, str]) -> dict[str, str]:
    """Return only the allowed CSS vars with sanitized values.

    Defense-in-depth: even after validation, re-filter before writing
    so only known variables with clean values reach disk.
    """
    result: dict[str, str] = {}
    for key, val in mode_data.items():
        if key not in _THEME_CSS_VARS_SET:
            continue
        clean = _sanitize_css_value(val)
        if clean is not None:
            result[key] = clean
    return result


def _slugify_theme_name(name: str) -> str:
    """Convert a theme name to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9\-]", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:_THEME_SLUG_MAX_LEN] or "custom"


async def api_themes(request: web.Request) -> web.Response:
    """GET /api/themes — list all custom themes, sorted by creation date."""
    themes_path = _themes_dir()
    result: list[dict[str, Any]] = []
    if themes_path.is_dir():
        for f in themes_path.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                result.append(
                    {
                        "slug": f.stem,
                        "name": data.get("name", f.stem),
                        "emoji": data.get("emoji", "🎨"),
                        "created_at": data.get("created_at", ""),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue
    # Sort by created_at (oldest first), falling back to name
    result.sort(key=lambda t: t.get("created_at") or "9999")
    return web.json_response({"themes": result})


async def api_themes_create(request: web.Request) -> web.Response:
    """POST /api/themes — create a new custom theme."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    err = _validate_theme_data(body)
    if err:
        return web.json_response({"error": err}, status=400)

    name = body["name"].strip()
    slug = _slugify_theme_name(name)
    emoji = (
        body.get("emoji", _THEME_DEFAULT_EMOJI).strip()[:_THEME_EMOJI_MAX_LEN]
        or _THEME_DEFAULT_EMOJI
    )

    themes_path = _themes_dir()
    themes_path.mkdir(parents=True, exist_ok=True)
    target = themes_path / f"{slug}.json"
    if target.exists():
        return web.json_response({"error": f"theme '{slug}' already exists"}, status=409)

    theme_data = {
        "name": name,
        "slug": slug,
        "emoji": emoji,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dark": _strip_to_allowed_vars(body.get("dark", {})),
        "light": _strip_to_allowed_vars(body.get("light", {})),
    }
    target.write_text(json.dumps(theme_data, indent=2) + "\n", encoding="utf-8")
    return web.json_response({"ok": True, "slug": slug, "theme": theme_data})


async def api_theme_detail(request: web.Request) -> web.Response:
    """GET/PUT/DELETE /api/themes/{slug} — get, update, or delete a custom theme."""
    slug = request.match_info["slug"]
    # Sanitize slug to prevent path traversal
    safe_slug = re.sub(r"[^a-z0-9\-]", "", slug)
    if not safe_slug or safe_slug != slug:
        return web.json_response({"error": "invalid theme slug"}, status=400)

    target = _themes_dir() / f"{safe_slug}.json"

    if request.method == "DELETE":
        if not target.exists():
            return web.json_response({"error": "not found"}, status=404)
        target.unlink()
        return web.json_response({"ok": True})

    if request.method == "PUT":
        if not target.exists():
            return web.json_response({"error": "not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "JSON body must be an object"}, status=400)
        err = _validate_theme_data(body)
        if err:
            return web.json_response({"error": err}, status=400)
        name = body["name"].strip()
        emoji = (
            body.get("emoji", _THEME_DEFAULT_EMOJI).strip()[:_THEME_EMOJI_MAX_LEN]
            or _THEME_DEFAULT_EMOJI
        )
        # Preserve created_at from existing file
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        theme_data = {
            "name": name,
            "slug": safe_slug,
            "emoji": emoji,
            "created_at": existing.get("created_at", datetime.now(timezone.utc).isoformat()),
            "dark": _strip_to_allowed_vars(body.get("dark", {})),
            "light": _strip_to_allowed_vars(body.get("light", {})),
        }
        target.write_text(json.dumps(theme_data, indent=2) + "\n", encoding="utf-8")
        return web.json_response({"ok": True, "theme": theme_data})

    # GET
    if not target.exists():
        return web.json_response({"error": "not found"}, status=404)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return web.json_response({"error": "failed to read theme"}, status=500)
    return web.json_response(data)


# ── Agent Config ──


def _auto_install_agent() -> None:
    """Re-install agent config so changes take effect immediately."""
    try:
        from personalclaw.agent import rebuild_agent_config  # noqa: F811

        rebuild_agent_config()
        logger.info("Auto-applied agent config via dashboard")
    except Exception:
        logger.debug("Auto-apply agent config failed", exc_info=True)


def _find_agent_config() -> Path:
    """Find agents/defaults.json — delegates to centralized resolver."""
    return resolve_agent_config_path()


def _installed_agent_config() -> Path:
    """Return the installed agent config path (~/.personalclaw/agents/personalclaw.json).

    This is the live config that ACP agent reads.  Dashboard MCP toggle
    and sync operations write here — NOT to agents/defaults.json.
    """
    from personalclaw.agent import AGENT_FILENAME, AGENTS_DIR  # noqa: F811

    return AGENTS_DIR / AGENT_FILENAME


async def api_agent_config(request: web.Request) -> web.Response:
    """GET/PUT /api/agent/config — read or write the installed agent config.

    Reads/writes ``~/.personalclaw/agents/personalclaw.json`` — the live config that
    ACP agent actually uses at runtime.  Falls back to ``agents/defaults.json``
    if the installed config doesn't exist yet.
    """
    import personalclaw.dashboard.handlers as _h  # noqa: F811

    installed_path = _h._installed_agent_config()
    defaults_path = _h._find_agent_config()
    # Prefer installed config (what ACP agents read); fall back to defaults
    agent_config_path = installed_path if installed_path.is_file() else defaults_path

    if request.method == "PUT":
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "JSON body must be an object"}, status=400)
        config = body.get("config")
        if not isinstance(config, dict):
            return web.json_response({"error": "config must be an object"}, status=400)
        try:
            # Track tools the user intentionally removed from shipped defaults
            # so they don't reappear on upgrade.  Stored in ~/.personalclaw/config.json
            # (NOT personalclaw.json — ACP agent rejects unknown fields).
            # Per-key dict so removing from allowedTools only doesn't affect tools.
            from personalclaw.agent import get_shipped_tools  # noqa: F811

            shipped = get_shipped_tools()
            removed_per_key: dict[str, list[str]] = {}
            for key in ("tools", "allowedTools"):
                diff = sorted(set(shipped.get(key, [])) - set(config.get(key, [])))
                if diff:
                    removed_per_key[key] = diff
            pc_cfg_path = _h.config_path()  # type: ignore[operator]
            try:
                pc_cfg = (
                    json.loads(pc_cfg_path.read_text(encoding="utf-8"))
                    if pc_cfg_path.exists()
                    else {}
                )
            except Exception:
                pc_cfg = {}
            if removed_per_key:
                pc_cfg["removedTools"] = removed_per_key
            else:
                pc_cfg.pop("removedTools", None)
            pc_cfg_path.write_text(json.dumps(pc_cfg, indent=2) + "\n", encoding="utf-8")
            installed_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            # Restart ACP agent sessions so new config takes effect
            await _h._reset_all_sessions(request)
            return web.json_response({"ok": True, "applied": True})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)
    # GET
    try:
        data = json.loads(agent_config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    return web.json_response(data)


async def api_default_agent(request: web.Request) -> web.Response:
    """GET/PUT /api/config/default-agent — read or set the default agent."""
    import personalclaw.dashboard.handlers as _h  # noqa: F811

    if request.method == "PUT":
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "JSON body must be an object"}, status=400)
        # Missing key ≠ reset: a malformed body (wrong key, typo — e.g. "name")
        # must 400, not silently CLEAR the default agent while returning ok:true.
        # Reset stays explicit: {"agent": ""}.
        if "agent" not in body:
            return web.json_response(
                {"error": "body must carry 'agent' (an agent name, or \"\" to reset)"},
                status=400,
            )
        name = str(body.get("agent", ""))
        # Reject an unknown agent up-front. Without this the write "succeeds"
        # (ok:true) but the very next AppConfig.load() re-migration reconciles the
        # dangling name back to the real default — so the caller sees success yet the
        # change silently didn't stick. Fail-fast with a clear error instead (same
        # set-time-validation principle as the model/search active setters, #16/#17).
        # Empty string is allowed (reset to the system default).
        if name:
            known = set((AppConfig.load().agents or {}).keys())
            if name not in known:
                return web.json_response(
                    {"error": f"Unknown agent {name!r}. Create it first (Agents), or pick an "
                     f"existing one. Known: {sorted(known)}"},
                    status=400,
                )
        path = _h.config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}
        # Single authoritative top-level default_agent (what AppConfig.default_agent,
        # the agents-list endpoint, and the resolver all read). Drop any stale
        # nested agent.default_agent left by older configs.
        data["default_agent"] = name
        if isinstance(data.get("agent"), dict):
            data["agent"].pop("default_agent", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return web.json_response({"ok": True, "default_agent": name})
    cfg = AppConfig.load()
    return web.json_response({"default_agent": cfg.default_agent})


# ── Config Schema ──


async def api_config_schema(request: web.Request) -> web.Response:
    """GET /api/config/schema — return config schema entries."""
    entries = SCHEMA_REGISTRY

    # Filter by tags (comma-separated, intersection)
    tags_param = request.query.get("tags", "").strip()
    if tags_param:
        requested_tags = {t.strip() for t in tags_param.split(",") if t.strip()}
        entries = [e for e in entries if set(e.tags) & requested_tags]

    # Filter out deprecated entries when deprecated=false
    dep_param = request.query.get("deprecated", "").strip().lower()
    if dep_param == "false":
        entries = [e for e in entries if not e.deprecated]

    # Serialize, masking sensitive defaultValues and converting dataclass
    # defaults to None (they aren't JSON-serializable).
    result = []
    for entry in entries:
        d = config_entry_to_dict(entry)
        if entry.sensitive or dataclasses.is_dataclass(d.get("defaultValue")):
            d["defaultValue"] = None
        result.append(d)

    return web.json_response({"entries": result})


async def api_agents_installed(request: web.Request) -> web.Response:
    """GET /api/agents/installed — list installed agent provider names.

    Returns one entry per registered agent in the loaded config plus the
    built-in 'personalclaw' provider, deduplicated. The frontend uses the
    .name field to populate the agent-provider dropdown on the Agents page.
    """
    cfg = AppConfig.load()
    names: list[str] = []
    seen: set[str] = set()
    for agent_cfg in cfg.agents.values():
        provider = getattr(agent_cfg, "provider_agent", "") or "personalclaw"
        if provider and provider not in seen:
            seen.add(provider)
            names.append(provider)
    if "personalclaw" not in seen:
        names.append("personalclaw")
    return web.json_response([{"name": n} for n in names])


async def api_slash_commands(request: web.Request) -> web.Response:
    """GET /api/slash-commands — the slash commands the composer "/" menu offers.

    Returns only the dashboard-handled set (_SLASH_COMMAND_HINTS), in menu order.
    These map to deterministic actions, so they work on any model. Other "/…"
    text stays typeable and dispatches to the native harness, but isn't advertised
    (an unrecognising model would only improvise it)."""
    return web.json_response([{"name": c, "description": d} for c, d in _SLASH_COMMAND_HINTS.items()])


async def api_agent_detail(request: web.Request) -> web.Response:
    """GET/DELETE/PATCH /api/agents/detail/{name} — view, delete, or update agent config."""
    name = request.match_info["name"]
    from personalclaw.agent import AGENTS_DIR  # noqa: F811

    # Parse body early so JSONDecodeError returns 400, not 404 from the file loop.
    patch_body = None
    if request.method == "PATCH":
        try:
            patch_body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid JSON"}, status=400)

    for f in AGENTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("name") == name or f.stem == name:
                if request.method == "DELETE":
                    if f.name in (
                        "personalclaw.json",
                        "personalclaw-lite.json",
                        "PersonalClawAICapabilities-personalclaw-lite.json",
                    ):
                        return web.json_response({"error": "cannot delete personalclaw"}, status=400)
                    f.unlink()
                    state: DashboardState = request.app["state"]
                    state.push_refresh("agents")
                    return web.json_response({"ok": True})
                if request.method == "PATCH" and patch_body is not None:
                    async with _get_config_lock():
                        data = json.loads(f.read_text(encoding="utf-8"))
                        for key in ("model", "description", "system_prompt", "approval_mode"):
                            if key in patch_body:
                                val = patch_body[key]
                                if val:
                                    data[key] = val
                                else:
                                    data.pop(key, None)
                        for key in ("skills", "tools", "triggers"):
                            if key in patch_body:
                                val = patch_body[key]
                                if isinstance(val, list):
                                    data[key] = val
                                else:
                                    data.pop(key, None)
                        f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                    state = request.app["state"]
                    state.push_refresh("agents")
                    return web.json_response({"ok": True})
                return web.json_response(data)
        except (json.JSONDecodeError, OSError):
            continue
    # No per-file agent JSON matched. The agent may instead be defined in
    # config.json's `agents` map (the native/config-defined agents the list
    # endpoint serves). Consult it so detail and list agree on model/provider/
    # persona — otherwise the chat header reads "No Model" for a configured agent.
    cfg = AppConfig.load()
    prof = (cfg.agents or {}).get(name)
    if prof is not None:
        if request.method == "GET":
            from personalclaw.agents.defaults import is_reserved_agent

            return web.json_response(
                {
                    "name": name,
                    **dataclasses.asdict(prof),
                    "reserved": is_reserved_agent(name),
                    "editable": not is_reserved_agent(name),
                    # Reserved agents are locked EXCEPT their model (swappable when
                    # the user changes active models). Non-reserved → fully editable.
                    "model_editable": True,
                }
            )
        # PATCH/DELETE on config-defined agents goes through the dedicated
        # /api/agents/{name} CRUD handlers, not this per-file editor.
        return web.json_response(
            {"error": "edit config-defined agents via /api/agents/{name}"}, status=400
        )

    # "default" built-in fallback when nothing else defines it. Surface the
    # global default model so the UI shows a real value, never an empty one.
    if name == "default":
        if request.method != "GET":
            return web.json_response({"error": "cannot modify built-in default agent"}, status=400)
        return web.json_response(
            {"name": "default", "model": getattr(cfg.agent, "model", "") or ""}
        )
    return web.json_response({"error": "not found"}, status=404)


# ── PersonalClaw Agent CRUD API ──


async def api_personalclaw_agents(request: web.Request) -> web.Response:
    """GET /api/agents — list all PersonalClaw agent definitions."""
    from personalclaw.agents.defaults import is_reserved_agent

    cfg = AppConfig.load()
    agents = [
        {
            "name": name,
            **dataclasses.asdict(agent_cfg),
            "reserved": is_reserved_agent(name),
            "editable": not is_reserved_agent(name),
            "model_editable": True,
        }
        for name, agent_cfg in cfg.agents.items()
    ]
    return web.json_response(
        {
            "agents": agents,
            "default_agent": cfg.default_agent,
        }
    )


_config_lock: asyncio.Lock | None = None
_config_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_config_lock() -> asyncio.Lock:
    """Return a config lock bound to the current event loop (Python 3.10 compat)."""
    global _config_lock, _config_lock_loop
    loop = asyncio.get_running_loop()
    if _config_lock is None or _config_lock_loop is not loop:
        _config_lock = asyncio.Lock()
        _config_lock_loop = loop
    return _config_lock


async def api_personalclaw_agents_sync(request: web.Request) -> web.Response:
    """POST /api/agents/sync — auto-sync marketplace-installed agents into config.json."""
    async with _get_config_lock():
        return await _do_agents_sync(request)


async def _do_agents_sync(request: web.Request) -> web.Response:
    cfg = AppConfig.load()
    cfg.save()
    return web.json_response({"ok": True, "synced": []})


async def api_personalclaw_agents_create(request: web.Request) -> web.Response:
    """POST /api/agents — create a new PersonalClaw agent."""

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name", "")
    if not isinstance(name, str):
        return web.json_response({"error": "name must be a string"}, status=400)
    name = name.strip()
    if not name:
        return web.json_response({"error": "Agent name is required"}, status=400)
    # Restrict to a safe character set so names can't be later interpolated
    # into filesystem paths or shell commands. Same shape as the prompt-name
    # regex.
    import re as _re
    if not _re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
        return web.json_response(
            {"error": "Agent name must match ^[a-zA-Z0-9_-]{1,64}$ (letters, digits, dashes, underscores)"},
            status=400,
        )
    async with _get_config_lock():
        cfg = AppConfig.load()
        if name in cfg.agents:
            return web.json_response({"error": f"Agent '{name}' already exists"}, status=409)
        cfg.agents[name] = AgentProfile(
            provider=body.get("provider", ""),
            provider_agent=body.get("provider_agent", ""),
            acp_mode=body.get("acp_mode", ""),
            default_dir=body.get("default_dir", ""),
            memory_store=body.get("memory_store", ""),
            description=body.get("description", ""),
            system_prompt=body.get("system_prompt", ""),
            voice=body.get("voice", ""),  # soul/voice layer (#42)
            model=body.get("model", ""),
            approval_mode=body.get("approval_mode", ""),
            skills=body.get("skills", []) if isinstance(body.get("skills"), list) else [],
            tools=body.get("tools", []) if isinstance(body.get("tools"), list) else [],
            # Triggers referenced at create time must persist too — the update
            # handler accepts them, so the create path has to match or triggers
            # chosen in the create form are silently lost until re-edit.
            triggers=[str(t) for t in body["triggers"]] if isinstance(body.get("triggers"), list) else [],
            source=body.get("source", "personalclaw"),
        )
        cfg.save()
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="agent.create",
        outcome="success",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True, "name": name})


async def api_personalclaw_agent_update(request: web.Request) -> web.Response:
    """PUT /api/agents/{name} — update a PersonalClaw agent."""

    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    from personalclaw.agents.defaults import is_reserved_agent

    # Reserved system agents stay locked EXCEPT for the model field: the user may
    # swap which model a built-in agent runs on (e.g. after changing their active
    # models), but cannot touch its persona/tools/skills/triggers which the
    # system relies on. A body that only sets ``model`` is allowed through.
    if is_reserved_agent(name):
        editable_keys = {"model"}
        if not set(body.keys()).issubset(editable_keys):
            return web.json_response(
                {"error": f"'{name}' is a built-in system agent; only its model can be changed"},
                status=403,
            )
    async with _get_config_lock():
        cfg = AppConfig.load()
        if name not in cfg.agents:
            return web.json_response({"error": f"Agent '{name}' not found"}, status=404)
        agent = cfg.agents[name]
        changed: list[str] = []
        if "provider" in body:
            agent.provider = body.get("provider", "")
            changed.append("provider")
        if "provider_agent" in body:
            agent.provider_agent = body.get("provider_agent", "")
            changed.append("provider_agent")
        if "acp_mode" in body:
            agent.acp_mode = body.get("acp_mode", "")
            changed.append("acp_mode")
        if "default_dir" in body:
            agent.default_dir = body["default_dir"]
            changed.append("default_dir")
        if "memory_store" in body:
            agent.memory_store = body["memory_store"]
            changed.append("memory_store")
        if "description" in body:
            agent.description = body["description"]
            changed.append("description")
        if "system_prompt" in body:
            agent.system_prompt = body["system_prompt"]
            changed.append("system_prompt")
        if "voice" in body:  # soul/voice layer (#42)
            agent.voice = body["voice"]
            changed.append("voice")
        if "model" in body:
            agent.model = body["model"]
            changed.append("model")
        if "approval_mode" in body:
            agent.approval_mode = body["approval_mode"]
            changed.append("approval_mode")
        if "skills" in body and isinstance(body["skills"], list):
            agent.skills = body["skills"]
            changed.append("skills")
        if "tools" in body and isinstance(body["tools"], list):
            agent.tools = body["tools"]
            changed.append("tools")
        if "triggers" in body and isinstance(body["triggers"], list):
            # Referenced lifecycle-trigger IDs — the only triggers that fire for this agent.
            agent.triggers = [str(t) for t in body["triggers"]]
            changed.append("triggers")
        if "source" in body:
            agent.source = body["source"]
            changed.append("source")
        cfg.save()
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="agent.update",
        outcome="success",
        source="dashboard",
        resources=f"{name} ({','.join(changed)})",
    )
    return web.json_response({"ok": True, "name": name})


async def api_personalclaw_agent_delete(request: web.Request) -> web.Response:
    """DELETE /api/agents/{name} — delete a PersonalClaw agent."""

    name = request.match_info["name"]
    from personalclaw.agents.defaults import is_reserved_agent

    if is_reserved_agent(name):
        return web.json_response(
            {"error": f"'{name}' is a built-in system agent and cannot be deleted"},
            status=403,
        )
    async with _get_config_lock():
        cfg = AppConfig.load()
        if name not in cfg.agents:
            return web.json_response({"error": f"Agent '{name}' not found"}, status=404)
        if name == cfg.default_agent:
            return web.json_response(
                {"error": f"Cannot delete default agent '{name}'. Change default_agent first."},
                status=409,
            )
        del cfg.agents[name]
        cfg.save()
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="agent.delete",
        outcome="success",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True})


# ── Agent metadata ──────────────────────


def _regen_orchestrator() -> None:
    """Regenerate orchestrator skill after metadata or agent roster changes."""
    try:
        cfg = AppConfig.load()
        if not cfg.agent.orchestrator_skill:
            return
        from personalclaw.orchestrator_skill import generate_orchestrator_skill  # noqa: F811
        from personalclaw.skills import SkillsLoader  # noqa: F811

        generate_orchestrator_skill(SkillsLoader())
    except Exception:
        logger.exception("Failed to regenerate orchestrator skill")


async def api_agent_metadata_get(request: web.Request) -> web.Response:
    """GET /api/agent-metadata/{name} — read agent routing metadata."""
    name = request.match_info["name"]
    from personalclaw.agent_metadata import load  # noqa: F811

    content = load(name)
    return web.json_response({"name": name, "content": content})


async def api_agent_metadata_put(request: web.Request) -> web.Response:
    """PUT /api/agent-metadata/{name} — write agent routing metadata."""
    caller = request.get("user", "")
    if not caller:
        try:
            _sel().log_api_access(
                caller="anonymous",
                operation="agent_metadata.put",
                outcome="denied",
                source="dashboard",
                resources="unauthenticated",
            )
        except Exception:
            logger.warning("SEL logging failed", exc_info=True)
        return web.json_response({"error": "authentication required"}, status=401)
    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    content = body.get("content", "").strip()
    if not content:
        return web.json_response({"error": "content required"}, status=400)
    from personalclaw.agent_metadata import save  # noqa: F811

    save(name, content)
    _regen_orchestrator()
    try:
        _sel().log_api_access(
            caller=caller, operation="agent_metadata.put", outcome="ok", resources=name
        )
    except Exception:
        logger.warning("SEL logging failed", exc_info=True)
    return web.json_response({"ok": True, "name": name})


async def api_agent_metadata_delete(request: web.Request) -> web.Response:
    """DELETE /api/agent-metadata/{name} — delete agent routing metadata."""
    caller = request.get("user", "")
    if not caller:
        try:
            _sel().log_api_access(
                caller="anonymous",
                operation="agent_metadata.delete",
                outcome="denied",
                source="dashboard",
                resources="unauthenticated",
            )
        except Exception:
            logger.warning("SEL logging failed", exc_info=True)
        return web.json_response({"error": "authentication required"}, status=401)
    name = request.match_info["name"]
    from personalclaw.agent_metadata import delete  # noqa: F811

    delete(name)
    _regen_orchestrator()
    try:
        _sel().log_api_access(
            caller=caller, operation="agent_metadata.delete", outcome="ok", resources=name
        )
    except Exception:
        logger.warning("SEL logging failed", exc_info=True)
    return web.json_response({"ok": True, "name": name})
