"""Where a rewrite of a chat's transcript began, for the counts that index its messages.

An /undo, a regenerate and an edit-and-resend save a chat's transcript shorter or changed, with
``force`` (``chat_persistence.save_session_to_history``). A count of how many of its messages
something has read (consolidation's ``last_consolidated``) then has to stop where the rewrite
began, or the messages written in the removed ones' place are never read: a forced save lowers it
there, and a consolidation pass that read the transcript before the rewrite marks no further
(``history.ConversationLog.mark_consolidated``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from personalclaw.instants import as_instant


def _same_message(was: Mapping[str, Any], now: Mapping[str, Any]) -> bool:
    """Whether *now*, at a place in a transcript, is still the message *was* there: the same
    speaker's, at the same time or in the same words. A turn a channel wrote to the file and to
    the chat open for it carries two times; a message sent in a removed one's place has its own."""
    if was.get("role") != now.get("role"):
        return False
    if was.get("ts") == now.get("ts") or was.get("content") == now.get("content"):
        return True
    return as_instant(was.get("ts")) == as_instant(now.get("ts"))


def messages_unchanged(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> int:
    """How many of *after*'s first messages are *before*'s, up to the first that is not: where a
    rewrite of a transcript began."""
    kept = 0
    for was, now in zip(before, after):
        if not _same_message(was, now):
            break
        kept += 1
    return kept
