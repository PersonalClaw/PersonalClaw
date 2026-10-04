"""Where a transcript row came from: recorded once, when the row is taken in.

A row records the thread it arrived on, who sent it there and, for a row a chat channel took in,
which channel that was (``source_thread``, ``source_user``, ``source_channel``), the same fields
in a chat's buffer and in its file. The code that takes a row in records them, and nothing does
afterwards:

* the dashboard's own chat records the dashboard for the thread and the sender
  (:data:`DASHBOARD_SOURCE`): what the owner types there, and the rows the chat adds itself (its
  answers, a tool's card, a notice);
* a channel's message records the channel thread it came on, its sender and the channel
  (:func:`arrived_on`), whether the guarded door hands it to a chat (``channel_inbound``) or a
  channel that runs the conversation itself writes it to the conversation's file
  (``llm_helpers.save_conversation_turn``);
* a copy of a row carries the row's own (:func:`source_of`): the chat loaded from its file, a
  fork, a rewound tail, a message that waited in the chat's queue.

A save writes back what each row records (``chat_persistence.save_session_to_history``), never a
source of its own. It used to write the dashboard as the source of every row, so once the
dashboard saved a chat that came from a channel, the channel's turns read as typed in the
dashboard, in the one record of a message the door let in.

What a row records decides whose words it holds (:func:`sent_by_owner`), which every reader of
the owner's own words asks (``own_words.own_words``): a channel's conversation can have other
people in it, and the door lets in everyone the owner trusts to talk to the agent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from types import MappingProxyType

logger = logging.getLogger(__name__)

#: The fields a row records where it came from: the thread it arrived on, who sent it, and the
#: chat channel that took it in.
SOURCE_FIELDS = ("source_thread", "source_user", "source_channel")

#: What a row the dashboard's own chat takes in records for its thread and its sender.
DASHBOARD = "dashboard"
DASHBOARD_SOURCE: Mapping[str, str] = MappingProxyType(
    {"source_thread": DASHBOARD, "source_user": DASHBOARD}
)


def source_of(row: object) -> dict[str, str]:
    """The source *row* records, each field as it has it: a row may record a thread and no sender,
    or nothing at all, and a copy of it records the same."""
    if not isinstance(row, Mapping):
        return {}
    return {f: v for f in SOURCE_FIELDS if isinstance(v := row.get(f), str) and v}


def arrived_on(
    thread: str | None, sender: str | None, channel: str | None = None
) -> dict[str, str]:
    """The source of a message that arrived on the thread *thread* from *sender*, through the
    chat channel *channel* when a channel took it in."""
    return source_of(dict(zip(SOURCE_FIELDS, (thread, sender, channel))))


def shared_source(rows: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """The source every one of *rows* records, for one row made of them all (queued messages run
    as one turn); ``{}`` when they differ, since such a row came from none of them alone."""
    sources = [source_of(row) for row in rows]
    return sources[0] if sources and all(s == sources[0] for s in sources) else {}


def sent_by_owner(row: object) -> bool:
    """Whether the owner sent *row*, read from where it records it came from.

    The dashboard's own chat is the owner's (:data:`DASHBOARD_SOURCE`). A row a chat channel took
    in is the owner's when it names the channel and its sender is the owner that channel keeps
    (``owner_id_for``: the id its owner pairing or its first contact stored); nobody's on a
    channel that knows no owner. In a group, a shared thread, a mailbox or an open direct message
    the door lets in people the owner trusts to talk to the agent, and what they write is theirs.
    Any other source names no one the owner is known to be, so nothing says the owner sent it: a
    program's message through the OpenAI-compatible door (its conversation and client), a
    channel's row saved before rows named their channel (a thread and a sender), one saved with no
    sender (a thread alone).

    A row that records no source at all reads as it always has, by its role and what its meta says
    of its words (``own_words``): an imported conversation, and the row several queued messages
    from different places run as, whose recorded words are those the owner sent
    (``own_words.queued_words``).
    """
    source = source_of(row)
    if not source or source == dict(DASHBOARD_SOURCE):
        return True
    channel = source.get("source_channel", "")
    sender = source.get("source_user", "")
    if not (channel and sender):
        return False
    from personalclaw.channel_delivery import same_user
    from personalclaw.config.credentials import owner_id_for

    try:
        owner = owner_id_for(channel)
    except Exception:  # noqa: BLE001 - fail closed: an owner that cannot be read is nobody's
        logger.warning("channel %r: its owner could not be read", channel, exc_info=True)
        return False
    return same_user(owner, sender)
