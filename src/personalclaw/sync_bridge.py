"""Session handoff between Dashboard and the messaging channel.

Two capabilities:
1. Dashboard → channel: POST /api/chat/sessions/{session}/handoff creates a channel
   DM thread and symlinks the JSONL history so the user can continue.
2. Channel !resume: lists recent sessions, symlinks the chosen one's JSONL
   to the current thread so context_builder injects the full history.
"""

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.channel_delivery import ChannelDelivery
    from personalclaw.history import ConversationLog

logger = logging.getLogger(__name__)


def list_recent_sessions(
    conversation_log: "ConversationLog",
    limit: int = 10,
) -> list[dict]:
    """Return recent sessions across all surfaces, newest first."""
    sessions = conversation_log.list_sessions()
    result: list[dict] = []
    for s in sessions[: limit * 2]:
        if len(result) >= limit:
            break
        key = s.get("key", "")
        if not key:
            continue
        # Restricted (incognito/temporary) sessions stay out of history —
        # they must not surface in the channel resume list either.
        if s.get("memory_mode") in ("incognito", "temporary"):
            continue
        if key.startswith("dashboard:") or key.startswith("dashboard_"):
            source = "dashboard"
        elif key.startswith("cron:") or key.startswith("cron_"):
            source = "cron"
        else:
            source = "channel"
        result.append(
            {
                "key": key,
                "title": s.get("title", key),
                "source": source,
                "modified": s.get("modified", 0),
            }
        )
    return result


def format_session_list(sessions: list[dict]) -> str:
    """Format sessions as a numbered channel message."""
    if not sessions:
        return "No recent sessions found."
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    lines = ["*Recent sessions:*\n"]
    now = time.time()
    for i, s in enumerate(sessions, 1):
        age_mins = int((now - s["modified"]) / 60)
        if age_mins < 60:
            age = f"{age_mins}m ago"
        elif age_mins < 1440:
            age = f"{age_mins // 60}h ago"
        else:
            age = f"{age_mins // 1440}d ago"
        icon = {"dashboard": "🖥️", "channel": "💬", "cron": "⏰"}.get(s["source"], "📝")
        title = s["title"][:60] or s["key"][:40]
        title, _ = redact_credentials(title)
        title, _ = redact_exfiltration_urls(title)
        lines.append(f"`{i}` {icon} {title}  _{age}_")
    lines.append("\n_Click a session's resume button to continue._")
    return "\n".join(lines)


async def handoff_to_channel(
    delivery: "ChannelDelivery",
    owner_id: str,
    conversation_log: "ConversationLog",
    session_key: str,
    title: str = "",
    channel: str | None = None,
    sessions: object | None = None,
) -> str | None:
    """Hand off a dashboard session to a new channel DM thread.

    Links the session via ``set_channel_link`` so bidirectional sync works.
    Delivers through the provider-agnostic :class:`ChannelDelivery` (open_dm +
    deliver_text) — core never touches a vendor client. Returns the thread_ts,
    or None on failure.
    """
    messages = conversation_log.read_messages(session_key)
    if not messages:
        return None

    if not title:
        meta = conversation_log.get_metadata(session_key)
        title = meta.get("title", session_key)

    preview = ""
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            preview = m["content"][:120]
            break

    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    title, _ = redact_credentials(title)
    title, _ = redact_exfiltration_urls(title)
    preview, _ = redact_credentials(preview)
    preview, _ = redact_exfiltration_urls(preview)

    try:
        target_channel = channel or await delivery.open_dm(owner_id)
        thread_ts = await delivery.deliver_text(
            target_channel,
            f"📲 *{title}*\n>{preview}\n\n_Reply to continue this session._",
        )

        # Link via SessionMap instead of symlink
        if sessions and hasattr(sessions, "set_channel_link"):
            try:
                sessions.set_channel_link(session_key, thread_ts, target_channel)  # type: ignore[union-attr]
            except Exception:
                logger.warning("Channel thread created but session link failed", exc_info=True)

        return thread_ts
    except Exception:
        logger.warning("Handoff to channel failed", exc_info=True)
        return None


# ── Resume state: track pending !resume selections ──
_PENDING_RESUME_TTL = 5 * 60  # 5 minutes

_pending_resumes: dict[str, tuple[float, list[dict]]] = {}
_pending_resume_msg_ts: dict[str, tuple[str, str]] = {}  # session_key → (bot_list_ts, user_cmd_ts)


def set_pending_resume(
    session_key: str,
    sessions: list[dict],
    bot_list_ts: str = "",
    user_cmd_ts: str = "",
) -> None:
    """Store the session list so a follow-up number reply can resolve it."""
    _pending_resumes[session_key] = (time.monotonic(), sessions)
    if bot_list_ts:
        _pending_resume_msg_ts[session_key] = (bot_list_ts, user_cmd_ts)


def peek_pending_resume(session_key: str) -> list[dict] | None:
    """Return pending resume sessions without consuming them, or None."""
    entry = _pending_resumes.get(session_key)
    if entry is None:
        return None
    created_at, sessions = entry
    if time.monotonic() - created_at > _PENDING_RESUME_TTL:
        _pending_resumes.pop(session_key, None)
        _pending_resume_msg_ts.pop(session_key, None)
        return None
    return sessions


def pop_pending_resume(session_key: str) -> list[dict] | None:
    """Pop and return pending resume sessions, or None if expired."""
    _pending_resume_msg_ts.pop(session_key, None)  # always clean
    entry = _pending_resumes.pop(session_key, None)
    if entry is None:
        return None
    created_at, sessions = entry
    if time.monotonic() - created_at > _PENDING_RESUME_TTL:
        return None
    return sessions


def pop_pending_resume_ts(session_key: str) -> tuple[str, str]:
    """Pop and return (bot_list_ts, user_cmd_ts) for cleanup."""
    return _pending_resume_msg_ts.pop(session_key, ("", ""))
