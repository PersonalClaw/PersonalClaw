"""Per-member context cursors — the sidecar that turns the transcript into a DIFF.

A room's members do not share a context window, so what a member knows is exactly what it has
been shown plus what it said. Without a record of that, every turn re-feeds the whole
transcript into a session that already holds it: the oldest line is paid for again on turn 50,
and what a member's model reads grows with the square of the room's length. This module is the
record: one cursor per member, all in ONE file beside the transcript::

    rooms/<id>/cursors.json     {"<member>": <offset>, …}

A cursor is how many transcript messages that member has been shown, counted in
:func:`~personalclaw.rooms.store.read_messages` order.

**A position in the file, never a time.** ``ConversationLog.append`` stamps naive LOCAL time,
so the clock that writes ``ts`` goes backwards once a year (the hour the clocks go back repeats)
and whenever an NTP step or a hand-set clock moves it. A cursor that compared stamps would read
every line of a repeated hour as already seen, and nothing would say so. An offset has no clock.
It is exact because the transcript is append-only: a room log is a
:class:`~personalclaw.history.ConversationLog`, which never shortens or rotates a transcript
(``tests/test_rooms_store.py`` drives one past 2 MB and reads every line back), so the message
at index ``i`` is the same message on every later read.

**Beside the transcript, not in ``rooms/index.json``.** A cursor moves on every turn while a
room record changes almost never. Writing the roster file N times per round would put the
member list, which decides who may speak and what each member may do, on the hot path of the
highest-frequency write in the subsystem.

**A cursor is only as good as the session it was taken in.** It says what the member's OWN
provider session was shown. A session that starts fresh remembers none of it, and a member
removed and re-added may still hold its session. So this module keeps positions and nothing
else, and :func:`personalclaw.rooms.turn.member_feed` decides per turn, from the session it
actually holds, whether the cursor still describes what the member has read.

**Both failure directions are handled, and they are not symmetric.** Re-feeding a line a member
has read wastes tokens; skipping one it has not read silently removes a position from a
deliberation. So the reader refuses rather than guesses: an unreadable sidecar fails CLOSED
(:func:`read_cursors`), because answering ``{}`` would replay the whole room to every member AND
let the next :func:`advance` persist that, erasing every other member's position.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.rooms.store import RoomError, room_dir

logger = logging.getLogger(__name__)

#: The sidecar's filename inside the room's own directory, so a room's whole on-disk footprint
#: stays under ``rooms/<id>/``, the property :func:`~personalclaw.rooms.store.room_log` set.
CURSORS_FILENAME = "cursors.json"


def cursors_path(room_id: str) -> Path:
    """``rooms/<id>/cursors.json`` — the sidecar, beside the room's transcript."""
    return room_dir(room_id) / CURSORS_FILENAME


def _parse(data: object) -> dict[str, int]:
    """Every member's offset, or ``ValueError`` on a shape the reader cannot trust.

    ``bool`` is refused explicitly: it is an ``int`` subclass, so a stray ``true`` would otherwise
    land as the offset 1, a plausible count nobody wrote.
    """
    if not isinstance(data, dict):
        raise ValueError("cursor sidecar is not an object")
    out: dict[str, int] = {}
    for name, offset in data.items():
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError(f"cursor for {name!r} is {offset!r}, not a non-negative integer")
        out[str(name)] = offset
    return out


def read_cursors(room_id: str) -> dict[str, int]:
    """Every member's cursor. A missing file is ``{}``. **Fails CLOSED** on anything else.

    A MISSING file is not a failure: a room that has never finished a turn has no sidecar, and
    every member correctly starts from the top.

    An unreadable one refuses (see the module docstring for why answering ``{}`` would be data
    loss rather than degradation). The refusal reaches the human as the member's failed turn, so
    its sentence names the file and the one thing that recovers the room.
    """
    path = cursors_path(room_id)
    if not path.exists():
        return {}
    try:
        return _parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        logger.error(
            "rooms: cursor sidecar at %s is unreadable — refusing the turn (fail closed): %s",
            path,
            exc,
        )
        raise RoomError(
            "room_cursor_unreadable",
            f"This room's record of what each member has read (rooms/{room_id}/"
            f"{CURSORS_FILENAME} in your PersonalClaw home) could not be read, so the turn was "
            "refused rather than guessing what the member has already seen. Delete that file and "
            "every member reads the whole room again on its next turn.",
        ) from exc


def cursor_for(room_id: str, member_name: str) -> int:
    """*member_name*'s cursor, or 0 when it has none yet. **Fails closed.**"""
    return read_cursors(room_id).get(member_name, 0)


def advance(room_id: str, member_name: str, offset: int) -> None:
    """Record that *member_name* has now read the first *offset* messages. One key only.

    Read-modify-write over the whole sidecar because it is one small file, but the change touches
    exactly one key: one member's turn must never move another's cursor, and the read is the strict
    one, so a corrupt file is never overwritten with a map that has dropped everybody else.
    """
    cursors = read_cursors(room_id)
    cursors[member_name] = offset
    atomic_write(
        cursors_path(room_id),
        json.dumps(cursors, indent=2, sort_keys=True) + "\n",
        fsync=True,
    )
