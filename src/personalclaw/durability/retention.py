"""Tiered snapshot retention (DURABILITY-AND-SYNC §3).

`--keep N` keeps the N most recent snapshots, which on a nightly schedule means a
week of history and nothing older. That is the wrong shape for the failure it
protects against: corruption you notice immediately needs yesterday, and corruption
you notice in April needs January.

So retention is generalized to tiers — N daily, M weekly, Y monthly. One snapshot is
promoted per period (the newest in that ISO week / calendar month), and everything
else ages out. Roughly 20 files cover a year instead of 365.

Pure functions over timestamps, deliberately: retention decisions are the kind of
thing you want to unit-test exhaustively without creating a single tar file.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Defaults: a fortnight of dailies, two months of weeklies, a year of monthlies.
DEFAULT_DAILY = 14
DEFAULT_WEEKLY = 8
DEFAULT_MONTHLY = 12

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


def plan_retention(
    snapshots: list[Snapshot],
    *,
    daily: int = DEFAULT_DAILY,
    weekly: int = DEFAULT_WEEKLY,
    monthly: int = DEFAULT_MONTHLY,
) -> tuple[list[Snapshot], list[Snapshot]]:
    """Split snapshots into ``(keep, prune)``.

    Newest-first so a snapshot can satisfy the daily tier and then, once it ages
    past it, still be the one its week or month promotes. A file is kept if ANY tier
    wants it — tiers are unions, not slices.
    """
    keep: list[Snapshot] = []
    keep_ids: set[Path] = set()

    def _hold(snapshot: Snapshot) -> None:
        if snapshot.path not in keep_ids:
            keep_ids.add(snapshot.path)
            keep.append(snapshot)

    ordered = sorted(snapshots, key=lambda s: s.taken_at, reverse=True)

    # Daily tier: the newest snapshot of each of the last `daily` distinct days.
    seen_days: list[str] = []
    for snapshot in ordered:
        if snapshot.day in seen_days:
            continue
        if len(seen_days) >= max(0, daily):
            break
        seen_days.append(snapshot.day)
        _hold(snapshot)

    # Weekly + monthly: one promoted snapshot per period, newest wins.
    for attr, budget in (("week", weekly), ("month", monthly)):
        seen: list[str] = []
        for snapshot in ordered:
            period = getattr(snapshot, attr)
            if period in seen:
                continue
            if len(seen) >= max(0, budget):
                break
            seen.append(period)
            _hold(snapshot)

    prune = [s for s in ordered if s.path not in keep_ids]
    return keep, prune


def apply_retention(
    directory: Path,
    *,
    daily: int = DEFAULT_DAILY,
    weekly: int = DEFAULT_WEEKLY,
    monthly: int = DEFAULT_MONTHLY,
    dry_run: bool = False,
) -> dict:
    """Prune ``directory`` to the tier budgets. Returns what it did (or would do).

    ``dry_run`` reports the same plan without unlinking, so a caller can show the
    user exactly which files a real run would remove.
    """
    snapshots = list_snapshots(directory)
    keep, prune = plan_retention(snapshots, daily=daily, weekly=weekly, monthly=monthly)
    removed: list[str] = []
    freed = 0
    if not dry_run:
        for snapshot in prune:
            try:
                snapshot.path.unlink()
                removed.append(snapshot.name)
                freed += snapshot.size
            except OSError:
                logger.debug("retention: could not remove %s", snapshot.name, exc_info=True)
    else:
        removed = [s.name for s in prune]
        freed = sum(s.size for s in prune)
    return {
        "kept": [s.name for s in keep],
        "pruned": removed,
        "bytes_freed": freed,
        "dry_run": dry_run,
        "tiers": {"daily": daily, "weekly": weekly, "monthly": monthly},
    }
