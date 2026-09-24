"""Deterministic turn arbitration — the FIFO speaker queue and the round budget.

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
and it is admitted here only because :func:`run_round` now bounds it. Every agent turn since
the human's last message is charged to ``Room.rounds_used``; at ``rooms.round_budget`` (6 by
default — two full passes of a three-member room) the room stops draining, sets ``paused``,
and raises ONE inbox attention item. Any human message resets the counter and closes that
item, so the room's own pause has no separate resume button to go missing. The counter is
persisted, so two concurrent rounds over one room share one bound and a gateway restart
cannot launder it.

**A pause SUSPENDS the queue; it does not cancel it.** The members still owed a turn when the
ceiling was reached are parked on ``Room.pending_queue`` and speak first when the human
replies — :func:`resume_queue` is that rule, and it is pure. This is the difference between a
budget that protects a conversation and one that quietly truncates it: without the park, the
reply a user sends to let the deliberation continue would be the very act that discarded the
turns it was continuing, and the room would restart from that sentence while looking like it
had resumed. The park is drained exactly once, by
:func:`~personalclaw.rooms.store.take_pending`, so the debt is owed to one round rather than
re-inherited by every later one.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from personalclaw.notification_kinds import AGENT
from personalclaw.rooms.store import (
    RoomMember,
    charge_round,
    effective_round_budget,
    pause_room,
    require_room,
    reset_round_budget,
    take_pending,
)
from personalclaw.rooms.turn import mentions_in_order, run_member_turn

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

    Pure: ``(carryover, roster, text) -> names``. This is what makes a pause a suspension
    rather than a cancellation. ``carryover`` is the remainder parked by
    :func:`~personalclaw.rooms.store.pause_room`, and it comes **first** because FIFO does not
    stop being FIFO across a pause: those members were enqueued before the human's new
    sentence was written, so they are ahead of it in the queue. A resume that dropped them
    would make the budget silently destructive — the user's reply would cancel the turns they
    were replying in order to allow.

    Two filters, both of which the drain loop would otherwise have to guess at:

    * **De-duplicated against the carry-over.** A human who replies ``@skeptic`` to a room that
      already owed skeptic a turn gets one turn, keeping its earlier position — the same
      "naming somebody twice is emphasis" rule :func:`queue_for_human` applies within one
      message, extended across the pause.
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


def _pause(state: Any, room_id: str, rounds: int, title: str, pending: list[str]) -> None:
    """Pause the room, park *pending*, and raise exactly ONE attention item for it.

    Through :func:`~personalclaw.inbox.emit_attention_item` rather than an inbox write plus a
    ``notify`` — that helper exists because the two drift apart otherwise, and a room that
    paused without telling anybody is a room that has silently stopped. ``dedup_key`` makes
    re-pausing the same room idempotent, so a second round that also hits the ceiling does not
    stack a second row.

    *pending* is parked BEFORE the item is raised, so the queue the user is being asked about
    is already durable when they are told about it: a crash between the two would otherwise
    leave an inbox row inviting them to resume a conversation whose remainder no longer exists.

    **The item carries no member text.** Only the room's own human-authored title and a count,
    so the one surface that escapes the transcript's fence cannot carry a member's words to
    the user as though they were the system's.
    """
    from personalclaw.inbox import emit_attention_item

    pause_room(room_id, pending)
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


async def run_round(
    state: Any, sessions: "SessionManager", room_id: str, content: str
) -> list[str]:
    """Drain the speaker queue one member at a time until it empties or the budget runs out.

    Returns the members that actually contributed, in the order they spoke — which lets a
    caller tell "silent by policy" from "failed" from "never got a turn before the pause".

    **The room record is re-read every iteration**, because the budget lives on disk and not
    in this frame: that is what makes two concurrent rounds over one room share a single bound
    instead of each spending the full budget, and what lets a human message arriving mid-drain
    be seen.

    **The queue starts with whatever the room still owed.** :func:`resume_queue` puts the
    remainder parked by the last pause ahead of the members this message names, so replying to
    a paused room continues its conversation instead of starting a new one over the same
    transcript. The park is drained once, by
    :func:`~personalclaw.rooms.store.take_pending`, so two rounds cannot both inherit it.

    **One member's failure does not silence the room**, and it still costs a round. The
    traceback is logged and the next member speaks — one broken binding must not look like a
    room where nobody had anything to say — but the charge happens before the turn, so a
    member that fails every time cannot drain the queue for free.
    """
    pending: deque[str] = deque(
        resume_queue(take_pending(room_id), require_room(room_id).members, content)
    )
    spoke: list[str] = []
    while pending:
        room = require_room(room_id)
        if room.paused:
            logger.info(
                "rooms: room %s is paused — %d queued member(s) do not speak", room_id, len(pending)
            )
            pause_room(room_id, list(pending))
            break
        if room.rounds_used >= effective_round_budget(room):
            logger.info(
                "rooms: room %s hit its round budget of %d — pausing with %d member(s) queued",
                room_id,
                effective_round_budget(room),
                len(pending),
            )
            _pause(state, room_id, room.rounds_used, room.title, list(pending))
            break
        name = pending.popleft()
        if room.member(name) is None:
            # Removed mid-round. Not an error: the roster is the human's to change while the
            # room is running, and a member they just removed must not get one last word.
            logger.info("rooms: %s left room %s before its turn — skipped", name, room_id)
            continue
        charge_round(room_id)
        try:
            reply = await run_member_turn(sessions, room_id, name)
        except Exception:
            logger.warning(
                "rooms: member %s failed its turn in room %s — the round continues",
                name,
                room_id,
                exc_info=True,
            )
            continue
        if not reply:
            continue
        spoke.append(name)
        for summoned in queue_for_member(room.members, name, reply):
            if summoned not in pending:
                pending.append(summoned)
    return spoke
