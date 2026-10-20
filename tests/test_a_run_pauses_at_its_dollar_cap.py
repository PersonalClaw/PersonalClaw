"""A run with a dollar cap pauses when what it has booked reaches the cap, as at its token cap.

The cap is the run's `RunBudget.max_cost`. What it counts is what the run's step rows book: each
attempt's model calls as the guard settles them (`workflows.step_usage`). The action providers below
stand in for the guard the way `guardrails.calls` is fed in production: each call is published to
the step's call log with `open_call` and settled with what its provider reported.

Measured before this change: the cap was read by nothing, so a run capped at $0.10 booked $0.18
across three steps and completed; nothing set a run's caps from its definition either, and a
subagent whose turn had no price was booked as a measured $0.00.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from personalclaw import approval_answer
from personalclaw.approval_answer import YOU
from personalclaw.guardrails.calls import DONE, open_call
from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunBudget, RunStatus, WorkflowRun
from personalclaw.workflows.step_usage import subagent_usage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


class _Result:
    """An `ActionResult` as a provider returns it."""

    def __init__(self) -> None:
        self.success = True
        self.stdout = '{"ok": true}'
        self.outcome = ""
        self.error = ""
        self.exit_code = 0
        self.stderr = ""
        self.agent_error = None
        self.failure_class = ""
        self.retry_after = 0.0


class _Priced:
    """Each step makes one model call that costs *dollars*: priced, or, for the steps *unpriced*
    names by their place, on a model nothing prices."""

    def __init__(self, dollars: float, *, unpriced: tuple[int, ...] = ()) -> None:
        self.dollars = dollars
        self.unpriced = unpriced
        self.steps: list[str] = []

    async def execute(self, cfg: dict[str, Any], ctx: Any, timeout: int = 30) -> _Result:
        priced = len(self.steps) not in self.unpriced
        self.steps.append(str((ctx.payload or {}).get("node_id", "")))
        call = open_call("cloud-a", "model-a", temperature=None)
        assert call is not None, "the step's dispatch had no call log bound"
        call.state = DONE
        call.input_tokens, call.output_tokens = 100, 20
        call.usage_reported = True
        call.cost_usd = self.dollars if priced else 0.0
        call.priced = priced
        return _Result()


def _spec(steps: int, name: str = "three-calls") -> dict[str, Any]:
    return {
        "name": name,
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "action", "id": f"a{i}", "config": {"provider": "priced"}}
                for i in range(steps)
            ],
        },
    }


def _controller(spec: dict[str, Any], provider: Any, **run_kw: Any) -> RunController:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], **run_kw))
    store.write_spec(run.id, spec)
    return RunController(run, spec, services=EngineServices(get_provider=lambda name: provider))


class _Supervisor:
    """What `service` reaches a live controller through."""

    def __init__(self, controller: RunController) -> None:
        self._c = controller

    def controller(self, run_id: str) -> RunController | None:
        return self._c if self._c.run.id == run_id else None


async def _paused(c: RunController) -> RunStatus:
    """Wait for the tick loop a resume restarted to stop again, then for the run to settle."""
    for _ in range(400):
        if c._task is not None and c._task.done():
            break
        await asyncio.sleep(0.05)
    return store.get(c.run.id).status


# ── the cap ─────────────────────────────────────────────────────────────────────────────────


async def test_a_run_pauses_once_what_it_booked_reaches_its_dollar_cap():
    provider = _Priced(0.06)
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=0.10))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    # Two steps booked $0.12, past the $0.10 cap, so the third never started.
    assert provider.steps == ["a0", "a1"]
    saved = store.get(c.run.id)
    assert saved is not None and saved.status == RunStatus.PAUSED
    assert saved.error_message == "Paused at its dollar budget: $0.12 of $0.10 spent."
    # The run row holds the dollars its step rows booked, as it holds their tokens.
    assert saved.total_cost_usd == pytest.approx(0.12)
    assert J.run_totals(c.run.id)["cost_usd"] == pytest.approx(0.12)


async def test_the_token_cap_pauses_in_the_same_state_with_the_same_words():
    provider = _Priced(0.0)
    c = _controller(_spec(3), provider, budget=RunBudget(max_tokens=200))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    assert provider.steps == ["a0", "a1"]
    assert store.get(c.run.id).error_message == (
        "Paused at its token budget: 240 of 200 tokens used."
    )


async def test_resume_with_the_cap_raised_continues_the_run():
    provider = _Priced(0.06)
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=0.10))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    sup = _Supervisor(c)

    resumed = service.resume_run(c.run.id, supervisor=sup, by=YOU, budget={"max_cost": 0.50})
    assert resumed.get("ok") is True and resumed.get("resumed") is True, resumed
    assert await _paused(c) == RunStatus.COMPLETE
    assert provider.steps == ["a0", "a1", "a2"]
    saved = store.get(c.run.id)
    assert saved.budget == RunBudget(max_cost=0.50)
    assert saved.total_cost_usd == pytest.approx(0.18)


async def test_a_resume_that_would_only_pause_again_is_refused_and_the_run_stays_paused():
    provider = _Priced(0.06)
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=0.10))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    sup = _Supervisor(c)

    bare = service.resume_run(c.run.id, supervisor=sup, by=YOU)
    assert bare["ok"] is False and bare["code"] == "WF_RUN_AT_BUDGET", bare
    assert "$0.12 of $0.10 spent" in bare["message"]
    too_low = service.resume_run(c.run.id, supervisor=sup, by=YOU, budget={"max_cost": 0.11})
    assert too_low["code"] == "WF_RUN_AT_BUDGET", too_low
    await asyncio.sleep(0.3)
    assert provider.steps == ["a0", "a1"], "a refused resume ran a step"
    assert store.get(c.run.id).status == RunStatus.PAUSED


async def test_only_the_owner_raises_a_runs_cap():
    provider = _Priced(0.06)
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=0.10))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    sup = _Supervisor(c)

    by_agent = service.resume_run(
        c.run.id,
        supervisor=sup,
        by=approval_answer.agent("dashboard:chat-1"),
        budget={"max_cost": 5.0},
    )
    assert by_agent["ok"] is False and by_agent["code"] == "WF_RESUME_NOT_OWNER", by_agent
    assert store.budget_request(c.run.id) is None
    assert store.get(c.run.id).budget == RunBudget(max_cost=0.10)


async def test_a_budget_that_is_not_a_cap_is_refused():
    provider = _Priced(0.06)
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=0.10))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    sup = _Supervisor(c)
    for budget in ({"max_cost": -1}, {"max_cost": "lots"}, {"dollars": 5}, {"max_tokens": 1.5}):
        refused = service.resume_run(c.run.id, supervisor=sup, by=YOU, budget=budget)
        assert refused["code"] == "WF_RUN_BUDGET_INVALID", (budget, refused)


# ── spend no price covers ───────────────────────────────────────────────────────────────────


async def test_a_step_no_price_covers_pauses_a_dollar_capped_run_and_her_resume_goes_on_past_it():
    """Blocks rather than counting as $0.00: the cap cannot say whether the run is inside it."""
    provider = _Priced(0.02, unpriced=(0,))
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=1.0))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    assert provider.steps == ["a0"]
    saved = store.get(c.run.id)
    assert saved.error_message == (
        "Paused at its dollar budget: 1 step ran on a model with no price, so the $1.00 budget "
        "cannot count what it spent."
    )
    assert saved.unpriced_steps == 1 and saved.total_cost_usd == 0.0
    status = service.status(c.run.id)
    assert status["at_budget"] is True and status["spend"]["unpriced_steps"] == 1

    # Her resume lets it go on past that step; the steps after it are priced, and count.
    resumed = service.resume_run(c.run.id, supervisor=_Supervisor(c), by=YOU)
    assert resumed.get("resumed") is True, resumed
    assert await _paused(c) == RunStatus.COMPLETE
    assert provider.steps == ["a0", "a1", "a2"]
    saved = store.get(c.run.id)
    assert saved.unpriced_allowed == 1 and saved.total_cost_usd == pytest.approx(0.04)
    # The figure still says it leaves the first step out.
    assert service.status(c.run.id)["spend"] == {
        "tokens": 360,
        "dollars": pytest.approx(0.04),
        "unpriced_steps": 1,
    }


async def test_an_agent_cannot_let_a_run_go_on_past_spend_its_cap_cannot_count():
    provider = _Priced(0.02, unpriced=(0,))
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=1.0))
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    refused = service.resume_run(
        c.run.id, supervisor=_Supervisor(c), by=approval_answer.agent("dashboard:chat-1")
    )
    assert refused["code"] == "WF_RESUME_NOT_OWNER", refused
    assert provider.steps == ["a0"]


def test_a_subagent_whose_turn_had_no_price_books_an_unknown_cost_not_a_free_one():
    class Info:
        input_tokens, output_tokens, cost_usd, model = 900, 100, 0.0, "model-x"
        priced = False

    assert subagent_usage(Info()).cost_usd is None
    Info.priced = True
    assert subagent_usage(Info()).cost_usd == 0.0


# ── a run with no dollar cap ────────────────────────────────────────────────────────────────


async def test_a_run_with_no_dollar_cap_runs_as_it_did():
    provider = _Priced(0.06, unpriced=(1,))
    c = _controller(_spec(3), provider)
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
    assert provider.steps == ["a0", "a1", "a2"]
    saved = store.get(c.run.id)
    assert saved.error_message == "" and saved.budget == RunBudget()
    assert service.status(c.run.id)["at_budget"] is False


# ── where a run's caps come from ────────────────────────────────────────────────────────────


async def _start_served(spec: dict[str, Any]) -> dict[str, Any]:
    """Start a run of *spec*, handed over as it stands by a definition provider of its own."""
    from personalclaw.workflows import defs as defs_mod

    class _Defs(defs_mod.WorkflowDefProvider):
        name = "capped-defs"
        readonly = True

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return [spec], 1

        async def get_def(self, name: str):
            return spec if name == spec["name"] else None

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    defs_mod.register_provider(_Defs())
    try:
        return await service.start_run(name=spec["name"], skip_preflight=True)
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


async def test_a_run_starts_with_the_caps_its_definition_declares():
    spec = {**_spec(1, name="capped-def"), "defaults": {"budget": {"max_cost": 0.25}}}
    started = await _start_served(spec)
    # No supervisor here, so the run is created and not launched: its record is what it starts as.
    assert started["code"] == "WF_NO_SUPERVISOR", started
    assert store.get(started["run_id"]).budget == RunBudget(max_cost=0.25)


async def test_caps_no_run_can_be_held_to_are_refused_rather_than_read_as_none():
    spec = {**_spec(1, name="unreadable-caps"), "defaults": {"budget": {"max_cost": "lots"}}}
    started = await _start_served(spec)
    assert started["code"] == "WF_RUN_BUDGET_INVALID", started
    assert "max_cost must be a number of 0 or more" in started["message"]
    assert store.all_runs() == [], "a run was created for a start that was refused"


@pytest.mark.parametrize(
    "budget",
    [{"max_cost": "lots"}, {"max_cost": -1}, {"max_tokens": 1.5}, {"max_tokens": True}, [0.5]],
)
def test_a_definition_is_not_saved_with_caps_no_run_can_be_held_to(budget):
    from personalclaw.workflows.validator import validate_spec

    found = validate_spec({**_spec(1, name="caps"), "defaults": {"budget": budget}})
    assert ("WF_BAD_BUDGET", "error") in {(i.code, i.severity) for i in found.issues}
    assert not found.ok


def test_a_key_that_names_no_cap_is_said_to_hold_nothing():
    from personalclaw.workflows.validator import validate_spec

    def budget_issues(budget: Any) -> set[tuple[str, str]]:
        found = validate_spec({**_spec(1, name="caps"), "defaults": {"budget": budget}})
        return {(i.code, i.severity) for i in found.issues if "BUDGET" in i.code}

    assert budget_issues({"max_costs": 0.25}) == {("WF_BUDGET_UNKNOWN_CAP", "warning")}
    assert budget_issues({"max_cost": 0.25, "max_tokens": 50000}) == set()
    # The presence flag a read leaves in a token cap's place is the save path's to restore.
    assert budget_issues({"_has_max_tokens": True, "max_cost": 0}) == set()


def test_a_run_another_run_starts_is_held_inside_what_that_run_has_left():
    from personalclaw.workflows import run_budget

    parent = store.create(
        WorkflowRun(id="", workflow_name="p", budget=RunBudget(max_tokens=1000, max_cost=1.0))
    )
    parent.total_tokens, parent.total_cost_usd = 400, 0.40
    store.save(parent)
    held = run_budget.for_run(RunBudget(max_cost=5.0), parent=store.get(parent.id))
    assert held == RunBudget(max_tokens=600, max_cost=pytest.approx(0.60))
    # Its own cap is kept where it is the tighter one, and no cap of its own loses to the parent's.
    assert run_budget.for_run(RunBudget(max_cost=0.10), parent=parent).max_cost == 0.10
    # A parent with nothing left holds it at the least amount there is, never at none.
    parent.total_cost_usd = 2.0
    assert 0 < run_budget.for_run(RunBudget(), parent=parent).max_cost < 0.01
    # A run nothing caps starts with its own caps.
    assert run_budget.for_run(RunBudget(max_cost=0.3)) == RunBudget(max_cost=0.3)


async def test_what_a_run_its_step_started_books_counts_toward_its_cap():
    """A `run-workflow` step's run is left running: the run that started it is held to what both
    booked, and pauses when they reach its cap."""
    provider = _Priced(0.06)
    c = _controller(_spec(3), provider, budget=RunBudget(max_cost=0.10))
    started = store.create(
        WorkflowRun(
            id="",
            workflow_name="started",
            parent_run_id=c.run.id,
            spawned_by_node_id="a0",
            total_cost_usd=0.08,
            total_tokens=50,
        )
    )
    assert [r.id for r in store.started_runs(c.run.id)] == [started.id]
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    # $0.06 of its own and the $0.08 the run its step started booked: past $0.10 after one step.
    assert provider.steps == ["a0"]
    assert store.get(c.run.id).error_message == (
        "Paused at its dollar budget: $0.14 of $0.10 spent."
    )


# ── what is running when a cap is reached ───────────────────────────────────────────────────


async def test_steps_already_running_finish_and_are_booked_before_the_run_pauses():
    """Nothing new starts once a cap is reached, and what is in flight finishes: a step stopped
    mid-way would leave what it spent uncounted and be paid for again on resume."""
    release = asyncio.Event()

    class TwoSpeeds:
        def __init__(self) -> None:
            self.steps: list[str] = []

        async def execute(self, cfg: dict[str, Any], ctx: Any, timeout: int = 30) -> _Result:
            node = str((ctx.payload or {}).get("node_id", ""))
            self.steps.append(node)
            if node == "slow":
                await release.wait()
            call = open_call("cloud-a", "model-a", temperature=None)
            call.state = DONE
            call.input_tokens, call.output_tokens = 10, 10
            call.usage_reported = True
            call.cost_usd = 0.20 if node == "fast" else 0.05
            return _Result()

    spec = {
        "name": "fan",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "parallel",
                    "id": "both",
                    "children": [
                        {"kind": "action", "id": "fast", "config": {"provider": "p"}},
                        {"kind": "action", "id": "slow", "config": {"provider": "p"}},
                    ],
                },
                {"kind": "action", "id": "after", "config": {"provider": "p"}},
            ],
        },
    }
    provider = TwoSpeeds()
    c = _controller(spec, provider, budget=RunBudget(max_cost=0.10))
    await c.start()
    for _ in range(200):
        if provider.steps.count("fast") and store.get(c.run.id).total_cost_usd > 0:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    # Over its cap with "slow" still going: not paused yet, and nothing new started.
    assert store.get(c.run.id).status == RunStatus.RUNNING
    release.set()
    assert await c.run_to_completion(timeout=20) == RunStatus.PAUSED
    assert "after" not in provider.steps
    saved = store.get(c.run.id)
    assert saved.total_cost_usd == pytest.approx(0.25)
    states = {i.path: i.state for i in store.read_state(c.run.id).values()}
    assert states["root.children[0].children[1]"] == InstanceState.DONE
