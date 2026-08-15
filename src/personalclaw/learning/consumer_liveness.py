"""Consumer liveness — is anybody reading what this work unit produces? (PP-10)

Every watchdog in this repo measures PRODUCER health: findings count, turn wall-time, stagnation,
consecutive errors. Nothing asks whether the output was ever READ. The field post-mortem
`PLATFORM-PRIMITIVES` §3 is drawn from is a fully autonomous pipeline that had been dead for two
months with nobody noticing, and its author's diagnosis was structural rather than tooling: nobody
owned acting on the output. Our equivalent is a monitor-kind run writing a deliverable on a cadence
into an artifact nobody opens — we detect a STALLED work unit and never a POINTLESS one.

**A PROPOSAL, never an automatic stop.** Nothing in this module can pause, cancel, retire or
otherwise touch a run, a template or a schedule. It files one `retirement` proposal through the
shared human-gated queue and stops, because *"nobody looked yet"* and *"nobody will ever look"* are
different facts and only the user knows which. A sweep that paused the work unit itself would be
confidently wrong exactly in the case that matters — the deliverable somebody reads quarterly.

**It reads `PP-9`'s outcome record; it counts nothing of its own.** A `publish:` node already
opens a `pending_outcome{producer: publish, metric: artifact.<slug>.consumed, horizon_secs: 7d,
baseline: 1.0, slug}` at publish time (`workflows/engine._open_publish_outcome`). That record IS the
consumption horizon. This module supplies the two halves `PP-9` left open:

1. :func:`measure_consumption` — the ground truth for a :data:`~personalclaw.ledger.outcomes.
   SOURCE_CONSUMPTION` question, so the ONE resolver grades a publish bet as `measured` 1.0/0.0
   instead of always `inconclusive`;
2. :func:`sweep` — the dormancy projection over those resolutions, grouped by work unit.

There is no second counter and no parallel store. The sweep is STATELESS: it re-derives its verdict
from the ledger each tick, and its idempotency comes from the proposal queue's own fingerprint plus
decision memory, not from a file it writes.

## What counts as a consumer touch

A touch is recorded by a writer that ALREADY EXISTS, on the artifact rather than in the run:

* an artifact lifecycle event of type `referenced`, `edited` or `reverted` whose actor is not
  `agent`. `referenced` is written by `record_impression` from two live surfaces — the dashboard's
  `POST /api/artifacts/{slug}/events` when the user opens the artifact, and `chat_runner` when the
  user pulls the artifact into a turn. `edited`/`reverted` are the user changing it, which is a
  stronger read than opening it;
* the slug appearing in the dashboard pin list (`workflows/pinned.py`, `entity_settings/
  pinned_artifacts.json`) — "I care about this now", the most explicit touch there is.

`created` and `iterated` are deliberately EXCLUDED: those are the producer writing its own output,
and counting them would make every work unit look consumed by itself — the exact tautology that
makes a liveness signal worthless.

**Known blind spot, recorded rather than papered over.** `NativeArtifactProvider.update()` appends a
timeline event only on the `snapshot=True` branch, and the API route defaults `snapshot` to False —
so an un-versioned content edit leaves NO trace and is invisible here. That is the existing writer's
behaviour; widening it means changing artifact event semantics for every consumer of the timeline,
which is a separate change. The consequence is conservative in the right direction: a missed touch
can only make the sweep report a work unit somebody is quietly using, which the user then rejects
once and never sees again — not the reverse.

## Why it cannot become a blanket nag

Measured on this tree: **0 of the 19 bundled templates declare a `publish:` node**, so on a fresh or
seeded install the population that can fire is EMPTY. Firing requires a user-authored work unit that
publishes, runs at least :data:`~personalclaw.ledger.outcomes.DORMANCY_CYCLES` times, and whose
every recent artifact sat untouched past its 7-day horizon. Three further guards:

* an `inconclusive` cycle (artifact deleted, timeline unreadable) yields `INSUFFICIENT`, never
  `DORMANT` — "we could not tell" does not accumulate into "nobody reads this";
* one touch anywhere in the window is `LIVE` and the sweep says nothing;
* the proposal BODY is stable per work unit (the volatile slugs live in `evidence_refs`, which is
  outside the fingerprint), so a re-file REINFORCES the existing row instead of stacking a new one,
  and a rejection lands in decision memory keyed to that same fingerprint and blocks the re-file
  outright. Not nagging is the feature.
"""

from __future__ import annotations

import calendar
import logging
import time
from datetime import datetime
from typing import Any

from personalclaw.ledger import outcomes
from personalclaw.ledger.kinds import OUTCOME_RESOLVED, PENDING_OUTCOME

logger = logging.getLogger(__name__)

#: How many recent runs one sweep scans, matching `outcome_resolver._MAX_RUNS` so the two passes see
#: the same window — a sweep with a longer reach than the resolver that grades its inputs would
#: report on cycles nothing had measured yet.
_MAX_RUNS = 200

#: Artifact timeline event types that mean a CONSUMER touched the output. `created`/`iterated` are
#: absent on purpose: they are the producer's own writes.
CONSUMER_EVENT_TYPES = frozenset({"referenced", "edited", "reverted"})

#: The actor label the engine writes for its own events. An event by this actor is never a touch,
#: whatever its type.
_PRODUCER_ACTOR = "agent"

#: Ledger `ts` format (`personalclaw.ledger.now()`), UTC.
_LEDGER_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _ledger_epoch(ts: str) -> float | None:
    """A ledger `ts` → epoch seconds, or None if unparseable."""
    try:
        return float(calendar.timegm(time.strptime(str(ts), _LEDGER_TS_FMT)))
    except (ValueError, TypeError):
        return None


def _iso_epoch(ts: str) -> float | None:
    """An artifact/pin ISO-8601 timestamp → epoch seconds, or None if unparseable.

    A different format from the ledger's on purpose — these come from `datetime.isoformat()` on the
    artifact side. Parsed rather than string-compared because the two formats do not sort against
    each other, and a naive comparison would silently read every touch as "before the publish".
    """
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return float(calendar.timegm(parsed.timetuple()))
    return parsed.timestamp()


def _touched_after(slug: str, since: float) -> bool:
    """Did a consumer touch `slug` after `since`? Reads existing writers only."""
    from personalclaw.artifacts import registry

    provider = registry.get_provider("native")
    if provider is None:
        return False
    art = provider.get(slug)
    if art is None:
        return False
    for event in getattr(art, "events", []) or []:
        if str(getattr(event, "type", "")) not in CONSUMER_EVENT_TYPES:
            continue
        if str(getattr(event, "by", "")) == _PRODUCER_ACTOR:
            continue
        at = _iso_epoch(str(getattr(event, "ts", "")))
        if at is not None and at >= since:
            return True
    return False


def _pinned_after(slug: str, since: float) -> bool:
    """Is `slug` pinned, and was it pinned after `since`? A pin is the most explicit touch."""
    from personalclaw.workflows import pinned

    try:
        pins = pinned.list_pins()
    except Exception:
        logger.debug("consumer-liveness: pin list unreadable", exc_info=True)
        return False
    for pin in pins:
        if str(pin.get("slug") or "") != slug:
            continue
        at = _iso_epoch(str(pin.get("pinned_at") or ""))
        # A pin with an unreadable timestamp still counts: the slug IS pinned right now, and
        # refusing to believe a live pin over a malformed date would report a cared-about artifact
        # as dormant.
        return at is None or at >= since
    return False


def measure_consumption(question: outcomes.OutcomeQuestion) -> float | None:
    """Ground truth for a `SOURCE_CONSUMPTION` question: was the output ever touched?

    :data:`~personalclaw.ledger.outcomes.CONSUMED` (1.0) if a consumer touched the artifact after
    the question was opened, :data:`~personalclaw.ledger.outcomes.UNCONSUMED` (0.0) if not, and
    None — which the resolver turns into `inconclusive` — when the answer is UNKNOWABLE, not no:
    the question carries no slug, its open timestamp will not parse, or the artifact no longer
    exists. That distinction is the whole atom: an unreadable cycle must never be counted as
    evidence that nobody looked.
    """
    slug = str(question.record.get("slug") or "") or outcomes.slug_from_metric(question.metric)
    if not slug:
        return None
    opened = _ledger_epoch(question.ts)
    if opened is None:
        return None
    try:
        from personalclaw.artifacts import registry

        provider = registry.get_provider("native")
        if provider is None or provider.get(slug) is None:
            return None
    except Exception:
        logger.debug("consumer-liveness: artifact read failed for %r", slug, exc_info=True)
        return None
    if _touched_after(slug, opened) or _pinned_after(slug, opened):
        return outcomes.CONSUMED
    return outcomes.UNCONSUMED


def _title(unit: str, cycles: int) -> str:
    return f"Nobody has opened `{unit}`'s output in its last {cycles} cycles"[:120]


def _body(unit: str, cycles: int, horizon_days: float) -> str:
    """The proposal body. STABLE per work unit and cycle count, so a re-file reinforces the existing
    row rather than stacking a second one — and so a rejection blocks the re-file by fingerprint.
    The volatile detail (which artifacts, which runs) travels in `evidence_refs`."""
    horizon = f"{horizon_days:.0f} day{'s' if horizon_days >= 2 else ''}"
    return (
        f"The last {cycles} cycles of `{unit}` each published an artifact that nobody opened, "
        f"pinned or edited within {horizon} of it landing. That is a work unit producing output "
        f"with no reader.\n\n"
        f"Two different facts look identical from here: nobody has looked YET, or nobody will ever "
        f"look. Only you know which. If it is the first, ignore this — the next cycle somebody "
        f"reads clears it. If it is the second, PAUSE `{unit}` while you decide, or RETIRE it.\n\n"
        f"Nothing has been stopped. This is a report, and accepting it does not itself change the "
        f"schedule — the pause or the retirement is yours to make."
    )


def _propose(unit: str, window: list[dict[str, Any]], *, horizon_days: float) -> bool:
    """File the dormancy proposal. Returns whether a row was written or reinforced."""
    from personalclaw.learning import proposals

    refs: list[str] = []
    for resolution in window:
        run_id = str(resolution.get("run_id") or "")
        if run_id and f"run:{run_id}" not in refs:
            refs.append(f"run:{run_id}")
        # The resolution carries the METRIC, not the slug — recovered through the one place that
        # knows the naming convention rather than by re-deriving the format string here.
        slug = outcomes.slug_from_metric(str(resolution.get("metric") or ""))
        if slug and f"artifact:{slug}" not in refs:
            refs.append(f"artifact:{slug}")
    verdict, proposal = proposals.enqueue(
        kind=proposals.Kind.RETIREMENT.value,
        title=_title(unit, len(window)),
        body=_body(unit, len(window), horizon_days),
        # One target per work unit, so the queue's own cascade treats a re-file as the same finding.
        target=f"consumer_liveness.{unit}",
        provenance="inferred",
        source_cadence="curator",
        evidence_refs=refs,
        # A horizon that elapsed with no touch IS a measurement, not a hunch — but the population
        # is one user's own work unit, so it argues for attention rather than for a conclusion.
        evidence_strength="correlated",
        confidence=0.5,
        tags=["consumer_liveness", outcomes.DORMANT],
        # Every cycle in the window is one independent unconsumed horizon. The queue's default
        # evidence floor is left ALONE rather than lowered: `DORMANCY_CYCLES` already clears it, and
        # a sweep that had to lower the floor to be heard would be one firing too early.
        occurrences=len(window),
    )
    if proposal is None:
        logger.debug("consumer-liveness: %s not filed for %s", verdict.value, unit)
    return proposal is not None


def sweep(*, max_runs: int = _MAX_RUNS, cycles: int = outcomes.DORMANCY_CYCLES) -> dict[str, int]:
    """Report every work unit whose recent output nobody read. Files proposals; stops nothing.

    A work unit is the TEMPLATE, not the run: the thing a user would pause or retire is the
    recurring producer, and a single run is one cycle of it. Runs with no `workflow_name` fall back
    to their own id, so an ad-hoc run is its own unit and can never make a named template look
    dormant.

    Reads only resolved consumption questions, so the 7-day horizon is enforced upstream by the
    resolver — a resolution exists only because its horizon elapsed. Best-effort per run: one
    unreadable ledger never blocks the rest.

    Report keys: ``units`` (work units with at least one graded publish), ``dormant`` / ``live`` /
    ``insufficient`` (the three verdicts, so the FIRING POPULATION is visible before the control is
    trusted), ``proposed`` (proposals filed or reinforced).
    """
    report = {"units": 0, "dormant": 0, "live": 0, "insufficient": 0, "proposed": 0}
    from personalclaw.workflows import journal as journal_mod
    from personalclaw.workflows import store as store_mod

    try:
        runs, _total = store_mod.list_runs(limit=max_runs)
    except Exception:
        logger.debug("consumer-liveness: could not list runs", exc_info=True)
        return report

    by_unit: dict[str, list[dict[str, Any]]] = {}
    horizons: dict[str, list[float]] = {}
    for run in runs:
        try:
            events = journal_mod.ledger(run.id)
        except Exception:
            logger.debug("consumer-liveness: ledger read failed for %s", run.id, exc_info=True)
            continue
        unit = str(getattr(run, "workflow_name", "") or run.id)
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("kind") == PENDING_OUTCOME:
                if event.get("producer") == outcomes.PRODUCER_PUBLISH:
                    horizons.setdefault(unit, []).append(float(event.get("horizon_secs") or 0.0))
                continue
            if event.get("kind") != OUTCOME_RESOLVED:
                continue
            if event.get("producer") != outcomes.PRODUCER_PUBLISH:
                continue
            by_unit.setdefault(unit, []).append({**event, "run_id": str(run.id)})

    for unit, resolutions in sorted(by_unit.items()):
        # Oldest first: `dormancy_verdict` takes the LAST `cycles` as its window, and `ts` is the
        # ledger's fixed-width UTC format, so a lexical sort IS chronological here.
        resolutions.sort(key=lambda r: str(r.get("ts") or ""))
        verdict = outcomes.dormancy_verdict(resolutions, cycles=cycles)
        report["units"] += 1
        report[verdict] = report.get(verdict, 0) + 1
        if verdict != outcomes.DORMANT:
            continue
        window = resolutions[-max(1, int(cycles)) :]
        seen = horizons.get(unit) or []
        horizon_days = (max(seen) if seen else 0.0) / 86400.0
        try:
            if _propose(unit, window, horizon_days=horizon_days):
                report["proposed"] += 1
        except Exception:
            logger.debug("consumer-liveness: proposal failed for %s", unit, exc_info=True)
    return report
