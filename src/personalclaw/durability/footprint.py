"""Per-store disk footprint: measure it, watch it grow, and reclaim what deletion left behind.

RETENTION-DURABILITY (RET-3). Three separate holes, and the third is the one that makes the
other two worth having:

* **Nothing reported the footprint.** Nothing anywhere printed bytes-on-disk per store, so
  "PersonalClaw is using 4 GB" was a thing a user could only discover with ``du``. The manifest
  in :mod:`personalclaw.durability.inventory` already declares every store that matters — this
  module reads sizes off it rather than inventing a second, drifting list.

* **A single size row cannot answer the question anyone actually has.** "12 GB" is not
  actionable; "12 GB, growing 400 MB/day, and it is ``workflows/runs.db``" is. So a size read is
  *recorded*, and the reported rate is derived from two samples taken at different times. One
  sample deliberately reports NO rate rather than a fabricated zero.

* **Deleting rows does not shrink a file.** SQLite marks pages free and reuses them later; the
  file never gets smaller on its own. So the run-retention pruner could delete every expired run
  and the disk would not move a byte. Reclaim therefore runs FTS5 ``'optimize'`` (which compacts
  index segments the deletes left behind), ``PRAGMA optimize``, and then ``VACUUM`` — VACUUM
  last, because it is the one that rewrites the file and hands the pages back to the filesystem.

Everything here is guarded to a fault: this runs on a background maintenance tick, and a
footprint probe that can crash the gateway or wedge a store is worse than no probe. A locked
database is *skipped and logged*, never raised — the next cadence gets it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.durability import inventory

logger = logging.getLogger(__name__)

#: State file, in the home beside the stores it describes. Its OWN file rather than a corner of
#: `durability_state.json`: that one is read-modify-written by `run_due_jobs`, and two writers on
#: one document is a lost update waiting for the day the two stop running in the same order.
_STATE_FILENAME = "footprint.json"

#: How many samples the series keeps. 60 daily samples is two months of trend for ~6 KB, and the
#: growth rate reads the ends of the window, so a longer series buys a steadier number.
SAMPLE_KEEP = 60

#: How often the maintenance tick reclaims. Daily: VACUUM rewrites the whole file, so it is
#: cheap relative to a day and absurd relative to the 5-minute tick that hosts it.
RECLAIM_SECS = 24 * 60 * 60

#: A SQLite sidecar that counts toward a store's real footprint. A 200 MB `-wal` is 200 MB of
#: disk, and a size row that ignored it would under-report exactly the store that is growing.
_SQLITE_SIDECARS = ("-wal", "-shm")

#: Seconds to wait for a lock before giving up on one store. Short on purpose: the gateway is
#: serving requests, and a maintenance job that blocks a store for a minute is the failure that
#: makes background work feel like a hang.
_BUSY_TIMEOUT_SECS = 5.0


# ── measuring ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoreFootprint:
    """One declared store's bytes on disk."""

    id: str
    path: str  # home-relative, as the manifest declares it
    domain: str
    kind: str
    bytes: int
    present: bool
    derived: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "domain": self.domain,
            "kind": self.kind,
            "bytes": self.bytes,
            "present": self.present,
            "derived": self.derived,
        }


@dataclass(frozen=True)
class Sample:
    """A footprint reading at one instant: the total plus the per-store breakdown."""

    at: float
    total_bytes: int
    stores: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"at": self.at, "total_bytes": self.total_bytes, "stores": dict(self.stores)}

    @staticmethod
    def from_dict(raw: dict) -> "Sample | None":
        try:
            at = float(raw["at"])
            total = int(raw["total_bytes"])
        except (KeyError, TypeError, ValueError):
            return None
        stores_raw = raw.get("stores")
        stores: dict[str, int] = {}
        if isinstance(stores_raw, dict):
            for key, value in stores_raw.items():
                try:
                    stores[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
        return Sample(at=at, total_bytes=total, stores=stores)


def _sqlite_paths(target: Path) -> tuple[Path, ...]:
    """A database and its sidecars, as paths."""
    return (target, *(Path(str(target) + suffix) for suffix in _SQLITE_SIDECARS))


def nested_entries(entry: inventory.StateEntry) -> tuple[inventory.StateEntry, ...]:
    """Manifest entries declared INSIDE *entry*.

    Five of them exist and they are the reason this function does: `workspace/` contains
    `knowledge.db`, `knowledge/files/` and `lexicon.db`; `workflows/` contains `runs.db`;
    `loop/` contains `loops.db`. Summing every entry naively therefore counted those bytes
    twice — the total over-reported and the container row claimed its child's growth, which is
    the one thing a "which store is growing?" report must never get wrong.
    """
    prefix = entry.path.rstrip("/") + "/"
    return tuple(
        other
        for other in inventory.all_entries()
        if other.path != entry.path and (other.path + "/").startswith(prefix)
    )


def _claimed_by_children(home: Path, entry: inventory.StateEntry) -> tuple[set[Path], set[Path]]:
    """(files, directories) inside *entry* that a nested entry bills separately."""
    files: set[Path] = set()
    dirs: set[Path] = set()
    for child in nested_entries(entry):
        target = home / child.path
        if child.kind == inventory.KIND_SQLITE:
            files.update(_sqlite_paths(target))
        elif target.is_dir():
            dirs.add(target)
        else:
            files.add(target)
    return files, dirs


def _tree_bytes(root: Path, *, skip_files: set[Path], skip_dirs: set[Path]) -> int:
    """Recursive size of a directory, minus anything a nested entry bills, never raising.

    Symlinks are counted as links, not targets: a store that symlinks a model cache would
    otherwise bill the cache's bytes to the store, and a symlink loop would never terminate.
    """
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        # Pruned in place — os.walk's documented way to not descend, and cheaper than walking a
        # whole subtree only to discard every file in it.
        dirnames[:] = [d for d in dirnames if (here / d) not in skip_dirs]
        for name in filenames:
            path = here / name
            if path in skip_files:
                continue
            try:
                total += path.lstat().st_size
            except OSError:
                continue
    return total


def entry_bytes(home: Path, entry: inventory.StateEntry) -> int:
    """Bytes on disk for one manifest entry — the file, its SQLite sidecars, or the whole tree.

    A tree EXCLUDES whatever a nested entry claims, so every byte under the home belongs to
    exactly one row and the rows sum to the total.
    """
    target = home / entry.path
    try:
        stat = target.lstat()
    except OSError:
        return 0
    if target.is_dir():
        skip_files, skip_dirs = _claimed_by_children(home, entry)
        return _tree_bytes(target, skip_files=skip_files, skip_dirs=skip_dirs)
    total = int(stat.st_size)
    if entry.kind == inventory.KIND_SQLITE:
        for sidecar in _sqlite_paths(target)[1:]:
            try:
                total += int(sidecar.lstat().st_size)
            except OSError:
                continue
    return total


def measure(home: Path) -> list[StoreFootprint]:
    """Every declared store's bytes on disk, largest first.

    Absent stores are RETAINED with ``present=False`` rather than dropped: a report that silently
    omits what is not there cannot be told apart from a report that missed it, and "knowledge.db
    does not exist yet" is a real answer to "where are my bytes going?".
    """
    rows = [
        StoreFootprint(
            id=entry.id,
            path=entry.path,
            domain=entry.domain,
            kind=entry.kind,
            bytes=entry_bytes(home, entry),
            present=(home / entry.path).exists(),
            derived=entry.derived,
        )
        for entry in inventory.all_entries()
    ]
    rows.sort(key=lambda r: (-r.bytes, r.path))
    return rows


def total_bytes(rows: list[StoreFootprint]) -> int:
    return sum(r.bytes for r in rows)


# ── the sample series ───────────────────────────────────────────────────────


def state_path(home: Path) -> Path:
    return home / _STATE_FILENAME


def load_state(home: Path) -> dict:
    """The persisted series, or an empty one.

    A corrupt file reads as EMPTY rather than propagating: the series is observational, so losing
    it costs a trend line, and refusing to run the probe because its own history is unreadable
    would turn a cosmetic failure into a missing maintenance pass.
    """
    try:
        raw = json.loads(state_path(home).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 — a hand-edited file must not wedge the probe
        logger.warning("footprint state unreadable; starting a fresh series")
    return {}


def _save_state(home: Path, state: dict) -> None:
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(state_path(home), json.dumps(state, indent=2) + "\n")
    except Exception:  # noqa: BLE001 — losing a sample costs a trend line, never a store
        logger.warning("footprint state not written", exc_info=True)


def load_samples(home: Path) -> list[Sample]:
    """The recorded series, oldest first."""
    raw = load_state(home).get("samples")
    if not isinstance(raw, list):
        return []
    samples = [s for s in (Sample.from_dict(r) for r in raw if isinstance(r, dict)) if s]
    samples.sort(key=lambda s: s.at)
    return samples


def record(home: Path, *, now: float | None = None) -> Sample:
    """Measure, append to the series, and return the new sample.

    Called from BOTH the maintenance tick and the CLI, on purpose: the tick gives the series a
    cadence a user never has to think about, and the CLI means a user who wants a rate right now
    can get one by running the command twice instead of waiting a day for the second point.
    """
    rows = measure(home)
    sample = Sample(
        at=float(now if now is not None else time.time()),
        total_bytes=total_bytes(rows),
        stores={r.id: r.bytes for r in rows if r.bytes},
    )
    state = load_state(home)
    series = [s.to_dict() for s in load_samples(home)]
    series.append(sample.to_dict())
    state["samples"] = series[-SAMPLE_KEEP:]
    _save_state(home, state)
    return sample


@dataclass(frozen=True)
class Growth:
    """A rate derived from two samples, and the window it was derived over."""

    first_at: float
    last_at: float
    bytes_per_day: float
    total_delta: int
    per_store: dict[str, float] = field(default_factory=dict)

    @property
    def span_secs(self) -> float:
        return self.last_at - self.first_at

    def to_dict(self) -> dict:
        return {
            "first_at": self.first_at,
            "last_at": self.last_at,
            "span_secs": self.span_secs,
            "bytes_per_day": self.bytes_per_day,
            "total_delta": self.total_delta,
            "per_store_bytes_per_day": dict(self.per_store),
        }


def growth(samples: list[Sample]) -> Growth | None:
    """The growth rate across the retained window, or None when it cannot be computed.

    Returns None for fewer than two samples, and for two samples that share a timestamp. A rate
    needs two readings AND elapsed time between them; reporting 0 B/day from one sample would be
    indistinguishable from a store that genuinely stopped growing, which is the one reading a
    footprint report must never fake.

    Uses the ENDS of the window rather than the last two samples: the ends span the longest
    interval available, so the number is a trend rather than yesterday's noise.
    """
    if len(samples) < 2:
        return None
    first, last = samples[0], samples[-1]
    span = last.at - first.at
    if span <= 0:
        return None
    per_day = 86400.0 / span
    per_store = {
        store_id: (last.stores.get(store_id, 0) - first.stores.get(store_id, 0)) * per_day
        for store_id in set(first.stores) | set(last.stores)
    }
    return Growth(
        first_at=first.at,
        last_at=last.at,
        bytes_per_day=(last.total_bytes - first.total_bytes) * per_day,
        total_delta=last.total_bytes - first.total_bytes,
        per_store={k: v for k, v in per_store.items() if v},
    )


# ── reclaiming ──────────────────────────────────────────────────────────────


@dataclass
class ReclaimResult:
    """What one reclaim pass actually moved. ``freed_bytes`` is measured, not estimated."""

    before_bytes: int = 0
    after_bytes: int = 0
    stores: int = 0
    per_store: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def freed_bytes(self) -> int:
        return self.before_bytes - self.after_bytes

    def to_dict(self) -> dict:
        return {
            "before_bytes": self.before_bytes,
            "after_bytes": self.after_bytes,
            "freed_bytes": self.freed_bytes,
            "stores": self.stores,
            "per_store_freed": dict(self.per_store),
            "skipped": dict(self.skipped),
        }


def _fts5_tables(conn: sqlite3.Connection) -> list[str]:
    """FTS5 virtual tables in this database.

    Read from ``sqlite_master`` rather than guessed from names: the FTS tables in this product are
    spelled six different ways across the stores, and a hardcoded list would silently stop
    covering whichever one gets renamed next.
    """
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND sql LIKE '%USING fts5%' COLLATE NOCASE"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows]


def reclaim_store(path: Path) -> None:
    """Compact one database in place: FTS5 merge, then ``PRAGMA optimize``, then ``VACUUM``.

    Order matters and is the whole point. FTS5 ``'optimize'`` merges the index segments that
    deleted rows left behind, freeing pages INSIDE the file; ``VACUUM`` then rewrites the file
    and returns those pages to the filesystem. Run the other way round, VACUUM would compact a
    file the FTS merge is about to bloat again.

    ``isolation_level=None`` because VACUUM cannot run inside a transaction, and Python's default
    connection opens one implicitly before the first statement — with the default this raises
    "cannot VACUUM from within a transaction" and nothing is ever reclaimed.

    Raises :class:`sqlite3.Error` on a locked or damaged store. The caller decides what a single
    unreachable store means for the pass; here, being honest about the failure is the job.
    """
    conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_SECS, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(_BUSY_TIMEOUT_SECS * 1000)}")
        for table in _fts5_tables(conn):
            try:
                conn.execute(f'INSERT INTO "{table}"("{table}") VALUES(\'optimize\')')
            except sqlite3.Error:
                # One unmergeable index must not cost the store its VACUUM — the merge is an
                # optimization, the VACUUM is the byte-mover.
                logger.debug("fts5 optimize failed for %s in %s", table, path.name, exc_info=True)
        conn.execute("PRAGMA optimize")
        conn.execute("VACUUM")
    finally:
        conn.close()


def reclaim(home: Path) -> ReclaimResult:
    """Compact every declared database under *home* and report the bytes actually returned.

    Only manifest-declared ``sqlite`` entries: a glob for ``*.db`` would sweep whatever a user
    dropped in their home, and compacting a file nothing here owns is not this job's business.

    Never raises. A store that is locked, missing or damaged is recorded in ``skipped`` and the
    pass continues — one busy database must not cost every other store its reclaim.
    """
    result = ReclaimResult()
    for entry in inventory.sqlite_entries():
        path = home / entry.path
        if not path.is_file():
            continue
        before = entry_bytes(home, entry)
        try:
            reclaim_store(path)
        except sqlite3.Error as exc:
            result.skipped[entry.id] = str(exc)
            logger.debug("reclaim skipped %s: %s", entry.id, exc)
            continue
        except OSError as exc:
            result.skipped[entry.id] = str(exc)
            logger.debug("reclaim skipped %s: %s", entry.id, exc)
            continue
        after = entry_bytes(home, entry)
        result.stores += 1
        result.before_bytes += before
        result.after_bytes += after
        if before != after:
            result.per_store[entry.id] = before - after
    return result


def reclaim_due(home: Path, *, now: float | None = None) -> bool:
    """Whether a reclaim pass is due (``RECLAIM_SECS`` since the last one)."""
    stamp = float(now if now is not None else time.time())
    last = float(load_state(home).get("last_reclaim") or 0.0)
    return (stamp - last) >= RECLAIM_SECS


def stamp_reclaim(home: Path, *, now: float | None = None) -> None:
    """Record that a reclaim pass ran, due or not, so the cadence is measured from real work."""
    state = load_state(home)
    state["last_reclaim"] = float(now if now is not None else time.time())
    _save_state(home, state)


# ── formatting ──────────────────────────────────────────────────────────────


def human_bytes(count: float) -> str:
    """Bytes as a short human string. Signed, because a growth rate can be negative."""
    sign = "-" if count < 0 else ""
    value = abs(float(count))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            precision = 0 if unit == "B" else 1
            return f"{sign}{value:.{precision}f} {unit}"
        value /= 1024
    return f"{sign}{value:.1f} TB"  # pragma: no cover — the loop always returns


def human_span(secs: float) -> str:
    if secs < 3600:
        return f"{secs / 60:.0f}m"
    if secs < 86400:
        return f"{secs / 3600:.1f}h"
    return f"{secs / 86400:.1f}d"


# ── CLI ─────────────────────────────────────────────────────────────────────


def _home() -> Path:
    """The home the CLI reports on — delegated, never re-derived.

    `service.active_home()` already resolves `PERSONALCLAW_HOME` then the config dir. A second
    spelling here would be a second thing to keep in sync, and the failure it produces is a
    report that quietly describes a different home than the tick compacts.
    """
    from personalclaw.durability.service import active_home

    return active_home()


def report(home: Path, *, now: float | None = None) -> dict:
    """The whole footprint answer as data: per-store bytes, the total, and the growth rate.

    Recording happens HERE rather than in the printer, because the rate is the product: a report
    that only read the series would leave a user who ran the command twice still looking at "one
    sample", which is the exact failure the two-sample requirement exists to prevent.
    """
    rows = measure(home)
    record(home, now=now)
    samples = load_samples(home)
    rate = growth(samples)
    return {
        "home": str(home),
        "stores": [r.to_dict() for r in rows],
        "total_bytes": total_bytes(rows),
        "samples": len(samples),
        "growth": rate.to_dict() if rate else None,
    }


def footprint_cmd(args) -> int:  # noqa: ANN001
    """``personalclaw footprint [--json] [--reclaim]`` — where the bytes are, and where they go.

    The default is read-only apart from appending one sample to the series, which is what makes
    the growth rate possible: run it now and again tomorrow and the second run reports a rate.
    ``--reclaim`` runs the same compaction the maintenance tick runs, for a user who wants the
    space back now rather than at the next cadence.
    """
    home = _home()
    as_json = bool(getattr(args, "json", False))

    reclaimed: ReclaimResult | None = None
    if getattr(args, "reclaim", False):
        from personalclaw.concurrency import single_flight

        with single_flight("footprint-reclaim") as acquired:
            if not acquired:
                print("⏭  Another reclaim is already running — skipping.")
                return 0
            reclaimed = reclaim(home)
        stamp_reclaim(home)

    data = report(home)
    if reclaimed is not None:
        data["reclaim"] = reclaimed.to_dict()

    if as_json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"Store footprint — {home}")
    print()
    width = max([len(r["path"]) for r in data["stores"]] + [5])
    for row in data["stores"]:
        if not row["bytes"]:
            continue
        tags = " (derived)" if row["derived"] else ""
        print(f"  {row['path']:<{width}}  {human_bytes(row['bytes']):>10}  {row['domain']}{tags}")
    print(f"  {'TOTAL':<{width}}  {human_bytes(data['total_bytes']):>10}")
    print()

    rate = data["growth"]
    if rate is None:
        print(
            f"Growth: not yet measurable — {data['samples']} sample recorded. "
            "Run `personalclaw footprint` again later for a rate."
        )
    else:
        print(
            f"Growth: {human_bytes(rate['bytes_per_day'])}/day "
            f"({data['samples']} samples over {human_span(rate['span_secs'])}, "
            f"{human_bytes(rate['total_delta'])} total)"
        )
        worst = sorted(rate["per_store_bytes_per_day"].items(), key=lambda kv: -kv[1])[:3]
        for store_id, per_day in worst:
            if per_day <= 0:
                break
            entry = inventory.by_id(store_id)
            print(f"  ↑ {entry.path if entry else store_id}  {human_bytes(per_day)}/day")

    if reclaimed is not None:
        print()
        if reclaimed.freed_bytes > 0:
            print(
                f"Reclaimed {human_bytes(reclaimed.freed_bytes)} "
                f"across {reclaimed.stores} database(s)."
            )
        else:
            print(f"Nothing to reclaim — {reclaimed.stores} database(s) already compact.")
        for store_id, reason in sorted(reclaimed.skipped.items()):
            print(f"⚠️  skipped {store_id}: {reason}")
    return 0
