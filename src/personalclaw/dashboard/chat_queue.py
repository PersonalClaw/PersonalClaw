"""A chat's message queue: what was sent while a turn ran, each waiting for its own turn.

Kept beside ``dashboard.state`` the way ``ws_state`` and ``approval_state`` are: the chat session
(``state._ChatSession``) takes these as a slotted mixin, so every caller still asks the session.
Items are dicts: ``id`` and ``content``, and what else the message carries when it runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from personalclaw.own_words import OWN_WORDS
from personalclaw.turn_source import DASHBOARD_SOURCE, source_of

#: The item key of who asked for the work a queued message carries on, when someone other than
#: the owner did and no message of theirs is in it: a subagent's report (``run_chat``'s
#: ``asked_for_by``, which its turn runs as).
ASKED_FOR_BY = "asked_for_by"


class ChatQueue:
    """The queue helpers of a chat session, over the session's own ``_queue``."""

    __slots__ = ()

    _queue: list[dict[str, Any]]

    def queue_append(
        self,
        content: str,
        *,
        channel: str = "",
        files: list[str] | None = None,
        own_words: str | None = None,
        source: Mapping[str, str] = DASHBOARD_SOURCE,
        asked_for_by: Mapping[str, str] | None = None,
    ) -> str:
        """Append a message to the queue. Returns the generated queue ID.

        ``channel`` names the chat channel the message came from, when it came from one. That
        channel already shows it, so the turn that runs it does not send it back there.
        ``files`` are its attached files, which the message carries when it runs. ``own_words``
        are the words of it its sender typed, when it holds more than them (``own_words``).
        ``source`` is where it came from (``turn_source``), which its row records when it runs.
        ``asked_for_by`` is who asked for the work it carries on (:data:`ASKED_FOR_BY`).
        """
        qid = uuid.uuid4().hex[:12]
        item: dict[str, Any] = {"id": qid, "content": content, **source_of(source)}
        if channel:
            item["channel"] = channel
        if files:
            item["files"] = list(files)
        if own_words is not None:
            item[OWN_WORDS] = own_words
        if asked_for_by:
            item[ASKED_FOR_BY] = dict(asked_for_by)
        self._queue.append(item)
        return qid

    def queue_retry(
        self,
        content: str,
        *,
        from_channel: bool = False,
        regenerate_hint: str = "",
        asked_for_by: Mapping[str, str] | None = None,
    ) -> str:
        """Queue the turn that just ended to run again, ahead of everything. Returns the queue ID.

        A retry is the SAME message, not a new one: *content* is the text it was sent as, whose row
        is already in the transcript. So its drain adds no row and no bubble, it is never merged
        with a message queued behind it, and it runs as the same turn (`run_chat(_retry=True)`).
        ``retry`` records where the message came from (``channel``: the chat channel the session is
        linked to, which already shows it; ``here``: anywhere else), and ``hint`` a regenerate's
        hint, so the retry is asked the same way, and ``asked_for_by`` who asked for the work it
        carries on (:data:`ASKED_FOR_BY`), so it runs as asked for by them again.
        """
        qid = uuid.uuid4().hex[:12]
        item: dict[str, Any] = {
            "id": qid,
            "content": content,
            "retry": "channel" if from_channel else "here",
        }
        if regenerate_hint:
            item["hint"] = regenerate_hint
        if asked_for_by:
            item[ASKED_FOR_BY] = dict(asked_for_by)
        self._queue.insert(0, item)
        return qid

    def queue_pop(self, index: int = 0) -> dict[str, Any]:
        """Pop a queue item by index. Returns {"id": ..., "content": ...}."""
        return self._queue.pop(index)

    def queue_remove_by_id(self, queue_id: str) -> str | None:
        """Remove a queue item by ID. Returns the content or None if not found."""
        for i, item in enumerate(self._queue):
            if item["id"] == queue_id:
                del self._queue[i]
                return item["content"]
        return None

    def queue_promote(self, queue_id: str) -> bool:
        """Move a queued item to the front, preserving its id. Returns True if found.

        Used by /interrupt's optional ``queue_id`` so the promoted message runs
        next without re-minting its id (the frontend queue card keys off id).
        """
        for i, item in enumerate(self._queue):
            if item["id"] == queue_id:
                if i > 0:
                    self._queue.insert(0, self._queue.pop(i))
                return True
        return False

    @property
    def queue_depth(self) -> int:
        """Number of prompts currently queued behind the active turn."""
        return len(self._queue)
