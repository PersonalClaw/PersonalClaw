"""Retry intelligence, the circuit breaker, and budget accounting.

Three mechanisms that all answer one question — *when should the engine stop spending?* —
and one that answers *how should it spend the next attempt better?*

**Mutation-hint retries (WF2-R4).** A blind retry re-sends the identical prompt and
reproduces the identical failure; the measured difference is stark (hints resolved 7/10
failures on attempt 2 where blind retries resolved ~0). So an attempt is a first-class
record, and retry N+1 receives a correction hint keyed to the failure MODE plus a pruned
digest of what already failed. The digest is pruned deliberately: pasting three full
failed transcripts back into a prompt spends the context that was supposed to fix it.

**The circuit breaker (WF2-R4).** Deterministic and LLM-free, because the failure it
catches — a loop thrashing on the same error forever — is the field's #1 autonomous-run
failure mode, and paying a model to notice it would be both slower and less reliable. It
trips on: iteration cap, N identical error signatures, M byte-identical outputs, or
cumulative token spend. Tripping produces `ESCALATED`, which is deliberately NOT `FAILED`:
"I gave up and need a human" is a different fact from "this broke", and collapsing them
loses the distinction a user needs to act.

**Budgets are SOFT (WF2-R4).** A breach pauses resumably. Killing the run would throw away
completed work the user already paid for; pausing lets them extend and continue. The
pre-charge invariant is what keeps that safe — a resumed run inherits its ledger spend, so
a crash loop cannot mint a fresh budget on every restart.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    Node,
    NodeKind,
    walk,
)

#: Per-mode correction hints. Keyed by failure class so a retry addresses the ACTUAL
#: problem — telling a model "your JSON was malformed" when the real failure was a network
#: timeout wastes the attempt and teaches it nothing.
MUTATION_HINTS = {
    FailureClass.PROTOCOL: (
        "Your previous response could not be parsed in the required format. Return ONLY "
        "the requested structure, with no prose before or after it and no markdown fence."
    ),
    FailureClass.TIMEOUT: (
        "The previous attempt ran out of time. Produce a shorter, more direct response; "
        "do not restate the task or explain your approach."
    ),
    FailureClass.USER: (
        "The previous attempt failed because an input was missing or malformed. Work only "
        "from the inputs actually provided; do not invent values for absent fields."
    ),
    FailureClass.BUDGET: (
        "The previous attempt exceeded its budget. Be substantially more concise."
    ),
    FailureClass.TRANSIENT: (
        "The previous attempt failed for a transient reason. Retry the same work."
    ),
    FailureClass.NETWORK: ("The previous attempt failed on a network error. Retry the same work."),
    FailureClass.PERMISSION: (
        "The previous attempt was refused for lack of permission. Do not retry the "
        "refused operation; report what access is required."
    ),
    FailureClass.INTERNAL: (
        "The previous attempt failed unexpectedly. Retry, and prefer the simplest "
        "approach that satisfies the task."
    ),
}

#: How many prior attempts feed the retry digest. Small on purpose: a digest that grows
#: with every attempt spends the context the correction hint needs.
MAX_DIGEST_ATTEMPTS = 3

#: Consecutive identical error signatures before the breaker trips.
DEFAULT_ERROR_STREAK = 3

#: Byte-identical outputs before the breaker calls it a stall.
DEFAULT_IDENTICAL_STREAK = 2


# ── attempt records ──────────────────────────────────────────────────────────


@dataclass
class Attempt:
    """One try at one node. Journaled per attempt so a retry loop gets actionable
    feedback rather than free prose, and so the flywheel can later see WHICH kinds of
    correction actually worked."""

    attempt: int
    failure_class: str = ""
    error: str = ""
    #: Structured per-issue payload: what was expected, what arrived, the evidence, and
    #: the fix instruction. Free text here would force the next attempt to re-derive it.
    expected: str = ""
    actual: str = ""
    evidence: str = ""
    fix_instruction: str = ""
    severity: str = "error"
    error_signature: str = ""
    tokens: int = 0
    duration_secs: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "failure_class": self.failure_class,
            "error": self.error,
            "expected": self.expected,
            "actual": self.actual,
            "evidence": self.evidence,
            "fix_instruction": self.fix_instruction,
            "severity": self.severity,
            "error_signature": self.error_signature,
            "tokens": self.tokens,
            "duration_secs": round(self.duration_secs, 3),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Attempt:
        d = d or {}
        return cls(
            attempt=int(d.get("attempt", 0) or 0),
            failure_class=str(d.get("failure_class", "") or ""),
            error=str(d.get("error", "") or ""),
            expected=str(d.get("expected", "") or ""),
            actual=str(d.get("actual", "") or ""),
            evidence=str(d.get("evidence", "") or ""),
            fix_instruction=str(d.get("fix_instruction", "") or ""),
            severity=str(d.get("severity", "error") or "error"),
            error_signature=str(d.get("error_signature", "") or ""),
            tokens=int(d.get("tokens", 0) or 0),
            duration_secs=float(d.get("duration_secs", 0.0) or 0.0),
        )


def attempt_from_failure(
    n: int, failure: Failure, *, tokens: int = 0, duration_secs: float = 0.0
) -> Attempt:
    """Build the attempt record from a typed failure. `fix_instruction` comes from the
    failure's own remediation when it has one — that text was written for a human, and it
    is equally the most actionable thing to hand the next attempt."""
    return Attempt(
        attempt=n,
        failure_class=failure.failure_class.value,
        error=failure.cause_plain[:500],
        fix_instruction=failure.remediation or MUTATION_HINTS.get(failure.failure_class, ""),
        error_signature=error_signature(failure),
        tokens=tokens,
        duration_secs=duration_secs,
    )


def error_signature(failure: Failure) -> str:
    """A stable short hash of (class, normalized message).

    Normalized so incidental variation — a changing request id, a timestamp, a line
    number — does not make two occurrences of the SAME error look different. Without that
    normalization the breaker's identical-error streak would never reach 2.
    """
    text = (failure.cause_plain or "").lower()
    cleaned = "".join(" " if ch.isdigit() else ch for ch in text)
    cleaned = " ".join(cleaned.split())[:200]
    raw = f"{failure.failure_class.value}|{cleaned}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def retry_prompt(base_prompt: str, attempts: list[Attempt]) -> str:
    """Compose the next attempt's prompt: the original, plus a correction hint, plus a
    pruned digest of what already failed.

    Implemented as prompt AUGMENTATION rather than a blind redo because that is the whole
    mechanism — the model needs to know what went wrong to do differently. Returns the
    base prompt unchanged when there is nothing to learn from.
    """
    if not attempts:
        return base_prompt
    recent = attempts[-MAX_DIGEST_ATTEMPTS:]
    last = recent[-1]
    try:
        cls = FailureClass(last.failure_class)
    except ValueError:
        cls = FailureClass.INTERNAL
    hint = last.fix_instruction or MUTATION_HINTS.get(cls, "")

    lines = [base_prompt, "", "--- PREVIOUS ATTEMPTS FAILED ---"]
    for a in recent:
        detail = a.error or a.failure_class or "unknown failure"
        lines.append(f"Attempt {a.attempt}: [{a.failure_class}] {detail}")
        if a.expected and a.actual:
            lines.append(f"  expected: {a.expected}")
            lines.append(f"  actual:   {a.actual}")
    if hint:
        lines.extend(["", f"CORRECTION: {hint}"])
    return "\n".join(lines)


# ── circuit breaker ──────────────────────────────────────────────────────────


@dataclass
class BreakerState:
    """Per-loop-node evidence the breaker reasons over. Cheap counters only — the point is
    to catch a thrash at zero model cost."""

    iterations: int = 0
    error_signatures: list[str] = field(default_factory=list)
    output_hashes: list[str] = field(default_factory=list)
    tokens: int = 0

    def record(self, *, signature: str = "", output: Any = None, tokens: int = 0) -> None:
        self.iterations += 1
        self.tokens += int(tokens)
        self.error_signatures.append(signature or "")
        self.output_hashes.append(_hash_output(output))


def _hash_output(value: Any) -> str:
    import json

    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class BreakerVerdict:
    tripped: bool = False
    reason: str = ""
    detail: str = ""


def check_breaker(node: Node, state: BreakerState) -> BreakerVerdict:
    """Should this loop stop? Deterministic, LLM-free, evaluated before each iteration.

    Catches the field's most common autonomous-run failure: a loop that keeps working and
    never converges. A simple max-iteration cap alone misses the informative cases — the
    same error three times running, or byte-identical output twice, are both stalls worth
    naming distinctly, because the remedy differs.
    """
    cfg = node.config or {}

    max_iters = cfg.get("max_iterations")
    if isinstance(max_iters, int) and max_iters > 0 and state.iterations >= max_iters:
        return BreakerVerdict(True, "max_iterations", f"reached {max_iters} iterations")

    streak = cfg.get("error_streak", DEFAULT_ERROR_STREAK)
    if not isinstance(streak, int) or streak < 1:
        streak = DEFAULT_ERROR_STREAK
    sigs = [s for s in state.error_signatures if s]
    if len(sigs) >= streak and len(set(sigs[-streak:])) == 1:
        return BreakerVerdict(
            True, "repeated_error", f"the same error {streak}x in a row ({sigs[-1]})"
        )

    identical = cfg.get("identical_streak", DEFAULT_IDENTICAL_STREAK)
    if not isinstance(identical, int) or identical < 1:
        identical = DEFAULT_IDENTICAL_STREAK
    hashes = state.output_hashes
    if len(hashes) >= identical + 1 and len(set(hashes[-(identical + 1) :])) == 1:
        return BreakerVerdict(True, "identical_output", f"byte-identical output {identical + 1}x")

    cap = cfg.get("max_tokens")
    if isinstance(cap, int) and cap > 0 and state.tokens >= cap:
        return BreakerVerdict(True, "token_cap", f"spent {state.tokens} of {cap} tokens")

    return BreakerVerdict(False)


# ── escalation ───────────────────────────────────────────────────────────────

#: The five typed options a human gets when the engine gives up. A free-text "it failed"
#: leaves the user to invent the next move; these are the moves.
ESCALATION_OPTIONS = ("reassign", "decompose", "revise", "accept_with_limitations", "defer")


def escalation_artifact(
    node_id: str,
    *,
    reason: str,
    detail: str = "",
    attempts: list[Attempt] | None = None,
) -> dict[str, Any]:
    """The first-class record produced when retries are exhausted or the breaker trips.

    Surfaced as a needs-input item, so the run parks on a real decision rather than
    dying silently.
    """
    return {
        "kind": "escalation",
        "node_id": node_id,
        "reason": reason,
        "detail": detail,
        "options": list(ESCALATION_OPTIONS),
        "attempts": [a.to_dict() for a in (attempts or [])],
    }


# ── budgets ──────────────────────────────────────────────────────────────────

#: Warn at 80% so a user can extend BEFORE work stops, not after.
WARN_FRACTION = 0.8


@dataclass
class BudgetVerdict:
    over: bool = False
    warn: bool = False
    reason: str = ""
    spent: int = 0
    cap: int = 0

    @property
    def fraction(self) -> float:
        return (self.spent / self.cap) if self.cap else 0.0


def check_budget(
    spent_tokens: int, cap_tokens: int, *, spent_cost: float = 0.0, cap_cost: float = 0.0
) -> BudgetVerdict:
    """Evaluate a soft budget. `cap == 0` means unbounded, which is the default: a cap the
    user did not ask for that silently halts a run is worse than no cap.

    `warn` is set whenever the 80% line has been crossed — INCLUDING when already over.
    The two are not mutually exclusive on purpose: a single large node can jump from 40%
    straight past the cap, and treating `over` as "no warning needed" is how a user ends up
    with a paused run and no notice that it was coming.
    """
    warn = bool(cap_tokens) and spent_tokens >= int(cap_tokens * WARN_FRACTION)
    if cap_tokens and spent_tokens >= cap_tokens:
        return BudgetVerdict(True, warn, "token budget reached", spent_tokens, cap_tokens)
    if cap_cost and spent_cost >= cap_cost:
        return BudgetVerdict(
            True, warn, f"cost budget reached (${spent_cost:.2f})", spent_tokens, cap_tokens
        )
    if warn:
        return BudgetVerdict(False, True, "approaching token budget", spent_tokens, cap_tokens)
    return BudgetVerdict(False, False, "", spent_tokens, cap_tokens)


def estimate_calls(root: Node) -> dict[str, int]:
    """Static model-call estimate from the spec's topology, for a plan review.

    An ESTIMATE, and named one: loop and foreach counts are unknowable before the run
    (`items` is a binding), so declared `n` is used where present and 1 assumed otherwise.
    Its job is to make "this workflow will make roughly 40 model calls" visible BEFORE the
    user starts it, not to be exact.
    """
    # Kinds that make a model call. `visualize` is one (AMBIENT-SURFACES §5.3) even
    # though it is not in LLM_KINDS (which is the model_tier-bearing set); a plan review
    # that ignored it would under-count a data-heavy pipeline's model spend.
    model_call = (NodeKind.STAGE, NodeKind.INFER, NodeKind.VISUALIZE)
    llm_calls = 0
    actions = 0
    nodes = 0
    for _path, node in walk(root):
        nodes += 1
        multiplier = 1
        if node.kind in model_call:
            llm_calls += multiplier
        elif node.kind == NodeKind.ACTION:
            actions += multiplier
    # Multiply the body of each loop/foreach by its declared size.
    for _path, node in walk(root):
        if node.kind == NodeKind.LOOP and node.body is not None:
            n = (node.config or {}).get("n")
            reps = n if isinstance(n, int) and n > 1 else 1
            body_llm = sum(1 for _p, b in walk(node.body) if b.kind in model_call)
            llm_calls += body_llm * (reps - 1)
        elif node.kind == NodeKind.FOREACH and node.body is not None:
            items = (node.config or {}).get("items")
            reps = len(items) if isinstance(items, list) else 1
            body_llm = sum(1 for _p, b in walk(node.body) if b.kind in model_call)
            llm_calls += body_llm * (max(reps, 1) - 1)
    return {"nodes": nodes, "llm_calls": llm_calls, "actions": actions}
