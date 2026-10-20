"""The `surfacing_events` log — one row per candidate the surfacing engine offered.

**This table was NAMED before it existed.** `measure.per_arm_precision` was written to consume
it, `evals/retrieval_bench.py` documented mining it and shipped a SUBSTITUTE instead, and
`dashboard/handlers/learning.py` explained in prose why it could not read it — three consumers
referring to a table with "no schema, no reader, no writer". The gap was the `surfacing_events`
TABLE and its 90d prune on the curator tick. This module is that table.

**What one row means.** A candidate ENTERED the allocator for one turn — it was offered, with a
named match arm and the score it competed on — and either its content reached the prompt or it
did not. That second fact is the `used` column, and it may only ever come from observation:
it is derived MECHANICALLY — skill body loaded after surfacing, template run started
from a suggestion, run outcome success/failure, lesson referenced by after_turn_review — never a
voluntary model feedback call (unenforced 'helpful' scores stay ornamental forever). So this
store has no API for recording an opinion. `used` is a fact the writer observed, or it is False.

**Every column earns its place from a real reader**, because a speculative wide table is worse
than a narrow correct one:

| Column | Read by |
|---|---|
| `kind`, `arm`, `used` | `measure.per_arm_precision` — the keys it destructures, exactly |
| `entity` | the retrieval benchmark's weak labels: the id a positive label points AT |
| `query` | the same: `mine_knowledge_qrels` needs the text the retrieval answered |
| `confidence` | `measure.propose_thresholds` — tuning 0.55/0.62 needs the scores actually seen |
| `session` | self-similar dedup, and forgetting a chat: its rows are found by it |
| `created_ts` | the 90d prune |

**Storage rides `learning.db`**, the one file for the flywheel's lifecycle tables, and
declares its own table lazily with `IF NOT EXISTS` — the same shape `usage.UsageStore` uses over
the same connection. That idempotent declaration IS the migration story here; this project ships
no migration machinery and is not getting any.

**A row keeps the turn's words** (`query`), so nothing is recorded for work that may change none
of your memory (``memory_writes.changes_no_memory``): a turn of an Incognito or Temporary chat,
whose words are kept nowhere but its transcript, and the work of an app not given your memory or
of a turn someone else asked for, which teach the flywheel nothing (``learning.gate``). The store
asks, where every writer of the log writes, as the memory reflex's own offer log is asked
(``memory_service``). What a chat's turns left here goes with the chat: when it is deleted or a
Temporary chat ends (``dashboard.chat_forget.purge_chat``), and, for what an earlier version
recorded of a chat that keeps nothing, when the gateway starts
(:func:`forget_what_restricted_sessions_left`).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from personalclaw.durability import numbered_rows

logger = logging.getLogger(__name__)

#: Events prune at 90d on the curator tick. A flat constant rather than a config knob —
#: there is one number, and a knob nobody has asked to turn is a surface to keep in sync
#: through four wiring points for no answer the constant does not already give.
DEFAULT_RETENTION_DAYS = 90

#: Rows returned by one unbounded read. A guard, not a policy: the readers are a panel and a
#: benchmark miner, and an unbounded fetch over 90 days of turns would make the cheapest page in
#: the app the most expensive one.
DEFAULT_READ_LIMIT = 5000


@dataclass
class SurfacingEvent:
    """One candidate offered on one turn, and whether its content reached the prompt.

    `confidence` carries the score the candidate COMPETED on rather than
    `measure.arm_confidence(arm)`. The latter is derivable from `arm` at any time, so storing it
    would persist a constant; the former is the turn-specific number a threshold proposal is
    actually about.
    """

    kind: str
    entity: str
    arm: str = ""
    confidence: float = 0.0
    used: bool = False
    query: str = ""
    session: str = ""
    created_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """The event as `per_arm_precision` wants it — `kind`/`arm`/`used` at the top level.

        Named to match that function's `event.get(...)` keys rather than translated at the call
        site: a mapping layer between a writer and its only aggregator is a place for the two to
        drift, and the drift would look like a precision change rather than a bug.
        """
        return {
            "kind": self.kind,
            "entity": self.entity,
            "arm": self.arm,
            "confidence": round(float(self.confidence), 4),
            "used": bool(self.used),
            "query": self.query,
            "session": self.session,
            "created_ts": self.created_ts,
        }

    @classmethod
    def from_dict(cls, data: Any) -> SurfacingEvent | None:
        """Tolerant parse: `None` for anything that is not a usable row.

        Tolerant because the aggregator downstream is (`per_arm_precision` skips non-dicts), and
        because a store that raises on one malformed historical row makes the whole report
        unavailable rather than slightly incomplete. A row with no `kind` is the one thing
        rejected: it would aggregate under "unknown" and silently pad a bucket nobody wrote.
        """
        if not isinstance(data, dict):
            return None
        kind = str(data.get("kind", "") or "")
        if not kind:
            return None
        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            created_ts = float(data.get("created_ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            created_ts = 0.0
        return cls(
            kind=kind,
            entity=str(data.get("entity", "") or ""),
            arm=str(data.get("arm", "") or ""),
            confidence=confidence,
            used=bool(data.get("used")),
            query=str(data.get("query", "") or ""),
            session=str(data.get("session", "") or ""),
            created_ts=created_ts,
        )


class SurfacingEventStore:
    """The `surfacing_events` log in learning.db. Shares the file with the staging log."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        from personalclaw.learning.staging import StagingStore

        self._staging = StagingStore(base_dir)
        self._lock = threading.RLock()
        self._bootstrapped = False

    @property
    def path(self) -> Path:
        return self._staging.path

    def close(self) -> None:
        self._staging.close()

    @staticmethod
    def bootstrap(conn: Any) -> None:
        """Make the events table in the learning log on *conn*, each event in it with its
        identity (``numbered_rows.give_identity``). Idempotent."""
        conn.executescript("""
            -- One row per candidate offered per turn. Append-only:
            -- `used` is written once, by the same call that observed the surfacing, because
            -- the writer knows both facts at the same moment. An UPDATE path would exist
            -- only to let a later caller assert a use it did not observe.
            CREATE TABLE IF NOT EXISTS surfacing_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                kind        TEXT NOT NULL,
                entity      TEXT NOT NULL DEFAULT '',
                arm         TEXT NOT NULL DEFAULT '',
                confidence  REAL NOT NULL DEFAULT 0.0,
                used        INTEGER NOT NULL DEFAULT 0,
                query       TEXT NOT NULL DEFAULT '',
                session     TEXT NOT NULL DEFAULT '',
                created_ts  REAL NOT NULL,
                uid         TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_surfacing_ts ON surfacing_events(created_ts);
            CREATE INDEX IF NOT EXISTS idx_surfacing_kind ON surfacing_events(kind, arm);
            """)
        numbered_rows.give_identity(
            conn,
            "surfacing_events",
            covers=(
                "kind",
                "entity",
                "arm",
                "confidence",
                "used",
                "query",
                "session",
                "created_ts",
            ),
        )

    def _ensure(self) -> None:
        if self._bootstrapped:
            return
        with self._staging._cursor() as cur:
            self.bootstrap(cur.connection)
        self._bootstrapped = True

    # ── Writing ──

    def record(self, events: Iterable[SurfacingEvent], *, now: float | None = None) -> int:
        """Append events. Returns rows written.

        Batched per turn rather than per candidate: one turn's offers are one observation of the
        surfacing engine, and a transaction per candidate would put a dozen commits on the turn's
        critical path to record the same thing.

        An event with no `kind` is dropped rather than stored under a placeholder — see
        `from_dict`. Returning the count is what lets the caller's test assert a row was written
        WITHOUT reaching past the store, which is the difference between testing the writer and
        testing sqlite.

        Nothing is written, and 0 returned, for work that may change none of your memory (the
        module docstring): its events would keep its words.
        """
        from personalclaw import memory_writes

        if memory_writes.changes_no_memory():
            return 0
        stamp = time.time() if now is None else now
        rows = [
            (
                e.kind,
                e.entity,
                e.arm,
                float(e.confidence),
                1 if e.used else 0,
                e.query,
                e.session,
                e.created_ts or stamp,
                numbered_rows.new_identity(),
            )
            for e in events
            if e is not None and e.kind
        ]
        if not rows:
            return 0
        self._ensure()
        with self._lock, self._staging._cursor() as cur:
            cur.executemany(
                "INSERT INTO surfacing_events "
                "(kind, entity, arm, confidence, used, query, session, created_ts, uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                rows,
            )
        return len(rows)

    # ── Reading ──

    def read(
        self,
        *,
        days: int | None = None,
        kind: str = "",
        limit: int = DEFAULT_READ_LIMIT,
        now: float | None = None,
    ) -> list[SurfacingEvent]:
        """Events, newest first. The one read path.

        One method rather than one per consumer: `per_arm_precision` takes a list precisely so
        the store is a separate concern, so a second reader shaped for a second
        aggregator would be a surface with no caller. Callers that want the aggregator's shape
        map `to_dict` over the result.

        Returns `[]` rather than raising when the table has never been written — a fresh home is
        the common case, not an error, and the callers are a panel and a background miner.
        """
        try:
            self._ensure()
        except Exception:
            logger.debug("surfacing_events unavailable", exc_info=True)
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if days is not None:
            stamp = time.time() if now is None else now
            clauses.append("created_ts >= ?")
            params.append(stamp - max(1, int(days)) * 86400)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit or DEFAULT_READ_LIMIT)))
        try:
            with self._staging._cursor() as cur:
                rows = cur.execute(
                    "SELECT kind, entity, arm, confidence, used, query, session, created_ts "
                    f"FROM surfacing_events{where} ORDER BY created_ts DESC, id DESC LIMIT ?;",
                    tuple(params),
                ).fetchall()
        except Exception:
            logger.debug("surfacing_events read failed", exc_info=True)
            return []
        out: list[SurfacingEvent] = []
        for row in rows:
            event = SurfacingEvent.from_dict(dict(row))
            if event is not None:
                out.append(event)
        return out

    # ── Retention ──

    def prune(
        self, *, retention_days: int = DEFAULT_RETENTION_DAYS, now: float | None = None
    ) -> int:
        """Drop events past the retention window. Returns rows removed.

        Unconditional, unlike `staging.prune`'s consumed-only rule: an event's value is the
        aggregate it already contributed to, and events prune at 90d precisely because the raw
        rows are not the artifact. Nothing is owed a later pass over them.
        """
        stamp = time.time() if now is None else now
        cutoff = stamp - max(1, int(retention_days)) * 86400
        try:
            self._ensure()
            with self._lock, self._staging._cursor() as cur:
                cur.execute("DELETE FROM surfacing_events WHERE created_ts < ?;", (cutoff,))
                return int(cur.rowcount or 0)
        except Exception:
            logger.debug("surfacing_events prune failed", exc_info=True)
            return 0

    def forget_sessions(self, gone: Callable[[str], bool]) -> int:
        """Remove every event filed under a session *gone* names (``gone(session)``, asked once
        for each). Returns rows removed. An event that names no session is no one chat's, and
        stays. Idempotent: a second pass finds nothing.

        Removed outright, as what such a session left in memory is
        (``VectorMemoryStore.purge_records_from``), and overwritten as it goes
        (``secure_delete``): a deleted row's words would otherwise stay in the file's free space,
        which a backup or a sync's copy of the database carries until the file is next compacted.
        The write-ahead log is folded into the file and emptied for the same reason, when no
        reader holds it longer than a moment. A log that was never written holds nothing to
        forget, and is not made by being asked.
        """
        if not self.path.exists():
            return 0
        self._ensure()
        with self._lock, self._staging._cursor() as cur:
            named = [
                row[0]
                for row in cur.execute(
                    "SELECT DISTINCT session FROM surfacing_events WHERE session != '';"
                ).fetchall()
            ]
            doomed = [session for session in named if gone(session)]
            if not doomed:
                return 0
            cur.execute("PRAGMA secure_delete = ON;")
            removed = 0
            for start in range(0, len(doomed), 400):
                batch = doomed[start : start + 400]
                marks = ",".join("?" * len(batch))
                cur.execute(f"DELETE FROM surfacing_events WHERE session IN ({marks});", batch)
                removed += int(cur.rowcount or 0)
        with self._lock, self._staging._cursor() as cur:
            # Waits a quarter of a second at most for a reader to finish: a chat is deleted on the
            # gateway's loop. A checkpoint a reader holds off folds the rest of the log in later.
            waited = cur.execute("PRAGMA busy_timeout;").fetchone()[0]
            cur.execute("PRAGMA busy_timeout = 250;")
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            cur.execute(f"PRAGMA busy_timeout = {int(waited)};")
        return removed


def forget_what_restricted_sessions_left() -> int:
    """Remove the events an earlier version recorded for a turn of an Incognito or Temporary chat:
    those filed under a session whose transcript records it so
    (``memory_writes.transcript_keeps_nothing``, the record the start's sweep of memory acts on).
    Returns how many went.

    Run when the gateway starts, before anything reads the log or exports it again; idempotent.
    An event that names no session, or one no transcript records as such a chat's, cannot be
    traced to one and is left: it ages out with the rest (:data:`DEFAULT_RETENTION_DAYS`).
    """
    from personalclaw import memory_writes

    store = SurfacingEventStore()
    try:
        removed = store.forget_sessions(memory_writes.transcript_keeps_nothing)
    except Exception:  # noqa: BLE001 - a failed sweep must not stop the gateway; it runs again
        logger.warning(
            "Could not remove what Incognito or Temporary chats left in the learning log",
            exc_info=True,
        )
        return 0
    finally:
        store.close()
    if removed:
        logger.warning(
            "Removed %d learning log row(s) that kept an Incognito or Temporary chat's words",
            removed,
        )
    return removed
