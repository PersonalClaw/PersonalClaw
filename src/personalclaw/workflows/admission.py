"""Admission policies — the ordered rules that decide what the frontier may launch (PP-11).

Four schedulers in this repo answered the same question — *what may run now, given persisted
state?* — and shared zero lines: `workflows/tick.frontier()` (typed lanes, per-container
`max_concurrency`, WIP=1), `loop/tick.evaluate()` (dwell / `min_findings` / metric gates),
`workflows/pool.py`'s `frontier`/`next` (priority + blocking-count + overdue with TTL'd leases),
and `triggers/` `tick_once`. Each held a capability the others structurally could not express, so
every new admission rule landed wherever its author happened to be standing. Three of the four are
now this module; `triggers/` stays out on purpose (it answers whether to START, on a wall clock).

This module is the seam that makes them one mechanism. `PP-11` introduced **nothing new**: `Lane`,
`ContainerConcurrency` and `Wip` are exactly the three rules `frontier()` already applied, moved
behind a named interface and composed explicitly. That restraint was the point — a refactor that
also changed behaviour could not be verified, because there is no oracle for *"did the scheduler
still decide the same thing"*. The oracle is `tests/test_workflows_frontier_golden.py`, whose
fixtures were captured before a line of this existed.

`PP-12` is where capability lands, on the seam already proven inert: `Lease` (exclusive occupancy of
a named external resource — the pool's capability), and `Dwell`/`MetricGate` (a bake floor and a
metric gate that rolls a step back — the loop's). Both reuse the proven implementation rather than
re-deriving it: `Lease` decides with `pool.acquire`, the same compare-and-swap decision the task
pool's flocked claim path uses, and `Dwell`/`MetricGate` parse with the loop's own
`step_config_from_phase` and judge with `loop.tick.evaluate`. Nothing here is a second
implementation of anything. `default_policies(state=None)` returns `PP-11`'s three, so a spec
declaring none of the new keys runs the same code it always did — additivity by construction,
re-proven by the golden file.

`PP-13` retires the last duplicate: `pool.py`'s private `frontier`/`next` projection. What the pool
knew that this module did not was an ORDER — priority, blocking-count, overdue, then recency then id
— and that is not a policy, because a policy answers *how many* while an order answers *which
first*. So it lands as `rank_key`, a comparator, and `ready()` is the two of them composed: the
`Lease` policy decides who is excluded, the comparator decides who is on top. The exclusion is no
longer an `if candidate.leased_by` inside one surface's projection — it is the same composed verdict
the engine's frontier gets, which is the whole of what "one admission core" buys.

**One shape covers both of today's admission questions.** Lane admission asks "may one more `llm`
node start, given how many are already in flight"; a capped `foreach` asks "may one more item of
this container start, given how many are already under way". Both are *a capacity over a keyed
bucket*, which is why one `AdmissionRequest` → `capacity` → `admits(in_flight)` chain expresses
both, and why a policy that has no opinion on a scope simply returns `None` instead of needing a
scope-specific list.

**Tightest wins, and ties go to the named refusal.** Composing by minimum is the only rule that
cannot be gamed by policy order: adding a policy may narrow what runs, never widen it. Ties matter
because two policies really can bind at the same number — `max_concurrency: 1` under a run-level
WIP=1 invariant — and the *number* being equal does not make the two refusals equal. One is a
declared invariant being enforced, which the run records by name in `Frontier.wip_held` so "why is
item 2 not running" is answerable from the ledger; the other is anonymous container pressure,
recorded nowhere. So a tie is broken toward the higher `rank`, and the invariant ranks above the
capacity limit. That is not a new rule: it is `cap = 1 if wip else _max_concurrency(node)` — the
line this module replaces — written down where it can be tested.

**Purity is load-bearing.** No clock, no I/O, no randomness, and no mutation of the arguments. The
frontier is re-derived from persisted state every tick rather than incrementally patched, which is
what makes `rewind` tractable and replay meaningful; a policy that consulted the wall clock would
decide differently on replay and the journal's guarantees would be worthless. Enforced by an AST
rail in `tests/test_workflows_frontier_golden.py`, not by convention.

A lease TTL and a bake floor both imply a clock, which is exactly the rail `PP-12` had to satisfy
rather than route around. It is satisfied by taking `now` as a PARAMETER (`AdmissionState`), the
way `pool.acquire(..., now=)` and `loop.tick.evaluate(cfg, state, now)` already do: the impurity
moves to the single caller that owns a clock and the run's persisted state, and every `capacity()`
here stays a pure function of what it was handed. The rail still passes unweakened — the two
modules it scans import no clock, and `AdmissionState` is what makes that possible rather than
what hides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from personalclaw.loop.tick import Action, Decision, StepConfig, TickConfig, TickState
from personalclaw.loop.tick import evaluate as evaluate_step
from personalclaw.loop.tick import step_config_from_phase
from personalclaw.workflows import pool
from personalclaw.workflows.models import LANE_COMPUTE, LANE_IO, LANE_LLM, Node

#: Default per-lane admission caps. `compute` is effectively unmetered — a transform is
#: microseconds of pure data reshaping, and capping it would only add latency.
DEFAULT_LANE_CAPS = {LANE_LLM: 4, LANE_IO: 2, LANE_COMPUTE: 64}


class Scope(str, Enum):
    """Which bucket a request is about. A policy answers one scope and abstains on the rest."""

    #: One typed engine lane (`llm` / `io` / `compute`), keyed by lane name.
    LANE = "lane"
    #: One fan-out container's in-flight items, keyed by the container's instance path.
    CONTAINER = "container"
    #: One named EXTERNAL resource an item holds for its whole body, keyed by the resource name
    #: (PP-12). Not the container bucket: two different fan-outs — in two different runs — can
    #: contend for one endpoint, and a per-container count cannot express that. This is the bucket
    #: whose occupancy lives on disk rather than in the run's own state.
    RESOURCE = "resource"
    #: One step instance about to start, keyed by its instance path (PP-12). A step bucket holds at
    #: most one thing, so its only interesting capacity is ZERO — "not yet". That is what a bake
    #: floor and a metric gate say, which is why they are capacities like everything else rather
    #: than a second kind of rule.
    STEP = "step"


class Hold(str, Enum):
    """How a refusal by this policy is reported — the `wip_held` vs `deferred` distinction.

    Collapsing these would make "why is item 2 not running" unanswerable from the ledger, which is
    the entire reason `wip_held` exists as a separate field.
    """

    #: Lane pressure. Not an error, and not a decision anybody made: the next tick admits it.
    DEFERRED = "deferred"
    #: A declared invariant was enforced. The run said WIP=1, so the refusal is a decision worth
    #: reading back, and an unrecorded refusal is indistinguishable from a scheduler that forgot.
    WIP_HELD = "wip_held"
    #: Refused, recorded nowhere. A capped container's unstarted item is not `deferred` (the lane
    #: was fine) and not `wip_held` (no invariant was declared) — it is simply not its turn.
    UNRECORDED = ""
    #: Another holder owns the resource this item needs (PP-12). Distinct from `DEFERRED` because
    #: the holder is usually not this run: "waiting for the `endpoint` lease held by run X" and
    #: "the llm lane is full" send a reader to completely different places.
    LEASED = "leased"
    #: A declared bake floor has not elapsed (PP-12). The refusal expires by itself, at a time the
    #: engine can name — which is why it is worth distinguishing from every other hold.
    BAKING = "baking"
    #: A metric regressed (PP-12). The only refusal that also CHANGES THE PLAN: it rolls a step
    #: back. Reported as anything else, the re-run of an already-finished step looks like a bug.
    REGRESSED = "regressed"


#: Tie-break rank, used ONLY when two policies bind at the same capacity. Ordered by how badly a
#: wrong name misleads the person reading the refusal back.
#:
#: An anonymous per-node capacity limit is the least informative, a declared invariant outranks it
#: (a run-level invariant a per-node knob can quietly contradict is not an invariant), mutual
#: exclusion over an EXTERNAL resource outranks that (its failure mode is not a slow run but two
#: holders both believing they own the resource), and a metric regression is highest because it is
#: the only refusal that also rolls a step back — a rollback attributed to a bake floor would leave
#: the re-run of a finished step unexplained.
RANK_CAPACITY = 0
RANK_INVARIANT = 10
RANK_EXCLUSION = 20
RANK_REGRESSION = 30


@dataclass(frozen=True)
class AdmissionRequest:
    """One admission question. Carries only what the policies read, so a policy cannot reach
    sideways into engine state and quietly become impure."""

    scope: Scope
    #: Lane name for `LANE`, container instance path for `CONTAINER`.
    key: str
    #: The container node, for policies that read its declaration. `None` for lane requests.
    node: Node | None = None


class AdmissionPolicy:
    """One rule of the form *(declaration) → capacity for a bucket*.

    Capacity is derived from the DECLARATION alone — never from how full the bucket is — so the
    frontier can ask once and skip counting entirely when nothing is bounded. That is not a
    micro-optimisation: counting in-flight items walks every item's subtree, and the uncapped
    fan-out is the common case.
    """

    #: Stable identifier, journalled as the refusal's reason rather than a class name.
    name: str = ""
    #: Which `Frontier` field names a refusal by this policy.
    hold: Hold = Hold.UNRECORDED
    #: Tie-break rank when two policies bind at the same capacity.
    rank: int = RANK_CAPACITY

    def capacity(self, request: AdmissionRequest) -> int | None:
        """The most this policy will allow in `request`'s bucket, or `None` to abstain."""
        raise NotImplementedError


@dataclass(frozen=True)
class Limits:
    """Per-lane concurrency caps, as a config carries them. A single total is accepted and split, so
    a config carrying one number keeps working (WF2-R21 back-compat).

    Parsing lives here rather than in the `Lane` policy because a cap read from `config.json` and a
    cap the policy enforces are different jobs: one is lenient about what a user typed, the other is
    a pure lookup.
    """

    lanes: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LANE_CAPS))

    @classmethod
    def from_config(cls, value: Any) -> Limits:
        """Accept either `{llm: 4, io: 2}` or a bare total. A bare total gives the LLM
        lane the lion's share: it is the lane a workflow actually spends time in."""
        if isinstance(value, dict):
            lanes = dict(DEFAULT_LANE_CAPS)
            for key, raw in value.items():
                name = str(key)
                if name in lanes:
                    try:
                        lanes[name] = max(1, int(raw))
                    except (TypeError, ValueError):
                        continue
            return cls(lanes=lanes)
        try:
            total = max(1, int(value))
        except (TypeError, ValueError):
            return cls()
        io = max(1, total // 3)
        return cls(lanes={LANE_LLM: max(1, total - io), LANE_IO: io, LANE_COMPUTE: 64})

    def cap(self, lane: str) -> int:
        return int(self.lanes.get(lane, DEFAULT_LANE_CAPS.get(lane, 1)))


@dataclass(frozen=True)
class Lane(AdmissionPolicy):
    """Typed-lane caps as an admission policy (WF2-R21). A `foreach` over minute-long local-model
    actions saturates the `io` lane while `llm` stages keep flowing; excess is deferred, not
    dropped — the next tick admits it."""

    limits: Limits = field(default_factory=Limits)

    name = "lane"
    hold = Hold.DEFERRED
    rank = RANK_CAPACITY

    def capacity(self, request: AdmissionRequest) -> int | None:
        if request.scope != Scope.LANE:
            return None
        return self.limits.cap(request.key)


@dataclass(frozen=True)
class ContainerConcurrency(AdmissionPolicy):
    """A fan-out's declared `max_concurrency` — how many ITEMS may be in flight at once.

    Separate from the lane caps because they answer different questions: a lane cap protects the
    engine from one greedy run, while this protects a scarce resource each ITEM holds for its whole
    body (a checkout, a lock, a rate-limited endpoint). Unset is unbounded, which is right for the
    common case of a handful of cheap items.
    """

    name = "max_concurrency"
    hold = Hold.UNRECORDED
    rank = RANK_CAPACITY

    def capacity(self, request: AdmissionRequest) -> int | None:
        if request.scope != Scope.CONTAINER or request.node is None:
            return None
        raw = (request.node.config or {}).get("max_concurrency")
        # A true int only. `int(1.5)` truncates to 1 and `int(True)` is 1, so a coercing read would
        # let a spec typo silently serialize a fan-out to one item at a time — the most expensive
        # possible misreading, and invisible because the run still succeeds.
        if not isinstance(raw, int) or isinstance(raw, bool):
            return None
        return raw if raw > 0 else None


@dataclass(frozen=True)
class Wip(AdmissionPolicy):
    """The run-level WIP=1 invariant (`single_active_feature`, LOOPS-EVOLUTION R5b: +37% feature
    completion). Caps EVERY fan-out in the run to one in-flight item, whatever each `foreach`
    declared for itself.

    Inactive means abstain rather than "unbounded", so a run without the invariant composes exactly
    as it did before this policy existed. Active, it outranks `ContainerConcurrency` on a tie: the
    contradiction is refused at authoring time by the validator (`WF_WIP_CONTRADICTION`), and this
    is the runtime half — clamping silently is what makes a control look enforced while a
    `max_concurrency: 3` quietly wins.
    """

    active: bool = False

    name = "single_active_feature"
    hold = Hold.WIP_HELD
    rank = RANK_INVARIANT

    def capacity(self, request: AdmissionRequest) -> int | None:
        if request.scope != Scope.CONTAINER or not self.active:
            return None
        return 1


@dataclass(frozen=True)
class AdmissionState:
    """The clock-and-disk inputs the `PP-12` policies read, gathered ONCE by the caller.

    **This is how `Lease(ttl)` gets a TTL without breaking `frontier()`'s purity.** A TTL implies a
    clock and a lease implies persisted occupancy, and the frontier may consult neither. So `now`
    and the lease records are PARAMETERS — the established pattern in this codebase, and the same
    one `pool.acquire(..., now=)` and `loop.tick.evaluate(cfg, state, now)` already use. Every
    `capacity()` below stays a pure function of `(declaration, this snapshot, request)`: hand it the
    same snapshot twice and it decides twice the same, which is what makes replay meaningful.

    The impurity does not vanish — it MOVES, to the one caller that already owns a clock and the
    run's persisted state (`controller.RunController`). `frontier()` never builds one of these,
    which is also the structural half of this atom's additivity claim: `default_policies()` with no
    state returns exactly the three `PP-11` policies, so a spec declaring none of the new keys does
    not merely behave as before — it runs the same code.
    """

    #: Wall clock for this decision, supplied by the caller. Never read here.
    now: float = 0.0
    #: Who is asking, for the lease. A session-scoped identity (run + item), because the whole point
    #: of a named holder is that a stuck claim is diagnosable.
    holder: str = ""
    #: Resource name → the lease record persisted on disk, as the caller read it.
    leases: Mapping[str, pool.Lease] = field(default_factory=dict)
    #: TTL for a lease this pass would take. Bounded by `pool.MAX_LEASE_SECS` inside the record.
    lease_ttl_secs: int = pool.DEFAULT_LEASE_SECS
    #: Step path → when its bake window started (the prior step's completion), from persisted state.
    since: Mapping[str, float] = field(default_factory=dict)
    #: Step path → the metric observed for it, resolved from the run's outputs by the caller.
    metrics: Mapping[str, float] = field(default_factory=dict)
    #: Step path → the floor the prior step established. Below it, the metric has REGRESSED.
    floors: Mapping[str, float] = field(default_factory=dict)
    #: Step path → consecutive rollbacks already taken on it, so the cap can bite.
    rollbacks: Mapping[str, int] = field(default_factory=dict)
    #: Consecutive rollbacks on one step before giving up. `loop.tick.TickConfig`'s default.
    rollback_cap: int = 3


@dataclass(frozen=True)
class Lease(AdmissionPolicy):
    """Exclusive occupancy of a named external resource — a `lease:` declaration (PP-12).

    The capability neither lane caps nor `max_concurrency` can express: a resource each ITEM HOLDS
    for the length of its body, shared across containers, runs and processes. `max_concurrency: 1`
    serializes one fan-out inside one run; it says nothing about the second run that starts while
    the first is mid-flight, and nothing at all after a restart.

    **The decision is `pool.acquire`, unchanged.** Not a second lease implementation: `S57`
    measured an `unlink`-based single-use claim failing 36 of 40 races, and a lease that loses a
    race is worse than no lease because both holders believe they own the work. So this policy
    calls the same decision function the task pool's claim path calls, and the WRITE stays where
    the compare-and-swap lives (`pool.claim_task`, a `single_flight` flocked read-modify-write).
    This policy therefore ADVISES — it composes with the others, and names the refusal — while the
    claim remains authoritative. A caller that admitted here and then lost the flock must still
    hold the item, and that is not a redundancy: it is the difference between deciding and
    committing.

    An empty `holder` abstains rather than refusing, following `pool.read_lease`'s own precedent
    (a malformed lease reads as NO lease): failing closed on a missing identity would strand every
    leased item forever with nobody able to release it, and a strand does not resolve while
    contention does. Safe precisely because the claim, not this verdict, grants the resource.
    """

    state: AdmissionState = field(default_factory=AdmissionState)

    name = "lease"
    hold = Hold.LEASED
    rank = RANK_EXCLUSION

    def capacity(self, request: AdmissionRequest) -> int | None:
        if request.scope != Scope.RESOURCE or not request.key:
            return None
        if not self.state.holder.strip():
            return None
        decision, _error = pool.acquire(
            self.state.leases.get(request.key),
            task_id=request.key,
            holder=self.state.holder,
            now=self.state.now,
            ttl_seconds=self.state.lease_ttl_secs,
        )
        # 1 rather than "unbounded": a lease is by definition an occupancy of ONE, and returning
        # `None` when the resource happens to be free would let a wider policy admit two items into
        # a bucket that can hold one.
        return 1 if decision is not None else 0


@dataclass(frozen=True)
class Dwell(AdmissionPolicy):
    """A bake floor before a step may start — `min_dwell_secs` (PP-12).

    Dwell exists only in `loop/tick.evaluate` today, consumed by exactly one kind, so a workflow
    cannot say "let the deploy settle for ten minutes before the smoke test". The threshold is
    parsed by the loop's OWN parser (`step_config_from_phase`), not re-read here: two parsers for
    `min_dwell_secs` is exactly the four-dialect problem this program exists to end, and that parser
    already ignores garbage so a typo degrades to "no dwell" instead of a stalled run.

    Abstains once the window has elapsed rather than returning 1 — an elapsed bake floor has no
    opinion about how many things may run, and saying 1 would silently serialize the lane.
    """

    state: AdmissionState = field(default_factory=AdmissionState)

    name = "min_dwell_secs"
    hold = Hold.BAKING
    rank = RANK_INVARIANT

    def capacity(self, request: AdmissionRequest) -> int | None:
        if request.scope != Scope.STEP or request.node is None:
            return None
        floor = step_config_from_phase(dict(request.node.config or {})).min_dwell_secs
        if floor <= 0:
            return None
        started = self.state.since.get(request.key)
        if started is None:
            # No prior completion to measure from: the first step of a run has nothing to bake
            # after. Refusing here would hold a run that has not started anything yet.
            return None
        return 0 if (self.state.now - started) < floor else None


@dataclass(frozen=True)
class MetricGate(AdmissionPolicy):
    """A metric gate on a step — `metric_pass` / `metric_hold`, with a regression rolling back.

    Reuses `loop.tick.evaluate` WHOLE, not just its thresholds: the config is built by the loop's
    parser and fed to the loop's branch order, so the `metric < prior_step_floor → ROLLBACK` rule
    has exactly one implementation in the tree. A second copy of that comparison is how the two
    schedulers drifted in the first place, and the drift would be invisible — both sides would look
    plausible and only disagree on the boundary.

    `capacity()` collapses the loop's `Action` to the one bit admission carries (`0` = not yet), and
    `decision()` exposes the full `Decision` for the caller that must ACT on a rollback. That split
    is deliberate: composition needs a number, while "which step do I re-run, and have I given up
    yet" is a plan change, and the policy is not the thing that changes plans.

    An unobserved metric abstains. A gate cannot judge a step that has not produced a number yet;
    refusing would deadlock the very run that was going to produce it.
    """

    state: AdmissionState = field(default_factory=AdmissionState)

    name = "metric_gate"
    hold = Hold.REGRESSED
    rank = RANK_REGRESSION

    def decision(self, request: AdmissionRequest) -> Decision | None:
        """The loop's own verdict for one workflow step, or `None` when this policy abstains."""
        if request.scope != Scope.STEP or request.node is None:
            return None
        cfg = step_config_from_phase(dict(request.node.config or {}))
        if cfg.metric_pass is None:
            return None
        metric = self.state.metrics.get(request.key)
        if metric is None:
            return None
        return evaluate_step(
            # A TRAILING NEUTRAL STEP, deliberately: `evaluate` reports an advance off the end of
            # the plan as `COMPLETE`, and this policy asks about ONE step — "the plan is finished"
            # is not an answer it can carry. With the trailing step, `COMPLETE` can only mean the
            # rollback cap was hit, which is the one COMPLETE the caller must act on.
            TickConfig(steps=(cfg, StepConfig()), rollback_cap=self.state.rollback_cap),
            TickState(
                step_index=0,
                # Neutralised so the dwell branch cannot fire inside the metric gate: dwell is
                # `Dwell`'s job, and two policies enforcing one threshold is how a control gets
                # enforced twice and reported once.
                step_started_at=self.state.now - cfg.min_dwell_secs,
                # The gate's I/O half already ran — the step's metric is IN HAND, which is the
                # workflow equivalent of the adapter's verify/judge having answered.
                gate_passed=True,
                findings_in_step=cfg.min_findings,
                metric=metric,
                prior_step_floor=self.state.floors.get(request.key),
                rollbacks_on_step=int(self.state.rollbacks.get(request.key, 0)),
            ),
            self.state.now,
        )

    def capacity(self, request: AdmissionRequest) -> int | None:
        decision = self.decision(request)
        if decision is None:
            return None
        # HOLD (marginal), ROLLBACK (regressed) and COMPLETE (rollback cap reached) all mean "not
        # this step, not now". ADVANCE and EXECUTE mean the gate has no objection.
        if decision.action in (Action.HOLD, Action.ROLLBACK, Action.COMPLETE):
            return 0
        return None


@dataclass(frozen=True)
class Admission:
    """The composed verdict for one bucket.

    `binding` is which policy set the capacity — the field that makes a refusal explainable. A
    verdict that only carried a number could say "no" but never "no, because the run declared
    WIP=1", and the second is the one a user needs.
    """

    capacity: int | None = None
    binding: AdmissionPolicy | None = None

    @property
    def bounded(self) -> bool:
        """Whether any policy had an opinion. Unbounded verdicts let the caller skip counting."""
        return self.capacity is not None

    @property
    def hold(self) -> Hold:
        """How a refusal by this verdict is reported. Unbounded verdicts never refuse."""
        return self.binding.hold if self.binding is not None else Hold.UNRECORDED

    def admits(self, in_flight: int) -> bool:
        """Whether one more may start given `in_flight` already occupying the bucket."""
        return self.capacity is None or in_flight < self.capacity


def compose(policies: tuple[AdmissionPolicy, ...], request: AdmissionRequest) -> Admission:
    """Tightest wins; a tie goes to the higher `rank`.

    Minimum rather than first-match or last-match because that is the only composition an added
    policy cannot loosen — the property that lets `PP-12` add `Lease` without re-auditing these
    three. Abstentions (`None`) are skipped entirely rather than treated as an infinite cap, so a
    bucket no policy speaks to stays genuinely unbounded.
    """
    best: AdmissionPolicy | None = None
    best_cap: int | None = None
    for policy in policies:
        cap = policy.capacity(request)
        if cap is None:
            continue
        if best_cap is None or cap < best_cap:
            best, best_cap = policy, cap
        elif cap == best_cap and best is not None and policy.rank > best.rank:
            best = policy
    return Admission(capacity=best_cap, binding=best)


def default_policies(
    limits: Limits,
    *,
    single_active_feature: bool,
    state: AdmissionState | None = None,
) -> tuple[AdmissionPolicy, ...]:
    """The admission rules, in order: lane caps, the container's declaration, the run's invariant,
    then (given a `state`) the resource lease, the bake floor and the metric gate. The order is
    documentation — composition is by capacity, not by position — but it reads outermost-first,
    which is the order a reader asks the questions in.

    Built once per `frontier()` call and threaded down the recursion, so every container in a run is
    judged by the same list. Constructing them per-node is how a run-level invariant becomes
    per-node-optional by accident.

    **`state=None` returns exactly `PP-11`'s three policies.** That is not a convenience default: it
    is this atom's additivity guarantee made structural. `frontier()` is pure and cannot build an
    `AdmissionState`, so the frontier's list is unchanged by construction and `PP-11`'s golden file
    is a real proof rather than a coincidence. The `PP-12` policies answer scopes the frontier
    never asks about (`RESOURCE`, `STEP`), so even the widened list leaves every lane and container
    verdict identical — asserted, not assumed, in `test_workflows_admission_policies.py`.
    """
    base: tuple[AdmissionPolicy, ...] = (
        Lane(limits=limits),
        ContainerConcurrency(),
        Wip(active=single_active_feature),
    )
    if state is None:
        return base
    return base + (Lease(state=state), Dwell(state=state), MetricGate(state=state))


# ── the ready projection: which work is on top, not how much of it may run (PP-13) ──


#: The identity the READ-ONLY ready projection asks as. A holder that never takes a lease gets the
#: honest answer to "what is free right now": work another holder is actively holding is excluded,
#: and work whose lease has EXPIRED is not — because `pool.acquire` treats an expired lease as
#: takeable, which is the same reasoning `containers.board_row` applies when it drops an expired
#: claim badge rather than rendering it. The retired `pool.frontier` filtered on `leased_by` being
#: truthy, so it hid work whose holder was already gone; that is the one behaviour this retirement
#: deliberately does not preserve, and it is pinned by its own test.
OBSERVER = "work-board"


class Urgency(str, Enum):
    """Why an item is at the top. Shown, not just used for sorting.

    A ranked list whose order cannot be explained is one a user overrides, and then the projection
    is decoration.
    """

    OVERDUE = "overdue"
    BLOCKING_OTHERS = "blocking_others"
    HIGH_PRIORITY = "high_priority"
    NORMAL = "normal"


#: Priority weights, keyed by the REAL `TaskPriority` values. Measured (S60): the shipped rungs are
#: `critical | high | medium | low | trivial` — there is no `urgent`. A hand-written scale invented
#: `urgent` and omitted `critical`, so the single most important rung in the product would have
#: scored as the default and a `critical` task would have ranked below a `high` one.
PRIORITY_WEIGHT = {
    "critical": 5.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
    "trivial": 0.5,
}


@dataclass(frozen=True)
class ReadyItem:
    """One unit of work as the ready projection sees it. A view, not a `Task`.

    Deliberately not a `Task`: building this from the fields the ranking actually reads means a
    caller can project from any provider's tasks, and a new `Task` field cannot silently change the
    ranking. The adapter that fills it is where the clock and the disk live (`tasks/registry.py`),
    which is what keeps this module's purity rail satisfiable.

    `unblocked` keeps the retired projection's polarity verbatim. This atom's whole bar is "the same
    ready set, in the same order"; flipping the sense of the boolean the equivalence turns on would
    add a way to be silently wrong — a polarity bug flips both sides of a test written after the
    rename — for no gain at all.
    """

    item_id: str
    title: str = ""
    priority: str = "medium"
    unblocked: bool = True
    blocks_count: int = 0
    overdue: bool = False
    updated_at: float = 0.0

    def urgency(self) -> Urgency:
        if self.overdue:
            return Urgency.OVERDUE
        if self.blocks_count > 0:
            return Urgency.BLOCKING_OTHERS
        if PRIORITY_WEIGHT.get(self.priority, 2.0) >= PRIORITY_WEIGHT["high"]:
            return Urgency.HIGH_PRIORITY
        return Urgency.NORMAL

    def score(self) -> float:
        """Rank score: priority, plus how much this item unblocks, plus an overdue bump.

        `blocks_count` is in the score because a medium task blocking four others is more valuable
        than a high task blocking none — the whole point of a dependency-aware pool.
        """
        base = PRIORITY_WEIGHT.get(self.priority, 2.0)
        return base + min(3.0, 0.5 * self.blocks_count) + (2.0 if self.overdue else 0.0)


def rank_key(item: ReadyItem) -> tuple[float, float, str]:
    """The pool's ordering, as a comparator on the unified core.

    An order is not an admission policy: a policy answers *how many may run* and composes by
    minimum, while an order answers *which one first* and composes by nothing — two orders do not
    have a tightest. So it is a separate, named function rather than a fourth thing bolted onto
    `AdmissionPolicy`, and `ready()` is the two composed.

    Ties break on recency then id. A stable order matters because an unstable "next task" makes an
    agent thrash between two equals, and it is the reason this returns a total order rather than
    leaving equal-scoring items in whatever sequence the store happened to yield.
    """
    return (-item.score(), -item.updated_at, item.item_id)


def explain(item: ReadyItem) -> str:
    """Why this item ranks where it does, in one line."""
    reasons = [f"priority={item.priority}"]
    if item.overdue:
        reasons.append("overdue")
    if item.blocks_count:
        reasons.append(f"blocks {item.blocks_count} other(s)")
    return f"{item.item_id}: " + ", ".join(reasons)


def ready(items: Sequence[ReadyItem], policies: tuple[AdmissionPolicy, ...]) -> list[ReadyItem]:
    """Everything workable right now, ranked — admission and order, composed.

    Blocked items are dropped first because they are not workable at all, so no policy needs an
    opinion about them. Everything else is put to the composed policy list as a `RESOURCE` request
    keyed by its own id, which is exactly how the pool's lease is keyed (`pool.claim_task` writes a
    sidecar per task id). A capacity of zero means another holder owns it; an abstention (nobody
    speaks to this bucket) means free.

    Pure, like every `capacity()` above: the clock and the lease records arrive inside the policies'
    `AdmissionState`, gathered once by the caller that owns them.
    """
    admitted = [
        item
        for item in items
        if item.unblocked
        and compose(policies, AdmissionRequest(scope=Scope.RESOURCE, key=item.item_id)).admits(0)
    ]
    return sorted(admitted, key=rank_key)


def next_ready(
    items: Sequence[ReadyItem], policies: tuple[AdmissionPolicy, ...]
) -> ReadyItem | None:
    """The single top item, or `None` when nothing is workable.

    `ready()`'s head by construction, so the list and the pick can never disagree — which is what
    stopped "what should I work on" from being reimplemented per surface.
    """
    ranked = ready(items, policies)
    return ranked[0] if ranked else None
