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

from personalclaw.security import redact_credentials, redact_exfiltration_urls

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
    """
    session_name = f"cron-{job.id}"
    session = state.get_or_create_session(name=session_name, agent=job.agent_id or "")
    session.title = f"Cron: {_redact(job.name)}"

    if not session.linked_session_key:
        # First open — link to the cron's agent session and hydrate its history.
        session.linked_session_key = f"cron:{job.id}"
        msgs = history
        if msgs is None and state.conversation_log is not None:
            try:
                msgs = state.conversation_log.read_messages(f"cron:{job.id}")
            except Exception:
                logger.debug("Failed to read cron history for %s", job.id, exc_info=True)
                msgs = []
        hydrate_session_from_history(session, msgs or [])

    if result_text:
        context = f"# Cron Job Result: {_redact(job.name)}\n\n{_redact(result_text)}"
        if not any(m.get("content") == context for m in session.messages):
            session.append("assistant", context, "msg msg-a")

    state.push_sessions_update()
    return session
