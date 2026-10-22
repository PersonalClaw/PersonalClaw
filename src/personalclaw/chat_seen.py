"""How far you have seen each chat: the gateway's record, so every window and the phone agree.

A chat is unread while its last finished reply is newer than the instant you last had it open in
front of you. That instant is kept here, per chat, as ``seen_through``, by this module alone: the
one writer of ``chat_seen.json``. It is no field of the chat's record (a write per glance would
churn the record) and no transcript event (it is no part of the conversation).

The document is ``{"v": 1, "since": <iso>, "chats": [{"id": <chat>, "seen_through": <iso>,
"updated_at": <iso>}]}``, one record per chat (``durability`` merges them by ``updated_at``, and
keeps the file on this machine). ``since`` is how far every chat has been seen: when the record
began, so the replies every chat held before there was a record are not all unread at once, and
then the last time you marked every chat seen. A chat reads as seen through the later of the two.

**Advancing is monotonic.** :func:`advance` moves a chat's instant forward and never back: an older
``through`` changes nothing. :func:`advance_every` moves every chat at once.

**Reads fail open.** A file that cannot be read reads as nothing seen, so every finished reply
shows unread, and the failure is logged (``record_files``). It never hides a finished reply.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from personalclaw import record_files
from personalclaw.instants import as_instant, utc_iso

logger = logging.getLogger(__name__)

FILE = "chat_seen.json"
VERSION = 1

#: Where the store keeps its records, for ``durability``.
SHAPE = record_files.Shape(key="chats")

#: The clock: the gateway's own, which a test replaces.
_now = time.time


def path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / FILE


@dataclass
class Seen:
    """What the record says: each chat's ``seen_through`` (epoch seconds), and the instant a chat
    with no record of its own reads as seen through (``since``; 0 when the file could not be read,
    so nothing reads as seen)."""

    since: float = 0.0
    chats: dict[str, float] = field(default_factory=dict)

    def through(self, chat: str) -> float:
        """How far *chat* has been seen: its own record, or every chat's, the later."""
        return max(self.chats.get(chat, 0.0), self.since)


def epoch_of(value: Any) -> float | None:
    """An instant as served or stored (ISO 8601; a zone-less one read as this machine's local
    time, ``instants.as_instant``) or epoch seconds, as epoch seconds; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(as_instant(value.strip())))
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else None


def _seen_of(document: Any) -> Seen:
    if not isinstance(document, dict):
        return Seen()
    seen = Seen(since=epoch_of(document.get("since")) or 0.0)
    for row in SHAPE.records(document) or []:
        chat = record_files.record_id(row)
        at = epoch_of(row.get("seen_through")) if isinstance(row, dict) else None
        if chat and at is not None:
            seen.chats[chat] = max(at, seen.chats.get(chat, 0.0))
    return seen


def _begun(document: Any) -> dict[str, Any]:
    """*document*, or the record begun now when there is none: seen through now, every chat."""
    if isinstance(document, dict):
        return document
    return {"v": VERSION, "since": utc_iso(_now()), "chats": []}


def read() -> Seen:
    """How far every chat has been seen. Begins the record when there is none, so the replies
    every chat already holds read as seen; reads as nothing seen when the file cannot be read."""
    file = path()
    try:
        document = record_files.read(file, dict)
    except record_files.Unreadable:
        return Seen()  # logged, and a copy kept, by record_files: every finished reply is unread
    if document is None:
        try:
            record_files.rewrite(file, lambda doc: None if isinstance(doc, dict) else _begun(doc))
            document = record_files.read(file, dict)
        except (OSError, record_files.Unreadable):
            logger.warning("chat seen: could not begin %s", file, exc_info=True)
            return Seen()
    return _seen_of(document)


def advance(chat: str, through: float) -> float | None:
    """*chat* has been seen through *through* (epoch seconds, never later than now): its new
    ``seen_through``, or None when that changes nothing (it was seen further already). Raises
    ``record_files.Unreadable`` for a file it cannot read, which is kept as it is rather than
    overwritten."""
    return advance_all([chat], through).get(chat)


def advance_every(through: float) -> float | None:
    """Every chat has been seen through *through* (never later than now): the new ``since``, or
    None when that changes nothing. Raises ``record_files.Unreadable`` as :func:`advance` does."""
    at = min(float(through), _now())
    moved: list[float] = []

    def change(document: Any) -> Any:
        doc = _begun(document)
        if at <= _seen_of(doc).since:
            return None if isinstance(document, dict) else doc
        moved.append(at)
        return {**doc, "v": VERSION, "since": utc_iso(at)}

    record_files.rewrite(path(), change, SHAPE)
    return moved[0] if moved else None


def advance_all(chats: Iterable[str], through: float) -> dict[str, float]:
    """Every one of *chats* has been seen through *through*: the ones that moved, each with its
    new ``seen_through``. Raises ``record_files.Unreadable`` as :func:`advance` does."""
    names = [c for c in dict.fromkeys(chats) if isinstance(c, str) and c]
    at = min(float(through), _now())
    moved: dict[str, float] = {}

    def change(document: Any) -> Any:
        doc = _begun(document)
        seen = _seen_of(doc)
        rows = [r for r in SHAPE.records(doc) or [] if isinstance(r, dict)]
        by_id = {record_files.record_id(r): r for r in rows if record_files.record_id(r)}
        stamp = utc_iso(_now())
        for chat in names:
            if at <= seen.through(chat):
                continue
            by_id[chat] = {"id": chat, "seen_through": utc_iso(at), "updated_at": stamp}
            moved[chat] = at
        if not moved and isinstance(document, dict):
            return None
        return {**doc, "v": VERSION, "chats": list(by_id.values())}

    record_files.rewrite(path(), change, SHAPE)
    return moved
