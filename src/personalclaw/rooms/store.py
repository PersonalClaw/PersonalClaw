"""Agent Rooms — the room record, the member list, and the shared transcript store.

A **room** is a persistent shared transcript plus a member list, where the human and N
bound agents deliberate over days. A **member** is an ordinary agent binding with a role
blurb and a listen policy. This module owns both records and the transcript's storage;
who speaks next (the arbiter), the per-member cursors and the per-member safety posture
are later atoms and deliberately absent here.

**Nothing here is a new storage engine.** The transcript is a
:class:`~personalclaw.history.ConversationLog` pointed at the room's own directory, so
rotation at 2 MB, the archive window and its 7-day retention are the SAME code paths a
session uses — not a second policy that can drift from them. The one thing a shared
transcript needs and a per-participant session does not is a per-message author, which is
``ConversationLog.append``'s additive ``speaker`` argument (see
:func:`~personalclaw.history.speaker_of`). Redaction reuses
:func:`~personalclaw.security.redact_field`, the shipped export/share redactor; export
hands :func:`export_payload` to ``dashboard/session_export.render`` from the HTTP handler.
There is no third redactor and no second renderer — and no import of ``dashboard/`` from
here, because a domain module may not reach up into the HTTP surface.

Layout under the home (``rooms/`` is per-room, one directory each, so the transcript
lands at the path AGENT-ROOMS names)::

    rooms/index.json                    the room records, members included
    rooms/<id>/transcript.jsonl         the shared transcript
    rooms/<id>/archive/                 inherited from ConversationLog, 7-day window

A room id is a strict slug (:data:`_ROOM_ID_RE`), which is what lets the directory name
BE the id: ``history._safe_key`` is the identity map over that alphabet, so there is no
id→filename mapping to get wrong and no way for two ids to collide onto one directory.

**Two readers over one index, with opposite failure postures** — the split is by what the
caller is about to do with the answer, per `AGENTS.md` §"Shared conventions":

* :func:`list_rooms` / :func:`get_room` fail **OPEN**. They back a listing surface; an
  unreadable index answers "no rooms" and warns, because a corrupt file must not take the
  page down.
* :func:`members_for_turn` fails **CLOSED**. The member list decides which agent may
  speak and (from `AR-6`) what it is permitted to do, so an unreadable index refuses the
  turn with an explicit log rather than guessing a roster.
* Every WRITE reads strictly (:func:`_read_index_strict`). A write that inherited the
  fail-open ``[]`` would persist it and silently delete every other room — the one
  direction in which failing open is data loss rather than degradation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.history import ConversationLog
from personalclaw.security import redact_field

logger = logging.getLogger(__name__)

ROOMS_DIR_NAME = "rooms"
INDEX_FILENAME = "index.json"

#: The ConversationLog key inside a room's own directory. One transcript per room, so the
#: key is a constant and the room identity lives in the directory name instead.
TRANSCRIPT_KEY = "transcript"

#: A member's listen policy: sees every message / activated only when @-named / observer
#: that speaks only when the human asks. Closed set — the arbiter (`AR-5`) branches on it.
LISTEN_POLICIES: tuple[str, ...] = ("all", "mention", "silent")
DEFAULT_LISTEN_POLICY = "all"

#: The human's ``speaker`` value. The human is not a member, so they have no member name.
HUMAN_SPEAKER = ""

#: A room id is a lowercase slug and nothing else, so it is safe as a directory name
#: verbatim (see the module docstring).
_ROOM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_MAX_TITLE_CHARS = 200
_MAX_ROLE_BLURB_CHARS = 500


class RoomError(Exception):
    """A refused room operation, carrying the stable wire code its route answers with.

    One exception with a ``code`` rather than a class per failure: the HTTP layer answers
    every one of them from a single table keyed on ``code``, so a new refusal is one raise
    here plus one row there — never a new class plus a new ``isinstance`` branch that some
    other caller forgets to add.

    ``code`` carries no HTTP status, deliberately: this store is also reachable from a CLI
    and from ``rooms.turn``'s turn path, neither of which has one. The status lives beside the
    wire code in ``dashboard.handlers.rooms._REFUSALS``, which a test keeps exhaustive over
    every code raised here — so adding a raise without a row reds rather than 500s.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class RoomMember:
    """One member: an agent binding, a role blurb, and a listen policy.

    ``name`` is the binding's KEY in ``config.json["agents"]``. Bindings have no id field
    and there is no ``AgentBinding`` class, so the key is the whole identity — which is
    why :func:`add_member` validates it against the configured agents and refuses an
    unknown one (fail closed) instead of letting the ordinary resolution path silently
    substitute the default agent.
    """

    name: str
    role_blurb: str = ""
    listen_policy: str = DEFAULT_LISTEN_POLICY


@dataclass
class Room:
    """A room record. Persisted whole into ``rooms/index.json``.

    ``rounds_used``/``paused``/``round_budget`` are written here rather than held in
    memory so a gateway restart cannot launder the budget state the arbiter (`AR-5`)
    enforces. ``round_budget`` of 0 means "inherit ``rooms.round_budget``" — resolved by
    :func:`effective_round_budget`, never by reading the field directly.
    """

    id: str
    title: str
    created_at: str
    archived: bool = False
    paused: bool = False
    rounds_used: int = 0
    round_budget: int = 0
    members: list[RoomMember] = field(default_factory=list)

    def to_dict(self) -> dict:
        """The wire/disk shape. ``asdict`` recurses into the members for free."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Room":
        """Parse one persisted record, or raise ``ValueError`` on a shape we can't trust.

        Strict on identity and lenient on the rest: an id that is not a slug would name a
        directory we did not choose, so it is a hard failure, while a missing ``paused``
        flag is simply the default. The caller decides whether a ValueError fails open or
        closed — that choice belongs to the reader, not to the parser.
        """
        if not isinstance(data, dict):
            raise ValueError("room record is not an object")
        room_id = str(data.get("id", ""))
        if not _ROOM_ID_RE.fullmatch(room_id):
            raise ValueError(f"room id {room_id!r} is not a slug")
        members: list[RoomMember] = []
        for raw in data.get("members", []) or []:
            if not isinstance(raw, dict):
                raise ValueError("member record is not an object")
            name = str(raw.get("name", ""))
            if not name:
                raise ValueError("member record carries no name")
            policy = str(raw.get("listen_policy", DEFAULT_LISTEN_POLICY))
            if policy not in LISTEN_POLICIES:
                raise ValueError(f"listen policy {policy!r} is not one of {LISTEN_POLICIES}")
            members.append(
                RoomMember(
                    name=name,
                    role_blurb=str(raw.get("role_blurb", "")),
                    listen_policy=policy,
                )
            )
        return cls(
            id=room_id,
            title=str(data.get("title", "")),
            created_at=str(data.get("created_at", "")),
            archived=bool(data.get("archived", False)),
            paused=bool(data.get("paused", False)),
            rounds_used=max(0, int(data.get("rounds_used", 0) or 0)),
            round_budget=max(0, int(data.get("round_budget", 0) or 0)),
            members=members,
        )

    def member(self, name: str) -> RoomMember | None:
        """The member named *name*, or None."""
        return next((m for m in self.members if m.name == name), None)


# ── paths ───────────────────────────────────────────────────────────────────


def rooms_dir() -> Path:
    """``<home>/rooms``, re-resolved per call so a test's ``config_dir`` patch is honored."""
    return config_loader.config_dir() / ROOMS_DIR_NAME


def room_dir(room_id: str) -> Path:
    """``<home>/rooms/<id>`` — the room's own directory, holding its transcript + archive."""
    return rooms_dir() / room_id


def _index_path() -> Path:
    return rooms_dir() / INDEX_FILENAME


def room_log(room_id: str) -> ConversationLog:
    """The room's transcript store: a ``ConversationLog`` on the room's own directory.

    Per-room rather than one log over a shared ``rooms/`` directory, so the transcript
    lands at ``rooms/<id>/transcript.jsonl`` and its rotation archive at
    ``rooms/<id>/archive/`` — the room's whole on-disk footprint under one path that
    :func:`delete_room`-style cleanup could remove in one move.
    """
    return ConversationLog(base_dir=room_dir(room_id))


# ── the index: one file, two readers, opposite postures ─────────────────────


def _decode_index(raw: str) -> list[Room]:
    """Parse the index text, or raise ``ValueError``. No I/O and no posture — see callers."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("rooms index is not an object")
    records = data.get("rooms", [])
    if not isinstance(records, list):
        raise ValueError("rooms index 'rooms' is not a list")
    return [Room.from_dict(r) for r in records]


def _read_index_lenient() -> list[Room]:
    """Every room, or ``[]`` when the index is missing or unreadable. **Fails OPEN.**

    Backs the listing surfaces. A corrupt index means the user sees no rooms and a warning
    lands in the log, which is the right degradation for a page — not a 500.
    """
    path = _index_path()
    if not path.exists():
        return []
    try:
        return _decode_index(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning(
            "rooms: index at %s is unreadable — listing as empty (fail open)", path, exc_info=True
        )
        return []


def _read_index_strict() -> list[Room]:
    """Every room, raising :class:`RoomError` when the index is unreadable. **Fails CLOSED.**

    The posture for every WRITE and for the roster a turn runs against. A write that
    inherited :func:`_read_index_lenient`'s ``[]`` would persist it and delete every other
    room; a turn that inherited it would run against a roster nobody configured.
    """
    path = _index_path()
    if not path.exists():
        return []
    try:
        return _decode_index(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("rooms: index at %s is unreadable — refusing (fail closed): %s", path, exc)
        raise RoomError(
            "room_state_unreadable",
            "The rooms index could not be read. Refusing rather than acting on a partial roster.",
        ) from exc


def _write_index(rooms: list[Room]) -> None:
    """Persist the whole index atomically."""
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"rooms": [r.to_dict() for r in rooms]}
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", fsync=True)


# ── config ─────────────────────────────────────────────────────────────────


def rooms_enabled() -> bool:
    """The ``rooms.enabled`` kill switch. Off by default; every room route refuses when off."""
    return bool(config_loader.AppConfig.load().rooms.enabled)


def effective_round_budget(room: Room) -> int:
    """The room's budget, or the configured default when the room declares 0 (inherit)."""
    if room.round_budget > 0:
        return room.round_budget
    return int(config_loader.AppConfig.load().rooms.round_budget)


# ── rooms ──────────────────────────────────────────────────────────────────


def _slugify(title: str) -> str:
    """A room id candidate from a title: lowercase, ``-``-joined alphanumerics."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")[:56]
    return slug


def _unique_id(candidate: str, taken: set[str]) -> str:
    """*candidate*, or the first ``-2``/``-3``/… suffix that is free."""
    if candidate and candidate not in taken:
        return candidate
    for n in range(2, 1000):
        probe = f"{candidate}-{n}" if candidate else f"room-{n}"
        if probe not in taken:
            return probe
    raise RoomError("room_id_exhausted", "Could not derive a free room id from that title.")


def list_rooms(*, include_archived: bool = False) -> list[Room]:
    """Every room, newest first. **Fails open** — see :func:`_read_index_lenient`."""
    rooms = _read_index_lenient()
    if not include_archived:
        rooms = [r for r in rooms if not r.archived]
    return sorted(rooms, key=lambda r: r.created_at, reverse=True)


def get_room(room_id: str) -> Room | None:
    """One room, or None when it does not exist. **Fails open.**"""
    return next((r for r in _read_index_lenient() if r.id == room_id), None)


def require_room(room_id: str) -> Room:
    """One room, raising ``room_not_found``. Reads STRICTLY — this backs write paths."""
    room = next((r for r in _read_index_strict() if r.id == room_id), None)
    if room is None:
        raise RoomError("room_not_found", f"No room with id {room_id!r}.")
    return room


def create_room(title: str) -> Room:
    """Create a room from *title* and return it. Only a human reaches this (V1 exclusion).

    The transcript file is NOT created here: ``ConversationLog.append`` creates it with its
    metadata line on the first message, and pre-creating it would mean two places that know
    the file's initial shape.
    """
    clean = title.strip()[:_MAX_TITLE_CHARS]
    if not clean:
        raise RoomError("room_title_required", "A room needs a title.")
    rooms = _read_index_strict()
    room = Room(
        id=_unique_id(_slugify(clean), {r.id for r in rooms}),
        title=clean,
        created_at=datetime.now().isoformat(),
    )
    rooms.append(room)
    _write_index(rooms)
    room_dir(room.id).mkdir(parents=True, exist_ok=True)
    logger.info("rooms: created room %s", room.id)
    return room


def archive_room(room_id: str) -> Room:
    """Archive a room: it stops accepting messages and drops out of the default listing.

    Idempotent — archiving an archived room is a no-op that returns it, because the caller's
    intent ("this room should be archived") is already satisfied and a 409 here would only
    make a double-click an error.
    """
    rooms = _read_index_strict()
    room = next((r for r in rooms if r.id == room_id), None)
    if room is None:
        raise RoomError("room_not_found", f"No room with id {room_id!r}.")
    if not room.archived:
        room.archived = True
        _write_index(rooms)
        logger.info("rooms: archived room %s", room.id)
    return room


# ── members ────────────────────────────────────────────────────────────────


def _validate_member_name(name: str) -> str:
    """The binding key, validated for shape AND existence. **Fails closed** on both.

    Existence is checked against ``config.agents`` directly rather than through
    ``config.loader.resolve_agent_bindings``: that function's documented resolution chain
    falls back to ``default_agent`` (and then to the first configured agent) for an unknown
    name, so it can never answer "no such binding" and is unusable as an existence
    predicate. Resolution for an ACCEPTED member still goes through it — the ordinary path,
    unchanged — but admission cannot.
    """
    from personalclaw.agent_metadata import _SAFE_NAME_RE

    clean = name.strip()
    if not clean:
        raise RoomError("room_member_name_required", "A member needs an agent-binding name.")
    if not _SAFE_NAME_RE.fullmatch(clean):
        raise RoomError(
            "room_member_name_invalid",
            f"Agent-binding name {clean!r} is not a plain name (letters, digits, - and _).",
        )
    if clean not in config_loader.AppConfig.load().agents:
        raise RoomError(
            "room_member_unknown_agent",
            f"No agent binding named {clean!r} is configured.",
        )
    return clean


def add_member(
    room_id: str,
    name: str,
    *,
    role_blurb: str = "",
    listen_policy: str = DEFAULT_LISTEN_POLICY,
) -> Room:
    """Add a member and return the updated room. Only a human reaches this (V1 exclusion).

    Validation order is deliberate: every check runs and raises BEFORE the index is
    rewritten, so a refused add writes nothing at all rather than leaving a half-member.
    """
    if listen_policy not in LISTEN_POLICIES:
        raise RoomError(
            "room_invalid_listen_policy",
            f"listen_policy must be one of {', '.join(LISTEN_POLICIES)}.",
        )
    clean_name = _validate_member_name(name)
    rooms = _read_index_strict()
    room = next((r for r in rooms if r.id == room_id), None)
    if room is None:
        raise RoomError("room_not_found", f"No room with id {room_id!r}.")
    if room.archived:
        raise RoomError("room_archived", f"Room {room_id!r} is archived.")
    if room.member(clean_name) is not None:
        raise RoomError("room_member_exists", f"{clean_name!r} is already a member of this room.")
    max_members = int(config_loader.AppConfig.load().rooms.max_members)
    if len(room.members) >= max_members:
        raise RoomError(
            "room_member_limit",
            f"This room already holds the configured maximum of {max_members} members.",
        )
    room.members.append(
        RoomMember(
            name=clean_name,
            role_blurb=role_blurb.strip()[:_MAX_ROLE_BLURB_CHARS],
            listen_policy=listen_policy,
        )
    )
    _write_index(rooms)
    logger.info("rooms: added member %s to room %s (policy=%s)", clean_name, room_id, listen_policy)
    return room


def remove_member(room_id: str, name: str) -> Room:
    """Remove a member and return the updated room.

    Deliberately NOT idempotent: removing someone who is not a member answers
    ``room_member_not_found`` so the caller learns its member list is stale instead of
    reporting a successful removal of something that was never there — the same reading
    ``channel_trust``'s revoke route already established.
    """
    rooms = _read_index_strict()
    room = next((r for r in rooms if r.id == room_id), None)
    if room is None:
        raise RoomError("room_not_found", f"No room with id {room_id!r}.")
    if room.member(name) is None:
        raise RoomError("room_member_not_found", f"{name!r} is not a member of this room.")
    room.members = [m for m in room.members if m.name != name]
    _write_index(rooms)
    logger.info("rooms: removed member %s from room %s", name, room_id)
    return room


def members_for_turn(room_id: str) -> list[RoomMember]:
    """The roster a turn runs against. **Fails closed** — see :func:`_read_index_strict`.

    Separate from ``get_room(...).members`` because the posture differs, not the data: this
    is the read whose answer decides which agent speaks (and, from `AR-6`, with what
    capabilities), so an unreadable index must refuse the turn rather than degrade to an
    empty roster the way the listing surface does.
    """
    return require_room(room_id).members


# ── the shared transcript ──────────────────────────────────────────────────


def _redact(text: str) -> str:
    """Both redaction passes over one transcript field.

    ``security.redact_field`` is the shipped single implementation (exfiltration URLs then
    credentials); this is a call to it, not a third redactor.

    **Applied to EVERY role, including the human's own words** — which is stricter than
    the session write path, and deliberately so. ``chat_persistence`` may exempt ``user``
    because a solo session's transcript is only ever replayed to that user's own provider;
    a room transcript is the inter-member wire (``turn.build_member_prompt`` feeds it to
    every member's provider as fenced text), so a credential the human types into a room
    would be handed to every bound agent. The premise that justifies the exemption does not
    hold here.
    """
    return redact_field(text)


def append_message(room_id: str, *, role: str, content: str, speaker: str = HUMAN_SPEAKER) -> None:
    """Append one message to the room's shared transcript.

    *speaker* is the member name, or ``""`` (:data:`HUMAN_SPEAKER`) for the human. The room
    must exist and must not be archived — checked strictly, because this is a write.
    """
    room = require_room(room_id)
    if room.archived:
        raise RoomError("room_archived", f"Room {room_id!r} is archived.")
    if speaker and room.member(speaker) is None:
        raise RoomError("room_member_not_found", f"{speaker!r} is not a member of this room.")
    room_log(room_id).append(
        TRANSCRIPT_KEY,
        role=role,
        content=_redact(content),
        speaker=speaker,
    )


def read_messages(room_id: str) -> list[dict]:
    """The room's transcript, oldest first. Raw message dicts, so ``speaker`` survives."""
    return room_log(room_id).read_messages(TRANSCRIPT_KEY)


def transcript_path(room_id: str) -> Path:
    """``rooms/<id>/transcript.jsonl`` — the file the transcript persists to."""
    return room_dir(room_id) / f"{TRANSCRIPT_KEY}.jsonl"


def export_payload(room_id: str) -> tuple[Room, dict, list[dict]]:
    """``(room, meta, messages)`` — everything a renderer needs, and nothing rendered.

    The RENDERING lives in the HTTP handler, which calls
    ``dashboard/session_export.render`` — the shipped renderer, re-running both redaction
    passes over every role on the way out. A room grows no second exporter.

    The split is not stylistic: ``rooms/`` is domain code and ``dashboard/`` is the HTTP
    surface, so a call in this direction is the upward edge the structural
    import-direction ratchet refuses (and the reason it refuses is real — a domain that
    imports a handler module can no longer be exercised without standing up the web app).
    Formatting a transcript for download is presentation, so it belongs on that side of
    the line anyway.
    """
    room = require_room(room_id)
    return room, room_log(room_id).get_metadata(TRANSCRIPT_KEY), read_messages(room_id)
