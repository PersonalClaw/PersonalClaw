"""Actor-based transitions and judge isolation — one invariant enforced, one authored.

Two invariants live here. Both exist because the alternative — telling the model not to
approve its own work — is advice, and advice does not survive a worker that is being
scored on completion. **They are not equally live, and this docstring used to claim they
were** (WF2LOO-15):

* **ENFORCED — judge isolation.** `plan_judge_session` / `validate_judge_model` are called
  from `engine.dispatch_gate` (`engine.py:1308-1311`), which refuses a `cross_model` gate
  whose candidate judge shares the worker's model family. That seam was itself dead once
  and was wired at S146; the comment there records it.
* **AUTHORED, NOT ENFORCED — the worker-transition rule.** `check_transition` and
  `resolve_transition` have NO production caller: the state machine has no ACTOR at a node
  transition to check. `controller.py`'s `actor` parameter belongs to the *mutation* queue,
  not to node state, so there is currently nothing to pass. Wiring it means introducing an
  actor into the transition path — real work, owned by `WF2LOO-13` with the rest of the
  unwired judge surface, not a one-liner.
* Also authored and unwired for the same reason: `blind_provenance` and
  `assemble_judge_evidence`. The live judge gate sends a one-word prompt and parses the
  word (`engine.py`'s `dispatch_gate`), so there is no message-list evidence to blind yet.

Read the two sections below as a specification of the rule, then, not as a description of
what runs. `tests/test_workflows_judge_actors_claims.py` fails if either half of that
statement stops being true — including if someone wires the transition rule and forgets to
update this text.

**The worker actor may never transition a node to `done`.** It can reach `waiting`
(work parked) or `review` (work submitted). The terminal transition belongs to a judge
or gate actor. This is a state-machine rule, so no amount of prompt engineering can
route around it: a worker claiming completion produces a `review` transition, which
is a *request* for adjudication rather than the adjudication.

**A judge never runs on the tier and session that produced the work.** Same-session
judging asks a model to disagree with its own reasoning trace, which it is measurably
poor at. `cross_model` goes one knob further and requires a different model FAMILY —
same-family judges share the blind spots they are supposed to catch, so a same-family
"independent" judge is a calibration failure wearing the costume of a control.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from personalclaw.workflows.judge_contract import Isolation

logger = logging.getLogger(__name__)


class Actor(str, Enum):
    """Who is asking for a transition.

    A closed enum because the whole invariant rests on the distinction: an unknown
    actor string defaulting to "probably fine" would be the hole this closes.
    """

    #: Produces the work. May park or submit; may NEVER complete.
    WORKER = "worker"
    #: Adjudicates. The only actor that may reach a terminal done.
    JUDGE = "judge"
    #: A deterministic gate — also terminal-capable, because it is not a claim.
    GATE = "gate"
    #: The engine itself: timeouts, cancellation, breaker trips.
    ENGINE = "engine"
    #: The human. Outranks everything.
    OWNER = "owner"


#: The states a WORKER may move a node into. `done` is deliberately absent — that is
#: the entire mechanism. `review` is the worker's way of saying "I believe this is
#: finished", which is a request, not a verdict.
WORKER_ALLOWED = frozenset({"running", "waiting", "review", "failed", "no_change"})

#: Terminal-capable actors. GATE is included because a deterministic check is evidence
#: rather than an opinion; ENGINE and OWNER because a timeout and a human decision are
#: both outside the maker/checker question.
TERMINAL_ACTORS = frozenset({Actor.JUDGE, Actor.GATE, Actor.ENGINE, Actor.OWNER})

#: The states only a terminal-capable actor may set.
TERMINAL_STATES = frozenset({"done", "degraded", "escalated", "cancelled", "discarded"})


@dataclass(frozen=True)
class TransitionRuling:
    """Whether a transition is permitted, and why not."""

    allowed: bool
    reason: str = ""
    #: What the transition was rewritten to, when it was redirected rather than
    #: refused. A worker claiming `done` becomes `review` — refusing outright would
    #: strand the run, while silently accepting would defeat the invariant.
    redirected_to: str = ""


def check_transition(actor: Actor | str, target_state: str) -> TransitionRuling:
    """Rule on one transition. The single place the invariant lives.

    A worker's `done` is REDIRECTED to `review` rather than rejected: the work may
    genuinely be finished, and the correct response to "I think I'm done" is to route
    it to a checker, not to error.
    """
    try:
        who = Actor(str(getattr(actor, "value", actor)))
    except ValueError:
        # An unknown actor gets the most restrictive treatment. Defaulting to
        # permissive here would be the hole this module closes.
        return TransitionRuling(False, f"unknown actor {actor!r} — refusing terminal authority")

    state = str(target_state or "").lower()

    if who in TERMINAL_ACTORS:
        return TransitionRuling(True)

    if state in WORKER_ALLOWED:
        return TransitionRuling(True)

    if state == "done":
        return TransitionRuling(
            False,
            "the worker actor may not complete its own work — routed to review for adjudication",
            redirected_to="review",
        )

    return TransitionRuling(
        False, f"the worker actor may not set state {state!r}", redirected_to="review"
    )


def resolve_transition(actor: Actor | str, target_state: str) -> tuple[str, str]:
    """Apply the ruling: returns (effective_state, note).

    The note is non-empty exactly when something was redirected, so a caller can
    journal the fact — an invariant that fires silently is indistinguishable from one
    that never fires, and this one needs to be auditable.
    """
    ruling = check_transition(actor, target_state)
    if ruling.allowed:
        return str(target_state), ""
    if ruling.redirected_to:
        logger.info("actor invariant: %s", ruling.reason)
        return ruling.redirected_to, ruling.reason
    return "failed", ruling.reason


# ── Judge isolation ──


@dataclass
class JudgeSessionSpec:
    """How to spawn a judge, given what produced the work."""

    fresh_session: bool = True
    #: The model family to avoid, when cross-model isolation is required.
    avoid_family: str = ""
    require_different_family: bool = False
    #: Provenance-blinded: which retry/iteration produced the output is stripped, so
    #: the judge cannot infer "this is attempt 4, they must be close by now".
    strip_provenance: bool = True
    reason: str = ""


def _family_of(model_id: str) -> str:
    """The model FAMILY, not the exact model.

    Crude prefix extraction on purpose: the question is only ever "is this a different
    family", and a registry of exact model lineages would need updating with every
    provider release — going stale in the direction of falsely reporting independence.
    """
    lowered = (model_id or "").lower()
    for family in ("claude", "gpt", "gemini", "llama", "qwen", "mistral", "deepseek", "cohere"):
        if family in lowered:
            return family
    return lowered.split("-")[0] if lowered else ""


def plan_judge_session(
    *,
    isolation: Isolation | str = Isolation.FRESH,
    worker_session_key: str = "",
    worker_model: str = "",
) -> JudgeSessionSpec:
    """Decide how the judge must be spawned for this work.

    Always a fresh session: asking a model to disagree with its own reasoning trace is
    something it is measurably poor at, and the trace is right there in the context.
    """
    try:
        mode = Isolation(str(getattr(isolation, "value", isolation)))
    except ValueError:
        mode = Isolation.FRESH  # unknown value → the stricter default

    spec = JudgeSessionSpec(
        fresh_session=True,
        strip_provenance=True,
        reason=f"isolated from worker session {worker_session_key or '<unknown>'}",
    )
    if mode is Isolation.CROSS_MODEL:
        spec.require_different_family = True
        spec.avoid_family = _family_of(worker_model)
        spec.reason += f"; must not use the {spec.avoid_family or 'worker'} family"
    return spec


def validate_judge_model(spec: JudgeSessionSpec, candidate_model: str) -> tuple[bool, str]:
    """Is this model an acceptable judge under the spec?

    Returns (ok, reason). A same-family judge under `cross_model` is refused with a
    reason rather than silently accepted, because the whole value of cross-model
    isolation is that it is actually cross-model.
    """
    if not spec.require_different_family:
        return True, ""
    candidate_family = _family_of(candidate_model)
    if not candidate_family:
        return False, "cannot determine the candidate judge's model family"
    if candidate_family == spec.avoid_family:
        return (
            False,
            f"cross_model isolation requires a different family; {candidate_model!r} is also "
            f"{candidate_family!r}",
        )
    return True, ""


def blind_provenance(text: str) -> str:
    """Strip retry/iteration markers from evidence handed to a judge.

    "Attempt 4 of 5" tells a judge how much patience is left, which is exactly the
    pressure that produces a lenient pass.
    """
    import re

    if not text:
        return text
    patterns = (
        (r"\battempt\s+\d+(?:\s*(?:of|/)\s*\d+)?\b", "[attempt redacted]"),
        (r"\bretry\s*#?\d+\b", "[retry redacted]"),
        (r"\biteration\s+\d+(?:\s*(?:of|/)\s*\d+)?\b", "[iteration redacted]"),
        (r"\bcycle\s+\d+(?:\s*(?:of|/)\s*\d+)?\b", "[cycle redacted]"),
        (r"\b(?:final|last)\s+(?:attempt|try|chance)\b", "[attempt redacted]"),
    )
    out = text
    for pattern, replacement in patterns:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


#: Evidence roles the judge MAY see. Worker narration is absent by design: prose is
#: precisely the channel a worker would use to influence a judge, and it survives
#: compaction, so excluding it structurally beats filtering it.
JUDGE_EVIDENCE_ROLES = frozenset({"user", "spec", "tool_call", "tool_output", "system"})


def assemble_judge_evidence(messages: list[dict], *, blind: bool = True) -> list[dict]:
    """Build the judge's evidence transcript: measurements, not narration.

    Structurally excludes assistant/worker prose. A worker cannot argue its way to a
    PASS if its arguments never reach the judge — which is a stronger guarantee than
    any instruction to discount them.
    """
    out: list[dict] = []
    for message in messages or []:
        role = str(message.get("role", "")).lower()
        if role not in JUDGE_EVIDENCE_ROLES:
            continue
        item = dict(message)
        if blind:
            content = item.get("content")
            if isinstance(content, str):
                item["content"] = blind_provenance(content)
        out.append(item)
    return out
