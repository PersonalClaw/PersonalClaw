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
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from personalclaw.apps.secret_fields import SECRET_MASK, is_credential_field_name
from personalclaw.env import augmented_path, gateway_env, startup_path
from personalclaw.hooks import safe_read_file
from personalclaw.mcp_argument_secrets import HEADER_FLAGS, SCHEME_RE, flag_carries, looks_secret
from personalclaw.mcp_status import switched_off
from personalclaw.security import redact_for_display

if TYPE_CHECKING:
    from personalclaw.mcp_status import StartFailure

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
# which copies a chosen spec into ``~/.personalclaw/mcp.json``. Another tool's setup is a place
# outside the home, read for the suggestions only when the owner presses Look in it or turned it
# on in Settings (``outside_home.readable``).
#
# ONE canonical MCP store. The former legacy ``settings/mcp.json`` source was dropped, so
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


def _import_sources() -> tuple[tuple[str, Callable[[], Path]], ...]:
    """The other tools' MCP configuration PersonalClaw can *import from* (but never silently
    loads), each as ``(tool, where)``: Claude Code's global config, which
    :func:`~personalclaw.onboarding_import.sources.claude_code.mcp_servers` follows to all three
    of its scopes (the file's own ``mcpServers``, each project entry's, each project's
    ``.mcp.json``), and the Codex home, whose ``config.toml``
    :func:`~personalclaw.onboarding_import.sources.codex.mcp_servers` reads. ``tool`` is the
    importer's name (its setup is the place ``setup:<tool>``) and ``where`` resolves the file
    when, and only when, that tool may be read.

    A FUNCTION, like :func:`_mcp_json_paths`: this was ``Path.home() / ".claude.json"`` frozen at
    import, so it ignored ``$CLAUDE_CONFIG_DIR`` while the onboarding importer honoured it. Both
    places now come from the resolvers that importer uses, per call. Tests monkeypatch this, and
    a test that names only one tool reads only that one.
    """
    from personalclaw.onboarding_import.sources import claude_code, codex

    return (
        (claude_code.NAME, claude_code.global_config_path),
        (codex.NAME, codex.resolve_root),
    )


def _importable_entries(
    asked: Collection[str] = (),
) -> tuple[list[tuple[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    """``(entries, unreadable, tools)``: ``(backend label, server)`` for every MCP server another
    tool this request may read has configured, every scope; ``{backend, path, why}`` for each of
    their configuration files that is there and could not be read, whose servers are therefore
    not among ``entries``; and each tool as ``{place, name, looked, allowed}``.

    A tool is read only when ``asked`` names its setup (the owner's press, for this request) or
    the owner turned it on in Settings (``outside_home.readable``). Any other is ``looked`` false,
    and nothing of it is opened, listed or checked for."""
    from personalclaw import outside_home
    from personalclaw.onboarding_import.registry import get_source
    from personalclaw.onboarding_import.sources import claude_code, codex

    readers: dict[str, Callable[[Path], Any]] = {
        claude_code.NAME: lambda path: claude_code.mcp_servers(config_path=path),
        codex.NAME: codex.mcp_servers,
    }
    entries: list[tuple[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    tools: list[dict[str, Any]] = []
    for tool, where in _import_sources():
        source = get_source(tool)
        looked = outside_home.readable(source.place, asked=asked)
        tools.append(
            {
                "place": source.place,
                "name": source.display_name,
                "looked": looked,
                "allowed": outside_home.allowed(source.place),
            }
        )
        if not looked:
            continue
        backend = source.display_name
        listing = readers[tool](where())
        entries.extend((backend, server) for server in listing.servers)
        unreadable.extend({"backend": backend, **entry.to_dict()} for entry in listing.unreadable)
    return entries, unreadable, tools


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

    A server of your own gets the gateway's environment, like any program you start, with the
    ``PATH`` the gateway started with (``env.gateway_env``): whatever has changed the gateway's own
    ``PATH`` since never decides which program a server's command is. An app's
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
        env = {**gateway_env(), **node_cli_env()}
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


# ── what the last start of a server found ───────────────────────────────────────────────────
#
# 🔴 A RESULT IS A RESULT OF ONE DEFINITION. The cache was keyed by the server's NAME alone and
# nothing that writes a server touched it, so the Tools page's card said what the last probe had
# found about something else: an edit that broke a server still read "ready", and a server removed
# and added again under its name showed the removed one's "command not found: docker" beside the
# new one's tools. Each result now carries what it was probed as (:func:`_probed_as`), and a
# server is shown only a result for what it is now. Anything else reads ``probing`` while a probe
# of it runs, and ``unknown`` until one starts.
#
# 🔴 ONE RECORD FOR EVERY START. The probe kept what it found here and an agent's connection kept
# what it found to itself, so a server the probe had given up on while it was still building read
# "timeout" on its card while every connection that started it next saw it exit. Both now record
# each start here (`note_start`, `_cache_probe`), in `mcp_status`'s words, with how many starts in
# a row failed. At ``mcp_status.STOP_AFTER`` the server reads ``stopped``, and nothing starts it
# (`start_refused`) until its owner presses Retry, a write changes it (`forget_probe`), or its
# definition changes. Each change is announced (`mcp_status.announce`) for the pages to re-read.

#: A server a probe is checking now, and has no result for yet as it is defined now.
PROBING = "probing"


@dataclass
class _ProbeResult:
    """What the last start of one server found, and what it started."""

    status: str
    tools: list[dict[str, Any]]  # each entry: `listed_rows`'s
    error: str
    #: The definition the result is of (:func:`_probed_as`).
    probed_as: str
    #: The tail of what a stdio server wrote to its error output, behind its card's Details.
    detail: str = ""
    #: How many starts of this definition in a row failed (`mcp_status.STOP_AFTER`).
    failures: int = 0


# Module-level record of each server's last start: server name → result
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


def _get_cached(server: "McpServerInfo") -> tuple[str, list[dict[str, Any]], str, str]:
    """``(status, tools, error, detail)`` of the last start of *server* as it is defined now; else
    ``probing`` while a probe of it runs, and ``unknown`` before one starts. A server that failed
    to start ``mcp_status.STOP_AFTER`` times in a row reads ``stopped``, with what its last start
    found.

    However old the result, it stands until the next start replaces it: the Tools page re-probes
    every server on its own schedule, and meanwhile the last answer is the one there is."""
    from personalclaw import mcp_status

    cached = _probe_cache.get(server.name)
    if cached is not None and cached.probed_as == _probed_as(server):
        if cached.failures >= mcp_status.STOP_AFTER:
            text = mcp_status.stopped_trying(server.name, cached.error)
            return mcp_status.STOPPED, [], text, cached.detail
        return cached.status, cached.tools, cached.error, cached.detail
    if _probing.get(server.name):
        return PROBING, [], "", ""
    return "unknown", [], "", ""


def _keep(name: str, result: _ProbeResult) -> None:
    """Keep *result* as what server *name*'s last start found, and say so when its card changes."""
    from personalclaw import mcp_status

    if _probe_cache.get(name) != result:
        _probe_cache[name] = result
        mcp_status.announce(name)


def forget_probe(name: str) -> None:
    """Drop what server ``name``'s last start found, and how many failed in a row: what it said is
    no longer true. Every write that changes a server, and its owner's Retry, comes through here,
    which is also what starts a stopped server again, and what gives a start of it its time to
    finish again (`mcp_stdio.forget_left`)."""
    from personalclaw import mcp_status, mcp_stdio

    mcp_stdio.forget_left(name)
    if _probe_cache.pop(name, None) is not None:
        mcp_status.announce(name)


def _cache_probe(server: "McpServerInfo") -> None:
    """Keep what a probe found as what *server*'s card shows. The count of failed starts is the
    connection's to keep (`note_start`), and is carried over; a probe that connected resets it."""
    seal = _probed_as(server)
    prev = _probe_cache.get(server.name)
    carried = prev.failures if prev is not None and prev.probed_as == seal else 0
    _keep(
        server.name,
        _ProbeResult(
            status=server.status,
            tools=list(server.tools),
            error=server.error,
            probed_as=seal,
            detail=server.detail,
            failures=0 if server.status == "ok" else carried,
        ),
    )


def definition_seal(name: str, spec: Mapping[str, Any]) -> str:
    """What a start of server *name* from *spec* is a start OF (:func:`_probed_as`). A spec whose
    secrets are resolved seals the same as the one in ``mcp.json``: values are not in the seal."""
    return _probed_as(_server_from_spec(name, dict(spec), "mcp.json"))


def _current_seal(name: str) -> str | None:
    """The seal of server *name* as it is defined now, or ``None`` when no server has that name."""
    server = next((s for s in list_servers(include_disabled=True) if s.name == name), None)
    return _probed_as(server) if server is not None else None


def listed_rows(tools: Iterable[Any]) -> list[dict[str, Any]]:
    """Each tool a start listed (an ``McpToolSpec``), as a server's card and the owner's trust read
    it: its name, description and input schema, and the labels its server gave it. The trust in a
    server's read-only labels is sealed to the four (`mcp_read_only_trust`), so a row without the
    labels could not be compared with it."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
            "annotations": dict(t.annotations or {}),
        }
        for t in tools
    ]


def note_start(
    name: str,
    seal: str,
    *,
    tools: list[Any] | None = None,
    failure: "StartFailure | None" = None,
) -> None:
    """Record one start of server *name* (sealed *seal*): connected with *tools*, or failed with
    *failure*. Called once per start, by the connection that made it (`mcp_client`), whoever asked
    for it: an agent's turn, the Tools page's listing, a probe.

    A failure that counts adds one to the failed starts in a row; a start that connected resets
    them. A start still going (`mcp_status.still_starting`) is no failure yet: the server reads
    ``probing`` until it is looked at again (:func:`look_again`). A start of a definition the
    server no longer has is not its result, and is dropped. What a start that connected listed is
    also what the owner's trust in the server's labels is checked against
    (`mcp_read_only_trust.observe`), for every server but PersonalClaw's own, whose tools are its
    own and say what they do themselves: an update that rewords them is not a server changing
    what it told her.
    """
    from personalclaw import mcp_read_only_trust, mcp_status

    if _current_seal(name) != seal:
        return
    prev = _probe_cache.get(name)
    same = prev is not None and prev.probed_as == seal
    if failure is None:
        _keep(name, _ProbeResult("ok", listed_rows(tools or []), "", seal))
        if name not in _MANAGED_SERVER_NAMES:
            mcp_read_only_trust.observe(name, tools or [])
        return
    failures = (prev.failures if same and prev is not None else 0) + int(failure.counts)
    status = PROBING if failure.pending else "error"
    _keep(
        name,
        _ProbeResult(status, [], failure.headline, seal, detail=failure.detail, failures=failures),
    )
    if failure.counts and failures == mcp_status.STOP_AFTER:
        logger.warning(
            "MCP server %r failed to start %d times in a row; it is not started again until its "
            "owner presses Retry or changes it",
            name,
            failures,
        )


def start_refused(name: str, seal: str) -> str | None:
    """Why server *name*, sealed *seal*, must not be started now, or ``None``: it failed to start
    ``mcp_status.STOP_AFTER`` times in a row as it is defined now. Asked before every start, by
    an agent's connection and by the probe, so nothing starts a stopped server."""
    from personalclaw import mcp_status

    cached = _probe_cache.get(name)
    if cached is None or cached.probed_as != seal or cached.failures < mcp_status.STOP_AFTER:
        return None
    return mcp_status.stopped_trying(name, cached.error)


def _probe_started(name: str) -> None:
    """A probe of *name* is running: a server with no result for what it is now reads
    ``probing`` from here on, which is announced."""
    from personalclaw import mcp_status

    _probing[name] = _probing.get(name, 0) + 1
    if _probing[name] == 1:
        mcp_status.announce(name)


def _probe_ended(name: str) -> None:
    from personalclaw import mcp_status

    left = _probing.get(name, 0) - 1
    if left > 0:
        _probing[name] = left
    else:
        _probing.pop(name, None)
        mcp_status.announce(name)


def look_again(name: str) -> None:
    """Probe server *name* again, now that a start of it left to finish (`mcp_stdio`) has ended:
    until a probe says what it is now, its card says it is still starting. Nothing to do when
    something has looked at it since and its card says something else. Called on the loop."""
    cached = _probe_cache.get(name)
    if cached is not None and cached.status == PROBING:
        recheck([name], forget=False)


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
    # unknown | ok | error | signin | probing | stopped (UNSERVED and ``waiting`` are only shown).
    # ``stopped``: it failed to start ``mcp_status.STOP_AFTER`` times in a row. ``signin``:
    # a remote server that refused the connection until its owner signs in, or signs in again —
    # only one that offers a sign-in (`_probe_remote`). ``probing``: a probe of it is running, and
    # there is no result yet for it as it is defined now.
    status: str = "unknown"
    # Each tool entry is a dict with at least "name"; a start that listed it gives the
    # "description", "inputSchema" and "annotations" of its tools/list answer too (`listed_rows`).
    # Plain strings are also accepted on input and normalized to dicts at probe.
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
    #: The tail of what a stdio server wrote to its error output when its last start failed: what
    #: its card shows behind Details. ``error`` is the one-line reason (`mcp_status`).
    detail: str = ""

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
        if self.detail:
            # A server's error output can print anything it was given, its variables included.
            d["detail"] = redact_for_display(self.detail)
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
        resolved = shutil.which("personalclaw", path=augmented_path(startup_path() or ""))
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
        off = switched_off(spec, name) or switched_off(entries.get(name, {}), name)
        if off and not include_disabled:
            continue
        # Re-resolve stale managed MCP server paths at runtime
        _fix_stale_managed_command(name, spec)
        servers[name] = _server_from_spec(name, spec, "agent")
        servers[name].disabled = off

    # 2. From mcp.json, whose switch is the owner's: the agent config's copy of it is rebuilt to
    #    match, and between rebuilds holds a stale one.
    for name, spec in own.items():
        off = switched_off(spec, name)
        if off and not include_disabled:
            continue
        servers[name] = _server_from_spec(name, spec, "mcp.json")
        servers[name].disabled = off

    # "disabledTools" by key presence, not truthiness: an explicit [] ("every tool enabled") is
    # the user's answer too.
    for name, spec in entries.items():
        if name in servers and "disabledTools" in spec:
            servers[name].disabled_tools = spec.get("disabledTools", [])

    # 3. Merge what each server's last start found, for what it is now (`_get_cached`).
    for s in servers.values():
        s.status, s.tools, s.error, s.detail = _get_cached(s)

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
        MissingSecretValue,
        resolve_mcp_command_line,
        resolve_mcp_values,
    )
    from personalclaw.mcp_client import try_start

    try:
        # The spec holds `{{secret:…}}` references; the header values, and a credential in the
        # URL, are resolved here, where the connection is made, and never written anywhere — only
        # keys the server's owner holds.
        headers = resolve_mcp_values(server.name, "headers", server.headers)
        url = resolve_mcp_command_line(server.name, {"url": server.url}).get("url", server.url)
    except (ForeignSecretReference, MissingSecretValue) as exc:
        # No request was made and no value was read: the whole sentence is this server's error.
        server.status = "error"
        server.error = str(exc)
        return server
    if server.transport not in MCP_TRANSPORTS:
        server.status = "error"
        server.error = f"PersonalClaw cannot connect over the {server.transport!r} transport"
    else:
        spec: dict[str, Any] = {"type": server.transport, "url": url, "headers": headers}
        if server.sign_in:
            spec[MCP_SIGN_IN] = server.sign_in
        waited = _get_probe_timeout()
        started = await try_start(server.name, spec, deadline=waited, seal=_probed_as(server))
        if started.timed_out:
            server.status = "error"
            server.error = await _why_no_answer(server.url, waited)
        elif started.error:
            server.status, server.error = "error", started.error
            if started.sign_in_needed:
                # A sign-in it holds has ended: signing in again is the answer. One it never had
                # is offered only if the server says how to sign in.
                why_not = None if server.sign_in else await _sign_in_unavailable(server, headers)
                if why_not is None:
                    server.status = "signin"
                else:
                    server.error = why_not
        else:
            server.status = "ok"
            server.tools = listed_rows(started.tools)
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


async def probe_server(server: McpServerInfo) -> McpServerInfo:
    """Probe a single MCP server: start it, or connect to it, as an agent's connection does.

    Updates server.status and server.tools in place and returns it. A server the owner has not
    allowed as it is defined now (`mcp_grants`) is neither spawned nor connected to: it reads
    ``waiting``, with the sentence that says what to do. Nor is one they switched off.

    Every other outcome is what the Tools page shows for the server until the next probe, so it
    is kept (`_cache_probe`) whatever it was, a failure included. A probe that kept only its
    successes left each failure on screen as whatever an older probe had said.
    """
    from personalclaw import mcp_grants, mcp_status

    _probe_started(server.name)
    try:
        # A new answer. The server came from `list_servers`, which carries what the last probe
        # found, and a probe that connected set only its status and tools: the card read "ok" over
        # the old "did not answer".
        server.tools, server.error, server.detail = [], "", ""
        await _probe(server)
    except Exception as exc:  # noqa: BLE001 — a probe that broke is this server's answer
        server.status = "error"
        server.error = str(exc)[:200]
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)
    finally:
        _probe_ended(server.name)
    # Whether it waits, and whether it is switched off, are read when the server is shown, and a
    # stopped one's record is the one that says so.
    if server.status not in (mcp_grants.WAITING, "disabled", mcp_status.STOPPED):
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

    if start_refused(server.name, _probed_as(server)) is not None:
        # Stopped after failing to start again and again: the Tools page's own look starts it no
        # more than an agent does. Retry (`forget_probe`) does.
        server.status, server.tools, server.error, server.detail = _get_cached(server)
        return

    if server.is_remote:
        await _probe_remote(server)
        return

    if not server.command:
        server.status = "error"
        server.error = "no command"
        logger.warning("MCP probe failed [%s]: no command configured", server.name)
        return

    from personalclaw.config.secret_refs import (
        ForeignSecretReference,
        MissingSecretValue,
        resolve_mcp_command_line,
        resolve_mcp_values,
    )

    try:
        # The spec holds `{{secret:…}}` references; the values are resolved here, at spawn, and
        # reach only the child's environment and its arguments — only keys the server's owner
        # holds.
        server_env = resolve_mcp_values(server.name, "env", server.env)
        args = resolve_mcp_command_line(server.name, {"args": list(server.args or [])})["args"]
    except (ForeignSecretReference, MissingSecretValue) as exc:
        # Nothing was spawned and no value was read: the whole sentence is this server's error.
        server.status = "error"
        server.error = str(exc)
        return
    from personalclaw.mcp_client import try_start

    # Started as an agent's connection starts it (`mcp_client`, `mcp_stdio`): in the same
    # environment, through the same ceiling, so a server that probes "ok" is one an agent can start
    # and one that cannot says why in the same words. Given the probe's own time to answer.
    spec: dict[str, Any] = {
        "command": server.command,
        "args": args,
        "env": server_env,
    }
    if server.cwd:
        spec["cwd"] = server.cwd
    started = await try_start(
        server.name, spec, deadline=_get_probe_timeout(), seal=_probed_as(server)
    )
    if started.error:
        server.error = started.error
        server.detail = started.detail
        if started.starting:
            # Left to finish (`mcp_stdio`), and looked at again when it has (`look_again`).
            server.status = PROBING
            logger.info("MCP probe [%s]: %s", server.name, server.error)
            return
        server.status = "error"
        logger.warning("MCP probe failed [%s]: %s", server.name, server.error)
        return
    server.status = "ok"
    server.tools = listed_rows(started.tools)


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


def discover_importable_servers(
    *, asked: Collection[str] = ()
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    """``(servers, unreadable, tools)``: the MCP servers configured in another tool (Claude Code,
    Codex) that are NOT yet present in any PersonalClaw scope — i.e. candidates the user can
    *import* into ``~/.personalclaw/mcp.json`` to make them callable by the native loop — and
    each of those tools' configuration files that is there and could not be read, as
    ``{backend, path, why}``. The servers such a file holds are not in the list, so the list
    beside it is incomplete, never "nothing to import".

    Only the tools this request may read are looked in: those whose setup ``asked`` names (the
    owner pressed Look in it) or the owner turned on in Settings. ``tools`` says, per tool, its
    ``place``, its ``name``, whether this list ``looked`` in it and whether it is ``allowed``
    without a press, so a page can offer the press for one it did not look in.

    PersonalClaw does not silently load these (the native loop can't reach a server only another
    tool has). The UI offers each as an explicit "Import" action backed by ``/api/mcp/apply``,
    which copies the spec into the PClaw scope.

    This is the list a browser renders, so each entry carries what the picker shows and no
    credential: ``{id, name, backend, place, scope, origin, note, transport, command, args, url,
    env, headers}``. ``id`` names the server in its scope — what the import sends back, so a pick
    names a listed row and never a file — and ``place`` the setup it came from, which the import
    names to be read again. ``scope`` is the tool's (``user``, ``local``, ``project``)
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

    from personalclaw.onboarding_import.registry import get_source

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    entries, unreadable, tools = _importable_entries(asked)
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
                "place": get_source(server.source).place,
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
    return out, unreadable, tools


def importable_spec(server_id: str, *, asked: Collection[str]) -> tuple[str, dict[str, Any]] | None:
    """``(name, definition)`` of the importable server ``server_id`` names, or ``None``.

    Read again from the other tool's own files, the way the list was, and only from the tools the
    import's press names (``asked``) or the owner turned on: the import copies exactly the server
    the row showed, in the scope it showed it in, and a caller can only ever name a row — never a
    path, a file or a definition of its own.
    """
    entries, _unreadable, _tools = _importable_entries(asked)
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
# stored value in its place: the mask stands for the value, and nothing else. A credential the
# arguments or the URL carry where its place or its format says so is kept in the credential
# store, and the file holds a reference (`mcp_argument_secrets`, `config.secret_refs`); a reference
# is masked like the value it stands for. One masked here only for its shape stays in ``mcp.json``
# as written, which `docs/security/limitations.md` says.


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
        return SECRET_MASK if looks_secret(url) else url
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    if "@" in parts.netloc:
        netloc = f"{SECRET_MASK}@{netloc}"
    path = "/".join(
        SECRET_MASK if seg and looks_secret(unquote(seg)) else seg for seg in parts.path.split("/")
    )
    query = "&".join(
        (SECRET_MASK if looks_secret(key) else key) + (f"={SECRET_MASK}" if value else "")
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
        carries = flag_carries(arg)
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


def _masked_header(text: str) -> str:
    name, sep, _value = text.partition(":")
    return f"{name.strip()}: {SECRET_MASK}" if sep and name.strip() else SECRET_MASK


def _masked_word(arg: str) -> str:
    scheme, sep, _rest = arg.partition("://")
    if sep and SCHEME_RE.fullmatch(scheme):
        return masked_url(arg)
    name, sep, value = arg.partition("=")
    # A name holds no space: in ``server --token abc --level=2`` the first ``=`` is a later
    # word's, and the words before it are read one by one below.
    if sep and name and not any(ch.isspace() for ch in name):
        if name in HEADER_FLAGS:
            return f"{name}={_masked_header(value)}"
        if is_credential_field_name(name.lstrip("-")):
            return f"{name}={SECRET_MASK}"
        return f"{name}={_masked_word(value)}" if value else arg
    if any(ch.isspace() for ch in arg):
        return " ".join(masked_args(arg.split()))
    return SECRET_MASK if looks_secret(arg) else arg


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
