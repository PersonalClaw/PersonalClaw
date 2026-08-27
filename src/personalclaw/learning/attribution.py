"""Predict-then-verify, wired: the curator grades what a human accepted (LEARN-R16 / §3.1 — S77).

`accountability.py` is the pure half — given before/after failure rates and what a change PREDICTED
it would fix, it returns the five-way verdict and decides whether a revert is owed. It was written
with zero production importers on purpose: the orchestration that reads the Run Ledger, persists a
record across the accept→observe→grade gap, and files the revert lives HERE, so accountability stays
a pure, source-scannable module (S77's `test_a_revert_is_a_PROPOSAL_never_an_application` asserts it
contains no `sqlite3`/`atomic_write`/`accept(`/`installer`).

This module is that orchestration, and it is what makes criterion 9 real. Two halves join over time:

**At accept-time — snapshot the bet.** `proposals.accept` DELETES the proposal file (only a
fingerprint-keyed `Decision` survives, with no manifest and no target), so the moment a human
accepts is the only moment the change's `predicted_fixes`, its `target`, and the failure rates
BEFORE it landed are still knowable. :func:`record_accepted_change` freezes exactly that into a
durable record. The baseline is keyed by run id, not timestamp: the two stores stamp time in
different formats, and a set difference over run ids is exact where a string compare across formats
is a bug in waiting.

**On the curator tick — grade it.** :func:`grade_accepted_changes` finds records with ≥N
post-acceptance runs of their target template, recomputes the failure rates over ONLY those new
runs, calls `accountability.attribute`, records the verdict, and — for a HARMFUL verdict — files a
revert PROPOSAL through the shared human-gated queue. It never applies anything; "mechanical" in
§3.1 means the revert appears in the queue without the user having to notice the regression, not
that it rolls back on its own, and S75's gate refuses a non-human accept regardless.

**Cluster = failure MODE, scope = the target's runs.** The join between what a proposer PREDICTED
and what the ledger MEASURED needs a shared vocabulary. Free-form prediction strings and per-run
signatures do not share one; the closed `FailureMode` enum does — a refiner says "this fixes
`schema_violation` failures" and the ledger's per-mode rate is directly comparable. Rates are scoped
to runs whose `workflow_name` equals the change's `target`, so a HARMFUL verdict means "this
template's runs got worse", not "something somewhere regressed after an unrelated accept". A target
matching no template has an empty scope and can only ever resolve INEFFECTIVE — the safe default,
never a spurious revert.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.record_ids import record_path

logger = logging.getLogger(__name__)

_DIRNAME = "attribution"

#: How many recent runs of a target one pass reads. Bounded so grading is O(1) per record no matter
#: the ledger size; older runs fall out of the window the way `outcome_resolver` bounds its scan.
_MAX_RUNS = 200

#: How many unresolved records one curator tick grades. A tick that graded hundreds would stall the
#: consolidation pass it rides on; the rest surface on the next tick.
_MAX_RECORDS_PER_TICK = 50

#: Pending-record cap. Beyond it the oldest unresolved records expire — a change whose target never
#: runs again would otherwise pend forever, and an unbounded store is the disk-growth bug §2.3 warns
#: about one module over.
_MAX_PENDING = 200

#: How many RESOLVED records to keep as verdict history (the proposer-trust corpus). They are never
#: re-graded, so an unbounded pile is pure growth, but the trust signal needs a window of them.
_RESOLVED_KEEP = 200


@dataclass
class AcceptedChange:
    """The frozen record of one accepted change — the bet, then the graded outcome.

    Everything above ``verdict`` is written once at accept-time and never mutated; everything from
    ``verdict`` down is filled by the grading pass. `resolved` flips true only when the verdict is
    something other than PENDING, so a re-run tick reads it, sees it is answered, and skips it — the
    same idempotency `outcome_resolver` gets from its `outcome_resolved` marker.
    """

    id: str
    target: str
    source: str
    kind: str
    predicted_fixes: list[str] = field(default_factory=list)
    before: dict[str, float] = field(default_factory=dict)
    baseline_run_ids: list[str] = field(default_factory=list)
    accepted_at: str = ""
    verdict: str = "PENDING"
    resolved: bool = False
    graded_at: str = ""
    after: dict[str, float] = field(default_factory=dict)
    runs_after: int = 0
    reason: str = ""
    revert_proposal_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── storage (dynamic config dir, exactly like proposals.py, so a test repointing it is honored) ──


def _dir() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / "learning" / _DIRNAME


def _path(record_id: object) -> Path:
    """The ONE expression turning an attribution-record id into a file in this store.

    Same class as #459's proven learning/skills instances — this store is its sibling and
    reached the same way, so it takes the same guard rather than waiting to be proven.
    """
    return record_path(_dir(), record_id, kind="record_id")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(rid: str) -> AcceptedChange | None:
    try:
        data = json.loads(_path(rid).read_text(encoding="utf-8"))
        return AcceptedChange(**data)
    except (OSError, ValueError, TypeError):
        return None


def _save(rec: AcceptedChange) -> bool:
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(_path(rec.id), json.dumps(rec.to_dict(), indent=2))
        return True
    except OSError:
        logger.debug("attribution record write failed for %s", rec.id, exc_info=True)
        return False


def _all() -> list[AcceptedChange]:
    d = _dir()
    if not d.is_dir():
        return []
    out: list[AcceptedChange] = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(AcceptedChange(**json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return out


def _attribution_enabled() -> bool:
    """The single gate. Off = neither snapshot nor grade runs, so the store never accumulates."""
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().learning
        return bool(getattr(cfg, "enabled", True)) and bool(
            getattr(cfg, "attribution_enabled", True)
        )
    except Exception:
        logger.debug("attribution config read failed; treating as enabled", exc_info=True)
        return True


# ── the shared ledger measurement ──


def _terminal_runs_for(target: str, *, limit: int = _MAX_RUNS) -> list[Any]:
    """Recent TERMINAL runs of the template a change targets.

    Scoped by `workflow_name == target`: the point of scoping is that a HARMFUL verdict names a real
    regression in THIS template's runs, not global noise after an unrelated accept. Non-terminal
    runs are excluded — a run still going has not yet failed or passed, so it is not evidence.
    """
    if not target:
        return []
    from personalclaw.workflows import store as store_mod

    try:
        runs, _total = store_mod.list_runs(workflow_name=target, limit=limit)
    except Exception:
        logger.debug("attribution: could not list runs for %r", target, exc_info=True)
        return []
    return [r for r in runs if getattr(r, "is_terminal", False)]


def _failure_rates(runs: list[Any]) -> dict[str, float]:
    """Per-failure-mode rate over `runs`: the fraction of runs exhibiting each mode.

    A rate rather than a count, because `accountability.Outcome` compares rates — five failures in
    five hundred runs is not worse than five in ten. A run counts ONCE per mode it exhibits, over
    its TERMINAL failures per node (`run_end._terminal_failures`): the retry-exhausted outcome, so a
    transient a retry recovered is not a run failure and one flapping step does not inflate its own
    mode's rate. Environment/timeout/infra modes are the world's fault, not the change's, so they
    are excluded via the same `NON_LESSON_MODES` deny-set the rest of §3.3 uses.
    """
    if not runs:
        return {}
    from personalclaw.learning.detectors import NON_LESSON_MODES, classify_failure
    from personalclaw.learning.run_end import _terminal_failures
    from personalclaw.workflows import journal as journal_mod

    counts: dict[str, int] = {}
    for run in runs:
        try:
            events = journal_mod.ledger(run.id, kinds={journal_mod.STEP_FAILED})
        except Exception:
            logger.debug("attribution: ledger read failed for %s", run.id, exc_info=True)
            continue
        modes = set()
        for rec in _terminal_failures(events).values():
            mode = classify_failure(str(rec.get("error") or ""))
            if mode and mode not in NON_LESSON_MODES:
                modes.add(mode)
        for mode in modes:
            counts[mode] = counts.get(mode, 0) + 1
    total = len(runs)
    return {mode: n / total for mode, n in counts.items()} if total else {}


# ── accept-time: freeze the bet ──


def record_accepted_change(prop: Any) -> AcceptedChange | None:
    """Snapshot an accepted change so it can be graded after its horizon. Best-effort, returns None.

    Called from `proposals.accept` AFTER a successful install. It never raises into that path — a
    failure to record a change for later grading must not fail the accept the user just made. A
    change with no `target` is not recorded: with nothing to scope failure rates to, there is
    nothing to attribute, and a record that can only ever be INEFFECTIVE is noise.
    """
    if not _attribution_enabled():
        return None
    try:
        target = str(getattr(prop, "target", "") or "")
        if not target:
            return None
        manifest = getattr(prop, "change_manifest", None) or {}
        predicted = manifest.get("predicted_fixes", []) if isinstance(manifest, dict) else []
        predicted = sorted({str(p) for p in predicted if str(p).strip()})
        rid = str(getattr(prop, "fingerprint", "") or getattr(prop, "id", "") or "")
        if not rid:
            return None
        baseline_runs = _terminal_runs_for(target)
        rec = AcceptedChange(
            id=rid,
            target=target,
            source=str(getattr(prop, "provenance", "") or "inferred"),
            kind=str(getattr(prop, "kind", "") or ""),
            predicted_fixes=predicted,
            before=_failure_rates(baseline_runs),
            baseline_run_ids=sorted(str(r.id) for r in baseline_runs),
            accepted_at=_now(),
        )
        _expire_over_cap()
        if _save(rec):
            logger.info("attribution: recorded accepted change to %s for later grading", target)
            return rec
    except Exception:
        logger.debug("attribution: record_accepted_change failed", exc_info=True)
    return None


def _expire_over_cap() -> None:
    """Drop the oldest PENDING record when the store is full — oldest is least likely to grade."""
    pending = sorted((r for r in _all() if not r.resolved), key=lambda r: r.accepted_at)
    for victim in pending[: max(0, len(pending) - _MAX_PENDING + 1)]:
        try:
            _path(victim.id).unlink()
            logger.info("attribution: store full; expired oldest pending record %s", victim.id)
        except OSError:
            logger.debug("attribution: expire failed for %s", victim.id, exc_info=True)


# ── curator tick: grade the outcome ──


def grade_accepted_changes(
    *, min_runs: int | None = None, max_records: int = _MAX_RECORDS_PER_TICK
) -> dict[str, int]:
    """Grade every recorded change with enough post-acceptance runs. Returns a report.

    Reads the Run Ledger, NOT semantic memory, so it is deliberately NOT gated on a vector store
    the way `outcome_resolver` is — the ledger is a sqlite/jsonl store on every box, and gating
    HARMFUL auto-reverts on an embedder being configured would be a silent capability loss. Inert by
    DATA instead: with no accepted-change records (the common case — nothing accepted, or the box
    unused) the scan returns immediately.

    Best-effort per record: one record's unreadable ledger never blocks the others. Idempotent: a
    resolved record is skipped, so a HARMFUL revert is filed once (and the proposal queue's own
    fingerprint memory blocks a re-file even if it were graded twice).

    Report keys: ``graded`` (records that reached a verdict this tick), ``pending`` (not enough runs
    yet), ``harmful``/``effective``/``mixed``/``ineffective``/``partial`` (verdict tallies),
    ``reverts`` (revert proposals filed).
    """
    report = {
        "graded": 0,
        "pending": 0,
        "harmful": 0,
        "effective": 0,
        "mixed": 0,
        "ineffective": 0,
        "partial": 0,
        "reverts": 0,
    }
    if not _attribution_enabled():
        return report

    from personalclaw.learning import accountability

    floor = accountability.MIN_RUNS if min_runs is None else min_runs
    unresolved = [r for r in _all() if not r.resolved]
    for rec in unresolved[:max_records]:
        try:
            terminal = _terminal_runs_for(rec.target)
            seen = set(rec.baseline_run_ids)
            after_runs = [r for r in terminal if str(r.id) not in seen]
            outcome = accountability.Outcome(
                before=dict(rec.before),
                after=_failure_rates(after_runs),
                runs_after=len(after_runs),
            )
            attr = accountability.attribute(
                predicted_fixes=rec.predicted_fixes, outcome=outcome, min_runs=floor
            )
            rec.after = dict(outcome.after)
            rec.runs_after = outcome.runs_after
            rec.verdict = attr.verdict
            rec.reason = attr.reason
            rec.graded_at = _now()

            if attr.verdict == accountability.Verdict.PENDING.value:
                report["pending"] += 1
                _save(rec)
                continue

            rec.resolved = True
            if attr.owes_revert:
                pid = _file_revert(rec, attr, [str(r.id) for r in after_runs])
                if pid:
                    rec.revert_proposal_id = pid
                    report["reverts"] += 1
            _save(rec)
            report["graded"] += 1
            tally = _TALLY.get(attr.verdict)
            if tally:
                report[tally] += 1
        except Exception:
            logger.debug("attribution: grading failed for %s", rec.id, exc_info=True)

    _prune_resolved()
    return report


#: Verdict → report counter. PARTIALLY_EFFECTIVE folds into ``partial``; the rest map by name.
_TALLY = {
    "HARMFUL": "harmful",
    "EFFECTIVE": "effective",
    "MIXED": "mixed",
    "INEFFECTIVE": "ineffective",
    "PARTIALLY_EFFECTIVE": "partial",
}


def _file_revert(rec: AcceptedChange, attr: Any, run_ids: list[str]) -> str:
    """File the revert a HARMFUL verdict owes, through the shared queue. Returns its id or "".

    The revert BODY comes from `accountability.revert_proposal`, which names the regressed clusters
    (including the ones nobody predicted) so the proposal is reviewable rather than "this made
    things worse". Filed as a RETIREMENT — a revert IS a retirement of the accepted change — with
    ``occurrences=1``/``min_evidence=1`` because a HARMFUL verdict is a single first-class signal,
    not a pattern that must recur three times before it is worth surfacing.
    """
    from personalclaw.learning import accountability, proposals

    revert = accountability.revert_proposal(target=rec.target, attribution=attr, run_ids=run_ids)
    if revert is None:
        return ""
    _verdict, proposal = proposals.enqueue(
        kind=proposals.Kind.RETIREMENT.value,
        title=revert.title,
        body=revert.body,
        target=revert.target,
        provenance="inferred",
        evidence_refs=list(revert.evidence_refs),
        evidence_strength="correlated",
        confidence=0.5,
        tags=["attribution", "revert", rec.verdict.lower()],
        occurrences=1,
        min_evidence=1,
    )
    return proposal.id if proposal is not None else ""


def _prune_resolved(keep: int = _RESOLVED_KEEP) -> int:
    """Drop the oldest resolved records beyond *keep*. Returns how many went.

    Resolved records are the proposer-trust corpus, so they outlive the change they graded — but
    they are never re-graded, so an unbounded pile is pure disk growth.
    """
    resolved = sorted((r for r in _all() if r.resolved), key=lambda r: r.graded_at or r.accepted_at)
    removed = 0
    for rec in resolved[: max(0, len(resolved) - max(0, keep))]:
        try:
            (_path(rec.id)).unlink()
            removed += 1
        except OSError:
            logger.debug("attribution: resolved prune failed for %s", rec.id, exc_info=True)
    return removed


# ── the trust readout (§3.1: "which of its own proposers to believe") ──


def verdict_history() -> list[tuple[str, str]]:
    """`(source, verdict)` for every resolved record — the input to `accountability.proposer_trust`.

    Kept here rather than in the pure module because it reads the store; feeds a per-source trust
    aggregate the observability panel (WF2LEA-9) surfaces.
    """
    return [(r.source or "unknown", r.verdict) for r in _all() if r.resolved]


def proposer_trust_report() -> list[dict[str, Any]]:
    """Per-proposer verdict aggregate, worst harm-rate first — the trust signal made reachable."""
    from personalclaw.learning import accountability

    return [t.to_dict() for t in accountability.proposer_trust(verdict_history())]
