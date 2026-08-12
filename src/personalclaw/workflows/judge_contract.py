"""The judge contract — maker/checker teeth, AUTHORED here and not enforced anywhere yet.

**Read this before trusting a rule below.** Nothing in `src/` calls this module's
enforcement: `validate_verdict`, `meets_ratchet`, `compute_overall`,
`detect_forbidden_modes`, `aggregate_samples` and `hints_from_dict` have zero production
callers. Only the TYPES leave this file — `judge_actors` imports `Isolation`,
`judge_pretier` imports `FallbackCheck`, and `engine.py` deliberately RESTATES the
sampling rule rather than importing `aggregate_samples`. So every rule stated here is a
specification of what a judge SHOULD be held to, not a description of what the running
engine does. `tests/test_workflows_judge_contract.py` proves the rules work on these
functions; it cannot prove they run, because they do not. What the live judges do
instead is measured at the bottom of this docstring.

A loop that judges its own work converges on whatever the worker finds easiest to
claim. Every mechanism here exists to make a specific degenerate pass impossible
rather than merely discouraged, because prompt doctrine ("be skeptical") is advice
and advice loses to gradient pressure.

**Self-approval is impossible by construction, not by instruction.** The worker actor
may transition a node to `waiting` or `review` — never to `done`. Only a judge or
gate actor can. That is a state-machine rule, so no prompt can talk its way past it.

**A PASS without cited proof is invalid — under this contract, which no live judge
consults.** `validate_verdict` rejects a PASS carrying neither `proof` nor
`evidence_refs`, because a completion record without proof is a claim and the whole
point of a checker is to stop accepting claims. That rejection has never fired on a
real run: see the measurement below for why, and for what it would cost to change.

**The deterministic tier runs BEFORE the model, every cycle.** Regex failure patterns,
schema checks, existence gates — microseconds each. Loop judges run every iteration,
so anything rule-solvable that reaches the model is pure waste. It is also the single
biggest token saving available here.

**`fallback_check` is a standing cross-check, not just a degradation path.** When the
judge is available AND the deterministic check disagrees with its PASS, that
disagreement auto-escalates. A judge that passes what `exit 1` failed is either
wrong or being gamed, and both need a human.

**Derivable fields are engine-computed.** The overall score is recomputed from
dimension scores server-side; the model's own overall survives only as metadata.
Otherwise the two drift and the drift is invisible.

**Evidence excludes worker narration.** The judge sees user/spec messages, tool calls
and tool outputs — not the worker's prose about its own work. Prose is exactly the
channel a worker would use to influence a judge, including prose that survives
compaction.

── What the live judges actually do (measured 2026-08-12, `WF2LOO-12`) ──

**Enforcement is NOT wired.** Two live judge paths exist, and neither consults this file:

1. **The judge GATE** — `engine.dispatch_gate`'s `GateKind.JUDGE`, used by 7 gates across 7
   bundled templates. Its prompt ends `Respond with EXACTLY ONE word, one of: PASS, RETRY,
   ESCALATE, REJECT. No other text.`, and the answer is read by `verify.parse_verdict`,
   which extracts the verdict WORD only — over a DIFFERENT four-member enum
   (`verify.Verdict`, carrying RETRY where this module carries REPLAN/NEEDS_INPUT). A bare
   word cannot carry proof, scores or a rubric result, so this contract is not merely
   unused there: it is inexpressible. Enforcing "a PASS without cited proof is invalid"
   against that prompt would invalidate every judge PASS in all 7 templates, which is an
   outage rather than a gate.
2. **The judge STAGE** — 6 bundled templates (`code-project`, `design-project`,
   `diagnose-run`, `general-project`, `goal-pursuit-open-ended`, `goal-pursuit-verifiable`)
   carry a `judge` stage that DOES ask for this module's exact shape: `{reasoning, verdict,
   scores, marginal_value, evidence_refs, proof, cannot_judge}`, with this module's
   five-verdict vocabulary spelled out in the prompt. `dispatch_infer` really does parse it
   (a `config.schema` sets `want_json`). But nothing validates it and nothing READS it:
   `dispatch_infer` returns DONE for any parseable JSON whatever the verdict says, and no
   shipped template binds `nodes.judge.output.*`. A contract-shaped verdict is produced
   every loop iteration and discarded.

The `rubric` in `runtime_hints.judge` (6 templates, 14 criteria) reaches the judge as PROSE
only — inlined into the prompt text, which is exactly what
`test_workflows_loop_templates.py::test_every_rubric_criterion_appears_in_a_judge_prompt`
asserts: the criterion STRING is a substring of some judge prompt. No `target_score` is
ever compared against a score, because `meets_ratchet` has no caller.

Wiring this contract into the live path is its own scoped atom, `WF2LOO-13`, with a
measured blast radius. Until it lands, `test_workflows_judge_contract.py::
test_the_unwired_enforcement_claim_matches_the_live_path` fails if this section and the
live path drift apart in EITHER direction — enforcement gaining a caller while this
docstring still says it has none, or the live gate leaving its one-word prompt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Verdict(str, Enum):
    """The CLOSED verdict enum. Loop nodes route on data, never on prose.

    `cannot_judge` is deliberately a field rather than a verdict: a refusal still
    has to say why, and folding it into the enum would let "I couldn't tell" be
    routed as if it were a decision.
    """

    PASS = "PASS"
    REJECT = "REJECT"
    #: Produces ONLY the remaining steps given the critique — a typed shape for
    #: mid-flight replanning instead of ad-hoc mutation.
    REPLAN = "REPLAN"
    ESCALATE = "ESCALATE"
    NEEDS_INPUT = "NEEDS_INPUT"


class Isolation(str, Enum):
    """How far the judge is separated from the work it judges."""

    #: A fresh session — never the one that produced the output.
    FRESH = "fresh"
    #: A different model FAMILY. Same-family judges share the failure modes they are
    #: supposed to catch, which is a calibration failure one knob deeper than
    #: same-session.
    CROSS_MODEL = "cross_model"


class FallbackCheck(str, Enum):
    """The deterministic check that backs every judge — and cross-checks it."""

    ARTIFACT_EXISTS = "artifact_exists"
    COMMAND_EXIT_CODE = "command_exit_code"
    DIFF_NONEMPTY = "diff_nonempty"


class Ratchet(str, Enum):
    """How rubric shortfalls combine."""

    #: ANY criterion below target fails the stage. No averaging — averaging is how a
    #: broken deliverable passes on the strength of good documentation.
    STRICT = "strict"
    RELAXED = "relaxed"


class FreedomLevel(str, Enum):
    """What the judge should measure.

    Per-step compliance mis-measures a research loop, and outcome-only judging
    mis-measures a checklist. The template says which it is.
    """

    HIGH = "high"  # judged on the outcome rubric
    MEDIUM = "medium"
    LOW = "low"  # judged on per-step compliance


#: Rubric dimensions are scored 0-2, fixed. A wider scale invites fence-sitting in
#: the middle, and the whole point of the ratchet is that a shortfall is legible.
SCORE_MIN = 0
SCORE_MAX = 2

#: N-sample median aggregation on terminal gates. Single-run LLM-judge acceptance was
#: measured to be indistinguishable from noise, so terminal accept/reject takes the
#: median of an odd number of independent samples.
DEFAULT_JUDGE_SAMPLES = 3

#: Marginal-value thresholds seeded from the REAL granularity dial, so the numbers
#: mean what the existing UI already means by them.
GRANULARITY_PRESETS: dict[str, dict[str, float]] = {
    "quick": {"marginal_threshold": 3.0, "consecutive_clean": 1},
    "balanced": {"marginal_threshold": 2.0, "consecutive_clean": 2},
    "exhaustive": {"marginal_threshold": 1.0, "consecutive_clean": 3},
}

#: The forbidden success modes every code-flavoured judge must actively verify did
#: NOT happen. These are the passes a worker under pressure actually produces.
#:
#: Phrased with TWO signal words each, because that is what the matcher requires (see
#: `detect_forbidden_modes`). "output stubbed or hardcoded" missed a judge saying "the
#: return value is hardcoded" — only one signal was present — so hardcoding is its own
#: entry rather than an alternative buried in another phrase.
DEFAULT_FORBIDDEN_MODES = (
    "test deleted or skipped",
    "gate/validation config modified",
    "output stubbed or placeholder",
    "value hardcoded to satisfy assertion",
)


@dataclass
class RubricCriterion:
    """One machine-checkable convergence dimension."""

    criterion: str
    target_score: int = SCORE_MAX
    #: Weight for the engine-computed overall. Equal by default: a template that
    #: wants emphasis says so explicitly rather than relying on ordering.
    weight: float = 1.0

    def clamp_target(self) -> int:
        return max(SCORE_MIN, min(SCORE_MAX, int(self.target_score)))


@dataclass
class JudgeHints:
    """The SEMANTIC half of `runtime_hints` — what "done" means."""

    rubric: list[RubricCriterion] = field(default_factory=list)
    ratchet: Ratchet = Ratchet.STRICT
    stop_condition: dict[str, int] = field(default_factory=lambda: {"consecutive_clean": 2})
    marginal_threshold: float = 2.0
    forbidden_success_modes: list[str] = field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_MODES)
    )
    proof_command: str = ""
    validator_script: str = ""
    #: Rendered ONLY into judge prompts. A worker that can read the hidden checks can
    #: satisfy them specifically, which is the same as not having them.
    hidden_validation_commands: list[str] = field(default_factory=list)
    #: Which node outputs are authoritative MEASUREMENTS rather than worker synthesis.
    #: "The model is a formatter over measured data, never the measurement."
    ground_truth_sources: list[str] = field(default_factory=list)
    judge_isolation: Isolation = Isolation.FRESH
    judge_samples: int = DEFAULT_JUDGE_SAMPLES
    fallback_check: FallbackCheck = FallbackCheck.ARTIFACT_EXISTS
    freedom_level: FreedomLevel = FreedomLevel.HIGH

    @property
    def consecutive_clean(self) -> int:
        """The double-clean rule: exit only after N INDEPENDENT clean passes.

        One clean pass is a sample; two is a signal. This is what stops a loop
        exiting on the judge's first good mood.
        """
        return max(1, int(self.stop_condition.get("consecutive_clean", 2)))

    def sample_count(self) -> int:
        """Odd-forced sample count — an even count has no median."""
        n = max(1, int(self.judge_samples))
        return n if n % 2 else n + 1


def hints_from_dict(raw: Any) -> JudgeHints:
    """Parse `runtime_hints.judge`, tolerating partial and malformed input.

    Lenient by design: a template with a typo'd hint should run with defaults, not
    fail to start. The defaults are the strict ones, so a malformed hint cannot
    accidentally *loosen* the contract.
    """
    if not isinstance(raw, dict):
        return JudgeHints()
    hints = JudgeHints()

    rubric = raw.get("rubric")
    if isinstance(rubric, list):
        parsed: list[RubricCriterion] = []
        for item in rubric:
            if isinstance(item, dict) and item.get("criterion"):
                parsed.append(
                    RubricCriterion(
                        criterion=str(item["criterion"]),
                        target_score=int(item.get("target_score", SCORE_MAX) or SCORE_MAX),
                        weight=float(item.get("weight", 1.0) or 1.0),
                    )
                )
        hints.rubric = parsed

    for enum_field, enum_cls in (
        ("ratchet", Ratchet),
        ("judge_isolation", Isolation),
        ("fallback_check", FallbackCheck),
        ("freedom_level", FreedomLevel),
    ):
        value = raw.get(enum_field)
        if value is not None:
            try:
                setattr(hints, enum_field, enum_cls(str(value)))
            except ValueError:
                logger.debug("unknown %s %r — keeping the strict default", enum_field, value)

    if isinstance(raw.get("stop_condition"), dict):
        hints.stop_condition = {
            k: int(v) for k, v in raw["stop_condition"].items() if isinstance(v, (int, float))
        }
    for num_field in ("marginal_threshold", "judge_samples"):
        if isinstance(raw.get(num_field), (int, float)):
            setattr(hints, num_field, type(getattr(hints, num_field))(raw[num_field]))
    for list_field in (
        "forbidden_success_modes",
        "hidden_validation_commands",
        "ground_truth_sources",
    ):
        if isinstance(raw.get(list_field), list):
            setattr(hints, list_field, [str(x) for x in raw[list_field] if x])
    for str_field in ("proof_command", "validator_script"):
        if isinstance(raw.get(str_field), str):
            setattr(hints, str_field, raw[str_field])
    return hints


# ── The verdict record ──


@dataclass
class JudgeVerdict:
    """One judge decision, validated against the contract."""

    verdict: Verdict
    reasoning: str = ""
    scores: dict[str, int] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    cannot_judge: str = ""
    #: Engine-computed, never taken from the model.
    overall: float = 0.0
    #: The model's own overall, kept only as metadata so drift is visible.
    model_overall: float | None = None
    proof: str = ""
    fallback_result: bool | None = None
    escalated: bool = False
    escalation_reason: str = ""
    invalid_reason: str = ""

    @property
    def valid(self) -> bool:
        return not self.invalid_reason

    @property
    def passed(self) -> bool:
        """A PASS that survived contract validation. Anything else is not a pass."""
        return self.verdict is Verdict.PASS and self.valid and not self.escalated


def compute_overall(scores: dict[str, int], rubric: list[RubricCriterion]) -> float:
    """Recompute the overall from dimension scores, server-side.

    The model's own overall is metadata. If the engine trusted a self-reported
    aggregate, a judge could score every dimension 0 and still report 5 — and
    nothing downstream would notice.
    """
    if not rubric:
        return round(sum(scores.values()) / len(scores), 4) if scores else 0.0
    total = 0.0
    weight_sum = 0.0
    for criterion in rubric:
        raw = scores.get(criterion.criterion)
        if raw is None:
            continue
        total += max(SCORE_MIN, min(SCORE_MAX, int(raw))) * criterion.weight
        weight_sum += criterion.weight
    return round(total / weight_sum, 4) if weight_sum else 0.0


def meets_ratchet(scores: dict[str, int], hints: JudgeHints) -> tuple[bool, list[str]]:
    """Does every criterion clear its target? Returns (ok, shortfalls).

    Under STRICT, any shortfall fails — no averaging. Averaging is how a broken
    deliverable passes on the strength of its documentation.
    """
    shortfalls: list[str] = []
    for criterion in hints.rubric:
        actual = scores.get(criterion.criterion)
        if actual is None:
            shortfalls.append(f"{criterion.criterion}: not scored")
            continue
        if int(actual) < criterion.clamp_target():
            shortfalls.append(f"{criterion.criterion}: {actual} < {criterion.clamp_target()}")
    if hints.ratchet is Ratchet.STRICT:
        return (not shortfalls), shortfalls
    # RELAXED: the weighted overall carries it, but shortfalls are still reported so
    # a relaxed pass is never silent about what it let through.
    overall = compute_overall(scores, hints.rubric)
    return overall >= hints.marginal_threshold / 2.5, shortfalls


def validate_verdict(
    raw: Any,
    hints: JudgeHints,
    *,
    fallback_result: bool | None = None,
) -> JudgeVerdict:
    """Turn a raw judge response into a contract-validated verdict.

    A parse failure is a REJECT with a reason, never an exception: a judge that
    returns malformed JSON has not approved anything, and crashing the run turns a
    judge outage into a lost iteration.
    """
    if not isinstance(raw, dict):
        return JudgeVerdict(
            verdict=Verdict.REJECT,
            invalid_reason="judge response was not an object",
            fallback_result=fallback_result,
        )

    try:
        verdict = Verdict(str(raw.get("verdict", "")).upper())
    except ValueError:
        return JudgeVerdict(
            verdict=Verdict.REJECT,
            reasoning=str(raw.get("reasoning", ""))[:2000],
            invalid_reason=f"unknown verdict {raw.get('verdict')!r}",
            fallback_result=fallback_result,
        )

    scores = {
        str(k): max(SCORE_MIN, min(SCORE_MAX, int(v)))
        for k, v in (raw.get("scores") or {}).items()
        if isinstance(v, (int, float))
    }
    result = JudgeVerdict(
        verdict=verdict,
        reasoning=str(raw.get("reasoning", ""))[:4000],
        scores=scores,
        evidence_refs=[str(x) for x in (raw.get("evidence_refs") or []) if x],
        cannot_judge=str(raw.get("cannot_judge", "")),
        overall=compute_overall(scores, hints.rubric),
        model_overall=(
            float(raw["overall"]) if isinstance(raw.get("overall"), (int, float)) else None
        ),
        proof=str(raw.get("proof", "")),
        fallback_result=fallback_result,
    )

    if result.cannot_judge:
        # A typed escape hatch: parseable refusal rather than a parse failure. It is
        # NOT a pass, and it is not an error either.
        result.verdict = Verdict.ESCALATE
        result.escalation_reason = f"judge could not judge: {result.cannot_judge}"
        result.escalated = True
        return result

    if verdict is not Verdict.PASS:
        return result

    # ── PASS-only preconditions ──

    # Proof attachment: a PASS without cited proof is invalid by contract, because a
    # completion record without proof is just a claim.
    if not (result.proof or result.evidence_refs):
        result.invalid_reason = "PASS without cited proof or evidence refs"
        return result

    ok, shortfalls = meets_ratchet(scores, hints)
    if not ok:
        result.invalid_reason = "PASS below rubric targets: " + "; ".join(shortfalls[:5])
        return result

    forbidden = detect_forbidden_modes(result.reasoning, hints)
    if forbidden:
        result.invalid_reason = f"forbidden success mode admitted: {forbidden}"
        return result

    # The standing cross-check. A judge PASS contradicting the deterministic result
    # is either wrong or being gamed, and both need a human.
    if fallback_result is False:
        result.escalated = True
        result.escalation_reason = (
            f"judge PASS contradicts {hints.fallback_check.value} (deterministic check failed)"
        )
    return result


#: Words that carry no discriminating power inside a forbidden-mode phrase. Stripped
#: before matching, because a mode is written as a human-readable phrase ("test
#: deleted or skipped") that enumerates ALTERNATIVES, not a conjunction.
_MODE_NOISE = frozenset({"and", "the", "was", "were", "been", "with", "that", "this"})


def detect_forbidden_modes(text: str, hints: JudgeHints) -> str:
    """Return the first forbidden success mode the text admits to, or "".

    Matched on the judge's own reasoning: a judge that says "the test was deleted but
    the code looks right" has stated the disqualifier out loud.

    **The match is subject-AND-any-alternative, not all-words.** Measured: requiring
    every long word missed "the test was deleted" against "test deleted or skipped",
    because the phrase lists alternatives — a real admission mentions the subject and
    ONE of them. All-words matching made the whole denylist inert on the exact
    phrasing a judge actually produces, which is the worst kind of failure for a
    control: present, plausible, and doing nothing.
    """
    lowered = (text or "").lower()
    for mode in hints.forbidden_success_modes:
        signals = {
            w for w in re.findall(r"[a-z]+", mode.lower()) if len(w) > 2 and w not in _MODE_NOISE
        }
        if not signals:
            continue
        # Stem to a common prefix so "test"/"tests" and "modified"/"modify" match. A
        # crude 4-char prefix beats a stemmer here: the vocabulary is a fixed handful
        # of phrases, and a dependency for six words is not worth its weight.
        stems = {w[:4] for w in signals}
        present = {stem for stem in stems if stem in lowered}
        # TWO distinct signals, not one and not all. One word ("output", "config")
        # appears in innocent prose constantly; requiring every word made the whole
        # denylist inert on real phrasing — the worst failure for a control: present,
        # plausible, and doing nothing.
        if len(present) >= min(2, len(stems)):
            return mode
    return ""


def aggregate_samples(verdicts: list[JudgeVerdict], hints: JudgeHints) -> JudgeVerdict:
    """Median-aggregate N independent samples for a terminal gate.

    Single-run LLM-judge acceptance was measured to be indistinguishable from noise.
    A terminal PASS therefore requires the sampled MAJORITY to pass **and** no
    forbidden-mode hit anywhere in the set — one sample spotting a disqualifier
    outweighs two that missed it, because a disqualifier is a fact rather than an
    opinion.
    """
    if not verdicts:
        return JudgeVerdict(verdict=Verdict.REJECT, invalid_reason="no judge samples")
    if len(verdicts) == 1:
        return verdicts[0]

    for candidate in verdicts:
        if candidate.escalated:
            return candidate  # any escalation wins: it names a contradiction
    for candidate in verdicts:
        if "forbidden success mode" in candidate.invalid_reason:
            return candidate

    passes = [v for v in verdicts if v.passed]
    if len(passes) * 2 > len(verdicts):
        winner = sorted(passes, key=lambda v: v.overall)[len(passes) // 2]
        return winner
    rejects = [v for v in verdicts if not v.passed]
    return sorted(rejects, key=lambda v: v.overall)[len(rejects) // 2]
