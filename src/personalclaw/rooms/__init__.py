"""Agent Rooms — a persistent shared transcript with a bound-agent member list.

Public surface of the room store, including every writer that moves the round — the four the
arbiter drives (:func:`~personalclaw.rooms.store.set_pending_queue`,
:func:`~personalclaw.rooms.store.begin_turn`, :func:`~personalclaw.rooms.store.end_turn`,
:func:`~personalclaw.rooms.store.pause_room`), the reset any human message makes
(:func:`~personalclaw.rooms.store.reset_round_budget`), plus the human's own
:func:`~personalclaw.rooms.store.set_round_budget`, which is what makes the per-room
override settable rather than merely readable.
The turn path lives in :mod:`personalclaw.rooms.turn`, the arbiter that decides who speaks
next in :mod:`personalclaw.rooms.arbiter`, and the per-member safety posture in
:mod:`personalclaw.rooms.posture`; all three are imported from there by name rather than
re-exported here, because their entry points reach the provider and guardrail layers and a
caller that only wants to list rooms should not pay for that import. Per-member transcript
cursors are a later AGENT-ROOMS atom and do not exist yet.
"""

from personalclaw.rooms.store import (
    DEFAULT_LISTEN_POLICY,
    HUMAN_SPEAKER,
    LISTEN_POLICIES,
    MAX_ROOM_ROUND_BUDGET,
    TRANSCRIPT_KEY,
    Room,
    RoomError,
    RoomMember,
    add_member,
    append_message,
    archive_room,
    begin_turn,
    create_room,
    effective_round_budget,
    end_turn,
    export_payload,
    get_room,
    list_rooms,
    pause_room,
    read_messages,
    remove_member,
    require_room,
    reset_round_budget,
    room_dir,
    room_log,
    rooms_dir,
    rooms_enabled,
    set_pending_queue,
    set_round_budget,
    transcript_path,
)

__all__ = [
    "DEFAULT_LISTEN_POLICY",
    "HUMAN_SPEAKER",
    "LISTEN_POLICIES",
    "MAX_ROOM_ROUND_BUDGET",
    "TRANSCRIPT_KEY",
    "Room",
    "RoomError",
    "RoomMember",
    "add_member",
    "append_message",
    "archive_room",
    "begin_turn",
    "create_room",
    "effective_round_budget",
    "end_turn",
    "export_payload",
    "get_room",
    "list_rooms",
    "pause_room",
    "read_messages",
    "remove_member",
    "require_room",
    "reset_round_budget",
    "room_dir",
    "room_log",
    "rooms_dir",
    "rooms_enabled",
    "set_pending_queue",
    "set_round_budget",
    "transcript_path",
]
