"""Contextual prompt suggestions — pre-computed via background LLM.

A suggestion is put in her message box to send as her own words, so it may say for her only what
the context it was written from says: each is written under :data:`_SUGGESTION_RULES`, whatever
prompt is bound, and one stating a detail the context does not give is left out
(``given_details.keep_given``).
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw import chores, memory_writes
from personalclaw.context import ContextBuilder
from personalclaw.given_details import keep_given
from personalclaw.llm_helpers import failure_clause, is_model_call_failure, parse_llm_json_list
from personalclaw.security import redact_credentials, redact_exfiltration_urls

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

#: What every suggestion is written under, after whatever prompt is bound for them.
_SUGGESTION_RULES = (
    "Rules for these suggestions, whatever is above:\n"
    "- The user sends a suggestion as their own words. So a suggestion never states a fact, time, "
    "date, name, number, place or decision for them that the context above does not give: "
    "suggest what they might ask or do, never what they have not said."
)


@dataclass
class SuggestionsCache:
    """Holds pre-computed suggestions with a timestamp."""

    suggestions: list[str] = field(default_factory=lambda: list(_FALLBACK_SUGGESTIONS))
    generated_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]


def _build_context(state: "DashboardState") -> str:
    """Assemble the SUBSTANTIVE context for the suggestions prompt — memory, sessions, automations.

    Returns "" when the instance has nothing to say about itself yet, which is what lets
    ``generate_suggestions`` decide between the fallback list and a real LLM turn.

    The current time is deliberately NOT part of this. It used to be appended here
    unconditionally, which quietly made that decision depend on the CALENDAR: the guard reads
    ``len(context) < 50``, and a lone time section measures 44-53 characters depending on how long
    today's weekday and month names are. On a brand-new empty install that meant

        "Friday, May 01"        -> 44 chars -> falls back correctly
        "Wednesday, September"  -> 53 chars -> passes the guard and calls the LLM

    which is 113 days of 2026 spent asking a model for suggestions from a context whose entire
    content is a timestamp. Worse, the first ``/api/suggestions`` call AWAITS that generation for
    up to 45s (see ``api_suggestions``), so whether a new user's chat suggestions appeared instantly
    or after most of a minute came down to the length of a weekday name.
    """
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

    # The five newest chats' titles and last messages. An Incognito or Temporary chat is left out:
    # no background model reads one, and the suggestions made from these are shown on every page.
    try:
        if state.conversation_log:
            session_parts: list[str] = []
            for s in state.conversation_log.list_sessions():
                if len(session_parts) >= 5:
                    break
                title = s.get("title", "")
                key = s.get("key", "")
                if not key or memory_writes.blocks_background_models(
                    key, memory_mode=s.get("memory_mode")
                ):
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

    # Automations (what's scheduled) — from the unified store. Reading `state.crons` here
    # described only the legacy file, which nothing has written: a user whose automations
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

    return "\n\n".join(parts)


def _time_context() -> str:
    """The time section, appended only once a real context has earned an LLM turn."""
    return f"## Current Time\n{datetime.now().strftime('%A, %B %d %Y at %H:%M')}"


def _parse_suggestions(text: str, *, quiet: bool = False) -> list[str]:
    """Parse LLM response into a list of suggestion strings; ``[]`` when it holds none, said in the
    log unless *quiet* (the check a fallback is decided by reads the same answer first). The list
    is read the way every model's JSON answer is (``llm_helpers.parse_llm_json_list``)."""
    result = parse_llm_json_list(text)
    if result is not None and all(isinstance(s, str) for s in result):
        return [s.strip() for s in result if s.strip() and len(s.strip()) <= 80][:6]

    if not quiet:
        logger.warning("Failed to parse suggestions response: %s", text.strip()[:200])
    return []


def _suggestions_problem(text: str) -> str:
    """What is wrong with *text* as a suggestions answer, ``""`` when it holds suggestions."""
    return "" if _parse_suggestions(text, quiet=True) else "no JSON list of suggestions"


def _redact_suggestions(suggestions: list[str]) -> list[str]:
    """Apply security redaction to each suggestion string."""
    result: list[str] = []
    for s in suggestions:
        s, _ = redact_exfiltration_urls(s)
        s, _ = redact_credentials(s)
        result.append(s)
    return result


async def generate_suggestions(state: "DashboardState") -> list[str]:
    """Generate suggestions, asked as a chore of their own (``chores.run_chore``)."""
    context = _build_context(state)
    if not context or len(context) < 50:
        logger.debug("Insufficient context for suggestions — using fallback")
        return list(_FALLBACK_SUGGESTIONS)

    # Earned it — now give the model the clock too. Appending AFTER the guard is the whole point:
    # see `_build_context`'s docstring for the calendar-dependent bug this ordering fixes.
    context = f"{context}\n\n{_time_context()}"

    # The suggestions instruction lives in the prompt system (bundled
    # ``task-suggestions``, bindable in Settings → Prompts), rendered here with the
    # assembled context. Fall back to fallback suggestions if it can't resolve.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt("suggestions", {"context": context})
    if not prompt:
        logger.debug("Suggestions prompt unresolved — using fallback")
        return list(_FALLBACK_SUGGESTIONS)

    # Asking RESOLVES a model; a pre-onboarding instance with no provider bound raises
    # ProviderResolutionError. That is the SAME "can't generate yet" state as an empty context, an
    # unresolved prompt, a timeout or an answer that cannot be read — a degradation, not a fault —
    # so each returns the fallback list, the first quietly (a debug line, never a WARNING
    # traceback). The list is what stamps `cache.generated_at`: an error that propagated to
    # ``refresh_suggestions`` left it unset, and ``api_suggestions`` re-ran generation on EVERY
    # poll. Two classes carry the no-model signal (the LLM registry's and the bridge's) — catch
    # both, as ``session.py`` and ``cli.py`` do for the same reason. A first model of the chain
    # that fails, is paused or answers no list hands the call to the next one.
    from personalclaw.guardrails.failure import OutputContractError
    from personalclaw.llm.registry import ProviderResolutionError as _LLMResolveErr
    from personalclaw.providers.provider_bridge import ProviderResolutionError as _BridgeResolveErr

    try:
        text = await asyncio.wait_for(
            chores.run_chore(
                f"{prompt}\n\n{_SUGGESTION_RULES}",
                usage=chores.chore_usage(),
                validate=_suggestions_problem,
            ),
            timeout=60,
        )
    except (_BridgeResolveErr, _LLMResolveErr):
        logger.debug("No model resolves for suggestions yet — using fallback")
        return list(_FALLBACK_SUGGESTIONS)
    except asyncio.TimeoutError:
        logger.warning("Suggestions generation timed out")
        return list(_FALLBACK_SUGGESTIONS)
    except OutputContractError as exc:
        logger.warning("Failed to parse suggestions response: %s", exc.raw[:200])
        return list(_FALLBACK_SUGGESTIONS)

    suggestions = keep_given(_parse_suggestions(text), context, what="Suggestions")
    if suggestions:
        suggestions = _redact_suggestions(suggestions)
        logger.info("Generated %d suggestions", len(suggestions))
        return suggestions

    return list(_FALLBACK_SUGGESTIONS)


async def refresh_suggestions(state: "DashboardState", cache: SuggestionsCache) -> None:
    """Background task: regenerate suggestions.

    A refresh no model answered keeps the suggestions there are and is asked again by the next
    poll. It is said in one line with what happened; its traceback, which holds only the HTTP
    client's frames, is at debug. A defect keeps its traceback.
    """
    async with cache._lock:
        try:
            suggestions = await generate_suggestions(state)
            cache.suggestions = suggestions
            cache.generated_at = time.time()
        except Exception as exc:
            if is_model_call_failure(exc):
                logger.warning("Suggestions generation failed: %s", failure_clause(exc))
                logger.debug("Suggestions generation failure", exc_info=exc)
            else:
                logger.warning("Suggestions generation failed", exc_info=True)


def start_refresh(state: "DashboardState", cache: SuggestionsCache, *, force: bool = False) -> bool:
    """Start a background refresh when the suggestions are owed one; whether one is running.

    They are owed one when none was generated in this process, when the last is older than the
    refresh interval, and when one is asked for (*force*: the widget's Refresh). One refresh at a
    time: a read while one runs starts nothing, and every page hears a ``suggestions`` refresh
    hint when it lands (:func:`_refresh_and_announce`), so no read waits for it.
    """
    if cache._task is not None and not cache._task.done():
        return True
    if not force and time.time() - cache.generated_at < _REFRESH_INTERVAL_SECS:
        return False
    task = asyncio.create_task(_refresh_and_announce(state, cache))
    cache._task = task
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return True


async def _refresh_and_announce(state: "DashboardState", cache: SuggestionsCache) -> None:
    """:func:`refresh_suggestions`, then the refresh hint the pages that show them re-read on.

    Said whatever came of it: a page showing a refresh in progress re-reads and stops showing
    one, and a refresh no model answered kept the suggestions there were."""
    try:
        await refresh_suggestions(state, cache)
    finally:
        state.push_refresh("suggestions")


def get_suggestions_cache(state: "DashboardState") -> SuggestionsCache:
    """Get or create the suggestions cache on the state object."""
    if not hasattr(state, "_suggestions_cache"):
        state._suggestions_cache = SuggestionsCache()  # type: ignore[attr-defined]
    return state._suggestions_cache  # type: ignore[attr-defined]


# ── HTTP Handler ──


async def api_suggestions(request: web.Request) -> web.Response:
    """GET /api/suggestions — the suggestions there are now, at once.

    Query params:
        force=1  — start a fresh generation whatever the cache's age (the widget's Refresh)

    Never waits on the model: a generation this read starts (:func:`start_refresh`) runs in
    the background, ``refreshing`` says one is running, and every page hears a ``suggestions``
    refresh hint when it lands. Until a first one lands they are the fallback list the cache
    starts with. This used to wait up to 45s for a first generation and for every ``force``,
    holding one of the browser's six connections to the gateway for as long.
    """
    state: "DashboardState" = request.app["state"]
    cache = get_suggestions_cache(state)
    refreshing = start_refresh(state, cache, force=request.query.get("force") == "1")
    return web.json_response(
        {
            "suggestions": cache.suggestions,
            "generated_at": cache.generated_at,
            "stale": (
                (time.time() - cache.generated_at) > _REFRESH_INTERVAL_SECS
                if cache.generated_at
                else True
            ),
            "refreshing": refreshing,
        }
    )
