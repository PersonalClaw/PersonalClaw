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
    wire_temperature,
)
from personalclaw.llm.credentials import Credential
from personalclaw.llm.inflight import InFlightRequests
from personalclaw.llm.prompt_cache import PromptCache
from personalclaw.llm.registry import CredentialMissing, require_model
from personalclaw.llm.stream_end import until_terminal
from personalclaw.llm.stream_tags import KIND_OUTSIDE, make_think_splitter
from personalclaw.turn_streams import closing_stream

logger = logging.getLogger(__name__)

# Max conversation history entries before trimming oldest.
_MAX_HISTORY = 50

#: Whose stream this adapter reads, and the event its answer ends with, as a cut-off names them.
_ADAPTER = "OpenAI-compatible"
_FINISH = "a finish_reason"

# The context gauge, in ONE place for every adapter — including the rule that an
# unresolvable window reports NOTHING rather than a percentage of an adapter-local
# fallback. This adapter used to carry `_DEFAULT_CONTEXT_WINDOW = 128_000` for that
# purpose and divide by it for any model absent from `model_tokens.json`; measured
# against a local `gemma4:12b` serving 32768 that denominator was 3.91× too large and
# capped the gauge at 25.19%, so the 70% compaction trigger could never fire (#3406).
from personalclaw.context_gauge import ContextGauge, prompt_text_chars  # noqa: E402
from personalclaw.model_windows import declared_context_window as _declared_window  # noqa: E402
from personalclaw.model_windows import resolved_context_window  # noqa: E402


def _read_cache_usage(usage: object) -> tuple[int, int]:
    """``(cache_creation_tokens, cache_read_tokens)`` from an OpenAI ``usage`` object.

    The OpenAI-dialect producer for ``LLMEvent.cache_creation_tokens`` /
    ``.cache_read_tokens`` — the twin of ``llm/anthropic.py``'s reader of the same name.
    OpenAI-family caching is AUTOMATIC: the request carries no marker (see
    :attr:`OpenAIProvider.prompt_cache`), but the vendor still REPORTS the hit — nested one
    level down on ``usage.prompt_tokens_details``, as ``cached_tokens`` for a read and
    ``cache_write_tokens`` for a write. This adapter read only ``prompt_tokens`` /
    ``completion_tokens``, so every automatic hit was dropped before it could reach the
    terminal event and the turn told the user ``0`` on a prompt the vendor had already
    discounted.

    Defensive exactly like the Anthropic reader: a ``usage`` without the nested object (an
    endpoint that does not cache, or an older SDK), a non-``int`` value, or ``None`` all
    yield ``0`` and NEVER raise — a cache-usage read must not break a completed turn's
    terminal event.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0, 0

    def _int(name: str) -> int:
        v = getattr(details, name, None)
        return v if isinstance(v, int) else 0

    return _int("cache_write_tokens"), _int("cached_tokens")


def _uncached_prompt_tokens(
    prompt_tokens: int, cache_creation_tokens: int, cache_read_tokens: int
) -> int:
    """OpenAI's ``prompt_tokens`` MINUS its cached span — the uncached remainder.

    THE DIALECT ASYMMETRY, and the reason reading the cache fields is not a straight field
    copy. ``LLMEvent``'s three prompt buckets are contractually DISJOINT: ``input_tokens``
    EXCLUDES the cached tokens. That is why ``stats.cache_hit_pct`` ADDS all three to recover
    the whole prompt (``stats.py:160-162``, which states the invariant and cites its
    evidence) and why the one pricing function bills them additively
    (``routing/rates.py::ModelRate.cost``).
    Anthropic's wire satisfies the contract natively — ``usage.input_tokens`` and the
    ``usage.cache_*_input_tokens`` fields are separate populations. **OpenAI's does not:**
    ``prompt_tokens_details`` is a BREAKDOWN of ``prompt_tokens``, not a sibling of it, so the
    cached tokens are counted inside ``prompt_tokens`` as well.

    Copying the field straight across would therefore bill the cached span twice, inflate
    ``cache_hit_pct``'s denominator, and — worst — inflate the cache saving
    (``routing/rates.py::cache_savings_usd``), whose counterfactual re-bills
    ``input + cache_read + cache_creation`` at the full input rate. An overstated saving is
    worse than the honest zero it replaces, so the vendor's overlap is resolved HERE, in the
    adapter that owns the dialect, and core keeps the single contract it documents.

    Clamped at ``0``: an endpoint that ever reports a cached span wider than the prompt yields
    an honest ``0`` remainder rather than a negative token count.
    """
    return max(0, prompt_tokens - cache_creation_tokens - cache_read_tokens)


def _finished(chunk: Any) -> bool:
    """Whether *chunk* ends the answer: a choice of it carries its ``finish_reason``. The SDK keeps
    the closing ``[DONE]`` line to itself, so this is the end of an answer the adapter can see."""
    return any(getattr(c, "finish_reason", None) for c in getattr(chunk, "choices", None) or [])


def _tool_call_events(tool_calls: dict[str, dict[str, Any]], stop_reason: str) -> list[LLMEvent]:
    """The answer's tool calls, one ``EVENT_TOOL_CALL`` each, once the answer has ended, each
    carrying how it ended (``stop_reason``).

    A call cut at ``max_tokens`` (``length``) then reaches the runtime as a truncation, not as a
    call that left out an argument it made correctly. A stream that ends before its
    ``finish_reason`` emits none: its reading raises first (``until_terminal``), since the
    arguments of its calls may never have finished.
    """
    events: list[LLMEvent] = []
    for tc_id, bucket in tool_calls.items():
        meta = {"extra_content": bucket["extra_content"]} if bucket.get("extra_content") else {}
        events.append(
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=tc_id,
                title=bucket["name"],
                tool_input=bucket["arguments"],
                tool_meta=meta,
                stop_reason=stop_reason,
            )
        )
    return events


class OpenAIProvider(ModelProvider):
    """ModelProvider backed by the OpenAI Chat Completions + Embeddings APIs.

    The ``openai`` SDK is imported inside ``__init__`` so the package
    ``personalclaw.providers`` can be imported without pulling the SDK into
    ``sys.modules`` (R6.1 / Property 11).
    """

    # The Chat Completions API accepts a multi-message history + tool schemas,
    # so the native loop can drive a stateless tool-enabled turn via complete().
    supports_tools: bool = True

    # Graded prompt-cache posture. OpenAI caches a stable prompt PREFIX
    # server-side on its own — no per-request marker, no opt-in — which is exactly what
    # AUTOMATIC means, and PCS-1 already ordered the prompt so that prefix is stable
    # across turns. So this adapter translates NOTHING: `mark_cacheable_prefix` returns
    # the caller's list unchanged (same object) for AUTOMATIC just as it does for NONE,
    # and the wire payload is byte-identical to what an undeclared provider sends.
    #
    # The value is therefore purely DECLARATIVE, and that is the point: the native loop
    # reads this attr by getattr (runtime.py:780, mirroring how it reads supports_tools),
    # so leaving it unset made the loop resolve `ModelProvider.prompt_cache` = NONE and
    # report "this provider does not cache" for a provider that does. It is also the
    # instance-side twin of the declarative `prompt_cache` the openai-models app already
    # sets on its ProviderCapability; capabilities.py's field comment requires the two to
    # carry the SAME grade, and until now they disagreed.
    prompt_cache: PromptCache = PromptCache.AUTOMATIC

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
        # openai is an OPTIONAL SDK — require_sdk raises a clear
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
        # The served context window this binding DECLARES (``entry.options``'
        # ``context_window``) — the escape hatch for a local endpoint whose real window
        # is neither the model's architectural maximum nor the conservative local
        # default. POPPED like ``embedding_model`` and ``max_tokens``: whatever is left
        # in _extra_options is forwarded into the SDK request kwargs, and
        # ``context_window`` is not a wire parameter. ``None`` = undeclared.
        self.context_window: int | None = _declared_window(
            self._extra_options.pop("context_window", None)
        )
        self._client: Any = openai.AsyncOpenAI(
            api_key=credential.secret,
            base_url=base_url,
        )
        self._history: list[dict[str, Any]] = []
        # ``None`` until the first usage report: before then this provider has no
        # measurement, and 0.0 would be a fabricated one (see llm/base contract).
        self._last_context_pct: float | None = None
        # The context gauge for THIS binding. Stateful because truncation detection is
        # ordinal — it compares a report against the largest prompt this binding has
        # already had measured — see personalclaw.context_gauge.
        self._gauge = ContextGauge()
        # The requests open now, relayed so ``cancel()`` can close one where it is.
        self._requests = InFlightRequests()

    @property
    def sampling_temperature(self) -> float | None:
        """The ``temperature`` every request carries: ``extra_options`` is forwarded verbatim."""
        return wire_temperature(self._extra_options.get("temperature"))

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Idempotent — the AsyncOpenAI client is already constructed.

        It never picks a model: a provider built for no model stays so, and each request refuses
        rather than name none (:func:`~personalclaw.llm.registry.require_model`). It used to take
        the first chat model the endpoint's ``/v1/models`` listed, so with nothing bound a Groq
        instance saved without a Default Model answered on a model nobody chose."""
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
        """Stream a turn (:meth:`_stream_chat`), closable by :meth:`cancel`."""
        async with closing_stream(self._requests.relay(self._stream_chat(message))) as events:
            async for event in events:
                yield event

    async def _stream_chat(self, message: str) -> AsyncIterator[LLMEvent]:
        """Stream a chat completion; translate deltas to :class:`LLMEvent`.

        Tool-call deltas are accumulated per ``tool_call_id`` because the
        SDK emits OpenAI tool arguments as streamed JSON fragments. A
        single ``EVENT_TOOL_CALL`` is emitted per call once the answer's
        ``finish_reason`` has arrived; a stream that ends before it raises
        ``AnswerCutOff`` (``until_terminal``).
        """
        model = require_model(self._model)
        self._history.append({"role": "user", "content": message})
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        request_kwargs: dict[str, Any] = {
            "model": model,
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
        last_tool_call_id: str | None = None

        input_tokens = 0
        output_tokens = 0
        cache_creation_tokens = 0
        cache_read_tokens = 0
        # How the completion ended (`finish_reason`), carried on the terminal event: `length` is
        # the one a consumer must know, a reply or a turn cut at the output cap.
        stop_reason = ""

        async for chunk in until_terminal(
            response, ends=_finished, adapter=_ADAPTER, missing=_FINISH, model=model
        ):
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
                if finish_reason:
                    stop_reason = str(finish_reason)

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", output_tokens) or output_tokens
                if prompt_tokens:
                    # Prompt-cache usage. The vendor reports the automatic hit under
                    # `prompt_tokens_details`, and `prompt_tokens` INCLUDES it — so the cached
                    # span is subtracted back out to keep the event's three buckets disjoint.
                    cache_creation_tokens, cache_read_tokens = _read_cache_usage(usage)
                    input_tokens = _uncached_prompt_tokens(
                        prompt_tokens, cache_creation_tokens, cache_read_tokens
                    )

        # Flush the splitter's held tail (an unterminated tag → visible text).
        for seg in splitter.flush():
            if seg.kind == KIND_OUTSIDE:
                assistant_text += seg.text
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=seg.text)
            else:
                yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=seg.text)

        for event in _tool_call_events(tool_calls, stop_reason):
            yield event

        # The gauge measures the WHOLE served prompt, so it reconstructs it from the three
        # disjoint buckets the same way `stats.cache_hit_pct` does (`stats.py:160-162`). A
        # cached turn must not read as a smaller context: the model still saw every token.
        # With no cache activity both buckets are 0 and this is the value it always was.
        prompt_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
        if prompt_tokens > 0:
            # ``override=`` is the per-binding declaration and deliberately the ONLY
            # window claim that reaches a measured gauge: the ``local=`` short-circuit is
            # a conservative FLOOR for the char estimate (LOCAL_SERVED_CONTEXT_WINDOW),
            # and substituting it here would divide a real 26682-token prompt by 4096 and
            # display 651% — fabricating in the opposite direction from #2364.
            # ``sent_chars`` is what keeps the reading monotone: a reported total is what
            # the endpoint ACCEPTED, so on a runtime that truncates silently it collapses
            # as the prompt grows (#3405).
            self._last_context_pct = self._gauge.measure(
                reported_tokens=prompt_tokens,
                sent_chars=prompt_text_chars(request_kwargs["messages"]),
                window=resolved_context_window(self._model, override=self.context_window),
            )

        if assistant_text:
            self._history.append({"role": "assistant", "content": assistant_text})

        yield LLMEvent(
            kind=EVENT_COMPLETE,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
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
        """Stream a stateless completion (:meth:`_complete_chat`), closable by :meth:`cancel`."""
        chat = self._complete_chat(
            messages, tools=tools, model=model, reasoning_effort=reasoning_effort
        )
        async with closing_stream(self._requests.relay(chat)) as events:
            async for event in events:
                yield event

    async def _complete_chat(
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
        call once the answer's ``finish_reason`` has arrived, and a stream that
        ends before it raises ``AnswerCutOff`` (``until_terminal``).
        """
        request_kwargs: dict[str, Any] = {
            "model": require_model(model or self._model),
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
        last_tool_call_id: str | None = None

        input_tokens = 0
        output_tokens = 0
        cache_creation_tokens = 0
        cache_read_tokens = 0
        # How the completion ended (`finish_reason`), carried on the terminal event: `length` is
        # the one a consumer must know, a reply or a turn cut at the output cap.
        stop_reason = ""

        async for chunk in until_terminal(
            response,
            ends=_finished,
            adapter=_ADAPTER,
            missing=_FINISH,
            model=str(request_kwargs["model"]),
        ):
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
                if finish_reason:
                    stop_reason = str(finish_reason)

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", output_tokens) or output_tokens
                if prompt_tokens:
                    # Prompt-cache usage — see the note at the streaming twin.
                    cache_creation_tokens, cache_read_tokens = _read_cache_usage(usage)
                    input_tokens = _uncached_prompt_tokens(
                        prompt_tokens, cache_creation_tokens, cache_read_tokens
                    )

        # Flush the splitter's held tail.
        for seg in splitter.flush():
            yield LLMEvent(
                kind=EVENT_TEXT_CHUNK if seg.kind == KIND_OUTSIDE else EVENT_THINKING_CHUNK,
                text=seg.text,
            )

        for event in _tool_call_events(tool_calls, stop_reason):
            yield event

        context_pct: float | None = None
        # The whole served prompt, reconstructed from the three disjoint buckets — see the
        # note at the streaming gauge above.
        prompt_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
        if prompt_tokens > 0:
            # ``override=`` only, and ``sent_chars`` — see the note at the streaming
            # gauge above for both.
            context_pct = self._gauge.measure(
                reported_tokens=prompt_tokens,
                sent_chars=prompt_text_chars(request_kwargs["messages"]),
                window=resolved_context_window(model or self._model, override=self.context_window),
            )

        yield LLMEvent(
            kind=EVENT_COMPLETE,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            context_usage_pct=context_pct,
            cost_usd=0.0,
        )

    # ── Embeddings ────────────────────────────────────────────────────

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``inputs``.

        The embedding model comes from the ``embedding_model`` key in
        ``extra_options`` (threaded from the embedding use-case binding); it has no
        vendor default (empty ⇒ the call is refused before it is sent, rather than naming
        no model or an OpenAI-specific id to a non-OpenAI compatible endpoint).
        """
        if not inputs:
            return []
        resp = await self._client.embeddings.create(
            model=require_model(self._embedding_model),
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

    def context_usage_pct(self) -> float | None:
        return self._last_context_pct

    async def served_context_window(self) -> int | None:
        """The window this binding's gauge divides by: a declared ``context_window``, else the
        table's entry for the model, else ``None`` — so the prompt budget and the gauge that
        measures it can never describe two different windows."""
        return resolved_context_window(self._model, override=self.context_window)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Close the request in flight, so a stop ends the turn now instead of when the endpoint
        finishes answering (and stops paying for an answer nobody reads). ``"no_turn"`` when none
        is open."""
        return await self._requests.cancel(wait_ack_timeout=wait_ack_timeout)


# The provider TYPE registration (OPENAI_CAPABILITY + factory + create_provider) lives
# in the standalone openai-models app (apps/openai-models/provider.py), which imports
# this OpenAIProvider class via personalclaw.sdk.model. This module is now just the
# OpenAI-compatible PROTOCOL client — a core-supported standard, no provider glue.
