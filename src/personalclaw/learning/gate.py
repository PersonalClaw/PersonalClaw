"""The LearningGate — one eligibility answer per event, consumed by every cadence.

Before this module, eligibility was computed independently at three sites and
*skipped entirely* at a fourth:

- ``chat_runner`` recomputed ``should_review(...)`` for the memory review and
  again for the skill-ladder review — same six arguments, two call sites, two
  chances to drift;
- preference-facet capture ran with **no** eligibility check beyond the ephemeral
  flag, deliberately, so a cheap heuristic wouldn't be suppressed by an
  expensive-review threshold;
- the incognito/temporary suppression came from a process-global registry
  consulted at whichever sites remembered to consult it.

Each of those was locally defensible. Together they mean "is this session allowed
to teach the system anything?" had no single answer — and the run-end cadence
would have made a fourth.

The gate separates the two questions those sites had conflated:

**Permission** (may this event teach us anything at all?) is a property of the
*session*: learning enabled, not ephemeral, not incognito/temporary. It is
identical for every cadence, and a denial suppresses everything downstream.

**Worthwhileness** (is this event worth paying an LLM for?) is a property of the
*turn*, and legitimately differs per cadence: a free heuristic runs on every
permitted turn, an expensive review waits for a correction or real tool work.

Conflating them is what produced the facet-capture carve-out. Splitting them
keeps the carve-out's *intent* — cheap capture on every turn — while routing it
through one gate, so incognito suppression can never again be a site that
someone forgot to add.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Cadence(str, Enum):
    """The four capture cadences. Each observes a different signal.

    Kept as a closed enum rather than free strings so a typo can't silently
    create a fifth cadence that no policy covers.
    """

    #: After every completed chat turn — corrections, facets, procedural priors.
    PER_TURN = "per_turn"
    #: At session end — the batched consolidation envelope.
    SESSION_END = "session_end"
    #: On a workflow run reaching a terminal state — the run-outcome learner.
    RUN_END = "run_end"
    #: On an external agent's turn observed through the `/capture/v1` proxy
    #: (EXTERNAL-ACCESS §7.2). Unlike the three above, this cadence observes a
    #: conversation PClaw did not conduct, so its content arrives already inside
    #: `fence_untrusted(source="capture:<client_id>")` — `capture_hygiene`'s rule
    #: ("content inside a fence is invisible to direct capture cadences; it may
    #: only travel the proposal path") therefore applies to these rows with zero
    #: new policy. The one cadence whose rows are un-actionable by construction.
    CAPTURE = "capture"


class GateReason(str, Enum):
    """Why the gate decided as it did — recorded, not just returned.

    A capture path that silently does nothing is indistinguishable from one that
    is broken (this is the failure the staging tier's outcome records exist to
    prevent). A denial reason makes "nothing was captured" an *explained*
    observation.
    """

    ALLOWED = "allowed"
    #: ``learning.enabled`` is off — the owner turned the subsystem off.
    DISABLED = "learning_disabled"
    #: An ephemeral session: nothing about it should outlive it.
    EPHEMERAL = "ephemeral_session"
    #: Incognito or temporary — writes suppressed by the restrictions registry.
    RESTRICTED = "restricted_session"
    #: Permitted, but this cadence's cost threshold wasn't met.
    NOT_WORTHWHILE = "below_threshold"
    #: Permitted, but this specific cadence is disabled by config.
    CADENCE_OFF = "cadence_disabled"


@dataclass(frozen=True)
class GateDecision:
    """One eligibility answer, computed once and shared by all capture paths.

    ``permitted`` and ``worthwhile`` are deliberately separate: a cheap capture
    path consumes ``permitted`` alone, an expensive one requires both. Reading
    only ``allowed`` gets the strict answer, which is the safe default.
    """

    permitted: bool
    worthwhile: bool
    reason: GateReason
    cadence: Cadence

    @property
    def allowed(self) -> bool:
        """The strict answer: permitted AND worth the cost."""
        return self.permitted and self.worthwhile

    def __bool__(self) -> bool:
        """Truthiness is the STRICT answer.

        A path that wants the permissive answer has to say ``permitted``
        explicitly — an ``if decision:`` that a reader glosses as "am I allowed"
        must not accidentally authorize an expensive review.
        """
        return self.allowed


class LearningGate:
    """Compute eligibility ONCE per event; every capture path consumes the result.

    Construct from a session at the top of a capture flow, then pass the
    resulting :class:`GateDecision` down. The constructor reads config and the
    restrictions registry; ``decide`` is pure with respect to those, so the two
    reviews in one turn can never disagree about whether the session is
    incognito.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        is_ephemeral: bool = False,
        is_restricted: bool = False,
        min_tool_calls: int = 4,
        correction_heuristic: bool = True,
    ) -> None:
        self.enabled = bool(enabled)
        self.is_ephemeral = bool(is_ephemeral)
        self.is_restricted = bool(is_restricted)
        self.min_tool_calls = int(min_tool_calls)
        self.correction_heuristic = bool(correction_heuristic)

    # ── Construction from live objects ──

    @classmethod
    def for_session(cls, session: Any, cfg: Any = None) -> LearningGate:
        """Build a gate for one session, consulting config AND the registry.

        The registry lookup is the point of this constructor. Suppression for
        incognito/temporary sessions used to depend on each call site
        remembering to check ``session_restrictions``; here it happens once, so
        a new cadence inherits it by construction rather than by review.

        A session object that doesn't carry a key still gets the config and
        ephemeral checks — a partial answer is correct, a crash is not.
        """
        if cfg is None:
            try:
                from personalclaw.config.loader import AppConfig

                cfg = AppConfig.load().learning
            except Exception:  # pragma: no cover - config load is exercised elsewhere
                logger.debug("learning config load failed; gate defaults apply", exc_info=True)
                cfg = None

        restricted = bool(getattr(session, "is_restricted", False))
        key = getattr(session, "key", None)
        if key:
            try:
                from personalclaw import session_restrictions

                restricted = restricted or session_restrictions.is_restricted(str(key))
            except Exception:  # pragma: no cover - registry is in-process
                logger.debug("session_restrictions lookup failed", exc_info=True)

        return cls(
            enabled=bool(getattr(cfg, "enabled", True)),
            is_ephemeral=bool(getattr(session, "_ephemeral", False)),
            is_restricted=restricted,
            min_tool_calls=int(getattr(cfg, "min_tool_calls", 4) or 4),
            correction_heuristic=bool(getattr(cfg, "correction_heuristic", True)),
        )

    # ── The decision ──

    def decide(
        self,
        cadence: Cadence,
        *,
        correction: bool = False,
        tool_calls: int = 0,
        cadence_enabled: bool = True,
        session_score: float | None = None,
        min_session_score: float = 0.0,
    ) -> GateDecision:
        """Answer both questions for one event.

        Permission is checked first and identically for every cadence. Only then
        does the cadence's own cost threshold apply, so a denial never depends on
        which cadence asked.
        """
        if not self.enabled:
            return GateDecision(False, False, GateReason.DISABLED, cadence)
        if self.is_ephemeral:
            return GateDecision(False, False, GateReason.EPHEMERAL, cadence)
        if self.is_restricted:
            return GateDecision(False, False, GateReason.RESTRICTED, cadence)

        # Permitted from here on: a cheap path may proceed even when the
        # expensive threshold below is not met.
        if not cadence_enabled:
            return GateDecision(True, False, GateReason.CADENCE_OFF, cadence)

        worthwhile = self._worthwhile(
            cadence,
            correction=correction,
            tool_calls=tool_calls,
            session_score=session_score,
            min_session_score=min_session_score,
        )
        reason = GateReason.ALLOWED if worthwhile else GateReason.NOT_WORTHWHILE
        return GateDecision(True, worthwhile, reason, cadence)

    def _worthwhile(
        self,
        cadence: Cadence,
        *,
        correction: bool,
        tool_calls: int,
        session_score: float | None,
        min_session_score: float,
    ) -> bool:
        if cadence is Cadence.PER_TURN:
            # Preserves the pre-existing rule exactly: a correction (when that
            # heuristic is on) OR substantial tool work.
            if self.correction_heuristic and correction:
                return True
            return tool_calls >= max(1, self.min_tool_calls)
        if cadence is Cadence.SESSION_END:
            # A thin session yields thin lessons; scoring is what keeps the
            # consolidation pass from paying to learn nothing.
            if session_score is None:
                return True
            return session_score >= min_session_score
        # RUN_END: a terminal run is itself the signal — there is no cheaper
        # proxy to threshold on, and skipping one loses the outcome permanently.
        # CAPTURE: same shape for a different reason — the observed turn has
        # already happened elsewhere, so there is nothing to spend by indexing it,
        # and its content is fenced (i.e. mineable only via the proposal path).
        return True


def record_denial(decision: GateDecision, *, detail: str = "") -> bool:
    """Persist a gate DENIAL so "nothing was captured" is explained, not silent.

    ``GateReason``'s own contract says the reason is "recorded, not just
    returned", and ``staging.FlushOutcome.FLUSH_SKIPPED`` exists precisely for
    "the gate denied it — recorded so a config-off period is legible". Neither
    half was wired: nothing in production wrote ``FLUSH_SKIPPED``, so a denial
    left no trace and a permanently-off gate looked identical to a healthy pass
    that simply found nothing. This closes that loop (LEARNING-FLYWHEEL §3.2 —
    every negative decision writes a row carrying its typed reason).

    Deliberately a SEPARATE function rather than a write inside
    :meth:`LearningGate.decide`: ``decide`` is documented as pure with respect to
    config and the restrictions registry, and two reviews in one turn must be
    able to consult it without double-recording. The call site that acts on a
    denial is the one that records it.

    Returns True iff a row was written. Best-effort by design — recording is
    observability, so a staging-store failure must never break a capture path.
    """
    if decision.allowed and decision.worthwhile:
        return False  # not a denial; nothing to explain
    try:
        from personalclaw.learning.staging import FlushOutcome, get_store

        get_store().record_flush(
            cadence=str(getattr(decision.cadence, "value", decision.cadence)),
            outcome=FlushOutcome.FLUSH_SKIPPED,
            detail=(
                f"{getattr(decision.reason, 'value', decision.reason)}"
                f"{': ' + detail if detail else ''}"
            ),
        )
        return True
    except Exception:
        logger.debug("record_denial failed for %s", decision.reason, exc_info=True)
        return False
