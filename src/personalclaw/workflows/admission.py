"""Admission policies — the ordered rules that decide what the frontier may launch (PP-11).

Four schedulers in this repo answer the same question — *what may run now, given persisted
state?* — and share zero lines: `workflows/tick.frontier()` (typed lanes, per-container
`max_concurrency`, WIP=1), `loop/tick.evaluate()` (dwell / `min_findings` / metric gates),
`workflows/pool.py`'s `frontier`/`next` (priority + blocking-count + overdue with TTL'd leases),
and `triggers/` `tick_once`. Each holds a capability the others structurally cannot express, so
every new admission rule lands wherever its author happened to be standing.

This module is the seam that makes them one mechanism. It introduces **nothing new**: the three
policies below are exactly the three rules `frontier()` already applied, moved behind a named
interface and composed explicitly. That restraint is the point — a refactor that also changed
behaviour could not be verified, because there is no oracle for *"did the scheduler still decide
the same thing"*. The oracle here is `tests/test_workflows_frontier_golden.py`, whose fixtures were
captured before a line of this existed. New capability (`Lease`, `Dwell`, `MetricGate`) lands in
`PP-12`, on a seam already proven inert.

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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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


#: Tie-break rank, used ONLY when two policies bind at the same capacity. Ordered by whether the
#: refusal has a name a user can read back: a declared run-level invariant outranks an anonymous
#: per-node capacity limit, because a run-level invariant a per-node knob can quietly contradict is
#: not an invariant.
RANK_CAPACITY = 0
RANK_INVARIANT = 10


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


def default_policies(limits: Limits, *, single_active_feature: bool) -> tuple[AdmissionPolicy, ...]:
    """Today's three rules, in order: lane caps, then the container's declaration, then the run's
    invariant. The order is documentation — composition is by capacity, not by position — but it
    reads outermost-first, which is the order a reader asks the questions in.

    Built once per `frontier()` call and threaded down the recursion, so every container in a run is
    judged by the same list. Constructing them per-node is how a run-level invariant becomes
    per-node-optional by accident.
    """
    return (Lane(limits=limits), ContainerConcurrency(), Wip(active=single_active_feature))
