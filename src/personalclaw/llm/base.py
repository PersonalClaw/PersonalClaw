"""ModelProvider ABC and provider-agnostic event types.

``ModelProvider`` is the inference-axis base — a model provider is not always an
LLM (it may serve chat, embedding, STT/TTS, vision, image-gen, or doc-parse), so
the abstraction is named for the model, not the modality. All inference backends
(ACP, OpenAI-compatible, Ollama, Claude Code, ...) implement ``ModelProvider``.
Consumers (handler, gateway, CLI) depend only on this interface, never on a
concrete provider.

The stateless completion contract (``complete()`` + ``supports_tools``) lives on
this same base: it owns **no** session, agent loop, or tool execution — those
belong to an ``AgentProvider`` (the native loop). ``complete()`` is the
loop-facing contract the native agent loop calls turn-by-turn; the four HTTP
adapters (openai / anthropic / ollama / vllm) and bedrock implement it. ACP is
also an ``AgentProvider``, not a stateless completion adapter.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

# Event kinds + the neutral event — the single source of truth is llm/events.py.
# LLMEvent stays as the public alias every provider/consumer imports; it is now
# the backend-neutral AgentEvent, no longer the ACP event (decouples G5).
from personalclaw.llm.events import (  # noqa: F401
    EVENT_AGENT_SWITCHED,
    EVENT_CLEAR_STATUS,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
)
from personalclaw.llm.events import AgentEvent as LLMEvent  # noqa: F401

CancelOutcome = Literal["acked", "timeout", "no_turn", "error"]


class ModelProvider(ABC):
    """Abstract inference backend (the model-provider axis)."""

    # Whether this provider can accept + emit tool calls. openai/anthropic set
    # True; ollama is False (degrade to tool-less single-shot in the loop).
    supports_tools: bool = False

    @abstractmethod
    async def start(self) -> None:
        """Initialize the provider (spawn process, create client, etc.)."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Gracefully shut down."""

    @abstractmethod
    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        """Send a message and yield events."""
        yield LLMEvent(kind=EVENT_COMPLETE)  # pragma: no cover

    @abstractmethod
    async def approve_tool(self, request_id: str | int) -> None:
        """Approve a pending tool permission request."""

    @abstractmethod
    async def reject_tool(self, request_id: str | int) -> None:
        """Reject a pending tool permission request."""

    @abstractmethod
    def context_usage_pct(self) -> float:
        """Return last known context usage percentage."""

    @property
    def session_id(self) -> str:
        """Provider-specific session identifier for file cleanup.

        Returns empty string if the provider has no persistent session files.
        Each provider overrides to return its own session_id.
        """
        return ""

    async def cleanup_session(self, session_id: str) -> None:
        """Delete on-disk session files for the given session ID.

        Default implementation is a no-op. Providers with persistent
        session files override this to perform actual deletion.

        cleanup_session only operates on the filesystem (Path.unlink,
        shutil.rmtree). It does NOT depend on the provider process being
        alive. This makes fire-and-forget via asyncio.ensure_future safe —
        the cleanup task only needs the session_id string, not a live process.
        """

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        """Execute a slash command and yield streaming events.

        Default falls back to :meth:`stream` for providers without native
        command support.
        """
        async for event in self.stream(command):
            yield event

    async def compact(self, context: str = "") -> None:
        """Trigger context compaction. No-op for providers without native support."""

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        """Wait for compaction completed/failed. Returns ``{'type': 'timeout'}`` by default."""
        return {"type": "timeout"}

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Cancel in-flight operation. Returns CancelOutcome."""
        return "no_turn"

    def is_alive(self) -> bool:
        """Return True if the provider's backing process/connection is alive."""
        return True

    def touch_activity(self) -> None:
        """Refresh provider activity timestamp without I/O. Default no-op."""
        return None

    def set_workspace(self, path: Path) -> None:
        """Override the working directory used for subsequent provider activity.

        Default implementation is a no-op. Providers backed by a process
        whose ``cwd`` is part of the protocol contract (e.g. ACP agents
        spawned over stdio) override this to redirect future invocations
        to *path*. Providers without a notion of a workspace (text-only
        Q&A, embedding-only) inherit the no-op and silently ignore the
        call.
        """
        return None

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        """Bind a logical session key (and optional channel) to this provider.

        The session key is an opaque identifier used by stateful agents
        (e.g. ACP-over-stdio backends) to scope on-disk session files
        and resume prior conversations. ``channel_id`` optionally rebinds
        the collaboration room in the same call (used by the warm-pool
        claim path). Default implementation is a no-op for stateless
        providers (HTTP-only chat completions, embedding-only). Providers
        that maintain per-session filesystem state override this to update
        their internal binding without re-spawning the underlying process.
        """
        return None

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        """Stream a completion for the full ``messages`` list (stateless).

        The caller (native loop) owns conversation history and passes the entire
        message list each turn. ``tools`` is a list of model tool schemas; the
        provider emits ``EVENT_TOOL_CALL`` events for any tool the model invokes
        (it does NOT execute them — the loop does).

        ``reasoning_effort`` is the session's chosen effort (``"" | low | medium |
        high | max`` for native providers). Providers whose model supports extended
        thinking / reasoning map it to their request (Anthropic ``thinking`` budget,
        OpenAI ``reasoning_effort``); others ignore it. "" = model default.

        Default implementation: a convenience adapter over the simple-prompt
        ``stream(str)`` API — it sends the last user message. Concrete completion
        adapters (openai / anthropic / ollama / vllm / bedrock) override this with
        real multi-message + tools support.
        """
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = str(m.get("content", ""))
                break
        async for ev in self.stream(last_user):
            yield ev
