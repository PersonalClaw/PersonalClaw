"""A run the day's spend ceiling refused is held on every path, never counted toward auto-pause.

🔴 The defect (Backlog: "A workflow run, or an action's result, the day's spend ceiling refused
still counts toward auto-pause"). A run the day's ceiling refused as it ran is held, not counted,
when its agent ended on the refusal or its action raised it
(`test_a_run_the_spend_ceiling_refused_waits_and_counts_nothing`). Two paths could not tell the
day's ceiling from a run's own cap, so they counted: a workflow run a step's refusal failed (the
step's `Failure` kept the refusal's headline and fix, not which ceiling), and an action that
returned a failure classed "budget" (`ActionResult.failure_class`). Five in a row paused the
automation, which stayed paused after the ceiling reset.

Now the refusal's scope rides along (`Failure.budget_scope`, `ActionResult.budget_scope`, from
`BudgetExceededError.scope`), and a day-scope one is held, as the agent and raised paths hold it.
A run's own cap still counts: nothing resets it. A result that says "budget" and not which
ceiling still counts, as before.

Driven through the real fire recorder (`run_record.record_run`), the real workflow ending's report
to its trigger (`run_finish.report_to_its_trigger`, `triggers.settle.settle_workflow_run`), the real
failure taxonomy and the real best-of-n provider, in a home of the test's own.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_a_run_the_spend_ceiling_refused_waits_and_counts_nothing as _held

from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.schedule_history import HELD_BY_A_SPEND_CEILING
from personalclaw.triggers import autopause
from personalclaw.triggers.models import TriggerState
from personalclaw.triggers.run_record import record_run
from personalclaw.workflows import run_finish
from personalclaw.workflows.failure_taxonomy import classify_action_result, classify_exception
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
    run_work_id,
)

TID = _held.TID
BUDGET = _held.BUDGET
SAID = _held.SAID
home = _held.home


def _returned(home: Path, n: int, *, scope: str) -> None:
    """*n* fires whose action returned a failure classed "budget", saying *scope*."""
    trigger = _held._live(home)
    for _ in range(n):
        result = ActionResult(success=False, failure_class="budget", budget_scope=scope, error=SAID)
        asyncio.run(
            record_run(trigger, started_at=time.time() - 1, result=result, source="schedule")
        )
        time.sleep(0.002)  # a row is named by the millisecond it was recorded


def _runs_end(home: Path, n: int, *, held: bool) -> None:
    """*n* workflow runs the automation started, each failed on a step's spend refusal, each
    reported to it as the run's terminal write reports it."""
    services = SimpleNamespace(on_attention=None, report_to_trigger=None)
    for i in range(n):
        run = WorkflowRun(
            id=f"run{i}",
            workflow_name="brief",
            origin=RunOrigin(kind=OriginKind.HOOK, trigger_id=TID),
            error_message=SAID,
        )
        _held._launched(home, run_work_id(run.id), i)
        run_finish.report_to_its_trigger(services, run, RunStatus.FAILED, held=held)


# ── an action that returned the refusal ──


def test_five_returned_day_ceiling_refusals_leave_the_automation_on(home):
    """🔴 Before: a returned "budget" failure counted, and five paused it."""
    _returned(home, BUDGET, scope="day")
    rows = _held._rows(home)
    assert {(r["status"], r["trigger"]) for r in rows} == {
        (HELD_BY_A_SPEND_CEILING, HELD_BY_A_SPEND_CEILING)
    }
    assert all(r["summary"][:80] == SAID[:80] for r in rows), [r["summary"] for r in rows]
    assert autopause.consecutive_failures_from(rows) == 0
    assert _held._live(home).enabled is True


def test_five_returned_run_cap_refusals_still_pause_it(home):
    _returned(home, BUDGET, scope="run")
    assert {r["status"] for r in _held._rows(home)} == {"failure"}
    assert _held._live(home).state == TriggerState.AUTOPAUSED.value


# ── a workflow run a step's refusal failed ──


def test_five_workflow_runs_the_day_ceiling_failed_leave_the_automation_on(home):
    """🔴 Before: the run's ending was a failure to its automation, whatever ceiling refused."""
    _runs_end(home, BUDGET, held=True)
    rows = _held._rows(home)
    assert {r["status"] for r in rows} == {HELD_BY_A_SPEND_CEILING}, rows
    assert {(r["summary"][:80], r.get("error") or "") for r in rows} == {(SAID[:80], "")}, rows
    live = _held._live(home)
    assert (live.enabled, live.state) == (True, TriggerState.ACTIVE.value)
    assert live.last_waiting_at and not live.last_failure_at


def test_five_workflow_runs_their_own_cap_failed_still_pause_it(home):
    _runs_end(home, BUDGET, held=False)
    assert {r["status"] for r in _held._rows(home)} == {"failure"}
    assert _held._live(home).state == TriggerState.AUTOPAUSED.value


# ── which ceiling, carried from the refusal ──


@pytest.mark.parametrize("scope", ["day", "run"])
def test_a_steps_failure_says_which_ceiling_refused(scope):
    """A step whose model call the ceiling refused, and one whose action returned the refusal."""
    raised = classify_exception(_held._refusal(scope))
    assert (raised.failure_class, raised.budget_scope) == (FailureClass.BUDGET, scope)
    returned = classify_action_result(
        ActionResult(success=False, failure_class="budget", budget_scope=scope, error=SAID)
    )
    assert (returned.failure_class, returned.budget_scope) == (FailureClass.BUDGET, scope)


def test_which_ceiling_is_kept_with_the_runs_state():
    """A run's state is written and read back (a restart, a resume): the scope survives it."""
    failure = Failure(failure_class=FailureClass.BUDGET, budget_scope="day")
    assert Failure.from_dict(failure.to_dict()).budget_scope == "day"
    assert "budget_scope" not in Failure(failure_class=FailureClass.USER).to_dict()


@pytest.mark.asyncio
async def test_a_best_of_n_step_the_day_ceiling_refused_says_so(tmp_path, monkeypatch):
    """The real provider, its every sample refused: its result says the day's ceiling."""
    import personalclaw.llm_helpers as llm_helpers
    from personalclaw.action_providers.best_of_n_provider import BestOfNActionProvider

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    async def _refused(*_a, **_kw):
        raise _held._refusal("day")

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _refused)
    result = await BestOfNActionProvider().execute(
        {"prompt": "Name one primary color.", "n": 2}, ActionContext(event="workflow")
    )
    assert (result.success, result.failure_class, result.budget_scope) == (False, "budget", "day")
    assert classify_action_result(result).budget_scope == "day"


def test_a_run_is_held_only_when_every_failed_step_is_the_day_ceiling():
    """The controller's own reading: a run another failure also failed is that failure."""
    from personalclaw.workflows.controller import RunController
    from personalclaw.workflows.models import InstanceState

    def _ctl(*failures):
        ctl = object.__new__(RunController)
        ctl.instances = {
            f"n{i}": SimpleNamespace(state=InstanceState.FAILED, failure=f)
            for i, f in enumerate(failures)
        }
        return ctl

    day = Failure(failure_class=FailureClass.BUDGET, budget_scope="day")
    run_cap = Failure(failure_class=FailureClass.BUDGET, budget_scope="run")
    other = Failure(failure_class=FailureClass.USER)
    assert _ctl(day)._held_by_the_day_ceiling() is True
    assert _ctl(day, day)._held_by_the_day_ceiling() is True
    assert _ctl(day, other)._held_by_the_day_ceiling() is False
    assert _ctl(run_cap)._held_by_the_day_ceiling() is False
    assert _ctl()._held_by_the_day_ceiling() is False


SPEC = {
    "name": "brief",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {
                "kind": "action",
                "id": "sample",
                "config": {"provider": "best-of-n", "with": {"prompt": "Sum.", "n": 2}},
            }
        ],
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope, held", [("day", True), ("run", False)])
async def test_a_real_run_a_steps_refusal_failed_reaches_its_automation_as_that(
    home, monkeypatch, scope, held
):
    """Through the real supervisor and controller: a run whose best-of-n step every sample of
    which the ceiling refused fails, and its automation's row is held for the day's ceiling and a
    failure for a run's own cap."""
    import copy

    import personalclaw.llm_helpers as llm_helpers
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    async def _refused(*_a, **_kw):
        raise _held._refusal(scope)

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _refused)
    supervisor = WorkflowWatchdog(state=None, services=EngineServices())
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=SPEC["name"],
            origin=RunOrigin(kind=OriginKind.HOOK, trigger_id=TID),
        )
    )
    _held._launched(home, run_work_id(run.id), 0)
    store.write_spec(run.id, copy.deepcopy(SPEC))
    controller = await supervisor.launch(run, copy.deepcopy(SPEC))
    await asyncio.wait_for(controller._terminal.wait(), timeout=20)

    assert controller.run.status == RunStatus.FAILED, controller.run.status
    (row,) = _held._rows(home)
    assert row["status"] == (HELD_BY_A_SPEND_CEILING if held else "failure"), row


def test_the_history_tells_a_held_run_from_a_failed_one(home):
    """The control for the counts above: a held row is not a failure to its automation."""
    _returned(home, 1, scope="day")
    _returned(home, 1, scope="run")
    assert sorted(r["status"] for r in _held._rows(home)) == sorted(
        [HELD_BY_A_SPEND_CEILING, "failure"]
    )
