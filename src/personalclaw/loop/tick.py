"""The pure tick decision core (P6).

The Goal-Loop supervisor's per-cycle lifecycle decision, extracted as a **pure
function** over an immutable snapshot: ``evaluate(cfg, state, now) -> Decision``.
No ``store``, no ``ctx``, no I/O — the adapter (the watchdog) gathers a snapshot
(findings count, verdict trail, phase timings, budget, a pre-computed metric) and
applies whatever ``Decision`` this returns. Pulling the decision out of the
watchdog's stateful poll makes the lifecycle logic exhaustively unit-testable and
makes the loop **restartable**: every input is derived from persisted state, so a
fresh process re-derives the same ``Decision`` with no in-memory liveness cache.

The metric gate + dwell/bake + zero-wait collapse + auto-rollback all live here as
pure branches. I/O the decision *implies* (running a verify command, a judge pass)
happens in the adapter and its RESULT is fed back in as ``state.metric`` next tick —
the key design line that keeps this function pure (see the plan's Risks §).

**PP-15 — this is now the ONE convergence decision, for both engines.** It used to be
consulted by exactly one loop kind while the workflows loop node ran a second, stateful
copy of the same reasoning (``loop_middleware.check_middleware``: identical-call /
hypothesis-exhausted / no-progress detection plus an escalation ladder). Two
implementations of "is this loop converging?" is how the two engines drifted, and the
drift was invisible — both sides read plausibly and only disagreed at the boundary. So
the ladder moved HERE, as pure branches over persisted counters, and the second copy was
deleted rather than kept behind a flag.

The move cost the ladder its mutability, which is the point. ``check_middleware``
advanced a cursor on the state object it was handed (``state.escalation_index = index``),
so the decision depended on how many times it had been *called* — unreproducible after a
restart, and untestable without replaying the call sequence. Here the rung is DERIVED
from counters that are already on disk (``escalations_taken``, ``attempts_at_rung``,
``nudges_issued``), and the adapter advances them with :func:`applied` once it has acted.
Same (cfg, state, now) in, same ``Decision`` out — including which rung.

Two members carry what the loops engine could not previously say:

* ``ESCALATE`` — take a rung (``Decision.rung``): the run continues with a changed
  STRATEGY (fresh session, model switch, workspace reset). Without it the engine failed
  BINARY, straight from "two consecutive errors" to "a human must look at this", making
  every middle rung of the declared ladder unreachable.
* ``REPLAN`` — the PLAN is wrong, not the execution. The adapter turns this into a real
  ``mutations`` batch (insert/delete/move/run_from) applied at the controller's drain
  point, so a run re-derives its remaining steps from a judge critique instead of
  retrying the same plan with a hint attached.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum

from personalclaw.workflows.loop_middleware import (
    CLASS_ENTRY_RUNG,
    DEFAULT_FINGERPRINT_WINDOW,
    DEFAULT_HYPOTHESIS_ABANDON,
    DEFAULT_LADDER,
    DEFAULT_NO_PROGRESS_STOP,
    RECOVERABLE,
    RECOVERABLE_HEADROOM,
    FailureClass,
    Rung,
    call_fingerprint,
    classify_failure,
    nudge_for,
)


class Action(str, Enum):
    """What the supervisor should do with a loop this tick."""

    EXECUTE = "execute"  # run/continue the current step's work (arm a worker cycle)
    WAITING = "waiting"  # nothing to do yet (e.g. a worker turn is still in flight)
    HOLD = "hold"  # stay on this step (bake/dwell not elapsed, or metric marginal)
    ADVANCE = "advance"  # step's gate passed → move to the next step
    ROLLBACK = "rollback"  # metric regressed below the prior step's floor → step back
    COMPLETE = "complete"  # the whole loop is done
    #: Take an escalation rung (see ``Decision.rung``): the run continues, with a changed
    #: STRATEGY. ``rung is Rung.SURFACE`` is the ONE rung that stops the run and asks a
    #: human; every other rung is something the engine itself does. Collapsing them all
    #: into a stop is what made the middle of the ladder dead configuration.
    ESCALATE = "escalate"
    #: The plan is the suspect, not the worker. The adapter queues a mutation batch built
    #: from ``Decision.replan_directive`` (the judge's critique of the plan).
    REPLAN = "replan"


#: Nudges and escalations are bounded by counters, not by the ladder's length.
DEFAULT_ATTEMPT_CAP = 3
#: How many times one run may re-derive its own remaining steps. Uncapped replanning is a
#: loop that rewrites its plan forever and never executes one.
DEFAULT_REPLAN_CAP = 2


@dataclass(frozen=True)
class Decision:
    """The immutable outcome of one :func:`evaluate` call.

    Absorbs what ``loop_middleware.MiddlewareVerdict`` used to carry, so a caller reads ONE
    decision object regardless of which tier decided. The convergence fields are neutral on
    the progress branches (no rung, no nudge, ``UNKNOWN`` class), so a loop that never fails
    produces exactly the ``Decision`` it produced before PP-15.
    """

    action: Action
    step_index: int  # the step the loop should be on AFTER applying this decision
    reason: str = ""  # human-facing why (surfaced in events / cockpit)
    metric: float | None = None  # the metric value the decision was made against, for observability
    #: Which escalation rung ``ESCALATE`` selected. ``SURFACE`` = stop and ask a human.
    rung: Rung | None = None
    #: The longer human-facing explanation (what the counters actually showed).
    detail: str = ""
    #: The class of the most recent failure this decision reasoned about.
    failure_class: FailureClass = FailureClass.UNKNOWN
    #: A corrective instruction for the adapter to inject. Non-empty on the nudge tier —
    #: an ``EXECUTE`` carrying one means "run again, but with this correction", the cheap
    #: fix that has to be tried before an escalation is justified.
    nudge_text: str = ""
    #: Seconds the adapter should wait before retrying, for recoverable classes.
    wait_secs: float = 0.0
    #: False when this failure must NOT advance the escalation ladder (a 429 is the world
    #: saying "wait", and burning a rung on it is how a run that would have finished
    #: doesn't).
    consumed_rung: bool = True
    #: The critique a ``REPLAN``'s mutation batch is derived from.
    replan_directive: str = ""

    @property
    def surfaced(self) -> bool:
        """Does this decision hand the run to a human? The one bit that used to be the
        difference between ``Action.ESCALATE`` and ``Action.HALT``."""
        return self.rung is Rung.SURFACE

    def __bool__(self) -> bool:  # pragma: no cover - explicit comparison preferred
        raise TypeError(
            "Decision has no truth value — compare .action explicitly. A convenience "
            "__bool__ on a decision is how `if decision` came to mean 'is this healthy' "
            "where the code meant 'did I get one'."
        )

    def to_dict(self) -> dict:
        d: dict = {
            "action": self.action.value,
            "step_index": self.step_index,
            "reason": self.reason,
        }
        if self.metric is not None:
            d["metric"] = round(self.metric, 3)
        if self.rung is not None:
            d["rung"] = self.rung.value
        if self.detail:
            d["detail"] = self.detail
        if self.failure_class is not FailureClass.UNKNOWN:
            d["failure_class"] = self.failure_class.value
        if self.nudge_text:
            d["nudge_text"] = self.nudge_text
        if self.wait_secs:
            d["wait_secs"] = round(self.wait_secs, 3)
        if not self.consumed_rung:
            d["consumed_rung"] = False
        if self.replan_directive:
            d["replan_directive"] = self.replan_directive
        return d


@dataclass(frozen=True)
class StepConfig:
    """One step's tick parameters (from a plan-phase dict). All optional — the
    defaults reproduce today's no-dwell, no-metric-gate behavior."""

    min_dwell_secs: float = 0.0  # a hold floor: stay on this step at least this long (bake period)
    min_findings: int = 0  # require at least this many findings before the step can advance
    metric_pass: float | None = None  # metric ≥ this → advance; None disables the metric gate
    metric_hold: float | None = (
        None  # metric in [hold, pass) → hold; below hold (+ prior floor) → rollback
    )


@dataclass(frozen=True)
class TickConfig:
    """The loop-level, tick-relevant config snapshot (immutable). Derived from the
    Loop row + kind_config by the adapter; never holds live handles."""

    steps: tuple[StepConfig, ...] = ()  # per-step configs, indexed by step_index
    max_cycles: int = 0  # 0 = uncapped (forever); else a hard budget
    rollback_cap: int = 3  # consecutive rollbacks on one step before giving up → COMPLETE(blocked)
    # A loop with no steps (a plain point-in-time open-ended/monitor loop) has steps=()
    # and evaluate() degrades to the budget/dwell-free path (EXECUTE until an external
    # done-signal completes it) — this engine governs *stepwise* loops (SDLC/design/plan
    # walkthroughs); point-in-time loops keep their existing is_done_signal path.

    # ── PP-15: the convergence half, from a SupervisorPolicy (see
    # ``supervisor_policy.tick_config``). Every default reproduces the thresholds
    # ``check_middleware`` used, so a caller that supplies none of them decides exactly
    # what the middleware decided.
    #: The escalation ladder, in order. Always SURFACE-terminal (``_resolve_ladder``).
    ladder: tuple[Rung, ...] = DEFAULT_LADDER
    #: FailureClass value → the corrective instruction a nudge injects.
    failure_mutations: Mapping[str, str] = field(default_factory=dict)
    #: How many identical failing CALLS in a row count as "the worker learned nothing".
    fingerprint_window: int = DEFAULT_FINGERPRINT_WINDOW
    #: How many identical attempted FIXES in a row mean the diagnosis is wrong.
    hypothesis_abandon_after: int = DEFAULT_HYPOTHESIS_ABANDON
    #: How many iterations without improvement count as a stall.
    no_progress_stop: int = DEFAULT_NO_PROGRESS_STOP
    #: Attempts allowed WITHIN one rung before moving to the next. Bounds attempts, NOT the
    #: ladder's length: treating it as a position cap made `restart_from_scratch`
    #: unreachable under the declared values (cap 3 against a 5-rung ladder).
    attempt_cap: int = DEFAULT_ATTEMPT_CAP
    #: How many times this run may re-derive its remaining steps.
    replan_cap: int = DEFAULT_REPLAN_CAP

    def step(self, i: int) -> StepConfig:
        return self.steps[i] if 0 <= i < len(self.steps) else StepConfig()

    def rungs(self) -> tuple[Rung, ...]:
        """The ladder, guaranteed non-empty and SURFACE-terminal. A ladder with no terminal
        rung would loop at its top forever, so the floor is enforced at READ time too — a
        hand-built ``TickConfig`` never gets a ladder that cannot end."""
        if not self.ladder:
            return DEFAULT_LADDER
        if self.ladder[-1] is not Rung.SURFACE:
            return (*self.ladder, Rung.SURFACE)
        return self.ladder


@dataclass(frozen=True)
class TickState:
    """An immutable snapshot of a loop's live state, gathered by the adapter.

    Everything here is derived from persisted state (the Loop row + findings/verdicts
    files), so a restarted process rebuilds an identical snapshot and re-derives the
    same Decision — the restartability guarantee."""

    step_index: int  # current step (0-based); == len(steps) means past the last step
    step_started_at: float  # monotonic-ish epoch when the current step began
    findings_in_step: int = 0  # findings produced since this step began
    gate_passed: bool = False  # did the adapter's I/O (verify/judge) say this step's exit is met?
    metric: float | None = None  # the metric the adapter observed (verify exit / quality score)
    worker_in_flight: bool = (
        False  # a worker turn is currently running → WAITING (don't double-arm)
    )
    prior_step_floor: float | None = (
        None  # the metric floor established by the prior step (rollback ref)
    )
    rollbacks_on_step: int = 0  # consecutive rollbacks already taken on this step
    total_cycles: int = 0  # cycles run so far (for the max_cycles budget)

    # ── PP-15: the counters absorbed from ``loop_middleware.LoopState`` ──
    #
    # Counters only, deliberately not a transcript: this is evaluated before every
    # iteration, and anything that grows with the run would make the check itself a cost
    # centre. Tuples rather than lists because a frozen snapshot whose contents can be
    # appended to is not a snapshot — the mutable version is what let the old middleware
    # advance its own ladder mid-decision.
    iterations: int = 0
    #: (tool, args) fingerprints of failing calls, newest last.
    call_fingerprints: tuple[str, ...] = ()
    #: ``FailureClass`` values, newest last.
    failure_classes: tuple[str, ...] = ()
    #: Scores or progress measures, newest last — for no-progress detection.
    progress_marks: tuple[float, ...] = ()
    #: Fingerprints of attempted FIXES, for hypothesis abandonment.
    fix_fingerprints: tuple[str, ...] = ()
    #: How many escalation rungs this stall has already consumed. PERSISTED, which is what
    #: makes the rung re-derivable: the old middleware kept this as a cursor it advanced
    #: itself, so the decision depended on call count rather than on state.
    escalations_taken: int = 0
    #: Attempts already spent at the current rung.
    attempts_at_rung: int = 0
    #: Corrective instructions already injected for this stall. The first stall gets a
    #: nudge; only a stall that survived its nudge is allowed to cost a rung.
    nudges_issued: int = 0
    #: Recoverable waits already taken, for the backoff curve.
    recoverable_waits: int = 0
    #: A stall a SEPARATE detector already confirmed, named by its reason (the workflow
    #: breaker's `identical_output`, `error_streak`, `token_cap`, ...). Non-empty means the
    #: response tiers must fire without re-deriving the stall from the counters below.
    #:
    #: This is the seam between the two engines. The loop kinds have no breaker, so for them the
    #: fingerprint / hypothesis / no-progress detectors below ARE the detection. The workflow
    #: `loop` node has one, and it trips on four rules — only one of which (an error streak)
    #: leaves failure signatures behind. Requiring this core to re-derive an `identical_output`
    #: trip from failure fingerprints it cannot have would make the trip a no-op: the detector
    #: fires, the response tier sees no evidence, and the loop runs on to its cap with the
    #: breaker silently defeated.
    stall_confirmed: str = ""
    #: A judge's critique OF THE PLAN, from persisted state. Non-empty means the plan is the
    #: suspect — the run should re-derive its remaining steps rather than retry them.
    plan_critique: str = ""
    #: How many times this run has already re-derived its remaining steps.
    replans_taken: int = 0

    @property
    def last_failure_class(self) -> FailureClass:
        """The most recent failure's class, or ``UNKNOWN`` when nothing has failed."""
        if not self.failure_classes:
            return FailureClass.UNKNOWN
        try:
            return FailureClass(self.failure_classes[-1])
        except ValueError:
            # An unreadable persisted value routes conservatively rather than crashing the
            # tick: UNKNOWN starts at the cheapest rung.
            return FailureClass.UNKNOWN


def evaluate(cfg: TickConfig, state: TickState, now: float) -> Decision:
    """Pure per-tick lifecycle decision for a *stepwise* loop. Deterministic given
    (cfg, state, now). See module docstring for the purity contract.

    Branch order (first match wins):
      1. budget exhausted           → COMPLETE
      2. all steps done             → COMPLETE
      3. worker turn in flight      → WAITING
      4. recoverable failure class  → WAITING (no rung) / ESCALATE(SURFACE) when exhausted
      5. environment broken         → ESCALATE(SURFACE)
      6. plan critique outstanding  → REPLAN (capped)
      7. confirmed stall            → EXECUTE(nudge) → ESCALATE(rung) → ESCALATE(SURFACE)
      8. metric regressed           → ROLLBACK (capped → COMPLETE)
      9. bake/dwell not elapsed     → HOLD
     10. min_findings not met       → EXECUTE (keep working this step)
     11. gate passed (+ metric ≥ pass, if gated) → ADVANCE
     12. metric marginal            → HOLD
     13. otherwise                  → EXECUTE

    Branches 4-7 are the convergence tiers PP-15 folded in from
    ``loop_middleware.check_middleware``. They sit ABOVE the progress branches because a
    loop that is failing is not a loop whose metric is marginal, and they are evaluated in
    COST order — the cheapest tier that can decide, decides. All four are vacuous on a
    default ``TickState`` (no recorded failures, no critique), so a loop that never fails
    reaches branch 8 exactly as it did before.
    """
    n_steps = len(cfg.steps)

    # 1. Budget (a capped loop stops when cycles run out; forever = max_cycles 0).
    if cfg.max_cycles and state.total_cycles >= cfg.max_cycles:
        return Decision(Action.COMPLETE, state.step_index, "cycle budget reached", state.metric)

    # 2. Past the last step → the stepwise plan is complete.
    if n_steps and state.step_index >= n_steps:
        return Decision(Action.COMPLETE, n_steps, "all steps complete", state.metric)

    # 3. A worker turn is still running — don't arm another; wait for it to land.
    if state.worker_in_flight:
        return Decision(Action.WAITING, state.step_index, "worker turn in flight", state.metric)

    convergence = _converge(cfg, state)
    if convergence is not None:
        return convergence

    step = cfg.step(state.step_index)

    # 8. Metric regression → rollback to the prior step (bounded by rollback_cap).
    #    Only meaningful when the step is metric-gated AND a prior floor exists.
    if (
        step.metric_pass is not None
        and state.metric is not None
        and state.prior_step_floor is not None
        and state.metric < state.prior_step_floor
    ):
        if state.rollbacks_on_step >= cfg.rollback_cap:
            return Decision(
                Action.COMPLETE,
                state.step_index,
                f"rollback cap ({cfg.rollback_cap}) hit on step {state.step_index} — blocked",
                state.metric,
            )
        prior = max(0, state.step_index - 1)
        return Decision(
            Action.ROLLBACK,
            prior,
            f"metric {state.metric:.2f} regressed below prior floor "
            f"{state.prior_step_floor:.2f}",
            state.metric,
        )

    # 9. Bake/dwell floor — a step with a min_dwell holds until the clock elapses,
    #    UNLESS its gate already passed and dwell is zero (handled at 11 as zero-wait).
    dwell_elapsed = (now - state.step_started_at) >= step.min_dwell_secs
    if step.min_dwell_secs > 0 and not dwell_elapsed:
        return Decision(Action.HOLD, state.step_index, "bake period not elapsed", state.metric)

    # 10. Not enough evidence yet to even consider advancing → keep working this step.
    if state.findings_in_step < step.min_findings:
        return Decision(
            Action.EXECUTE,
            state.step_index,
            f"gathering evidence ({state.findings_in_step}/{step.min_findings})",
            state.metric,
        )

    # 11. Gate passed → advance (metric gate, if configured, must also clear the pass line).
    if state.gate_passed:
        if step.metric_pass is None or (
            state.metric is not None and state.metric >= step.metric_pass
        ):
            nxt = state.step_index + 1
            done = nxt >= n_steps if n_steps else False
            return Decision(
                Action.COMPLETE if done else Action.ADVANCE,
                nxt,
                "all steps complete" if done else f"step {state.step_index} gate passed",
                state.metric,
            )
        # gate passed structurally but metric below pass → fall through to marginal/hold.

    # 12. Metric-gated + marginal (between hold and pass) → hold for another cycle.
    if (
        step.metric_pass is not None
        and step.metric_hold is not None
        and state.metric is not None
        and step.metric_hold <= state.metric < step.metric_pass
    ):
        return Decision(
            Action.HOLD,
            state.step_index,
            f"metric {state.metric:.2f} marginal (< pass {step.metric_pass:.2f})",
            state.metric,
        )

    # 13. Default — keep executing the current step.
    return Decision(Action.EXECUTE, state.step_index, "continue current step", state.metric)


def _window(value: object, default: int) -> int:
    """Normalize a counter-window, rejecting bools explicitly.

    ``True`` is an ``int`` in Python, so a ``fingerprint_window: true`` would otherwise become a
    window of ONE and trip the stall detector on the very first failure. The guard travelled with
    the thresholds from ``check_middleware``; dropping it during the move would have re-opened a
    hole that was already measured and closed.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return default
    return value


def _is_recoverable(value: str) -> bool:
    """Is this persisted ``FailureClass`` value one the world owns rather than the work?"""
    try:
        return FailureClass(value) in RECOVERABLE
    except ValueError:
        return False


def _converge(cfg: TickConfig, state: TickState) -> Decision | None:
    """Branches 4-7: the failure/stall tiers. ``None`` means this tier has no objection.

    Pure over the persisted counters. Split out of :func:`evaluate` for readability only —
    it takes no clock and no ladder cursor, which is the whole difference from the
    ``check_middleware`` it replaces.
    """
    cls = state.last_failure_class

    # 4. Recoverable classes FIRST: a rate limit is the world saying "wait", not a stall.
    #    Treating a 429 like a wrong answer burns the ladder on something that would have
    #    resolved itself, so these get a wider window and never consume a rung.
    if cls in RECOVERABLE:
        headroom = _window(cfg.fingerprint_window, DEFAULT_FINGERPRINT_WINDOW) * (
            RECOVERABLE_HEADROOM
        )
        recent = state.failure_classes[-headroom:]
        if len(recent) >= headroom and all(_is_recoverable(c) for c in recent):
            return Decision(
                Action.ESCALATE,
                state.step_index,
                "recoverable_exhausted",
                state.metric,
                rung=Rung.SURFACE,
                detail=f"{headroom} consecutive recoverable failures — the world is not clearing",
                failure_class=cls,
            )
        return Decision(
            Action.WAITING,
            state.step_index,
            "recoverable_wait",
            state.metric,
            detail=f"{cls.value} — waiting rather than escalating",
            failure_class=cls,
            # Exponential-ish backoff without a clock dependency: the ADAPTER sleeps. The
            # curve is a function of a persisted counter, so a restart resumes the same
            # backoff instead of starting over at one second.
            wait_secs=min(60.0, 2.0 ** min(6, state.recoverable_waits)),
            consumed_rung=False,
        )

    # 5. An environment failure cannot be retried into working.
    if cls is FailureClass.ENVIRONMENT:
        return Decision(
            Action.ESCALATE,
            state.step_index,
            "environment_broken",
            state.metric,
            rung=Rung.SURFACE,
            detail="no retry fixes a missing binary or a permission denial",
            failure_class=cls,
        )

    # 6. The PLAN is under critique → re-derive the remaining steps.
    #
    #    Above the stall tiers deliberately: a judge critique naming what is wrong with the
    #    plan is strictly better information than "the same call failed three times", and
    #    nudging a worker to try harder against a plan that cannot work is the retry-with-a-
    #    hint this branch exists to replace. Bounded by `replan_cap` so a run cannot spend
    #    itself rewriting its plan forever; past the cap it falls through to the ladder.
    if state.plan_critique and state.replans_taken < max(0, cfg.replan_cap):
        return Decision(
            Action.REPLAN,
            state.step_index,
            "plan_critique",
            state.metric,
            detail=f"replan {state.replans_taken + 1}/{cfg.replan_cap} from a judge critique",
            failure_class=cls,
            replan_directive=state.plan_critique,
        )

    # 7. Confirmed stalls. An EXTERNAL detector's verdict is taken first — it has already
    #    decided, and re-deriving its conclusion from evidence it may not have produced is how a
    #    trip becomes a no-op.
    if state.stall_confirmed:
        return _stall_response(
            cfg,
            state,
            cls,
            reason=state.stall_confirmed,
            detail=f"stall confirmed by the trip detector ({state.stall_confirmed})",
        )

    #    Otherwise detect it here, cheapest detector first. The same error twice is a signal; the
    #    same error from the same CALL twice is a much stronger one — a worker re-running an
    #    identical command has learned nothing from the failure, and that is detectable without a
    #    model.
    window = _window(cfg.fingerprint_window, DEFAULT_FINGERPRINT_WINDOW)
    prints = state.call_fingerprints
    if len(prints) >= window and len(set(prints[-window:])) == 1:
        return _stall_response(
            cfg,
            state,
            cls,
            reason="identical_call",
            detail=f"the same failing call {window}x in a row",
        )

    abandon = _window(cfg.hypothesis_abandon_after, DEFAULT_HYPOTHESIS_ABANDON)
    fixes = state.fix_fingerprints
    if len(fixes) >= abandon and len(set(fixes[-abandon:])) == 1:
        return _stall_response(
            cfg,
            state,
            cls,
            reason="hypothesis_exhausted",
            detail=f"the same fix failed {abandon}x — the diagnosis is wrong",
        )

    stop = _window(cfg.no_progress_stop, DEFAULT_NO_PROGRESS_STOP)
    marks = state.progress_marks
    if len(marks) >= stop:
        recent_marks = marks[-stop:]
        if max(recent_marks) <= recent_marks[0]:
            return _stall_response(
                cfg,
                state,
                cls,
                reason="no_progress",
                detail=f"{stop} iterations without improving on {recent_marks[0]}",
            )
    return None


def _stall_response(
    cfg: TickConfig,
    state: TickState,
    cls: FailureClass,
    *,
    reason: str,
    detail: str,
) -> Decision:
    """The Continue→Nudge→Escalate→Surface ladder for a confirmed stall, DERIVED.

    The first stall gets a nudge — one injected corrective sentence — because halting a run
    that one sentence would fix is expensive in exactly the way autonomous execution cannot
    afford. Only a stall that survived its nudge is allowed to cost a rung.

    Every position here is computed from persisted counters, never from a cursor this
    function advances. ``applied`` is the write half.
    """
    if state.nudges_issued < 1:
        # The cheap tier: run again, with a correction. No rung spent.
        return Decision(
            Action.EXECUTE,
            state.step_index,
            reason,
            state.metric,
            detail=detail,
            failure_class=cls,
            nudge_text=nudge_for(cls, dict(cfg.failure_mutations), detail, stall=reason),
        )

    ladder = cfg.rungs()
    entry = CLASS_ENTRY_RUNG.get(cls, Rung.CLASSIFIED_RETRY)
    try:
        entry_index = ladder.index(entry)
    except ValueError:
        # A ladder that omits this class's entry rung starts at the bottom rather than
        # skipping the class straight to a human.
        entry_index = 0
    # Skipping cheap rungs for a class they cannot fix is the point: a fresh session does
    # not fix a missing binary, and a classified retry does not fix work aimed at the wrong
    # target.
    index = max(state.escalations_taken, entry_index)
    cap = _window(cfg.attempt_cap, DEFAULT_ATTEMPT_CAP)
    if state.attempts_at_rung >= cap:
        index = min(index + 1, len(ladder) - 1)

    if index >= len(ladder) - 1:
        return Decision(
            Action.ESCALATE,
            state.step_index,
            reason,
            state.metric,
            rung=Rung.SURFACE,
            detail=f"{detail}; escalation ladder exhausted",
            failure_class=cls,
        )

    rung = ladder[index]
    # CLASSIFIED_RETRY is a re-prompt: the run keeps its session and gets a targeted
    # correction, so it EXECUTES. Every other rung is a strategy change the ENGINE makes
    # (fresh session, model switch, workspace reset), which is what ESCALATE names. Mapping
    # them all to one action is what made the middle of the ladder dead configuration.
    action = Action.EXECUTE if rung is Rung.CLASSIFIED_RETRY else Action.ESCALATE
    return Decision(
        action,
        state.step_index,
        reason,
        state.metric,
        rung=rung,
        detail=detail,
        failure_class=cls,
        # `stall=reason` on the later nudges too, not only the first: measured on a real
        # sequence, cycles 4-5 fell back to the generic "change your approach" while the
        # first nudge got the precise "you ran the identical command" text. The later
        # nudges are the ones a worker most needs specifics from.
        nudge_text=nudge_for(cls, dict(cfg.failure_mutations), detail, stall=reason),
    )


# ── the adapter's write half: persisted-counter advances ─────────────────────
# `evaluate` reads counters; these produce the NEXT snapshot. Both halves are pure (each
# returns a new frozen `TickState`), and keeping them separate is what makes a decision
# reproducible: the old middleware advanced its ladder INSIDE the decision, so asking it
# twice gave two different answers and a restart gave a third.


def record_failure(
    state: TickState,
    *,
    text: str = "",
    tool: str = "",
    args: object = None,
    fix: str = "",
    hint: str = "",
) -> TickState:
    """Fold one failed iteration into the snapshot, returning the next one."""
    cls = classify_failure(text, hint=hint)
    return replace(
        state,
        iterations=state.iterations + 1,
        failure_classes=(*state.failure_classes, cls.value),
        call_fingerprints=(
            (*state.call_fingerprints, call_fingerprint(tool, args))
            if tool
            else state.call_fingerprints
        ),
        fix_fingerprints=(
            (*state.fix_fingerprints, call_fingerprint("fix", fix))
            if fix
            else state.fix_fingerprints
        ),
    )


def record_progress(state: TickState, mark: float) -> TickState:
    """Fold one progress measure into the snapshot."""
    return replace(
        state,
        iterations=state.iterations + 1,
        progress_marks=(*state.progress_marks, float(mark)),
    )


def reset_after_success(state: TickState) -> TickState:
    """Success clears the stall counters — a run that recovers is not on thin ice.

    The escalation position resets too: a loop that got unstuck and later gets stuck for a
    DIFFERENT reason deserves the cheap rungs again, and carrying the index forward would
    surface it to a human on its first new problem.
    """
    return replace(
        state,
        call_fingerprints=(),
        failure_classes=(),
        fix_fingerprints=(),
        escalations_taken=0,
        attempts_at_rung=0,
        nudges_issued=0,
        plan_critique="",
    )


def applied(cfg: TickConfig, state: TickState, decision: Decision) -> TickState:
    """The counter advance the adapter performs AFTER acting on a convergence decision.

    Mirrors what ``check_middleware`` did to its own argument mid-decision, moved to where a
    write belongs. A progress decision (ADVANCE/HOLD/ROLLBACK/plain EXECUTE) advances
    nothing here — those counters are the adapter's own (`step_index`, `total_cycles`).
    """
    if decision.action is Action.REPLAN:
        # The critique is CONSUMED: leaving it set would re-decide REPLAN every tick until
        # the cap, spending the whole budget re-deriving the same plan.
        return replace(state, replans_taken=state.replans_taken + 1, plan_critique="")
    if not decision.consumed_rung:
        return replace(state, recoverable_waits=state.recoverable_waits + 1)
    if decision.rung is None:
        return (
            replace(state, nudges_issued=state.nudges_issued + 1) if decision.nudge_text else state
        )
    if decision.rung is Rung.SURFACE:
        # A human owns it now; there is no further rung to bank.
        return state
    ladder = cfg.rungs()
    try:
        index = ladder.index(decision.rung)
    except ValueError:
        index = state.escalations_taken
    cap = _window(cfg.attempt_cap, DEFAULT_ATTEMPT_CAP)
    spent = 0 if state.attempts_at_rung >= cap else state.attempts_at_rung
    return replace(state, escalations_taken=index, attempts_at_rung=spent + 1)


def collapse(cfg: TickConfig, state: TickState, now: float, *, max_iters: int = 64) -> Decision:
    """Zero-wait collapse across *no-gate* steps.

    A step is "instant" (no adapter observation needed to leave it) when it has NO
    metric gate, NO min_findings, and NO dwell — such a step is exit-satisfied the
    moment it's entered, so a plan of them shouldn't burn one poll-interval each.
    ``collapse`` folds an ADVANCE that lands on instant steps forward through them,
    settling on COMPLETE (advanced off the end) or on the first step that DOES need an
    observation (returning that ADVANCE for the adapter to act on + re-observe next tick).

    Purity: this only reasons about STATIC step config (``_needs_observation``), never
    synthesizes a ``gate_passed``/``metric`` it cannot know — the design fix for the
    unsound "reset gate then re-evaluate" approach.
    """
    first = evaluate(cfg, state, now)
    if first.action is not Action.ADVANCE:
        return first
    # We're advancing onto step `idx`. Walk forward over consecutive instant steps.
    idx = first.step_index
    iters = 0
    n = len(cfg.steps)
    while iters < max_iters:
        if n and idx >= n:
            return Decision(Action.COMPLETE, n, "all steps complete (collapsed)", state.metric)
        if _needs_observation(cfg.step(idx)):
            # This step needs a real gate/metric/dwell → hand the adapter an ADVANCE onto it.
            return Decision(Action.ADVANCE, idx, first.reason, state.metric)
        idx += 1
        iters += 1
    return Decision(Action.ADVANCE, idx, first.reason, state.metric)


def _needs_observation(step: StepConfig) -> bool:
    """True if leaving this step requires an adapter observation (gate/metric/dwell/
    evidence) — i.e. it is NOT instant."""
    return (
        step.min_dwell_secs > 0
        or step.min_findings > 0
        or step.metric_pass is not None
        or step.metric_hold is not None
    )


# ── pure adapters: plan-phase dict → tick config ────────────────────────────
# These bridge the free-form execution_plan phase dicts (loop.py) to the typed
# StepConfig/TickConfig the engine consumes, without the engine importing store or
# knowing the phase-dict shape. Pure + defensive: unknown/garbage fields are ignored,
# so a phase with none of the P6 keys yields today's no-dwell/no-metric StepConfig().


def _opt_float(v: object) -> float | None:
    try:
        return float(v) if v is not None and str(v).strip() != "" else None  # type: ignore[arg-type]  # noqa: E501
    except (TypeError, ValueError):
        return None


def step_config_from_phase(phase: dict) -> StepConfig:
    """Parse one execution_plan phase dict into a StepConfig. Reads the optional P6
    keys (``min_dwell_secs``, ``min_findings``, ``metric_pass``, ``metric_hold``);
    absent → the neutral defaults (reproduces pre-P6 behavior)."""
    if not isinstance(phase, dict):
        return StepConfig()
    dwell = _opt_float(phase.get("min_dwell_secs")) or 0.0
    try:
        min_findings = max(0, int(phase.get("min_findings", 0) or 0))
    except (TypeError, ValueError):
        min_findings = 0
    return StepConfig(
        min_dwell_secs=max(0.0, dwell),
        min_findings=min_findings,
        metric_pass=_opt_float(phase.get("metric_pass")),
        metric_hold=_opt_float(phase.get("metric_hold")),
    )


def tick_config_from_plan(plan: list, max_cycles: int = 0, rollback_cap: int = 3) -> TickConfig:
    """Build a TickConfig from a loop's execution_plan (list of phase dicts) + budget."""
    steps = tuple(step_config_from_phase(p) for p in (plan or []))
    return TickConfig(
        steps=steps, max_cycles=max(0, int(max_cycles or 0)), rollback_cap=rollback_cap
    )


def tick_state_from_snapshot(
    *,
    step_index: int,
    step_started_at: float,
    findings_total: int,
    findings_at_step_start: int,
    gate_passed: bool = False,
    metric: float | None = None,
    worker_in_flight: bool = False,
    prior_step_floor: float | None = None,
    rollbacks_on_step: int = 0,
    total_cycles: int = 0,
) -> TickState:
    """Assemble an immutable :class:`TickState` from raw values the adapter already
    fetched (findings counts, the pure step-index derivation, timings, a pre-computed
    metric). Pure — takes plain values, does NO store/ctx I/O (the caller does the
    reads, per the purity contract). ``findings_in_step`` is derived here as
    ``max(0, findings_total - findings_at_step_start)`` so the adapter can pass the two
    counts it already has (total now, and the total banked when the step began) without
    re-deriving the delta itself."""
    return TickState(
        step_index=step_index,
        step_started_at=step_started_at,
        findings_in_step=max(0, findings_total - findings_at_step_start),
        gate_passed=gate_passed,
        metric=metric,
        worker_in_flight=worker_in_flight,
        prior_step_floor=prior_step_floor,
        rollbacks_on_step=max(0, rollbacks_on_step),
        total_cycles=max(0, total_cycles),
    )


def validate_step_phase(phase: dict) -> list[str]:
    """Validate the optional P6 tick keys on one phase dict → list of error strings
    (empty = ok). Used by the kinds' validate_config so a malformed dwell/metric is
    caught at intake, not silently ignored at runtime."""
    errs: list[str] = []
    if not isinstance(phase, dict):
        return errs
    if "min_dwell_secs" in phase and _opt_float(phase.get("min_dwell_secs")) is None:
        errs.append(f"phase {phase.get('title', '?')!r}: min_dwell_secs must be a number")
    if "min_findings" in phase:
        try:
            if int(phase["min_findings"]) < 0:
                errs.append(f"phase {phase.get('title', '?')!r}: min_findings must be ≥ 0")
        except (TypeError, ValueError):
            errs.append(f"phase {phase.get('title', '?')!r}: min_findings must be an integer")
    mp, mh = _opt_float(phase.get("metric_pass")), _opt_float(phase.get("metric_hold"))
    if "metric_pass" in phase and mp is None:
        errs.append(f"phase {phase.get('title', '?')!r}: metric_pass must be a number")
    if "metric_hold" in phase and mh is None:
        errs.append(f"phase {phase.get('title', '?')!r}: metric_hold must be a number")
    if mp is not None and mh is not None and mh > mp:
        errs.append(
            f"phase {phase.get('title', '?')!r}: metric_hold ({mh}) must be ≤ metric_pass ({mp})"
        )
    return errs
