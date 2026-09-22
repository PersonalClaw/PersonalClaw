"""The per-member provider session — one session per member, never one per room.

A room's defining property is that its members do NOT share a context window. Each
member holds its own provider session through the ordinary
:meth:`~personalclaw.session.SessionManager.get_or_create` path under the session key
``room:<room_id>:<member_name>``, so what a member knows is exactly what the shared
transcript showed it plus its own turns — not the other members' internal reasoning. Two
members of one room are two distinct provider objects with two distinct histories, and
that is a property :func:`member_session` exists to make structural rather than
aspirational.

**The ``room:`` key prefix is load-bearing, by absence.** It appears in neither
``session._STATELESS_PREFIXES`` nor ``guardrails.policy._EXTRA_UNATTENDED_PREFIXES``, so
``is_unattended_session("room:x:y")`` is False and ``profile_for_session`` hands back the
INTERACTIVE profile, whose ``approval`` is ``"ask"``. That is what makes "the human is the
room's sole approver" true by construction rather than by a policy branch a later atom
could forget. A room must never register itself as an unattended or stateless prefix: the
HEADLESS profile approves via hooks, which would silently remove the human from the loop
of a surface whose entire point is that they are in it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from personalclaw.rooms.store import RoomError, require_room

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.llm.base import ModelProvider
    from personalclaw.session import SessionManager

logger = logging.getLogger(__name__)

#: Session-key prefix for a room member. Deliberately absent from every
#: stateless/unattended prefix tuple — see the module docstring.
SESSION_KEY_PREFIX = "room:"


def session_key(room_id: str, member_name: str) -> str:
    """The session key for *member_name* in *room_id*.

    Both components are already constrained (a room id is a slug, a member name passed
    ``_SAFE_NAME_RE``), so the two colons can never be ambiguous and the key needs no
    escaping of its own.
    """
    return f"{SESSION_KEY_PREFIX}{room_id}:{member_name}"


@asynccontextmanager
async def member_session(
    sessions: "SessionManager", room_id: str, member_name: str
) -> AsyncIterator["ModelProvider"]:
    """The member's own provider session, held for the body and always released.

    A context manager rather than a plain call because ``get_or_create`` acquires a
    per-session semaphore that the caller MUST release: a room runs members in sequence,
    so a leaked permit does not fail loudly — it wedges that one member's next turn
    forever while the room otherwise looks healthy.

    The member must be in the room's roster, read through the fail-closed path: this is
    the check that decides whether an agent speaks into a shared transcript, so an
    unreadable roster refuses rather than guessing.
    """
    room = require_room(room_id)
    member = room.member(member_name)
    if member is None:
        raise RoomError("room_member_not_found", f"{member_name!r} is not a member of this room.")
    if room.archived:
        raise RoomError("room_archived", f"Room {room_id!r} is archived.")

    key = session_key(room_id, member_name)
    provider, _is_new, _resumed = await sessions.get_or_create(key, agent=member.name)
    try:
        yield provider
    finally:
        sessions.release(key)
