"""MCP server management handlers — probe, sync, toggle, remove."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.request_validation import require_string
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# Allowlist pattern for MCP server names.  Matches the convention used
# (alphanumerics, dashes, underscores, slashes, dots,
# and ``@`` for scoped names like ``@org/server``) and defends against a
# name smuggling argv flags, shell metacharacters or path traversal into the
# config files and specs other components read.
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


# The INSTALLED agent config, resolved the same deferred way and for the same reason as
# `_canonical_mcp_json()` above. Two call sites in this file spelled it
# `Path.home() / ".personalclaw" / "agents" / "personalclaw.json"`, which ignores
# PERSONALCLAW_HOME outright — so a dev gateway read the operator's REAL agent config, and its
# uninstall path DELETED a server from it.
#
# Through `agent.agents_dir()`, the ONE owner of `<home>/agents` (#3463). This used to spell
# `config_dir() / "agents"` itself, because `agent.AGENTS_DIR` was evaluated at IMPORT time and
# froze the home as it was then. Resolving per use is still the whole point — it is just no
# longer a reason to derive the path a second time here.
def _installed_agent_json() -> Path:
    from personalclaw.agent import AGENT_FILENAME, agents_dir

    return agents_dir() / AGENT_FILENAME


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
            _atomic_write(canon, cdata)
            logger.info("mcp: migrated %d server(s) from legacy settings/mcp.json", moved)
        # empty the legacy file so it can't re-diverge
        _atomic_write(legacy, {"mcpServers": {}})
    except Exception:
        logger.debug("mcp: legacy migration skipped", exc_info=True)


# There is no `_GLOBAL_MCP_JSON`. It was `_canonical_mcp_json()` frozen at import, kept after UT3
# folded the "global" store into this file, and `/api/mcp/apply` still treated it as a second
# scope: Import sent `globalMcp: false`, which removed the server from the file
# `personalclaw: true` had just added it to. One store, one name for it.


class _McpFileLock:
    """Async context manager wrapping fcntl.flock for mcp.json, on a sidecar ``mcp.lock``
    (works cross-process too). Resolved per use, like the file it guards."""

    async def __aenter__(self) -> None:
        import fcntl

        lock_path = _canonical_mcp_json().with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=True)
        self._fd = open(lock_path, "r")
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
    """Return an MCP config file lock."""
    return _McpFileLock()


def _read_mcp_json() -> dict[str, Any]:
    """``mcp.json``'s servers for a LOOKUP (absent or unreadable → ``{}``)."""
    return _load_json_or_empty(_canonical_mcp_json()).get("mcpServers", {})


# ── MCP Servers ──


_mcp_probe_cache: list[dict] = []
_mcp_probe_ts: float = 0.0
_MCP_PROBE_CACHE_SECS = 600  # 10 min
_mcp_probe_in_progress = False


def _sync_mcp_to_agent(name: str, enabled: bool) -> None:
    """Sync a server's enabled state to personalclaw.json: its spec and its ``@name`` refs.

    Removing a server is not this function's job: :func:`secret_refs.remove_mcp_servers` takes it
    out of both documents, which is what deletes the values it owns.
    """
    from personalclaw.dashboard.handlers.agents import (  # noqa: F811 circular: agents imports mcp
        _installed_agent_config,
    )

    path = _installed_agent_config()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read agent config %s, skipping sync: %s", path, exc)
        return

    if enabled:
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
    # On disable, clean up any @server-name refs the user may have added
    if not enabled:
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
    try:
        _atomic_write(path, cfg)
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
        own = _read_mcp_json()
        for name in names:
            if name not in mcp_servers:
                spec = own.get(name, {})
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
        _atomic_write(path, cfg)
    except OSError as exc:
        logger.warning("Cannot write agent config %s: %s", path, exc)


async def _bg_mcp_probe() -> None:
    """Background MCP probe — populates cache at startup."""
    global _mcp_probe_ts, _mcp_probe_in_progress
    try:
        from personalclaw.mcp_discovery import list_servers, probe_server  # noqa: F811

        mcp_specs = _read_mcp_json()

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
            spec = mcp_specs.get(s.name, {})
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

    # mcp.json holds each server's disabled state
    mcp_specs = _read_mcp_json()
    result: list[dict] = []
    for s in servers:
        d = s.to_dict()
        # Prefer handler cache status over discovery cache "outdated"
        cached = cached_by_name.get(s.name)
        if cached and d["status"] in ("outdated", "unknown"):
            d["status"] = cached.get("status", d["status"])
            d["tools"] = cached.get("tools", d["tools"])
            d["error"] = cached.get("error", d["error"])
        spec = mcp_specs.get(s.name, {})
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
    from personalclaw.agent import agents_dir  # noqa: F811

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
        for f in agents_dir().glob("*.json"):
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

    # Personalclaw / default: read from mcp.json
    from personalclaw.mcp_discovery import list_servers  # noqa: F811

    mcp_specs = _read_mcp_json()
    servers = list_servers()
    result: list[dict] = []
    for s in servers:
        spec = mcp_specs.get(s.name, {})
        enabled = not (isinstance(spec, dict) and spec.get("disabled"))
        result.append({"name": s.name, "enabled": enabled})
    # Also include personalclaw-core (always enabled)
    names = {r["name"] for r in result}
    for builtin in ("personalclaw-core",):
        if builtin not in names:
            result.insert(0, {"name": builtin, "enabled": True})
    return web.json_response(result)


async def api_mcp_probe(request: web.Request) -> web.Response:
    """POST /api/mcp/probe — probe all MCP servers and return live status.

    Merges ``enabled`` and ``disabledTools`` from mcp.json so
    probe results don't reset user's previous enable/disable choices.
    """
    global _mcp_probe_ts
    from personalclaw.mcp_discovery import probe_all  # noqa: F811

    servers = await probe_all()
    # mcp.json holds the enabled/disabledTools state
    mcp_specs = _read_mcp_json()
    result: list[dict[str, Any]] = []
    for s in servers:
        d = s.to_dict()
        spec = mcp_specs.get(s.name, {})
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
    spec = _read_mcp_json().get(name, {})
    d["enabled"] = not (isinstance(spec, dict) and spec.get("disabled"))
    if isinstance(spec, dict) and spec.get("disabledTools"):
        d["disabledTools"] = spec["disabledTools"]
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

    1. Discovers servers in mcp.json the agent config lacks, or holds a stale copy of.
    2. Rebuilds the agent config from mcp.json and registers them for Claude Code.
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
    name = require_string(body, "name")
    enabled = body.get("enabled", True)

    async with _get_mcp_lock():
        # 1. Update mcp.json
        try:
            data = json.loads(_canonical_mcp_json().read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response({"error": "cannot parse mcp.json"}, status=500)

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
            _atomic_write(_canonical_mcp_json(), data)
        except Exception as exc:
            # Raw text is diagnostics for the log; the wire speaks guidance (failure_copy).
            logger.warning("mcp: failed to write mcp.json", exc_info=True)
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

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
            data = json.loads(_canonical_mcp_json().read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response({"error": "cannot parse mcp.json"}, status=500)

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
            _atomic_write(_canonical_mcp_json(), data)
        except Exception as exc:
            # Raw text is diagnostics for the log; the wire speaks guidance (failure_copy).
            logger.warning("mcp: failed to write mcp.json", exc_info=True)
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
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
            data = json.loads(_canonical_mcp_json().read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"mcpServers": {}}
        except json.JSONDecodeError:
            return web.json_response({"error": "cannot parse mcp.json"}, status=500)

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
            _atomic_write(_canonical_mcp_json(), data)
        except Exception as exc:
            # Raw text is diagnostics for the log; the wire speaks guidance (failure_copy).
            logger.warning("mcp: failed to write mcp.json", exc_info=True)
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

        # Batch sync: single read-modify-write of personalclaw.json
        from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

        async with _get_config_lock():
            _sync_mcp_to_agent_batch(toggled, enabled)

    return web.json_response({"ok": True, "enabled": enabled, "count": len(servers)})


# ---------------------------------------------------------------------------
# One MCP server: read it for the edit form, add or edit it, remove it
# ---------------------------------------------------------------------------


def _not_editable_reason(name: str, spec: dict[str, Any]) -> str | None:
    """Why the Tools page's form does not own ``name``'s definition, or ``None`` when it does.

    The sentence is the edit button's answer, so each clause has to be true of this server.
    """
    from personalclaw.agent import _MANAGED_MCP_SERVERS

    if name in _MANAGED_MCP_SERVERS:
        return "PersonalClaw manages this server itself and sets it up again on every start."
    if ":" in name:
        app = name.split(":", 1)[0]
        return f"The '{app}' app provides this server and sets it up from its own definition."
    if spec.get("url") and not spec.get("command"):
        return (
            "This is a remote server at a URL. The edit form changes a server PersonalClaw "
            "starts with a command."
        )
    return None


def _definition_of(name: str) -> dict[str, Any] | None:
    """The spec the edit form reads: the user's own ``mcp.json`` first, else the agent config's
    copy (a server that exists only there is still the user's to edit)."""
    spec = _read_mcp_json().get(name)
    if isinstance(spec, dict):
        return spec
    spec = _load_json_or_empty(_installed_agent_json()).get("mcpServers", {}).get(name)
    return spec if isinstance(spec, dict) else None


def _has_saved_value(value: Any) -> bool:
    from personalclaw.config.credentials import credential_names
    from personalclaw.config.secret_refs import ref_key

    key = ref_key(value)
    if key is not None:
        return key in credential_names()
    return value not in (None, "")


def _rebuild_agent_config_logged() -> None:
    """The rebuild after a server's definition changed, so the agent config's copy matches it.
    A failure is logged, never raised: ``mcp.json`` already holds the change, and the next
    rebuild (every gateway start) reconciles it."""
    try:
        from personalclaw.agent import rebuild_agent_config  # noqa: F811  # circular

        rebuild_agent_config()
    except Exception:  # noqa: BLE001 — see above
        logger.warning("rebuild_agent_config failed after an MCP server write", exc_info=True)


async def api_mcp_server_detail(request: web.Request) -> web.Response:
    """GET/PUT/DELETE /api/mcp/servers/{name} — read, add or edit, or remove one MCP server.

    GET is what the edit form reads: ``command``, ``args`` and ``env`` as ``[{name, plain,
    value | hasValue}]`` — a plain variable's value, and for a stored one only whether a value is
    saved. A stored value never leaves the server. ``editable`` is false, with a ``reason``, for a
    server the form does not own (PersonalClaw's own, an app's, a remote one).

    PUT adds or edits a stdio server, the one write path for both. Body::

        { "command": "node", "args": ["server.js"], "env": {"KEY": "val"},
          "plainEnv": ["LOG_LEVEL"], "keepEnv": ["API_KEY"] }

    Every ``env`` value is saved in the credential store and ``mcp.json`` holds a reference to it,
    except the variables ``plainEnv`` names, which stay in the file as settings. ``keepEnv`` names
    variables whose saved value stays as it is — the edit form sends a secret it only showed
    masked this way, so its value never makes the round trip. A kept variable marked plain has its
    stored value moved into the file. Keys the form does not own (``disabled``, ``disabledTools``,
    ``autoApprove``, ``cwd``) are kept, and the agent config's copy is rebuilt to match.

    DELETE removes the server from ``mcp.json`` and the agent config and deletes the values it
    owns in the credential store (``secret_refs.remove_mcp_servers``, the one delete).
    """
    from personalclaw.config.secret_refs import (
        MCP_DEFINITION_KEYS,
        MCP_PLAIN_ENV,
        ForeignSecretReference,
        mcp_env_view,
        ref_key,
        remove_mcp_servers,
        resolve_mcp_values,
        store_mcp_spec,
    )

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

    if request.method == "GET":
        spec = _definition_of(name)
        if spec is None:
            return json_error(
                "not_found", message=f"No MCP server named '{name}' is configured.", status=404
            )
        reason = _not_editable_reason(name, spec)
        if reason is not None:
            return web.json_response({"name": name, "editable": False, "reason": reason})
        args = spec.get("args")
        return web.json_response(
            {
                "name": name,
                "editable": True,
                "command": spec.get("command", ""),
                "args": [str(a) for a in args] if isinstance(args, list) else [],
                "env": mcp_env_view(spec),
            }
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
        # Out of mcp.json AND the agent config, so the second write deletes its stored values.
        # Never Claude Code's own file: removing a server here is not removing it there.
        from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

        async with _get_mcp_lock():
            async with _get_config_lock():
                removed = bool(remove_mcp_servers([name]))
        sel().log_api_access(
            caller="dashboard",
            operation="mcp_server_remove",
            outcome="completed" if removed else "not_found",
            resources=name,
        )
        if removed:
            return web.json_response({"ok": True, "name": name, "removed": True}, status=200)
        # The 404 body must carry an `error` key like every other refusal on this handler
        # (the 409 branch above already does) — otherwise the frontend funnel has nothing
        # to show and the toast reads the bare "HTTP 404" (#2942). `ok`/`name`/`removed`
        # stay at the top level so an existing reader of those keys is unaffected.
        return json_error(
            "not_found",
            message=f"No MCP server named '{name}' was found to remove.",
            status=404,
            ok=False,
            name=name,
            removed=False,
        )

    # PUT — add or edit
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    command = body.get("command", "")
    if not command or not isinstance(command, str):
        return web.json_response({"error": "command is required"}, status=400)
    args = body.get("args") or []
    env = body.get("env") or {}
    plain = body.get("plainEnv") or []
    keep = body.get("keepEnv") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return json_error(
            "invalid_field_type", message="args must be a list of strings", status=400
        )
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        return json_error(
            "invalid_env", message="env must map variable names to string values", status=400
        )
    for field_name, names in (("plainEnv", plain), ("keepEnv", keep)):
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return json_error(
                "invalid_env", message=f"{field_name} must be a list of variable names", status=400
            )

    # Write to ~/.personalclaw/mcp.json — the PersonalClaw scope the native MCP
    # client actually spawns + lists tools from (mcp_client._personalclaw_mcp_specs).
    async with _get_mcp_lock():
        # `_load_json_for_update`, not `_load_json_or_empty`: an mcp.json that exists but cannot be
        # read would otherwise load as `{}` and be written back holding ONLY this one server,
        # erasing every MCP server the user had configured. Raising surfaces it as a failed request
        # instead — see `ConfigUnreadable`.
        data = _load_json_for_update(_canonical_mcp_json())
        servers = data.setdefault("mcpServers", {})
        existing = servers.get(name)
        if not isinstance(existing, dict):
            existing = _definition_of(name) or {}
        reason = _not_editable_reason(name, {"command": command})
        if reason is not None:
            return json_error("mcp_server_not_editable", message=reason, status=409)

        current = existing.get("env") if isinstance(existing.get("env"), dict) else {}
        unkept = [n for n in keep if not _has_saved_value(current.get(n))]
        if unkept:
            return json_error(
                "invalid_env",
                message=f"{', '.join(unkept)}: no value is saved for this server to keep. "
                "Enter a value.",
                status=400,
            )
        marked_plain = set(plain)
        new_env: dict[str, Any] = {}
        for var in keep:
            value = current[var]
            if var in marked_plain and ref_key(value) is not None:
                # Marked plain now: the value leaves the store and is kept in the file — read as
                # any start of the server reads it, against its own owner, so a reference to
                # another owner's key cannot be turned into that key in plaintext here.
                try:
                    value = resolve_mcp_values(name, "env", {var: value}).get(var, "")
                except ForeignSecretReference as exc:
                    return json_error("secret_owned_elsewhere", message=str(exc), status=400)
            new_env[var] = value
        new_env.update(env)  # a value typed now replaces a kept one

        # Keys the form does not own survive the edit; the definition is replaced whole, so a
        # cleared argument list or a removed variable is gone rather than merged back.
        entry: dict[str, Any] = {k: v for k, v in existing.items() if k not in MCP_DEFINITION_KEYS}
        entry["command"] = command
        if args:
            entry["args"] = args
        if new_env:
            entry["env"] = new_env
            marked = sorted(n for n in marked_plain if n in new_env)
            if marked:
                entry[MCP_PLAIN_ENV] = marked
        try:
            # STRICT: a value typed now that the store cannot hold is refused, not left inline.
            entry = store_mcp_spec(name, entry, strict=True)
        except ForeignSecretReference as exc:
            return json_error("secret_owned_elsewhere", message=str(exc), status=400)
        except ValueError as exc:
            return json_error("invalid_env", message=str(exc), status=400)
        servers[name] = entry
        _atomic_write(_canonical_mcp_json(), data)

    # The agent config's copy: added if new (with its `@name` refs), then rebuilt from mcp.json,
    # so an edit reaches what `list_servers` lists and the Tools page probes.
    from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

    async with _get_config_lock():
        _sync_mcp_to_agent(name, True)
    await asyncio.to_thread(_rebuild_agent_config_logged)

    logger.info("MCP register via REST: %s command=%s", name, command)
    sel().log_api_access(
        caller="dashboard",
        operation="mcp_server_register",
        outcome="completed",
        resources=name,
    )
    return web.json_response({"ok": True, "name": name}, status=200)


# ─── Batched scope apply ────────────────────────────────────────────────

# NO `_canonical_mcp_json()` constant here: it was `Path.home() / ".personalclaw" /
# "mcp.json"`, computed at import time, so it ignored PERSONALCLAW_HOME exactly as the
# comment on `_canonical_mcp_json()` (above) says the old hardcode did — the same bug, fixed
# in one function and left in three siblings. Call `_canonical_mcp_json()` instead.
# The claude-code CLI's own global config; PersonalClaw reads/writes MCP server
# specs here so servers stay in sync when that ACP backend is in use.
_CC_GLOBAL_JSON = Path.home() / ".claude.json"


def _load_json_or_empty(path: Path) -> dict[str, Any]:
    """Load JSON from a path; return empty dict on missing/malformed/unreadable.

    Catches the broad ``OSError`` (not just ``FileNotFoundError``) so a
    ``PermissionError`` or ``IsADirectoryError`` on a user-owned file like
    ``~/.claude.json`` won't crash ``api_mcp_apply`` mid-batch and leave
    partially-applied changes without a rebuild.

    ⚠️ READ-ONLY. Never feed the result of this into a write: see
    ``_load_json_for_update`` for why collapsing "absent" and "unreadable" into ``{}`` is safe
    for a lookup and destructive for a read-modify-write.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class ConfigUnreadable(Exception):
    """A config file EXISTS but could not be read or parsed — distinct from absent.

    That distinction is the entire point. ``_load_json_or_empty`` collapses both into ``{}``,
    which is right for a LOOKUP (a missing server is missing either way) and catastrophic for a
    READ-MODIFY-WRITE: an empty dict that gains one key and is written back **replaces the
    file's whole contents**.

    The file most at risk is ``~/.claude.json``, which PersonalClaw does not own. Claude Code
    keeps far more than ``mcpServers`` there — projects, history, auth state — so one transient
    read failure (a permission blip, a lock, a concurrent write caught mid-flush and therefore
    momentarily invalid JSON) turned "enable one MCP server" into "replace the user's entire
    Claude Code config with a one-server dict", taking every other server's API keys with it.
    The write is atomic, which is exactly why there was no partial-file evidence afterwards.
    """


def _load_json_for_update(path: Path) -> dict[str, Any]:
    """Load JSON for a read-modify-write cycle. Absent → ``{}``; unreadable → raise.

    ``{}`` is only safe when the file genuinely does not exist, because then writing it CREATES
    rather than destroys. Every other failure mode must stop the write.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigUnreadable(f"{path} exists but could not be read: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigUnreadable(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigUnreadable(f"{path} holds {type(data).__name__}, not a JSON object")
    return data


def _is_personalclaw_document(path: Path) -> bool:
    """``mcp.json`` (and its legacy ``settings/`` twin) or the agent config — the files whose MCP
    server secrets live in the credential store. Any other path is another tool's own file."""
    return path in {_canonical_mcp_json(), _legacy_mcp_json(), _installed_agent_json()}


def _atomic_write(path: Path, data: dict) -> None:
    """Atomic JSON write of an MCP document.

    PersonalClaw's own documents go through ``secret_refs.write_mcp_document``, which keeps a
    server's ``env``/``headers`` values in the credential store, so no path in this module can
    write one into the file. Another tool's file (``~/.claude.json``) is written as
    given — :func:`_set_scope_entry` has already put the spec in the form that tool reads.
    """
    if _is_personalclaw_document(path):
        from personalclaw.config.secret_refs import write_mcp_document

        write_mcp_document(path, data)
        return
    from personalclaw.agent import (  # noqa: F811  # circular: agent imports dashboard handlers
        _atomic_json_write,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(path, data)


def _find_server_spec_anywhere(name: str) -> dict | None:
    """Locate a server's full spec from any known source.

    Search order matches the PersonalClaw merge: agent config → ~/.personalclaw/mcp.json
    → claude-code global.  Returns a shallow copy with ``disabled`` stripped (the caller
    decides whether to disable in its target scope).
    """
    candidates = [_installed_agent_json(), _canonical_mcp_json(), _CC_GLOBAL_JSON]
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
    try:
        data = _load_json_for_update(_canonical_mcp_json())
    except ConfigUnreadable as exc:
        # Same refusal as `_set_scope_entry`: an unreadable file must not be REPLACED by a
        # dict holding only the entry being written.
        logger.warning("mcp: refusing to rewrite %s — %s", _canonical_mcp_json(), exc)
        raise
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

    _atomic_write(_canonical_mcp_json(), data)
    return action


def _set_scope_entry(path: Path, name: str, *, enabled: bool, spec: dict | None = None) -> str:
    """Add/remove a server from a provider global file (global settings or claude-code).

    When enabled=True and the server is absent, adds the spec.  When
    enabled=False, removes the entry entirely (NOT soft-disable — the
    dashboard badge treats absent and disabled identically).

    🔴 Returns ``"unreadable"`` and writes NOTHING when the target file exists but cannot be read
    or parsed. This used to load ``{}`` and write it back with one server in it, which replaced
    the file — and one of the two files this function is called with is ``~/.claude.json``,
    which PersonalClaw does not own. See ``ConfigUnreadable``.

    Returns ``"refused"`` and writes nothing when copying the server into another tool's file
    would resolve a credential its owner does not hold (``secret_refs.ForeignSecretReference``).
    """
    try:
        data = _load_json_for_update(path)
    except ConfigUnreadable as exc:
        # Refusing is the fix. Reporting it is what makes the refusal actionable rather than a
        # silent no-op the user reads as success.
        logger.warning("mcp: refusing to rewrite %s — %s", path, exc)
        return "unreadable"
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
        entry = {k: v for k, v in spec.items() if k != "disabled"}
        if not _is_personalclaw_document(path):
            # Putting a server into another tool's scope is the user choosing to hand it over,
            # and that tool reads only its own file, so the values go with it — resolved from
            # the credential store, in the one form it understands, against the server's own
            # owner: one naming another owner's credential is not copied at all.
            from personalclaw.config.secret_refs import ForeignSecretReference, foreign_mcp_spec

            try:
                entry = foreign_mcp_spec(name, entry, with_secrets=True)
            except ForeignSecretReference as exc:
                logger.warning("mcp: not copying %r into %s — %s", name, path, exc)
                return "refused"
        servers[name] = entry
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
    try:
        data = _load_json_for_update(_canonical_mcp_json())
    except ConfigUnreadable as exc:
        # Same refusal as `_set_scope_entry`: an unreadable file must not be REPLACED by a
        # dict holding only the entry being written.
        logger.warning("mcp: refusing to rewrite %s — %s", _canonical_mcp_json(), exc)
        raise
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
        _atomic_write(_canonical_mcp_json(), data)
    return changed


async def api_mcp_apply(request: web.Request) -> web.Response:
    """POST /api/mcp/apply — batched per-scope apply for MCP servers.

    Request body::

        {
          "changes": [
            {
              "name": "my-mcp-server",
              "personalclaw": true,     // desired presence in ~/.personalclaw/mcp.json
              "ccGlobal": true,         // optional: desired presence in ~/.claude.json
              "toolOverrides": {        // optional: per-tool enable/disable
                "SkillsTool": false,
                "ReadFile": true
              }
            }
          ]
        }

    Two scopes, two files: PersonalClaw's ``mcp.json`` and Claude Code's ``~/.claude.json``.
    ``personalclaw: true`` for a server ``mcp.json`` lacks copies its spec there from wherever it
    is configured, values included (they go to the credential store on the way in) — the Tools
    page's Import. ``ccGlobal`` absent leaves Claude Code's file exactly as it is: removing a
    server from another tool's config is never a default. A change whose server could not be
    added carries an ``error``, so a caller can tell an import that landed from one that did not.

    Removing a server is ``DELETE /api/mcp/servers/{name}``, not a change here.

    After all changes are written, ``rebuild_agent_config`` is called once so the agent config
    (``~/.personalclaw/agents/personalclaw.json``) reflects the new merged state. Returns a
    summary with per-change outcomes.
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
            # Defense-in-depth: the name flows into filesystem paths via the scope helpers and
            # into the specs other components read, so reject names that contain
            # argv-injection chars or path traversal.
            if not _is_valid_mcp_name(name):
                results.append({"error": "invalid name", "name": name})
                sel().log_api_access(
                    caller="dashboard",
                    operation="mcp_apply_rejected_name",
                    outcome="denied",
                    resources=name[:64],
                )
                continue

            if "uninstall" in change:
                # Removed with the rest of the four delete paths. Refused rather than ignored:
                # ignored, `personalclaw` defaults on, and an old caller's "uninstall" would ADD
                # the server it meant to remove.
                results.append(
                    {
                        "name": name,
                        "error": "Removing a server is not an apply change: send "
                        "DELETE /api/mcp/servers/{name}, which removes it everywhere.",
                    }
                )
                continue

            outcome: dict[str, Any] = {"name": name, "actions": {}}

            # ── Scope toggles: compute desired + apply preservation ──
            desired_mc = bool(change.get("personalclaw", True))
            desired_cc = bool(change["ccGlobal"]) if "ccGlobal" in change else None

            # Preservation rule: if PersonalClaw is desired ON and the server
            # isn't already in ~/.personalclaw/mcp.json, copy its spec there so
            # PClaw owns a runnable copy. This is purely additive — it never
            # removes the server from whatever scope it came from — so it also
            # serves the Tools-page "Import from Claude Code" action. Without this,
            # importing a Claude-Code-only server would be a no-op (nothing to
            # enable in the PClaw scope).
            preserved_spec: dict | None = None
            if desired_mc and not _scope_has_entry(name, _canonical_mcp_json()):
                preserved_spec = _find_server_spec_anywhere(name)

            # Flipping PersonalClaw on needs the entry to exist or the disabled override
            # removed. Flipping it off writes disabled:true, keeping the config for later.
            outcome["actions"]["personalclaw"] = _set_personalclaw_entry(
                name,
                enabled=desired_mc,
                spec=preserved_spec,
            )
            if desired_mc and not _scope_has_entry(name, _canonical_mcp_json()):
                outcome["error"] = f"No MCP server named '{name}' was found to add."

            if desired_cc is not None:
                outcome["actions"]["ccGlobal"] = _set_scope_entry(
                    _CC_GLOBAL_JSON,
                    name,
                    enabled=desired_cc,
                    spec=_find_server_spec_anywhere(name),
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
            # event that belongs in the SEL log.
            sel().log_api_access(
                caller="dashboard",
                operation="mcp_scope_apply",
                outcome="ok",
                resources=(
                    f"{name} "
                    f"mc={'on' if desired_mc else 'off'} "
                    f"cc={'unchanged' if desired_cc is None else 'on' if desired_cc else 'off'}"
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
        # contents), so the text is redacted before it reaches the dashboard.
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
