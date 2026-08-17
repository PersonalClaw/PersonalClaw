"""PP-15 — the widened convergence core: purity, restartability, and a REAL replan.

Three claims are load-bearing and each is easy to assert falsely:

1. **Purity is PRESERVED and RE-PROVEN.** `evaluate` gained the escalation ladder, and the
   ladder it absorbed was stateful — `check_middleware` advanced a cursor on its own
   argument, so asking it twice gave two answers. A test that only checks the new enum
   members exist would not notice that regression. So the same `(cfg, state, now)` must
   yield the same `Decision` on every branch, and no clock may leak in.
2. **Restartability.** Every input is derived from persisted state, so a fresh process
   re-derives the same `Decision` — including WHICH RUNG. Proven by round-tripping the
   snapshot through JSON bytes, which is the only form a restart actually gets.
3. **`REPLAN` queues a REAL mutation batch.** Asserting that the enum member came back
   would satisfy nothing: the behaviour being replaced (retry with the critique stapled to
   the prompt) also "returns a decision". So the test drives a real run and asserts the SPEC
   changed and the re-derived step EXECUTED.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from personalclaw.loop import tick
from personalclaw.loop.tick import (
    Action,
    StepConfig,
    TickConfig,
    TickState,
    evaluate,
)
from personalclaw.workflows import journal as J
from personalclaw.workflows import store, supervisor_policy
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.loop_middleware import DEFAULT_LADDER, FailureClass, Rung
from personalclaw.workflows.models import WorkflowRun

NOW = 1_000.0


def _state(**kw) -> TickState:
    """A neutral snapshot. `step_index`/`step_started_at` default but stay overridable — a
    helper that pinned them would make half the branch corpus unreachable."""
    kw.setdefault("step_index", 0)
    kw.setdefault("step_started_at", 0.0)
    return TickState(**kw)


def _fail(state: TickState, *, n: int = 1, **kw) -> TickState:
    for _ in range(n):
        state = tick.record_failure(state, **kw)
    return state


# ── the branch corpus: one case per reachable branch, including the new members ──
#
# Named cases rather than an ad-hoc state so a future branch that is NOT covered here is
# visible as a missing name, not as a silently smaller loop.


def _corpus() -> dict[str, tuple[TickConfig, TickState]]:
    gated = StepConfig(metric_pass=0.8, metric_hold=0.5)
    stall_cfg = TickConfig()
    return {
        # 1-3: the structural branches
        "budget_exhausted": (
            TickConfig(steps=(StepConfig(),), max_cycles=2),
            _state(total_cycles=2),
        ),
        "all_steps_done": (TickConfig(steps=(StepConfig(),)), _state(step_index=1)),
        "worker_in_flight": (TickConfig(), _state(worker_in_flight=True)),
        # 4-5: the recoverable and environment tiers
        "recoverable_wait": (stall_cfg, _fail(_state(), n=2, text="429 rate limited")),
        "recoverable_exhausted": (stall_cfg, _fail(_state(), n=12, text="429 rate limited")),
        "environment_broken": (stall_cfg, _fail(_state(), text="command not found: pytest")),
        # 6: REPLAN
        "replan": (stall_cfg, _state(plan_critique="the plan skips verification")),
        "replan_capped": (
            TickConfig(replan_cap=1),
            _state(plan_critique="still wrong", replans_taken=1),
        ),
        # 7: the stall ladder, at each of its positions
        "stall_nudge": (
            stall_cfg,
            _fail(_state(), n=3, tool="bash", args={"cmd": "make test"}),
        ),
        "stall_escalate": (
            stall_cfg,
            _fail(_state(nudges_issued=1), n=3, tool="t", args={}, hint="wrong_work"),
        ),
        "stall_surfaced": (
            stall_cfg,
            _fail(
                _state(nudges_issued=1, escalations_taken=len(DEFAULT_LADDER) - 1),
                n=3,
                tool="t",
                args={},
            ),
        ),
        "hypothesis_exhausted": (
            stall_cfg,
            _fail(_state(nudges_issued=1), n=3, text="still failing", fix="null check line 52"),
        ),
        # 8-13: the progress branches, unchanged by PP-15
        "rollback": (
            TickConfig(steps=(gated, gated)),
            _state(step_index=1, metric=0.2, prior_step_floor=0.6),
        ),
        "rollback_capped": (
            TickConfig(steps=(gated, gated), rollback_cap=1),
            _state(step_index=1, metric=0.2, prior_step_floor=0.6, rollbacks_on_step=1),
        ),
        "dwell_hold": (
            TickConfig(steps=(StepConfig(min_dwell_secs=50.0),)),
            _state(step_started_at=NOW - 1.0),
        ),
        "gathering_evidence": (
            TickConfig(steps=(StepConfig(min_findings=3),)),
            _state(findings_in_step=1),
        ),
        "advance": (TickConfig(steps=(StepConfig(), StepConfig())), _state(gate_passed=True)),
        "marginal_hold": (TickConfig(steps=(gated,)), _state(metric=0.6)),
        "execute": (TickConfig(steps=(StepConfig(),)), _state()),
    }


CORPUS = _corpus()


def test_the_corpus_actually_reaches_every_action_including_the_new_two():
    """Vacuity control. A purity suite that only ever exercised EXECUTE would pass forever
    while the branches that gained state stayed unproven, so the corpus must be shown to
    reach every member of `Action` — above all the two PP-15 added."""
    reached = {evaluate(cfg, state, NOW).action for cfg, state in CORPUS.values()}
    missing = set(Action) - reached
    assert not missing, f"the corpus never reaches {sorted(a.value for a in missing)}"
    assert Action.ESCALATE in reached and Action.REPLAN in reached


# ── clause: purity is preserved and re-proven ──


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_the_same_inputs_yield_the_same_decision(name):
    """The purity contract, on EVERY branch: `evaluate` is a function, not a step.

    Called twice with identical `(cfg, state, now)`, it must return equal `Decision`s AND
    leave the state untouched. The second half is the half that would have caught
    `check_middleware`: it mutated `state.escalation_index`, so its second answer differed
    from its first on exactly the branches that mattered most.
    """
    cfg, state = CORPUS[name]
    before = dataclasses.asdict(state)
    first = evaluate(cfg, state, NOW)
    second = evaluate(cfg, state, NOW)
    assert first == second, f"{name}: two calls, two answers"
    assert first.rung is second.rung, f"{name}: the RUNG is not stable across calls"
    assert dataclasses.asdict(state) == before, f"{name}: evaluate mutated its input"


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_a_restarted_process_rederives_the_same_decision(name):
    """Restartability, through the only channel a restart has: persisted bytes.

    The snapshot is serialized to JSON and rebuilt — tuples come back as lists, enums as
    strings — and the rebuilt snapshot must decide identically, rung included. A test that
    merely copied the dataclass would prove nothing about a restart, because a restart never
    gets the object.
    """
    cfg, state = CORPUS[name]
    live = evaluate(cfg, state, NOW)

    raw = json.dumps(dataclasses.asdict(state)).encode("utf-8")
    fields = {f.name for f in dataclasses.fields(TickState)}
    revived_kwargs = {}
    for key, value in json.loads(raw.decode("utf-8")).items():
        assert key in fields
        revived_kwargs[key] = tuple(value) if isinstance(value, list) else value
    revived = TickState(**revived_kwargs)
    assert revived == state, "the snapshot did not survive a JSON round trip"

    restarted = evaluate(cfg, revived, NOW)
    assert restarted == live, f"{name}: a restarted process decided differently"
    assert restarted.rung is live.rung, f"{name}: the rung was not re-derived"


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_evaluate_reads_no_clock(name, monkeypatch):
    """`now` is a parameter for exactly this reason.

    Both `time.time` and `time.monotonic` are replaced with values no real clock would ever
    return. A decision that changes is a decision that read a clock, and a clock inside
    `evaluate` breaks replay AND makes the dwell branch untestable.
    """
    cfg, state = CORPUS[name]
    baseline = evaluate(cfg, state, NOW)
    for absurd in (0.0, -1.0, 9e12):
        monkeypatch.setattr("time.time", lambda v=absurd: v)
        monkeypatch.setattr("time.monotonic", lambda v=absurd: v)
        assert evaluate(cfg, state, NOW) == baseline, f"{name}: the decision moved with the clock"


def test_now_is_the_only_time_input_that_changes_anything():
    """The complement: `now` must genuinely be READ, or the dwell branch is dead and the
    clock-independence test above is vacuous."""
    cfg = TickConfig(steps=(StepConfig(min_dwell_secs=50.0),))
    state = _state(step_started_at=100.0)
    assert evaluate(cfg, state, 120.0).action is Action.HOLD
    assert evaluate(cfg, state, 200.0).action is not Action.HOLD


# ── clause: a retryable class never consumes a rung ──


@pytest.mark.parametrize("text", ["429 rate limited", "connection reset by peer", "504 Gateway"])
def test_a_retryable_class_never_consumes_a_rung(text):
    """Burning the ladder on a 429 is how a run that would have finished doesn't. Asserted
    through `applied` — the WRITE half — because "the decision said consumed_rung=False" is
    only half the claim; the counters must actually stay put."""
    cfg = TickConfig()
    state = _fail(_state(), n=3, text=text)
    decision = evaluate(cfg, state, NOW)
    assert decision.consumed_rung is False
    after = tick.applied(cfg, state, decision)
    assert after.escalations_taken == 0
    assert after.attempts_at_rung == 0
    assert after.nudges_issued == 0
    assert after.recoverable_waits == state.recoverable_waits + 1


def test_the_rung_a_stall_takes_is_a_function_of_persisted_position_only():
    """The ladder's position must come from the snapshot, not from call history. Two DIFFERENT
    processes handed the same persisted position must pick the same rung."""
    cfg = TickConfig()
    for taken, expected in enumerate(DEFAULT_LADDER[:-1]):
        state = _fail(_state(nudges_issued=1, escalations_taken=taken), n=3, tool="t", args={})
        assert evaluate(cfg, state, NOW).rung is expected


# ── clause: the policy DRIVES the decision ──


def test_the_supervisor_policy_supplies_the_thresholds():
    """The wiring PP-14 declared and PP-15 owed: a template's `supervisor:` block must be what
    the engine applies. A policy whose ladder is one rung long must surface a stall that a
    default policy would only escalate."""

    def _cycles_until_surfaced(policy: supervisor_policy.SupervisorPolicy) -> int:
        cfg = supervisor_policy.tick_config(policy)
        state = _state()
        for n in range(1, 40):
            state = tick.record_failure(state, tool="t", args={}, hint="wrong_work")
            decision = evaluate(cfg, state, NOW)
            if decision.surfaced:
                return n
            state = tick.applied(cfg, state, decision)
        raise AssertionError("never surfaced")

    short = supervisor_policy.SupervisorPolicy(escalation_ladder=(Rung.CLASSIFIED_RETRY,))
    default = supervisor_policy.SupervisorPolicy()

    # A declared ladder with one real rung must run out sooner than the five-rung default.
    # Comparing the two is the assertion that the DECLARATION is what the engine read: a
    # hard-coded ladder would give both policies the same number.
    assert _cycles_until_surfaced(short) < _cycles_until_surfaced(default)
    assert supervisor_policy.tick_config(short).rungs() == (Rung.CLASSIFIED_RETRY, Rung.SURFACE)


def test_the_policy_budget_and_mutations_reach_the_decision():
    policy = supervisor_policy.SupervisorPolicy(
        budget_max_cycles=3,
        failure_mutations={"malformed_output": "MY INSTRUCTION"},
    )
    cfg = supervisor_policy.tick_config(policy)
    assert cfg.max_cycles == 3
    assert evaluate(cfg, _state(total_cycles=3), NOW).action is Action.COMPLETE
    stall = _fail(_state(), n=3, text="json decode error", tool="t", args={})
    assert evaluate(cfg, stall, NOW).nudge_text == "MY INSTRUCTION"


def test_the_marginal_value_band_becomes_a_real_metric_gate():
    """The band was a declared field nothing read. It is a metric gate expressed on the judge
    scale, so it must gate — and only when `gates` declares none of its own, so one loop never
    carries two thresholds that could disagree."""
    banded = supervisor_policy.tick_config(
        supervisor_policy.SupervisorPolicy(marginal_value_band=(1.0, 4.0))
    )
    assert banded.steps[0].metric_hold == 1.0
    assert banded.steps[0].metric_pass == 4.0

    explicit = supervisor_policy.tick_config(
        supervisor_policy.SupervisorPolicy(
            gates=StepConfig(metric_pass=0.9, metric_hold=0.4), marginal_value_band=(1.0, 4.0)
        )
    )
    assert explicit.steps[0].metric_pass == 0.9, "an explicit gate must win over the band"


# ── clause: REPLAN queues a REAL mutation batch, end to end ──

pytestmark_anyio = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _replan_spec() -> dict:
    """A sequence root holding a loop that fails identically every iteration.

    `error_streak: 2` makes the breaker trip fast; the loop's body raises the same way each
    time, which is the shape the convergence core is asked about.
    """
    return {
        "name": "replanner",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "loop",
                    "id": "l",
                    "config": {
                        "mode": "counted",
                        "n": 6,
                        "error_streak": 2,
                        "supervisor": {"escalation_ladder": ["classified_retry", "surface"]},
                    },
                    "body": {"kind": "infer", "id": "b", "config": {"prompt": "work {{iter}}"}},
                }
            ],
        },
    }


def _make_run(spec: dict) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec.get("name", "wf"), inputs={}))
    store.write_spec(run.id, spec)
    return run


@pytest.mark.anyio
async def test_a_replan_queues_a_real_mutation_batch_and_the_run_reruns_from_it():
    """THE clause. Not "REPLAN came back" — the SPEC must change and the re-derived step must
    RUN.

    A judge critique is parked on the run (`plan_critique`), the loop body then fails
    identically until the breaker trips, and the convergence core answers REPLAN. What must be
    observable afterwards:

    * the spec gained a node built from the critique (a real `mutations.insert`, applied at the
      controller's drain point, not a prompt with a hint stapled on);
    * `spec_version` bumped, so the change is auditable rather than invisible;
    * the inserted step actually EXECUTED — the run re-derived its remaining steps.

    The behaviour being replaced would fail every one of those three.
    """
    prompts: list[str] = []
    spec = _replan_spec()
    run = _make_run(spec)

    async def body(prompt, *, use_case="background", output_type=None):
        prompts.append(prompt)
        if "re-derive" in prompt.lower() or "critique" in prompt.lower():
            return "revised plan: verify before shipping"
        # Fail identically until the breaker trips and the convergence core replans; then
        # succeed, so the run reaches the step the replan inserted. A body that failed forever
        # would surface the loop and prove only that a batch was queued — never that the run
        # went on to re-derive anything.
        if len([p for p in prompts if "re-derive" not in p.lower()]) <= 3:
            raise RuntimeError("boom: the same failure every time")
        return "ok"

    c = RunController(run, spec, services=EngineServices(completion=body))
    c.run.extra["plan_critique"] = "the plan never verifies its own output"
    await c.run_to_completion(timeout=40)

    # 1. A real mutation landed.
    inserted = [
        n
        for n in (c.spec.get("root") or {}).get("children") or []
        if isinstance(n, dict) and str(n.get("id", "")).endswith("__replan1")
    ]
    assert inserted, (
        "REPLAN did not change the spec — this is the retry-with-a-hint behaviour it replaces. "
        f"root children: {[n.get('id') for n in (c.spec.get('root') or {}).get('children') or []]}"
    )

    # 2. The critique is what it was built from, and the change is auditable.
    assert "never verifies its own output" in json.dumps(inserted[0])
    assert c.run.spec_version >= 1, "a plan change that does not bump spec_version is invisible"

    # 3. The re-derived step RAN. This is the half that separates "queued a batch" from "the run
    #    re-derived its remaining steps".
    assert any(
        "re-derive" in p.lower() for p in prompts
    ), f"the inserted replan step never executed; prompts were {prompts}"

    kinds = [r["kind"] for r in J.ledger(run.id)]
    assert (
        J.USER_EDITED_MID_FLIGHT in kinds
    ), f"the applied batch left no ledger record: {sorted(set(kinds))}"
    assert J.MUTATION_REJECTED not in kinds, "the batch was rejected, not applied"


@pytest.mark.anyio
async def test_a_replan_that_cannot_land_surfaces_instead_of_looping():
    """A shape with no structural target must not silently "replan" nowhere. Surfacing is the
    honest outcome; quietly applying the batch elsewhere would change a different part of the
    plan than the critique named."""
    spec = {
        "name": "unreplannable",
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "counted", "n": 4, "error_streak": 2},
            "body": {"kind": "infer", "id": "b", "config": {"prompt": "work"}},
        },
    }
    run = _make_run(spec)

    async def body(prompt, *, use_case="background", output_type=None):
        raise RuntimeError("boom: identical")

    c = RunController(run, spec, services=EngineServices(completion=body))
    c.run.extra["plan_critique"] = "the plan is unsound"
    await c.run_to_completion(timeout=40)

    ids = [n.get("id") for n in (c.spec.get("root") or {}).get("children") or []]
    assert not any(
        str(i).endswith("__replan1") for i in ids
    ), "a leaf-bodied root loop has no safe insert target; a batch must not have landed"
    kinds = [r["kind"] for r in J.ledger(run.id)]
    assert J.STEP_ESCALATED in kinds, f"it neither replanned nor surfaced: {sorted(set(kinds))}"


@pytest.mark.anyio
async def test_the_ladder_replaces_the_binary_failure():
    """The engine used to fail BINARY: a tripped breaker went straight to a human, so every
    middle rung was unreachable in production. With a ladder declared, a trip must first be
    answered by something cheaper than a human."""
    spec = _replan_spec()
    run = _make_run(spec)

    async def body(prompt, *, use_case="background", output_type=None):
        raise RuntimeError("boom: identical")

    c = RunController(run, spec, services=EngineServices(completion=body))
    await c.run_to_completion(timeout=40)

    book = c.run.extra.get("convergence") or {}
    entry = next(iter(book.values()), {})
    decisions = entry.get("log") or []
    assert (
        decisions
    ), f"a tripped breaker produced no convergence decision — the trip is still binary: {book}"
    assert any(
        d.get("rung") != Rung.SURFACE.value for d in decisions
    ), f"every decision went straight to a human; the ladder is unreachable: {decisions}"
    # The position is PERSISTED on the run row — what a restarted process re-reads to resume at
    # the same rung instead of starting again at the cheapest one.
    assert entry.get("nudges", 0) or entry.get(
        "escalations", 0
    ), f"the ladder position was not persisted: {entry}"


def test_the_middleware_no_longer_owns_a_second_decision():
    """The clean break, asserted. A re-introduced `check_middleware` would be a second
    convergence brain, which is the whole failure mode this atom exists to close."""
    from personalclaw.workflows import loop_middleware

    for gone in ("check_middleware", "LoopState", "MiddlewareVerdict"):
        assert not hasattr(loop_middleware, gone), f"{gone} is back — two brains again"
    assert not hasattr(
        loop_middleware, "Action"
    ), "loop_middleware minted a second Action vocabulary again"
    assert FailureClass is loop_middleware.FailureClass, "the taxonomy must NOT be duplicated"
