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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from personalclaw.apps.secret_fields import SECRET_MASK, is_credential_field_name
from personalclaw.env import augmented_path
from personalclaw.hooks import safe_read_file
from personalclaw.security import redact_for_display

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


# PersonalClaw discovers MCP servers ONLY from its own config. A Claude-Code-only server
# (``~/.claude.json``) is not invocable by the native loop, so surfacing it here would imply
# tools the agent can't call. Such servers are instead offered as explicit *import
# suggestions* via :func:`discover_importable_servers` + the ``/api/mcp/apply`` endpoint,
# which copies a chosen spec into ``~/.personalclaw/mcp.json``.
#
# UT3: ONE canonical MCP store. The former legacy ``settings/mcp.json`` source was dropped, so
# there is a single read+write path the dashboard, the provider instances, agent.py, and the
# native runtime all share. The "global" scope that file used to be is gone with it: a second
# scope NAME left pointing at the one file is how Import added a server and removed it again in
# the same request. Nothing reads that file any more — not even a fold at startup, which used to
# copy whatever it held into this store on every start, where the boot probe spawned it and the
# next rebuild allowed its tools without asking. A server still in it is named by the Doctor
# (`resilience.doctor`, `tools.legacy_mcp_settings`) for the owner to add, if they want it.
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
    """The other tools' MCP configuration PersonalClaw can *import from* (but never silently
    loads), each as ``(where, backend label)``: Claude Code's global config, which
    :func:`~personalclaw.onboarding_import.sources.claude_code.mcp_servers` follows to all three
    of its scopes (the file's own ``mcpServers``, each project entry's, each project's
    ``.mcp.json``), and the Codex home, whose ``config.toml``
    :func:`~personalclaw.onboarding_import.sources.codex.mcp_servers` reads.

    A FUNCTION, like :func:`_mcp_json_paths`: this was ``Path.home() / ".claude.json"`` frozen at
    import, so it ignored ``$CLAUDE_CONFIG_DIR`` while the onboarding importer honoured it. Both
    places now come from the resolvers that importer uses, per call. Tests monkeypatch this, and
    a test that names only one tool reads only that one.
    """
    from personalclaw.onboarding_import.sources import claude_code, codex

    return (
        (claude_code.global_config_path(), claude_code.DISPLAY_NAME),
        (codex.resolve_root(), codex.DISPLAY_NAME),
    )


def _importable_entries() -> tuple[list[tuple[str, Any]], list[dict[str, str]]]:
    """``(entries, unreadable)``: ``(backend label, server)`` for every MCP server another tool has
    configured, every scope, and ``{backend, path, why}`` for each of their configuration files
    that is there and could not be read, whose servers are therefore not among ``entries``."""
    from personalclaw.onboarding_import.sources import claude_code, codex

    readers = {
        claude_code.DISPLAY_NAME: lambda path: claude_code.mcp_servers(config_path=path),
        codex.DISPLAY_NAME: codex.mcp_servers,
    }
    entries: list[tuple[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    for path, backend in _import_sources():
        listing = readers[backend](path)
        entries.extend((backend, server) for server in listing.servers)
        unreadable.extend({"backend": backend, **entry.to_dict()} for entry in listing.unreadable)
    return entries, unreadable


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

#: Other names for Streamable HTTP in server configs.
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


def stdio_spawn_env(server_env: Mapping[str, str], *, server: str) -> dict[str, str]:
    """The environment stdio server *server* is spawned in — by the probe AND by the agent's
    connection.

    A server of your own gets the gateway's environment, like any program you start. An app's
    server (``apps.mcp_bridge.server_app``) gets the child allowlist instead
    (``sandbox.build_child_env``): it is the app's code, and the gateway's environment holds every
    secret saved in PersonalClaw. What it needs from the credential store it declares in its
    ``env``, which resolves only that app's own secrets (``config.secret_refs``).

    Either way ``PATH`` is augmented, so a daemon's thin ``PATH`` still finds
    ``node``/``npx``/``uvx``, then the server's own variables go over it. A server's own ``PATH``
    goes IN FRONT of the gateway's instead of replacing it, which is also how
    ``rebuild_agent_config`` resolves the command. One definition because there were two: the
    probe prepended and the agent's connection replaced, so a server that set ``PATH`` probed "ok"
    while every call to it failed with the command not found. A Node server keeps its compile
    cache in the home (``_installer.node_cli_env``), not in the temp folder.
    """
    from personalclaw._installer import node_cli_env
    from personalclaw.apps.mcp_bridge import server_app

    if server_app(server) is not None:
        from personalclaw.sandbox import build_child_env

        env = build_child_env(site="app-mcp-server", extra=node_cli_env())
    else:
        env = {**os.environ, **node_cli_env()}
    env["PATH"] = augmented_path(env.get("PATH", ""))
    if "PATH" in server_env:
        env["PATH"] = server_env["PATH"] + os.pathsep + env["PATH"]
    env.update({k: v for k, v in server_env.items() if k != "PATH"})
    return env


# ── what an agent can call ────────────────────────────────────────────────────────────────────
#
# A server PersonalClaw connects to is not therefore one an agent can use. Every external server's
# tools reach an agent through ONE registered tool provider
# (`tool_providers.registry.EXTERNAL_MCP_PROVIDER`, which the built-in MCP Tool Servers app
# registers), and without it the native loop has none of them. So "ok", which the Tools page draws
# as a green "ready", is said only while that provider is on an agent's surface. It is applied
# where a status is SHOWN, not where it is probed: the probe's result is cached, and the provider
# can be switched off, or can fail to load, after the probe ran.

#: A server PersonalClaw connected to whose tools no agent can call.
UNSERVED = "unserved"

#: Why, as the Tools page says it. The app is named here, in copy, and nowhere in the predicate.
UNSERVED_REASON = (
    "Connected, but no agent can call its tools: the built-in MCP Tool Servers provider is "
    "switched off, or it failed to load. The Tools page shows its load error if it has one."
)


def as_agents_see_it(row: dict[str, Any]) -> dict[str, Any]:
    """*row* (a :meth:`McpServerInfo.to_dict`) with an ``ok`` it has not earned taken back.

    ``ok`` stays ``ok`` only when an agent's surface carries external servers' tools; otherwise
    it becomes :data:`UNSERVED` with :data:`UNSERVED_REASON`. Every other status is already not a
    claim that an agent can call the server, and passes through. So does PersonalClaw's own
    server (:data:`_MANAGED_SERVER_NAMES`): it is not in ``mcp.json`` and reaches no agent through
    that provider — a native agent has its tools in-process, and an ACP session is handed it in
    ``session/new`` (``acp.mcp_servers``).
    """
    from personalclaw.tool_providers.registry import serves_external_mcp_tools

    if row.get("name") in _MANAGED_SERVER_NAMES:
        return row
    if row.get("status") == "ok" and not serves_external_mcp_tools():
        return {**row, "status": UNSERVED, "error": UNSERVED_REASON}
    return row


# ── what the last probe of a server found ───────────────────────────────────────────────────
#
# 🔴 A RESULT IS A RESULT OF ONE DEFINITION. The cache was keyed by the server's NAME alone and
# nothing that writes a server touched it, so the Tools page's card said what the last probe had
# found about something else: an edit that broke a server still read "ready", and a server removed
# and added again under its name showed the removed one's "command not found: docker" beside the
# new one's tools. Each result now carries what it was probed as (:func:`_probed_as`), and a
# server is shown only a result for what it is now. Anything else reads ``probing`` while a probe
# of it runs, and ``unknown`` until one starts.

#: A server a probe is checking now, and has no result for yet as it is defined now.
PROBING = "probing"


@dataclass
class _ProbeResult:
    """What the last probe of one server found, and what it probed."""

    status: str
    tools: list[dict[str, Any]]  # each entry: {"name", "description", "inputSchema"}
    error: str
    #: The definition the result is of (:func:`_probed_as`).
    probed_as: str


# Module-level probe cache: server name → result
_probe_cache: dict[str, _ProbeResult] = {}

#: How many probes of each server are running now, by name.
_probing: dict[str, int] = {}


def _probed_as(server: "McpServerInfo") -> str:
    """What a probe of *server* is a probe OF: what it runs or where it connects, as its owner
    allows it (`mcp_grants.definition`), and who its sign-in is with. A value behind a reference
    is not in it: a save from the Tools page probes again whatever it changed (:func:`recheck`)."""
    from personalclaw import mcp_grants
    from personalclaw.config.secret_refs import MCP_SIGN_IN
    from personalclaw.mcp_oauth import sign_in_identity

    seal = {
        **mcp_grants.definition(server),
        "signIn": sign_in_identity({MCP_SIGN_IN: server.sign_in}),
    }
    return json.dumps(seal, sort_keys=True, separators=(",", ":"))


def _get_cached(server: "McpServerInfo") -> tuple[str, list[dict[str, Any]], str]:
    """``(status, tools, error)`` of the last probe of *server* as it is defined now; else
    ``probing`` while one runs, and ``unknown`` before one starts.

    However old the result, it stands until the next probe replaces it: the Tools page re-probes
    every server on its own schedule, and meanwhile the last answer is the one there is."""
    cached = _probe_cache.get(server.name)
    if cached is not None and cached.probed_as == _probed_as(server):
        return cached.status, cached.tools, cached.error
    if _probing.get(server.name):
        return PROBING, [], ""
    return "unknown", [], ""


def forget_probe(name: str) -> None:
    """Drop server ``name``'s cached probe: what it said is no longer true."""
    _probe_cache.pop(name, None)


def _cache_probe(server: "McpServerInfo") -> None:
    """Store probe result in cache."""
    _probe_cache[server.name] = _ProbeResult(
        status=server.status,
        tools=list(server.tools),
        error=server.error,
        probed_as=_probed_as(server),
    )


def _probe_started(name: str) -> None:
    _probing[name] = _probing.get(name, 0) + 1


def _probe_ended(name: str) -> None:
    left = _probing.get(name, 0) - 1
    if left > 0:
        _probing[name] = left
    else:
        _probing.pop(name, None)


#: The re-probes :func:`recheck` started for a caller that holds no task set of its own.
_RECHECKS: set["asyncio.Task[Any]"] = set()


def recheck(
    names: Iterable[str],
    *,
    hold: set["asyncio.Task[Any]"] | None = None,
    forget: bool = True,
) -> "asyncio.Task[Any] | None":
    """Probe the servers *names* again, in the background, and say so until it lands.

    For what just changed a server — it was added, edited, allowed, switched on, signed in or
    out — ``forget`` drops what its last probe said, since it no longer holds; each then reads
    ``probing`` from this call on, until its new result lands. The Tools page's own periodic
    re-probe passes ``forget=False``: a server keeps its last result on screen meanwhile.

    The task is added to *hold* (the dashboard's background tasks, which shutdown and tests
    await), else kept here. Called on the event loop; ``None`` when there is nothing to probe.
    """
    wanted = list(dict.fromkeys(str(n) for n in names))
    if not wanted:
        return None
    for name in wanted:
        if forget:
            forget_probe(name)
        _probe_started(name)  # before the task runs, so the very next read says it

    async def _run() -> None:
        await asyncio.gather(*(probe_one(n) for n in wanted), return_exceptions=True)

    def _ended(_task: "asyncio.Task[Any]") -> None:
        # A done callback, not a `finally`: it runs for a task cancelled before it ever started
        # too (a shutdown), which would otherwise leave each server reading `probing` for good.
        for n in wanted:
            _probe_ended(n)

    task = asyncio.get_running_loop().create_task(_run())
    task.add_done_callback(_ended)
    tasks = hold if hold is not None else _RECHECKS
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


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
    # unknown | ok | error | signin | probing (UNSERVED and ``waiting`` are only shown). ``signin``:
    # a remote server that refused the connection until its owner signs in, or signs in again —
    # only one that offers a sign-in (`_probe_remote`). ``probing``: a probe of it is running, and
    # there is no result yet for it as it is defined now.
    status: str = "unknown"
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
    #: A remote server's OAuth sign-in as its spec stores it (``secret_refs.MCP_SIGN_IN``):
    #: references and what the grant is for, never a token. ``{}`` when it has none.
    sign_in: dict[str, Any] = field(default_factory=dict)
    #: Switched off by the owner. Only ``list_servers(include_disabled=True)`` lists such a server,
    #: for the pages that show it switched off; nothing probes or connects to one.
    disabled: bool = False

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
            # Masked for display: a failed connection's error can quote the URL or the header it
            # used. Every read of a server's state goes through here, the probes included.
            "error": redact_for_display(self.error) if self.error else self.error,
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
    from personalclaw.config.secret_refs import MCP_SIGN_IN

    sign_in = spec.get(MCP_SIGN_IN)
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
        sign_in=dict(sign_in) if isinstance(sign_in, dict) else {},
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


def list_servers(*, include_disabled: bool = False) -> list[McpServerInfo]:
    """Return all known MCP servers from the agent config and ``mcp.json``.

    ``mcp.json`` DEFINES each server whose entry holds any part of a definition (a command, a URL,
    a transport, its variables…), so that is where its definition is read. The agent config's copy
    of it is rebuilt from ``mcp.json`` with the command resolved to a path, so reading the copy made
    the probe run a command spelled differently from the one the owner allowed (`mcp_grants`). The
    agent config still defines the servers only it names, PersonalClaw's own among them, and a
    server of that name in ``mcp.json`` is not read. An entry in ``mcp.json`` that defines nothing
    holds the owner's state for a server defined elsewhere (the Tools page's switches write one).

    A server switched off is left out: every prober reads this list, and a server switched off in
    ``mcp.json`` used to be probed until the next rebuild. Its switch is in ``mcp.json``, and for a
    server only the agent config defines, in either file. *include_disabled* lists
    it too, marked :attr:`McpServerInfo.disabled`, for the pages that show it switched off: without
    it, a switched-off server was gone from the Tools page after a restart, with no switch left to
    turn it back on.

    Merges cached probe results so status/tools survive across requests. A server configured
    only in another tool (Claude Code) is not listed here: it is an import suggestion
    (:func:`discover_importable_servers`), because the native loop cannot call it.
    """
    from personalclaw.config.secret_refs import MCP_DEFINITION_KEYS

    servers: dict[str, McpServerInfo] = {}
    entries = {name: spec for name, spec in _load_mcp_json().items() if isinstance(spec, dict)}
    own = {
        name: spec
        for name, spec in entries.items()
        if name not in _MANAGED_SERVER_NAMES and any(k in spec for k in MCP_DEFINITION_KEYS)
    }

    # 1. From agent config (mcpServers key): the servers mcp.json does not define.
    agent_cfg = _load_agent_config()
    for name, spec in agent_cfg.get("mcpServers", {}).items():
        if not isinstance(spec, dict) or name in own:
            continue
        off = bool(spec.get("disabled") or entries.get(name, {}).get("disabled"))
        if off and not include_disabled:
            continue
        # Re-resolve stale managed MCP server paths at runtime
        _fix_stale_managed_command(name, spec)
        servers[name] = _server_from_spec(name, spec, "agent")
        servers[name].disabled = off

    # 2. From mcp.json, whose switch is the owner's: the agent config's copy of it is rebuilt to
    #    match, and between rebuilds holds a stale one.
    for name, spec in own.items():
        off = bool(spec.get("disabled"))
        if off and not include_disabled:
            continue
        servers[name] = _server_from_spec(name, spec, "mcp.json")
        servers[name].disabled = off

    # "disabledTools" by key presence, not truthiness: an explicit [] ("every tool enabled") is
    # the user's answer too.
    for name, spec in entries.items():
        if name in servers and "disabledTools" in spec:
            servers[name].disabled_tools = spec.get("disabledTools", [])

    # 3. Merge cached probe results: each server's own, for what it is now (`_get_cached`).
    for s in servers.values():
        status, tools, error = _get_cached(s)
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

    A server that refuses the connection until its owner signs in — a 401 with a Bearer
    challenge, or a sign-in that has ended — reads ``signin``, which the Tools page answers with
    its Sign in control. Only one that offers a sign-in: a server that takes a static token (an
    API key, a personal access token) refuses a request without one the same way, and reads
    ``error`` with what it wants instead (:func:`_sign_in_unavailable`), so the page never offers
    a Sign in that cannot start. A probe that ran out of time says what it was waiting for
    (:func:`_why_no_answer`).
    """
    from personalclaw.config.secret_refs import (
        MCP_SIGN_IN,
        ForeignSecretReference,
        resolve_mcp_values,
    )
    from personalclaw.mcp_client import McpServerConn

    try:
        # The spec holds `{{secret:…}}` references; the header values are resolved here, where the
        # connection is made, and never written anywhere — only keys the server's owner holds.
        headers = resolve_mcp_values(server.name, "headers", server.headers)
    except ForeignSecretReference as exc:
        # No request was made and no value was read: the whole sentence is this server's error.
        server.status = "error"
        server.error = str(exc)
        return server
    if server.transport not in MCP_TRANSPORTS:
        server.status = "error"
        server.error = f"PersonalClaw cannot connect over the {server.transport!r} transport"
    else:
        spec: dict[str, Any] = {"type": server.transport, "url": server.url, "headers": headers}
        if server.sign_in:
            spec[MCP_SIGN_IN] = server.sign_in
        conn = McpServerConn(server.name, spec)
        waited = _get_probe_timeout()
        tools: list[Any] = []
        timed_out = False
        try:
            tools = await asyncio.wait_for(conn.list_tools(), timeout=waited)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            await conn.shutdown()
        if timed_out:
            server.status = "error"
            server.error = await _why_no_answer(server.url, waited)
        elif conn.error:
            server.status, server.error = "error", conn.error
            if conn.sign_in_needed:
                # A sign-in it holds has ended: signing in again is the answer. One it never had
                # is offered only if the server says how to sign in.
                why_not = None if server.sign_in else await _sign_in_unavailable(server, headers)
                if why_not is None:
                    server.status = "signin"
                else:
                    server.error = why_not
        else:
            server.status = "ok"
            server.tools = [
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                for t in tools
            ]
    if server.status in ("error", "signin"):
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)
    return server


async def _sign_in_unavailable(server: McpServerInfo, headers: Mapping[str, str]) -> str | None:
    """Why *server*, which refused the connection with a Bearer challenge and holds no sign-in,
    cannot be signed in to, or ``None`` when it can.

    Asked the way Sign in asks it (`mcp_oauth.discover`, through the same egress guard, sending no
    token): a server that takes a static token answers a request without one exactly like one that
    signs in with OAuth, and only the metadata it publishes tells the two apart. Its sentence says
    what the server wants instead. A look that does not finish in the probe's time says neither,
    and the server did ask for a sign-in, so Sign in is offered, and says why if it cannot start.
    """
    from personalclaw.mcp_oauth import SignInFailed, discover

    try:
        await asyncio.wait_for(
            discover(server.name, server.url, server.transport, headers),
            timeout=_get_probe_timeout(),
        )
    except SignInFailed as exc:
        return str(exc)
    except asyncio.TimeoutError:
        return None
    return None


#: How long a probe that ran out of time spends looking the host's name up again, to say whether
#: the name was what it waited for.
_NAME_LOOKUP_SECS = 5.0


def _seconds(n: float) -> str:
    return f"{n:g} second" if n == 1 else f"{n:g} seconds"


def _is_address(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _proxied(url: str, host: str) -> bool:
    """Whether the connection to *url* goes through a proxy, which then looks the name up, not
    this computer. Read the way the HTTP client reads it (``urllib.request.getproxies``)."""
    import urllib.request

    proxies = urllib.request.getproxies()
    scheme = urlsplit(url).scheme.lower()
    if not (proxies.get(scheme) or proxies.get("all")):
        return False
    return not urllib.request.proxy_bypass(host)


async def _why_no_answer(url: str, waited: float) -> str:
    """What a probe of the server at *url*, which ran out of time, was waiting for.

    Often the host's name: a name no name server answers for takes as long to fail as the
    resolver's own retries, which can be longer than the probe waits, and the card then read
    "timeout" for a host that does not exist. So the name is looked up once more, briefly, and a
    lookup that fails or hangs is named as the cause. Not when the host is an address already, or
    when a proxy makes the connection (the proxy looks the name up, and this computer may not
    know it)."""
    import socket

    from personalclaw.mcp_client import slow_lookup_text, unresolved_host_text

    host = urlsplit(url).hostname or ""
    no_answer = f"{host or 'The server'} did not answer within {_seconds(waited)}."
    if not host or _is_address(host) or _proxied(url, host):
        return no_answer
    budget = min(_NAME_LOOKUP_SECS, float(waited))
    try:
        await asyncio.wait_for(_look_up(host), timeout=budget)
    except socket.gaierror:
        return unresolved_host_text(host)
    except asyncio.TimeoutError:
        return slow_lookup_text(host, _seconds(budget))
    except OSError:
        return no_answer
    return no_answer


async def _look_up(host: str) -> None:
    """Look *host*'s name up the way a connection to it does (the resolver's ``getaddrinfo``).
    Raises ``socket.gaierror`` for a name that does not resolve."""
    import socket

    await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)


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

    Updates server.status and server.tools in place and returns it. A server the owner has not
    allowed as it is defined now (`mcp_grants`) is neither spawned nor connected to: it reads
    ``waiting``, with the sentence that says what to do. Nor is one they switched off.

    Every other outcome is what the Tools page shows for the server until the next probe, so it
    is kept (`_cache_probe`) whatever it was, a failure included. A probe that kept only its
    successes left each failure on screen as whatever an older probe had said.
    """
    from personalclaw import mcp_grants

    _probe_started(server.name)
    try:
        await _probe(server)
    except Exception as exc:  # noqa: BLE001 — a probe that broke is this server's answer
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)
    finally:
        _probe_ended(server.name)
    # Whether it waits, and whether it is switched off, are read when the server is shown.
    if server.status not in (mcp_grants.WAITING, "disabled"):
        _cache_probe(server)
    return server


async def _probe(server: McpServerInfo) -> None:
    """The probe itself (:func:`probe_server`): *server*'s status, tools and error, in place."""
    problem = server_name_problem(server.name)
    if problem is not None:
        # Never started, by the native client or here: the sentence is this server's status.
        server.status = "error"
        server.error = problem
        return
    if server.disabled:
        server.status = "disabled"
        server.error = "Switched off. It does not run until you switch it on."
        return

    from personalclaw import mcp_grants

    if not mcp_grants.allowed(server):
        server.status = mcp_grants.WAITING
        server.error = mcp_grants.WAITING_REASON
        server.tools = []
        return

    if server.is_remote:
        await _probe_remote(server)
        return

    if not server.command:
        server.status = "error"
        server.error = "no command"
        logger.warning("MCP probe failed [%s]: no command configured", server.name)
        return

    from personalclaw.config.secret_refs import ForeignSecretReference, resolve_mcp_values

    try:
        # The spec holds `{{secret:…}}` references; the values are resolved here, at spawn, and
        # reach only the child's environment — only keys the server's owner holds.
        server_env = resolve_mcp_values(server.name, "env", server.env)
    except ForeignSecretReference as exc:
        # Nothing was spawned and no value was read: the whole sentence is this server's error.
        server.status = "error"
        server.error = str(exc)
        return
    proc = None
    try:
        env = stdio_spawn_env(server_env, server=server.name)

        # Resolve command to absolute path using the merged env PATH
        resolved = shutil.which(server.command, path=env.get("PATH"))
        if not resolved:
            server.status = "error"
            server.error = f"command not found: {server.command}"
            logger.warning(
                "MCP probe failed [%s]: command not found: %s", server.name, server.command
            )
            return

        # Resource ceiling: an MCP server is agent-influenced (its command comes
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
            return

        resp = json.loads(line.decode())
        if "error" in resp:
            server.status = "error"
            server.error = resp["error"].get("message", "unknown error")
            return

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


async def probe_one(name: str) -> McpServerInfo | None:
    """Probe a SINGLE configured MCP server by name — backs per-provider reconnect
    so a user can recover one timed-out server without re-probing the whole fleet
    (a slow/erroring server shouldn't force a full re-probe). Returns the probed
    info, or None if no server by that name is configured. A switched-off server is configured:
    it answers ``disabled``, and nothing is started."""
    server = next((s for s in list_servers(include_disabled=True) if s.name == name), None)
    if server is None:
        return None
    return await probe_server(server)


async def probe_all() -> list[McpServerInfo]:
    """Discover and probe all configured MCP servers. A probe that breaks is that server's
    error (:func:`probe_server`), so every server comes back."""
    return list(await asyncio.gather(*(probe_server(s) for s in list_servers())))


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
        # `url`, `headers` and the transport too: without them a remote server reads as a stdio
        # one with no command.
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


def discover_importable_servers() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """``(servers, unreadable)``: the MCP servers configured in another tool (Claude Code, Codex)
    that are NOT yet present in any PersonalClaw scope — i.e. candidates the user can *import*
    into ``~/.personalclaw/mcp.json`` to make them callable by the native loop — and each of
    those tools' configuration files that is there and could not be read, as
    ``{backend, path, why}``. The servers such a file holds are not in the list, so the list
    beside it is incomplete, never "nothing to import".

    PersonalClaw does not silently load these (the native loop can't reach a server only another
    tool has). The UI offers each as an explicit "Import" action backed by ``/api/mcp/apply``,
    which copies the spec into the PClaw scope.

    This is the list a browser renders, so each entry carries what the picker shows and no
    credential: ``{id, name, backend, scope, origin, note, transport, command, args, url, env,
    headers}``. ``id`` names the server in its scope — what the import sends back, so a pick names
    a listed row and never a file. ``scope`` is the tool's (``user``, ``local``, ``project``)
    and ``origin`` says where, in words; ``note`` is what to know first (a project server nobody
    approved, a variable nothing sets, a server the tool has turned off). ``command`` is the
    command's file name, ``args`` the arguments with every credential in them masked
    (:func:`masked_args`), ``url`` the address with its userinfo, query values and any
    token-shaped path segment masked (:func:`masked_url`), and ``env``/``headers``
    ``[{name, hasValue}]`` — which variables the server sets, never what they hold. The import
    reads the whole definition from the tool's own files, server-side (:func:`importable_spec`),
    and stores its values.
    """
    # Servers already known to PClaw (mcp.json + the agent config) are not
    # "importable" — they're already first-class.
    known: set[str] = set(_load_mcp_json().keys())
    known |= set(_load_agent_config().get("mcpServers", {}).keys())

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    entries, unreadable = _importable_entries()
    for backend, server in entries:
        spec = server.spec
        if server.name in known or server.id in seen:
            continue
        # Only surface servers PersonalClaw can run: a stdio command, or a URL over a
        # transport it has a client for. Skip anything else silently.
        transport = mcp_transport(spec)
        remote = transport != "stdio"
        if transport not in MCP_TRANSPORTS or not spec.get("url" if remote else "command"):
            continue
        seen.add(server.id)
        args = spec.get("args")
        out.append(
            {
                "id": server.id,
                "name": server.name,
                "backend": backend,
                "scope": server.scope,
                "origin": server.origin,
                "note": server.note,
                "transport": transport,
                "command": "" if remote else _command_name(str(spec["command"])),
                "args": masked_args(args) if not remote and isinstance(args, list) else [],
                "url": masked_url(str(spec["url"])) if remote else "",
                "env": _names_with_presence(spec.get("env")),
                "headers": _names_with_presence(spec.get("headers")),
            }
        )
    return out, unreadable


def importable_spec(server_id: str) -> tuple[str, dict[str, Any]] | None:
    """``(name, definition)`` of the importable server ``server_id`` names, or ``None``.

    Read again from the other tool's own files, the way the list was: the import copies exactly
    the server the row showed, in the scope it showed it in, and a caller can only ever name a
    row — never a path, a file or a definition of its own.
    """
    entries, _unreadable = _importable_entries()
    for _backend, server in entries:
        if server.id == server_id:
            return server.name, dict(server.spec)
    return None


def _names_with_presence(values: Any) -> list[dict[str, Any]]:
    """``{name: value}`` as ``[{"name", "hasValue"}]``: what a listing may say about another
    tool's environment or headers without holding any of it."""
    if not isinstance(values, dict):
        return []
    return [{"name": str(k), "hasValue": v not in (None, "")} for k, v in values.items()]


# ── what a browser may see of a server's definition ─────────────────────────
#
# A server's arguments and URL are where a token goes when it is not in the environment or a
# header: ``--api-key sk-…``, ``--api-token=4c1f…``, ``--header "Authorization: Bearer …"``,
# ``?token=…``, ``https://user:pw@…``, or the secret path segment a hosted endpoint embeds. Every
# surface that shows one masks it with the functions below and no other: the import picker, the
# Allow question (`mcp_grants.shown`), the Tools page's edit form and the MCP Tool Servers card in
# Settings → Providers. The edit form used the generic display mask instead, which knows token
# SHAPES but not credential-named flags, so ``--api-token=<hex>`` was in the form in clear beside
# the Allow question that masked it. Deliberately biased toward masking: an over-masked argument
# costs a little recognisability, an under-masked one is a token in the page and in its session
# storage. An argument with nothing to mask is shown exactly as it is, so an edit form seeded from
# it saves it back unchanged.
#
# A form that shows a mask gets it back on save, and the ``keep_masked_*`` functions put the
# stored value in its place: the mask stands for the value, and nothing else. The value itself
# still sits in ``mcp.json`` as written (only ``env`` and ``headers`` values are kept in the
# credential store), which `docs/security/limitations.md` says.

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
    a host is masked whole if it looks like a credential and kept otherwise. A URL with nothing to
    mask comes back exactly as written.
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
    shown = urlunsplit((parts.scheme, netloc, path, query, SECRET_MASK if parts.fragment else ""))
    return shown if SECRET_MASK in shown else url


def masked_args(args: Iterable[Any]) -> list[str]:
    """Command arguments as a browser may see them: every credential they carry masked.

    The value a credential-named flag carries (``--api-key X``, ``--token=X``, ``API_KEY=X``,
    named by :func:`~personalclaw.apps.secret_fields.is_credential_field_name`, the repository's
    one rule), a header's value (``--header "Authorization: Bearer X"``), a URL's credential parts
    (:func:`masked_url`), and an argument shaped like a credential. A shell string (``sh -c "…"``)
    is read word by word by the same rules. Everything else stays exactly as written, so the
    command line still says which server it starts.
    """
    out: list[str] = []
    carries: str | None = None
    for raw in args:
        arg = str(raw)
        if carries == "header":
            shown = _masked_header(arg)
        elif carries == "value":
            shown = SECRET_MASK
        else:
            shown = _masked_word(arg)
        out.append(shown if SECRET_MASK in shown else arg)
        carries = _what_flag_carries(arg)
    return out


def masked_command(command: str) -> str:
    """A server's command as a browser may see it: its words masked as arguments are
    (:func:`masked_args`), and kept exactly as written when there is nothing to mask."""
    shown = _masked_word(command)
    return shown if SECRET_MASK in shown else command


#: A save's refusal when a value it sends still shows the mask, and what is around the mask
#: changed, so the stored value it stood for cannot be matched to it.
MASK_MOVED = (
    f"A value shown as {SECRET_MASK} stands for one PersonalClaw did not show you, and the text "
    "around it changed, so it cannot tell which saved value you mean. Nothing was saved. Type the "
    f"value in place of {SECRET_MASK}, or put back what was around it."
)


def _hidden_value_key(args: list[str], i: int) -> tuple[str, str]:
    """What names the value argument *i* hides: the argument as shown, and for one that is the
    mask and nothing else (``--api-key``'s value), the flag before it too. Two such arguments show
    alike, so the flag is what says which value each stands for."""
    return (args[i - 1] if i > 0 and args[i] == SECRET_MASK else "", args[i])


def keep_masked_args(submitted: Iterable[Any], stored: Iterable[Any]) -> list[str]:
    """The arguments a save stores when its form was seeded from :func:`masked_args` over
    *stored*: an argument that still reads exactly as a stored one was shown (with the flag before
    it, for a masked flag value) is that stored argument again, at the same place first, then any
    other not yet claimed, so arguments added, removed or moved around it keep what they hold.
    Anything else is what was typed.

    One that holds the mask and matches no stored argument (its flag renamed, part of it typed
    over, or nothing is stored for it) raises :class:`~personalclaw.security.MaskConflict`: saving
    it would store the mask as the value.
    """
    from personalclaw.security import MaskConflict

    kept = [str(a) for a in stored]
    shown = masked_args(kept)
    sent = [str(a) for a in submitted]
    claimed: set[int] = set()
    out: list[str] = []
    for index, arg in enumerate(sent):
        if SECRET_MASK not in arg:
            out.append(arg)
            continue
        want = _hidden_value_key(sent, index)
        order = ([index] if index < len(shown) else []) + [
            i for i in range(len(shown)) if i != index
        ]
        match = next(
            (i for i in order if i not in claimed and _hidden_value_key(shown, i) == want), None
        )
        if match is None:
            raise MaskConflict(MASK_MOVED)
        claimed.add(match)
        out.append(kept[match])
    return out


def keep_masked_command(submitted: str, stored: str) -> str:
    """The command a save stores when its form was seeded from :func:`masked_command`: the
    stored one when it came back as it was shown, what was typed when it shows no mask, and a
    :class:`~personalclaw.security.MaskConflict` otherwise."""
    from personalclaw.security import MaskConflict

    if SECRET_MASK not in submitted:
        return submitted
    if submitted.strip() == masked_command(stored):
        return stored
    raise MaskConflict(MASK_MOVED)


def keep_masked_url(submitted: str, stored: str) -> str:
    """The URL a save stores when its form was seeded from :func:`masked_url`: the stored one
    when it came back as it was shown, and what was typed when it shows no mask.

    A URL that changed around a mask is refused (:class:`~personalclaw.security.MaskConflict`)
    rather than given the hidden value: a credential is never carried to an address its owner
    typed without seeing it there."""
    from personalclaw.security import MaskConflict

    if SECRET_MASK not in submitted:
        return submitted
    if submitted.strip() == masked_url(stored):
        return stored
    raise MaskConflict(MASK_MOVED)


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
