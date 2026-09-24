"""Prompt optimizer endpoint — rewrites vague prompts before sending to agent."""

import asyncio
import logging

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

# CC-5: the composer assembles at most ``_CTX_MAX_TURNS`` role-labeled turns of at most
# ``_CTX_TURN_CHARS`` characters each, one turn per line, newest LAST (see
# web/src/pages/chat/optimizerContext.ts, whose CTX_BUDGET_CHARS is this same number).
# The cap is therefore that exact worst case, which makes a well-formed composer context
# survive untouched. It stays a cap because the route is public and other callers (the
# loop composer, apps) may send anything at all.
_CTX_MAX_TURNS = 10
_CTX_TURN_CHARS = 400
_CTX_PER_TURN_OVERHEAD = len("assistant: ") + len("…")  # widest label + clip marker
MAX_CONTEXT_CHARS = _CTX_MAX_TURNS * (_CTX_TURN_CHARS + _CTX_PER_TURN_OVERHEAD) + (
    _CTX_MAX_TURNS - 1  # the newlines joining the turns
)

# The bare token the bundled optimizer prompt asks for when a prompt needs no rewrite.
_UNCHANGED_TOKEN = "UNCHANGED"


def _clip_context(context: str) -> str:
    """Keep the newest ``MAX_CONTEXT_CHARS`` of a newest-last context.

    A plain ``context[-MAX:]`` keeps the right END but lands mid-line, so the oldest
    surviving turn reaches the model decapitated — a fragment attributed to nobody,
    which is worse than not sending it. Snap forward past that partial line instead and
    drop the turn whole. A context with no line boundary at all (one blob from some
    other caller) keeps the raw tail: a fragment beats an empty context there, because
    there is no attribution to lose.
    """
    if len(context) <= MAX_CONTEXT_CHARS:
        return context
    tail = context[-MAX_CONTEXT_CHARS:]
    nl = tail.find("\n")
    if nl == -1 or nl + 1 >= len(tail):
        return tail
    return tail[nl + 1 :]


def _is_unchanged_reply(text: str) -> bool:
    """True when the model invoked the prompt's already-specific contract.

    The bundled prompt asks for the bare token, and this check is deliberately lenient
    about how a model wraps it — quotes, markdown emphasis, a trailing period, a short
    trailing justification. Every lenient case fails toward KEEPING the user's draft,
    which is the safe direction here: the alternative is pasting the literal word
    "UNCHANGED." into their composer over the prompt they wrote.
    """
    head = text.strip().strip('"').strip("'").strip("*`").strip()
    if not head:
        return False
    first = head.split(maxsplit=1)[0]
    return first.strip("*`\"'.,:;!?—-").upper() == _UNCHANGED_TOKEN


async def handle_optimize(request: web.Request) -> web.Response:
    """POST /api/optimizer/optimize — rewrite a prompt using session context."""
    state: DashboardState = request.app["state"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    prompt = data.get("prompt", "")
    if not isinstance(prompt, str):
        return web.json_response({"error": "prompt must be a string"}, status=400)
    prompt = prompt.strip()
    context = data.get("context", "")
    if not isinstance(context, str):
        return web.json_response({"error": "context must be a string"}, status=400)

    if not prompt:
        return web.json_response({"optimized": prompt, "changed": False})

    # Build the user message with context
    parts = []
    if context:
        parts.append(f"<context>\n{_clip_context(context)}\n</context>\n")
    parts.append(f"<original_prompt>\n{prompt}\n</original_prompt>")
    user_msg = "\n".join(parts)

    # Use a dedicated optimizer session to avoid semaphore contention
    # with title generation and folder categorization on BACKGROUND_KEY.
    optimizer_session_key = "_optimizer"
    # The optimizer system prompt lives in the prompt system (bundled
    # ``task-prompt-optimizer``, bindable in Settings → Prompts).
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    optimizer_system = render_use_case_prompt("prompt_optimizer", {})
    if not optimizer_system:
        logger.warning("Optimizer system prompt unresolved — returning original")
        return web.json_response({"optimized": prompt, "changed": False})
    full_prompt = f"[System: {optimizer_system}]\n\n{user_msg}"

    try:

        async def _optimize() -> str:
            """Acquire session, stream, release — all under one timeout."""
            logger.debug("Optimizer: acquiring dedicated session")
            client, _is_new, _resumed = await state.sessions.get_or_create(
                optimizer_session_key, agent="personalclaw-lite"
            )
            logger.debug("Optimizer: session acquired, streaming")
            try:
                text = ""
                async for event in client.stream(full_prompt):
                    if event.kind == EVENT_TEXT_CHUNK:
                        text += event.text
                    elif event.kind == EVENT_PERMISSION_REQUEST:
                        await client.reject_tool(event.request_id)
                    elif event.kind == EVENT_COMPLETE:
                        break
                return text
            finally:
                logger.debug("Optimizer: releasing dedicated session")
                state.sessions.release(optimizer_session_key)

        text = await asyncio.wait_for(_optimize(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning(
            "Optimizer timed out (30s) — personalclaw-lite may be unresponsive or overloaded"
        )
        return web.json_response({"optimized": prompt, "changed": False})
    except Exception:
        logger.warning("Optimizer failed, returning original", exc_info=True)
        return web.json_response({"optimized": prompt, "changed": False})

    optimized = text.strip().strip('"').strip("'")
    if not optimized or _is_unchanged_reply(text):
        return web.json_response({"optimized": prompt, "changed": False})

    # Redact any exfiltration URLs or credentials from LLM output
    optimized, _ = redact_exfiltration_urls(optimized)
    optimized, _ = redact_credentials(optimized)

    changed = optimized.lower().strip() != prompt.lower().strip()
    if not changed:
        return web.json_response({"optimized": prompt, "changed": False})
    return web.json_response({"optimized": optimized, "changed": True})
