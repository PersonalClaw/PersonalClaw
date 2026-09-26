"""What a restart leaves for the user to decide: missed runs and interrupted runs (§3.4).

Two passes at boot find work that did not happen. `service.boot` enumerates the slots a stopped
gateway missed (`missed.review_at_boot`), and `reaper.terminalize_orphans` closes the runs a
restart cut off. Neither is re-run on its own, deliberately: running the 3am backup at 9am is
sometimes right and sometimes exactly wrong, and a run a restart interrupted may already have
done part of its work, so running it again is the user's decision (§3.4 "review, don't
auto-run").

They used to end at a notification that said "Review them and choose what to run now" with
nothing to review. The boot re-arms every schedule, which destroys the evidence of what was
missed, and nothing kept the list, so `missed.resolve_missed` (the review's run-now and dismiss)
had no caller. This module keeps the list: ONE card per trigger and kind, until the user runs it
or dismisses it. A card, not a row per slot: a laptop opened after a weekend missed many slots of
one automation, and the decision is still one decision.

Stored beside `triggers.json` (the store's own root), never at the ambient home, for the reason
`reaper._write_interrupted_row` gives: a card describing a fire from `<base_dir>/triggers.json`
belongs beside it.
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

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

REVIEW_FILENAME = "trigger-review.json"
MISSED = "missed"
INTERRUPTED = "interrupted"
KINDS = (MISSED, INTERRUPTED)


@dataclass
class ReviewCard:
    """One decision the user owes: a trigger's missed slots, or a run a restart interrupted.

    `latest` is the slot a Run now stands in for (the newest missed slot, or when the interrupted
    run started) and `oldest` the first; `count` is how many slots the card covers, and
    `count_is_floor` says the enumeration stopped early so the number is "at least".
    """

    trigger_id: str
    kind: str
    count: int
    latest: float
    oldest: float
    reason: str = ""
    count_is_floor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewCard":
        return cls(
            trigger_id=str(raw.get("trigger_id") or ""),
            kind=str(raw.get("kind") or MISSED),
            count=int(raw.get("count") or 0),
            latest=float(raw.get("latest") or 0.0),
            oldest=float(raw.get("oldest") or 0.0),
            reason=str(raw.get("reason") or ""),
            count_is_floor=bool(raw.get("count_is_floor")),
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
    root = _root(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / f"{REVIEW_FILENAME}.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _read(base_dir: Path | str | None) -> list[ReviewCard]:
    path = _path(base_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("trigger review %s is unreadable; starting it empty", path)
        return []
    cards = raw.get("cards") if isinstance(raw, dict) else None
    return [ReviewCard.from_dict(c) for c in cards or [] if isinstance(c, dict)]


def _write(cards: list[ReviewCard], base_dir: Path | str | None) -> None:
    payload = {"cards": [c.to_dict() for c in cards], "saved_at": time.time()}
    atomic_write(_path(base_dir), json.dumps(payload, indent=2) + "\n")


def _merge(cards: list[ReviewCard], new: ReviewCard) -> None:
    """Fold `new` into the card already held for its trigger and kind, or add it.

    A second restart before the user decided adds its slots to the same card rather than stacking
    a second one: the decision is still one decision.
    """
    for card in cards:
        if card.trigger_id == new.trigger_id and card.kind == new.kind:
            card.count += new.count
            card.latest = max(card.latest, new.latest)
            card.oldest = min(card.oldest, new.oldest) if card.oldest else new.oldest
            card.reason = new.reason or card.reason
            card.count_is_floor = card.count_is_floor or new.count_is_floor
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


def cards_from_boot(report: dict[str, Any]) -> list[ReviewCard]:
    """One MISSED card per trigger from a `service.boot` report: its review rows plus its summary.

    A trigger the boot catches up on its own gets no card (`catching_up`).
    """
    review = report.get("review") or {}
    skip = catching_up(report)
    by_trigger: dict[str, ReviewCard] = {}
    floor = bool(review.get("truncated"))
    for row in review.get("rows") or []:
        tid = str(row.get("trigger_id") or "")
        slot = float(row.get("scheduled_for") or 0.0)
        if not tid or slot <= 0 or tid in skip:
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
        if not tid or count <= 0 or tid in skip:
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


def cards_from_orphans(records: list[dict[str, Any]], *, now: float) -> list[ReviewCard]:
    """One INTERRUPTED card per run `reaper.terminalize_orphans` closed."""
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
            )
        )
    return cards


def record(cards: list[ReviewCard], *, base_dir: Path | str | None = None) -> list[ReviewCard]:
    """Add a boot's cards to the pending review. Returns every card now pending. Never raises.

    Never raises for the reason the boot passes do not: the schedule is already re-armed and the
    run already closed, and losing the card must not undo either. Losing it is the worse outcome
    for the user, so it is logged loudly.
    """
    try:
        with _locked(base_dir):
            held = _read(base_dir)
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


def take(trigger_id: str, kind: str, *, base_dir: Path | str | None = None) -> ReviewCard | None:
    """Remove and return the card for `trigger_id`/`kind`, or None when there is none."""
    with _locked(base_dir):
        held = _read(base_dir)
        found = next((c for c in held if c.trigger_id == trigger_id and c.kind == kind), None)
        if found is None:
            return None
        _write([c for c in held if c is not found], base_dir)
        return found


def forget(trigger_id: str, *, base_dir: Path | str | None = None) -> None:
    """Drop every card about a trigger that no longer exists. Never raises."""
    try:
        with _locked(base_dir):
            held = _read(base_dir)
            kept = [c for c in held if c.trigger_id != trigger_id]
            if len(kept) != len(held):
                _write(kept, base_dir)
    except Exception:  # noqa: BLE001 - a card whose trigger is gone is also skipped when read
        logger.debug("could not drop review cards for %s", trigger_id, exc_info=True)
