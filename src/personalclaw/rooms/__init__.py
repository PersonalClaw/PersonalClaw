"""Agent Rooms — a persistent shared transcript with a bound-agent member list.

Public surface of the room store. The turn path lives in :mod:`personalclaw.rooms.turn` and
is imported from there by name, because its entry points take a ``SessionManager`` and
re-exporting them here would put a provider-shaped import on the path of every caller that
only wants to list rooms. Per-member cursors and per-member safety posture are later
AGENT-ROOMS atoms and do not exist yet.
"""

from personalclaw.rooms.store import (
    DEFAULT_LISTEN_POLICY,
    HUMAN_SPEAKER,
    LISTEN_POLICIES,
    TRANSCRIPT_KEY,
    Room,
    RoomError,
    RoomMember,
    add_member,
    append_message,
    archive_room,
    create_room,
    effective_round_budget,
    export_payload,
    get_room,
    list_rooms,
    members_for_turn,
    read_messages,
    remove_member,
    require_room,
    room_dir,
    room_log,
    rooms_dir,
    rooms_enabled,
    transcript_path,
)

__all__ = [
    "DEFAULT_LISTEN_POLICY",
    "HUMAN_SPEAKER",
    "LISTEN_POLICIES",
    "TRANSCRIPT_KEY",
    "Room",
    "RoomError",
    "RoomMember",
    "add_member",
    "append_message",
    "archive_room",
    "create_room",
    "effective_round_budget",
    "export_payload",
    "get_room",
    "list_rooms",
    "members_for_turn",
    "read_messages",
    "remove_member",
    "require_room",
    "room_dir",
    "room_log",
    "rooms_dir",
    "rooms_enabled",
    "transcript_path",
]
