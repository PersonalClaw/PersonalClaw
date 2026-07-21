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


async def one_shot_completion(prompt: str, *, use_case: str = "background") -> str:
    """Send a single prompt to the system's configured LLM and return the response.

    Resolves the provider through the same use-case bridge the chat path uses —
    which reads the active model selection from ``active_models.json`` (Settings →
    Models) — then builds a temporary instance, streams the response, and returns
    the collected text.

    ``use_case`` is an INFORMAL label (``"background"``, ``"ingestion"``) — these
    are not model-axis use cases, so they map to ``"reasoning"``, a chat
    sub-category that falls back to the active ``chat`` model when unpinned. We use
    ``"reasoning"`` rather than ``"chat"`` deliberately: ``chat``/``code_tools``
    route a native agent through the in-process agent runtime, but a one-shot
    completion wants a plain model provider, which the ``reasoning`` axis resolves.
    """
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case
    from personalclaw.providers.use_cases import VALID_USE_CASES

    # Honor a caller that already named a real model-axis use case; otherwise the
    # informal label collapses to the reasoning axis (→ chat fallback).
    resolved_uc = (
        use_case
        if use_case in VALID_USE_CASES and use_case not in ("chat", "code_tools")
        else "reasoning"
    )

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

    try:
        await provider.start()
        return await stream_and_collect(provider, prompt)
    finally:
        try:
            await provider.shutdown()
        except Exception:
            pass


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
