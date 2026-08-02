"""The hardened curator — ages and reviews learned entities, and can always be undone.

`skills/curator.py` did this for one entity type with one invariant list. Generalizing
it means adding the guards that a single-type, aging-only pass never needed, because
the failure modes of an automated janitor are not the failure modes of an automated
author:

**Demote, never delete.** Every mutation is WAL-logged with its before/after, so any
pass can be undone. The maximum destructive action is archival, and archival is
reversible. This extends the reversible-event WAL that `vector_memory` already uses.

**Provenance scoping.** Every entity carries `source_type` (user | agent | run). The
curator may age, consolidate, or patch **agent-created entities only**. Deleting what
the user wrote is not curation; it is data loss with a tidy justification.

**Over-deletion refusal.** A pass that would cut more than half of a non-trivial set
is refused outright rather than executed. The realistic cause of such a pass is a bug
in the pass — a mis-parsed timestamp, an empty usage table read as "nothing is used" —
and the honest response to "I am about to delete most of your library" is to stop.

**Bounded batches, oldest-audited first.** ~8 entities per tick. An unbounded curator
tick is a latency spike attached to whatever cadence hosts it, and the work is never
urgent enough to justify one.

**Decayed-but-stable becomes a REVIEW proposal,** not a silent archival. Something the
system is confident about that nobody uses is a question for the user, and the
confidence is itself the evidence that they may want it back.

**Scheduling, corrected.** `skills/curator.run_aging` had no verified scheduled caller
— it existed and nothing ran it. This is wired into the consolidation maintenance
cadence in `history.py`, which is a real, verified tick. No new scheduler: a new
daemon for janitorial work is a new thing to monitor and a new thing to fail silently.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from personalclaw.learning.decay import DecayVerdict, active_days_between, evaluate

logger = logging.getLogger(__name__)

#: Entities examined per tick, oldest-audited first. Small on purpose.
BATCH_SIZE = 8

#: A pass that would cut more than this fraction of a set is refused. The likely
#: cause of such a pass is a bug in the pass itself.
MAX_CUT_FRACTION = 0.5
#: …but only once the set is big enough for a fraction to mean anything. Cutting 1
#: of 2 is not a red flag; cutting 30 of 40 is.
MIN_SET_FOR_REFUSAL = 8

#: Lifecycle states, matching the ladder `skills/curator.py` already established so a
#: skill's meaning doesn't change under it.
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"

#: The optimizer detectors, each with a token-saving estimate so proposals of
#: different kinds can be compared in one currency.
DETECTOR_KINDS = (
    "compress_summary",
    "downgrade_detail",
    "promote_importance",
    "merge_candidates",
    "archive_unused",
)


@dataclass
class Mutation:
    """One reversible curator action, as journaled."""

    operation: str
    kind: str
    entity: str
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    at: str = ""
    undone_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "kind": self.kind,
            "entity": self.entity,
            "before": self.before,
            "after": self.after,
            "at": self.at,
            "undone_at": self.undone_at,
        }


@dataclass
class CuratorReport:
    """What one pass did — or would have done, in dry-run."""

    mode: str = ""
    scanned: int = 0
    to_stale: list[str] = field(default_factory=list)
    to_archived: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    review_proposals: list[str] = field(default_factory=list)
    skipped_pinned: list[str] = field(default_factory=list)
    skipped_user: list[str] = field(default_factory=list)
    refused: str = ""
    dry_run: bool = False
    mutations: list[Mutation] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.to_stale) + len(self.to_archived) + len(self.reactivated)

    def summary(self) -> str:
        parts = [f"curator[{self.mode or 'all'}]: scanned {self.scanned}"]
        if self.refused:
            return f"{parts[0]} — REFUSED: {self.refused}"
        for label, items in (
            ("stale", self.to_stale),
            ("archived", self.to_archived),
            ("reactivated", self.reactivated),
            ("review", self.review_proposals),
        ):
            if items:
                parts.append(f"{label} {len(items)}")
        if self.skipped_user:
            parts.append(f"skipped-user {len(self.skipped_user)}")
        if self.dry_run:
            parts.append("(dry run)")
        return ", ".join(parts)


class MutationLog:
    """The undo journal. Append-only, in learning.db.

    Undo is what makes an automated janitor acceptable. Without it, every curator
    heuristic has to be right the first time on data the user cannot get back.
    """

    def __init__(self, base_dir: Any = None) -> None:
        from personalclaw.learning.staging import StagingStore

        self._staging = StagingStore(base_dir)
        self._bootstrapped = False

    def _ensure(self) -> None:
        if self._bootstrapped:
            return
        with self._staging._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS curator_mutations (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    kind      TEXT NOT NULL,
                    entity    TEXT NOT NULL,
                    before    TEXT NOT NULL DEFAULT '{}',
                    after     TEXT NOT NULL DEFAULT '{}',
                    at        TEXT NOT NULL,
                    undone_at TEXT NOT NULL DEFAULT ''
                );
                """)
        self._bootstrapped = True

    def append(self, mutation: Mutation) -> int:
        self._ensure()
        with self._staging._cursor() as cur:
            cur.execute(
                "INSERT INTO curator_mutations (operation, kind, entity, before, after, at) "
                "VALUES (?, ?, ?, ?, ?, ?);",
                (
                    mutation.operation,
                    mutation.kind,
                    mutation.entity,
                    json.dumps(mutation.before),
                    json.dumps(mutation.after),
                    mutation.at or _now(),
                ),
            )
            return int(cur.lastrowid or 0)

    def pending_undo(self, limit: int = 100) -> list[tuple[int, Mutation]]:
        """Mutations that have not been undone, newest first."""
        self._ensure()
        with self._staging._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM curator_mutations WHERE undone_at = '' " "ORDER BY id DESC LIMIT ?;",
                (int(limit),),
            ).fetchall()
        return [(int(r["id"]), _row_to_mutation(r)) for r in rows]

    def mark_undone(self, mutation_id: int) -> bool:
        self._ensure()
        with self._staging._cursor() as cur:
            cur.execute(
                "UPDATE curator_mutations SET undone_at = ? WHERE id = ? AND undone_at = '';",
                (_now(), int(mutation_id)),
            )
            return bool(cur.rowcount)

    def changelog(self, limit: int = 200) -> list[dict[str, Any]]:
        """The dated, append-only record the curator UI renders."""
        self._ensure()
        with self._staging._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM curator_mutations ORDER BY id DESC LIMIT ?;", (int(limit),)
            ).fetchall()
        return [_row_to_mutation(r).to_dict() for r in rows]

    def close(self) -> None:
        self._staging.close()


def _row_to_mutation(row: Any) -> Mutation:
    def _load(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    return Mutation(
        operation=str(row["operation"]),
        kind=str(row["kind"]),
        entity=str(row["entity"]),
        before=_load(row["before"]),
        after=_load(row["after"]),
        at=str(row["at"]),
        undone_at=str(row["undone_at"] or ""),
    )


# ── The aging pass ──


@dataclass
class Candidate:
    """One entity presented to the curator, with everything it needs to judge.

    A plain value object rather than a live handle: the curator decides, and the
    caller applies. That split is what lets the aging pass be tested without a
    skills loader, a template store, and a database.
    """

    kind: str
    entity: str
    state: str = STATE_ACTIVE
    last_used_at: str = ""
    created_at: str = ""
    importance: float = 0.0
    stability: float = 0.0
    pinned: bool = False
    source_type: str = "agent"
    linked_neighbors: int = 0
    audited_at: str = ""


def target_state(verdict: DecayVerdict, current: str) -> str:
    """Where this entity should sit on the ladder.

    Kept as a pure mapping from the kernel's verdict so the ladder and the curve
    cannot disagree — the previous code had a day-threshold ladder with no curve at
    all, which meant "stale" and "decayed" were unrelated notions.
    """
    if verdict.prune:
        return STATE_ARCHIVED
    if verdict.review:
        return current  # a review proposal is raised instead of a state change
    if verdict.strength < 0.5:
        return STATE_STALE
    return STATE_ACTIVE


def run_aging(
    candidates: list[Candidate],
    *,
    active_dates: list[str] | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    mode: str = "",
    batch_size: int = BATCH_SIZE,
    log: MutationLog | None = None,
) -> CuratorReport:
    """Age a bounded batch of candidates. Deterministic, reversible, no LLM.

    Returns the report; the CALLER applies the state changes it names. The curator
    does not reach into a skills loader or a template store — it decides, and the
    thing that owns the entity writes.
    """
    now = now or datetime.now(timezone.utc)
    report = CuratorReport(mode=mode, dry_run=dry_run)
    if mode:
        candidates = [c for c in candidates if c.kind == mode]

    # Oldest-audited first, so a bounded batch still covers the whole library over
    # successive ticks instead of re-examining the same head every time.
    ordered = sorted(candidates, key=lambda c: (c.audited_at or "", c.entity))
    batch = ordered[: max(1, batch_size)]

    proposed_cuts: list[Candidate] = []
    verdicts: dict[str, DecayVerdict] = {}

    for cand in batch:
        report.scanned += 1
        if cand.pinned:
            report.skipped_pinned.append(cand.entity)
            continue
        if cand.source_type == "user":
            # Archive-only for user content: exempt from auto-dedup and eviction.
            report.skipped_user.append(cand.entity)
            continue

        idle = active_days_between(
            active_dates or [],
            cand.last_used_at or cand.created_at,
            now.isoformat(),
        )
        verdict = evaluate(
            kind=cand.kind,
            active_days_since_use=idle,
            importance=cand.importance,
            stability=cand.stability,
            pinned=cand.pinned,
            source_type=cand.source_type,
            linked_neighbors=cand.linked_neighbors,
        )
        verdicts[cand.entity] = verdict
        if verdict.review:
            report.review_proposals.append(cand.entity)
            continue
        target = target_state(verdict, cand.state)
        if target == cand.state:
            continue
        if target == STATE_ARCHIVED:
            proposed_cuts.append(cand)
        elif target == STATE_STALE:
            report.to_stale.append(cand.entity)
        elif target == STATE_ACTIVE:
            report.reactivated.append(cand.entity)

    # Over-deletion refusal, evaluated against the WHOLE set rather than the batch:
    # eight archivals out of eight is normal for a small batch of a large library,
    # and refusing that would make the curator unable to work at all.
    eligible = [c for c in candidates if not c.pinned and c.source_type != "user"]
    if (
        len(eligible) >= MIN_SET_FOR_REFUSAL
        and len(proposed_cuts) > len(eligible) * MAX_CUT_FRACTION
    ):
        report.refused = (
            f"would archive {len(proposed_cuts)} of {len(eligible)} eligible entities "
            f"(>{MAX_CUT_FRACTION:.0%}) — refusing; the likely cause is a bug in the pass"
        )
        logger.warning(report.summary())
        return report

    report.to_archived = [c.entity for c in proposed_cuts]

    if not dry_run:
        journal = log or MutationLog()
        try:
            for entity in report.to_stale:
                journal.append(_mutation("age", entity, batch, STATE_STALE, verdicts))
            for entity in report.to_archived:
                journal.append(_mutation("archive", entity, batch, STATE_ARCHIVED, verdicts))
            for entity in report.reactivated:
                journal.append(_mutation("reactivate", entity, batch, STATE_ACTIVE, verdicts))
        finally:
            if log is None:
                journal.close()

    if report.changed or report.review_proposals:
        logger.info(report.summary())
    return report


def _mutation(
    operation: str,
    entity: str,
    batch: list[Candidate],
    target: str,
    verdicts: dict[str, DecayVerdict],
) -> Mutation:
    cand = next((c for c in batch if c.entity == entity), None)
    verdict = verdicts.get(entity)
    return Mutation(
        operation=operation,
        kind=cand.kind if cand else "",
        entity=entity,
        before={"state": cand.state if cand else ""},
        after={
            "state": target,
            # `is not None`, NOT truthiness: DecayVerdict.__bool__ is False for a
            # pruned or flagged entity, so `if verdict` asked "is this healthy?"
            # when the question was "did I get a verdict?" — and every archival
            # journaled `strength: None`, losing the evidence for exactly the
            # mutations most likely to need undoing.
            "strength": round(verdict.strength, 4) if verdict is not None else None,
            "reason": verdict.reason if verdict is not None else "",
        },
        at=_now(),
    )


# ── Review proposals ──


def file_review_proposals(report: CuratorReport, *, dry_run: bool = False) -> int:
    """Turn decayed-but-stable findings into REVIEW proposals. Returns how many filed.

    Deliberately routed through the shared queue rather than acted on: this is the
    "the system is confident about something nobody uses" case, and the resolution is
    a user decision, not a curator one.
    """
    if dry_run or not report.review_proposals:
        return 0
    from personalclaw.learning.proposals import Kind, enqueue

    filed = 0
    for entity in report.review_proposals:
        verdict, prop = enqueue(
            kind=Kind.RETIREMENT.value,
            title=f"Review {entity} — confident but unused",
            body=(
                f"{entity} has decayed to a low strength while remaining highly stable: "
                "the system is confident about it, but nothing has used it. Keep it, pin "
                "it, or retire it."
            ),
            target=entity,
            provenance="inferred",
            source_cadence="curator",
            occurrences=1,
            min_evidence=1,
        )
        if prop is not None:
            filed += 1
    return filed


# ── Optimizer detectors ──


@dataclass
class Detection:
    """One optimizer finding, with a comparable saving estimate."""

    detector: str
    kind: str
    entity: str
    rationale: str
    estimated_token_saving: int = 0
    partner: str = ""


def detect(candidates: list[Candidate], *, sizes: dict[str, int] | None = None) -> list[Detection]:
    """Run the detector battery. Pure — returns findings, changes nothing.

    ``estimated_token_saving`` is the point of the battery: it makes a compression
    finding and an archival finding comparable, so the queue can be ordered by what
    it actually buys rather than by which detector happened to run first.
    """
    sizes = sizes or {}
    out: list[Detection] = []
    for cand in candidates:
        size = sizes.get(cand.entity, 0)
        if size > 500:
            out.append(
                Detection(
                    detector="compress_summary",
                    kind=cand.kind,
                    entity=cand.entity,
                    rationale=f"{size} tokens — summarizable",
                    estimated_token_saving=int(size * 0.6),
                )
            )
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
