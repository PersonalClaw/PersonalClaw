"""The judge contract — maker/checker teeth, and the live judge path that enforces them.

**Enforcement is wired (WF2LOO-13).** `engine.dispatch_gate`'s `GateKind.JUDGE` branch asks
the model for the object `judge_instruction` describes, parses it with `parse_judge_json`,
and hands it to `validate_verdict` — which is what decides the gate. `meets_ratchet`,
`compute_overall`, `detect_forbidden_modes` and `aggregate_samples` run underneath it on
every judge gate; `hints_from_dict` parses the `runtime_hints.judge` block the controller
threads in. `apply_judge_contract` applies the same validation to a judge STAGE's output at
the dispatch seam. What is NOT wired is stated in "The seams this does not own" below, so
this docstring stays a description rather than a wish.

A loop that judges its own work converges on whatever the worker finds easiest to
claim. Every mechanism here exists to make a specific degenerate pass impossible
rather than merely discouraged, because prompt doctrine ("be skeptical") is advice
and advice loses to gradient pressure.

**Self-approval is impossible by construction, not by instruction.** The worker actor
may transition a node to `waiting` or `review` — never to `done`. Only a judge or
gate actor can. That is a state-machine rule, so no prompt can talk its way past it.

**A PASS without cited proof is invalid.** `validate_verdict` rejects a PASS carrying
neither `proof` nor `evidence_refs`, because a completion record without proof is a claim
and the whole point of a checker is to stop accepting claims. The rejection is only fair
because the same object generates the prompt: `judge_instruction` names the field and says
out loud that a PASS without it will be refused, so no judge is failed for a requirement it
was never told about.

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

── The enforcement posture, and why it is not an outage (WF2LOO-13, measured 2026-08-12) ──

Giving a never-run control teeth is how a gate becomes an outage, so the population was
measured first. On this tree:

* **7 judge GATES across 7 bundled templates** (`gap-healing`, `goal-pursuit-open-ended`,
  `knowledge-lint`, `knowledge-synthesis`, `publish-article`, `rich-ingest`,
  `thesis-tracker`). **1** of them (`goal-pursuit-open-ended`'s terminal `accept`) already
  asked for this module's exact object in its own prompt — and the engine then appended
  `Respond with EXACTLY ONE word`, contradicting the template. The other 6 asked an open
  question and relied on that appended word.
* **6 templates declare `runtime_hints.judge`, carrying 13 rubric criteria** (not 14 — the
  WF2LOO-12 count was one high). Exactly **1** template has BOTH a judge gate and a rubric,
  so on 6 of the 7 gates the ratchet has nothing to compare and is a no-op by construction.

Three rules keep the teeth off the templates' throat:

1. **The prompt is generated from the object that enforces it.** `judge_instruction` renders
   the closed verdict vocabulary, the proof requirement, the EXACT rubric keys the ratchet
   will look up, and the forbidden modes. A judge is never refused for a requirement it was
   not given, which is the only honest way to enforce a contract on a live population.
2. **An undeclared rubric is not a shortfall.** `meets_ratchet` iterates the DECLARED
   criteria, so a gate whose run declares none returns "ok" with no shortfalls. A template
   that never described convergence cannot be REJECTed into a dead loop for it.
3. **A restated key still scores.** `score_for` matches a criterion exactly, then
   normalized, then by unique containment. Byte-exact key matching would have made
   "not scored" — i.e. a REJECT under STRICT — the likely outcome of a model that wrote
   `"verify command passes"` for `"the verify command passes"`.

And the failure mode this adds is NAMED, never a silent pass: a judge whose answer does not
parse as the contract object fails its gate as `FailureClass.PROTOCOL` with the raw text on
the node output, and a PASS that scored none of a declared rubric is flagged
`protocol_error` rather than being read as "below target". Both are visible in the Run
Ledger's `judge_verdict` event.

── The seams this does not own ──

`JudgeHints.judge_samples` / `sample_count()` are NOT the gate's sample count: the gate reads
`config.judge_samples` per node (default 1, clamped by `engine.MAX_JUDGE_SAMPLES`).
Defaulting to this module's `DEFAULT_JUDGE_SAMPLES` of 3 would have tripled the model spend
of all 7 live gates in a change whose subject is enforcement, so the node keeps the say.
`marginal_threshold` is read only by `Ratchet.RELAXED`; no bundled template declares it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Verdict(str, Enum):
    """The ONE closed verdict enum. Loop nodes route on data, never on prose.

    There used to be two (WF2LOO-13). `verify.Verdict` carried PASS/RETRY/ESCALATE/REJECT for
    the live gate and the verification ladder; this one carried PASS/REJECT/REPLAN/ESCALATE/
    NEEDS_INPUT for a contract nothing called. Two vocabularies over one decision meant
    `engine.py` had to RESTATE the sampling rule rather than import `aggregate_samples`, to
    avoid feeding one enum's values to the other's aggregator. So the sets were merged HERE —
    the module that owns the judge contract — and `verify.Verdict` was deleted. `RETRY`
    survived the merge because it was the only live member this set lacked, and it carries
    real routing: a recoverable TRANSIENT failure the engine retries, as against `REJECT`,
    which stops and asks.

    `cannot_judge` is deliberately a field rather than a verdict: a refusal still
    has to say why, and folding it into the enum would let "I couldn't tell" be
    routed as if it were a decision.
    """

    PASS = "PASS"
    REJECT = "REJECT"
    #: Recoverable: try the producing node again. Kept distinct from REJECT because the
    #: difference is what a human reads — a hiccup versus a dead end.
    RETRY = "RETRY"
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
    #: The rubric criteria that fell short, reported even on a RELAXED pass so a relaxed
    #: acceptance is never silent about what it let through.
    shortfalls: list[str] = field(default_factory=list)
    #: True when the invalidity is about the judge's ANSWER (not an object, unknown verdict,
    #: a PASS that scored none of a declared rubric) rather than about the WORK. The gate
    #: reads it to choose `PROTOCOL` over `USER`: "the judge could not answer in the required
    #: shape" and "the work fell short" send a reader to two different places.
    protocol_error: bool = False

    @property
    def valid(self) -> bool:
        return not self.invalid_reason

    @property
    def passed(self) -> bool:
        """A PASS that survived contract validation. Anything else is not a pass."""
        return self.verdict is Verdict.PASS and self.valid and not self.escalated

    def to_dict(self) -> dict[str, Any]:
        """The record a node output carries, so a template binds VALIDATED data.

        `overall` is the engine-computed aggregate, never the model's — `model_overall`
        keeps the model's own claim beside it precisely so the drift is visible instead of
        being resolved silently in the model's favour.
        """
        return {
            "verdict": self.verdict.value,
            "valid": self.valid,
            "passed": self.passed,
            "reasoning": self.reasoning,
            "scores": dict(self.scores),
            "overall": self.overall,
            "model_overall": self.model_overall,
            "proof": self.proof,
            "evidence_refs": list(self.evidence_refs),
            "cannot_judge": self.cannot_judge,
            "shortfalls": list(self.shortfalls),
            "invalid_reason": self.invalid_reason,
            "protocol_error": self.protocol_error,
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
            "fallback_result": self.fallback_result,
        }


def _normalize_key(text: Any) -> str:
    """Casefold and collapse everything that is not a letter or digit to one space."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def score_for(criterion: str, scores: dict[str, int]) -> int | None:
    """The score a judge gave `criterion`, tolerant of key restatement.

    🔴 This tolerance is what keeps the ratchet from being an outage (WF2LOO-13). Under
    `Ratchet.STRICT` an unscored criterion is a shortfall, so a REJECT; with byte-exact
    lookup, a judge answering `"verify command passes"` for the declared
    `"the verify command passes"` would have failed every PASS in the templates that
    declare a rubric. Three attempts, narrowest first:

    1. exact key;
    2. normalized key (case, punctuation and spacing collapsed);
    3. containment — but ONLY when exactly one key contains the criterion or vice versa.
       An ambiguous partial match is left unscored on purpose: guessing which of two keys
       the judge meant is a routing decision made on noise, and "not scored" is the
       auditable answer.
    """
    if criterion in scores:
        return int(scores[criterion])
    wanted = _normalize_key(criterion)
    if not wanted:
        return None
    normalized = {_normalize_key(k): v for k, v in scores.items()}
    if wanted in normalized:
        return int(normalized[wanted])
    hits = [v for k, v in normalized.items() if k and (wanted in k or k in wanted)]
    return int(hits[0]) if len(hits) == 1 else None


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
        raw = score_for(criterion.criterion, scores)
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
        actual = score_for(criterion.criterion, scores)
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
            protocol_error=True,
            fallback_result=fallback_result,
        )

    try:
        verdict = Verdict(str(raw.get("verdict", "")).upper())
    except ValueError:
        return JudgeVerdict(
            verdict=Verdict.REJECT,
            reasoning=str(raw.get("reasoning", ""))[:2000],
            invalid_reason=f"unknown verdict {raw.get('verdict')!r}",
            protocol_error=True,
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
        result.protocol_error = True
        return result

    ok, shortfalls = meets_ratchet(scores, hints)
    result.shortfalls = list(shortfalls)
    if hints.rubric and not any(score_for(c.criterion, scores) is not None for c in hints.rubric):
        # A PASS that scored NOTHING against a declared rubric is a different failure from a
        # PASS that scored and fell short, and the remediation is different too: the first
        # needs the judge's answer fixed, the second needs the work fixed. Reporting both as
        # "below rubric targets" sends an operator to read a deliverable that was never
        # measured.
        result.invalid_reason = (
            f"PASS scored none of the {len(hints.rubric)} declared rubric criteria"
        )
        result.protocol_error = True
        return result
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

    The four rules, in order — this is the ONE aggregator now. `engine.py` used to restate
    them over its own verdict enum (`_aggregate_gate_verdicts`, deleted in WF2LOO-13),
    because the two vocabularies could not share a function; the merged `Verdict` removed
    the reason for the duplicate:

    1. **Any escalation wins**, whether it came from the contract (`escalated`, i.e. a
       cannot-judge or a contradicted deterministic check) or from the judge saying ESCALATE
       outright. It names something the other samples did not see, and outvoting it would
       discard the one sample that noticed.
    2. **Any forbidden-mode hit wins**, for the same reason: a disqualifier is a fact.
    3. **A PASS needs a strict majority** (2 of 3, never 1 of 2).
    4. **Otherwise the majority rejection stands**, preferring a terminal REJECT over a
       spinning RETRY when the samples split — the safe reading of a split is the one that
       stops and asks.
    """
    if not verdicts:
        return JudgeVerdict(verdict=Verdict.REJECT, invalid_reason="no judge samples")
    if len(verdicts) == 1:
        return verdicts[0]

    for candidate in verdicts:
        if candidate.escalated or candidate.verdict is Verdict.ESCALATE:
            return candidate
    for candidate in verdicts:
        if "forbidden success mode" in candidate.invalid_reason:
            return candidate

    passes = [v for v in verdicts if v.passed]
    if len(passes) * 2 > len(verdicts):
        winner = sorted(passes, key=lambda v: v.overall)[len(passes) // 2]
        return winner
    rejects = [v for v in verdicts if not v.passed]
    terminal = [v for v in rejects if v.verdict is not Verdict.RETRY]
    pool = terminal or rejects
    return sorted(pool, key=lambda v: v.overall)[len(pool) // 2]


# ── the wire shape ──


def parse_judge_json(text: Any) -> dict[str, Any] | None:
    """Extract the contract object from a judge's answer, or None.

    None is the PROTOCOL signal: the caller turns it into a named failure. It is never a
    verdict, because "the judge did not answer in the required shape" and "the judge said
    REJECT" are different facts and only one of them is about the work.

    Tolerant of the two things models do to JSON and nothing else: a ```json fence, and
    prose either side of the object. Deliberately NOT tolerant of a bare verdict word — that
    was the old protocol, and accepting it would let a PASS with no proof through the exact
    precondition this contract exists to apply.
    """
    if text is None:
        return None
    body = str(text).strip()
    if not body:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", body, flags=re.DOTALL)
    if fence:
        body = fence.group(1).strip()
    for candidate in (body, _first_object(body)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_object(text: str) -> str:
    """The first balanced `{...}` in `text`, brace-counted outside string literals.

    A regex cannot do this: the contract object nests (`scores`), and `reasoning` routinely
    contains braces and escaped quotes.
    """
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def judge_instruction(prompt: str, hints: JudgeHints) -> str:
    """The judge's full instruction: the template's rubric prose plus this contract's shape.

    🔴 Generated from the SAME `JudgeHints` the validation reads. That is the whole
    anti-outage argument of WF2LOO-13: every requirement `validate_verdict` will refuse a
    PASS for is stated here first, by name, including the exact `scores` keys `meets_ratchet`
    will look up. A contract enforced against a prompt that never mentioned it is not a gate,
    it is a trap — which is why the one-word prompt could not be given teeth in place.
    """
    lines = [prompt.rstrip(), ""]
    if hints.freedom_level is FreedomLevel.LOW:
        lines.append("Judge per-step compliance: did it do what it was told, step by step?")
    elif hints.freedom_level is FreedomLevel.HIGH:
        lines.append("Judge the OUTCOME against the rubric, not the route taken to it.")
    if hints.ground_truth_sources:
        lines.append(
            "These are MEASUREMENTS and outrank any narration about them: "
            + ", ".join(hints.ground_truth_sources)
        )
    if hints.rubric:
        lines.append("")
        lines.append(
            "Score EVERY criterion below 0, 1 or 2, using the key exactly as written — an "
            "unscored criterion counts as a shortfall:"
        )
        for criterion in hints.rubric:
            lines.append(f'  "{criterion.criterion}" (target {criterion.clamp_target()})')
        if hints.ratchet is Ratchet.STRICT:
            lines.append(
                "Any criterion below its target fails this gate. There is no averaging: a "
                "broken deliverable does not pass on the strength of its documentation."
            )
    if hints.forbidden_success_modes:
        lines.append("")
        lines.append("These passes are forbidden. Do not PASS if the work did any of them:")
        lines.extend(f"  - {mode}" for mode in hints.forbidden_success_modes)
    if hints.hidden_validation_commands:
        # Rendered ONLY here. A worker that can read the hidden checks satisfies them
        # specifically, which is the same as not having them.
        lines.append("")
        lines.append("Run these checks yourself and report what they returned:")
        lines.extend(f"  - {command}" for command in hints.hidden_validation_commands)
    if hints.proof_command:
        lines.append("")
        lines.append(f"Re-run this yourself and cite its output as proof: {hints.proof_command}")
    lines += [
        "",
        "Respond with ONE JSON object and nothing else — no prose either side, no code fence:",
        "{",
        '  "reasoning": "what you checked and what you found",',
        '  "verdict": ' + " | ".join(f'"{v.value}"' for v in Verdict) + ",",
        '  "scores": {"<criterion exactly as written above>": 0},',
        '  "proof": "the command you ran, the file you read, the line you checked",',
        '  "evidence_refs": ["the artifact or output you relied on"],',
        '  "cannot_judge": "why you could not judge, or an empty string"',
        "}",
        "",
        "A PASS carrying neither `proof` nor `evidence_refs` is REJECTED by the engine: a "
        "completion record without proof is a claim, not a verdict.",
        "If you genuinely cannot tell, say why in `cannot_judge` — that escalates to a human, "
        "which is useful. A coin flip dressed as a verdict is not.",
    ]
    return "\n".join(lines)
