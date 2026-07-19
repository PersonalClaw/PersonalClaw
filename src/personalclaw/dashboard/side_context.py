"""Frozen-snapshot builder for a side chat.

Reads the parent session's VISIBLE conversation (user/assistant) read-only and
formats it as a plain-text snapshot. Never mutates the parent; never reads from
disk or memory — only the in-memory ``_ChatSession.messages`` it is handed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from personalclaw.dashboard.side_prompts import build_side_prompt
from personalclaw.dashboard.side_state import SideState
from personalclaw.security import redact_credentials, redact_exfiltration_urls

if TYPE_CHECKING:
    from personalclaw.dashboard.state import _ChatSession

# Cap the snapshot so a very long parent conversation can't blow the context.
_MAX_SNAPSHOT_MESSAGES = 40
_MAX_SNAPSHOT_CHARS = 24_000


def _redact(text: str) -> str:
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def build_snapshot(session: "_ChatSession") -> str:
    """Format the parent's visible messages as a read-only text snapshot."""
    visible = [m for m in session.messages if m.get("role") in ("user", "assistant")]
    visible = visible[-_MAX_SNAPSHOT_MESSAGES:]
    lines: list[str] = []
    for m in visible:
        role = "User" if m.get("role") == "user" else "Assistant"
        content = _redact(str(m.get("content", "")).strip())
        if content:
            lines.append(f"{role}: {content}")
    snapshot = "\n\n".join(lines)
    if len(snapshot) > _MAX_SNAPSHOT_CHARS:
        snapshot = "… [earlier turns truncated]\n\n" + snapshot[-_MAX_SNAPSHOT_CHARS:]
    return snapshot


def build_side_message(session: "_ChatSession", side: SideState, question: str) -> str:
    """Build the full prompt for one side turn: parent snapshot + prior side
    Q&A in this side chat + the new question."""
    snapshot = build_snapshot(session)
    prior = "\n\n".join(
        f"{'You' if m.role == 'user' else 'Side'}: {m.content}" for m in side.messages
    )
    return build_side_prompt(snapshot, question.strip(), prior)
