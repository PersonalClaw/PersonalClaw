"""MCP server management handlers — probe, sync, toggle, remove."""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# Allowlist pattern for MCP server names.  Matches the convention used
# (alphanumerics, dashes, underscores, slashes, dots,
# and ``@`` for scoped names like ``@org/server``) and defends against
# command-injection into subprocess calls that pass the name as an argv
# element (e.g. `personalclaw skills mcp uninstall <name>`).
#
# The leading char must be alphanumeric or ``@`` so a name can't begin
# with ``.`` or ``/``.  Path-traversal sequences (``..``) are rejected
# separately at validation time below.
_VALID_MCP_NAME_RE = re.compile(r"^[@a-zA-Z0-9][@a-zA-Z0-9/_.-]*$")
_MAX_MCP_NAME_LEN = 128


def _is_valid_mcp_name(name: str) -> bool:
    """Return True if ``name`` is a well-formed, non-traversal MCP name."""
    if not name or len(name) > _MAX_MCP_NAME_LEN:
        return False
    if ".." in name:  # reject path traversal even if it matches the charset
        return False
    return bool(_VALID_MCP_NAME_RE.match(name))


# THE canonical MCP config. UT3 collapsed the former dual store (this handler
# used to write ``~/.personalclaw/settings/mcp.json`` while the runtime
# (mcp_client), provider instances (mcp_instances), and agent.py all read
# ``~/.personalclaw/mcp.json``) — a divergence where a server added via the
# dashboard wrote one file but the native loop read another, only reconciled
# because discovery merged both. Now everything reads+writes the ONE file, via
# config_dir() so PERSONALCLAW_HOME is honored (the old Path.home() hardcode
# ignored it). A one-time migration folds any legacy settings/mcp.json content in.
def _canonical_mcp_json() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "mcp.json"


def _legacy_mcp_json() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "settings" / "mcp.json"


def _migrate_legacy_mcp_json() -> None:
    """One-time fold of the legacy ``settings/mcp.json`` into the canonical file.

    Any server present only in the legacy file is copied into the canonical one
    (canonical wins on a name clash), then the legacy file is emptied so it can
    never re-diverge. No-op when the legacy file is absent/empty."""
    legacy = _legacy_mcp_json()
    try:
        if not legacy.is_file():
            return
        ldata = json.loads(legacy.read_text(encoding="utf-8"))
        lservers = ldata.get("mcpServers") or {}
        if not lservers:
            return
        canon = _canonical_mcp_json()
        cdata = json.loads(canon.read_text(encoding="utf-8")) if canon.is_file() else {}
        cservers = cdata.setdefault("mcpServers", {})
        moved = 0
        for name, spec in lservers.items():
            if name not in cservers:  # canonical wins on clash
                cservers[name] = spec
                moved += 1
        if moved:
            from personalclaw.agent import _atomic_json_write

            canon.parent.mkdir(parents=True, exist_ok=True)
            _atomic_json_write(canon, cdata)
            logger.info("mcp: migrated %d server(s) from legacy settings/mcp.json", moved)
        # empty the legacy file so it can't re-diverge
        from personalclaw.agent import _atomic_json_write

        _atomic_json_write(legacy, {"mcpServers": {}})
    except Exception:
        logger.debug("mcp: legacy migration skipped", exc_info=True)


_GLOBAL_MCP_JSON = _canonical_mcp_json()

# File-based lock for mcp.json — shared with bridges.py so that app
# registration and dashboard MCP handlers coordinate properly.
# Uses fcntl.flock on a sidecar .lock file (works cross-process too).
_MCP_LOCK_PATH = _GLOBAL_MCP_JSON.with_suffix(".lock")


class _McpFileLock:
    """Async context manager wrapping fcntl.flock for mcp.json."""

    async def __aenter__(self) -> None:
        import fcntl

        _GLOBAL_MCP_JSON.parent.mkdir(parents=True, exist_ok=True)
        _MCP_LOCK_PATH.touch(exist_ok=True)
        self._fd = open(_MCP_LOCK_PATH, "r")
        # Run blocking flock in a thread to avoid blocking the event loop
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: fcntl.flock(self._fd, fcntl.LOCK_EX),
        )

    async def __aexit__(self, *args: Any) -> None:
        import fcntl

        fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._fd.close()


def _get_mcp_lock() -> _McpFileLock:
    """Return an MCP config file lock (compatible with bridges.py)."""
    return _McpFileLock()


def _write_mcp_json(data: dict) -> None:
    """Atomically write global mcp.json to prevent partial reads."""
    from personalclaw.agent import (  # noqa: F811  # circular import: agent imports handlers
        _atomic_json_write,
    )

    _GLOBAL_MCP_JSON.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(_GLOBAL_MCP_JSON, data)


# ── MCP Servers ──


_mcp_probe_cache: list[dict] = []
_mcp_probe_ts: float = 0.0
_MCP_PROBE_CACHE_SECS = 600  # 10 min
_mcp_probe_in_progress = False


def _server_in_agent_config(name: str) -> bool:
    """Whether ``name`` is in the installed agent config's mcpServers — the
    ``source="agent"`` MCP servers live there, not in mcp.json, so a delete must
    check here to report honestly + actually remove them (via _sync remove)."""
    from personalclaw.dashboard.handlers.agents import _installed_agent_config

    try:
        cfg = json.loads(_installed_agent_config().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return name in (cfg.get("mcpServers") or {})


def _sync_mcp_to_agent(name: str, enabled: bool, *, remove: bool = False) -> None:
    """Sync MCP server state to personalclaw.json mcpServers (not tools/allowedTools)."""
    from personalclaw.dashboard.handlers.agents import (  # noqa: F811 circular: agents imports mcp
        _installed_agent_config,
    )

    path = _installed_agent_config()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read agent config %s, skipping sync: %s", path, exc)
        return

    if enabled and not remove:
        # Ensure server exists in personalclaw.json mcpServers when enabled
        mcp_servers = cfg.setdefault("mcpServers", {})
        tool_ref = f"@{name}"
        changed = False
        if name not in mcp_servers:
            # Copy the spec from whichever MCP store holds it (PersonalClaw scope
            # first, then legacy settings/claude-code) into the agent config.
            spec = _find_server_spec_anywhere(name)
            if spec:
                mcp_servers[name] = spec
                changed = True
            else:
                return
        # Ensure @server-name in tools and allowedTools
        for key in ("tools", "allowedTools"):
            lst = cfg.setdefault(key, [])
            if tool_ref not in lst:
                lst.append(tool_ref)
                changed = True
        if not changed:
            return
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_added",
            outcome="ok",
            source="dashboard",
            resources=f"{tool_ref} added to tools/allowedTools",
        )
    # On disable/remove, clean up any @server-name refs the user may have added
    if not enabled or remove:
        tool_ref = f"@{name}"
        cfg["tools"] = [t for t in cfg.get("tools", []) if t != tool_ref]
        cfg["allowedTools"] = [t for t in cfg.get("allowedTools", []) if t != tool_ref]
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_removed",
            outcome="ok",
            source="dashboard",
            resources=f"{tool_ref} removed from tools/allowedTools",
        )
    if remove:
        cfg.get("mcpServers", {}).pop(name, None)
    try:
        from personalclaw.agent import (  # noqa: F811 circular: agent imports handlers
            _atomic_json_write,
        )

        _atomic_json_write(path, cfg)
    except OSError as exc:
        logger.warning("Cannot write agent config %s: %s", path, exc)


def _sync_mcp_to_agent_batch(names: list[str], enabled: bool) -> None:
    """Batch sync multiple MCP servers to personalclaw.json in a single read-modify-write."""
    from personalclaw.dashboard.handlers.agents import (  # noqa: F811 circular: agents imports mcp
        _installed_agent_config,
    )

    path = _installed_agent_config()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read agent config %s, skipping batch sync: %s", path, exc)
        return

    changed = False
    if enabled:
        # Ensure all servers exist in personalclaw.json mcpServers
        mcp_servers = cfg.setdefault("mcpServers", {})
        try:
            gdata = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            gdata = {}
        for name in names:
            if name not in mcp_servers:
                spec = gdata.get("mcpServers", {}).get(name, {})
                if not isinstance(spec, dict) or not spec:
                    continue
                mcp_servers[name] = {k: v for k, v in spec.items() if k != "disabled"}
                changed = True
            # Ensure @server-name in tools and allowedTools
            tool_ref = f"@{name}"
            for key in ("tools", "allowedTools"):
                lst = cfg.setdefault(key, [])
                if tool_ref not in lst:
                    lst.append(tool_ref)
                    changed = True
        if changed:
            sel().log_api_access(
                caller="system",
                operation="mcp_tools_added",
                outcome="ok",
                source="dashboard",
                resources=f"{', '.join(f'@{n}' for n in names)} added to tools/allowedTools",
            )
    else:
        refs_to_remove = {f"@{name}" for name in names}
        cfg["tools"] = [t for t in cfg.get("tools", []) if t not in refs_to_remove]
        cfg["allowedTools"] = [t for t in cfg.get("allowedTools", []) if t not in refs_to_remove]
        changed = True
        sel().log_api_access(
            caller="system",
            operation="mcp_tools_removed",
            outcome="ok",
            source="dashboard",
            resources=f"{', '.join(sorted(refs_to_remove))} removed from tools/allowedTools",
        )
    if not changed:
        return
    try:
        from personalclaw.agent import (  # noqa: F811 circular: agent imports handlers
            _atomic_json_write,
        )

        _atomic_json_write(path, cfg)
    except OSError as exc:
        logger.warning("Cannot write agent config %s: %s", path, exc)


async def _bg_mcp_probe() -> None:
    """Background MCP probe — populates cache at startup."""
    global _mcp_probe_ts, _mcp_probe_in_progress
    try:
        from personalclaw.mcp_discovery import list_servers, probe_server  # noqa: F811

        global_mcps: dict[str, Any] = {}
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
            global_mcps = data.get("mcpServers", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        all_servers = list_servers()
        probed = await asyncio.gather(
            *(probe_server(s) for s in all_servers), return_exceptions=True
        )
        result: list[dict[str, Any]] = []
        for i, r in enumerate(probed):
            if isinstance(r, BaseException):
                s = all_servers[i]
                s.status = "error"
                s.error = str(r)[:200]
            else:
                s = r
            d = s.to_dict()
            spec = global_mcps.get(s.name, {})
            d["enabled"] = not (isinstance(spec, dict) and spec.get("disabled"))
            if isinstance(spec, dict) and spec.get("disabledTools"):
                d["disabledTools"] = spec["disabledTools"]
            result.append(d)
        _mcp_probe_cache[:] = result
        _mcp_probe_ts = time.time()
        logger.info("MCP probe complete: %d servers", len(result))
    except Exception:
        logger.debug("Background MCP probe failed", exc_info=True)
    finally:
        _mcp_probe_in_progress = False


async def api_mcp_servers(request: web.Request) -> web.Response:
    """GET /api/mcp — list configured MCP servers with enabled state.

    Reads from ``~/.personalclaw/mcp.json`` — the global MCP config.
    Agent-level ``mcpServers`` and ``includeMcpJson`` are merged at runtime.
    """
    global _mcp_probe_in_progress
    from personalclaw.mcp_discovery import list_servers  # circular import

    # Kick off a background re-probe if the handler cache is stale,
    # so the next request gets fresh results.
    now = time.time()
    should_reprobe = now - _mcp_probe_ts > _MCP_PROBE_CACHE_SECS and not _mcp_probe_in_progress

    servers = list_servers()

    # Overlay handler-level probe cache (last successful probe results)
    # so that "outdated" from the expired discovery cache is replaced with
    # the actual last-known status.  Without this, every page load after
    # 30 min shows "Outdated" even though the servers are healthy.
    cached_by_name: dict[str, dict] = {s["name"]: s for s in _mcp_probe_cache}

    # Also re-probe if a new server appeared (e.g. fresh install from
    # marketplace) so status transitions from "Unknown" to "ok"/"error" on the
    # next page refresh without waiting out the 30-min TTL.
    if not should_reprobe and not _mcp_probe_in_progress:
        for srv in servers:
            if srv.name not in cached_by_name:
                should_reprobe = True
                break

    if should_reprobe:
        _mcp_probe_in_progress = True
        state: DashboardState = request.app["state"]
        task = asyncio.create_task(_bg_mcp_probe())
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

    # Read global mcp.json for disabled state
    global_mcps: dict[str, Any] = {}
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        global_mcps = data.get("mcpServers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    result: list[dict] = []
    for s in servers:
        d = s.to_dict()
        # Prefer handler cache status over discovery cache "outdated"
        cached = cached_by_name.get(s.name)
        if cached and d["status"] in ("outdated", "unknown"):
            d["status"] = cached.get("status", d["status"])
            d["tools"] = cached.get("tools", d["tools"])
            d["error"] = cached.get("error", d["error"])
        spec = global_mcps.get(s.name, {})
        is_disabled = isinstance(spec, dict) and spec.get("disabled")
        d["enabled"] = not is_disabled
        if is_disabled:
            d["status"] = "disabled"
        err = d.get("error")
        if err:
            err, _ = redact_credentials(err)
            err, _ = redact_exfiltration_urls(err)
            d["error"] = err
        result.append(d)
    return web.json_response(result)


async def api_mcp_active(request: web.Request) -> web.Response:
    """GET /api/mcp/active — return MCP servers for the current agent.

    For non-personalclaw agents, reads ``mcpServers`` from the agent's config
    in ``~/.personalclaw/agents/`` — these are the only servers the ACP agent loads
    when ``--agent <name>`` is passed.  For personalclaw (or no agent),
    reads from global ``~/.personalclaw/mcp.json`` as before.
    """
    from personalclaw.agent import AGENTS_DIR  # noqa: F811

    agent = request.query.get("agent", "")

    # Resolve PersonalClaw agent name → provider agent name so "default" → "personalclaw".
    # A native agent (provider='native') resolves to an EMPTY provider_agent — it runs on
    # the built-in personalclaw provider and inherits the global ~/.personalclaw/mcp.json,
    # so it must take the global branch below (not the custom-file branch). Only a
    # discovered/custom agent with its own agents/<name>.json has a non-empty
    # provider_agent that names a per-agent mcpServers block. Clearing `agent` to "" when
    # the resolution is empty routes native agents (default/personalclaw-lite/…) correctly.
    if agent:
        try:
            from personalclaw.config.loader import AppConfig, resolve_agent_bindings  # noqa: F811

            cfg = AppConfig.load()
            bindings = resolve_agent_bindings(cfg, agent)
            agent = bindings.provider_agent  # "" for a native agent → falls through to global
        except Exception:
            pass

    # A custom/discovered agent (non-empty, non-personalclaw): read its per-agent config.
    if agent and agent != "personalclaw":
        for f in AGENTS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("name") == agent:
                    agent_mcps = data.get("mcpServers", {})
                    return web.json_response(
                        [{"name": n, "enabled": True} for n in sorted(agent_mcps)]
                    )
            except (json.JSONDecodeError, OSError):
                continue
        return web.json_response([])

    # Personalclaw / default: read from global mcp.json
    from personalclaw.mcp_discovery import list_servers  # noqa: F811

    global_mcps: dict[str, Any] = {}
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        global_mcps = data.get("mcpServers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    servers = list_servers()
    result: list[dict] = []
    for s in servers:
        spec = global_mcps.get(s.name, {})
        enabled = not (isinstance(spec, dict) and spec.get("disabled"))
        result.append({"name": s.name, "enabled": enabled})
    # Also include personalclaw-schedule and personalclaw-core (always enabled)
    names = {r["name"] for r in result}
    for builtin in ("personalclaw-schedule", "personalclaw-core"):
        if builtin not in names:
            result.insert(0, {"name": builtin, "enabled": True})
    return web.json_response(result)


async def api_mcp_probe(request: web.Request) -> web.Response:
    """POST /api/mcp/probe — probe all MCP servers and return live status.

    Merges ``enabled`` and ``disabledTools`` from global mcp.json so
    probe results don't reset user's previous enable/disable choices.
    """
    global _mcp_probe_ts
    from personalclaw.mcp_discovery import probe_all  # noqa: F811

    servers = await probe_all()
    # Read global mcp.json for enabled/disabledTools state
    global_mcps: dict[str, Any] = {}
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        global_mcps = data.get("mcpServers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    result: list[dict[str, Any]] = []
    for s in servers:
        d = s.to_dict()
        spec = global_mcps.get(s.name, {})
        d["enabled"] = not (isinstance(spec, dict) and spec.get("disabled"))
        if isinstance(spec, dict) and spec.get("disabledTools"):
            d["disabledTools"] = spec["disabledTools"]
        result.append(d)
    _mcp_probe_cache[:] = result
    _mcp_probe_ts = time.time()
    return web.json_response(result)


async def api_mcp_probe_one(request: web.Request) -> web.Response:
    """POST /api/mcp/probe/{name} — reconnect (re-probe) a SINGLE MCP server.

    Lets the user recover one timed-out/errored provider without re-probing the
    whole fleet (a slow server shouldn't force an all-provider re-probe). Updates
    just this server's entry in the probe cache + merges its enabled/disabledTools
    so the page reflects it immediately. 404 if no server by that name."""
    global _mcp_probe_ts
    name = request.match_info["name"].strip()
    if not name:
        return web.json_response({"error": "server name is required"}, status=400)
    from personalclaw.mcp_discovery import probe_one  # noqa: F811

    info = await probe_one(name)
    if info is None:
        return web.json_response({"error": f"no MCP server {name!r} configured"}, status=404)
    d = info.to_dict()
    # Preserve the user's enable/disabledTools choices (mirror api_mcp_probe).
    try:
        data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        spec = data.get("mcpServers", {}).get(name, {})
        d["enabled"] = not (isinstance(spec, dict) and spec.get("disabled"))
        if isinstance(spec, dict) and spec.get("disabledTools"):
            d["disabledTools"] = spec["disabledTools"]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Update just this server's row in the cache (leave the rest untouched).
    replaced = False
    for i, row in enumerate(_mcp_probe_cache):
        if row.get("name") == name:
            _mcp_probe_cache[i] = d
            replaced = True
            break
    if not replaced:
        _mcp_probe_cache.append(d)
    _mcp_probe_ts = time.time()
    return web.json_response(d)


async def api_mcp_probe_cached(request: web.Request) -> web.Response:
    """GET /api/mcp/probe — return cached probe results (non-blocking)."""
    global _mcp_probe_in_progress
    now = time.time()
    if now - _mcp_probe_ts > _MCP_PROBE_CACHE_SECS and not _mcp_probe_in_progress:
        _mcp_probe_in_progress = True
        state: DashboardState = request.app["state"]
        task = asyncio.create_task(_bg_mcp_probe())
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
    return web.json_response(_mcp_probe_cache)


async def api_mcp_pool_stats(request: web.Request) -> web.Response:
    """GET /api/mcp/pool-stats — the in-process MCP connection-pool observability tile
    (P23d): live/shared/session connection counts + lifetime spawn/reap/served/reuse
    counters. Returns ``{available:false}`` when the ``mcp`` SDK extra is absent (no
    pool exists) so the FE can show a graceful 'MCP not installed' state."""
    from personalclaw.mcp_client import get_mcp_client_registry

    reg = get_mcp_client_registry()
    if reg is None:
        return web.json_response({"available": False})
    return web.json_response({"available": True, **reg.pool_stats()})


async def api_mcp_importable(request: web.Request) -> web.Response:
    """GET /api/mcp/importable — MCP servers configured in an external backend
    (e.g. Claude Code) that aren't yet in any PersonalClaw scope.

    These are NOT loaded by PersonalClaw — the native loop can't reach a
    backend-only server. The Tools UI lists them as import suggestions; choosing
    one POSTs ``/api/mcp/apply`` with ``personalclaw: true`` to copy the spec
    into ``~/.personalclaw/mcp.json`` so it becomes a first-class PClaw server.
    """
    from personalclaw.mcp_discovery import discover_importable_servers

    try:
        servers = await asyncio.to_thread(discover_importable_servers)
    except Exception as exc:
        logger.warning("discover_importable_servers failed: %s", exc)
        servers = []
    return web.json_response({"servers": servers})


async def api_mcp_sync(request: web.Request) -> web.Response:
    """POST /api/mcp/sync — apply MCP config changes and restart sessions.

    1. Discovers new MCP servers from mcp.json sources.
    2. Adds them to both personalclaw agent config AND global mcp.json
       (ACP agent only reads the global config).
    3. Resets all sessions so changes take effect.
    """
    from personalclaw.mcp_discovery import (  # noqa: F811
        discover_servers_to_sync,
        register_servers_for_cc,
        sync_to_agent_config,
    )

    to_sync = discover_servers_to_sync()
    synced = 0
    if to_sync:
        ok = sync_to_agent_config(to_sync)
        if ok:
            synced = len(to_sync)
        register_servers_for_cc(to_sync)
        # Also add to global mcp.json (what ACP actually reads)
        async with _get_mcp_lock():
            try:
                gdata = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                gdata = {"mcpServers": {}}
            gservers = gdata.setdefault("mcpServers", {})
            for s in to_sync:
                if s.name not in gservers:
                    entry: dict[str, Any] = {"command": s.command}
                    if s.args:
                        entry["args"] = s.args
                    if s.env:
                        entry["env"] = s.env
                    gservers[s.name] = entry
            _GLOBAL_MCP_JSON.parent.mkdir(parents=True, exist_ok=True)
            _write_mcp_json(gdata)

    # Always reset sessions — even with no new servers, the user may have
    # toggled enable/disable which writes to personalclaw.json but requires
    # a session restart for the ACP agent to pick up the change.
    from personalclaw.dashboard.handlers.sessions import _reset_all_sessions  # noqa: F811

    sessions_reset = await _reset_all_sessions(request)
    return web.json_response(
        {
            "ok": True,
            "synced": synced,
            "servers": [s.name for s in to_sync],
            "sessions_reset": sessions_reset,
        }
    )


async def api_mcp_toggle(request: web.Request) -> web.Response:
    """POST /api/mcp/toggle — enable or disable an MCP server globally.

    1. Sets ``disabled`` in ``~/.personalclaw/mcp.json`` (ACP runtime).
    2. Syncs ``tools``/``allowedTools`` in ``personalclaw.json`` (non-ACP mode).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name", "").strip()
    enabled = body.get("enabled", True)
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    async with _get_mcp_lock():
        # 1. Update global mcp.json
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response({"error": "cannot parse global mcp.json"}, status=500)

        servers = data.setdefault("mcpServers", {})
        if name not in servers:
            # Server may exist in another scope (agent config, ~/.claude.json).
            # Create a stub so we can store disabled state here.
            from personalclaw.mcp_discovery import (  # circular import: mcp_discovery defers imports of personalclaw.agent which shares state with this module  # noqa: E501
                list_servers as _ls,
            )

            known = {s.name for s in _ls()}
            if name not in known:
                return web.json_response({"error": f"server {name!r} not found"}, status=404)
            servers[name] = {}

        spec = servers[name]
        if not isinstance(spec, dict):
            if isinstance(spec, str):
                servers[name] = spec = {"command": spec}
            else:
                return web.json_response(
                    {"error": f"server {name!r} has invalid config type: {type(spec).__name__}"},
                    status=500,
                )
        if enabled:
            spec.pop("disabled", None)
        else:
            spec["disabled"] = True

        try:
            _write_mcp_json(data)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

        # 2. Sync to personalclaw.json tools/allowedTools (lock prevents lost updates vs agents.py)
        from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

        async with _get_config_lock():
            _sync_mcp_to_agent(name, enabled)

    return web.json_response({"ok": True, "name": name, "enabled": enabled, "applied": True})


async def api_mcp_toggle_tool(request: web.Request) -> web.Response:
    """POST /api/mcp/toggle-tool — enable or disable a specific tool in an MCP server.

    Updates ``disabledTools`` in ``~/.personalclaw/mcp.json``.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    server = body.get("server", "").strip()
    tool = body.get("tool", "").strip()
    enabled = body.get("enabled", True)
    if not server or not tool:
        return web.json_response({"error": "server and tool are required"}, status=400)

    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response({"error": "cannot parse global mcp.json"}, status=500)

        servers = data.setdefault("mcpServers", {})
        if server not in servers:
            # Server may exist in another scope (agent config, ~/.claude.json)
            # but not in the global settings mcp.json. Create a stub entry to hold
            # disabledTools state — the ACP agent reads this file for enforcement.
            from personalclaw.mcp_discovery import (  # circular import: mcp_discovery defers imports of personalclaw.agent which shares state with this module  # noqa: E501
                list_servers as _ls,
            )

            known = {s.name for s in _ls()}
            if server not in known:
                return web.json_response({"error": f"server {server!r} not found"}, status=404)
            servers[server] = {}

        spec = servers[server]
        if not isinstance(spec, dict):
            if isinstance(spec, str):
                servers[server] = spec = {"command": spec}
            else:
                return web.json_response(
                    {"error": f"server {server!r} has invalid config type: {type(spec).__name__}"},
                    status=500,
                )
        disabled_tools: list[str] = spec.get("disabledTools", [])
        if enabled:
            disabled_tools = [t for t in disabled_tools if t != tool]
        else:
            if tool not in disabled_tools:
                disabled_tools.append(tool)
        if disabled_tools:
            spec["disabledTools"] = disabled_tools
        else:
            spec.pop("disabledTools", None)

        try:
            _write_mcp_json(data)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"ok": True, "server": server, "tool": tool, "enabled": enabled})


async def api_mcp_toggle_all(request: web.Request) -> web.Response:
    """POST /api/mcp/toggle-all — enable or disable all MCP servers."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    enabled = body.get("enabled", True)

    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response({"error": "cannot parse global mcp.json"}, status=500)

        servers = data.get("mcpServers", {})
        toggled: list[str] = []
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                continue
            if enabled:
                spec.pop("disabled", None)
            else:
                spec["disabled"] = True
            toggled.append(name)

        try:
            _write_mcp_json(data)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

        # Batch sync: single read-modify-write of personalclaw.json
        from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

        async with _get_config_lock():
            _sync_mcp_to_agent_batch(toggled, enabled)

    return web.json_response({"ok": True, "enabled": enabled, "count": len(servers)})


async def api_mcp_remove(request: web.Request) -> web.Response:
    """POST /api/mcp/remove — uninstall an MCP server.

    Removes from ``~/.personalclaw/mcp.json``
    and syncs personalclaw.json.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name", "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    logger.info("MCP remove: %s", name)

    # Try marketplace uninstall (best-effort)
    try:
        proc = await asyncio.create_subprocess_exec(
            "personalclaw",
            "skills",
            "mcp",
            "uninstall",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        rc = proc.returncode
        out = (stdout or b"").decode(errors="replace").strip()
        err = (stderr or b"").decode(errors="replace").strip()
        logger.info("MCP uninstall via marketplace: rc=%d out=%s err=%s", rc, out[:100], err[:100])
    except FileNotFoundError:
        logger.debug("personalclaw CLI not in PATH")
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.communicate()
        logger.warning("marketplace mcp uninstall timed out for %s", name)
    except Exception as exc:
        logger.warning("marketplace mcp uninstall failed for %s: %s", name, exc)

    # Remove from global mcp.json
    async with _get_mcp_lock():
        try:
            data = json.loads(_GLOBAL_MCP_JSON.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"mcpServers": {}}
        removed = data.get("mcpServers", {}).pop(name, None) is not None
        if removed:
            _write_mcp_json(data)
            logger.info("MCP remove: removed %s from global mcp.json", name)
        else:
            logger.warning("MCP remove: %s not found in global mcp.json", name)

        # Sync personalclaw.json
        from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

        async with _get_config_lock():
            _sync_mcp_to_agent(name, False, remove=True)

    return web.json_response({"ok": True, "name": name, "removed": removed})


# ---------------------------------------------------------------------------
# Skills marketplace integration
# ---------------------------------------------------------------------------


async def api_mcp_server_detail(request: web.Request) -> web.Response:
    """PUT/DELETE /api/mcp/servers/{name} — register or remove an MCP server.

    PUT registers (or updates) an MCP server definition in the global
    ``~/.personalclaw/mcp.json`` config.  Requires localhost + X-Internal-Secret.

    Body (PUT)::

        { "command": "node", "args": ["server.js"], "env": {"KEY": "val"} }

    DELETE removes the server from the config.
    """
    name = request.match_info["name"]
    if not name or not name.strip():
        return web.json_response({"error": "server name is required"}, status=400)
    name = name.strip()
    # Server names are interpolated into mcp.json keys and surfaced to MCP
    # launchers; restrict the character set so an attacker can't smuggle
    # path-traversal or shell-meta tokens into the registry. A single ':' is
    # allowed because app-contributed servers are namespaced ``{app}:{server}``
    # (mcp_bridge) — without it the DELETE 400s before it can remove one.
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}(:[a-zA-Z0-9_-]{1,64})?", name):
        return web.json_response(
            {
                "error": "MCP server name must be letters/digits/dashes/underscores, optionally one ':' namespace"  # noqa: E501
            },
            status=400,
        )

    if request.method == "DELETE":
        # An app-contributed MCP server (``{app}:{server}``) is OWNED by its app —
        # it re-registers on every app enable, so a standalone delete here can't
        # truly remove it. Refuse + point the caller at uninstalling the app, so we
        # don't silently no-op (the bug: the row vanished then came back).
        if ":" in name:
            app_name = name.split(":", 1)[0]
            from personalclaw.apps.manager import _read_installed

            if _read_installed(app_name) is not None:
                return web.json_response(
                    {
                        "ok": False,
                        "name": name,
                        "removed": False,
                        "ownedByApp": app_name,
                        "error": f"This MCP server is provided by the '{app_name}' app. Uninstall that app "  # noqa: E501
                        f"(Store → Library) to remove it.",
                    },
                    status=409,
                )
        # Remove from EVERY store a server can live in — the PersonalClaw scope the
        # registry reads (~/.personalclaw/mcp.json), the legacy settings/mcp.json,
        # AND the agent config (source="agent" servers live there, not mcp.json) —
        # so a delete fully removes a server regardless of where it was written.
        async with _get_mcp_lock():
            removed = False
            for store in (_PERSONALCLAW_MCP_JSON, _GLOBAL_MCP_JSON):
                try:
                    data = json.loads(store.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                if data.get("mcpServers", {}).pop(name, None) is not None:
                    _atomic_write(store, data)
                    removed = True
        # _sync_mcp_to_agent(remove=True) also pops the server from the agent
        # config's mcpServers — so an agent-config-sourced server is removed too.
        # Detect whether it WAS in the agent config so the result is honest.
        in_agent = _server_in_agent_config(name)
        _sync_mcp_to_agent(name, False, remove=True)
        removed = removed or in_agent
        sel().log_api_access(
            caller="dashboard",
            operation="mcp_server_remove",
            outcome="completed" if removed else "not_found",
            resources=name,
        )
        status = 200 if removed else 404
        return web.json_response({"ok": removed, "name": name, "removed": removed}, status=status)

    # PUT — register or update
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    command = body.get("command", "")
    if not command:
        return web.json_response({"error": "command is required"}, status=400)

    entry: dict[str, Any] = {"command": command}
    if body.get("args"):
        entry["args"] = body["args"]
    if body.get("env"):
        entry["env"] = body["env"]

    # Write to ~/.personalclaw/mcp.json — the PersonalClaw scope the native MCP
    # client actually spawns + lists tools from (mcp_client._personalclaw_mcp_specs).
    # Writing the legacy settings/mcp.json instead would surface the server in the
    # list but expose ZERO tools, since the live registry never reads that file.
    # Full upsert (overwrite an existing spec) + enabled (drop any disabled flag).
    async with _get_mcp_lock():
        data = _load_json_or_empty(_PERSONALCLAW_MCP_JSON)
        data.setdefault("mcpServers", {})[name] = entry
        _atomic_write(_PERSONALCLAW_MCP_JSON, data)

    # Mirror into personalclaw.json (enable by default)
    _sync_mcp_to_agent(name, True)

    logger.info("MCP register via REST: %s command=%s", name, command)
    sel().log_api_access(
        caller="dashboard",
        operation="mcp_server_register",
        outcome="completed",
        resources=name,
    )
    return web.json_response({"ok": True, "name": name}, status=200)


# ─── Batched scope apply ────────────────────────────────────────────────

_PERSONALCLAW_MCP_JSON = Path.home() / ".personalclaw" / "mcp.json"
# The claude-code CLI's own global config; PersonalClaw reads/writes MCP server
# specs here so servers stay in sync when that ACP backend is in use.
_CC_GLOBAL_JSON = Path.home() / ".claude.json"


def _load_json_or_empty(path: Path) -> dict[str, Any]:
    """Load JSON from a path; return empty dict on missing/malformed/unreadable.

    Catches the broad ``OSError`` (not just ``FileNotFoundError``) so a
    ``PermissionError`` or ``IsADirectoryError`` on a user-owned file like
    ``~/.claude.json`` won't crash ``api_mcp_apply`` mid-batch and leave
    partially-applied changes without a rebuild.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(path: Path, data: dict) -> None:
    """Atomic JSON write; reuses the agent helper."""
    from personalclaw.agent import (  # noqa: F811  # circular: agent imports dashboard handlers
        _atomic_json_write,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(path, data)


def _find_server_spec_anywhere(name: str) -> dict | None:
    """Locate a server's full spec from any known source.

    Search order matches the PersonalClaw merge: agent config → ~/.personalclaw/mcp.json
    → global settings → claude-code global.  Returns a shallow copy with
    ``disabled`` stripped (the caller decides whether to disable in its target scope).
    """
    candidates = [
        Path.home() / ".personalclaw" / "agents" / "personalclaw.json",
        _PERSONALCLAW_MCP_JSON,
        _GLOBAL_MCP_JSON,
        _CC_GLOBAL_JSON,
    ]
    for p in candidates:
        spec = _load_json_or_empty(p).get("mcpServers", {}).get(name)
        if isinstance(spec, dict) and (spec.get("command") or spec.get("url")):
            return {k: v for k, v in spec.items() if k != "disabled"}
    return None


def _scope_has_entry(name: str, path: Path) -> bool:
    return isinstance(_load_json_or_empty(path).get("mcpServers", {}).get(name), dict)


def _set_personalclaw_entry(name: str, *, enabled: bool, spec: dict | None = None) -> str:
    """Set the server's ``disabled`` state in ``~/.personalclaw/mcp.json``.

    When ``enabled`` is True and ``spec`` is provided, upserts the full spec
    (used for preservation copies).  When enabled is False, adds/updates the
    entry to carry ``disabled: true`` — preserves existing command/args/env
    if already present; otherwise uses ``spec`` as the seed.

    Returns a short label describing what happened: ``"added"``, ``"enabled"``,
    ``"disabled"``, or ``"noop"``.
    """
    data = _load_json_or_empty(_PERSONALCLAW_MCP_JSON)
    servers = data.setdefault("mcpServers", {})
    existing = servers.get(name)
    existing = existing if isinstance(existing, dict) else None

    if enabled:
        if existing is None and spec is None:
            return "noop"
        if existing is None:
            servers[name] = {k: v for k, v in (spec or {}).items() if k != "disabled"}
            action = "added"
        else:
            # Remove disabled flag if set; otherwise no change needed.
            if existing.get("disabled") is True:
                existing.pop("disabled", None)
                action = "enabled"
            else:
                return "noop"
    else:
        if existing is None:
            base = spec or _find_server_spec_anywhere(name) or {}
            entry = {k: v for k, v in base.items() if k != "disabled"}
            entry["disabled"] = True
            servers[name] = entry
            action = "disabled"
        elif existing.get("disabled") is True:
            return "noop"
        else:
            existing["disabled"] = True
            action = "disabled"

    _atomic_write(_PERSONALCLAW_MCP_JSON, data)
    return action


def _remove_personalclaw_entry(name: str) -> bool:
    """Delete the server from ``~/.personalclaw/mcp.json`` entirely.  Returns True on change."""
    data = _load_json_or_empty(_PERSONALCLAW_MCP_JSON)
    servers = data.get("mcpServers", {})
    if name not in servers:
        return False
    del servers[name]
    _atomic_write(_PERSONALCLAW_MCP_JSON, data)
    return True


def _remove_from_agent_file(path: Path, name: str) -> bool:
    """Delete a server entry from a rendered agent file.

    Used by the uninstall path so the entry doesn't linger in
    ``~/.personalclaw/agents/personalclaw.json`` / ``~/.claude/agents/personalclaw.mcp.json``
    — the rebuild uses the existing agent file as its merge base, so without
    this targeted delete, additive merging would keep the entry alive.
    Returns True when the file was modified.
    """
    if not path.is_file():
        return False
    data = _load_json_or_empty(path)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    _atomic_write(path, data)
    return True


def _set_scope_entry(path: Path, name: str, *, enabled: bool, spec: dict | None = None) -> str:
    """Add/remove a server from a provider global file (global settings or claude-code).

    When enabled=True and the server is absent, adds the spec.  When
    enabled=False, removes the entry entirely (NOT soft-disable — the
    dashboard badge treats absent and disabled identically).
    """
    data = _load_json_or_empty(path)
    servers = data.setdefault("mcpServers", {})
    present = name in servers and isinstance(servers[name], dict)

    if enabled:
        if present:
            # Already enabled; if the entry had disabled:true, clear it.
            s = servers[name]
            if isinstance(s, dict) and s.get("disabled") is True:
                s.pop("disabled", None)
                _atomic_write(path, data)
                return "enabled"
            return "noop"
        if spec is None:
            spec = _find_server_spec_anywhere(name)
        if spec is None:
            return "missing_spec"
        servers[name] = {k: v for k, v in spec.items() if k != "disabled"}
        _atomic_write(path, data)
        return "added"
    # enabled=False — hard remove.
    if not present:
        return "noop"
    del servers[name]
    _atomic_write(path, data)
    return "removed"


def _set_tool_overrides(name: str, tool_overrides: dict[str, bool]) -> list[str]:
    """Apply per-tool enable/disable overrides to a server's entry in
    ``~/.personalclaw/mcp.json``.

    ``tool_overrides`` maps tool name → desired enabled state.  Disabled
    tools are added to the entry's ``disabledTools`` list; re-enabling
    removes them.  Creates the entry if absent (sourcing full spec from
    any scope so the server keeps loading).

    Returns a list of tool names whose state changed.
    """
    if not tool_overrides:
        return []
    data = _load_json_or_empty(_PERSONALCLAW_MCP_JSON)
    servers = data.setdefault("mcpServers", {})
    entry = servers.get(name)
    if not isinstance(entry, dict):
        # Seed from the best-available spec so the server keeps its config.
        base = _find_server_spec_anywhere(name) or {}
        entry = {k: v for k, v in base.items() if k != "disabled"}
        servers[name] = entry

    disabled = list(entry.get("disabledTools") or [])
    changed: list[str] = []
    for tool, tool_enabled in tool_overrides.items():
        if tool_enabled and tool in disabled:
            disabled.remove(tool)
            changed.append(tool)
        elif (not tool_enabled) and tool not in disabled:
            disabled.append(tool)
            changed.append(tool)

    if disabled:
        entry["disabledTools"] = disabled
    else:
        entry.pop("disabledTools", None)

    if changed:
        _atomic_write(_PERSONALCLAW_MCP_JSON, data)
    return changed


async def api_mcp_apply(request: web.Request) -> web.Response:
    """POST /api/mcp/apply — batched per-scope apply for MCP servers.

    Request body::

        {
          "changes": [
            {
              "name": "my-mcp-server",
              "personalclaw": true,     // desired PersonalClaw visibility
              "globalMcp": true,   // desired presence in ~/.personalclaw/mcp.json
              "ccGlobal": false,    // desired presence in ~/.claude.json
              "uninstall": false,   // optional: remove from all scopes + marketplace
              "toolOverrides": {    // optional: per-tool enable/disable
                "SkillsTool": false,
                "ReadFile": true
              }
            }
          ]
        }

    Each change is processed in the order PersonalClaw → agent config → claude-code, with a
    preservation step first: if the user is removing the server from its
    only source AND PersonalClaw is desired on, the full spec is copied into
    ``~/.personalclaw/mcp.json`` before the removal so PersonalClaw keeps its config.

    After all changes are written, ``rebuild_agent_config`` is called once
    so the provider-native agent files (``~/.personalclaw/agents/personalclaw.json`` and
    ``~/.claude/agents/personalclaw.md`` + ``personalclaw.mcp.json``) reflect the
    new merged state.  Returns a summary with per-change outcomes.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    changes = body.get("changes")
    if not isinstance(changes, list):
        return web.json_response({"error": "changes must be a list"}, status=400)

    results: list[dict] = []

    async with _get_mcp_lock():
        for change in changes:
            name = str(change.get("name", "")).strip()
            if not name:
                results.append({"error": "empty name", "change": change})
                continue
            # Defense-in-depth: name flows into subprocess argv (marketplace
            # mcp uninstall) and filesystem paths via scope helpers.  Even
            # though we use list-form subprocess (no shell), reject names
            # that contain argv-injection chars or path traversal.
            if not _is_valid_mcp_name(name):
                results.append({"error": "invalid name", "name": name})
                sel().log_api_access(
                    caller="dashboard",
                    operation="mcp_apply_rejected_name",
                    outcome="denied",
                    resources=name[:64],
                )
                continue

            outcome: dict[str, Any] = {"name": name, "actions": {}}

            # ── Uninstall path: wipe from all scopes and (best-effort) marketplace ──
            if change.get("uninstall"):
                outcome["actions"]["personalclaw"] = (
                    "removed" if _remove_personalclaw_entry(name) else "noop"
                )
                outcome["actions"]["globalMcp"] = _set_scope_entry(
                    _GLOBAL_MCP_JSON, name, enabled=False
                )
                outcome["actions"]["ccGlobal"] = _set_scope_entry(
                    _CC_GLOBAL_JSON, name, enabled=False
                )
                # Also strip the entry directly from the rendered agent files
                # so the next rebuild doesn't resurrect it via the
                # "start from existing agent config" base.  Without this the
                # additive merge keeps the entry around.
                _remove_from_agent_file(
                    Path.home() / ".personalclaw" / "agents" / "personalclaw.json", name
                )
                _remove_from_agent_file(
                    Path.home() / ".claude" / "agents" / "personalclaw.mcp.json", name
                )
                # Best-effort marketplace uninstall (don't block on failure)
                mkt_cli = shutil.which("personalclaw")
                if mkt_cli:
                    try:
                        # subprocess.run blocks — run in a thread so we don't
                        # stall the asyncio event loop under the MCP file lock.
                        await asyncio.to_thread(
                            subprocess.run,
                            [mkt_cli, "skills", "mcp", "uninstall", name],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        outcome["actions"]["marketplace"] = "uninstall_attempted"
                    except Exception as exc:
                        # Error strings may include env vars / AWS keys /
                        # URLs surfaced by failing subprocesses; scrub
                        # them before returning to the dashboard.  Both
                        # redact helpers return (cleaned_text, warnings);
                        # we only surface the cleaned text.
                        _urls_clean, _ = redact_exfiltration_urls(str(exc))
                        _redacted, _ = redact_credentials(_urls_clean)
                        outcome["actions"]["marketplace_error"] = _redacted
                sel().log_api_access(
                    caller="dashboard",
                    operation="mcp_uninstall",
                    outcome="ok",
                    resources=name,
                )
                results.append(outcome)
                continue

            # ── Scope toggles: compute desired + apply preservation ──
            desired_mc = bool(change.get("personalclaw", True))
            desired_global = bool(change.get("globalMcp", False))
            desired_cc = bool(change.get("ccGlobal", False))

            # Preservation rule: if PersonalClaw is desired ON and the server
            # isn't already in ~/.personalclaw/mcp.json, copy its spec there so
            # PClaw owns a runnable copy. This is purely additive — it never
            # removes the server from whatever scope it came from — so it also
            # serves the Tools-page "Import from Claude Code" action (which keeps
            # ccGlobal on). Without this, importing a Claude-Code-only server
            # would be a no-op (nothing to enable in the PClaw scope).
            preserved_spec: dict | None = None
            if desired_mc and not _scope_has_entry(name, _PERSONALCLAW_MCP_JSON):
                preserved_spec = _find_server_spec_anywhere(name)

            # Apply PersonalClaw first — flipping PersonalClaw green needs the entry to exist or
            # the disabled override removed.  Flipping PersonalClaw gray writes
            # disabled:true, preserving config for later re-enable.
            outcome["actions"]["personalclaw"] = _set_personalclaw_entry(
                name,
                enabled=desired_mc,
                spec=preserved_spec,
            )

            # Apply agent config and claude-code (add/remove from their respective globals).
            # Resolve the spec ONCE before any scope mutation — otherwise
            # the global-settings removal can vacate the only source that had the
            # spec, and the claude-code add would get "missing_spec" even though
            # the user clearly intended it to move over.
            resolved_spec = _find_server_spec_anywhere(name)
            outcome["actions"]["globalMcp"] = _set_scope_entry(
                _GLOBAL_MCP_JSON,
                name,
                enabled=desired_global,
                spec=resolved_spec,
            )
            outcome["actions"]["ccGlobal"] = _set_scope_entry(
                _CC_GLOBAL_JSON,
                name,
                enabled=desired_cc,
                spec=resolved_spec,
            )

            # ── Per-tool overrides (disabledTools in ~/.personalclaw/mcp.json) ──
            tool_overrides = change.get("toolOverrides")
            if isinstance(tool_overrides, dict) and tool_overrides:
                # Apply the same allowlist as server names — tool names are
                # persisted to ~/.personalclaw/mcp.json and later consumed by
                # ACP agent / other components, so reject anything that
                # could smuggle argv-injection chars or path traversal
                # into downstream reads.  Invalid names are filtered out
                # silently and audited separately.
                sanitized: dict[str, bool] = {}
                rejected: list[str] = []
                for k, v in tool_overrides.items():
                    tool_name = str(k)
                    if _is_valid_mcp_name(tool_name):
                        sanitized[tool_name] = bool(v)
                    else:
                        rejected.append(tool_name[:64])
                if rejected:
                    outcome["actions"]["tools_rejected"] = rejected
                    sel().log_api_access(
                        caller="dashboard",
                        operation="mcp_apply_rejected_tool_name",
                        outcome="denied",
                        resources=f"{name}:{','.join(rejected)[:128]}",
                    )
                if sanitized:
                    changed_tools = _set_tool_overrides(name, sanitized)
                    if changed_tools:
                        outcome["actions"]["tools"] = changed_tools

            # Audit the scope-toggle decision.  Changing scope presence
            # controls which MCP servers (and therefore tools) are
            # reachable from PersonalClaw sessions — a permission-shaping
            # event that belongs in the SEL log alongside uninstalls.
            sel().log_api_access(
                caller="dashboard",
                operation="mcp_scope_apply",
                outcome="ok",
                resources=(
                    f"{name} "
                    f"mc={'on' if desired_mc else 'off'} "
                    f"global={'on' if desired_global else 'off'} "
                    f"cc={'on' if desired_cc else 'off'}"
                ),
            )

            results.append(outcome)

    # ── Rebuild agent artifacts once all scope writes complete ──
    rebuild_ok = False
    rebuild_error: str | None = None
    try:
        # circular import: personalclaw.agent imports dashboard handlers, so
        # this is delayed to runtime to break the cycle at module load.
        from personalclaw.agent import rebuild_agent_config  # noqa: F811

        await asyncio.to_thread(rebuild_agent_config)
        rebuild_ok = True
    except Exception as exc:
        # Rebuild failures can surface file paths, env var contents, or
        # credential fragments (e.g. JSON decode errors that echo file
        # contents).  Apply the same redaction pipeline we use for the
        # marketplace uninstall error before handing it to the dashboard.
        _urls_clean, _ = redact_exfiltration_urls(str(exc))
        rebuild_error, _ = redact_credentials(_urls_clean)
        logger.warning("rebuild_agent_config failed after apply: %s", exc)

    return web.json_response(
        {
            "ok": True,
            "applied": len(results),
            "results": results,
            "rebuild": {"ok": rebuild_ok, "error": rebuild_error},
        }
    )
