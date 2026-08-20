"""Follow-up chips (CHAT-CRAFT S3) — after each completed interactive chat turn,
suggest 2-3 short next messages via ONE cheap cancellable background call.

Mirrors ``suggestions.py`` / ``chat_title.py``: the instruction lives in the
bundled ``task-followups`` prompt (bindable in Settings → Prompts), streamed
through the shared background lite session (``BACKGROUND_KEY``) whose provider
build is already ``ModelCallGuard``-wrapped (breaker + timeout + budgets), with
permission requests rejected and the output redacted. It NEVER blocks the turn:
the task is fire-and-forget, stored on ``session._followups_task``, and the next
``run_chat`` dispatch cancels it. When no model is bound the background session
factory raises → caught here → no event fires (the degrade contract), so the turn
completes normally and the FE simply renders no chips.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)

# Cap on the background call — chips are a nicety, never a stall.
_FOLLOWUPS_TIMEOUT_SECS = 20
# How many trailing chars of the exchange to feed the prompt (recency beats breadth).
_USER_CAP = 1000
_REPLY_CAP = 2000


def _followups_enabled() -> bool:
    """Read the chat follow-up-chips config flag (default on)."""
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().dashboard.followup_chips)
    except Exception:
        return True


#: HARNESS-CRAFT §3.3 — a turn only earns the "Check this work" offer when it both DID
#: multi-step work and CLAIMED it finished. Three tool calls is the floor: one or two is
#: a lookup, not a build worth re-verifying.
_CHECK_WORK_MIN_TOOL_CALLS = 3
_COMPLETION_CLAIM = re.compile(
    r"\b(done|complete|completed|finished|implemented|added|created|wrote|updated|fixed|"
    r"working now|all set|ready|shipped|landed|passes|passing|green)\b",
    re.IGNORECASE,
)


def _check_work_offer_enabled() -> bool:
    """Read the 'Check this work' chip config flag (default on)."""
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().dashboard.offer_check_work)
    except Exception:
        return True


def turn_earns_check_work_offer(assistant_text: str, tool_calls: int) -> bool:
    """The §3.3 heuristic, deterministic and free: ≥3 tool calls in the turn AND
    completion language in the reply. No model call — an offer must never cost
    anything, since the user may not click it."""
    if (tool_calls or 0) < _CHECK_WORK_MIN_TOOL_CALLS:
        return False
    return bool(_COMPLETION_CLAIM.search((assistant_text or "")[-_REPLY_CAP:]))


def maybe_offer_check_work(
    state: "DashboardState", session: "_ChatSession", tool_calls: int
) -> None:
    """Broadcast the "Check this work" offer for a just-completed turn, if it earned one.

    OFFER only: this never invokes the ``check-work`` skill. Invocation is always the
    user's click on the chip, which sends "check your work" as a normal message — so the
    cost and latency of verification stay user-consented (§3.3). Synchronous and
    model-free, so it cannot delay or fail the turn. Independent of ``followup_chips``:
    an operator who turned suggestions off may still want the verification offer.
    """
    try:
        if not _check_work_offer_enabled():
            return
        if getattr(session, "is_restricted", False):
            return
        if getattr(session, "_last_turn_errored", False):
            return
        assistant_text = ""
        for m in reversed(session.messages):
            if m.get("role") == "assistant" and (m.get("content") or "").strip():
                assistant_text = m["content"]
                break
        if not turn_earns_check_work_offer(assistant_text, tool_calls):
            return
        state.broadcast_ws(
            "chat_check_work_offer",
            {"session": session.key, "prompt": "check your work", "label": "Check this work"},
        )
    except Exception:
        logger.debug("check-work offer failed for %s", session.key, exc_info=True)


def _build_exchange(session: "_ChatSession") -> str:
    """The last user message + assistant reply tail, as 'role: content' lines."""
    last_user = ""
    last_assistant = ""
    for m in reversed(session.messages):
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant" and not last_assistant:
            last_assistant = content[-_REPLY_CAP:]
        elif role == "user" and not last_user:
            last_user = content[-_USER_CAP:]
        if last_user and last_assistant:
            break
    lines: list[str] = []
    if last_user:
        lines.append(f"user: {last_user}")
    if last_assistant:
        lines.append(f"assistant: {last_assistant}")
    return "\n".join(lines)


def _parse_followups(text: str) -> list[str]:
    """Parse the LLM response into ≤3 short follow-up strings."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("\n")
        text = "\n".join(parts[1:-1] if parts[-1].startswith("```") else parts[1:]).strip()
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Failed to parse followups response: %s", text[:200])
        return []
    if not isinstance(result, list):
        return []
    out: list[str] = []
    for s in result:
        if not isinstance(s, str):
            continue
        s = s.strip()
        if s and len(s) <= 60:
            out.append(s)
    return out[:3]


def _redact(items: list[str]) -> list[str]:
    result: list[str] = []
    for s in items:
        s, _ = redact_exfiltration_urls(s)
        s, _ = redact_credentials(s)
        result.append(s)
    return result


async def _generate_followups(state: "DashboardState", session: "_ChatSession") -> list[str]:
    """Run the background call and return parsed+redacted follow-up strings.

    Raises nothing on a bound-model absence path except what get_or_create raises;
    the caller catches everything (the degrade contract).
    """
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    exchange = _build_exchange(session)
    if not exchange:
        return []
    prompt = render_use_case_prompt("followups", {"exchange": exchange})
    if not prompt:
        return []

    # No model bound → get_or_create raises at the factory; propagate so the caller
    # emits no event (chips simply don't render).
    client, _is_new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
    text = ""
    try:

        async def _stream() -> str:
            nonlocal text
            # Clear accumulated background history so prior utility prompts don't bleed in.
            if hasattr(client, "_history"):
                client._history.clear()
            async for event in client.stream(prompt):
                if event.kind == EVENT_TEXT_CHUNK:
                    text += event.text
                elif event.kind == EVENT_PERMISSION_REQUEST:
                    await client.reject_tool(event.request_id)
                elif event.kind == EVENT_COMPLETE:
                    break
            return text

        await asyncio.wait_for(_stream(), timeout=_FOLLOWUPS_TIMEOUT_SECS)
    finally:
        if hasattr(client, "_history"):
            client._history.clear()
        state.sessions.release(BACKGROUND_KEY)

    return _redact(_parse_followups(text))


async def _maybe_followups(state: "DashboardState", session: "_ChatSession") -> None:
    """Fire-and-forget: emit follow-up chips for a just-completed interactive turn.

    Gated OFF when: config disabled, session restricted (temporary/incognito —
    mirrors auto-title), a message is queued (the next turn is imminent), or the
    turn errored. Broadcasts ``chat_followups`` on success; silent on any failure
    or when no model is bound.
    """
    if not _followups_enabled():
        return
    if getattr(session, "is_restricted", False):
        return
    if session._queue:
        return
    if getattr(session, "_last_turn_errored", False):
        return
    try:
        items = await _generate_followups(state, session)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("Follow-up generation failed for %s", session.key, exc_info=True)
        return
    if not items:
        return
    sel().log_tool_invocation(
        session_key=BACKGROUND_KEY,
        agent="personalclaw-lite",
        source="chat_followups",
        tool_name="chat_followups",
        tool_kind="command",
        outcome="allowed",
        metadata={"session": session.key, "count": len(items)},
    )
    state.broadcast_ws("chat_followups", {"session": session.key, "items": items})
