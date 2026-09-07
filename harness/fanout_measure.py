"""Token-matched fan-out measurement (WORK-CONTAINERS amendment (e), C2.3).

**"Prove it helps" has an honest version and a dishonest one, and the difference is the
denominator.** The dishonest version compares a fan-out to a single agent and reports the score
delta. The honest version compares them **at equal token spend** — because the largest published
fan-out win (+90.2%) came with ~3.75x the tokens, and that paper's own regression says token usage
alone explains 80% of the outcome variance. An unmatched comparison measures the budget, then
credits the topology.

So this module refuses to report a winner unless the two arms spent within
:data:`TOKEN_MATCH_TOLERANCE` of each other. A `not_token_matched` verdict is not a failure of the
fan-out; it is the measurement declining to answer a question it did not ask.

**"The two arms spent the same" is not one quantity, and the design decides which.** Amendment (e)
matches budget by giving the cheaper arm more samples, so its arms' trial counts are unequal on
purpose and the gate divides TOTALS. A PAIRED design runs both arms over identical work the same
number of times, so its totals are commensurable only while the counts match and its gate must
divide PER TRIAL. :data:`SPEND_TOTAL` / :data:`SPEND_PER_TRIAL` make the caller say which, and
:func:`compare` records the answer on the :class:`Comparison` — a ratio whose denominator has to be
inferred was published once already, and it read as a 48.6% spend difference that was really two
missing trials.

**And a matched comparison still usually cannot see anything.** The literature's own noise floor
exceeds most of its reported architecture deltas: run-to-run variance is 1-3 points, format errors
cause >50% of failures in some harnesses, a scorer swap moved one result 79.0 -> 25.6, and the
benchmarks behind these claims run n=24-100. Amendment (e) draws the only conclusion available:
**a sub-5-point delta is reported as `inconclusive`.** Two consequences that are easy to get wrong
and are therefore mechanized here rather than left to the reader:

* A delta ABOVE the band is still `inconclusive` when the WITHIN-ARM spread reaches the band. Six
  points between arms means nothing if one arm varies by seven across its own trials — the reader
  would otherwise see the six and stop reading.
* One trial per arm is not a measurement. With variance at 1-3 points, n=1 cannot distinguish a win
  from a re-run, so :data:`MIN_TRIALS_PER_ARM` trials are required before any verdict is offered.

Scores are in POINTS on whatever 0-100 scale the work is graded on; the module never grades. It
reads a recorded observation file, which is the only input a measurement should trust — a harness
that generated its own numbers would be measuring itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Amendment (e), verbatim: "Treat any sub-5-point delta as unresolved — including our own future
#: measurements." Not a tunable. Lowering it to make a result presentable is the exact move the
#: amendment's risk section calls out ("a plan that only ever reports wins is not measuring").
INCONCLUSIVE_BAND_POINTS = 5.0

#: How closely the two arms' token spends must agree to count as token-matched. 5% is tight enough
#: that the budget cannot explain a >=5-point delta and loose enough to be reachable by a real
#: local run, where you cannot dial spend exactly (you add single-agent samples until the budget is
#: consumed).
TOKEN_MATCH_TOLERANCE = 0.05

#: Trials per arm before a verdict is offered at all. Three is a floor, not a sufficiency claim: the
#: papers behind the fan-out literature run n=24-100 and still sit near their own noise floor, which
#: is why the verdict reports the observed spread beside the delta instead of pretending three
#: trials settled anything.
MIN_TRIALS_PER_ARM = 3

#: WHICH quantity the token-match gate divides. A property of the experiment's DESIGN, which is why
#: it is a named vocabulary and not a number — there is no honest default that fits both designs:
#:
#: * :data:`SPEND_TOTAL` is the fan-out design's basis and this module's default. Amendment (e)
#:   matches budget by giving the cheaper arm more samples (see
#:   :data:`TOKEN_MATCH_TOLERANCE`: "you add single-agent samples until the budget is consumed"), so
#:   the two arms' trial counts are DELIBERATELY unequal and the question really is what the whole
#:   arm cost to produce its answer. Dividing by trial count here would defeat the design.
#: * :data:`SPEND_PER_TRIAL` is a PAIRED design's basis, where both arms run the identical work the
#:   identical number of times and spend is an OUTCOME of the treatment rather than a knob. Totals
#:   are commensurable there only while the counts match, so a lost trial makes the gate compare
#:   n_a attempts against n_b attempts and report the missing attempts as a spend difference.
#:   MEASURED (`learnbench-20260907T003211Z`, protocol §6's absent-cell case): a paired arm lost two
#:   cells, was verdicted on 3 trials against 5, and published a `token_ratio` of 0.5139 — "a 48.6%
#:   spend difference" — where the per-trial value is 0.8565, i.e. 14.3%.
SPEND_TOTAL = "total"
SPEND_PER_TRIAL = "per_trial"

#: Every basis the gate can divide on. Exported so a caller declares its design by naming a member
#: rather than passing a string this module would have to guess the meaning of.
SPEND_BASES = frozenset({SPEND_TOTAL, SPEND_PER_TRIAL})

#: The arm names. Fixed rather than free-form: the comparison is always fan-out against the
#: single-agent path on IDENTICAL work, and an observation file naming its arms something else is
#: measuring a different question than the one amendment (e) requires before a width increase.
ARM_FANOUT = "fanout"
ARM_SINGLE = "single"

VERDICT_FANOUT_WINS = "fanout_wins"
VERDICT_SINGLE_WINS = "single_wins"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_NOT_TOKEN_MATCHED = "not_token_matched"
VERDICT_INSUFFICIENT_TRIALS = "insufficient_trials"

#: Every verdict this module can return. Exported so a caller can assert the vocabulary rather than
#: string-matching, and so "inconclusive" is a first-class outcome instead of an error path.
VERDICTS = frozenset(
    {
        VERDICT_FANOUT_WINS,
        VERDICT_SINGLE_WINS,
        VERDICT_INCONCLUSIVE,
        VERDICT_NOT_TOKEN_MATCHED,
        VERDICT_INSUFFICIENT_TRIALS,
    }
)


class MeasurementError(ValueError):
    """A malformed observation file. Raised rather than defaulted: a measurement that silently
    substituted a zero for a missing token count would report a token-matched comparison between an
    arm that spent and an arm that did not."""


@dataclass(frozen=True)
class Trial:
    """One graded attempt: a score in points and what it cost in tokens.

    Both are required. A trial with a score and no token count is exactly the shape that produces an
    unmatched comparison presented as a matched one.
    """

    score: float
    tokens: int

    @classmethod
    def from_json(cls, obj: Any) -> Trial:
        if not isinstance(obj, dict):
            raise MeasurementError(f"a trial must be an object, got {type(obj).__name__}")
        for key in ("score", "tokens"):
            if obj.get(key) is None:
                raise MeasurementError(f"trial is missing {key!r}: {obj!r}")
        try:
            score = float(obj["score"])
            tokens = int(obj["tokens"])
        except (TypeError, ValueError) as exc:
            raise MeasurementError(f"trial has non-numeric score/tokens: {obj!r}") from exc
        if tokens < 0:
            raise MeasurementError(f"trial has negative tokens: {obj!r}")
        return cls(score=score, tokens=tokens)


@dataclass
class Arm:
    """One side of the comparison, and the aggregates the verdict reads off it."""

    name: str
    trials: list[Trial] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        """Total spend — summed, not averaged, because in the fan-out design the question is what
        the whole arm cost to produce its answer."""
        return sum(t.tokens for t in self.trials)

    @property
    def tokens_per_trial(self) -> float:
        """Mean spend per trial. The commensurable quantity when the two arms ran DIFFERENT numbers
        of trials — a total is only a spend comparison while the counts match."""
        return self.tokens / len(self.trials) if self.trials else 0.0

    def spend(self, basis: str = SPEND_TOTAL) -> float:
        """This arm's spend on ``basis``.

        Raises on an unknown basis instead of falling back to totals: a silent fallback is exactly
        how the incommensurable comparison reached a published table in the first place.
        """
        if basis == SPEND_TOTAL:
            return float(self.tokens)
        if basis == SPEND_PER_TRIAL:
            return self.tokens_per_trial
        raise MeasurementError(
            f"unknown spend basis {basis!r} — expected one of {sorted(SPEND_BASES)}"
        )

    @property
    def mean_score(self) -> float:
        return sum(t.score for t in self.trials) / len(self.trials) if self.trials else 0.0

    @property
    def spread(self) -> float:
        """Max minus min score within this arm — the arm's own noise, in the same unit as the
        delta. Reported beside the delta because a delta smaller than this measures nothing."""
        if len(self.trials) < 2:
            return 0.0
        scores = [t.score for t in self.trials]
        return max(scores) - min(scores)

    @property
    def tokens_per_point(self) -> float:
        """Spend per mean point. The literature's actual framing: fan-out costs 4-15x for
        single-digit gains where it helps at all, and the best cost outcomes come from PRUNING
        communication rather than adding it — a ratio makes that visible where two raw totals
        do not."""
        return self.tokens / self.mean_score if self.mean_score > 0 else 0.0


@dataclass
class Comparison:
    """The measurement's answer, with everything needed to disbelieve it.

    `notes` carries the reasons a verdict was withheld or downgraded. A report that printed only the
    verdict would be asking to be quoted without its caveats, which is how a 5-point noise band
    becomes "we measured a win".
    """

    work: str
    fanout: Arm
    single: Arm
    verdict: str
    notes: list[str] = field(default_factory=list)
    #: The basis the token-match gate divided on, carried on the answer rather than only on the
    #: call, so a reader of a published ratio can tell which denominator produced it.
    spend_basis: str = SPEND_TOTAL

    @property
    def delta_points(self) -> float:
        """Fan-out mean minus single-agent mean. Positive favours the fan-out."""
        return self.fanout.mean_score - self.single.mean_score

    @property
    def token_ratio(self) -> float:
        """Fan-out spend / single-agent spend on :attr:`spend_basis`. 1.0 is matched."""
        denominator = self.single.spend(self.spend_basis)
        return self.fanout.spend(self.spend_basis) / denominator if denominator else 0.0

    @property
    def conclusive(self) -> bool:
        return self.verdict in (VERDICT_FANOUT_WINS, VERDICT_SINGLE_WINS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "work": self.work,
            "verdict": self.verdict,
            "delta_points": round(self.delta_points, 2),
            "inconclusive_band_points": INCONCLUSIVE_BAND_POINTS,
            "token_ratio": round(self.token_ratio, 4),
            "token_matched": self.verdict != VERDICT_NOT_TOKEN_MATCHED,
            "spend_basis": self.spend_basis,
            "arms": {
                arm.name: {
                    "trials": len(arm.trials),
                    "mean_score": round(arm.mean_score, 2),
                    "spread": round(arm.spread, 2),
                    "tokens": arm.tokens,
                    "tokens_per_point": round(arm.tokens_per_point, 1),
                }
                for arm in (self.fanout, self.single)
            },
            "notes": list(self.notes),
        }


def load_observations(path: str | Path) -> tuple[str, Arm, Arm]:
    """Read an observation file into `(work, fanout_arm, single_arm)`.

    Shape::

        {"work": "<the identical work both arms did>",
         "arms": {"fanout": {"trials": [{"score": 62.0, "tokens": 41000}, ...]},
                  "single": {"trials": [{"score": 59.0, "tokens": 40500}, ...]}}}

    A missing arm is an error, not an empty arm: the comparison is the only thing this module does,
    and one arm plus a default is a single measurement wearing a comparison's clothes.
    """
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MeasurementError(f"no observation file at {p}") from exc
    except json.JSONDecodeError as exc:
        raise MeasurementError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise MeasurementError(f"{p} must contain a JSON object")

    work = str(raw.get("work", "") or "").strip()
    if not work:
        raise MeasurementError(
            f"{p} declares no `work` — the arms must be measured on IDENTICAL work, and a "
            "comparison that cannot name the work cannot claim they were"
        )
    arms_raw = raw.get("arms")
    if not isinstance(arms_raw, dict):
        raise MeasurementError(f"{p} has no `arms` object")

    built: dict[str, Arm] = {}
    for name in (ARM_FANOUT, ARM_SINGLE):
        entry = arms_raw.get(name)
        if not isinstance(entry, dict):
            raise MeasurementError(f"{p} is missing the {name!r} arm")
        trials_raw = entry.get("trials")
        if not isinstance(trials_raw, list) or not trials_raw:
            raise MeasurementError(f"{p}: arm {name!r} has no trials")
        built[name] = Arm(name=name, trials=[Trial.from_json(t) for t in trials_raw])
    return work, built[ARM_FANOUT], built[ARM_SINGLE]


def compare(work: str, fanout: Arm, single: Arm, *, spend_basis: str = SPEND_TOTAL) -> Comparison:
    """Verdict the two arms. Pure: no I/O, no clock.

    Order of checks is the order of honesty. Trials first (n=1 is not a measurement), then token
    matching (an unmatched comparison never happened), then the band (a sub-5-point delta is
    unresolved), then the within-arm spread (a delta below the noise it sits in is unresolved too).
    Each earlier check makes the later ones unaskable, which is why they are gates and not
    annotations on a verdict that was already computed.

    ``spend_basis`` names WHICH spend the token gate compares, and it has no honest default that
    fits every design — see :data:`SPEND_TOTAL` and :data:`SPEND_PER_TRIAL`. It defaults to totals
    because that is amendment (e)'s own design, where the arms' trial counts are unequal on purpose.
    A paired caller must pass :data:`SPEND_PER_TRIAL`, and the basis is recorded on the answer so a
    published ratio names its denominator instead of leaving it to be inferred.
    """
    if spend_basis not in SPEND_BASES:
        raise MeasurementError(
            f"unknown spend basis {spend_basis!r} — expected one of {sorted(SPEND_BASES)}"
        )
    notes: list[str] = []

    thin = [a.name for a in (fanout, single) if len(a.trials) < MIN_TRIALS_PER_ARM]
    if thin:
        notes.append(
            f"arm(s) {', '.join(thin)} have fewer than {MIN_TRIALS_PER_ARM} trials — "
            "run-to-run variance is 1-3 points, so this cannot distinguish a result from a re-run"
        )
        return Comparison(
            work=work,
            fanout=fanout,
            single=single,
            verdict=VERDICT_INSUFFICIENT_TRIALS,
            notes=notes,
            spend_basis=spend_basis,
        )

    fanout_spend = fanout.spend(spend_basis)
    single_spend = single.spend(spend_basis)
    if not fanout_spend or not single_spend:
        notes.append(
            "an arm spent zero tokens — a comparison against a free arm measures nothing about "
            "topology"
        )
        return Comparison(
            work=work,
            fanout=fanout,
            single=single,
            verdict=VERDICT_NOT_TOKEN_MATCHED,
            notes=notes,
            spend_basis=spend_basis,
        )

    ratio = fanout_spend / single_spend
    if abs(ratio - 1.0) > TOKEN_MATCH_TOLERANCE:
        unit = "per trial" if spend_basis == SPEND_PER_TRIAL else "in total"
        notes.append(
            f"token spend differs by {abs(ratio - 1.0) * 100:.1f}% "
            f"(fanout {fanout_spend:.0f} vs single {single_spend:.0f} {unit}), over the "
            f"{TOKEN_MATCH_TOLERANCE * 100:.0f}% match tolerance — give the cheaper arm more "
            "budget (more single-agent samples, or a wider fan-out) and re-measure; the largest "
            "published fan-out win was ~3.75x tokens and its own regression credits spend for 80% "
            "of the variance"
        )
        return Comparison(
            work=work,
            fanout=fanout,
            single=single,
            verdict=VERDICT_NOT_TOKEN_MATCHED,
            notes=notes,
            spend_basis=spend_basis,
        )

    delta = fanout.mean_score - single.mean_score
    if abs(delta) < INCONCLUSIVE_BAND_POINTS:
        notes.append(
            f"|delta| {abs(delta):.2f} < {INCONCLUSIVE_BAND_POINTS} points — under the noise floor "
            "the literature reports for its own results (scorer swaps move scores further than "
            "architecture does), so this is unresolved, not a tie and not a small win"
        )
        return Comparison(
            work=work,
            fanout=fanout,
            single=single,
            verdict=VERDICT_INCONCLUSIVE,
            notes=notes,
            spend_basis=spend_basis,
        )

    worst_spread = max(fanout.spread, single.spread)
    if worst_spread >= INCONCLUSIVE_BAND_POINTS:
        notes.append(
            f"|delta| {abs(delta):.2f} clears the band, but within-arm spread is "
            f"{worst_spread:.2f} points (fanout {fanout.spread:.2f}, single {single.spread:.2f}) — "
            "a delta smaller than the variance it sits in is unresolved; add trials or reduce "
            "per-trial nondeterminism before claiming either direction"
        )
        return Comparison(
            work=work,
            fanout=fanout,
            single=single,
            verdict=VERDICT_INCONCLUSIVE,
            notes=notes,
            spend_basis=spend_basis,
        )

    verdict = VERDICT_FANOUT_WINS if delta > 0 else VERDICT_SINGLE_WINS
    notes.append(
        f"|delta| {abs(delta):.2f} points clears the {INCONCLUSIVE_BAND_POINTS}-point band at a "
        f"token ratio of {ratio:.3f}, with within-arm spread {worst_spread:.2f}"
    )
    return Comparison(
        work=work,
        fanout=fanout,
        single=single,
        verdict=verdict,
        notes=notes,
        spend_basis=spend_basis,
    )


def measure_file(path: str | Path) -> Comparison:
    """Load an observation file and verdict it.

    The observation file carries no basis and is not given one: its shape is amendment (e)'s
    fan-out/single pair, whose arms are budget-matched by adding samples, so totals are the basis
    that design asks for.
    """
    work, fanout, single = load_observations(path)
    return compare(work, fanout, single)
