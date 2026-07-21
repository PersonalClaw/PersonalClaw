"""Generic ACP-over-stdio agent provider.

Spawns an external CLI that speaks the open Agent Client Protocol
(JSON-RPC 2.0 over stdio) and translates ACP events into ``LLMEvent``s.

This module is vendor-neutral: it knows nothing about specific ACP agent CLIs,
hardcoded internal binary discovery paths, or any specific bot-name
branding. The launch command is supplied by the caller (typically a
``ProviderEntry``'s ``options.command``); ``claude`` is one possible
backend, [Zed's ACP-compliant agents](https://github.com/zed-industries/agent-client-protocol)
are another.

Capabilities are negotiated through the ACP ``initialize`` handshake:
``declared_capabilities`` reflects the capability set declared by the
spawned agent in its initialize response (R5.4).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.acp.client import AcpClient
from personalclaw.acp.errors import AcpError
from personalclaw.acp.types import STOP_REASON_CANCELLED, STOP_REASON_END_TURN
from personalclaw.agents.provider import AgentProvider
from personalclaw.llm.base import (
    CancelOutcome,
    LLMEvent,
    ModelProvider,
)
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import (
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
)

if TYPE_CHECKING:
    from personalclaw.agents.provider import DiscoveredAgent, ReadinessStatus

logger = logging.getLogger(__name__)


# The ``initialize`` handshake advertises CLIENT_NAME ("personalclaw") as the
# client identity. The ACP *agent* (the session/set_mode modeId) has NO
# vendor-neutral default — it is a per-session choice, and an unselected agent
# means "use the CLI's own built-in default" (empty agent → dialect skips
# activation). There is deliberately no fabricated default agent name.


class AcpAgentProvider(ModelProvider, AgentProvider):
    """Generic ACP-over-stdio agent runtime.

    Spawns the configured ``command`` as a subprocess, completes the open
    ACP ``initialize`` handshake, and translates ACP events into
    ``LLMEvent``s. The underlying protocol layer is
    :class:`personalclaw.acp.client.AcpClient`; this provider stays vendor-
    neutral and does not reach into that layer for agent-specific
    knobs.

    Implements both axes during the introduce-alongside transition: it is an
    :class:`~personalclaw.llm.base.ModelProvider` (the surface the factory/bridge
    return today) and an :class:`~personalclaw.agents.provider.AgentProvider`
    (the stateful-runtime axis the SessionManager probes for session/pid/resume
    capabilities via ``isinstance``). The native loop (E2-P4) is the second
    ``AgentProvider``.
    """

    @property
    def provider_id(self) -> str:
        """Runtime id: ``acp:<cli>`` keyed off the launch command basename."""
        cli = Path(self._command[0]).name if self._command else "agent"
        return f"acp:{cli}"

    @classmethod
    async def probe_readiness(cls, options: dict) -> "ReadinessStatus":
        """Probe whether the configured ACP CLI is installed and signed in.

        Resolution order:
          * no ``command`` configured → ``error``
          * adapter binary not on PATH → ``not_found``
          * a declared delegate engine CLI (``options.requires_executable``) is
            absent → ``not_found`` (handshake never attempted — see below)
          * spawn + ACP ``initialize`` succeeds → ``ready``
          * handshake fails with an auth/login signal → ``needs_login`` (with a
            best-effort ``login_command`` argv for the Sign-in terminal)
          * any other failure → ``error``

        This is the single source of truth for ACP readiness; the CLI doctor
        consumes it too (no parallel probe path).
        """
        import shutil

        from personalclaw.agents.provider import ReadinessStatus

        command = options.get("command")
        if not isinstance(command, list) or not command:
            return ReadinessStatus(
                ready=False, state="error", detail="no options.command configured"
            )
        command = [str(part) for part in command]

        bin_path = shutil.which(command[0])
        if not bin_path:
            return ReadinessStatus(
                ready=False,
                state="not_found",
                detail=f"'{command[0]}' not found on PATH",
            )

        # Adapter-provisioning gate. When the launch argv is the ``npx -y <pkg>``
        # last resort (the adapter isn't installed on disk — auto-provisioning
        # either wasn't run or couldn't find a new-enough Node), that path only
        # works under a Node the adapter supports (>= 20). If NO such Node exists
        # on this machine, npx would die with EBADENGINE — so report a clean,
        # actionable ``not_found`` up front instead of spawning npx and surfacing
        # a raw fetch/engine stack trace. When a Node >= 20 IS present, npx is a
        # supported cold path (see the timeout budget below) — let it proceed.
        from personalclaw.acp.cli_resolve import is_npx_fallback, resolve_node_ge

        if is_npx_fallback(command) and not resolve_node_ge():
            return ReadinessStatus(
                ready=False,
                state="not_found",
                detail=(
                    "ACP adapter is not installed and cannot be auto-provisioned: "
                    "no Node >= 20 found (the adapter needs it). Install Node >= 20, "
                    "install the adapter, or set the bundle's *_ACP_BIN override."
                ),
            )

        # Delegate-engine gate. Some ACP adapters (claude-agent-acp, codex-acp)
        # are thin protocol shims that run via ``npx`` and hand the actual model
        # turn to a SEPARATE engine CLI. The ACP ``initialize`` handshake
        # succeeds without that engine, so a passing handshake is NOT proof the
        # runtime can serve a turn. When a bundle declares ``requires_executable``
        # (vendor knowledge — which adapter delegates to what), enforce it here
        # so the absent engine surfaces as ``not_found`` up front instead of a
        # hollow ``ready`` that dies on the first prompt. The engine is satisfied
        # when the bundle resolved a path (forwarded via its env var) OR it is
        # live-resolvable on the daemon PATH right now.
        requires = options.get("requires_executable")
        if isinstance(requires, dict):
            label = str(requires.get("label") or "engine")
            declared_path = str(requires.get("path") or "").strip()
            engine = declared_path or shutil.which(label) or ""
            if not engine:
                env_var = str(requires.get("env_var") or "").strip()
                hint = f" (set {env_var} or install it)" if env_var else ""
                return ReadinessStatus(
                    ready=False,
                    state="not_found",
                    detail=(
                        f"ACP adapter present but its engine CLI '{label}' was "
                        f"not found{hint} — the adapter delegates the model turn "
                        f"to it, so the runtime cannot serve without it."
                    ),
                )

        provider = cls(
            command=command,
            cwd=options.get("cwd"),
            env=options.get("env") or {},
            # The probe MUST use the configured dialect — otherwise it handshakes
            # with the default protocol shape and an adapter expecting a
            # different one (e.g. claude/codex int protocolVersion) rejects it
            # with -32602, masking a perfectly-installed CLI as "error".
            dialect=options.get("dialect"),
        )
        # A cold start can be slow on desktop: claude-code-acp runs via an
        # ``npx`` fetch on first use, and a version-manager-shimmed CLI has its
        # own warm-up — both can exceed a few seconds before the ACP
        # ``initialize`` even begins. A 10s budget made authenticated-but-slow
        # CLIs probe as timed-out and (worse) get mislabeled needs_login. Use a
        # realistic cold-start budget; the probe is one-shot per Settings load.
        probe_timeout = float(options.get("probe_timeout_secs") or 45)
        try:
            await asyncio.wait_for(provider.start(), timeout=probe_timeout)
            caps = sorted(provider.declared_capabilities)
            await provider.shutdown()
            return ReadinessStatus(
                ready=True,
                state="ready",
                detail=f"initialize OK (caps: {', '.join(caps) or 'none'})",
            )
        except Exception as exc:  # noqa: BLE001 - probe summarizes any failure
            try:
                await provider.shutdown()
            except Exception:
                pass
            msg = str(exc).lower()
            timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError))

            # ACP CLIs that require interactive auth surface it via an explicit
            # initialize error ("not logged in", "authenticate", "unauthorized"
            # — codex returns "Authentication required"). Detect that generically
            # (no vendor-specific string) and route to Sign-in.
            #
            # A bare *timeout* with NO auth signal is NOT proof of "not signed
            # in" — on desktop a cold ``npx`` fetch or a shimmed-CLI warm-up can
            # simply outrun the probe budget even when the CLI is fully
            # authenticated. Reporting those as ``needs_login`` is exactly what
            # made an authenticated CLI / claude-code show a false Sign-in
            # prompt + "not available". So a timeout gets its own ``timeout``
            # state (retryable), keeping the bundle's ``login_command`` attached
            # only as an optional fallback action — never asserting the user is
            # unauthenticated.
            login_signals = ("log in", "login", "logged in", "authenticat", "unauthor", "sign in")
            declared = options.get("login_command")
            has_login_cmd = isinstance(declared, list) and bool(declared)
            signalled = any(sig in msg for sig in login_signals)

            # Sign-in runs the CLI interactively (without the ACP/stdio flags
            # that suppress the TUI). Prefer the bundle-declared login argv (e.g.
            # ``claude /login``) — the vendor knows its own auth verb; the core
            # never names it. Fall back to bare invocation of the adapter binary.
            login_cmd = [str(p) for p in (declared or [])] if has_login_cmd else [command[0]]

            if signalled:
                return ReadinessStatus(
                    ready=False,
                    state="needs_login",
                    detail=f"agent requires sign-in: {exc}",
                    login_command=login_cmd,
                )
            if timed_out:
                return ReadinessStatus(
                    ready=False,
                    state="timeout",
                    detail=(
                        f"handshake timed out after {probe_timeout:.0f}s (cold start "
                        "may be slow — retry; sign in only if the agent needs auth)"
                    ),
                    login_command=login_cmd,
                )
            return ReadinessStatus(ready=False, state="error", detail=f"handshake failed: {exc}")

    @classmethod
    async def discover_agents(cls, options: dict) -> list["DiscoveredAgent"]:
        """Open one session, read ``session/new``, normalize via the dialect.

        Maps the dialect's vendor-neutral :class:`DiscoveryResult` to
        :class:`DiscoveredAgent` rows for the chat picker. The model-override list
        is attached to every discovered agent of the runtime (the runtime offers
        the same models regardless of which persona/effort is active). Returns
        ``[]`` on any failure — discovery never raises into the API.

        ``options`` mirrors the ``probe_readiness`` shape plus ``runtime_id`` (the
        ``acp:<cli>`` entry name) used to label and id the rows; falls back to the
        command basename when absent.
        """
        import shutil

        from personalclaw.acp.client import CLIENT_NAME, CLIENT_VERSION
        from personalclaw.acp.dialect import get_dialect

        command = options.get("command")
        if not isinstance(command, list) or not command:
            return []
        command = [str(p) for p in command]
        if not shutil.which(command[0]):
            return []

        # Gate discovery on readiness: a runtime that can't actually serve (e.g.
        # codex's adapter handshakes via npx but its engine CLI is absent →
        # not_found) must contribute NO discovered agents, so the picker never
        # offers a runtime that would fail on the first turn. This reuses the
        # single readiness source of truth (delegate gate included), keeping
        # discovery and /api/agent-providers in agreement.
        status = await cls.probe_readiness(options)
        if not status.ready:
            logger.debug(
                "discover_agents: %s not ready (%s) — no agents",
                options.get("runtime_id") or command[0],
                status.state,
            )
            return []

        # Ensure runtime_id is present in options so agents_from_snapshot (which
        # reads it) gets the derived fallback too.
        if not str(options.get("runtime_id") or "").strip():
            options = {**options, "runtime_id": f"acp:{Path(command[0]).name}"}

        dialect = get_dialect(options.get("dialect"))
        # Probe on a THROWAWAY AcpConnection — the same shared machinery the client
        # wraps N=1, but here used bare: spawn → initialize → new_session returns exactly
        # the discovery snapshot we need (no half-built client, no retired internals).
        from personalclaw.acp.session import AcpConnection

        work_dir = Path.home() / ".personalclaw" / "workspace"
        timeout = float(options.get("probe_timeout_secs") or 45)
        connection: "AcpConnection | None" = None
        try:
            connection = await asyncio.wait_for(
                AcpConnection.spawn(
                    command=command,
                    work_dir=work_dir,
                    dialect=dialect,
                    extra_env=options.get("env") or {},
                ),
                timeout=timeout,
            )
            await connection.initialize(
                {
                    "protocolVersion": dialect.protocol_version(),
                    "clientInfo": dialect.client_info(
                        client_name=CLIENT_NAME, client_version=CLIENT_VERSION
                    ),
                },
                timeout=timeout,
            )
            await connection.new_session({"cwd": str(work_dir), "mcpServers": []}, timeout=timeout)
            session_new = dict(connection.last_session_new_snapshot or {})
        except Exception as exc:  # noqa: BLE001 - discovery never raises into the API
            logger.debug("discover_agents failed for %s: %s", options.get("runtime_id"), exc)
            return []
        finally:
            if connection is not None:
                try:
                    await connection.close()
                except Exception:
                    pass

        return cls.agents_from_snapshot(options, session_new)

    @classmethod
    def agents_from_snapshot(cls, options: dict, snapshot: dict) -> list["DiscoveredAgent"]:
        """Map a raw ``session/new`` snapshot to ``DiscoveredAgent`` rows.

        Pure (no spawn): the dialect normalizes the vendor shape, then we apply the
        runtime-scoped id + display name. Shared by :meth:`discover_agents` (after
        its throwaway probe) and the connection pool's snapshot fast-path, so both
        produce identical rows."""
        from personalclaw.acp.dialect import get_dialect
        from personalclaw.agents.provider import DiscoveredAgent

        runtime_id = str(options.get("runtime_id") or "").strip() or "acp"
        runtime_label = str(options.get("runtime_label") or "").strip() or (
            runtime_id.split(":", 1)[-1] if ":" in runtime_id else runtime_id
        )
        dialect = get_dialect(options.get("dialect"))
        result = dialect.normalize_discovery(snapshot or {})
        # Reasoning effort is a per-turn SETTING now, so a runtime exposes ONE
        # agent (default-dialect personas still map 1:1 to availableModes). The
        # backend's declared effort options ride along as supported_efforts for the
        # composer's reasoning control — no effort-agent variants in the picker.
        supported_efforts = list(result.supported_efforts)
        agents: list[DiscoveredAgent] = []
        for a in result.agents:
            use_prefix = bool(a.get("use_runtime_prefix"))
            label = str(a.get("label") or "")
            if use_prefix:
                name = runtime_label if not label else f"{runtime_label} ({label})"
            else:
                name = label or str(a.get("id") or runtime_label)
            raw_id = str(a.get("id") or "")
            disc_id = f"{runtime_id}/{raw_id}" if raw_id else runtime_id
            agents.append(
                DiscoveredAgent(
                    id=disc_id,
                    name=name,
                    runtime=runtime_id,
                    description=str(a.get("description") or ""),
                    provider_agent=str(a.get("provider_agent") or ""),
                    reasoning_effort=str(a.get("reasoning_effort") or ""),
                    models=list(result.models),
                    supported_efforts=supported_efforts,
                )
            )
        return agents

    def __init__(
        self,
        *,
        command: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        model: str | None = None,
        agent_name: str = "",
        capability_flags: dict[str, bool] | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
        sandbox_mode: str = "auto",
        session_files_dir: Path | None = None,
        dialect: str | None = None,
        mode: str = "",
        reasoning_effort: str = "",
    ) -> None:
        if not command:
            raise ValueError("AcpAgentProvider requires a non-empty command list")
        self._command: list[str] = list(command)
        # Per-CLI ACP protocol dialect, selected by the bundle (the ``<cli>`` of
        # ``acp:<cli>``). Resolved to a strategy object the vendor-neutral client
        # delegates its handshake/permission divergences to. None → default
        # (default-dialect) shape.
        self._dialect_id: str | None = dialect
        self._cwd: Path | None = Path(cwd) if cwd is not None else None
        self._env: dict[str, str] = dict(env or {})
        self._model: str | None = model
        self._agent_name: str = agent_name
        self._capability_flags: dict[str, bool] = dict(capability_flags or {})
        self._session_key: str | None = session_key
        self._channel_id: str | None = channel_id
        self._sandbox_mode: str = sandbox_mode
        # Permission/operating mode for adapters with a separate mode axis (Zed:
        # default/acceptEdits/plan/dontAsk/bypassPermissions). Empty for the default dialect
        # (no separate mode axis) and for native default behaviour.
        self._mode: str = mode or ""
        # Per-turn reasoning effort — one of the backend's declared effort options
        # (verbatim); applied via the dialect's set_effort_request. Empty = default.
        self._reasoning_effort: str = reasoning_effort or ""
        self._session_files_dir: Path | None = (
            Path(session_files_dir) if session_files_dir is not None else None
        )

        # Negotiated capability set, populated after ``start()`` completes
        # the ACP ``initialize`` handshake (R5.4). Empty until then.
        self._negotiated_capabilities: frozenset[str] = frozenset()

        # The provider holds the protocol-level client. The client is
        # vendor-neutral with respect to its API surface; backend-specific
        # session-files behavior is opted in via ``session_files_dir``.
        from personalclaw.acp.dialect import get_dialect

        self._client: AcpClient = AcpClient(
            work_dir=self._cwd,
            model=self._model,
            agent=self._agent_name,
            sandbox_mode=self._sandbox_mode,
            session_key=self._session_key,
            channel_id=self._channel_id,
            extra_env=self._env or None,
            command=self._command,
            session_files_dir=self._session_files_dir,
            dialect=get_dialect(self._dialect_id),
            mode=self._mode,
            reasoning_effort=self._reasoning_effort,
        )

    # ── Lifecycle ────────────────────────────────────────────────────

    @property
    def client(self) -> AcpClient:
        """Expose the underlying protocol client (for is_ready / readiness checks)."""
        return self._client

    async def start(self) -> None:
        """Spawn the configured command and run the ACP ``initialize`` handshake.

        After this returns, :attr:`declared_capabilities` reflects the
        capability set declared by the spawned agent in its ACP
        ``initialize`` response (R5.4).
        """
        await self._client.ensure_ready()
        # Snapshot the negotiated capabilities. ``AcpClient`` records the
        # full ``agentCapabilities`` dict from the initialize response.
        # We surface it as a vendor-neutral ``frozenset[str]`` keyed by
        # capability name; downstream consumers can look up specific
        # boolean flags via ``client._agent_capabilities`` if they need
        # the raw shape.
        agent_caps = getattr(self._client, "_agent_capabilities", None) or {}
        names: set[str] = set()
        if isinstance(agent_caps, dict):
            for key, value in agent_caps.items():
                if isinstance(value, bool):
                    if value:
                        names.add(str(key))
                else:
                    # Non-boolean entries (sub-objects, version strings, ...)
                    # are surfaced by name so callers can still introspect
                    # what was declared.
                    names.add(str(key))
        self._negotiated_capabilities = frozenset(names)

    async def shutdown(self) -> None:
        """Send ACP shutdown, drain stdio, and reap the subprocess (R5.7)."""
        await self._client.shutdown()

    @property
    def declared_capabilities(self) -> frozenset[str]:
        """Capabilities declared by the spawned agent in its ACP ``initialize`` response.

        Empty until :meth:`start` completes the handshake. The router
        consults this set (or the static :class:`ProviderCapability`
        descriptor for the ``acp_agent`` type) to decide whether the
        entry can serve a use case.
        """
        return self._negotiated_capabilities

    @property
    def session_snapshot(self) -> dict:
        """The live session's raw ``session/new`` response (modes / models /
        configOptions), or ``{}`` before start. The connection pool reads this to
        serve agent discovery off a warmed connection without a second spawn."""
        return self._client.session_snapshot

    # ── Streaming and events ─────────────────────────────────────────

    @staticmethod
    def _to_llm_event(e: Any) -> LLMEvent:
        # The ACP→neutral translation lives in acp/adapter.py (the only place
        # that knows both AcpEvent and the neutral AgentEvent shapes).
        from personalclaw.acp.adapter import acp_event_to_agent_event

        return acp_event_to_agent_event(e)

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        async for e in self._client.stream_events(message):
            yield self._to_llm_event(e)

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        async for e in self._client.stream_command(command):
            yield self._to_llm_event(e)

    async def approve_tool(self, request_id: str | int) -> None:
        await self._client.approve_tool(request_id)

    async def reject_tool(self, request_id: str | int) -> None:
        await self._client.reject_tool(request_id)

    async def start_fresh_turn_session(self) -> None:
        """Start a fresh agent session on the live process (see AcpClient)."""
        await self._client.start_fresh_turn_session()

    def context_usage_pct(self) -> float:
        return self._client.last_prompt_stats.context_pct

    async def compact(self, context: str = "") -> None:
        """Trigger a native ``/compact`` slash command on the spawned agent.

        Behavior is protocol-level (the open ACP ``commands/execute`` path)
        rather than vendor-specific. When ``context`` is provided, it is
        truncated to keep the prompt bounded.
        """
        if context:
            prompt = context[:4000] if len(context) > 4000 else context
            await self._client.send_command(
                f"/compact Preserve this session context in the summary:\n{prompt}"
            )
        else:
            await self._client.send_command("/compact")

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Cancel in-flight operation via ACP ``session/cancel``."""
        if not self._client.has_active_turn():
            logger.debug("provider.cancel: no active turn, skip")
            return "no_turn"
        try:
            await self._client.cancel_session()
        except AcpError:
            logger.debug("provider.cancel: cancel_session raised AcpError", exc_info=True)
            return "error"
        if wait_ack_timeout <= 0:
            # Fire-and-forget: cancel notification sent, caller does not
            # wait for the agent's ack. Return "acked" optimistically so
            # callers that don't care about confirmation stay happy. Any
            # caller that needs a real ack MUST pass a positive timeout.
            logger.debug("provider.cancel: wait_ack_timeout=0, returning acked")
            return "acked"
        try:
            reason = await self._client.wait_turn_done(timeout=wait_ack_timeout)
            logger.debug("provider.cancel: wait_turn_done returned reason=%r", reason)
            if reason in (STOP_REASON_CANCELLED, STOP_REASON_END_TURN):
                return "acked"
            logger.debug("Unexpected stop reason after cancel: %r", reason)
            return "timeout"
        except asyncio.TimeoutError:
            logger.debug("provider.cancel: wait_turn_done timed out after %.1fs", wait_ack_timeout)
            return "timeout"

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        """Wait for compaction completed/failed after stream ends."""
        return await self._client.wait_for_compaction(timeout)

    def is_alive(self) -> bool:
        return self._client.is_responsive()

    def is_process_alive(self) -> bool:
        """True if the underlying OS process has not exited (ignores I/O staleness)."""
        return self._client.is_process_alive()

    @property
    def exit_code(self) -> int | None:
        """Process exit code, or None if still running."""
        return self._client.exit_code

    def touch_activity(self) -> None:
        self._client.touch_activity()

    def set_workspace(self, path: Path) -> None:
        self._cwd = Path(path)
        # The protocol client is the source of truth for the spawned
        # subprocess's cwd; keep it in sync with the provider-level view.
        self._client._work_dir = self._cwd

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        """Rebind this provider to a different logical session.

        Updates the session-scoped filesystem identifier the spawned
        agent uses to namespace its on-disk session files. Implemented
        by delegating to :meth:`AcpClient.rekey`, which is safe to call
        whether or not the agent process has started yet. ``channel_id``
        rebinds the channel in the same call (warm-pool claim path).
        """
        self._session_key = session_key
        self._channel_id = channel_id
        self._client.rekey(session_key, channel_id)

    def set_channel(self, channel_id: str | None) -> None:
        """Rebind the channel id (collab room) without changing the session key."""
        self._channel_id = channel_id

    async def set_model(self, model: str) -> None:
        """Switch the active model on the running agent (post-pool-claim)."""
        await self._client.set_model(model)

    async def set_agent(self, agent: str) -> None:
        """Switch the active agent/persona on the running connection (post-pool-
        claim). The default dialect issues ``session/set_mode``; Zed adapters no-op."""
        await self._client.set_agent(agent)

    async def set_mode(self, mode: str) -> None:
        """Switch the permission/operating mode on the running connection (post-
        pool-claim). Zed adapters issue ``set_config_option`` (configId=mode);
        the default dialect no-ops. MUST follow :meth:`set_model`."""
        await self._client.set_mode(mode)

    async def set_reasoning_effort(self, effort: str) -> None:
        """Apply the per-turn reasoning effort on the running connection (Zed:
        ``set_config_option`` configId=effort; default dialect no-ops). The value
        is the backend's own declared effort option — no translation. MUST follow
        :meth:`set_model`."""
        self._reasoning_effort = effort or ""
        await self._client.set_effort(effort or "")

    def set_resume(self, session_id: str) -> None:
        """Set the ACP session id to resume before ``start()`` initializes."""
        self._client.set_resume_session_id(session_id)

    @property
    def resumed(self) -> bool:
        """True if the last ``start()`` resumed an existing ACP session."""
        return bool(getattr(self._client, "resumed", False))

    @property
    def agent_model(self) -> str:
        """The model the wrapped client is configured with (for context table)."""
        return self._client._model or ""

    @property
    def agent_name(self) -> str:
        """The agent name the wrapped client is configured with."""
        return self._client._agent or ""

    @property
    def pid(self) -> int | None:
        """The spawned agent process PID, or None."""
        return getattr(self._client, "_pid", None)

    @property
    def session_id(self) -> str:
        """Return the spawned ACP agent's session UUID."""
        return self._client._session_id if self._client and self._client._session_id else ""

    # ``cleanup_session`` intentionally inherits the no-op default from
    # :class:`ModelProvider`. Filesystem cleanup of agent-specific session
    # files is the protocol layer's responsibility and is wired through
    # :mod:`personalclaw.subagent_persistence`. Keeping the provider free of
    # any hardcoded path satisfies R5.5 (no internal binary discovery
    # paths or vendor-specific defaults at the provider boundary).


# ── Capability descriptor ────────────────────────────────────────────────
#
# An ``acp_agent`` is a generic JSON-RPC stdio agent. The static descriptor
# advertises the protocol-level capability surface the wrapper exposes
# (chat, code-tools with approval, streaming); per-spawn capabilities are
# negotiated through the ACP ``initialize`` handshake and surfaced via
# :attr:`AcpAgentProvider.declared_capabilities`. Embedding and vision are
# off by default — agents that support them can declare so during
# initialize and a follow-up router task can route those use cases
# accordingly.
ACP_AGENT_CAPABILITY = ProviderCapability(
    type="acp_agent",
    capabilities=frozenset(
        {
            Capability.CHAT,
            Capability.CODE_TOOLS,
            Capability.STREAMING,
            Capability.TOOL_APPROVAL,
            Capability.PLANNING,
            Capability.SUMMARIZATION,
        }
    ),
    supports_streaming=True,
    supports_tools=True,
    supports_embeddings=False,
    supports_vision=False,
    max_context_tokens=0,  # agent-dependent (negotiated at initialize time)
    notes=(
        "Generic ACP-over-stdio agent; spawn command supplied via "
        "options.command. Per-spawn capabilities are negotiated through "
        "the ACP initialize handshake."
    ),
)


# ── Factory ──────────────────────────────────────────────────────────────


def _factory(
    *,
    entry: ProviderEntry,
    session_key: str | None = None,
    **kwargs: object,
) -> ModelProvider:
    """Construct an :class:`AcpAgentProvider` from a :class:`ProviderEntry`.

    Reads launch parameters from ``entry.options``:

    * ``command`` — required, the full launch argv (R5.6).
    * ``cwd`` — optional working directory.
    * ``env`` — optional extra environment variables.
    * ``agent_name`` — optional ACP agent/persona (the session/set_mode
      modeId), set by the per-session ``agent`` kwarg. Empty (the default)
      means no activation message — the CLI uses its own built-in default.
    * ``capability_flags`` — optional dict of extra ACP initialize hints.
    * ``sandbox_mode`` — optional OS-level sandbox mode.
    * ``session_files_dir`` — optional on-disk session-files directory
      for agents (e.g. claude) that persist tool results to JSONL.
    * ``channel_id`` — optional channel id passed via env.

    The credential, if declared on the entry, is currently not consumed —
    ACP agents authenticate themselves (e.g. claude auth). The
    parameter is reserved for future ACP backends that require explicit
    credentials.
    """
    options = dict(entry.options or {})

    command_value = options.get("command")
    if not isinstance(command_value, list) or not command_value:
        raise ProviderResolutionError(
            f"acp_agent provider entry {entry.name!r} requires a non-empty "
            f"options.command list (got {type(command_value).__name__})"
        )
    command = [str(part) for part in command_value]

    cwd_value = options.get("cwd")
    cwd: Path | None = Path(str(cwd_value)) if cwd_value else None

    env_value = options.get("env") or {}
    env: dict[str, str] = (
        {str(k): str(v) for k, v in env_value.items()} if isinstance(env_value, dict) else {}
    )

    # Agent selection is PER SESSION, not a property of the global acp:<cli>
    # runtime entry. The provider bridge passes the user's chosen agent as the
    # ``agent`` kwarg (the ACP modeId for session/set_mode — e.g. a persona-style
    # agent like "gpu-dev"); it must win over the entry-level default. Without this the
    # modeId never reaches the client and every session runs the agent's
    # built-in default — selecting an ACP agent in PersonalClaw would silently
    # do nothing.
    #
    # Falls back to EMPTY (not a fabricated name): ACP has no global default
    # agent, so an unselected agent means "let the CLI use its own built-in
    # default" — the dialect skips set_mode for an empty agent. Forwarding the
    # native brand here would send a modeId no ACP backend defines (the default dialect errors
    # ``Mode 'PersonalClaw' not found``).
    agent_name = (
        str(kwargs.get("agent") or "").strip() or str(options.get("agent_name") or "").strip()
    )

    # Model is likewise per session. The bridge maps the session's
    # ``model_override`` to the ``model`` kwarg; honor it over the entry's
    # default model hint so a user's model pick (or "auto") binds at spawn —
    # before the first prompt — rather than only via a post-start set_model.
    model = str(kwargs.get("model") or "").strip() or entry.model

    # Permission/operating mode — a per-session axis distinct from the agent
    # (Zed adapters: default/acceptEdits/plan/dontAsk/bypassPermissions). The
    # bridge threads the session's chosen mode as the ``acp_mode`` kwarg. Empty
    # → adapter default. Dialects without a separate mode axis (the default dialect) ignore it.
    mode = str(kwargs.get("acp_mode") or "").strip() or str(options.get("mode") or "").strip()

    # Per-turn reasoning effort — the bridge threads the session's chosen effort as
    # ``reasoning_effort_override`` (one of the backend's declared effort options,
    # verbatim). Empty → adapter default; dialects without an effort axis ignore it.
    reasoning_effort = str(kwargs.get("reasoning_effort_override") or "").strip()

    capability_flags_value = options.get("capability_flags") or {}
    capability_flags: dict[str, bool] = (
        {str(k): bool(v) for k, v in capability_flags_value.items()}
        if isinstance(capability_flags_value, dict)
        else {}
    )

    sandbox_mode = str(options.get("sandbox_mode") or "auto")

    session_files_dir_value = options.get("session_files_dir")
    session_files_dir: Path | None = (
        Path(str(session_files_dir_value)) if session_files_dir_value else None
    )

    channel_id_value = options.get("channel_id")
    channel_id: str | None = str(channel_id_value) if channel_id_value else None

    # Resolve credential through the credential store, if one is wired.
    # Currently informational — see docstring.
    if entry.credential:
        store = kwargs.get("credential_store")
        if store is not None:
            try:
                store.resolve(entry.credential)  # type: ignore[attr-defined]
            except Exception:
                logger.debug(
                    "acp_agent entry %r credential %r not resolvable; ignoring",
                    entry.name,
                    entry.credential,
                )

    dialect_value = options.get("dialect")
    dialect: str | None = str(dialect_value) if dialect_value else None

    return AcpAgentProvider(
        command=command,
        cwd=cwd,
        env=env,
        model=model,
        agent_name=agent_name,
        capability_flags=capability_flags,
        session_key=session_key,
        channel_id=channel_id,
        sandbox_mode=sandbox_mode,
        session_files_dir=session_files_dir,
        dialect=dialect,
        mode=mode,
        reasoning_effort=reasoning_effort,
    )


# ── Registration ─────────────────────────────────────────────────────────
#
# Register on import so ``import personalclaw.llm.acp_agent`` (and
# ``import personalclaw.llm``) wires the type into the default
# registry. The ``try``/``except`` makes the registration idempotent
# against module reload in tests; the registry itself remains strict and
# rejects duplicate types in normal use.
try:
    get_default_registry().register_type(ACP_AGENT_CAPABILITY, _factory)
except ProviderResolutionError:
    logger.debug("acp_agent provider type already registered with default registry")


# Also register on the AgentProvider runtime axis under the ``acp`` family, so
# the discovery/readiness layer (GET /api/agent-providers) can resolve any
# ``acp:<cli>`` runtime id to this class. Distinct from the ModelProvider
# ``register_type`` above — that wires the *factory* the bridge uses today;
# this wires the *class* the agent-runtime registry exposes for selection.
try:
    from personalclaw.agents.registry import register_agent_provider

    register_agent_provider("acp", AcpAgentProvider)
except Exception:  # pragma: no cover - defensive against import-order edge cases
    logger.debug("could not register acp AgentProvider family", exc_info=True)
