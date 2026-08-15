"""The outcome record — one facility for every producer that makes a bet (PP-9).

The platform records what it DID and never what LANDED. That single shape is what produces loop
output nobody reads, unmeasured routing edges, and the declared-but-inert class this repo keeps
rediscovering by AST audit rather than at runtime.

The mechanism to fix it already existed and was scoped to one feature: LEARN-R18's
`pending_outcome` / `outcome_resolved` pair, openable only by a decision-producing workflow node,
with `instance_path`/`node_id`/`epoch` welded into the record shape. This module is that same pair
with the decision assumptions lifted out of it, so ANY producer may open a question:

* a **decision** — the original: this run chose `subject`, and `metric` will reveal whether it was
  right (`PRODUCER_DECISION`);
* a **published artifact** — we produced a deliverable; did anyone consume it (`PRODUCER_PUBLISH`);
* an **escalation** — we interrupted the user; did they answer (`PRODUCER_ESCALATION`);
* a **proposal** or a **declared control** — we asked for something, or we declared a rail; did it
  ever fire (`PRODUCER_PROPOSAL`, `PRODUCER_CONTROL`).

There is exactly ONE outcome facility, and this is it. PROACTIVE-ASSISTANT's `PA-4` decision
journal lands here as a producer (`PRODUCER_DECISION` with its own `context` fields), not as a
second decision-shaped pair beside this one — the whole point of generalizing was to make that
second facility unnecessary before it is written.

Three invariants survive the generalization unchanged, because each of them is why the original
worked:

**Idempotency via `pending_event_id`.** Every resolution cites the `event_id` of the question it
closes, so :func:`open_questions` can subtract the answered ones from the asked ones by set
membership. A second resolver tick over the same ledger writes nothing. No timestamps, no
"already done" flag file, no lock — the ledger IS the state.

**`measured` is distinct from `inconclusive`.** A question whose ground truth could not be read
after its horizon still CLOSES — otherwise a permanently unreadable metric re-resolves forever —
but it closes as weaker evidence. The two resolutions carry different decay profiles
(:data:`DECAY_PROFILE`) and different proposal-queue tiers, so an outcome we could not measure
ages out fast instead of masquerading as a confirmed one.

**Ground truth has a declared SOURCE.** A decision's metric lives in semantic memory; an
escalation's lives in the producer's own ledger (did a `confirmation_resolved` citing this
confirmation ever appear?); a published artifact's lives on the ARTIFACT — did a consumer ever touch
it (:data:`SOURCE_CONSUMPTION`, PP-10). Naming the source on the question is what lets one resolver
grade all three without a per-producer branch, and what keeps ledger- and consumption-sourced
questions resolvable on a box with no vector store.

`PP-10` reads this facility rather than counting alongside it: a work unit whose last
:data:`DORMANCY_CYCLES` published artifacts all resolved `UNCONSUMED` is :data:`DORMANT`
(:func:`dormancy_verdict`), which becomes a PROPOSAL to pause or retire it and never an automatic
stop — "nobody looked yet" and "nobody will ever look" are different facts and only the user knows
which.

Nothing here imports `personalclaw.workflows` (there is an AST rail): the workflow-shaped
adapters — the ones that stamp `instance_path`/`node_id`/`epoch` — stay in `workflows/journal.py`
over :meth:`OutcomeLedger.open_outcome`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw.ledger.kinds import OUTCOME_RESOLVED, PENDING_OUTCOME

# ── who may open a question ──

#: The original producer: a run node that chose something (LEARN-R18, and `PA-4`'s journal).
PRODUCER_DECISION = "decision"
#: A `publish:` declaration that landed an artifact — the bet is that somebody consumes it.
PRODUCER_PUBLISH = "publish"
#: A gate that stopped to ask a human — the bet is that the interruption was worth making.
PRODUCER_ESCALATION = "escalation"
#: A proposal filed into the human-gated queue — the bet is that it gets accepted, not ignored.
PRODUCER_PROPOSAL = "proposal"
#: A declared control (a rail, a guardrail, a gate criterion) — the bet is that it ever fires.
PRODUCER_CONTROL = "control"

#: Closed set, so a typo becomes a loud `ValueError` at the open rather than a producer nobody can
#: query for later. Widening it is a deliberate edit with a docstring, which is the point.
PRODUCERS = frozenset(
    {
        PRODUCER_DECISION,
        PRODUCER_PUBLISH,
        PRODUCER_ESCALATION,
        PRODUCER_PROPOSAL,
        PRODUCER_CONTROL,
    }
)

# ── how a question closes ──

#: Ground truth was read after the horizon. Real evidence.
MEASURED = "measured"
#: The horizon passed and ground truth could not be read. A real CLOSURE (so it never re-resolves)
#: carrying weaker evidence than a measurement.
INCONCLUSIVE = "inconclusive"

#: The memory decay profile each resolution's evidence inherits. Keys of
#: `learning.decay.KIND_MULTIPLIERS` — `speculative` (3.0) decays roughly four times faster than
#: `lesson` (0.7), which is the whole "an unmeasurable outcome decays faster" rule expressed as
#: data rather than as an `if` at each consumer. `test_ledger_outcomes.py` rails the direction
#: against the kernel's own table so the two can never drift into agreeing.
DECAY_PROFILE = {MEASURED: "lesson", INCONCLUSIVE: "speculative"}

#: The proposals-queue evidence tier per resolution, and the confidence floor an inconclusive
#: closure gets: a bet we could not grade is worth recording and not worth arguing from.
_EVIDENCE_STRENGTH = {MEASURED: "correlated", INCONCLUSIVE: "anecdotal"}
INCONCLUSIVE_CONFIDENCE = 0.1

# ── where ground truth is read from ──

#: A semantic-memory key whose value reduces to a number. Requires a live vector store, so these
#: questions stay open (not inconclusive) on a box that has none — "nobody could look yet" is not
#: the same fact as "the metric is unreadable".
SOURCE_MEMORY = "memory"
#: A later event in the producer's OWN ledger. `metric` is the event kind, `match` the fields it
#: must carry, `value_field` the number to read off it (absent ⇒ mere presence scores 1.0).
#: Resolvable with no memory service at all.
SOURCE_LEDGER = "ledger"
#: A CONSUMER TOUCH on the thing the producer landed (PP-10). Neither a semantic key nor an event in
#: the producer's own log: the reader is a person on a surface the run cannot see, and their touch
#: is recorded on the OBJECT (an artifact's lifecycle timeline, the dashboard pin list) by writers
#: that already exist. Naming it as a third source is what keeps ONE resolver — the alternative is a
#: `if producer == publish` branch inside the grader, which is how the facility ended up
#: decision-shaped the first time. The reader lives in `learning.consumer_liveness`, because
#: reading it is I/O and this module is pure.
SOURCE_CONSUMPTION = "consumption"

SOURCES = frozenset({SOURCE_MEMORY, SOURCE_LEDGER, SOURCE_CONSUMPTION})

# ── consumer liveness: is anybody reading what this work unit produces? (PP-10) ──

#: A consumption measurement is binary by construction — somebody touched the output, or nobody did.
#: Written as constants because `baseline=1.0` on the publish question means "one consumption is the
#: whole bet", and a reader comparing against a bare literal would not say why.
CONSUMED = 1.0
UNCONSUMED = 0.0

#: How many consecutive MATURED, unconsumed cycles make a work unit a dormancy candidate. Three,
#: because the field post-mortem this exists for is a pipeline that ran dead for MONTHS — one
#: unopened deliverable is a busy week, three in a row past their horizons is a pattern — and
#: because a sweep that fires on cycle one trains the user to skim past the surface it reports on,
#: which is the failure mode that makes a real dormancy finding invisible.
DORMANCY_CYCLES = 3

#: Nobody touched the last :data:`DORMANCY_CYCLES` outputs, every one of them past its horizon.
DORMANT = "dormant"
#: Somebody touched at least one recent output. The sweep says NOTHING — this is the verdict that
#: makes it a detector rather than a nag.
LIVE = "live"
#: Not enough matured cycles to have an opinion, or a cycle whose consumption could not be read.
#: A THIRD verdict on purpose: "nobody looked yet" and "we could not tell" are both different facts
#: from "nobody reads this", and collapsing either into `DORMANT` is exactly how the control gets
#: teeth it has not earned.
INSUFFICIENT = "insufficient"


#: The `metric` name a `publish:` question asks under. Built and parsed in ONE place because the
#: resolution record carries the metric and not the slug, so the sweep recovers the artifact from
#: this name — two hand-written format strings would drift and the sweep would silently lose its
#: evidence refs.
_METRIC_PREFIX = "artifact."
_METRIC_SUFFIX = ".consumed"


def consumption_metric(slug: str) -> str:
    """The `metric` a `publish:` producer asks its consumption question under."""
    return f"{_METRIC_PREFIX}{slug}{_METRIC_SUFFIX}"


def slug_from_metric(metric: str) -> str:
    """The artifact slug inside a consumption metric name, or "" if it is not one."""
    name = str(metric or "")
    if not name.startswith(_METRIC_PREFIX) or not name.endswith(_METRIC_SUFFIX):
        return ""
    return name[len(_METRIC_PREFIX) : -len(_METRIC_SUFFIX)]


def dormancy_verdict(resolutions: list[dict[str, Any]], *, cycles: int = DORMANCY_CYCLES) -> str:
    """Grade one work unit's recent consumption resolutions, oldest first.

    Pure, and deliberately conservative in three directions, because the atom this implements is
    one whose failure mode is nagging rather than missing something:

    * **A touch anywhere in the window wins.** One recent read means somebody is reading, so the
      verdict is :data:`LIVE` even if the other cycles went untouched — a fortnightly reader is a
      reader.
    * **An unreadable cycle is not evidence.** An `inconclusive` resolution (the artifact was
      deleted, the timeline was unreadable) yields :data:`INSUFFICIENT`, never :data:`DORMANT`:
      "we could not tell" must not accumulate into "nobody looked".
    * **Fewer than `cycles` matured resolutions is :data:`INSUFFICIENT`.** Only resolutions exist
      here, and a resolution only exists once its horizon elapsed, so this is the horizon guard —
      a work unit publishing since yesterday cannot be called dormant at any `cycles`.
    """
    window = [r for r in resolutions if isinstance(r, dict)][-max(1, int(cycles)) :]
    if any(
        r.get("resolution") == MEASURED and _float(r.get("measured")) >= CONSUMED for r in window
    ):
        return LIVE
    if len(window) < max(1, int(cycles)):
        return INSUFFICIENT
    if any(r.get("resolution") != MEASURED for r in window):
        return INSUFFICIENT
    return DORMANT


@dataclass(frozen=True)
class OutcomeQuestion:
    """One open question, parsed from a `pending_outcome` record with tolerant reads.

    Frozen because a question is a historical fact: the resolver grades it, it never edits it.
    `record` keeps the raw event so a producer's own context fields (a workflow's `instance_path`,
    an artifact's `slug`) survive into the resolution without this module knowing what they mean.
    """

    event_id: str
    producer: str
    subject: str
    metric: str
    metric_source: str
    horizon_secs: float
    baseline: float
    ts: str
    match: dict[str, Any] = field(default_factory=dict)
    value_field: str = ""
    record: dict[str, Any] = field(default_factory=dict)


def _float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def parse(record: dict[str, Any]) -> OutcomeQuestion | None:
    """A `pending_outcome` record → :class:`OutcomeQuestion`, or None if it is not one.

    Tolerant on every field except `event_id`: a question with no id cannot be cited back, so it
    cannot be closed idempotently and is better ignored than resolved forever. A record written
    before this generalization has no `producer`/`metric_source` and reads back as the decision
    question it was.
    """
    if not isinstance(record, dict) or record.get("kind") != PENDING_OUTCOME:
        return None
    event_id = str(record.get("event_id") or "")
    if not event_id:
        return None
    raw_match = record.get("match")
    return OutcomeQuestion(
        event_id=event_id,
        producer=str(record.get("producer") or PRODUCER_DECISION),
        subject=str(record.get("subject") or ""),
        metric=str(record.get("metric") or ""),
        metric_source=str(record.get("metric_source") or SOURCE_MEMORY),
        horizon_secs=_float(record.get("horizon_secs")),
        baseline=_float(record.get("baseline")),
        ts=str(record.get("ts") or ""),
        match=dict(raw_match) if isinstance(raw_match, dict) else {},
        value_field=str(record.get("value_field") or ""),
        record=record,
    )


def open_questions(events: list[dict[str, Any]]) -> list[OutcomeQuestion]:
    """Every question in `events` that no resolution cites. The idempotency primitive.

    One pass, set membership on `pending_event_id` — which is why resolving twice is a no-op
    rather than a second write the reader has to de-duplicate later.
    """
    answered = {
        str(e.get("pending_event_id") or "")
        for e in events
        if isinstance(e, dict) and e.get("kind") == OUTCOME_RESOLVED
    }
    parsed = (parse(e) for e in events if isinstance(e, dict))
    return [q for q in parsed if q is not None and q.event_id not in answered]


def measure_from_events(question: OutcomeQuestion, events: list[dict[str, Any]]) -> float | None:
    """Ground truth for a :data:`SOURCE_LEDGER` question, read off the producer's own ledger.

    Matches events of kind `question.metric` that (a) come AFTER the question was opened and
    (b) carry every `match` field. The LAST match wins — a gate answered, rewound and answered
    again resolves to the answer that stuck. Returns None when nothing matched, which the caller
    turns into :data:`INCONCLUSIVE`: no answer inside the horizon is a real closure, and it is a
    different fact from an answer of zero.

    "After" is FILE POSITION, not `seq`: the log is append-only, so position is append order, and
    a second writer built for the same run restarts its sequence at 1 (a live hazard — two writers
    in one process re-mint each other's `event_id`s). Position cannot be fooled by that.

    A boolean `value_field` reads as 1.0/0.0 on purpose: "approved" is a measurement.
    """
    if not question.metric:
        return None
    start = 0
    for index, event in enumerate(events):
        if isinstance(event, dict) and str(event.get("event_id") or "") == question.event_id:
            start = index + 1
            break
    found: float | None = None
    for event in events[start:]:
        if not isinstance(event, dict) or event.get("kind") != question.metric:
            continue
        if any(str(event.get(k, "")) != str(v) for k, v in question.match.items()):
            continue
        if not question.value_field:
            found = 1.0
            continue
        raw = event.get(question.value_field)
        if isinstance(raw, bool):
            found = 1.0 if raw else 0.0
        else:
            found = _float(raw, default=1.0)
    return found


def is_due(question: OutcomeQuestion, *, opened_epoch: float | None, now: float) -> bool:
    """Has this question's horizon elapsed? An unparseable open timestamp counts as due —
    a question we cannot date would otherwise stay pending forever."""
    if opened_epoch is None:
        return True
    return (now - opened_epoch) >= question.horizon_secs


def score(measured: float, baseline: float) -> float:
    """Benchmark-relative score in [-1, 1]: how far `measured` beat `baseline`, normalized.

    Normalized by the magnitude of both so the score is comparable across metrics of wildly
    different scale (a latency in ms and a pass-rate in [0,1] both land in [-1, 1]). Positive
    means the bet beat its baseline, negative means it lost to it, zero means it matched.
    """
    denom = abs(measured) + abs(baseline)
    if denom < 1e-9:
        return 0.0
    return max(-1.0, min(1.0, (measured - baseline) / denom))


def resolution_for(measured: float | None) -> str:
    """`measured` ⇒ :data:`MEASURED`, unreadable ⇒ :data:`INCONCLUSIVE`. One place, so no
    consumer invents a third state (a fabricated measurement is worse than an honest
    "could not tell")."""
    return INCONCLUSIVE if measured is None else MEASURED


def decay_profile(resolution: str) -> str:
    """The `learning.decay` kind whose rate this resolution's evidence ages at."""
    return DECAY_PROFILE.get(resolution, DECAY_PROFILE[INCONCLUSIVE])


def evidence_strength(resolution: str) -> str:
    """The proposals-queue evidence tier for a resolution."""
    return _EVIDENCE_STRENGTH.get(resolution, _EVIDENCE_STRENGTH[INCONCLUSIVE])


def confidence(resolution: str, score_value: float) -> float:
    """Proposal confidence: how far the bet moved for a measured outcome, a fixed floor for an
    inconclusive one (there is nothing to be confident about)."""
    if resolution != MEASURED:
        return INCONCLUSIVE_CONFIDENCE
    return min(1.0, abs(float(score_value)))


class OutcomeLedger:
    """The two general emitters, mixed into :class:`personalclaw.ledger.writer.LedgerWriter`.

    On the BASE writer rather than on one producer's facade: an outcome is not a workflow
    concept, and a producer that had to reach for a workflow journal to open one is exactly how
    the mechanism ended up scoped to a single feature the first time.
    """

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:  # pragma: no cover - overridden
        """Provided by `LedgerWriter`. Declared so the mixin's contract is explicit."""
        raise NotImplementedError

    def open_outcome(
        self,
        *,
        producer: str,
        subject: str,
        metric: str,
        horizon_secs: float,
        baseline: float = 0.0,
        metric_source: str = SOURCE_MEMORY,
        match: dict[str, Any] | None = None,
        value_field: str = "",
        **context: Any,
    ) -> dict[str, Any]:
        """Open a question: what this producer bet, and what would later prove it.

        Returns the written record so the caller can note its `event_id` — the key every
        resolution cites back, and the reason a second resolver tick is a no-op. `context` rides
        through untouched, which is how a producer keeps its own coordinates (a node path, an
        artifact slug) on the record without this module learning what they are.
        """
        if producer not in PRODUCERS:
            raise ValueError(f"unknown outcome producer {producer!r}")
        if metric_source not in SOURCES:
            raise ValueError(f"unknown outcome metric source {metric_source!r}")
        record = {
            **context,
            "producer": producer,
            "subject": subject,
            "metric": metric,
            "metric_source": metric_source,
            "horizon_secs": round(float(horizon_secs), 3),
            "baseline": round(float(baseline), 6),
        }
        if match:
            record["match"] = dict(match)
        if value_field:
            record["value_field"] = value_field
        return self.write(PENDING_OUTCOME, **record)

    def resolve_outcome(
        self,
        *,
        pending_event_id: str,
        producer: str,
        subject: str,
        metric: str,
        baseline: float,
        measured: float | None,
        score: float,
        resolution: str,
        **context: Any,
    ) -> dict[str, Any]:
        """Close a question with ground truth. Cites `pending_event_id`, always.

        Carries the resolution's `decay_profile` on the record so a consumer grading the evidence
        later — `PP-10`'s dormancy sweep, the curator — reads the ageing rule off the ledger
        instead of re-deriving it and picking a different one.
        """
        return self.write(
            OUTCOME_RESOLVED,
            **context,
            pending_event_id=pending_event_id,
            producer=producer,
            subject=subject,
            metric=metric,
            baseline=round(float(baseline), 6),
            measured=(None if measured is None else round(float(measured), 6)),
            score=round(float(score), 6),
            resolution=resolution,
            decay_profile=decay_profile(resolution),
        )
