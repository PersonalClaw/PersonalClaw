"""Loop-node middleware — the breaker's next tier, the escalation ladder, and steering.

`resilience.check_breaker` already catches four stalls: max iterations, the same error
N times, byte-identical output, and a token cap. This adds the tiers that need more than
counters, and the response machinery for when one trips.

**Continue → Nudge → Halt, not Continue → Halt.** The existing breaker is binary: it
trips or it doesn't. But most thrash is recoverable if you tell the worker what it is
doing wrong, and halting a run that one corrective sentence would fix is expensive in
exactly the way autonomous execution cannot afford. The nudge tier costs one injected
instruction; the halt tier costs a human.

**Tool-argument fingerprinting.** The same error twice is a signal; the same error from
the same *call* twice is a much stronger one. A worker retrying `pytest tests/foo.py`
identically three times has not learned anything from the failure, and that is
detectable without a model.

**Recoverable classes get headroom, not equality.** A rate limit is not a stall — it is
the world saying "wait". Treating a 429 like a wrong answer burns the escalation ladder
on something that would have resolved itself, so recoverable classes get a wider window
and, crucially, **do not consume an escalation rung**.

**Failure classes route to different arms.** Malformed output wants a cheap resume with
feedback. Wrong work wants an expensive fresh session. A rate limit wants a wait and no
escalation at all. One retry policy for all three is the policy that is wrong twice.

**The interrupt queue is consumed atomically at iteration boundaries.** Mid-iteration
injection would race the worker's own state; consuming at the boundary means the
instruction lands where the worker can act on it, and single-use consumption means a
double-resume cannot replay it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ── Failure classification ──


class FailureClass(str, Enum):
    """What KIND of failure this is. The retry arm follows from it.

    A closed enum because the routing table is exhaustive by construction: an
    unclassified failure falls to `UNKNOWN`, which routes conservatively rather than
    picking an arm by accident.
    """

    #: Bad shape, right intent — cheap to fix with feedback.
    MALFORMED_OUTPUT = "malformed_output"
    #: The worker did the wrong thing. Needs a clean head, not a correction.
    WRONG_WORK = "wrong_work"
    #: The world said wait. NOT a stall.
    RATE_LIMIT = "rate_limit"
    #: Transient infrastructure. Retry as-is.
    TRANSIENT = "transient"
    #: Ran out of room mid-iteration.
    CONTEXT_OVERFLOW = "context_overflow"
    #: The environment is broken (missing binary, permission). No retry will fix it.
    ENVIRONMENT = "environment"
    #: The worker said it could not do this.
    GAVE_UP = "gave_up"
    UNKNOWN = "unknown"


#: Classes that are the world's fault, not the work's. They get wider windows and never
#: consume an escalation rung — burning the ladder on a 429 is how a run that would have
#: succeeded gets surfaced to a human instead.
RECOVERABLE = frozenset({FailureClass.RATE_LIMIT, FailureClass.TRANSIENT})

#: Headroom multiplier for recoverable classes.
RECOVERABLE_HEADROOM = 3

_CLASS_PATTERNS: tuple[tuple[FailureClass, str], ...] = (
    (FailureClass.RATE_LIMIT, r"\b(?:429|rate ?limit(?:ed|ing)?|too many requests|throttl)"),
    (
        FailureClass.CONTEXT_OVERFLOW,
        r"\b(?:context_length_exceeded|prompt is too long|prompt_too_long|maximum context"
        r"|context window (?:exceeded|full))",
    ),
    (
        FailureClass.ENVIRONMENT,
        r"\b(?:command not found|no such file or directory|permission denied"
        r"|modulenotfounderror|executable not found)",
    ),
    (
        FailureClass.TRANSIENT,
        r"\b(?:5\d\d\b|timed? ?out|timeout|connection (?:reset|refused|aborted)"
        r"|temporarily unavailable|service unavailable|econnreset)",
    ),
    (
        FailureClass.MALFORMED_OUTPUT,
        r"\b(?:json ?decode|invalid json|schema (?:validation )?(?:error|failed)"
        r"|failed to parse|unexpected token|validationerror|parse error)",
    ),
    (
        FailureClass.GAVE_UP,
        r"\b(?:i (?:could ?n[o']?t|was unable to)|giv(?:e|ing) up|needs? human)",
    ),
)


def classify_failure(text: str, *, hint: str = "") -> FailureClass:
    """Classify one failure from its text. Deterministic, ordered, zero cost.

    Order matters and is not alphabetical: rate-limit and context-overflow are checked
    FIRST because their messages often also contain generic words ("failed", "error")
    that a broader pattern would claim. Misclassifying a 429 as wrong-work is the
    expensive direction — it spends a fresh session on something that needed a sleep.
    """
    if hint:
        try:
            return FailureClass(hint)
        except ValueError:
            pass
    lowered = (text or "").lower()
    for cls, pattern in _CLASS_PATTERNS:
        if re.search(pattern, lowered):
            return cls
    return FailureClass.UNKNOWN


# ── Tool-argument fingerprinting ──


def call_fingerprint(tool: str, args: Any) -> str:
    """A stable fingerprint of (tool, arguments).

    The same error twice is a signal; the same error from the same CALL twice is much
    stronger — a worker re-running an identical command has learned nothing from the
    failure, and that is detectable without a model.
    """
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(args)
    return hashlib.sha256(f"{tool}\x1f{payload}".encode("utf-8")).hexdigest()[:12]


# ── The ladder ──


class Action(str, Enum):
    """What the middleware decided to do about this iteration."""

    CONTINUE = "continue"
    #: Inject a corrective instruction and keep going — one sentence instead of a human.
    NUDGE = "nudge"
    #: Take an escalation rung: the run continues, but with a changed STRATEGY (fresh
    #: session, different model, workspace reset). Distinct from HALT because these are
    #: things the ENGINE does — collapsing them into a halt makes every middle rung of
    #: the ladder unreachable and turns "try a clean session" into "ask the human".
    ESCALATE = "escalate"
    HALT = "halt"


class Rung(str, Enum):
    """The escalation ladder, in order. Each rung is more expensive than the last."""

    #: Re-prompt with a targeted correction from `failure_mutations`.
    CLASSIFIED_RETRY = "classified_retry"
    #: Discard the session, keep the workspace, hand off a structured summary.
    FRESH_SESSION = "fresh_session"
    MODEL_SWITCH = "model_switch"
    #: Discard the workspace too, and re-run from the spec.
    RESTART_FROM_SCRATCH = "restart_from_scratch"
    #: Ask the human, with a structured brief.
    SURFACE = "surface"


DEFAULT_LADDER = (
    Rung.CLASSIFIED_RETRY,
    Rung.FRESH_SESSION,
    Rung.MODEL_SWITCH,
    Rung.RESTART_FROM_SCRATCH,
    Rung.SURFACE,
)

#: Which rung a failure class STARTS at. Skipping cheap rungs for a class they cannot
#: fix is the point: a fresh session does not fix a missing binary, and a classified
#: retry does not fix work that was aimed at the wrong target.
CLASS_ENTRY_RUNG: dict[FailureClass, Rung] = {
    FailureClass.MALFORMED_OUTPUT: Rung.CLASSIFIED_RETRY,
    FailureClass.WRONG_WORK: Rung.FRESH_SESSION,
    FailureClass.CONTEXT_OVERFLOW: Rung.FRESH_SESSION,
    FailureClass.GAVE_UP: Rung.FRESH_SESSION,
    # No retry fixes a broken environment; go straight to the human.
    FailureClass.ENVIRONMENT: Rung.SURFACE,
    FailureClass.UNKNOWN: Rung.CLASSIFIED_RETRY,
}

#: Proven abandonment values, carried from the plan's field data.
DEFAULT_NO_PROGRESS_STOP = 5
DEFAULT_HYPOTHESIS_ABANDON = 3
DEFAULT_FINGERPRINT_WINDOW = 3


@dataclass
class LoopState:
    """What the middleware remembers about one loop node. Counters only.

    Deliberately not a transcript: this evaluates before every iteration, and anything
    that grows with the run would make the check itself a cost centre.
    """

    iterations: int = 0
    #: (tool, args) fingerprints of failing calls, newest last.
    call_fingerprints: list[str] = field(default_factory=list)
    failure_classes: list[str] = field(default_factory=list)
    #: Scores or progress measures, newest last — for no-progress detection.
    progress_marks: list[float] = field(default_factory=list)
    #: Fingerprints of attempted FIXES, for hypothesis abandonment.
    fix_fingerprints: list[str] = field(default_factory=list)
    escalation_index: int = 0
    #: Attempts spent at the CURRENT rung. `attempt_cap` bounds this, not the ladder's
    #: length — see `_nudge_or_halt`.
    attempts_at_rung: int = 0
    nudges_issued: int = 0
    recoverable_waits: int = 0

    def record_failure(
        self,
        *,
        text: str = "",
        tool: str = "",
        args: Any = None,
        fix: str = "",
        hint: str = "",
    ) -> FailureClass:
        """Record one failed iteration and return its class."""
        self.iterations += 1
        cls = classify_failure(text, hint=hint)
        self.failure_classes.append(cls.value)
        if tool:
            self.call_fingerprints.append(call_fingerprint(tool, args))
        if fix:
            self.fix_fingerprints.append(call_fingerprint("fix", fix))
        if cls in RECOVERABLE:
            self.recoverable_waits += 1
        return cls

    def record_progress(self, mark: float) -> None:
        self.iterations += 1
        self.progress_marks.append(float(mark))

    def reset_after_success(self) -> None:
        """Success resets the counters — a run that recovers is not on thin ice.

        The escalation index resets too: a loop that got unstuck and later gets stuck
        for a DIFFERENT reason deserves the cheap rungs again, and carrying the index
        forward would surface it to a human on its first new problem.
        """
        self.call_fingerprints.clear()
        self.failure_classes.clear()
        self.fix_fingerprints.clear()
        self.escalation_index = 0
        self.attempts_at_rung = 0
        self.nudges_issued = 0


@dataclass
class MiddlewareVerdict:
    """The decision, and everything needed to act on it."""

    action: Action = Action.CONTINUE
    reason: str = ""
    detail: str = ""
    failure_class: FailureClass = FailureClass.UNKNOWN
    rung: Rung | None = None
    #: The corrective instruction to inject, for NUDGE.
    nudge_text: str = ""
    #: Seconds to wait before retrying, for recoverable classes.
    wait_secs: float = 0.0
    #: True when this failure must NOT advance the escalation ladder.
    consumed_rung: bool = True

    def __bool__(self) -> bool:  # pragma: no cover - explicit comparison preferred
        raise TypeError(
            "MiddlewareVerdict has no truth value — compare .action explicitly. "
            "A convenience __bool__ on a verdict object is how `if verdict` came to mean "
            "'is this healthy' where the code meant 'did I get one'."
        )


def _window(cfg: dict, key: str, default: int) -> int:
    raw = cfg.get(key, default)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        return default
    return raw


def check_middleware(
    state: LoopState,
    *,
    breaker_cfg: dict[str, Any] | None = None,
    escalation_cfg: dict[str, Any] | None = None,
    failure_mutations: dict[str, str] | None = None,
) -> MiddlewareVerdict:
    """Decide Continue / Nudge / Halt before the next iteration. LLM-free.

    Evaluated in cost order: the cheapest tier that can decide, decides. A halt is only
    reached when a nudge has already been tried and did not help, because halting a run
    that one corrective sentence would fix is expensive in exactly the way autonomous
    execution cannot afford.
    """
    cfg = breaker_cfg or {}
    esc = escalation_cfg or {}
    mutations = failure_mutations or {}

    last_class = (
        FailureClass(state.failure_classes[-1]) if state.failure_classes else FailureClass.UNKNOWN
    )

    # ── Recoverable classes first: they are not stalls, and must not burn a rung. ──
    if last_class in RECOVERABLE:
        window = _window(cfg, "fingerprint_window", DEFAULT_FINGERPRINT_WINDOW)
        headroom = window * RECOVERABLE_HEADROOM
        recent = state.failure_classes[-headroom:]
        if len(recent) >= headroom and all(FailureClass(c) in RECOVERABLE for c in recent):
            return MiddlewareVerdict(
                action=Action.HALT,
                reason="recoverable_exhausted",
                detail=f"{headroom} consecutive recoverable failures — the world is not clearing",
                failure_class=last_class,
                rung=Rung.SURFACE,
            )
        return MiddlewareVerdict(
            action=Action.CONTINUE,
            reason="recoverable_wait",
            detail=f"{last_class.value} — waiting rather than escalating",
            failure_class=last_class,
            # Exponential-ish backoff without a clock dependency: the caller sleeps.
            wait_secs=min(60.0, 2.0 ** min(6, state.recoverable_waits)),
            consumed_rung=False,
        )

    # ── An environment failure cannot be retried into working. ──
    if last_class is FailureClass.ENVIRONMENT:
        return MiddlewareVerdict(
            action=Action.HALT,
            reason="environment_broken",
            detail="no retry fixes a missing binary or a permission denial",
            failure_class=last_class,
            rung=Rung.SURFACE,
        )

    # ── Identical failing CALL repeated: the worker learned nothing. ──
    window = _window(cfg, "fingerprint_window", DEFAULT_FINGERPRINT_WINDOW)
    prints = state.call_fingerprints
    if len(prints) >= window and len(set(prints[-window:])) == 1:
        return _nudge_or_halt(
            state,
            esc,
            mutations,
            last_class,
            reason="identical_call",
            detail=f"the same failing call {window}x in a row",
        )

    # ── Same FIX attempted repeatedly: the hypothesis is wrong, not the execution. ──
    abandon = _window(cfg, "hypothesis_abandon_after", DEFAULT_HYPOTHESIS_ABANDON)
    fixes = state.fix_fingerprints
    if len(fixes) >= abandon and len(set(fixes[-abandon:])) == 1:
        return _nudge_or_halt(
            state,
            esc,
            mutations,
            last_class,
            reason="hypothesis_exhausted",
            detail=f"the same fix failed {abandon}x — the diagnosis is wrong",
        )

    # ── No progress: scores flat or declining across a long window. ──
    stop = _window(cfg, "no_progress_stop", DEFAULT_NO_PROGRESS_STOP)
    marks = state.progress_marks
    if len(marks) >= stop:
        recent_marks = marks[-stop:]
        if max(recent_marks) <= recent_marks[0]:
            return _nudge_or_halt(
                state,
                esc,
                mutations,
                last_class,
                reason="no_progress",
                detail=f"{stop} iterations without improving on {recent_marks[0]}",
            )

    return MiddlewareVerdict(action=Action.CONTINUE, failure_class=last_class)


def _nudge_or_halt(
    state: LoopState,
    esc: dict[str, Any],
    mutations: dict[str, str],
    cls: FailureClass,
    *,
    reason: str,
    detail: str,
) -> MiddlewareVerdict:
    """Apply the Continue→Nudge→Halt ladder to a confirmed stall.

    The first stall gets a nudge — a corrective instruction, one injected sentence. Only
    a stall that survives its nudge escalates, because the cheap fix has to actually be
    tried before the expensive one is justified.
    """
    ladder = _resolve_ladder(esc)
    attempt_cap = esc.get("attempt_cap", 3)
    if not isinstance(attempt_cap, int) or attempt_cap < 1:
        attempt_cap = 3

    if state.nudges_issued < 1:
        state.nudges_issued += 1
        return MiddlewareVerdict(
            action=Action.NUDGE,
            reason=reason,
            detail=detail,
            failure_class=cls,
            nudge_text=_nudge_for(cls, mutations, detail, stall=reason),
        )

    entry = CLASS_ENTRY_RUNG.get(cls, Rung.CLASSIFIED_RETRY)
    try:
        entry_index = ladder.index(entry)
    except ValueError:
        entry_index = 0
    index = max(state.escalation_index, entry_index)

    # `attempt_cap` bounds attempts WITHIN a rung, not the ladder's length. Treating it
    # as a position cap made `restart_from_scratch` unreachable under the plan's own
    # declared values (attempt_cap 3 against a 5-rung ladder) — a rung that can never
    # be selected is dead configuration that reads as a working feature.
    if state.attempts_at_rung >= attempt_cap:
        index = min(index + 1, len(ladder) - 1)
        state.attempts_at_rung = 0

    if index >= len(ladder) - 1:
        return MiddlewareVerdict(
            action=Action.HALT,
            reason=reason,
            detail=f"{detail}; escalation ladder exhausted",
            failure_class=cls,
            rung=Rung.SURFACE,
        )

    state.escalation_index = index
    state.attempts_at_rung += 1
    rung = ladder[index]
    # SURFACE is the only rung that stops the run; every other rung is an engine action
    # that changes strategy and keeps going. Mapping them all to HALT is what made the
    # middle of the ladder dead code.
    if rung is Rung.SURFACE:
        action = Action.HALT
    elif rung is Rung.CLASSIFIED_RETRY:
        action = Action.NUDGE
    else:
        action = Action.ESCALATE
    return MiddlewareVerdict(
        action=action,
        reason=reason,
        detail=detail,
        failure_class=cls,
        # `stall=reason` here too, not only on the first nudge: measured on a real
        # sequence, cycles 4-5 fell back to the generic "change your approach" while the
        # first nudge got the precise "you ran the identical command" text. The later
        # nudges are the ones a worker most needs specifics from.
        rung=rung,
        nudge_text=_nudge_for(cls, mutations, detail, stall=reason),
    )


def _resolve_ladder(esc: dict[str, Any]) -> tuple[Rung, ...]:
    """Parse a template-declared ladder, falling back to the default.

    An unknown rung name is DROPPED rather than fatal — a template with a typo should
    escalate along the rungs it named correctly, not fail to run.
    """
    raw = esc.get("ladder")
    if not isinstance(raw, list):
        return DEFAULT_LADDER
    rungs: list[Rung] = []
    for item in raw:
        try:
            rungs.append(Rung(str(item)))
        except ValueError:
            logger.debug("unknown escalation rung %r — dropped", item)
    # SURFACE is always the last resort, even if a template forgot it: a ladder with no
    # terminal rung would loop at its top forever.
    if not rungs:
        return DEFAULT_LADDER
    if rungs[-1] is not Rung.SURFACE:
        rungs.append(Rung.SURFACE)
    return tuple(rungs)


#: What each STALL SHAPE means, when the failure class itself is uninformative. An
#: `identical_call` stall with an UNKNOWN class is the common case — the failure text
#: matched no pattern, but "you ran the same command three times" is still precise
#: advice, and far better than "change your approach".
_STALL_NUDGES: dict[str, str] = {
    "identical_call": (
        "You have run the identical command with identical arguments several times and it "
        "failed the same way each time. Do not run it again unchanged — either change the "
        "command, or investigate why it fails before retrying."
    ),
    "hypothesis_exhausted": (
        "You have applied the same fix repeatedly and it has not worked. Your DIAGNOSIS is "
        "wrong, not your execution. State a different hypothesis before changing any more code."
    ),
    "no_progress": (
        "Several iterations have not improved the outcome. Stop refining the current approach "
        "and state explicitly what is blocking progress, then try a structurally different one."
    ),
}


def _nudge_for(
    cls: FailureClass, mutations: dict[str, str], detail: str, *, stall: str = ""
) -> str:
    """The corrective instruction for this failure.

    Precedence: the template's own `failure_mutations` (its author knows what "test
    timeout" means for their workflow), then the failure class, then the stall SHAPE.
    The stall shape is last but not least — when the class is UNKNOWN it is the only
    thing that actually describes what went wrong.
    """
    specific = mutations.get(cls.value)
    if specific:
        return specific
    generic = {
        FailureClass.MALFORMED_OUTPUT: (
            "Your last output did not match the required schema. Return ONLY the JSON "
            "object described, with no prose or code fence."
        ),
        FailureClass.WRONG_WORK: (
            "Re-read the task statement before continuing. Your last attempt addressed "
            "something the task did not ask for."
        ),
        FailureClass.GAVE_UP: (
            "Do not stop at the first obstacle. State precisely what blocked you, then "
            "try a different approach to the same goal."
        ),
        FailureClass.CONTEXT_OVERFLOW: (
            "Summarize your progress so far into the handoff fields, then continue from "
            "the summary rather than the full history."
        ),
    }.get(cls)
    if generic:
        return generic
    by_stall = _STALL_NUDGES.get(stall)
    if by_stall:
        return by_stall
    return f"The run is not progressing ({detail}). Change your approach, not your wording."


# ── The "never silence" brief ──


def structured_brief(
    *,
    goal: str,
    attempts: list[dict[str, Any]],
    where_stuck: str,
    recommendation: str,
    options: list[str] | None = None,
) -> dict[str, Any]:
    """The brief a halt surfaces to the human. Never a raw transcript.

    A transcript makes the user do the diagnosis the engine already did. Verbatim error
    signatures are kept — paraphrasing an error is how the one detail that identifies
    the problem gets lost.
    """
    return {
        "goal": goal,
        "attempts": [
            {
                "n": i + 1,
                "class": a.get("class", ""),
                "error_signature": str(a.get("error_signature", ""))[:400],
                "what_changed": str(a.get("what_changed", ""))[:200],
            }
            for i, a in enumerate(attempts[:8])
        ],
        "where_stuck": where_stuck,
        "recommendation": recommendation,
        "options": options
        or ["reassign", "decompose", "revise", "accept_with_limitations", "defer"],
    }


# ── The interrupt queue ──


@dataclass
class Interrupt:
    """One queued mid-run steering instruction."""

    id: str
    text: str
    created_ts: float
    consumed_ts: float = 0.0

    @property
    def consumed(self) -> bool:
        return self.consumed_ts > 0


class InterruptQueue:
    """Mid-run steering, consumed atomically at iteration boundaries.

    Mid-iteration injection would race the worker's own state. Consuming at the boundary
    means the instruction lands where the worker can act on it — and single-use
    consumption means a double-resume cannot replay it, which is the same discipline the
    human-input continuations already follow.
    """

    def __init__(self) -> None:
        self._items: list[Interrupt] = []
        self._seq = 0

    def push(self, text: str, *, now: float | None = None) -> Interrupt | None:
        """Queue an instruction. Returns it, or None if empty."""
        if not text or not text.strip():
            return None
        self._seq += 1
        item = Interrupt(
            id=f"int-{self._seq}", text=text.strip(), created_ts=now if now is not None else 0.0
        )
        self._items.append(item)
        return item

    def pending(self) -> list[Interrupt]:
        return [i for i in self._items if not i.consumed]

    def consume(self, *, now: float | None = None) -> list[Interrupt]:
        """Take every pending instruction, atomically. Idempotent afterwards.

        Returns them in order queued: the user's second thought usually refines the
        first, so reversing them would apply the refinement before the thing it refines.
        """
        stamp = now if now is not None else (time.time() if now is None else now)
        taken = self.pending()
        for item in taken:
            item.consumed_ts = stamp or 1.0
        return taken

    def as_steering_prompt(self, items: list[Interrupt]) -> str:
        """Render consumed interrupts for injection, with a re-plan instruction.

        The re-ranking instruction matters: an instruction dropped into a running loop
        without one gets treated as extra work appended to the existing plan, when the
        user usually meant it to CHANGE the plan.
        """
        if not items:
            return ""
        lines = "\n".join(f"- {i.text}" for i in items)
        return (
            "[NEW INSTRUCTIONS FROM THE USER — mid-run]\n"
            f"{lines}\n\n"
            "Before your next work cycle: re-rank your remaining sub-goals against these "
            "instructions. They may supersede or reprioritise what you planned, not merely "
            "add to it. State briefly what changed, then continue."
        )
