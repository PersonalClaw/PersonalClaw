"""Vendor-neutral ACP client — a thin N=1 wrapper over the shared concurrent machinery.

Implements the open `Agent Client Protocol
<https://github.com/zed-industries/agent-client-protocol>`__ (JSON-RPC 2.0 over
stdio). Callers supply a ``command`` (full launch argv) and the client speaks the
protocol against any ACP-compliant agent.

Architecture (P9 task #7 convergence): ``AcpClient`` is now a **convenience wrapper**
that holds exactly ONE :class:`AcpConnection` (one backend process + one
:class:`~personalclaw.acp.reader.FrameRouter` over its stdout) and ONE
:class:`AcpSession`, delegating every turn/lifecycle/config method to them. There is a
SINGLE stdout reader (the router) and a SINGLE turn loop (the session) — no inline
reader, no process-wide lock, no dual path. The N-session concurrent path
(:class:`~personalclaw.llm.acp_session_provider.AcpSessionProvider`) and this N=1 path
share the exact same machinery, so there is one implementation of the turn loop.

Protocol (ACP JSON-RPC 2.0):
  initialize → session/new → session/set_mode → session/set_model → session/prompt

Agent selection: the dialect's ``activate_agent_request`` (default: ``session/set_mode``
with ``modeId``) activates the agent config. MCP servers are passed in ``session/new``.

This module MUST stay vendor-neutral. It does not import backend-specific discovery
paths or session-storage layouts; callers that need such specifics own them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from collections import deque

    from personalclaw.acp.dialect import ACPDialect
    from personalclaw.acp.session import AcpConnection, AcpSession

from personalclaw.acp import translate
from personalclaw.acp.errors import (  # noqa: F401 — re-exported for existing importers
    AcpCommandsUnsupported,
    AcpError,
    AcpPermissionNeeded,
    AcpProcessDied,
    AcpTimeoutError,
)
from personalclaw.acp.transport import AcpProcess
from personalclaw.acp.types import (
    CAP_LOAD_SESSION,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    METHOD_COMPACTION_STATUS,
    METHOD_METADATA,
    AcpEvent,
    AcpPromptStats,
)

logger = logging.getLogger(__name__)

# Raw ACP JSON-RPC frame tracing — gated on PERSONALCLAW_ACP_TRACE=1 (DEV ONLY).
_ACP_TRACE = os.environ.get("PERSONALCLAW_ACP_TRACE") == "1"


def _acp_trace(direction: str, text: str) -> None:
    if _ACP_TRACE:
        logger.info("ACP-TRACE %s %s", direction, text[:600])


CLIENT_NAME = "personalclaw"
CLIENT_VERSION = "0.1.3"
PROTOCOL_VERSION = "2025-08-22"
DEFAULT_MODEL = "auto"

# Timeouts for session initialization steps
_INIT_TIMEOUT = 240.0  # 4 min — MCP servers can be slow to initialize
_DRAIN_DURATION = 10.0  # drain MCP server init notifications (~3s observed)
_DEFAULT_PROMPT_TIMEOUT = 7200.0  # 2 hours — allow very long tool execution
# After streaming content, if no new data arrives for this many seconds, the
# session treats the turn as done (handles agents finishing silently). Kept as a
# module constant so the oracle can monkeypatch it to keep the stale-EOF test fast.
_STALE_TURN_TIMEOUT = 90.0


# Process-tree helpers live in the shared transport; re-exported here because the
# acp client tests import them by name from this module.
from personalclaw.acp.transport import (  # noqa: E402,F401
    _direct_children,
    _get_child_pids,
    _get_start_time,
    _is_our_child,
    _kill_escaped_children,
    _resolve_ssh_auth_sock,
)


def _make_unified_diff(old: str, new: str, path: str, max_len: int = 6000) -> str:
    """Thin re-export of ``translate.make_unified_diff`` — kept at module scope
    because it's imported by name in the acp client tests."""
    return translate.make_unified_diff(old, new, path, max_len)


class AcpClient:
    """N=1 convenience wrapper over :class:`AcpConnection` + :class:`AcpSession`.

    Vendor-neutral: callers supply the launch ``command`` (full argv); the client
    speaks the open Agent Client Protocol against whatever agent that command starts.
    Holds ONE connection (process + router) and ONE session, delegating every turn to
    them — the same machinery the N-session concurrent path uses.
    """

    def __init__(
        self,
        work_dir: str | Path | None = None,
        model: str | None = None,
        agent: str = CLIENT_NAME,
        sandbox_mode: str = "auto",
        sandbox: str = "none",
        session_key: str | None = None,
        channel_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        command: list[str] | None = None,
        session_files_dir: Path | None = None,
        dialect: "ACPDialect | None" = None,
        mode: str | None = None,
        reasoning_effort: str | None = None,
        unattended: bool = False,
    ):
        from personalclaw.acp.dialect import DefaultDialect
        from personalclaw.config.loader import workspace_root

        self._dialect: ACPDialect = dialect or DefaultDialect()
        # The spawned CLI's cwd. An unbound session inherits the CONFIGURED workspace
        # root — ``workspace_root()``, the same definition ``default_workspace_dir()``
        # and ``resolve_agent_bindings`` use, so it honors PERSONALCLAW_WORKSPACE, the
        # saved ``<home>/workspace_dir`` file and PERSONALCLAW_HOME. The literal this
        # replaced (``Path.home()/".personalclaw"/"workspace"``) read the OPERATOR'S
        # REAL HOME regardless of every isolation mechanism in the repo, so a test
        # fixture, a dev home or a PERSONALCLAW_HOME-isolated gateway that failed to
        # bind a per-session cwd spawned its CLI in the real home and wrote there.
        #
        # Held on the transport, NOT here: ``_work_dir`` below is a property over
        # ``self._transport._work_dir``, because the transport is what puts ``cwd=`` on
        # the spawn. Two copies meant a post-construction rebind (``set_workspace`` →
        # ``client._work_dir = …``) moved this view and left the process spawning in
        # the ORIGINAL directory.
        resolved_work_dir = Path(work_dir) if work_dir else workspace_root()
        self._model = model or DEFAULT_MODEL
        self._agent = agent
        # Unattended run (§2.3 gap 3): set BEFORE the mode clamp below, which reads it.
        # This is the single input that widens the mode gate, so it defaults False —
        # an interactive session, or any caller that forgets to say otherwise, keeps
        # AAP-5's clamp. It is paired with host-side fail-fast in chat_runner: the two
        # ship together on purpose, because forwarding ``bypassPermissions`` to a CLI
        # whose prompts could still park on a human is the wedge, not a fix for it.
        self._unattended: bool = bool(unattended)
        # The host is the permission authority (§2.2): never hand the CLI a mode
        # that lets it self-approve, and assert the restrictive mode positively
        # instead of inheriting "whatever the CLI defaults to". Clamped HERE — the
        # one chokepoint every mode path crosses (factory kwarg, bundle entry
        # option, per-session override, loop/planning worker) — so no caller can
        # forget it. An unattended session is §2.3's explicit exception and is the
        # ONLY way through.
        self._mode: str = self._authority_mode(mode)
        self._reasoning_effort: str = reasoning_effort or ""
        self._sandbox_mode = sandbox_mode
        self._sandbox = sandbox or "none"
        self._session_key = session_key
        self._channel_id = channel_id
        self._extra_env = extra_env or {}
        self._command: list[str] | None = list(command) if command else None
        self._session_files_dir: Path | None = (
            Path(session_files_dir) if session_files_dir else None
        )
        # The ACP subprocess + its stdio/PID-tree/liveness live in the shared
        # AcpProcess transport. The connection owns the router over its stdout; the
        # session owns the turn loop. We hold both (N=1) + keep the transport handle
        # so the leaky ``_pid`` proxy (read by session.py / session_pid.py) works.
        self._transport = AcpProcess(
            command=self._command or [],
            work_dir=resolved_work_dir,
            sandbox_mode=self._sandbox_mode,
            sandbox=self._sandbox,
            extra_env=self._extra_env or None,
            session_key=self._session_key,
            channel_id=self._channel_id,
        )
        self._connection: "AcpConnection | None" = None
        self._session: "AcpSession | None" = None
        self._session_id: str | None = None
        self._resume_session_id: str | None = None
        self._resumed = False
        self._can_load_session = False
        self._can_execute_commands = False
        self._agent_capabilities: dict[str, object] = {}
        self._session_new_snapshot: dict[str, object] = {}
        self.last_prompt_stats = AcpPromptStats()
        self._last_stop_reason: str = ""

    def _core_mcp_servers(self) -> list[dict[str, Any]]:
        """The ``mcpServers`` array every ``session/new``/``session/load`` sends.

        Prong A of ACP-AGENT-PARITY §2.1: without it a session sees only the CLI's
        own tools and none of knowledge / tasks / inbox / artifacts / workflows /
        subagents / notify. Rebuilt per call so a ``rekey()``-ed warm process
        carries the CURRENT session key into the server's env, not the key the
        process was first spawned with.
        """
        from personalclaw.acp.mcp_servers import core_mcp_servers

        return core_mcp_servers(session_key=self._session_key)

    # ── transport-state proxies ────────────────────────────────────────────────
    # The process + PID/child-PID + stderr + activity clock physically live on the
    # shared AcpProcess transport. These proxies keep external reach-throughs
    # (``client._pid`` in session.py / session_pid.py; ``_work_dir`` in acp_agent.py)
    # and the test suite reading/writing the same names.
    @property
    def _work_dir(self) -> Path:
        """The spawned CLI's cwd — SINGLE-SOURCED from the transport that spawns it."""
        return self._transport._work_dir

    @_work_dir.setter
    def _work_dir(self, value: "str | Path") -> None:
        self._transport._work_dir = Path(value)

    @property
    def _process(self) -> "asyncio.subprocess.Process | None":
        return self._transport.process

    @_process.setter
    def _process(self, value: "asyncio.subprocess.Process | None") -> None:
        self._transport._process = value

    @property
    def _pid(self) -> int | None:
        return self._transport.pid

    @_pid.setter
    def _pid(self, value: int | None) -> None:
        self._transport._pid = value

    @property
    def _last_activity(self) -> float:
        return self._transport.last_activity

    @_last_activity.setter
    def _last_activity(self, value: float) -> None:
        self._transport._last_activity = value

    @property
    def _child_pids(self) -> dict[int, int | None]:
        return self._transport._child_pids

    @_child_pids.setter
    def _child_pids(self, value: dict[int, int | None]) -> None:
        self._transport._child_pids = value

    @property
    def _sandbox_handle(self) -> object | None:
        return self._transport._sandbox_handle

    @_sandbox_handle.setter
    def _sandbox_handle(self, value: object | None) -> None:
        self._transport._sandbox_handle = value

    @property
    def _stderr_lines(self) -> "deque[str]":
        return self._transport._stderr_lines

    @property
    def _start_time(self) -> int | None:
        return self._transport._start_time

    @_start_time.setter
    def _start_time(self, value: int | None) -> None:
        self._transport._start_time = value

    # ── introspection ───────────────────────────────────────────────────────────
    @property
    def is_ready(self) -> bool:
        return self._process is not None and self._session_id is not None

    @property
    def session_snapshot(self) -> dict[str, object]:
        """The raw ``session/new`` response (modes / models / configOptions) from the
        live session, or ``{}`` before one is created. The connection pool reads this
        to serve agent discovery off a warmed connection."""
        return self._session_new_snapshot

    def is_process_alive(self) -> bool:
        """True if the underlying process exists and has not exited."""
        return self._transport.is_alive()

    @property
    def exit_code(self) -> int | None:
        """Return the process exit code, or None if still running / never started."""
        return self._transport.exit_code

    def is_responsive(self, stale_threshold: float = 600.0) -> bool:
        """True if process is alive AND has had I/O activity within threshold seconds."""
        return self._transport.is_responsive(stale_threshold)

    def touch_activity(self) -> None:
        """Refresh activity without I/O (used by long-running MCP tools like ``wait``)."""
        self._transport.touch()

    @property
    def resumed(self) -> bool:
        """True if the last session was restored via session/load."""
        return self._resumed

    def set_resume_session_id(self, sid: str) -> None:
        """Set an ACP session ID to restore via ``session/load`` on next ensure_ready()."""
        self._resume_session_id = sid

    def rekey(self, session_key: str, channel_id: str | None = None) -> None:
        """Re-key this client for a different session (used by warm pool)."""
        self._session_key = session_key
        self._channel_id = channel_id
        self._last_activity = time.monotonic()

    # ── live per-session reconfig (pool post-claim + per-turn) ──────────────────
    # The dialect's set_* requests are SESSION-SCOPED (carry sessionId); issued on
    # the connection's send path, mirroring AcpSessionProvider. Fire-and-forget but NOT
    # reply-blind: both Zed adapters DO answer every ``session/set_config_option``
    # (measured), so the correlated future is watched for a rejection instead of being
    # discarded. A dialect with no verb returns None → no-op.
    def _watch_dialect_reply(self, method: str, params: dict, rid, fut) -> None:
        """Surface an adapter's REJECTION of a session-config frame instead of
        dropping it on the floor.

        ``AcpConnection.send_request`` registers the pending future and returns
        WITHOUT awaiting it, and every ``session/set_*`` send site here discarded
        that future — so the adapter's answer was garbage-collected unread. The
        comment justifying it ("adapters usually send no response") is false:
        measured live, BOTH Zed adapters answer every ``session/set_config_option``.
        The consequence was the worst kind of silence — codex-acp answered
        ``-32602 Invalid params`` to §2.2's host-authority permission mode on every
        single session, kept its own default-allow mode, and nothing logged it, so
        the mode read as forwarded when it had been refused.

        A done-callback rather than an ``await``: the send stays fire-and-forget, so
        neither the handshake nor a per-turn reconfig pays latency for an adapter
        that answers slowly or not at all, and a rejection still becomes a warning.
        """

        def _on_done(f: "asyncio.Future") -> None:
            try:
                resp = f.result()
            except Exception:
                return  # cancelled / process gone — the send is best-effort by design
            err = getattr(resp, "error", None)
            if err:
                logger.warning(
                    "ACP adapter REJECTED %s (%s=%r) rid=%s: %r — the setting did NOT "
                    "apply and the session keeps the adapter's own value",
                    method,
                    params.get("configId") or "value",
                    params.get("value"),
                    rid,
                    err,
                )

        fut.add_done_callback(_on_done)

    async def _send_dialect_request(self, req) -> None:
        if req is not None and self._connection is not None:
            rid, fut = await self._connection.send_request(req.method, req.params)
            self._watch_dialect_reply(req.method, req.params, rid, fut)

    async def set_model(self, model_id: str) -> None:
        """Switch model on a running session (pass a sentinel default so the dialect's
        "skip when model==default" guard never suppresses an explicit switch)."""
        if not self._session_id:
            raise AcpError("Cannot set model before session is initialized")
        req = self._dialect.set_model_request(
            session_id=self._session_id,
            model=model_id,
            default_model="\x00",
        )
        await self._send_dialect_request(req)
        self._model = model_id

    async def set_agent(self, agent: str) -> None:
        """Switch the active agent/persona on a running session (pool post-claim)."""
        if not agent or agent == self._agent:
            return
        if not self._session_id:
            raise AcpError("Cannot set agent before session is initialized")
        req = self._dialect.activate_agent_request(session_id=self._session_id, agent=agent)
        await self._send_dialect_request(req)
        self._agent = agent

    def _authority_mode(self, mode: str | None) -> str:
        """Clamp a requested native mode so the HOST stays the permission authority.

        §2.2: an ``acceptEdits``/``dontAsk``/``bypassPermissions`` session makes the
        CLI its own authority, and everything it self-approves bypasses the
        deny-list, the task-mode gate and blocking PreToolUse hooks — they all hang
        off ``session/request_permission``. A downgrade is AUDITED, never silent, so
        a caller that thought it had an auto-approve session can see why it did not.

        §2.3: an UNATTENDED session is the one declared exception — it may keep an
        auto-approve mode, because the host pairs it with fail-fast permission
        handling so the run resolves deterministically instead of wedging. The grant
        is audited too (not just the clamp), so an auditor can see WHICH sessions ran
        with the CLI self-approving and why they were allowed to.
        """
        from personalclaw.acp.permission_authority import sanitize_mode

        decision = sanitize_mode(mode, unattended=self._unattended)
        if decision.reason and not decision.downgraded and decision.requested:
            # A widened (not clamped) decision: only the unattended path produces one.
            logger.info("ACP permission mode allowed unattended: %s", decision.reason)
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller="acp:permission_authority",
                    operation="mode_change:unattended_auto_approve",
                    outcome="allowed",
                    resources=(
                        f"session={getattr(self, '_session_key', None) or '-'} "
                        f"mode={decision.mode}"
                    ),
                )
            except Exception:
                logger.warning("SEL audit failed for unattended ACP mode", exc_info=True)
        if decision.downgraded:
            logger.warning("ACP permission mode clamped: %s", decision.reason)
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller="acp:permission_authority",
                    operation="mode_change:clamped_to_host_authority",
                    outcome="downgraded",
                    resources=(
                        f"session={getattr(self, '_session_key', None) or '-'} "
                        f"requested={decision.requested} effective={decision.mode}"
                    ),
                )
            except Exception:
                logger.warning("SEL audit failed for ACP mode clamp", exc_info=True)
        return decision.mode

    async def set_mode(self, mode: str) -> None:
        """Switch the permission/operating mode on a running session. MUST be issued
        after :meth:`set_model` (adapters clamp modes to the active model)."""
        mode = self._authority_mode(mode)
        if not mode or mode == self._mode:
            return
        if not self._session_id:
            raise AcpError("Cannot set mode before session is initialized")
        req = self._dialect.set_mode_request(session_id=self._session_id, mode=mode)
        await self._send_dialect_request(req)
        self._mode = mode

    async def set_effort(self, effort: str) -> None:
        """Set the per-turn reasoning effort on a running session. MUST follow
        :meth:`set_model` (effort granularity can be model-dependent)."""
        if effort == self._reasoning_effort:
            return
        if not self._session_id:
            raise AcpError("Cannot set effort before session is initialized")
        req = self._dialect.set_effort_request(session_id=self._session_id, effort=effort)
        await self._send_dialect_request(req)
        self._reasoning_effort = effort

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def ensure_ready(self) -> None:
        """Ensure the connection is spawned + handshaken and the N=1 session is open.

        Retries once — agents typically have slow first launch (MCP server init), and
        transient failures (MCP crash, bad config read) are recoverable.
        """
        self._work_dir.mkdir(parents=True, exist_ok=True)
        if self._connection is not None and self._transport.is_alive() and self._session_id:
            return

        for attempt in range(2):
            try:
                if (
                    self._transport.process is not None
                    and self._transport.process.returncode is not None
                ):
                    await self._reset()
                if self._connection is None:
                    await self._open_connection()
                await self._initialize_session()
                try:
                    await self._transport.snapshot_process_tree()
                except Exception:
                    logger.warning("Failed to snapshot process tree", exc_info=True)
                return
            except (AcpTimeoutError, AcpError) as exc:
                if attempt == 0:
                    logger.warning("ACP init failed (%s), retrying with fresh process...", exc)
                await self._teardown()
                if attempt == 1:
                    raise

    async def _open_connection(self) -> None:
        """Spawn the process (via the shared transport) + start the FrameRouter over its
        stdout, holding the resulting :class:`AcpConnection`. One reader, one process."""
        from personalclaw.acp.reader import FrameRouter
        from personalclaw.acp.session import AcpConnection

        await self._transport.spawn()
        router = FrameRouter(self._transport.readline)
        router.start()
        self._connection = AcpConnection(
            None, router, dialect=self._dialect, transport=self._transport
        )

    async def _initialize_session(self) -> None:
        """Handshake on the connection: initialize → session/load or session/new →
        activate agent → set model → set mode → set effort → drain MCP init."""
        assert self._connection is not None
        conn = self._connection

        # 1. Initialize — protocolVersion + clientInfo shape are dialect-owned.
        await conn.initialize(
            {
                "protocolVersion": self._dialect.protocol_version(),
                "clientInfo": self._dialect.client_info(
                    client_name=CLIENT_NAME, client_version=CLIENT_VERSION
                ),
            },
            timeout=_INIT_TIMEOUT,
        )
        self._agent_capabilities = dict(conn.agent_capabilities or {})
        logger.info("ACP initialized (protocol=%s)", self._dialect.protocol_version())
        self._can_load_session = bool(self._agent_capabilities.get(CAP_LOAD_SESSION, False))
        # Same shape, same handshake, same allowlist direction — see
        # ``AcpConnection.supports_native_commands`` for why absence means "don't send".
        self._can_execute_commands = conn.supports_native_commands

        # 2. Try session/load if we have a resume ID and the agent supports it.
        self._resumed = False
        resume_sid = self._resume_session_id
        self._resume_session_id = None  # consume — no retry loop
        self._session = None
        self._session_id = None

        if resume_sid and self._can_load_session:
            session_file = (
                str(self._session_files_dir / f"{resume_sid}.json")
                if self._session_files_dir is not None
                else None
            )
            if session_file is not None and Path(session_file).exists():
                try:
                    self._session = await conn.load_session(
                        {
                            "sessionId": resume_sid,
                            "cwd": str(self._work_dir),
                            "mcpServers": self._core_mcp_servers(),
                            "_meta": {"_vendor.dev/session_file": session_file},
                        },
                        session_id=resume_sid,
                        timeout=_INIT_TIMEOUT,
                        session_files_dir=self._session_files_dir,
                    )
                    if self._session is not None:
                        self._session_id = resume_sid
                        self._resumed = True
                        logger.info("ACP session resumed: %s", resume_sid)
                except (AcpError, AcpTimeoutError):
                    logger.info(
                        "session/load failed for %s, falling back to session/new", resume_sid
                    )
            else:
                logger.info("Session file missing for %s, skipping load", resume_sid)

        # 3. Create a new session if load didn't succeed.
        if not self._session_id:
            self._session = await conn.new_session(
                {"cwd": str(self._work_dir), "mcpServers": self._core_mcp_servers()},
                timeout=_INIT_TIMEOUT,
                session_files_dir=self._session_files_dir,
            )
            self._session_id = self._session.session_id
            self._session_new_snapshot = dict(conn.last_session_new_snapshot or {})
            logger.info("ACP session created: %s", self._session_id)
        self._last_activity = time.monotonic()

        # 4. Activate agent — dialect decides the message (or None if activated via argv).
        _activate = self._dialect.activate_agent_request(
            session_id=self._session_id, agent=self._agent
        )
        if _activate is not None:
            _rid, _fut = await conn.send_request(_activate.method, _activate.params)
            self._watch_dialect_reply(_activate.method, _activate.params, _rid, _fut)
            logger.info("ACP agent activated: %s", self._agent)

        # 5. Set model — dialect decides (None = agent default / no verb).
        _set_model = self._dialect.set_model_request(
            session_id=self._session_id, model=self._model, default_model=DEFAULT_MODEL
        )
        if _set_model is not None:
            _rid, _fut = await conn.send_request(_set_model.method, _set_model.params)
            self._watch_dialect_reply(_set_model.method, _set_model.params, _rid, _fut)
            logger.info("ACP model: %s", self._model)
        else:
            logger.info("ACP model: %s (from agent config)", self._model or "auto")

        # 6. Set permission/operating mode — MUST follow model (adapters clamp modes).
        # The dialect translates the host-canonical mode into the adapter's OWN
        # vocabulary (``ZedAdapterDialect.native_mode``); an adapter with no equivalent
        # gets no frame rather than one it answers ``-32602`` to. The reply is watched,
        # because a refused mode leaves the CLI its own permission authority and that
        # must never be silent again (§2.2).
        _set_mode = self._dialect.set_mode_request(session_id=self._session_id, mode=self._mode)
        if _set_mode is not None:
            _rid, _fut = await conn.send_request(_set_mode.method, _set_mode.params)
            self._watch_dialect_reply(_set_mode.method, _set_mode.params, _rid, _fut)
            logger.info("ACP mode: %s (native %r)", self._mode, _set_mode.params.get("value"))
        else:
            logger.info("ACP mode: %s — no native mode axis on this adapter", self._mode)

        # 7. Set reasoning effort — MUST follow model (granularity can be model-dependent).
        _set_effort = self._dialect.set_effort_request(
            session_id=self._session_id, effort=self._reasoning_effort
        )
        if _set_effort is not None:
            _rid, _fut = await conn.send_request(_set_effort.method, _set_effort.params)
            self._watch_dialect_reply(_set_effort.method, _set_effort.params, _rid, _fut)
            logger.info("ACP effort: %s", self._reasoning_effort)

        # Drain MCP server init notifications (best-effort, bounded).
        await conn.drain_init_notifications(duration=_DRAIN_DURATION)

    async def start_fresh_turn_session(self) -> None:
        """Begin a NEW agent session on the EXISTING process (no respawn).

        Some ACP agents (claude-code) treat a session as finished after their first
        turn ends and no-op subsequent prompts on it. A long-lived driver that sends one
        prompt per cycle calls this between cycles to get a clean session the agent will
        fully service. No-op if the process isn't alive (next ensure_ready() spawns)."""
        if not (self._connection is not None and self._transport.is_alive()):
            return
        if self._session is not None and self._session_id is not None:
            await self._connection.close_session(self._session_id)
        self._session = None
        self._session_id = None
        self._resume_session_id = None  # force session/new, not load
        # Re-run the handshake tail: session/new + activate/model/mode/effort + drain.
        # initialize() is one-per-process and already done, so re-open just the session.
        conn = self._connection
        self._session = await conn.new_session(
            {"cwd": str(self._work_dir), "mcpServers": self._core_mcp_servers()},
            timeout=_INIT_TIMEOUT,
            session_files_dir=self._session_files_dir,
        )
        self._session_id = self._session.session_id
        self._session_new_snapshot = dict(conn.last_session_new_snapshot or {})
        _activate = self._dialect.activate_agent_request(
            session_id=self._session_id, agent=self._agent
        )
        if _activate is not None:
            _rid, _fut = await conn.send_request(_activate.method, _activate.params)
            self._watch_dialect_reply(_activate.method, _activate.params, _rid, _fut)
        _set_model = self._dialect.set_model_request(
            session_id=self._session_id, model=self._model, default_model=DEFAULT_MODEL
        )
        if _set_model is not None:
            _rid, _fut = await conn.send_request(_set_model.method, _set_model.params)
            self._watch_dialect_reply(_set_model.method, _set_model.params, _rid, _fut)
        _set_mode = self._dialect.set_mode_request(session_id=self._session_id, mode=self._mode)
        if _set_mode is not None:
            _rid, _fut = await conn.send_request(_set_mode.method, _set_mode.params)
            self._watch_dialect_reply(_set_mode.method, _set_mode.params, _rid, _fut)
        # 7 + drain — this method's own docstring promised "activate/model/mode/effort +
        # drain" and ran only the first three (`G20`). Two consequences, both silent:
        #   * a driver that reopens a session per cycle (``gateway.py``, one prompt per
        #     cycle) lost the session's EFFORT pin from turn 2 on, while the agent/model/mode
        #     it re-sent kept the session looking correctly specialized;
        #   * MCP init notifications stayed queued and interleaved into the turn instead of
        #     being drained before it, on the very path `AAP-4` exists to keep core reachable.
        # Effort follows model here for the same reason it does in the full handshake:
        # granularity can be model-dependent.
        _set_effort = self._dialect.set_effort_request(
            session_id=self._session_id, effort=self._reasoning_effort
        )
        if _set_effort is not None:
            _rid, _fut = await conn.send_request(_set_effort.method, _set_effort.params)
            self._watch_dialect_reply(_set_effort.method, _set_effort.params, _rid, _fut)
        await conn.drain_init_notifications(duration=_DRAIN_DURATION)
        logger.info("ACP fresh turn session: %s", self._session_id)

    async def _reset(self) -> None:
        """Reset session + connection state after the process is dead."""
        await self._teardown()

    async def _teardown(self) -> None:
        """Close the connection (router + sessions) and kill the process tree."""
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                logger.debug("connection close failed during teardown", exc_info=True)
        self._connection = None
        self._session = None
        self._session_id = None
        self._session_new_snapshot = {}
        self._resumed = False
        self._last_stop_reason = ""

    async def shutdown(self) -> None:
        """Gracefully stop the ACP process (closes the connection + reaps the tree)."""
        await self._teardown()

    # ── the turn (delegates to the single session) ──────────────────────────────
    async def stream_events(
        self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        """Send a prompt and yield AcpEvent objects. Delegates to the session's turn
        loop (its per-session lock keeps one prompt in flight); telemetry is stamped on
        the terminal complete event exactly as before."""
        await self.ensure_ready()
        assert self._session is not None
        async for event in self._session.stream_events(message, timeout=timeout):
            self._stamp_turn_telemetry(event)
            self.last_prompt_stats = self._session.last_prompt_stats
            self._last_stop_reason = self._session._last_stop_reason
            yield event

    @property
    def supports_native_commands(self) -> bool:
        """Whether the spawned agent advertised the slash-command extension. False before
        the handshake — a caller must not send a command to a process that hasn't spoken
        yet."""
        return self._can_execute_commands

    async def stream_command(
        self, command: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        """Execute a slash command (e.g. '/compact', '/usage') and yield streaming
        AcpEvents. Delegates to the session's ``stream_command``.

        Gated on the declared capability: an agent that never advertised the extension
        gets no request at all, and the caller sees :class:`AcpCommandsUnsupported`
        BEFORE any wire write — so it can substitute a plain prompt knowing the turn is
        still untouched (`G4`)."""
        await self.ensure_ready()
        if not self._can_execute_commands:
            raise AcpCommandsUnsupported(command)
        assert self._session is not None
        async for event in self._session.stream_command(command, timeout=timeout):
            self._stamp_turn_telemetry(event)
            self.last_prompt_stats = self._session.last_prompt_stats
            self._last_stop_reason = self._session._last_stop_reason
            yield event

    async def send_message(self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT) -> str:
        """Send a prompt and return the full response text (thinking excluded).

        Reimplemented on the shared frame stream: drain ``stream_events`` and
        concatenate the non-thinking text chunks (the old ``_read_prompt_response``
        did the same over the inline loop). An ``error`` terminal frame raises inside
        the session loop, propagating here — same contract as before.
        """
        parts: list[str] = []
        async for event in self.stream_events(message, timeout=timeout):
            if event.kind == EVENT_TEXT_CHUNK and event.text:
                parts.append(event.text)
            # EVENT_THINKING_CHUNK is intentionally excluded from the answer string.
        return "".join(parts)

    async def send_command(self, command: str) -> str:
        """Execute an ACP slash command and return the response text (if any).

        For streaming output use :meth:`stream_command`. Drains the command's event
        stream into a string (the terminal result is formatted into a text chunk by the
        session's ``extract_agent_from_result`` path).

        Returns "" when the agent never advertised commands: this is the fire-and-forget
        surface (``compact()`` drives it), and ``ModelProvider.compact`` declares itself a
        no-op for providers without native support. A caller that must DISTINGUISH "no
        output" from "cannot" reads :attr:`supports_native_commands` or drives
        :meth:`stream_command`, which raises."""
        parts: list[str] = []
        try:
            async for event in self.stream_command(command, timeout=60.0):
                if event.kind == EVENT_TEXT_CHUNK and event.text:
                    parts.append(event.text)
        except AcpTimeoutError:
            logger.debug("Command '%s' response timed out (may still be running)", command)
        except AcpCommandsUnsupported:
            logger.info("Command '%s' skipped — this agent declares no command support", command)
        return "".join(parts)

    def _stamp_turn_telemetry(self, event: AcpEvent) -> None:
        """Surface this turn's telemetry on its terminal complete event (mirrors the
        native loop — provider-agnostic ``event_count``/``tool_call_count`` fields)."""
        if event.kind == EVENT_COMPLETE and self._session is not None:
            stats = self._session.last_prompt_stats
            event.event_count = stats.event_count
            event.tool_call_count = len(stats.tool_calls)

    # ── permissions (delegate to the session) ───────────────────────────────────
    async def approve_tool(self, request_id: str | int, option_id: str | None = None) -> None:
        """Approve a pending tool permission (resolves the agent-offered option id)."""
        if self._session is None:
            raise AcpError("Cannot approve tool before session is initialized")
        await self._session.approve_tool(request_id, option_id)

    async def reject_tool(self, request_id: str | int) -> None:
        if self._session is None:
            raise AcpError("Cannot reject tool before session is initialized")
        await self._session.reject_tool(request_id)

    # ── control (delegate to the session) ───────────────────────────────────────
    async def cancel_session(self) -> None:
        """Cancel the current in-flight operation via ACP session/cancel (scoped to
        this session). The ack arrives as stopReason:"cancelled" on the prompt response."""
        if self._session is None:
            logger.debug("cancel_session: no session, skip")
            return
        await self._session.cancel()

    async def wait_turn_done(self, timeout: float) -> str:
        """Wait for the current prompt to finish. Returns stop_reason or raises TimeoutError."""
        if self._session is None:
            return ""
        return await self._session.wait_turn_done(timeout)

    def has_active_turn(self) -> bool:
        """True if a prompt is in flight AND has not been cancelled."""
        return self._session is not None and self._session.has_active_turn()

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        """Wait for a compaction status frame on this session's queue. Returns the
        status dict (``{type, summary}``) or ``{type: "timeout"}``."""
        if self._connection is None or self._session_id is None:
            return {"type": "timeout"}
        return await self._connection.wait_for_session_frame(
            self._session_id,
            method=METHOD_COMPACTION_STATUS,
            terminal_types=("completed", "failed"),
            timeout=timeout,
            also_track=(METHOD_METADATA,),
        )
