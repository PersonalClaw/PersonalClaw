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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    """What the supervisor should do with a loop this tick."""

    EXECUTE = "execute"     # run/continue the current step's work (arm a worker cycle)
    WAITING = "waiting"     # nothing to do yet (e.g. a worker turn is still in flight)
    HOLD = "hold"           # stay on this step (bake/dwell not elapsed, or metric marginal)
    ADVANCE = "advance"     # step's gate passed → move to the next step
    ROLLBACK = "rollback"   # metric regressed below the prior step's floor → step back
    COMPLETE = "complete"   # the whole loop is done


@dataclass(frozen=True)
class Decision:
    """The immutable outcome of one :func:`evaluate` call."""

    action: Action
    step_index: int          # the step the loop should be on AFTER applying this decision
    reason: str = ""         # human-facing why (surfaced in events / cockpit)
    metric: float | None = None  # the metric value the decision was made against, for observability

    def to_dict(self) -> dict:
        d: dict = {"action": self.action.value, "step_index": self.step_index, "reason": self.reason}
        if self.metric is not None:
            d["metric"] = round(self.metric, 3)
        return d


@dataclass(frozen=True)
class StepConfig:
    """One step's tick parameters (from a plan-phase dict). All optional — the
    defaults reproduce today's no-dwell, no-metric-gate behavior."""

    min_dwell_secs: float = 0.0   # a hold floor: stay on this step at least this long (bake period)
    min_findings: int = 0         # require at least this many findings before the step can advance
    metric_pass: float | None = None  # metric ≥ this → advance; None disables the metric gate
    metric_hold: float | None = None  # metric in [hold, pass) → hold; below hold (+ prior floor) → rollback


@dataclass(frozen=True)
class TickConfig:
    """The loop-level, tick-relevant config snapshot (immutable). Derived from the
    Loop row + kind_config by the adapter; never holds live handles."""

    steps: tuple[StepConfig, ...] = ()   # per-step configs, indexed by step_index
    max_cycles: int = 0                  # 0 = uncapped (forever); else a hard budget
    rollback_cap: int = 3                # consecutive rollbacks on one step before giving up → COMPLETE(blocked)
    # A loop with no steps (a plain point-in-time open-ended/monitor loop) has steps=()
    # and evaluate() degrades to the budget/dwell-free path (EXECUTE until an external
    # done-signal completes it) — this engine governs *stepwise* loops (SDLC/design/plan
    # walkthroughs); point-in-time loops keep their existing is_done_signal path.

    def step(self, i: int) -> StepConfig:
        return self.steps[i] if 0 <= i < len(self.steps) else StepConfig()


@dataclass(frozen=True)
class TickState:
    """An immutable snapshot of a loop's live state, gathered by the adapter.

    Everything here is derived from persisted state (the Loop row + findings/verdicts
    files), so a restarted process rebuilds an identical snapshot and re-derives the
    same Decision — the restartability guarantee."""

    step_index: int              # current step (0-based); == len(steps) means past the last step
    step_started_at: float       # monotonic-ish epoch when the current step began
    findings_in_step: int = 0    # findings produced since this step began
    gate_passed: bool = False    # did the adapter's I/O (verify/judge) say this step's exit is met?
    metric: float | None = None  # the metric the adapter observed (verify exit / quality score)
    worker_in_flight: bool = False   # a worker turn is currently running → WAITING (don't double-arm)
    prior_step_floor: float | None = None  # the metric floor established by the prior step (rollback ref)
    rollbacks_on_step: int = 0   # consecutive rollbacks already taken on this step
    total_cycles: int = 0        # cycles run so far (for the max_cycles budget)


def evaluate(cfg: TickConfig, state: TickState, now: float) -> Decision:
    """Pure per-tick lifecycle decision for a *stepwise* loop. Deterministic given
    (cfg, state, now). See module docstring for the purity contract.

    Branch order (first match wins):
      1. budget exhausted           → COMPLETE
      2. all steps done             → COMPLETE
      3. worker turn in flight      → WAITING
      4. metric regressed           → ROLLBACK (capped → COMPLETE)
      5. bake/dwell not elapsed     → HOLD
      6. min_findings not met       → EXECUTE (keep working this step)
      7. gate passed (+ metric ≥ pass, if gated) → ADVANCE
      8. metric marginal            → HOLD
      9. otherwise                  → EXECUTE
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

    step = cfg.step(state.step_index)

    # 4. Metric regression → rollback to the prior step (bounded by rollback_cap).
    #    Only meaningful when the step is metric-gated AND a prior floor exists.
    if (step.metric_pass is not None and state.metric is not None
            and state.prior_step_floor is not None
            and state.metric < state.prior_step_floor):
        if state.rollbacks_on_step >= cfg.rollback_cap:
            return Decision(Action.COMPLETE, state.step_index,
                            f"rollback cap ({cfg.rollback_cap}) hit on step {state.step_index} — blocked",
                            state.metric)
        prior = max(0, state.step_index - 1)
        return Decision(Action.ROLLBACK, prior,
                        f"metric {state.metric:.2f} regressed below prior floor "
                        f"{state.prior_step_floor:.2f}", state.metric)

    # 5. Bake/dwell floor — a step with a min_dwell holds until the clock elapses,
    #    UNLESS its gate already passed and dwell is zero (handled at 7 as zero-wait).
    dwell_elapsed = (now - state.step_started_at) >= step.min_dwell_secs
    if step.min_dwell_secs > 0 and not dwell_elapsed:
        return Decision(Action.HOLD, state.step_index, "bake period not elapsed", state.metric)

    # 6. Not enough evidence yet to even consider advancing → keep working this step.
    if state.findings_in_step < step.min_findings:
        return Decision(Action.EXECUTE, state.step_index,
                        f"gathering evidence ({state.findings_in_step}/{step.min_findings})", state.metric)

    # 7. Gate passed → advance (metric gate, if configured, must also clear the pass line).
    if state.gate_passed:
        if step.metric_pass is None or (state.metric is not None and state.metric >= step.metric_pass):
            nxt = state.step_index + 1
            done = nxt >= n_steps if n_steps else False
            return Decision(
                Action.COMPLETE if done else Action.ADVANCE,
                nxt,
                "all steps complete" if done else f"step {state.step_index} gate passed",
                state.metric,
            )
        # gate passed structurally but metric below pass → fall through to marginal/hold.

    # 8. Metric-gated + marginal (between hold and pass) → hold for another cycle.
    if (step.metric_pass is not None and step.metric_hold is not None and state.metric is not None
            and step.metric_hold <= state.metric < step.metric_pass):
        return Decision(Action.HOLD, state.step_index,
                        f"metric {state.metric:.2f} marginal (< pass {step.metric_pass:.2f})", state.metric)

    # 9. Default — keep executing the current step.
    return Decision(Action.EXECUTE, state.step_index, "continue current step", state.metric)


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
    return (step.min_dwell_secs > 0 or step.min_findings > 0
            or step.metric_pass is not None or step.metric_hold is not None)


# ── pure adapters: plan-phase dict → tick config ────────────────────────────
# These bridge the free-form execution_plan phase dicts (loop.py) to the typed
# StepConfig/TickConfig the engine consumes, without the engine importing store or
# knowing the phase-dict shape. Pure + defensive: unknown/garbage fields are ignored,
# so a phase with none of the P6 keys yields today's no-dwell/no-metric StepConfig().

def _opt_float(v: object) -> float | None:
    try:
        return float(v) if v is not None and str(v).strip() != "" else None  # type: ignore[arg-type]
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
    return TickConfig(steps=steps, max_cycles=max(0, int(max_cycles or 0)), rollback_cap=rollback_cap)


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
        errs.append(f"phase {phase.get('title','?')!r}: min_dwell_secs must be a number")
    if "min_findings" in phase:
        try:
            if int(phase["min_findings"]) < 0:
                errs.append(f"phase {phase.get('title','?')!r}: min_findings must be ≥ 0")
        except (TypeError, ValueError):
            errs.append(f"phase {phase.get('title','?')!r}: min_findings must be an integer")
    mp, mh = _opt_float(phase.get("metric_pass")), _opt_float(phase.get("metric_hold"))
    if "metric_pass" in phase and mp is None:
        errs.append(f"phase {phase.get('title','?')!r}: metric_pass must be a number")
    if "metric_hold" in phase and mh is None:
        errs.append(f"phase {phase.get('title','?')!r}: metric_hold must be a number")
    if mp is not None and mh is not None and mh > mp:
        errs.append(f"phase {phase.get('title','?')!r}: metric_hold ({mh}) must be ≤ metric_pass ({mp})")
    return errs
