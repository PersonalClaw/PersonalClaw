"""HTTP surface for Agent Rooms — create, list, member management, transcript, export.

Built before any UI exists, so a room is fully drivable with ``curl``: `AGENT-ROOMS`
session 1 delivers the store and the member model, and the frontend arrives with the
arbiter that makes a room worth looking at. Driving the real routes is what validates the
store; a store with only unit tests is a store nobody has used.

Every route funnels its refusals through one ``except RoomError`` into :func:`_refusal`,
which answers from the single :data:`_REFUSALS` table — so a new refusal is one registry
row, one raise and one row here, rather than a per-route translation table that drifts.
Why that table names each code as a *literal* instead of forwarding ``exc.code`` is
argued at :data:`_REFUSALS`; it is the append-only registry rail, not a style choice.

Two postures worth naming here, because they are invisible in the route bodies:

* **The kill switch fails closed.** :func:`_require_enabled` runs first on every route,
  including the reads, so ``rooms.enabled=false`` means the feature is off rather than
  "off for writes but still listing".
* **App-scoped callers are already refused, with no code here.** ``/api/rooms`` sits under
  ``apps.permissions.APP_SCOPED_PREFIXES``, and ``app_request_denial`` fails closed for a
  permission an app has not declared — so an installed app cannot reach these routes
  unless it declares them. That is asserted in the tests rather than re-implemented.

The human is the only caller that reaches these routes. Posting a message is the one route
that runs anything afterwards: it hands the room to ``rooms.arbiter`` (see
:func:`api_room_message_post`), which decides the speaker order, bounds the round, and drives
each member's turn through ``rooms.turn``. The cursors that keep that feed from re-sending
what a member has already read are `AR-4`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import web

from personalclaw.config import loader as config_loader
from personalclaw.dashboard import session_export
from personalclaw.http_errors import json_error
from personalclaw.request_validation import (
    RequestValidationError,
    json_object_body,
    require_string,
    string_field,
)
from personalclaw.rooms import arbiter, posture, store

logger = logging.getLogger(__name__)


def _require_enabled() -> None:
    """Raise ``rooms_disabled`` unless the feature is on. First line of every route."""
    if not store.rooms_enabled():
        raise store.RoomError(
            "rooms_disabled", "Agent Rooms is disabled. Enable rooms.enabled to use it."
        )


#: Every refusal this surface can put on the wire: a store ``RoomError.code`` → the
#: emitter that answers it, status and all.
#:
#: A table here rather than a status on ``RoomError`` itself, because the store is also
#: reachable from a CLI and from ``rooms.turn``, neither of which has a status code —
#: HTTP framing belongs to the HTTP layer.
#:
#: **Why each row spells its code out twice.** The first shape was one line repeated in
#: all eight ``except`` blocks — ``json_error(exc.code, message=exc.message,
#: status=_status_for(exc.code))`` — and it reads better than this does. It also makes the
#: wire code a COMPUTED value, which is the one hole in the append-only registry rail
#: (``tests/test_http_error_codes_append_only.py``): a static reader cannot tell which
#: codes a module can emit, so a brand-new unregistered code enters without the rail
#: noticing, and eight such sites arrived at once. Naming the literal puts all thirteen
#: back inside ``test_every_emitted_code_is_registered`` — the check that makes the
#: registry a contract rather than a list. It is also why the status lives in this row
#: instead of a second ``code -> status`` mapping: two tables keyed by the same code are
#: two places to forget a row.
#:
#: Exhaustive over the store BY TEST, not by hope. ``tests/test_rooms_api.py`` reds when a
#: raised code has no row here, when a row's key and its emitted literal disagree, and
#: when this module emits a computed code again.
_REFUSALS: dict[str, Callable[[str], web.Response]] = {
    # 403 — the kill switch, checked first on every route including the reads.
    "rooms_disabled": lambda msg: json_error("rooms_disabled", message=msg, status=403),
    # 503 — the roster is unknown, so the request is refused rather than half-answered.
    "room_state_unreadable": lambda msg: json_error(
        "room_state_unreadable", message=msg, status=503
    ),
    # 404 — the addressed room or member does not exist.
    "room_not_found": lambda msg: json_error("room_not_found", message=msg, status=404),
    "room_member_not_found": lambda msg: json_error(
        "room_member_not_found", message=msg, status=404
    ),
    # 409 — the room is in a state that refuses this change.
    "room_archived": lambda msg: json_error("room_archived", message=msg, status=409),
    "room_member_exists": lambda msg: json_error("room_member_exists", message=msg, status=409),
    # 400 — a refused request, not a server fault: each of these is a validation branch.
    "room_title_required": lambda msg: json_error("room_title_required", message=msg, status=400),
    "room_id_exhausted": lambda msg: json_error("room_id_exhausted", message=msg, status=400),
    "room_member_name_required": lambda msg: json_error(
        "room_member_name_required", message=msg, status=400
    ),
    "room_member_name_invalid": lambda msg: json_error(
        "room_member_name_invalid", message=msg, status=400
    ),
    "room_member_unknown_agent": lambda msg: json_error(
        "room_member_unknown_agent", message=msg, status=400
    ),
    "room_member_limit": lambda msg: json_error("room_member_limit", message=msg, status=400),
    "room_invalid_listen_policy": lambda msg: json_error(
        "room_invalid_listen_policy", message=msg, status=400
    ),
    # 400 — the member's declared safety posture is unreadable, or reaches past the room's
    # own. Both are authoring mistakes in a declaration a human wrote, which is why neither
    # is clamped silently: see ``rooms.posture``.
    "room_member_posture_invalid": lambda msg: json_error(
        "room_member_posture_invalid", message=msg, status=400
    ),
    "room_member_posture_widens": lambda msg: json_error(
        "room_member_posture_widens", message=msg, status=400
    ),
    # 403 — somebody other than the human tried to approve a member's tool call. A refused
    # IDENTITY, not a refused request shape, so it is an authorization answer.
    "room_approver_not_human": lambda msg: json_error(
        "room_approver_not_human", message=msg, status=403
    ),
    # 400 — the per-room round-budget override is out of range. Refused rather than clamped:
    # see ``store.set_round_budget``.
    "room_round_budget_invalid": lambda msg: json_error(
        "room_round_budget_invalid", message=msg, status=400
    ),
}


def _refusal(exc: store.RoomError) -> web.Response:
    """A store refusal on the wire. The one funnel every route's ``except`` block uses.

    An unlisted code degrades to ``bad_request`` and is logged at ERROR rather than
    escaping as a 500: a missing row is a bug in this module, not something the caller can
    act on. The table is kept exhaustive by test, so this branch is unreachable on a green
    tree — it exists so that "unreachable" is a claim about the tests and not about luck.
    """
    emit = _REFUSALS.get(exc.code)
    if emit is None:
        logger.error(
            "rooms: store refusal %r has no _REFUSALS row — answering bad_request. Add the "
            "row (and its HTTP_ERROR_CODES row) in the change that raises it.",
            exc.code,
        )
        return json_error("bad_request", message=exc.message, status=400)
    return emit(exc.message)


def _room_payload(room: store.Room) -> dict:
    """One room on the wire, with its resolved budget alongside its declared one.

    ``effective_round_budget`` is included because a caller reading ``round_budget: 0``
    would otherwise have to know that 0 means "inherit" and go read the config itself to
    learn the real number. ``max_round_budget`` travels with it so the UI's stepper takes
    its bounds from the save path rather than restating them — a control whose range
    disagrees with the writer's is an offer the writer refuses.
    """
    payload = room.to_dict()
    payload["effective_round_budget"] = store.effective_round_budget(room)
    payload["max_round_budget"] = store.MAX_ROOM_ROUND_BUDGET
    payload["transcript_path"] = str(store.transcript_path(room.id))
    return payload


def _member_bindings(room: store.Room) -> list[dict]:
    """What each member's agent binding IS — its model and runtime — or that it is gone.

    A member is a binding KEY, and :func:`~personalclaw.rooms.store.add_member` refuses an
    unconfigured one. But a binding can be DELETED from ``config.json`` after the member was
    added, and at that point the ordinary resolution path is actively misleading:
    ``config.loader.resolve_agent_bindings`` falls back to ``default_agent`` for an unknown
    name (its own docstring says so), so asking it would report the DEFAULT agent's model as
    this member's. That is the fabricated-value shape — a member whose binding vanished would
    render as healthy, bound to a model it is not.

    So this reads ``config.agents`` directly, the same predicate
    ``store._validate_member_name`` uses, and answers ``configured: false`` with EMPTY model
    and provider when the binding is absent. Empty means unknown here and the UI renders it as
    unknown; it never means "the default".

    Lives in the handler rather than in ``rooms/``: it is a presentation join of two existing
    domain reads, the same reasoning that keeps ``session_export.render`` on this side of the
    line.
    """
    agents = config_loader.AppConfig.load().agents
    out: list[dict] = []
    for member in room.members:
        profile = agents.get(member.name)
        if profile is None:
            out.append({"name": member.name, "configured": False, "model": "", "provider": ""})
            continue
        out.append(
            {
                "name": member.name,
                "configured": True,
                # The binding's own model, verbatim. Empty is a REAL state — a binding that
                # names no model runs on the configured default for its use case — so it is
                # passed through rather than substituted, and the UI says "default model".
                "model": profile.model,
                "provider": profile.provider,
                "description": profile.description,
            }
        )
    return out


async def api_rooms_list(request: web.Request) -> web.Response:
    """GET /api/rooms — every room, newest first. ``?archived=1`` includes archived ones."""
    try:
        _require_enabled()
    except store.RoomError as exc:
        return _refusal(exc)
    include_archived = request.query.get("archived", "") in ("1", "true", "yes")
    rooms = store.list_rooms(include_archived=include_archived)
    return web.json_response({"rooms": [_room_payload(r) for r in rooms]})


async def api_rooms_create(request: web.Request) -> web.Response:
    """POST /api/rooms {title} — create a room."""
    try:
        _require_enabled()
        body = await json_object_body(request, empty_ok=False)
        title = require_string(body, "title")
        room = store.create_room(title)
    except RequestValidationError as exc:
        return exc.response
    except store.RoomError as exc:
        return _refusal(exc)
    return web.json_response({"room": _room_payload(room)}, status=201)


async def api_room_get(request: web.Request) -> web.Response:
    """GET /api/rooms/{room_id} — one room, its members, its posture, and its transcript.

    ``member_posture`` is the RESOLVED answer per member — the tier, allowlist and budget each
    one actually runs under once the restrictive default and the operator ceiling are folded
    in. The declaration is already on the wire inside each member record; the resolution is
    the part a reader cannot compute, and without it "this member is read-only" would be a
    claim the UI had to re-derive. It is the contract `AR-8`'s member chips render from.

    ``member_bindings`` is the other half a member chip needs and cannot compute: which agent
    binding each member IS, and whether that binding still exists. See
    :func:`_member_bindings` for why it is not resolved through the ordinary binding path.

    One request rather than three, because the room surface reads all of this together on every
    poll: splitting posture and bindings onto their own routes would triple the polling traffic
    for state that is only ever read as one picture.
    """
    room_id = request.match_info["room_id"]
    try:
        _require_enabled()
        room = store.require_room(room_id)
        messages = store.read_messages(room_id)
        member_posture = posture.describe_members(room)
        member_bindings = _member_bindings(room)
    except store.RoomError as exc:
        return _refusal(exc)
    return web.json_response(
        {
            "room": _room_payload(room),
            "member_posture": member_posture,
            "member_bindings": member_bindings,
            "messages": messages,
        }
    )


async def api_room_update(request: web.Request) -> web.Response:
    """PATCH /api/rooms/{room_id} {round_budget} — the room's own budget override.

    The write path ``Room.round_budget`` shipped without. ``effective_round_budget`` read the
    field and :func:`_room_payload` published it, so a client could see a per-room budget it had
    no way to set — which is worse than an absent field, because publishing it implies it is
    settable. One PATCH rather than widening the config PATCH allowlist: ``rooms.round_budget``
    in ``_EDITABLE_CONFIG`` is the INSTALL-wide default, and this is one room's override of it.

    ``round_budget`` is the only accepted key, and it is REQUIRED. A PATCH that silently
    ignored an unknown key would let a caller believe it had changed something; naming the one
    key this route owns is how a typo comes back as an error instead of as a no-op.
    """
    room_id = request.match_info["room_id"]
    try:
        _require_enabled()
        body = await json_object_body(request, empty_ok=False)
        unknown = sorted(k for k in body if k != "round_budget")
        if unknown or "round_budget" not in body:
            raise store.RoomError(
                "room_round_budget_invalid",
                f"This route sets round_budget and nothing else; it was given "
                f"{', '.join(sorted(body)) or 'nothing'}.",
            )
        room = store.set_round_budget(room_id, body["round_budget"])
    except RequestValidationError as exc:
        return exc.response
    except store.RoomError as exc:
        return _refusal(exc)
    return web.json_response({"room": _room_payload(room)})


async def api_room_archive(request: web.Request) -> web.Response:
    """POST /api/rooms/{room_id}/archive — archive a room. Idempotent."""
    room_id = request.match_info["room_id"]
    try:
        _require_enabled()
        room = store.archive_room(room_id)
    except store.RoomError as exc:
        return _refusal(exc)
    return web.json_response({"room": _room_payload(room)})


async def api_room_member_add(request: web.Request) -> web.Response:
    """POST /api/rooms/{room_id}/members {name, role_blurb?, listen_policy?, profile_narrowing?}.

    ``profile_narrowing`` is the member's own safety posture — the capability axes it may
    reach, narrowed against the room's. Omitting it is the common case and yields the
    RESTRICTIVE default (``rooms.posture.DEFAULT_MEMBER_TOOL_GRANTS``), never the room's own
    reach, so an unconfigured member is the read-only one.
    """
    room_id = request.match_info["room_id"]
    try:
        _require_enabled()
        body = await json_object_body(request, empty_ok=False)
        name = require_string(body, "name")
        role_blurb = string_field(body, "role_blurb")
        # string_field, not optional_string: both fields are omittable, and an omitted
        # listen policy means the declared default rather than a refusal.
        listen_policy = string_field(body, "listen_policy", default=store.DEFAULT_LISTEN_POLICY)
        # Passed through unvalidated ON PURPOSE: the safety axes and their refusals belong to
        # ``rooms.posture``, and re-checking the shape here would be a second opinion about
        # what a posture is. ``add_member`` raises the posture codes this route maps above.
        room = store.add_member(
            room_id,
            name,
            role_blurb=role_blurb,
            listen_policy=listen_policy,
            profile_narrowing=body.get("profile_narrowing"),
        )
    except RequestValidationError as exc:
        return exc.response
    except store.RoomError as exc:
        return _refusal(exc)
    return web.json_response({"room": _room_payload(room)}, status=201)


async def api_room_member_remove(request: web.Request) -> web.Response:
    """DELETE /api/rooms/{room_id}/members/{name} — remove a member."""
    room_id = request.match_info["room_id"]
    name = request.match_info["name"]
    try:
        _require_enabled()
        room = store.remove_member(room_id, name)
    except store.RoomError as exc:
        return _refusal(exc)
    return web.json_response({"room": _room_payload(room)})


async def api_room_message_post(request: web.Request) -> web.Response:
    """POST /api/rooms/{room_id}/messages {content} — the human speaks, then the room answers.

    Only the human may POST: the message is written with an empty ``speaker``, which is what
    ``history.speaker_of`` reports for the human. A member's message is written by
    :func:`~personalclaw.rooms.turn.run_member_turn` after that member's provider answers,
    never by a caller claiming a speaker name here — accepting one would let any caller forge
    a member's words into the transcript every other member then reads as that member's
    position.

    The human's line is appended FIRST and synchronously, so a 201 means it is durable even
    if every member then fails; the round itself runs in the background because N provider
    turns do not fit in a request. The response carries ``speaking``: the arbiter's FIFO
    speaker queue for this message, in the order those members will speak — which is what
    lets a caller (and the `AR-8` UI) distinguish "nobody was listening" from "the answers
    have not landed yet". Poll ``GET /api/rooms/{room_id}`` for the replies.

    ``speaking`` is computed through :func:`~personalclaw.rooms.arbiter.resume_queue` rather
    than the bare mention queue, so answering a PAUSED room reports the turns it still owed
    ahead of the ones this message asks for — the same order the round will actually take. The
    parked queue is read here and not consumed; the round drains it.

    **The budget is refilled synchronously too**, on the same reasoning as the append: a human
    message is what resets ``rounds_used`` and closes a standing pause item, and doing that
    inside the background round would skip it exactly when the round is dropped — leaving a
    room paused that its human had already answered.
    """
    room_id = request.match_info["room_id"]
    try:
        _require_enabled()
        body = await json_object_body(request, empty_ok=False)
        content = require_string(body, "content")
        store.append_message(room_id, role="user", content=content, speaker=store.HUMAN_SPEAKER)
        state = _gateway_state(request)
        owed = store.require_room(room_id).pending_queue
        arbiter.note_human_message(state, room_id)
        messages = store.read_messages(room_id)
        speaking = arbiter.resume_queue(owed, store.members_for_turn(room_id), content)
    except RequestValidationError as exc:
        return exc.response
    except store.RoomError as exc:
        return _refusal(exc)
    if speaking:
        _start_round(state, room_id, content)
    return web.json_response({"messages": messages, "speaking": speaking}, status=201)


def _gateway_state(request: web.Request) -> Any:
    """The gateway state this request carries, or None when it has none.

    ``app["state"]`` rather than ``app.get("state")``, which is both this surface's idiom and
    the only one that is honest under ``make_mocked_request``: its app is a ``MagicMock``, so
    ``.get`` answers a truthy mock for a key nobody set, and every guard downstream would wave
    through a mock. A ``KeyError`` here is the honest "there is no gateway behind this
    request", which the inbox writes tolerate and :func:`_start_round` refuses.

    ``Any`` rather than a state protocol, matching ``inbox.emit_attention_item`` and
    ``rooms.arbiter``: the gateway state is assembled at runtime and there is no declared type
    for it, so a narrower annotation here would only be a cast the handlers then fight.
    """
    try:
        return request.app["state"]
    except KeyError:
        return None


def _start_round(state: Any, room_id: str, content: str) -> None:
    """Fire the arbiter's round in the background, holding a reference so it is not GC'd.

    ``state._background_tasks`` is the shipped set every other fire-and-forget handler
    parks its task in (``dashboard/side.py`` is the closest sibling); an un-referenced
    ``create_task`` is collectable mid-turn, which would make a member's reply vanish for
    reasons no log explains.

    A missing ``SessionManager`` is logged at ERROR and drops the round rather than 500-ing a
    request whose message is already durably on the transcript — the human's words are the
    part they cannot re-derive, and every member's reply is one more human message away. The
    log is the point: a dropped round must be findable, not inferred from a quiet room.
    """
    sessions = getattr(state, "sessions", None) if state is not None else None
    if sessions is None:
        logger.error(
            "rooms: no SessionManager on the dashboard state — room %s takes no turn", room_id
        )
        return
    task = asyncio.create_task(arbiter.run_round(state, sessions, room_id, content))
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)


async def api_room_export(request: web.Request) -> web.Response:
    """GET /api/rooms/{room_id}/export?format=md|json — the transcript, redacted.

    The render happens HERE rather than in the store: ``session_export.render`` is the
    shipped renderer for both formats, and ``rooms/`` may not import it because a domain
    module reaching up into the HTTP surface is the edge the structural import-direction
    ratchet refuses. The store hands over the payload; presentation stays on this side.
    """
    room_id = request.match_info["room_id"]
    fmt = request.query.get("format", "md")
    try:
        _require_enabled()
        room, meta, messages = store.export_payload(room_id)
        text, content_type = session_export.render(
            fmt, title=room.title, key=room.id, meta=meta, messages=messages
        )
    except store.RoomError as exc:
        return _refusal(exc)
    except ValueError:
        # session_export.render's contract for an unknown format, translated into this
        # surface's envelope rather than leaking a 500 from a caller typo.
        return json_error("room_export_format_invalid", status=400)
    return web.Response(text=text, content_type=content_type)
