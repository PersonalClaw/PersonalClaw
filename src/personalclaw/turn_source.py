"""Where a transcript row came from: recorded once, when the row is taken in.

A row records the thread it arrived on and who sent it there (``source_thread``,
``source_user``), the same two fields in a chat's buffer and in its file. The code that takes a
row in records them, and nothing does afterwards:

* the dashboard's own chat records the dashboard for both (:data:`DASHBOARD_SOURCE`): what the
  owner types there, and the rows the chat adds itself (its answers, a tool's card, a notice);
* a channel's message records the channel thread it came on and its sender (:func:`arrived_on`),
  whether the guarded door hands it to a chat (``channel_inbound``) or a channel that runs the
  conversation itself writes it to the conversation's file (``llm_helpers.save_conversation_turn``);
* a copy of a row carries the row's own (:func:`source_of`): the chat loaded from its file, a
  fork, a rewound tail, a message that waited in the chat's queue.

A save writes back what each row records (``chat_persistence.save_session_to_history``), never a
source of its own. It used to write the dashboard as the source of every row, so once the
dashboard saved a chat that came from a channel, the channel's turns read as typed in the
dashboard, in the one record of a message the door let in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

#: The two fields a row records where it came from: the thread it arrived on, and who sent it.
SOURCE_FIELDS = ("source_thread", "source_user")

#: What a row the dashboard's own chat takes in records for both.
DASHBOARD = "dashboard"
DASHBOARD_SOURCE: Mapping[str, str] = MappingProxyType(
    {field: DASHBOARD for field in SOURCE_FIELDS}
)


def source_of(row: object) -> dict[str, str]:
    """The source *row* records, each field as it has it: a row may record a thread and no sender,
    or nothing at all, and a copy of it records the same."""
    if not isinstance(row, Mapping):
        return {}
    return {f: v for f in SOURCE_FIELDS if isinstance(v := row.get(f), str) and v}


def arrived_on(thread: str | None, sender: str | None) -> dict[str, str]:
    """The source of a message that arrived on the thread *thread* from *sender*."""
    return source_of(dict(zip(SOURCE_FIELDS, (thread, sender))))


def shared_source(rows: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """The source every one of *rows* records, for one row made of them all (queued messages run
    as one turn); ``{}`` when they differ, since such a row came from none of them alone."""
    sources = [source_of(row) for row in rows]
    return sources[0] if sources and all(s == sources[0] for s in sources) else {}
