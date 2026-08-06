"""Shared LLM interaction helpers — stream collection, JSON parsing, history saving.

Eliminates duplicate code across gateway, handler, dashboard, subagent,
and history modules.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TYPE_CHECKING

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
            text is returned — the seam the cost/token ledger's non-``_run_chat``
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


async def one_shot_completion(
    prompt: str,
    *,
    use_case: str = "background",
    output_type: type | None = None,
    model: str = "",
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

    ``model`` PINS resolution to one concrete model (a ``"Provider:model_id"`` ref,
    or a bare id), bypassing the use case's active-selection CHAIN — a pin is not a
    chain. This is the seam cross-model judge isolation needs (WF2LOO-11): the engine
    resolves a different-FAMILY judge model up front, validates it against the
    producing stage's model, and pins it here so the judge provably runs on the model
    it was checked against. The default ``""`` keeps today's use-case-only resolution
    byte-for-byte.
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
        return await _run(resolve_provider_for_use_case(resolved_uc, model_override=model))

    # Call-failure chain advance (MODEL-USE-CASES-V2 T2.4): with a multi-entry
    # chain declared, a CircuitOpenError/provider failure from entry N advances to
    # entry N+1 for THIS call — once per remaining entry, bounded by chain length.
    # An OutputContractError does NOT advance (the model responded; the contract
    # miss is not a provider outage). A one-entry/empty chain takes the plain
    # resolution path below — today's exact behavior.
    try:
        from personalclaw.providers.use_cases import resolution_chain

        _chain = resolution_chain(resolved_uc)
    except Exception:
        _chain = []
    if len(_chain) > 1:
        last_exc: Exception | None = None
        for i, ref in enumerate(_chain):
            try:
                entry_provider = resolve_provider_for_use_case(resolved_uc, model_override=ref)
            except Exception as exc:  # noqa: BLE001 — an unbuildable entry advances
                last_exc = exc
                continue
            try:
                return await _run(entry_provider)
            except OutputContractError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed call advances
                last_exc = exc
                if i + 1 < len(_chain):
                    logger.warning(
                        "one_shot chain advance: %s entry %d (%s) failed (%s) — trying next",
                        resolved_uc,
                        i,
                        ref,
                        type(exc).__name__,
                    )
        # The whole chain failed — surface ONE clear error, not N stack traces.
        raise RuntimeError(
            f"every model in the {resolved_uc!r} fallback chain failed "
            f"({len(_chain)} entr{'y' if len(_chain) == 1 else 'ies'}); "
            f"last error: {last_exc}"
        ) from last_exc

    provider = None
    try:
        provider = resolve_provider_for_use_case(resolved_uc)
    except Exception:
        logger.debug(
            "one_shot_completion: use-case bridge resolve failed for %r", resolved_uc, exc_info=True
        )

    # Last-resort fallback: no active selection AND the bridge couldn't resolve a
    # capable provider — build the first registered provider so a single-provider
    # setup with no explicit selection still works.
    if provider is None:
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
        entries = registry.list_entries()
        if not entries:
            raise RuntimeError("No provider entries registered")
        provider = registry.build(entries[0].name)

    return await _run(provider)


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
    """
    raw = str(exc or "").strip()
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
