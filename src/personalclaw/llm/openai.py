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
from personalclaw.llm.prompt_cache import PromptCache
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
from personalclaw.model_windows import declared_context_window as _declared_window  # noqa: E402
from personalclaw.model_windows import model_context_window as _model_window  # noqa: E402


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
    evidence) and why ``pricing.estimate_cost`` bills them additively (``pricing.py:106-113``).
    Anthropic's wire satisfies the contract natively — ``usage.input_tokens`` and the
    ``usage.cache_*_input_tokens`` fields are separate populations. **OpenAI's does not:**
    ``prompt_tokens_details`` is a BREAKDOWN of ``prompt_tokens``, not a sibling of it, so the
    cached tokens are counted inside ``prompt_tokens`` as well.

    Copying the field straight across would therefore bill the cached span twice, inflate
    ``cache_hit_pct``'s denominator, and — worst — inflate ``pricing.cache_savings_usd``,
    whose counterfactual re-bills ``input + cache_read + cache_creation`` at the full input
    rate (``pricing.py:166-171``). An overstated saving is worse than the honest zero it
    replaces, so the vendor's overlap is resolved HERE, in the adapter that owns the dialect,
    and core keeps the single contract it documents.

    Clamped at ``0``: an endpoint that ever reports a cached span wider than the prompt yields
    an honest ``0`` remainder rather than a negative token count.
    """
    return max(0, prompt_tokens - cache_creation_tokens - cache_read_tokens)


class OpenAIProvider(ModelProvider):
    """ModelProvider backed by the OpenAI Chat Completions + Embeddings APIs.

    The ``openai`` SDK is imported inside ``__init__`` so the package
    ``personalclaw.providers`` can be imported without pulling the SDK into
    ``sys.modules`` (R6.1 / Property 11).
    """

    # The Chat Completions API accepts a multi-message history + tool schemas,
    # so the native loop can drive a stateless tool-enabled turn via complete().
    supports_tools: bool = True

    # Graded prompt-cache posture (PCS-3 / §C4). OpenAI caches a stable prompt PREFIX
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
        # One-shot image content part for the next turn (MI-4). None on every ordinary
        # turn, which is what keeps the untouched wire shape byte-identical.
        self._pending_image: str = ""

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

    # ── Image content parts (MI-4) ────────────────────────────────────

    def stage_image_part(self, data_url: str) -> bool:
        """Stage *data_url* onto the next turn's user message. See the base docstring.

        True unconditionally for this provider: the OpenAI Chat Completions wire
        format carries ``image_url`` parts, so delivery is a property of the
        TRANSPORT and is always available here. Whether the bound *model* can read
        the pixels is a separate question, decided by the caller against the model's
        declared capabilities — this method deliberately does not second-guess it,
        because a transport that quietly refused would be indistinguishable from one
        that dropped the image.
        """
        if not data_url:
            return False
        self._pending_image = data_url
        return True

    def _with_pending_image(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return *messages* with any staged image appended to the last user turn.

        Returns the SAME list object when nothing is staged, so an ordinary turn's
        request payload is byte-identical to what it was before this seam existed.
        Consumes the stage (one-shot) and never mutates the caller's dicts.
        """
        data_url = self._pending_image
        if not data_url:
            return messages
        self._pending_image = ""
        idx = next(
            (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
            -1,
        )
        if idx < 0:
            return messages
        out = list(messages)
        original = out[idx]
        content = original.get("content")
        parts: list[dict[str, Any]]
        if isinstance(content, list):
            parts = list(content)
        else:
            parts = [{"type": "text", "text": str(content or "")}]
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
        out[idx] = {**original, "content": parts}
        return out

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
            "messages": self._with_pending_image(self._history),
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
        cache_creation_tokens = 0
        cache_read_tokens = 0

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
                # `length` joins the flush set, and the reason RIDES the event. A completion cut at
                # `max_tokens` used to fall through to the defensive flush below, which emits the
                # partial call with no indication that it was partial — so the runtime parsed
                # truncated JSON, got nothing, and told the model it had omitted an argument for a
                # call it made correctly. The failure was misattributed, which is worse than a
                # visible error: there was no retry, no counter and no event (issue 1773).
                if finish_reason in {"tool_calls", "stop", "length"}:
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
                            stop_reason=str(finish_reason or ""),
                        )

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", output_tokens) or output_tokens
                if prompt_tokens:
                    # Prompt-cache usage (PCS-9). The vendor reports the automatic hit under
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

        # The gauge measures the WHOLE served prompt, so it reconstructs it from the three
        # disjoint buckets the same way `stats.cache_hit_pct` does (`stats.py:160-162`). A
        # cached turn must not read as a smaller context: the model still saw every token.
        # With no cache activity both buckets are 0 and this is the value it always was.
        prompt_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
        if prompt_tokens > 0:
            # ``override=`` and deliberately NOT ``local=``: this is a MEASURED
            # percentage, and the local short-circuit is a conservative FLOOR for the
            # char estimate (LOCAL_SERVED_CONTEXT_WINDOW), not a served-window claim.
            # Substituting it here would divide a real 26682-token prompt by 4096 and
            # display 651% — fabricating in the opposite direction from the bug in
            # #2364. The per-binding declaration is the only thing that is truth for
            # both paths, so only it reaches the gauge; with no declaration this stays
            # byte-identical to the table lookup it has always done.
            ctx = _model_window(self._model, _DEFAULT_CONTEXT_WINDOW, override=self.context_window)
            self._last_context_pct = (prompt_tokens / ctx) * 100

        if assistant_text:
            self._history.append({"role": "assistant", "content": assistant_text})

        yield LLMEvent(
            kind=EVENT_COMPLETE,
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
            "messages": self._with_pending_image(messages),
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
        cache_creation_tokens = 0
        cache_read_tokens = 0

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
                # `length` joins the flush set, and the reason RIDES the event. A completion cut at
                # `max_tokens` used to fall through to the defensive flush below, which emits the
                # partial call with no indication that it was partial — so the runtime parsed
                # truncated JSON, got nothing, and told the model it had omitted an argument for a
                # call it made correctly. The failure was misattributed, which is worse than a
                # visible error: there was no retry, no counter and no event (issue 1773).
                if finish_reason in {"tool_calls", "stop", "length"}:
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
                            stop_reason=str(finish_reason or ""),
                        )

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", output_tokens) or output_tokens
                if prompt_tokens:
                    # Prompt-cache usage (PCS-9) — see the note at the streaming twin.
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

        context_pct: float | None = None
        # The whole served prompt, reconstructed from the three disjoint buckets — see the
        # note at the streaming gauge above.
        prompt_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
        if prompt_tokens > 0:
            # ``override=`` only — see the note at the streaming gauge above.
            ctx = _model_window(
                model or self._model, _DEFAULT_CONTEXT_WINDOW, override=self.context_window
            )
            context_pct = (prompt_tokens / ctx) * 100

        yield LLMEvent(
            kind=EVENT_COMPLETE,
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

    def context_usage_pct(self) -> float | None:
        return self._last_context_pct

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Cancel is a no-op for now; later phases can wire abort plumbing."""
        return "no_turn"


# The provider TYPE registration (OPENAI_CAPABILITY + factory + create_provider) lives
# in the standalone openai-models app (apps/openai-models/provider.py), which imports
# this OpenAIProvider class via personalclaw.sdk.model. This module is now just the
# OpenAI-compatible PROTOCOL client — a core-supported standard, no provider glue.
