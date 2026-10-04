"""What the next digest carries: the proposals you have not answered yet.

A digest numbers ONE window, and its card is answerable until the next digest replaces it. A
proposal nobody answered used to vanish with its card: the next digest collected only what was
newer than the last one, so what was still waiting on you was never offered again, and a quiet
morning replaced the card with an empty one.

The next digest now carries every proposal the previous one still had waiting:

* **What is carried** is what the previous card still showed waiting on you
  (:func:`waiting_from`, over `surface.build_digest_view`'s pending rows that carry no answer): its
  own proposals and the ones it had carried itself. One you answered is not carried, whatever the
  answer did, and neither is one the digest ran on its own.
* **Only while it still applies** (:func:`recheck`). Each is looked up as its lane reads it now
  (`collect.current_item`): an Inbox message archived, dismissed or answered elsewhere, a
  conversation that was answered, a run resumed or removed, has been dealt with, and its proposal
  drops out with no question. A lane that cannot be read here keeps it as it was recorded: not
  knowing is not "gone".
* **One proposal per item.** When the new window collected the item again (something new happened
  to it), the fresh look decides (:func:`place`): a fresh proposal replaces the carried one, an
  item your rules now filter takes it with it, and an item kept with no fresh proposal keeps the
  carried one under its new number.
* **For a week** (:data:`CARRY_DAYS`). A proposal is carried for up to that many days from the
  digest that first made it, counted in whole days so a schedule that fires a few seconds early
  still counts the seventh day. The digest that would carry it longer drops it and says so,
  naming it (:func:`dropped_note`), and every digest that carries one says how long it comes back
  for (:data:`CARRY_RULE`).

A carried proposal is never run on its own again: it was offered to you, and it waits for your
answer. It is not shown to the model either: the model is asked only about what is new.

Pure: nothing here reads a store or the clock. The provider reads the previous digest and the
live items; the pipeline places what is carried into the new digest.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from personalclaw.proactive.gate import GateResult
from personalclaw.proactive.manifest import (
    COLLECT_SOURCES,
    MATERIALITY_RESPONSE,
    CollectedItem,
    Manifest,
)
from personalclaw.proactive.proposals import Proposal

#: How long a proposal you have not answered stays in view, in days from the digest that first
#: proposed it. A week: a weekday schedule carries one over a weekend, and a proposal about a
#: message older than that is about a conversation that has moved on.
CARRY_DAYS = 7

#: Why a proposal the previous digest had waiting is not carried. A closed vocabulary, so a
#: reader of the run journal counts them.
#:
#: Its item no longer waits on you: dealt with elsewhere, or gone.
DROPPED_HANDLED = "handled"
#: The new window collected its item again, and the fresh look proposed for it.
DROPPED_SUPERSEDED = "superseded"
#: The new window collected its item again, and your rules filtered it.
DROPPED_FILTERED = "filtered"
#: It waited :data:`CARRY_DAYS` days without an answer. The only reason the digest names.
DROPPED_EXPIRED = "waited_too_long"

#: What the digest says once wherever it carries a proposal or drops one for its age.
CARRY_RULE = (
    f"A proposal you have not answered comes back in each digest for up to {CARRY_DAYS} days."
)


@dataclass(frozen=True)
class Waiting:
    """A proposal the previous digest's card still showed waiting on you, and its item.

    ``proposal.item_id`` and ``item.ordinal`` are its number in the digest it was read from, which
    names nothing in the next one: :func:`place` numbers it again. ``item`` is as its lane reads it
    now once :func:`recheck` found it, and as the previous digest recorded it before that.
    """

    proposal: Proposal
    item: CollectedItem
    #: When the digest that FIRST proposed it ran (a UTC instant), however many digests carried it.
    first_proposed_at: str
    first_run_id: str
    #: Whether its tier was raised from the one the model asked (shown as "raised" on its badge).
    clamped: bool = False


@dataclass(frozen=True)
class Carried:
    """A proposal this digest carries, under its number in THIS digest."""

    proposal: Proposal
    item: CollectedItem
    first_proposed_at: str
    first_run_id: str
    clamped: bool = False


@dataclass(frozen=True)
class Dropped:
    """A proposal the previous digest had waiting that this one does not offer, and why."""

    waiting: Waiting
    reason: str


@dataclass(frozen=True)
class CarryResult:
    """What the digest carried, under the numbers it gave them, and what it dropped."""

    carried: tuple[Carried, ...] = ()
    dropped: tuple[Dropped, ...] = ()
    #: The carried items numbered after the window's own, in ordinal order. An item the window
    #: collected again keeps its number there and is not here.
    items: tuple[CollectedItem, ...] = ()

    @property
    def expired(self) -> tuple[Dropped, ...]:
        """The drops the digest names: proposals that waited too long."""
        return tuple(d for d in self.dropped if d.reason == DROPPED_EXPIRED)

    def numbered(self, window: Manifest) -> Manifest:
        """The digest's whole id space: the window's items, then the carried ones after them."""
        return Manifest(
            items=window.items + self.items,
            window_start=window.window_start,
            duplicates=window.duplicates,
        )


def _text(row: Mapping, key: str) -> str:
    return str(row.get(key, "") or "")


def waiting_from(view: Mapping) -> tuple[Waiting, ...]:
    """The proposals a digest's card still shows waiting on you: its pending rows with no answer.

    *view* is `surface.build_digest_view`'s card. A proposal the card shows as carried keeps when
    and in which digest it was first proposed; one the digest made itself was first proposed when
    that digest ran. A row whose stamp is missing takes the digest's own, so its age is never
    unknown and it cannot be carried for ever.
    """
    from personalclaw.proactive.surface import STATE_READY

    if view.get("state") != STATE_READY:
        return ()
    own_run = _text(view, "run_id")
    out: list[Waiting] = []
    for row in view.get("pending") or []:
        if not isinstance(row, Mapping) or row.get("answered"):
            continue
        ordinal = _text(row, "ordinal")
        source_id = _text(row, "source_id")
        action_type = _text(row, "action_type")
        if not ordinal or not source_id or not action_type:
            # Nothing a later digest could put back in front of you: no item, or nothing to do.
            continue
        config = row.get("action_config")
        out.append(
            Waiting(
                proposal=Proposal(
                    item_id=ordinal,
                    action_type=action_type,
                    tier=_text(row, "tier"),
                    action_config=dict(config) if isinstance(config, Mapping) else {},
                    pattern_key=_text(row, "pattern_key"),
                ),
                item=CollectedItem(
                    source=_text(row, "source"),
                    source_id=source_id,
                    title=_text(row, "title"),
                    permalink=_text(row, "item_permalink"),
                    materiality=_text(row, "materiality") or MATERIALITY_RESPONSE,
                    ordinal=ordinal,
                ),
                first_proposed_at=_text(row, "first_proposed_at") or _text(view, "proposed_at"),
                first_run_id=_text(row, "first_run_id") or own_run,
                clamped=bool(row.get("clamped")),
            )
        )
    return tuple(out)


#: ``(source, source_id) -> item``: the item as its lane reads it now, None when it no longer waits
#: on you; raises `collect.LaneUnreadable` when its lane cannot be read.
LookFn = Callable[[str, str], "CollectedItem | None"]


def recheck(
    waiting: Sequence[Waiting], *, look: LookFn
) -> tuple[tuple[Waiting, ...], tuple[Waiting, ...]]:
    """``(still waiting, dealt with)``: each proposal checked against its item as it is now.

    A proposal whose item no longer waits on you, or now reads as a different outcome (a failed
    run that was resumed and finished), has been dealt with. One whose lane cannot be read stays,
    as it was recorded: the answer that acts on it checks its item again when it runs.
    """
    from personalclaw.proactive.collect import LaneUnreadable

    still: list[Waiting] = []
    handled: list[Waiting] = []
    for entry in waiting:
        try:
            item = look(entry.item.source, entry.item.source_id)
        except LaneUnreadable:
            still.append(entry)
            continue
        if item is None or item.materiality != entry.item.materiality:
            handled.append(entry)
            continue
        still.append(replace(entry, item=replace(item, ordinal=entry.item.ordinal)))
    return tuple(still), tuple(handled)


def _instant(stamp: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def waited_days(first_proposed_at: str, now: datetime) -> int | None:
    """Whole days since *first_proposed_at*, rounded half up, or None when it cannot be read.

    Rounded rather than floored: a digest scheduled for the same minute every day starts a few
    seconds either side of it, and a week on it has waited "7 days" whichever side it fell.
    """
    first = _instant(first_proposed_at)
    if first is None:
        return None
    seconds = max(0.0, (now - first).total_seconds())
    return int(seconds / 86400 + 0.5)


def _age(first_proposed_at: str, now: datetime | None) -> str:
    """How long ago it was first proposed, in the words a digest uses, or ``""``.

    In days from 20 hours on, rounded as :func:`waited_days` rounds them, so yesterday's digest is
    "1 day" ago even when today's started a few seconds earlier than it did.
    """
    first = _instant(first_proposed_at)
    if first is None or now is None:
        return ""
    seconds = max(0.0, (now - first).total_seconds())
    if seconds >= 20 * 3600:
        days = max(1, int(seconds / 86400 + 0.5))
        return "1 day" if days == 1 else f"{days} days"
    hours = int(seconds // 3600)
    if hours >= 2:
        return f"{hours} hours"
    if hours == 1:
        return "1 hour"
    return "under an hour"


def carried_note(first_proposed_at: str, now: datetime | None) -> str:
    """The line a carried proposal carries: that it is carried over, and how long it has waited."""
    age = _age(first_proposed_at, now)
    if not age:
        return "Carried over from an earlier digest, and not answered yet."
    return f"Carried over: proposed {age} ago, and not answered yet."


def dropped_note(first_proposed_at: str, now: datetime | None) -> str:
    """The line a proposal dropped for its age carries: why it is gone, and what is left to do.

    Said as when it was proposed, which is true whenever it is read; a span it "waited" would be
    the day it was dropped, and the card is read later.
    """
    age = _age(first_proposed_at, now)
    when = f"proposed {age} ago" if age else f"proposed more than {CARRY_DAYS} days ago"
    return f"Not offered again: {when}, and never answered. Open the item to act on it."


def _order(entry: Waiting) -> tuple[str, int, int, str]:
    """Oldest first, then as the digest they came from numbered them."""
    source = entry.item.source
    ordinal = entry.item.ordinal
    return (
        entry.first_proposed_at,
        int(ordinal) if ordinal.isdigit() else 0,
        COLLECT_SOURCES.index(source) if source in COLLECT_SOURCES else len(COLLECT_SOURCES),
        entry.item.source_id,
    )


def place(
    waiting: Sequence[Waiting],
    *,
    handled: Sequence[Waiting] = (),
    window: Manifest,
    gate: GateResult,
    proposals: Sequence[Proposal],
    now: datetime,
) -> CarryResult:
    """Place what is still waiting into the new digest, numbered after its window.

    *window* is the new digest's manifest, *gate* what its gate made of it and *proposals* every
    proposal the fresh look made (whether it then ran on its own or not). *handled* are the
    proposals :func:`recheck` found dealt with, recorded as dropped. Each other one is, in order:
    superseded by a fresh proposal for its item, filtered with its item by your rules, dropped
    for its age, carried under the number the window gave its item, or carried under the next
    number after the window's.
    """
    fresh = {item.fingerprint: item for item in window.items}
    proposed = {p.item_id for p in proposals}
    filtered = {item.ordinal for item in gate.dropped}
    dropped: list[Dropped] = [Dropped(waiting=w, reason=DROPPED_HANDLED) for w in handled]
    carried: list[Carried] = []
    numbered: list[CollectedItem] = []
    seen: set[str] = set()
    for entry in sorted(waiting, key=_order):
        fingerprint = entry.item.fingerprint
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        again = fresh.get(fingerprint)
        if again is not None and again.ordinal in proposed:
            dropped.append(Dropped(waiting=entry, reason=DROPPED_SUPERSEDED))
            continue
        if again is not None and again.ordinal in filtered:
            dropped.append(Dropped(waiting=entry, reason=DROPPED_FILTERED))
            continue
        days = waited_days(entry.first_proposed_at, now)
        if days is None or days >= CARRY_DAYS:
            dropped.append(Dropped(waiting=entry, reason=DROPPED_EXPIRED))
            continue
        if again is not None:
            item = again
        else:
            item = replace(entry.item, ordinal=str(len(window) + len(numbered) + 1))
            numbered.append(item)
        carried.append(
            Carried(
                proposal=replace(entry.proposal, item_id=item.ordinal),
                item=item,
                first_proposed_at=entry.first_proposed_at,
                first_run_id=entry.first_run_id,
                clamped=entry.clamped,
            )
        )
    carried.sort(key=lambda c: int(c.item.ordinal) if c.item.ordinal.isdigit() else 0)
    return CarryResult(carried=tuple(carried), dropped=tuple(dropped), items=tuple(numbered))


__all__ = [
    "CARRY_DAYS",
    "CARRY_RULE",
    "DROPPED_EXPIRED",
    "DROPPED_FILTERED",
    "DROPPED_HANDLED",
    "DROPPED_SUPERSEDED",
    "Carried",
    "CarryResult",
    "Dropped",
    "LookFn",
    "Waiting",
    "carried_note",
    "dropped_note",
    "place",
    "recheck",
    "waited_days",
    "waiting_from",
]
