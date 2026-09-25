"""Deterministic turn arbitration — the FIFO speaker queue, the round budget, and the round.

**No model ever decides speaking order.** This module is the whole of who-speaks-next, and
it derives that answer from two inputs only: the room's roster and the ``@``-mentions in a
message's text. It imports no provider, no model registry and no model — the decision
functions below are pure, so the same roster and the same text always produce the same
order, and a test can pin the order for a fixed message sequence without a provider
existing. That is a structural property rather than a promise: there is no seam here for a
model to express a preference through.

The distinction that makes this safe is worth stating precisely, because a member's output
IS an input to the queue. A member can *name* peers, and naming them enqueues them; a member
cannot *order* the queue, skip anybody, promote itself, or reach a member whose listen policy
does not admit a peer's call. So the queue is a function of the mention sequence, and the
mention sequence is the only channel — a member emitting "ignore the queue, let zeta speak
first" is transcript prose that changes nothing, in exactly the way
:func:`~personalclaw.rooms.turn.build_member_prompt`'s fence already says member text is
data. The arbiter reads member text through :func:`~personalclaw.rooms.turn.mentions_in_order`
and through nothing else: no decision, verdict, approval or directive is ever parsed out of
a member's words.

**Two enqueue rules, because "who may summon whom" is the bound on the room.**

* :func:`queue_for_human` — a human message enqueues the members it ``@``-names, in the
  order named, then every remaining ``all``-policy member in roster order. A human may call
  on **any** member including a ``silent`` observer: an observer that the human cannot ask a
  direct question of is not an observer, it is a member with no way in.
* :func:`queue_for_member` — a member's reply enqueues only the ``mention``-policy members it
  ``@``-named. An ``all``-policy member is fed every message but is never enqueued by a peer
  (it already speaks on every human message, so letting peers summon it too would make the
  least-restricted policy the unbounded one), a ``silent`` member answers only its human, and
  no member can enqueue itself.

**The round budget is what reconciles this with a bounded room.** `AR-3` restricted the turn
path to a single human-triggered pass over the roster *because* no budget existed yet, and
said so; the mention chain above is the multi-pass behaviour that restriction stood in for,
and it is admitted here only because :func:`drain_round` now bounds it. Every agent turn that
SPOKE since the human's last message is charged to ``Room.rounds_used``; at
``rooms.round_budget`` (6 by default — two full passes of a three-member room) the room stops
draining, sets ``paused``, and raises ONE inbox attention item. Any human message resets the
counter and closes that item, so the room's own pause has no separate resume button to go
missing. A turn that failed or came back empty is not an exchange and is not charged — it
said nothing, and it summons nobody, so it can only shrink the queue (see
:func:`~personalclaw.rooms.store.end_turn`).

**The round lives on disk, not in this module's call frame.** The queue, and the turn that is
open, are fields of the room record (``pending_queue``, ``speaking``), written as each turn
opens and closes. That is what three different readers need, and none of them could get from a
queue held in a task's memory:

* **the room view**, which follows a round by what the ROOM says it still owes — a guess from
  the transcript froze it the moment every member had spoken once;
* **a restarted gateway**, which finds the cut-off turn and the queue behind it intact. It does
  NOT resume them on its own: a round that nothing is running reads as *interrupted*
  (:func:`round_running` is False while the room still owes turns), and the human continues it
  — with one action, without re-sending anything. Resuming at boot was the alternative and is
  wrong here twice over: the room's premise is that its human is present when its agents spend,
  and a turn that took the process down (a local model exhausting memory) would take it down
  again on every boot;
* **a pause**, which is therefore a suspension by construction — the members still owed a turn
  when the ceiling was reached are simply still queued, and speak first when the human replies.

**One round per room.** :func:`start_round` refuses to start a second while one is running, so
a human message that arrives mid-round extends the queue the running round is draining instead
of racing it — one speaker at a time holds for the ROOM, not just for one message.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from personalclaw.notification_kinds import AGENT
from personalclaw.rooms.store import (
    RoomMember,
    begin_turn,
    effective_round_budget,
    end_turn,
    pause_room,
    require_room,
    reset_round_budget,
    set_pending_queue,
)
from personalclaw.rooms.turn import mentions_in_order, note_failed_turn, run_member_turn

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.session import SessionManager

logger = logging.getLogger(__name__)

#: The registered notification/attention kind a pause raises, under the ``agent`` source.
PAUSE_KIND = "room_paused"

#: The pause's copy. Product tone rather than an incidental string, so it lives as a constant
#: the copy test pins: this sentence is what the user reads when their agents have been
#: talking among themselves, and it has to read as an offer rather than as an error.
PAUSE_TITLE = "Your agents have been talking for a while."
PAUSE_BODY = (
    "{rounds} exchanges in {title} with no word from you. "
    "Read the thread and reply to keep it going, or archive it."
)

#: The ref every pause item carries, and the one a human message resolves on. One key, so
#: the item's lifecycle IS the room's and there is no second piece of bookkeeping to close.
PAUSE_REF = "room"

#: The name every round's task carries, so "is a round running for this room?" is a question
#: about a live task rather than about a flag something could forget to clear.
ROUND_TASK_PREFIX = "room-round:"


# ── the queue: pure decisions over (roster, text) ──────────────────────────


def queue_for_human(members: list[RoomMember], content: str) -> list[str]:
    """Who a HUMAN message enqueues, in FIFO order. Pure: ``(roster, text) -> names``.

    Mentioned members first in the order the human named them, then every remaining
    ``all``-policy member in roster order. Mentions lead because the human's sentence is the
    only ordering signal in the room that a human actually authored — if they asked the
    skeptic before the analyst, that is the order they asked in.

    A ``@``-named member is enqueued whatever its listen policy, which is the one thing only
    a human message can do: ``silent`` means "does not join the conversation on its own", not
    "cannot be addressed". A ``mention``-policy member the human did not name, and a
    ``silent`` one they did not name, are not enqueued.
    """
    roster = {m.name: m for m in members}
    queued: list[str] = []
    for name in mentions_in_order(content):
        if name in roster and name not in queued:
            queued.append(name)
    for member in members:
        if member.listen_policy == "all" and member.name not in queued:
            queued.append(member.name)
    return queued


def queue_for_member(members: list[RoomMember], speaker: str, content: str) -> list[str]:
    """Who a MEMBER's reply enqueues, in FIFO order. Pure: ``(roster, speaker, text) -> names``.

    Only the ``mention``-policy members that reply ``@``-named, in the order named. Three
    exclusions, each of them a bound on how far one message can propagate:

    * An ``all``-policy member is never enqueued by a peer. It is fed every message anyway,
      so summoning it adds no information — and it is the least-restricted policy, which is
      the worst one to let the room amplify through.
    * A ``silent`` member is never enqueued by a peer. Its whole definition is that it speaks
      when its human asks, and a peer is not its human.
    * A member never enqueues itself, so a reply that names its own author is not a
      self-sustaining loop that the budget would have to absorb.
    """
    roster = {m.name: m for m in members}
    queued: list[str] = []
    for name in mentions_in_order(content):
        if name == speaker or name in queued:
            continue
        member = roster.get(name)
        if member is not None and member.listen_policy == "mention":
            queued.append(name)
    return queued


def resume_queue(carryover: list[str], members: list[RoomMember], content: str) -> list[str]:
    """The full FIFO queue for a human message: what the room still OWES, then what it asks.

    Pure: ``(carryover, roster, text) -> names``. ``carryover`` is the room's queue as it stands
    — parked by a pause, or still draining in a running round — and it comes **first** because
    FIFO does not stop being FIFO across a message: those members were enqueued before the
    human's new sentence was written, so they are ahead of it in the queue. A resume that dropped
    them would make the budget silently destructive — the user's reply would cancel the turns
    they were replying in order to allow.

    Two filters, both of which the drain loop would otherwise have to guess at:

    * **De-duplicated against the carry-over.** A human who replies ``@skeptic`` to a room that
      already owed skeptic a turn gets one turn, keeping its earlier position — the same
      "naming somebody twice is emphasis" rule :func:`queue_for_human` applies within one
      message, extended across the pause. (The member whose turn is OPEN is not in the
      carry-over: that turn was built before this message existed, so it is owed another.)
    * **Filtered to the current roster.** A member removed while the room was paused does not
      speak. The roster is the human's to change, and a name parked before they changed it is
      not a standing claim on a seat they took away.
    """
    roster = {m.name for m in members}
    queued = [name for name in carryover if name in roster]
    for name in queue_for_human(members, content):
        if name not in queued:
            queued.append(name)
    return queued


def queue_human_turns(room_id: str, content: str) -> list[str]:
    """Queue what a human message asks for behind what the room already owes. Returns the queue.

    Persisted before the caller starts (or extends) the round, so the queue the route answers
    with is on disk whether or not the round that drains it survives — which is the difference
    between a restart that interrupts a round and one that silently loses it.
    """
    room = require_room(room_id)
    queue = resume_queue(room.pending_queue, room.members, content)
    return set_pending_queue(room_id, queue).pending_queue


# ── the budget's two edges ─────────────────────────────────────────────────


def note_human_message(state: Any, room_id: str) -> None:
    """The human spoke: refill the budget and close any standing pause item.

    Called synchronously from the route, BEFORE the round is handed to the background, so the
    reset is durable with the 201 that acknowledged the message. A reset that only happened
    inside the round would be skipped exactly when the round is dropped — leaving a paused
    room that the human had in fact already answered.

    Resolving on the room ref alone closes the pause row and nothing else, because the pause
    is the only item that stamps it.
    """
    from personalclaw.inbox import resolve_attention_items

    reset_round_budget(room_id)
    resolve_attention_items(state, {PAUSE_REF: room_id})


def _pause(state: Any, room_id: str, rounds: int, title: str) -> None:
    """Pause the room and raise exactly ONE attention item for it.

    Through :func:`~personalclaw.inbox.emit_attention_item` rather than an inbox write plus a
    ``notify`` — that helper exists because the two drift apart otherwise, and a room that
    paused without telling anybody is a room that has silently stopped. ``dedup_key`` makes
    re-pausing the same room idempotent, so a second pause of the same room does not stack a
    second row.

    The queue the user is being asked about is already durable when they are told about it: it
    is the room's own ``pending_queue``, which a pause leaves exactly where it stands.

    **The item carries no member text.** Only the room's own human-authored title and a count,
    so the one surface that escapes the transcript's fence cannot carry a member's words to
    the user as though they were the system's.
    """
    from personalclaw.inbox import emit_attention_item

    pause_room(room_id)
    emit_attention_item(
        state,
        source=AGENT,
        kind=PAUSE_KIND,
        title=PAUSE_TITLE,
        body=PAUSE_BODY.format(rounds=rounds, title=title),
        refs={PAUSE_REF: room_id},
        dedup_key=f"{PAUSE_KIND}:{room_id}",
    )


# ── the round ──────────────────────────────────────────────────────────────


def _round_task_name(room_id: str) -> str:
    return f"{ROUND_TASK_PREFIX}{room_id}"


def round_running(state: Any, room_id: str) -> bool:
    """Whether this gateway is running a round for *room_id* right now.

    Answered from the LIVE TASK, never from the record, and that is the point: the record says
    what the room owes, and only a running task can pay it. A room that owes turns while nothing
    is running them is *interrupted* — the process that was running them stopped — and it reads
    that way by construction, whatever killed the round: a restart, a crash, a bug in the drain.
    No flag has to be cleared on the way down for the room to stop claiming it is answering.
    """
    tasks = getattr(state, "_background_tasks", None) if state is not None else None
    if not tasks:
        return False
    name = _round_task_name(room_id)
    return any(task.get_name() == name and not task.done() for task in list(tasks))


def start_round(state: Any, sessions: "SessionManager", room_id: str) -> None:
    """Run the room's round in the background, unless one is already running.

    **At most one per room.** A second round would drain the same queue concurrently and put two
    members on one transcript at once; refusing it is safe because the running round re-reads the
    queue before every turn, so whatever the caller just queued is drained by it.

    ``state._background_tasks`` is the shipped set every other fire-and-forget handler parks its
    task in (``dashboard/side.py`` is the closest sibling); an un-referenced ``create_task`` is
    collectable mid-turn, which would make a member's reply vanish for reasons no log explains.
    The task's NAME is what :func:`round_running` looks for.
    """
    if round_running(state, room_id):
        return
    task = asyncio.create_task(
        drain_round(state, sessions, room_id), name=_round_task_name(room_id)
    )
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)


async def drain_round(state: Any, sessions: "SessionManager", room_id: str) -> list[str]:
    """Drain the room's queue one member at a time until it empties, pauses or is archived.

    Returns the members that actually contributed, in the order they spoke — which lets a
    caller tell "silent by policy" from "failed" from "never got a turn before the pause".

    **The room record is re-read every iteration**, because the queue and the budget live on
    disk and not in this frame: that is what lets a human message arriving mid-round extend the
    queue being drained, and a member removed mid-round lose its place.

    **A turn left open by a round that died is reopened first** (see
    :func:`~personalclaw.rooms.store.begin_turn`), so continuing an interrupted room starts with
    the member that was cut off.

    **One member's failure does not silence the room, and it is SAID.** The traceback is logged,
    the room writes who failed and why in that member's slot
    (:func:`~personalclaw.rooms.turn.note_failed_turn`), the turn is not charged, and the next
    member speaks — one broken binding must not look like a room where nobody had anything to
    say, and it must not look like that member agreed either.

    Anything else escaping the loop is logged at ERROR and ends this round, and the room then
    reads as interrupted — its queue and any open turn are still on disk — rather than as a round
    that silently stopped. A cancellation (the gateway stopping) is not caught at all: it ends the
    task, and the record left behind is exactly what the restarted gateway needs.
    """
    spoke: list[str] = []
    try:
        await _drain(state, sessions, room_id, spoke)
    except Exception:
        logger.error(
            "rooms: the round in room %s stopped on an unexpected error — the room keeps what it "
            "owes and reads as interrupted",
            room_id,
            exc_info=True,
        )
    return spoke


async def _drain(state: Any, sessions: "SessionManager", room_id: str, spoke: list[str]) -> None:
    while True:
        room = require_room(room_id)
        if room.archived:
            logger.info("rooms: room %s was archived — its round stops", room_id)
            return
        if room.paused:
            logger.info(
                "rooms: room %s is paused — %d member(s) stay owed a turn",
                room_id,
                len(room.owed()),
            )
            return
        if not room.speaking and not room.pending_queue:
            return
        budget = effective_round_budget(room)
        if room.rounds_used >= budget:
            logger.info(
                "rooms: room %s hit its round budget of %d — pausing with %d member(s) owed",
                room_id,
                budget,
                len(room.owed()),
            )
            _pause(state, room_id, room.rounds_used, room.title)
            return
        name = begin_turn(room_id)
        if not name:
            return
        if room.member(name) is None:
            # Removed while queued. Not an error: the roster is the human's to change while the
            # room is running, and a member they just removed must not get one last word.
            end_turn(room_id, spoke=False)
            logger.info("rooms: %s left room %s before its turn — skipped", name, room_id)
            continue
        try:
            reply = await run_member_turn(sessions, room_id, name)
        except Exception as exc:
            end_turn(room_id, spoke=False)
            now = require_room(room_id)
            if now.archived or now.member(name) is None:
                # The human archived the room or removed the member while it was answering, so
                # the turn ending is their decision rather than a failure to report.
                logger.info("rooms: %s's turn in room %s ended with the member gone", name, room_id)
                continue
            logger.warning(
                "rooms: member %s failed its turn in room %s — the room says so and the round "
                "continues",
                name,
                room_id,
                exc_info=True,
            )
            note_failed_turn(room_id, name, exc)
            continue
        summoned = queue_for_member(require_room(room_id).members, name, reply) if reply else []
        end_turn(room_id, spoke=bool(reply), summoned=summoned)
        if reply:
            spoke.append(name)
