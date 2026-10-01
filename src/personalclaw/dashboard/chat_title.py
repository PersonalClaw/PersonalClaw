"""Title generation — auto-title and rename."""

import logging
from collections.abc import Callable
from functools import partial

from aiohttp import web

from personalclaw import owed_chores
from personalclaw.dashboard.chat_utils import _history_key_for, persisted_history_key
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
from personalclaw.llm.events import EVENT_MODEL_SUBSTITUTION
from personalclaw.llm_helpers import (
    any_answer,
    failure_clause,
    is_model_call_failure,
    let_fail_over,
    say_background_substitution,
)
from personalclaw.request_validation import require_string
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY, chore_usage
from personalclaw.textfmt import TAGS_LINE_RE, parse_title
from personalclaw.usage_ledger import Attribution, recorder

logger = logging.getLogger(__name__)

# Max turns to attempt auto-titling before giving up
_TITLE_MAX_ATTEMPTS = 5

# Auto-tagging bounds: at most this many tags assigned, at most this many
# NEW (not-yet-existing) tags created per session.
_AUTO_TAG_MAX_TOTAL = 4
_AUTO_TAG_MAX_NEW = 2


def _build_title_prompt(messages: list[dict[str, str]]) -> str | None:
    """Build a title generation prompt from conversation messages.

    Renders the ``title`` use-case prompt (bundled ``task-title``, editable/bindable
    in Settings → Prompts) through the prompt engine, so the instruction text is no
    longer hardcoded here.
    """
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    lines: list[str] = []
    for m in messages[:10]:
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            lines.append(f"{role}: {content[:200]}")
    if not lines:
        return None
    return render_use_case_prompt("title", {"transcript": "\n".join(lines)})


async def _stream_background_prompt(
    state: DashboardState,
    prompt: str,
    *,
    usage: Attribution,
    validate: Callable[[str], str] = any_answer,
) -> str:
    """Stream *prompt* through the shared background session and collect the text.

    The call writes its usage row for *usage* (``session.chore_usage``): whose spend it is.

    A first model of the background chain that fails before it says anything (an error, a
    timeout, an open breaker, an empty answer or one *validate* rejects) falls back to the next
    one, and the substitute is said in the log
    (:func:`~personalclaw.llm_helpers.say_background_substitution`): a chore that never
    announced a fallback kept a slow first model's failure however many were bound behind it.
    """
    client, _is_new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
    record = recorder(client, usage)
    say = say_background_substitution("Background chat chore")
    text = ""
    try:
        # Clear accumulated history so prior utility prompts don't confuse the model
        if hasattr(client, "_history"):
            client._history.clear()
        let_fail_over(client, validate)
        async for event in client.stream(prompt):
            if event.kind == EVENT_TEXT_CHUNK:
                text += event.text
            elif event.kind == EVENT_MODEL_SUBSTITUTION:
                say(event.text)
            elif event.kind == EVENT_PERMISSION_REQUEST:
                await client.reject_tool(event.request_id)
            elif event.kind == EVENT_COMPLETE:
                record(event)
                break
    finally:
        # Clear again so the title prompt doesn't pollute future calls
        if hasattr(client, "_history"):
            client._history.clear()
        state.sessions.release(BACKGROUND_KEY)
    return text


async def _stream_chat_chore(
    state: DashboardState,
    session: _ChatSession,
    prompt: str,
    *,
    validate: Callable[[str], str] = any_answer,
) -> str:
    """:func:`_stream_background_prompt` for a chore made for *session*: the chat's spend, under
    the key its own turns are recorded by."""
    return await _stream_background_prompt(
        state, prompt, usage=chore_usage(_history_key_for(session.key)), validate=validate
    )


def _title_problem(text: str) -> str:
    """What is wrong with *text* as a title reply, ``""`` when it holds a title line."""
    return "" if parse_title(text) else "no title line"


async def _generate_title_via_provider(
    state: DashboardState, messages: list[dict[str, str]], *, usage: Attribution
) -> str:
    """Generate a title using the shared background agent session, recorded for *usage*."""

    prompt = _build_title_prompt(messages)
    if not prompt:
        logger.debug("Title generation skipped — no usable messages")
        return ""

    logger.debug("Title generation prompt (%d chars): %s", len(prompt), prompt[:120])
    text = await _stream_background_prompt(state, prompt, usage=usage, validate=_title_problem)
    return parse_title(text)


# ── Auto-tagging (same LLM call as the title — no second roundtrip) ─────────


def _build_tags_suffix(state: DashboardState) -> str:
    """The tag-proposal instructions appended to the title prompt.

    Asks the model for ONE extra line so the title stays line 1 (the title
    parser already only reads the first line).
    """
    existing = ", ".join(
        str(t.get("name", ""))
        for t in sorted(state._tags, key=lambda t: t.get("order", 0))
        if t.get("name")
    )
    return (
        "\n\nThen, on a SECOND line, propose tags for this conversation in the form:\n"
        "TAGS: tag1, tag2\n"
        f"Existing tags: {existing or '(none)'}\n"
        "Rules: strongly prefer existing tags that genuinely fit. Propose at most "
        f"{_AUTO_TAG_MAX_NEW} NEW tags (short, 1-2 words) only when no existing tag fits. "
        f"At most {_AUTO_TAG_MAX_TOTAL} tags total. If nothing fits, reply exactly: TAGS: none"
    )


def _parse_tags_line(text: str) -> list[str]:
    """Extract proposed tag names from a ``TAGS:`` line in the response."""
    for line in text.splitlines():
        label = TAGS_LINE_RE.match(line)
        if label is None:
            continue
        raw = line[label.end() :].strip()
        if not raw or raw.lower() in ("none", "n/a", "-"):
            return []
        names: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            name = part.strip().strip('"').strip("'").strip(".")
            # Defensive: drop a leaked NEW: prefix and anything unusable
            if name.lower().startswith("new:"):
                name = name[4:].strip()
            if not name or len(name) > 40:
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            names.append(name)
        return names[:_AUTO_TAG_MAX_TOTAL]
    return []


def _apply_auto_tags(state: DashboardState, session: _ChatSession, names: list[str]) -> list[str]:
    """Resolve proposed tag names to ids (creating up to 2 new tags) and assign.

    New tags are created via the SAME helper the UI's create endpoint uses
    (:func:`personalclaw.dashboard.chat_tags.create_tag`) so they get proper
    ids/colors/order. Returns the assigned tag ids (empty = nothing applied).
    """
    from personalclaw.dashboard.chat_persistence import save_session_to_history
    from personalclaw.dashboard.chat_tags import _auto_color, create_tag, find_tag_by_name

    if not names:
        return []
    # Re-check the guards at apply time (state may have changed mid-LLM-call).
    if session.is_restricted or session.tags:
        return []
    assigned: list[str] = []
    created = 0
    for name in names:
        if len(assigned) >= _AUTO_TAG_MAX_TOTAL:
            break
        tag = find_tag_by_name(state, name)
        if tag is None:
            if created >= _AUTO_TAG_MAX_NEW:
                continue
            tag = create_tag(state, name, color=_auto_color(name))
            if tag is None:
                continue
            created += 1
        if tag["id"] not in assigned:
            assigned.append(tag["id"])
    if not assigned:
        return []
    session.tags = assigned
    save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    logger.info(
        "Auto-tagged session %s with %d tag(s) (%d new)", session.key, len(assigned), created
    )
    return assigned


def _auto_tag_enabled() -> bool:
    """Read the chat auto-tag config flag (default on)."""
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().dashboard.auto_tag_sessions)
    except Exception:
        return True


def _persist_title(state: DashboardState, session: _ChatSession) -> None:
    """Save the session title to the conversation history file.

    ``set_title`` MERGES into an existing meta line and returns silently when there is no
    file to merge into (``ConversationLog.update_metadata``), which is every conversation
    that has not had a turn yet — so a rename of a brand-new chat was accepted
    ``200 {"ok": true}`` and lost on the next restart (#2969). Marking the session dirty as
    well routes the title through the flush loop, which owns creating the file; this stays
    the immediate write for a conversation that already has one.
    """

    session._dirty = True
    if state.conversation_log:
        history_key = persisted_history_key(state.conversation_log, session.key)
        try:
            state.conversation_log.set_title(history_key, session.title)
            logger.debug("Persisted title %r for session %s", session.title, session.key)
        except Exception:
            logger.debug("Failed to persist title for session %s", session.key)


def _apply_title(state: DashboardState, session: _ChatSession, title: str) -> None:
    """Commit a resolved title to the session: mark it titled, persist, broadcast."""
    session.title = title
    session._titled = True
    _persist_title(state, session)
    state.push_session_title(session.key, title)


async def _maybe_auto_title(state: DashboardState, session: _ChatSession) -> None:
    """Background task: attempt to auto-title a session after a response completes.

    When auto-tagging is enabled the SAME LLM call also proposes tags for the
    session (the tag instructions are appended to the title prompt — no second
    roundtrip). Tags are only applied when the user hasn't tagged the session
    themselves and the session isn't restricted (incognito/temporary).

    A title no model could be asked for is owed (``owed_chores``) and asked for again once a
    model answers, rather than waiting on a next turn that may never come.
    """
    try:
        await _title_once(state, session)
    except Exception as exc:
        # The chat keeps its first line as the title either way. A model that did not answer
        # is said in one line — its traceback holds only the HTTP client's frames — and a
        # defect keeps its traceback.
        if is_model_call_failure(exc):
            owed = owed_chores.no_model_answered(exc)
            logger.warning(
                "Auto-title failed for session %s: %s%s",
                session.key,
                failure_clause(exc),
                "; it is asked again once a model answers" if owed else "",
            )
            logger.debug("Auto-title failure for session %s", session.key, exc_info=exc)
            if owed:
                owed_chores.owe(
                    f"title:{session.key}",
                    f"the title of {session.key}",
                    partial(_owed_title, state, session.key),
                )
        else:
            logger.warning("Auto-title failed for session %s", session.key, exc_info=True)


async def _owed_title(state: DashboardState, key: str) -> bool:
    """Ask again for the title of the chat *key* (``owed_chores``); a chat that has its title
    by now, or is gone, owes none. A model that still cannot answer raises."""
    session = state._sessions.get(key)
    if session is not None:
        await _title_once(state, session)
    return True


async def _title_once(state: DashboardState, session: _ChatSession) -> None:
    """Title *session* once, if it still wants one; a model that could not answer raises."""
    if session._titled:
        return
    if session.blocks_reads:
        return
    user_count = sum(1 for m in session.messages if m.get("role") == "user")
    if user_count < 1 or user_count > _TITLE_MAX_ATTEMPTS:
        if user_count > _TITLE_MAX_ATTEMPTS and not session._titled:
            first_user = next(
                (m["content"] for m in session.messages if m.get("role") == "user"), ""
            )
            _apply_title(state, session, first_user[:60] or session.key)
        return
    logger.info("Auto-title: attempting for session %s (turn %d)", session.key, user_count)
    # Piggyback tag proposal on the title call only when it can actually apply:
    # flag on, user hasn't tagged, session isn't restricted.
    want_tags = not session.is_restricted and not session.tags and _auto_tag_enabled()
    prompt = _build_title_prompt(session.messages)
    if not prompt:
        logger.debug("Title generation skipped — no usable messages")
        return
    if want_tags:
        prompt += _build_tags_suffix(state)
    text = await _stream_chat_chore(state, session, prompt, validate=_title_problem)
    title = parse_title(text)
    logger.info("Auto-title: agent returned %r for session %s", title, session.key)
    if title:
        _apply_title(state, session, title)
        if want_tags:
            _apply_auto_tags(state, session, _parse_tags_line(text))


async def api_chat_session_generate_title(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/generate-title — manually trigger title generation."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    logger.info("Manual title generation requested for session %s", name)
    try:
        title = await _generate_title_via_provider(
            state, session.messages, usage=chore_usage(_history_key_for(session.key))
        )
    except Exception:
        logger.debug("Title generation failed for session %s", name, exc_info=True)
        user_msgs = [m for m in session.messages if m.get("role") == "user"]
        title = user_msgs[0].get("content", "")[:60] if user_msgs else ""

    if title:
        _apply_title(state, session, title)

    return web.json_response({"ok": True, "title": title})


async def api_chat_session_rename(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/title — rename a chat session."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)
    title = require_string(body, "title")[:200]
    _apply_title(state, session, title)
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_rename",
        outcome="allowed",
        source="dashboard",
        resources=session.key,
    )
    return web.json_response({"ok": True, "title": title})
