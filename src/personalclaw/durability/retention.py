"""Tiered snapshot retention.

`--keep N` keeps the N most recent snapshots, which on a nightly schedule means a
week of history and nothing older. That is the wrong shape for the failure it
protects against: corruption you notice immediately needs yesterday, and corruption
you notice in April needs January.

So retention is generalized to tiers — N daily, M weekly, Y monthly. One snapshot is
promoted per period (the newest in that ISO week / calendar month), and everything
else ages out. Roughly 20 files cover a year instead of 365.

**No pass trades the newest VERIFIED snapshot for unverified ones.** A restore drill is the
only proof that a snapshot restores, and it runs on the newest one. One per day meant a
snapshot taken by hand before a risky change removed that day's earlier snapshot, the one the
drill had passed on, leaving a backup nobody had restored. So when a pass would remove the
newest snapshot a drill passed on while keeping a newer one, it keeps that snapshot as well:
at most one more than the budget, until a newer snapshot passes a drill. Every plan says which
one it held and why each other file goes, so a run reports what it removed, not a count.

Pure functions over timestamps, deliberately: retention decisions are the kind of
thing you want to unit-test exhaustively without creating a single tar file.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Defaults: a fortnight of dailies, two months of weeklies, a year of monthlies.
DEFAULT_DAILY = 14
DEFAULT_WEEKLY = 8
DEFAULT_MONTHLY = 12

# Why a pass removes a snapshot, in the words a run's report and the CLI both print. The tier
# reasons name the finest tier whose window holds the snapshot's period: inside it, the tier
# keeps a newer snapshot of that period instead.
SAME_DAY = "replaced by a newer snapshot from the same day"
SAME_WEEK = "replaced by a newer snapshot from the same week"
SAME_MONTH = "replaced by a newer snapshot from the same month"
AGED_OUT = "older than the retention settings keep"
NONE_KEPT = "every retention tier is set to 0"

#: Why the newest verified snapshot stays past the budget: the one sentence for it.
HELD = (
    "it is the newest snapshot a restore drill verified, and the newer ones are not verified "
    "yet. It stays until a newer one passes a drill"
)

# `personalclaw-snapshot-20260728T013000Z.tar.gz` — the format snapshot.py writes
# (`%Y%m%dT%H%M%SZ`). Verified against the real output rather than assumed; an
# earlier guess at `-%H%M%S` silently matched nothing, so retention kept everything.
_STAMP = re.compile(r"personalclaw-snapshot-(\d{8})T(\d{6})Z")


@dataclass(frozen=True)
class Snapshot:
    """One snapshot file with its parsed timestamp."""

    path: Path
    taken_at: datetime
    size: int = 0

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def day(self) -> str:
        return self.taken_at.strftime("%Y-%m-%d")

    @property
    def week(self) -> str:
        year, week, _ = self.taken_at.isocalendar()
        return f"{year}-W{week:02d}"

    @property
    def month(self) -> str:
        return self.taken_at.strftime("%Y-%m")


@dataclass(frozen=True)
class Plan:
    """What one pass keeps, what it removes, and why. ``keep`` and ``prune`` are newest first."""

    keep: list[Snapshot]
    prune: list[Snapshot]
    #: The newest verified snapshot, kept past the budget because every kept one after it is
    #: unverified (:data:`HELD`). It is in ``keep`` too.
    held: Snapshot | None = None
    #: Each pruned snapshot's name → why it goes.
    reasons: dict[str, str] = field(default_factory=dict)


def parse_stamp(path: Path) -> "datetime | None":
    """The timestamp encoded in a snapshot filename, or None.

    Read from the NAME rather than mtime because a copied or restored file carries a
    new mtime, and retention must reflect when state was captured, not when the file
    was last touched.
    """
    match = _STAMP.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def list_snapshots(directory: Path) -> list[Snapshot]:
    """Every parseable snapshot in ``directory``, newest first."""
    out: list[Snapshot] = []
    try:
        candidates = sorted(directory.glob("personalclaw-snapshot-*.tar.gz"))
    except OSError:
        return out
    for path in candidates:
        taken = parse_stamp(path)
        if taken is None:
            # An unparseable name is left strictly alone — retention only ever
            # deletes files it positively recognizes.
            logger.debug("retention: skipping unrecognized name %s", path.name)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        out.append(Snapshot(path=path, taken_at=taken, size=size))
    out.sort(key=lambda s: s.taken_at, reverse=True)
    return out


def _hold_verified(
    ordered: list[Snapshot], kept: set[Path], verified: str | None
) -> Snapshot | None:
    """The snapshot named ``verified`` when a pass would trade it for newer unverified ones.

    ``verified`` is the name of the newest snapshot a restore drill passed on. Every snapshot
    after it is unverified: the drill only ever checks the newest one, and a later pass would
    have named that one. A pass that keeps nothing newer trades it for nothing, so it is not
    held: with every tier at 0 the budget is to keep no snapshot at all.
    """
    if not verified:
        return None
    target = next((s for s in ordered if s.name == verified), None)
    if target is None or target.path in kept:
        return None
    if not any(s.taken_at > target.taken_at for s in ordered if s.path in kept):
        return None
    return target


def _plan(
    ordered: list[Snapshot], kept: set[Path], verified: str | None, why: Callable[[Snapshot], str]
) -> Plan:
    held = _hold_verified(ordered, kept, verified)
    if held is not None:
        kept = kept | {held.path}
    keep = [s for s in ordered if s.path in kept]
    prune = [s for s in ordered if s.path not in kept]
    return Plan(keep=keep, prune=prune, held=held, reasons={s.name: why(s) for s in prune})


def plan_retention(
    snapshots: list[Snapshot],
    *,
    verified: str | None,
    daily: int = DEFAULT_DAILY,
    weekly: int = DEFAULT_WEEKLY,
    monthly: int = DEFAULT_MONTHLY,
) -> Plan:
    """The tier plan for ``snapshots``, holding the newest verified one (:func:`_hold_verified`).

    Newest-first so a snapshot can satisfy the daily tier and then, once it ages
    past it, still be the one its week or month promotes. A file is kept if ANY tier
    wants it — tiers are unions, not slices.

    ``verified`` has no default on purpose: a caller that does not say which snapshot a drill
    passed on would get a plan that protects nothing, and would never notice.
    """
    kept: set[Path] = set()
    ordered = sorted(snapshots, key=lambda s: s.taken_at, reverse=True)
    # One period → the tier's window: the newest snapshot of each of the last `budget` periods.
    windows: dict[str, set[str]] = {}
    for attr, budget in (("day", daily), ("week", weekly), ("month", monthly)):
        seen: list[str] = []
        for snapshot in ordered:
            period = getattr(snapshot, attr)
            if period in seen:
                continue
            if len(seen) >= max(0, budget):
                break
            seen.append(period)
            kept.add(snapshot.path)
        windows[attr] = set(seen)

    def why(snapshot: Snapshot) -> str:
        if not any(windows.values()):
            return NONE_KEPT
        for attr, reason in (("day", SAME_DAY), ("week", SAME_WEEK), ("month", SAME_MONTH)):
            if getattr(snapshot, attr) in windows[attr]:
                return reason
        return AGED_OUT

    return _plan(ordered, kept, verified, why)


def plan_newest(snapshots: list[Snapshot], *, keep: int, verified: str | None) -> Plan:
    """``personalclaw snapshot --keep N``: the N newest by the time in each name, plus the hold."""
    ordered = sorted(snapshots, key=lambda s: s.taken_at, reverse=True)
    kept = {s.path for s in ordered[: max(0, keep)]}
    return _plan(ordered, kept, verified, lambda _s: f"older than the {keep} newest")


def remove(snapshots: list[Snapshot]) -> tuple[list[str], int]:
    """Delete ``snapshots`` with their manifest sidecars; return the names removed, bytes freed."""
    from personalclaw.durability.archive import sidecar_path

    removed: list[str] = []
    freed = 0
    for snapshot in snapshots:
        try:
            snapshot.path.unlink()
            # The manifest sidecar goes with its archive. An orphaned sidecar would
            # accumulate forever and — worse — could be re-read as the manifest of a
            # future snapshot that happened to reuse the name.
            sidecar_path(snapshot.path).unlink(missing_ok=True)
            removed.append(snapshot.name)
            freed += snapshot.size
        except OSError:
            logger.debug("retention: could not remove %s", snapshot.name, exc_info=True)
    return removed, freed


def apply_retention(
    directory: Path,
    *,
    verified: str | None,
    daily: int = DEFAULT_DAILY,
    weekly: int = DEFAULT_WEEKLY,
    monthly: int = DEFAULT_MONTHLY,
    dry_run: bool = False,
) -> dict:
    """Prune ``directory`` to the tier budgets. Returns what it did (or would do).

    ``dry_run`` reports the same plan without unlinking, so a caller can show the
    user exactly which files a real run would remove.
    """
    plan = plan_retention(
        list_snapshots(directory), verified=verified, daily=daily, weekly=weekly, monthly=monthly
    )
    if dry_run:
        removed, freed = [s.name for s in plan.prune], sum(s.size for s in plan.prune)
    else:
        removed, freed = remove(plan.prune)
    return {
        "kept": [s.name for s in plan.keep],
        "pruned": removed,
        "held": plan.held.name if plan.held else "",
        "reasons": {name: plan.reasons[name] for name in removed},
        "bytes_freed": freed,
        "dry_run": dry_run,
        "tiers": {"daily": daily, "weekly": weekly, "monthly": monthly},
    }
