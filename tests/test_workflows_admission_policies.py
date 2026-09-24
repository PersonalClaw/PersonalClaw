"""`Lease`, `Dwell` and `MetricGate` — the capabilities `PP-12` adds to the admission seam.

Three claims are worth more than the rest, so they are asserted three different ways:

* **Additive.** A spec declaring none of the new keys must decide exactly what it decided before.
  Proven at three levels: `PP-11`'s golden frontier file re-runs byte-identically (unchanged, in
  `test_workflows_frontier_golden.py`), `compose()` returns identical lane/container verdicts under
  the three-policy and the six-policy list here, and a real run whose spec declares nothing never
  even builds an `AdmissionState` — asserted by making that construction throw.
* **Not a second lease.** `S57` measured an `unlink`-based single-use claim failing 36 of 40 races,
  so the atom forbids a second implementation. The decision here IS `pool.acquire` (railed by an AST
  scan, because "we reuse it" is a claim a passing test cannot settle) and the write IS
  `pool.claim_task`'s flocked compare-and-swap — driven from 16 threads contending for one resource,
  which is the shape that actually distinguishes a CAS from a check-then-act.
* **A lease survives a restart.** The record lives on disk, so the proof rebuilds the controller
  from persisted state — a fresh object with an empty memory — and asserts the holder is unchanged
  and the other items are still refused.

Purity note: every unit test below hands the policies a `now`. That is the whole design — a policy
that read a clock would be untestable at a boundary and unreplayable in a run.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from personalclaw.loop.tick import Action
from personalclaw.workflows import pool, store
from personalclaw.workflows.admission import (
    RANK_CAPACITY,
    RANK_EXCLUSION,
    RANK_INVARIANT,
    RANK_REGRESSION,
    AdmissionPolicy,
    AdmissionRequest,
    AdmissionState,
    ContainerConcurrency,
    Dwell,
    Hold,
    Lane,
    Lease,
    Limits,
    MetricGate,
    Scope,
    Wip,
    compose,
    default_policies,
)
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, Node, RunStatus, WorkflowRun

NOW = 1_700_000_000.0


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _node(**config) -> Node:
    return Node.from_dict({"id": "step", "kind": "transform", "config": config})


def _step_request(**config) -> AdmissionRequest:
    return AdmissionRequest(scope=Scope.STEP, key="root.children[1]", node=_node(**config))


def _lease_record(holder: str = "run-a:item#0", *, ttl: int = 600, at: float = NOW) -> pool.Lease:
    return pool.Lease(task_id="endpoint", holder=holder, acquired_at=at, ttl_seconds=ttl)


# ── additivity ───────────────────────────────────────────────────────────────


def test_default_policies_with_no_state_is_exactly_the_three_pp11_policies():
    """The structural half of additivity: the frontier's list is not merely equivalent, it is the
    same objects it was before this atom."""
    policies = default_policies(Limits(), single_active_feature=True)
    assert [type(p) for p in policies] == [Lane, ContainerConcurrency, Wip]
    widened = default_policies(Limits(), single_active_feature=True, state=AdmissionState())
    assert [type(p) for p in widened] == [Lane, ContainerConcurrency, Wip, Lease, Dwell, MetricGate]


@pytest.mark.parametrize("wip", [False, True])
@pytest.mark.parametrize("max_concurrency", [None, 1, 3])
@pytest.mark.parametrize("lane", ["llm", "io", "compute", "unknown"])
def test_the_widened_list_decides_lane_and_container_verdicts_identically(
    wip, max_concurrency, lane
):
    """The behavioural half: every scope the frontier asks about composes to the same verdict —
    same capacity, same binding, same hold — with the three new policies in the list.

    A new policy that answered a scope it should abstain on would be invisible in a unit test of the
    policy itself and catastrophic in a run, so the comparison is over the composed verdict.
    """
    config = {} if max_concurrency is None else {"max_concurrency": max_concurrency}
    container = Node.from_dict({"id": "fan", "kind": "foreach", "config": config})
    limits = Limits(lanes={"llm": 2, "io": 1, "compute": 8})
    narrow = default_policies(limits, single_active_feature=wip)
    widened = default_policies(
        limits,
        single_active_feature=wip,
        # A fully loaded state, so a policy that leaked into the wrong scope would have something
        # to say rather than abstaining for lack of inputs.
        state=AdmissionState(
            now=NOW,
            holder="run-a:item",
            leases={"endpoint": _lease_record(holder="someone-else")},
            since={"root.fan": NOW},
            metrics={"root.fan": 0.1},
            floors={"root.fan": 0.9},
        ),
    )
    for request in (
        AdmissionRequest(scope=Scope.LANE, key=lane),
        AdmissionRequest(scope=Scope.CONTAINER, key="root.fan", node=container),
    ):
        before, after = compose(narrow, request), compose(widened, request)
        assert before.capacity == after.capacity
        assert before.hold == after.hold
        assert getattr(before.binding, "name", "") == getattr(after.binding, "name", "")


def test_the_new_policies_abstain_on_the_frontiers_scopes():
    """The same claim at the policy level, where the reason is legible: a policy outside its scope
    abstains, which is how six policies coexist in one list."""
    state = AdmissionState(now=NOW, holder="run-a:item", leases={"endpoint": _lease_record()})
    node = Node.from_dict({"id": "fan", "kind": "foreach", "config": {"max_concurrency": 2}})
    for policy in (Lease(state=state), Dwell(state=state), MetricGate(state=state)):
        assert policy.capacity(AdmissionRequest(scope=Scope.LANE, key="llm")) is None
        assert (
            policy.capacity(AdmissionRequest(scope=Scope.CONTAINER, key="root.fan", node=node))
            is None
        )


# ── the lease is the pool's lease ────────────────────────────────────────────


def test_the_lease_policy_decides_with_the_pools_own_acquire():
    """A RAIL, not a docstring promise: `Lease.capacity` must CALL `pool.acquire`.

    The atom forbids a second lease implementation because `S57` measured the naive one losing 36 of
    40 races. A re-implementation would pass every behavioural test in this file — the semantics are
    easy to copy and the race is not — so the reuse itself is what has to be asserted.
    """
    tree = ast.parse(inspect.getsource(Lease))
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "pool.acquire" in calls, (
        "Lease must decide with pool.acquire — the compare-and-swap decision the task pool's "
        f"flocked claim path uses. Calls found: {sorted(calls)}"
    )
    # And it must not have grown its own expiry arithmetic alongside it: two answers to "is this
    # lease still live" is the drift this atom exists to prevent.
    source = Path(inspect.getsourcefile(Lease) or "").read_text(encoding="utf-8")
    assert "acquired_at" not in source
    assert "def expired" not in source


@pytest.mark.parametrize(
    "record,expected",
    [
        (None, 1),
        (_lease_record(holder="run-a:item#0"), 1),
        (_lease_record(holder="run-b:item#0"), 0),
        (_lease_record(holder="run-b:item#0", ttl=1, at=NOW - 600), 1),
    ],
    ids=["free", "ours-renews", "held-by-another", "expired-is-takeable"],
)
def test_lease_capacity_follows_the_pools_acquire_semantics(record, expected):
    state = AdmissionState(
        now=NOW,
        holder="run-a:item#0",
        leases={} if record is None else {"endpoint": record},
    )
    request = AdmissionRequest(scope=Scope.RESOURCE, key="endpoint")
    assert Lease(state=state).capacity(request) == expected


def test_a_lease_without_a_holder_abstains_rather_than_stranding_the_resource():
    """`pool.read_lease`'s precedent: a claim nobody can ever take is worse than one two callers
    briefly contend for, because contention resolves and a strand does not. Safe because the flocked
    claim — not this verdict — is what grants the resource."""
    state = AdmissionState(now=NOW, holder="   ", leases={"endpoint": _lease_record()})
    assert (
        Lease(state=state).capacity(AdmissionRequest(scope=Scope.RESOURCE, key="endpoint")) is None
    )


def test_sixteen_concurrent_claims_on_one_resource_produce_exactly_one_holder():
    """The race the atom is written around, driven rather than reasoned about.

    Every worker composes the same admission verdict — all of them read the resource as FREE, which
    is exactly the stale read a check-then-act would act on — and then claims. `pool.claim_task`'s
    `single_flight` flock is what makes only one of them win. Sixteen workers rather than two
    because `S57`'s failure rate (36 of 40) was measured under contention, not at N=2.
    """
    request = AdmissionRequest(scope=Scope.RESOURCE, key="endpoint")
    state = AdmissionState(now=NOW, leases={})

    def claim(index: int) -> str:
        holder = f"worker-{index}"
        verdict = Lease(state=AdmissionState(now=NOW, holder=holder, leases={})).capacity(request)
        assert verdict == 1, "every worker must see the resource as free — that is the stale read"
        lease, _error = pool.claim_task("endpoint", holder=holder, now=state.now, ttl_seconds=600)
        return holder if lease is not None else ""

    with ThreadPoolExecutor(max_workers=16) as pool_exec:
        winners = [w for w in pool_exec.map(claim, range(16)) if w]

    assert len(winners) == 1, f"{len(winners)} holders won one lease: {winners}"
    assert (pool.read_lease("endpoint") or _lease_record()).holder == winners[0]


def test_the_flock_under_the_claim_excludes_THREADS_not_only_PROCESSES():
    """The load-bearing assumption of the test above, asserted instead of assumed.

    `single_flight` is documented as a "cross-process" guard, and reading that as *only*
    cross-process is what once turned the red above into a supposedly unfixable in-process hole. It
    is wrong: flock is scoped to the open file description and `single_flight` opens a fresh one per
    call, so threads contend as processes do. This matters because the engine fans out in-process
    (`asyncio.create_task`), so an exclusion that skipped threads would not cap the one shape that
    occurs. Measures the PEAK — a serial count could not tell overlap from fast succession.
    """
    from personalclaw.concurrency import single_flight

    guard = threading.Lock()
    inside = 0
    peak = 0

    def hold(_index: int) -> None:
        nonlocal inside, peak
        with single_flight("pp12:thread-exclusion") as acquired:
            if not acquired:
                return
            with guard:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.02)  # hold it, so any overlap is observable rather than theoretical
            with guard:
                inside -= 1

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(hold, range(16)))

    assert peak == 1, f"{peak} threads were inside one single_flight critical section at once"


# ── dwell ────────────────────────────────────────────────────────────────────


def test_dwell_holds_until_the_bake_floor_elapses_then_abstains():
    state = AdmissionState(now=NOW, since={"root.children[1]": NOW - 10})
    request = _step_request(min_dwell_secs=30)
    assert Dwell(state=state).capacity(request) == 0
    later = AdmissionState(now=NOW + 25, since={"root.children[1]": NOW - 10})
    assert Dwell(state=later).capacity(request) is None


def test_dwell_reads_the_loops_own_parser():
    """`min_dwell_secs: "soon"` must degrade to no-dwell, not to a stalled step. That leniency is
    `step_config_from_phase`'s, and reusing it is why this policy has no parsing of its own."""
    state = AdmissionState(now=NOW, since={"root.children[1]": NOW - 1})
    assert Dwell(state=state).capacity(_step_request(min_dwell_secs="soon")) is None
    assert Dwell(state=state).capacity(_step_request(min_dwell_secs=-5)) is None
    assert Dwell(state=state).capacity(_step_request(min_dwell_secs="900")) == 0


def test_dwell_abstains_with_nothing_to_measure_from():
    """The first step of a run has no prior completion. Refusing would hold a run that has not
    started anything yet — a bake floor with no cake."""
    assert Dwell(state=AdmissionState(now=NOW)).capacity(_step_request(min_dwell_secs=30)) is None


# ── the metric gate ──────────────────────────────────────────────────────────


def test_a_passing_metric_lets_the_step_through():
    state = AdmissionState(
        now=NOW, metrics={"root.children[1]": 0.91}, floors={"root.children[1]": 0.5}
    )
    request = _step_request(metric_pass=0.8, metric_hold=0.6)
    gate = MetricGate(state=state)
    assert gate.capacity(request) is None
    decision = gate.decision(request)
    assert decision is not None and decision.action is Action.ADVANCE


def test_a_marginal_metric_holds_the_step():
    state = AdmissionState(now=NOW, metrics={"root.children[1]": 0.7})
    request = _step_request(metric_pass=0.8, metric_hold=0.6)
    gate = MetricGate(state=state)
    assert gate.capacity(request) == 0
    decision = gate.decision(request)
    assert decision is not None and decision.action is Action.HOLD


def test_a_regressed_metric_asks_for_a_rollback():
    state = AdmissionState(
        now=NOW, metrics={"root.children[1]": 0.4}, floors={"root.children[1]": 0.5}
    )
    request = _step_request(metric_pass=0.8, metric_hold=0.6)
    gate = MetricGate(state=state)
    assert gate.capacity(request) == 0
    decision = gate.decision(request)
    assert decision is not None and decision.action is Action.ROLLBACK
    assert "regressed" in decision.reason


def test_the_rollback_cap_turns_a_regression_into_a_refusal_to_keep_trying():
    state = AdmissionState(
        now=NOW,
        metrics={"root.children[1]": 0.4},
        floors={"root.children[1]": 0.5},
        rollbacks={"root.children[1]": 3},
        rollback_cap=3,
    )
    decision = MetricGate(state=state).decision(_step_request(metric_pass=0.8, metric_hold=0.6))
    assert decision is not None and decision.action is Action.COMPLETE
    assert "rollback cap" in decision.reason


def test_the_metric_gate_abstains_without_a_declaration_or_an_observation():
    """Two abstentions with one reason: a gate that judged an unmeasured step would deadlock the run
    that was about to measure it."""
    state = AdmissionState(now=NOW, metrics={"root.children[1]": 0.1})
    assert MetricGate(state=state).capacity(_step_request(expr="1")) is None
    unobserved = AdmissionState(now=NOW)
    assert MetricGate(state=unobserved).capacity(_step_request(metric_pass=0.8)) is None


def test_the_metric_gate_does_not_enforce_the_bake_floor_as_well():
    """One threshold, one policy. If `MetricGate` also honoured `min_dwell_secs`, a bake hold would
    be reported as a regression — and a step held by a policy that is not the one holding it is the
    exact confusion `Hold` exists to prevent."""
    state = AdmissionState(now=NOW, metrics={"root.children[1]": 0.95})
    request = _step_request(metric_pass=0.8, min_dwell_secs=3600)
    decision = MetricGate(state=state).decision(request)
    assert decision is not None and decision.action is Action.ADVANCE


# ── ranks and ties ───────────────────────────────────────────────────────────


def test_the_rank_order_is_stated_once_and_ordered_deliberately():
    assert RANK_CAPACITY < RANK_INVARIANT < RANK_EXCLUSION < RANK_REGRESSION


def test_a_step_tie_names_the_regression_not_the_bake_floor():
    """A real tie between two shipped policies: a baking step whose metric also regressed. Both bind
    at 0, and only one of them explains the rollback that is about to happen."""
    state = AdmissionState(
        now=NOW,
        since={"root.children[1]": NOW - 1},
        metrics={"root.children[1]": 0.4},
        floors={"root.children[1]": 0.5},
    )
    request = _step_request(min_dwell_secs=600, metric_pass=0.8, metric_hold=0.6)
    policies = default_policies(Limits(), single_active_feature=False, state=state)
    verdict = compose(policies, request)
    assert verdict.capacity == 0
    assert isinstance(verdict.binding, MetricGate)
    assert verdict.hold == Hold.REGRESSED
    # Both really did bind — otherwise this asserts a tie that never happened.
    assert Dwell(state=state).capacity(request) == 0


def test_a_lease_outranks_a_same_scope_invariant_on_a_tie():
    """The lease's rank, decided deliberately: a lease is mutual exclusion, not throughput, and a
    lease hold reported as an invariant sends the reader to the run's own knobs for a resource
    another process is holding.

    Asserted through `compose` with a stub invariant so the tie is at the SAME capacity in the SAME
    scope — no shipped policy answers `RESOURCE` yet, and a tie nobody can construct is a rank
    nobody can trust.
    """

    class _Invariant(AdmissionPolicy):
        name = "stub_invariant"
        hold = Hold.WIP_HELD
        rank = RANK_INVARIANT

        def capacity(self, request: AdmissionRequest) -> int | None:
            return 1 if request.scope == Scope.RESOURCE else None

    lease = Lease(state=AdmissionState(now=NOW, holder="run-a:item#0"))
    request = AdmissionRequest(scope=Scope.RESOURCE, key="endpoint")
    assert compose((_Invariant(), lease), request).binding is lease
    # …and it is the RANK, not the position, that decided it.
    assert isinstance(compose((lease, _Invariant()), request).binding, Lease)


def test_the_container_tie_still_names_wip_under_the_widened_list():
    """`PP-11`'s most refactor-fragile decision, re-asserted with six policies in the list:
    `max_concurrency: 1` under a run-level WIP=1 must still be reported as `wip_held`."""
    node = Node.from_dict({"id": "fan", "kind": "foreach", "config": {"max_concurrency": 1}})
    request = AdmissionRequest(scope=Scope.CONTAINER, key="root.fan", node=node)
    state = AdmissionState(now=NOW, holder="run-a:item", leases={"endpoint": _lease_record()})
    verdict = compose(default_policies(Limits(), single_active_feature=True, state=state), request)
    assert verdict.capacity == 1
    assert verdict.hold == Hold.WIP_HELD


# ── the runtime: a leased fan-out, across a restart ──────────────────────────


def _leased_fanout_spec() -> dict:
    """A three-item fan-out whose items each hold one endpoint for their whole body.

    The body's first node is a long `wait`, so an item that HAS the lease stays non-terminal for the
    length of the test. Without that, transforms complete inside the same tick and serialization
    would be unobservable — the run would look serialized because it was instantaneous.
    """
    return {
        "name": "leased-fanout",
        "root": {
            "id": "root",
            "kind": "sequence",
            "children": [
                {
                    "id": "fan",
                    "kind": "foreach",
                    "config": {
                        "items": ["alpha", "beta", "gamma"],
                        "lease": "endpoint",
                        "lease_ttl_secs": 600,
                    },
                    "body": {
                        "id": "body",
                        "kind": "sequence",
                        "children": [
                            {"id": "hold", "kind": "wait", "config": {"seconds": 3600}},
                            {"id": "after", "kind": "transform", "config": {"expr": "1"}},
                        ],
                    },
                }
            ],
        },
    }


def _controller(run: WorkflowRun, spec: dict) -> RunController:
    return RunController(run, spec, services=EngineServices())


def _decisions(run_id: str, decision: str) -> list[dict]:
    """The run's journalled DECISION records of one kind.

    Read from `journal.jsonl` rather than the `events.jsonl` mirror: a scheduling decision is not
    mirrored, and asserting against the mirror would silently assert nothing.
    """
    path = store.run_dir(run_id) / "journal.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in records if r.get("decision") == decision]


@pytest.mark.anyio
async def test_a_leased_fanout_serializes_and_the_claim_survives_a_restart():
    """The atom's runtime bar, in one trajectory.

    1. Three items are ready in one tick; exactly one starts, because the lease admits one.
    2. The refusal is in the ledger by name, so "why is item 2 not running" is answerable.
    3. A NEW controller built from persisted state — the restart — does not steal the lease: the
       record is on disk, the holder is unchanged, and the other items are still refused.
    4. When the holding item settles, the lease is released and the next item takes it.
    """
    spec = _leased_fanout_spec()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
    store.write_spec(run.id, spec)

    live = _controller(run, spec)
    assert await live._prepare()
    await live._step()

    started = [p for p, i in live.instances.items() if i.state != InstanceState.PENDING]
    holders = [p for p in started if p.endswith("children[0]")]
    assert len(holders) == 1, f"a leased fan-out started {len(holders)} items at once: {holders}"
    first_item = holders[0].rsplit(".", 1)[0]

    record = pool.read_lease("endpoint")
    assert record is not None and record.holder == f"{run.id}:{first_item}"

    held = _decisions(run.id, "admission_leased")
    assert len(held) == 2, f"expected both refused items in the ledger, got {held}"

    # The restart: a fresh controller with no memory of the claim, re-reading persisted state.
    restarted = _controller(run, spec)
    assert restarted._held_leases == {}
    await restarted._step()
    after = pool.read_lease("endpoint")
    assert (
        after is not None and after.holder == record.holder
    ), "a restarted gateway stole a live lease from the item that holds it"
    # A refused item has no instance row at all — it was never launched — so "exactly one item
    # started" is the observable, and a second row appearing would mean the restart admitted one.
    assert [
        p for p in restarted.instances if p.endswith("children[0]")
    ] == holders, "a restart admitted a second item into a resource that holds one"
    assert restarted._held_leases == {
        "endpoint": record.holder
    }, "a restarted controller must re-adopt its own claim, or only the TTL could release it"

    # The handoff. Each pass settles whatever of item 0 is currently launched: the item holds its
    # resource across its WHOLE body, so the lease is RENEWED rather than handed on until the last
    # node of the body is terminal. That renewal is as much the behaviour under test as the
    # handoff — a lease released between an item's stages would serialize nothing.
    handed = None
    for _ in range(4):
        for path, inst in restarted.instances.items():
            if path.startswith(first_item):
                inst.state = InstanceState.DONE
        await restarted._step()
        if restarted._inflight:
            # The launched node's completion is folded in by the tick loop's progress wait, and
            # `_scope_settled` treats an in-flight node as not settled — driving `_step` alone would
            # leave the item permanently "still working" and the lease permanently held.
            await restarted._await_progress()
        handed = pool.read_lease("endpoint")
        if handed is not None and handed.holder != record.holder:
            break
    assert (
        handed is not None and handed.holder != record.holder
    ), "the lease was never handed on: a settled item must release it"
    assert handed.holder.startswith(
        f"{run.id}:root.children[0].body#"
    ), f"the lease went to something other than another item of the fan-out: {handed.holder}"
    await restarted._finish(RunStatus.CANCELLED)
    assert pool.read_lease("endpoint") is None, "a terminal run must not strand its resources"


@pytest.mark.anyio
async def test_a_metric_regression_rolls_the_prior_step_back_inside_a_run():
    """The other runtime bar: a workflow step whose metric regressed re-runs its predecessor.

    `verify` reports 0.4 against a declared floor of 0.5, so the gate on `gated` rolls `verify` back
    through the REAL mutation queue (`rewind`) rather than a bespoke reset. Each rollback bumps
    `verify`'s epoch, which is also the persisted rollback count, so the run terminates at the cap
    instead of rolling back forever — and it says so.
    """
    spec = {
        "name": "metric-rollback",
        "root": {
            "id": "root",
            "kind": "sequence",
            "children": [
                {"id": "verify", "kind": "transform", "config": {"expr": {"score": 0.4}}},
                {
                    "id": "gated",
                    "kind": "transform",
                    "config": {
                        "expr": {"ok": True},
                        "metric_from": "verify.score",
                        "metric_pass": 0.8,
                        "metric_hold": 0.6,
                        "metric_floor": 0.5,
                    },
                },
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
    store.write_spec(run.id, spec)
    controller = _controller(run, spec)
    status = await controller.run_to_completion(timeout=30)

    assert status == RunStatus.FAILED
    assert "metric gate" in run.error_message and "rollback cap" in run.error_message
    assert (
        controller._instance("root.children[0]").epoch >= 3
    ), "the prior step was never rolled back: a regression that only HOLDS is a stall, not a gate"
    rollbacks = _decisions(run.id, "metric_rollback")
    assert rollbacks, "the rollback is a plan change and must be readable back from the ledger"
    assert controller._instance("root.children[1]").state == InstanceState.PENDING


@pytest.mark.anyio
async def test_a_spec_declaring_no_admission_keys_never_builds_an_admission_state(monkeypatch):
    """Additivity at the run level, and the reason it is worth asserting separately: the cheapest
    way to break "a spec declaring neither behaves exactly as before" is to make every run pay for
    a state it has no use for. So the construction is made to THROW, and a plain run still passes.
    """

    def _boom(self, ready):  # pragma: no cover - the point is that it is never called
        raise AssertionError("a spec declaring no PP-12 keys must not gather admission state")

    monkeypatch.setattr(RunController, "_admission_state", _boom)
    spec = {
        "name": "plain",
        "root": {
            "id": "root",
            "kind": "sequence",
            "children": [
                {"id": "one", "kind": "transform", "config": {"expr": "1"}},
                {"id": "two", "kind": "transform", "config": {"expr": "2"}},
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
    store.write_spec(run.id, spec)
    controller = _controller(run, spec)
    assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
