"""Anthropic-compatible PROTOCOL client — Messages API via the ``anthropic`` SDK.

The ``anthropic`` SDK is imported lazily inside
:meth:`AnthropicProvider.__init__` to satisfy Requirement R6.3 / Property
11 (Provider SDK Lazy Import). The module file itself is safe to import
without ``anthropic`` installed: only constructing an
:class:`AnthropicProvider` instance triggers the SDK import.

This module carries NO registration side effect: the provider TYPE
registration (capability descriptor + factory) lives in the standalone
``apps/anthropic-models`` bundle, which imports this class via
``personalclaw.sdk.model`` (see the tail comment).

Anthropic streams ``tool_use`` blocks as a sequence of ``content_block_*``
events; ``input_json_delta`` fragments are accumulated per content-block
index and emitted as a single :data:`EVENT_TOOL_CALL` once the block
ends. ``tool_result`` is NOT emitted by the provider at the stream
layer — Anthropic clients submit tool results via subsequent ``messages``
turns, so tool-result handling lives at the conversation layer (session /
chat runner) rather than in :meth:`AnthropicProvider.stream`.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    CancelOutcome,
    LLMEvent,
    ModelProvider,
)
from personalclaw.llm.credentials import Credential
from personalclaw.llm.registry import CredentialMissing

logger = logging.getLogger(__name__)

# Max conversation history entries before trimming oldest. Mirrors
# ``personalclaw.providers.openai._MAX_HISTORY``.
_MAX_HISTORY = 50

# Default fallback when the model is not found in ``model_tokens.json``.
# Anthropic's standard context window across the Claude 3 / 3.5 / 4 family
# is 200k tokens.
_DEFAULT_CONTEXT_WINDOW = 200_000

# Reasoning effort → Anthropic extended-thinking token budget. "" / unknown =
# no thinking (model default). Clamped to < max_tokens at request time. These are
# the native effort levels; ACP backends declare + map their own values.
_THINKING_BUDGETS: dict[str, int] = {
    "low": 4_096,
    "medium": 10_240,
    "high": 24_576,
    "max": 63_999,
}

# Model → context window: the shared reader (personalclaw.model_windows) is the ONE
# loader of model_tokens.json; this provider passes its own absent-model default.
from personalclaw.model_windows import model_context_window as _model_window


# ── OpenAI-shape → Anthropic-shape translation ────────────────────────────
#
# The native loop sends one uniform (OpenAI-shaped) message + tool format
# across all ModelProviders; each provider adapts. Anthropic's Messages API
# diverges from Chat Completions in three ways the loop's shapes hit:
#
#   * the system prompt is a top-level ``system=`` param, not a message;
#   * assistant tool calls are ``tool_use`` content blocks (not a separate
#     ``tool_calls`` field);
#   * tool results are ``tool_result`` content blocks inside a *user* turn
#     (not a ``role: "tool"`` message).
#
# These helpers perform that translation so ``complete()`` can accept the
# loop's OpenAI-shaped messages unchanged.


def _translate_tools(tools: list[dict]) -> list[dict]:
    """Map OpenAI ``tools`` entries to Anthropic ``[{name, description, input_schema}]``.

    Each OpenAI entry is ``{"type": "function", "function": {name, description,
    parameters}}``. Anthropic wants the function fields hoisted, with
    ``parameters`` renamed to ``input_schema``. Entries already in Anthropic
    shape (``name`` at the top level) pass through unchanged so a caller that
    pre-translated is not double-mapped.
    """
    out: list[dict] = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict):
            out.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", "") or "",
                    "input_schema": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        elif isinstance(tool, dict) and "name" in tool:
            # Already Anthropic-shaped — accept as-is.
            out.append(tool)
    return out


def _translate_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split OpenAI-shaped ``messages`` into ``(system_prompt, anthropic_messages)``.

    * ``role: "system"`` messages are concatenated into the returned system
      string (Anthropic carries the system prompt out-of-band).
    * ``role: "assistant"`` with ``tool_calls`` becomes a content-block list
      mixing an optional leading ``text`` block and one ``tool_use`` block per
      call (``arguments`` JSON string parsed into the ``input`` dict).
    * ``role: "tool"`` becomes a ``tool_result`` block; consecutive tool
      results are merged into a single user turn (Anthropic groups parallel
      tool results in one user message).
    * Plain ``user``/``assistant`` string messages pass through as
      ``{role, content}``.
    """
    system_parts: list[str] = []
    out: list[dict] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if content:
                system_parts.append(str(content))
            continue

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(msg.get("tool_call_id", "") or ""),
                "content": "" if content is None else str(content),
            }
            # Merge into the previous user turn if it is already carrying
            # tool_result blocks (parallel tool calls answered together).
            if (
                out
                and out[-1].get("role") == "user"
                and isinstance(out[-1].get("content"), list)
                and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in out[-1]["content"]
                )
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for call in msg["tool_calls"]:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                raw_args = fn.get("arguments", "") or ""
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (json.JSONDecodeError, ValueError):
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id", "") or ""),
                        "name": fn.get("name", ""),
                        "input": parsed,
                    }
                )
            out.append({"role": "assistant", "content": blocks})
            continue

        # Plain user / assistant text message — pass through unchanged.
        out.append({"role": role, "content": content})

    return "\n\n".join(system_parts), out


class AnthropicProvider(ModelProvider):
    """ModelProvider backed by the Anthropic Messages API.

    The ``anthropic`` SDK is imported inside ``__init__`` so the package
    ``personalclaw.providers`` can be imported without pulling the SDK into
    ``sys.modules`` (R6.3 / Property 11).
    """

    # The Messages API accepts a multi-message history + tool schemas; the
    # native loop drives tool-enabled turns via complete(), which translates
    # the OpenAI-shaped messages/tools it receives into Anthropic's wire format.
    supports_tools: bool = True

    def __init__(
        self,
        *,
        model: str,
        credential: Credential | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
        extra_options: dict[str, object] | None = None,
    ) -> None:
        # Lazy import per R6.3 / Property 11. Do NOT lift to module top.
        import anthropic  # noqa: WPS433

        if credential is None or not credential.secret:
            raise CredentialMissing(
                "AnthropicProvider requires a credential with a populated secret"
            )

        self._anthropic_module = anthropic
        self._model = model
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._extra_options: dict[str, object] = dict(extra_options or {})
        # ``base_url`` lets Anthropic-compatible endpoints (proxies, gateways,
        # self-hosted relays) be reached through the same provider. When None,
        # the SDK uses the official api.anthropic.com base.
        client_kwargs: dict[str, Any] = {"api_key": credential.secret}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client: Any = anthropic.AsyncAnthropic(**client_kwargs)
        self._history: list[dict[str, Any]] = []
        self._last_context_pct: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Idempotent — the AsyncAnthropic client is already constructed."""
        logger.info(
            "Anthropic provider ready: model=%s base_url=%s",
            self._model,
            self._base_url or "<default>",
        )

    async def shutdown(self) -> None:
        """Close the underlying HTTP client and clear conversation history."""
        try:
            await self._client.close()
        except Exception:  # pragma: no cover — defensive
            logger.warning("Anthropic client close raised", exc_info=True)
        self._history.clear()

    # ── Streaming ─────────────────────────────────────────────────────

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        """Stream a Messages turn; translate deltas to :class:`LLMEvent`.

        Anthropic emits a sequence of typed events:

        * ``message_start`` carries ``message.usage.input_tokens``.
        * ``content_block_start`` opens a block (``text`` or ``tool_use``).
          For ``tool_use`` we record the block's ``id`` and ``name``.
        * ``content_block_delta`` carries ``text_delta`` /
          ``input_json_delta`` / ``thinking_delta`` payloads. Text and
          thinking are emitted immediately; tool-input JSON is
          accumulated per block index.
        * ``content_block_stop`` finalizes a block; for ``tool_use`` we
          emit a single :data:`EVENT_TOOL_CALL` with the accumulated
          JSON string.
        * ``message_delta`` carries cumulative ``usage.output_tokens``.

        SDK runtime types live under ``anthropic.types``; we use
        duck-typed ``getattr`` access here so the module never imports
        them at load time (Property 11).
        """
        self._history.append({"role": "user", "content": message})
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._history,
            "max_tokens": self._max_tokens,
        }
        # Allow ``system``, ``tools``, ``temperature``, etc. to flow
        # through unchanged; the SDK ignores unknown keys.
        for key, value in self._extra_options.items():
            request_kwargs.setdefault(key, value)

        assistant_text = ""
        # Per content-block-index accumulators for tool_use blocks.
        tool_blocks: dict[int, dict[str, str]] = {}
        emitted_tool_calls: set[int] = set()

        input_tokens = 0
        output_tokens = 0

        async with self._client.messages.stream(**request_kwargs) as stream:
            async for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "message_start":
                    msg = getattr(event, "message", None)
                    usage = getattr(msg, "usage", None) if msg is not None else None
                    if usage is not None:
                        it = getattr(usage, "input_tokens", None)
                        if it is not None:
                            input_tokens = it
                        ot = getattr(usage, "output_tokens", None)
                        if ot is not None:
                            output_tokens = ot

                elif event_type == "content_block_start":
                    index = getattr(event, "index", 0) or 0
                    block = getattr(event, "content_block", None)
                    block_type = getattr(block, "type", None) if block is not None else None
                    if block_type == "tool_use":
                        tool_blocks[index] = {
                            "id": str(getattr(block, "id", "") or ""),
                            "name": str(getattr(block, "name", "") or ""),
                            "arguments": "",
                        }

                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", None) if delta is not None else None
                    if delta_type == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            assistant_text += text
                            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
                    elif delta_type == "input_json_delta":
                        index = getattr(event, "index", 0) or 0
                        partial = getattr(delta, "partial_json", "") or ""
                        bucket = tool_blocks.get(index)
                        if bucket is not None and partial:
                            bucket["arguments"] += partial
                    elif delta_type == "thinking_delta":
                        thinking = getattr(delta, "thinking", "") or ""
                        if thinking:
                            yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=thinking)

                elif event_type == "content_block_stop":
                    index = getattr(event, "index", 0) or 0
                    bucket = tool_blocks.get(index)
                    if bucket is not None and index not in emitted_tool_calls:
                        emitted_tool_calls.add(index)
                        yield LLMEvent(
                            kind=EVENT_TOOL_CALL,
                            tool_call_id=bucket["id"],
                            title=bucket["name"],
                            tool_input=bucket["arguments"],
                        )

                elif event_type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        ot = getattr(usage, "output_tokens", None)
                        if ot is not None:
                            output_tokens = ot

                # ``message_stop`` is informational; the ``async with``
                # context exit handles teardown.

        # Defensive flush — emit any unfinalized tool blocks (the SDK
        # normally emits ``content_block_stop`` for every started block,
        # but we don't want a crash mid-stream to swallow a tool call).
        for index, bucket in tool_blocks.items():
            if index in emitted_tool_calls:
                continue
            emitted_tool_calls.add(index)
            yield LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=bucket["id"],
                title=bucket["name"],
                tool_input=bucket["arguments"],
            )

        if input_tokens > 0:
            ctx = _model_window(self._model, _DEFAULT_CONTEXT_WINDOW)
            self._last_context_pct = (input_tokens / ctx) * 100

        if assistant_text:
            self._history.append({"role": "assistant", "content": assistant_text})

        yield LLMEvent(
            kind=EVENT_COMPLETE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_usage_pct=self._last_context_pct,
        )

    # ── Stateless completion (native loop) ────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        """Stream a stateless Messages turn for the full ``messages`` list.

        Unlike :meth:`stream`, this NEVER touches ``self._history`` — the
        native loop owns conversation state. The loop sends OpenAI-shaped
        messages + tools uniformly across providers; this method translates
        them into Anthropic's wire format (see :func:`_translate_messages` /
        :func:`_translate_tools`): a system message becomes the top-level
        ``system=`` param, assistant ``tool_calls`` become ``tool_use``
        content blocks, and ``role: "tool"`` results become ``tool_result``
        blocks inside a user turn.

        Streaming + token accumulation mirror :meth:`stream` exactly; a
        completed ``tool_use`` block emits one :data:`EVENT_TOOL_CALL` with
        ``tool_call_id`` = the block id, ``title`` = the tool name, and
        ``tool_input`` = the accumulated JSON argument string.
        """
        system_prompt, anth_messages = _translate_messages(messages)

        request_kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": anth_messages,
            "max_tokens": self._max_tokens,
        }
        if system_prompt:
            request_kwargs["system"] = system_prompt
        if tools:
            request_kwargs["tools"] = _translate_tools(tools)
        # Extended thinking: map the session's reasoning effort → a thinking token
        # budget. Anthropic requires budget_tokens < max_tokens, so clamp. "" =
        # no thinking (model default). budget must leave room for the answer.
        budget = _THINKING_BUDGETS.get(reasoning_effort or "")
        if budget:
            budget = min(budget, max(1024, self._max_tokens - 1024))
            request_kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Extended thinking requires temperature unset (or 1); drop any override.
            request_kwargs.pop("temperature", None)
        # Allow ``temperature`` etc. to flow through from extra_options without
        # clobbering anything set above (system/tools/messages stay authoritative).
        for key, value in self._extra_options.items():
            if key == "temperature" and "thinking" in request_kwargs:
                continue  # thinking mode forbids a custom temperature
            request_kwargs.setdefault(key, value)

        # Per content-block-index accumulators for tool_use blocks.
        tool_blocks: dict[int, dict[str, str]] = {}
        emitted_tool_calls: set[int] = set()

        input_tokens = 0
        output_tokens = 0

        async with self._client.messages.stream(**request_kwargs) as stream:
            async for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "message_start":
                    msg = getattr(event, "message", None)
                    usage = getattr(msg, "usage", None) if msg is not None else None
                    if usage is not None:
                        it = getattr(usage, "input_tokens", None)
                        if it is not None:
                            input_tokens = it
                        ot = getattr(usage, "output_tokens", None)
                        if ot is not None:
                            output_tokens = ot

                elif event_type == "content_block_start":
                    index = getattr(event, "index", 0) or 0
                    block = getattr(event, "content_block", None)
                    block_type = getattr(block, "type", None) if block is not None else None
                    if block_type == "tool_use":
                        tool_blocks[index] = {
                            "id": str(getattr(block, "id", "") or ""),
                            "name": str(getattr(block, "name", "") or ""),
                            "arguments": "",
                        }

                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", None) if delta is not None else None
                    if delta_type == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
                    elif delta_type == "input_json_delta":
                        index = getattr(event, "index", 0) or 0
                        partial = getattr(delta, "partial_json", "") or ""
                        bucket = tool_blocks.get(index)
                        if bucket is not None and partial:
                            bucket["arguments"] += partial
                    elif delta_type == "thinking_delta":
                        thinking = getattr(delta, "thinking", "") or ""
                        if thinking:
                            yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=thinking)

                elif event_type == "content_block_stop":
                    index = getattr(event, "index", 0) or 0
                    bucket = tool_blocks.get(index)
                    if bucket is not None and index not in emitted_tool_calls:
                        emitted_tool_calls.add(index)
                        yield LLMEvent(
                            kind=EVENT_TOOL_CALL,
                            tool_call_id=bucket["id"],
                            title=bucket["name"],
                            tool_input=bucket["arguments"],
                        )

                elif event_type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        ot = getattr(usage, "output_tokens", None)
                        if ot is not None:
                            output_tokens = ot

        # Defensive flush — emit any unfinalized tool blocks.
        for index, bucket in tool_blocks.items():
            if index in emitted_tool_calls:
                continue
            emitted_tool_calls.add(index)
            yield LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=bucket["id"],
                title=bucket["name"],
                tool_input=bucket["arguments"],
            )

        context_pct = 0.0
        if input_tokens > 0:
            ctx = _model_window(model or self._model, _DEFAULT_CONTEXT_WINDOW)
            context_pct = (input_tokens / ctx) * 100

        yield LLMEvent(
            kind=EVENT_COMPLETE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_usage_pct=context_pct,
            cost_usd=0.0,
        )

    # ── Tool approval (no-op) ─────────────────────────────────────────

    async def approve_tool(self, request_id: str | int) -> None:
        """No-op: Anthropic tool calls are not interactive at this layer."""
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        """No-op: Anthropic tool calls are not interactive at this layer."""
        return None

    # ── Status ────────────────────────────────────────────────────────

    def context_usage_pct(self) -> float:
        return self._last_context_pct

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Cancel is a no-op for now; later phases can wire abort plumbing."""
        return "no_turn"


# The provider TYPE registration (ANTHROPIC_CAPABILITY + factory + create_provider)
# lives in the standalone anthropic-models app (apps/anthropic-models/provider.py),
# which imports this AnthropicProvider class via personalclaw.sdk.model. This module is
# now just the Anthropic-compatible PROTOCOL client — a core-supported standard.
