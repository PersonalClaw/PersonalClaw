"""MCP server discovery — detects configured MCP servers and checks liveness.

Scans the agent config (``agents/defaults.json``) for ``mcpServers`` entries,
then optionally probes each server by spawning the command and sending an
MCP ``initialize`` handshake.

Used by the dashboard to show live MCP server badges and by the heartbeat
to auto-sync newly discovered servers into the agent config.
"""

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from personalclaw.env import augmented_path
from personalclaw.hooks import safe_read_file

logger = logging.getLogger(__name__)

# How long to wait for MCP handshake before marking server as unreachable.
# Configurable via dashboard.mcp_probe_timeout_secs in ~/.personalclaw/config.json.
_PROBE_TIMEOUT_SECS = 15  # fallback if config not loaded yet


def _get_probe_timeout() -> int:
    try:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().dashboard.mcp_probe_timeout_secs
    except Exception:
        return _PROBE_TIMEOUT_SECS


# Probe results expire after 30 minutes → status becomes "outdated"
_PROBE_TTL_SECS = 1800

# Well-known MCP config locations, tagged by scope.  Scope names match
# the dashboard badges (personalclaw / globalMcp / ccGlobal) and are the
# source of truth for the ``presence`` field on each server.
SCOPE_PERSONALCLAW = "personalclaw"
SCOPE_LEGACY_GLOBAL = "globalMcp"
SCOPE_CC_GLOBAL = "ccGlobal"

# Single source of truth pairing each config path with its scope label.
# Priority at collision time is controlled by the explicit scope iteration
# order in :func:`_load_mcp_json` (personalclaw > legacy global), not by this
# tuple's order.
#
# PersonalClaw discovers MCP servers ONLY from its own config scopes. A
# Claude-Code-only server (``~/.claude.json``) is not invocable by the native
# loop, so surfacing it here would imply tools the agent can't call. Such
# servers are instead offered as explicit *import suggestions* via
# :func:`discover_importable_servers` + the ``/api/mcp/apply`` endpoint, which
# copies a chosen spec into ``~/.personalclaw/mcp.json``.
# UT3: ONE canonical MCP store. The former legacy ``settings/mcp.json`` source was
# dropped (its content is migrated into this file once, at startup, by
# handlers/mcp._migrate_legacy_mcp_json) so there is a single read+write path the
# dashboard, the provider instances, agent.py, and the native runtime all share.
_MCP_SOURCES: tuple[tuple[Path, str], ...] = (
    (Path.home() / ".personalclaw" / "mcp.json", SCOPE_PERSONALCLAW),
)

# External backend MCP configs PersonalClaw can *import from* (but never
# silently loads). Each entry maps a config file to the backend label shown in
# the import-suggestions UI. Extensible: add further backend config paths here
# once their formats are confirmed — the discovery + import path is backend-agnostic.
_IMPORT_SOURCES: tuple[tuple[Path, str], ...] = ((Path.home() / ".claude.json", "Claude Code"),)

# Test override seam: tests monkeypatch this to inject fixture paths.
# Derived from :data:`_MCP_SOURCES` so the two can never drift.
_MCP_JSON_PATHS: tuple[Path, ...] = tuple(p for p, _ in _MCP_SOURCES)


@dataclass
class _ProbeResult:
    """Cached probe result for a single server."""

    status: str
    tools: list[dict[str, Any]]  # each entry: {"name", "description", "inputSchema"}
    error: str
    probed_at: float


# Module-level probe cache: server name → result
_probe_cache: dict[str, _ProbeResult] = {}


def _get_cached(name: str) -> tuple[str, list[dict[str, Any]], str]:
    """Return (status, tools, error) from cache.

    If within TTL: returns original status + tools.
    If expired: returns "outdated" + tools (tools always preserved).
    If not cached: returns ("unknown", [], "").
    """
    cached = _probe_cache.get(name)
    if cached is None:
        return "unknown", [], ""
    age = time.monotonic() - cached.probed_at
    if age <= _PROBE_TTL_SECS:
        return cached.status, cached.tools, cached.error
    # Expired — mark outdated but preserve tools
    return "outdated", cached.tools, ""


def _cache_probe(server: "McpServerInfo") -> None:
    """Store probe result in cache."""
    _probe_cache[server.name] = _ProbeResult(
        status=server.status,
        tools=list(server.tools),
        error=server.error,
        probed_at=time.monotonic(),
    )


@dataclass
class McpServerInfo:
    """Metadata for a single MCP server (local stdio or remote HTTP)."""

    name: str
    command: str = ""
    args: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""  # working dir for the spawn (app-shipped servers set this to the app dir)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    status: str = "unknown"  # unknown | ok | error | probing | outdated
    # Each tool entry is a dict with at least "name"; optionally "description"
    # and "inputSchema" populated by tools/list responses. Plain strings are
    # also accepted on input and normalized to dicts at probe.
    tools: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    source: str = "agent"  # agent | mcp.json | discovered
    presence: dict[str, bool] = field(
        default_factory=lambda: {
            SCOPE_PERSONALCLAW: False,
            SCOPE_LEGACY_GLOBAL: False,
            SCOPE_CC_GLOBAL: False,
        }
    )
    disabled_tools: list[str] = field(default_factory=list)

    @property
    def is_remote(self) -> bool:
        """True for Streamable HTTP servers (url-based, no command)."""
        return bool(self.url) and not self.command

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "command": self.command,
            "args": self.args or [],
            "status": self.status,
            "tools": self.tools,
            "error": self.error,
            "source": self.source,
            "presence": dict(self.presence),
        }
        if self.url:
            d["url"] = self.url
            if self.headers:
                d["headers"] = self.headers
        if self.cwd:
            d["cwd"] = self.cwd
        if self.disabled_tools:
            d["disabledTools"] = self.disabled_tools
        return d


def _load_agent_config() -> dict[str, Any]:
    """Load the agent config to read mcpServers.

    Merges mcpServers from project-dir (if set), bundled defaults.json,
    AND the installed personalclaw.json — because defaults.json may not have
    mcpServers (they're added dynamically at install time by ``personalclaw setup``).
    """
    configs: list[dict[str, Any]] = []

    # Project-dir override (development)
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR")
    if proj:
        p = Path(proj) / "agents" / "defaults.json"
        if p.is_file():
            try:
                configs.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass

    # Bundled defaults.json (fallback when no project-dir)
    if not configs:
        bundled = Path(__file__).resolve().parent / "config" / "defaults.json"
        if bundled.is_file():
            try:
                configs.append(json.loads(bundled.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass

    # Installed agent config (always check for mcpServers)
    from personalclaw.agent import AGENT_FILENAME  # circular import: agent imports mcp_discovery

    installed = Path.home() / ".personalclaw" / "agents" / AGENT_FILENAME
    if installed.is_file():
        try:
            configs.append(json.loads(installed.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass

    if not configs:
        return {}

    # Merge: use first config as base, merge mcpServers from all sources
    merged = dict(configs[0])
    mcp: dict[str, Any] = dict(merged.get("mcpServers", {}))
    for cfg in configs[1:]:
        for name, spec in cfg.get("mcpServers", {}).items():
            if name not in mcp:
                mcp[name] = spec
    merged["mcpServers"] = mcp
    return merged


def _load_mcp_json_by_source() -> dict[str, dict[str, Any]]:
    """Return ``{scope: {name: spec}}`` keyed by scope name.

    Reads every well-known MCP config location and bucketizes servers by
    their origin scope.  Unlike :func:`_load_mcp_json`, no cross-source
    merging happens — callers that need per-scope presence use this.

    Iterates :data:`_MCP_SOURCES` (path + scope pairs), so paths and scope
    labels can never drift.  When tests monkeypatch :data:`_MCP_JSON_PATHS`
    to a shorter tuple for isolation, the corresponding scopes are
    recovered by looking up each patched path in ``_MCP_SOURCES``; any
    unknown path falls back to :data:`SCOPE_PERSONALCLAW`.
    """
    result: dict[str, dict[str, Any]] = {
        SCOPE_PERSONALCLAW: {},
        SCOPE_LEGACY_GLOBAL: {},
    }
    path_to_scope = {p: scope for p, scope in _MCP_SOURCES}
    for p in _MCP_JSON_PATHS:
        scope = path_to_scope.get(p, SCOPE_PERSONALCLAW)
        if not p.is_file():
            continue
        try:
            data = json.loads(safe_read_file(str(p)))
        except (json.JSONDecodeError, OSError) as exc:
            # PermissionError (subclass of OSError) is raised by
            # safe_read_file when is_sensitive_path() blocks the read.
            logger.warning("Failed to load MCP config from %s: %s", p, exc)
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers", {})
        if isinstance(servers, dict):
            # Merge instead of overwriting — if two paths resolve to the
            # same scope (legitimate duplicates, or tests that monkeypatch
            # _MCP_JSON_PATHS with fallback-scoped paths), setdefault keeps
            # first-wins semantics within the scope.
            bucket = result[scope]
            for name, spec in servers.items():
                bucket.setdefault(name, spec)
    return result


def _load_mcp_json() -> dict[str, Any]:
    """Load and merge mcpServers from all well-known mcp.json locations.

    Earlier paths take precedence — if the same server name appears in
    multiple files, the first definition wins (via ``setdefault``).
    Retained for callers that only need a merged view; use
    :func:`_load_mcp_json_by_source` when per-scope presence matters.
    """
    merged: dict[str, Any] = {}
    by_source = _load_mcp_json_by_source()
    # Iteration order = priority (setdefault is a no-op once populated):
    # personalclaw-specific file > legacy global. Matches rebuild_agent_config's
    # merge order in agent.py.
    for scope in (SCOPE_PERSONALCLAW, SCOPE_LEGACY_GLOBAL):
        for name, spec in by_source.get(scope, {}).items():
            merged.setdefault(name, spec)
    return merged


def _server_from_spec(name: str, spec: dict, source: str) -> McpServerInfo:
    return McpServerInfo(
        name=name,
        command=spec.get("command", ""),
        args=spec.get("args", []),
        env=spec.get("env", {}),
        cwd=spec.get("cwd", ""),
        url=spec.get("url", ""),
        headers=spec.get("headers", {}),
        source=source,
    )


_MANAGED_SERVER_NAMES = {"personalclaw-core", "personalclaw-schedule"}

# Cached resolved binary path — avoids subprocess.run on every list_servers() call.
_resolved_managed_bin: str | None = None


def _fix_stale_managed_command(name: str, spec: dict) -> None:
    """Re-resolve command for managed MCP servers to the running binary.

    Always re-resolves — the stored path may exist as a file/symlink but
    still crash at runtime (e.g. stale build output after a reinstall).
    The running gateway knows its own binary, so we always overwrite.
    """
    if name not in _MANAGED_SERVER_NAMES:
        return
    global _resolved_managed_bin
    # Use cached result to avoid blocking subprocess.run on every call.
    if _resolved_managed_bin:
        cmd = spec.get("command", "")
        if cmd != _resolved_managed_bin:
            logger.info("Re-resolved %s command: %s → %s", name, cmd, _resolved_managed_bin)
            spec["command"] = _resolved_managed_bin
        return
    resolved: str | None = None
    # Prefer a personalclaw binary on the user's augmented PATH.
    if not resolved:
        resolved = shutil.which("personalclaw", path=augmented_path(os.environ.get("PATH", "")))
    if not resolved:
        return
    _resolved_managed_bin = resolved
    cmd = spec.get("command", "")
    if cmd != resolved:
        logger.info("Re-resolved %s command: %s → %s", name, cmd, resolved)
        spec["command"] = resolved


def list_servers() -> list[McpServerInfo]:
    """Return all known MCP servers from agent config + mcp.json + CC global.

    Merges cached probe results so status/tools survive across requests.
    Populates ``presence`` for each server with booleans for whether the
    server appears in each of the three scope config files.

    Servers that live only in a provider global (e.g. a user added one via
    directly to ``~/.claude.json``) still show up
    on the dashboard so users get a full inventory from one page.
    """
    servers: dict[str, McpServerInfo] = {}
    disabled_in_agent: set[str] = set()

    # 1. From agent config (mcpServers key)
    agent_cfg = _load_agent_config()
    for name, spec in agent_cfg.get("mcpServers", {}).items():
        if isinstance(spec, dict):
            if spec.get("disabled"):
                disabled_in_agent.add(name)
            else:
                # Re-resolve stale managed MCP server paths at runtime
                _fix_stale_managed_command(name, spec)
                servers[name] = _server_from_spec(name, spec, "agent")

    # 2. From scope-tagged mcp.json sources, in priority order so highest-
    #    priority scope populates disabled_tools first and lower scopes
    #    don't overwrite it.  Order = personalclaw-specific > CC global >
    #    Legacy global, matching rebuild_agent_config's merge priority.
    by_source = _load_mcp_json_by_source()
    disabled_tools_claimed: set[str] = set()
    for scope in (SCOPE_PERSONALCLAW, SCOPE_LEGACY_GLOBAL):
        for name, spec in by_source.get(scope, {}).items():
            if not isinstance(spec, dict):
                continue
            # Introduce the server first (if new) so the disabledTools
            # carry below applies to both new and existing entries.  Without
            # this ordering, the highest-priority scope's disabledTools is
            # dropped for new servers because `name in servers` is False
            # before insertion, letting a lower-priority scope's value
            # overwrite the (empty) default on a later iteration.
            if not spec.get("disabled") and name not in servers and name not in disabled_in_agent:
                servers[name] = _server_from_spec(name, spec, "mcp.json")

            # Per-tool disables: first-scope-wins.  Use "disabledTools" in
            # spec (key presence) rather than truthiness so an explicit
            # "disabledTools": [] (user intent: "all tools enabled") is
            # respected and prevents lower-priority scopes from overwriting.
            if name in servers and "disabledTools" in spec and name not in disabled_tools_claimed:
                servers[name].disabled_tools = spec.get("disabledTools", [])
                disabled_tools_claimed.add(name)

    # 3. Compute per-scope presence.
    #
    #    MC presence = "will this load in PersonalClaw sessions after the next
    #    rebuild".  A server present in any PClaw scope source (or already in
    #    the current merged agent config) counts as MC green unless PersonalClaw
    #    has an explicit ``disabled: true`` override.  ``ccGlobal`` presence is
    #    always False here: PersonalClaw does not read ``~/.claude.json`` as a
    #    discovery source — Claude-Code servers are surfaced only as explicit
    #    import suggestions (see :func:`discover_importable_servers`).
    agent_names = set(agent_cfg.get("mcpServers", {}).keys())
    personalclaw_own = by_source.get(SCOPE_PERSONALCLAW, {})
    for name, server in servers.items():
        pc_disabled = (
            isinstance(personalclaw_own.get(name), dict)
            and personalclaw_own[name].get("disabled") is True
        )
        in_any_source = (
            name in agent_names
            or name in personalclaw_own
            or name in by_source.get(SCOPE_LEGACY_GLOBAL, {})
        )
        server.presence = {
            SCOPE_PERSONALCLAW: in_any_source and not pc_disabled,
            SCOPE_LEGACY_GLOBAL: name in by_source.get(SCOPE_LEGACY_GLOBAL, {}),
            SCOPE_CC_GLOBAL: False,
        }

    # 4. Merge cached probe results
    for s in servers.values():
        status, tools, error = _get_cached(s.name)
        s.status = status
        s.tools = tools
        s.error = error

    return list(servers.values())


async def _read_jsonrpc_response(resp: aiohttp.ClientResponse) -> dict:
    """Parse a JSON-RPC response from either JSON or SSE content-type.

    MCP Streamable HTTP servers may respond with ``application/json`` (single
    object) or ``text/event-stream`` (SSE with ``data:`` lines containing JSON).
    """
    ct = resp.content_type or ""
    if "text/event-stream" in ct:
        body = await resp.text()
        last: dict = {}
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if payload:
                    try:
                        parsed = json.loads(payload)
                        if isinstance(parsed, dict) and "id" in parsed:
                            last = parsed
                    except json.JSONDecodeError:
                        pass
        return last
    return await resp.json()


async def _probe_remote(server: McpServerInfo) -> McpServerInfo:
    """Probe a remote Streamable HTTP MCP server via POST."""
    server.status = "probing"
    try:
        init_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "personalclaw-probe", "version": "1.0.0"},
            },
        }
        hdrs = {
            **server.headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        timeout = aiohttp.ClientTimeout(total=_get_probe_timeout())
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(server.url, json=init_body, headers=hdrs) as resp:
                if resp.status != 200:
                    server.status = "error"
                    server.error = f"HTTP {resp.status}"
                    _cache_probe(server)
                    return server
                data = await _read_jsonrpc_response(resp)
                if data.get("error"):
                    server.status = "error"
                    err = data["error"]
                    server.error = (
                        err.get("message", "unknown error") if isinstance(err, dict) else str(err)
                    )
                    _cache_probe(server)
                    return server

            list_body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            async with session.post(server.url, json=list_body, headers=hdrs) as resp:
                if resp.status == 200:
                    data = await _read_jsonrpc_response(resp)
                    tools_data = data.get("result", {}).get("tools", [])
                    server.tools = [
                        {
                            "name": t.get("name", ""),
                            "description": t.get("description", ""),
                            "inputSchema": t.get("inputSchema", {}),
                        }
                        for t in tools_data
                        if isinstance(t, dict) and t.get("name")
                    ]

        server.status = "ok"
    except asyncio.TimeoutError:
        server.status = "error"
        server.error = "timeout"
        logger.warning("MCP probe failed [%s]: timeout", server.name)
    except Exception as exc:
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)

    _cache_probe(server)
    return server


async def _drain_stderr_reason(proc: Any) -> str:
    """Read whatever the server wrote to stderr, condensed to a one-line reason.

    Used when stdout is empty (server exited before responding) so the failure
    is legible — e.g. ``server exited: Node version 18 detected, requires >=20``
    instead of a bare ``no response``. Bounded read + short timeout so a server
    that holds stderr open can't hang the probe.
    """
    if proc is None or proc.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(proc.stderr.read(4096), timeout=2.0)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return ""
    text = (data or b"").decode("utf-8", "replace").strip()
    if not text:
        return ""
    # Last non-empty line is usually the actionable error.
    last = [ln.strip() for ln in text.splitlines() if ln.strip()]
    reason = last[-1] if last else text
    return f"server exited: {reason[:200]}"


async def probe_server(server: McpServerInfo) -> McpServerInfo:
    """Probe a single MCP server by spawning it and sending initialize.

    Updates server.status and server.tools in place and returns it.
    """
    if server.is_remote:
        return await _probe_remote(server)

    if not server.command:
        server.status = "error"
        server.error = "no command"
        logger.warning("MCP probe failed [%s]: no command configured", server.name)
        return server

    server.status = "probing"
    proc = None
    try:
        env = dict(os.environ)
        env["PATH"] = augmented_path(env.get("PATH", ""))
        # Merge server-specific env additively
        if "PATH" in server.env:
            env["PATH"] = server.env["PATH"] + os.pathsep + env["PATH"]
        env.update({k: v for k, v in server.env.items() if k != "PATH"})

        # Resolve command to absolute path using the merged env PATH
        resolved = shutil.which(server.command, path=env.get("PATH"))
        if not resolved:
            server.status = "error"
            server.error = f"command not found: {server.command}"
            logger.warning(
                "MCP probe failed [%s]: command not found: %s", server.name, server.command
            )
            return server

        proc = await asyncio.create_subprocess_exec(
            resolved,
            *(server.args or []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            # An app-shipped server sets cwd to its app dir so relative args
            # (e.g. "backend/mcp_server.py") resolve; None keeps the gateway cwd.
            cwd=server.cwd or None,
            limit=1024 * 1024,  # 1 MB
        )

        # Send initialize request
        init_req = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "personalclaw-probe", "version": "1.0.0"},
                    },
                }
            )
            + "\n"
        )

        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(init_req.encode())
        await proc.stdin.drain()

        # Read initialize response
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=_get_probe_timeout())
        if not line:
            server.status = "error"
            # Empty stdout usually means the server exited before responding
            # (a common cause: wrong Node/interpreter version). Surface the
            # child's stderr so the reason is legible instead of "no response".
            server.error = await _drain_stderr_reason(proc) or "no response"
            return server

        resp = json.loads(line.decode())
        if "error" in resp:
            server.status = "error"
            server.error = resp["error"].get("message", "unknown error")
            return server

        # Send initialized notification
        notif = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }
            )
            + "\n"
        )
        proc.stdin.write(notif.encode())
        await proc.stdin.drain()

        # Request tool list
        list_req = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            )
            + "\n"
        )
        proc.stdin.write(list_req.encode())
        await proc.stdin.drain()

        line2 = await asyncio.wait_for(proc.stdout.readline(), timeout=_get_probe_timeout())
        if line2:
            resp2 = json.loads(line2.decode())
            tools_data = resp2.get("result", {}).get("tools", [])
            server.tools = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema", {}),
                }
                for t in tools_data
                if isinstance(t, dict) and t.get("name")
            ]

        server.status = "ok"

    except asyncio.TimeoutError:
        server.status = "error"
        server.error = "timeout"
        logger.warning(
            "MCP probe failed [%s]: timeout after %ds", server.name, _get_probe_timeout()
        )
    except FileNotFoundError:
        server.status = "error"
        server.error = f"command not found: {server.command}"
        logger.warning("MCP probe failed [%s]: command not found: %s", server.name, server.command)
    except Exception as exc:
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)
    finally:
        if proc is not None and proc.returncode is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, Exception):
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass

    _cache_probe(server)
    return server


async def probe_one(name: str) -> McpServerInfo | None:
    """Probe a SINGLE configured MCP server by name — backs per-provider reconnect
    so a user can recover one timed-out server without re-probing the whole fleet
    (a slow/erroring server shouldn't force a full re-probe). Returns the probed
    info, or None if no server by that name is configured."""
    server = next((s for s in list_servers() if s.name == name), None)
    if server is None:
        return None
    try:
        return await probe_server(server)
    except Exception as exc:  # noqa: BLE001
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", name, server.error)
        return server


async def probe_all() -> list[McpServerInfo]:
    """Discover and probe all configured MCP servers."""
    servers = list_servers()
    if not servers:
        return []
    results = await asyncio.gather(
        *(probe_server(s) for s in servers),
        return_exceptions=True,
    )
    out: list[McpServerInfo] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            servers[i].status = "error"
            servers[i].error = str(r)[:200]
            logger.warning("MCP probe failed [%s]: %s", servers[i].name, servers[i].error)
            out.append(servers[i])
        else:
            out.append(r)  # type: ignore[arg-type]
    return out


def discover_servers_to_sync() -> list[McpServerInfo]:
    """Find MCP servers in mcp.json that need syncing to the agent config.

    Returns new servers not yet in the agent config, plus existing servers
    whose env, command, or args have diverged from the mcp.json source.
    """
    agent_cfg = _load_agent_config()
    agent_mcp = agent_cfg.get("mcpServers", {})
    agent_names = set(agent_mcp.keys())
    mcp_servers = _load_mcp_json()

    out: list[McpServerInfo] = []
    for name, spec in mcp_servers.items():
        if not isinstance(spec, dict):
            continue
        info = McpServerInfo(
            name=name,
            command=spec.get("command", ""),
            args=spec.get("args"),
            env=spec.get("env") or {},
            source="discovered",
        )
        if name not in agent_names:
            out.append(info)
        else:
            # Include existing local servers with divergent fields
            existing = agent_mcp[name]
            if not isinstance(existing, dict) or info.is_remote:
                continue
            existing_env = existing.get("env", {})
            if not isinstance(existing_env, dict):
                existing_env = {}
            if (
                not all(existing_env.get(k) == v for k, v in info.env.items())
                or info.command != existing.get("command", "")
                or (info.args is not None and info.args != existing.get("args", []))
            ):
                out.append(info)
    return out


# Test override seam: tests monkeypatch this to inject fixture import paths.
_IMPORT_JSON_PATHS: tuple[tuple[Path, str], ...] = _IMPORT_SOURCES


def discover_importable_servers() -> list[dict[str, Any]]:
    """Return MCP servers configured in an external backend (e.g. Claude Code)
    that are NOT yet present in any PersonalClaw scope — i.e. candidates the
    user can *import* into ``~/.personalclaw/mcp.json`` to make them callable by
    the native loop.

    PersonalClaw does not silently load these (the native loop can't reach a
    Claude-Code-only server). The UI offers each as an explicit "Import" action
    backed by ``/api/mcp/apply``, which copies the spec into the PClaw scope.

    Each entry: ``{name, backend, command, args, env, url, headers}`` — enough
    to render the suggestion and round-trip the spec on import.
    """
    # Servers already known to PClaw (own scope + legacy global + agent config)
    # are not "importable" — they're already first-class.
    known: set[str] = set(_load_mcp_json().keys())
    known |= set(_load_agent_config().get("mcpServers", {}).keys())

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, backend in _IMPORT_JSON_PATHS:
        if not path.is_file():
            continue
        try:
            data = json.loads(safe_read_file(str(path)))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read import source %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            continue
        for name, spec in servers.items():
            if not isinstance(spec, dict) or name in known or name in seen:
                continue
            # Only surface servers we can actually run: a stdio command or a
            # remote url. Skip malformed entries silently.
            if not (spec.get("command") or spec.get("url")):
                continue
            seen.add(name)
            out.append(
                {
                    "name": name,
                    "backend": backend,
                    "command": spec.get("command", ""),
                    "args": spec.get("args", []),
                    "env": spec.get("env", {}),
                    "url": spec.get("url", ""),
                    "headers": spec.get("headers", {}),
                }
            )
    return out


def sync_to_agent_config(servers: list[McpServerInfo]) -> bool:
    """Sync discovered MCP servers into the agent config.

    Delegates to ``rebuild_agent_config()`` which is the single authoritative merge
    function.  ``rebuild_agent_config()`` reads all source files
    (``~/.personalclaw/mcp.json``), merges them with correct priority, resolves
    commands, injects fresh marketplace skill paths, and writes the final
    ``personalclaw.json``.

    Returns True if any servers were added or the config was refreshed.
    """
    from personalclaw.agent import (  # circular import
        AGENT_FILENAME,
        AGENTS_DIR,
        rebuild_agent_config,
    )

    config_path = AGENTS_DIR / AGENT_FILENAME

    # Determine which servers are genuinely new (not yet in agent config)
    existing_names: set[str] = set()
    try:
        pre = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(pre, dict):
            existing_names = set(pre.get("mcpServers", {}).keys())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    new_servers = [s for s in servers if s.name not in existing_names]
    added = bool(new_servers)

    # Delegate the actual config merge to rebuild_agent_config() — the single
    # authoritative function that reads all sources, merges with correct
    # priority, resolves paths, and injects fresh marketplace skill paths.
    rebuild_agent_config()

    # Audit: log which servers triggered the config rebuild
    try:
        from personalclaw.sel import sel  # circular import

        sel().log_api_access(
            caller="system",
            operation="mcp_server_config_sync",
            outcome="ok",
            source="agent",
            resources=", ".join(s.name for s in servers),
        )
    except Exception:
        logger.debug("SEL audit log failed for mcp_server_config_sync", exc_info=True)

    return added or bool(servers)


def register_servers_for_cc(
    servers: list[McpServerInfo],
    mcp_json_path: Path | None = None,
) -> bool:
    """Register MCP servers in CC format (.mcp.json).

    Adds entries without removing existing ones. CC-side complement
    to sync_to_agent_config() which handles agent-side registration.

    Returns True if any servers were added or updated.
    """
    if mcp_json_path is None:
        mcp_json_path = Path.home() / ".mcp.json"

    existing: dict = {}
    if mcp_json_path.is_file():
        try:
            existing = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    mcp = existing.setdefault("mcpServers", {})
    changed = False

    for s in servers:
        if s.is_remote:
            entry: dict = {"url": s.url, "type": "streamable-http"}
            if s.headers:
                entry["headers"] = s.headers
        else:
            entry = {"command": s.command, "args": s.args or [], "type": "stdio"}
            if s.env:
                entry["env"] = s.env

        if s.name not in mcp or mcp[s.name] != entry:
            mcp[s.name] = entry
            changed = True
            logger.info("Registered MCP server for CC: %s", s.name)

    if changed:
        mcp_json_path.parent.mkdir(parents=True, exist_ok=True)
        from personalclaw.agent import (
            _atomic_json_write,  # circular import: agent imports mcp_discovery
        )

        _atomic_json_write(mcp_json_path, existing)

    return changed
