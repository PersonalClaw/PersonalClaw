"""The one way a message enters a chat: with its id, answered by its receipt, and taken once.

A send names its message (``client_message_id``: ``msg_`` and 26 letters and digits, which the
gateway mints when the send names none), and the chat takes it in here (:func:`take_in`), in one
step with no wait in it. It looks the id up among the messages the chat took, decides what becomes
of the message by the rules a send has always had, and records it:

* A repeat of a message the chat took, with the same words (:func:`body_digest`), is answered that
  message's receipt again and runs nothing: a double click, a flaky network, a script's retry, an
  outbox resend. The same id with other words is refused (``message_id_conflict``).
* What becomes of it (its ``disposition``): ``started`` when nothing runs, ``steered`` into the
  running turn when it is a steer that turn takes, ``queued`` behind it otherwise, ``replaced`` when
  the mid-turn policy stops the running turn for it.
* A steer that names the turn it is for (``expected_message_id``) lands nowhere when that turn is
  not the one running, or the running one takes none (``no_running_turn``, ``turn_not_current``,
  ``turn_not_steerable``): the page offers to send or queue it instead.

What a chat took is read from the chat itself, once, when it is first sent to: its rows (a row's
``meta.message_id`` and ``meta.receipt``, a steer's ``meta.steered_into``, a merged row's
``meta.merged``), its steers waiting to be taken and its queue. Every take-in after that is kept
beside the chat, so one withdrawn from the queue stays taken; a message still queued when the
gateway restarts is lost with the queue, and a repeat of it after the restart runs as new.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw import ids
from personalclaw.http_errors import json_error
from personalclaw.ledger.hashing import canonical_digest
from personalclaw.own_words import OWN_WORDS
from personalclaw.turn_source import (
    DASHBOARD_SOURCE,
    MERGED,
    STEERED,
    TURN_STARTS,
    source_of,
    starts_a_turn,
)

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

#: The ``meta`` key of a row's message id, and the item key of a queued message's.
MESSAGE_ID = "message_id"
#: The ``meta`` key of what the send of a row's message was answered, and the item key of a queued
#: message's: when it was taken in (``accepted_at``), what became of it (``disposition``) and the
#: digest of what it said (``body_digest``).
RECEIPT = "receipt"
#: The ``meta`` key of a steer's row: the message that started the turn it joined.
STEERED_INTO = "steered_into"

STARTED, STEERED_IN, QUEUED, REPLACED = "started", "steered", "queued", "replaced"


@dataclass(frozen=True)
class Refusal:
    """A message the chat did not take, and why: nothing of it was kept or run. ``extra`` is what
    the error names besides (``expected`` and ``actual``, or ``kind``)."""

    code: str
    extra: Mapping[str, str] = field(default_factory=dict)

    def response(self) -> web.Response:
        """The refusal as the chat's door answers it, in its own words (``http_errors`` holds each
        code's meaning)."""
        extra = dict(self.extra) or None
        if self.code == "invalid_message_id":
            return json_error(
                "invalid_message_id",
                message="A message id must be `msg_` followed by 26 letters and digits; nothing "
                "was sent.",
                status=400,
            )
        if self.code == "message_id_conflict":
            return json_error(
                "message_id_conflict",
                message="That message id was already sent in this chat with different text, so "
                "nothing was sent again.",
                status=409,
            )
        if self.code == "no_running_turn":
            return json_error(
                "no_running_turn",
                message="No turn is running in this chat, so there was nothing to steer; send it "
                "as a message.",
                status=409,
            )
        if self.code == "turn_not_current":
            return json_error(
                "turn_not_current",
                message="That turn is no longer running, so nothing was changed.",
                status=409,
                error_extra=extra,
            )
        return json_error(
            "turn_not_steerable",
            message="This turn cannot take a message while it runs; queue it instead.",
            status=409,
            error_extra=extra,
        )


@dataclass(frozen=True)
class Receipt:
    """What a send is answered: the message it took in, when, what became of it, and the message
    whose turn it runs in (``turn_message_id``: its own, or the running turn's for a steer)."""

    session: str
    message_id: str
    accepted_at: str
    disposition: str
    turn_message_id: str
    duplicate: bool = False

    def answer(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def response(self, routing_suggestion: Mapping[str, Any] | None = None) -> web.Response:
        """The receipt as the send's answer, with the agent it suggests when there is one."""
        return web.json_response(
            {
                **self.answer(),
                **({"routing_suggestion": routing_suggestion} if routing_suggestion else {}),
            }
        )

    def event(self) -> bytes:
        """The receipt as a stream's first event."""
        return f"event: receipt\ndata: {json.dumps(self.answer())}\n\n".encode()


@dataclass(frozen=True)
class Intake:
    """A message at a door of the chat, as the door read it.

    ``message`` is what runs, ``body_digest`` what was sent (:func:`body_digest`). ``queue_mode`` is
    how a message sent while a turn runs joins it, and ``expected_message_id`` the turn a steer is
    for. ``replaces`` says the mid-turn policy stops the running turn for it. ``meta`` is the row's
    when it starts a turn; ``own_words``, ``pastes`` and ``heard`` what the send said of its words
    when it waits for or joins the running turn (``running_turn.steer``, ``chat_queue``).

    The row a turn starts with is a ``role`` row (``turn_source.TURN_STARTS``) of ``cls``, showing
    ``shown`` when it shows other words than run (a channel's message, masked). ``source`` is where
    the message came from, ``channel`` the chat channel that already shows it, ``files`` what it
    carries, ``asked_for_by`` who asked for the work it reports on, and ``attended`` says the owner
    typed it where they are (``chat_queue``): each kept on the queue item, when it waits. A door
    whose message starts its turn whatever runs (``starts``: plan mode's next turn) is never
    queued."""

    message: str
    message_id: str
    body_digest: str
    queue_mode: str = ""
    expected_message_id: str = ""
    replaces: bool = False
    meta: Mapping[str, Any] | None = None
    own_words: str | None = None
    pastes: Sequence[Mapping[str, Any]] = ()
    heard: bool = False
    role: str = "user"
    cls: str = "msg msg-u"
    shown: str | None = None
    source: Mapping[str, str] = DASHBOARD_SOURCE
    channel: str = ""
    files: Sequence[str] = ()
    asked_for_by: Mapping[str, str] | None = None
    attended: bool = False
    starts: bool = False

    @classmethod
    def at_door(intake, message: str, **fields: Any) -> Intake:  # noqa: N804 - `cls` is a field
        """A message a door takes in under an id the gateway mints, which no repeat can name: a
        channel's (its delivery is told new by the channel's own door), an automation's, a
        helper's report, a loop's cycle, a program's."""
        return intake(
            message=message,
            message_id=ids.new_id("msg"),
            body_digest=body_digest(message, "", "", fields.get("meta")),
            **fields,
        )


def message_id_of(body: Mapping[str, Any], key: str = "client_message_id") -> str | Refusal:
    """The message id a send names under *key*, a new one when it names none (an absent or null
    *key*), or the refusal of any other shape, before anything of the send is kept."""
    value = body.get(key)
    if value is None:
        return ids.new_id("msg")
    if isinstance(value, str) and ids.is_id("msg", value):
        return value
    return Refusal("invalid_message_id")


def expected_id_of(body: Mapping[str, Any]) -> str | Refusal:
    """The turn a steer names (``expected_message_id``): ``""`` when it names none."""
    return (
        ""
        if body.get("expected_message_id") is None
        else message_id_of(body, "expected_message_id")
    )


def body_digest(message: str, agent: str, queue_mode: str, meta: Mapping[str, Any] | None) -> str:
    """The digest of a send: the text as sent, the agent and delivery it asks for, and its meta as
    the door kept it. A repeat with the same id is the same message only when this is the same."""
    return canonical_digest(
        {"message": message, "agent": agent, "queue_mode": queue_mode, "meta": dict(meta or {})}
    )


@dataclass(frozen=True)
class _Taken:
    body_digest: str
    receipt: Receipt


@dataclass(frozen=True)
class _Held:
    """A message a send is taking in while it waits (:func:`hold`): settled with that send."""

    body_digest: str
    settled: asyncio.Future[None]


#: What each chat took, or a send is taking, by message id, kept beside the chat (it is slotted).
_TAKEN: weakref.WeakKeyDictionary[_ChatSession, dict[str, _Taken | _Held]] = (
    weakref.WeakKeyDictionary()
)


async def repeat_of(
    session: _ChatSession, message_id: str, digest: str
) -> Receipt | Refusal | None:
    """The answer to a send of *message_id* that the chat took already: its receipt again when it
    said the same (*digest*), the refusal when it said something else; ``None`` for a new one.

    One another send is taking in while it waits (:func:`hold`) is answered once that send is: as
    the message it took, or as a new one when it took nothing. A door asks this before anything of
    the send changes the chat, so a repeat changes nothing."""
    while True:
        held = _taken(session).get(message_id)
        if held is None:
            return None
        if held.body_digest != digest:
            return Refusal("message_id_conflict")
        if isinstance(held, _Taken):
            return dataclasses.replace(held.receipt, duplicate=True)
        await asyncio.shield(held.settled)


def repeat_now(session: _ChatSession, message_id: str, digest: str) -> Receipt | Refusal | None:
    """:func:`repeat_of` for a door that must not wait: the answer to a send of *message_id* the
    chat took already, ``None`` for one it did not take (or that another send is taking)."""
    held = _taken(session).get(message_id)
    if not isinstance(held, _Taken):
        return None
    if held.body_digest != digest:
        return Refusal("message_id_conflict")
    return dataclasses.replace(held.receipt, duplicate=True)


def hold(session: _ChatSession, message_id: str, digest: str) -> Callable[[], None]:
    """Hold *message_id* while the send taking it in waits before :func:`take_in` (it moves the chat
    to the agent it names), so a repeat sent meanwhile waits for its answer (:func:`repeat_of`)
    instead of taking the message in, or changing the chat, a second time. :func:`take_in` settles
    the hold, whatever it answers; the release returned is for a send that ends before it."""
    held = _Held(digest, asyncio.get_running_loop().create_future())
    _taken(session)[message_id] = held
    return functools.partial(_settle, session, message_id, held)


def _settle(session: _ChatSession, message_id: str, held: _Held) -> None:
    taken = _taken(session)
    if taken.get(message_id) is held:
        del taken[message_id]
    if not held.settled.done():
        held.settled.set_result(None)


def take_in(state: DashboardState, session: _ChatSession, intake: Intake) -> Receipt | Refusal:
    """Take *intake* into *session*, or refuse it, in one step with no wait in it: two identical
    sends racing take one message.

    A message that starts a turn is written as its row here, and the caller starts the turn before
    anything else waits. One that replaces the running turn is queued here, and the caller stops
    that turn (:func:`not_replaced` when it cannot)."""
    held = _taken(session).get(intake.message_id)
    if isinstance(held, _Held):  # this send's own: a repeat of it waits in `repeat_of`
        try:
            return _take(state, session, intake)
        finally:
            _settle(session, intake.message_id, held)
    if (repeat := repeat_now(session, intake.message_id, intake.body_digest)) is not None:
        return repeat
    return _take(state, session, intake)


def _take(state: DashboardState, session: _ChatSession, intake: Intake) -> Receipt | Refusal:
    from personalclaw.dashboard import (  # it imports the chat's module, which imports this
        running_turn,
    )

    current = _running_start(session)
    if intake.queue_mode == "steer" and intake.expected_message_id:
        if (
            refused := _not_its_turn(state, session, current, intake.expected_message_id)
        ) is not None:
            return refused
    accepted_at = datetime.now(timezone.utc).isoformat()
    noted = {"accepted_at": accepted_at, "body_digest": intake.body_digest}
    into = intake.message_id
    if current is None or intake.starts:
        disposition = STARTED
        said = {} if intake.own_words is None else {OWN_WORDS: intake.own_words}
        meta = {**said, **(intake.meta or {}), MESSAGE_ID: into}
        meta[RECEIPT] = {**noted, "disposition": STARTED}
        shown = intake.message if intake.shown is None else intake.shown
        session.append(intake.role, shown, intake.cls, meta=meta, source=intake.source)
        # Written as it happens, so its time is the row's own: the receipt's.
        accepted_at = meta[RECEIPT]["accepted_at"] = session.messages[-1]["ts"]
    elif (
        intake.queue_mode == "steer"
        and not intake.replaces
        and running_turn.steer(
            state,
            session,
            intake.message,
            ts=accepted_at,
            own_words=intake.own_words,
            heard=intake.heard,
            pastes=intake.pastes,
            message_id=intake.message_id,
            receipt={**noted, "disposition": STEERED_IN},
            steered_into=_id_of(current),
        )
    ):
        disposition, into = STEERED_IN, _id_of(current)
    else:
        disposition = REPLACED if intake.replaces else QUEUED
        session.queue_append(
            intake.message,
            channel=intake.channel,
            files=list(intake.files) or None,
            own_words=intake.own_words,
            source=intake.source,
            asked_for_by=intake.asked_for_by,
            pastes=intake.pastes,
            attended=intake.attended,
            message_id=intake.message_id,
            receipt={**noted, "disposition": disposition},
        )
    receipt = Receipt(session.key, intake.message_id, accepted_at, disposition, into)
    _taken(session)[intake.message_id] = _Taken(intake.body_digest, receipt)
    return receipt


def not_replaced(session: _ChatSession, receipt: Receipt) -> Receipt:
    """The message *receipt* took in to replace the running turn waits behind it instead: the turn
    could not be stopped. Its record says so, and so does the receipt returned."""
    queued = dataclasses.replace(receipt, disposition=QUEUED)
    for item in session._queue:
        if item.get(MESSAGE_ID) == receipt.message_id:
            item[RECEIPT] = {**item.get(RECEIPT, {}), "disposition": QUEUED}
    taken = _taken(session)
    taken[receipt.message_id] = _Taken(taken[receipt.message_id].body_digest, queued)
    return queued


def with_message_id(role: str, ts: str, meta: dict | None) -> dict | None:
    """*meta* for a row as it is written: a row that starts a turn as it happens (no *ts*: not one
    read back) is given a message id when its door gave it none."""
    if role not in TURN_STARTS or ts or (meta or {}).get(MESSAGE_ID) or (meta or {}).get(STEERED):
        return meta
    return {**(meta or {}), MESSAGE_ID: ids.new_id("msg")}


def write_drained(
    session: _ChatSession,
    items: Sequence[Mapping[str, Any]],
    text: str,
    *,
    role: str,
    cls: str,
    meta: Mapping[str, Any],
    source: Mapping[str, str],
) -> None:
    """Write the row the queued *items*, taken in when they were queued, run as: *text*, a *role*
    row of *cls*, keeping each message's id and receipt (:func:`carried`)."""
    session.append(role, text, cls, meta={**meta, **carried(items)} or None, source=source)


def carried(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the row queued *items* run as keeps of their ids: one message's id and receipt as its
    own, or each of several messages' with where it came from (:data:`MERGED`)."""
    if len(items) == 1:
        item = items[0]
        return {
            **({MESSAGE_ID: item[MESSAGE_ID]} if item.get(MESSAGE_ID) else {}),
            **({RECEIPT: dict(item[RECEIPT])} if item.get(RECEIPT) else {}),
        }
    return {
        MERGED: [
            {
                MESSAGE_ID: item.get(MESSAGE_ID, ""),
                "source": source_of(item),
                **({RECEIPT: dict(item[RECEIPT])} if item.get(RECEIPT) else {}),
            }
            for item in items
        ]
    }


def _taken(session: _ChatSession) -> dict[str, _Taken | _Held]:
    held = _TAKEN.get(session)
    if held is None:
        held = _TAKEN[session] = _read(session)
    return held


def _read(session: _ChatSession) -> dict[str, _Taken | _Held]:
    """What *session* holds of the messages it took: its rows, with the tails it was rewound past,
    its steers and its queue."""
    held: dict[str, _Taken | _Held] = {}
    rows = [
        r
        for row in session.messages
        for r in (
            row,
            *(m for snap in row.get("rewound") or () for m in (snap.get("messages") or ())),
        )
    ]
    for meta in [r.get("meta") for r in rows] + [s.get("meta") for s in session._steers]:
        if isinstance(meta, Mapping):
            _note(session, held, meta, meta.get(STEERED_INTO))
            for each in meta.get(MERGED) or ():
                if isinstance(each, Mapping):
                    _note(session, held, each)
    for item in session._queue:
        _note(session, held, item)
    return held


def _note(
    session: _ChatSession, held: dict[str, _Taken | _Held], said: Mapping, into: object = ""
) -> None:
    message_id = said.get(MESSAGE_ID)
    if not isinstance(message_id, str) or not message_id or message_id in held:
        return
    found = said.get(RECEIPT)
    noted: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
    receipt = Receipt(
        session.key,
        message_id,
        str(noted.get("accepted_at") or ""),
        str(noted.get("disposition") or ""),
        into if isinstance(into, str) and into else message_id,
    )
    held[message_id] = _Taken(str(noted.get("body_digest") or ""), receipt)


def _running_start(session: _ChatSession) -> Mapping[str, Any] | None:
    """The row the running turn started with, or ``None`` when no turn runs."""
    if not session.running:
        return None
    return next((r for r in reversed(session.messages) if starts_a_turn(r)), {})


def _id_of(row: Mapping[str, Any]) -> str:
    meta = row.get("meta")
    found = meta.get(MESSAGE_ID) if isinstance(meta, Mapping) else None
    return found if isinstance(found, str) else ""


def _not_its_turn(
    state: DashboardState, session: _ChatSession, current: Mapping[str, Any] | None, expected: str
) -> Refusal | None:
    """Why a steer for the turn *expected* started cannot join the running one (*current*)."""
    if current is None:
        return Refusal("no_running_turn")
    if (actual := _id_of(current)) != expected:
        return Refusal("turn_not_current", {"expected": expected, "actual": actual})
    if kind := _runs_as_command(state, session, current):
        return Refusal("turn_not_steerable", {"kind": kind})
    return None


def _runs_as_command(state: DashboardState, session: _ChatSession, start: Mapping) -> str:
    """``compact`` or ``command`` when the turn *start* began is a slash word its runtime runs
    itself, which takes no message while it runs; ``""`` for any other turn."""
    from personalclaw.dashboard.chat_utils import (  # it imports the chat's module
        _SLASH_COMMANDS,
        _history_key_for,
        runs_as_command,
    )

    word = next(iter(str(start.get("content") or "").split()), "")
    if word not in _SLASH_COMMANDS:
        return ""
    if not runs_as_command(state.sessions.get_provider(_history_key_for(session.key)), word):
        return ""
    return "compact" if word == "/compact" else "command"
