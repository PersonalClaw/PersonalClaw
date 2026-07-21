"""Long-lived in-process MCP client — external MCP servers in the native loop.

Unlike :func:`mcp_discovery.probe_server` (one-shot spawn → read → die), this
keeps a connection alive for the process and routes ``tools/list`` / ``tools/call``
over it. It is what lets PersonalClaw's *native* agent loop invoke tools from an
external MCP server configured in ``~/.personalclaw/mcp.json`` — independent of
the ACP CLI backends, which spawn their own MCP servers.

Built on the official ``mcp`` Python SDK (an optional ``personalclaw[mcp]``
extra). The SDK's clients are async-context-manager based, so each server runs
as a small **actor**: one background task holds the transport + session context
open and serves ``list_tools`` / ``call_tool`` requests off a queue, with
health/respawn and clean shutdown (drained by the gateway's reaper on exit).

Transports: stdio (``command``/``args``/``env``) and remote SSE/HTTP (``url``).
The SDK is imported lazily so the package still imports without the extra; a
missing SDK degrades to an empty registry (no servers), never an ImportError at
module load.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Per-call ceiling so one wedged tool can't stall a chat turn indefinitely.
_CALL_TIMEOUT_SECS = 120.0
# Handshake ceiling — a server that never completes initialize is marked failed.
_CONNECT_TIMEOUT_SECS = 30.0
# Reap a connection unused for this long (frees the subprocess; respawns on next
# use). Bounds resident MCP memory to actively-used (server, session) pairs.
_IDLE_TTL_SECS = 600.0
# How often the registry sweeps for idle connections.
_SWEEP_INTERVAL_SECS = 120.0
# Spawn circuit-breaker: after this many consecutive connect failures, stop
# respawning for a cooldown so a server that crashes on connect can't churn
# (spawn → fail → spawn) in a hot loop and burn CPU.
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_SECS = 60.0


def mcp_sdk_available() -> bool:
    """True when the optional ``mcp`` SDK is importable."""
    try:
        import mcp  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class McpToolSpec:
    """One tool advertised by a connected server (provider-neutral)."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


# Strict numeric-literal guards for schema-driven arg coercion. A model (notably
# Claude-on-Bedrock via Converse) sometimes emits a numeric tool argument as a
# STRING ("128") even when the tool's inputSchema types the field as number/
# integer; a strict MCP server then rejects the call with -32602 "expected
# number, received string". We coerce such a value back to a real number — but
# ONLY against these strict patterns, NEVER bare int()/float(), because
# int("1_000")==1000 and float("inf")/float("nan")/float("1e9") all parse and
# would silently mangle a value the model never meant as that number.
_INT_LITERAL_RE = re.compile(r"^-?\d+$")
_NUM_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


def _schema_numeric_kind(prop_schema: object) -> str | None:
    """Return "integer"/"number" iff ``prop_schema`` types the field as EXACTLY
    that numeric kind — i.e. it permits no string. A ``type`` that is a list
    (e.g. ``["integer", "null"]``) coerces; one that also allows ``"string"``
    (``["string", "integer"]``) does NOT. anyOf/oneOf/$ref branches are treated
    as "do not coerce" (unknown shape → leave the value alone)."""
    if not isinstance(prop_schema, dict):
        return None
    if any(k in prop_schema for k in ("anyOf", "oneOf", "allOf", "$ref")):
        return None
    t = prop_schema.get("type")
    types = {t} if isinstance(t, str) else set(t) if isinstance(t, list) else set()
    if "string" in types:
        return None
    if "integer" in types:
        return "integer"
    if "number" in types:
        return "number"
    return None


def _coerce_args_to_schema(
    arguments: dict[str, Any], input_schema: dict[str, Any] | None
) -> dict[str, Any]:
    """Coerce numeric-looking STRING args back to numbers per the tool's
    inputSchema. Only top-level properties are handled (nested object/array
    numerics are a known, accepted limitation). Non-string values, string-typed
    fields, and strings that don't strictly match a numeric literal are left
    untouched, so a genuinely bad value still reaches the server as-is (its own
    -32602 then reports the real error)."""
    if not isinstance(input_schema, dict) or not isinstance(arguments, dict):
        return arguments
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return arguments
    out: dict[str, Any] = dict(arguments)
    for key, val in arguments.items():
        if not isinstance(val, str):
            continue
        kind = _schema_numeric_kind(props.get(key))
        if kind == "integer" and _INT_LITERAL_RE.match(val):
            out[key] = int(val)
        elif kind == "number" and _NUM_LITERAL_RE.match(val):
            out[key] = float(val)
    return out


class McpServerConn:
    """An actor owning one MCP server's live connection.

    The connection is established lazily on first use and held open by a
    background task. Calls are marshalled to that task so the SDK's anyio task
    scope stays on a single task (its context managers are not reentrant across
    tasks).
    """

    def __init__(self, name: str, spec: dict[str, Any], scope: str = "") -> None:
        self.name = name
        self.spec = spec
        # "" = shared (poolable server); else the owning session key (isolation).
        self.scope = scope
        self._task: asyncio.Task | None = None
        self._requests: asyncio.Queue[tuple[str, dict, asyncio.Future]] | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._tools: list[McpToolSpec] = []
        self._error: str = ""
        self._closing = False
        # Idle reaping: bump on every use; the registry sweeper reaps when stale.
        self._last_used: float = time.monotonic()
        # Spawn circuit-breaker: count consecutive connect failures; once over
        # the threshold, refuse to respawn until the cooldown elapses.
        self._consecutive_failures: int = 0
        self._breaker_until: float = 0.0

    @property
    def error(self) -> str:
        return self._error

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def started(self) -> bool:
        return self._task is not None and not self._task.done()

    def touch(self) -> None:
        self._last_used = time.monotonic()

    async def ensure_started(self) -> bool:
        """Start the actor + wait for the handshake. Returns connected-ok.

        Gated by a circuit-breaker: a server that keeps failing to connect stops
        being respawned for a cooldown, so a broken server can't churn the CPU in
        a spawn→fail→spawn loop."""
        self.touch()
        if self._task is None or self._task.done():
            now = time.monotonic()
            if now < self._breaker_until:
                self._error = self._error or "circuit breaker open (repeated connect failures)"
                return False
            self._requests = asyncio.Queue()
            self._ready = asyncio.Event()
            self._error = ""
            self._closing = False
            self._task = asyncio.create_task(self._run(), name=f"mcp-conn-{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=_CONNECT_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            self._error = self._error or "handshake timed out"
            self._note_failure()
            return False
        if self._error:
            self._note_failure()
            return False
        self._consecutive_failures = 0  # healthy handshake resets the breaker
        return True

    def _note_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            logger.warning(
                "MCP server '%s' tripped the spawn breaker after %d failures; "
                "cooling down %.0fs",
                self.name,
                self._consecutive_failures,
                _BREAKER_COOLDOWN_SECS,
            )

    async def list_tools(self) -> list[McpToolSpec]:
        if not await self.ensure_started():
            return []
        self.touch()
        return list(self._tools)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Invoke ``tool``; returns ``(ok, text_output_or_error)``."""
        if not await self.ensure_started():
            return False, f"MCP server '{self.name}' not connected: {self._error}"
        self.touch()
        # Schema-driven arg coercion: heal a model that emitted a numeric arg as a
        # string ("128") for a number/integer-typed field, which strict MCP servers
        # reject with -32602. ensure_started() has populated self._tools, so the
        # cached inputSchema is available. Non-numeric fields are untouched.
        spec = next((t for t in self._tools if t.name == tool), None)
        if spec is not None:
            arguments = _coerce_args_to_schema(arguments, spec.input_schema)
        assert self._requests is not None
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._requests.put(("call", {"tool": tool, "arguments": arguments}, fut))
        try:
            return await asyncio.wait_for(fut, timeout=_CALL_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            return False, f"MCP tool '{tool}' timed out after {_CALL_TIMEOUT_SECS:.0f}s"

    async def shutdown(self) -> None:
        self._closing = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ── actor body ──────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Hold the transport+session open, serve queued requests until cancelled."""
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession

            async with AsyncExitStack() as stack:
                read, write = await self._open_transport(stack)
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                await self._refresh_tools(session)
                self._ready.set()
                await self._serve(session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)[:300] or exc.__class__.__name__
            logger.warning("MCP server '%s' connection failed: %s", self.name, self._error)
            self._ready.set()  # unblock waiters with the error recorded

    async def _open_transport(self, stack: Any):
        """Enter the right transport context for this server's spec."""
        url = self.spec.get("url") or self.spec.get("endpoint")
        if url:
            transport = (self.spec.get("transport") or "").lower()
            if transport in ("http", "streamable-http", "streamable_http"):
                from mcp.client.streamable_http import streamablehttp_client

                read, write, _ = await stack.enter_async_context(streamablehttp_client(url))
                return read, write
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(sse_client(url))
            return read, write

        # stdio: spawn the declared command with the augmented PATH so a daemon
        # PATH still resolves node/npx/uvx the same way probe_server does.
        import os

        from mcp.client.stdio import StdioServerParameters, stdio_client

        from personalclaw.env import augmented_path

        command = self.spec.get("command", "")
        if not command:
            raise ValueError("server spec has neither 'url' nor 'command'")
        env = dict(os.environ)
        env["PATH"] = augmented_path(env.get("PATH", ""))
        env.update(self.spec.get("env") or {})
        # ``cwd`` lets an app-shipped server (registered by the app-platform MCP
        # bridge with cwd=app_dir) resolve relative command/args; ignored when
        # absent (the historical behavior — spawn in the gateway's cwd).
        cwd = self.spec.get("cwd") or None
        params = StdioServerParameters(
            command=command,
            args=list(self.spec.get("args") or []),
            env=env,
            cwd=cwd,
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        return read, write

    async def _refresh_tools(self, session: Any) -> None:
        result = await session.list_tools()
        tools: list[McpToolSpec] = []
        for t in getattr(result, "tools", None) or []:
            tools.append(
                McpToolSpec(
                    name=getattr(t, "name", ""),
                    description=getattr(t, "description", "") or "",
                    input_schema=getattr(t, "inputSchema", None)
                    or {"type": "object", "properties": {}},
                )
            )
        self._tools = tools

    async def _serve(self, session: Any) -> None:
        assert self._requests is not None
        while not self._closing:
            kind, payload, fut = await self._requests.get()
            if kind != "call":
                if not fut.done():
                    fut.set_result((False, f"unknown request: {kind}"))
                continue
            try:
                result = await session.call_tool(payload["tool"], payload["arguments"])
                if not fut.done():
                    fut.set_result((not getattr(result, "isError", False), _coerce_output(result)))
            except Exception as exc:  # noqa: BLE001
                if not fut.done():
                    fut.set_result((False, str(exc)[:500]))


def _coerce_output(result: Any) -> str:
    """Flatten an MCP ``CallToolResult`` into the text string the loop feeds back."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
            continue
        data = getattr(block, "data", None)
        if data is not None:
            parts.append(f"[{getattr(block, 'mimeType', 'binary')} data]")
    if parts:
        return "\n".join(parts)
    # Structured-only result (no content blocks) → serialize the model.
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        import json

        try:
            return json.dumps(dump(), default=str)
        except Exception:  # noqa: BLE001
            pass
    return str(result)


def _is_poolable(spec: dict[str, Any]) -> bool:
    """Whether a server is safe to SHARE across sessions.

    Safe-by-default means *not* shared: a server is pooled (one connection for
    all sessions) only when it explicitly declares ``poolable: true``. Stateful
    servers (a browser with a logged-in page, a shell with a cwd) must default to
    per-session isolation so one session's state can't leak into another's."""
    return bool(spec.get("poolable", False))


def _spec_hash(spec: dict[str, Any]) -> str:
    """A stable content hash of the connection-defining fields of a server spec, so two
    servers sharing a NAME but differing in command/args/env/url/transport get DISTINCT
    pool entries instead of colliding on one connection (P23e). Uses sha256 over a
    sort-keyed JSON of only the fields that change what process/endpoint we talk to —
    NEVER Python ``hash()`` (its per-process salt would give a different key every run,
    breaking any cross-process/cross-surface sharing that keys off this)."""
    import hashlib
    import json

    material = {
        "command": spec.get("command", ""),
        "args": spec.get("args", []),
        "env": spec.get("env", {}),
        "url": spec.get("url", ""),
        "transport": spec.get("transport", ""),
    }
    blob = json.dumps(material, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# Registry key = (name, scope, spec_hash):
#   name       — the configured server name (listing / reconcile / removal key on k[0]).
#   scope      — "" for a poolable (shared) server, else the owning session_key (isolation
#                / eviction key on k[1]); unchanged semantics.
#   spec_hash  — content hash (P23e) so same-name/different-config servers don't collide.
_ConnKey = tuple[str, str, str]


def _conn_key(name: str, spec: dict[str, Any], session_key: str) -> _ConnKey:
    """Registry key for a (server, caller). Poolable → shared scope ``""``; otherwise
    scoped to the session. The content hash disambiguates same-name/different-spec."""
    scope = "" if _is_poolable(spec) else (session_key or "")
    return (name, scope, _spec_hash(spec))


class McpClientRegistry:
    """Process-wide registry of live MCP server connections (PClaw scope).

    Connections are keyed by ``(server_name, scope)``. Poolable servers share one
    connection (scope ``""``) across every session — the optimal, already-pooled
    path. Non-poolable (stateful) servers get one connection PER session so their
    state can't leak across sessions (safe-by-default isolation). Idle connections
    are reaped on a sweep so resident memory tracks *active* (server, session)
    pairs, not every session that ever touched a server."""

    def __init__(self) -> None:
        self._conns: dict[_ConnKey, McpServerConn] = {}
        self._specs: dict[str, dict[str, Any]] = {}
        self._sweeper: asyncio.Task | None = None
        # P23d observability counters (process-lifetime totals).
        self._stats = {"spawns": 0, "reaps": 0, "served": 0, "evicted": 0}

    def _canonical_key(self, name: str) -> _ConnKey | None:
        """The shared (scope ``""``) key for a configured server, or None if unknown.
        Computed via the current spec so it tracks a content-hash change on reconcile."""
        spec = self._specs.get(name)
        return _conn_key(name, spec, "") if spec is not None else None

    def items(self):
        """Canonical (scope ``""``) connection per configured server — the Tools
        page lists each server once. The tool *surface* is identical across scopes,
        so listing from the canonical connection is correct; per-session isolation
        only matters for stateful ``call_tool`` traffic."""
        out = []
        for name in self._specs:
            key = self._canonical_key(name)
            if key is not None and key in self._conns:
                out.append((name, self._conns[key]))
        return out

    def get(self, name: str, session_key: str = "") -> McpServerConn | None:
        """Resolve the connection a caller should use for ``name``.

        - Poolable server → the shared canonical connection (one for all sessions).
        - Stateful server + a session key → a per-session connection, created on
          demand, so that session's state can't leak into another's.
        - Stateful server + no session key → the canonical connection (listing /
          no session to isolate).

        Returns ``None`` for an unknown server."""
        spec = self._specs.get(name)
        if spec is None:
            # directly-registered / legacy: match the first key for this name.
            return next((c for k, c in self._conns.items() if k[0] == name), None)
        key = _conn_key(name, spec, session_key)
        conn = self._conns.get(key)
        if conn is None:
            conn = McpServerConn(name, spec, scope=key[1])
            self._conns[key] = conn
            self._stats["spawns"] += 1
        self._stats["served"] += 1
        return conn

    def load_from_specs(self, specs: dict[str, dict[str, Any]]) -> None:
        """Reconcile the registry to ``specs`` ({name: spec}). Each configured
        server gets its canonical (scope ``""``) connection eagerly (lazy-connected
        on first use); per-session connections for stateful servers are added on
        demand by :meth:`get`. Removed servers — AND servers whose spec content
        changed (new content hash) — have their stale connections dropped."""
        self._specs = {
            n: s for n, s in specs.items() if isinstance(s, dict) and not s.get("disabled")
        }
        # The set of keys that SHOULD exist for the current specs (canonical scope).
        want_canonical = {self._canonical_key(n) for n in self._specs}
        for name, spec in self._specs.items():
            key = _conn_key(name, spec, "")
            if key not in self._conns:
                self._conns[key] = McpServerConn(name, spec, scope="")
                self._stats["spawns"] += 1
        # Drop connections whose server is gone OR whose spec content changed (a
        # canonical key no longer in want_canonical is a stale-hash orphan). Per-session
        # (scoped) conns of a still-configured server are left to evict_session/sweep.
        for key in list(self._conns):
            name, scope, _hash = key
            gone = name not in self._specs
            stale_hash = scope == "" and key not in want_canonical
            if gone or stale_hash:
                conn = self._conns.pop(key)
                asyncio.ensure_future(conn.shutdown())

    def evict_session(self, session_key: str) -> None:
        """Shut down + drop all connections scoped to an ending session. Shared
        (poolable, scope ``""``) connections are untouched."""
        if not session_key:
            return
        for key in [k for k in self._conns if k[1] == session_key]:
            conn = self._conns.pop(key)
            self._stats["evicted"] += 1
            asyncio.ensure_future(conn.shutdown())

    def sweep_idle(self, ttl_secs: float = _IDLE_TTL_SECS) -> int:
        """Reap connections unused for longer than ``ttl_secs``. Returns the count
        reaped. A reaped server simply re-lazy-starts on its next use."""
        now = time.monotonic()
        reaped = 0
        for key in [k for k, c in self._conns.items() if now - c.last_used > ttl_secs]:
            conn = self._conns.pop(key)
            asyncio.ensure_future(conn.shutdown())
            reaped += 1
        self._stats["reaps"] += reaped
        if reaped:
            logger.debug("MCP idle sweep reaped %d connection(s)", reaped)
        return reaped

    def start_sweeper(self) -> None:
        """Start the periodic idle-eviction loop (idempotent)."""
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop(), name="mcp-idle-sweeper")

    async def _sweep_loop(self) -> None:
        from personalclaw import shutdown_event

        while not shutdown_event.is_set():
            try:
                await asyncio.sleep(_SWEEP_INTERVAL_SECS)
                self.sweep_idle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("MCP idle sweep failed", exc_info=True)

    def pool_stats(self) -> dict[str, Any]:
        """P23d observability: process-lifetime counters + a live snapshot of the pool.

        ``shared_conns`` counts poolable (scope ``""``) connections — the ones serving
        many callers from ONE process; ``session_conns`` counts per-session (isolated)
        ones. ``dedup_saved`` estimates connections avoided by pooling: every ``served``
        beyond the number of live connections is a call that reused an existing conn
        instead of spawning. Pure/read-only — safe to call from an API handler."""
        live = len(self._conns)
        shared = sum(1 for k in self._conns if k[1] == "")
        served = self._stats["served"]
        return {
            "live_connections": live,
            "shared_conns": shared,
            "session_conns": live - shared,
            "configured_servers": len(self._specs),
            "spawns": self._stats["spawns"],
            "reaps": self._stats["reaps"],
            "served": served,
            "evicted": self._stats["evicted"],
            # calls that reused a live conn rather than spawning (pooling payoff).
            "reused": max(0, served - self._stats["spawns"]),
        }

    async def shutdown_all(self) -> None:
        if self._sweeper and not self._sweeper.done():
            self._sweeper.cancel()
            self._sweeper = None
        await asyncio.gather(*(c.shutdown() for c in self._conns.values()), return_exceptions=True)
        self._conns.clear()


_registry: McpClientRegistry | None = None


def _personalclaw_mcp_specs() -> dict[str, dict[str, Any]]:
    """Load the PClaw-scope server specs from ``~/.personalclaw/mcp.json``.

    This is the single store the native client spawns from — the one the MCP
    Tools provider card writes and ``/api/mcp/apply`` imports into.
    """
    import json

    path = Path.home() / ".personalclaw" / "mcp.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {}
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    return (
        {k: v for k, v in servers.items() if isinstance(v, dict)}
        if isinstance(servers, dict)
        else {}
    )


def get_mcp_client_registry() -> McpClientRegistry | None:
    """Return the process-wide registry, or ``None`` if the SDK is absent.

    Lazily loads specs from ``~/.personalclaw/mcp.json`` on first call. Returns
    ``None`` (not an empty registry) when the ``mcp`` extra isn't installed, so
    callers can distinguish "no SDK" from "SDK present, no servers".
    """
    if not mcp_sdk_available():
        return None
    global _registry
    if _registry is None:
        _registry = McpClientRegistry()
    _registry.load_from_specs(_personalclaw_mcp_specs())
    return _registry


def with_mcp_session_eviction(
    prior: "Callable[[str], Awaitable[object]] | None",
) -> "Callable[[str], Awaitable[None]]":
    """Wrap a session-expire callback so it also evicts that session's per-session
    MCP connections (stateful servers). Composed onto the existing expire chain so
    it runs ALONGSIDE consolidation + workflow cleanup, never instead of them.
    Best-effort: a failure here never blocks the rest of session teardown."""

    async def _expire(session_key: str) -> None:
        if prior is not None:
            try:
                await prior(session_key)
            except Exception:
                logger.warning(
                    "session-expire prior callback failed for %s", session_key, exc_info=True
                )
        try:
            if _registry is not None:
                _registry.evict_session(session_key)
        except Exception:
            logger.debug("MCP session eviction failed for %s", session_key, exc_info=True)

    return _expire
