"""A nested spend ceiling stays inside what the run scope it runs within has left, and never
replaces it.

A replay pass, a room member's turn, the doctor's judgment lane and an optimize search each bind a
ceiling of their own around their model calls. Before this change the replay and the room member
REPLACED whatever ceiling was already bound: inside an automation's run with $0.30 left, a replay
with a $5.00 budget could spend $5.00, and none of it reached the automation's account. Each is now
held, in each dimension, to the tighter of its own ceiling and what the enclosing one has left
(`guardrails.budgets.held_within`), and what it spends is charged to the enclosing account.

The calls below are admitted and settled the way `ModelCallGuard` does it (`admit_call`, then
`SpendMeter.settle`), at a fixed price per call, against a meter whose day file lives under this
test's own folder.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from personalclaw.guardrails import budgets
from personalclaw.guardrails.budgets import (
    Budget,
    CallCost,
    current_run_budget,
    current_run_key,
    reset_current_run_budget,
    reset_current_run_key,
    set_current_run_budget,
    set_current_run_key,
)
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.model_call import admit_call

#: What one call costs, and the model it is a call to.
PER_CALL = 0.20
REF = "cloud-a:model-a"


@pytest.fixture(autouse=True)
def meter(tmp_path, monkeypatch):
    meter = budgets.SpendMeter(config_dir=tmp_path)
    monkeypatch.setattr(budgets, "get_meter", lambda: meter)
    return meter


async def _call(meter: budgets.SpendMeter) -> None:
    """One model call as the guard makes it: admitted against the ambient ceiling first, then
    charged what it cost to the ambient run's account."""
    hold = await admit_call(
        meter, CallCost(ref=REF, prompt_tokens=100, rate=(10.0, 10.0)), Budget(), Budget()
    )
    meter.settle(
        hold,
        ref=REF,
        tokens=300,
        answer_tokens=200,
        dollars=PER_CALL,
        priced=True,
        run_key=current_run_key(),
    )


async def _calls_until_refused(meter: budgets.SpendMeter, most: int = 20) -> int:
    for made in range(most):
        try:
            await _call(meter)
        except BudgetExceededError:
            return made
    return most


@pytest.fixture
def doctor(tmp_path, monkeypatch):
    """The doctor, with its ledger under this test's folder and its job registry put back as it
    was: a job a test registers would otherwise stay registered for every test after it."""
    from personalclaw.resilience import remediation as rem

    monkeypatch.setattr("personalclaw.resilience.remediation.config_dir", lambda: tmp_path)
    monkeypatch.setattr(rem, "_load_job_state", lambda: {})
    monkeypatch.setattr(rem, "_save_job_state", lambda state: None, raising=False)
    saved = dict(rem._JOBS)
    yield rem
    rem._JOBS.clear()
    rem._JOBS.update(saved)


@contextlib.contextmanager
def _outer(meter: budgets.SpendMeter, key: str, ceiling: float, spent: float):
    """An automation's run with *ceiling* dollars and *spent* of them gone, bound as its fire binds
    its run scope with nothing around it: its key and its ceiling."""
    meter.charge_run(key, 0, spent)
    key_token = set_current_run_key(key)
    budget_token = set_current_run_budget(Budget(max_dollars=ceiling))
    try:
        yield
    finally:
        reset_current_run_budget(budget_token)
        reset_current_run_key(key_token)


# ── the rule ────────────────────────────────────────────────────────────────────────────────


def test_a_nested_ceiling_is_held_inside_what_the_enclosing_one_has_left(meter):
    from personalclaw.guardrails.budgets import held_within

    async def go() -> int:
        with _outer(meter, "trigger:digest:1", 0.50, 0.20):
            with held_within("learning_replay", Budget(max_dollars=5.00)):
                assert current_run_budget().max_dollars == pytest.approx(0.30)
                return await _calls_until_refused(meter)

    # $0.30 left outside: one $0.20 call fits, a second would pass it. With the replay's own $5.00
    # standing in for the automation's, every call fit.
    assert asyncio.run(go()) == 1
    assert meter.run_totals("trigger:digest:1").dollars == pytest.approx(0.40)
    assert current_run_key() == "" and current_run_budget().is_unlimited


def test_with_nothing_enclosing_it_a_nested_ceiling_holds_on_its_own(meter):
    from personalclaw.guardrails.budgets import held_within

    async def go() -> int:
        with held_within("learning_replay", Budget(max_dollars=0.50)):
            return await _calls_until_refused(meter)

    assert asyncio.run(go()) == 2


def test_the_operators_run_default_holds_beside_an_ambient_dollar_ceiling(meter):
    """`max_tokens_per_run` and a run's own dollar ceiling are two ceilings, and both hold: a
    dollar ceiling bound for a run used to take the token default's place."""

    async def go() -> None:
        with _outer(meter, "trigger:digest:2", 5.0, 0.0):
            meter.charge_run("trigger:digest:2", 600, 0.0)  # past the 500-token default
            await admit_call(
                meter,
                CallCost(ref=REF, prompt_tokens=100, rate=(10.0, 10.0)),
                Budget(),
                Budget(max_tokens=500),
            )

    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(go())
    assert refused.value.dimension == "tokens"


# ── each nested ceiling ─────────────────────────────────────────────────────────────────────


def test_a_replay_cannot_spend_more_than_the_run_it_runs_within_has_left(meter):
    from personalclaw.learning import replay as replay_mod
    from personalclaw.learning.proposals import Proposal

    class Judge:
        async def start(self) -> None: ...

        async def shutdown(self) -> None: ...

        async def score(self, *a, **k):
            return SimpleNamespace(score=4.0, reason="ok")

        async def evaluate(self, *a, **k):
            return SimpleNamespace(score=4.0, reason="ok")

    made: list[float] = []

    async def completion(prompt, *, use_case="background", **kw):
        made.append(current_run_budget().max_dollars)
        await _call(meter)
        return "an answer"

    case = replay_mod.ReplayCase(
        session_id="sess-a",
        record_hash="hash1",
        prompt="<untrusted_content>\nrefactor the retry helper\n</untrusted_content>",
        tool_free=True,
        captured_at=1.0,
    )
    prop = Proposal(
        id="skill-nested01",
        kind="skill",
        title="Promote the checklist",
        body="Widen the test first.",
        provenance="inferred",
    )

    async def go():
        with _outer(meter, "trigger:digest:3", 0.50, 0.20):
            return await replay_mod.replay_proposal(
                prop, [case], completion=completion, judge_factory=Judge, budget_dollars=5.0
            )

    report = asyncio.run(go())
    # Held to the $0.30 the automation had left, not to its own $5.00: the first arm's call fit,
    # the second did not, and the replay says the budget stopped it.
    assert made and made[0] == pytest.approx(0.30), made
    assert report.deferred is True
    assert meter.run_totals("trigger:digest:3").dollars == pytest.approx(0.40)


def test_a_room_members_turn_cannot_spend_more_than_the_run_it_runs_within_has_left(meter):
    from personalclaw.guardrails.policy import INTERACTIVE
    from personalclaw.rooms import posture

    profile = INTERACTIVE.with_overrides(budget=Budget(max_dollars=5.0))

    async def go() -> int:
        with _outer(meter, "trigger:digest:4", 0.50, 0.20):
            with posture.member_spend_scope("room:r:critic", profile):
                assert current_run_key() == "room:r:critic"
                return await _calls_until_refused(meter)

    assert asyncio.run(go()) == 1
    # The automation's account counts the turn, and the member keeps its own across turns.
    assert meter.run_totals("trigger:digest:4").dollars == pytest.approx(0.40)
    assert meter.run_totals("room:r:critic").dollars == pytest.approx(0.20)


def test_the_doctors_judgment_lane_is_held_inside_the_run_it_runs_within(
    meter, doctor, monkeypatch
):
    from personalclaw.resilience.remediation import Deficit, RemediationJob

    rem = doctor

    made: list[int] = []

    def judge() -> str:
        made.append(asyncio.run(_calls_until_refused(meter)))
        return "judged"

    rem.register_job(
        RemediationJob(
            id="fix.nested", title="Judge", run=judge, fixes_deficit="n", lane="judgment"
        )
    )
    monkeypatch.setattr(
        rem,
        "measure_deficits",
        lambda: [Deficit(key="n", count=20, weight=1.0, max_penalty=20.0, job_id="fix.nested")],
    )
    with _outer(meter, "trigger:doctor:1", 0.50, 0.20):
        rem.run_remediation(target_score=90, max_cost_usd=5.0, now=1000.0)
    assert made == [1], "held to the $0.30 its automation had left, not its own $5.00"
    assert meter.run_totals("trigger:doctor:1").dollars == pytest.approx(0.40)


def test_each_doctor_run_is_held_to_its_own_cap_not_to_what_an_earlier_run_spent(
    meter, doctor, monkeypatch
):
    """`max_cost_usd` is per run ("Max Cost / Run"): its account used to live as long as the
    gateway, so once one run had spent the cap no later run's judgment lane ever ran."""
    from personalclaw.resilience.remediation import Deficit, RemediationJob

    rem = doctor

    ran: list[str] = []
    rem.register_job(
        RemediationJob(
            id="fix.again",
            title="Judge",
            run=lambda: ran.append("j") or "judged",
            fixes_deficit="g",
            lane="judgment",
        )
    )
    monkeypatch.setattr(
        rem,
        "measure_deficits",
        lambda: [Deficit(key="g", count=20, weight=1.0, max_penalty=20.0, job_id="fix.again")],
    )
    meter.charge_run("doctor", 0, 0.60)  # an earlier run spent past this one's cap
    result = rem.run_remediation(target_score=90, max_cost_usd=0.50, now=1000.0)
    assert ran == ["j"], result.stopped_reason


# ── a run of an automation's action, whichever door ran it ──────────────────────────────────


def _trigger(provider: str, *, cap: float) -> object:
    from personalclaw.triggers.models import Trigger

    return Trigger(
        id="clock:digest",
        name="Post the digest",
        kind="clock",
        created_by="user",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow={"inline": {"provider": provider, "config": {}}},
        capabilities={"providers": [provider]},
        gates={"max_cost_usd_per_run": cap},
    )


class _Observer:
    """An action that records the run scope its call would be made in."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, Budget]] = []

    async def execute(self, action_config, ctx, timeout=30):
        from personalclaw.action_providers.base import ActionResult

        self.seen.append((current_run_key(), current_run_budget()))
        return ActionResult(success=True, stdout="done")


def test_run_now_is_held_to_its_automations_per_run_dollar_cap(monkeypatch, meter):
    from personalclaw.action_providers import registry as actions
    from personalclaw.dashboard.handlers import trigger_runs

    actions._ensure_default_providers_registered()
    observer = _Observer()
    monkeypatch.setitem(actions._providers, "observe-scope", observer)

    ran, _note = asyncio.run(
        trigger_runs._dispatch_store_action(
            _trigger("observe-scope", cap=0.5), {"trigger_id": "clock:digest", "manual": True}
        )
    )

    assert ran is True
    ((key, ceiling),) = observer.seen
    assert key.startswith("trigger:clock:digest:"), key
    assert ceiling == Budget(max_dollars=0.5)
    assert meter.run_totals(key).dollars == 0.0 and current_run_key() == ""


def test_a_fire_binds_the_same_scope(monkeypatch, meter):
    from personalclaw.action_providers import registry as actions
    from personalclaw.gateway import GatewayOrchestrator

    actions._ensure_default_providers_registered()
    observer = _Observer()
    monkeypatch.setitem(actions._providers, "observe-scope", observer)
    trigger = _trigger("observe-scope", cap=0.75)

    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(
            trigger, {"trigger_id": trigger.id, "kind": trigger.kind}, event="cron"
        )
    )

    ((key, ceiling),) = observer.seen
    assert key.startswith("trigger:clock:digest:") and ceiling == Budget(max_dollars=0.75)


def test_a_workflow_run_an_automations_action_starts_takes_what_its_cap_has_left(meter):
    from personalclaw.workflows import run_budget
    from personalclaw.workflows.models import RunBudget

    with _outer(meter, "trigger:digest:5", 0.50, 0.20):
        held = run_budget.for_run(RunBudget(max_cost=5.0))
    assert held == RunBudget(max_cost=pytest.approx(0.30))
    assert run_budget.for_run(RunBudget(max_cost=5.0)) == RunBudget(max_cost=5.0)
