"""The frontier — a PURE function from (spec tree, instance states) to what may run now.

This module is deliberately free of I/O, clocks, and randomness. `frontier()` takes a
spec and a state map and returns a decision; the same inputs always produce the same
output. That is not stylistic preference, it is what makes the engine testable and
`rewind` tractable: after a rewind mutates state, the scheduler is re-derived from
scratch rather than patched, so there is no incremental bookkeeping to get wrong.

Three rules carry most of the design weight:

**Containers do not execute.** A `sequence` has no work of its own — it is a scheduling
policy over its children. So the frontier recurses into containers and only ever
returns leaf work. A container's own state is *derived* from its children's, which is
why `container_outcome()` exists and why nothing writes a container's state directly.

**Ordering is DERIVED, not declared (`PP-2`).** A node waits for the nodes its bindings
read. The edge list comes from `validator.dep_ordering_edges` — the same derivation the
validator's own rules use — so admission and validation cannot disagree about what "ordered
first" means; keeping a second, hand-maintained list was the defect `PP-1` made visible and
this module's gate deletes. A hand-written `needs` is folded into that same list and honoured
GLOBALLY, so a diamond may span two containers. Nothing here trusts a `needs` on its own: an
edge the spec's structure cannot honour is refused at authoring time and never reaches the
frontier, because a scheduler that waits on an impossible edge hangs instead of failing.

This derived ordering is ORTHOGONAL to admission (`PP-11`): ordering decides whether a node is
even a CANDIDATE this tick (are its producers terminal?), admission decides whether a candidate
gets a SLOT (is the lane/container/WIP budget free?). Both are built once in `frontier()` and
threaded down the recursion — `order`/`inst` for ordering, `policies` for admission — so
neither can become per-node-optional by accident.

**Active-edge join gating (WF2-R18).** A join must not wait on a leg that will never run.
A `branch` picking `cases[bug]` leaves `cases[feat]` unreachable; a join that waited on
"all predecessors" would deadlock forever. Conversely a join firing on "any completed
predecessor" fires early on a fan-out whose other legs are still waiting. Both directions
are bugs, so the rule here is: **an ordering edge is satisfied by any TERMINAL predecessor,
and unreachable paths are made terminal by marking them SKIPPED.** Declining is recorded
explicitly (`declined_edges`) rather than inferred from "the source routed elsewhere" —
inferring it would starve a sibling whose `needs` names a branch, since routing among
cases says nothing about that sibling.

Reachability is where the two directions are decided, and the asymmetry is deliberate. A
SKIPPED predecessor SATISFIES a plain ordering edge (it is terminal — that is what keeps a
join off an untaken leg), but a SKIPPED predecessor whose OUTPUT the reader binds makes the
reader unreachable: the output will never exist, so waiting is a hang and running is a
guaranteed binding failure. Only that reader is skipped, and only through a real dataflow
edge. Skipping a reader whose producer is merely still pending would be the far worse bug — a
join would then fire early on a live leg and the run would report a plausible wrong answer
instead of waiting.

The wait-entry subtlety still matters: a `wait`/`gate` enters WAITING rather than
completing, and WAITING is not terminal, so a join behind it correctly keeps waiting
instead of firing on the fast leg alone.

**Admission is a policy list, not a rule (PP-11).** Every "may this start?" question in this
module goes through `admission.compose()` over one ordered list of `AdmissionPolicy` objects —
today typed lane caps, a container's `max_concurrency`, and the run's WIP=1 invariant — composed
tightest-wins. Three schedulers elsewhere in the repo answer the same question with their own
rules; this seam is where they converge. The list is built once per `frontier()` call and threaded
down the recursion, so a run-level invariant cannot become per-node-optional by accident.

Lane admission itself is WF2-R21: ready work is admitted per-lane, derived from node kind, so a
`foreach` over minute-long local-model actions saturates the `io` lane while `llm` stages keep
flowing. Excess is `deferred` rather than dropped — the next tick admits it.

The frontier reports `blocked` when nothing can run and nothing is running: that state
is a deadlock, and naming it here rather than letting the run hang forever is the whole
point of computing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# `Limits` is named in `frontier()`'s signature but LIVES with the policy that enforces it
# (`admission.Lane`), not with the projection that consults it — the lane caps ARE an admission
# rule, which is the fact this seam makes structural.
from personalclaw.workflows.admission import (
    AdmissionPolicy,
    AdmissionRequest,
    Hold,
    Limits,
    Scope,
    compose,
    default_policies,
)
from personalclaw.workflows.bindings import BindingContext, BindingError, resolve_expr
from personalclaw.workflows.conditions import evaluate as evaluate_condition
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    ItemErrorPolicy,
    JoinMode,
    LoopMode,
    Node,
    NodeKind,
    lane_for,
    walk,
)
from personalclaw.workflows.validator import EDGE_BINDING, dep_edges_for_root


@dataclass
class ReadyNode:
    """One unit of launchable work. Carries the resolved instance path because the path
    — not the node id — is the engine's addressing key: a `foreach` body produces many
    instances of one node, and only the path distinguishes them."""

    path: str
    node: Node
    lane: str
    #: foreach/loop iteration context, threaded into the binding context at dispatch.
    item: Any = None
    has_item: bool = False
    iter_index: int | None = None

    @property
    def node_id(self) -> str:
        return self.node.id


@dataclass
class Frontier:
    """The scheduling decision for one tick.

    `blocked` is the important field. A run with no ready work and nothing running has
    deadlocked, and the engine must fail it loudly — a silent hang is the worst outcome
    for an unattended run.
    """

    ready: list[ReadyNode] = field(default_factory=list)
    #: Ready but lane-capped. Not an error — the next tick admits them.
    deferred: list[ReadyNode] = field(default_factory=list)
    #: Item paths a `single_active_feature` run REFUSED to start because another item of
    #: the same fan-out is still in flight (WIP=1). Separate from `deferred`, which is
    #: lane pressure: this one is a declared invariant being enforced, and the controller
    #: journals it so "why is item 2 not running" is answerable from the ledger rather
    #: than from reading the scheduler.
    wip_held: list[str] = field(default_factory=list)
    running: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    #: Paths on a path the run did not take. The controller marks these SKIPPED, which is
    #: what lets a downstream join proceed: a skipped predecessor is terminal, so it
    #: satisfies a `needs` edge instead of blocking it forever (WF2-R18).
    to_skip: list[str] = field(default_factory=list)
    complete: bool = False
    blocked: bool = False
    block_reason: str = ""
    #: Derived root outcome once complete — the run's terminal status comes from here.
    outcome: InstanceState | None = None

    @property
    def has_work(self) -> bool:
        return bool(self.ready)

    @property
    def is_idle(self) -> bool:
        """Nothing to launch and nothing in flight."""
        return not self.ready and not self.running and not self.waiting


def edge_key(src: str, dst: str) -> str:
    return f"{src}->{dst}"


# ── state helpers ────────────────────────────────────────────────────────────


def _state_of(states: dict[str, InstanceState], path: str) -> InstanceState:
    return states.get(path, InstanceState.PENDING)


def _is_terminal(st: InstanceState) -> bool:
    return st in TERMINAL_STATES


def _is_success(st: InstanceState) -> bool:
    return st in SUCCESS_STATES


def tolerate_failures(
    children: list[Node], child_states: list[InstanceState]
) -> list[InstanceState]:
    """Mask a FAILED child that declared `allow_failure: true` to DEGRADED (S148).

    🔴 `allow_failure` was DECLARED BY FIVE NODES IN A SHIPPED TEMPLATE AND READ BY NOTHING. All five
    of `rich-ingest`'s extraction lenses set it, and they run in a `parallel` with `join: all` —
    measured, `container_outcome([DONE]*4 + [FAILED], join=ALL)` is **FAILED**, so one flaky lens
    discarded the four that had succeeded. Losing four lenses' extracted knowledge because a fifth
    timed out is exactly the outcome the key exists to prevent.

    **DEGRADED, not DONE**, and that is the whole design decision. `SUCCESS_STATES` already includes
    DEGRADED, so the join proceeds — but the container does not claim clean success, and
    `container_outcome`'s existing ALL branch already propagates "any DEGRADED child ⇒ DEGRADED
    container". Masking to DONE instead would make a partial extraction indistinguishable from a
    complete one, which is the silent-drop shape this program keeps finding: the run would report
    success and nothing would ever say a lens was missing.

    Only FAILED is masked. A CANCELLED child is a decision someone made and a BLOCKED one is waiting
    on a human — tolerating either would convert a deliberate stop into a shrug.
    """
    if not children or len(children) != len(child_states):
        return child_states
    masked: list[InstanceState] = []
    for child, state in zip(children, child_states):
        tolerated = bool((getattr(child, "config", None) or {}).get("allow_failure"))
        if tolerated and state == InstanceState.FAILED:
            masked.append(InstanceState.DEGRADED)
        else:
            masked.append(state)
    return masked


def container_outcome(
    child_states: list[InstanceState], *, join: JoinMode = JoinMode.ALL, quorum: int = 0
) -> InstanceState:
    """Derive a container's state from its children. Never stored — always computed, so
    a rewind that resets children cannot leave a stale container verdict behind.

    A container whose children all skipped is itself SKIPPED rather than DONE: a
    `branch` whose taken case was skipped did not do work, and reporting success would
    make an empty run look productive.
    """
    if not child_states:
        return InstanceState.DONE
    if any(
        st
        in (
            InstanceState.PENDING,
            InstanceState.READY,
            InstanceState.RUNNING,
            InstanceState.WAITING,
        )
        for st in child_states
    ):
        return InstanceState.RUNNING
    successes = [st for st in child_states if _is_success(st)]
    if join == JoinMode.ANY:
        return InstanceState.DONE if successes else _worst(child_states)
    if join == JoinMode.QUORUM:
        return InstanceState.DONE if len(successes) >= max(1, quorum) else _worst(child_states)
    # ALL
    if len(successes) == len(child_states):
        return (
            InstanceState.DEGRADED
            if any(st == InstanceState.DEGRADED for st in child_states)
            else InstanceState.DONE
        )
    if all(st == InstanceState.SKIPPED for st in child_states):
        return InstanceState.SKIPPED
    return _worst(child_states)


#: Severity order for collapsing a mixed child set into one verdict. Ordered by how
#: much a human needs to know about it: a cancel is a decision, a failure is a defect.
_SEVERITY = (
    InstanceState.CANCELLED,
    InstanceState.BLOCKED,
    InstanceState.ESCALATED,
    InstanceState.SCOPE_VIOLATION,
    InstanceState.FAILED,
    InstanceState.SKIPPED,
    InstanceState.DISCARDED,
    InstanceState.NO_CHANGE,
    InstanceState.DEGRADED,
    InstanceState.DONE,
)


def _worst(states: list[InstanceState]) -> InstanceState:
    for candidate in _SEVERITY:
        if candidate in states:
            return candidate
    return InstanceState.DONE


# ── derived ordering (PP-2) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Ordering:
    """The ordering graph the frontier admits work against, derived from the spec (`PP-2`).

    Keyed by SPEC path — the position in the definition, `root.children[1].body`, with no
    fan-out decoration. Instance paths (`…body#2`) are resolved per reader at admission time
    by `_producer_instance`, because one spec-level edge inside a `foreach` body means a
    *separate* edge per item and the frontier must gate item 2's reader on item 2's producer.
    """

    #: reader spec path → ((producer spec path, producer node id, carries dataflow) …).
    #: `carries_data` is False for a hand-written `needs`, and that distinction is load-
    #: bearing: only a dataflow edge can make its reader UNREACHABLE when the producer is
    #: skipped, because only a dataflow edge needs the producer's output to exist.
    deps: dict[str, tuple[tuple[str, str, bool], ...]] = field(default_factory=dict)
    #: spec path → node, so a container producer's state can be derived rather than read.
    nodes: dict[str, Node] = field(default_factory=dict)
    #: Spec paths of `foreach`/`loop` bodies, LONGEST FIRST so the first prefix match is the
    #: innermost enclosing fan-out.
    iterated: tuple[str, ...] = ()


def ordering_for(root: Node) -> Ordering:
    """Derive the ordering graph from the spec's own bindings and `needs`.

    Computed here rather than accepted as an argument on purpose: a caller that forgot to pass
    it would get a scheduler with no ordering at all, which is the "live reader of a key nobody
    writes" shape — silently wrong and invisible. `frontier()` is called with the spec tree it
    needs, so it can never be handed a graph that does not describe that tree.

    Edges the spec's structure cannot honour are DROPPED, not gated on. Those are exactly
    `WF_UNORDERED_DEP`/`WF_UNSATISFIABLE_NEEDS` — a producer that encloses its reader, a
    `sequence` that runs it afterwards, a mutually exclusive `branch` case — and gating on one
    would turn a typed authoring-time refusal into a run that hangs. A spec that validates has
    none; a spec saved before the rule existed still runs exactly as it did.
    """
    deps: dict[str, list[tuple[str, str, bool]]] = {}
    for edge in dep_edges_for_root(root):
        if not edge.ordered:
            continue
        entry = (edge.producer_path, edge.producer_id, edge.origin == EDGE_BINDING)
        bucket = deps.setdefault(edge.reader_path, [])
        if entry not in bucket:
            bucket.append(entry)
    nodes = dict(walk(root))
    iterated = tuple(
        sorted(
            (
                f"{path}.body"
                for path, node in nodes.items()
                if node.body is not None and node.kind in (NodeKind.FOREACH, NodeKind.LOOP)
            ),
            key=len,
            reverse=True,
        )
    )
    return Ordering({k: tuple(v) for k, v in deps.items()}, nodes, iterated)


def _producer_instance(producer_spec: str, order: Ordering, inst: dict[str, str]) -> str | None:
    """The instance path holding this producer's state, or None when there is no single one.

    `inst` maps each fan-out body the walk is currently INSIDE to its instance path, so a
    reader in item 2 resolves its producer to item 2's copy. When the producer sits inside a
    fan-out the reader is not inside, there is no single answer — fifty items produced fifty
    outputs and a binding naming the node id resolves to whichever wrote last — so this returns
    None and the reader is admitted exactly as it is today. Censused before shipping: ZERO of
    the bundled templates contain such an edge, so this is a documented boundary rather than a
    silent gap, and inventing an answer here would be inventing fan-out aggregation semantics
    that no other part of the engine has.
    """
    for body in order.iterated:
        if producer_spec == body or producer_spec.startswith(f"{body}."):
            base = inst.get(body)
            if base is None:
                return None
            return base + producer_spec[len(body) :]
    return producer_spec


# ── the frontier ─────────────────────────────────────────────────────────────


def frontier(
    root: Node,
    states: dict[str, InstanceState],
    *,
    limits: Limits | None = None,
    declined_edges: set[str] | None = None,
    outputs: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    iterations: dict[str, int] | None = None,
    running_lanes: dict[str, int] | None = None,
    single_active_feature: bool = False,
) -> Frontier:
    """Compute what may run now. Pure: no I/O, no clock, no mutation of the arguments.

    `outputs` is node-id keyed and only used to evaluate `branch` selectors and loop
    conditions — the frontier reads data to make ROUTING decisions, never to execute.

    `single_active_feature` is the run's declared WIP=1 invariant
    (`runtime_hints.execution.single_active_feature`, parsed by `execution_hints`). It caps
    every fan-out in the run to ONE in-flight item, whatever each `foreach` declares for
    itself — a run-level invariant that a per-node knob could quietly contradict is not an
    invariant. Held items land in `Frontier.wip_held` rather than being dropped silently.

    Both admission questions this function asks — "is the lane full?" and "may another item of this
    fan-out start?" — are answered by ONE ordered policy list (`admission.default_policies`),
    composed tightest-wins. Built here and threaded down, never per-node.
    """
    lim = limits or Limits()
    policies = default_policies(lim, single_active_feature=single_active_feature)
    edges = set(declined_edges or ())
    fr = Frontier()
    ctx_base = BindingContext(inputs=dict(inputs or {}), node_outputs=dict(outputs or {}))

    _visit(
        root,
        "root",
        spec="root",
        states=states,
        edges=edges,
        iterations=dict(iterations or {}),
        ctx=ctx_base,
        fr=fr,
        enabled=True,
        order=ordering_for(root),
        inst={},
        policies=policies,
    )

    # Lane admission. Sorting by path keeps admission deterministic when a lane is
    # oversubscribed — two identical runs must launch the same nodes in the same order,
    # or the journal's replay guarantees are worthless.
    fr.ready.sort(key=lambda r: r.path)
    admitted: list[ReadyNode] = []
    deferred: list[ReadyNode] = []
    used = dict(running_lanes or {})
    for item in fr.ready:
        verdict = compose(policies, AdmissionRequest(scope=Scope.LANE, key=item.lane))
        if verdict.admits(used.get(item.lane, 0)):
            used[item.lane] = used.get(item.lane, 0) + 1
            admitted.append(item)
        else:
            # Lane-scope refusal. `deferred` is what lane pressure is called, and the verdict's
            # own `hold` agrees — asserted in the tests rather than trusted here, because a policy
            # whose refusals silently vanished would look like a scheduler that forgot the node.
            deferred.append(item)
    fr.ready = admitted
    fr.deferred = deferred

    root_state = _derive(root, "root", states, edges, dict(iterations or {}), ctx_base)
    if _is_terminal(root_state):
        fr.complete = True
        fr.outcome = root_state
    elif not fr.ready and not fr.deferred and not fr.running and not fr.waiting and not fr.to_skip:
        # Nothing terminal, nothing runnable, nothing in flight: deadlock. Naming it is
        # the whole reason this is computed rather than assumed.
        #
        # `to_skip` counts as progress. The controller applies it before the next tick, and
        # each skip strictly shrinks the non-terminal set, so a run cannot loop on it. Left
        # out, a tick whose only work was retiring an unreachable path would report deadlock
        # and the controller would FAIL a run that was about to proceed — the reachability
        # cascade (`PP-2`) makes exactly such a tick possible.
        fr.blocked = True
        fr.block_reason = "no runnable nodes and none in flight"
    return fr


def _visit(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    enabled: bool,
    order: Ordering,
    inst: dict[str, str],
    item: Any = None,
    has_item: bool = False,
    iter_index: int | None = None,
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    """Walk the tree collecting ready leaves. `enabled` is how a container gates its
    children without mutating their state — a sequence's later children are simply not
    visited as ready until the earlier ones finish.

    `spec` is this node's position in the DEFINITION and `path` its position in this RUN;
    they differ only inside a fan-out (`…body#2`). The ordering graph is keyed by the former
    and states by the latter, which is why both travel together.
    """
    st = _state_of(states, path)
    if st == InstanceState.RUNNING:
        fr.running.append(path)
        return
    if st == InstanceState.WAITING:
        fr.waiting.append(path)
        return
    # A CONTAINER's completeness is derived, never stored — a `branch` writes its own
    # stored state when it routes, so testing `stored` here would return before ever
    # visiting the case it selected.
    effective = _derive(node, path, states, edges, iterations, ctx) if node.is_container else st
    if _is_terminal(effective):
        return
    if not enabled:
        return
    # Derived ordering, applied to the whole subtree: a node waits for what its own config
    # binds (and any `needs` it declares), and its children are not visited until then. Placed
    # before the kind dispatch so one gate covers every shape — a leaf, a `foreach` whose
    # `items` binds an upstream list, a `branch` whose selector does.
    if not _ordering_satisfied(
        node,
        path,
        spec,
        states=states,
        edges=edges,
        iterations=iterations,
        ctx=ctx,
        fr=fr,
        order=order,
        inst=inst,
    ):
        return

    kind = node.kind
    if kind == NodeKind.SEQUENCE:
        for i, child in enumerate(node.children):
            cpath = f"{path}.children[{i}]"
            cst = _state_of(states, cpath)
            _visit(
                child,
                cpath,
                spec=f"{spec}.children[{i}]",
                states=states,
                edges=edges,
                iterations=iterations,
                ctx=ctx,
                fr=fr,
                enabled=True,
                order=order,
                inst=inst,
                item=item,
                has_item=has_item,
                iter_index=iter_index,
                policies=policies,
            )
            # A sequence admits exactly one unfinished child at a time. Stop at the
            # first child that has not reached a terminal state.
            if not _is_terminal(_derive(child, cpath, states, edges, iterations, ctx)):
                break
            if cst == InstanceState.FAILED and _on_error(child) == "fail_run":
                break
        return

    if kind == NodeKind.PARALLEL:
        _visit_parallel(
            node,
            path,
            spec=spec,
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            order=order,
            inst=inst,
            item=item,
            has_item=has_item,
            iter_index=iter_index,
            policies=policies,
        )
        return

    if kind == NodeKind.FOREACH:
        _visit_foreach(
            node,
            path,
            spec=spec,
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            order=order,
            inst=inst,
            policies=policies,
        )
        return

    if kind == NodeKind.LOOP:
        _visit_loop(
            node,
            path,
            spec=spec,
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            order=order,
            inst=inst,
            policies=policies,
        )
        return

    if kind == NodeKind.BRANCH:
        _visit_branch(
            node,
            path,
            spec=spec,
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            order=order,
            inst=inst,
            item=item,
            has_item=has_item,
            iter_index=iter_index,
            policies=policies,
        )
        return

    # A leaf: real work.
    fr.ready.append(
        ReadyNode(
            path=path,
            node=node,
            lane=lane_for(kind),
            item=item,
            has_item=has_item,
            iter_index=iter_index,
        )
    )


def _visit_parallel(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    item: Any,
    has_item: bool,
    iter_index: int | None,
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    """Fan-out. Every leg is visited; `_ordering_satisfied` decides which may run.

    The `needs` gate used to live HERE, over a sibling-only `by_id` map, which is why a
    `needs` naming anything else was refused by the validator and a `needs` declared anywhere
    but a parallel child was silently inert. `PP-2` moved it into `_visit`, where the derived
    graph applies to every node at any depth — so a leg's inner leaf can wait on another leg's
    inner leaf, which is the cross-container diamond the tree shape used to make inexpressible.
    """
    for i, child in enumerate(node.children):
        cpath = f"{path}.children[{i}]"
        # `_derive`, not the stored state: a `branch` stores DONE the moment it routes,
        # and testing the stored value here would skip past the case it selected.
        if _is_terminal(_derive(child, cpath, states, edges, iterations, ctx)):
            continue
        _visit(
            child,
            cpath,
            spec=f"{spec}.children[{i}]",
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            enabled=True,
            order=order,
            inst=inst,
            item=item,
            has_item=has_item,
            iter_index=iter_index,
            policies=policies,
        )


def _ordering_satisfied(
    node: Node,
    path: str,
    spec: str,
    *,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
) -> bool:
    """May this node be visited yet, given the ordering its bindings and `needs` imply?

    Three outcomes, and the difference between the last two is the whole of WF2-R18:

    * **Satisfied** — every producer is TERMINAL (done, degraded, skipped, failed alike).
      "After" is the whole contract; what a failure then MEANS is the reader's `on_error`
      policy, not the scheduler's business.
    * **Not yet** — a producer is still live. Return False and visit nothing; the next tick
      re-derives. This branch must never skip anything: skipping a reader whose producer is
      merely pending would make a downstream join fire early on a live leg, and a join that
      fires early produces a plausible WRONG answer, which is worse than a hang.
    * **Unreachable** — a producer whose OUTPUT this node binds went SKIPPED, or the edge was
      explicitly declined. The output will never exist, so the reader can neither wait (a
      hang) nor run (a certain binding failure). It is skipped, which makes it terminal, which
      lets the join behind it proceed.

    Only a DATAFLOW edge can make a reader unreachable. A plain `needs` onto a skipped node is
    SATISFIED — that is precisely how a join stays off an untaken leg — and treating it as
    unreachable instead would cascade a skip along every ordering edge in the run.
    """
    deps = order.deps.get(spec)
    if not deps:
        return True
    satisfied = True
    for producer_spec, producer_id, carries_data in deps:
        # An explicitly declined edge can never be satisfied by execution, whatever it
        # carries: something considered this path and rejected it.
        if node.id and _edge_declined(edges, producer_id, node.id):
            _mark_unreachable(path, states, fr)
            return False
        ppath = _producer_instance(producer_spec, order, inst)
        if ppath is None:
            continue  # not a single producer instance — see `_producer_instance`
        pnode = order.nodes.get(producer_spec)
        pstate = (
            _derive(pnode, ppath, states, edges, iterations, ctx)
            if pnode is not None
            else _state_of(states, ppath)
        )
        if pstate == InstanceState.SKIPPED and carries_data:
            _mark_unreachable(path, states, fr)
            return False
        if not _is_terminal(pstate):
            satisfied = False
    return satisfied


def _mark_unreachable(path: str, states: dict[str, InstanceState], fr: Frontier) -> None:
    """Queue a node the run can never satisfy for SKIPPED. Idempotent: the controller applies
    `to_skip` once per tick and re-deriving must not queue the same path twice."""
    if not _is_terminal(_state_of(states, path)) and path not in fr.to_skip:
        fr.to_skip.append(path)


def _edge_declined(declined: set[str], src: str, dst: str) -> bool:
    """Was this specific edge considered and NOT taken?

    Declining is recorded EXPLICITLY rather than inferred from "the source routed
    somewhere else". The inference is tempting and wrong: a `branch` routes among its
    *cases*, while a sibling's `needs: [router]` is a plain ordering edge onto the branch
    as a whole. Inferring a decline from any recorded routing would starve that sibling
    forever — the branch chose a case, it did not reject the sibling.

    A declined edge can never be satisfied by execution, so the frontier marks its target
    SKIPPED, which makes it terminal, which is what lets a downstream join proceed
    instead of deadlocking (WF2-R18).
    """
    return edge_key(src, dst) in declined


def _visit_foreach(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    """One body instance per item. Item paths are `<path>.body#<i>` — the `#i` suffix is
    what lets many instances of one body node coexist in a flat state map.

    **On `pipeline` (WF2-R5).** The plan describes it as "no barrier between stages". Measured
    against the real engine, there is no barrier to remove: because each item's body is an
    independent subtree and the frontier is re-derived every tick, item 0 enters stage 2 as soon as
    its own stage 1 finishes, regardless of where the other items are. Streaming handoff is
    already the engine's only behaviour, and a `pipeline: false` that imposed a barrier would be
    NEW machinery whose sole purpose is to make fan-outs slower.

    So `pipeline` is accepted, documented and non-semantic for scheduling — and the knob that
    genuinely governs a fan-out's shape is `max_concurrency`.

    **`max_concurrency`** caps how many ITEMS are in flight at once, independent of the global lane
    caps. Without it a fan-out over fifty files occupies every compute slot in the engine and
    starves the rest of the run; with it a template can say "two at a time" and mean it. Unset is
    unbounded, which is right for the common case of a handful of cheap items.

    An item counts against the cap from its first launched node until its whole body is terminal:
    the point of the cap is usually a scarce resource an item holds for its duration (a checkout, a
    lock, a rate-limited endpoint), and releasing it between stages would defeat that.

    Both `max_concurrency` and the run-level **WIP=1 invariant** (`single_active_feature`,
    LOOPS-EVOLUTION R5b: +37% feature completion) reach this container through the same policy list
    (`admission.ContainerConcurrency` and `admission.Wip`), composed tightest-wins. WIP therefore
    OVERRIDES whatever this node declared — and wins even the TIE at `max_concurrency: 1`, because a
    run-level invariant a per-node knob can contradict is not an invariant, and clamping silently is
    what makes a control look enforced while a `max_concurrency: 3` quietly wins. The contradiction
    is refused at authoring time by the validator (`WF_WIP_CONTRADICTION`); this is the runtime
    half, and the items it refuses to start are named in `fr.wip_held`.
    """
    if node.body is None:
        return
    items = _resolve_items(node, ctx)
    if items is None:
        # The items binding does not resolve yet (an upstream node has not produced it).
        # Not an error — the foreach is simply not ready.
        return
    # The SCHEDULING half of the item-error policy, and the only half that differs here:
    # HALT stops starting items, SKIP and COLLECT both run the whole fan-out. What each
    # policy then makes of the failures is `foreach_outcome`'s decision.
    policy = item_error_policy(node)
    for idx, value in enumerate(items):
        ipath = f"{path}.body#{idx}"
        ist = _state_of(states, ipath)
        if ist == InstanceState.FAILED and policy == ItemErrorPolicy.HALT:
            return

    verdict = compose(policies, AdmissionRequest(scope=Scope.CONTAINER, key=path, node=node))

    # Which items are ALREADY under way. Counted from the state map rather than tracked, so a
    # resumed run re-derives the same answer instead of restarting a fan-out it had half-finished.
    #
    # "Started" is decided by whether the item's subtree has any RECORDED state — NOT by
    # `_derive`: `container_outcome` maps "every child still pending" to RUNNING (a container with
    # unfinished children is running by its definition), so an untouched item derives as RUNNING
    # too and every item would look in-flight. That made the cap admit everything, which is the
    # bug this comment exists to prevent a future reader from reintroducing.
    def _started(ipath: str) -> bool:
        prefix = f"{ipath}."
        return any(p == ipath or p.startswith(prefix) for p in states)

    # Counted only when some policy actually bound: an unbounded fan-out is the common case, and
    # counting walks every item's subtree.
    in_flight = 0
    if verdict.bounded:
        for idx in range(len(items)):
            ipath = f"{path}.body#{idx}"
            if not _started(ipath):
                continue
            if _is_terminal(_derive(node.body, ipath, states, edges, iterations, ctx)):
                continue
            in_flight += 1

    for idx, value in enumerate(items):
        ipath = f"{path}.body#{idx}"
        if _is_terminal(_state_of(states, ipath)):
            continue
        if verdict.bounded and not _started(ipath):
            # An item already under way is visited regardless — it holds its slot either way, and
            # skipping it would stall its remaining stages forever. Only a NEW item needs a slot.
            if not verdict.admits(in_flight):
                # Slot exhausted, and the BINDING policy names the refusal. `max_concurrency` alone
                # records nothing: `deferred` means "ready but the lane is full", and an unstarted
                # item of a capped foreach is not ready — the cap is a property of the container,
                # not of lane pressure.
                #
                # A WIP=1 refusal IS recorded, under its own name: the run declared the invariant,
                # so "the engine refused to start feature 2" is a decision worth being able to read
                # back, and an unrecorded refusal is indistinguishable from a scheduler that simply
                # forgot the item.
                if verdict.hold == Hold.WIP_HELD:
                    fr.wip_held.append(ipath)
                continue
            in_flight += 1
        _visit(
            node.body,
            ipath,
            spec=f"{spec}.body",
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            enabled=True,
            order=order,
            # This item's copy of the body, so a producer inside it resolves to THIS item's
            # instance rather than to another item's or to the undecorated spec path.
            inst={**inst, f"{spec}.body": ipath},
            item=value,
            has_item=True,
            iter_index=idx,
            policies=policies,
        )


def _visit_loop(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    """Sequential iteration: exactly one body instance in flight at a time. Iteration
    paths are `<path>.body@<n>`, so a rewind can invalidate one iteration by prefix."""
    if node.body is None:
        return
    current = int(iterations.get(path, 0))
    ipath = f"{path}.body@{current}"
    ist = _state_of(states, ipath)
    if _is_terminal(ist):
        return  # the controller advances the counter; the next tick sees the new path
    _visit(
        node.body,
        ipath,
        spec=f"{spec}.body",
        states=states,
        edges=edges,
        iterations=iterations,
        ctx=ctx,
        fr=fr,
        enabled=True,
        order=order,
        # The CURRENT iteration. An edge inside the body resolves within this iteration only:
        # iteration 3 reading iteration 2's output would be a cross-iteration dependency the
        # engine has no vocabulary for, and it is the previous iteration's instance path that a
        # rewind invalidates.
        inst={**inst, f"{spec}.body": ipath},
        iter_index=current,
        policies=policies,
    )


def _visit_branch(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    item: Any,
    has_item: bool,
    iter_index: int | None,
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    """Route: dispatch the branch itself, then visit only the taken case.

    The branch node is REAL work, not pure structure — it evaluates a selector, records
    the routing decision, and produces `{"case": label}` as an output downstream nodes can
    bind to. So it runs first and its own state gates its cases.

    An unresolvable selector means an upstream node has not produced its output yet, so
    the branch is simply not ready — distinct from "resolved to a value with no case",
    which the dispatcher reports as a real routing failure.
    """
    own = _state_of(states, path)
    if not _is_terminal(own):
        # The branch has not routed yet. It is itself the ready work.
        fr.ready.append(
            ReadyNode(
                path=path,
                node=node,
                lane=lane_for(node.kind),
                item=item,
                has_item=has_item,
                iter_index=iter_index,
            )
        )
        return
    if not _is_success(own):
        return  # routing failed; there is no case to run

    selected = _select_case(node, ctx)
    if selected is None:
        return
    label, case_node = selected
    cpath = f"{path}.cases[{label}]" if label != "__default__" else f"{path}.default"

    # Untaken cases are marked SKIPPED so they become terminal. That is precisely what
    # keeps a downstream join alive: a `needs` edge is satisfied by any terminal
    # predecessor, and without this the untaken leg stays pending forever (WF2-R18).
    for other_label, other in node.cases.items():
        opath = f"{path}.cases[{other_label}]"
        if opath != cpath and not _is_terminal(_state_of(states, opath)):
            fr.to_skip.append(opath)
    if node.default_case is not None:
        dpath = f"{path}.default"
        if dpath != cpath and not _is_terminal(_state_of(states, dpath)):
            fr.to_skip.append(dpath)

    if _is_terminal(_state_of(states, cpath)):
        return
    _visit(
        case_node,
        cpath,
        spec=f"{spec}.cases[{label}]" if label != "__default__" else f"{spec}.default",
        states=states,
        edges=edges,
        iterations=iterations,
        ctx=ctx,
        fr=fr,
        enabled=True,
        order=order,
        inst=inst,
        item=item,
        has_item=has_item,
        iter_index=iter_index,
        policies=policies,
    )


def _select_case(node: Node, ctx: BindingContext) -> tuple[str, Node] | None:
    """Resolve `config.on` and pick a case. Returns None when the selector cannot be
    resolved yet."""
    expr = str((node.config or {}).get("on", "") or "")
    if not expr:
        return None
    inner = expr.strip()
    if inner.startswith("{{") and inner.endswith("}}"):
        inner = inner[2:-2].strip()
    try:
        value = resolve_expr(inner, ctx)
    except BindingError:
        return None
    key = str(value)
    if key in node.cases:
        return key, node.cases[key]
    if node.default_case is not None:
        return "__default__", node.default_case
    return None


def _resolve_items(node: Node, ctx: BindingContext) -> list[Any] | None:
    raw = (node.config or {}).get("items")
    if isinstance(raw, list):
        return list(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    inner = raw.strip()
    if inner.startswith("{{") and inner.endswith("}}"):
        inner = inner[2:-2].strip()
    try:
        value = resolve_expr(inner, ctx)
    except BindingError:
        return None
    if isinstance(value, list):
        return value
    if value is None:
        return None
    return [value]


def item_error_policy(node: Node) -> ItemErrorPolicy:
    """One `foreach`'s declared item-error policy. Public because the controller needs it to
    decide whether a fan-out owes the ledger a collected-failure record (WV-13)."""
    raw = str((node.config or {}).get("on_item_error", "skip") or "skip")
    try:
        return ItemErrorPolicy(raw)
    except ValueError:
        return ItemErrorPolicy.SKIP


def foreach_outcome(policy: ItemErrorPolicy, item_states: list[InstanceState]) -> InstanceState:
    """One fan-out's verdict, given its policy and its items' states.

    🔴 The reason this is a named function rather than three lines inside `_derive`: `COLLECT`
    shipped as a DECLARED STRATEGY WITH NO EXECUTOR. `models.py` declared it, `validator.py`
    accepted it and the capabilities manifest advertised it to authoring models — while the
    derivation branched on `HALT` and `SKIP` only and let `collect` fall through to
    `container_outcome`. The fallthrough happened to produce roughly the right shape, which is
    exactly why it survived: nothing was visibly broken, and nothing anywhere said what the
    member meant. A fourth member added tomorrow would have inherited `SKIP`'s wait and
    `HALT`'s verdict just as silently. So the choice is made HERE, once, exhaustively over the
    enum, and the unreachable tail RAISES rather than defaulting (WV-13).

    Every member's branch is driven by a test, and the three produce three DIFFERENT run-level
    observables for the same seeded failure — which is the only proof that the members are
    worth having:

    * `HALT` → the run FAILS having skipped the rest of the fan-out.
    * `SKIP` → the run COMPLETES (container DEGRADED) having run all of it.
    * `COLLECT` → the run FAILS having run all of it.

    `COLLECT` returns `container_outcome` rather than a hard-coded FAILED on purpose: an item
    that was CANCELLED or BLOCKED outranks a failure in `_worst`'s severity order, and
    flattening "someone cancelled item 2" into "the fan-out failed" would throw away the more
    informative half of the verdict.
    """
    if policy == ItemErrorPolicy.HALT:
        # No terminal verdict is invented here. `advance_foreach` has already stopped starting
        # items, so the un-started ones are PENDING and this derives RUNNING; the run then
        # terminates through the frontier's deadlock path, which is what makes a halted
        # fan-out a FAILED run rather than a silent hang.
        return container_outcome(item_states)

    # Both remaining policies run EVERY item to a terminal state — neither halts the fan-out
    # early — so they share the wait and differ only in the verdict that follows it.
    if not all(_is_terminal(st) for st in item_states):
        return InstanceState.RUNNING

    if policy == ItemErrorPolicy.SKIP:
        # Tolerated, but never invisible: DEGRADED is a SUCCESS state, so the run completes,
        # and the container still refuses to claim clean success.
        if any(st == InstanceState.FAILED for st in item_states):
            return InstanceState.DEGRADED
        return container_outcome(item_states)

    if policy == ItemErrorPolicy.COLLECT:
        # The failures COUNT. `container_outcome` reports the worst item verdict, so any
        # failure is a FAILED container and `_ROOT_TO_RUN` makes that a FAILED run. Returning
        # DEGRADED here instead would make COLLECT indistinguishable from SKIP at the run
        # level, and the one policy whose entire point is "the failures matter" would report
        # success — the silent-drop shape this program keeps finding.
        return container_outcome(item_states)

    raise AssertionError(
        f"no branch for ItemErrorPolicy.{getattr(policy, 'name', policy)} — a new member must "
        "declare its own behaviour here rather than inherit another policy's"
    )


def _on_error(node: Node) -> str:
    return str((node.config or {}).get("on_error", "null_continue") or "null_continue")


# ── derived container state ──────────────────────────────────────────────────


def derive_state(
    node: Node,
    path: str,
    states: dict[str, InstanceState],
    *,
    declined_edges: set[str] | None = None,
    outputs: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    iterations: dict[str, int] | None = None,
) -> InstanceState:
    """One subtree's effective state, through the same derivation the scheduler uses.

    Public so the controller can ask "has this loop iteration's whole body finished?" without a
    second, divergent notion of completeness — a container-bodied loop advances on that answer,
    and two implementations of it would disagree exactly where it matters.
    """
    return _derive(
        node,
        path,
        states,
        set(declined_edges or ()),
        dict(iterations or {}),
        BindingContext(inputs=dict(inputs or {}), node_outputs=dict(outputs or {})),
    )


def _derive(
    node: Node,
    path: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
) -> InstanceState:
    """A node's effective state. Leaves report their stored state; containers derive
    theirs from children, so no code path can persist a stale container verdict."""
    stored = _state_of(states, path)
    if not node.is_container:
        return stored
    if stored in (InstanceState.SKIPPED, InstanceState.CANCELLED, InstanceState.DISCARDED):
        return stored  # an explicit skip of the whole container wins over derivation

    kind = node.kind
    if kind == NodeKind.SEQUENCE:
        child_states = [
            _derive(c, f"{path}.children[{i}]", states, edges, iterations, ctx)
            for i, c in enumerate(node.children)
        ]
        return container_outcome(tolerate_failures(node.children, child_states))

    if kind == NodeKind.PARALLEL:
        cfg = node.config or {}
        try:
            join = JoinMode(str(cfg.get("join", "all") or "all"))
        except ValueError:
            join = JoinMode.ALL
        quorum = cfg.get("quorum", 0)
        child_states = [
            _derive(c, f"{path}.children[{i}]", states, edges, iterations, ctx)
            for i, c in enumerate(node.children)
        ]
        return container_outcome(
            tolerate_failures(node.children, child_states),
            join=join,
            quorum=quorum if isinstance(quorum, int) else 0,
        )

    if kind == NodeKind.FOREACH:
        if node.body is None:
            return InstanceState.DONE
        items = _resolve_items(node, ctx)
        if items is None:
            return InstanceState.PENDING
        if not items:
            return InstanceState.DONE  # an empty fan-out is vacuously complete
        item_states = [
            _derive(node.body, f"{path}.body#{i}", states, edges, iterations, ctx)
            for i in range(len(items))
        ]
        return foreach_outcome(item_error_policy(node), item_states)

    if kind == NodeKind.LOOP:
        if node.body is None:
            return InstanceState.DONE
        current = int(iterations.get(path, 0))
        ipath = f"{path}.body@{current}"
        ist = _derive(node.body, ipath, states, edges, iterations, ctx)
        if not _is_terminal(ist):
            return InstanceState.RUNNING if ist == InstanceState.RUNNING else stored
        # The body finished this iteration. Whether the loop is done is a controller
        # decision (it owns the counter and the exit test); from the frontier's view the
        # loop is still running until the controller says otherwise.
        return stored if _is_terminal(stored) else InstanceState.RUNNING

    if kind == NodeKind.BRANCH:
        # A branch is done only when it has ROUTED *and* the taken case finished. Its own
        # stored state covers routing; the case covers the work.
        if not _is_terminal(stored):
            return stored
        if not _is_success(stored):
            return stored  # routing itself failed
        selected = _select_case(node, ctx)
        if selected is None:
            return InstanceState.PENDING
        label, case_node = selected
        cpath = f"{path}.cases[{label}]" if label != "__default__" else f"{path}.default"
        return _derive(case_node, cpath, states, edges, iterations, ctx)

    return stored


# ── loop exit evaluation (controller-facing, still pure) ─────────────────────


def loop_should_continue(
    node: Node,
    *,
    iteration: int,
    last_output: Any = None,
    dry_streak: int = 0,
    ctx: BindingContext | None = None,
) -> tuple[bool, str]:
    """Decide whether a loop runs another iteration. Returns `(continue?, reason)`.

    Kept here rather than in the controller because it is a pure function of the node
    and the iteration record, which makes the loop-exit rules unit-testable without an
    engine. `counted` is capped structurally; `until` evaluates a binding; `until_dry`
    exits on a clean streak.
    """
    cfg = node.config or {}
    raw_mode = str(cfg.get("mode", "counted") or "counted")
    try:
        mode = LoopMode(raw_mode)
    except ValueError:
        mode = LoopMode.COUNTED

    hard_cap = cfg.get("max_iterations")
    if isinstance(hard_cap, int) and hard_cap > 0 and iteration >= hard_cap:
        return False, "max_iterations"

    if mode == LoopMode.COUNTED:
        n = cfg.get("n")
        total = n if isinstance(n, int) and n > 0 else 1
        return (iteration < total, "" if iteration < total else "counted_complete")

    if mode == LoopMode.UNTIL:
        expr = str(cfg.get("condition", "") or "")
        if not expr:
            return False, "missing_condition"
        try:
            # ONE dialect (`conditions.evaluate`), shared with the expression gate and
            # `success_when`. A bare `{{ref}}` evaluates exactly as it did before this was
            # centralised; what is new is that `a && b` / `x == 'done'` now mean what they
            # read as, instead of being interpolated into a always-truthy string.
            met = evaluate_condition(expr, ctx or BindingContext())
        except BindingError:
            # An unresolvable exit condition must not spin forever. Stopping is the safe
            # reading: a loop that cannot evaluate its own exit test is broken.
            return False, "condition_unresolvable"
        return (not met, "" if not met else "condition_met")

    if mode == LoopMode.UNTIL_CANCELLED:
        # No self-terminating condition by definition: a watcher stops when something
        # outside it says so. `max_iterations` above still applies, and `reap_watchers`
        # is what turns "the work this watcher accompanied is finished" into a stop.
        return True, ""

    # until_dry
    streak = cfg.get("streak", 1)
    need = streak if isinstance(streak, int) and streak > 0 else 1
    return (dry_streak < need, "" if dry_streak < need else "dry_streak")


def reap_watchers(
    root: Node,
    states: dict[str, InstanceState],
    *,
    iterations: dict[str, int] | None = None,
) -> list[str]:
    """Paths of `until_cancelled` loops whose reason to exist has finished.

    The plan describes a watcher as cancelled by "a sibling completing in a `join: any`
    parallel". Measured, that does not happen on its own: `container_outcome` checks for
    non-terminal children BEFORE the ANY rule, so a parallel whose watcher is still running
    reads RUNNING and the run never completes. That check is correct and deliberate
    (a join must not fire early on a fan-out whose other legs are still working) — so the
    reaping is a separate, narrower rule rather than a change to join semantics.

    The rule: inside a `join: any` (or `quorum`, once met) parallel, an `until_cancelled`
    loop is reaped once ENOUGH of its non-watcher siblings have succeeded. A watcher never
    counts toward its own parallel's join, because a mode with no exit condition can never
    be the leg that satisfies one.

    Returns paths, not a mutation: the controller owns writes, and keeping this pure is what
    makes "would this watcher be reaped?" answerable in a unit test.
    """
    iters = dict(iterations or {})
    reap: list[str] = []
    for path, node in walk(root):
        if node.kind != NodeKind.PARALLEL:
            continue
        cfg = node.config or {}
        try:
            join = JoinMode(str(cfg.get("join", "all") or "all"))
        except ValueError:
            join = JoinMode.ALL
        if join == JoinMode.ALL:
            # Under `join: all` the watcher IS a leg the container waits for; reaping it
            # would silently change the template's declared completion semantics.
            continue

        watchers: list[tuple[str, Node]] = []
        worker_states: list[InstanceState] = []
        for index, child in enumerate(node.children):
            cpath = f"{path}.children[{index}]"
            if _is_until_cancelled(child):
                watchers.append((cpath, child))
                continue
            worker_states.append(_derive_child_state(child, cpath, states, iters))
        if not watchers:
            continue

        successes = [st for st in worker_states if _is_success(st)]
        if join == JoinMode.QUORUM:
            quorum = cfg.get("quorum", 0)
            need = max(1, quorum if isinstance(quorum, int) else 0)
        else:
            need = 1
        if not worker_states or len(successes) < need:
            continue
        for wpath, _w in watchers:
            if not _is_terminal(states.get(wpath, InstanceState.PENDING)):
                reap.append(wpath)
    return reap


def _is_until_cancelled(node: Node) -> bool:
    if node.kind != NodeKind.LOOP:
        return False
    return str((node.config or {}).get("mode", "") or "") == LoopMode.UNTIL_CANCELLED.value


def _derive_child_state(
    node: Node, path: str, states: dict[str, InstanceState], iterations: dict[str, int]
) -> InstanceState:
    """A parallel child's derived state, for the reap decision only.

    Containers hold no state of their own, so a `join: any` parallel whose worker leg is a
    `sequence` would read PENDING from the raw map and the watcher would never be reaped.
    """
    return _derive(node, path, states, set(), iterations, BindingContext())
