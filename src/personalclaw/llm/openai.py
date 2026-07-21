"""OpenAI-compatible PROTOCOL client — Chat Completions + Embeddings via the
``openai`` SDK.

The ``openai`` SDK is imported lazily inside :meth:`OpenAIProvider.__init__`
to satisfy Requirement R6.2 / Property 11 (Provider SDK Lazy Import). The
module file itself is safe to import without ``openai`` installed: only
constructing an :class:`OpenAIProvider` instance triggers the SDK import.

This module carries NO registration side effect: the provider TYPE
registration (capability descriptor + factory) lives in the standalone
``apps/openai-models`` bundle, which imports this class via
``personalclaw.sdk.model`` (see the tail comment).
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from personalclaw._sdk_deps import require_sdk
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
from personalclaw.llm.stream_tags import KIND_OUTSIDE, make_think_splitter

logger = logging.getLogger(__name__)

# Max conversation history entries before trimming oldest.
_MAX_HISTORY = 50

# Default fallback when the model is not found in ``model_tokens.json``.
# Older OpenAI 4-class context windows are typically 128k; newer models
# (gpt-4.1 / gpt-4o / o-series) are 200k+. We pick a conservative value
# so the percentage estimate skews high rather than hides usage.
_DEFAULT_CONTEXT_WINDOW = 128_000


# Model → context window tokens (loaded from shared JSON).
from personalclaw.model_windows import model_context_window as _model_window  # noqa: E402


class OpenAIProvider(ModelProvider):
    """ModelProvider backed by the OpenAI Chat Completions + Embeddings APIs.

    The ``openai`` SDK is imported inside ``__init__`` so the package
    ``personalclaw.providers`` can be imported without pulling the SDK into
    ``sys.modules`` (R6.1 / Property 11).
    """

    # The Chat Completions API accepts a multi-message history + tool schemas,
    # so the native loop can drive a stateless tool-enabled turn via complete().
    supports_tools: bool = True

    def __init__(
        self,
        *,
        model: str,
        credential: Credential | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, object] | None = None,
    ) -> None:
        # Lazy import per R6.2 / Property 11. Do NOT lift to module top.
        # openai is an OPTIONAL SDK (plan 34 T1.4) — require_sdk raises a clear
        # MissingSDKError naming `pip install personalclaw[openai]` when absent.
        openai = require_sdk("openai", "openai", feature="the OpenAI chat/embedding provider")

        if credential is None or not credential.secret:
            raise CredentialMissing("OpenAIProvider requires a credential with a populated secret")

        self._openai_module = openai
        self._model = model
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._extra_options: dict[str, object] = dict(extra_options or {})
        # The bound embedding model (the Settings → Models ``embedding`` selection)
        # arrives as a build kwarg via extra_options. No vendor default is baked
        # in — an empty value means embed() errors clearly instead of silently
        # calling an OpenAI-specific model id on a non-OpenAI endpoint.
        self._embedding_model = str(self._extra_options.pop("embedding_model", ""))
        self._client: Any = openai.AsyncOpenAI(
            api_key=credential.secret,
            base_url=base_url,
        )
        self._history: list[dict[str, Any]] = []
        self._last_context_pct: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Idempotent — the AsyncOpenAI client is already constructed.

        When no model was pinned (``self._model`` empty), resolve a default from
        LIVE ``/v1/models`` discovery rather than a hardcoded id (de-hardcode
        directive 2026-07-06): pick the first chat-capable model the endpoint
        advertises. Leaves it empty if discovery yields nothing (the call then
        errors clearly rather than sending a bogus baked id)."""
        if not self._model:
            try:
                from personalclaw.llm.catalog import (
                    infer_capabilities,
                    openai_compatible_list_models,
                )

                cred = getattr(self._client, "api_key", "") or ""
                models = await openai_compatible_list_models(self._base_url or "", cred)
                chat = next(
                    (
                        m.id
                        for m in models
                        if "chat" in (m.capabilities or infer_capabilities(m.id))
                    ),
                    models[0].id if models else "",
                )
                if chat:
                    self._model = chat
                    logger.info("OpenAI: auto-selected default %r from /v1/models discovery", chat)
            except Exception:
                logger.debug("OpenAI default resolution via discovery failed", exc_info=True)
        logger.info(
            "OpenAI provider ready: model=%s base_url=%s",
            self._model or "<unresolved>",
            self._base_url or "<default>",
        )

    async def shutdown(self) -> None:
        """Close the underlying HTTP client and clear conversation history."""
        try:
            await self._client.close()
        except Exception:  # pragma: no cover — defensive
            logger.warning("OpenAI client close raised", exc_info=True)
        self._history.clear()

    # ── Streaming ─────────────────────────────────────────────────────

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        """Stream a chat completion; translate deltas to :class:`LLMEvent`.

        Tool-call deltas are accumulated per ``tool_call_id`` because the
        SDK emits OpenAI tool arguments as streamed JSON fragments. A
        single ``EVENT_TOOL_CALL`` is emitted per completed call once the
        next call begins or the stream finishes.
        """
        self._history.append({"role": "user", "content": message})
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._history,
            "stream": True,
            # Ask the endpoint to emit a final usage chunk so we can report
            # input/output token counts (drives the dashboard token tickers).
            # Without this, streaming responses carry no usage and tokens read 0.
            "stream_options": {"include_usage": True},
        }
        if self._max_tokens is not None:
            request_kwargs["max_tokens"] = self._max_tokens
        # Let extra_options override any default above (e.g. disable usage for
        # an endpoint that rejects stream_options).
        for key, value in self._extra_options.items():
            request_kwargs[key] = value

        # Some OpenAI-compatible endpoints reject `stream_options`. Don't assume
        # every provider supports it — on a 400 that mentions it, retry once
        # without it so the turn still streams (we just won't get a usage chunk).
        import openai  # noqa: PLC0415

        try:
            response = await self._client.chat.completions.create(**request_kwargs)
        except openai.BadRequestError as exc:
            if "stream_options" not in request_kwargs:
                raise
            if "stream_options" not in str(exc) and "include_usage" not in str(exc):
                raise
            logger.info("Endpoint rejected stream_options; retrying without usage reporting")
            request_kwargs.pop("stream_options", None)
            response = await self._client.chat.completions.create(**request_kwargs)

        assistant_text = ""
        # Splits inline <think>…</think> reasoning from answer text across chunk
        # boundaries (DeepSeek-R1, Qwen, etc.). Self-gating: a stream with no
        # think tags passes through as plain text, so it's safe unconditionally.
        splitter = make_think_splitter()
        # Accumulators for tool-call deltas, keyed by tool_call_id.
        tool_calls: dict[str, dict[str, Any]] = {}
        emitted_tool_calls: set[str] = set()
        last_tool_call_id: str | None = None

        input_tokens = 0
        output_tokens = 0

        async for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if choices:
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    text_delta = getattr(delta, "content", None) or ""
                    if text_delta:
                        for seg in splitter.feed(text_delta):
                            if seg.kind == KIND_OUTSIDE:
                                assistant_text += seg.text
                                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=seg.text)
                            else:
                                yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=seg.text)

                    raw_tool_calls = getattr(delta, "tool_calls", None) or []
                    for tc in raw_tool_calls:
                        tc_id = getattr(tc, "id", None) or last_tool_call_id or ""
                        if not tc_id:
                            # OpenAI streams the id only on the first
                            # fragment; defensively fall back to index.
                            tc_id = f"call-{getattr(tc, 'index', 0)}"
                        last_tool_call_id = tc_id

                        bucket = tool_calls.setdefault(tc_id, {"name": "", "arguments": ""})
                        function = getattr(tc, "function", None)
                        if function is not None:
                            name_delta = getattr(function, "name", None) or ""
                            args_delta = getattr(function, "arguments", None) or ""
                            if name_delta:
                                bucket["name"] += name_delta
                            if args_delta:
                                bucket["arguments"] += args_delta
                        # Gemini 3.x attaches a thought_signature to tool calls
                        # that MUST be echoed back when the history is replayed.
                        # Capture it so the runtime can include it in the stored
                        # assistant message.
                        extra = getattr(tc, "extra_content", None)
                        if extra and isinstance(extra, dict):
                            bucket["extra_content"] = extra

                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason in {"tool_calls", "stop"}:
                    for tc_id, bucket in tool_calls.items():
                        if tc_id in emitted_tool_calls:
                            continue
                        emitted_tool_calls.add(tc_id)
                        meta = {}
                        if bucket.get("extra_content"):
                            meta["extra_content"] = bucket["extra_content"]
                        yield LLMEvent(
                            kind=EVENT_TOOL_CALL,
                            tool_call_id=tc_id,
                            title=bucket["name"],
                            tool_input=bucket["arguments"],
                            tool_meta=meta,
                        )

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                input_tokens = getattr(usage, "prompt_tokens", input_tokens) or input_tokens
                output_tokens = getattr(usage, "completion_tokens", output_tokens) or output_tokens

        # Flush the splitter's held tail (an unterminated tag → visible text).
        for seg in splitter.flush():
            if seg.kind == KIND_OUTSIDE:
                assistant_text += seg.text
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=seg.text)
            else:
                yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=seg.text)

        # Flush any tool calls whose finish_reason did not arrive (defensive).
        for tc_id, bucket in tool_calls.items():
            if tc_id in emitted_tool_calls:
                continue
            emitted_tool_calls.add(tc_id)
            meta = {}
            if bucket.get("extra_content"):
                meta["extra_content"] = bucket["extra_content"]
            yield LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=tc_id,
                title=bucket["name"],
                tool_input=bucket["arguments"],
                tool_meta=meta,
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
        """Stream a stateless completion for the full ``messages`` list.

        Unlike :meth:`stream`, this NEVER touches ``self._history`` — the
        native loop owns conversation state and passes the entire message
        list (user / assistant-with-``tool_calls`` / ``tool``-result shapes)
        each turn. Those OpenAI-shaped messages pass through unchanged.

        The tool-call delta accumulation mirrors :meth:`stream` exactly: the
        SDK streams OpenAI tool arguments as JSON fragments keyed by
        ``tool_call_id``, so a single :data:`EVENT_TOOL_CALL` is emitted per
        completed call once the next call begins or the stream finishes.
        """
        request_kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            "stream": True,
            # Ask for a final usage chunk (drives the token tickers); without
            # it streaming responses carry no usage and tokens read 0.
            "stream_options": {"include_usage": True},
        }
        if tools:
            # ``tools`` already arrives in the model's tool-schema format
            # (a list of ``{"type": "function", "function": {...}}``). Let the
            # model decide whether to call one (the default tool_choice="auto").
            request_kwargs["tools"] = tools
        if self._max_tokens is not None:
            request_kwargs["max_tokens"] = self._max_tokens
        # Reasoning models (o-series / gpt-5*) accept ``reasoning_effort`` directly
        # (minimal/low/medium/high). Pass ours through when set; map "max"→"high"
        # (OpenAI has no "max"). Only send for a reasoning-capable model name;
        # others reject it. "" = omit (model default).
        if reasoning_effort:
            _m = (model or self._model or "").lower()
            if any(p in _m for p in ("o1", "o3", "o4", "gpt-5")):
                request_kwargs["reasoning_effort"] = (
                    "high" if reasoning_effort == "max" else reasoning_effort
                )
        # Let extra_options override any default above (e.g. disable usage for
        # an endpoint that rejects stream_options).
        for key, value in self._extra_options.items():
            request_kwargs[key] = value

        # Some OpenAI-compatible endpoints reject `stream_options`. Don't assume
        # every provider supports it — on a 400 that mentions it, retry once
        # without it so the turn still streams (we just won't get a usage chunk).
        import openai  # noqa: PLC0415

        try:
            response = await self._client.chat.completions.create(**request_kwargs)
        except openai.BadRequestError as exc:
            if "stream_options" not in request_kwargs:
                raise
            if "stream_options" not in str(exc) and "include_usage" not in str(exc):
                raise
            logger.info("Endpoint rejected stream_options; retrying without usage reporting")
            request_kwargs.pop("stream_options", None)
            response = await self._client.chat.completions.create(**request_kwargs)

        # See stream(): self-gating inline <think> splitter.
        splitter = make_think_splitter()
        # Accumulators for tool-call deltas, keyed by tool_call_id.
        tool_calls: dict[str, dict[str, Any]] = {}
        emitted_tool_calls: set[str] = set()
        last_tool_call_id: str | None = None

        input_tokens = 0
        output_tokens = 0

        async for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if choices:
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    text_delta = getattr(delta, "content", None) or ""
                    if text_delta:
                        for seg in splitter.feed(text_delta):
                            yield LLMEvent(
                                kind=(
                                    EVENT_TEXT_CHUNK
                                    if seg.kind == KIND_OUTSIDE
                                    else EVENT_THINKING_CHUNK
                                ),
                                text=seg.text,
                            )

                    raw_tool_calls = getattr(delta, "tool_calls", None) or []
                    for tc in raw_tool_calls:
                        tc_id = getattr(tc, "id", None) or last_tool_call_id or ""
                        if not tc_id:
                            # OpenAI streams the id only on the first
                            # fragment; defensively fall back to index.
                            tc_id = f"call-{getattr(tc, 'index', 0)}"
                        last_tool_call_id = tc_id

                        bucket = tool_calls.setdefault(tc_id, {"name": "", "arguments": ""})
                        function = getattr(tc, "function", None)
                        if function is not None:
                            name_delta = getattr(function, "name", None) or ""
                            args_delta = getattr(function, "arguments", None) or ""
                            if name_delta:
                                bucket["name"] += name_delta
                            if args_delta:
                                bucket["arguments"] += args_delta
                        # Gemini 3.x attaches a thought_signature to tool calls
                        # that MUST be echoed back when the history is replayed.
                        # Capture it so the runtime can include it in the stored
                        # assistant message.
                        extra = getattr(tc, "extra_content", None)
                        if extra and isinstance(extra, dict):
                            bucket["extra_content"] = extra

                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason in {"tool_calls", "stop"}:
                    for tc_id, bucket in tool_calls.items():
                        if tc_id in emitted_tool_calls:
                            continue
                        emitted_tool_calls.add(tc_id)
                        meta = {}
                        if bucket.get("extra_content"):
                            meta["extra_content"] = bucket["extra_content"]
                        yield LLMEvent(
                            kind=EVENT_TOOL_CALL,
                            tool_call_id=tc_id,
                            title=bucket["name"],
                            tool_input=bucket["arguments"],
                            tool_meta=meta,
                        )

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                input_tokens = getattr(usage, "prompt_tokens", input_tokens) or input_tokens
                output_tokens = getattr(usage, "completion_tokens", output_tokens) or output_tokens

        # Flush the splitter's held tail.
        for seg in splitter.flush():
            yield LLMEvent(
                kind=EVENT_TEXT_CHUNK if seg.kind == KIND_OUTSIDE else EVENT_THINKING_CHUNK,
                text=seg.text,
            )

        # Flush any tool calls whose finish_reason did not arrive (defensive).
        for tc_id, bucket in tool_calls.items():
            if tc_id in emitted_tool_calls:
                continue
            emitted_tool_calls.add(tc_id)
            meta = {}
            if bucket.get("extra_content"):
                meta["extra_content"] = bucket["extra_content"]
            yield LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=tc_id,
                title=bucket["name"],
                tool_input=bucket["arguments"],
                tool_meta=meta,
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

    # ── Embeddings ────────────────────────────────────────────────────

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``inputs``.

        The embedding model comes from the ``embedding_model`` key in
        ``extra_options`` (threaded from the embedding use-case binding); it has no
        vendor default (empty ⇒ the call errors clearly rather than sending an
        OpenAI-specific id to a non-OpenAI compatible endpoint).
        """
        if not inputs:
            return []
        resp = await self._client.embeddings.create(
            model=self._embedding_model,
            input=inputs,
        )
        data = getattr(resp, "data", []) or []
        return [list(getattr(d, "embedding", [])) for d in data]

    # ── Tool approval (no-op) ─────────────────────────────────────────

    async def approve_tool(self, request_id: str | int) -> None:
        """No-op: OpenAI tool calls are not interactive at this layer."""
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        """No-op: OpenAI tool calls are not interactive at this layer."""
        return None

    # ── Status ────────────────────────────────────────────────────────

    def context_usage_pct(self) -> float:
        return self._last_context_pct

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Cancel is a no-op for now; later phases can wire abort plumbing."""
        return "no_turn"


# The provider TYPE registration (OPENAI_CAPABILITY + factory + create_provider) lives
# in the standalone openai-models app (apps/openai-models/provider.py), which imports
# this OpenAIProvider class via personalclaw.sdk.model. This module is now just the
# OpenAI-compatible PROTOCOL client — a core-supported standard, no provider glue.
