"""Repeated ad-hoc work → templates; failed stages → typed lessons (§3.2/§3.3 — S74).

Two spokes that share one discipline: a DETERMINISTIC gate chain decides, and a model is consulted
only at the score boundary. §3.2 is explicit that this replaces "pure LLM-prompt branches" — the
previous shape asked a model whether something was template-worthy, which costs a call per
candidate and answers differently on Tuesday.

The chain, in order (LEARN-R13):

1. **Hard pre-gates.** Plan ≥2 steps; no template already surfaced for the run; budget burn ≤80%.
   Near-death plans make bad templates: a run that spent its budget flailing is not a procedure.
2. **A deterministic structural score.** Action-verb diversity, inter-step dependencies,
   parameterizable slots, −1 per hardcoded entity. Free, reproducible, and the thing thresholds get
   tuned against.
3. **The LLM runs ONLY at the score boundary.** A high score auto-FILES with zero model calls; a low
   score is dropped; only the ambiguous middle is worth paying for.
4. **Every negative decision writes a `skipped(reason)` row.** §3.2: the flywheel's negative space
   how thresholds get tuned" — a detector that silently declines is one nobody can calibrate.

**Measured before writing.** `is_environment_failure_claim` — §3.3's deny-filter, the guardrail that
keeps a flaky network from becoming a durable lesson — caught **1 of 4** real environment failures:
"connection refused", `ECONNRESET`, and rate-limit noise all passed straight through. §3.3 routes
every
`step_failed` through it, which made the gap worse, so this session widened it (now 12/12,
with 0 false positives on real lessons — a bare `429` had been filtering "the 429 rate limiter
config
lives in settings.py").

Pure decisions. Nothing here calls a model, writes memory, or files a proposal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── §3.2: the deterministic gate chain ──

#: Minimum plan steps. One step is a command, not a procedure — templating it adds indirection with
#: nothing reusable inside.
MIN_PLAN_STEPS = 2

#: Budget burn above which a run is disqualified. §3.2's "near-death plans make bad templates": a
#: run that spent 80% of its budget getting there was flailing, and encoding the flail as a
#: procedure teaches the expensive path.
MAX_BUDGET_BURN = 0.80

#: Score at or above which a proposal is auto-FILED with ZERO model calls. Filing, not installing —
#: the human-accept invariant is untouched, which is what makes a free auto-file safe.
AUTO_FILE_SCORE = 0.70

#: Score below which a candidate is dropped without a model call. The band between this and
#: `AUTO_FILE_SCORE` is the only place an LLM is worth paying for.
DROP_SCORE = 0.35

#: Penalty per hardcoded entity (§3.2). A "template" full of one project's paths and ids is a
#: transcript: it will match nothing else, and its slots cannot be filled.
HARDCODED_PENALTY = 1.0

#: Cosine similarity at which two ad-hoc specs count as the same shape, and how many prior matches
#: within the window trigger a suggestion (§3.2's plan-similarity detector).
SIMILARITY_THRESHOLD = 0.85
SIMILARITY_MIN_PRIORS = 2
SIMILARITY_WINDOW_DAYS = 30

#: Verbs that describe an ACTION rather than a state. Diversity across these is the signal that a
#: plan has structure — five "check" steps is one step repeated, not a procedure.
_ACTION_VERBS = re.compile(
    r"\b(build|compile|run|test|deploy|fetch|read|write|update|create|delete|review|"
    r"summari[sz]e|analy[sz]e|validate|verify|publish|migrate|sync|notify|render|"
    r"extract|transform|load|clean|format|lint|commit|push|tag|release)\b",
    re.IGNORECASE,
)

#: Things that make a step un-reusable: absolute paths, urls, long ids, quoted literals that look
#: like names. Narrow on purpose: over-detecting penalizes any plan naming a file.
_HARDCODED = re.compile(
    r"(/(?:Users|home|var|tmp|opt)/[\w./-]+)"
    r"|(https?://[\w./-]+)"
    r"|(\b[0-9a-f]{12,}\b)"
    r"|(\b[\w.-]+@[\w.-]+\.\w+\b)",
    re.IGNORECASE,
)

#: A parameterizable slot: `{{var}}`, `$VAR`, or `<placeholder>`. Their presence is the difference
#: between a template and a recording.
_SLOT = re.compile(r"(\{\{[^}]+\}\})|(\$[A-Za-z_][\w]*)|(<[a-z_][\w ]{2,}>)")


class Skip(str, Enum):
    """Why a candidate was declined. §3.2 requires a row for EVERY negative decision.

    Typed rather than prose because these are what thresholds get tuned against: "declined" is
    unfilterable, while a count per reason says which gate is doing the work and which is dead
    weight.
    """

    TOO_FEW_STEPS = "too_few_steps"
    TEMPLATE_EXISTS = "template_exists"
    BUDGET_BURN = "budget_burn"
    LOW_SCORE = "low_score"
    NO_SLOTS = "no_slots"
    TOO_FEW_PRIORS = "too_few_priors"
    STALE_PRIORS = "stale_priors"


@dataclass
class Candidate:
    """One ad-hoc run being considered for templating."""

    run_id: str
    steps: list[str] = field(default_factory=list)
    budget_burn: float = 0.0
    template_surfaced: bool = False
    intent: str = ""

    @property
    def text(self) -> str:
        return " \n".join(self.steps)


@dataclass
class Score:
    """The deterministic structural score, with its components visible.

    Components rather than one number, because §3.2 tunes thresholds from data and a scalar cannot
    say WHICH signal was weak. A candidate rejected for having no slots needs a different fix from
    one rejected for repeating a single verb.
    """

    verb_diversity: float = 0.0
    dependencies: float = 0.0
    slots: float = 0.0
    hardcoded: int = 0

    @property
    def total(self) -> float:
        """Weighted sum, clamped to [0, 1].

        Slots weigh most: a plan with no parameterizable slot cannot be reused however
        well-structured it is, so it is closest to the real question.
        """
        raw = 0.30 * self.verb_diversity + 0.25 * self.dependencies + 0.45 * self.slots
        return max(0.0, min(1.0, raw - HARDCODED_PENALTY * self.hardcoded * 0.1))

    def to_dict(self) -> dict[str, Any]:
        return {
            "verb_diversity": round(self.verb_diversity, 4),
            "dependencies": round(self.dependencies, 4),
            "slots": round(self.slots, 4),
            "hardcoded": self.hardcoded,
            "total": round(self.total, 4),
        }


def structural_score(candidate: Candidate) -> Score:
    """Score a candidate's shape. Zero LLM calls, fully reproducible.

    Reproducibility is the point: §3.2 replaced "pure LLM-prompt branches" with this because a model
    asked "is this template-worthy" costs a call per candidate and answers differently daily.
    """
    steps = [s for s in candidate.steps if s and s.strip()]
    if not steps:
        return Score()

    verbs = {m.group(0).lower() for s in steps for m in _ACTION_VERBS.finditer(s)}
    # Diversity against step COUNT, not against a fixed target: three distinct verbs across three
    # steps is a structured plan, while three across twelve steps is nine steps of repetition.
    verb_diversity = min(1.0, len(verbs) / max(1, len(steps)))

    # A dependency is a step referring to an earlier one's output. Approximated by back-references
    # ("the above", "it", "that result", "step 2") — cheap, and the alternative is a parser for
    # prose.
    back_refs = sum(
        1
        for s in steps[1:]
        if re.search(
            r"\b(the (?:above|previous|result|output)|step \d|that (?:result|output)|it)\b",
            s,
            re.IGNORECASE,
        )
    )
    dependencies = min(1.0, back_refs / max(1, len(steps) - 1))

    slot_count = len(_SLOT.findall(candidate.text))
    slots = min(1.0, slot_count / max(1, len(steps)))

    hardcoded = len(_HARDCODED.findall(candidate.text))
    return Score(
        verb_diversity=verb_diversity,
        dependencies=dependencies,
        slots=slots,
        hardcoded=hardcoded,
    )


class Action(str, Enum):
    """What the gate chain decided.

    `CONSULT` is the only branch that costs anything, the whole design: free at both extremes,
    paid only in the ambiguous middle.
    """

    AUTO_FILE = "auto_file"
    CONSULT = "consult"
    SKIP = "skip"


@dataclass
class GateDecision:
    """The chain's verdict for one candidate."""

    action: str
    score: Score = field(default_factory=Score)
    reason: str = ""
    skip_reason: str = ""

    @property
    def costs_a_model_call(self) -> bool:
        return self.action == Action.CONSULT.value

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.action,
            "score": self.score.to_dict(),
            "reason": self.reason,
        }
        if self.skip_reason:
            out["skip_reason"] = self.skip_reason
        return out


def gate(candidate: Candidate) -> GateDecision:
    """Run the deterministic chain. Pure, and never calls a model itself.

    Hard pre-gates first, cheapest and most decisive: a one-step plan cannot become a useful
    template however it scores, so scoring it is waste.

    Every negative branch names a TYPED skip reason (§3.2's negative space). A detector silently
    declines is one nobody can calibrate — the reason counts are what say which gate earns its
    place.
    """
    steps = [s for s in candidate.steps if s and s.strip()]
    if len(steps) < MIN_PLAN_STEPS:
        return GateDecision(
            action=Action.SKIP.value,
            skip_reason=Skip.TOO_FEW_STEPS.value,
            reason=f"{len(steps)} step(s); one step is a command, not a procedure",
        )
    if candidate.template_surfaced:
        return GateDecision(
            action=Action.SKIP.value,
            skip_reason=Skip.TEMPLATE_EXISTS.value,
            reason="a template already surfaced for this run, so there is no library gap to fill",
        )
    if candidate.budget_burn > MAX_BUDGET_BURN:
        return GateDecision(
            action=Action.SKIP.value,
            skip_reason=Skip.BUDGET_BURN.value,
            reason=f"burned {candidate.budget_burn:.0%} of budget; a run that flailed "
            "teaches the expensive path",
        )

    score = structural_score(candidate)
    if score.slots <= 0:
        return GateDecision(
            action=Action.SKIP.value,
            score=score,
            skip_reason=Skip.NO_SLOTS.value,
            reason="no parameterizable slot — this is a recording of one run, not a template",
        )
    if score.total >= AUTO_FILE_SCORE:
        return GateDecision(
            action=Action.AUTO_FILE.value,
            score=score,
            reason=f"structural score {score.total:.2f} clears {AUTO_FILE_SCORE:.2f} — no "
            "model call (filing, not installing)",
        )
    if score.total < DROP_SCORE:
        return GateDecision(
            action=Action.SKIP.value,
            score=score,
            skip_reason=Skip.LOW_SCORE.value,
            reason=f"structural score {score.total:.2f} is below {DROP_SCORE:.2f}",
        )
    return GateDecision(
        action=Action.CONSULT.value,
        score=score,
        reason=f"score {score.total:.2f} sits between {DROP_SCORE:.2f} and {AUTO_FILE_SCORE:.2f} — "
        "the only band where a model call is worth paying for",
    )


def similarity_verdict(
    *,
    matches: list[tuple[str, float, float]],
    now: float,
    threshold: float = SIMILARITY_THRESHOLD,
    min_priors: int = SIMILARITY_MIN_PRIORS,
    window_days: float = SIMILARITY_WINDOW_DAYS,
) -> GateDecision:
    """The plan-similarity detector: "you've built this three times — save as template?"

    `matches` is `(run_id, cosine, age_days)`. Both filters matter and they fail differently: a
    below-threshold match is a different plan, while an out-of-window match is the same plan from a
    project that ended. Counting either would propose a template for work nobody does any more.
    """
    fresh = [m for m in matches or [] if m[1] >= threshold and m[2] <= window_days]
    stale = [m for m in matches or [] if m[1] >= threshold and m[2] > window_days]
    if len(fresh) < max(1, min_priors):
        if stale:
            return GateDecision(
                action=Action.SKIP.value,
                skip_reason=Skip.STALE_PRIORS.value,
                reason=f"{len(stale)} similar plan(s), all older than {window_days:g} days — the "
                "same plan from a project that ended",
            )
        return GateDecision(
            action=Action.SKIP.value,
            skip_reason=Skip.TOO_FEW_PRIORS.value,
            reason=f"{len(fresh)} similar plan(s) in the window; {min_priors} needed before "
            "calling it a pattern",
        )
    return GateDecision(
        action=Action.AUTO_FILE.value,
        reason=f"{len(fresh)} plans within {window_days:g} days scored ≥{threshold:.2f} similar — "
        "built the same thing repeatedly",
    )


# ── §3.3: typed failure data ──


class FailureMode(str, Enum):
    """The first-class failure dimension §3.3 (LEARN-R8a) puts on the Run Ledger.

    A closed enum, so `failure_distribution` is computable and the refiner targets the DOMINANT
    mode. Prose failure text cannot be counted, and a refiner that cannot count cannot choose..
    """

    SCHEMA_VIOLATION = "schema_violation"
    CONSTRAINT_VIOLATION = "constraint_violation"
    SPEC_MISMATCH = "spec_mismatch"
    TIMEOUT = "timeout"
    ENVIRONMENT = "environment"
    #: The RCA taxonomy seed (§3.3): code / config / data / infra / dependency / process.
    CODE = "code"
    CONFIG = "config"
    DATA = "data"
    INFRA = "infra"
    DEPENDENCY = "dependency"
    PROCESS = "process"
    #: Nothing matched. Distinct from a guess: an unclassified failure must be visible as such, or
    #: the distribution silently attributes it to whichever mode the classifier leans toward.
    UNKNOWN = "unknown"


FAILURE_MODES: tuple[str, ...] = tuple(m.value for m in FailureMode)

#: Modes that are conditions of the WORLD, not of the work. §3.3's deny-filter rule as a set: a
#: lesson from one of these teaches the agent to refuse a valid action later.
NON_LESSON_MODES: frozenset[str] = frozenset(
    {FailureMode.ENVIRONMENT.value, FailureMode.INFRA.value, FailureMode.TIMEOUT.value}
)

_MODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        FailureMode.SCHEMA_VIOLATION.value,
        re.compile(
            r"\b(schema|json ?decode|validation ?error|invalid (?:json|payload|shape)|"
            r"unexpected (?:field|key)|pydantic|missing required (?:field|key))\b",
            re.I,
        ),
    ),
    (
        FailureMode.CONSTRAINT_VIOLATION.value,
        re.compile(
            r"\b(constraint|unique|foreign key|not null|integrity ?error|"
            r"violates|assertion ?(?:error|failed))\b",
            re.I,
        ),
    ),
    (
        FailureMode.TIMEOUT.value,
        re.compile(r"\b(timed? ?out|timeout|deadline exceeded|took too long)\b", re.I),
    ),
    (
        FailureMode.DEPENDENCY.value,
        re.compile(
            r"\b(no module named|import ?error|module not found|package .* not|"
            r"unresolved (?:import|dependency)|version conflict)\b",
            re.I,
        ),
    ),
    (
        FailureMode.CONFIG.value,
        re.compile(
            r"\b(not configured|missing (?:env|environment|config|setting)|"
            r"unset (?:variable|env)|no such (?:profile|credential))\b",
            re.I,
        ),
    ),
    (
        FailureMode.SPEC_MISMATCH.value,
        re.compile(
            r"\b(does not match the (?:spec|contract)|output ?contract|"
            r"expected .* but (?:got|received))\b",
            re.I,
        ),
    ),
    (
        FailureMode.DATA.value,
        re.compile(
            r"\b(empty (?:result|response|file)|no rows|malformed (?:row|record|csv)|"
            r"encoding ?error|unicode ?(?:decode|error))\b",
            re.I,
        ),
    ),
    (
        FailureMode.CODE.value,
        re.compile(
            r"\b(traceback|attribute ?error|type ?error|name ?error|index ?error|"
            r"key ?error|zero ?division)\b",
            re.I,
        ),
    ),
)


def classify_failure(text: str) -> str:
    """Map failure text onto the closed mode enum.

    The ENVIRONMENT check runs FIRST and wins outright, because §3.3's guardrail is absolute: an
    environment failure must never become a lesson, and a message that is both (a refused
    a traceback) is still the world's fault, not the work's.

    Unmatched text is `UNKNOWN`, never a guess. An unclassified failure has to be visible as such,
    or `failure_distribution` silently attributes it to whichever mode the pattern list leans
    toward — and the refiner then targets a dominant mode that does not exist.
    """
    if not text:
        return FailureMode.UNKNOWN.value
    from personalclaw.after_turn_review import is_environment_failure_claim

    if is_environment_failure_claim(text):
        return FailureMode.ENVIRONMENT.value
    for mode, pattern in _MODE_PATTERNS:
        if pattern.search(text):
            return mode
    return FailureMode.UNKNOWN.value


def failure_distribution(failures: list[str]) -> dict[str, int]:
    """Counts per failure mode, for the refiner's dominant-mode targeting (§3.3a).

    Returns only NON-ZERO modes, so a reader sees what is actually happening rather than a table of
    twelve zeros — and so a caller cannot mistake an absent mode for a measured zero.
    """
    counts: dict[str, int] = {}
    for text in failures or []:
        mode = classify_failure(str(text))
        counts[mode] = counts.get(mode, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def dominant_mode(failures: list[str]) -> str:
    """The mode the refiner should target, or `""` when nothing is actionable.

    `UNKNOWN` and the environment-class modes are EXCLUDED from being dominant: a refiner cannot
    fix an unclassified failure and must not try to fix the network. Returning "" rather than the
    raw top mode is what stops it proposing against a target it cannot influence.
    """
    for mode, _count in failure_distribution(failures).items():
        if mode != FailureMode.UNKNOWN.value and mode not in NON_LESSON_MODES:
            return mode
    return ""


def lesson_worthy(text: str) -> tuple[bool, str]:
    """Whether a failure should become a lesson proposal. Returns `(worthy, reason)`.

    §3.3's guardrail, as a function with the reason attached. The refusal is as important as the
    accept:
    a `skipped(reason)` row for a filtered failure makes the filter tunable; without it a
    widened pattern list would silently start eating real lessons.
    """
    mode = classify_failure(text)
    if mode in NON_LESSON_MODES:
        return (
            False,
            f"{mode} is a condition of the environment; a lesson from it would teach the "
            "agent to refuse a valid action later",
        )
    if mode == FailureMode.UNKNOWN.value:
        return False, "the failure could not be classified, so there is nothing specific to learn"
    return True, ""


@dataclass
class LessonKey:
    """The (template, failure_mode) key §3.3b stores failed-stage lessons under.

    Keyed rather than free-floating so the lesson can be RE-INJECTED on future runs of the same
    template — §3.3 calls a lesson "a persistent mutation hint", and a hint nobody can look up by
    template is a note in a drawer.
    """

    template: str
    mode: str
    signature: str = ""

    @property
    def key(self) -> str:
        parts = [self.template or "unknown", self.mode or FailureMode.UNKNOWN.value]
        if self.signature:
            parts.append(self.signature)
        return "lesson." + ":".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "mode": self.mode,
            "signature": self.signature,
            "key": self.key,
        }


def dedupe_signature(text: str, *, limit: int = 10) -> str:
    """The collapsed signature §3.3b keys lessons by.

    Reuses the refiner's noise-stripping so the same failure produces the same signature in BOTH
    spokes. Two signature schemes would make a clustered failure and its lesson un-joinable — the
    refiner would target a cluster whose lesson it could not find.
    """
    from personalclaw.learning.refiner import failure_signature

    return " ".join(failure_signature(text).split()[:limit])
