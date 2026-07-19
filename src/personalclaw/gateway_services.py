"""The service surface the gateway exposes to inbound channel transports.

A channel transport (Slack, and future Telegram/Discord) receives inbound
messages from its external system and must drive the platform's shared runtime:
route a message to a chat session, read cron jobs, append channel history,
consolidate conversation memory, surface notifications on the dashboard, etc.

Rather than hand a channel the whole :class:`~personalclaw.gateway.GatewayOrchestrator`
(and let it reach into private internals — the coupling that kept Slack welded to
core), the orchestrator exposes exactly this contract. The core orchestrator
satisfies it structurally; a transport depends only on this Protocol.

This is the *core → channel* seam. It carries NO channel-specific state (no Slack
client, tokens, tracking-channels, socket connection) — those live on the channel
transport itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from personalclaw.channel_history import ChannelHistory
    from personalclaw.config.loader import AppConfig
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog, HistoryConsolidator
    from personalclaw.schedule import ScheduleService
    from personalclaw.session import SessionManager
    from personalclaw.subagent import SubagentManager


@runtime_checkable
class GatewayServices(Protocol):
    """Shared runtime services a channel transport drives for inbound handling.

    All attributes are live once the gateway has started its services; a
    transport's ``start_inbound`` runs after ``_init_services``/dashboard init, so
    they are populated by the time inbound routing begins.
    """

    # Core session + conversation runtime.
    sessions: "SessionManager | None"
    ctx_builder: "ContextBuilder | None"
    conv_log: "ConversationLog | None"
    consolidator: "HistoryConsolidator | None"
    cron_svc: "ScheduleService | None"
    subagent_mgr: "SubagentManager | None"
    channel_history: "ChannelHistory | None"
    dashboard_state: "DashboardState | None"

    @property
    def config(self) -> "AppConfig":
        """The live gateway config (read-only from a transport's perspective)."""
        ...

    @property
    def owner_id(self) -> str:
        """The primary owner's channel-user id, or ``""`` if unset."""
        ...
