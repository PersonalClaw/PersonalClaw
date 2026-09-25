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

from personalclaw.config import loader as config_loader
from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value
from personalclaw.config.loader import AgentProfile, AppConfig, resolve_agent_config_path
from personalclaw.config.schema import SCHEMA_REGISTRY, config_entry_to_dict
from personalclaw.dashboard.chat_utils import _SLASH_COMMAND_HINTS
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.request_validation import json_object_body, string_field


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


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
    from personalclaw.agent import AGENT_FILENAME, agents_dir  # noqa: F811

    return agents_dir() / AGENT_FILENAME


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
            # Raw text is diagnostics for the log; the wire speaks guidance (failure_copy).
            logger.exception("agent config apply failed")
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
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
                    {
                        "error": f"Unknown agent {name!r}. Create it first (Agents), or pick an "
                        f"existing one. Known: {sorted(known)}"
                    },
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
    return web.json_response(
        [{"name": c, "description": d} for c, d in _SLASH_COMMAND_HINTS.items()]
    )


async def api_agent_detail(request: web.Request) -> web.Response:
    """GET/DELETE/PATCH /api/agents/detail/{name} — view, delete, or update agent config."""
    name = request.match_info["name"]
    from personalclaw.agent import agents_dir  # noqa: F811

    # Parse body early so JSONDecodeError returns 400, not 404 from the file loop.
    patch_body = None
    if request.method == "PATCH":
        try:
            patch_body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid JSON"}, status=400)
        # 🔴 …and validate the SHAPE, which seven sibling handlers in this file already do
        # (`:223`, `:278`, `:363`, `:418`, `:741`, `:812`, `:974`). This one did not, so a scalar
        # body answered a bare 500 — and `null` was worse than that: the PATCH branch is gated on
        # `patch_body is not None`, so `null` skipped the mutation entirely and the caller got a
        # 200 with a GET response body for a mutating request (#427).
        if not isinstance(patch_body, dict):
            return json_error("invalid_body", message="JSON body must be an object", status=400)
        # 🔴 …and the VALUES, with the same table the two `/api/agents` write paths use. This
        # path writes `personalclaw.json` — the runtime config the ACP agent reads at boot —
        # and it type-checked nothing: `{"description": 12345}` persisted an int where a
        # string is declared, `{"tools": [{"a": 1}, 5]}` persisted objects into a list of
        # server names, and a 2000-deep value was a raw `RecursionError` 500 out of
        # `_atomic_json_write`'s `json.dumps`. Same defect class as #349's create/update half,
        # one function away from the guard that fixed the list fields for #427.
        #
        # Before the file loop, so a malformed body is refused whether or not the agent
        # exists, and so nothing is read or written before the refusal.
        try:
            patch_staged = _staged_agent_fields(patch_body, _AGENT_DETAIL_PATCH_KEYS)
        except ConfigValueError as exc:
            return _agent_write_refusal(exc)

    for f in agents_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("name") == name or f.stem == name:
                if request.method == "DELETE":
                    # 🔴 Guard on the RESERVED NAME, not on the filename. This listed three
                    # filenames against a five-name reserved set (`RESERVED_AGENT_NAMES`), covering
                    # only `personalclaw-lite` — and it cannot be authoritative in principle either,
                    # because the match two lines up tests the file's INTERNAL `name` field as
                    # well as its stem, so a filename allowlist cannot be authoritative
                    # regardless. The sibling `DELETE /api/agents/{name}`
                    # already does this correctly (`is_reserved_agent`), and answering 403 rather
                    # than 400 matches it: the request is well-formed and refused, not malformed.
                    #
                    # Latent today only because `agents_dir()` holds one file, so the four unguarded
                    # reserved names 404 — the ABSENCE of a file is what protects them, not the
                    # guard. Any flow that materializes a reserved agent as a per-file JSON (a
                    # marketplace activate, an app, a restored snapshot) makes it deletable here
                    # while the UI's own route refuses. This removes the class, not the instance.
                    from personalclaw.agents.defaults import is_reserved_agent

                    if is_reserved_agent(name) or f.name == "personalclaw.json":
                        # STRUCTURED, and it SHRINKS the flat population rather than merely not
                        # growing it: this refusal replaced a flat one, so converting it is free.
                        return json_error(
                            "forbidden",
                            message=(f"'{name}' is a built-in system agent and cannot be deleted"),
                            status=403,
                        )
                    f.unlink()
                    state: DashboardState = request.app["state"]
                    state.push_refresh("agents")
                    _sel().log_api_access(
                        caller=request.get("user", "dashboard"),
                        operation="agent.detail_delete",
                        outcome="success",
                        source="dashboard",
                        resources=name,
                    )
                    return web.json_response({"ok": True})
                if request.method == "PATCH" and patch_body is not None:
                    async with _get_config_lock():
                        data = json.loads(f.read_text(encoding="utf-8"))
                        for key in ("model", "description", "system_prompt", "approval_mode"):
                            if key in patch_staged:
                                val = patch_staged[key]
                                if val:
                                    data[key] = val
                                else:
                                    data.pop(key, None)
                        for key in ("skills", "tools", "triggers"):
                            if key in patch_staged:
                                val = patch_staged[key]
                                if isinstance(val, list):
                                    data[key] = val
                                # 🔴 A wrong TYPE is not a delete instruction. `else: data.pop(key)`
                                # treated one as the other, so `{"tools": "@personalclaw-core"}`
                                # DROPPED the field and answered `{"ok": true}` — disarming the live
                                # agent's whole MCP tool surface, on the one agent that always
                                # exists
                                # as a per-file JSON (`personalclaw.json`, the runtime config the
                                # ACP
                                # agent reads). Measured: `tools` went from two servers to absent
                                # (#427).
                                #
                                # Both siblings already ignore a non-list rather than acting on it —
                                # the create path coerces (`:780`) and `PUT /api/agents/{name}`
                                # guards with `isinstance` (`:869`). Ignoring is what makes this a
                                # defect rather than a convention. Clearing a list stays
                                # expressible,
                                # by sending `[]`.
                        # Atomic, via the SAME writer `agent.py` and `apps/mcp_bridge.py` use for
                        # this exact file. A bare `write_text` truncates the live runtime config if
                        # the process dies mid-write, and this is the file the agent reads at boot.
                        from personalclaw.agent import _atomic_json_write

                        _atomic_json_write(f, data)
                    state = request.app["state"]
                    state.push_refresh("agents")
                    _sel().log_api_access(
                        caller=request.get("user", "dashboard"),
                        operation="agent.detail_update",
                        outcome="success",
                        source="dashboard",
                        resources=f"{name}:{','.join(sorted(patch_body))}",
                    )
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
                    # A reserved agent is model-only: everything but its model is
                    # locked. That narrowing is enforced by the client off `reserved`
                    # alone (`AgentDetail.tsx` → `isReservedAgent`), so there is no
                    # separate model-editability field on the wire to disagree with it.
                    "editable": not is_reserved_agent(name),
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


def _resolve_agent_name(name: str, cfg) -> str | None:
    """Case-insensitively resolve an agent name against the loaded config."""
    name_lower = name.lower()
    for existing in cfg.agents:
        if existing.lower() == name_lower:
            return existing
    return None


# ── The one validator every agent write passes through (#349) ──
#
# Bounds are deliberately NON-BINDING. This is a TYPE fix, not a size policy: #349
# records a 200 KB `system_prompt` and a 100 000-element `triggers` list that both
# persist correctly today, so a cap that refused either would be a new refusal
# smuggled in under a type fix. `coerce_edit_value` requires *some* bound (its
# defaults — 256 chars, 20 items — are sized for the Settings fields it was written
# for, not for an agent persona), so these say "effectively unbounded" out loud
# instead of silently inheriting a cap nobody chose for this surface.
_AGENT_TEXT_MAX_LEN = 4_000_000
_AGENT_LIST_MAX_ITEMS = 1_000_000

#: The write-side allowlist for an agent profile: every field of
#: :class:`~personalclaw.config.loader.AgentProfile`, spelled in the ``_EDITABLE_CONFIG``
#: spec shape so it is checked by the SAME
#: :func:`~personalclaw.config.edit_spec.coerce_edit_value` every other config write path
#: already uses. This is a table, not a second validator — the rules stay that function's.
#:
#: Why it exists: the three LIST fields were guarded (``isinstance(..., list)``) and the
#: thirteen SCALAR fields were not, so ``{"description": 12345}`` answered 200 and an int
#: landed in ``config.json`` where the schema declares a string. The loader notices on
#: every subsequent load and logs "using default" — which never happens, because
#: ``config/validation.py``'s ``_apply_field_default`` documents itself as handling
#: one-level paths and ``agents.<name>.<field>`` is three segments. So one bad write bought
#: an unbounded warning flood (#349 B). Storing something other than what was declared is
#: worse than refusing it: the caller was told it succeeded, so nothing ever looks wrong —
#: the reasoning ``config/edit_spec.py``'s module docstring already spells out.
#:
#: ``tests/test_agent_write_validation.py`` pins this table against
#: ``dataclasses.fields(AgentProfile)``, so a new profile field cannot be added without a
#: spec, and cannot drift from the JSON Schema the loader reads the same field back with.
_AGENT_FIELD_SPECS: dict[str, dict] = {
    "provider": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "provider_agent": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "acp_mode": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "default_dir": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "memory_store": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "description": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "system_prompt": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "voice": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    # A real boolean, not `bool(value)`. `bool("false")` is True, so coercing here would
    # turn a request to switch a behaviour OFF into one that switches it ON — the exact
    # defect `config/edit_spec.py` was created to stop re-deriving.
    "natural_voice": {"type": "bool"},
    "model": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "approval_mode": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "skills": {"type": "str_list", "max_items": _AGENT_LIST_MAX_ITEMS},
    "tools": {"type": "str_list", "max_items": _AGENT_LIST_MAX_ITEMS},
    "triggers": {"type": "str_list", "max_items": _AGENT_LIST_MAX_ITEMS},
    "source": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "specialty": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "route_hints": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
}

#: The keys ``PATCH /api/agents/detail/{name}`` applies to the per-file runtime config.
#: A subset of :data:`_AGENT_FIELD_SPECS`, so that path validates with the same table
#: rather than growing a dialect for the same seven fields.
_AGENT_DETAIL_PATCH_KEYS = (
    "model",
    "description",
    "system_prompt",
    "approval_mode",
    "skills",
    "tools",
    "triggers",
)

#: How deep a value in an agent write body may nest. Every field above is a scalar or a
#: list of strings — depth 2 — so nothing legitimate comes close. The bound exists because
#: both writers this handler family reaches RECURSE: ``cfg.save()`` goes through
#: ``dataclasses.asdict`` and ``json.dumps``, so a 1200-deep body was a raw
#: ``RecursionError`` 500 out of the writer with a traceback where a 400 belongs (#349 A).
#: 12 is the bound ``workflows/validator.py`` already uses for a nested spec tree.
_MAX_AGENT_BODY_DEPTH = 12


def _agent_value_too_deep(value: Any) -> bool:
    """True when *value* nests deeper than :data:`_MAX_AGENT_BODY_DEPTH`.

    ITERATIVE, so its safety does not depend on the cap's VALUE. A recursive walker happens
    to be safe at 12 (it returns at the cap, so it can only be ~12 frames deep), which is
    exactly what makes the recursive form a trap: raise the cap and the guard becomes the
    thing that raises ``RecursionError``. An explicit stack cannot acquire that coupling.
    """
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_AGENT_BODY_DEPTH:
            return True
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return False


def _staged_agent_fields(body: dict, keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Return the agent-profile fields present in *body*, validated and coerced.

    Only keys actually present are returned, so a caller can tell "set this to empty" from
    "leave it alone" — which is what makes one helper serve both the create path (absent →
    dataclass default) and the two update paths (absent → unchanged).

    Raises :class:`~personalclaw.config.edit_spec.ConfigValueError` whose message NAMES the
    field; the caller renders it as a 4xx. Nothing is written before it raises, so a refused
    body cannot leave a half-applied profile behind.
    """
    staged: dict[str, Any] = {}
    for key in keys if keys is not None else tuple(_AGENT_FIELD_SPECS):
        if key not in body:
            continue
        value = body[key]
        # Depth FIRST, and once for the whole value rather than per field. Two reasons it
        # cannot be folded into the type checks: the crash reproduced through
        # `tools: [<2000-deep>]` as well as through a scalar, so a scalars-only guard would
        # not have closed it; and `coerce_edit_value` interpolates the offending value into
        # its message, where `repr()` of a deep structure recurses in turn.
        if _agent_value_too_deep(value):
            raise ConfigValueError(
                f"{key} is nested more than {_MAX_AGENT_BODY_DEPTH} levels deep",
                f"{key}=<nested>",
            )
        try:
            staged[key] = coerce_edit_value(key, value, _AGENT_FIELD_SPECS[key])
        except ConfigValueError as exc:
            # The message names the FIELD: a body can carry seventeen of them, so a bare
            # "must be a string" would not say which one was refused. Same reason
            # `PUT /api/config/personalclaw` prefixes its own.
            raise ConfigValueError(f"{key} {exc}", exc.resources, exc.status) from None
    return staged


def _agent_write_refusal(exc: ConfigValueError) -> web.Response:
    """Render a staged-field rejection as a structured 4xx naming the field.

    ``exc.status`` is 400 for every rule ``_AGENT_FIELD_SPECS`` uses;
    ``test_no_spec_uses_a_type_coerce_edit_value_does_not_support`` is what keeps
    ``coerce_edit_value``'s 500 branch (an unrecognised spec ``type``) unreachable from here.
    """
    return json_error("invalid_request", message=str(exc), status=exc.status)


def _unavailable_agent_name(name: str) -> str | None:
    """Why *name* may not be CREATED, or None when it is free.

    Two name classes, one answer, because both produced a lying ``{"ok": true}``:

    * **Retired** (``RETIRED_AGENT_NAMES``) — the config migration PRUNES these on the very
      next load, so ``POST {"name": "personalclaw-autonomous"}`` answered 200 with the name
      echoed back and the agent then did not exist (404 on its detail route). That is the
      exact failure ``api_default_agent`` guards against a few hundred lines up, with the
      same reasoning written out there.
    * **Reserved** (``is_reserved_agent``) — protected only by the seeding migration having
      ALREADY run. On a config with no ``agents`` map yet (a first-run install), create
      answered 200 for ``personalclaw-lite``; seeding is add-if-MISSING, so it then never
      overwrote the caller's profile, and the background chore worker ran with the caller's
      ``system_prompt`` and model. PUT and DELETE both answer 403 for a reserved name, so
      the impostor was also un-editable and un-deletable. The ABSENCE of the seeded profile
      was the protection, not a guard — the same shape as this file's own PATCH-delete
      note (#349 C).

    Answering 403 matches the two siblings that already refuse a reserved agent
    (``PUT``/``DELETE /api/agents/{name}``): the request is well-formed and refused, not
    malformed.
    """
    from personalclaw.agents.defaults import RETIRED_AGENT_NAMES, is_reserved_agent

    if is_reserved_agent(name):
        return f"'{name}' is a built-in system agent name and cannot be created"
    if name.lower() in {n.lower() for n in RETIRED_AGENT_NAMES}:
        return (
            f"'{name}' is a retired system agent name — the next config load prunes it, "
            "so creating it would report success and leave nothing behind"
        )
    return None


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


#: The canonical agent-name validator, shared by every path that can INTRODUCE a name into
#: ``cfg.agents`` — ``POST /api/agents`` and the file-store sync below. Mirrors
#: ``agents.marketplace._NAME_RE``, and is a constant rather than a literal inlined in the
#: create handler because a name arriving from a FILE needs exactly the same guard as one
#: arriving in a request body: the create path's own comment is "so names can't be later
#: interpolated", and a JSON file on disk is not a more trustworthy source than a POST.
_AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: Filenames under ``agents_dir()`` that are NOT agent profiles.
#:
#: ``personalclaw.json`` (:data:`~personalclaw.agent.AGENT_FILENAME`) is the ACP RUNTIME
#: config ``rebuild_agent_config()`` writes — ``mcpServers``, ``hooks``, a ``prompt`` URI,
#: and a ``tools`` list of *MCP server refs* (``@personalclaw-core``), not tool-name
#: patterns. Folding it in as a profile would copy those refs into an ``AgentProfile.tools``
#: that means something else. The sibling DELETE path already special-cases this same
#: filename (``:591``), so the rule is this file's, not a new one.
_NON_PROFILE_AGENT_FILES = frozenset({"personalclaw.json"})

#: The profile fields ``POST /api/agents/sync`` folds in from a file-store definition.
#: A subset of :data:`_AGENT_FIELD_SPECS` — every key a per-file agent JSON or an
#: ``AgentDefinition`` actually carries — so the sync validates with the SAME table the
#: three write paths use rather than trusting a hand-edited file on disk. ``source`` is
#: excluded deliberately: the sync STAMPS it (see :func:`_do_agents_sync`) rather than
#: letting the file claim its own origin.
_AGENT_SYNC_KEYS = (
    "provider",
    "provider_agent",
    "acp_mode",
    "default_dir",
    "memory_store",
    "description",
    "system_prompt",
    "voice",
    "natural_voice",
    "model",
    "approval_mode",
    "skills",
    "tools",
    "triggers",
    "specialty",
    "route_hints",
)


def _file_store_agents() -> tuple[list[tuple[str, dict]], list[str]]:
    """Every agent that exists as a FILE under ``agents_dir()``, with its raw fields.

    ``agents_dir()`` holds TWO on-disk layouts, and both are invisible to
    ``GET /api/agents`` (which lists ``cfg.agents`` alone):

    * ``agents_dir()/<name>.json`` — the flat per-file layout. ``PATCH``/``DELETE
      /api/agents/detail/{name}`` (``:570``), ``chat_persistence`` (``:48``),
      ``session.py`` (``:923``) and ``skills.py`` (``:86``) all read it, and ``:584``'s
      own comment names what materializes one: *"a marketplace activate, an app, a
      restored snapshot."*
    * ``agents_dir()/<name>/agent.json`` — the local agent marketplace's layout
      (``agents.marketplace.LocalAgentMarketplace._agent_path``). Read through the
      REGISTRY rather than by globbing the directory, so a marketplace that stores its
      definitions elsewhere is enumerated by its own ``list()``. This is the store the
      old docstring's *"marketplace-installed agents"* meant.

    Returns ``(entries, unreadable)`` — entries as ``(name, fields)`` in the order they
    were found, first occurrence winning; ``unreadable`` names the files that could not be
    parsed, so the response can say so instead of silently reporting a smaller scan.
    """
    from personalclaw.agent import agents_dir  # noqa: F811
    from personalclaw.agents.marketplace import get_default_agent_registry

    entries: list[tuple[str, dict]] = []
    unreadable: list[str] = []
    seen: set[str] = set()

    def _add(name: str, fields: dict) -> None:
        key = name.lower()
        if not name or key in seen:
            return
        seen.add(key)
        entries.append((name, fields))

    try:
        files = sorted(agents_dir().glob("*.json"))
    except OSError:
        files = []
    for f in files:
        if f.name in _NON_PROFILE_AGENT_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            unreadable.append(f.name)
            continue
        if not isinstance(data, dict):
            unreadable.append(f.name)
            continue
        # Name by the file's own `name` field, falling back to the stem — the SAME rule
        # the four other readers of this layout use, so detail/list/sync agree on the id.
        _add(str(data.get("name") or f.stem), data)

    registry = get_default_agent_registry()
    for mp_name in registry.names():
        try:
            definitions = registry.get(mp_name).list()
        except Exception:
            logger.debug("agents sync: marketplace %r listing failed", mp_name, exc_info=True)
            unreadable.append(f"marketplace:{mp_name}")
            continue
        for defn in definitions:
            _add(defn.name, defn.to_dict())

    return entries, unreadable


def _reportable_agent_name(name: str) -> str:
    """*name* clipped to a length safe to echo back in a sentence and an audit row.

    Only ever shortens a name the sync REFUSED — an accepted one already passed
    :data:`_AGENT_NAME_RE`, whose own bound is 63 characters, so a legal name is returned
    untouched. A refused one came out of a JSON file's ``name`` field and is unbounded, and it
    reaches a toast, a response body and a SEL ``resources`` column; echoing it verbatim would
    let one file on disk decide the size of all three.
    """
    return name if len(name) <= 63 else name[:60] + "…"


def _sync_sentence(synced: list[str], skipped: list[str], unreadable: list[str]) -> str:
    """The one sentence the Agents header reports after a sync.

    Composed HERE, not on the client: the outcome has four independent parts (what was
    added, what was refused and why, what could not be read, and the "nothing to do" case),
    and a client re-deriving that from three arrays is a second author for the same
    sentence. The frontend renders this verbatim.
    """
    parts: list[str] = []
    if synced:
        noun = "agent" if len(synced) == 1 else "agents"
        parts.append(f"Added {len(synced)} {noun} from your agent files: {', '.join(synced)}.")
    else:
        parts.append("Already up to date — no agent files were missing from your config.")
    if skipped:
        parts.append(f"Skipped {len(skipped)}: {', '.join(skipped)}.")
    if unreadable:
        parts.append(f"Could not read {len(unreadable)}: {', '.join(unreadable)}.")
    return " ".join(parts)


async def api_personalclaw_agents_sync(request: web.Request) -> web.Response:
    """POST /api/agents/sync — fold file-store agents into config.json and report what it did.

    ``agents_dir()`` is a real second agent store (see :func:`_file_store_agents`) and this is
    the ONLY endpoint that reconciles it with ``config.json``'s ``agents`` map, which
    ``GET /api/agents`` serves. An agent that arrives as a file — from the Store, from an app
    bundle, from a restored snapshot — is therefore invisible everywhere in the UI until this
    runs (#344).
    """
    async with _get_config_lock():
        return await _do_agents_sync(request)


async def _do_agents_sync(request: web.Request) -> web.Response:
    cfg = AppConfig.load()
    entries, unreadable = _file_store_agents()
    synced: list[str] = []
    skipped: list[str] = []
    for name, fields in entries:
        # Already in config.json — case-insensitively, the same resolution the CRUD paths
        # use, so `personalclaw.json`'s "personalclaw" matches the seeded "PersonalClaw"
        # instead of folding a duplicate profile in beside it.
        if _resolve_agent_name(name, cfg) is not None:
            continue
        # The SAME name guard `POST /api/agents` applies, for the same stated reason ("so
        # names can't be later interpolated"). A file's `name` field is attacker-shaped input
        # on the install path — an app bundle or a restored snapshot writes it — so a
        # traversal-looking or unbounded name must not become a config key here either.
        if not _AGENT_NAME_RE.fullmatch(name):
            skipped.append(_reportable_agent_name(name))
            continue
        # The two name classes a CREATE already refuses, refused here for the same reasons
        # (`_unavailable_agent_name`): a RESERVED name is owned by the seeding migration, and
        # a RETIRED one is pruned by the very next config load — so folding either in would
        # report success and leave nothing behind.
        if _unavailable_agent_name(name) is not None:
            skipped.append(_reportable_agent_name(name))
            continue
        try:
            staged = _staged_agent_fields(fields, _AGENT_SYNC_KEYS)
        except ConfigValueError as exc:
            # A file on disk is not a request: one bad field must not fail the whole sync,
            # and it must not persist a wrong type into config.json either (the #349 defect
            # class). Skip the file, name it in the response.
            logger.info("agents sync: skipping %r — %s", name, exc)
            skipped.append(_reportable_agent_name(name))
            continue
        # `source` is STAMPED, never read off the file: it records where PersonalClaw found
        # the profile, and a file that named its own origin could claim "builtin".
        cfg.agents[name] = AgentProfile(**staged, source="local")
        synced.append(name)
    # Only write when something changed. The old body was an unconditional `load(); save()`,
    # which rewrote the user's whole config.json on every press to bump `lastTouchedAt` — a
    # full-file write of the live config in answer to a control that reported nothing.
    if synced:
        cfg.save()
        state: DashboardState = request.app["state"]
        state.push_refresh("agents")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="agents.sync",
        outcome="success",
        source="dashboard",
        resources=f"synced={','.join(synced) or '-'} skipped={','.join(skipped) or '-'}",
    )
    return web.json_response(
        {
            "ok": True,
            # A LIST of names, not a count — the deliverable is visibility, so the response
            # says WHICH agents just became visible. `web/src/lib/api.ts` typed this
            # `number` while the server has always returned a list; the type is what moved.
            "synced": synced,
            "skipped": skipped,
            "unreadable": unreadable,
            "scanned": len(entries),
            "message": _sync_sentence(synced, skipped, unreadable),
        }
    )


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
    name = name.lower()

    if not _AGENT_NAME_RE.fullmatch(name):
        return web.json_response(
            {
                "error": f"Agent name must match {_AGENT_NAME_RE.pattern} (lowercase letters, digits, dashes, no leading dash)"  # noqa: E501
            },
            status=400,
        )
    # Validate BEFORE taking the lock: this depends only on the request body and the spec
    # table, so holding the lock across it would serialise every rejected request behind
    # whoever is writing, for no benefit. Same placement as `PUT /api/config/personalclaw`.
    try:
        staged = _staged_agent_fields(body)
    except ConfigValueError as exc:
        return _agent_write_refusal(exc)

    async with _get_config_lock():
        cfg = AppConfig.load()
        if _resolve_agent_name(name, cfg):
            return web.json_response({"error": f"Agent '{name}' already exists"}, status=409)
        # A name the system owns is refused BEFORE the write, not reconciled after it.
        # AFTER the duplicate check deliberately: when the name IS already in the config,
        # "already exists" is the more specific answer, and it is what the case-insensitive
        # conflict rail asserts for the reserved agents that are normally seeded. This branch
        # is what the duplicate check cannot cover — a name that is NOT in the config yet and
        # must never be written under (see `_unavailable_agent_name`).
        unavailable = _unavailable_agent_name(name)
        if unavailable:
            return json_error("forbidden", message=unavailable, status=403)
        # Every field comes from the ONE validated table, and the dataclass defaults fill
        # whatever the body omitted. The seventeen hand-written `body.get(...)` calls this
        # replaces had a parallel seventeen in the update handler, which is how `triggers`
        # came to be accepted by one path and dropped by the other, and how thirteen scalar
        # fields came to skip the guard their three list siblings had.
        cfg.agents[name] = AgentProfile(**staged)
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
    # Same table, same coercion, same placement (before the lock) as create — so the pair
    # cannot drift again. This also RETIRES the list fields' silent ignore: a wrong-typed
    # `skills`/`tools`/`triggers` used to answer 200 having changed nothing, and being told a
    # write succeeded when it did not is the failure `config/edit_spec.py` exists to stop.
    # Clearing a list stays expressible, by sending `[]`.
    try:
        staged = _staged_agent_fields(body)
    except ConfigValueError as exc:
        return _agent_write_refusal(exc)

    async with _get_config_lock():
        cfg = AppConfig.load()
        if name not in cfg.agents:
            return web.json_response({"error": f"Agent '{name}' not found"}, status=404)
        agent = cfg.agents[name]
        # `_staged_agent_fields` walks `_AGENT_FIELD_SPECS` in order, so this audit list is
        # deterministic rather than request-order dependent.
        changed: list[str] = []
        for field_name, value in staged.items():
            setattr(agent, field_name, value)
            changed.append(field_name)
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
    # The PARENT is the agent (#2995). This door checked auth and the body shape, then wrote
    # routing metadata for an agent that does not exist — answering `{"ok": true, "name": ...}`
    # and leaving an `.md` file that is in the durability inventory (so it rides into snapshots
    # and sync) and that no surface lists or reaps. Measured with zero agents installed: a PUT to
    # `no-such-agent-zz9` reported success and the note was simply gone, because the only reader
    # (`orchestrator_skill`) walks `cfg.agents` and never sees a file keyed to a name that is not
    # one. A misspelling in the metadata editor silently discarded the user's text.
    #
    # EXACT membership, deliberately, not `_resolve_agent_name`'s case-insensitive resolve: the
    # reader looks these notes up by the literal `cfg.agents` key, so admitting a case variant
    # would save a file no reader can find — the same lost write this refusal exists to stop.
    # Same predicate as the sibling write doors `PUT`/`DELETE /api/agents/{name}`, and the same
    # `not_found` code its own `DELETE .../metadata` sibling answers, so the family cannot drift.
    #
    # The DELETE sibling deliberately does NOT gain this check: it 404s on a missing FILE, which
    # is what lets an orphaned note left by an agent that has since been deleted still be cleaned
    # up. Requiring the agent there would strand exactly the files this door stops creating.
    if name not in AppConfig.load().agents:
        return json_error(
            "not_found", message=f"No agent named {name!r} is configured.", status=404
        )
    body = await json_object_body(request)
    content = string_field(body, "content")
    from personalclaw.agent_metadata import delete, save  # noqa: F811

    if not content:
        # Clearing the field is the natural way to say "this agent has no routing
        # note" — the empty state is already supported everywhere else (load()
        # returns "" for a missing file), and only this write path forbade
        # producing it, so an emptied editor 400'd 'content required' (#668).
        # An empty PUT now clears the stored note; the file is removed so the
        # canonical empty representation stays "absent", not littered empty .md.
        delete(name)
    else:
        save(name, content)
    _regen_orchestrator()
    try:
        _sel().log_api_access(
            caller=caller,
            operation="agent_metadata.put",
            outcome="ok",
            resources=f"{name} (cleared)" if not content else name,
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

    existed = delete(name)
    if not existed:
        # A name that was never stored must not read as a successful delete (#2936):
        # `delete()` already returns the bool the handler needs, so branch on it rather
        # than discarding it. Also stop writing a false "ok" entry to the tamper-evident
        # SEL audit log for a delete that deleted nothing — `not_found` is the registered
        # outcome word for exactly this (sel.py's `failed` family).
        try:
            _sel().log_api_access(
                caller=caller,
                operation="agent_metadata.delete",
                outcome="not_found",
                resources=name,
            )
        except Exception:
            logger.warning("SEL logging failed", exc_info=True)
        return json_error(
            "not_found", message="No agent metadata is stored under that name.", status=404
        )
    _regen_orchestrator()
    try:
        _sel().log_api_access(
            caller=caller, operation="agent_metadata.delete", outcome="ok", resources=name
        )
    except Exception:
        logger.warning("SEL logging failed", exc_info=True)
    return web.json_response({"ok": True, "name": name})
