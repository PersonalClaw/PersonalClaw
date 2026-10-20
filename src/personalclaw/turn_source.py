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
people in it, and the door lets in everyone the owner trusts to talk to the agent. It decides who
asked for the turn a row starts too (:func:`asked_by`), which is what a change to her memory that
turn makes waits on (``memory_writes.asker``), what none of her standing grants answers
(``approval_grants.stands_for_work``) and what of her chats it may not search
(``chat_recall.not_searched_for``), who asked for what that turn learns from its words
(:func:`taught_by`), and how a model is shown such a line in any history of the conversation
(:func:`turn_line`): whole, fenced, and labelled as someone else's words. Every other row of a
conversation is its turn's (:func:`by_turn`), so memory consolidation, which reads only the turns
the owner asked for (``own_words.her_turns``), takes nothing of such a turn at all.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

#: A transcript row, as whichever reader holds it: a chat's buffer, a file's lines, a copy.
Row = TypeVar("Row", bound=Mapping[str, Any])

#: The fields a row records where it came from: the thread it arrived on, who sent it, and the
#: chat channel that took it in.
SOURCE_FIELDS = ("source_thread", "source_user", "source_channel")

#: The ``meta`` key of a row several queued messages run as when they came from different places:
#: the source each of them records, in order, written by the chat's queue as it merges them. Such a
#: row records no source of its own (:func:`shared_source`), so this is what says whose they were.
QUEUED_FROM = "queued_from"

#: The ``meta`` key of a row whose words PersonalClaw took in from outside as they came, which no
#: model of the conversation wrote: a scheduled run's result opened as a chat
#: (``dashboard.schedule_inject``). It holds what a model is told they are and where they came
#: from (``what``, and the fence's ``source``, ``source_type`` and ``source_id``), and every model
#: is handed them through the door every text from outside takes into a prompt
#: (:func:`model_text`). The row keeps its words as a person reads them.
FROM_OUTSIDE = "from_outside"

#: The roles of the row a turn starts with: a person's message, and the row an automation's result,
#: a subagent's report or a loop's nudge starts one with. Every row after it, up to the next such
#: row, is that turn's (:func:`by_turn`): its calls, its approvals, its answer.
TURN_STARTS = frozenset({"user", "inject", "subagent", "nudge"})

#: The ``meta`` key of a row of hers the running turn took while it answered (a steer). Written only
#: by the dashboard's running turn (``running_turn.take_steer``); a send's own meta never carries it
#: (``chat_handlers`` drops it). Such a row joins the turn it was sent into and starts none.
STEERED = "steered"

#: The ``meta`` key of a row that starts a turn for work someone other than the owner asked for,
#: when no message of theirs is in it: the report of a subagent their turn started. It holds their
#: source (``memory_writes.asked_for_work``), which :func:`asked_by` reads, and a queued message
#: carries it under the same key until its row is written (``chat_queue``).
ASKED_FOR_BY = "asked_for_by"

#: How a model is told a user line someone other than the owner sent is not hers, ahead of the
#: line, fenced (:func:`theirs`): every history of the conversation a model is handed shows such a
#: line this way.
NOT_THE_USERS_WORDS = "SENT BY SOMEONE OTHER THAN THE USER (not the user's words)"
#: The same for a row the owner's queued message and someone else's were run as together.
PARTLY_THE_USERS_WORDS = (
    "SENT BY THE USER AND SOMEONE ELSE TOGETHER (not all of it is the user's words)"
)

#: What a row the dashboard's own chat takes in records for its thread and its sender.
DASHBOARD = "dashboard"
DASHBOARD_SOURCE: Mapping[str, str] = MappingProxyType(
    {"source_thread": DASHBOARD, "source_user": DASHBOARD}
)

#: Who asked, where nothing can say who did: a record of lasting work that is there and cannot be
#: read (``lasting_work.recorded``), an answer the gateway did not give the tool server an agent
#: CLI runs (``memory_writes.asker``). It names no one the owner is known to be, so what it asked
#: for is not hers, and it is named as "a sender no record names" (:func:`named`).
UNNAMED: Mapping[str, str] = MappingProxyType({"source_thread": "unnamed"})


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
    (``owner_id_for``: the id its owner pairing, or its setup, stored); nobody's on a
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


def sent_by_someone_else(row: object) -> bool:
    """Whether *row* is a message the channel that took it in knows someone other than its owner
    sent: it names the channel and its sender, the channel keeps an owner, and the sender is not
    that owner. Stricter than ``not sent_by_owner``, which is true of a row nothing can place as
    well (a channel that knows no owner, a row saved before rows named their channel, a row several
    queued messages run as): what is removed on this ground must be known to be theirs."""
    if not isinstance(row, Mapping) or row.get("role") != "user" or _queued(row) is not None:
        return False
    source = source_of(row)
    channel, sender = source.get("source_channel", ""), source.get("source_user", "")
    if not (channel and sender):
        return False
    from personalclaw.channel_delivery import same_user
    from personalclaw.config.credentials import owner_id_for

    try:
        owner = owner_id_for(channel)
    except Exception:  # noqa: BLE001 - an owner that cannot be read says nobody is someone else
        logger.warning("channel %r: its owner could not be read", channel, exc_info=True)
        return False
    return bool(owner) and not same_user(owner, sender)


def _queued(row: object) -> list[dict[str, str]] | None:
    """The sources a row several queued messages run as records for them (:data:`QUEUED_FROM`),
    or ``None`` for any other row."""
    meta = row.get("meta") if isinstance(row, Mapping) else None
    queued = meta.get(QUEUED_FROM) if isinstance(meta, Mapping) else None
    return [source_of(s) for s in queued] if isinstance(queued, list) else None


def _asked_for(row: object) -> dict[str, str]:
    """Who asked for the work the turn a row starts carries on, as the row records it
    (:data:`ASKED_FOR_BY`), or ``{}``."""
    meta = row.get("meta") if isinstance(row, Mapping) else None
    asked = meta.get(ASKED_FOR_BY) if isinstance(meta, Mapping) else None
    return source_of(asked) if isinstance(asked, Mapping) else {}


def provenance(row: object) -> dict[str, Any]:
    """What *row* records of where it came from, for a copy of it that keeps its role and text: its
    source (:func:`source_of`), for a row queued messages from different places run as, theirs,
    for a row of words taken in from outside, that (:data:`FROM_OUTSIDE`), and who asked for the
    work its turn carries on (:data:`ASKED_FOR_BY`). A reader the copy reaches tells whose words
    and whose turn it holds as it would from the row itself."""
    kept: dict[str, Any] = source_of(row)
    meta: dict[str, Any] = {}
    queued = _queued(row)
    if queued is not None:
        meta[QUEUED_FROM] = queued
    outside = _from_outside(row)
    if outside is not None:
        meta[FROM_OUTSIDE] = outside
    if asked := _asked_for(row):
        meta[ASKED_FOR_BY] = asked
    if meta:
        kept["meta"] = meta
    return kept


def _from_outside(row: object) -> dict[str, str] | None:
    """What a row of words taken in from outside records of them (:data:`FROM_OUTSIDE`), or
    ``None`` for any other row."""
    meta = row.get("meta") if isinstance(row, Mapping) else None
    taken = meta.get(FROM_OUTSIDE) if isinstance(meta, Mapping) else None
    if not isinstance(taken, Mapping):
        return None
    return {key: value for key, value in taken.items() if isinstance(value, str)}


def model_text(row: Mapping[str, Any], text: str | None = None) -> str:
    """*text* (the row's own by default) as a model may be handed it in any history of the
    conversation: as it is, except a row of words taken in from outside (:data:`FROM_OUTSIDE`),
    which goes through the door every such text takes into a prompt (``outside_text.admit``),
    screened and fenced with the source it records; one the screen refuses is a sentence saying
    what was withheld, never its words."""
    said = str(row.get("content", "")) if text is None else text
    outside = _from_outside(row)
    if outside is None:
        return said
    from personalclaw.outside_text import admit, withheld

    admitted = admit(
        said,
        source=outside.get("source") or "outside",
        source_type=outside.get("source_type", ""),
        source_id=outside.get("source_id", ""),
        transformation_path="history",
    )
    if admitted.refused:
        return withheld(outside.get("what") or "Text from outside", admitted.refused)
    return admitted.text


def asked_by(row: object) -> dict[str, str]:
    """Who asked for the turn *row* starts, when the owner did not: the source of the first of its
    messages someone other than the owner sent (:func:`sent_by_owner`), else whoever asked for the
    work it carries on, as it records (:data:`ASKED_FOR_BY`: a subagent's report). ``{}`` when the
    owner asked for all of it: a row with no source reads as hers, as it does for memory. A row
    several queued messages run as is asked for by everyone who sent one of them
    (:data:`QUEUED_FROM`)."""
    sources = _queued(row)
    if sources is None:
        sources = [source_of(row)]
    sent = next((source for source in sources if not sent_by_owner(source)), {})
    return sent or _asked_for(row)


def starts_a_turn(row: object) -> bool:
    """Whether *row* starts a turn of its conversation (:data:`TURN_STARTS`). A message she sent
    into a running turn (:data:`STEERED`) joins that turn and starts none."""
    if not isinstance(row, Mapping) or row.get("role") not in TURN_STARTS:
        return False
    meta = row.get("meta")
    return not (isinstance(meta, Mapping) and meta.get(STEERED))


def by_turn(rows: Iterable[Row]) -> list[list[Row]]:
    """*rows*, a conversation's rows in order, as its turns: each from a row that starts one
    (:func:`starts_a_turn`) up to the next, so a turn's calls, approvals, notices and answer are its
    own, and whoever asked for it (:func:`asked_by` of its first row) asked for all of them. Rows
    before the first such row are a turn no row started (a conversation brought over from another
    tool can open with an answer)."""
    turns: list[list[Row]] = []
    for row in rows:
        if not turns or starts_a_turn(row):
            turns.append([row])
        else:
            turns[-1].append(row)
    return turns


def taught_by(row: object) -> dict[str, str]:
    """Who asked for what the turn *row* starts learns from its words, when the owner did not.

    That learning reads only the words the owner sent (``own_words.own_words``). So a row several
    queued messages run as teaches her words as hers when she sent one of them, whoever sent the
    others: ``{}``. Any other row is asked for as its turn is (:func:`asked_by`), a row none of
    whose messages she sent included. What the turn did is asked for by everyone whose message it
    ran (:func:`asked_by`), since nothing says which of them it did it for."""
    queued = _queued(row)
    if queued is not None and any(map(sent_by_owner, queued)):
        return {}
    return asked_by(row)


def fence_source(source: Mapping[str, str]) -> str:
    """What a fence around *source*'s words names as their source: ``channel:<channel>:<sender>``,
    or ``sender:<sender>`` for a source that names no channel."""
    channel = source.get("source_channel", "")
    return (f"channel:{channel}:" if channel else "sender:") + source.get("source_user", "")


def theirs(row: object, text: str | None = None) -> str:
    """*row*, a user line the owner did not send (:func:`asked_by`), as a model is shown it:
    labelled as not the user's words, then *text* (the row's own by default) whole, through the
    door every text from outside takes into a prompt (``outside_text.admit``): screened, and fenced
    as its sender's. One the screen refuses is a sentence saying it was withheld, never its words.
    A row the owner's queued message and someone else's were run as together is labelled as only
    partly hers. Every history of the conversation a model is handed reads such a line so; memory
    consolidation is shown none of it (``own_words.her_turns``)."""
    from personalclaw.outside_text import admit, withheld

    said = str(row.get("content") or "") if text is None and isinstance(row, Mapping) else text
    queued = _queued(row) or []
    label = (
        PARTLY_THE_USERS_WORDS
        if any(map(sent_by_owner, queued)) and not all(map(sent_by_owner, queued))
        else NOT_THE_USERS_WORDS
    )
    sender = fence_source(asked_by(row))
    admitted = admit(said or "", source=sender)
    if admitted.refused:
        # Named by the ids the source records: a display name is the sender's own text.
        return f"{label}: {withheld(f'A message from {sender}', admitted.refused)}"
    return f"{label}: {admitted.text}"


def turn_line(row: Mapping[str, Any], text: str | None = None) -> str:
    """One turn of a conversation as a model reads it in a history of it: its role and *text* (the
    row's own by default), ``User: …``, ``Assistant: …``, ``Summary: …``, except that a user line
    someone other than the owner asked for reads as :func:`theirs` shows it, and a row of words
    taken in from outside as :func:`model_text` hands it on. The history a fresh runtime is given
    back, a compressed one, and the summary background compression keeps read each turn through
    this."""
    said = str(row.get("content", "")) if text is None else text
    if _from_outside(row) is not None:
        return f"{str(row.get('role', '')).title()}: {model_text(row, said)}"
    if row.get("role") == "user" and asked_by(row):
        return theirs(row, said)
    return f"{str(row.get('role', '')).title()}: {said}"


def named(source: Mapping[str, str]) -> str:
    """Who *source* names, in words a sentence can hold: the name the channel's trust list holds
    for the sender with their id, on the channel as the owner calls it (``Jonas (U0JONAS) on
    Slack``); a source that names no channel, by the sender it records."""
    sender = source.get("source_user", "")
    channel = source.get("source_channel", "")
    if not channel:
        return f"“{sender}”" if sender else "a sender no record names"
    from personalclaw.channel_trust import channel_display_name, sender_name

    name = sender_name(channel, sender) if sender else ""
    who = f"{name} ({sender})" if name and name != sender else (sender or "someone")
    return f"{who} on {channel_display_name(channel)}"
