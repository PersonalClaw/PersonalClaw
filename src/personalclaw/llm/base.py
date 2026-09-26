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
from personalclaw.llm.prompt_cache import PromptCache


def wire_temperature(value: object) -> float | None:
    """A request-bound ``temperature`` value as the float it is, or ``None`` when it is none.

    ``bool`` is refused even though it is an ``int``: ``True`` is not a temperature.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


CancelOutcome = Literal["acked", "timeout", "no_turn", "error"]


class ModelProvider(ABC):
    """Abstract inference backend (the model-provider axis)."""

    # Whether this provider can accept + emit tool calls. openai/anthropic set
    # True; ollama is False (degrade to tool-less single-shot in the loop).
    supports_tools: bool = False

    # Graded prompt-cache support the native loop reads (via getattr, mirroring
    # supports_tools) to decide how to mark a cacheable prefix. Defaults NONE so an
    # undeclared provider gets the byte-identical no-marker path; a caching provider
    # sets AUTOMATIC (stable-prefix, no marker) or EXPLICIT (per-request marker).
    prompt_cache: PromptCache = PromptCache.NONE

    # Whether this provider hands its model ONLY the user's request, never the assembled
    # context (identity, memory, skills, instructions). True for a model too small to follow a
    # long instruction block — it continues the instructions instead of answering — so the
    # provider reads the request back out at ``USER_REQUEST_MARKER`` and discards the rest.
    # Declared, because core must assemble, measure and record exactly what such a model
    # receives: a skill "used" by a model that was never shown it is a false record.
    request_only: bool = False

    # The ``"<entry>:<model>"`` ref this instance was BUILT for. Stamped by the resolution seam
    # (``providers.provider_bridge._resolve_from_config_registry``) — the one point that knows
    # both halves — so the window resolver can name the model that actually serves a turn,
    # including the zero-config fallback, which is a registry entry and not a binding.
    served_ref: str = ""

    async def served_context_window(self) -> int | None:
        """The context window, in tokens, this provider will serve its next completion with.

        ``None`` means "this provider cannot say" — the window is then resolved from what was
        declared about the model (its catalog card, then the shared window table). A provider
        that KNOWS better overrides it: an operator-declared ``context_window``, a runtime that
        publishes the window it loaded the model with, or a model this provider runs itself.
        This is the "served" step of ``context_headroom.resolve_window``, and a provider's own
        context gauge must divide by the same number, or the gauge and the prompt budget
        describe two different windows.
        """
        return None

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
    def context_usage_pct(self) -> float | None:
        """Last known context usage percentage, or ``None`` if never measured.

        ``None`` and ``0.0`` are different answers: ``None`` means this provider
        was never told, ``0.0`` means it was told the context is empty. Consumers
        must OMIT the number for ``None`` rather than render zero.
        """

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

    @property
    def supports_native_commands(self) -> bool:
        """Whether this provider can execute a slash command as a COMMAND.

        False by default, and the default is the honest answer for every provider that
        has no command axis at all (the native loop, every HTTP model provider): for
        those, :meth:`stream_command` below is a plain prompt wearing a command's name.
        A caller that must tell the user which of the two it got — because "the agent ran
        /compact" and "the agent was asked about the text /compact" are different
        answers — reads THIS, not the method's success (`G4`).
        """
        return False

    @property
    def compacts_in_process(self) -> bool:
        """Whether this provider compacts its OWN conversation history itself.

        Orthogonal to :attr:`supports_native_commands`, which asks whether a *backend*
        can be handed a slash command over the wire. This asks whether the provider owns
        the message list at all: the native loop does (``runtime._messages``), so it can
        run ``context_compaction.compact`` synchronously with no command axis and no
        agent to fire anything. An ACP backend owns its own history out of process, so
        the answer there is False — its compaction arrives as a status frame.

        The two flags answer the same question for ``/compact`` from opposite ends, and
        exactly one of them being true is what stops the command reaching a model as the
        literal text "/compact" (#470).
        """
        return False

    @property
    def sampling_temperature(self) -> float | None:
        """The sampling temperature this instance puts on the request, or ``None`` if it sends none.

        ``None`` by default, and — like :meth:`stage_image_part`'s ``False`` — the default is
        the safety property: a provider that never declared where a temperature goes cannot be
        credited with sampling at one. A caller that ASKED for a temperature (best-of-N's
        ladder, threaded as the ``temperature`` build kwarg) reads this back through the
        model-call record the guard keeps (:mod:`personalclaw.guardrails.calls`) and says so
        when it differs, instead of presenting N answers at the provider default as a sweep.

        A statement about the REQUEST, not the model: an endpoint can still ignore the field,
        which no client can observe — a zero spread across a slate is then the visible sign.
        """
        return None

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        """Execute a slash command and yield streaming events.

        Default falls back to :meth:`stream` for providers without native
        command support. That fallback is silent by design at this layer — it is the
        caller's job to say so, because only the caller owns a user-visible surface.
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

    def stage_image_part(self, data_url: str) -> bool:
        """Attach *data_url* as an image content part on the NEXT user turn.

        Returns True only if this provider will actually put the image on the wire.
        The default is **False**, and that default is the safety property: a caller
        (MULTIMODAL-IO §5.3's screen-context channel) uses the return value to decide
        between handing the model pixels and degrading to a text description. A
        provider that inherits this no-op therefore routes to the description path
        instead of having its image silently dropped — "declared support" can never
        stand in for "delivered".

        One-shot by contract: the staged part rides exactly one turn and is cleared as
        the request is built, so a later turn can't inherit an earlier image.
        """
        return False

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
