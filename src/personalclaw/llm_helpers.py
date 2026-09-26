"""Shared LLM interaction helpers — stream collection, JSON parsing, history saving.

Eliminates duplicate code across gateway, handler, dashboard, subagent,
and history modules.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

from personalclaw.hooks import fire_tool_hooks, get_global_hook_store
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
    ModelProvider,
)
from personalclaw.sel import sel as _sel

_PROMPT_BUSY_RETRIES = 2
_PROMPT_BUSY_DELAY = 1.5  # seconds between retries

_ChainResult = TypeVar("_ChainResult")


class PromptBusyExhaustedError(Exception):
    """Provider was shut down after prompt-busy retries were exhausted."""


if TYPE_CHECKING:
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import HookManager

logger = logging.getLogger(__name__)


# ── Tool Approval Policies ──


class ToolApprovalPolicy(Enum):
    """How to handle tool permission requests during streaming."""

    AUTO_APPROVE = "auto_approve"
    REJECT_ALL = "reject_all"
    HOOK_BASED = "hook_based"


# Callback type for custom tool approval logic
OnPermissionCallback = Callable[[LLMEvent], Awaitable[bool]]


# ── Stream and Collect ──


async def stream_and_collect(
    provider: ModelProvider,
    message: str,
    *,
    approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.AUTO_APPROVE,
    hooks: "HookManager | None" = None,
    on_chunk: Callable[[str], None] | None = None,
    on_tool_approval: Callable[[LLMEvent], Awaitable[bool]] | None = None,
    on_complete: Callable[[LLMEvent], None] | None = None,
) -> str:
    """Stream a message through an LLM provider and collect the full response.

    This is the core pattern used by cron, heartbeat, subagent, consolidator,
    and title generation.

    Args:
        provider: The LLM provider to stream through.
        message: The prompt to send.
        approval_policy: How to handle tool permission requests.
        hooks: HookManager for HOOK_BASED approval policy.
        on_chunk: Optional callback invoked with each text chunk (for progress).
        on_tool_approval: Optional async callback for interactive approval.
        on_complete: Optional callback invoked with the terminal ``EVENT_COMPLETE``
            event (which carries the turn's token counts + cost) just before the
            text is returned — the seam the cost/token ledger's non-``run_chat``
            write-sites use (COST-AND-TOKEN-OBSERVABILITY C2). Default ``None``
            leaves the streamed text byte-identical for every other caller. Never
            raises into the turn: a callback fault is swallowed.

    Returns:
        The complete response text.
    """
    from personalclaw.acp.errors import AcpError

    for attempt in range(_PROMPT_BUSY_RETRIES + 1):
        result_text = ""
        try:
            async for event in provider.stream(message):
                if event.kind == EVENT_TEXT_CHUNK:
                    result_text += event.text
                    if on_chunk:
                        on_chunk(event.text)
                elif event.kind == EVENT_PERMISSION_REQUEST:
                    approved = await _resolve_permission(
                        provider, event, approval_policy, hooks, on_tool_approval
                    )
                    if not approved:
                        continue
                elif event.kind == EVENT_TOOL_CALL:
                    # Fire PreToolUse hooks for auto-approved tools (informational only)
                    _sel().log_tool_invocation(
                        session_key="",
                        source="llm_helpers",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="auto_approved",
                    )
                    await fire_tool_hooks(
                        get_global_hook_store(),
                        event.title,
                        event.tool_input,
                    )
                elif event.kind == EVENT_COMPLETE:
                    if on_complete is not None:
                        try:
                            on_complete(event)
                        except Exception:  # noqa: BLE001 — telemetry must never break a turn
                            logger.debug("stream_and_collect on_complete failed", exc_info=True)
                    break
            return result_text
        except AcpError as exc:
            if "already in progress" not in str(exc) or attempt >= _PROMPT_BUSY_RETRIES:
                if "already in progress" in str(exc):
                    # Provider is permanently stuck — kill it so the next
                    # get_or_create cold-starts a fresh process.
                    logger.warning(
                        "Prompt busy after %d retries, shutting down provider", _PROMPT_BUSY_RETRIES
                    )
                    try:
                        await provider.shutdown()
                    except Exception:
                        logger.debug("Provider shutdown after busy retries failed", exc_info=True)
                    raise PromptBusyExhaustedError(str(exc)) from exc
                raise
            logger.warning(
                "Prompt busy (attempt %d/%d), cancelling and retrying: %s",
                attempt + 1,
                _PROMPT_BUSY_RETRIES,
                exc,
            )
            try:
                await provider.cancel()
            except Exception:
                logger.debug("Cancel before retry failed", exc_info=True)
            await asyncio.sleep(_PROMPT_BUSY_DELAY * (2**attempt))
    return ""  # unreachable, satisfies type checker


async def stream_and_collect_json(
    provider: ModelProvider,
    message: str,
    *,
    approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.AUTO_APPROVE,
    hooks: "HookManager | None" = None,
) -> dict | None:
    """Stream a message and parse the response as JSON.

    Combines ``stream_and_collect`` with ``parse_llm_json``.
    Returns parsed dict or None on failure.
    """
    text = await stream_and_collect(provider, message, approval_policy=approval_policy, hooks=hooks)
    return parse_llm_json(text)


async def _resolve_permission(
    provider: ModelProvider,
    event: LLMEvent,
    policy: ToolApprovalPolicy,
    hooks: "HookManager | None",
    on_tool_approval: Callable[[LLMEvent], Awaitable[bool]] | None = None,
    session_key: str = "",
    agent: str = "",
) -> bool:
    """Resolve a tool permission request. Returns True if approved."""
    from personalclaw.hooks import TOOL_AUTO_APPROVE, TOOL_DENY
    from personalclaw.sel import sel

    def _log(outcome: str, **extra):
        sel().log_tool_invocation(
            session_key=session_key,
            agent=agent,
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome=outcome,
            request_id=event.request_id,
            **extra,
        )

    if policy == ToolApprovalPolicy.REJECT_ALL:
        await provider.reject_tool(event.request_id)
        _log("rejected", metadata={"reason": "reject_all_policy"})
        return False

    if policy == ToolApprovalPolicy.HOOK_BASED and hooks:
        tool_result = hooks.on_tool_call(event.title)
        if tool_result.action == TOOL_DENY:
            await provider.reject_tool(event.request_id)
            _log("denied", error=tool_result.reason)
            return False
        if tool_result.action == TOOL_AUTO_APPROVE:
            await provider.approve_tool(event.request_id)
            _log("auto_approved", metadata={"reason": "hook_auto_approve"})
            return True

    # Interactive approval if callback provided
    if on_tool_approval:
        approved = await on_tool_approval(event)
        if not approved:
            await provider.reject_tool(event.request_id)
            _log("rejected", metadata={"reason": "interactive_rejected"})
            return False

    # Default: auto-approve
    await provider.approve_tool(event.request_id)
    _log("auto_approved")
    return True


# ── JSON Parsing ──


def _parse_llm(text: str, expected_type: type) -> dict | list | None:
    """Parse JSON from LLM output, stripping markdown fences if present."""
    text = text.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
        if isinstance(data, expected_type):
            return data  # type: ignore[return-value]
        return None
    except json.JSONDecodeError:
        logger.debug("Failed to parse LLM JSON: %.200s", text)
        return None


def parse_llm_json(text: str) -> dict | None:
    """Parse JSON dict from LLM output, stripping markdown fences if present."""
    return _parse_llm(text, dict)  # type: ignore[return-value]


def parse_llm_json_list(text: str) -> list | None:
    """Parse a JSON array from LLM output, stripping markdown fences."""
    return _parse_llm(text, list)  # type: ignore[return-value]


# ── Conversation History Helpers ──


def save_conversation_turn(
    log: "ConversationLog",
    key: str,
    user_text: str,
    assistant_text: str,
    source_thread: str | None = None,
    source_user: str | None = None,
) -> None:
    """Save a user+assistant conversation turn to the history log.

    Consolidates the repeated pattern of appending user and assistant
    messages with provenance tracking.
    """
    log.append(
        key,
        "user",
        user_text,
        source_thread=source_thread,
        source_user=source_user,
    )
    if assistant_text:
        log.append(
            key,
            "assistant",
            assistant_text,
            source_thread=source_thread,
            source_user=source_user,
        )


def _enforces_json_schema_natively(model_ref: str) -> bool:
    """Does the provider behind ``model_ref`` enforce a supplied JSON Schema NATIVELY?

    The gate on forwarding ``output_type`` as a build kwarg (AUTONOMY-GUARDRAILS §2.4,
    AG-9). It asks a narrow question about ONE entry and answers ``False`` to every
    doubt, because a false positive here does not degrade to a weaker constraint — it
    corrupts the request. Build kwargs land in the provider's ``extra_options``, and both
    wire clients copy that bag onto the request body (``llm/openai.py`` assigns
    unconditionally, ``llm/anthropic.py`` ``setdefault``s). A provider that does not POP
    ``output_type`` therefore hands a Python ``type`` to the JSON encoder and dies with an
    opaque ``TypeError`` from the HTTP client — a call that works today would start
    failing. So only a provider that ADVERTISED the capability is sent the key; the ollama
    app pops it (``resolve_output_format``) and normalizes it into ollama's own ``format``.

    ``JSON_SCHEMA`` ONLY. ``JSON_MODE`` is deliberately excluded, and the comparison below
    names the grade rather than spelling it ``!= NONE`` so the code states that decision.
    A ``JSON_MODE`` provider speaks OpenAI-wire ``response_format={"type":
    "json_object"}`` — a DIFFERENT request field that no adapter derives from
    ``output_type``, so the key would ride through unconsumed (the corruption above)
    instead of being translated. It also cannot express ``output_type=list`` at all
    (``json_object`` mode requires an object), so for half of this function's real inputs
    the weaker grade is not weaker, it is wrong. Reaching ``JSON_MODE`` is genuine work —
    its own ``response_format`` build kwarg plus an adapter that consumes it — not a
    loosened comparison here.

    Contrast ``workflows/grounding.py``, which asks ``!= NONE``: that answers a different
    question — "does schema-constrained emission exist ANYWHERE in this install" — for
    which ``JSON_MODE`` legitimately counts.

    The comparison is ``==`` rather than ``is`` because :class:`StructuredOutput` is a
    ``str`` enum: that accepts both the enum member and its own ``"json_schema"`` spelling
    (an app declaring the grade as a bare string still gets its capability honoured) and
    nothing else — no other grade or value compares equal, so the acceptance set widens
    without admitting a false positive.

    Everything unverifiable degrades to ``False``: an unqualified ref (a bare model id,
    including the ``"gpt-oss:20b"`` shape whose colon is not a provider prefix — the
    ``get_entry`` lookup rejects it exactly as the bridge does), the legacy
    ``"Provider/model"`` slash spelling, an unregistered provider type, an unbootstrapped
    registry, or ``""`` (the unbound-axis plain path, where the implicit fallback has not
    picked a provider yet). In every one of those cases core sends no constraint and the
    universal parse-with-targeted-retry net in ``_run`` owns the contract — byte-for-byte
    today's behaviour.
    """
    try:
        from personalclaw.llm.capabilities import StructuredOutput
        from personalclaw.llm.registry import get_default_registry
        from personalclaw.providers.use_cases import split_ref

        parsed = split_ref(model_ref)
        if not parsed:
            return False
        registry = get_default_registry()
        # get_entry raises for a name that is not a registered entry, which is exactly
        # how the bridge distinguishes a "Provider:model" ref from a bare id whose model
        # name happens to contain a colon. Same rule, same outcome.
        cap = registry.capability_of(registry.get_entry(parsed[0]).type)
        grade = getattr(cap, "structured_output", StructuredOutput.NONE)
        return grade == StructuredOutput.JSON_SCHEMA
    except Exception:  # noqa: BLE001 — an unverifiable capability is NOT a capability
        logger.debug(
            "one_shot_completion: no native schema enforcement resolvable for %r", model_ref
        )
        return False


# ── Call-failure chain advance (MODEL-USE-CASES-V2 T2.4) ─────────────────────
# ONE walk, shared by every NON-INTERACTIVE consumer of the use-case chain. It lives
# here because ``one_shot_completion`` was the first consumer, not because it is the
# only one: the direct ``resolve_provider_for_use_case`` consumers on the
# non-interactive axes (the knowledge-pipeline nodes, the loop stage-gate judge) need
# exactly this walk, and a second hand-rolled copy of it would be a divergence defect
# — two answers to "should we try the next model" drifting apart.
#
# The INTERACTIVE chat/code_tools stream is deliberately NOT a consumer: it advances at
# call-start only, via the seam's own resolution-time chain walk (the breaker-OPEN skip
# in ``provider_bridge.resolve_provider_for_use_case``). See that seam's comment for
# why — a human-watched turn must not stack N provider timeouts, and its provider is a
# NativeAgentRuntime holding per-turn tool/transcript state that cannot be rebuilt
# mid-stream.


def use_case_chain(use_case: str) -> list[str]:
    """The ordered resolution chain for ``use_case``, or ``[]`` when unreadable.

    A tolerant read of :func:`personalclaw.providers.use_cases.resolution_chain` that
    NEVER raises: a missing or corrupt ``active_models.json`` must leave the caller on
    its plain single-resolution path, not fail the completion.

    Callers gate on ``len(chain) > 1`` themselves rather than handing a one-entry chain
    to :func:`run_over_use_case_chain`, and that is deliberate: "a one-entry chain takes
    today's plain path" is a rule about WHICH resolution call is made. The walk passes
    ``model_override=<ref>``; a plain resolve passes none, which additionally admits the
    implicit-capability fallback and (in ``one_shot_completion``) the last-resort
    registry build. Collapsing the two would silently change single-binding behaviour.

    The composer/session override is deliberately NOT plumbed through here. Every
    consumer of this walk is NON-INTERACTIVE (one-shot completions, the knowledge
    pipeline nodes, the loop stage-gate judge), and a session override is a property
    of an interactive composer turn — there is no session to override on these axes.
    Forwarding it would also widen ``resolution_chain``'s call shape for no caller.
    """
    try:
        from personalclaw.providers.use_cases import resolution_chain

        return list(resolution_chain(use_case))
    except Exception:  # noqa: BLE001 — an unreadable chain degrades to the plain path
        logger.debug("use_case_chain: chain unreadable for %r", use_case, exc_info=True)
        return []


async def run_over_use_case_chain(
    use_case: str,
    chain: list[str],
    run: Callable[[Any], Awaitable[_ChainResult]],
    *,
    entry_kwargs: Callable[[str], Awaitable[dict]] | None = None,
    no_advance: tuple[type[BaseException], ...] = (),
    label: str = "chain",
) -> _ChainResult:
    """Run ``run`` against ``chain`` entry 0, advancing to N+1 on a call failure.

    The call-failure half of the fallback chain (MODEL-USE-CASES-V2 T2.4), complementing
    the seam's resolution-time breaker skip: a ``CircuitOpenError``/provider failure from
    entry N rebuilds from entry N+1 — once per remaining entry, bounded by chain length —
    so a declared fallback actually serves a call that a live provider dropped mid-flight
    rather than only one that was already known-down at resolution time.

    ``run`` receives one resolved provider and owns its whole lifecycle (start / stream /
    shutdown); it must RAISE on failure, because a swallowed error is indistinguishable
    from a good answer and would pin the walk to entry 0 forever.

    ``entry_kwargs`` derives the per-ENTRY build kwargs (budget/constraint) for the ref
    about to run — per entry, not once, because the walk can advance from a capable model
    to an incapable one mid-call.

    ``no_advance`` names the exception types that must NOT burn the chain. The canonical
    member is ``OutputContractError``: the model RESPONDED, so a contract miss is a prompt
    problem, and walking a whole chain of models for it would spend N calls on the same
    bad prompt. It is re-raised unchanged.

    An exhausted chain raises ONE ``RuntimeError`` naming the axis, the chain length and
    the last error — one clear error, not N stack traces.
    """
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

    last_exc: Exception | None = None
    for i, ref in enumerate(chain):
        try:
            kw = await entry_kwargs(ref) if entry_kwargs is not None else {}
            provider = resolve_provider_for_use_case(use_case, model_override=ref, **kw)
        except Exception as exc:  # noqa: BLE001 — an unbuildable entry advances
            last_exc = exc
            continue
        try:
            return await run(provider)
        except no_advance:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed call advances
            last_exc = exc
            if i + 1 < len(chain):
                logger.warning(
                    "%s advance: %s entry %d (%s) failed (%s) — trying next",
                    label,
                    use_case,
                    i,
                    ref,
                    type(exc).__name__,
                )
    raise RuntimeError(
        f"every model in the {use_case!r} fallback chain failed "
        f"({len(chain)} entr{'y' if len(chain) == 1 else 'ies'}); "
        f"last error: {last_exc}"
    ) from last_exc


async def one_shot_completion(
    prompt: str,
    *,
    use_case: str = "background",
    output_type: type | None = None,
    model: str = "",
    temperature: float | None = None,
) -> str:
    """Send a single prompt to the system's configured LLM and return the response.

    Resolves the provider through the same use-case bridge the chat path uses —
    which reads the active model selection from ``active_models.json`` (Settings →
    Models) — then builds a temporary instance, streams the response, and returns
    the collected text. The resolved provider is wrapped in the model-call guard
    (circuit breaker + hard timeout + attempt audit) at the bridge seam.

    ``use_case`` names a chat sub-category axis (MODEL-USE-CASES-V2):
    ``"background"`` IS a real axis now (titles/tags/suggestions/digests/
    consolidation route through it, falling back to the active ``chat`` chain when
    unbound), as are ``"reasoning"``, ``"loops"``, and ``"orchestration"``. The
    remaining informal label ``"ingestion"`` collapses to ``"background"``; anything
    unrecognized collapses to ``"reasoning"``. ``chat``/``code_tools`` are never
    used here — they route a native agent through the in-process agent runtime,
    but a one-shot completion wants a plain model provider.

    On a chain with fallbacks, a ``CircuitOpenError``/provider failure from entry N
    advances to entry N+1 for this call (bounded by chain length) — the
    call-failure walk that complements the seam's resolution-time breaker skip.

    ``output_type`` (AUTONOMY-GUARDRAILS §2.4) opts into typed structured output:
    pass ``dict`` or ``list`` to require the response parse as that JSON shape.
    On a parse miss the call is retried ONCE with a targeted correction note
    injected (the dominant real-world cause is the schema not being visible), and
    if it still fails an :class:`~personalclaw.guardrails.failure.OutputContractError`
    is raised — replacing the silent ``None`` degrade that ``parse_llm_json``
    returned at every call site. Returns the raw text unchanged when ``output_type``
    is ``None`` (the response is still a ``str``; typed callers parse the returned
    text, e.g. via ``json.loads``).

    ``output_type`` ALSO rides the bridge as a build kwarg (AG-9) — but only to an entry
    whose provider advertised ``StructuredOutput.JSON_SCHEMA``, so a natively capable
    provider CONSTRAINS generation instead of merely being asked nicely in prose. The
    ollama app is the first consumer: it pops the key and normalizes ``dict``/``list``
    into ollama's own ``format=<schema>``. The gate is per ENTRY, not per call, because a
    chain can advance from a capable model to an incapable one mid-call; a provider that
    never advertised the capability is sent NOTHING, because build kwargs reach the
    request body and an unconsumed key there is a corrupt request, not a soft no-op.
    ``JSON_MODE`` is deliberately excluded — see :func:`_enforces_json_schema_natively`.
    The native constraint LAYERS with the parse-and-retry above rather than replacing it:
    constrained decoding can still return a valid-but-empty document, so dropping the
    parse would turn a caught failure into a silent one.

    ``model`` PINS resolution to one concrete model (a ``"Provider:model_id"`` ref,
    or a bare id), bypassing the use case's active-selection CHAIN — a pin is not a
    chain. This is the seam cross-model judge isolation needs (WF2LOO-11): the engine
    resolves a different-FAMILY judge model up front, validates it against the
    producing stage's model, and pins it here so the judge provably runs on the model
    it was checked against. The default ``""`` keeps today's use-case-only resolution
    byte-for-byte.

    ``temperature`` pins the SAMPLING temperature for this one call (HARNESS-CRAFT
    §2.1): it rides the bridge as a build kwarg into the provider's ``extra_options``,
    where both protocol clients already forward call params into the request. This is
    what makes best-of-N sampling genuinely varied rather than N identical calls. Two
    honest caveats: an Anthropic model in extended-thinking mode forbids a custom
    temperature and drops it (``llm/anthropic.py``), and a provider that ignores the
    parameter simply returns its default — a temperature is a request, not a promise.
    ``None`` (the default) sends nothing, keeping every existing call site unchanged.

    The per-call OUTPUT BUDGET (LMMV §2.2) is derived, never hardcoded: every resolution
    path (pin / chain-advance / plain / last-resort) asks
    :func:`personalclaw.local_models.budgets.output_budget` for the model it is about to
    run and rides the answer as a ``max_tokens`` build kwarg. A local model whose card
    declares ``context_tokens``/``output_tokens`` therefore gets ITS window instead of the
    hosted-model table's 200k default and the adapters' hardcoded 4096; a model the
    catalog does not know falls back to that table and, only then, to 4096 — the same
    number as before, now named once in ``local_models/budgets.py``. Because the bridge
    merges build kwargs with ``setdefault``, an operator's configured ``max_tokens`` still
    wins. No compaction logic is involved: this makes the number available, it does not
    decide what to drop.
    """
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case
    from personalclaw.providers.use_cases import VALID_USE_CASES

    # Honor a caller that already named a real model-axis use case; the remaining
    # informal label ("ingestion") collapses to the background axis; anything
    # unrecognized collapses to reasoning (→ chat fallback either way).
    if use_case in VALID_USE_CASES and use_case not in ("chat", "code_tools"):
        resolved_uc = use_case
    elif use_case == "ingestion":
        resolved_uc = "background"
    else:
        resolved_uc = "reasoning"

    from personalclaw.guardrails.failure import OutputContractError

    # A pinned sampling temperature rides EVERY resolution path as a build kwarg
    # (pin / chain-advance / plain / last-resort), so a fallback entry samples at the
    # temperature the caller asked for rather than silently reverting to the provider
    # default.
    _bridge_kw: dict = {} if temperature is None else {"temperature": float(temperature)}

    async def _entry_kw(model_ref: str) -> dict:
        """The build kwargs for the ONE entry ``model_ref`` names: budget + constraint.

        ``max_tokens`` is the per-model output budget DERIVED for ``model_ref``. The
        number comes from the local-model catalog's ``context_tokens`` /
        ``output_tokens`` (LMMV §2.2) via ``local_models.budgets``, which falls back to
        the shared hosted-model window table and, only when that too is silent, to the
        4096 the adapters used to hardcode. Riding it as a build kwarg means it reaches
        the provider through the same seam ``temperature`` uses — and because the bridge
        merges build kwargs with ``setdefault``, an operator's explicitly configured
        ``max_tokens`` still wins over the derived one.

        Fail-soft: an unresolvable budget must not be the thing that stops a completion,
        so the call site degrades to "pass nothing" (the adapter default) rather than
        raising.

        ``output_type`` rides here too (AG-9), and ONLY when this entry's provider
        advertised ``StructuredOutput.JSON_SCHEMA`` — see
        :func:`_enforces_json_schema_natively` for why an unadvertised provider must not
        receive the key. PER ENTRY is the whole point of deciding here rather than once
        up front: every resolution path (pin / chain advance / plain / last-resort) funnels
        through this function WITH the ref it is about to run, and the chain walk can advance
        from a capable entry to an incapable one mid-call. A decision made once would send
        the constraint to a fallback provider that cannot honour it — precisely the
        corrupt-request failure the capability gate exists to prevent.

        The constraint is a REQUEST, never a promise, so it does not replace anything: the
        parse-with-targeted-retry net in ``_run`` still checks every response. Constrained
        decoding is measured in this repo to return valid-but-empty documents, so dropping
        the parse because "the provider guarantees it" would convert a caught failure into
        a silent one. Both layers stay.

        ``output_type=None`` (the overwhelmingly common case) short-circuits before the
        capability lookup, so the kwargs are byte-for-byte what they were and no registry
        work happens on that path.
        """
        kw = dict(_bridge_kw)
        if output_type is not None and _enforces_json_schema_natively(model_ref):
            kw["output_type"] = output_type
        try:
            from personalclaw.local_models.budgets import output_budget

            kw["max_tokens"] = await output_budget(model_ref)
        except Exception:  # noqa: BLE001 — a budget miss degrades, it never blocks
            logger.debug("one_shot_completion: budget derivation failed for %r", model_ref)
        return kw

    async def _run(provider) -> str:
        try:
            await provider.start()
            text = await stream_and_collect(provider, prompt)
            if output_type is None:
                return text
            # Typed path: parse; on a miss, ONE targeted correction-note retry.
            if _parse_llm(text, output_type) is not None:
                return text
            from personalclaw.guardrails.failure import FailureMode, correction_note

            retry_prompt = f"{prompt}\n\n{correction_note(FailureMode.SCHEMA_VIOLATION)}"
            retry_text = await stream_and_collect(provider, retry_prompt)
            if _parse_llm(retry_text, output_type) is not None:
                return retry_text
            raise OutputContractError(
                getattr(output_type, "__name__", str(output_type)), retry_text
            )
        finally:
            try:
                await provider.shutdown()
            except Exception:
                pass

    # A pinned model bypasses the active-selection chain entirely — a pin is not a
    # chain (WF2LOO-11). The caller has already decided WHICH model must run (a
    # cross-model judge validated against the worker's family), so walking the
    # use-case fallback chain would defeat the pin: a fallback entry could be the
    # very family the isolation control excluded. Resolve the one model and run it.
    if model:
        return await _run(
            resolve_provider_for_use_case(
                resolved_uc, model_override=model, **(await _entry_kw(model))
            )
        )

    # Call-failure chain advance (MODEL-USE-CASES-V2 T2.4): with a multi-entry
    # chain declared, a CircuitOpenError/provider failure from entry N advances to
    # entry N+1 for THIS call — once per remaining entry, bounded by chain length.
    # An OutputContractError does NOT advance (the model responded; the contract
    # miss is not a provider outage). A one-entry/empty chain takes the plain
    # resolution path below — today's exact behavior. The walk itself is
    # :func:`run_over_use_case_chain`, shared with the other non-interactive
    # consumers of the chain so there is exactly one answer to "advance?".
    _chain = use_case_chain(resolved_uc)
    if len(_chain) > 1:
        return await run_over_use_case_chain(
            resolved_uc,
            _chain,
            _run,
            entry_kwargs=_entry_kw,
            no_advance=(OutputContractError,),
            label="one_shot chain",
        )

    provider = None
    # The single-entry / empty chain resolves the axis itself, so the budget is derived
    # from the model that axis will actually run (``_chain`` already holds it when there
    # is one) rather than from nothing — an unbound axis falls back to the window table.
    _plain_ref = _chain[0] if _chain else ""
    try:
        provider = resolve_provider_for_use_case(resolved_uc, **(await _entry_kw(_plain_ref)))
    except Exception:
        logger.debug(
            "one_shot_completion: use-case bridge resolve failed for %r", resolved_uc, exc_info=True
        )

    # Last-resort fallback: no active selection AND the bridge couldn't resolve a
    # capable provider — build the first registered provider so a single-provider
    # setup with no explicit selection still works.
    #
    # This is the FOURTH resolution path, and it was the one place a completion still
    # inherited the adapters' hardcoded cap: it called ``build`` with no kwargs at all,
    # so neither the derived budget nor a pinned ``temperature`` reached the provider.
    # ``registry.build`` forwards kwargs to the type factory exactly as the bridge does
    # (``provider_bridge`` builds its own ``build_kwargs`` the same way), so the entry
    # gets the budget derived for the model it is ACTUALLY about to run.
    #
    # Fail-soft, and the retry is the point: this path exists because the bridge already
    # failed, and one reason a bridge resolve fails is a factory that rejects an extra
    # kwarg. Passing kwargs unconditionally would convert today's working degraded build
    # into a hard failure for exactly that provider, so a rejected kwarg falls back to
    # the bare build rather than propagating.
    if provider is None:
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
        entries = registry.list_entries()
        if not entries:
            raise RuntimeError("No provider entries registered")
        fallback = entries[0]
        fallback_ref = f"{fallback.name}:{fallback.model}" if fallback.model else fallback.name
        try:
            provider = registry.build(fallback.name, **(await _entry_kw(fallback_ref)))
        except Exception:  # noqa: BLE001 — an unaccepted build kwarg degrades, never blocks
            logger.debug(
                "one_shot_completion: last-resort build rejected derived kwargs for %r",
                fallback.name,
            )
            provider = registry.build(fallback.name)

    return await _run(provider)


def _failed_endpoint(exc: BaseException) -> str:
    """``" at <host:port>"`` for a transport error that knows its request, else ``""``.

    Only the host and port — never the path or query, which is where a provider that takes
    its key in the URL would carry it. httpx attaches the request to the errors it raises
    while sending; its ``request`` property raises instead of returning ``None`` when it was
    never attached, so the absence is caught rather than tested.
    """
    try:
        url = exc.request.url  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return ""
    host = str(getattr(url, "host", "") or "")
    if not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    port = getattr(url, "port", None)
    return f" at {host}:{port}" if port else f" at {host}"


def _describe_unexplained_failure(exc: object) -> str:
    """The sentence for a failure whose ``str()`` is empty — never an empty string.

    ``httpx.ReadError``, every httpx timeout, ``asyncio.TimeoutError`` and a bare
    ``ConnectionResetError`` all stringify to ``""``, and a turn error is SHOWN as its text:
    an empty string rendered as an error bar with nothing in it, over the WebSocket and on
    disk alike. So the class — and, for httpx, the endpoint — has to say what the message
    did not. A wrapper raised ``from`` a transport error is described by that cause, since the
    cause is what actually failed.
    """
    if exc is None:
        return (
            "The turn failed without reporting an error. Try again; if it keeps failing, "
            "check the gateway log."
        )
    import httpx

    seen: BaseException | None = exc if isinstance(exc, BaseException) else None
    for _ in range(5):
        if seen is None:
            break
        where = _failed_endpoint(seen)
        if isinstance(seen, httpx.ConnectTimeout):
            return (
                f"Timed out connecting to the model provider{where}. Check that it is "
                "running and reachable, then try again."
            )
        if isinstance(seen, (httpx.TimeoutException, TimeoutError)):
            return (
                f"The model provider{where} did not answer in time, so the request timed "
                "out. Wait a moment and try again, or pick a different model."
            )
        if isinstance(seen, (httpx.ConnectError, ConnectionRefusedError)):
            return (
                f"Couldn't connect to the model provider{where}. Check that it is running "
                "and reachable, then try again."
            )
        if isinstance(
            seen, (httpx.NetworkError, httpx.RemoteProtocolError, ConnectionError, EOFError)
        ):
            return (
                f"The connection to the model provider{where} was lost before its reply was "
                "complete. Check that it is still running and reachable, then try again."
            )
        seen = seen.__cause__
    return (
        f"The turn failed with {type(exc).__name__}, and the error carried no message. "
        "Try again; if it keeps failing, check the gateway log."
    )


def humanize_provider_error(exc: object) -> str:
    """Turn a raw LLM-provider exception into a short, actionable user-facing line.

    Providers (Anthropic/OpenAI/…-compatible) surface failures as verbose SDK
    exceptions whose ``str()`` is a JSON-ish blob (e.g. ``Error code: 400 - {'type':
    'error', 'error': {'message': 'Your credit balance is too low…'}}``). Shown raw
    in the chat error bubble that's noise, not guidance. Map the common, recognizable
    classes — billing/credits, auth, rate-limit, model-not-found, overload — to a
    concise hint; pass anything unrecognized through (trimmed) so we never HIDE a
    real error, just clean up the ones we know. Pure string heuristics (provider SDKs
    don't share a typed error taxonomy), matched on the lowercased message.

    Never returns an empty string: an exception with no message is described from its
    class instead (:func:`_describe_unexplained_failure`).

    Two classes are answered BEFORE the matcher, because the matcher would get them wrong:

    * ``PromptExceedsWindow`` is already the user-facing sentence (model, limit, fix). Its
      figures are this turn's own — "1,429 tokens" contains ``429``, which the substring map
      below reads as a rate limit — so it passes through verbatim.
    * ``MemoryError`` from an in-process model is numpy's allocator text ("Unable to allocate
      26.0 GiB for an array with shape (9, 27862, 27862)") — true, and nothing a user can act on.
      Answered before the empty-message rule too, since a bare ``MemoryError()`` is the same
      failure with the same fix.
    """
    from personalclaw.guardrails.failure import PromptExceedsWindow

    if isinstance(exc, PromptExceedsWindow):
        return str(exc)
    if isinstance(exc, MemoryError):
        return (
            "This machine ran out of memory while the model was reading this conversation, so "
            "no reply was produced. Shorten the message or start a new chat — or bind a model "
            "that does not run on this machine in Settings → Models."
        )
    raw = str(exc or "").strip()
    if not raw:
        return _describe_unexplained_failure(exc)
    low = raw.lower()
    # (needle, friendly) — order matters; first match wins.
    _MAP = [
        (
            ("credit balance is too low", "insufficient_quota", "insufficient credit", "billing"),
            "This model's provider account is out of credits/quota. Top it up, or pick a "
            "different model for this chat (the model selector is in the composer).",
        ),
        (
            (
                "rate limit",
                "rate_limit",
                "429",
                "too many requests",
                "overloaded",
                "overloaded_error",
            ),
            "The model provider is rate-limiting or overloaded right now. Wait a moment and "
            "retry, or switch to a different model.",
        ),
        (
            (
                "authentication",
                "invalid api key",
                "invalid x-api-key",
                "401",
                "unauthorized",
                "permission",
                "invalid_api_key",
            ),
            "The model provider rejected the API key (auth failed). Check the key in "
            "Settings → Providers, or pick a different model.",
        ),
        (
            (
                "model not found",
                "does not exist",
                "not_found_error",
                "unknown model",
                "invalid model",
            ),
            "The selected model id isn't valid for this provider. Pick a listed model in "
            "the composer's model selector.",
        ),
    ]
    for needles, friendly in _MAP:
        if any(n in low for n in needles):
            return friendly
    # Unrecognized — return the raw text (trimmed) so no real error is hidden.
    return raw if len(raw) <= 500 else raw[:500] + "…"
