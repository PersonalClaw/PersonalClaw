"""Agent Rooms — the room record, the member list, and the shared transcript store.

A **room** is a persistent shared transcript plus a member list, where the human and N
bound agents deliberate over days. A **member** is an ordinary agent binding with a role
blurb, a listen policy and its own declared safety posture. This module owns those records
and the transcript's storage, plus the persistence of the round budget the arbiter enforces;
deciding who speaks next is :mod:`personalclaw.rooms.arbiter`, the posture VOCABULARY lives in
:mod:`personalclaw.rooms.posture` — this module stores the declaration and never judges it —
and the per-member transcript cursors are a later atom, deliberately absent here.

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
* :func:`members_for_turn` fails **CLOSED**. The member list decides which agent may speak
  AND what each one is permitted to do, so an unreadable index refuses the turn with an
  explicit log rather than guessing a roster — a guessed roster is a guessed posture.
* Every WRITE reads strictly (:func:`_read_index_strict`). A write that inherited the
  fail-open ``[]`` would persist it and silently delete every other room — the one
  direction in which failing open is data loss rather than degradation.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
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
#: that speaks only when the human asks. Closed set — ``rooms.arbiter`` branches on it.
LISTEN_POLICIES: tuple[str, ...] = ("all", "mention", "silent")
DEFAULT_LISTEN_POLICY = "all"

#: The human's ``speaker`` value. The human is not a member, so they have no member name.
HUMAN_SPEAKER = ""

#: The ``role`` of a line the ROOM itself writes — today, `AR-6`'s tool refusals. It carries
#: the member's name as its ``speaker`` (that is the attribution a reader needs) but must not
#: render as that member having SAID it, so the role is what distinguishes it:
#: ``turn.render_transcript`` labels a line of this role ``[room]``. A dedicated speaker
#: sentinel was the alternative and is worse — ``room`` is a legal agent-binding name, so a
#: member could collide with it, while a role cannot be forged by naming an agent.
ROOM_NOTE_ROLE = "system"

#: A room id is a lowercase slug and nothing else, so it is safe as a directory name
#: verbatim (see the module docstring).
_ROOM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_MAX_TITLE_CHARS = 200
_MAX_ROLE_BLURB_CHARS = 500

#: The ceiling a room's OWN ``round_budget`` may declare, matching the ``rooms.round_budget``
#: row in ``_EDITABLE_CONFIG`` exactly (``min 1, max 100``). Kept identical on purpose: a
#: per-room override that accepted a value the config key refuses would make the two controls
#: disagree about what a legal budget is, and the UI renders both from this one number.
MAX_ROOM_ROUND_BUDGET = 100


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

    ``profile_narrowing`` is what lets a read-only critic and a tool-bearing executor share
    one room: the Autonomy-Guardrails capability axes this member declares against the room's
    own posture. The vocabulary, the validation, the restrictive default and the refusal all
    live in :mod:`personalclaw.rooms.posture` — this field is storage. It holds the raw
    DECLARATION rather than a resolved profile because the base it narrows is resolved per
    turn (the operator ceiling can move between two turns of one room), and because a resolved
    profile on disk would be a second safety object to keep in step with the first.

    Empty ``{}`` is the common case, and it does NOT mean "the room's posture": a member that
    declares nothing runs at ``posture.DEFAULT_MEMBER_TOOL_GRANTS``, the narrowest tier.
    """

    name: str
    role_blurb: str = ""
    listen_policy: str = DEFAULT_LISTEN_POLICY
    profile_narrowing: dict = field(default_factory=dict)


@dataclass
class Room:
    """A room record. Persisted whole into ``rooms/index.json``.

    ``rounds_used``/``paused``/``round_budget`` are written here rather than held in
    memory so a gateway restart cannot launder the budget state the arbiter enforces —
    written by :func:`charge_round`, :func:`pause_room` and :func:`reset_round_budget`, which
    are the only three places any of them changes. ``round_budget`` of 0 means "inherit
    ``rooms.round_budget``" — resolved by :func:`effective_round_budget`, never by reading
    the field directly.

    ``pending_queue`` is the speaker queue's remainder at the moment the room paused: the
    members that were owed a turn and did not get one. It is persisted for the same reason
    the counter is — a pause is a suspension rather than a cancellation, so resuming has to
    continue the queue rather than mint a fresh one, and a queue held in the paused round's
    call frame would be lost with it. Written by :func:`pause_room` and emptied by
    :func:`take_pending`; deliberately NOT cleared by :func:`reset_round_budget`, because the
    human message that refills the budget is precisely the event that lets the remainder run.
    """

    id: str
    title: str
    created_at: str
    archived: bool = False
    paused: bool = False
    rounds_used: int = 0
    round_budget: int = 0
    pending_queue: list[str] = field(default_factory=list)
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
            narrowing = raw.get("profile_narrowing") or {}
            if not isinstance(narrowing, dict):
                # The one member field where "lenient on the rest" would be the WRONG
                # default. Coercing an unreadable narrowing to {} runs the member at the
                # restrictive default, which is safe — but it also silently discards a
                # posture its author wrote, so the member everyone believes is configured is
                # running on something else. Raising hands the choice to the reader, whose
                # strict side refuses the write and whose lenient side warns.
                raise ValueError("member profile_narrowing is not an object")
            members.append(
                RoomMember(
                    name=name,
                    role_blurb=str(raw.get("role_blurb", "")),
                    listen_policy=policy,
                    profile_narrowing=dict(narrowing),
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
            pending_queue=[str(n) for n in (data.get("pending_queue") or []) if str(n)],
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
    profile_narrowing: object = None,
) -> Room:
    """Add a member and return the updated room. Only a human reaches this (V1 exclusion).

    Validation order is deliberate: every check runs and raises BEFORE the index is
    rewritten, so a refused add writes nothing at all rather than leaving a half-member.

    ``profile_narrowing`` is validated for SHAPE here and judged for actual narrowing at turn
    time, because "is this narrower" is only answerable against the posture the operator
    ceiling resolves to when the member speaks. Storing a declaration nothing could read would
    defer the refusal into the turn, where the member simply falls silent; refusing it at the
    add is how its author finds out. Imported lazily because ``posture`` reads this module.
    """
    from personalclaw.rooms.posture import parse_narrowing

    if listen_policy not in LISTEN_POLICIES:
        raise RoomError(
            "room_invalid_listen_policy",
            f"listen_policy must be one of {', '.join(LISTEN_POLICIES)}.",
        )
    parse_narrowing(profile_narrowing)  # raises RoomError on a shape we could not honour
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
            profile_narrowing=(
                dict(profile_narrowing) if isinstance(profile_narrowing, dict) else {}
            ),
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
    is the read whose answer decides which agent speaks and with what capabilities, so an
    unreadable index must refuse the turn rather than degrade to an empty roster the way the
    listing surface does.
    """
    return require_room(room_id).members


# ── the round budget ───────────────────────────────────────────────────────


def _require_indexed_room(rooms: list[Room], room_id: str) -> Room:
    """The room *room_id* inside an already-read index, or ``room_not_found``.

    The three budget writers below all need the room AND the list it came from — mutating a
    record read through :func:`require_room` would change an object no longer connected to
    anything writable, and the write would silently do nothing.
    """
    room = next((r for r in rooms if r.id == room_id), None)
    if room is None:
        raise RoomError("room_not_found", f"No room with id {room_id!r}.")
    return room


def reset_round_budget(room_id: str) -> Room:
    """Zero ``rounds_used`` and clear ``paused``. **Any** human message calls this.

    The human's attention is what the budget is measuring the absence of, so their arriving
    message is the thing that refills it — there is no separate resume action to forget to
    press, and a paused room therefore cannot become permanently stuck.

    **``pending_queue`` is deliberately left alone.** It is the queue's remainder, not budget
    state: clearing it here would make every human message silently cancel the turns their
    room still owed, which is the "resume restarts the conversation" bug this field exists to
    prevent. :func:`take_pending` is the only reader, and it is the one that empties it.

    Idempotent, and it avoids the write when there is nothing to reset: the common case is a
    human talking to a room that never came near its ceiling, and rewriting every room record
    on every message would make the index churn proportional to conversation length rather
    than to state change.
    """
    rooms = _read_index_strict()
    room = _require_indexed_room(rooms, room_id)
    if room.rounds_used or room.paused:
        room.rounds_used = 0
        room.paused = False
        _write_index(rooms)
        logger.info("rooms: a human message reset room %s's budget and cleared its pause", room_id)
    return room


def charge_round(room_id: str) -> Room:
    """Charge one agent turn against the room's budget and persist it. Returns the room.

    **Persisted, not counted in memory** — an in-memory counter would make restarting the
    gateway the cheapest way to run a room forever, which is precisely the bound this field
    exists to hold.

    The caller charges BEFORE running the turn, deliberately. A member whose provider dies
    still spent its round: charging afterwards would let a member that fails every time drain
    the queue indefinitely without the counter ever reaching the ceiling, so the failure mode
    that most needs a bound would be the one without one.
    """
    rooms = _read_index_strict()
    room = _require_indexed_room(rooms, room_id)
    room.rounds_used += 1
    _write_index(rooms)
    return room


def pause_room(room_id: str, pending: Sequence[str] = ()) -> Room:
    """Set ``paused`` and park *pending* — the queue's remainder. Idempotent.

    Distinct from :func:`archive_room`: an archived room is finished and refuses messages,
    while a paused one is mid-deliberation and is waiting for its human. The room still
    accepts a message — accepting one is how it resumes.

    *pending* is the speaker queue as it stood when the ceiling was reached, stored so the
    members still owed a turn take it after the human replies. Parking it is what makes the
    pause a **suspension**: without it the remainder dies with the round's call frame and the
    resume would silently be a fresh conversation that happens to reuse the same room.

    The remainder is written even when the room is already paused, because a second round that
    also reaches the ceiling is carrying its own un-spoken members, and dropping them on the
    grounds that the flag was already set would lose exactly the queue this argument exists to
    keep. Names are appended rather than replaced, and de-duplicated, so two rounds pausing
    over one room owe each member one turn rather than two.
    """
    rooms = _read_index_strict()
    room = _require_indexed_room(rooms, room_id)
    carried = list(room.pending_queue)
    for name in pending:
        if name not in carried:
            carried.append(name)
    if not room.paused or carried != room.pending_queue:
        room.paused = True
        room.pending_queue = carried
        _write_index(rooms)
        logger.info(
            "rooms: paused room %s at %d agent rounds, %d member(s) still queued",
            room_id,
            room.rounds_used,
            len(carried),
        )
    return room


def set_round_budget(room_id: str, budget: int) -> Room:
    """Set this room's OWN round budget, or 0 to go back to inheriting the configured one.

    The write path for ``Room.round_budget``. It exists because the field was readable long
    before it was settable: :func:`effective_round_budget` consumed it and the HTTP surface
    published it, so a client could see a value it had no way to change — which reads as
    "this is per-room configurable" while being false. Either the field gets a writer or it
    comes off the wire; a per-room budget is genuinely useful (a standing research room and a
    quick two-agent debate want different ceilings), so it gets a writer.

    Range refused rather than clamped, and deliberately the SAME range the config key takes
    (``rooms.round_budget`` is ``min 1, max 100`` in ``_EDITABLE_CONFIG``), plus 0 for
    inherit. A control whose bounds disagree with the save path's is an offer the save path
    refuses, and clamping silently would store a ceiling its author did not choose.
    """
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise RoomError(
            "room_round_budget_invalid",
            "round_budget must be a whole number of exchanges.",
        )
    if budget < 0 or budget > MAX_ROOM_ROUND_BUDGET:
        raise RoomError(
            "room_round_budget_invalid",
            f"round_budget must be 0 (inherit the configured default) or 1-"
            f"{MAX_ROOM_ROUND_BUDGET}.",
        )
    rooms = _read_index_strict()
    room = _require_indexed_room(rooms, room_id)
    if room.archived:
        raise RoomError("room_archived", f"Room {room_id!r} is archived.")
    if room.round_budget != budget:
        room.round_budget = budget
        _write_index(rooms)
        logger.info("rooms: room %s round budget set to %d (0 = inherit)", room_id, budget)
    return room


def take_pending(room_id: str) -> list[str]:
    """Read AND clear the parked speaker queue. The only reader of ``pending_queue``.

    Read-and-clear in one indexed write, because the alternative — read here, clear later —
    lets two concurrent rounds over one room both inherit the same remainder and give every
    parked member two turns. Draining it makes the carry-over a once-only debt: whoever starts
    the next round owes those turns, and a round that then pauses parks its own remainder
    again through :func:`pause_room`.
    """
    rooms = _read_index_strict()
    room = _require_indexed_room(rooms, room_id)
    parked = list(room.pending_queue)
    if parked:
        room.pending_queue = []
        _write_index(rooms)
        logger.info("rooms: room %s resumes owing %d queued turn(s)", room_id, len(parked))
    return parked


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

    ``created_at`` comes off the :class:`Room`, not off the transcript's metadata line.
    The transcript log is created LAZILY on the first message write, so its own
    ``created_at`` is *the first message's* timestamp on a room that has spoken — 17.6s of
    drift on a room measured live, and a week for a room that sat idle before anyone
    spoke — and absent entirely on a room nobody has spoken in, which exported
    ``created_at: ""`` and dropped the ``Created:`` header row altogether. The merge
    belongs here rather than in the handler because this function's whole contract is
    "everything a renderer needs": a payload that omits the room's own creation time makes
    every caller re-derive the merge, and the second one to do so will derive it
    differently.

    The metadata dict is COPIED before the merge. ``ConversationLog.get_metadata`` returns
    its mtime-keyed cache entry itself, so writing into the returned dict would publish the
    room's creation time into the transcript log's cached metadata for every later reader.
    """
    room = require_room(room_id)
    meta = dict(room_log(room_id).get_metadata(TRANSCRIPT_KEY))
    meta["created_at"] = room.created_at
    return room, meta, read_messages(room_id)
