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

**Active-edge join gating (WF2-R18).** A join must not wait on a leg that will never run.
A `branch` picking `cases[bug]` leaves `cases[feat]` unreachable; a join that waited on
"all predecessors" would deadlock forever. Conversely a join firing on "any completed
predecessor" fires early on a fan-out whose other legs are still waiting. Both directions
are bugs, so the rule here is: **a `needs` edge is satisfied by any TERMINAL predecessor,
and unreachable paths are made terminal by marking them SKIPPED.** Declining is recorded
explicitly (`declined_edges`) rather than inferred from "the source routed elsewhere" —
inferring it would starve a sibling whose `needs` names a branch, since routing among
cases says nothing about that sibling.

The wait-entry subtlety still matters: a `wait`/`gate` enters WAITING rather than
completing, and WAITING is not terminal, so a join behind it correctly keeps waiting
instead of firing on the fast leg alone.

**Typed lanes (WF2-R21).** Ready work is admitted per-lane, derived from node kind. A
`foreach` over minute-long local-model actions saturates the `io` lane while `llm`
stages keep flowing. Excess stays `ready` rather than being dropped — the next tick
admits it.

The frontier reports `blocked` when nothing can run and nothing is running: that state
is a deadlock, and naming it here rather than letting the run hang forever is the whole
point of computing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.bindings import BindingContext, BindingError, resolve_expr
from personalclaw.workflows.models import (
    LANE_COMPUTE,
    LANE_IO,
    LANE_LLM,
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    ItemErrorPolicy,
    JoinMode,
    LoopMode,
    Node,
    NodeKind,
    lane_for,
)

#: Default per-lane admission caps. `compute` is effectively unmetered — a transform is
#: microseconds of pure data reshaping, and capping it would only add latency.
DEFAULT_LANE_CAPS = {LANE_LLM: 4, LANE_IO: 2, LANE_COMPUTE: 64}


@dataclass(frozen=True)
class Limits:
    """Per-lane concurrency caps. A single total is accepted and split, so a config
    carrying one number keeps working (WF2-R21 back-compat)."""

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
) -> Frontier:
    """Compute what may run now. Pure: no I/O, no clock, no mutation of the arguments.

    `outputs` is node-id keyed and only used to evaluate `branch` selectors and loop
    conditions — the frontier reads data to make ROUTING decisions, never to execute.
    """
    lim = limits or Limits()
    edges = set(declined_edges or ())
    fr = Frontier()
    ctx_base = BindingContext(inputs=dict(inputs or {}), node_outputs=dict(outputs or {}))

    _visit(
        root,
        "root",
        states=states,
        edges=edges,
        iterations=dict(iterations or {}),
        ctx=ctx_base,
        fr=fr,
        enabled=True,
    )

    # Lane admission. Sorting by path keeps admission deterministic when a lane is
    # oversubscribed — two identical runs must launch the same nodes in the same order,
    # or the journal's replay guarantees are worthless.
    fr.ready.sort(key=lambda r: r.path)
    admitted: list[ReadyNode] = []
    deferred: list[ReadyNode] = []
    used = dict(running_lanes or {})
    for item in fr.ready:
        cap = lim.cap(item.lane)
        if used.get(item.lane, 0) < cap:
            used[item.lane] = used.get(item.lane, 0) + 1
            admitted.append(item)
        else:
            deferred.append(item)
    fr.ready = admitted
    fr.deferred = deferred

    root_state = _derive(root, "root", states, edges, dict(iterations or {}), ctx_base)
    if _is_terminal(root_state):
        fr.complete = True
        fr.outcome = root_state
    elif not fr.ready and not fr.deferred and not fr.running and not fr.waiting:
        # Nothing terminal, nothing runnable, nothing in flight: deadlock. Naming it is
        # the whole reason this is computed rather than assumed.
        fr.blocked = True
        fr.block_reason = "no runnable nodes and none in flight"
    return fr


def _visit(
    node: Node,
    path: str,
    *,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    enabled: bool,
    item: Any = None,
    has_item: bool = False,
    iter_index: int | None = None,
) -> None:
    """Walk the tree collecting ready leaves. `enabled` is how a container gates its
    children without mutating their state — a sequence's later children are simply not
    visited as ready until the earlier ones finish."""
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

    kind = node.kind
    if kind == NodeKind.SEQUENCE:
        for i, child in enumerate(node.children):
            cpath = f"{path}.children[{i}]"
            cst = _state_of(states, cpath)
            _visit(
                child,
                cpath,
                states=states,
                edges=edges,
                iterations=iterations,
                ctx=ctx,
                fr=fr,
                enabled=True,
                item=item,
                has_item=has_item,
                iter_index=iter_index,
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
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            item=item,
            has_item=has_item,
            iter_index=iter_index,
        )
        return

    if kind == NodeKind.FOREACH:
        _visit_foreach(
            node, path, states=states, edges=edges, iterations=iterations, ctx=ctx, fr=fr
        )
        return

    if kind == NodeKind.LOOP:
        _visit_loop(node, path, states=states, edges=edges, iterations=iterations, ctx=ctx, fr=fr)
        return

    if kind == NodeKind.BRANCH:
        _visit_branch(
            node,
            path,
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            item=item,
            has_item=has_item,
            iter_index=iter_index,
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
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    item: Any,
    has_item: bool,
    iter_index: int | None,
) -> None:
    """Fan-out with intra-block `needs` edges, honouring declined edges (WF2-R18)."""
    by_id: dict[str, tuple[str, Node]] = {}
    for i, child in enumerate(node.children):
        if child.id:
            by_id[child.id] = (f"{path}.children[{i}]", child)

    for i, child in enumerate(node.children):
        cpath = f"{path}.children[{i}]"
        # `_derive`, not the stored state: a `branch` stores DONE the moment it routes,
        # and testing the stored value here would skip past the case it selected.
        if _is_terminal(_derive(child, cpath, states, edges, iterations, ctx)):
            continue
        # Gate on `needs`: a predecessor satisfies a need once it is TERMINAL — done,
        # degraded, skipped or failed alike. "After" is the whole contract; what a failure
        # then means is the child's `on_error` policy, not the scheduler's business.
        #
        # An explicitly DECLINED edge is different: it will never be satisfied by
        # execution, so this child is unreachable and gets skipped rather than waited on.
        ready = True
        for need in child.needs:
            if child.id and _edge_declined(edges, need, child.id):
                if not _is_terminal(_state_of(states, cpath)):
                    fr.to_skip.append(cpath)
                ready = False
                break
            entry = by_id.get(need)
            if entry is None:
                # An unresolvable need is a validation error, not a runtime deadlock;
                # treat it as satisfied so the run surfaces the real problem downstream.
                continue
            npath, nnode = entry
            if not _is_terminal(_derive(nnode, npath, states, edges, iterations, ctx)):
                ready = False
                break
        if ready:
            _visit(
                child,
                cpath,
                states=states,
                edges=edges,
                iterations=iterations,
                ctx=ctx,
                fr=fr,
                enabled=True,
                item=item,
                has_item=has_item,
                iter_index=iter_index,
            )


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
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
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
    """
    if node.body is None:
        return
    items = _resolve_items(node, ctx)
    if items is None:
        # The items binding does not resolve yet (an upstream node has not produced it).
        # Not an error — the foreach is simply not ready.
        return
    policy = _item_error_policy(node)
    for idx, value in enumerate(items):
        ipath = f"{path}.body#{idx}"
        ist = _state_of(states, ipath)
        if ist == InstanceState.FAILED and policy == ItemErrorPolicy.HALT:
            return

    cap = _max_concurrency(node)

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

    in_flight = 0
    if cap:
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
        if cap and not _started(ipath):
            # An item already under way is visited regardless — it holds its slot either way, and
            # skipping it would stall its remaining stages forever. Only a NEW item needs a slot.
            if in_flight >= cap:
                # Slot exhausted. NOT recorded as deferred: `deferred` means "ready but the lane is
                # full", and an unstarted item of a capped foreach is not ready — the cap is a
                # property of the container, not of lane pressure.
                continue
            in_flight += 1
        _visit(
            node.body,
            ipath,
            states=states,
            edges=edges,
            iterations=iterations,
            ctx=ctx,
            fr=fr,
            enabled=True,
            item=value,
            has_item=True,
            iter_index=idx,
        )


def _visit_loop(
    node: Node,
    path: str,
    *,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
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
        states=states,
        edges=edges,
        iterations=iterations,
        ctx=ctx,
        fr=fr,
        enabled=True,
        iter_index=current,
    )


def _visit_branch(
    node: Node,
    path: str,
    *,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    item: Any,
    has_item: bool,
    iter_index: int | None,
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
        states=states,
        edges=edges,
        iterations=iterations,
        ctx=ctx,
        fr=fr,
        enabled=True,
        item=item,
        has_item=has_item,
        iter_index=iter_index,
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


def _max_concurrency(node: Node) -> int:
    """A `foreach`'s per-container item cap, or 0 for unbounded.

    Separate from the lane caps because they answer different questions: a lane cap protects the
    ENGINE (four concurrent model calls across all runs), and this protects the RUN's shape (this
    fan-out takes two at a time because each item holds a lock). A fan-out over fifty files with
    no cap occupies every compute slot and starves everything else in the run.
    """
    raw = (node.config or {}).get("max_concurrency")
    # A true int only. `int(1.5)` truncates to 1 and `int(True)` is 1, so a coercing read would let
    # a spec typo silently serialize a fan-out to one item at a time — the most expensive possible
    # misreading, and invisible because the run still succeeds.
    if not isinstance(raw, int) or isinstance(raw, bool):
        return 0
    return raw if raw > 0 else 0


def _item_error_policy(node: Node) -> ItemErrorPolicy:
    raw = str((node.config or {}).get("on_item_error", "skip") or "skip")
    try:
        return ItemErrorPolicy(raw)
    except ValueError:
        return ItemErrorPolicy.SKIP


def _on_error(node: Node) -> str:
    return str((node.config or {}).get("on_error", "null_continue") or "null_continue")


# ── derived container state ──────────────────────────────────────────────────


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
        return container_outcome(child_states)

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
            child_states, join=join, quorum=quorum if isinstance(quorum, int) else 0
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
        policy = _item_error_policy(node)
        if policy == ItemErrorPolicy.SKIP:
            # One bad item must not sink the fan-out: failures are tolerated as long as
            # every item reached a terminal state.
            if all(_is_terminal(st) for st in item_states):
                return (
                    InstanceState.DEGRADED
                    if any(st == InstanceState.FAILED for st in item_states)
                    else container_outcome(item_states)
                )
            return InstanceState.RUNNING
        return container_outcome(item_states)

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
        inner = expr.strip()
        if inner.startswith("{{") and inner.endswith("}}"):
            inner = inner[2:-2].strip()
        try:
            value = resolve_expr(inner, ctx or BindingContext())
        except BindingError:
            # An unresolvable exit condition must not spin forever. Stopping is the safe
            # reading: a loop that cannot evaluate its own exit test is broken.
            return False, "condition_unresolvable"
        return (not _truthy(value), "" if not _truthy(value) else "condition_met")

    # until_dry
    streak = cfg.get("streak", 1)
    need = streak if isinstance(streak, int) and streak > 0 else 1
    return (dry_streak < need, "" if dry_streak < need else "dry_streak")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "null", "none")
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(value)
