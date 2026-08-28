"""Contextual prompt suggestions — pre-computed via background LLM."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw.context import ContextBuilder
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

# Regenerate suggestions every 30 minutes
_REFRESH_INTERVAL_SECS = 30 * 60

# Fallback suggestions when the LLM is unavailable or context is empty.
#
# 🔴 EVERY ENTRY HERE MUST BE EXECUTABLE ON AN INSTANCE WITH NO DATA, because that is the only
# state this list is ever shown in. ``generate_suggestions`` returns it precisely when
# ``_build_context`` came back empty — no memory, no sessions, no automations — and
# ``SuggestionsCache`` seeds from it before the first generation. So it is the brand-new-install
# list, and it is the first thing a new user reads on the most-visited surface in the product.
#
# Two entries used to ask about state the empty state does not have by construction:
#
#     "Summarize my recent conversations"   there are none — that is WHY we are in the fallback
#     "Review my latest PR"                 assumes a repository context nobody has configured
#
# Both dead-end into "you don't have any", which is a poor first answer and teaches nothing about
# what the product can do. Replaced with one orientation prompt and one that names a real,
# distinctive capability a fresh instance can actually perform.
#
# ``test_suggestions_fallback_needs_no_data.py`` holds the criterion, not the strings — so this
# list can be re-worded freely and only a *dead-end* re-appearing reds the gate.
#
# 🔑 AND EACH ENTRY NAMES SOMETHING THIS PRODUCT DISTINCTIVELY DOES. That is a copy JUDGMENT,
# not a defect, so it is stated here rather than asserted in a test: this list is the product's
# first self-description, on the surface a new user opens most.
#
# Three entries used to be generic text generation — "Generate sunrise haiku", "Give me a
# three-word farewell", "Help me brainstorm an idea". Any chat box can serve those, so half the
# list taught nothing about a self-hosted agentic OS with tools, tasks, automations, knowledge
# and local execution. Every entry now points at a shipped surface:
#
#     Show health-check status               the doctor / system surface
#     Break a goal into tasks                Tasks
#     Save a note to my knowledge base       Knowledge (ingest works on an empty base)
#     Run a command and explain the output   Terminal + tools — the local-execution thesis
#     What can you help me with?             orientation, the one thing a new user always asks
#     Set up a daily briefing                Triggers / automations
#
# Nothing here promises a capability the product lacks — the one hard rule a suggestion list has,
# because a chip that leads to "I can't do that" is worse than a generic one.
_FALLBACK_SUGGESTIONS = [
    "Show health-check status",
    "Break a goal into tasks",
    "Save a note to my knowledge base",
    "Run a command and explain the output",
    "What can you help me with?",
    "Set up a daily briefing",
]


@dataclass
class SuggestionsCache:
    """Holds pre-computed suggestions with a timestamp."""

    suggestions: list[str] = field(default_factory=lambda: list(_FALLBACK_SUGGESTIONS))
    generated_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]


def _build_context(state: "DashboardState") -> str:
    """Assemble context for the suggestions prompt from memory and recent activity."""
    parts: list[str] = []

    # Active workspace memory
    try:
        memory = ContextBuilder.get_memory_for(None)
        prefs = memory.read_preferences()
        if prefs and prefs.strip() != "# User Preferences\n\n<!-- Learned from conversations -->":
            parts.append(f"## User Preferences\n{prefs[:2000]}")

        projects = memory.read_projects()
        if projects and projects.strip() != "# Active Projects\n\n<!-- Current work context -->":
            parts.append(f"## Active Projects\n{projects[:3000]}")

        # Recent history (last 2 days)
        recent_history = memory.read_recent_history(days=2)
        if recent_history:
            parts.append(f"## Recent Activity\n{recent_history[:4000]}")
    except Exception:
        logger.debug("Failed to read memory for suggestions", exc_info=True)

    # Recent session titles and last messages
    try:
        if state.conversation_log:
            sessions = state.conversation_log.list_sessions()
            if sessions:
                session_parts: list[str] = []
                for s in sessions[:5]:
                    title = s.get("title", "")
                    key = s.get("key", "")
                    if not key:
                        continue
                    line = f"- **{title or key}**"
                    try:
                        recent = state.conversation_log.recent(key, max_messages=6)
                        user_msgs = [
                            m["content"][:150]
                            for m in recent
                            if m.get("role") == "user" and m.get("content")
                        ][-3:]
                        if user_msgs:
                            line += "\n" + "\n".join(f"  - User: {msg}" for msg in user_msgs)
                    except Exception:
                        pass
                    session_parts.append(line)
                if session_parts:
                    parts.append("## Recent Sessions\n" + "\n".join(session_parts))
    except Exception:
        logger.debug("Failed to read sessions for suggestions", exc_info=True)

    # Automations (what's scheduled) — from the unified store (S111). Reading `state.crons` here
    # described only the legacy file, which nothing has written since S108: a user whose automations
    # all live in `triggers.json` got NO scheduled context in their suggestions.
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers.store import TriggerStore

        rows = [r for r in TriggerStore(base_dir=config_dir()).load() if r.trigger.enabled]
        if rows:
            names = [f"- {r.trigger.name}" for r in rows[:5]]
            parts.append("## Active Automations\n" + "\n".join(names))
    except Exception:
        logger.debug("Failed to read automations for suggestions", exc_info=True)

    # Time context
    now = datetime.now()
    parts.append(f"## Current Time\n{now.strftime('%A, %B %d %Y at %H:%M')}")

    return "\n\n".join(parts)


def _parse_suggestions(text: str) -> list[str]:
    """Parse LLM response into a list of suggestion strings."""
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, list) and all(isinstance(s, str) for s in result):
            return [s.strip() for s in result if s.strip() and len(s.strip()) <= 80][:6]
    except (json.JSONDecodeError, TypeError):
        pass

    logger.warning("Failed to parse suggestions response: %s", text[:200])
    return []


def _redact_suggestions(suggestions: list[str]) -> list[str]:
    """Apply security redaction to each suggestion string."""
    result: list[str] = []
    for s in suggestions:
        s, _ = redact_exfiltration_urls(s)
        s, _ = redact_credentials(s)
        result.append(s)
    return result


async def generate_suggestions(state: "DashboardState") -> list[str]:
    """Generate suggestions using the background ACP agent session."""
    context = _build_context(state)
    if not context or len(context) < 50:
        logger.debug("Insufficient context for suggestions — using fallback")
        return list(_FALLBACK_SUGGESTIONS)

    # The suggestions instruction lives in the prompt system (bundled
    # ``task-suggestions``, bindable in Settings → Prompts), rendered here with the
    # assembled context. Fall back to fallback suggestions if it can't resolve.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt("suggestions", {"context": context})
    if not prompt:
        logger.debug("Suggestions prompt unresolved — using fallback")
        return list(_FALLBACK_SUGGESTIONS)

    client, _is_new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
    text = ""
    try:

        async def _stream() -> str:
            nonlocal text
            async for event in client.stream(prompt):
                if event.kind == EVENT_TEXT_CHUNK:
                    text += event.text
                elif event.kind == EVENT_PERMISSION_REQUEST:
                    sel().log_tool_invocation(
                        session_key="_bg",
                        tool_name=getattr(event, "title", "unknown"),
                        outcome="denied",
                        source="suggestions",
                    )
                    await client.reject_tool(event.request_id)
                elif event.kind == EVENT_COMPLETE:
                    break
            return text

        await asyncio.wait_for(_stream(), timeout=60)
    except asyncio.TimeoutError:
        logger.warning("Suggestions generation timed out")
        return list(_FALLBACK_SUGGESTIONS)
    finally:
        state.sessions.release(BACKGROUND_KEY)

    suggestions = _parse_suggestions(text)
    if suggestions:
        suggestions = _redact_suggestions(suggestions)
        logger.info("Generated %d suggestions", len(suggestions))
        return suggestions

    return list(_FALLBACK_SUGGESTIONS)


async def refresh_suggestions(state: "DashboardState", cache: SuggestionsCache) -> None:
    """Background task: regenerate suggestions."""
    async with cache._lock:
        try:
            suggestions = await generate_suggestions(state)
            cache.suggestions = suggestions
            cache.generated_at = time.time()
        except Exception:
            logger.warning("Suggestions generation failed", exc_info=True)


async def maybe_refresh(state: "DashboardState", cache: SuggestionsCache) -> None:
    """Trigger a background refresh if suggestions are stale."""
    now = time.time()
    if now - cache.generated_at < _REFRESH_INTERVAL_SECS:
        return
    if cache._lock.locked():
        return

    # Fire and forget
    task = asyncio.create_task(refresh_suggestions(state, cache))
    cache._task = task
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)


def get_suggestions_cache(state: "DashboardState") -> SuggestionsCache:
    """Get or create the suggestions cache on the state object."""
    if not hasattr(state, "_suggestions_cache"):
        state._suggestions_cache = SuggestionsCache()  # type: ignore[attr-defined]
    return state._suggestions_cache  # type: ignore[attr-defined]


# ── HTTP Handler ──


async def api_suggestions(request: web.Request) -> web.Response:
    """GET /api/suggestions — return pre-computed contextual suggestions.

    Query params:
        force=1  — force a fresh generation (ignores cache age)
    """
    state: "DashboardState" = request.app["state"]
    cache = get_suggestions_cache(state)
    force = request.query.get("force") == "1"

    if force:
        try:
            await asyncio.wait_for(refresh_suggestions(state, cache), timeout=45)
        except (asyncio.TimeoutError, Exception):
            pass
    elif cache.generated_at == 0:
        # Never generated yet — wait for result
        if cache._lock.locked() and cache._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(cache._task), timeout=45)
            except (asyncio.TimeoutError, Exception):
                pass
        if cache.generated_at == 0 and not cache._lock.locked():
            try:
                await asyncio.wait_for(refresh_suggestions(state, cache), timeout=45)
            except (asyncio.TimeoutError, Exception):
                pass
    else:
        await maybe_refresh(state, cache)

    return web.json_response(
        {
            "suggestions": cache.suggestions,
            "generated_at": cache.generated_at,
            "stale": (
                (time.time() - cache.generated_at) > _REFRESH_INTERVAL_SECS
                if cache.generated_at
                else True
            ),
        }
    )
