"""The durable sync outbox (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-b).

Every local export owes a push to each configured remote. That obligation must survive a
crash, a network outage, and a restart — so it lives on disk, not in memory: one JSON file
per (target, seq) under ``<home>/sync/outbox/``. The deliverer (the cycle engine, 6c-ii-c)
drains pending entries, attempts the transport push, and records the typed outcome; this
module owns only the durable bookkeeping, never the transport.

The status/outcome contract, verbatim from §4.1:

* A push has a **status**: ``pending`` (owed), ``delivered`` (landed), ``given-up`` (a
  permanent failure — a bad payload or auth error retrying will not fix).
* A deliverer reports a typed **outcome**: ``delivered`` / ``transient`` (retryable — a
  lock, a race, a network blip) / ``permanent``. An *unexpected throw maps to transient*,
  never a drop: the obligation is only ever discharged by a real ``delivered`` or an
  explicit ``permanent``, so no push is silently lost.
* A ``transient`` outcome leaves the entry ``pending`` and bumps its attempt count; the
  staleness window rate-limits the retry. Faithful to spec, transient never auto-drops.
* **One target giving up never blocks others** — entries are per-(target, seq) files, so a
  ``given-up`` entry for one remote leaves every other remote's queue untouched.

Clock-free like the registry model: timestamps are passed in, so a replay is deterministic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# ── statuses (an entry's durable state) ──────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_GIVEN_UP = "given-up"

# ── deliverer outcomes (mirror sync_transports.base.PushResult.outcome) ──────
OUTCOME_DELIVERED = "delivered"
OUTCOME_TRANSIENT = "transient"
OUTCOME_PERMANENT = "permanent"

_OUTBOX_DIR = "outbox"


def _safe(target: str) -> str:
    """A filesystem-safe token for a transport name in an entry filename. Transport
    names are kebab-case app names, but never trust that a path separator can't appear."""
    return target.replace("/", "_").replace("\\", "_").replace("..", "_") or "unnamed"


def entry_id(target: str, seq: int) -> str:
    """The deterministic id for a (target, seq) obligation, so enqueue is idempotent —
    re-enqueueing after a crash finds the same file rather than duplicating the push."""
    return f"{_safe(target)}__seq-{seq:04d}"


@dataclass
class OutboxEntry:
    """One push obligation: deliver seq ``seq``'s shards (written locally under
    ``local_dir``, destined for remote prefix ``prefix``) to ``target``."""

    id: str
    target: str
    seq: int
    prefix: str = ""  # remote key prefix (registry.shard_prefix)
    local_dir: str = ""  # local export dir the bytes are read from at delivery
    status: str = STATUS_PENDING
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""
    last_outcome: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target": self.target,
            "seq": self.seq,
            "prefix": self.prefix,
            "local_dir": self.local_dir,
            "status": self.status,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_outcome": self.last_outcome,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OutboxEntry:
        return cls(
            id=str(d.get("id", "")),
            target=str(d.get("target", "")),
            seq=int(d.get("seq", 0) or 0),
            prefix=str(d.get("prefix", "")),
            local_dir=str(d.get("local_dir", "")),
            status=str(d.get("status", STATUS_PENDING)),
            attempts=int(d.get("attempts", 0) or 0),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
            last_outcome=str(d.get("last_outcome", "")),
            detail=str(d.get("detail", "")),
        )


class Outbox:
    """The durable per-(target, seq) push queue rooted at ``<sync_root>/outbox/``."""

    def __init__(self, sync_root: Path) -> None:
        self._dir = Path(sync_root) / _OUTBOX_DIR

    def _path(self, eid: str) -> Path:
        return self._dir / f"{eid}.json"

    # ── enqueue / read ───────────────────────────────────────────────────────
    def enqueue(
        self, target: str, seq: int, *, prefix: str = "", local_dir: str = "", now: str = ""
    ) -> OutboxEntry:
        """Record a push obligation. Idempotent on (target, seq): if an entry already
        exists it is returned untouched — re-enqueueing after a crash never resets a
        ``delivered`` entry back to ``pending`` or double-counts a push."""
        eid = entry_id(target, seq)
        existing = self.get(eid)
        if existing is not None:
            return existing
        entry = OutboxEntry(
            id=eid,
            target=target,
            seq=seq,
            prefix=prefix,
            local_dir=local_dir,
            status=STATUS_PENDING,
            created_at=now,
            updated_at=now,
        )
        self._write(entry)
        return entry

    def get(self, eid: str) -> OutboxEntry | None:
        try:
            data = self._path(eid).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            return OutboxEntry.from_dict(json.loads(data))
        except (json.JSONDecodeError, TypeError):
            # A corrupt entry file is not a reason to lose the whole queue; log and skip.
            logger.warning("outbox: skipping unreadable entry %s", eid)
            return None

    def all_entries(self) -> list[OutboxEntry]:
        """Every entry, ordered by seq then target — deterministic for drain and tests."""
        out: list[OutboxEntry] = []
        if not self._dir.is_dir():
            return out
        for p in sorted(self._dir.glob("*.json")):
            e = self.get(p.stem)
            if e is not None:
                out.append(e)
        return sorted(out, key=lambda e: (e.seq, e.target))

    def pending(self) -> list[OutboxEntry]:
        """Entries still owed a delivery (``pending``), oldest seq first. ``delivered`` and
        ``given-up`` are terminal and excluded — a give-up on one target never appears here
        again, so it can't block the drain of another target."""
        return [e for e in self.all_entries() if e.status == STATUS_PENDING]

    # ── record a deliverer outcome ───────────────────────────────────────────
    def record_outcome(
        self, eid: str, outcome: str, *, now: str = "", detail: str = ""
    ) -> OutboxEntry | None:
        """Apply a deliverer's typed outcome to an entry's durable status.

        ``delivered`` → ``delivered`` (terminal, obligation discharged); ``permanent`` →
        ``given-up`` (terminal); anything else — ``transient`` OR an unrecognized value
        (an unexpected throw the deliverer couldn't classify) — leaves the entry
        ``pending`` and bumps ``attempts``, so a push is only ever discharged by a real
        success or an explicit permanent failure, never silently dropped.
        """
        entry = self.get(eid)
        if entry is None:
            return None
        entry.updated_at = now
        entry.last_outcome = outcome
        entry.detail = detail
        if outcome == OUTCOME_DELIVERED:
            entry.status = STATUS_DELIVERED
        elif outcome == OUTCOME_PERMANENT:
            entry.status = STATUS_GIVEN_UP
        else:  # transient, or an unclassified throw → retry, never drop
            entry.status = STATUS_PENDING
            entry.attempts += 1
        self._write(entry)
        return entry

    # ── persistence ──────────────────────────────────────────────────────────
    def _write(self, entry: OutboxEntry) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self._path(entry.id), json.dumps(entry.to_dict(), indent=2) + "\n")

    def stats(self) -> dict:
        """A small tally for the doctor/observability — counts by status."""
        tally = {STATUS_PENDING: 0, STATUS_DELIVERED: 0, STATUS_GIVEN_UP: 0}
        for e in self.all_entries():
            tally[e.status] = tally.get(e.status, 0) + 1
        return tally
