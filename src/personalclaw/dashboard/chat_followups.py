"""Follow-up chips — after each completed interactive chat turn,
suggest 2-3 short next messages via ONE cheap cancellable background call.

Mirrors ``suggestions.py`` / ``chat_title.py``: the instruction lives in the
bundled ``task-followups`` prompt (bindable in Settings → Prompts), asked as a
chore of the chat's (``chat_title.chat_chore``): one call of its own on the
Background chain, behind the spend guard, with no tools, and the output redacted.
It NEVER blocks the turn: the task is fire-and-forget, stored on
``session._followups_task``, and the next ``run_chat`` dispatch cancels it. When no
model is bound the call raises → caught here → no event fires (the degrade
contract), so the turn completes normally and the FE simply renders no chips.

A chip is sent with one click as her own words, so it may say for her only what the exchange
says. Every chip is written under :data:`_FOLLOWUP_RULES`, whatever prompt is bound, and a chip
stating a detail the exchange does not give is left out (``given_details.keep_given``). Measured
before these: a reply asked her for a swim class's time, which nothing of hers held, and a chip
under it read "Lina's swim class is Saturday at 10:00 AM".
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from personalclaw.agents.defaults import LITE_AGENT_NAME
from personalclaw.dashboard.chat_title import chat_chore, keeps_to_its_own_model
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.given_details import keep_given
from personalclaw.llm_helpers import parse_llm_json_list
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)

# Cap on the background call — chips are a nicety, never a stall.
_FOLLOWUPS_TIMEOUT_SECS = 20
# How many trailing chars of the exchange to feed the prompt (recency beats breadth).
_USER_CAP = 1000
_REPLY_CAP = 2000

#: What every follow-up is written under, after whatever prompt is bound for them: the bound
#: prompt may be her own edit, or a copy seeded before these rules existed.
_FOLLOWUP_RULES = (
    "Rules for these follow-ups, whatever is above:\n"
    "- The user sends a follow-up with one click, as their own words. So a follow-up never states "
    "a fact, time, date, name, number, place or decision for them that neither their message nor "
    "the reply above gives.\n"
    "- When the reply asks the user for something (a time, a detail, a choice), a follow-up never "
    "answers it for them, not even as a guess: suggest what they might ask or do next instead. "
    'After "Tell me the time and I\'ll add it to your notes", "Where else might the time be '
    'written down?" is a good follow-up, and "It\'s at 10:00 AM" never is.'
)


def _followups_enabled() -> bool:
    """Read the chat follow-up-chips config flag (default on)."""
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().dashboard.followup_chips)
    except Exception:
        return True


#: A turn only earns the "Check this work" offer when it both DID
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


def _followups_problem(text: str) -> str:
    """What is wrong with *text* as a follow-ups answer, ``""`` when it holds a JSON list: read as
    :func:`_parse_followups` reads it, and an empty list (nothing worth suggesting) is an answer."""
    return "" if parse_llm_json_list(text) is not None else "no JSON list"


def _parse_followups(text: str) -> list[str]:
    """Parse the LLM response into ≤3 short follow-up strings."""
    result = parse_llm_json_list(text)
    if result is None:
        logger.debug("Failed to parse followups response: %s", text.strip()[:200])
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


async def _generate_followups(session: "_ChatSession") -> list[str]:
    """Ask for *session*'s follow-ups and return them parsed, checked and redacted.

    A first model of the Background chain that fails, is paused or answers no list hands the
    call to the next one. No model bound, or none answering, raises; the caller catches
    everything (the degrade contract). A follow-up stating a detail the exchange it was written
    from does not give is left out.
    """
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    exchange = _build_exchange(session)
    if not exchange:
        return []
    prompt = render_use_case_prompt("followups", {"exchange": exchange})
    if not prompt:
        return []
    text = await asyncio.wait_for(
        chat_chore(session, f"{prompt}\n\n{_FOLLOWUP_RULES}", validate=_followups_problem),
        timeout=_FOLLOWUPS_TIMEOUT_SECS,
    )
    return _redact(keep_given(_parse_followups(text), exchange, what="Follow-ups"))


async def _maybe_followups(state: "DashboardState", session: "_ChatSession") -> None:
    """Fire-and-forget: emit follow-up chips for a just-completed interactive turn.

    Gated OFF when: config disabled, the chat keeps to its own model (Incognito or
    Temporary — the answer auto-title asks), a loop's hidden worker or planner session, a
    message is queued (the next turn is imminent), or the turn errored. Broadcasts
    ``chat_followups`` on success; silent on any failure or when no model is bound.
    """
    if not _followups_enabled():
        return
    if keeps_to_its_own_model(state, session):
        return
    # Nobody reads a chip in a loop's hidden session, so the call there only spent — once per
    # cycle, and once more for each turn a Pause or incident mode stopped, which is how a held loop
    # still reached the model.
    from personalclaw.dashboard.chat_utils import LOOP_WORK_APPS

    if getattr(session, "_app", "") in LOOP_WORK_APPS:
        return
    if session._queue:
        return
    if getattr(session, "_last_turn_errored", False):
        return
    try:
        items = await _generate_followups(session)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("Follow-up generation failed for %s", session.key, exc_info=True)
        return
    if not items:
        return
    sel().log_tool_invocation(
        session_key=_history_key_for(session.key),
        agent=LITE_AGENT_NAME,
        source="chat_followups",
        tool_name="chat_followups",
        tool_kind="command",
        tool_input=None,
        outcome="allowed",
        metadata={"session": session.key, "count": len(items)},
    )
    state.broadcast_ws("chat_followups", {"session": session.key, "items": items})
