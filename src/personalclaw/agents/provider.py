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
    persisted config). It carries no system prompt: the prompt reaches the model in
    the turn's assembled context, resolved in one place (``ContextBuilder.build_message``).
    """

    name: str
    provider: str = "native"  # "native" | "acp:<cli>"
    model: str = ""  # native: binds a ModelProvider; acp: hint only
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_store: str = ""
    workspace_dir: str = ""
    approval_mode: str = ""  # "" inherits global
    triggers: list[str] = field(default_factory=list)  # referenced lifecycle-trigger IDs


@dataclass
class ReadinessStatus:
    """Whether an agent backend is usable, as far as one check could tell.

    ``untested`` is the answer :meth:`AgentProvider.presence` gives for a backend that is
    installed but that nobody has started: only running it can say more, and PersonalClaw runs
    it only when the user asks (:meth:`AgentProvider.probe_readiness`).
    """

    ready: bool
    state: str  # "ready" | "untested" | "not_found" | "needs_login" | "timeout" | "error"
    detail: str = ""
    login_command: list[str] | None = None  # argv for the Sign-in terminal
    #: What the backend offered in the one session the check opened (the raw ACP
    #: ``session/new`` response), so the agents it lists are read from the same run rather than
    #: from a second one. Empty when the check started nothing or the session did not open.
    session_snapshot: dict = field(default_factory=dict)


@dataclass
class DiscoveredAgent:
    """A selectable agent exposed by a backend's live discovery surface.

    A normalized, vendor-neutral view of one agent a runtime offers — the chat
    agent-picker lists these alongside PersonalClaw's own native/saved agents so
    the user can pick a backend persona directly. Reading them needs an open session,
    so they are what the runtime offered at its last Test, kept with that Test's
    result (``agents/runtime_tests.py``) and never made AgentDefinitions.

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
    def presence(cls, options: dict) -> ReadinessStatus:
        """What can be known about this backend with ``options`` WITHOUT starting anything.

        Default ready: a backend with nothing to start (the in-process native runtime) is as
        ready as it will ever be. A backend that runs another program answers from what is on
        disk — ``not_found`` when that program is not there, else ``untested`` — and never
        starts it, because only the user decides when another agent's program runs.
        """
        return ReadinessStatus(ready=True, state="ready")

    @classmethod
    async def probe_readiness(cls, options: dict) -> ReadinessStatus:
        """Start the backend once and report whether it is usable. Default ready.

        A backend that runs another program STARTS it here, so this is called only for an
        action the user took to test that backend: the Test on its card, or
        ``personalclaw doctor --start-agent-clis``. Listings answer from :meth:`presence`.
        """
        return ReadinessStatus(ready=True, state="ready")

    @classmethod
    def agents_from_snapshot(cls, options: dict, snapshot: dict) -> list[DiscoveredAgent]:
        """Map a ``session/new`` snapshot (:attr:`ReadinessStatus.session_snapshot`) to
        discovered agents WITHOUT starting anything. Default: ``[]`` — only runtimes that
        expose a discovery surface (the ACP runtime) override this; every other backend
        contributes none.
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

    @property
    def supports_native_commands(self) -> bool:
        """Can this provider run a slash command AS a command? False by default, and the
        default matches the method below: it re-sends the command text as an ordinary
        prompt. A caller that must tell the user which of the two happened reads this,
        not the method's success (`G4`) — an unadvertised command must not go on the wire,
        because an agent that doesn't implement it answers JSON-RPC ``-32601`` on the
        turn's terminal frame and kills the whole turn."""
        return False

    @property
    def compacts_in_process(self) -> bool:
        """Can this provider compact its OWN conversation history itself? False by default.

        Orthogonal to :attr:`supports_native_commands` (can a BACKEND be handed a slash
        command over the wire): this asks whether the provider owns the message list at
        all. See :attr:`personalclaw.llm.base.ModelProvider.compacts_in_process` — the
        contract is declared identically on both ABCs, like the property above, because
        they are deliberately method-compatible.
        """
        return False

    @property
    def compacts_automatically(self) -> bool:
        """Will this provider compact its own history on its own at the Settings threshold?
        False by default. See :attr:`personalclaw.llm.base.ModelProvider.compacts_automatically`
        — declared identically on both ABCs, like :attr:`compacts_in_process` above."""
        return False

    @property
    def keeps_cancelled_turns(self) -> bool:
        """Whether a turn stopped mid-way stays in this provider's OWN history. False by
        default. See :attr:`personalclaw.llm.base.ModelProvider.keeps_cancelled_turns` —
        declared identically on both ABCs, like :attr:`compacts_in_process` above."""
        return False

    async def stream_command(self, command: str) -> AsyncIterator[AgentEvent]:
        async for ev in self.stream(command):
            yield ev

    def stage_image_part(self, data_url: str) -> bool:
        """Put *data_url* (``data:<media-type>;base64,…``) on the NEXT turn as an image part.

        Returns True only when the image WILL ride that turn. False by default, and the
        default is the safety property: a runtime that takes no image parts (an ACP CLI owns
        its own wire) never has an image silently dropped, because the caller reads the False
        and sends the image's extracted text instead. Whether the MODEL can read pixels is a
        separate question the caller answers first (``providers.image_input``).
        """
        return False

    # ── permissions ──
    @abstractmethod
    async def approve_tool(self, request_id: str | int) -> None: ...
    @abstractmethod
    async def reject_tool(self, request_id: str | int) -> None: ...

    # ── status / control (default no-ops; ACP + native override) ──
    def context_usage_pct(self) -> float | None:
        """Unknown by default — a provider that measures nothing reports nothing."""
        return None

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
        """Switch the active agent/persona on a running connection (a session opened on a
        shared connection). Default no-op; ACP overrides (default dialect ``session/set_mode``)."""
        ...

    async def set_reasoning_effort(self, effort: str) -> None:
        """Set the per-turn reasoning effort on a running connection (a session opened on a
        shared connection, or per turn). ``effort`` is one of the backend's declared effort
        options (see ``DiscoveredAgent.supported_efforts``) or "" for default. Default no-op;
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
