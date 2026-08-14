"""The pending-outcome resolver — a decision's bet gets graded once its horizon elapses.

LEARNING-FLYWHEEL §3.3 (LEARN-R18). A decision-producing workflow run journals a `pending_outcome`
at DECISION time — before the answer is knowable — recording the `subject` it decided, the `metric`
that will reveal whether it was right, the `horizon_secs` after which that metric is meaningful, and
the `baseline` to beat. This module is the other half: a background one-shot on the curator tick
that, for every open question whose horizon has passed, measures ground truth, scores it relative to
the baseline, journals an `outcome_resolved` (closing the question), and files a lesson PROPOSAL
citing the measured figure and the run.

Two rules mirror the rest of the flywheel:

**Inert unless a memory service with a live vector store is injected.** Ground truth is read from
semantic memory; with no vector store there is nothing to measure and nothing to resolve, so
:func:`resolve` returns an empty report immediately.

**Propose, never install.** The graded lesson is a `lesson_batch` proposal through the shared
human-gated queue — a measured outcome is stronger evidence than a hunch, but it still does not get
to write a standing instruction without a person accepting it.

**Idempotent.** Each `outcome_resolved` cites the `pending_event_id` of the question it closes, so a
second curator tick reads the resolution, sees the question is answered, and skips it. A run whose
metric cannot be read after the horizon resolves as "inconclusive" — a real closure that decays
faster than a measured one, so a permanently unreadable metric neither re-resolves forever nor
masquerades as a confirmed outcome.

The `pending_outcome` events themselves are journal records, and the journal is append-only by
contract (never rewritten), so an open question is inherently exempt from the lesson/memory eviction
the curator runs elsewhere — it survives until its horizon, however far out.
"""

from __future__ import annotations

import calendar
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

#: How many recent runs one tick scans for open questions. Bounded so the resolver is O(1) per
#: tick regardless of how many runs exist; older unresolved questions surface on later ticks.
_MAX_RUNS = 200

#: Journal timestamp format (`personalclaw.ledger.now()`), UTC. Parsed back to an epoch to test
#: the horizon.
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _epoch(ts: str) -> float | None:
    """Journal `ts` (UTC `_TS_FMT`) → epoch seconds, or None if unparseable."""
    try:
        return float(calendar.timegm(time.strptime(str(ts), _TS_FMT)))
    except (ValueError, TypeError):
        return None


def _read_metric(service: Any, metric: str) -> float | None:
    """Read the current numeric ground truth for `metric` from semantic memory, or None.

    A metric is a semantic key whose value is a number (stored bare or under a ``value``/``score``
    field of a small payload). Anything that does not reduce to a float is "unmeasurable" — the
    resolver treats that as inconclusive rather than guessing, because a fabricated measurement is
    worse than an honest "could not tell".
    """
    try:
        row = service.get_semantic(metric)
    except Exception:
        logger.debug("outcome-resolver: metric read failed for %r", metric, exc_info=True)
        return None
    if not row:
        return None
    raw: Any = row.get("value_json", row) if isinstance(row, dict) else row
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, dict):
        for field in ("value", "score", "measured"):
            if field in raw:
                raw = raw[field]
                break
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _score(measured: float, baseline: float) -> float:
    """Benchmark-relative score in [-1, 1]: how far `measured` beat `baseline`, normalized.

    Normalized by the magnitude of both so the score is comparable across metrics of wildly
    different scale (a latency in ms and a pass-rate in [0,1] both land in [-1, 1]). Positive means
    the decision beat its baseline, negative means it lost to it, zero means it matched.
    """
    denom = abs(measured) + abs(baseline)
    if denom < 1e-9:
        return 0.0
    return max(-1.0, min(1.0, (measured - baseline) / denom))


def resolve(service: Any, *, now: float | None = None, max_runs: int = _MAX_RUNS) -> dict[str, int]:
    """Grade every open question whose horizon has elapsed. Returns a report.

    Inert unless `service` has a live vector store. Best-effort per run: one run's unreadable
    ledger never blocks the others.

    Report keys: ``resolved`` (questions closed with a measurement), ``inconclusive`` (closed
    because the metric could not be read), ``pending`` (still inside their horizon), ``proposed``
    (lesson proposals filed).
    """
    report = {"resolved": 0, "inconclusive": 0, "pending": 0, "proposed": 0}
    if service is None or not getattr(service, "has_vector", False):
        return report

    from personalclaw.learning import proposals
    from personalclaw.workflows import journal as journal_mod
    from personalclaw.workflows import store as store_mod

    now = time.time() if now is None else now
    try:
        runs, _total = store_mod.list_runs(limit=max_runs)
    except Exception:
        logger.debug("outcome-resolver: could not list runs", exc_info=True)
        return report

    for run in runs:
        try:
            events = journal_mod.ledger(
                run.id, kinds={journal_mod.PENDING_OUTCOME, journal_mod.OUTCOME_RESOLVED}
            )
        except Exception:
            logger.debug("outcome-resolver: ledger read failed for %s", run.id, exc_info=True)
            continue
        if not events:
            continue

        resolved_ids = {
            str(e.get("pending_event_id") or "")
            for e in events
            if e.get("kind") == journal_mod.OUTCOME_RESOLVED
        }
        open_questions = [
            e
            for e in events
            if e.get("kind") == journal_mod.PENDING_OUTCOME
            and str(e.get("event_id") or "") not in resolved_ids
        ]
        if not open_questions:
            continue

        journal = None  # built lazily on the first resolution so a read-only pass writes nothing
        for q in open_questions:
            opened = _epoch(q.get("ts", ""))
            horizon = float(q.get("horizon_secs", 0.0) or 0.0)
            if opened is not None and now - opened < horizon:
                report["pending"] += 1
                continue

            subject = str(q.get("subject") or "")
            metric = str(q.get("metric") or "")
            baseline = float(q.get("baseline", 0.0) or 0.0)
            measured = _read_metric(service, metric)
            if measured is None:
                resolution, score = "inconclusive", 0.0
            else:
                resolution, score = "measured", _score(measured, baseline)

            if journal is None:
                journal = journal_mod.Journal(run.id)
            journal.outcome_resolved(
                str(q.get("instance_path") or ""),
                str(q.get("node_id") or ""),
                pending_event_id=str(q.get("event_id") or ""),
                subject=subject,
                metric=metric,
                baseline=baseline,
                measured=measured,
                score=score,
                resolution=resolution,
            )
            if resolution == "measured":
                report["resolved"] += 1
            else:
                report["inconclusive"] += 1

            # File the graded lesson. A strict, short body (§3.3): what was decided, the measured
            # figure vs baseline, and which run — so a reviewer can trace the claim to its ledger.
            if resolution == "measured":
                body = (
                    f"Run `{run.id}` decided {subject}. After its horizon the metric `{metric}` "
                    f"measured {measured:.4g} against a baseline of {baseline:.4g} "
                    f"(benchmark-relative score {score:+.2f}). Weight future runs of "
                    f"`{run.workflow_name or 'this template'}` toward decisions this outcome "
                    f"supports."
                )
                confidence = min(1.0, abs(score))
                strength = "correlated"
            else:
                body = (
                    f"Run `{run.id}` decided {subject}, but its metric `{metric}` could not be "
                    f"measured after the horizon, so the decision's outcome is inconclusive. "
                    f"Treat this bet as unconfirmed rather than validated."
                )
                confidence = 0.1
                strength = "anecdotal"
            _verdict, proposal = proposals.enqueue(
                kind=proposals.Kind.LESSON_BATCH.value,
                title=f"Outcome of {run.workflow_name or 'run'}: {subject}"[:120],
                body=body,
                target=f"outcome.{run.id}.{q.get('event_id') or ''}",
                provenance="inferred",
                source_cadence="run_end",
                run_id=str(run.id),
                evidence_strength=strength,
                confidence=confidence,
                tags=["run_end", "outcome", resolution],
                # A measured outcome is a single first-class signal — the horizon elapsing IS
                # the evidence, and it happens once per decision. The ≥3 floor (for repeated
                # consolidation-mined habits) would silently SKIP every graded outcome, so it is
                # lowered to 1 here, matching the curator's own proposals.
                occurrences=1,
                min_evidence=1,
            )
            if proposal is not None:
                report["proposed"] += 1
    return report
