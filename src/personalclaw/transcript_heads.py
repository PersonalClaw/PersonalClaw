"""What the chat list reads of a transcript's first lines, and the one store that keeps it.

A transcript's head (:class:`_Head`) is its metadata line, how many messages it holds and its first
prompt. ``history._read_head`` reads one from the file; :class:`_TranscriptHeads` keeps each for as
long as the file is unchanged (``history._HEADS``, one for the process). The line readers here
(:func:`_metadata_line`, :func:`_message_line`) are the ones every transcript reader goes through.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from personalclaw.instants import as_instant


@dataclass(frozen=True)
class _Head:
    """What the chat list reads of one transcript: its metadata line, how many messages it holds,
    and — when the metadata names no title — its first prompt.

    ``meta`` is the metadata line (empty when the first line is not one) and ``head_bytes`` that
    line's length. A head read for its metadata alone (:meth:`ConversationLog.get_metadata`) is
    not ``complete``: ``messages`` and ``first_prompt`` are ``None`` until a listing reads them.
    """

    meta: Mapping[str, Any]
    head_bytes: int
    messages: int | None
    first_prompt: str | None
    complete: bool


def _with_instants(record: dict) -> dict:
    """*record*, a parsed transcript line, with each time it holds read as an instant.

    A message's ``ts`` and the metadata's ``*_at`` times were written as this machine's local
    time with no offset until stamps carried one, and a reader in another zone read them hours
    off. Each is read here as the instant it was (:func:`personalclaw.instants.as_instant`), so
    every reader of a transcript sees the same one; a stamp with an offset keeps its text.
    """
    for key, value in record.items():
        if (key == "ts" or key.endswith("_at")) and isinstance(value, str):
            record[key] = as_instant(value)
    return record


def _metadata_line(first: bytes) -> dict:
    """The metadata a transcript's first line holds, or ``{}`` when it is not a metadata line."""
    if not first.strip():
        return {}
    try:
        data = json.loads(first)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("_type") != "metadata":
        return {}
    return _with_instants(data)


def _mode_of(metadata: Mapping[str, Any], unreadable: str) -> str | None:
    """The memory mode *metadata* names: ``None`` for none, *unreadable* for a mode no string."""
    mode = metadata.get("memory_mode")
    return mode if mode is None or isinstance(mode, str) else unreadable


def _listed_title(head: _Head | None, name: str) -> str:
    """The title the chat list shows: the metadata's, else the first user message, else *name*."""
    return (head.meta.get("title") or head.first_prompt or name) if head is not None else name


def _recorded_count(meta: Mapping[str, Any], message_bytes: int) -> int | None:
    """The message count the metadata records, while the file still holds exactly the message
    bytes it was recorded with — the dashboard's save and the import both record them. A writer
    that appended since (a channel app's ``append``) changed those bytes: ``None``, count them."""
    recorded, recorded_bytes = meta.get("message_count"), meta.get("message_bytes")
    if type(recorded) is int and type(recorded_bytes) is int and recorded_bytes == message_bytes:
        return recorded
    return None


def _message_line(raw: bytes) -> dict | None:
    """A transcript line as :meth:`ConversationLog._read_messages` reads it: a message, or
    ``None`` for a blank, unparsable or metadata line."""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("_type") == "metadata":
        return None
    return data


class _TranscriptHeads:
    """Each transcript's :class:`_Head`, kept for as long as the file is unchanged.

    ONE store for every :class:`ConversationLog` in the process — the dashboard's, the search
    index's, a script's — so a log made after another lists without opening a transcript again.
    Two logs each keeping their own had every new one read all of them: 24,000 opens to list
    12,005 chats, which the search index's pass paid every five minutes (measured). Keyed by the
    file's path and valid for its :func:`_signature`; it holds metadata lines, never a message.
    """

    #: A bound far past any real history: cleared whole when crossed.
    LIMIT = 200_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kept: dict[str, tuple[tuple[int, ...], _Head]] = {}

    def get(self, path: str, signature: tuple[int, ...]) -> _Head | None:
        with self._lock:
            kept = self._kept.get(path)
        return kept[1] if kept is not None and kept[0] == signature else None

    def keep(self, path: str, signature: tuple[int, ...], head: _Head) -> None:
        with self._lock:
            if len(self._kept) >= self.LIMIT:
                self._kept.clear()
            self._kept[path] = (signature, head)

    def drop(self, path: str) -> None:
        with self._lock:
            self._kept.pop(path, None)

    def clear(self) -> None:
        with self._lock:
            self._kept.clear()
