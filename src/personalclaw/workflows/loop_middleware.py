"""Loop-node middleware — the escalation VOCABULARY, the nudge texts, and steering.

**The decision moved out (PP-15).** This module used to own a second convergence
decision (`check_middleware`, over a mutable `LoopState`, returning a
`MiddlewareVerdict`) alongside `loop.tick.evaluate`'s. Two implementations of "is this
loop converging?" is how the two engines drifted, so the decision was folded into
`loop.tick.evaluate` — the pure one — and the copy here was DELETED rather than kept
behind a flag. `TickState` now carries the counters `LoopState` held, `Decision` carries
what `MiddlewareVerdict` carried, and `tick.Action` carries the four verdict actions.

What stays is what the decision READS and what acting on it needs: the failure
taxonomy (`FailureClass`, `classify_failure`), the call fingerprint, the ladder vocabulary
(`Rung`, `DEFAULT_LADDER`, `CLASS_ENTRY_RUNG`, `_resolve_ladder`), the corrective
instructions (`nudge_for`), the human-facing brief, and the steering queue. These are
reused BY `loop.tick`, not duplicated in it.

`resilience.check_breaker` remains the trip detector for max iterations, the same error
N times, byte-identical output, and a token cap. The tiers below need more than those
counters, and the response machinery for when one trips lives here.

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
from dataclasses import dataclass
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


def nudge_for(cls: FailureClass, mutations: dict[str, str], detail: str, *, stall: str = "") -> str:
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
