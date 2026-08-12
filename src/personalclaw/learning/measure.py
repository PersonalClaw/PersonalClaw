"""Is the flywheel working? Per-arm precision + Beta-Binomial trust (LEARN-R4 / §2.5 — S71).

§7's criterion 7 is the bar: per-arm surfaced-vs-used precision is REPORTABLE per entity kind,
threshold profiles are TUNABLE FROM DATA, and a muted chip visibly LOWERS an entity's trust
posterior. All three are measurements, not opinions, and the plan is explicit about why:
"unenforced 'helpful' scores stay ornamental forever".

**Measured before writing.** `learning/surfacing.py` already carries `THRESHOLD_PROFILES` with the
0.55/0.62 split the plan wants preserved, and `learning/usage.py` already persists
`surfaced`/`used`/`successes`/`failures` per entity — the exact counts a posterior needs. What is
missing is the middle: `Candidate` has no `arm` field, so nothing can attribute a surfacing to the
match path that produced it, and nothing computes a posterior from the counts that exist.

So this module adds the attribution and the statistics, and deliberately does NOT add a second
threshold table or a second usage store.

**Why per-arm and not one scalar.** §2.5: "a single scalar can't be calibrated per-arm". An
exact-name match and an embedding match that both score 0.7 are not equally trustworthy, and
averaging them produces a threshold too permissive for one and too strict for the other. Precision
is therefore reported per `(kind, arm)` pair — the unit a threshold can actually be tuned on.

**Why Beta-Binomial and not a ratio.** A raw used/surfaced ratio says 1.0 after a single lucky hit
and 0.0 after a single miss — so a brand-new entity outranks a proven one, and one bad turn
condemns a good lesson. The Beta posterior encodes "we have barely any evidence" as a wide interval
around the prior, which is what makes the tie-break honest. `LOWER_BOUND_Z` shrinks toward the
prior in proportion to ignorance; ranking on it is what stops a 1-for-1 entity leading the board.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.memory_push import ARM_CONFIDENCE as _SHIPPED_ARMS
from personalclaw.memory_push import RECENCY_BONUS as RECENCY_BONUS

#: Base confidence per match arm (§2.5). Distinct per path — the paths are not equally reliable:
#: an exact name match is nearly certain, an embedding neighbour is a guess with a good prior.
#:
#: **The shipped arms are imported, not restated.** `memory_push.ARM_CONFIDENCE` already defines
#: `alias`/`exact_name`/`suffix`, and its docstring records that "how the name was recognised IS the
#: evidence". Writing a second table diverged immediately on first measurement: I had `exact_name`
#: at 0.90 where the shipped table says 0.80. Two confidence scales for one arm name is precisely
#: the drift this program keeps finding, so the shipped values win and the retrieval-only arms
#: (`exact_title`/`path`/`keyword`/`embedding`, which memory_push has no notion of) extend them.
ARM_CONFIDENCE: dict[str, float] = {
    **_SHIPPED_ARMS,
    # Retrieval arms, ordered by how much the match itself tells you. `embedding` is last because a
    # nearest neighbour is a guess: it is the arm the plan names at ~0.6, and the one whose
    # precision the report is most likely to find wanting.
    "exact_title": 0.80,
    "path": 0.75,
    "keyword": 0.70,
    "embedding": 0.60,
}

#: Added when the entity was touched recently. A bonus rather than a separate arm: recency modifies
#: how much to trust the SAME match, it is not itself a way of matching. Same 0.05 as
#: `memory_push.RECENCY_BONUS` — imported for the same reason the arms are.

#: The arm used when a caller does not say. Deliberately the WEAKEST arm rather than a neutral
#: middle: an unattributed surfacing is one nobody could explain, and treating it as a strong match
#: would let un-instrumented paths inflate the precision report they are absent from.
DEFAULT_ARM = "embedding"

ARMS: tuple[str, ...] = tuple(sorted(ARM_CONFIDENCE))

#: The trust prior. §2.5 says "start 0.50", so a new entity begins at even odds — neither
#: suspected. Expressed as Beta(1,1) rather than a bare 0.5 so the first observation moves it a
#: sensible amount: a stronger prior would need many uses to budge, a weaker one would swing wildly.
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0

#: How many standard deviations below the mean the ranking score sits. ~1.0 (≈84% confidence) rather
#: than the 1.96 of a 95% interval: at 95% a promising entity with 3 uses ranks below one with 30
#: mediocre ones, which stalls the flywheel it is supposed to steer.
LOWER_BOUND_Z = 1.0

#: A muted chip's weight against the posterior. §7's criterion says a mute must VISIBLY lower trust,
#: so it counts as a full negative observation — the same weight as a surfacing that went unused.
#: Anything less would make muting a gesture the numbers ignore.
MUTE_WEIGHT = 1.0

#: Minimum surfacings before a per-arm precision figure is reported as actionable. Below this the
#: number exists but is noise, and a threshold tuned on 2 observations is worse than an untuned one.
MIN_SAMPLES_FOR_TUNING = 20


class Precision(str, Enum):
    """How a per-arm precision figure should be read.

    `INSUFFICIENT` is a distinct state rather than a low number, because the two demand opposite
    responses: a low precision means tighten the threshold, while insufficient evidence means leave
    it alone and collect more. Collapsing them is how a threshold gets tuned on noise.
    """

    GOOD = "good"
    POOR = "poor"
    INSUFFICIENT = "insufficient"


def arm_confidence(arm: str, *, recent: bool = False) -> float:
    """The base confidence for one match arm, plus the recency bonus.

    An unknown arm gets `DEFAULT_ARM`'s confidence, not zero: a zero would suppress the candidate
    entirely and make an un-instrumented match path silently invisible instead of merely weak.
    """
    base = ARM_CONFIDENCE.get(arm, ARM_CONFIDENCE[DEFAULT_ARM])
    return min(1.0, base + (RECENCY_BONUS if recent else 0.0))


@dataclass
class ArmStats:
    """Surfaced-vs-used counts for one `(kind, arm)` pair.

    `used` is derived MECHANICALLY by the caller (a skill body loaded, a template run started, a
    lesson cited) — never from a voluntary model "was this helpful" call. §2.5 is explicit:
    helpfulness scores stay ornamental forever, so this dataclass only ever receives observed facts.
    """

    kind: str
    arm: str
    surfaced: int = 0
    used: int = 0

    @property
    def precision(self) -> float:
        """Used ÷ surfaced, or 0.0 with no surfacings.

        A bare ratio here is correct — this is the descriptive statistic. The Beta posterior below
        is what handles small samples; conflating the two would leave no way to see the raw rate.
        """
        return (self.used / self.surfaced) if self.surfaced else 0.0

    @property
    def verdict(self) -> str:
        if self.surfaced < MIN_SAMPLES_FOR_TUNING:
            return Precision.INSUFFICIENT.value
        return Precision.GOOD.value if self.precision >= 0.5 else Precision.POOR.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "arm": self.arm,
            "surfaced": self.surfaced,
            "used": self.used,
            "precision": round(self.precision, 4),
            "verdict": self.verdict,
        }


def per_arm_precision(events: list[dict[str, Any]]) -> list[ArmStats]:
    """Aggregate surfacing events into per-`(kind, arm)` precision. Pure.

    Takes events rather than reading a store, so one function serves the live report, a backfill
    over pruned history, and a test — and so the caller owns retention (§2.5 prunes at 90d on the
    curator tick).

    An event with no `arm` is attributed to `DEFAULT_ARM` rather than dropped. Dropping would make
    the report describe only the instrumented paths while claiming to describe surfacing as a whole,
    which is the more misleading of the two options.
    """
    buckets: dict[tuple[str, str], ArmStats] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind", "") or "unknown")
        arm = str(event.get("arm", "") or DEFAULT_ARM)
        stats = buckets.setdefault((kind, arm), ArmStats(kind=kind, arm=arm))
        stats.surfaced += 1
        if event.get("used"):
            stats.used += 1
    return sorted(buckets.values(), key=lambda s: (s.kind, s.arm))


@dataclass
class Posterior:
    """A Beta-Binomial usefulness posterior for one entity.

    Carries `alpha`/`beta` rather than just the mean so a caller can update incrementally and so the
    interval width — the "how much do we actually know" part — stays inspectable.
    """

    kind: str
    entity: str
    alpha: float = PRIOR_ALPHA
    beta: float = PRIOR_BETA

    @property
    def mean(self) -> float:
        """The posterior mean: the expected probability this entity is useful when surfaced."""
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    @property
    def observations(self) -> float:
        """Evidence gathered, excluding the prior. What distinguishes 1-for-1 from 30-for-30."""
        return (self.alpha - PRIOR_ALPHA) + (self.beta - PRIOR_BETA)

    def precision_ratio(self) -> float:
        """The NAIVE used/surfaced ratio, prior removed.

        Exposed deliberately, and deliberately not used for ranking: it is the number a reader
        expects, and having it here is what makes the difference visible. A lucky 1-of-1 scores 1.0
        on this and still ranks below a 27-of-30 entity on `lower_bound` — the whole reason this is
        a posterior rather than a division.
        """
        used = self.alpha - PRIOR_ALPHA
        total = used + (self.beta - PRIOR_BETA)
        return (used / total) if total > 0 else 0.0

    @property
    def stdev(self) -> float:
        total = self.alpha + self.beta
        if total <= 1:
            return 0.5
        return math.sqrt((self.alpha * self.beta) / (total * total * (total + 1)))

    @property
    def lower_bound(self) -> float:
        """The ranking score: mean minus `LOWER_BOUND_Z` standard deviations, clamped to [0, 1].

        Ranking on this rather than the mean is what makes the tie-break honest. A brand-new entity
        with one lucky hit has a mean of 0.67 and a huge stdev, so its lower bound sits near the
        prior — while a proven entity's interval is tight and its lower bound is close to its mean.
        """
        return max(0.0, min(1.0, self.mean - LOWER_BOUND_Z * self.stdev))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entity": self.entity,
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "mean": round(self.mean, 4),
            "lower_bound": round(self.lower_bound, 4),
            "observations": round(self.observations, 2),
        }


def posterior_from_counts(
    *,
    kind: str,
    entity: str,
    surfaced: int,
    used: int,
    mutes: int = 0,
) -> Posterior:
    """Build a posterior from the counts `usage.UsageRecord` already persists.

    `used` counts as a success; every surfacing that went UNused counts as a failure; a mute adds a
    further negative at `MUTE_WEIGHT`. Deriving failures as `surfaced - used` rather than requiring
    a separate counter means the posterior works on data already on disk — no migration, and no
    second store to keep in sync.

    Counts are clamped defensively: a `used` larger than `surfaced` (possible if a caller records a
    use for an entity surfaced before events existed) would otherwise produce a negative beta and a
    nonsense posterior.
    """
    surfaced = max(0, int(surfaced or 0))
    used = max(0, min(int(used or 0), surfaced))
    unused = surfaced - used
    return Posterior(
        kind=kind,
        entity=entity,
        alpha=PRIOR_ALPHA + used,
        beta=PRIOR_BETA + unused + MUTE_WEIGHT * max(0, int(mutes or 0)),
    )


def apply_mute(posterior: Posterior, *, count: int = 1) -> Posterior:
    """A muted chip, applied to a posterior. Returns a NEW posterior.

    §7's criterion 7 requires a mute to VISIBLY lower trust, so this is a real negative observation
    rather than a display flag. Returning a new object rather than mutating keeps the call site
    honest that trust changed — a mutation would let a caller drop the result and lose it.
    """
    return Posterior(
        kind=posterior.kind,
        entity=posterior.entity,
        alpha=posterior.alpha,
        beta=posterior.beta + MUTE_WEIGHT * max(0, int(count or 0)),
    )


def rank_by_trust(posteriors: list[Posterior]) -> list[Posterior]:
    """Order entities for the surfacing tie-break: best lower bound first.

    Ties break on `observations` (more evidence wins) then on entity name, so the order is stable.
    An unstable tie-break would make two runs of the same corpus surface different things, which is
    indistinguishable from the flywheel having learned something.
    """
    return sorted(
        posteriors,
        key=lambda p: (-p.lower_bound, -p.observations, p.entity),
    )


# ── threshold tuning from data (§7 criterion 7's second clause) ──


@dataclass
class ThresholdProposal:
    """A proposed change to one entity kind's threshold, with the evidence behind it.

    A PROPOSAL, never an applied change. §2.5 says recalibration happens "empirically, not by
    taste" — and the corollary is that it also does not happen automatically: the 0.55/0.62 split
    was deliberately calibrated, so overwriting it from a week of data would discard a real
    decision. The caller decides; this says what the data supports.
    """

    kind: str
    current: float
    proposed: float
    reason: str
    samples: int

    @property
    def changed(self) -> bool:
        return abs(self.proposed - self.current) > 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "current": round(self.current, 4),
            "proposed": round(self.proposed, 4),
            "reason": self.reason,
            "samples": self.samples,
            "changed": self.changed,
        }


#: How far a single proposal may move a threshold. Small on purpose: a threshold is a calibration,
#: and a report that could swing it 0.2 in one step would oscillate as the corpus changes.
MAX_THRESHOLD_STEP = 0.05

#: Precision bands. Below `POOR_BELOW` the arm is surfacing too much noise (tighten); above
#: `RICH_ABOVE` it is so precise it is probably missing things (loosen). The gap between them is
#: deliberately wide — a threshold that chases every fluctuation is worse than a fixed one.
POOR_BELOW = 0.35
RICH_ABOVE = 0.85


def propose_thresholds(
    stats: list[ArmStats],
    *,
    current: dict[str, float] | None = None,
) -> list[ThresholdProposal]:
    """What the precision data says each kind's threshold should be. Never applies anything.

    Reads the CURRENT values from `surfacing.THRESHOLD_PROFILES` rather than restating them, so the
    proposal is always relative to what is actually in force — a second copy of the table would let
    a proposal recommend a move away from a value nobody uses.

    A kind with insufficient evidence gets a no-change proposal WITH a reason, rather than being
    omitted. Omission would read as "nothing to say about this kind", when the actual finding is
    "not enough data yet", and those lead to different decisions.
    """
    from personalclaw.learning.surfacing import THRESHOLD_PROFILES

    thresholds = dict(current if current is not None else THRESHOLD_PROFILES)
    by_kind: dict[str, list[ArmStats]] = {}
    for stat in stats or []:
        by_kind.setdefault(stat.kind, []).append(stat)

    out: list[ThresholdProposal] = []
    for kind in sorted(set(thresholds) | set(by_kind)):
        current_value = float(thresholds.get(kind, 0.55))
        kind_stats = by_kind.get(kind, [])
        samples = sum(s.surfaced for s in kind_stats)
        used = sum(s.used for s in kind_stats)
        if samples < MIN_SAMPLES_FOR_TUNING:
            out.append(
                ThresholdProposal(
                    kind=kind,
                    current=current_value,
                    proposed=current_value,
                    reason=f"only {samples} surfacings; {MIN_SAMPLES_FOR_TUNING} needed before "
                    "tuning a threshold on them",
                    samples=samples,
                )
            )
            continue
        precision = used / samples
        if precision < POOR_BELOW:
            proposed = min(0.95, current_value + MAX_THRESHOLD_STEP)
            reason = f"{precision:.0%} of surfaced items were used; raising the bar to cut noise"
        elif precision > RICH_ABOVE:
            proposed = max(0.30, current_value - MAX_THRESHOLD_STEP)
            reason = (
                f"{precision:.0%} of surfaced items were used; the threshold is likely too strict "
                "and hiding useful items"
            )
        else:
            proposed = current_value
            reason = f"{precision:.0%} used — inside the healthy band, leave it alone"
        # A kind's threshold gates the FUSED score, so it is genuinely one number and the aggregate
        # is the right input. But the aggregate can hide a lot: measured while probing, `skill` had
        # a 90%-precision `exact_name` arm and a 16% `embedding` arm, which average to a
        # healthy-looking 49% and move nothing. Naming the spread in the reason is what stops a
        # reader concluding the kind is fine — the fix for a bad arm is that arm's own
        # confidence, not the kind's threshold.
        spread = _arm_spread(kind_stats)
        if spread:
            reason = f"{reason}. {spread}"
        out.append(
            ThresholdProposal(
                kind=kind,
                current=current_value,
                proposed=proposed,
                reason=reason,
                samples=samples,
            )
        )
    return out


#: How far apart two arms' precisions must be before the aggregate is called misleading. 0.4 is wide
#: enough that ordinary variation between a name match and an embedding match does not trip it, and
#: narrow enough to catch the 90%-vs-16% case that motivated it.
ARM_SPREAD_ALERT = 0.4


def _arm_spread(stats: list[ArmStats]) -> str:
    """A note naming the best and worst arm when they disagree sharply, else `""`.

    Only considers arms with enough samples to be meaningful on their own: a 1-of-1 arm always looks
    like 100% and would report a spread against every real arm forever.
    """
    eligible = [s for s in stats if s.surfaced >= max(3, MIN_SAMPLES_FOR_TUNING // 4)]
    if len(eligible) < 2:
        return ""
    best = max(eligible, key=lambda s: s.precision)
    worst = min(eligible, key=lambda s: s.precision)
    if best.precision - worst.precision < ARM_SPREAD_ALERT:
        return ""
    return (
        f"NOTE: the arms disagree sharply — {best.arm} is at {best.precision:.0%} while "
        f"{worst.arm} is at {worst.precision:.0%}; the aggregate hides that, and a weak "
        f"arm is its own confidence, not this threshold"
    )


@dataclass
class FlywheelReport:
    """The answer to "is the flywheel working", as one renderable object.

    Exists because §7's criterion is about REPORTABILITY: three numbers scattered across three
    modules do not answer it, and callers assembling them ad hoc would each answer it differently.
    """

    arms: list[ArmStats] = field(default_factory=list)
    proposals: list[ThresholdProposal] = field(default_factory=list)
    trusted: list[Posterior] = field(default_factory=list)

    @property
    def overall_precision(self) -> float:
        surfaced = sum(a.surfaced for a in self.arms)
        used = sum(a.used for a in self.arms)
        return (used / surfaced) if surfaced else 0.0

    @property
    def actionable(self) -> list[ThresholdProposal]:
        return [p for p in self.proposals if p.changed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_precision": round(self.overall_precision, 4),
            "surfaced": sum(a.surfaced for a in self.arms),
            "used": sum(a.used for a in self.arms),
            "arms": [a.to_dict() for a in self.arms],
            "proposals": [p.to_dict() for p in self.proposals],
            "actionable": [p.to_dict() for p in self.actionable],
            "trusted": [p.to_dict() for p in self.trusted],
        }


def build_report(
    events: list[dict[str, Any]],
    *,
    usage: list[dict[str, Any]] | None = None,
    top: int = 20,
) -> FlywheelReport:
    """Assemble the whole measurement from raw events + usage counts. Pure.

    `usage` rows are the shape `usage.UsageRecord.to_dict()` already produces, so the live caller
    passes the store's own output and nothing has to be reshaped. `top` bounds the trust list: the
    report is a page, and every entity ever surfaced is not one.
    """
    arms = per_arm_precision(events)
    posteriors = [
        posterior_from_counts(
            kind=str(row.get("kind", "") or ""),
            entity=str(row.get("entity", "") or ""),
            surfaced=int(row.get("surfaced", 0) or 0),
            used=int(row.get("used", 0) or 0),
            mutes=int(row.get("mutes", 0) or 0),
        )
        for row in (usage or [])
        if isinstance(row, dict)
    ]
    return FlywheelReport(
        arms=arms,
        proposals=propose_thresholds(arms),
        trusted=rank_by_trust(posteriors)[: max(1, top)],
    )


# ── The flywheel health composite (LEARN-R14b) ──

#: The ideal budget-utilization band. Below `LOW` the allocator is starving a budget the
#: user is paying for; above `HIGH` it is crowding out on every turn and the sacrificial
#: slot is doing all the work. §6.2 names this band explicitly — 50-80%.
UTILIZATION_IDEAL_LOW = 0.50
UTILIZATION_IDEAL_HIGH = 0.80

#: Component weights. Precision is heaviest because "does surfacing land?" is the
#: flywheel's whole claim; capture is next because a flywheel that never captures has
#: nothing to surface. They sum to 1.0 so the composite is a real 0-100.
HEALTH_WEIGHTS: dict[str, float] = {
    "precision": 0.4,
    "capture": 0.3,
    "utilization": 0.2,
    "judge": 0.1,
}


def _band_score(value: float) -> float:
    """Score a utilization figure against the ideal band. 1.0 inside, falling outside.

    Falls off LINEARLY toward each end rather than dropping to zero: 45% is nearly
    ideal and 5% is not, and a cliff at the band edge would report those as the same
    failure.
    """
    if UTILIZATION_IDEAL_LOW <= value <= UTILIZATION_IDEAL_HIGH:
        return 1.0
    if value < UTILIZATION_IDEAL_LOW:
        return max(0.0, value / UTILIZATION_IDEAL_LOW)
    return max(0.0, (1.0 - value) / (1.0 - UTILIZATION_IDEAL_HIGH))


def health_composite(
    *,
    precision: float | None,
    capture_passes: int,
    capture_errors: int,
    utilization: float | None,
    judge_false_pass_rate: float | None,
) -> dict[str, Any]:
    """One 0-100 number, plus every component that produced it. Pure.

    **Unmeasured is not zero.** Any component with no data is EXCLUDED and its weight
    redistributed, and the response says which. A composite that scored silence as 0
    would report a fresh install as critically unhealthy and a healthy install with one
    un-instrumented subsystem as mediocre — and the user's only available fix would be
    to generate traffic, which is not a fix.
    """
    components: list[dict[str, Any]] = []

    def _add(name: str, value: float | None, detail: str) -> None:
        components.append(
            {
                "name": name,
                "score": None if value is None else round(max(0.0, min(1.0, value)) * 100, 1),
                "weight": HEALTH_WEIGHTS[name],
                "detail": detail,
            }
        )

    _add(
        "precision",
        precision,
        (
            "unmeasured — nothing surfaced yet"
            if precision is None
            else f"{precision:.0%} of surfacings were used"
        ),
    )
    if capture_passes <= 0:
        _add("capture", None, "unmeasured — no capture pass has run")
    else:
        ok = capture_passes - capture_errors
        _add(
            "capture",
            max(0.0, ok / capture_passes),
            f"{ok} of {capture_passes} pass(es) clean",
        )
    _add(
        "utilization",
        None if utilization is None else _band_score(utilization),
        (
            "unmeasured — no ambient render recorded"
            if utilization is None
            else f"{utilization:.0%} of the context budget used "
            f"(ideal {UTILIZATION_IDEAL_LOW:.0%}-{UTILIZATION_IDEAL_HIGH:.0%})"
        ),
    )
    _add(
        "judge",
        None if judge_false_pass_rate is None else 1.0 - judge_false_pass_rate,
        (
            "unmeasured — no judge verdicts with human labels"
            if judge_false_pass_rate is None
            else f"{judge_false_pass_rate:.0%} of judged work was wrongly passed"
        ),
    )

    scored = [c for c in components if c["score"] is not None]
    total_weight = sum(float(c["weight"]) for c in scored)
    score = (
        round(sum(float(c["score"]) * float(c["weight"]) for c in scored) / total_weight, 1)
        if total_weight > 0
        else None
    )
    return {
        "score": score,
        "components": components,
        "measured": len(scored),
        "of": len(components),
        "ideal_band": [UTILIZATION_IDEAL_LOW, UTILIZATION_IDEAL_HIGH],
    }
