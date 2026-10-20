"""Anthropic-compatible PROTOCOL client — Messages API via the ``anthropic`` SDK.

The ``anthropic`` SDK is imported lazily inside
:meth:`AnthropicProvider.__init__`, so a provider SDK is never imported
eagerly. The module file itself is safe to import
without ``anthropic`` installed: only constructing an
:class:`AnthropicProvider` instance triggers the SDK import.

This module carries NO registration side effect: the provider TYPE
registration (capability descriptor + factory) lives in the standalone
``apps/anthropic-models`` bundle, which imports this class via
``personalclaw.sdk.model`` (see the tail comment).

Anthropic streams ``tool_use`` blocks as a sequence of ``content_block_*``
events; ``input_json_delta`` fragments are accumulated per content-block
index and emitted as a single :data:`EVENT_TOOL_CALL` each once the message
says how it ended (its ``stop_reason``). ``tool_result`` is NOT emitted by
the provider at the stream layer — Anthropic clients submit tool results via subsequent ``messages``
turns, so tool-result handling lives at the conversation layer (session /
chat runner) rather than in :meth:`AnthropicProvider.stream`.
"""

import json
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
from personalclaw.llm.catalog import (
    SAMPLING_PARAMETERS,
    refused_sampling,
    returns_thinking,
    thinking_shape,
)
from personalclaw.llm.credentials import Credential
from personalclaw.llm.inflight import InFlightRequests
from personalclaw.llm.prompt_cache import CACHE_HINT_KEY, VOLATILE_KEY, PromptCache
from personalclaw.llm.reasoning_record import REASONING_KEY
from personalclaw.llm.registry import CredentialMissing, require_model
from personalclaw.llm.stream_end import until_terminal
from personalclaw.net.http_clients import http_client
from personalclaw.turn_streams import closing_stream

logger = logging.getLogger(__name__)

# Max conversation history entries before trimming oldest. Mirrors
# ``personalclaw.providers.openai._MAX_HISTORY``.
_MAX_HISTORY = 50

#: Whose stream this adapter reads, and the event its answer ends with, as a cut-off names them.
_ADAPTER = "Anthropic-compatible"
_STOP = "a stop_reason"

# Reasoning effort → a thinking token budget, for a model that still takes ``budget_tokens``
# (``catalog.thinking_shape``). "" / unknown = no thinking (model default). Clamped to below
# max_tokens at request time. These are the native effort levels; ACP backends declare and map
# their own values.
_THINKING_BUDGETS: dict[str, int] = {
    "low": 4_096,
    "medium": 10_240,
    "high": 24_576,
    "xhigh": 40_960,
    "max": 63_999,
}

#: The level sent in place of one an adaptive model does not take: ``xhigh`` exists from Opus 4.7
#: on, so Opus 4.6 and Sonnet 4.6 are sent ``high``.
_EFFORT_STEP_DOWN: dict[str, str] = {"xhigh": "high"}

#: The content blocks a model writes its reasoning in. They go back exactly as received.
_THINKING_BLOCK_TYPES = ("thinking", "redacted_thinking")


def thinking_efforts(model: str) -> tuple[str, ...]:
    """The effort levels this wire sends ``model`` as themselves, lowest first: the levels an
    adaptive model takes (``catalog.thinking_shape``), and every budget level for any other model.
    A level outside them is sent as the nearest lower one (:func:`_thinking_request`)."""
    shape = thinking_shape(model)
    return shape.efforts if shape.adaptive else tuple(_THINKING_BUDGETS)


def _thinking_request(model: str, effort: str, max_tokens: int) -> dict[str, Any]:
    """The request fields that ask ``model`` to reason at ``effort``: ``{}`` for no effort.

    A model that thinks adaptively (``catalog.thinking_shape``) is sent
    ``thinking: {type: adaptive}`` and the depth as ``output_config.effort``, with a level it does
    not take sent as the nearest lower one. Every other model is sent a ``budget_tokens`` budget,
    which must stay below ``max_tokens`` and leave room for the answer.
    """
    effort = (effort or "").strip().lower()
    if effort not in _THINKING_BUDGETS:
        return {}
    shape = thinking_shape(model)
    if shape.adaptive:
        if effort not in shape.efforts:
            effort = _EFFORT_STEP_DOWN.get(effort, "high")
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
    budget = min(_THINKING_BUDGETS[effort], max(1024, max_tokens - 1024))
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}


def _thinking_blocks(message: dict, model: str) -> list[dict]:
    """The thinking blocks a request to ``model`` carries back on assistant ``message``: copies of
    those its reasoning record holds when ``model`` wrote them, else none."""
    if not model:
        return []
    record = message.get(REASONING_KEY)
    if not isinstance(record, dict) or record.get("model") != model:
        return []
    blocks = record.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [
        dict(b) for b in blocks if isinstance(b, dict) and b.get("type") in _THINKING_BLOCK_TYPES
    ]


def _continues_without_thinking(out: list[dict]) -> bool:
    """Whether ``out`` continues a tool call (a user turn after its last assistant turn carries a
    ``tool_result``, steers and notes beside it included) whose assistant turn does not begin with
    a thinking block. A budget-shape request with thinking on is refused then, so that request
    goes without thinking."""
    last = next((i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "assistant"), -1)
    if last < 0:
        return False
    answered = any(
        isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
        for m in out[last + 1 :]
        if m.get("role") == "user"
    )
    if not answered:
        return False
    first = out[last].get("content")
    if not isinstance(first, list) or not first:
        return True
    return not (isinstance(first[0], dict) and first[0].get("type") in _THINKING_BLOCK_TYPES)


# The context gauge, in ONE place for every adapter — including the rule that an
# unresolvable window reports NOTHING rather than a percentage of an adapter-local
# fallback. This adapter used to carry `_DEFAULT_CONTEXT_WINDOW = 200_000` for that
# purpose; a Claude model the shared table has not been taught yet now reads UNMEASURED
# instead of being measured against a window nobody declared (#3406).
from personalclaw.context_gauge import ContextGauge, prompt_text_chars  # noqa: E402
from personalclaw.model_windows import declared_context_window as _declared_window  # noqa: E402
from personalclaw.model_windows import resolved_context_window  # noqa: E402

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


def _read_cache_usage(usage: object) -> tuple[int, int]:
    """`(cache_creation_tokens, cache_read_tokens)` from an Anthropic ``usage`` object.

    The producer for `LLMEvent.cache_creation_tokens` / `.cache_read_tokens`, which the event
    declared but nothing populated. Defensive by design: a `usage` missing the fields
    (a non-cached response, or an older SDK), a non-int value, or `None` all yield ``0`` and NEVER
    raise — a cache-usage read must never break a completed turn's terminal event.
    """
    if usage is None:
        return 0, 0

    def _int(name: str) -> int:
        v = getattr(usage, name, None)
        return v if isinstance(v, int) else 0

    return _int("cache_creation_input_tokens"), _int("cache_read_input_tokens")


def _stop_reason_of(event: Any) -> str:
    """How the message ended, when *event* is the ``message_delta`` that says it, else ``""``.

    That event ends the answer: every content block has closed before it, and only
    ``message_stop``, which carries nothing, follows it. Read from ``delta`` first (where the wire
    puts it) and then the event, because the two SDK generations differ and guessing one costs the
    signal.
    """
    if getattr(event, "type", None) != "message_delta":
        return ""
    delta = getattr(event, "delta", None)
    return str(getattr(delta, "stop_reason", None) or getattr(event, "stop_reason", None) or "")


def _ends_the_answer(event: Any) -> bool:
    return bool(_stop_reason_of(event))


def _tool_call_events(tool_blocks: dict[int, dict[str, str]], stop_reason: str) -> list[LLMEvent]:
    """The answer's ``tool_use`` blocks, one ``EVENT_TOOL_CALL`` each in the order they opened,
    once the message has said how it ended, each carrying that ``stop_reason``.

    ``max_tokens`` is the one that matters: a block cut at the output cap reaches the runtime as a
    truncation rather than as a malformed call. A stream that ends before the message's stop reason
    emits none: its reading raises first (``until_terminal``), since a block's arguments may never
    have finished.
    """
    return [
        LLMEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id=block["id"],
            title=block["name"],
            tool_input=block["arguments"],
            stop_reason=stop_reason,
        )
        for _index, block in sorted(tool_blocks.items())
    ]


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
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        elif isinstance(tool, dict) and "name" in tool:
            # Already Anthropic-shaped — accept as-is.
            out.append(tool)
    return out


# Anthropic's wire form for a cache breakpoint. THIS FILE IS THE ONLY
# PLACE IN CORE THAT MAY NAME IT — `llm/anthropic.py` is one of the two in-core
# protocol clients enumerated in docs/architecture/provider-boundary.md, and the
# rails sweep in tests/test_prompt_cache_wire_translation.py fails the build if
# ``cache_control`` / ``ephemeral`` appear in any other core module. Core emits the
# NEUTRAL ``_cache_hint`` key (llm/prompt_cache.py); this is where it becomes vendor
# syntax. ``ephemeral`` is Anthropic's ~5-minute TTL tier.
_CACHE_CONTROL_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _mark_block(block: dict) -> dict:
    """Return ``block`` with Anthropic's cache breakpoint attached.

    Mutates and returns the block. Every block this touches is one the translation
    itself just constructed (or shallow-copied), never a caller's dict.
    """
    block["cache_control"] = dict(_CACHE_CONTROL_EPHEMERAL)
    return block


def _image_block(data_url: str) -> dict | None:
    """Translate a ``data:<media-type>;base64,<payload>`` URL into an Anthropic
    ``image`` content block, or ``None`` if it isn't one.

    Anthropic takes the media type and the raw payload as separate fields rather
    than a joined URL, so this is a real translation and not a rename.
    """
    if not data_url.startswith("data:"):
        return None
    head, _, payload = data_url.partition(",")
    if not payload:
        return None
    meta = head[len("data:") :]
    if not meta.endswith(";base64"):
        return None
    media_type = meta[: -len(";base64")].strip().lower()
    if not media_type:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": payload},
    }


def _translate_parts(content: list) -> list:
    """A user/assistant message's list ``content`` with neutral image parts in Anthropic's shape.

    The platform's image part is ``{"type": "image_url", "image_url": {"url": <data URL>}}``
    (``ModelProvider.complete``). The Messages API refuses that type outright, so every one
    becomes an ``image`` block; a part whose URL is not a base64 data URL is dropped rather
    than sent as a block the API would 400 on. Every other part passes through, and the
    caller's list and dicts are never mutated.
    """
    out: list = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            ref = part.get("image_url")
            url = ref.get("url", "") if isinstance(ref, dict) else ""
            block = _image_block(url) if isinstance(url, str) else None
            if block is not None:
                out.append(block)
            continue
        out.append(part)
    return out


def _lay_system_notes(out: list[dict], notes: list[str]) -> None:
    """Append each volatile system note to the last user turn of ``out``, as a text block.

    The turn keeps its own blocks first (Anthropic wants a turn's ``tool_result`` blocks ahead of
    any text), so the notes end the request, past every cache breakpoint. A note arrives fenced as
    the runtime's (``prompt_cache.system_note_text``) and is sent as it is. ``out`` holds only
    dicts and lists the translation built, so nothing a caller passed in is mutated.
    """
    if not notes:
        return
    blocks = [{"type": "text", "text": note} for note in notes]
    for msg in reversed(out):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            msg["content"] = [*content, *blocks]
        else:
            own = [{"type": "text", "text": str(content)}] if content else []
            msg["content"] = [*own, *blocks]
        return
    out.append({"role": "user", "content": blocks})


def _translate_messages(
    messages: list[dict], *, thinking_from: str = ""
) -> tuple[str | list[dict], list[dict]]:
    """Split OpenAI-shaped ``messages`` into ``(system_prompt, anthropic_messages)``.

    * An assistant message whose reasoning record
      (:data:`~personalclaw.llm.reasoning_record.REASONING_KEY`) was written by the model
      ``thinking_from`` names begins with those thinking blocks, exactly as received, ahead of its
      text and ``tool_use`` blocks. A record from any other model, and every record when
      ``thinking_from`` is ``""``, is left off: a request without thinking blocks is always valid.
      The key itself never reaches the wire.

    * ``role: "system"`` messages are concatenated into the returned system
      string (Anthropic carries the system prompt out-of-band, so it leads the
      served prompt) — UNLESS the message is tagged VOLATILE (see below).
    * A ``role: "system"`` message tagged :data:`~personalclaw.llm.prompt_cache.VOLATILE_KEY`
      is NOT hoisted into ``system=``. Anthropic serves ``system=`` ahead of
      ``messages[0]``, so a per-turn-changing note there would break the cacheable EXACT
      prefix. The Messages API has no system turn inside the conversation either, so
      the note rides the way :data:`~personalclaw.llm.prompt_cache.VOLATILE_KEY` lays
      down: one text block (its content, already fenced as the runtime's) appended to the
      request's LAST user turn — after its ``tool_result`` blocks and after any cache
      breakpoint — and never a user turn of its own, which the model reads as the newest
      thing the user said and answers in the chat. Multiple volatile notes ship once each,
      in order. A request with no user turn at all gets one carrying only the notes.
    * ``role: "assistant"`` with ``tool_calls`` becomes a content-block list
      mixing an optional leading ``text`` block and one ``tool_use`` block per
      call (``arguments`` JSON string parsed into the ``input`` dict).
    * ``role: "tool"`` becomes a ``tool_result`` block; consecutive tool
      results are merged into a single user turn (Anthropic groups parallel
      tool results in one user message).
    * Plain ``user``/``assistant`` string messages pass through as
      ``{role, content}``. A LIST content passes through with its neutral image parts
      translated to Anthropic ``image`` blocks (:func:`_translate_parts`).

    Cache translation: a message carrying the NEUTRAL
    :data:`~personalclaw.llm.prompt_cache.CACHE_HINT_KEY` is the trailing boundary of
    the cacheable span, and Anthropic marks that boundary with ``cache_control`` on a
    CONTENT BLOCK — so the hinted message's LAST block gets the marker:

    * a hinted **system** message forces ``system=`` to become BLOCK-SHAPED (a
      one-element list of ``{"type": "text", ...}``) so the marker has a block to sit
      on; the text is the same string the unhinted path would have returned, so the
      model is told byte-for-byte the same thing (soul guardrail 3). Anthropic serves
      ``system=`` ahead of ``messages[0]``, so a breakpoint there caches the whole
      stable head.
    * a hinted plain ``user``/``assistant`` message becomes block-shaped the same way
      (``content`` string → one marked ``text`` block).
    * a hinted assistant-with-``tool_calls`` / ``tool`` message keeps its block list
      and the marker lands on the LAST block.
    * a hint on an ABSENT or EMPTY span is a NO-OP: there is no block to mark, so
      nothing is emitted (never a marker on nothing).
    * a hint on a VOLATILE note is ignored — per-turn content is not cacheable by
      construction. (The neutral marker layer never places one there; this is the
      belt-and-braces read.)

    Byte-identical invariant: a ``messages`` list with NO volatile-tagged and NO
    cache-hinted message produces exactly the ``(system, out)`` this returned before
    volatile notes and cache hints existed — a ``str`` ``system`` included. ``system=`` is
    block-shaped ONLY when a hinted system message is present, so an unhinted request serializes
    today's bytes.
    """
    system_parts: list[str] = []
    out: list[dict] = []
    # Volatile system notes, laid on the last user turn after the loop (empty ⇒ the
    # return is byte-for-byte the behavior without volatile notes).
    volatile_notes: list[str] = []
    # True once a hoisted (non-volatile, non-empty) system message carried the neutral
    # cache hint. Only then does ``system=`` become block-shaped.
    system_hinted = False

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        hinted = CACHE_HINT_KEY in msg

        if role == "system":
            if msg.get(VOLATILE_KEY):
                # Volatile per-turn note → the last user turn's tail, not out-of-band
                # system=. Never marked: its content changes every turn, so a breakpoint
                # here would guarantee a miss.
                if content:
                    volatile_notes.append(str(content))
            elif content:
                # Stable system content → out-of-band system=, leads the prompt.
                system_parts.append(str(content))
                if hinted:
                    system_hinted = True
            continue

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(msg.get("tool_call_id", "") or ""),
                "content": "" if content is None else str(content),
            }
            if hinted:
                _mark_block(block)
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

        thinking = _thinking_blocks(msg, thinking_from) if role == "assistant" else []

        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict] = list(thinking)
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
            if hinted and blocks:
                _mark_block(blocks[-1])
            out.append({"role": "assistant", "content": blocks})
            continue

        # Block-shaped content carries the platform's neutral parts: its image parts
        # become Anthropic image blocks before anything else looks at the list.
        if isinstance(content, list):
            content = _translate_parts(content)

        own = (
            list(content)
            if isinstance(content, list)
            else ([{"type": "text", "text": str(content)}] if content else [])
        )
        if thinking and own:
            # A plain assistant answer its model reasoned for: the thinking blocks lead, then its
            # text, so the turn reads back as the model wrote it. A turn with no text of its own
            # is sent as before: an assistant turn of thinking alone is no answer.
            blocks = [*thinking, *own]
            if hinted:
                blocks[-1] = _mark_block(dict(blocks[-1]))
            out.append({"role": role, "content": blocks})
            continue

        # Plain user / assistant text message — pass through unchanged UNLESS hinted,
        # in which case it must become block-shaped to carry the marker.
        if hinted and content:
            if isinstance(content, list):
                # Already block-shaped: copy the list AND the final block so the
                # caller's dicts are never mutated, then mark the copy.
                blocks = list(content)
                if isinstance(blocks[-1], dict):
                    blocks[-1] = _mark_block(dict(blocks[-1]))
                out.append({"role": role, "content": blocks})
            else:
                out.append(
                    {
                        "role": role,
                        "content": [_mark_block({"type": "text", "text": str(content)})],
                    }
                )
            continue
        out.append({"role": role, "content": content})

    # Deliver any volatile notes on the last user turn, in order — after the stable
    # context and every breakpoint, so the served prompt's prefix stays stable across
    # turns. Empty when no message was tagged, keeping the untagged path
    # byte-identical.
    _lay_system_notes(out, volatile_notes)

    system_text = "\n\n".join(system_parts)
    if system_hinted:
        # BLOCK-SHAPED system=: ONE text block carrying the exact string the
        # unhinted path returns, with the breakpoint on it. One block, not one per
        # part — the hinted system message is by construction the LAST hoisted one
        # (the neutral marker picks the last non-tool, non-volatile message), so a
        # single block puts the breakpoint at the same place while keeping the served
        # text identical to the string form.
        return [_mark_block({"type": "text", "text": system_text})], out
    return system_text, out


class AnthropicProvider(ModelProvider):
    """ModelProvider backed by the Anthropic Messages API.

    The ``anthropic`` SDK is imported inside ``__init__`` so the package
    ``personalclaw.providers`` can be imported without pulling the SDK into
    ``sys.modules``.
    """

    # The Messages API accepts a multi-message history + tool schemas; the
    # native loop drives tool-enabled turns via complete(), which translates
    # the OpenAI-shaped messages/tools it receives into Anthropic's wire format.
    supports_tools: bool = True

    # Graded prompt-cache posture. Anthropic needs an EXPLICIT per-request
    # breakpoint — it does not cache a stable prefix on its own — so the native loop
    # places a neutral hint (it reads this attr by getattr, exactly as it reads
    # supports_tools) and `_translate_messages` above turns that hint into
    # ``cache_control``. A provider that leaves this NONE never sees a marker.
    prompt_cache: PromptCache = PromptCache.EXPLICIT

    # Its model's thinking blocks are handed back on later requests (``llm.reasoning_record``), so
    # the native loop leaves the reasoning record on the messages it sends here.
    reads_reasoning: bool = True

    def __init__(
        self,
        *,
        model: str,
        credential: Credential | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
        extra_options: dict[str, object] | None = None,
    ) -> None:
        # Lazy import on purpose. Do NOT lift to module top.
        # anthropic is an OPTIONAL SDK — require_sdk raises a clear
        # MissingSDKError naming `pip install personalclaw[anthropic]` when absent.
        anthropic = require_sdk("anthropic", "anthropic", feature="the Anthropic chat provider")

        if credential is None or not credential.secret:
            raise CredentialMissing(
                "AnthropicProvider requires a credential with a populated secret"
            )

        self._anthropic_module = anthropic
        self._model = model
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._extra_options: dict[str, object] = dict(extra_options or {})
        # The served context window this binding DECLARES (``entry.options``'
        # ``context_window``) — the escape hatch for a self-hosted endpoint whose real
        # window is neither the model's architectural maximum nor the conservative local
        # default. POPPED like ``max_tokens``: whatever is left in _extra_options is
        # forwarded into the SDK request kwargs, and ``context_window`` is not a wire
        # parameter. ``None`` = undeclared.
        self.context_window: int | None = _declared_window(
            self._extra_options.pop("context_window", None)
        )
        # ``base_url`` lets Anthropic-compatible endpoints (proxies, gateways,
        # self-hosted relays) be reached through the same provider. When None,
        # the SDK uses the official api.anthropic.com base.
        client_kwargs: dict[str, Any] = {"api_key": credential.secret}
        if base_url:
            client_kwargs["base_url"] = base_url
        # Every request the SDK sends asks the egress guard first, each redirect hop included,
        # under the owner's Network egress settings: a host on Denied hosts is never contacted.
        # The agent's model is shared by every run, so no run's egress tier holds it. The client
        # follows redirects as the SDK's own does, and a refusal is raised as an
        # ``AnthropicError`` too, which the SDK treats as final, not as a failed send to retry.
        self._client: Any = anthropic.AsyncAnthropic(
            **client_kwargs,
            http_client=http_client(
                endpoint=base_url or "",
                model_provider=True,
                shared_by_every_run=True,
                refused_as=anthropic.AnthropicError,
                follow_redirects=True,
            ),
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
        """The ``temperature`` a ``stream()`` request carries from ``extra_options``.

        ``None`` when the model refuses a custom temperature (see :attr:`unsent_options`).
        ``complete()`` with a reasoning effort turns extended thinking on, which forbids a custom
        temperature and drops it — a per-turn fact about the native loop's path, which one-shot
        sampling never takes.
        """
        if "temperature" in self.unsent_options:
            return None
        return wire_temperature(self._extra_options.get("temperature"))

    @property
    def unsent_options(self) -> dict[str, str]:
        """The sampling options a ``stream()`` request leaves off because the model refuses them."""
        return self._unsent(self._model, thinking=False)

    def _unsent(self, model: str, *, thinking: bool) -> dict[str, str]:
        """The extra options a request to ``model`` leaves off, each with the reason.

        ``temperature`` is asked about first, so a model that takes a temperature or a top_p (not
        both) keeps the temperature core asked for.
        """
        requested = [p for p in SAMPLING_PARAMETERS if p in self._extra_options]
        unsent = refused_sampling(model, requested)
        if thinking and "temperature" in self._extra_options and "temperature" not in unsent:
            unsent["temperature"] = "extended thinking does not accept a custom temperature"
        return unsent

    def _add_options(self, request_kwargs: dict[str, Any], *, unsent: dict[str, str]) -> None:
        """Put this instance's extra options on the request as BODY fields.

        Through ``extra_body``, which the SDK merges into the request JSON as-is, and not as SDK
        keywords: ``anthropic`` 1.x dropped ``temperature``, ``top_p`` and ``top_k`` from its
        method signatures, so a keyword raised ``TypeError`` before any request went out (0.x
        and 1.x both take ``extra_body``). A field the request already carries stays
        authoritative (``system``, ``tools``, ``messages``), and an option in ``unsent`` is left
        off.
        """
        body = {
            key: value
            for key, value in self._extra_options.items()
            if key not in request_kwargs and key not in unsent
        }
        if body:
            request_kwargs["extra_body"] = body

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
        """Stream a turn (:meth:`_stream_chat`), closable by :meth:`cancel`."""
        async with closing_stream(self._requests.relay(self._stream_chat(message))) as events:
            async for event in events:
                yield event

    async def _stream_chat(self, message: str) -> AsyncIterator[LLMEvent]:
        """Stream a Messages turn; translate deltas to :class:`LLMEvent`.

        Anthropic emits a sequence of typed events:

        * ``message_start`` carries ``message.usage.input_tokens``.
        * ``content_block_start`` opens a block (``text`` or ``tool_use``).
          For ``tool_use`` we record the block's ``id`` and ``name``.
        * ``content_block_delta`` carries ``text_delta`` /
          ``input_json_delta`` / ``thinking_delta`` payloads. Text and
          thinking are emitted immediately; tool-input JSON is
          accumulated per block index.
        * ``content_block_stop`` finalizes a block.
        * ``message_delta`` carries the message's ``stop_reason`` and cumulative
          ``usage.output_tokens``. It ends the answer, and each ``tool_use`` block is
          then emitted as one :data:`EVENT_TOOL_CALL` with its accumulated JSON
          string; a stream that ends before it raises ``AnswerCutOff``
          (``until_terminal``) and emits none.

        SDK runtime types live under ``anthropic.types``; we use
        duck-typed ``getattr`` access here so the module never imports
        them at load time (Property 11).
        """
        model = require_model(self._model)
        self._history.append({"role": "user", "content": message})
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._history,
            "max_tokens": self._max_tokens,
        }
        self._add_options(request_kwargs, unsent=self._unsent(self._model, thinking=False))

        assistant_text = ""
        # Per content-block-index accumulators for tool_use blocks.
        tool_blocks: dict[int, dict[str, str]] = {}
        #: The provider's own reason the message ended, once `message_delta` reports it, carried
        #: on every tool call: `"max_tokens"` is what makes a block whose JSON never closed a
        #: TRUNCATION rather than a malformed call, which the runtime could not otherwise tell.
        stop_reason = ""

        input_tokens = 0
        output_tokens = 0
        cache_creation_tokens = 0
        cache_read_tokens = 0

        async with self._client.messages.stream(**request_kwargs) as stream:
            async for event in until_terminal(
                stream, ends=_ends_the_answer, adapter=_ADAPTER, missing=_STOP, model=model
            ):
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
                        # Prompt-cache usage: the event fields exist but had no
                        # producer. `_read_cache_usage` reads them defensively — a response
                        # without cache use (or an older SDK) leaves these 0 and never raises.
                        cache_creation_tokens, cache_read_tokens = _read_cache_usage(usage)

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

                elif event_type == "message_delta":
                    reason = _stop_reason_of(event)
                    if reason:
                        stop_reason = reason
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        ot = getattr(usage, "output_tokens", None)
                        if ot is not None:
                            output_tokens = ot

                # ``message_stop`` is informational; the ``async with``
                # context exit handles teardown.

        for event in _tool_call_events(tool_blocks, stop_reason):
            yield event

        if input_tokens > 0:
            # ``override=`` and deliberately NOT ``local=``: the binding's declaration is
            # truth for a measured percentage, while the conservative local floor is only
            # a denominator for the char ESTIMATE — dividing a real token count by it
            # would over-report (see the matching note in llm/openai.py, #2364).
            # ``sent_chars`` keeps the reading monotone — see ``context_gauge``.
            self._last_context_pct = self._gauge.measure(
                reported_tokens=input_tokens,
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
        """Stream a stateless Messages turn for the full ``messages`` list.

        Unlike :meth:`stream`, this NEVER touches ``self._history`` — the
        native loop owns conversation state. The loop sends OpenAI-shaped
        messages + tools uniformly across providers; this method translates
        them into Anthropic's wire format (see :func:`_translate_messages` /
        :func:`_translate_tools`): a system message becomes the top-level
        ``system=`` param, assistant ``tool_calls`` become ``tool_use``
        content blocks, and ``role: "tool"`` results become ``tool_result``
        blocks inside a user turn.

        Streaming + token accumulation mirror :meth:`stream` exactly; once the
        message says how it ended, each ``tool_use`` block emits one
        :data:`EVENT_TOOL_CALL` with ``tool_call_id`` = the block id, ``title`` =
        the tool name, and ``tool_input`` = the accumulated JSON argument string.
        A stream that ends before that raises ``AnswerCutOff`` and emits none.
        """
        served_model = require_model(model or self._model)
        # Thinking blocks go back only to the model that wrote them, and never to a model that
        # binds them to an exact prefix (``catalog.returns_thinking``): the runtime edits earlier
        # turns between requests, and leaving every block off is always a valid request.
        system_prompt, anth_messages = _translate_messages(
            messages, thinking_from=served_model if returns_thinking(served_model) else ""
        )

        request_kwargs: dict[str, Any] = {
            "model": served_model,
            "messages": anth_messages,
            "max_tokens": self._max_tokens,
        }
        if system_prompt:
            request_kwargs["system"] = system_prompt
        if tools:
            request_kwargs["tools"] = _translate_tools(tools)
        # The session's reasoning effort, in the shape this model takes it (``_thinking_request``).
        # "" = no thinking fields at all: a model that thinks adaptively then does so at its own
        # default. A budget-shape request that continues a tool call whose assistant turn carries
        # no thinking block of this model's is refused with thinking on, so that request goes
        # without it.
        reasoning = _thinking_request(served_model, reasoning_effort, self._max_tokens)
        if reasoning.get("thinking", {}).get("type") == "enabled" and _continues_without_thinking(
            anth_messages
        ):
            reasoning = {}
        request_kwargs.update(reasoning)
        # A thinking request takes no custom temperature, so a thinking turn drops it too.
        unsent = self._unsent(served_model, thinking="thinking" in request_kwargs)
        self._add_options(request_kwargs, unsent=unsent)

        # Per content-block-index accumulators for tool_use blocks.
        tool_blocks: dict[int, dict[str, str]] = {}
        # Per content-block-index thinking blocks, built as the model writes them and kept as
        # received, an empty thinking text included: the signature carries the reasoning.
        thinking_blocks: dict[int, dict[str, Any]] = {}
        #: The provider's own reason the message ended — see the note at the streaming twin.
        stop_reason = ""

        input_tokens = 0
        output_tokens = 0
        cache_creation_tokens = 0
        cache_read_tokens = 0

        async with self._client.messages.stream(**request_kwargs) as stream:
            async for event in until_terminal(
                stream,
                ends=_ends_the_answer,
                adapter=_ADAPTER,
                missing=_STOP,
                model=str(request_kwargs["model"]),
            ):
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
                        # Prompt-cache usage: the event fields exist but had no
                        # producer. `_read_cache_usage` reads them defensively — a response
                        # without cache use (or an older SDK) leaves these 0 and never raises.
                        cache_creation_tokens, cache_read_tokens = _read_cache_usage(usage)

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
                    elif block_type == "thinking":
                        thinking_blocks[index] = {
                            "type": "thinking",
                            "thinking": str(getattr(block, "thinking", "") or ""),
                            "signature": str(getattr(block, "signature", "") or ""),
                        }
                    elif block_type == "redacted_thinking":
                        thinking_blocks[index] = {
                            "type": "redacted_thinking",
                            "data": str(getattr(block, "data", "") or ""),
                        }

                elif event_type == "content_block_delta":
                    index = getattr(event, "index", 0) or 0
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", None) if delta is not None else None
                    if delta_type == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
                    elif delta_type == "input_json_delta":
                        partial = getattr(delta, "partial_json", "") or ""
                        bucket = tool_blocks.get(index)
                        if bucket is not None and partial:
                            bucket["arguments"] += partial
                    elif delta_type == "thinking_delta":
                        thinking = getattr(delta, "thinking", "") or ""
                        held = thinking_blocks.get(index)
                        if held is not None and held["type"] == "thinking":
                            held["thinking"] += thinking
                        if thinking:
                            yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=thinking)
                    elif delta_type == "signature_delta":
                        held = thinking_blocks.get(index)
                        if held is not None and held["type"] == "thinking":
                            held["signature"] += str(getattr(delta, "signature", "") or "")

                elif event_type == "message_delta":
                    reason = _stop_reason_of(event)
                    if reason:
                        stop_reason = reason
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        ot = getattr(usage, "output_tokens", None)
                        if ot is not None:
                            output_tokens = ot

        for event in _tool_call_events(tool_blocks, stop_reason):
            yield event

        # The answer's reasoning, as opaque model data stamped with the model that wrote it
        # (``llm.reasoning_record``): the native loop keeps it on the assistant message and hands
        # it back, and ``_translate_messages`` decides which requests carry it.
        reasoning_record = (
            {"model": served_model, "blocks": [b for _i, b in sorted(thinking_blocks.items())]}
            if thinking_blocks
            else None
        )

        context_pct: float | None = None
        if input_tokens > 0:
            # ``override=`` only, and ``sent_chars`` — see the note at the streaming
            # gauge above for both.
            context_pct = self._gauge.measure(
                reported_tokens=input_tokens,
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
            reasoning=reasoning_record,
        )

    # ── Tool approval (no-op) ─────────────────────────────────────────

    async def approve_tool(self, request_id: str | int) -> None:
        """No-op: Anthropic tool calls are not interactive at this layer."""
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        """No-op: Anthropic tool calls are not interactive at this layer."""
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


# The provider TYPE registration (ANTHROPIC_CAPABILITY + factory + create_provider)
# lives in the standalone anthropic-models app (apps/anthropic-models/provider.py),
# which imports this AnthropicProvider class via personalclaw.sdk.model. This module is
# now just the Anthropic-compatible PROTOCOL client — a core-supported standard.
