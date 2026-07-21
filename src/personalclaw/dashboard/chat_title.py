"""Title generation — auto-title, rename, plan rephrase."""

import logging

from aiohttp import web

from personalclaw.config.loader import config_dir
from personalclaw.context_management import extract_plan_metadata, rephrase_plan
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY

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


def _reset_auto_run_for_new_plan(session: "_ChatSession") -> None:
    """Clear auto-run state so a new plan requires fresh user approval."""
    session_dir = config_dir() / "sessions" / session.key
    if session_dir.exists():
        for f in session_dir.glob("stage_*_result.md"):
            try:
                f.unlink()
            except OSError:
                pass
    session._orch_tracker = None
    session._auto_run = False


def _extract_and_redact_plan_metadata(text: str) -> tuple[list[str], str, list[list[str]]]:
    """Extract stage titles, goal, and descriptions from plan text, redacted."""
    titles, goal, descriptions = extract_plan_metadata(text)
    titles = [redact_credentials(redact_exfiltration_urls(t)[0])[0] for t in titles]
    if goal:
        goal = redact_credentials(redact_exfiltration_urls(goal)[0])[0]
    descriptions = [
        [redact_credentials(redact_exfiltration_urls(d)[0])[0] for d in stage_descs]
        for stage_descs in descriptions
    ]
    return titles, goal, descriptions


async def _rephrase_plan_lite(
    state: DashboardState,
    text: str,
    issues: list[str],
    *,
    might_not_be_plan: bool = False,
) -> str | None:
    """Rephrase a plan using the cheap background session (personalclaw-lite)."""

    try:
        bg, _new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
    except Exception:
        logger.warning("Failed to get background session for plan rephrase", exc_info=True)
        return None
    try:
        result = await rephrase_plan(text, issues, bg, might_not_be_plan=might_not_be_plan)
    finally:
        state.sessions.release(BACKGROUND_KEY)
    if result:
        result, _ = redact_exfiltration_urls(result)
        result, _ = redact_credentials(result)
    return result


async def _stream_background_prompt(state: DashboardState, prompt: str) -> str:
    """Stream *prompt* through the shared background session and collect the text."""
    client, _is_new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
    text = ""
    try:
        # Clear accumulated history so prior utility prompts don't confuse the model
        if hasattr(client, "_history"):
            client._history.clear()
        async for event in client.stream(prompt):
            if event.kind == EVENT_TEXT_CHUNK:
                text += event.text
            elif event.kind == EVENT_PERMISSION_REQUEST:
                await client.reject_tool(event.request_id)
            elif event.kind == EVENT_COMPLETE:
                break
    finally:
        # Clear again so the title prompt doesn't pollute future calls
        if hasattr(client, "_history"):
            client._history.clear()
        state.sessions.release(BACKGROUND_KEY)
    return text


def _parse_title(text: str) -> str:
    """Extract + sanitize the title from a raw title-generation response."""
    title = text.strip().strip('"').strip("'").strip(".")
    # Take only the first line — ignore hallucinated continuations
    title = title.split("\n")[0].strip()
    if not title or title.upper() == "SKIP":
        logger.info("Title generation returned SKIP/empty — topic not clear yet")
        return ""
    # Reject if model generated a conversation continuation instead of a title
    lower = title.lower()
    if lower.startswith("user:") or lower.startswith("assistant:") or len(title) > 60:
        logger.info("Title generation rejected (looks like continuation): %r", title[:80])
        return ""
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    logger.info("Title generated: %r", title[:80])
    return title[:60]


async def _generate_title_via_provider(
    state: DashboardState, messages: list[dict[str, str]]
) -> str:
    """Generate a title using the shared background agent session."""

    prompt = _build_title_prompt(messages)
    if not prompt:
        logger.debug("Title generation skipped — no usable messages")
        return ""

    logger.debug("Title generation prompt (%d chars): %s", len(prompt), prompt[:120])
    text = await _stream_background_prompt(state, prompt)
    return _parse_title(text)


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
        stripped = line.strip()
        if not stripped.lower().startswith("tags:"):
            continue
        raw = stripped[5:].strip()
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
    from personalclaw.dashboard.chat_persistence import _save_session_to_history
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
    _save_session_to_history(state, session, force=True)
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
    """Save the session title to the conversation history file."""

    if state.conversation_log:
        history_key = _history_key_for(session.key)
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
    """
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
    try:
        prompt = _build_title_prompt(session.messages)
        if not prompt:
            logger.debug("Title generation skipped — no usable messages")
            return
        if want_tags:
            prompt += _build_tags_suffix(state)
        text = await _stream_background_prompt(state, prompt)
        title = _parse_title(text)
        logger.info("Auto-title: agent returned %r for session %s", title, session.key)
        if title:
            _apply_title(state, session, title)
            if want_tags:
                _apply_auto_tags(state, session, _parse_tags_line(text))
    except Exception:
        logger.warning("Auto-title failed for session %s", session.key, exc_info=True)


async def api_chat_session_generate_title(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/generate-title — manually trigger title generation."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    logger.info("Manual title generation requested for session %s", name)
    try:
        title = await _generate_title_via_provider(state, session.messages)
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
    title = body.get("title", "").strip()[:200]
    if not title:
        return web.json_response({"error": "title required"}, status=400)
    _apply_title(state, session, title)
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_rename",
        outcome="allowed",
        source="dashboard",
        resources=session.key,
    )
    return web.json_response({"ok": True, "title": title})
