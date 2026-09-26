"""MCP server discovery — detects configured MCP servers and checks liveness.

Scans the agent config (``agents/defaults.json``) for ``mcpServers`` entries,
then optionally probes each server: a stdio server by spawning its command and sending an
MCP ``initialize`` handshake, a server at a URL through the native client, over its own
transport and with its headers.

Used by the dashboard to show live MCP server badges and by the heartbeat
to auto-sync newly discovered servers into the agent config.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from personalclaw.apps.secret_fields import SECRET_MASK, is_credential_field_name
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


# PersonalClaw discovers MCP servers ONLY from its own config. A Claude-Code-only server
# (``~/.claude.json``) is not invocable by the native loop, so surfacing it here would imply
# tools the agent can't call. Such servers are instead offered as explicit *import
# suggestions* via :func:`discover_importable_servers` + the ``/api/mcp/apply`` endpoint,
# which copies a chosen spec into ``~/.personalclaw/mcp.json``.
#
# UT3: ONE canonical MCP store. The former legacy ``settings/mcp.json`` source was dropped (its
# content is migrated into this file once, at startup, by handlers/mcp._migrate_legacy_mcp_json)
# so there is a single read+write path the dashboard, the provider instances, agent.py, and the
# native runtime all share. The "global" scope that file used to be is gone with it: a second
# scope NAME left pointing at the one file is how Import added a server and removed it again in
# the same request.
#
# A FUNCTION, not a module constant: a `Path.home()` value computed at import time is
# frozen before `PERSONALCLAW_HOME` can matter, so an isolated dev home read the operator's
# real `~/.personalclaw/mcp.json` — their servers and their credentials — while the
# dashboard wrote the dev home. Resolved per call, through `config_dir()`.
def _mcp_json_paths() -> tuple[Path, ...]:
    """The MCP config files discovery reads. Tests monkeypatch this to inject fixture paths
    (several patch more than one, to exercise first-wins precedence)."""
    from personalclaw.config.loader import config_dir

    return (config_dir() / "mcp.json",)


def _import_sources() -> tuple[tuple[Path, str], ...]:
    """External backend MCP configs PersonalClaw can *import from* (but never silently loads),
    each with the backend label the import suggestions show. Extensible: add further backend
    config paths here once their formats are confirmed — the discovery + import path is
    backend-agnostic.

    A FUNCTION, like :func:`_mcp_json_paths`: this was ``Path.home() / ".claude.json"`` frozen at
    import, so it ignored ``$CLAUDE_CONFIG_DIR`` while the onboarding importer honoured it. Claude
    Code's file now comes from the resolver that importer uses, per call. Tests monkeypatch this.
    """
    from personalclaw.onboarding_import.sources import claude_code

    return ((claude_code.global_config_path(), claude_code.DISPLAY_NAME),)


# ── transports ──────────────────────────────────────────────────────────────
#
# A server spec says how it is reached in ONE key, ``type``, with one of three values. It is
# Claude Code's key and spelling (and VS Code's), so a spec copied between PersonalClaw and Claude
# Code keeps its transport in both directions. Every reader asks :func:`mcp_transport` — the
# native client, the probe, the rebuild, the edit form, the provider card — so no two of them can
# disagree about what a server is. Before, the client read only ``transport``, so an imported
# ``"type": "http"`` server was opened as SSE, while the probe posted to every URL as Streamable
# HTTP.

#: The transports PersonalClaw connects over: a spawned ``command``, Streamable HTTP, and the older
#: HTTP+SSE transport.
MCP_TRANSPORTS = ("stdio", "http", "sse")

#: Other names for Streamable HTTP in server configs. ``streamable-http`` is also what PersonalClaw
#: itself wrote into ``~/.mcp.json`` before, which Claude Code's schema does not accept.
_TRANSPORT_ALIASES = {
    "streamable-http": "http",
    "streamable_http": "http",
    "streamablehttp": "http",
}


def mcp_transport(spec: Mapping[str, Any]) -> str:
    """How PersonalClaw connects to the server ``spec`` describes: ``stdio``, ``http`` or ``sse``.

    The declared ``type`` decides (``transport``, the other key server configs use for it, when
    ``type`` is absent). With neither, a ``url`` and no ``command`` is ``sse``: what a bare URL has
    always meant to the native client, and what the MCP Tool Servers card and pack connectors meant
    when they wrote one. Anything else is ``stdio``. A declared transport PersonalClaw has no client
    for (``ws``) comes back as it is, so the caller can say so instead of guessing.
    """
    declared = spec.get("type") or spec.get("transport")
    if isinstance(declared, str) and declared.strip():
        name = declared.strip().lower()
        return _TRANSPORT_ALIASES.get(name, name)
    return "sse" if spec.get("url") and not spec.get("command") else "stdio"


def server_name_problem(name: str) -> str | None:
    """Why *name* cannot be an MCP server's name, or ``None`` when it can.

    A server's tools are named ``mcp/<server>/<tool>`` and read back at the first two slashes, so
    a ``/`` in the server's name makes its tools' names another server's: ``github/admin``'s
    ``delete_repo`` is ``mcp/github/admin/delete_repo``, which is also the ``github`` server's
    ``admin/delete_repo``, and a call to either reaches ``github``. One rule for every way a server
    arrives (an import, an app's own servers) and for one already in ``mcp.json``: it is not
    started, and this sentence is its status.
    """
    if "/" not in name:
        return None
    first = name.split("/", 1)[0]
    return (
        f"A server's name cannot contain '/': its tools would be named mcp/{name}/<tool>, which "
        f"reads as the tools of a server named '{first}', and a call to one would go to that "
        f"server. Rename '{name}' where it is configured, without '/'."
    )


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
    """Metadata for a single MCP server (local stdio, or remote over Streamable HTTP or SSE)."""

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
    disabled_tools: list[str] = field(default_factory=list)
    #: ``stdio``, ``http`` or ``sse`` (:func:`mcp_transport`). Left empty, it is derived from
    #: ``url`` and ``command`` exactly as for a spec that declares none.
    transport: str = ""

    def __post_init__(self) -> None:
        self.transport = mcp_transport(
            {"type": self.transport, "url": self.url, "command": self.command}
        )

    @property
    def is_remote(self) -> bool:
        """True for a server reached at a URL rather than spawned from a command."""
        return self.transport != "stdio"

    def to_dict(self) -> dict[str, Any]:
        """The server as the Tools page reads it: its name, how it is reached, and its state.

        Never its definition. Arguments and a URL can carry a token (``--api-key …``,
        ``?token=…``), the page shows neither, and it keeps this response in session storage;
        so ``command``, ``args``, ``url``, ``headers`` and ``cwd`` stay on the server. The edit
        form reads one server's definition from ``GET /api/mcp/servers/{name}`` when it opens.
        """
        d: dict[str, Any] = {
            "name": self.name,
            "transport": self.transport,
            "status": self.status,
            "tools": self.tools,
            "error": self.error,
            "source": self.source,
        }
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

    # Installed agent config (always check for mcpServers). Resolved through `config_dir()`,
    # not a `Path.home()` hardcode: this spelled the real home outright, so a gateway on a
    # custom PERSONALCLAW_HOME discovered the OPERATOR's MCP servers.
    #
    # Through `agent.agents_dir()`, which is the ONE owner of `<home>/agents` (#3463). This
    # used to spell `config_dir() / "agents"` itself and say why: `agent.AGENTS_DIR` was a
    # module-level constant evaluated at IMPORT time, so it froze whatever the home was then
    # — measurably, routing this through it made four `TestListServers` cases read the real
    # installed config. That is fixed at the source now, so the workaround is no longer a
    # workaround and the duplicated derivation is gone with it.
    from personalclaw.agent import AGENT_FILENAME  # circular import: agent imports mcp_discovery
    from personalclaw.agent import agents_dir

    installed = agents_dir() / AGENT_FILENAME
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


def _load_mcp_json() -> dict[str, Any]:
    """``mcpServers`` from every path :func:`_mcp_json_paths` names, merged.

    Earlier paths take precedence — if the same server name appears in
    multiple files, the first definition wins (via ``setdefault``).
    """
    merged: dict[str, Any] = {}
    for p in _mcp_json_paths():
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
            for name, spec in servers.items():
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
        transport=mcp_transport(spec),
    )


_MANAGED_SERVER_NAMES = {"personalclaw-core"}

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
    """Return all known MCP servers from the agent config and ``mcp.json``.

    Merges cached probe results so status/tools survive across requests. A server configured
    only in another tool (Claude Code) is not listed here: it is an import suggestion
    (:func:`discover_importable_servers`), because the native loop cannot call it.
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

    # 2. From mcp.json. Introduce the server first (if new) so the disabledTools carry below
    #    applies to new and existing entries alike. "disabledTools" by key presence, not
    #    truthiness: an explicit [] ("every tool enabled") is the user's answer too.
    for name, spec in _load_mcp_json().items():
        if not isinstance(spec, dict):
            continue
        if not spec.get("disabled") and name not in servers and name not in disabled_in_agent:
            servers[name] = _server_from_spec(name, spec, "mcp.json")
        if name in servers and "disabledTools" in spec:
            servers[name].disabled_tools = spec.get("disabledTools", [])

    # 3. Merge cached probe results
    for s in servers.values():
        status, tools, error = _get_cached(s.name)
        s.status = status
        s.tools = tools
        s.error = error

    return list(servers.values())


async def _probe_remote(server: McpServerInfo) -> McpServerInfo:
    """Probe a server at a URL through the native client — the connection an agent's tool call
    makes, over the server's own transport and with its headers — so ``ok`` here means an agent
    can use it.

    This used to be a hand-written Streamable HTTP exchange: it posted to every URL, so an SSE
    server always read as an error, and it dropped the session id between ``initialize`` and
    ``tools/list``, so a stateful server read ``ok`` with no tools.
    """
    from personalclaw.config.secret_refs import ForeignSecretReference, resolve_mcp_values
    from personalclaw.mcp_client import McpServerConn, mcp_sdk_available

    try:
        # The spec holds `{{secret:…}}` references; the header values are resolved here, where the
        # connection is made, and never written anywhere — only keys the server's owner holds.
        headers = resolve_mcp_values(server.name, "headers", server.headers)
    except ForeignSecretReference as exc:
        # No request was made and no value was read: the whole sentence is this server's error.
        server.status = "error"
        server.error = str(exc)
        _cache_probe(server)
        return server
    server.status = "probing"
    if server.transport not in MCP_TRANSPORTS:
        server.status = "error"
        server.error = f"PersonalClaw cannot connect over the {server.transport!r} transport"
    elif not mcp_sdk_available():
        server.status = "error"
        server.error = (
            "connecting to a server at a URL needs the 'mcp' extra "
            "(pip install 'personalclaw[mcp]')"
        )
    else:
        conn = McpServerConn(
            server.name, {"type": server.transport, "url": server.url, "headers": headers}
        )
        try:
            tools = await asyncio.wait_for(conn.list_tools(), timeout=_get_probe_timeout())
            if conn.error:
                server.status = "error"
                server.error = conn.error
            else:
                server.status = "ok"
                server.tools = [
                    {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                    for t in tools
                ]
        except asyncio.TimeoutError:
            server.status = "error"
            server.error = "timeout"
        finally:
            await conn.shutdown()
    if server.status == "error":
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
    problem = server_name_problem(server.name)
    if problem is not None:
        # Never started, by the native client or here: the sentence is this server's status.
        server.status = "error"
        server.error = problem
        _cache_probe(server)
        return server

    if server.is_remote:
        return await _probe_remote(server)

    if not server.command:
        server.status = "error"
        server.error = "no command"
        logger.warning("MCP probe failed [%s]: no command configured", server.name)
        return server

    from personalclaw.config.secret_refs import ForeignSecretReference, resolve_mcp_values

    try:
        # The spec holds `{{secret:…}}` references; the values are resolved here, at spawn, and
        # reach only the child's environment — only keys the server's owner holds.
        server_env = resolve_mcp_values(server.name, "env", server.env)
    except ForeignSecretReference as exc:
        # Nothing was spawned and no value was read: the whole sentence is this server's error.
        server.status = "error"
        server.error = str(exc)
        _cache_probe(server)
        return server
    server.status = "probing"
    proc = None
    try:
        env = dict(os.environ)
        env["PATH"] = augmented_path(env.get("PATH", ""))
        # Merge server-specific env additively
        if "PATH" in server_env:
            env["PATH"] = server_env["PATH"] + os.pathsep + env["PATH"]
        env.update({k: v for k, v in server_env.items() if k != "PATH"})

        # Resolve command to absolute path using the merged env PATH
        resolved = shutil.which(server.command, path=env.get("PATH"))
        if not resolved:
            server.status = "error"
            server.error = f"command not found: {server.command}"
            logger.warning(
                "MCP probe failed [%s]: command not found: %s", server.name, server.command
            )
            return server

        # Resource ceiling (PHF-1): an MCP server is agent-influenced (its command comes
        # from a discovered/installed server spec). Deliver the ``tool`` ceiling via the
        # post-exec shim — no preexec_fn, so this probe spawn never wedges the loop.
        from personalclaw.sandbox import PROFILE_TOOL, create_subprocess_limited

        proc = await create_subprocess_limited(
            resolved,
            *(server.args or []),
            profile=PROFILE_TOOL,
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
        # `url`, `headers` and the transport too: without them a remote server read as a stdio
        # one with no command, and `register_servers_for_cc` wrote it into `~/.mcp.json` as
        # `{"command": "", "type": "stdio"}`, an entry Claude Code refuses.
        info = McpServerInfo(
            name=name,
            command=spec.get("command", ""),
            args=spec.get("args"),
            env=spec.get("env") or {},
            url=spec.get("url", ""),
            headers=spec.get("headers") or {},
            transport=mcp_transport(spec),
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


def discover_importable_servers() -> list[dict[str, Any]]:
    """Return MCP servers configured in an external backend (e.g. Claude Code)
    that are NOT yet present in any PersonalClaw scope — i.e. candidates the
    user can *import* into ``~/.personalclaw/mcp.json`` to make them callable by
    the native loop.

    PersonalClaw does not silently load these (the native loop can't reach a
    Claude-Code-only server). The UI offers each as an explicit "Import" action
    backed by ``/api/mcp/apply``, which copies the spec into the PClaw scope.

    This is the list a browser renders, so each entry carries what the picker shows and no
    credential: ``{name, backend, transport, command, args, url, env, headers}``. ``command`` is
    the command's file name, ``args`` the arguments with every credential in them masked
    (:func:`masked_args`), ``url`` the address with its userinfo, query values and any token-shaped
    path segment masked (:func:`masked_url`), and ``env``/``headers`` ``[{name, hasValue}]`` —
    which variables the server sets, never what they hold. The import reads the whole definition
    from the backend's own file, server-side, and stores its values.
    """
    # Servers already known to PClaw (mcp.json + the agent config) are not
    # "importable" — they're already first-class.
    known: set[str] = set(_load_mcp_json().keys())
    known |= set(_load_agent_config().get("mcpServers", {}).keys())

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, backend in _import_sources():
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
            # Only surface servers PersonalClaw can run: a stdio command, or a URL over a
            # transport it has a client for. Skip anything else silently.
            transport = mcp_transport(spec)
            remote = transport != "stdio"
            if transport not in MCP_TRANSPORTS or not spec.get("url" if remote else "command"):
                continue
            seen.add(name)
            args = spec.get("args")
            out.append(
                {
                    "name": name,
                    "backend": backend,
                    "transport": transport,
                    "command": "" if remote else _command_name(str(spec["command"])),
                    "args": masked_args(args) if not remote and isinstance(args, list) else [],
                    "url": masked_url(str(spec["url"])) if remote else "",
                    "env": _names_with_presence(spec.get("env")),
                    "headers": _names_with_presence(spec.get("headers")),
                }
            )
    return out


def _names_with_presence(values: Any) -> list[dict[str, Any]]:
    """``{name: value}`` as ``[{"name", "hasValue"}]``: what a listing may say about another
    tool's environment or headers without holding any of it."""
    if not isinstance(values, dict):
        return []
    return [{"name": str(k), "hasValue": v not in (None, "")} for k, v in values.items()]


# ── what a browser may see of another tool's server definition ──────────────
#
# A server's arguments and URL are where a token goes when it is not in the environment or a
# header: ``--api-key sk-…``, ``--header "Authorization: Bearer …"``, ``?token=…``,
# ``https://user:pw@…``, or the secret path segment a hosted endpoint embeds. The import picker
# needs to show which server a row is, not how it authenticates, so those parts reach the browser
# as the mask the edit form uses. Deliberately biased toward masking: an over-masked argument
# costs a little recognisability, an under-masked one is a token in the page and in its session
# storage.

#: Flags whose NEXT argument is an HTTP header (``mcp-remote --header "Authorization: Bearer …"``,
#: curl's ``-H``): its name stays, its value is masked.
_HEADER_FLAGS = frozenset({"--header", "--headers", "-H"})
#: A scheme, as ``urlsplit`` reads one: so ``--url=https://…`` is not mistaken for a URL.
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")
#: A run of token characters. Long enough, and mixing letters and digits, it is a key in a format
#: no named rule knows (a hex or base64url secret). ``/`` and ``.`` are not in it: a path, a host
#: name and a version number are not tokens, and a JWT's parts are tested one by one.
_TOKEN_RUN_RE = re.compile(r"[A-Za-z0-9_\-+=~]{20,}")
_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")


def _looks_secret(text: str) -> bool:
    """Shaped like a credential: a format the credential redactor knows (a provider key, a bearer
    token, ``api_key=…``), or a long run of token characters mixing letters and digits."""
    from personalclaw.security import redact_credentials

    if redact_credentials(text)[1]:
        return True
    return any(
        _TOKEN_RUN_RE.fullmatch(piece) and _LETTER_RE.search(piece) and _DIGIT_RE.search(piece)
        for piece in text.split(".")
    )


def _command_name(command: str) -> str:
    """A command's file name (``npx``, ``node.exe``): the picker shows which program, not where
    it lives."""
    return re.split(r"[\\/]", command.rstrip("\\/"))[-1]


def masked_url(url: str) -> str:
    """``url`` as a browser may see it: where it points, and no credential it carries.

    Scheme, host, port and path stay, so the server is recognisable. Userinfo, every query value,
    the fragment and any path segment shaped like a token are masked. Text that is not a URL with
    a host is masked whole if it looks like a credential and kept otherwise.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return SECRET_MASK
    host = parts.hostname
    if not parts.scheme or not host:
        return SECRET_MASK if _looks_secret(url) else url
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    if "@" in parts.netloc:
        netloc = f"{SECRET_MASK}@{netloc}"
    path = "/".join(
        SECRET_MASK if seg and _looks_secret(unquote(seg)) else seg for seg in parts.path.split("/")
    )
    query = "&".join(
        (SECRET_MASK if _looks_secret(key) else key) + (f"={SECRET_MASK}" if value else "")
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    )
    return urlunsplit((parts.scheme, netloc, path, query, SECRET_MASK if parts.fragment else ""))


def masked_args(args: Iterable[Any]) -> list[str]:
    """Command arguments as a browser may see them: every credential they carry masked.

    The value a credential-named flag carries (``--api-key X``, ``--token=X``, ``API_KEY=X``,
    named by :func:`~personalclaw.apps.secret_fields.is_credential_field_name`, the repository's
    one rule), a header's value (``--header "Authorization: Bearer X"``), a URL's credential parts
    (:func:`masked_url`), and an argument shaped like a credential. A shell string (``sh -c "…"``)
    is read word by word by the same rules. Everything else stays, so the command line still says
    which server it starts.
    """
    out: list[str] = []
    carries: str | None = None
    for raw in args:
        arg = str(raw)
        if carries == "header":
            out.append(_masked_header(arg))
        elif carries == "value":
            out.append(SECRET_MASK)
        else:
            out.append(_masked_word(arg))
        carries = _what_flag_carries(arg)
    return out


def _what_flag_carries(arg: str) -> str | None:
    """What the argument AFTER ``arg`` holds: ``"header"``, a credential ``"value"``, or neither."""
    if arg in _HEADER_FLAGS:
        return "header"
    if arg.startswith("-") and "=" not in arg and is_credential_field_name(arg.lstrip("-")):
        return "value"
    return None


def _masked_header(text: str) -> str:
    name, sep, _value = text.partition(":")
    return f"{name.strip()}: {SECRET_MASK}" if sep and name.strip() else SECRET_MASK


def _masked_word(arg: str) -> str:
    scheme, sep, _rest = arg.partition("://")
    if sep and _SCHEME_RE.fullmatch(scheme):
        return masked_url(arg)
    name, sep, value = arg.partition("=")
    if sep and name:
        if name in _HEADER_FLAGS:
            return f"{name}={_masked_header(value)}"
        if is_credential_field_name(name.lstrip("-")):
            return f"{name}={SECRET_MASK}"
        return f"{name}={_masked_word(value)}" if value else arg
    if any(ch.isspace() for ch in arg):
        return " ".join(masked_args(arg.split()))
    return SECRET_MASK if _looks_secret(arg) else arg


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
        agents_dir,
        rebuild_agent_config,
    )

    config_path = agents_dir() / AGENT_FILENAME

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

    🔴 Only a server's PLAIN values are written: this file is outside the PersonalClaw home,
    nobody asked for the copy, and a new one is created at the umask mode — so a credential-store
    value copied here would be a world-readable plaintext secret. A server whose token Claude Code
    needs goes into Claude Code's own scope on purpose (the Tools page's Claude Code toggle).

    Returns True if any servers were added or updated.
    """
    from personalclaw.config.secret_refs import foreign_mcp_spec

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
        plain = foreign_mcp_spec(s.name, {"env": s.env, "headers": s.headers}, with_secrets=False)
        if s.is_remote:
            # Claude Code's own `type` values (`http`, `sse`): it refuses the `streamable-http`
            # this used to write.
            entry: dict = {"url": s.url, "type": s.transport}
            if plain.get("headers"):
                entry["headers"] = plain["headers"]
        else:
            entry = {"command": s.command, "args": s.args or [], "type": "stdio"}
            if plain.get("env"):
                entry["env"] = plain["env"]

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
