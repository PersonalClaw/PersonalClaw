"""The staging tier — an append-only capture log with explicit outcome records.

Two problems this solves, both learned from real failures.

**Silent capture failure.** A capture pass that reads the wrong transcript, or
throws inside a best-effort ``except``, produces exactly what a working pass
produces on a quiet day: nothing. That class of bug survived a long time here
precisely because its symptom is indistinguishable from correct operation. So
every pass persists an outcome — ``FLUSH_OK`` (ran, nothing worth proposing),
``FLUSH_ERROR`` (ran, broke, with the type and message), or the proposal ids it
produced. Absence of output stops being invisible: a week of nothing on an active
system is now a *reportable* state rather than a silence nobody can distinguish
from health.

**Cheap-vs-expensive coupling.** Extraction has to be cheap enough to run on every
turn; consolidation is expensive enough that it must batch. Writing them as one
pass forces a choice between paying too often and losing signal. Staging splits
them: extraction appends raw entries immediately, and the expensive pass runs
later over the accumulation, gated by activity + a time window + input-hash
idempotence — no new daemon, it piggybacks on cadences that already fire.

**Append-only.** Consolidation never edits staging entries; it reads them and
writes elsewhere. Compiled proposals keep ``sources`` pointers back, so a
surprising proposal can be traced to the turns that produced it. If consolidation
could rewrite its own inputs, that audit trail would describe the conclusion
rather than the evidence.

Storage is ``learning.db`` beside ``memory.db`` — a separate file because staging
is high-volume, low-value, and independently prunable, and because a corrupt
capture log must never take semantic memory down with it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from personalclaw.learning.hygiene import fingerprint

logger = logging.getLogger(__name__)

DB_FILE = "learning.db"

#: Entries older than this are prunable. Staging is a buffer, not an archive —
#: anything of durable value has been compiled into a proposal by now.
DEFAULT_RETENTION_DAYS = 30

#: The batch window: consolidation waits at least this long between passes so a
#: burst of turns produces one expensive pass, not one per turn.
DEFAULT_BATCH_WINDOW_SECS = 900.0


class FlushOutcome(str, Enum):
    """The three things an extraction pass can honestly report."""

    #: Ran to completion; nothing met the bar. The common, healthy case.
    FLUSH_OK = "flush_ok"
    #: Ran and produced staged entries or proposals.
    FLUSH_PRODUCED = "flush_produced"
    #: Raised. Recorded WITH the exception type and message.
    FLUSH_ERROR = "flush_error"
    #: The gate denied it. Recorded so a config-off period is legible.
    FLUSH_SKIPPED = "flush_skipped"


@dataclass(frozen=True)
class StagingEntry:
    """One raw captured signal, as stored."""

    id: int
    day: str
    cadence: str
    kind: str
    content: str
    content_hash: str
    session_key: str
    created_ts: float
    meta: dict[str, Any]


def input_hash(items: list[str]) -> str:
    """A stable hash of an expensive pass's inputs, for idempotence.

    Order-insensitive: the same set of staging entries yields the same hash
    regardless of the order the query returned them, so a re-run that happens to
    read rows differently is still recognised as the same work.
    """
    return fingerprint("\n".join(sorted(items)))


class StagingStore:
    """The append-only capture log. One instance per home directory.

    Thread-safe by lock rather than by connection-per-thread: writes are small
    and infrequent relative to the turn they ride on, and a single connection
    keeps the WAL story simple.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base = Path(base_dir) if base_dir else _default_home()
        self._path = self._base / DB_FILE
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    # ── Connection ──

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            if self._conn is None:
                self._base.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._bootstrap(self._conn)
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            finally:
                cur.close()

    @staticmethod
    def _bootstrap(conn: sqlite3.Connection) -> None:
        """Create the schema. Idempotent, so opening an existing db is a no-op."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS staging (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                day           TEXT NOT NULL,
                cadence       TEXT NOT NULL,
                kind          TEXT NOT NULL,
                content       TEXT NOT NULL,
                content_hash  TEXT NOT NULL,
                session_key   TEXT NOT NULL DEFAULT '',
                created_ts    REAL NOT NULL,
                meta          TEXT NOT NULL DEFAULT '{}',
                consumed_by   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_staging_day ON staging(day);
            CREATE INDEX IF NOT EXISTS idx_staging_hash ON staging(content_hash);

            CREATE TABLE IF NOT EXISTS flush_records (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                cadence      TEXT NOT NULL,
                outcome      TEXT NOT NULL,
                detail       TEXT NOT NULL DEFAULT '',
                staged_count INTEGER NOT NULL DEFAULT 0,
                proposal_ids TEXT NOT NULL DEFAULT '[]',
                cost_usd     REAL NOT NULL DEFAULT 0.0,
                created_ts   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_flush_ts ON flush_records(created_ts);

            CREATE TABLE IF NOT EXISTS batch_passes (
                input_hash TEXT PRIMARY KEY,
                created_ts REAL NOT NULL,
                detail     TEXT NOT NULL DEFAULT ''
            );

            -- One row per ambient render (LEARN-R14b). `ambient.report()` computed
            -- exactly this and sent it to a debug log, so the budget-utilization the
            -- health composite needs had no persisted writer at all: a panel reading
            -- it would have rendered from a key nothing wrote.
            CREATE TABLE IF NOT EXISTS allocation_samples (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                used_tokens  INTEGER NOT NULL,
                budget_tokens INTEGER NOT NULL,
                created_ts   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alloc_ts ON allocation_samples(created_ts);

            -- One row per heuristic per sweep (§2.5's ablation-delta rule).
            CREATE TABLE IF NOT EXISTS ablation_sweeps (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_ts   REAL NOT NULL,
                heuristic  TEXT NOT NULL,
                delta      REAL NOT NULL,
                verdict    TEXT NOT NULL,
                items      INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ablation_ts ON ablation_sweeps(sweep_ts);
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_staging_consumed ON staging(consumed_by) "
            "WHERE consumed_by IS NOT NULL;"
        )
        conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── Append (cheap path) ──

    def stage(
        self,
        *,
        cadence: str,
        kind: str,
        content: str,
        session_key: str = "",
        meta: dict[str, Any] | None = None,
    ) -> int:
        """Append one raw signal. Returns its id (0 if deduplicated).

        Same-content entries within the same day are collapsed: a user repeating
        a preference in one session is one signal for staging purposes, and the
        evidence count that matters (``MIN_EVIDENCE_DEFAULT``) is about distinct
        occasions, not repetitions inside one.
        """
        if not content or not content.strip():
            return 0
        chash = fingerprint(content)
        day = _today()
        with self._cursor() as cur:
            existing = cur.execute(
                "SELECT id FROM staging WHERE day = ? AND content_hash = ? LIMIT 1;",
                (day, chash),
            ).fetchone()
            if existing:
                return 0
            cur.execute(
                "INSERT INTO staging (day, cadence, kind, content, content_hash, "
                "session_key, created_ts, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    day,
                    str(cadence),
                    str(kind),
                    content,
                    chash,
                    session_key or "",
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid or 0)

    # ── Outcome records (the observability floor) ──

    def record_flush(
        self,
        *,
        cadence: str,
        outcome: FlushOutcome,
        detail: str = "",
        staged_count: int = 0,
        proposal_ids: list[str] | None = None,
        cost_usd: float = 0.0,
    ) -> int:
        """Persist what one pass did. EVERY pass calls this, including no-ops."""
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO flush_records (cadence, outcome, detail, staged_count, "
                "proposal_ids, cost_usd, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?);",
                (
                    str(cadence),
                    outcome.value,
                    detail[:2000],
                    int(staged_count),
                    json.dumps(list(proposal_ids or [])),
                    float(cost_usd),
                    time.time(),
                ),
            )
            return int(cur.lastrowid or 0)

    @contextmanager
    def flush(self, cadence: str) -> Iterator[dict[str, Any]]:
        """Run a pass so its outcome is recorded no matter how it exits.

        The whole point: an exception inside a best-effort capture used to vanish
        into a ``debug`` log. Here it becomes a ``FLUSH_ERROR`` row with the
        exception type and message, and is then re-raised or swallowed by the
        caller's own policy — the record happens either way.
        """
        result: dict[str, Any] = {"staged": 0, "proposals": [], "cost_usd": 0.0}
        try:
            yield result
        except Exception as exc:
            self.record_flush(
                cadence=cadence,
                outcome=FlushOutcome.FLUSH_ERROR,
                detail=f"{type(exc).__name__}: {exc}",
                cost_usd=float(result.get("cost_usd") or 0.0),
            )
            raise
        staged = int(result.get("staged") or 0)
        proposals = list(result.get("proposals") or [])
        produced = bool(staged or proposals)
        self.record_flush(
            cadence=cadence,
            outcome=FlushOutcome.FLUSH_PRODUCED if produced else FlushOutcome.FLUSH_OK,
            detail=str(result.get("detail") or ""),
            staged_count=staged,
            proposal_ids=[str(p) for p in proposals],
            cost_usd=float(result.get("cost_usd") or 0.0),
        )

    # ── Read (the expensive path's input) ──

    def pending(self, *, limit: int = 500, cadence: str = "") -> list[StagingEntry]:
        """Unconsumed entries, oldest first — what the batch pass reads."""
        sql = "SELECT * FROM staging WHERE consumed_by IS NULL"
        params: list[Any] = []
        if cadence:
            sql += " AND cadence = ?"
            params.append(cadence)
        sql += " ORDER BY created_ts ASC LIMIT ?;"
        params.append(int(limit))
        with self._cursor() as cur:
            return [_row_to_entry(r) for r in cur.execute(sql, params).fetchall()]

    def mark_consumed(self, ids: list[int], marker: str) -> int:
        """Mark entries as consumed by a batch pass. Does NOT edit content.

        Consumption is the one mutation staging allows, and it deliberately
        touches only this pointer — the entry itself stays exactly as captured,
        which is what makes ``sources`` provenance trustworthy.
        """
        if not ids:
            return 0
        with self._cursor() as cur:
            cur.executemany(
                "UPDATE staging SET consumed_by = ? WHERE id = ? AND consumed_by IS NULL;",
                [(marker, int(i)) for i in ids],
            )
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(ids)

    def sources_for(self, ids: list[int]) -> list[dict[str, Any]]:
        """Provenance pointers for a compiled proposal."""
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with self._cursor() as cur:
            rows = cur.execute(
                f"SELECT id, day, cadence, kind, content_hash FROM staging WHERE id IN ({marks});",
                [int(i) for i in ids],
            ).fetchall()
        return [dict(r) for r in rows]

    # ── The batch gate ──

    def should_batch(
        self,
        *,
        min_entries: int = 5,
        window_secs: float = DEFAULT_BATCH_WINDOW_SECS,
        now: float | None = None,
    ) -> bool:
        """Is an expensive pass warranted? Activity AND a time window.

        Both conditions, because either alone misbehaves: entry-count alone runs
        the expensive pass mid-burst and then again ten seconds later, while a
        timer alone pays for a pass over an empty log.
        """
        now = time.time() if now is None else now
        with self._cursor() as cur:
            count = int(
                cur.execute("SELECT COUNT(*) FROM staging WHERE consumed_by IS NULL;").fetchone()[0]
            )
            if count < max(1, min_entries):
                return False
            last = cur.execute("SELECT MAX(created_ts) FROM batch_passes;").fetchone()[0]
        return last is None or (now - float(last)) >= window_secs

    def claim_batch(self, ihash: str, *, detail: str = "") -> bool:
        """Claim a pass by input hash. False if this exact work already ran.

        Input-hash idempotence is what makes the batch pass safe to trigger from
        several cadences: a retry, a restart, or two cadences firing together all
        try to claim, and exactly one wins.
        """
        with self._cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO batch_passes (input_hash, created_ts, detail) VALUES (?, ?, ?);",
                    (ihash, time.time(), detail[:500]),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    # ── Observability + maintenance ──

    def health(self, *, days: int = 7, now: float | None = None) -> dict[str, Any]:
        """Answer "is capture working?" over a window.

        ``all_ok_streak`` is the signal worth alarming on: passes running,
        producing nothing, on a system that was active. That is exactly the
        shape of the dead-read bug this tier exists to make visible.
        """
        now = time.time() if now is None else now
        since = now - days * 86400
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT outcome, COUNT(*) AS n, SUM(cost_usd) AS cost FROM flush_records "
                "WHERE created_ts >= ? GROUP BY outcome;",
                (since,),
            ).fetchall()
            staged = int(
                cur.execute(
                    "SELECT COUNT(*) FROM staging WHERE created_ts >= ?;", (since,)
                ).fetchone()[0]
            )
            recent = [
                r[0]
                for r in cur.execute(
                    "SELECT outcome FROM flush_records ORDER BY created_ts DESC LIMIT 50;"
                ).fetchall()
            ]
        by_outcome = {r["outcome"]: int(r["n"]) for r in rows}
        streak = 0
        for outcome in recent:
            if outcome == FlushOutcome.FLUSH_OK.value:
                streak += 1
            else:
                break
        return {
            "days": days,
            "passes": sum(by_outcome.values()),
            "by_outcome": by_outcome,
            "staged_entries": staged,
            "errors": by_outcome.get(FlushOutcome.FLUSH_ERROR.value, 0),
            "cost_usd": round(sum(float(r["cost"] or 0.0) for r in rows), 6),
            "all_ok_streak": streak,
        }

    def cost_by_op(self, *, days: int = 7, now: float | None = None) -> list[dict[str, Any]]:
        """Per-op LLM cost aggregates (LEARN-R19e), dearest first.

        The "op" is the flush record's `cadence` — the identity every flush already
        carries. A single total answers "was it expensive"; only the per-op split
        answers "expensive at WHAT", which is the question that leads to a change.
        """
        now = time.time() if now is None else now
        since = now - max(1, days) * 86400
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT cadence, COUNT(*) AS passes, SUM(cost_usd) AS cost "
                "FROM flush_records WHERE created_ts >= ? GROUP BY cadence;",
                (since,),
            ).fetchall()
        out: list[dict[str, Any]] = [
            {
                "op": str(r["cadence"]),
                "passes": int(r["passes"] or 0),
                "cost_usd": round(float(r["cost"] or 0.0), 6),
            }
            for r in rows
        ]
        # Dearest first, ties broken by name so the order is stable across reads.
        out.sort(key=lambda row: (-float(row["cost_usd"]), str(row["op"])))
        return out

    # ── Budget utilization (LEARN-R14b) ──

    #: Rows kept in `allocation_samples`. A rolling window, not a history: the panel
    #: reports a recent mean, and an unbounded per-turn table would be the largest
    #: thing in learning.db within a week for no added answer.
    ALLOCATION_KEEP = 500

    def record_allocation(
        self, *, used_tokens: int, budget_tokens: int, now: float | None = None
    ) -> bool:
        """Record one ambient render's budget usage. Returns False when unusable.

        A zero or negative budget is not a 0% sample — it is an absent measurement,
        and averaging it in would drag the composite toward "starving" on every turn
        where the allocator was switched off.
        """
        if budget_tokens <= 0:
            return False
        ts = time.time() if now is None else now
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO allocation_samples (used_tokens, budget_tokens, created_ts) "
                "VALUES (?, ?, ?);",
                (max(0, int(used_tokens)), int(budget_tokens), ts),
            )
            cur.execute(
                "DELETE FROM allocation_samples WHERE id <= "
                "(SELECT MAX(id) - ? FROM allocation_samples);",
                (self.ALLOCATION_KEEP,),
            )
        return True

    def utilization(self, *, days: int = 7, now: float | None = None) -> dict[str, Any]:
        """Mean budget utilization over the window, as a fraction in [0, 1]."""
        now = time.time() if now is None else now
        since = now - max(1, days) * 86400
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT COUNT(*) AS n, SUM(used_tokens) AS used, SUM(budget_tokens) AS budget "
                "FROM allocation_samples WHERE created_ts >= ?;",
                (since,),
            ).fetchone()
        samples = int(row["n"] or 0)
        budget = float(row["budget"] or 0.0)
        used = float(row["used"] or 0.0)
        return {
            "samples": samples,
            # None, not 0.0: "never measured" and "measured at zero" are opposite
            # claims about the allocator, and the composite must be able to tell them
            # apart or an install with no traffic reads as a starving one.
            "mean": round(used / budget, 4) if samples and budget > 0 else None,
        }

    # ── Ablation sweeps (§2.5) ──

    def record_ablation(self, rows: list[dict[str, Any]], *, now: float | None = None) -> int:
        """Persist one sweep's rows. Returns how many landed."""
        if not rows:
            return 0
        ts = time.time() if now is None else now
        with self._cursor() as cur:
            for row in rows:
                cur.execute(
                    "INSERT INTO ablation_sweeps (sweep_ts, heuristic, delta, verdict, items) "
                    "VALUES (?, ?, ?, ?, ?);",
                    (
                        ts,
                        str(row.get("heuristic", "")),
                        float(row.get("delta", 0.0) or 0.0),
                        str(row.get("verdict", "")),
                        int(row.get("items", 0) or 0),
                    ),
                )
        return len(rows)

    def latest_ablation(self) -> dict[str, Any]:
        """The most recent sweep, or an empty dict when none has ever run."""
        with self._cursor() as cur:
            newest = cur.execute("SELECT MAX(sweep_ts) AS ts FROM ablation_sweeps;").fetchone()
            ts = newest["ts"] if newest else None
            if not ts:
                return {}
            rows = cur.execute(
                "SELECT heuristic, delta, verdict, items FROM ablation_sweeps "
                "WHERE sweep_ts = ? ORDER BY delta;",
                (ts,),
            ).fetchall()
        return {
            "at": datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(),
            "rows": [
                {
                    "heuristic": str(r["heuristic"]),
                    "delta": round(float(r["delta"]), 6),
                    "verdict": str(r["verdict"]),
                    "items": int(r["items"]),
                }
                for r in rows
            ],
        }

    def ablation_due(self, *, every_secs: float = 86400.0, now: float | None = None) -> bool:
        """Is a sweep due? True when none has run, or the newest is older than the cadence.

        The cadence is the whole reason this is affordable: the sweep runs the
        allocator once per heuristic, and paying that per turn to learn something that
        changes monthly would be a cost with no matching benefit.
        """
        ts_now = time.time() if now is None else now
        with self._cursor() as cur:
            row = cur.execute("SELECT MAX(sweep_ts) AS ts FROM ablation_sweeps;").fetchone()
        newest = float(row["ts"]) if row and row["ts"] else 0.0
        return (ts_now - newest) >= max(1.0, every_secs)

    def week(self, *, days: int = 7, now: float | None = None) -> dict[str, Any]:
        """The week-at-a-glance panel: one bucket per DAY (§6 — S76).

        `health()` answers "is capture working" over a WINDOW, and that aggregation hides the thing
        this panel exists to show. Measured: a day with ZERO passes is indistinguishable from a
        healthy day in the windowed view, because an absent day contributes nothing to either the
        outcome counts or the streak — and silent capture death is precisely the failure the staging
        tier was built to make visible.

        So every day in the window gets a row, INCLUDING the empty ones. A gap is the signal.

        Days are bucketed by local date rather than by 86400-second slices: a user reading "Tuesday"
        means their Tuesday, and a UTC-slice panel drifts a few hours off every reader's calendar.
        """
        now = time.time() if now is None else now
        span = max(1, days)
        since = now - span * 86400

        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT outcome, cost_usd, proposal_ids, created_ts FROM flush_records "
                "WHERE created_ts >= ? ORDER BY created_ts;",
                (since,),
            ).fetchall()
            staged_rows = cur.execute(
                "SELECT created_ts FROM staging WHERE created_ts >= ?;", (since,)
            ).fetchall()

        def _day(ts: float) -> str:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

        # Pre-seed every day so an empty one renders as a gap rather than vanishing.
        buckets: dict[str, dict[str, Any]] = {}
        for offset in range(span):
            key = _day(now - offset * 86400)
            buckets[key] = {
                "day": key,
                "passes": 0,
                "by_outcome": {},
                "produced": 0,
                "errors": 0,
                "staged": 0,
                "cost_usd": 0.0,
                "proposal_ids": [],
            }

        for row in rows:
            key = _day(float(row["created_ts"]))
            bucket = buckets.get(key)
            if bucket is None:
                continue
            outcome = str(row["outcome"])
            bucket["passes"] += 1
            bucket["by_outcome"][outcome] = bucket["by_outcome"].get(outcome, 0) + 1
            bucket["cost_usd"] += float(row["cost_usd"] or 0.0)
            if outcome == FlushOutcome.FLUSH_ERROR.value:
                bucket["errors"] += 1
            try:
                ids = json.loads(row["proposal_ids"] or "[]")
            except (TypeError, ValueError):
                ids = []
            if isinstance(ids, list) and ids:
                # Proposal ids are what turn "a pass produced something" into "produced WHAT" — the
                # panel links straight to the Proposal Inbox rows a day generated.
                bucket["proposal_ids"].extend(str(i) for i in ids)
                bucket["produced"] += len(ids)

        for row in staged_rows:
            bucket = buckets.get(_day(float(row["created_ts"])))
            if bucket is not None:
                bucket["staged"] += 1

        ordered = [buckets[k] for k in sorted(buckets)]
        for bucket in ordered:
            bucket["cost_usd"] = round(bucket["cost_usd"], 6)
        silent = [b["day"] for b in ordered if b["passes"] == 0]
        return {
            "days": span,
            "buckets": ordered,
            # The two summary numbers a panel headline needs. `silent_days` is the alarming one: a
            # day with no passes at all on a machine that was in use means capture did not run.
            "silent_days": silent,
            "error_days": [b["day"] for b in ordered if b["errors"]],
            "produced_total": sum(b["produced"] for b in ordered),
            "cost_usd": round(sum(b["cost_usd"] for b in ordered), 6),
        }

    def prune(self, *, retention_days: int = DEFAULT_RETENTION_DAYS, now: float | None = None):
        """Drop consumed entries past the retention window. Returns rows removed.

        Only *consumed* rows: an unconsumed entry is still owed a batch pass, and
        deleting it would lose the signal silently — the exact failure mode this
        module exists to prevent.
        """
        now = time.time() if now is None else now
        cutoff = now - max(1, retention_days) * 86400
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM staging WHERE consumed_by IS NOT NULL AND created_ts < ?;",
                (cutoff,),
            )
            return int(cur.rowcount or 0)


# ── Module-level accessor ──

_INSTANCE: StagingStore | None = None
_INSTANCE_LOCK = threading.Lock()


def get_store(base_dir: Path | str | None = None) -> StagingStore:
    """The shared store for the active home, or a fresh one for an explicit dir.

    An explicit ``base_dir`` deliberately bypasses the cache so a test can point
    at ``tmp_path`` without the process-global instance leaking a real home into
    it — or the other way round.
    """
    global _INSTANCE
    if base_dir is not None:
        return StagingStore(base_dir)
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = StagingStore()
        return _INSTANCE


def reset_store() -> None:
    """Drop the cached instance (tests, and home-directory switches)."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is not None:
            _INSTANCE.close()
        _INSTANCE = None


def _default_home() -> Path:
    try:
        from personalclaw.config.loader import config_dir

        return Path(config_dir())
    except Exception:  # pragma: no cover - config import is exercised elsewhere
        return Path.home() / ".personalclaw"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _row_to_entry(row: sqlite3.Row) -> StagingEntry:
    try:
        meta = json.loads(row["meta"] or "{}")
    except Exception:
        meta = {}
    return StagingEntry(
        id=int(row["id"]),
        day=str(row["day"]),
        cadence=str(row["cadence"]),
        kind=str(row["kind"]),
        content=str(row["content"]),
        content_hash=str(row["content_hash"]),
        session_key=str(row["session_key"] or ""),
        created_ts=float(row["created_ts"]),
        meta=meta if isinstance(meta, dict) else {},
    )
