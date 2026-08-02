"""One usage store, with per-entity semantics — what got surfaced, and what got used.

Usage lived in `<skills_dir>/.usage.json`, a JSON sidecar. That was a skills-ism:
it works for one entity type in one directory, and templates/lessons have neither.
This moves it into `learning.db` beside the staging log.

**Per-entity semantics, because a naive shared store degenerates.** The recorded
events differ by what the entity's lifecycle actually depends on:

| Entity | Events | Why |
|---|---|---|
| skill | surfaced, loaded | surfacing is cheap, loading is the real signal |
| template | surfaced, run, outcome | one that runs and FAILS is worse than one never run |
| lesson | **EXEMPT** | see below |

**Lessons are deliberately exempt.** They render as always-on, caps-bounded blocks,
so "surfaced" degenerates to "a session happened" — a counter that only measures
how much the user talks. Their lifecycle signal is the contradiction judge, capsule
replay, and explicit forget. Recording usage for them would produce a number that
looks like evidence and means nothing, which is worse than having no number.

**Reinforcement flushes once per session, not per retrieval.** Ten retrievals in
one turn is one act of attention. Counting each one inflates heat until every
comparison against it is distorted — and heat feeds eviction decisions, so the
inflation ends up deleting the wrong things.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Entity kinds this store tracks. Lessons are absent BY DESIGN — see the module
#: docstring. A caller that tries to record one gets a no-op, not an exception:
#: the exemption is a policy, and a policy that crashes callers gets worked around.
TRACKED_KINDS = ("skill", "template")

#: Events per kind. Recording an event a kind doesn't define is a no-op with a debug
#: log — the alternative (accepting anything) makes the table meaningless.
KIND_EVENTS: dict[str, tuple[str, ...]] = {
    "skill": ("surfaced", "loaded"),
    "template": ("surfaced", "run", "run_success", "run_failure"),
}


@dataclass
class UsageRecord:
    """One entity's accumulated usage."""

    kind: str
    entity: str
    surfaced: int = 0
    used: int = 0
    successes: int = 0
    failures: int = 0
    first_seen_at: str = ""
    last_used_at: str = ""
    last_surfaced_at: str = ""
    source_type: str = "agent"
    pinned: bool = False
    contexts: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float | None:
        """Outcome rate, or None when nothing has run — NOT 0.0.

        The distinction matters: 0.0 means "ran and always failed", None means "never
        ran". Collapsing them would make an unused template look like a broken one
        and get it archived for the wrong reason.
        """
        total = self.successes + self.failures
        return (self.successes / total) if total else None

    @property
    def context_diversity(self) -> int:
        """How many distinct contexts this was used in — the multi-gate evidence
        that separates "genuinely general" from "used twice in one session"."""
        return len(self.contexts)


class UsageStore:
    """Usage counters in learning.db. Shares the file with the staging log."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        from personalclaw.learning.staging import StagingStore

        self._staging = StagingStore(base_dir)
        self._lock = threading.RLock()
        #: Pending reinforcements, flushed once per session rather than per
        #: retrieval. Keyed (kind, entity, event) so a burst collapses to one.
        self._pending: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._bootstrapped = False

    @property
    def path(self) -> Path:
        return self._staging.path

    def close(self) -> None:
        self._staging.close()

    def _ensure(self) -> None:
        if self._bootstrapped:
            return
        with self._staging._cursor() as cur:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS usage (
                    kind              TEXT NOT NULL,
                    entity            TEXT NOT NULL,
                    surfaced          INTEGER NOT NULL DEFAULT 0,
                    used              INTEGER NOT NULL DEFAULT 0,
                    successes         INTEGER NOT NULL DEFAULT 0,
                    failures          INTEGER NOT NULL DEFAULT 0,
                    first_seen_at     TEXT NOT NULL DEFAULT '',
                    last_used_at      TEXT NOT NULL DEFAULT '',
                    last_surfaced_at  TEXT NOT NULL DEFAULT '',
                    source_type       TEXT NOT NULL DEFAULT 'agent',
                    pinned            INTEGER NOT NULL DEFAULT 0,
                    contexts          TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (kind, entity)
                );
                CREATE TABLE IF NOT EXISTS active_days (
                    day TEXT PRIMARY KEY
                );
                """)
        self._bootstrapped = True

    # ── Recording ──

    def record(
        self,
        *,
        kind: str,
        entity: str,
        event: str,
        context: str = "",
        source_type: str = "",
        immediate: bool = False,
    ) -> bool:
        """Buffer one usage event. Returns True if it was accepted.

        Buffered rather than written: see the module docstring on flush cadence.
        ``immediate=True`` is for a genuinely one-off event (a run outcome), where
        there is no burst to collapse and losing it to an un-flushed buffer would
        lose real information.
        """
        if kind not in TRACKED_KINDS:
            # Lessons land here. A no-op, deliberately — see the module docstring.
            logger.debug("usage not tracked for kind %r (by design)", kind)
            return False
        if event not in KIND_EVENTS.get(kind, ()):
            logger.debug("event %r is not defined for kind %r", event, kind)
            return False
        if not entity:
            return False

        key = (kind, entity, event)
        with self._lock:
            slot = self._pending.setdefault(
                key, {"count": 0, "contexts": set(), "source_type": source_type, "ts": 0.0}
            )
            # Reinforcement damping: a repeat inside the window counts half, so a
            # retrieval burst can't inflate the heat that drives eviction.
            from personalclaw.learning.decay import reinforcement_weight

            now = time.time()
            weight = reinforcement_weight(now - slot["ts"]) if slot["ts"] else 1.0
            slot["count"] += weight
            slot["ts"] = now
            if context:
                slot["contexts"].add(context)
            if source_type:
                slot["source_type"] = source_type
        if immediate:
            self.flush()
        return True

    def flush(self) -> int:
        """Write buffered events. Called once per session by the idle watchdog."""
        with self._lock:
            pending, self._pending = self._pending, {}
        if not pending:
            return 0
        self._ensure()
        now = _now()
        written = 0
        with self._staging._cursor() as cur:
            for (kind, entity, event), slot in pending.items():
                count = max(1, int(round(slot["count"])))
                row = cur.execute(
                    "SELECT contexts, first_seen_at FROM usage WHERE kind = ? AND entity = ?;",
                    (kind, entity),
                ).fetchone()
                contexts = (
                    set(filter(None, (row["contexts"] or "").split("\x1f"))) if row else set()
                )
                contexts |= set(slot["contexts"])
                first_seen = (row["first_seen_at"] if row else "") or now

                surfaced = count if event == "surfaced" else 0
                used = count if event in ("loaded", "run") else 0
                successes = count if event == "run_success" else 0
                failures = count if event == "run_failure" else 0

                cur.execute(
                    """
                    INSERT INTO usage (kind, entity, surfaced, used, successes, failures,
                                       first_seen_at, last_used_at, last_surfaced_at,
                                       source_type, contexts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(kind, entity) DO UPDATE SET
                        surfaced = surfaced + excluded.surfaced,
                        used = used + excluded.used,
                        successes = successes + excluded.successes,
                        failures = failures + excluded.failures,
                        last_used_at = CASE WHEN excluded.used > 0 OR excluded.successes > 0
                                            OR excluded.failures > 0
                                       THEN excluded.last_used_at ELSE usage.last_used_at END,
                        last_surfaced_at = CASE WHEN excluded.surfaced > 0
                                           THEN excluded.last_surfaced_at
                                           ELSE usage.last_surfaced_at END,
                        contexts = excluded.contexts;
                    """,
                    (
                        kind,
                        entity,
                        surfaced,
                        used,
                        successes,
                        failures,
                        first_seen,
                        now if (used or successes or failures) else "",
                        now if surfaced else "",
                        slot["source_type"] or "agent",
                        "\x1f".join(sorted(contexts)),
                    ),
                )
                written += 1
            cur.execute(
                "INSERT OR IGNORE INTO active_days (day) VALUES (?);",
                (now[:10],),
            )
        return written

    # ── Reading ──

    def get(self, kind: str, entity: str) -> UsageRecord | None:
        self._ensure()
        with self._staging._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM usage WHERE kind = ? AND entity = ?;", (kind, entity)
            ).fetchone()
        return _to_record(row) if row else None

    def list_kind(self, kind: str) -> list[UsageRecord]:
        self._ensure()
        with self._staging._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM usage WHERE kind = ? ORDER BY entity;", (kind,)
            ).fetchall()
        return [_to_record(r) for r in rows]

    def set_flags(
        self, kind: str, entity: str, *, pinned: bool | None = None, source_type: str = ""
    ) -> bool:
        """Set the curator-relevant flags. Creates the row if absent.

        ``source_type`` and ``pinned`` are what make the curator safe, so they must
        be settable for an entity that has never been used — otherwise a
        user-authored, never-surfaced item has no row and inherits the agent default.
        """
        self._ensure()
        with self._staging._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO usage (kind, entity, first_seen_at) VALUES (?, ?, ?);",
                (kind, entity, _now()),
            )
            if pinned is not None:
                cur.execute(
                    "UPDATE usage SET pinned = ? WHERE kind = ? AND entity = ?;",
                    (1 if pinned else 0, kind, entity),
                )
            if source_type:
                cur.execute(
                    "UPDATE usage SET source_type = ? WHERE kind = ? AND entity = ?;",
                    (source_type, kind, entity),
                )
        return True

    def active_days(self) -> list[str]:
        """Every day the user was present — the vacation-proof decay clock."""
        self._ensure()
        with self._staging._cursor() as cur:
            return [r[0] for r in cur.execute("SELECT day FROM active_days ORDER BY day;")]

    def mark_active(self, day: str = "") -> None:
        self._ensure()
        with self._staging._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO active_days (day) VALUES (?);", (day or _now()[:10],)
            )

    # ── Migration ──

    def import_skill_sidecar(self, sidecar: Path) -> int:
        """Absorb a legacy `.usage.json`. Idempotent, additive, returns rows read.

        An idempotent backfill rather than a migration file: the sidecar is read and
        its counts are merged only where this store has no row yet, so running it
        twice cannot double a counter. The sidecar is left on disk — deleting the old
        source before the new one has been verified in real use trades a recoverable
        state for an unrecoverable one.
        """
        import json

        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        self._ensure()
        imported = 0
        with self._staging._cursor() as cur:
            for name, rec in data.items():
                if not isinstance(rec, dict):
                    continue
                count = int(rec.get("count", 0) or 0)
                last = str(rec.get("last_used_at", "") or "")
                cur.execute(
                    "INSERT OR IGNORE INTO usage (kind, entity, used, last_used_at, "
                    "first_seen_at, source_type) VALUES ('skill', ?, ?, ?, ?, 'agent');",
                    (str(name), count, last, last or _now()),
                )
                imported += 1
        if imported:
            logger.info("imported %d legacy skill-usage row(s) from %s", imported, sidecar.name)
        return imported


def _to_record(row: Any) -> UsageRecord:
    return UsageRecord(
        kind=str(row["kind"]),
        entity=str(row["entity"]),
        surfaced=int(row["surfaced"] or 0),
        used=int(row["used"] or 0),
        successes=int(row["successes"] or 0),
        failures=int(row["failures"] or 0),
        first_seen_at=str(row["first_seen_at"] or ""),
        last_used_at=str(row["last_used_at"] or ""),
        last_surfaced_at=str(row["last_surfaced_at"] or ""),
        source_type=str(row["source_type"] or "agent"),
        pinned=bool(row["pinned"]),
        contexts=[c for c in str(row["contexts"] or "").split("\x1f") if c],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Heat-earned promotion ──

#: Multi-gate promotion thresholds. The bare "surfaced ≥2×" this replaces was too
#: weak: two surfacings in one session is not evidence of generality, and widening
#: an entity's scope is a trust decision the user should make from real evidence.
PROMOTE_MIN_USES = 3
PROMOTE_MIN_CONTEXTS = 2
PROMOTE_MAX_IDLE_DAYS = 30.0


def promotion_ready(record: UsageRecord, *, active_days_idle: float) -> tuple[bool, str]:
    """Should this session-scoped entity be SUGGESTED for wider scope?

    Never auto-promotes. Three gates, all required: enough uses, enough distinct
    contexts, and recent enough to still be relevant. Usage alone measures one busy
    afternoon; context diversity is what distinguishes "genuinely general" from
    "used repeatedly in one place".
    """
    if record.used < PROMOTE_MIN_USES:
        return False, f"only {record.used} use(s), need {PROMOTE_MIN_USES}"
    if record.context_diversity < PROMOTE_MIN_CONTEXTS:
        return False, f"used in {record.context_diversity} context(s), need {PROMOTE_MIN_CONTEXTS}"
    if active_days_idle > PROMOTE_MAX_IDLE_DAYS:
        return False, f"idle {active_days_idle:.0f} active days"
    if record.success_rate is not None and record.success_rate < 0.5:
        return False, f"success rate {record.success_rate:.0%}"
    return True, "multi-gate evidence met"
