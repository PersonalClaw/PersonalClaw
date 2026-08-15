"""The outcome resolver — every open bet gets graded once its horizon elapses.

LEARNING-FLYWHEEL §3.3 (LEARN-R18), generalized by PP-9. A producer journals a `pending_outcome`
at BET time — before the answer is knowable — recording what it did (`subject`), the `metric` that
will reveal whether it landed, where that metric is read from (`metric_source`), the `horizon_secs`
after which it is meaningful, and the `baseline` to beat. This module is the other half: a
background one-shot on the curator tick that, for every open question whose horizon has passed,
reads ground truth, scores it against the baseline, journals an `outcome_resolved` (closing the
question), and — for the producers whose bets are lessons — files a graded PROPOSAL citing the
measured figure and the run.

The facility, its producer vocabulary and its two resolutions live in
:mod:`personalclaw.ledger.outcomes`; this module is one resolver over all of them, not one
resolver per producer. Adding a producer means opening questions, not editing this file.

**Two metric sources, two availability rules.** A memory-sourced metric (a decision's ground truth)
needs a live vector store; with none there is nothing to read, so those questions stay OPEN rather
than resolving as inconclusive — "nobody could look yet" is a different fact from "the metric is
unreadable". A ledger-sourced metric (an escalation's answer) is an event the producer wrote
itself, so it resolves on any box.

**Propose, never install.** A graded lesson is a `lesson_batch` proposal through the shared
human-gated queue — a measured outcome is stronger evidence than a hunch, but it still does not get
to write a standing instruction without a person accepting it. Only the producers whose bet IS a
lesson propose (see `outcomes.PRODUCER_*` and `_PROPOSING_PRODUCERS`): an artifact nobody read is a
fact for `PP-10`'s dormancy sweep to interpret, not a lesson to file on the spot, and filing one
per publish would nag the user with something they cannot act on.

**Idempotent.** Each `outcome_resolved` cites the `pending_event_id` of the question it closes, so
a second tick reads the resolution, sees the question is answered, and skips it — the subtraction
happens once, in `outcomes.open_questions`. A question whose metric cannot be read after the
horizon resolves as "inconclusive": a real closure that decays faster than a measured one (see
`outcomes.DECAY_PROFILE`), so a permanently unreadable metric neither re-resolves forever nor
masquerades as a confirmed outcome.

The `pending_outcome` events themselves are ledger records, and the ledger is append-only by
contract (never rewritten), so an open question is inherently exempt from the lesson/memory
eviction the curator runs elsewhere — it survives until its horizon, however far out.
"""

from __future__ import annotations

import calendar
import json
import logging
import time
from typing import Any

from personalclaw.ledger import outcomes

logger = logging.getLogger(__name__)

#: How many recent runs one tick scans for open questions. Bounded so the resolver is O(1) per
#: tick regardless of how many runs exist; older unresolved questions surface on later ticks.
_MAX_RUNS = 200

#: Journal timestamp format (`personalclaw.ledger.now()`), UTC. Parsed back to an epoch to test
#: the horizon.
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

#: Which producers' resolutions become a graded lesson proposal. A DECISION's outcome is a lesson
#: about how to decide; the others are facts their own consumers interpret (`PP-10` for publish,
#: the attention surfaces for escalations). Keeping this list short is what stops the resolver
#: turning every generalization into a new source of queue noise.
_PROPOSING_PRODUCERS = frozenset({outcomes.PRODUCER_DECISION})


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


def _lesson(run: Any, question: outcomes.OutcomeQuestion, resolved: dict[str, Any]) -> str:
    """The proposal body: what was bet, the measured figure vs baseline, and which run — so a
    reviewer can trace the claim back to its ledger."""
    if resolved["resolution"] == outcomes.MEASURED:
        return (
            f"Run `{run.id}` decided {question.subject}. After its horizon the metric "
            f"`{question.metric}` measured {resolved['measured']:.4g} against a baseline of "
            f"{question.baseline:.4g} (benchmark-relative score {resolved['score']:+.2f}). Weight "
            f"future runs of `{run.workflow_name or 'this template'}` toward decisions this "
            f"outcome supports."
        )
    return (
        f"Run `{run.id}` decided {question.subject}, but its metric `{question.metric}` could not "
        f"be measured after the horizon, so the decision's outcome is inconclusive. Treat this bet "
        f"as unconfirmed rather than validated."
    )


def resolve(service: Any, *, now: float | None = None, max_runs: int = _MAX_RUNS) -> dict[str, int]:
    """Grade every open question whose horizon has elapsed. Returns a report.

    Best-effort per run: one run's unreadable ledger never blocks the others. Memory-sourced
    questions are skipped (left open) when no vector store is available; ledger-sourced ones
    resolve regardless.

    Report keys: ``resolved`` (questions closed with a measurement), ``inconclusive`` (closed
    because the metric could not be read), ``pending`` (still inside their horizon, or waiting on
    a metric source that is not available), ``proposed`` (lesson proposals filed).
    """
    report = {"resolved": 0, "inconclusive": 0, "pending": 0, "proposed": 0}
    if service is None:
        return report
    has_vector = bool(getattr(service, "has_vector", False))

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
            events = journal_mod.ledger(run.id)
        except Exception:
            logger.debug("outcome-resolver: ledger read failed for %s", run.id, exc_info=True)
            continue
        if not events:
            continue

        open_questions = outcomes.open_questions(events)
        if not open_questions:
            continue

        journal = None  # built lazily on the first resolution so a read-only pass writes nothing
        for question in open_questions:
            if not outcomes.is_due(question, opened_epoch=_epoch(question.ts), now=now):
                report["pending"] += 1
                continue
            if question.metric_source == outcomes.SOURCE_MEMORY and not has_vector:
                # Nothing can read this yet. Leaving it OPEN is the honest answer; closing it as
                # inconclusive would spend the question on a missing dependency.
                report["pending"] += 1
                continue

            if question.metric_source == outcomes.SOURCE_LEDGER:
                measured = outcomes.measure_from_events(question, events)
            else:
                measured = _read_metric(service, question.metric)
            resolution = outcomes.resolution_for(measured)
            score = 0.0 if measured is None else outcomes.score(measured, question.baseline)

            if journal is None:
                journal = journal_mod.Journal(run.id)
            resolved = journal.outcome_resolved(
                str(question.record.get("instance_path") or ""),
                str(question.record.get("node_id") or ""),
                pending_event_id=question.event_id,
                producer=question.producer,
                subject=question.subject,
                metric=question.metric,
                baseline=question.baseline,
                measured=measured,
                score=score,
                resolution=resolution,
            )
            # The resolution is now in `events` too, so a second pass in this same tick — and the
            # next tick's `open_questions` — subtracts it instead of re-resolving.
            events.append(resolved)
            if resolution == outcomes.MEASURED:
                report["resolved"] += 1
            else:
                report["inconclusive"] += 1

            if question.producer not in _PROPOSING_PRODUCERS:
                continue
            _verdict, proposal = proposals.enqueue(
                kind=proposals.Kind.LESSON_BATCH.value,
                title=f"Outcome of {run.workflow_name or 'run'}: {question.subject}"[:120],
                body=_lesson(run, question, resolved),
                target=f"outcome.{run.id}.{question.event_id}",
                provenance="inferred",
                source_cadence="run_end",
                run_id=str(run.id),
                # Both derived from the resolution in ONE place (`ledger.outcomes`), so the
                # measured/inconclusive distinction cannot mean one thing here and another in the
                # decay kernel: `correlated`/|score| for a measurement, `anecdotal`/0.1 for a bet
                # nobody could grade.
                evidence_strength=outcomes.evidence_strength(resolution),
                confidence=outcomes.confidence(resolution, score),
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
