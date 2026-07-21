"""AgentProvider — the stateful agent-runtime axis.

An ``AgentProvider`` executes an agent definition for ONE session: it owns the
turn loop, tool execution, permissions, and lifecycle. Distinct from a
``ModelProvider`` (stateless inference). Two implementations exist: the native
in-process loop and the ACP CLI backend (``acp:<cli>``).

The ABC is intentionally **method-compatible with ``ModelProvider``** (same
lifecycle / turn / permission / status surface the SessionManager + chat_runner
call), so the factory can return something satisfying both.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotation-only (PEP 563 strings) — importing at runtime would create a
    # cycle: agents.provider → llm.events → llm/__init__ (eager) → acp_agent →
    # agents.provider.
    from personalclaw.llm.events import AgentEvent


@dataclass
class AgentRuntimeDefinition:
    """The conceptual bundle a session's agent runtime executes.

    The runtime-facing shape carrying the per-agent ``provider`` selection,
    distinct from ``agents.marketplace.AgentDefinition`` (the user-authored,
    persisted config).
    """

    name: str
    provider: str = "native"  # "native" | "acp:<cli>"
    system_prompt: str = ""
    model: str = ""  # native: binds a ModelProvider; acp: hint only
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_store: str = ""
    workspace_dir: str = ""
    approval_mode: str = ""  # "" inherits global
    triggers: list[str] = field(default_factory=list)  # referenced lifecycle-trigger IDs


@dataclass
class ReadinessStatus:
    """Result of probing whether an agent backend is usable."""

    ready: bool
    state: str  # "ready" | "not_found" | "needs_login" | "timeout" | "error"
    detail: str = ""
    login_command: list[str] | None = None  # argv for the Sign-in terminal


@dataclass
class DiscoveredAgent:
    """A selectable agent exposed by a backend's live discovery surface.

    A normalized, vendor-neutral view of one agent a runtime offers — the chat
    agent-picker lists these alongside PersonalClaw's own native/saved agents so
    the user can pick a backend persona directly. Discovery is live (a session
    must be opened to read it); these are ephemeral, never persisted as
    AgentDefinitions, so the catalog always reflects the live backend.

    Each ACP axis maps onto an existing PersonalClaw concept:
      * default-dialect ``availableModes`` → one DiscoveredAgent each (``provider_agent`` =
        the modeId for ``session/set_mode``);
      * ``configOptions.effort`` → ``supported_efforts`` on the (single) runtime
        agent — a per-turn SETTING, not separate agents. The composer's reasoning
        control populates from + applies these.
    ``models`` are the selectable model-overrides the runtime offers for this
    agent (populates the model dropdown); empty = inherit.
    """

    id: str  # stable picker id, unique within the runtime
    name: str  # display name
    runtime: str  # owning runtime id, e.g. "acp:claude-code"
    description: str = ""
    provider_agent: str = ""  # ACP modeId for session/set_mode (default-dialect personas)
    reasoning_effort: str = ""  # pinned effort (legacy; unused now effort is per-turn)
    models: list[str] = field(default_factory=list)  # selectable model overrides
    # Backend-declared reasoning-effort options ({value,label}), surfaced verbatim.
    # Empty = runtime has no effort axis (composer hides the reasoning control).
    supported_efforts: list[dict] = field(default_factory=list)


@dataclass
class PermissionCapability:
    """A runtime's declared permission-mode support, for the capability-aware
    trust ladder. ``supported`` lists the PersonalClaw rungs the active runtime
    can honor (e.g. claude → all 5; the default dialect → none beyond the host gate), so the UI
    greys out rungs a backend cannot enforce."""

    supported_modes: list[str] = field(default_factory=list)  # PClaw rung names


class AgentProvider(ABC):
    """Stateful agent runtime for one session.

    Lifecycle mirrors ``ModelProvider`` (start/shutdown/stream/approve/reject/
    cancel/context_usage_pct) so the SessionManager + chat_runner consume it
    through the same surface.
    """

    # ── identity / static metadata ──
    @property
    @abstractmethod
    def provider_id(self) -> str:
        """e.g. "native" | "acp:claude-code"."""
        ...

    @classmethod
    async def probe_readiness(cls, options: dict) -> ReadinessStatus:
        """Probe whether this backend is usable with ``options``. Default ready."""
        return ReadinessStatus(ready=True, state="ready")

    @classmethod
    async def discover_agents(cls, options: dict) -> list[DiscoveredAgent]:
        """List the agents this backend exposes for the chat agent-picker.

        Default: ``[]`` — a runtime contributes no discovered agents (the native
        runtime's agents are PersonalClaw's own definitions, not discovered). The
        ACP runtime opens one session and reads its live discovery surface,
        delegating the vendor-specific normalization to its dialect. Discovery is
        EXPENSIVE (spawn + ``initialize`` + ``session/new``), so callers cache it
        (the API route owns a TTL cache) and never invoke it on a hot path.
        """
        return []

    @classmethod
    def agents_from_snapshot(cls, options: dict, snapshot: dict) -> list[DiscoveredAgent]:
        """Map a warmed pool ``session/new`` snapshot to discovered agents WITHOUT
        a fresh spawn. Default: ``[]`` — only runtimes that expose a live discovery
        surface (the ACP runtime) override this; every other backend contributes none.
        """
        return []

    # ── lifecycle (one session) ──
    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def shutdown(self) -> None: ...

    # ── the turn ──
    @abstractmethod
    def stream(self, message: str) -> AsyncIterator[AgentEvent]: ...

    async def stream_command(self, command: str) -> AsyncIterator[AgentEvent]:
        async for ev in self.stream(command):
            yield ev

    # ── permissions ──
    @abstractmethod
    async def approve_tool(self, request_id: str | int) -> None: ...
    @abstractmethod
    async def reject_tool(self, request_id: str | int) -> None: ...

    # ── status / control (default no-ops; ACP + native override) ──
    def context_usage_pct(self) -> float:
        return 0.0

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "no_turn"

    async def compact(self, context: str = "") -> None: ...

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        return {"type": "timeout"}

    def is_alive(self) -> bool:
        return True

    def is_process_alive(self) -> bool:
        return True

    def touch_activity(self) -> None: ...

    def set_workspace(self, path: Path) -> None: ...

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None: ...

    def set_channel(self, channel_id: str | None) -> None: ...

    async def set_model(self, model: str) -> None: ...

    async def set_agent(self, agent: str) -> None:
        """Switch the active agent/persona on a running connection (pool claim).
        Default no-op; ACP overrides (default dialect ``session/set_mode``)."""
        ...

    async def set_reasoning_effort(self, effort: str) -> None:
        """Set the per-turn reasoning effort on a running connection (pool claim /
        per turn). ``effort`` is one of the backend's declared effort options (see
        ``DiscoveredAgent.supported_efforts``) or "" for default. Default no-op;
        ACP overrides (Zed ``set_config_option`` configId=effort). MUST follow
        :meth:`set_model`."""
        ...

    def set_resume(self, session_id: str) -> None: ...

    @property
    def resumed(self) -> bool:
        return False

    @property
    def session_id(self) -> str:
        return ""

    @property
    def pid(self) -> int | None:
        """OS pid of a backing subprocess, or None (in-process runtimes)."""
        return None

    @property
    def agent_model(self) -> str:
        """Model the runtime is bound to (for the context table). ``""`` = unknown."""
        return ""

    @property
    def agent_name(self) -> str:
        """Agent/definition name the runtime is running."""
        return ""

    async def cleanup_session(self, session_id: str) -> None: ...
