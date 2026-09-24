"""Agent Rooms — a persistent shared transcript with a bound-agent member list.

Public surface of the room store, including the three writers that move the round budget.
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
    TRANSCRIPT_KEY,
    Room,
    RoomError,
    RoomMember,
    add_member,
    append_message,
    archive_room,
    charge_round,
    create_room,
    effective_round_budget,
    export_payload,
    get_room,
    list_rooms,
    members_for_turn,
    pause_room,
    read_messages,
    remove_member,
    require_room,
    reset_round_budget,
    room_dir,
    room_log,
    rooms_dir,
    rooms_enabled,
    take_pending,
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
    "charge_round",
    "create_room",
    "effective_round_budget",
    "export_payload",
    "get_room",
    "list_rooms",
    "members_for_turn",
    "pause_room",
    "read_messages",
    "remove_member",
    "require_room",
    "reset_round_budget",
    "room_dir",
    "room_log",
    "rooms_dir",
    "rooms_enabled",
    "take_pending",
    "transcript_path",
]
