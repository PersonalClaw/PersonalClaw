"""Session-backed ACP provider — one :class:`AcpSession` on a (possibly shared)
:class:`AcpConnection`, exposed through the :class:`AgentProvider` surface.

This is the concurrent path's provider (P9): where :class:`AcpAgentProvider` wraps one
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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.acp.errors import AcpError
from personalclaw.acp.types import STOP_REASON_CANCELLED, STOP_REASON_END_TURN
from personalclaw.agents.provider import AgentProvider
from personalclaw.llm.base import CancelOutcome, LLMEvent

if TYPE_CHECKING:
    from personalclaw.acp.session import AcpConnection, AcpSession

logger = logging.getLogger(__name__)


class AcpSessionProvider(AgentProvider):
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
    ) -> None:
        self._conn = connection
        self._session = session
        self._runtime_id = runtime_id
        self._model = model
        self._agent_name = agent_name

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
        async for e in self._session.stream_events(message):
            self._stamp_turn_telemetry(e)
            yield self._to_llm_event(e)

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        async for e in self._session.stream_command(command):
            self._stamp_turn_telemetry(e)
            yield self._to_llm_event(e)

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

    # ── status / control ────────────────────────────────────────────────────────
    def context_usage_pct(self) -> float:
        return self._session.context_usage_pct()

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
    # co-tenants. Fire-and-forget (adapters usually send no response), mirroring the
    # client. The connection owns the dialect + the send path.
    async def _send_dialect_request(self, req) -> None:
        if req is not None:
            await self._conn.send_request(req.method, req.params)

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
        logger.debug("concurrent_sessions_enabled: gate check failed — treating as OFF", exc_info=True)
        return False


async def open_acp_session_provider(
    connection: "AcpConnection",
    *,
    runtime_id: str,
    cwd: Path,
    session_files_dir: Path | None = None,
    model: str = "",
    agent_name: str = "",
    mcp_servers: list | None = None,
) -> "AcpSessionProvider":
    """Open a new session on an already-live (spawned + ``initialize``-d) connection and
    wrap it in an :class:`AcpSessionProvider`. Multiple calls on the same connection =
    concurrent sessions on one process (the P9 win). The caller (pool) owns spawning the
    connection + its lifetime."""
    session = await connection.new_session(
        {"cwd": str(cwd), "mcpServers": mcp_servers or []},
        session_files_dir=session_files_dir,
    )
    return AcpSessionProvider(
        connection, session, runtime_id=runtime_id, model=model, agent_name=agent_name
    )
