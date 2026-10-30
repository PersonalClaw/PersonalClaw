"""A chat's message queue: what was sent while a turn ran, each waiting for its own turn.

Kept beside ``dashboard.state`` the way ``ws_state`` and ``approval_state`` are: the chat session
(``state._ChatSession``) takes these as a slotted mixin, so every caller still asks the session.
Items are dicts: ``id`` and ``content``, and what else the message carries when it runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from personalclaw import ids
from personalclaw.dashboard import chat_intake
from personalclaw.dashboard.chat_intake import MESSAGE_ID, RECEIPT, Receipt
from personalclaw.own_words import OWN_WORDS, PASTES
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.turn_source import ASKED_FOR_BY, DASHBOARD_SOURCE, source_of

if TYPE_CHECKING:
    from personalclaw.dashboard.state import _ChatSession

#: The item key of a message whose row is in the chat already: a steer the running turn took that
#: its agent then refused (``running_turn.end_steers``). It runs on its own, and adds no second row.
ON_RECORD = "on_record"

#: The item key of a message the owner typed in a surface they are at (the dashboard's chat, the
#: terminal's, the Inbox), or the retry of a turn that ran unbounded. A turn made only of such
#: messages runs until it is done, as one typed into an idle chat does; any other is bounded
#: (``turn_deadline``).
ATTENDED = "attended"


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
        on_record: bool = False,
        pastes: Sequence[Mapping[str, Any]] = (),
        attended: bool = False,
        message_id: str = "",
        receipt: Mapping[str, Any] | None = None,
    ) -> str:
        """Append a message to the queue. Returns the generated queue ID.

        ``channel`` names the chat channel the message came from, when it came from one. That
        channel already shows it, so the turn that runs it does not send it back there.
        ``files`` are its attached files, which the message carries when it runs. ``own_words``
        are the words of it its sender typed, when it holds more than them (``own_words``).
        ``source`` is where it came from (``turn_source``), which its row records when it runs.
        ``asked_for_by`` is who asked for the work it carries on, when someone other than the owner
        did and no message of theirs is in it: a subagent's report, whose turn runs as asked for by
        them and whose row records it (``turn_source.ASKED_FOR_BY``).
        ``on_record`` says its row is in the chat already (:data:`ON_RECORD`).
        ``pastes`` are the blocks pasted into it (``own_words.PASTES``), which its row keeps.
        ``attended`` says the owner typed it where they are (:data:`ATTENDED`).
        ``message_id`` is the message's id, which the row it runs as keeps (one is minted when its
        door gave none), and ``receipt`` what its send was answered (``chat_intake.RECEIPT``).
        """
        qid = uuid.uuid4().hex[:12]
        item: dict[str, Any] = {"id": qid, "content": content, **source_of(source)}
        item[MESSAGE_ID] = message_id or ids.new_id("msg")
        if receipt:
            item[RECEIPT] = dict(receipt)
        if channel:
            item["channel"] = channel
        if files:
            item["files"] = list(files)
        if own_words is not None:
            item[OWN_WORDS] = own_words
        if pastes:
            item[PASTES] = [dict(p) for p in pastes]
        if asked_for_by:
            item[ASKED_FOR_BY] = dict(asked_for_by)
        if on_record:
            item[ON_RECORD] = True
        if attended:
            item[ATTENDED] = True
        self._queue.append(item)
        return qid

    def take_in_at_door(
        self, state: Any, message: str, role: str = "user", cls: str = "msg msg-u", **fields: Any
    ) -> Receipt:
        """Take *message* into this chat at a door whose message names no id of its own (a
        channel's, an automation's, a helper's report, a loop's cycle, a program's): the one way a
        message enters a chat (``chat_intake.take_in``), under an id the gateway mints. Queued while
        a turn runs, else written as the row its turn starts with, which the caller then starts.
        It starts its turn as a *role* row of *cls*; *fields* are what else the door says of it
        (``chat_intake.Intake``)."""
        intake = chat_intake.Intake.at_door(message, role=role, cls=cls, **fields)
        taken = chat_intake.take_in(state, cast("_ChatSession", self), intake)
        if isinstance(taken, chat_intake.Refusal):  # a new id names no turn and repeats nothing
            raise RuntimeError(f"a door's message was refused: {taken.code}")
        return taken

    def queue_retry(
        self,
        content: str,
        *,
        from_channel: bool = False,
        regenerate_hint: str = "",
        asked_for_by: Mapping[str, str] | None = None,
        attended: bool = False,
    ) -> str:
        """Queue the turn that just ended to run again, ahead of everything. Returns the queue ID.

        A retry is the SAME message, not a new one: *content* is the text it was sent as, whose row
        is already in the transcript. So its drain adds no row and no bubble, it is never merged
        with a message queued behind it, and it runs as the same turn (`run_chat(_retry=True)`).
        ``retry`` records where the message came from (``channel``: the chat channel the session is
        linked to, which already shows it; ``here``: anywhere else), and ``hint`` a regenerate's
        hint, so the retry is asked the same way, and ``asked_for_by`` who asked for the work it
        carries on (``turn_source.ASKED_FOR_BY``), so it runs as asked for by them again.
        ``attended`` says the attempt it repeats ran unbounded (:data:`ATTENDED`), so it runs
        unbounded again; a bounded attempt's retry is bounded the same.
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
        if attended:
            item[ATTENDED] = True
        self._queue.insert(0, item)
        return qid

    def queue_pop(self, index: int = 0) -> dict[str, Any]:
        """Pop a queue item by index. Returns {"id": ..., "content": ...}."""
        return self._queue.pop(index)

    def queued(self, queue_id: str) -> dict[str, Any]:
        """The queued message *queue_id*, which is waiting (``KeyError`` when it is not)."""
        for item in self._queue:
            if item["id"] == queue_id:
                return item
        raise KeyError(queue_id)

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


def shown(item: Mapping[str, Any]) -> dict[str, Any]:
    """A queued message as a page is told it, in a ``queue_push`` frame or a read of the chat: its
    words with what could leak masked, as in every message a page is sent, and the blocks pasted
    into it (``own_words.PASTES``), so the queue shows each as its chip and gives it back to the
    composer with the message."""
    words, _ = redact_exfiltration_urls(str(item.get("content") or ""))
    words, _ = redact_credentials(words)
    pastes = item.get(PASTES)
    return {"content": words, **({PASTES: [dict(p) for p in pastes]} if pastes else {})}


def queued_pastes(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The blocks pasted into *items*, messages taken off a chat's queue to run as one turn, for
    the row that turn starts with. Each message numbers its own blocks from one, so blocks merged
    from two messages are numbered again, in order: each is then its own chip."""
    blocks = [dict(p) for item in items for p in item.get(PASTES) or ()]
    if len(items) > 1:
        for n, block in enumerate(blocks, start=1):
            block["seq"] = n
    return blocks
