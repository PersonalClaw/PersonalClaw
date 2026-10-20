"""Bidirectional cron→dashboard chat threading.

A scheduled job's results thread into a persistent dashboard chat session
``cron-{id}`` that is *linked* to the cron's own agent session (``cron:{id}``).
The link means the dashboard chat IS the cron's conversation: it hydrates from
the cron's history on first open, and sending a message there continues the
same agent session.

This is the deliberate INVERSE of the side chat: the side chat reads a frozen
snapshot and writes nothing back; this writes the main transcript and continues
the live conversation. Same Session primitives, opposite isolation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw import session_keys
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.turn_source import FROM_OUTSIDE

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.schedule import ScheduleJob

logger = logging.getLogger(__name__)

_HYDRATE_LIMIT = 50  # most-recent N turns hydrated into the dashboard session


def _redact(text: str) -> str:
    out, _ = redact_exfiltration_urls(text or "")
    out, _ = redact_credentials(out)
    return out


def hydrate_session_from_history(session: "_ChatSession", messages: list[dict[str, Any]]) -> None:
    """Append the last N user/assistant turns to *session* without broadcasting.

    Dedups against what's already on the session (by role+content) and redacts.
    ``broadcast=False`` avoids an SSE storm on first open.
    """
    recent = [m for m in messages if m.get("role") in ("user", "assistant")][-_HYDRATE_LIMIT:]
    existing = {(m.get("role"), m.get("content")) for m in session.messages}
    for m in recent:
        role = m.get("role", "")
        content = _redact(m.get("content", "") or "")
        if not content or (role, content) in existing:
            continue
        cls = "msg msg-u" if role == "user" else "msg msg-a"
        session.append(role, content, cls, broadcast=False)
        existing.add((role, content))


def inject_schedule_result_to_session(
    state: "DashboardState",
    job: "ScheduleJob",
    result_text: str,
    *,
    history: list[dict[str, Any]] | None = None,
) -> "_ChatSession":
    """Create/update the linked ``cron-{id}`` dashboard session for *job*.

    On first open the session is linked to ``cron:{id}`` and hydrated from the
    cron's conversation history; subsequent calls thread the new result in
    (deduped). Returns the dashboard session.

    The chat is one ongoing chat. After a restart its kept transcript comes back whole
    (``get_or_create_session``), so its owner's earlier turns there are given to the model
    again, and only a chat that holds nothing yet is filled from the cron's history: a kept
    one is threaded the result, as an open one is, rather than handed the cron's runs since
    as turns of their own beside the results already in it.
    """
    session_name = session_keys.SCHEDULE_CHAT.key(job.id)
    session = state.get_or_create_session(name=session_name, agent=job.agent_id or "")
    session.title = f"Cron: {_redact(job.name)}"

    if not session.linked_session_key:
        # First open here — link to the cron's agent session, and hydrate its history into a
        # new chat.
        session.linked_session_key = session_keys.TRIGGER.key(job.id)
        if not session.messages:
            msgs = history
            if msgs is None and state.conversation_log is not None:
                try:
                    msgs = state.conversation_log.read_messages(f"cron:{job.id}")
                except Exception:
                    logger.debug("Failed to read cron history for %s", job.id, exc_info=True)
                    msgs = []
            hydrate_session_from_history(session, msgs or [])

    if result_text:
        # What the run produced came from its action, a program or a page as much as an agent,
        # and no model of this chat wrote it. The chat shows it as it is, and it records where it
        # came from (`turn_source.FROM_OUTSIDE`), so every model it is handed to reads it
        # through the injection screen, fenced as data with the run as its source, never as an
        # answer of its own (`turn_source.model_text`).
        context = f"# Cron Job Result: {_redact(job.name)}\n\n{_redact(result_text)}"
        if not any(m.get("content") == context for m in session.messages):
            session.append(
                "assistant",
                context,
                "msg msg-a",
                meta={
                    FROM_OUTSIDE: {
                        "what": f"The result of the scheduled run of “{_redact(job.name)}”",
                        "source": f"trigger:{job.id}",
                        "source_type": "schedule_result",
                        "source_id": job.id,
                    }
                },
            )

    state.push_sessions_update()
    return session
