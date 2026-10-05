"""What a restart or a sleep leaves for the user to decide: missed runs and interrupted runs.

Three passes find work that did not happen. `service.boot` enumerates the slots a stopped gateway
missed (`missed.review_at_boot`), `service.tick` the slots a gateway that was alive and asleep
missed (the same `service.recover`, on a wake), and `reaper.terminalize_orphans` closes the runs a
crash or a restart left with no ending. A stop closes the runs it cuts off itself, as it cuts them
(`reaper.record_stopped_run`, and `reaper.record_stopped_work` for the agent a run started), and
the next start announces them in its one notice. None is re-run on its own, deliberately: running
the 3am backup at 9am is sometimes right and sometimes exactly wrong, and a run a restart
interrupted may already have done part of its work, so running it again is the user's decision
("review, don't auto-run"). A card says which it was (`ReviewCard.cause`), because "while
PersonalClaw was not running" is false about a laptop whose lid was shut.

They used to end at a notification that said "Review them and choose what to run now" with
nothing to review. The boot re-arms every schedule, which destroys the evidence of what was
missed, and nothing kept the list, so `missed.resolve_missed` (the review's run-now and dismiss)
had no caller. This module keeps the list: ONE card per trigger and kind, until the user runs it
or dismisses it. A card, not a row per slot: a laptop opened after a weekend missed many slots of
one automation, and the decision is still one decision.

Stored beside `triggers.json` (the store's own root), never at the ambient home, for the reason
`reaper._write_row` gives: a card describing a fire from `<base_dir>/triggers.json` belongs beside
it.
"""

from __future__ import annotations

import fcntl
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from personalclaw import record_files
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

REVIEW_FILENAME = "trigger-review.json"
MISSED = "missed"
INTERRUPTED = "interrupted"
KINDS = (MISSED, INTERRUPTED)

#: Why a card's slots did not run. STOPPED: PersonalClaw was not running (the boot found them).
#: PAUSED: it was running and could not tick — the computer slept, or the process was stopped — so
#: a wake found them. A card that holds slots of both says so (STOPPED_OR_PAUSED).
STOPPED = "stopped"
PAUSED = "paused"
STOPPED_OR_PAUSED = "stopped_or_paused"
CAUSES = (STOPPED, PAUSED, STOPPED_OR_PAUSED)


@dataclass
class ReviewCard:
    """One decision the user owes: a trigger's missed slots, or a run a restart interrupted.

    `latest` is the slot a Run now stands in for (the newest missed slot, or when the interrupted
    run started) and `oldest` the first; `count` is how many slots the card covers, and
    `count_is_floor` says the enumeration stopped early so the number is "at least". `cause` is why
    the slots did not run (`CAUSES`); an interrupted run's is always a stop.

    `unannounced` marks a card kept by a stop for a run it cut off (`reaper.record_stopped_run`):
    the stop sends no notice, and the next start counts the card in its one notice
    (`take_unannounced`, `boot_notice`), once the gateway is back to deliver it.
    """

    trigger_id: str
    kind: str
    count: int
    latest: float
    oldest: float
    reason: str = ""
    count_is_floor: bool = False
    cause: str = STOPPED
    unannounced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewCard":
        cause = str(raw.get("cause") or STOPPED)
        return cls(
            trigger_id=str(raw.get("trigger_id") or ""),
            kind=str(raw.get("kind") or MISSED),
            count=int(raw.get("count") or 0),
            latest=float(raw.get("latest") or 0.0),
            oldest=float(raw.get("oldest") or 0.0),
            reason=str(raw.get("reason") or ""),
            count_is_floor=bool(raw.get("count_is_floor")),
            # A card kept before cards named their cause was a boot's: only a restart made cards.
            cause=cause if cause in CAUSES else STOPPED,
            unannounced=bool(raw.get("unannounced")),
        )


def _root(base_dir: Path | str | None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    from personalclaw.config.loader import config_dir

    return config_dir()


def _path(base_dir: Path | str | None) -> Path:
    return _root(base_dir) / REVIEW_FILENAME


@contextmanager
def _locked(base_dir: Path | str | None) -> Iterator[None]:
    """Cross-process lock on a sidecar file, so a boot and a click cannot lose each other's card."""
    from personalclaw.durability.home_paths import open_lock

    root = _root(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    with open_lock(root / f"{REVIEW_FILENAME}.lock") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


#: Where the file keeps its cards.
_CARDS = record_files.Shape(key="cards")


def _read(base_dir: Path | str | None, *, strict: bool = False) -> list[ReviewCard]:
    """The cards pending. A file that cannot be read lists none (the read keeps a copy of it and
    says so once); a write reads it *strict*, and is refused with ``record_files.Unreadable``
    rather than replace every card waiting in it with its own."""
    read = record_files.records if strict else record_files.records_or_empty
    return [ReviewCard.from_dict(c) for c in read(_path(base_dir), _CARDS) if isinstance(c, dict)]


def _write(cards: list[ReviewCard], base_dir: Path | str | None) -> None:
    payload = {"cards": [c.to_dict() for c in cards], "saved_at": time.time()}
    atomic_write(_path(base_dir), json.dumps(payload, indent=2) + "\n")


def _merge(cards: list[ReviewCard], new: ReviewCard) -> None:
    """Fold `new` into the card already held for its trigger and kind, or add it.

    A second restart before the user decided adds its slots to the same card rather than stacking
    a second one: the decision is still one decision. Whether a notice is still owed for it is the
    newer card's to say: a stop's card waits for the next start's notice, and a boot's is in the
    notice that boot sends.
    """
    for card in cards:
        if card.trigger_id == new.trigger_id and card.kind == new.kind:
            card.count += new.count
            card.latest = max(card.latest, new.latest)
            card.oldest = min(card.oldest, new.oldest) if card.oldest else new.oldest
            card.reason = new.reason or card.reason
            card.count_is_floor = card.count_is_floor or new.count_is_floor
            card.cause = card.cause if card.cause == new.cause else STOPPED_OR_PAUSED
            card.unannounced = new.unannounced
            return
    cards.append(new)


def catching_up(report: dict[str, Any]) -> set[str]:
    """The triggers this boot catches up on their own (`service.catch_up_at_boot`).

    Their decision is already made: the user opted in, and each fires once, staggered, without a
    card. A card offering Run now beside that fire would run the automation twice.
    """
    return {
        str(c.get("id") or "")
        for c in report.get("catch_up") or []
        if c.get("catching_up") and c.get("id")
    }


def catch_up_slots(report: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Each catch-up in a `service.recover` report: id → `(the slot it stands in for, its fire)`.

    The slot is the newest one the review counted for that trigger (what a Run now stands in for
    too, `ReviewCard.latest`), or the armed slot when the review counted none. The clock loop holds
    this across ticks (`service.tick`'s `catching_up`), so the staggered fire is recorded late
    against the slot it replaces.
    """
    newest: dict[str, float] = {}
    for row in (report.get("review") or {}).get("rows") or []:
        tid = str(row.get("trigger_id") or "")
        slot = float(row.get("scheduled_for") or 0.0)
        if tid and slot > newest.get(tid, 0.0):
            newest[tid] = slot
    out: dict[str, tuple[float, float]] = {}
    for c in report.get("catch_up") or []:
        tid = str(c.get("id") or "")
        fire_at = float(c.get("fire_at") or 0.0)
        if not (c.get("catching_up") and tid and fire_at > 0):
            continue
        slot = newest.get(tid) or float(c.get("slot") or 0.0)
        if slot > 0:
            out[tid] = (slot, fire_at)
    return out


def cards_from_boot(report: dict[str, Any]) -> list[ReviewCard]:
    """One MISSED card per trigger from a `service.recover` report: its rows plus its summary.

    A trigger that catches up on its own gets no card (`catching_up`). Each card carries the
    report's `cause`: a boot's report names none (PersonalClaw was stopped), a wake's says PAUSED.
    """
    skip = catching_up(report)
    cause = str(report.get("cause") or STOPPED)
    cards = [card for card in missed_by_trigger(report) if card.trigger_id not in skip]
    for card in cards:
        card.cause = cause
    return cards


def missed_by_trigger(report: dict[str, Any]) -> list[ReviewCard]:
    """Every trigger's missed slots in a `service.boot` report, as one card each.

    The one reading of the report: the cards the Triggers page keeps (`cards_from_boot`, which
    leaves out the triggers catching up on their own) and the notice that sends the owner there
    (`boot_notice`) both come from it, so the two cannot count the same boot differently.
    """
    review = report.get("review") or {}
    by_trigger: dict[str, ReviewCard] = {}
    floor = bool(review.get("truncated"))
    for row in review.get("rows") or []:
        tid = str(row.get("trigger_id") or "")
        slot = float(row.get("scheduled_for") or 0.0)
        if not tid or slot <= 0:
            continue
        card = by_trigger.setdefault(
            tid,
            ReviewCard(trigger_id=tid, kind=MISSED, count=0, latest=slot, oldest=slot),
        )
        card.count += 1
        card.latest = max(card.latest, slot)
        card.oldest = min(card.oldest, slot)
    for summary in review.get("summaries") or []:
        tid = str(summary.get("trigger_id") or "")
        count = int(summary.get("count") or 0)
        if not tid or count <= 0:
            continue
        oldest = float(summary.get("oldest") or 0.0)
        newest = float(summary.get("newest") or 0.0)
        card = by_trigger.setdefault(
            tid,
            ReviewCard(trigger_id=tid, kind=MISSED, count=0, latest=newest, oldest=oldest),
        )
        card.count += count
        card.oldest = min(card.oldest, oldest) if oldest else card.oldest
        card.latest = max(card.latest, newest)
    for card in by_trigger.values():
        card.count_is_floor = floor
    return sorted(by_trigger.values(), key=lambda c: c.trigger_id)


def cards_from_orphans(
    records: list[dict[str, Any]], *, now: float, unannounced: bool = False
) -> list[ReviewCard]:
    """One INTERRUPTED card per run the reaper closed as interrupted: by the boot's orphan pass
    (`reaper.terminalize_orphans`), or by a stop that cut it off (`reaper.record_stopped_run`,
    `reaper.record_stopped_work`), whose cards are *unannounced* until the next start."""
    cards: list[ReviewCard] = []
    for record in records or []:
        tid = str(record.get("trigger_id") or "")
        if not tid:
            continue
        started = now - float(record.get("elapsed") or 0.0)
        cards.append(
            ReviewCard(
                trigger_id=tid,
                kind=INTERRUPTED,
                count=1,
                latest=started,
                oldest=started,
                reason=str(record.get("reason") or ""),
                unannounced=unannounced,
            )
        )
    return cards


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


#: Why the slots did not run, as the notice says it. Each is true of its cause and only of it: a
#: laptop whose lid was shut was running PersonalClaw the whole time.
_WHILE: dict[str, str] = {
    STOPPED: "while PersonalClaw was not running",
    PAUSED: "while PersonalClaw was paused or the computer was asleep",
    STOPPED_OR_PAUSED: "while PersonalClaw was stopped, paused or asleep",
}


def boot_notice(report: dict[str, Any], cards: list[ReviewCard]) -> dict[str, Any] | None:
    """The ONE notice about what a boot or a wake found, or None when it found nothing.

    *cards* are the ones kept for the Triggers page (`cards_from_boot`, plus at a boot
    `cards_from_orphans` and the runs the stop before it cut off, `take_unannounced`), so the
    sentence that sends the owner there counts exactly what waits there. A trigger catching up on
    its own is counted as missed and named as firing by itself, and has no card. One notice
    naming the count, not one per slot: a laptop opened after a weekend would otherwise deliver
    hundreds. And none at all when nothing was missed: "0 automations missed a run" on every
    restart trains the owner to dismiss the one that matters. The report's `cause` says why they
    were missed (a wake's is PAUSED), in words true of it.
    """
    every = missed_by_trigger(report)
    missed = sum(card.count for card in every)
    cut_off = sum(1 for card in cards if card.kind == INTERRUPTED)
    if missed <= 0 and cut_off <= 0:
        return None
    cause = str(report.get("cause") or STOPPED)
    caught_up = len(catching_up(report))
    # What waits: each missed slot, and each interrupted run's card once. A card a second stop
    # cut its run off again before you decided counts both times, and it is still one decision.
    waiting = sum(card.count for card in cards if card.kind != INTERRUPTED) + cut_off
    said: list[str] = []
    if missed > 0:
        said.append(
            f"{missed} scheduled {_plural(missed, 'run was', 'runs were')} missed across "
            f"{len(every)} {_plural(len(every), 'automation', 'automations')} "
            f"{_WHILE.get(cause, _WHILE[STOPPED])}."
        )
    if caught_up:
        said.append(
            f"{caught_up} with catch-up enabled will fire once, staggered, on "
            f"{_plural(caught_up, 'its', 'their')} own."
        )
    if cut_off > 0:
        said.append(
            f"{cut_off} {_plural(cut_off, 'run was', 'runs were')} interrupted by the restart and "
            f"{_plural(cut_off, 'is', 'are')} not run again on {_plural(cut_off, 'its', 'their')} "
            "own."
        )
    if waiting > 0:
        which = "the others" if caught_up and waiting > cut_off else _plural(waiting, "it", "them")
        said.append(f"Review {which} on the Triggers page and choose what to run now.")
    return {
        "title": "Missed scheduled runs" if missed > 0 else "Runs interrupted by a restart",
        "body": " ".join(said),
        "meta": {
            "event": "automation.missed_review",
            "statusUrl": "#/triggers",
            "missed": missed,
            "interrupted": cut_off,
            "triggers": len(every),
            "caught_up": caught_up,
            "cause": cause,
            "truncated": bool((report.get("review") or {}).get("truncated")),
        },
    }


def record(cards: list[ReviewCard], *, base_dir: Path | str | None = None) -> list[ReviewCard]:
    """Add a boot's cards to the pending review. Returns every card now pending. Never raises.

    Never raises for the reason the boot passes do not: the schedule is already re-armed and the
    run already closed, and losing the card must not undo either. Losing it is the worse outcome
    for the user, so it is logged loudly.
    """
    try:
        with _locked(base_dir):
            held = _read(base_dir, strict=True)
            for card in cards:
                _merge(held, card)
            if cards:
                _write(held, base_dir)
            return held
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("could not record %d trigger review card(s)", len(cards), exc_info=True)
        return []


def pending(*, base_dir: Path | str | None = None) -> list[ReviewCard]:
    """Every card still waiting for a decision, oldest trigger id first."""
    return sorted(_read(base_dir), key=lambda c: (c.trigger_id, c.kind))


def take_unannounced(*, base_dir: Path | str | None = None) -> list[ReviewCard]:
    """The cards a stop kept that no notice has counted yet, now counted. Never raises.

    The start after a stop calls this once, for its one notice (`boot_notice`): each card is
    returned once and stays on the review, waiting for its decision, with no notice still owed.
    """
    try:
        with _locked(base_dir):
            held = _read(base_dir, strict=True)
            owed = [card for card in held if card.unannounced]
            if not owed:
                return []
            for card in owed:
                card.unannounced = False
            _write(held, base_dir)
            return sorted(owed, key=lambda c: (c.trigger_id, c.kind))
    except Exception:  # noqa: BLE001 - the cards still wait on the review; only the notice is lost
        logger.warning("could not read the runs a stop left for the next notice", exc_info=True)
        return []


def take(trigger_id: str, kind: str, *, base_dir: Path | str | None = None) -> ReviewCard | None:
    """Remove and return the card for `trigger_id`/`kind`, or None when there is none."""
    with _locked(base_dir):
        held = _read(base_dir, strict=True)
        found = next((c for c in held if c.trigger_id == trigger_id and c.kind == kind), None)
        if found is None:
            return None
        _write([c for c in held if c is not found], base_dir)
        return found


def forget(trigger_id: str, *, base_dir: Path | str | None = None) -> None:
    """Drop every card about a trigger that no longer exists. Never raises."""
    try:
        with _locked(base_dir):
            held = _read(base_dir, strict=True)
            kept = [c for c in held if c.trigger_id != trigger_id]
            if len(kept) != len(held):
                _write(kept, base_dir)
    except Exception:  # noqa: BLE001 - a card whose trigger is gone is also skipped when read
        logger.debug("could not drop review cards for %s", trigger_id, exc_info=True)
