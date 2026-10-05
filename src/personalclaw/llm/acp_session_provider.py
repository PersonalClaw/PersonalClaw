"""Session-backed ACP provider — one :class:`AcpSession` on a (possibly shared)
:class:`AcpConnection`, exposed through the :class:`AgentProvider` surface.

This is the concurrent path's provider: where :class:`AcpAgentProvider` wraps one
:class:`AcpClient` (one process, one inline-reader turn loop), an ``AcpSessionProvider``
wraps one demux-routed :class:`AcpSession`, so N of them can share ONE backend process
via a single :class:`AcpConnection` + :class:`FrameRouter`. It is ADDITIVE: gated behind
``dialect.supports_concurrent_sessions`` AND the ``acp_concurrent_sessions`` runtime flag,
and the one-session ``AcpAgentProvider`` stays authoritative until both are on.

The ACP→neutral event translation is the SAME ``acp/adapter.py`` the client-backed
provider uses — an :class:`AcpSession` yields the identical :class:`AcpEvent` shape, so
there is no second translation path.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.acp.errors import AcpError
from personalclaw.acp.outcomes import AcpToolOutcomesMixin
from personalclaw.acp.spend import AcpTurnMeter
from personalclaw.acp.types import STOP_REASON_CANCELLED, STOP_REASON_END_TURN
from personalclaw.agents.provider import AgentProvider
from personalclaw.llm.base import CancelOutcome, LLMEvent
from personalclaw.turn_streams import closing_stream

if TYPE_CHECKING:
    from personalclaw.acp.session import AcpConnection, AcpSession

logger = logging.getLogger(__name__)


class AcpSessionProvider(AcpToolOutcomesMixin, AcpTurnMeter, AgentProvider):
    """One ACP session (on a shared connection) behind the AgentProvider surface.

    Construct with a live :class:`AcpConnection` and the :class:`AcpSession` opened on
    it. The connection owns the process + handshake + live ``set_*`` reconfig; the
    session owns the per-session turn loop (its own turn lock — co-tenant sessions never
    block each other)."""

    supports_tools = True

    def __init__(
        self,
        connection: "AcpConnection",
        session: "AcpSession",
        *,
        runtime_id: str,
        model: str = "",
        agent_name: str = "",
        self_approval: bool = False,
        compacts_itself: bool = False,
    ) -> None:
        self._conn = connection
        self._session = session
        self._runtime_id = runtime_id
        self._model = model
        self._agent_name = agent_name
        # The same contract as ``AcpClient._self_approval``, on the POOLED door. Defaults False
        # so a pooled session keeps the ``sanitize_mode`` clamp; only work whose owner let its
        # CLI approve its own calls may keep an auto-approve mode.
        self._self_approval = bool(self_approval)
        # What the runtime's app declared about its CLI (``acp_agent.options_compacts_itself``),
        # answered here as the N=1 provider answers it.
        self._compacts_itself = bool(compacts_itself)

    # ── identity ────────────────────────────────────────────────────────────────
    @property
    def provider_id(self) -> str:
        return self._runtime_id

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def agent_model(self) -> str:
        return self._model

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def pid(self) -> int | None:
        t = getattr(self._conn, "_transport", None)
        return t.pid if t is not None else None

    @property
    def declared_capabilities(self) -> frozenset[str]:
        caps = self._conn.agent_capabilities or {}
        names: set[str] = set()
        if isinstance(caps, dict):
            for key, value in caps.items():
                if not isinstance(value, bool) or value:
                    names.add(str(key))
        return frozenset(names)

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def start(self) -> None:
        """The connection is spawned + handshaken and the session opened by the pool
        before construction, so there is nothing to start here."""
        return None

    async def shutdown(self) -> None:
        """Close THIS session. The pool owns the connection's lifetime (a connection
        shared by co-tenants is torn down only when its last session closes)."""
        try:
            await self._conn.close_session(self._session.session_id)
        except Exception:
            logger.debug("AcpSessionProvider.shutdown: close_session failed", exc_info=True)

    # ── the turn (same acp/adapter.py translation as the client-backed provider) ──
    @staticmethod
    def _to_llm_event(e: Any) -> LLMEvent:
        from personalclaw.acp.adapter import acp_event_to_agent_event

        return acp_event_to_agent_event(e)

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        # Procedural-memory signal — identical to the N=1 client-backed provider:
        # clean accumulator per turn, every event folded in, and the turn metered on a
        # metered axis. See acp/outcomes.py and acp/spend.py.
        self._outcome_accumulator.begin_turn()
        turn = self._events(self._session.stream_events(message))
        async with closing_stream(self._metered(turn, prompt=message)) as events:
            async for event in events:
                self._outcome_accumulator.observe(event)
                yield event

    async def _events(self, frames: AsyncIterator[Any]) -> AsyncIterator[LLMEvent]:
        async with closing_stream(frames) as turn:
            async for e in turn:
                self._stamp_turn_telemetry(e)
                yield self._to_llm_event(e)

    @property
    def supports_native_commands(self) -> bool:
        """The connection's declared flag — the SAME derivation the N=1 client reads, so a
        co-tenant session on one process cannot disagree with it."""
        return bool(self._conn.supports_native_commands)

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        """Gated exactly like the N=1 client: no advertisement, no request."""
        if not self._conn.supports_native_commands:
            from personalclaw.acp.errors import AcpCommandsUnsupported

            raise AcpCommandsUnsupported(command)
        self._outcome_accumulator.begin_turn()
        turn = self._events(self._session.stream_command(command))
        async with closing_stream(self._metered(turn, prompt=command)) as events:
            async for event in events:
                self._outcome_accumulator.observe(event)
                yield event

    def _stamp_turn_telemetry(self, event: Any) -> None:
        from personalclaw.acp.types import EVENT_COMPLETE

        if event.kind == EVENT_COMPLETE:
            stats = self._session.last_prompt_stats
            event.event_count = stats.event_count
            event.tool_call_count = len(stats.tool_calls)

    # ── permissions ─────────────────────────────────────────────────────────────
    async def approve_tool(self, request_id: str | int) -> None:
        await self._session.approve_tool(request_id)

    async def reject_tool(self, request_id: str | int) -> None:
        await self._session.reject_tool(request_id)

    def refusal_answer(self, request_id: str | int) -> dict[str, str] | None:
        """The agent's option a Deny of *request_id* was answered with (see AcpSession)."""
        return self._session.refusal_answer(request_id)

    def deny_outcome(self, request_id: str | int) -> str:
        """What a Deny of the pending *request_id* would do (see AcpSession)."""
        return self._session.deny_outcome(request_id)

    # ── mid-turn steering ───────────────────────────────────────────────────────
    # The pooled provider gets the seam for the same reason it gets every other session
    # method: it wraps the SAME AcpSession the N=1 client does, so leaving it out would make
    # steering silently depend on which ACP path a session happened to open on.
    def steer_capable(self) -> bool:
        return self._session.steer_capable()

    def set_steer_source(self, pull: "Callable[[], list[str]] | None") -> bool:
        return self._session.set_steer_source(pull)

    def undelivered_steers(self) -> list[str]:
        return self._session.undelivered_steers()

    # ── the agent's questions to the user ───────────────────────────────────────────
    @property
    def asks_through_elicitation(self) -> bool:
        """Never on this door: the shared connection's ``initialize`` advertises no form
        elicitation, so its agent keeps its question tool to itself."""
        return False

    def set_question_handler(self, handler: "Callable[[dict], Awaitable[dict]] | None") -> None:
        self._session.set_question_handler(handler)

    # ── status / control ────────────────────────────────────────────────────────
    def context_usage_pct(self) -> float | None:
        return self._session.context_usage_pct()

    @property
    def compacts_automatically(self) -> bool:
        """The app's declaration, the SAME answer the N=1 provider gives
        (``AcpAgentProvider.compacts_automatically``): a session on a shared connection is
        left alone at the threshold exactly when one on its own process would be."""
        return self._compacts_itself

    def is_alive(self) -> bool:
        return self._conn.is_process_alive()

    def is_process_alive(self) -> bool:
        return self._conn.is_process_alive()

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        if not self._session.has_active_turn():
            return "no_turn"
        try:
            await self._session.cancel()
        except AcpError:
            logger.debug("AcpSessionProvider.cancel: cancel raised AcpError", exc_info=True)
            return "error"
        if wait_ack_timeout <= 0:
            return "acked"
        try:
            reason = await self._session.wait_turn_done(timeout=wait_ack_timeout)
            if reason in (STOP_REASON_CANCELLED, STOP_REASON_END_TURN):
                return "acked"
            return "timeout"
        except (TimeoutError, Exception):
            return "timeout"

    def set_workspace(self, path: Path) -> None:
        return None  # the session's cwd was fixed at connection spawn

    # ── live per-session reconfig (post-open specialization) ────────────────────
    # The default dialect's set_* requests are SESSION-SCOPED (carry sessionId), so
    # applying them on a shared connection affects ONLY this session — safe for
    # co-tenants. Fire-and-forget: the reply is never awaited, and is read when it settles so
    # a connection closing before it arrives is not an exception nobody retrieved. The
    # connection owns the dialect + the send path.
    async def _send_dialect_request(self, req) -> None:
        if req is not None:
            from personalclaw.acp.session import read_when_settled

            _rid, fut = await self._conn.send_request(req.method, req.params)
            fut.add_done_callback(read_when_settled)

    async def set_agent(self, agent: str) -> None:
        if not agent or agent == self._agent_name:
            return
        req = self._conn._dialect.activate_agent_request(
            session_id=self._session.session_id, agent=agent
        )
        await self._send_dialect_request(req)
        self._agent_name = agent

    async def set_model(self, model: str) -> None:
        if not model:
            return
        req = self._conn._dialect.set_model_request(
            session_id=self._session.session_id, model=model, default_model="\x00"
        )
        await self._send_dialect_request(req)
        self._model = model

    async def set_mode(self, mode: str) -> None:
        # The POOLED path is a second door onto the same adapter, and it builds the
        # dialect request itself instead of going through AcpClient — so it needs the
        # host-authority clamp too. Without it, a caller that hands the pool
        # ``bypassPermissions`` would make the CLI its own permission authority on the
        # very sessions AcpClient refuses to.
        from personalclaw.acp.permission_authority import sanitize_mode

        decision = sanitize_mode(mode, self_approval=self._self_approval)
        if decision.reason and not decision.downgraded and decision.requested:
            # Allowed, not clamped (only the owner's choice is): written down as the AcpClient
            # door writes it, so the audit shows every session whose CLI approved its own calls,
            # whichever door opened it.
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller="acp:permission_authority",
                    operation="mode_change:self_approval_allowed",
                    outcome="allowed",
                    resources=f"pooled mode={decision.mode}",
                )
            except Exception:
                logger.warning("SEL audit failed for a pooled self-approving mode", exc_info=True)
        if decision.downgraded:
            logger.warning("ACP pooled permission mode clamped: %s", decision.reason)
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller="acp:permission_authority",
                    operation="mode_change:clamped_to_host_authority",
                    outcome="downgraded",
                    resources=f"pooled requested={decision.requested} "
                    f"effective={decision.mode}",
                )
            except Exception:
                logger.warning("SEL audit failed for pooled ACP mode clamp", exc_info=True)
        mode = decision.mode
        if not mode:
            return
        req = self._conn._dialect.set_mode_request(session_id=self._session.session_id, mode=mode)
        await self._send_dialect_request(req)

    async def set_reasoning_effort(self, effort: str) -> None:
        if not effort:
            return
        req = self._conn._dialect.set_effort_request(
            session_id=self._session.session_id, effort=effort
        )
        await self._send_dialect_request(req)

    def set_self_approval(self, allowed: bool) -> None:
        """Say BEFORE :meth:`set_mode` whether the owner let this work's CLI approve its own calls.

        A pooled connection is opened generic, so a session claimed for work whose owner allowed
        that must say so here or its self-approving mode is clamped back to the host-authority
        one on the next line. Ordering is load-bearing: ``set_mode`` reads this flag."""
        self._self_approval = bool(allowed)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        return None  # the session key was bound at connection spawn (env)


# ── double-gate + concurrent-session opener ──────────────────────────────────


def concurrent_sessions_enabled(dialect_id: str | None) -> bool:
    """True only when BOTH gates are on: the backend dialect declares
    ``supports_concurrent_sessions`` (proven-concurrent — currently the default dialect) AND
    the ``acp_concurrent_sessions`` runtime flag is set. Either off → the one-session
    AcpClient path stays authoritative (a true no-op until deliberately enabled)."""
    from personalclaw.acp.dialect import get_dialect
    from personalclaw.config import AppConfig

    try:
        if not get_dialect(dialect_id).supports_concurrent_sessions:
            return False
        return bool(AppConfig.load().agent.acp_concurrent_sessions)
    except Exception:
        logger.debug(
            "concurrent_sessions_enabled: gate check failed — treating as OFF", exc_info=True
        )
        return False


async def open_acp_session_provider(
    connection: "AcpConnection",
    *,
    runtime_id: str,
    cwd: Path,
    session_files_dir: Path | None = None,
    model: str = "",
    agent_name: str = "",
    session_key: str | None = "",
    mcp_servers: list | None = None,
    compacts_itself: bool = False,
) -> "AcpSessionProvider":
    """Open a new session on an already-live (spawned + ``initialize``-d) connection and
    wrap it in an :class:`AcpSessionProvider`. Multiple calls on the same connection =
    concurrent sessions on one process. The caller (pool) owns spawning the
    connection + its lifetime.

    ``mcp_servers`` defaults to the ``personalclaw-core`` server
    rather than to nothing: this parameter existed with no supplier, so the
    concurrent path opened every session with an empty ``mcpServers`` exactly like the
    one-session path did. Pass ``[]`` to open a session with no MCP servers at all.
    """
    from personalclaw.acp.mcp_servers import core_mcp_servers

    servers = mcp_servers if mcp_servers is not None else core_mcp_servers(session_key=session_key)
    session = await connection.new_session(
        {"cwd": str(cwd), "mcpServers": servers},
        session_files_dir=session_files_dir,
    )
    return AcpSessionProvider(
        connection,
        session,
        runtime_id=runtime_id,
        model=model,
        agent_name=agent_name,
        compacts_itself=compacts_itself,
    )
